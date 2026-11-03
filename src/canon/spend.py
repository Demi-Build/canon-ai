"""Per-project spend ledger — an append-only record of what each paid op
cradle fired actually cost.

Lives beside the provenance journal at ``<pack>/.canon/spend.jsonl`` but is a
separate concern: the journal records artifact MUTATIONS (training signal); the
spend ledger records the DOLLARS a project has consumed, one line per paid op
cradle triggered. Cradle shells out to ``canon spend record`` after each op (it
never writes pack files directly — locked doctrine) and reads the running total
via ``canon spend list`` for its cost dashboard.

Entry shape (``schema="cradle-spend/v1"``)::

    {schema, ts, op, scope, level_id?, backends, estimate?:{best,worst},
     actual_usd?, tokens?:{input,output,calls},
     actor?, identity?, category?, accuracy?, genKind?, session?, batchId?,
     journal_ref?}

``actual_usd`` is the MEASURED cost from the op result (real returned tokens ×
price, or provider-reported asset cost); ``estimate`` is the pre-run forecast
shown at the confirm. A full New-Project run records its
``generation_stats.total_cost_usd`` as ``actual_usd``. Fake/`$0` ops record 0 —
they still belong in the ledger so the dashboard shows the whole history.

Row P1-A6 (ASSUMPTION-8, P0 paper P.8.7) — **the ledger's role changed, its
shape only grew**. The JOURNAL is now authoritative for the cost dashboard
(one number: Σ ``costCents`` over journal events); this file becomes a
**derived compat index**, still written best-effort, read for pre-A6 history
and for the ``world new`` create run until it journals (P.9 J8). The schema
string is unchanged, :func:`summarize` is unchanged, and the new keys are all
OPTIONAL — ``record_spend`` passes the caller's dict through verbatim
("cradle owns its shape"), so a row simply carries more when the writer knows
more:

- ``actor`` / ``identity`` — who spent it (``identity`` is
  ``canon.provenance.identity_for(actor)``; the ledger never invents one).
- ``category`` — ``tokens`` | ``generation`` (:data:`CATEGORIES`, DATA and
  open, never a Literal — P.8.8).
- ``accuracy`` — ``measured`` | ``estimated``, the same flag the journal event
  carries, so a spend-only row is never mistaken for a measured figure.
- ``genKind`` · ``session`` (= the conversation id) · ``batchId`` — the lanes.
- ``journal_ref`` — the ``ts`` of the op's FIRST journal event. Its presence
  is what tells the reconciler this row is already counted in the journal:
  total = Σ ``costCents`` (journal) + Σ ``actual_usd`` over rows WITHOUT a
  ``journal_ref``. No row is ever in both sets.

:func:`spend_row_from_journal` builds exactly that row from a journal event so
the two files cannot drift apart in the fields that matter.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = "cradle-spend/v1"
LEDGER_NAME = "spend.jsonl"

#: The two lanes a row can belong to — DATA (a tuple for labels/ordering),
#: never a type: a third lane is a value, not a schema change (P.8.8).
CATEGORIES: tuple[str, ...] = ("tokens", "generation")


def _ledger_path(pack_dir: str | Path) -> Path:
    return Path(pack_dir) / ".canon" / LEDGER_NAME


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def record_spend(pack_dir: str | Path, entry: dict, *, ts: str | None = None) -> dict:
    """Append one spend entry to the pack's ledger. Stamps ``schema`` + ``ts``
    (UTC ISO 8601) when absent; the entry is otherwise passed through verbatim
    so cradle owns its shape. Returns the stored line."""
    path = _ledger_path(pack_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = {"schema": SCHEMA, "ts": ts or entry.get("ts") or _now_iso(), **entry}
    line["schema"] = SCHEMA  # authoritative — never let the caller override it
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, default=str) + "\n")
    return line


def spend_row_from_journal(event: dict, **extra: Any) -> dict:
    """The derived compat row for one costed journal event (P.8.7).

    Carries the lane fields verbatim off the event and stamps ``journal_ref``
    with the event's ``ts`` — the marker that says "already counted in the
    journal, do NOT sum this row again". ``actual_usd`` comes from the event's
    ``gen.cost_usd`` (the precise audit value; ``costCents`` is the rounded
    figure the dashboard sums). ``extra`` is the caller's own shape (``op`` /
    ``scope`` / ``level_id`` / ``backends`` / ``estimate``), which wins — the
    ledger stays cradle's to shape.
    """
    from canon.provenance import TOKENS_GEN_KIND, identity_for

    gen = event.get("gen") if isinstance(event.get("gen"), dict) else {}
    gen_kind = event.get("genKind")
    row: dict[str, Any] = {
        "op": event.get("op") or "",
        "actor": event.get("actor"),
        "identity": event.get("identity") or identity_for(event.get("actor")),
        "category": "tokens" if gen_kind == TOKENS_GEN_KIND else "generation",
        "actual_usd": float(gen.get("cost_usd") or 0.0),
        "journal_ref": event.get("ts"),
    }
    for key, value in (
        ("accuracy", event.get("accuracy")),
        ("genKind", gen_kind),
        ("session", event.get("session")),
        ("batchId", event.get("batchId")),
    ):
        if value:
            row[key] = value
    tokens = {
        "input": int(gen.get("input_tokens") or 0),
        "output": int(gen.get("output_tokens") or 0),
        "calls": int(gen.get("calls") or 0),
    }
    if any(tokens.values()):
        row["tokens"] = tokens
    row.update({k: v for k, v in extra.items() if v is not None})
    return row


def read_spend(pack_dir: str | Path) -> list[dict]:
    """All ledger entries in write order (empty list if none). Malformed lines
    are skipped, not fatal — the ledger is observability, not run state."""
    path = _ledger_path(pack_dir)
    if not path.is_file():
        return []
    out: list[dict] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):  # a valid-JSON scalar/array is not an entry
            out.append(obj)
    return out


def _num(entry: dict, *keys: str) -> float:
    """Dig a float out of a nested entry by key path; 0.0 if missing."""
    cur: Any = entry
    for k in keys:
        if not isinstance(cur, dict):
            return 0.0
        cur = cur.get(k)
    try:
        return float(cur)
    except (TypeError, ValueError):
        return 0.0


def summarize(pack_dir: str | Path) -> dict:
    """Roll the ledger up for the dashboard: ``total_actual_usd`` (measured
    spend across all ops) and ``total_estimate_usd`` (the sum of every op's
    pre-run forecast) as an independent estimated-vs-actual pair, plus a per-op
    breakdown carrying both figures so the dashboard can show forecast beside
    measured. ``actual`` is an entry's measured ``actual_usd`` (0 when absent,
    e.g. a still-running or fake op); ``estimate`` is its forecast ``estimate.best``.
    The two totals are separate sums — an op contributes to actual AND estimate,
    which is comparison, not double-counting."""
    entries = read_spend(pack_dir)
    by_op: dict[str, dict] = {}
    total_actual = 0.0
    total_estimate = 0.0
    for e in entries:
        op = str(e.get("op") or e.get("scope") or "unknown")
        has_actual = "actual_usd" in e and e.get("actual_usd") is not None
        actual = _num(e, "actual_usd") if has_actual else 0.0
        est = _num(e, "estimate", "best")
        agg = by_op.setdefault(
            op, {"count": 0, "actual_usd": 0.0, "estimate_usd": 0.0}
        )
        agg["count"] += 1
        agg["actual_usd"] += actual
        agg["estimate_usd"] += est
        total_actual += actual
        total_estimate += est
    return {
        "count": len(entries),
        "total_actual_usd": round(total_actual, 6),
        "total_estimate_usd": round(total_estimate, 6),
        "by_op": {
            op: {
                "count": a["count"],
                "actual_usd": round(a["actual_usd"], 6),
                "estimate_usd": round(a["estimate_usd"], 6),
            }
            for op, a in sorted(by_op.items())
        },
        "entries": entries,
    }
