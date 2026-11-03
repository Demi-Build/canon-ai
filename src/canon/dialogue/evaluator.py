"""The ONE dialogue evaluator — ten condition namespaces, five effects, five
scopes, over the P.3.3 ``--state`` payload (Phase 0 §7.2, P0 paper P.2.1–P.2.3;
row P0-9).

"One evaluator; the UI never reimplements gating" (§7.2). Every gate verdict
cradle renders — the tester dock, the gate ribbon, the selector rail's
would-play/blocked grouping — comes from this module through
``canon dialogue test`` / ``select`` / ``scene test``. There is no TypeScript
twin, by design (`PLAN.md:229`: "do not port the evaluator into TypeScript").

What it extends: ``canon.dialogue.grammar`` (which owns parsing, scope
legality and the operand vocabulary — all of it registry data). This module
adds only the *semantics*: what each namespace asks of the simulated state,
and what each effect does to it.

The state payload (P.2.3 / P.3.3), every section optional and open::

    {"inventory": {"2000": 1}, "quests": {"4000": "active"},
     "clock": {"period": "night", "day": 2}, "room": "room_1",
     "player": {"health": 14}, "flags": {}, "segment": null,
     "scenes_seen": [], "events": {"3000": "solved"},
     "actors": {"1000": "present"}}

Decisions this implements verbatim (P.9): quest states are the ENGINE's four
(C2); ``time:`` operands are PERIOD NAMES, not hours (C3); ``event:<id>:solved``
means resolved-with-SUCCESS, which the engine cannot honour today and the
tester therefore implements alone (C5); ``flag:<key>`` is truthy with
``flag:<key>:<bool>`` legal and ``advance_quest:<id>`` takes the engine-defined
next state with an optional explicit one (C7); operands are the ``id_field``
value stringified (C8).

A verdict carries THREE facts, never one: the tester's boolean ``pass``, the
named ``reason``, and whether the ENGINE evaluates the token at all. The
ribbon glyph (``verdict``) is ``unevaluable`` whenever the engine does not
evaluate it — the split verdict of README §6, and doctrine 10's whole point:
the tester answers, the engine may not, and both are said out loud.

Deliberately absent, by row ownership: engine-side evaluation of any of this
(its own arc); the live in-game scene (Phase 2 W2.2).
"""

from __future__ import annotations

import copy
from typing import Any

from canon.dialogue.grammar import (
    ParsedToken,
    TokenError,
    engine_evaluable,
    parse_effect,
    parse_token,
    spec_of,
)
from canon.packs.spec import DialogueSpec

__all__ = [
    "STATE_SECTIONS",
    "apply_effects",
    "evaluate_condition",
    "evaluate_conditions",
    "normalize_state",
]

#: P.2.3 / P.3.3 — the tester sections. Open: an unknown key is carried
#: through untouched (a template's own axis), never rejected.
STATE_SECTIONS: tuple[str, ...] = (
    "inventory", "quests", "clock", "room", "player", "flags", "segment",
    "scenes_seen", "events", "actors",
)

_NUMERIC_OPS = {
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    ">=": lambda a, b: a >= b,
    ">": lambda a, b: a > b,
}


def normalize_state(state: Any) -> dict[str, Any]:
    """A ``--state`` payload with every P.2.3 section present and correctly
    typed. A missing section is EMPTY, never "everything true" — an absent
    inventory fails ``has_item``, and the reason says the section was empty
    rather than pretending the item is missing from a stocked bag."""
    src = state if isinstance(state, dict) else {}
    out: dict[str, Any] = {
        "inventory": {str(k): v for k, v in (src.get("inventory") or {}).items()},
        "quests": {str(k): str(v) for k, v in (src.get("quests") or {}).items()},
        "clock": dict(src.get("clock") or {}),
        "room": None if src.get("room") is None else str(src.get("room")),
        "player": dict(src.get("player") or {}),
        "flags": dict(src.get("flags") or {}),
        "segment": None if src.get("segment") is None else str(src.get("segment")),
        "scenes_seen": [str(s) for s in (src.get("scenes_seen") or [])],
        "events": {str(k): str(v) for k, v in (src.get("events") or {}).items()},
        "actors": {str(k): str(v) for k, v in (src.get("actors") or {}).items()},
    }
    for key, value in src.items():
        if key not in out:
            out[key] = copy.deepcopy(value)
    return out


def _as_number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _verdict_player(parsed: ParsedToken, state: dict) -> tuple[bool, str]:
    field, op, wanted = parsed.slots["field"], parsed.slots["op"], parsed.slots["value"]
    if field not in state["player"]:
        return False, f"player.{field} is not set in the simulated state"
    actual = state["player"][field]
    left, right = _as_number(actual), _as_number(wanted)
    if left is not None and right is not None:
        ok = _NUMERIC_OPS[op](left, right)
    elif op == "==":
        ok = str(actual) == wanted
    else:
        return False, f"player.{field} is {actual!r} — {op} needs numbers on both sides"
    return ok, (
        f"player.{field} is {actual}" + ("" if ok else f", not {op} {wanted}")
    )


def _verdict(parsed: ParsedToken, state: dict) -> tuple[bool, str]:
    """The tester's boolean + its named reason, per namespace (P.2.1)."""
    ns = parsed.namespace
    slots = parsed.slots
    if ns == "has_item":
        item = slots["entity_id"]
        qty = state["inventory"].get(item)
        try:
            held = int(qty or 0)
        except (TypeError, ValueError):
            held = 1 if qty else 0
        return held > 0, (f"{held} in inventory" if held else "not in inventory")
    if ns == "quest":
        quest, wanted = slots["entity_id"], slots["state"]
        actual = state["quests"].get(quest, "not_started")
        return actual == wanted, (
            f"quest is {actual}" + ("" if actual == wanted else f", not {wanted}")
        )
    if ns == "time":
        # C3: period names, not hours. `always` is a gate VALUE in row data,
        # not a period, so it is never a `time:` operand here.
        window = slots["window"]
        actual = state["clock"].get("period")
        return actual == window, (
            f"period is {actual}" if actual else "the simulated clock has no period"
        ) + ("" if actual == window else f", not {window}")
    if ns == "player":
        return _verdict_player(parsed, state)
    if ns == "flag":
        key = slots["key"]
        actual = bool(state["flags"].get(key))
        wanted = slots.get("value")
        if wanted is None:  # C7: bare `flag:<key>` is truthy
            return actual, f"flag is {'set' if actual else 'unset'}"
        want = wanted == "true"
        return actual == want, f"flag is {str(actual).lower()}, not {wanted}"
    if ns == "segment":
        wanted = slots["value"]
        actual = state["segment"]
        return actual == wanted, (
            f"segment is {actual!r}" if actual else "no segment in the simulated state"
        ) + ("" if actual == wanted else f", not {wanted!r}")
    if ns == "room":
        wanted, actual = slots["entity_id"], state["room"]
        return actual == wanted, (
            f"room is {actual!r}" if actual else "no room in the simulated state"
        ) + ("" if actual == wanted else f", not {wanted!r}")
    if ns == "scene":
        scene, wanted = slots["entity_id"], slots["state"]
        actual = "seen" if scene in state["scenes_seen"] else "unseen"
        return actual == wanted, f"scene is {actual}" + ("" if actual == wanted else f", not {wanted}")
    if ns == "event":
        # C5: `solved` is resolved-WITH-SUCCESS. The engine records only
        # `resolved` (set on failure too), so this verdict is tester-only.
        event, wanted = slots["entity_id"], slots["state"]
        actual = state["events"].get(event, "unsolved")
        return actual == wanted, f"event is {actual}" + ("" if actual == wanted else f", not {wanted}")
    if ns == "actor":
        actor, wanted = slots["entity_id"], slots["state"]
        actual = state["actors"].get(actor, "absent")
        return actual == wanted, f"actor is {actual}" + ("" if actual == wanted else f", not {wanted}")
    return False, f"no evaluator for namespace {ns!r} in this canon build"


def evaluate_condition(
    token: Any,
    state: dict[str, Any],
    *,
    scope: str = "tree",
    spec: DialogueSpec | None = None,
    engine_blocks: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One condition token → ``{token, namespace, pass, reason, verdict,
    engine_evaluable, engine_reason}``.

    ``pass`` is the TESTER's verdict and is always present. ``verdict`` is
    the ribbon glyph: ``unevaluable`` whenever the engine does not evaluate
    the token (README §6's split verdict), else ``pass`` / ``fail``. A token
    that does not parse comes back ``pass: False``, ``verdict: "error"`` with
    the parse reason — a malformed gate blocks the choice in the tester and
    is reported, never raised into the caller's face mid-walk.
    """
    spec = spec_of(spec)
    try:
        parsed = parse_token(token, scope=scope, spec=spec)
    except TokenError as exc:
        return {
            "token": str(token), "namespace": str(token).split(":")[0],
            "pass": False, "reason": str(exc), "verdict": "error",
            "engine_evaluable": False, "engine_reason": None,
        }
    ok, reason = _verdict(parsed, state)
    evaluable, engine_reason = engine_evaluable(parsed, scope, engine_blocks)
    return {
        "token": parsed.token,
        "namespace": parsed.namespace,
        "operands": list(parsed.operands),
        "pass": ok,
        "reason": reason,
        "verdict": ("pass" if ok else "fail") if evaluable else "unevaluable",
        "engine_evaluable": evaluable,
        "engine_reason": engine_reason,
    }


def evaluate_conditions(
    tokens: Any,
    state: dict[str, Any],
    *,
    scope: str = "tree",
    spec: DialogueSpec | None = None,
    engine_blocks: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A token list → ``{pass, conditions[], failing_condition, unevaluable[]}``.

    ALL tokens must pass for ``pass`` (the same all-of rule a selector's rows
    use). ``failing_condition`` names the FIRST failing token — the design's
    "the failing condition named" — and ``unevaluable`` lists the tokens the
    engine will ignore in game.
    """
    results = [
        evaluate_condition(t, state, scope=scope, spec=spec, engine_blocks=engine_blocks)
        for t in (tokens or [])
    ]
    failing = next((r for r in results if not r["pass"]), None)
    return {
        "pass": all(r["pass"] for r in results),
        "conditions": results,
        "failing_condition": failing["token"] if failing else None,
        "failing_reason": (f"{failing['token']} — {failing['reason']}" if failing else None),
        "unevaluable": [r["token"] for r in results if not r["engine_evaluable"]],
    }


# ---------------------------------------------------------------------------
# Effects (P.2.2)
# ---------------------------------------------------------------------------


def _next_quest_state(spec: DialogueSpec, current: str) -> str:
    """C7's "engine-defined next state" for a bare ``advance_quest``: the
    successor in the pack's OWN ``quest.states`` order (data — a template
    with a five-state quest gets its own chain), stopping at the last state.
    ``failed`` is terminal: nothing advances out of it."""
    states = list((spec.operands.get("quest") or {}).get("states") or [])
    if current == "failed" or current not in states:
        return current if current in states else (states[-1] if states else current)
    index = states.index(current)
    for candidate in states[index + 1 :]:
        if candidate != "failed":
            return candidate
    return current


def apply_effects(
    tokens: Any,
    state: dict[str, Any],
    *,
    spec: DialogueSpec | None = None,
    scope: str = "effects",
    engine_blocks: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Apply effect tokens to a COPY of *state*; return ``(post_state,
    firings)``.

    Each firing is ``{token, namespace, applied, detail, engine_evaluable,
    engine_reason}`` — the green ledger the transcript renders under the
    chosen choice, and the ``new``/``set`` tags the state panel flashes.
    An effect that cannot apply (``takes_item`` on an empty bag) is reported
    with its reason and still clamps the state — never an exception mid-walk.
    """
    spec = spec_of(spec)
    post = normalize_state(state)
    firings: list[dict[str, Any]] = []
    for token in tokens or []:
        try:
            parsed = parse_effect(token, spec=spec)
        except TokenError as exc:
            firings.append({
                "token": str(token), "namespace": str(token).split(":")[0],
                "applied": False, "detail": str(exc), "engine_evaluable": False,
                "engine_reason": None,
            })
            continue
        evaluable, engine_reason = engine_evaluable(parsed, scope, engine_blocks)
        ns, slots = parsed.namespace, parsed.slots
        applied, detail = True, ""
        if ns in ("gives_item", "takes_item"):
            item = slots.get("entity_id", "")
            try:
                held = int(post["inventory"].get(item, 0) or 0)
            except (TypeError, ValueError):
                held = 1
            if ns == "gives_item":
                post["inventory"][item] = held + 1
                detail = f"item {item} → {held + 1}"
            elif held <= 0:
                applied, detail = False, f"item {item} is not in the inventory"
            else:
                if held - 1 <= 0:
                    post["inventory"].pop(item, None)
                else:
                    post["inventory"][item] = held - 1
                detail = f"item {item} → {max(held - 1, 0)}"
        elif ns == "gives_quest":
            quest = slots.get("entity_id", "")
            post["quests"][quest] = "active"
            detail = f"quest {quest} → active"
        elif ns == "advance_quest":
            quest = slots.get("entity_id", "")
            current = post["quests"].get(quest, "not_started")
            wanted = slots.get("state") or _next_quest_state(spec, current)
            post["quests"][quest] = wanted
            detail = f"quest {quest}: {current} → {wanted}"
        elif ns == "set_flag":
            key = slots.get("key", "")
            value = slots.get("value")
            post["flags"][key] = True if value is None else value == "true"
            detail = f"flag {key} → {str(post['flags'][key]).lower()}"
        else:
            applied, detail = False, f"no applier for effect {ns!r} in this canon build"
        firings.append({
            "token": parsed.token, "namespace": ns, "applied": applied, "detail": detail,
            "engine_evaluable": evaluable, "engine_reason": engine_reason,
        })
    return post, firings
