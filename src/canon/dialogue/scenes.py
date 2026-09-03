"""Scenes — the ``type: "scene"`` event row, its resolution and its walk
(Phase 0 §7.1, P0 paper P.1.5 and P.9 S7; row P0-9).

S7, decided: a scene lives in ``events/events.json`` as a row whose ``type``
is ``scene``, sharing the 3000 event id space — one store, three readers (NPC,
quest, scene surfaces). It NEVER gets an ``event_positions`` entry, so the
engine, which loads an unknown ``type`` as a ``CombatEvent``, is never handed
one to trigger. That single omission is the whole safety property; the writer
below enforces it and ``validate`` re-checks it.

The sub-shape (P.1.5, `PLAN.md:96-108`): ``id · type:"scene" · title ·
actors[{character_id, required}] · settings[Token] · trigger · once ·
on_finish[Token] · lines[]``, where a line is ``{k:"line", n, speaker, text,
conditions[]}`` or ``{k:"choice", n, options[{text, to, conditions[]}]}``.
``trigger`` and ``once`` come from ``DialogueSpec.scene`` — template data, not
an enum here.

The walk (``scene test``) answers the one control scenes need that trees do
not (README, screen 08): actor presence. An absent REQUIRED actor cancels the
scene and says so; an absent OPTIONAL actor's lines are skipped and NAMED
("line 05 will be skipped — 1004 is absent") rather than silently vanishing.

Deliberately absent, by row ownership: the live in-game scene in either
runtime (Phase 2 W2.2); the cradle scene surface (wave 3).
"""

from __future__ import annotations

from typing import Any

from canon.dialogue.evaluator import apply_effects, evaluate_conditions, normalize_state
from canon.packs.spec import DialogueSpec

__all__ = ["SCENE_FIELDS", "blank_scene", "is_scene", "normalize_scene", "walk_scene"]

#: The scene-only keys of an event row (the ones the ``event`` EntityKind
#: routes to this verb — P.1.5's ``routed`` map).
SCENE_FIELDS: tuple[str, ...] = (
    "title", "actors", "settings", "trigger", "once", "on_finish", "lines",
)


def is_scene(row: Any, spec: DialogueSpec) -> bool:
    event_type = str((spec.scene or {}).get("event_type") or "scene")
    return isinstance(row, dict) and str(row.get("type")) == event_type


def blank_scene(scene_id: Any, spec: DialogueSpec, *, title: str = "") -> dict[str, Any]:
    """A new scene row with the template's own defaults (trigger = the first
    declared trigger, ``once`` = the declared default).

    ``name`` and ``description`` are emitted even though they are not part of
    the P.1.5 scene sub-shape: the engine loads EVERY row of
    ``events/events.json`` through ``create_event_from_data``, an unknown
    ``type`` lands on ``CombatEvent``, and that model REQUIRES both fields —
    so a scene without them would fail the whole pack's registry load rather
    than being harmlessly ignored. Extra keys are ignored by the model, so the
    scene's own fields ride along safely (P.9 S7's "one store, three
    readers", made true rather than merely intended)."""
    scene = spec.scene or {}
    triggers = list(scene.get("triggers") or [])
    label = title or f"Scene {scene_id}"
    return {
        "id": scene_id,
        "type": str(scene.get("event_type") or "scene"),
        "name": label,
        "description": "",
        "title": label,
        "actors": [],
        "settings": [],
        "trigger": triggers[0] if triggers else "enter_room",
        "once": bool(scene.get("once", True)),
        "on_finish": [],
        "lines": [],
    }


def normalize_scene(row: dict, spec: DialogueSpec) -> dict[str, Any]:
    """A scene row with every P.1.5 key present and typed — never a rewrite of
    keys the row already carries (an event row's ``name``/``description``
    survive untouched, so a scene stays a legal event row)."""
    out = dict(row)
    scene = spec.scene or {}
    triggers = list(scene.get("triggers") or [])
    out.setdefault("type", str(scene.get("event_type") or "scene"))
    out.setdefault("title", "")
    # The two engine-required Event fields (see ``blank_scene``): a scene row
    # that lost them would break the pack's registry load, so a save puts them
    # back rather than leaving a pack that will not boot.
    out.setdefault("name", out.get("title") or f"Scene {out.get('id')}")
    out.setdefault("description", "")
    out["actors"] = [
        {"character_id": str(a.get("character_id")), "required": bool(a.get("required", True))}
        for a in (out.get("actors") or [])
        if isinstance(a, dict)
    ]
    out["settings"] = [str(s) for s in (out.get("settings") or [])]
    out["on_finish"] = [str(s) for s in (out.get("on_finish") or [])]
    out.setdefault("trigger", triggers[0] if triggers else "enter_room")
    out["once"] = bool(out.get("once", scene.get("once", True)))
    lines: list[dict[str, Any]] = []
    for position, line in enumerate(out.get("lines") or [], start=1):
        if not isinstance(line, dict):
            continue
        if str(line.get("k")) == "choice":
            lines.append({
                "k": "choice",
                "n": int(line.get("n") or position),
                "options": [
                    {
                        "text": str(o.get("text", "")),
                        "to": o.get("to"),
                        "conditions": [str(c) for c in (o.get("conditions") or [])],
                    }
                    for o in (line.get("options") or [])
                    if isinstance(o, dict)
                ],
            })
        else:
            lines.append({
                "k": "line",
                "n": int(line.get("n") or position),
                "speaker": None if line.get("speaker") is None else str(line.get("speaker")),
                "text": str(line.get("text", "")),
                "conditions": [str(c) for c in (line.get("conditions") or [])],
            })
    out["lines"] = lines
    return out


def walk_scene(
    scene: dict,
    state: Any,
    *,
    spec: DialogueSpec,
    engine_blocks: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Play *scene* against a simulated state. Returns the settings verdict,
    the per-line transcript with skips NAMED, the choice blocks' per-option
    gates, and the post-``on_finish`` state.

    Everything evaluates at ``scene`` scope, which is the ONLY scope where the
    ``actor:`` namespace is legal (§7.1) — a tree carrying one is rejected by
    the grammar with that reason, not silently ignored.
    """
    sim = normalize_state(state)
    settings = evaluate_conditions(
        scene.get("settings"), sim, scope="scene", spec=spec, engine_blocks=engine_blocks
    )
    absent_required = [
        a["character_id"]
        for a in scene.get("actors") or []
        if a.get("required") and sim["actors"].get(str(a["character_id"]), "absent") == "absent"
    ]
    plays = settings["pass"] and not absent_required
    blocked = list(settings.get("conditions") or [])
    transcript: list[dict[str, Any]] = []
    for line in scene.get("lines") or []:
        entry: dict[str, Any] = {"n": line.get("n"), "k": line.get("k")}
        if line.get("k") == "choice":
            entry["options"] = [
                {
                    "text": option.get("text", ""),
                    "to": option.get("to"),
                    **evaluate_conditions(
                        option.get("conditions"), sim, scope="scene", spec=spec,
                        engine_blocks=engine_blocks,
                    ),
                }
                for option in line.get("options") or []
            ]
            entry["played"] = plays
            transcript.append(entry)
            continue
        gates = evaluate_conditions(
            line.get("conditions"), sim, scope="scene", spec=spec, engine_blocks=engine_blocks
        )
        entry.update({"speaker": line.get("speaker"), "text": line.get("text"), **gates})
        speaker = None if line.get("speaker") is None else str(line.get("speaker"))
        absent = speaker is not None and sim["actors"].get(speaker, "absent") == "absent"
        optional = any(
            str(a.get("character_id")) == speaker and not a.get("required")
            for a in scene.get("actors") or []
        )
        if not plays:
            entry["played"] = False
            entry["skipped_because"] = (
                f"the scene does not play — required actor(s) {absent_required} absent"
                if absent_required
                else f"the scene's own gates fail: {settings['failing_reason']}"
            )
        elif absent and optional:
            entry["played"] = False
            entry["skipped_because"] = f"line {line.get('n')} will be skipped — {speaker} is absent"
        elif not gates["pass"]:
            entry["played"] = False
            entry["skipped_because"] = f"line {line.get('n')} is gated: {gates['failing_reason']}"
        else:
            entry["played"] = True
        transcript.append(entry)
    post, firings = apply_effects(
        scene.get("on_finish") if plays else [], sim, spec=spec, engine_blocks=engine_blocks
    )
    return {
        "scene": scene.get("id"),
        "title": scene.get("title"),
        "plays": plays,
        "settings": settings,
        "blocked_by": None if plays else (
            f"required actor(s) {absent_required} absent" if absent_required else settings["failing_reason"]
        ),
        "absent_required_actors": absent_required,
        "gates": _tally(blocked + [c for line in transcript for c in line.get("conditions") or []]),
        "transcript": transcript,
        "on_finish": firings,
        "state": sim,
        "post_effect_state": post,
    }


def _tally(conditions: list[dict]) -> dict[str, int]:
    """The statusbar aggregate — ``gates N pass · N fail · N unevaluable``."""
    out = {"pass": 0, "fail": 0, "unevaluable": 0, "error": 0}
    for condition in conditions:
        out[str(condition.get("verdict", "error"))] = out.get(str(condition.get("verdict", "error")), 0) + 1
    return out
