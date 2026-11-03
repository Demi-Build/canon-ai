"""`game_coder`'s tools — the engine copy's read, sync and code write (row P1-A7.5).

``register_code_tools(registry, pack_dir, *, actor_for)`` registers the three
Phase 1 §4.A rows row A3 deliberately left for "the row that needs them"
(``tools_read``'s own note): ``engine_status`` (auto), ``engine_sync`` (ask)
and ``edit_project_code`` (ask + the §7.1 gate ladder). They are the tools
``roster/game_coder.json`` already lists — until this row they resolved to
nothing and the roster report named them ``missing``, loudly, which is what
made the gap visible.

Each is a THIN in-process wrapper over a canon verb (D3: imports, not
subprocesses), exactly like every other tool module here:

- ``engine_status`` → ``canon.packs.platformer.godot_export.engine_status``
  plus ``canon.engine_ops.code_evolved`` (the probe block, so one answer
  carries both the file states and what they MEAN).
- ``engine_sync``   → ``godot_export.engine_sync`` (fail-closed on hand
  edits; it refuses a modified file by name).
- ``edit_project_code`` → ``canon.engine_ops.edit_project_code`` — doctrine 1
  end to end (wall → validate → stamp → journal → CAS), inside row A4's
  ``journal_window`` so the events land on THIS call's sink and the run card
  attributes them correctly.

The gate ladder is NOT a tool. It runs automatically after any code edit, in
the same run, through A7's verify loop (``RunManager.verify_run`` →
``canon.agent.gates.run_ladder``) — one verification path, not two, and not
something a specialist can decline to call.

``engine_sync`` is registered NEVER-ALWAYS-ALLOWABLE (A4.5's
``forbid_always``): ``force=true`` overwrites the user's own hand-edited
engine files, and a standing grant that quietly authorises that later is
exactly what the per-instance rule exists to prevent.

Deliberately absent, by row ownership: the promoted-pygame copy and its
ladder (W2.0), ``game_coder``'s tuning smoke (W2.1), the panel's code-diff
card (A5 — this module feeds it the ``diff`` block), play/launch tools (W2.0).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from canon.agent.actors import CallContext
from canon.agent.registry import Tool, ToolRegistry
from canon.agent.tools_read import compact, validate_input
from canon.agent.tools_write import with_journal
from canon.llm.chat import ToolSpec

#: Tiers (data, like every tier): the read never asks, the two writes do.
CODE_READ_TIER = "auto"
CODE_WRITE_TIER = "ask"

#: Registration order (= the order every request offers them).
CODE_TOOL_NAMES: tuple[str, ...] = ("engine_status", "engine_sync", "edit_project_code")

#: Why ``engine_sync`` can never ride a standing grant.
SYNC_NEVER_ALWAYS = (
    "engine sync can overwrite hand-edited engine files (--force), so it confirms per instance — a standing "
    "grant would authorise a future overwrite of the user's own code"
)


# ---------------------------------------------------------------------------
# The tool bodies — each ``(pack, input, call) -> JSON-able``
# ---------------------------------------------------------------------------


def engine_status(pack: Path, _input: dict, _call: CallContext | None = None) -> dict:
    """The engine copy's file states + what they mean (pure read)."""
    from canon.engine_ops import code_evolved

    probe = code_evolved(pack)
    out: dict[str, Any] = {"engine_copy": probe}
    if probe.get("present"):
        from canon.packs.platformer import godot_export

        out["status"] = godot_export.engine_status(pack)
    return out


def engine_sync(pack: Path, tool_input: dict, call: CallContext) -> dict:
    from canon.packs.platformer import godot_export

    dry_run = bool(tool_input.get("dry_run"))
    force = bool(tool_input.get("force"))
    return with_journal(
        pack,
        lambda: godot_export.engine_sync(
            pack, dry_run=dry_run, force=force, actor=call.actor, session=call.conversation
        ),
    )


def edit_project_code(pack: Path, tool_input: dict, call: CallContext) -> dict:
    from canon.engine_ops import edit_project_code as verb

    return with_journal(
        pack,
        lambda: verb(
            pack,
            str(tool_input["path"]),
            str(tool_input["diff"]),
            actor=call.actor,
            session=call.conversation,
        ),
    )


# ---------------------------------------------------------------------------
# Chip copy
# ---------------------------------------------------------------------------


def describe_edit(tool_input: dict) -> str:
    return f"edit the project's own {tool_input.get('path')}"


def describe_sync(tool_input: dict) -> str:
    if tool_input.get("dry_run"):
        return "check what an engine sync would change"
    if tool_input.get("force"):
        return "sync the engine runtime, OVERWRITING hand-edited files"
    return "sync the engine runtime from canon's template"


TARGETS: dict[str, Callable[[dict], str]] = {
    "engine_sync": describe_sync,
    "edit_project_code": describe_edit,
}


# ---------------------------------------------------------------------------
# Specs + registration
# ---------------------------------------------------------------------------

_EDIT_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": (
                "Pack-relative file inside THIS project's own engine copy (godot/** today, e.g. 'godot/main.gd'). "
                "canon's source, the shared engine template and every other pack are refused by name."
            ),
        },
        "diff": {
            "type": "string",
            "description": (
                "A unified diff of that file: '@@ -12,4 +12,6 @@' hunk headers, ' ' context, '-' removed, '+' added. "
                "Read the file first and diff against its CURRENT text — a hunk whose context is not found refuses "
                "the whole call and writes nothing."
            ),
        },
    },
    "required": ["path", "diff"],
    "additionalProperties": False,
}

_SYNC_SCHEMA = {
    "type": "object",
    "properties": {
        "dry_run": {"type": "boolean", "description": "Report what would change; write nothing."},
        "force": {
            "type": "boolean",
            "description": (
                "Overwrite hand-edited/agent-edited runtime files too. They are REFUSED by name by default; "
                "forcing throws that work away."
            ),
        },
    },
    "additionalProperties": False,
}

_TOOLS: dict[str, tuple[str, dict, Callable[..., Any], str]] = {
    "engine_status": (
        "How this project's engine copy compares with canon's template, per file: current / stale (template moved "
        "on) / modified (someone edited it — sync will refuse to overwrite it, and by whom) / unstamped / missing. "
        "Also answers whether the pack is CODE-EVOLVED, which you must disclose before running or capturing "
        "anything with this engine.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        engine_status,
        "reads godot/.engine.json and the runtime files (no writes)",
    ),
    "engine_sync": (
        "Refresh this project's engine runtime from canon's current template (canon engine sync). Fail-closed: a "
        "file that differs from its own stamp was edited by a human or by you, and is refused BY NAME rather than "
        "overwritten. Use dry_run first.",
        _SYNC_SCHEMA,
        engine_sync,
        "writes the pack's engine runtime files; journals import on engine:godot",
    ),
    "edit_project_code": (
        "Change gameplay code in THIS project's own engine copy (godot/main.gd and its siblings). Ask-tier with the "
        "full diff. The file is stamped 'modified' so the attribution survives and engine sync will refuse to "
        "overwrite it, the edit is journaled with before/after hashes (restore reverts it in one click), and the "
        "§7.1 gate ladder runs automatically afterwards — syntax, headless boot, scripted smoke, validate_level on "
        "affected levels. Never claim the change works before the ladder is green.",
        _EDIT_SCHEMA,
        edit_project_code,
        "writes one file in the pack's own engine copy; stamps it modified; journals edit on code:<path>",
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


def code_tool_specs() -> list[ToolSpec]:
    """The specs alone (the eval corpus and the panel's tool list)."""
    return [ToolSpec(name=name, description=desc, input_schema=schema) for name, (desc, schema, _, _) in _TOOLS.items()]


def register_code_tools(
    registry: ToolRegistry,
    pack_dir: str | Path,
    *,
    actor_for: Callable[[], CallContext],
) -> list[str]:
    """Register the three engine-copy tools for ``pack_dir``; returns the names."""
    pack = Path(pack_dir)
    engine = registry.permissions
    names: list[str] = []
    for name in CODE_TOOL_NAMES:
        description, schema, body, touches = _TOOLS[name]
        tier = CODE_READ_TIER if name == "engine_status" else CODE_WRITE_TIER
        registry.register(
            Tool(
                spec=ToolSpec(name=name, description=description, input_schema=schema),
                tier=tier,
                run=_bind(name, schema, body, pack, actor_for),
                touches=touches,
            )
        )
        if name in TARGETS and hasattr(engine, "describe"):
            engine.describe(name, TARGETS[name])
        names.append(name)
    if hasattr(engine, "forbid_always"):
        engine.forbid_always("engine_sync", SYNC_NEVER_ALWAYS)
    return names


__all__ = [
    "CODE_READ_TIER",
    "CODE_TOOL_NAMES",
    "CODE_WRITE_TIER",
    "SYNC_NEVER_ALWAYS",
    "TARGETS",
    "code_tool_specs",
    "edit_project_code",
    "engine_status",
    "engine_sync",
    "register_code_tools",
]
