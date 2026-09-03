"""The $-tier paid tools (row P1-A6; master §3.1 stage 5; Phase 1 §4.B/§4.C).

``register_paid_tools(registry, pack_dir, *, actor_for)`` registers Phase 1
§4's ``$ confirm`` rows into row A2's ``ToolRegistry``, exactly the way
``tools_write`` registers the ask-tier ones: tier is a plain string
(``"paid"``), each tool is a THIN in-process wrapper over the canon verb that
already exists (D3: imports, not subprocesses), and nothing here writes a byte
or prices anything itself — the verbs carry doctrine 1's write discipline and
``canon.pricing`` is the one price source (master §3.0-C).

What each tool extends:

- ``generate_asset`` / ``animate_asset`` → ``canon.packs.platformer.ops``
  (``canon asset generate`` / ``asset animate``).
- ``generate_music`` → ``ops.generate_level_music`` (``canon level music``).
- ``generate_layout`` → ``ops.regenerate_terrain`` (``level regenerate``);
  ``improve_layout`` → ``ops.improve_terrain``; ``place_enemies`` /
  ``place_items`` → the same-named ops; ``generate_level`` →
  ``ops.generate_level`` (terrain + placements as one chain).
- ``complete_row`` → ``canon.db_ops.complete_db_row`` through the pack's own
  builder (``canon db complete``).
- ``create_project`` → **row P1-A9**: the ONE create verb, ``canon world new``
  (P0-10's registry dispatch), spawned into P0-10's project store. It is the
  same verb cradle's ``new_project`` JobQueue command spawns — one pipeline,
  two launchers (headless/CLI here, the app's worker there), never a second
  create. What it reads for "what is creatable" is ``canon.packs``'
  ``pack_templates()`` (``canon pack templates``), so a third template is an
  entry, not a branch. See :func:`_create_project`.

**Two things this row adds to the gate, both data, both additive:**

*The estimate rides the permission payload.* Every tool registers an estimator
on the engine (``PermissionEngine.estimate_with``), so the chip renders
``Accept · spend up to $X`` with the backend and model named — before the tool
body runs, which is the only moment at which that is still a choice. The
estimator is canon's own (the pack type's ``estimate_cradle``, row P0-7); a
paid call it has no scope for still gets a shape-complete zero-range payload
naming the backend, so the card renders its honest "— not estimated" state
instead of degrading to a chip with no price block at all.
**No estimate is not $0** (doctrine 3).

*Free never spend-confirms.* Tiers are data, and one tool is $-tier or
ask-tier depending on the backends the CALL selects: an all-fake/none
selection bills nothing, so it gets the ordinary ask chip showing "$0" instead
of the accent-outlined spend card (master §8 A-5, doctrine 3). That is
``PermissionEngine.tier_with`` + :func:`paid_tier_for`; ``paid`` itself is
still never Always-allowable, in any mode, and this row does not touch that.

Attribution and cost: every verb is called with ``actor=<agent actor>`` and
``session=<conversation>`` from ``actor_for()`` (I6 — the actor string is
``agent_actor``'s alone), inside ``tools_write.journal_window`` so the events
belong to THIS call. The verbs journal their own money now (row A6's
``provenance.record`` fields), and the result carries the verb's ``cost``
block verbatim plus the ``spend`` compat row that was written beside it.

Deliberately absent, by row ownership: budget caps (master §8 A-2 forbids
them — warnings only, and none are built here), the gen-inputs manifest
population (W2.1), ``edit_project_code`` (A7.5), the wizard and the Rust
JobQueue command (P0-10), pack-less conversation storage (the service's own
row — a headless ``create_project`` therefore runs from a conversation whose
session pack is some OTHER pack, and writes its ledgers into the pack it
CREATED, never the open one).
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from canon.agent.actors import CallContext
from canon.agent.registry import Tool, ToolRegistry
from canon.agent.tools_read import compact, validate_input
from canon.agent.tools_write import compact_events, journal_window
from canon.llm.chat import ToolSpec

log = logging.getLogger(__name__)

#: The tier these tools register under — always ask, never Always-allowable.
PAID_TIER = "paid"

#: The tier a call whose every selected backend is unpaid falls back to
#: (doctrine 3 / master §8 A-5: an ordinary chip showing "$0").
FREE_TIER = "ask"

#: Registration order = the order every request offers them, after the writes.
PAID_TOOL_NAMES: tuple[str, ...] = (
    "generate_layout",
    "improve_layout",
    "place_enemies",
    "place_items",
    "generate_level",
    "generate_asset",
    "animate_asset",
    "generate_music",
    "complete_row",
    "create_project",
)

#: tool → ``(category, input field, the body's OWN default)`` per backend the
#: call selects. Used ONLY to decide whether the call spends real money
#: (``canon.pricing.is_paid``); the price itself always comes from the
#: estimator.
#:
#: The default matters as much as the field. The model may omit an optional
#: ``llm_backend`` — none of the schemas require it — and the tool body then
#: runs on the literal beside it here (``i.get("llm_backend", "fake")``). Read
#: without the default, such a call classified PAID and raised the accent
#: spend-confirm card for a run that bills nothing, which is exactly what
#: "free never spend-confirms" forbids (master §8 A-5, doctrine 3). ``None``
#: means the body has NO default (it raises, or refuses) — those keep the
#: fail-closed "no backend named → paid" rule, because guessing free is the
#: one guess that costs the user money.
_BACKEND_FIELDS: dict[str, tuple[tuple[str, str, str | None], ...]] = {
    "generate_layout": (("llm", "llm_backend", "fake"),),
    "improve_layout": (("llm", "llm_backend", "fake"),),
    "place_enemies": (("llm", "llm_backend", "fake"),),
    "place_items": (("llm", "llm_backend", "fake"),),
    "generate_level": (("llm", "llm_backend", "fake"),),
    "generate_asset": (
        ("image", "image_backend", None), ("music", "music_backend", None),
        ("sfx", "sfx_backend", None),
    ),
    "animate_asset": (("image", "image_backend", None), ("vlm", "vlm_backend", None)),
    "generate_music": (("music", "music_backend", "fake"),),
    "complete_row": (("llm", "llm_backend", "fake"),),
    # Row A9 wired the body, so the defaults beside each field are now the
    # ones the call really runs on: ``canon world new``'s own ($0 preview —
    # canned text, placeholder art, no audio). Reading them as "no default →
    # paid" would raise the accent spend card for a create that bills nothing,
    # which is what "free never spend-confirms" forbids (master §8 A-5).
    "create_project": (
        ("llm", "llm_backend", "fake"), ("image", "image_backend", "fake"),
        ("music", "music_backend", "none"), ("sfx", "sfx_backend", "none"),
        ("vlm", "vlm_backend", "none"),
    ),
}

#: The rows that own the create flow (P0-10) and the start-page create
#: conversation (A9) — still named in every refusal this tool raises, so a
#: failure says whose contract it belongs to (doctrine 4).
CREATE_PROJECT_ROWS = "P0-10 (create flow + project store) / P1-A9 (the start-page create conversation)"

#: Where a project CREATED by the agent lands when the call names no
#: ``parent_dir`` — the SAME rule cradle's Rust ``project_store_root`` follows
#: (Phase 0 §8.4, row P0-10): the env override first, then ``~/CradleProjects``.
#: Kept as data so a headless create and the app agree on one directory.
PROJECT_STORE_ENV = "CRADLE_PROJECTS_DIR"
PROJECT_STORE_DIRNAME = "CradleProjects"


# ---------------------------------------------------------------------------
# What is creatable — `pack templates`, never a list in this file (row A9)
# ---------------------------------------------------------------------------


def creatable_templates() -> list[dict[str, Any]]:
    """``canon pack templates`` — the P.4.4 wizard metadata for every INSTALLED
    template, which is exactly what ``create_project`` can create.

    The same call the wizard's cards render from (row P0-10), so the agent and
    the modal can never disagree about what exists, and a third template shows
    up in both with no code change here (the M0-readiness rule: a template id
    is an entry, never a union). Never raises — a registry that will not import
    yields an empty list, and the body's refusal then names the problem.
    """
    try:
        from canon.packs import pack_templates

        return list(pack_templates())
    except Exception:  # noqa: BLE001 — "nothing is creatable" is a refusal, not a crash
        log.exception("pack templates could not be read; create_project has nothing to offer")
        return []


def _chosen_template(template: Any) -> Any | None:
    """The ``PackSpec`` a call selected, or ``None`` when it named one that is
    not installed. An ABSENT/blank template means the first registered seed —
    ``PACKS`` insertion order is the wizard's card order (P0-10), so "the
    default template" is one fact in one place."""
    from canon.packs import PACKS

    if not PACKS:
        return None
    if not (isinstance(template, str) and template.strip()):
        return next(iter(PACKS.values()))
    return PACKS.get(template.strip())


def effective_counts(tool_input: dict) -> dict[str, int]:
    """The counts a create call really runs at: the chosen template's own
    defaults (``PackSpec.counts`` = ``pack templates``' ``defaults``) with the
    call's ``counts`` object laid over them.

    Keys are the TEMPLATE's count names, so the estimate, the plan card and
    the runner all quote one vocabulary. A key the template does not declare is
    kept here and refused BY NAME downstream (``world new``'s
    ``_runner_argv`` warning), never silently dropped (doctrine 4).
    """
    spec = _chosen_template(tool_input.get("template"))
    counts: dict[str, int] = dict(getattr(spec, "counts", {}) or {})
    given = tool_input.get("counts")
    if isinstance(given, dict):
        for key, value in given.items():
            try:
                counts[str(key)] = int(value)
            except (TypeError, ValueError):
                log.debug("create_project: ignoring non-numeric count %r=%r", key, value)
    return counts


# ---------------------------------------------------------------------------
# Tier: free never spend-confirms
# ---------------------------------------------------------------------------


def selected_backends(name: str, tool_input: dict) -> dict[str, str]:
    """The ``{category: backend_id}`` this call will actually RUN on, per
    :data:`_BACKEND_FIELDS`.

    An omitted field falls back to the tool body's own default when it has one,
    so the tier and the estimate both describe the call that is really about to
    happen. A category whose body has no default stays absent — the caller then
    treats the call as paid (fail-closed).
    """
    out: dict[str, str] = {}
    for kind, field, default in _BACKEND_FIELDS.get(name, ()):
        value = tool_input.get(field)
        if not (isinstance(value, str) and value):
            value = default
        if isinstance(value, str) and value:
            out[kind] = value
    return out


def spends_money(name: str, tool_input: dict) -> bool:
    """Whether ANY category this call selected actually bills.

    ``canon.pricing.is_paid`` is the authority (a category with no default
    price row — ``fake`` / ``none`` / ``local`` / ``""`` — costs nothing). A
    call that resolves to NO backend at all is treated as PAID: the body has no
    default to read (``generate_asset`` / ``animate_asset`` raise without one,
    ``create_project`` refuses), and guessing free would skip the spend confirm.
    """
    from canon import pricing

    chosen = selected_backends(name, tool_input)
    if not chosen:
        return True
    return any(pricing.is_paid(kind, backend) for kind, backend in chosen.items())


def paid_tier_for(name: str) -> Callable[[dict], str]:
    """The per-input tier resolver for ``name`` (``PermissionEngine.tier_with``)."""

    def resolve(tool_input: dict) -> str:
        return PAID_TIER if spends_money(name, tool_input) else FREE_TIER

    resolve.__name__ = f"tier_for_{name}"
    return resolve


# ---------------------------------------------------------------------------
# Estimates: the price inside the Accept button
# ---------------------------------------------------------------------------

#: tool → the estimator scope its call prices under (``estimate_cradle``'s
#: vocabulary). A tool with no scope here has no PRICE — the card says the
#: price is unknown rather than a confident $0 (see :func:`estimate_payload`).
#:
#: ``generate_asset`` and ``complete_row`` are the two absentees, and the gap is
#: canon-side, not this row's: ``estimate_cradle`` prices
#: ``world|music|animate|layout|enemies|items|generate`` and has no per-sprite
#: or per-row scope. The single-sprite estimator belongs to the row that owns
#: ``packs/platformer/estimate.py``; until it lands, both tools open the paid
#: card in its honest "— not estimated" state rather than with no price block
#: at all.
_ESTIMATE_SCOPE: dict[str, str] = {
    "generate_layout": "layout",
    "improve_layout": "layout",
    "place_enemies": "enemies",
    "place_items": "items",
    "generate_level": "generate",
    "animate_asset": "animate",
    "generate_music": "music",
    "create_project": "world",
}

#: tool → the unit an UNPRICED paid call names on its card, copy only (the
#: priced ones use :data:`_UNIT_LABEL` under their scope).
_UNPRICED_UNIT_LABEL: dict[str, str] = {
    "generate_asset": "one asset",
    "complete_row": "one row",
}

#: scope → the unit the card names ("work: 3 states"), copy only.
_UNIT_LABEL: dict[str, str] = {
    "layout": "level layout",
    "enemies": "enemy placements",
    "items": "item placements",
    "generate": "a whole level",
    "animate": "animation states",
    "music": "one track",
    "world": "a whole project",
}


def _estimator_of(pack_type: str) -> Callable[..., dict] | None:
    """``canon.packs.<pack_type>.estimate.estimate_cradle``, or ``None``."""
    from importlib import import_module

    try:
        return getattr(import_module(f"canon.packs.{pack_type}.estimate"), "estimate_cradle")
    except (ImportError, AttributeError):
        log.debug("pack type %r has no estimate_cradle — no estimate for this call", pack_type)
        return None


def _pack_estimator(pack: Path, name: str = "", tool_input: dict | None = None) -> Callable[..., dict] | None:
    """The ``estimate_cradle`` that prices this call, resolved by ``pack_type``.

    Registry dispatch, exactly as the CLI's estimate verbs do it: a dungeon
    prices with ``canon.packs.dungeon.estimate``, a platformer with the
    platformer's (both wrap the ONE engine of row P0-7 — there is no second
    price table anywhere). Never raises: a pack type with no cradle-facing
    estimator yields no estimate, which the card renders as "not estimated"
    rather than a confident $0.

    Row A9's one addition: ``create_project`` prices under the TEMPLATE it is
    about to create (``PackSpec`` in ``PACKS``), not the pack the conversation
    happens to have open — a dungeon proposed from a platformer session must
    quote dungeon money. Every other tool edits the open pack, so the open
    pack's type is still the right answer for them.
    """
    if name == "create_project":
        chosen = _chosen_template((tool_input or {}).get("template"))
        if chosen is not None:
            return _estimator_of(chosen.pack_type)
        return None
    pack_type = "platformer"
    try:
        from canon.packs import resolve_pack

        pack_type = resolve_pack(pack).pack_type
    except Exception:  # noqa: BLE001 — an unresolvable pack still gets a default
        log.debug("could not resolve pack type for %s; estimating as platformer", pack, exc_info=True)
    return _estimator_of(pack_type)


def estimate_payload(pack: Path, name: str, tool_input: dict) -> dict | None:
    """``{low, high, backend, model, unitCount, unitLabel}`` for one paid call.

    The §3.0-E estimate contract, straight off canon's estimator (row P0-7 put
    ``low``/``high``/``backend``/``model``/``unitCount``/``accuracy`` on every
    estimate document) — nothing is computed here.

    A paid call canon has NO scope for (``generate_asset``, ``complete_row``)
    gets :func:`_unpriced_payload` instead: the same keys, a zero range, the
    backend named — the card's "— not estimated" state. ``None`` only when the
    call is free (the ordinary ask chip carries it) or the estimator refused
    this input.
    """
    scope = _ESTIMATE_SCOPE.get(name)
    if scope is None:
        return _unpriced_payload(name, tool_input)
    estimate_cradle = _pack_estimator(pack, name, tool_input)
    if estimate_cradle is None:
        return _unpriced_payload(name, tool_input)
    backends = selected_backends(name, tool_input)
    kwargs: dict[str, Any] = {"backends": backends}
    if scope == "world":
        # Row A9: counts are a template-keyed OBJECT (`pack templates`'
        # `defaults` keys — `stages`/`levels`/… on a platformer,
        # `rooms`/`npc`/… on a dungeon), never a fixed field list here; the
        # unset ones fall back to the template's own defaults so the card
        # prices the run that is really proposed.
        kwargs["counts"] = effective_counts(tool_input)
    elif scope == "animate":
        kwargs.update({"pack_dir": pack, "target": tool_input.get("target"),
                       "reuse_spec": bool(tool_input.get("reuse_spec"))})
    elif scope != "music":
        kwargs.update({"pack_dir": pack, "level_id": tool_input.get("level_id")})
        if tool_input.get("width") is not None:
            kwargs["width"] = int(tool_input["width"])
    try:
        estimate = estimate_cradle(scope, **kwargs)
    except Exception as exc:  # noqa: BLE001 — an unpriceable input is "unknown", not an error
        log.debug("no estimate for %s(%s): %s", name, scope, exc)
        return None
    payload = {
        "low": float(estimate.get("low") or 0.0),
        "high": float(estimate.get("high") or 0.0),
        "backend": estimate.get("backend") or "",
        "model": estimate.get("model") or "",
        "unitCount": int(estimate.get("unitCount") or 0),
        "unitLabel": _UNIT_LABEL.get(scope, scope),
    }
    if estimate.get("accuracy"):
        payload["accuracy"] = str(estimate["accuracy"])
    return payload


def _unpriced_payload(name: str, tool_input: dict) -> dict | None:
    """The SHAPE-COMPLETE "price unknown" estimate for a paid call canon has no
    scope for (row P1-A6; Phase 1 §2.4's four paid-card states).

    ``None`` from :func:`estimate_payload` means the request carries no
    ``estimate``, so ``PermissionRequest.payload`` emits no ``paid`` block and
    the client falls back to a bare pending chip that says NOTHING about money —
    the priciest tools losing the very card that exists to price them. A
    zero-range payload naming the backend renders the documented unknown state
    instead: "estimate — not estimated" and ``Accept · spend on <backend>``,
    never a confident "$0.00" (doctrine 3: no estimate is NOT $0).

    A FREE call needs none of this: it resolves to the ask tier and gets the
    ordinary chip, so this returns ``None`` for it.
    """
    if not spends_money(name, tool_input):
        return None
    backends = selected_backends(name, tool_input)
    # The primary is the first category the tool declares — image for a sprite,
    # llm for a row — so the card names the backend the user is about to spend on.
    primary = next(
        (backends[kind] for kind, _field, _default in _BACKEND_FIELDS.get(name, ()) if kind in backends),
        "",
    )
    model = tool_input.get("image_model") or tool_input.get("model") or ""
    return {
        "low": 0.0,
        "high": 0.0,
        "backend": str(primary),
        "model": str(model),
        "unitCount": 0,
        "unitLabel": _UNPRICED_UNIT_LABEL.get(name, "this call"),
    }


def _estimator_for(pack: Path, name: str) -> Callable[[dict], dict | None]:
    def estimate(tool_input: dict) -> dict | None:
        return estimate_payload(pack, name, tool_input)

    estimate.__name__ = f"estimate_{name}"
    return estimate


# ---------------------------------------------------------------------------
# The tool bodies — each ``(pack, input, call) -> JSON-able`` with a cost block
# ---------------------------------------------------------------------------


def _ops():
    from canon.packs.platformer import ops

    return ops


def _spend(pack: Path, events: list[dict], *, op: str, name: str, tool_input: dict, call: CallContext) -> dict:
    """Write the DERIVED compat spend row for a costed tool call (P.8.7).

    The journal is authoritative — this row exists so pre-A6 readers keep
    working, and it carries ``journal_ref`` (the ts of the op's first journal
    event) precisely so a reconciler never counts it twice. Best-effort: a
    ledger-write failure must never surface as if the generation failed, the
    same rule cradle's own ``recordSpend`` follows.
    """
    from canon.provenance import identity_for
    from canon.spend import record_spend, spend_row_from_journal

    costed = next((e for e in events if e.get("costCents") is not None), None)
    row: dict[str, Any] = {}
    try:
        if costed is not None:
            row = spend_row_from_journal(
                costed, op=op, scope=name, level_id=tool_input.get("level_id"),
                backends=selected_backends(name, tool_input) or None,
            )
        else:
            # No costed event: the verb journalled no money (a free selection,
            # or an op that wrote nothing). The row carries NO ``journal_ref``,
            # which is exactly what tells a reconciler to count it on its own.
            row = {
                "op": op, "scope": name, "actor": call.actor,
                "identity": identity_for(call.actor),
                "session": call.conversation, "category": "generation",
                "backends": selected_backends(name, tool_input) or None,
                "level_id": tool_input.get("level_id"),
            }
        record_spend(pack, {k: v for k, v in row.items() if v is not None})
    except Exception as exc:  # noqa: BLE001 — the op already ran
        log.warning("spend row for %s not written (the op still succeeded): %s", name, exc)
    return row


def _run(name: str, op: str, verb: Callable[..., dict]) -> Callable[[Path, dict, CallContext], dict]:
    """Wrap a canon verb as a paid tool body: run it inside row A4's journal
    window (so the events belong to THIS call), then write the derived spend
    row. The verb's own result — INCLUDING its ``cost`` block — is returned
    verbatim; nothing is recomputed here."""

    def body(pack: Path, tool_input: dict, call: CallContext) -> dict:
        # The RAW events (not the compacted ones the result carries) — the
        # derived spend row needs the event's ``ts`` and ``gen.cost_usd``,
        # which the transcript's compact view deliberately drops.
        with journal_window(pack) as events:
            result = dict(verb(pack, tool_input, actor=call.actor, session=call.conversation))
        result["journal"] = compact_events(events)
        return {**result, "spend": _spend(pack, events, op=op, name=name, tool_input=tool_input, call=call)}

    body.__name__ = name
    return body


def _generate_layout(pack: Path, i: dict, **kw: Any) -> dict:
    return _ops().regenerate_terrain(
        pack, level_id=i["level_id"], brief=i.get("brief", ""),
        backend=i.get("llm_backend", "fake"), model=i.get("model"), **kw,
    )


def _improve_layout(pack: Path, i: dict, **kw: Any) -> dict:
    return _ops().improve_terrain(
        pack, level_id=i["level_id"], instruction=i["instruction"],
        fix_problems=bool(i.get("fix_problems", False)),
        reroll_placements=bool(i.get("reroll_placements", False)),
        backend=i.get("llm_backend", "fake"), model=i.get("model"), **kw,
    )


def _place_enemies(pack: Path, i: dict, **kw: Any) -> dict:
    return _ops().place_enemies(
        pack, level_id=i["level_id"], backend=i.get("llm_backend", "fake"),
        model=i.get("model"), max_enemies=int(i.get("max_enemies", 4)), **kw,
    )


def _place_items(pack: Path, i: dict, **kw: Any) -> dict:
    return _ops().place_items(
        pack, level_id=i["level_id"], backend=i.get("llm_backend", "fake"),
        model=i.get("model"), max_items=int(i.get("max_items", 12)), **kw,
    )


def _generate_level(pack: Path, i: dict, **kw: Any) -> dict:
    return _ops().generate_level(
        pack, stage_id=i["stage_id"], brief=i.get("brief", ""),
        backend=i.get("llm_backend", "fake"), model=i.get("model"),
        width=i.get("width"), height=i.get("height"), axis=i.get("axis"),
        max_enemies=int(i.get("max_enemies", 4)), max_items=int(i.get("max_items", 12)), **kw,
    )


def _generate_asset(pack: Path, i: dict, **kw: Any) -> dict:
    return _ops().generate_asset(
        pack, i["target"], image_backend=i.get("image_backend"), image_model=i.get("image_model"),
        music_backend=i.get("music_backend"), sfx_backend=i.get("sfx_backend"),
        prompt_override=i.get("prompt"), **kw,
    )


def _animate_asset(pack: Path, i: dict, **kw: Any) -> dict:
    return _ops().animate_asset(
        pack, i["target"], image_backend=i.get("image_backend"), image_model=i.get("image_model"),
        vlm_backend=i.get("vlm_backend"), reuse_spec=bool(i.get("reuse_spec", False)),
        prompt_override=i.get("prompt"), **kw,
    )


def _generate_music(pack: Path, i: dict, **kw: Any) -> dict:
    return _ops().generate_level_music(
        pack, level_id=i["level_id"], brief=i.get("brief", ""), section=i.get("section"),
        backend=i.get("music_backend", "fake"), **kw,
    )


def _complete_row(pack: Path, i: dict, **kw: Any) -> dict:
    from canon.db_ops import complete_db_row
    from canon.packs.platformer.ops import build_llm

    llm = build_llm(i.get("llm_backend", "fake"), i.get("model"))
    return complete_db_row(
        pack, i["type"], i["id"], list(i.get("locked") or []),
        reroll=bool(i.get("reroll", False)), llm=llm, **kw,
    )


def project_store_root() -> Path:
    """Where a created project lands by default — ``$CRADLE_PROJECTS_DIR``,
    else ``~/CradleProjects`` (Phase 0 §8.4). The same two-step rule cradle's
    Rust ``project_store_root`` follows, so the agent's create and the wizard's
    create land in one place and the recents rail sees both."""
    override = os.environ.get(PROJECT_STORE_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / PROJECT_STORE_DIRNAME


def slugify(name: str) -> str:
    """``<name>`` → a filesystem-safe directory name (the Rust command's rule:
    ASCII alphanumerics lowercased, everything else ``_``, trimmed)."""
    slug = "".join(c.lower() if c.isascii() and c.isalnum() else "_" for c in name).strip("_")
    return slug or "project"


def unique_pack_dir(parent: Path, slug: str) -> Path:
    """The first free ``<parent>/<slug>``, ``<slug>_2``, … — auto-uniquify, so
    a second "Ice World" is a new project rather than the hard refusal
    ``world new`` still (correctly) raises on a genuine collision (P0-10)."""
    def occupied(path: Path) -> bool:
        return path.exists() and any(path.iterdir())

    first = parent / slug
    if not occupied(first):
        return first
    for n in range(2, 1000):
        candidate = parent / f"{slug}_{n}"
        if not occupied(candidate):
            return candidate
    return parent / f"{slug}_{os.getpid()}"


def create_argv(out: Path, tool_input: dict, *, actor: str) -> list[str]:
    """The ``canon world new`` argv one create call runs — the ONE create verb
    (P0-10's registry dispatch), never a re-implementation of it.

    Counts go by NAME (``--<count key>``), exactly as cradle's Rust command
    sends them: ``world new``'s signature is the union of the templates' count
    flags and every count key is one of its aliases, so a third template needs
    no change here and a flag its template does not declare is refused BY NAME
    by canon (doctrine 4). ``--orchestrate`` is not passed: master §8 Q6 made
    it the DEFAULT for every template that has a DAG, and passing it explicitly
    to a DAG-less template is exactly the case P0-10 warns about.
    """
    spec = _chosen_template(tool_input.get("template"))
    argv = [
        sys.executable, "-m", "canon.cli.main", "world", "new", str(out),
        "--name", str(tool_input.get("name") or "New project"),
        "--template", str(getattr(spec, "pack_type", "") or tool_input.get("template") or ""),
        "--actor", actor,
    ]
    for key, value in sorted(effective_counts(tool_input).items()):
        argv += [f"--{key}", str(value)]
    for kind, backend in sorted(selected_backends("create_project", tool_input).items()):
        argv += [f"--{kind}-backend", backend]
    seed = str(tool_input.get("seed") or "").strip()
    if seed:
        argv += ["--seed", seed]
    model = str(tool_input.get("model") or "").strip()
    if model:
        argv += ["--model", model]
    return argv


def _create_spend(new_pack: Path, tool_input: dict, call: CallContext) -> dict:
    """The derived compat spend row for a create, written into the pack the run
    CREATED — never the pack the conversation had open (the exact rule cradle's
    ``NewProjectModal`` follows: "both ledgers land in the pack the run
    created"). The ACTUAL figure is the created tree's own
    ``manifest.generation_stats.total_cost_usd``; the runner already journalled
    its per-step money inside that pack. Best-effort, like every other ledger
    write here: a ledger failure must never read as a failed create."""
    from canon.provenance import identity_for
    from canon.spend import record_spend

    actual = 0.0
    try:
        manifest = json.loads((new_pack / "manifest.json").read_text(encoding="utf-8"))
        actual = float((manifest.get("generation_stats") or {}).get("total_cost_usd") or 0.0)
    except Exception:  # noqa: BLE001 — stats are optional; $0 is the honest fallback
        log.debug("no generation_stats in %s; recording the create at $0", new_pack, exc_info=True)
    row = {
        "op": "world", "scope": "create_project", "actor": call.actor,
        "identity": identity_for(call.actor), "session": call.conversation,
        "category": "generation", "actual_usd": actual,
        "backends": selected_backends("create_project", tool_input) or None,
    }
    try:
        record_spend(new_pack, {k: v for k, v in row.items() if v is not None})
    except Exception as exc:  # noqa: BLE001 — the project exists either way
        log.warning("spend row for create_project not written (the project still exists): %s", exc)
    return row


def _create_project(pack: Path, tool_input: dict, call: CallContext) -> dict:
    """Create a WHOLE new project — row P1-A9, the tool the start-page
    conversation drives (Phase 1 §2.4, agent-panel README §11).

    What it extends, in order:

    1. **``canon pack templates``** (:func:`creatable_templates`) decides what
       is creatable. An unknown template is refused by name, listing the
       installed ones — never a branch on a template id.
    2. **P0-10's project store** for the destination (``$CRADLE_PROJECTS_DIR``
       → ``~/CradleProjects``), with the same slug + auto-uniquify rule the
       Rust command uses, so the agent's projects and the wizard's are one
       list. ``parent_dir`` overrides it (tests, "choose location").
    3. **The folder is written to disk BEFORE anything is spent** — the
       start page's own promise ("you can stop at any step and keep what
       exists"). The empty directory is created here, so a create that dies
       (or is stopped) leaves something to come back to, and the refusal /
       error says the path.
    4. **``canon world new``** — P0-10's registry dispatch — runs the create.
       This is the same verb cradle's ``new_project`` JobQueue command spawns:
       one pipeline, two launchers. ``--orchestrate`` defaults on (Q6), the
       world name lands through the journaled write core and the pack's
       ``.canon/registry.json`` is stamped, all inside that verb.
    5. **The ledgers land in the CREATED pack** (:func:`_create_spend`).

    Doctrine 3 is upheld by the SELECTION, not by this body: an all-fake/none
    call bills nothing and is ask-tier (:func:`paid_tier_for`); a paid one
    confirmed with an estimate first. Nothing here reaches a provider on its
    own — every backend it passes was named by the call the user approved.
    """
    import subprocess

    templates = creatable_templates()
    spec = _chosen_template(tool_input.get("template"))
    if spec is None:
        raise ValueError(
            f"unknown template {tool_input.get('template')!r}: installed templates are "
            f"{[t['id'] for t in templates] or 'none — the pack registry did not load'} "
            f"(`canon pack templates` describes them). Row {CREATE_PROJECT_ROWS}."
        )
    name = str(tool_input.get("name") or "").strip()
    if not name:
        raise ValueError("create_project needs a project name — it becomes the world's title and its folder")

    parent = Path(str(tool_input["parent_dir"])).expanduser() if tool_input.get("parent_dir") else project_store_root()
    parent.mkdir(parents=True, exist_ok=True)
    out = unique_pack_dir(parent, slugify(name))
    # (3) The folder first — before a single token is spent.
    out.mkdir(parents=True, exist_ok=True)

    argv = create_argv(out, tool_input, actor=call.actor)
    try:
        completed = subprocess.run(argv, check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        tail = (exc.stderr or b"").decode(errors="replace")[-800:]
        raise RuntimeError(
            f"create_project failed for {name!r}; the folder is kept at {out} so nothing is lost "
            f"(delete it yourself if you don't want it):\n{tail}"
        ) from exc
    try:
        result = dict(json.loads((completed.stdout or b"").decode(errors="replace") or "{}"))
    except ValueError:
        result = {}
    result.setdefault("pack_dir", str(out))
    result["template"] = getattr(spec, "pack_type", result.get("template"))
    result["counts"] = effective_counts(tool_input)
    result["spend"] = _create_spend(out, tool_input, call)
    return result


# ---------------------------------------------------------------------------
# Chip copy: "‹Specialist› wants to ‹verb› ‹target›"
# ---------------------------------------------------------------------------

TARGETS: dict[str, Callable[[dict], str]] = {
    "generate_layout": lambda i: f"re-roll the layout of {i['level_id']}",
    "improve_layout": lambda i: f"improve {i['level_id']}",
    "place_enemies": lambda i: f"place enemies in {i['level_id']}",
    "place_items": lambda i: f"place items in {i['level_id']}",
    "generate_level": lambda i: f"generate a level in stage {i['stage_id']}",
    "generate_asset": lambda i: f"generate art for {i['target']}",
    "animate_asset": lambda i: f"animate {i['target']}",
    "generate_music": lambda i: f"generate music for {i['level_id']}",
    "complete_row": lambda i: f"LLM-complete {i['type']} {i['id']}",
    "create_project": lambda i: f"create the project {i.get('name') or 'a new project'}",
}


# ---------------------------------------------------------------------------
# Specs + registration
# ---------------------------------------------------------------------------

_LEVEL_ID = {"type": "string", "description": "Level id as describe_pack lists it, e.g. 'l1'."}
_LLM = {"type": "string", "description": "Chat backend id (data): fake ($0) | anthropic | … ."}
_MODEL = {"type": "string", "description": "Model id for that backend (a plain string; ids are data)."}
_IMAGE = {"type": "string", "description": "Image backend id: none | fake | fal | retro | pixellab | local."}
_TARGET = {"type": "string", "description": "enemy:<id> | item:<id> | player | backdrop:<stage> | audio:<stage>."}


def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


def _creatable_line() -> str:
    """One sentence naming the INSTALLED templates for ``create_project``'s
    description, read from ``pack templates`` at import (row A9) — the model
    is told what exists by the registry, never by a list in this file."""
    entries = creatable_templates()
    if not entries:
        return "No templates are installed, so nothing is creatable right now."
    return "Templates: " + " · ".join(f"{e['id']} ({e['label']})" for e in entries) + "."


#: name → (description for the model, input schema, body, touches)
_TOOLS: dict[str, tuple[str, dict, Callable[[Path, dict, CallContext], Any], str]] = {
    "generate_layout": (
        "PAID. Re-roll ONE level's terrain from a brief (blind — the model does not see the current level). "
        "Placements are CLEARED because they belonged to the old terrain; re-run place_enemies/place_items. "
        "Journals 'generate' per changed step with the run's cost; validate_level afterwards.",
        _schema({"level_id": _LEVEL_ID, "brief": {"type": "string"}, "llm_backend": _LLM, "model": _MODEL},
                ["level_id"]),
        _run("generate_layout", "layout", _generate_layout),
        "writes level/<stage>/<id>/ collision+derived, clears placements; journals generate + cost",
    ),
    "improve_layout": (
        "PAID. Re-author ONE level IN PLACE with the model SEEING its current terrain plus your instruction "
        "(the context-aware sibling of generate_layout). Placements are kept unless reroll_placements. "
        "fix_problems also feeds the level's validation problems to the model.",
        _schema({"level_id": _LEVEL_ID, "instruction": {"type": "string"},
                 "fix_problems": {"type": "boolean"}, "reroll_placements": {"type": "boolean"},
                 "llm_backend": _LLM, "model": _MODEL},
                ["level_id", "instruction"]),
        _run("improve_layout", "improve", _improve_layout),
        "writes level/<stage>/<id>/ collision+derived; journals regenerate + cost",
    ),
    "place_enemies": (
        "PAID. Place enemies onto an EXISTING level against its on-disk grid, rewriting entities.json. "
        "Grid-driven and self-validating, so it adapts to whatever terrain is there.",
        _schema({"level_id": _LEVEL_ID, "max_enemies": {"type": "integer", "minimum": 0},
                 "llm_backend": _LLM, "model": _MODEL}, ["level_id"]),
        _run("place_enemies", "enemies", _place_enemies),
        "writes level/<stage>/<id>/entities.json + level.json; journals generate + cost",
    ),
    "place_items": (
        "PAID. Place items onto an EXISTING level against its on-disk grid and enemy roster, rewriting items.json.",
        _schema({"level_id": _LEVEL_ID, "max_items": {"type": "integer", "minimum": 0},
                 "llm_backend": _LLM, "model": _MODEL}, ["level_id"]),
        _run("place_items", "items", _place_items),
        "writes level/<stage>/<id>/items.json + level.json; journals generate + cost",
    ),
    "generate_level": (
        "PAID. A WHOLE new draft level in a stage — terrain, then enemies, then items — as one chain sharing "
        "one seed. The level is a DRAFT: publish_level puts it in the playable progression.",
        _schema({"stage_id": {"type": "string"}, "brief": {"type": "string"},
                 "width": {"type": "integer", "minimum": 8}, "height": {"type": "integer", "minimum": 8},
                 "axis": {"type": "string"}, "max_enemies": {"type": "integer", "minimum": 0},
                 "max_items": {"type": "integer", "minimum": 0}, "llm_backend": _LLM, "model": _MODEL},
                ["stage_id"]),
        _run("generate_level", "generate", _generate_level),
        "writes a new level/<stage>/<id>/ dir; journals generate + cost",
    ),
    "generate_asset": (
        "PAID. (Re)generate ONE asset's art or audio: enemy:<id> | item:<id> | player | backdrop:<stage> | "
        "audio:<stage>. 'prompt' replaces the image prompt for sprite targets only. Journals regenerate with "
        "the backend's own reported cost where it reports one.",
        _schema({"target": _TARGET, "prompt": {"type": "string"}, "image_backend": _IMAGE,
                 "image_model": _MODEL, "music_backend": {"type": "string"}, "sfx_backend": {"type": "string"}},
                ["target"]),
        _run("generate_asset", "sprite", _generate_asset),
        "writes sprite/backdrop/audio bytes + manifests; journals regenerate + cost",
    ),
    "animate_asset": (
        "PAID. Author an actor's animation: a VLM motion spec plus one img2img sheet per state, sliced into "
        "strips + frames.json + a packed atlas. Targets: enemy:<id> | player. reuse_spec skips the authoring "
        "call and re-renders the stored spec.",
        _schema({"target": {"type": "string", "description": "enemy:<id> | player"},
                 "prompt": {"type": "string"}, "image_backend": _IMAGE, "image_model": _MODEL,
                 "vlm_backend": {"type": "string"}, "reuse_spec": {"type": "boolean"}}, ["target"]),
        _run("animate_asset", "animate", _animate_asset),
        "writes state strips + atlas + frames.json; journals regenerate + cost",
    ),
    "generate_music": (
        "PAID. Generate ONE music track for a level (or one of its music sections), write it under "
        "music/<stage>/<level>/ and repoint the level. Journals the repoint edit with the track's cost.",
        _schema({"level_id": _LEVEL_ID, "brief": {"type": "string"},
                 "section": {"type": "integer", "minimum": 0}, "music_backend": {"type": "string"}},
                ["level_id"]),
        _run("generate_music", "music", _generate_music),
        "writes music/<stage>/<level>/*.wav + level.json; journals edit + cost",
    ),
    "complete_row": (
        "PAID. LLM-complete an EXISTING db row around its locked anchors: 'locked' fields are preserved as "
        "constraints, the rest are authored. 'reroll' also re-rolls the unlocked mechanical fields. "
        "Journals regenerate with the tokens it burned.",
        _schema({"type": {"type": "string"}, "id": {"type": "string"},
                 "locked": {"type": "array", "items": {"type": "string"}},
                 "reroll": {"type": "boolean"}, "llm_backend": _LLM, "model": _MODEL}, ["type", "id"]),
        _run("complete_row", "complete", _complete_row),
        "writes <type>/<id>.json via canon db complete; journals regenerate + cost",
    ),
    "create_project": (
        "PAID (ask-tier when every backend is fake/none — a $0 preview never spend-confirms). Create a "
        "WHOLE new project from a template, in the project store, via `canon world new`. Ask the user AT "
        "MOST TWO clarifying questions first, then propose a numbered plan; the create is one approved "
        f"step of it. {_creatable_line()} 'counts' is an object keyed by THAT template's count names. "
        "A folder is written to disk before anything is spent, so a stopped create keeps what exists.",
        _schema({"name": {"type": "string", "description": "The world's title; also its folder name."},
                 "template": {"type": "string",
                              "description": "A template id from `pack templates` (data, not an enum)."},
                 "counts": {"type": "object",
                            "description": "Count key → number, in the CHOSEN template's vocabulary "
                                           "(pack templates → defaults). Unset keys use its defaults.",
                            "additionalProperties": {"type": "integer", "minimum": 0}},
                 "seed": {"type": "string"}, "model": _MODEL,
                 "parent_dir": {"type": "string",
                                "description": "Override the project store; normally omitted."},
                 "llm_backend": _LLM, "image_backend": _IMAGE, "music_backend": {"type": "string"},
                 "sfx_backend": {"type": "string"}, "vlm_backend": {"type": "string"}}, ["name"]),
        _create_project,
        "creates a NEW pack in the project store via `canon world new` (never this pack); "
        f"its ledgers land in the created pack — rows {CREATE_PROJECT_ROWS}",
    ),
}


def _bind(
    name: str,
    schema: dict,
    body: Callable[[Path, dict, CallContext], Any],
    pack: Path,
    actor_for: Callable[[], CallContext],
) -> Callable[[dict], str]:
    def run(tool_input: dict) -> str:
        validate_input(name, schema, tool_input)
        return compact(body(pack, tool_input, actor_for()))

    run.__name__ = name
    return run


def paid_tool_specs() -> list[ToolSpec]:
    """The specs alone (what the eval corpus and the panel's tool list show)."""
    return [ToolSpec(name=name, description=desc, input_schema=schema) for name, (desc, schema, _, _) in _TOOLS.items()]


def register_paid_tools(
    registry: ToolRegistry,
    pack_dir: str | Path,
    *,
    actor_for: Callable[[], CallContext],
) -> list[str]:
    """Register every paid tool for ``pack_dir`` into ``registry`` (tier
    ``"paid"``, :data:`PAID_TOOL_NAMES` order) and return the names.

    ``actor_for`` is called at EVERY tool run for the ``CallContext`` the verb
    is attributed to — the same seam ``register_write_tools`` uses, so the
    actor string is ``agent_actor``'s alone (I6) and the registry (A2) is
    unchanged. Three things register on the engine beside the tool: the chip
    describer (``describe``), the pre-spend estimator (``estimate_with``) and
    the free-selection tier resolver (``tier_with``). Nothing is read or priced
    at registration time.
    """
    pack = Path(pack_dir)
    names: list[str] = []
    engine = registry.permissions
    for name in PAID_TOOL_NAMES:
        description, schema, body, touches = _TOOLS[name]
        spec = ToolSpec(name=name, description=description, input_schema=schema)
        registry.register(
            Tool(spec=spec, tier=PAID_TIER, run=_bind(name, schema, body, pack, actor_for), touches=touches)
        )
        if hasattr(engine, "describe"):
            engine.describe(name, TARGETS[name])
        if hasattr(engine, "estimate_with"):
            engine.estimate_with(name, _estimator_for(pack, name))
        if hasattr(engine, "tier_with"):
            engine.tier_with(name, paid_tier_for(name))
        names.append(name)
    return names


__all__ = [
    "CREATE_PROJECT_ROWS",
    "FREE_TIER",
    "PAID_TIER",
    "PAID_TOOL_NAMES",
    "PROJECT_STORE_DIRNAME",
    "PROJECT_STORE_ENV",
    "TARGETS",
    "create_argv",
    "creatable_templates",
    "effective_counts",
    "estimate_payload",
    "paid_tier_for",
    "paid_tool_specs",
    "project_store_root",
    "register_paid_tools",
    "selected_backends",
    "slugify",
    "spends_money",
    "unique_pack_dir",
]
