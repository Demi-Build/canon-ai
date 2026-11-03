"""Content-provenance journal + content-addressed object store.

Canon's Bible captures *state + lineage* (provenance_hash, parents, status). This
module captures the *trajectory*: every generate / edit / import mutation as an
append-only event in ``.canon/journal.jsonl``, with the exact bytes of each
version preserved in a content-addressed store at ``.canon/objects/<sha256>``.

Together they are the training-data substrate — a (generated → human-edited) pair
is recoverable by replaying an ``edit`` event's ``before_hash``/``after_hash``
against the object store. Everything cradle does to a pack flows through the
canon write verbs, and each verb records here, so coverage is complete from one
choke point.

Local-first: a webapp flushes the same event shape to a remote sink. This log is
outside canon's byte-determinism contract (like the StepLog).

Event shape (one line of journal.jsonl) — row P1-A6 implements master §3.0-B /
P0 paper P.8 ONCE here; every field it added is ADDITIVE and ``schema`` stays
``1`` (nothing written before A6 becomes invalid):

    {schema, ts, artifact_id, op, source, actor, identity, session?,
     detail?, before_hash?, after_hash?, gen?, batchId?,
     costCents?, accuracy?, genKind?}

  op      : generate | edit | keep | delete | import | switch | regenerate |
            create | restore — an OPEN string vocabulary, never a Literal.
  source  : llm | user | import | code — likewise open.
  identity: ``user`` | ``agent:<conversation>/<specialist>`` (P.8.2) — a PURE
            FUNCTION of ``actor`` computed inside :func:`record` at write time
            and re-derived at read time for pre-A6 events (:func:`identity_for`).
            No verb takes ``--identity``; ``cradle:user`` / ``cradle`` / ``user``
            all collapse to ``user``.
  costCents: int ≥ 0, ``round_half_up(gen["cost_usd"] × 100)``, stamped by
            :func:`record` whenever the gen block carries a cost — INCLUDING a
            $0 fake run. It is the ONLY number the cost dashboard sums (tiles,
            by-kind, by-identity, by-conversation all sum this one field, so
            they reconcile by construction). Absent ⇒ not a costed event.
  accuracy: ``measured`` | ``estimated`` (``canon.pricing.MEASURED`` /
            ``ESTIMATED``, compared by string) — required whenever ``costCents``
            is present; a costed event without one is a write-time error.
  genKind : OPEN value vocabulary (:data:`GEN_KINDS` is a data tuple for UI
            ordering, never a type): image · animation · video · code · audio ·
            text · tokens at launch, ``mesh`` joins at W2.2 as a VALUE. Assigned
            by the verb, not the backend.
  gen     : the existing keys (``llm_model``, ``prompt``, ``fallback``,
            ``image_model``, ``vlm_model``, ``music_model``, ``sfx_model``,
            ``renormalized``, ``reused_spec``) plus A6's cost keys
            (``backend``, ``model``, ``prompt_hash``, ``input_tokens``,
            ``output_tokens``, ``calls``, ``cost_usd``, ``cost_breakdown``).
            The inputs manifest (``refs`` / ``context`` / ``params`` /
            ``lineage``, P.8.3) is row W2.1's to POPULATE — the field is free,
            nothing here reads it as a schema.
  batchId : the approved plan every write under it belongs to (row P1-A4.5;
            master §3.0-B names the field — A6 owns the rest of the shape).
            ``record(batch_id=…)`` sets it explicitly; otherwise the batch
            bound around the call (``bind_batch``, the run manager's plan
            execution) is read, so every existing verb journals it without
            a signature change. Absent outside a plan. "Undo this plan"
            walks the events sharing one batchId in reverse.
  session : from A6 on this MEANS the conversation id (master §3.0-D's two id
            spaces). Play-session ids NEVER enter this journal.

Never a silent $0 (P.8.2): a paid backend with neither a reported cost nor a
price row still WRITES its event — hashes intact — with ``costCents`` absent
and ``detail.cost_error`` naming the backend (``record(cost_error=…)``). The
write is never lost (doctrine 6).

Cancelled runs (P.8.5) journal with ``detail.cancelled: true``, the partial
``costCents`` the backend reported, and NO ``after_hash`` — hash-less events
are invisible to :func:`artifact_versions`, the lineage builder and restore by
construction, and callers' change signals must ignore them too.

Reading: :func:`read_events` is the ONE reader that applies P.8.7's read-time
defaults (identity derived; every other A6 field left absent rather than
defaulted); :func:`list_events` filters it for ``canon journal list``, and
:func:`summarize_events` rolls it up for the cost dashboard.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

#: The identity every non-agent actor collapses to (P.8.2).
USER_IDENTITY = "user"

#: The prefix an agent actor (and therefore an agent identity) carries.
#: ``canon.agent.actors.agent_actor`` is the only CONSTRUCTOR; this module
#: only needs to recognise the shape, and must not import the agent layer.
AGENT_IDENTITY_PREFIX = "agent:"

#: Launch ``genKind`` values — DATA for UI ordering/labels, never a type
#: (P.8.8). ``mesh`` joins at W2.2 as a value; an unknown kind is accepted on
#: write and rendered on read as its own row.
GEN_KINDS: tuple[str, ...] = ("image", "animation", "video", "code", "audio", "text", "tokens")

#: The genKind that means "conversation tokens" — the dashboard's `tokens`
#: column; every other kind rolls into `generation` (P.8.6's category mapping).
TOKENS_GEN_KIND = "tokens"

#: The batch (approved plan id) bound around the current write, per thread.
_BATCH: contextvars.ContextVar[str | None] = contextvars.ContextVar("canon_batch", default=None)


@contextmanager
def bind_batch(batch_id: str | None) -> Iterator[None]:
    """Stamp ``batchId`` on every event recorded inside the block (row
    P1-A4.5: a plan's writes, whichever specialist's thread makes them —
    the caller binds it per thread; contexts do not cross threads)."""
    token = _BATCH.set(batch_id)
    try:
        yield
    finally:
        _BATCH.reset(token)


def current_batch() -> str | None:
    """The batch bound around this thread's current write, or ``None``."""
    return _BATCH.get()


def identity_for(actor: str | None) -> str:
    """``identity`` from ``actor`` — the pure function P.8.2 defines.

    ``agent:<conversation>/<specialist>`` passes through verbatim; EVERY other
    actor string (``user``, ``cradle``, ``cradle:user``, a future
    ``user:<uid>``) is a person and collapses to ``user``. Used at write time
    by :func:`record` and at read time by :func:`read_events` for pre-A6
    events — which is why it must stay a function of ``actor`` alone.
    """
    if isinstance(actor, str) and actor.startswith(AGENT_IDENTITY_PREFIX):
        return actor
    return USER_IDENTITY


def conversation_of(identity: str | None) -> str | None:
    """The conversation id inside an agent identity (``None`` for ``user``)."""
    if not isinstance(identity, str) or not identity.startswith(AGENT_IDENTITY_PREFIX):
        return None
    rest = identity[len(AGENT_IDENTITY_PREFIX) :]
    return (rest.split("/", 1)[0] or None) if rest else None


def specialist_of(identity: str | None) -> str | None:
    """The specialist inside an agent identity (``None`` when absent)."""
    if not isinstance(identity, str) or not identity.startswith(AGENT_IDENTITY_PREFIX):
        return None
    rest = identity[len(AGENT_IDENTITY_PREFIX) :]
    _, _, specialist = rest.partition("/")
    return specialist or None


def cost_cents(usd: float | int | str | None) -> int | None:
    """``round_half_up(usd × 100)`` as an int ≥ 0 — the dashboard's one number.

    Half-up (not banker's rounding) is the design contract (P.9 J1: the precise
    audit value stays in ``gen.cost_usd``); ``None``/unparseable ⇒ ``None`` =
    not a costed event. A negative figure is clamped to 0 — a ledger never owes.
    """
    if usd is None or isinstance(usd, bool):
        return None
    try:
        cents = Decimal(str(usd)) * 100
    except (ArithmeticError, ValueError, TypeError):
        return None
    return max(0, int(cents.quantize(Decimal("1"), rounding=ROUND_HALF_UP)))


def _canon_dir(pack: str | Path) -> Path:
    return Path(pack) / ".canon"


def _objects_dir(pack: str | Path) -> Path:
    return _canon_dir(pack) / "objects"


def journal_path(pack: str | Path) -> Path:
    return _canon_dir(pack) / "journal.jsonl"


def snapshot_bytes(pack: str | Path, data: bytes) -> str:
    """Store *data* in the content-addressed store; return ``sha256:<hex>``.

    Idempotent: identical content dedups to one object (the whole point of a CAS).
    """
    digest = hashlib.sha256(data).hexdigest()
    obj = _objects_dir(pack) / digest
    if not obj.exists():
        obj.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=obj.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, obj)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    return "sha256:" + digest


def snapshot_file(pack: str | Path, path: str | Path) -> str | None:
    """Snapshot a file's current bytes into the CAS. None if it doesn't exist."""
    p = Path(path)
    if not p.is_file():
        return None
    return snapshot_bytes(pack, p.read_bytes())


def read_object(pack: str | Path, content_hash: str) -> bytes:
    """Fetch a stored version's bytes by ``sha256:<hex>``."""
    digest = content_hash.split(":", 1)[1] if ":" in content_hash else content_hash
    return (_objects_dir(pack) / digest).read_bytes()


def already_recorded(pack: str | Path, artifact_id: str, after_hash: str) -> bool:
    """True if an (artifact_id, after_hash) event already exists.

    Lets ``baseline`` be called repeatedly (e.g. every time cradle opens a level)
    without duplicating generate events.
    """
    jp = journal_path(pack)
    if not jp.is_file():
        return False
    with jp.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("artifact_id") == artifact_id and e.get("after_hash") == after_hash:
                return True
    return False


def append_event(pack: str | Path, event: dict) -> dict:
    """Append one event (schema + ts stamped) to the journal."""
    jp = journal_path(pack)
    jp.parent.mkdir(parents=True, exist_ok=True)
    full = {"schema": SCHEMA_VERSION, "ts": datetime.now(UTC).isoformat(), **event}
    with jp.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(full, default=str) + "\n")
    return full


def all_events(pack: str | Path) -> list[dict]:
    """Every parsed journal event, in append order. The lineage builder
    reads the WHOLE journal once — cross-artifact connections are found by
    shared content hashes, which no per-artifact filter can see."""
    jp = journal_path(pack)
    out: list[dict] = []
    if not jp.is_file():
        return out
    with jp.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def artifact_versions(pack: str | Path, artifact_id: str) -> list[dict]:
    """The ordered version chain for one artifact, from the journal.

    Each entry is a compact view of the event that produced a version:
    ``{ts, op, source, actor, hash}`` (hash = the resulting ``after_hash``).
    This is what a user-facing version-history / restore picker reads; the raw
    bytes of any entry are fetched via :func:`read_object`. Deleted versions
    stay in the chain — the object store is never pruned by user actions.
    """
    jp = journal_path(pack)
    out: list[dict] = []
    if not jp.is_file():
        return out
    with jp.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("artifact_id") != artifact_id or not e.get("after_hash"):
                continue
            out.append(
                {
                    "ts": e.get("ts"),
                    "op": e.get("op"),
                    "source": e.get("source"),
                    "actor": e.get("actor"),
                    "hash": e["after_hash"],
                }
            )
    return out


def record(
    pack: str | Path,
    *,
    artifact_id: str,
    op: str,
    source: str,
    actor: str = "user",
    session: str | None = None,
    detail: Any = None,
    before_hash: str | None = None,
    after_hash: str | None = None,
    gen: dict | None = None,
    batch_id: str | None = None,
    gen_kind: str | None = None,
    accuracy: str | None = None,
    cost_error: str | None = None,
) -> dict:
    """Assemble and append a provenance event (see module docstring for shape).

    ``batch_id`` (additive, row P1-A4.5) lands as ``batchId``; ``None``
    falls back to the batch bound by ``bind_batch`` (the plan a write
    runs under), and no batch at all writes no field.

    Row P1-A6 (master §3.0-B / P.8.2) adds — all additive, ``schema`` unchanged:

    - ``identity``: never a parameter. It is computed here from ``actor``
      (:func:`identity_for`) so no verb ever needs an ``--identity`` flag and
      no caller can disagree with the journal about who acted.
    - ``costCents``: derived from ``gen["cost_usd"]`` when the gen block
      carries one — the single field every dashboard table sums.
    - ``accuracy``: ``measured`` | ``estimated`` (plain strings from
      ``canon.pricing``). REQUIRED alongside a cost: a costed event without
      one raises ``ValueError`` rather than shipping an unlabelled figure.
    - ``gen_kind``: lands as ``genKind`` — an open string; unknown values are
      written and rendered without a schema change.
    - ``cost_error``: the never-lose-the-write escape hatch. A paid backend
      with neither a reported cost nor a price row passes its reason here: the
      event is written with its hashes intact, ``costCents`` ABSENT, and
      ``detail.cost_error`` set, so the dashboard renders an unpriced row
      instead of a confident (and wrong) $0.
    """
    ev: dict[str, Any] = {
        "artifact_id": artifact_id,
        "op": op,
        "source": source,
        "actor": actor,
        "identity": identity_for(actor),
    }
    if session:
        ev["session"] = session
    if cost_error:
        # detail is Any by contract; a non-dict detail keeps its place under
        # "detail" and the error rides beside it rather than being dropped.
        detail = {**detail, "cost_error": cost_error} if isinstance(detail, dict) else {"cost_error": cost_error}
    if detail is not None:
        ev["detail"] = detail
    if before_hash:
        ev["before_hash"] = before_hash
    if after_hash:
        ev["after_hash"] = after_hash
    if gen:
        ev["gen"] = gen
    if gen_kind:
        ev["genKind"] = str(gen_kind)
    cents = None if cost_error else cost_cents((gen or {}).get("cost_usd"))
    if cents is not None:
        if not accuracy:
            raise ValueError(
                f"{artifact_id}: a costed journal event needs an accuracy flag "
                "('measured' | 'estimated' — canon.pricing.MEASURED / ESTIMATED); "
                "an unlabelled cost is exactly the silent-$0 failure P.8.2 forbids"
            )
        ev["costCents"] = cents
        ev["accuracy"] = str(accuracy)
    batch = batch_id if batch_id is not None else current_batch()
    if batch:
        ev["batchId"] = batch
    return append_event(pack, ev)


# ---------------------------------------------------------------------------
# The gen block's cost keys — built once, used by every costed verb (P.8.3)
# ---------------------------------------------------------------------------


def combine_accuracy(*flags: str | None) -> str:
    """``estimated`` if ANY contributing component was priced from the table
    without a provider-reported quantity, else ``measured`` (P.8.2's mixed-row
    rule). Empty/None flags are ignored — a category that did not run cannot
    make the row less accurate. The two values are plain strings compared by
    value, never an Enum (P.8.8)."""
    from canon import pricing

    seen = [f for f in flags if f]
    return pricing.ESTIMATED if any(f == pricing.ESTIMATED for f in seen) else pricing.MEASURED


def backend_accuracy(backend: Any) -> str | None:
    """A backend object's ``last_cost_accuracy`` (row P0-7 put one on every
    backend), or ``None`` when there is no backend for that category."""
    if backend is None:
        return None
    from canon import pricing

    return str(getattr(backend, "last_cost_accuracy", pricing.MEASURED) or pricing.MEASURED)


def gen_cost(
    cost: dict | None,
    *,
    accuracy: str,
    backend: str | None = None,
    model: str | None = None,
    prompt_hash: str | None = None,
    component_accuracy: dict[str, str] | None = None,
) -> dict:
    """A6's cost keys for a ``gen`` block, from an op's own ``_cost_block``.

    ``cost`` is the shape the platformer ops already return
    (``{usd, llm_usd, image_usd, audio_usd, input_tokens, output_tokens,
    calls, backend}``); this maps it onto P.8.3's names WITHOUT touching the
    existing keys (``llm_model`` / ``prompt`` / … stay exactly as they are, so
    ``LineageNode.gen`` and cradle's LineagePanel keep reading them).
    ``component_accuracy`` is the per-component detail the top-level flag
    summarises. Returns ``{}`` for no cost block at all, so a verb can splat it
    unconditionally.
    """
    if not cost:
        return {}
    block: dict[str, Any] = {
        "backend": backend if backend is not None else (cost.get("backend") or ""),
        "cost_usd": round(float(cost.get("usd") or 0.0), 6),
        "cost_breakdown": {
            "llm_usd": round(float(cost.get("llm_usd") or 0.0), 6),
            "image_usd": round(float(cost.get("image_usd") or 0.0), 6),
            "audio_usd": round(float(cost.get("audio_usd") or 0.0), 6),
            "accuracy": dict(component_accuracy or {}),
        },
        "input_tokens": int(cost.get("input_tokens") or 0),
        "output_tokens": int(cost.get("output_tokens") or 0),
        "calls": int(cost.get("calls") or 0),
    }
    if model:
        block["model"] = str(model)
    if prompt_hash:
        block["prompt_hash"] = prompt_hash
    block["cost_accuracy"] = accuracy  # audit copy; the EVENT's flag is the one summed
    return block


def token_gen_block(backend: str, model: str | None, usage: dict) -> dict | None:
    """Price ONE conversation turn's measured token usage (row P1-A6, P.8.6).

    Lives here, beside the rest of the journal's cost stamping, so the agent
    service carries no price arithmetic of its own — every figure comes from
    ``canon.pricing``, the product's only price source (master §3.0-C). Cache
    reads bill at their own rate and cache writes at the input rate, which is
    why ``Usage`` keeps all four counts apart.

    Returns ``{"gen", "accuracy", "cost_error"}`` ready to splat into
    :func:`record`, or ``None`` when the turn burned no tokens at all (not a
    cost row). A PAID chat backend whose model has no price row comes back with
    ``cost_error`` set and no cost — the never-a-silent-$0 rule; a fake backend
    really did cost nothing, and ``$0 measured`` is the honest answer there.
    """
    from canon import pricing

    counts = {
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "cache_read_input_tokens": int(usage.get("cache_read_input_tokens") or 0),
        "cache_creation_input_tokens": int(usage.get("cache_creation_input_tokens") or 0),
    }
    if not any(counts.values()):
        return None
    warnings: list[str] = []
    row = pricing.price_for("llm", model, warnings) if model else None
    unpriced = row is None and pricing.is_paid("llm", backend)
    rates = pricing.per_token(row) if row is not None else {"input": 0.0, "output": 0.0}
    cache_rate = float((row or {}).get("cache_read_per_1m", 0.0)) / 1_000_000
    total = round(
        (counts["input_tokens"] + counts["cache_creation_input_tokens"]) * rates["input"]
        + counts["output_tokens"] * rates["output"]
        + counts["cache_read_input_tokens"] * cache_rate,
        6,
    )
    return {
        "gen": {"backend": backend, "model": model or "", "calls": 1, **counts, "cost_usd": total},
        "accuracy": pricing.ESTIMATED if unpriced else pricing.MEASURED,
        "cost_error": (
            f"{backend}: no llm price row for {model!r} in canon.pricing" if unpriced else None
        ),
    }


def prompt_hash(prompt: str | None) -> str | None:
    """``sha256:<hex>`` of a prompt string (P.8.3's ``gen.prompt_hash``)."""
    if not prompt:
        return None
    return "sha256:" + hashlib.sha256(prompt.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# The ONE reader — P.8.7's read-time defaults (never by rewriting the file)
# ---------------------------------------------------------------------------


def read_events(pack: str | Path) -> list[dict]:
    """Every journal event with P.8.7's read-time defaults applied.

    The ONLY default is ``identity`` ← :func:`identity_for` for events written
    before row A6 stamped it. Every other A6 field stays ABSENT when absent —
    deliberately: ``costCents`` absent means "not a cost row" (History yes,
    dashboard no), ``accuracy`` absent is never defaulted to ``measured``,
    ``genKind`` absent is not a generation row even when ``op`` says
    generate/regenerate, ``batchId`` absent is a single entry, ``session``
    absent is the editor door. Nothing is rewritten on disk — this is the read
    side, and every other reader in the product goes through it.
    """
    out: list[dict] = []
    for event in all_events(pack):
        if not isinstance(event, dict):
            continue
        if not event.get("identity"):
            event = {**event, "identity": identity_for(event.get("actor"))}
        out.append(event)
    return out


def list_events(
    pack: str | Path,
    *,
    identity: str | None = None,
    session: str | None = None,
    gen_kind: str | None = None,
    since: str | None = None,
    artifact_prefix: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    """``canon journal list``'s filter set (P.8.7), over :func:`read_events`.

    Every filter is an exact string match except ``since`` (ISO-8601, keeps
    events at or after it — plain string comparison, which is correct for the
    ISO timestamps this journal writes) and ``artifact_prefix`` (a
    ``startswith`` over ``artifact_id``, so ``level:s1/`` scopes a stage and
    ``conversation:`` scopes token rows). ``limit`` keeps the LAST N events —
    the newest are the ones a dashboard or a History pane wants.
    """
    events = read_events(pack)
    if identity:
        events = [e for e in events if e.get("identity") == identity]
    if session:
        events = [e for e in events if e.get("session") == session]
    if gen_kind:
        events = [e for e in events if e.get("genKind") == gen_kind]
    if since:
        events = [e for e in events if str(e.get("ts") or "") >= since]
    if artifact_prefix:
        events = [e for e in events if str(e.get("artifact_id") or "").startswith(artifact_prefix)]
    if limit is not None and limit >= 0:
        events = events[-limit:] if limit else []
    return events


# ---------------------------------------------------------------------------
# The dashboard roll-up — every figure sums costCents, so tables reconcile
# ---------------------------------------------------------------------------


def _cents(event: dict) -> int | None:
    value = event.get("costCents")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def summarize_events(events: list[dict], *, today: str | None = None) -> dict:
    """The cost dashboard's server-side roll-up (README §12, board 06).

    EVERY figure is a sum of ``costCents`` over the same event list, so the
    tiles, the you/agent split, by-kind, by-identity and by-conversation
    reconcile by construction — the tables cannot disagree because there is
    only one field to disagree about. Events without ``costCents`` are counted
    nowhere (an unconfirmed estimate never spent anything); events carrying
    ``detail.cost_error`` are reported as ``unpricedRuns`` so the gap is
    visible rather than silently $0.

    ``today`` is an ISO date (``YYYY-MM-DD``, default: the current UTC date,
    the same day the ``ts`` strings are stamped in) —
    the fourth tile. Kinds and identities are grouped by VALUE: an unknown
    ``genKind`` becomes its own row, never a dropped one.
    """
    # The ``ts`` strings are UTC ISO (``append_event``), so the day this
    # buckets on must be the UTC day too — a LOCAL date would put a
    # UTC-evening event in yesterday's tile for every user west of Greenwich,
    # and disagree with cradle's ``summarizeJournal`` (which uses the UTC day).
    today = today or datetime.now(UTC).date().isoformat()
    kinds: dict[str, dict] = {}
    identities: dict[str, dict] = {}
    conversations: dict[str, dict] = {}
    totals = {"totalCents": 0, "generationCents": 0, "tokensCents": 0, "todayCents": 0}
    split = {"youCents": 0, "agentCents": 0}
    accuracy_totals: dict[str, int] = {}
    unpriced = 0
    costed = 0

    for event in events:
        detail = event.get("detail")
        if isinstance(detail, dict) and detail.get("cost_error"):
            unpriced += 1
        cents = _cents(event)
        if cents is None:
            continue
        costed += 1
        identity = str(event.get("identity") or identity_for(event.get("actor")))
        is_agent = identity.startswith(AGENT_IDENTITY_PREFIX)
        kind = str(event.get("genKind") or "")
        is_tokens = kind == TOKENS_GEN_KIND
        flag = str(event.get("accuracy") or "")
        gen = event.get("gen") if isinstance(event.get("gen"), dict) else {}

        totals["totalCents"] += cents
        if is_tokens:
            totals["tokensCents"] += cents
        else:
            totals["generationCents"] += cents
            split["agentCents" if is_agent else "youCents"] += cents
        if str(event.get("ts") or "")[:10] == today:
            totals["todayCents"] += cents
        if flag:
            accuracy_totals[flag] = accuracy_totals.get(flag, 0) + cents

        row = kinds.setdefault(
            kind or "unknown",
            {"genKind": kind or "unknown", "runs": 0, "youCents": 0, "agentCents": 0,
             "totalCents": 0, "backends": {}},
        )
        row["runs"] += 1
        row["totalCents"] += cents
        row["agentCents" if is_agent else "youCents"] += cents
        pair = f"{gen.get('backend') or ''}·{gen.get('model') or ''}"
        row["backends"][pair] = row["backends"].get(pair, 0) + 1

        who = identities.setdefault(
            identity,
            {"identity": identity, "kind": "agent" if is_agent else "user",
             "conversation": conversation_of(identity), "specialist": specialist_of(identity),
             "tokensCents": 0, "generationCents": 0, "totalCents": 0, "runs": 0},
        )
        who["runs"] += 1
        who["totalCents"] += cents
        who["tokensCents" if is_tokens else "generationCents"] += cents

        conversation = event.get("session") or conversation_of(identity)
        if conversation:
            conv = conversations.setdefault(
                str(conversation),
                {"session": str(conversation), "tokensCents": 0, "generationCents": 0,
                 "totalCents": 0, "runs": 0},
            )
            conv["runs"] += 1
            conv["totalCents"] += cents
            conv["tokensCents" if is_tokens else "generationCents"] += cents

    by_kind = []
    for row in sorted(kinds.values(), key=lambda r: (-r["totalCents"], r["genKind"])):
        pairs = sorted(row.pop("backends").items(), key=lambda kv: (-kv[1], kv[0]))
        backend, _, model = (pairs[0][0] if pairs else "·").partition("·")
        by_kind.append({**row, "backend": backend, "model": model, "variants": max(0, len(pairs) - 1)})

    return {
        **totals,
        **split,
        "costedEvents": costed,
        "eventCount": len(events),
        "unpricedRuns": unpriced,
        "accuracyCents": accuracy_totals,
        "byKind": by_kind,
        "byIdentity": sorted(identities.values(), key=lambda r: (-r["totalCents"], r["identity"])),
        "byConversation": sorted(conversations.values(), key=lambda r: (-r["totalCents"], r["session"])),
        "today": today,
    }
