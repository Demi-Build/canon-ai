"""System prompt assembly — the four layers of Phase 1 §3.1 (row P1-A4.5; user decision: the service assembles).

``assemble`` builds the prompt every run resends::

    1. Core (static, versioned in canon)   roster/core.md — identity + law
    2. Pack context (per conversation)     pack_context(pack_dir): type, kinds
                                           + counts, capabilities, engines and
                                           modified files, validation summary,
                                           spend to date when the ledger exists
    3. UI state (per message, latest only) the request body's ``ui_state``
    4. Specialist layer (per run)          the role prompt + matched skills
                                           (+ the task brief and refs for a
                                           delegated run)

The service uses it when ``POST /conversations`` carries no ``system``;
``GET /conversations/{id}/prompt`` returns the assembled text for the
inspectable read-only view (the ``prompt show`` precedent). Everything
pack-derived comes from ``canon.packs.pack_info`` (row P0-3's probe), the
engine-status verb, ``validate_level`` per level and ``canon.spend`` — no
number here is typed by hand.

Deliberately absent: compaction (§3.4), ``@``-attached context beyond what
``ui_state`` carries (A5 attaches; the service keeps the latest copy). Row
A7's routing work needed nothing here either: routing is prompt + eval work
on the FOREMAN (§5.1), so it lives in the roster's ``foreman.md`` (data) and
is measured by the routing corpus — this assembler only lays the layers out.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from canon.agent.roster import Specialist, core_law
from canon.agent.skills import Skill
from canon.engine_ops import TEMPLATE_PHYSICS_NOTE

#: Levels probed for the validation summary — beyond this the summary
#: says "first N of M" rather than probing a wide pack on every turn.
VALIDATION_PROBE_CAP = 12

#: Row P1-A7.5 / Phase 1 §7.1: what the model must say before it runs a
#: code-evolved engine copy. The probe surfaces ``modified``/``unstamped``
#: BEFORE any agent-triggered execution and the agent must disclose it.
CODE_EVOLVED_DISCLOSURE = (
    "This project's engine copy has hand- or agent-edited files. Say so in the transcript BEFORE you run, capture "
    "or launch anything with it (§7.1), and name the files."
)


def _grid_ids(pack: Path, template: str) -> list[dict[str, str]]:
    from canon.agent.tools_read import grid_ids

    return grid_ids(pack, template)


def pack_context(pack_dir: str | Path) -> dict[str, Any]:
    """The pack-context layer as data: ``pack_info`` plus the summaries
    the prompt renders. Every field is probed; a probe that fails names
    its failure instead of guessing (``problems``)."""
    from canon.packs import pack_info

    pack = Path(pack_dir)
    problems: list[str] = []
    try:
        info = pack_info(pack)
    except Exception as exc:  # noqa: BLE001 — the prompt must still assemble; the problem is named
        return {"pack": str(pack), "problems": [f"pack_info failed: {type(exc).__name__}: {exc}"]}

    kinds = {kind: entry.get("count") for kind, entry in (info.get("entities") or {}).items()}
    grids: dict[str, list[str]] = {}
    for kind in info.get("grids") or {}:
        try:
            from canon.packs import resolve_pack

            template = str(resolve_pack(pack).spec.grids[kind].path_template)
            # The id is the template's LAST placeholder (level_id, map_id, …).
            grids[kind] = [list(entry.values())[-1] if entry else "" for entry in _grid_ids(pack, template)]
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{kind} ids: {type(exc).__name__}: {exc}")

    # The engine-copy probe is ``pack_info``'s (row A7.5 moved it there so one
    # answer serves the prompt, `canon pack info`, cradle and the agent's
    # §7.1 disclosure). This renders it; it does not re-probe.
    copy = info.get("engine_copy") if isinstance(info.get("engine_copy"), dict) else {}
    if copy.get("problem"):
        problems.append(f"engine status: {copy['problem']}")
    engines: list[dict[str, Any]] = []
    for engine in info.get("engines") or []:
        entry: dict[str, Any] = {"id": engine.get("id"), "primary": bool(engine.get("primary"))}
        if copy.get("present") and engine.get("id") == copy.get("engine"):
            entry["modified"] = sorted({*(copy.get("modified") or []), *(copy.get("unstamped") or [])})
            entry["stale"] = list(copy.get("stale") or [])
            entry["code_evolved"] = bool(copy.get("code_evolved"))
        engines.append(entry)

    validation: dict[str, Any] = {}
    level_ids = grids.get("level") or []
    if level_ids:
        try:
            from canon.packs.platformer.ops import validate_level

            probed = level_ids[:VALIDATION_PROBE_CAP]
            failing = []
            for level_id in probed:
                report = validate_level(pack, level_id)
                if not report.get("ok", True):
                    failing.append(level_id)
            validation = {"probed": len(probed), "of": len(level_ids), "failing": failing}
        except Exception as exc:  # noqa: BLE001
            problems.append(f"validation: {type(exc).__name__}: {exc}")

    spend: dict[str, Any] | None = None
    ledger = pack / ".canon" / "spend.jsonl"
    if ledger.is_file():
        try:
            from canon.spend import summarize

            summary = summarize(pack)
            spend = {"total_actual_usd": summary.get("total_actual_usd"), "count": summary.get("count")}
        except Exception as exc:  # noqa: BLE001
            problems.append(f"spend ledger: {type(exc).__name__}: {exc}")

    return {
        "pack": str(pack),
        "pack_type": info.get("pack_type"),
        "label": info.get("label"),
        "template": info.get("template"),
        "capabilities": list(info.get("capabilities") or []),
        "kinds": kinds,
        "grids": grids,
        "engines": engines,
        "validation": validation,
        "spend": spend,
        "problems": problems,
    }


def render_pack_context(context: dict[str, Any]) -> str:
    """The pack-context layer as prose the model reads."""
    lines = [f"Pack: {context.get('pack')}"]
    if context.get("pack_type"):
        template = context.get("template") or {}
        version = template.get("version")
        lines.append(
            f"Type: {context['pack_type']} ({context.get('label') or context['pack_type']}; template "
            f"{template.get('id', context['pack_type'])}"
            + (f" v{version}" if version else ", pre-registry")
            + ")"
        )
    if context.get("capabilities"):
        lines.append("Capabilities: " + ", ".join(context["capabilities"]))
    kinds = context.get("kinds") or {}
    if kinds:
        lines.append("Kinds: " + ", ".join(f"{kind} × {count}" for kind, count in kinds.items()))
    for kind, ids in (context.get("grids") or {}).items():
        shown = ", ".join(ids[:40]) + (" …" if len(ids) > 40 else "")
        lines.append(f"{kind.capitalize()}s ({len(ids)}): {shown}")
    for engine in context.get("engines") or []:
        note = f"Engine: {engine.get('id')}" + (" (primary)" if engine.get("primary") else "")
        if engine.get("code_evolved"):
            note += " — CODE-EVOLVED"
        if engine.get("modified"):
            note += " — modified/unstamped: " + ", ".join(engine["modified"])
        if engine.get("stale"):
            note += " — stale: " + ", ".join(engine["stale"])
        lines.append(note)
        if engine.get("code_evolved"):
            # Row A7.5 / Phase 1 §7.1: the disclosure obligation and master
            # §3.0-I's interim rule, stated where the model reads them.
            lines.append("  " + CODE_EVOLVED_DISCLOSURE)
            lines.append("  " + TEMPLATE_PHYSICS_NOTE)
    validation = context.get("validation") or {}
    if validation:
        failing = validation.get("failing") or []
        lines.append(
            f"Validation: {validation.get('probed')} of {validation.get('of')} levels probed; "
            + ("all clean" if not failing else "failing: " + ", ".join(failing))
        )
    spend = context.get("spend")
    if spend:
        lines.append(f"Spend to date: ${float(spend.get('total_actual_usd') or 0):.2f} over {spend.get('count')} op(s)")
    for problem in context.get("problems") or []:
        lines.append(f"Probe problem: {problem}")
    return "\n".join(lines)


def render_ui_state(ui_state: dict[str, Any] | None) -> str:
    if not ui_state:
        return "(no UI state attached)"
    return json.dumps(ui_state, indent=None, sort_keys=True, default=str)


def assemble(
    pack_dir: str | Path,
    specialist: Specialist,
    *,
    ui_state: dict[str, Any] | None = None,
    task_brief: str | None = None,
    refs: list[Any] | None = None,
    skills: list[Skill] | tuple[Skill, ...] = (),
    core: str | None = None,
    context: dict[str, Any] | None = None,
) -> str:
    """The assembled system prompt for one run (see the module docstring).

    ``core`` and ``context`` let a caller reuse an already-read law and an
    already-probed pack context (the service probes once per conversation
    and re-probes after external changes); ``None`` reads/probes here.
    """
    law = core if core is not None else core_law()
    probed = context if context is not None else pack_context(pack_dir)
    parts = [
        law.rstrip(),
        "# Pack context\n\n" + render_pack_context(probed),
        "# UI state (latest)\n\n" + render_ui_state(ui_state),
        f"# Role: {specialist.label} (`{specialist.id}`)\n\n" + specialist.role_prompt.rstrip(),
    ]
    if skills:
        blocks = [f"## Skill: {skill.id} ({skill.source})\n\n{skill.body.rstrip()}" for skill in skills]
        parts.append("# Skills\n\n" + "\n\n".join(blocks))
    if task_brief is not None or refs:
        task = ["# Task"]
        if task_brief is not None:
            task.append(task_brief.rstrip())
        if refs:
            task.append("Refs: " + json.dumps(list(refs), default=str))
        parts.append("\n\n".join(task))
    return "\n\n".join(parts) + "\n"


__all__ = [
    "CODE_EVOLVED_DISCLOSURE",
    "VALIDATION_PROBE_CAP",
    "assemble",
    "pack_context",
    "render_pack_context",
    "render_ui_state",
]
