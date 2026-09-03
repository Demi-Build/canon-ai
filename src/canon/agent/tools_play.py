"""The ask-tier play-surface tool contract (row P1-A4.5, C19; Phase 1 §4.D).

``register_play_tools(registry, pack_dir, *, actor_for)`` registers
``sandbox_level(level_id?, spawn?)`` — the ONE play-surface tool this row
owns: a thin in-process wrapper over ``canon level sandbox`` (extended in
the same row with ``--level`` and ``--spawn x,y``). Default = the reserved
draft room (``ensure_sandbox_level``, idempotent, journals only the first
time); ``level_id`` sandboxes an existing level (a read); ``spawn`` names
the start cell and rides to the launched harness as ``PLAT_SPAWN`` (the
smallest extension of the ``PLAT_*`` hooks, kept by W2.0).

The tool answers the CONTRACT only — ``{level_id, stage_id, created,
draft, spawn, launch: {env}}``: launching is the existing play path
(cradle's ``play_level`` with ``sandbox`` + ``spawn``; W2.0's
``session_launch`` re-points the backend, never this contract — master
§3.0-I). ``play_level`` / ``play_game`` as tools are W2.0's seam.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from canon.agent.actors import CallContext
from canon.agent.registry import Tool, ToolRegistry
from canon.agent.tools_read import compact, validate_input
from canon.agent.tools_write import journal_window
from canon.llm.chat import ToolSpec

PLAY_TIER = "ask"

SANDBOX_SCHEMA = {
    "type": "object",
    "properties": {
        "level_id": {
            "type": "string",
            "description": "An existing level id to sandbox (default: the reserved draft room, created on first use).",
        },
        "spawn": {
            "type": "array",
            "items": {"type": "integer", "minimum": 0},
            "minItems": 2,
            "maxItems": 2,
            "description": "[x, y] start cell for the harness (default: the level's spawn).",
        },
    },
    "additionalProperties": False,
}

SANDBOX_DESCRIPTION = (
    "Prepare a movement sandbox: the reserved flat DRAFT room (created once, then reused) or an existing level, "
    "played with no win condition and a HUD naming the animation state the game picked and why. Returns the "
    "level to launch and the launch env (PLAT_SANDBOX, PLAT_SPAWN); the user's play surface does the launching."
)


def sandbox_existing_level(pack_dir: str | Path, level_id: str) -> dict:
    """``level sandbox --level <id>`` and the tool's ``level_id``: locate the
    level's directory under ``level/<stage>/<id>/`` and answer the shape
    ``ensure_sandbox_level`` does (``created: false``; ``draft`` from its
    ``level.json`` ``review_status``) — a read, so a missing id is a
    ``FileNotFoundError``, never a scaffold. The CLI imports this so both
    surfaces answer one shape."""
    pack = Path(pack_dir)
    level_root = pack / "level"
    matches = sorted(p for p in level_root.glob(f"*/{level_id}") if p.is_dir()) if level_root.is_dir() else []
    if not matches:
        raise FileNotFoundError(f"no level {level_id!r} under {level_root} (level/<stage>/<id>/)")
    level_dir = matches[0]
    draft = False
    meta = level_dir / "level.json"
    if meta.is_file():
        try:
            draft = json.loads(meta.read_text(encoding="utf-8")).get("review_status") == "draft"
        except (OSError, ValueError):
            draft = False
    return {"level_id": level_id, "stage_id": level_dir.parent.name, "created": False, "draft": draft}


def sandbox_level(pack: Path, tool_input: dict, call: CallContext) -> dict:
    from canon.adapters.platformer_write import ensure_sandbox_level

    level_id = tool_input.get("level_id")
    spawn = tool_input.get("spawn")
    if level_id is not None:
        result = dict(sandbox_existing_level(pack, level_id))
    else:
        # The first-use create is a write: run it inside row A4's journal
        # window so the created room lands on THIS call's journal sink (the
        # run card's artifacts) and never on a concurrent call's.
        with journal_window(pack):
            result = dict(ensure_sandbox_level(pack, actor=call.actor, session=call.conversation))
    result["spawn"] = list(spawn) if spawn is not None else None
    env = {"PLAT_SANDBOX": "1"}
    if spawn is not None:
        env["PLAT_SPAWN"] = f"{int(spawn[0])},{int(spawn[1])}"
    result["launch"] = {"env": env, "engine": "pygame", "via": "play_level"}
    return result


def describe_sandbox(tool_input: dict) -> str:
    target = tool_input.get("level_id") or "the sandbox room"
    spawn = tool_input.get("spawn")
    return f"sandbox {target}" + (f" at {spawn[0]},{spawn[1]}" if spawn else "")


def register_play_tools(
    registry: ToolRegistry,
    pack_dir: str | Path,
    *,
    actor_for: Callable[[], CallContext],
) -> list[str]:
    """Register ``sandbox_level`` (tier ``ask``) into ``registry``; returns the names."""
    pack = Path(pack_dir)

    def run(tool_input: dict) -> str:
        validate_input("sandbox_level", SANDBOX_SCHEMA, tool_input)
        return compact(sandbox_level(pack, tool_input, actor_for()))

    spec = ToolSpec(name="sandbox_level", description=SANDBOX_DESCRIPTION, input_schema=SANDBOX_SCHEMA)
    registry.register(
        Tool(
            spec=spec,
            tier=PLAY_TIER,
            run=run,
            touches="creates the reserved draft room on first use (journals create); otherwise reads",
        )
    )
    engine = registry.permissions
    if hasattr(engine, "describe"):
        engine.describe("sandbox_level", describe_sandbox)
    return ["sandbox_level"]


__all__ = [
    "PLAY_TIER",
    "SANDBOX_DESCRIPTION",
    "SANDBOX_SCHEMA",
    "register_play_tools",
    "sandbox_existing_level",
    "sandbox_level",
]
