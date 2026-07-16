"""Level phases: LayoutStampPhase (Layout Agent → DSL → Stamp Tool →
collision.npz) and PlacementPhase (Entity Agent → validated placements).

Invariants exercised: I3 (agent emits DSL, the deterministic stamp writes
cells), I5-lite (placements validated against the stamped grid — nothing
spawns on spikes), and the §6.3 hash contract (adapter's content hash for
collision.npz lands on Level.collision_hash). Since 3b both phases are
registry-driven (tiles) and the placement phase speaks the game's variant
vocabulary; checkpoints land in the triggers layer.

Since 3b-2 the per-LEVEL work lives in module-level functions shared by
two callers: the sequential ``Phase``es below (legacy `run_pipeline`
path) and the per-(step, level) DAG nodes in ``dag.py`` (orchestrated
path). One body, two schedulers — the outputs must be byte-identical.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from canon.bible.artifacts import make_artifact_id
from canon.bible.platformer import Level, Placement, SparseMaskEntry
from canon.llm.parsing import extract_json_object
from canon.pipeline.retry import default_token_escalation, retry_with_feedback
from canon.pipeline.rng import derive_rng
from canon.skeleton.core import roll_skeleton
from canon.skeleton.loader import load_skeleton_spec
from examples.platformer_pack.combat import DEFAULT_COMBAT, CombatSpec
from examples.platformer_pack.dsl import (
    DslError,
    StampResult,
    parse_dsl,
    rebase_dsl,
    stamp,
)
from examples.platformer_pack.graphics import DEFAULT_GRAPHICS, GraphicsSpec
from examples.platformer_pack.movement import DEFAULT_MOVEMENT, PlayerMovementSpec
from examples.platformer_pack.phases import (
    SCHEMAS_DIR,
    _stamp_metadata,
    resolved_model,
    stamp_provenance,
    warn,
)
from examples.platformer_pack.rules import DEFAULT_RULES, GameRules
from examples.platformer_pack.sections import (
    DEFAULT_VOCAB,
    SECTION_OVERLAP,
    PlannedSection,
    SectionArchetype,
    composite,
    plan_level,
    section_owner_of_cell,
)
from examples.platformer_pack.tiles import DEFAULT_TILES, TileRegistry
from examples.platformer_pack.validate import (
    _body_stand,
    auto_bridge_grid,
    check_item_placements,
    check_placements,
    flyer_spot_exists,
    place_checkpoints_grid,
    place_exit,
    reachable_cells,
    repair_containment_grid,
    snap_spawn_grid,
    standable_cells,
    swimmer_spot_exists,
    volume_cells,
)
from examples.platformer_pack.variants import DEFAULT_VARIANTS, VariantSet

logger = logging.getLogger(__name__)

#: All per-level layer steps, in generation order — the "level" finalize
#: step (level.json) descends from every one of them in the §6.1 edge set.
LEVEL_STEPS = (
    "collision", "hazards", "triggers", "terrain", "background",
    "entities", "items", "foreground",
)


def _fallback_dsl(width: int) -> str:
    """A guaranteed-valid layout so a fully misbehaving LLM still yields a
    walkable (if boring) level instead of a dead pipeline."""
    return f"floor(0,{width - 1})\nspawn(2)\nexit({width - 3})"


def _whole_fallback(
    width: int, height: int, axis: str, tiles: TileRegistry
) -> StampResult:
    """The last-resort WHOLE-level fallback (used only when even a local
    section fallback can't stitch a walkable level): a flat floor for a
    horizontal level, a climbable LADDER with a summit exit for a vertical one —
    so a climb never degenerates into a side-scrolling floor (G6)."""
    if axis == "vertical":
        res = stamp(
            _section_fallback_dsl(width, height, 0, "vertical"),
            width, height, tiles=tiles, validate_markers=False,
        )
        exclude = {res.spawn} if res.spawn else set()
        res.exit = place_exit(res.grid, tiles, exclude=exclude, axis="vertical")
        return res
    return stamp(_fallback_dsl(width), width, height, tiles=tiles)


def _stage_for_level(ctx: Any, level_id: str):
    """The stage that owns ``level_id`` — level ids are globally unique
    (allocated in world order by StagePhase), so the Bible is the
    resolver; single-stage callers/tests that never planned stages fall
    back to the lone stage."""
    for stage in ctx.bible.stages.values():
        if level_id in stage.level_ids:
            return stage
    stages = list(ctx.bible.stages.values())
    if len(stages) == 1:
        return stages[0]
    raise KeyError(
        f"no stage owns level {level_id!r} — stages: "
        f"{ {s.stage_id: s.level_ids for s in stages} }"
    )


def _stage_number(ctx: Any, stage_id: str) -> int:
    """1-based position of the stage in world play order."""
    world = getattr(ctx.bible, "world", None)
    order = list(world.stage_ids) if world and world.stage_ids else list(
        ctx.bible.stages
    )
    return order.index(stage_id) + 1 if stage_id in order else 1


def world_stages(ctx: Any) -> list:
    """Stages in world play order — the iteration every multi-stage
    phase shares (sequential and DAG alike, for byte parity)."""
    world = getattr(ctx.bible, "world", None)
    order = list(world.stage_ids) if world and world.stage_ids else list(
        ctx.bible.stages
    )
    return [
        ctx.bible.stages[stage_id]
        for stage_id in order
        if stage_id in ctx.bible.stages
    ]


# ---------------------------------------------------------------------------
# Per-level bodies (shared by sequential phases and DAG nodes)
# ---------------------------------------------------------------------------


#: Fraction of ELIGIBLE levels composed as a VERTICAL climb (deterministic per
#: level). Only stages 2+ are eligible — stage-1 levels (a world's intro) stay
#: side-scrolling so the player learns the basics on flat ground first.
VERTICAL_FRACTION = 0.4


def _roll_level_axis(ctx: Any, level_id: str, phase_name: str, stage_number: int) -> str:
    """Deterministically pick a level's layout AXIS. Vertical climbs appear
    only from stage 2 on (a progression: horizontal intros, vertical variety
    later), on a per-level roll keyed independently of the dims/plan rolls."""
    if stage_number < 2:
        return "horizontal"
    seed = str(getattr(ctx.config, "seed", ""))
    r = derive_rng(seed, phase_name, level_id, "axis").random()
    return "vertical" if r < VERTICAL_FRACTION else "horizontal"


def _vertical_dims(width: int, height: int) -> tuple[int, int]:
    """Recast a rolled (wide, short) level into TALL + NARROW for a climb: the
    wide dimension becomes the HEIGHT (the ascent), the short becomes the WIDTH
    (a shaft). Clamped to sane bands — Chunk F widened them for taller climbs
    (a shaft up to 26 wide / 96 tall) now that horizontal levels reach ~132."""
    v_width = min(max(height, 16), 26)
    v_height = min(max(width, 48), 96)
    return v_width, v_height


def _roll_level_knobs(
    ctx: Any,
    level_id: str,
    index: int,
    *,
    phase_name: str,
    default_width: int,
    default_height: int,
) -> tuple[dict, int, int, str]:
    """The rolled difficulty knobs + dims + AXIS for a level — the DETERMINISTIC
    roll (difficulty escalates across the WORLD: stage position + level
    position, clamped to the schema's 1..3 table; the schema keys off this
    context, code computes the progression). A VERTICAL level recasts its dims
    to tall+narrow. Factored so the placement phase can recompute the exact
    same section plan (dims + axis) without a persisted field."""
    spec = load_skeleton_spec(SCHEMAS_DIR / "level_layout.json")
    stage = _stage_for_level(ctx, level_id)
    seed = str(getattr(ctx.config, "seed", ""))
    stage_number = _stage_number(ctx, stage.stage_id)
    roll_context = {
        "level_number": min(stage_number + index, 3),
        "stage_number": stage_number,
    }
    knobs = roll_skeleton(
        spec, derive_rng(seed, phase_name, level_id), context=roll_context
    )
    # Variable dims (GridDims, §4.2): schema-rolled per level; the defaults
    # are the fallback for schemas without them.
    width = int(knobs.get("grid_width", default_width))
    height = int(knobs.get("grid_height", default_height))
    axis = _roll_level_axis(ctx, level_id, phase_name, stage_number)
    if axis == "vertical":
        width, height = _vertical_dims(width, height)
    return knobs, width, height, axis


def level_section_plan(
    ctx: Any,
    level: Level,
    *,
    layout_phase: str = "plat:layout",
    default_width: int = 48,
    default_height: int = 16,
) -> list[PlannedSection]:
    """Recompute the (deterministic) section plan the layout phase stitched, so
    a LATER phase (placement) can read the section map without a persisted
    field. Uses the level's stored dims + a re-derived difficulty, keyed on the
    LAYOUT phase name so the roll matches ``_generate_sectioned_level`` exactly."""
    stage = _stage_for_level(ctx, level.level_id)
    index = (
        stage.level_ids.index(level.level_id)
        if level.level_id in stage.level_ids
        else 0
    )
    knobs, _, _, axis = _roll_level_knobs(
        ctx, level.level_id, index, phase_name=layout_phase,
        default_width=default_width, default_height=default_height,
    )
    difficulty = int(knobs.get("difficulty", 1))
    seed = str(getattr(ctx.config, "seed", ""))
    return plan_level(
        level.grid_width, level.grid_height, difficulty,
        derive_rng(seed, layout_phase, level.level_id, "plan"),
        axis=axis,
    ).sections


def section_encounter_summary(
    plan: list[PlannedSection],
    vocab: dict[str, SectionArchetype] = DEFAULT_VOCAB,
    axis: str = "horizontal",
) -> str:
    """A compact per-section ENCOUNTER map for the placement prompt — which
    whole-level ranges are combat/mixed (concentrate enemies) vs traversal
    (lighter). Text only (I3), derived from the section archetypes' encounter
    dimension; the range is columns (horizontal) or rows (vertical)."""
    label, off = ("cols", "x_off") if axis == "horizontal" else ("rows", "y_off")
    return "; ".join(
        f"{label} {getattr(ps, off)}-{getattr(ps, off) + ps.length - 1} "
        f"{ps.archetype} ({vocab[ps.archetype].encounter})"
        for ps in plan
        if ps.archetype in vocab
    )


@dataclass
class _SectionState:
    """One generated section: its plan slot, the accepted local DSL, the
    stamped local sub-grid, whether it fell back to flat, and its retry log."""

    ps: PlannedSection
    dsl: str
    res: StampResult
    fell_back: bool
    attempts: list[dict[str, Any]]


def _section_digest(idx: int, ps: PlannedSection, dsl_text: str) -> str:
    """One line of 'what this section built' for the sequential-handoff
    HISTORY block — op counts only (cheap cohesion context; the full DSL is
    reserved for the immediate predecessor, the only section that physically
    touches the receiver)."""
    counts: dict[str, int] = {}
    for op, _ in parse_dsl(dsl_text, skipped=[]):
        counts[op] = counts.get(op, 0) + 1
    ops = ", ".join(f"{n}x {op}" for op, n in sorted(counts.items()))
    return f"s{idx} {ps.archetype}: {ops or 'empty'}"


def _section_fallback_dsl(
    local_w: int, local_h: int, index: int, axis: str
) -> str:
    """A guaranteed-VALID (and REACHABLE) section (the per-section analogue of
    :func:`_fallback_dsl`).
    - horizontal: a flat floor; the FIRST section also plants the spawn.
    - vertical: a climbable ladder of staggered platforms (2 rows apart, dx 1
      so every step is jumpable); the FIRST (bottom) section also lays the
      ground floor + spawn.
    NO section declares the exit — the stitcher places it (goal mode)."""
    if axis == "vertical":
        lines: list[str] = []
        if index == 0:
            lines += [f"floor(0,{local_w - 1})", "spawn(2)"]
        y = local_h - 4
        i = 0
        while y >= 1:
            lines.append(f"platform({2 + i % 2},{y},2)")
            y -= 2
            i += 1
        return "\n".join(lines) or f"platform(2,{max(1, local_h - 4)},2)"
    base = f"floor(0,{local_w - 1})"
    return f"{base}\nspawn(2)" if index == 0 else base


def _generate_one_section(
    ctx: Any,
    *,
    level_id: str,
    brief: str,
    ps: PlannedSection,
    arch: SectionArchetype,
    index: int,
    total: int,
    local_w: int,
    local_h: int,
    axis: str,
    movement: PlayerMovementSpec,
    rules: GameRules,
    tiles: TileRegistry,
    seam: str | None,
    phase_name: str,
    initial_feedback: list[str] | None = None,
    previous: str | None = None,
    predecessor: str | None = None,
    predecessor_occupied: str | None = None,
    history: str | None = None,
    successor_occupied: str | None = None,
) -> _SectionState:
    """Generate ONE section: Layout Agent → local DSL → stamped local sub-grid
    at the section's own (``local_w`` x ``local_h``) dims. ``retry_with_feedback``
    covers LOCAL validity (stamps without a ``DslError``; the first section must
    plant a spawn). Falls back to a flat floor / climbable ladder.
    ``initial_feedback``/``previous`` seed a whole-level-driven regeneration's
    FIRST attempt (the stitcher routed a design problem here); the
    ``predecessor*``/``history``/``successor_occupied`` args are the
    sequential handoff (see :meth:`PlatformerPrompts.section_layout`)."""
    last_attempt: dict[str, str | None] = {"content": previous}

    def generate(
        feedback: list[str] | None = None, max_tokens: int | None = None
    ) -> str:
        # retry_with_feedback passes feedback=None on attempt 1 (kwarg name
        # MUST be `feedback` — _call_generate strips unknown kwargs); seed that
        # first call with any whole-level feedback the stitcher routed here.
        eff_fb = feedback if feedback is not None else initial_feedback
        request = ctx.prompts.section_layout(
            level_id, brief, ps.archetype, arch.flavor, arch.feature_bias,
            index, total, local_w, local_h, movement, rules=rules, tiles=tiles,
            seam=seam, intensity=arch.intensity, water=arch.water, axis=axis,
            previous=last_attempt["content"], feedback=eff_fb,
            predecessor=predecessor,
            predecessor_occupied=predecessor_occupied,
            history=history,
            successor_occupied=successor_occupied,
        )
        if max_tokens is not None:
            request.max_tokens = max_tokens
        content = ctx.llm.generate(
            request, phase=f"{phase_name}:{level_id}:s{index}"
        )
        last_attempt["content"] = content
        return content

    def validate(content: str) -> tuple[bool, list[str]]:
        try:
            skipped: list[str] = []
            ops = parse_dsl(content, skipped=skipped)
            if not ops:
                # Lenient parsing skips junk lines, but a reply that is ALL
                # junk must still fail — an empty section is not a design.
                return False, [
                    "Your reply contained no valid DSL ops at all — emit "
                    "the section as ops, one per line, nothing else."
                ]
            res = stamp(
                content, local_w, local_h, tiles=tiles,
                validate_markers=False, lenient=True,
            )
        except DslError as exc:
            return False, list(exc.problems)
        if index == 0 and res.spawn is None:
            return False, [
                "The FIRST section must declare exactly one spawn(x) on clear "
                "flat floor near the "
                + ("bottom-left." if axis == "vertical" else "left.")
            ]
        return True, []

    fallback = _section_fallback_dsl(local_w, local_h, index, axis)
    attempts: list[dict[str, Any]] = []
    # A climb section fills a 2D shaft, not a 1D strip — its DSL is far
    # denser per grid area (the paid l4 sections ran 55+ lines against a
    # 512-token budget, and a line-boundary truncation passes local
    # validation SILENTLY with the top of the climb missing). Vertical
    # sections start at a 1024 floor; escalation still covers the rest.
    token_floor = 1024 if axis == "vertical" else 512
    text = retry_with_feedback(
        generate_fn=generate,
        validate_fn=validate,
        fallback=fallback,
        max_retries=getattr(ctx.config, "max_retries", 3),
        label=f"{phase_name}:{level_id}:s{index}",
        attempt_log=attempts,
        token_escalation=default_token_escalation,
        initial_max_tokens=max(token_floor, min(2048, local_w * local_h)),
    )
    # Fell back only if NO attempt validated (retry_with_feedback then returns
    # the fallback) — never by comparing text to the fallback string, since a
    # flat section (a runway) legitimately generates the same flat DSL.
    fell = not any(a["outcome"] == "passed" for a in attempts)
    for a in attempts:
        a["section"] = index
        a["archetype"] = ps.archetype
    res = stamp(
        text, local_w, local_h, tiles=tiles, validate_markers=False,
        lenient=True,
    )
    return _SectionState(
        ps=ps, dsl=text, res=res, fell_back=fell, attempts=attempts
    )


def _stitch_and_repair(
    states: list[_SectionState],
    width: int,
    height: int,
    axis: str,
    movement: PlayerMovementSpec,
    rules: GameRules,
    tiles: TileRegistry,
    checkpoint_sections: list[int],
) -> tuple[StampResult, list[str], list[str], list[str]]:
    """Composite the section sub-grids into the whole level, place the
    whole-level exit (right edge for horizontal, the climb SUMMIT for vertical —
    "goal mode"), run the COMPUTABLE repairs (snap spawn + auto_bridge) on the
    whole grid, PLACE the blueprint's checkpoints on the final grid, and return
    ``(result, bridges, snaps, problems)`` — ``problems`` is the whole-level
    ``check_level`` residue (DESIGN failures the owning section must fix)."""
    parts = [(st.res, st.ps.x_off, st.ps.y_off) for st in states]
    whole = composite(parts, width, height)
    # Code-not-LLM containment repair: a pool that spills only once composited
    # (e.g. at a pit lip or a seam) becomes a free-standing water FEATURE rather
    # than a design failure routed to the LLM — the water stays, no fallback is
    # burned. Done BEFORE validation so check_level (via auto_bridge) sees it.
    if rules.water_containment == "contained":
        spilled, cont_notes = repair_containment_grid(
            whole.grid, tiles, whole.free_volume
        )
        whole.free_volume |= spilled
        whole.repairs.extend(cont_notes)
    exclude = {whole.spawn} if whole.spawn else set()
    exit_ = place_exit(whole.grid, tiles, exclude=exclude, axis=axis)
    if exit_ is None:
        whole.exit = None
        edge = "top has no standable platform" if axis == "vertical" else (
            "right edge has no open ground floor"
        )
        return whole, [], [], [
            f"exit: the level's {edge} to exit onto — the FINAL section must "
            "end on solid standable ground."
        ]
    whole.spawn, spawn_moves = snap_spawn_grid(
        whole.grid, whole.spawn, exit_, tiles
    )
    # Checkpoints are STITCHER-owned (the blueprint decides count + which
    # sections). Strip any that slipped out of a section's DSL BEFORE the
    # bridge/validate loop — a stray unreachable checkpoint used to feed
    # check_level a non-reachability problem that burned whole-level regen
    # rounds for a marker the stitcher was about to discard anyway.
    whole.triggers = [t for t in whole.triggers if t.type != "checkpoint"]
    grid, bridges, problems = auto_bridge_grid(
        whole.grid, whole.spawn, exit_, movement, rules=rules, tiles=tiles,
        triggers=whole.triggers, free_volume=whole.free_volume,
        hazards=whole.hazards,
    )
    # EXIT-RELOCATE (code-not-LLM): place_exit picks the rightmost/topmost
    # standable cell BLIND to reachability — the plat_seaside4 l2 design had
    # a sealed decorative chamber at its far right and the exit went inside
    # it. When the only residue is exit-unreachability AND the exit's own
    # section is partly reached, move the exit to the farthest REACHED
    # foothold in that section (the design intent — "as far right / as high
    # as the player can actually get") and re-run the bridge loop once.
    if problems and all(
        p.startswith("exit at") and "is not reachable" in p for p in problems
    ):
        last = states[-1].ps
        reached = reachable_cells(grid, whole.spawn, movement, tiles)
        body = _body_stand(grid, standable_cells(grid, tiles), tiles)
        if axis == "vertical":
            in_range = lambda c: last.y_off <= c[1] < last.y_off + last.length  # noqa: E731
            metric = lambda c: (-c[1], c[0])  # noqa: E731 — highest wins
        else:
            in_range = lambda c: last.x_off <= c[0] < last.x_off + last.length  # noqa: E731
            metric = lambda c: (c[0], -c[1])  # noqa: E731 — rightmost wins
        cands = [
            c for c in (reached & body)
            if in_range(c) and c != whole.spawn
        ]
        if cands:
            new_exit = max(cands, key=metric)
            if new_exit != exit_:
                whole.repairs.append(
                    f"exit relocated {exit_} -> {new_exit} — the designed "
                    "corner is sealed off; the exit goes where the player "
                    "can actually get."
                )
                exit_ = new_exit
                grid, more, problems = auto_bridge_grid(
                    grid, whole.spawn, exit_, movement, rules=rules,
                    tiles=tiles, triggers=whole.triggers,
                    free_volume=whole.free_volume, hazards=whole.hazards,
                )
                bridges = bridges + more
    whole.grid = grid
    whole.exit = exit_
    # Place the blueprint's checkpoints on the FINAL (bridged) grid at
    # reachable standable cells — count-capped + reachable by construction,
    # both axes.
    whole.triggers += place_checkpoints_grid(
        grid, whole.spawn, exit_,
        [st.ps for st in states], checkpoint_sections, axis, movement, tiles,
    )
    return whole, bridges, spawn_moves, problems


def _owner_of_problem(
    plan: list[PlannedSection], problems: list[str], axis: str
) -> int:
    """Route the first whole-level problem to the section that owns its cell
    (by x-range for horizontal, y-range for vertical); a cell-less message
    (exit/containment at the far edge) goes to the LAST section.

    A reachability break carries a machine-readable ``[break@x,y]`` tag for
    the FRONTIER cell (the reached foothold where progress stalls) — routed
    FIRST, so the section that owns the stall point regenerates, not the one
    holding the far-off target coord that the prose prints before it. (The
    tag used to be the frontier/nearest midpoint; on a tall vertical break
    that average can land in a section containing neither endpoint — the
    plat_seaside3 l7 misroute.)"""
    for p in problems:
        m = re.search(r"\[break@(\d+),(\d+)\]", p)
        if m:
            return section_owner_of_cell(
                plan, int(m.group(1)), int(m.group(2)), axis
            )
    for p in problems:
        m = re.search(r"\((\d+),\s*(\d+)\)", p)
        if m:
            return section_owner_of_cell(
                plan, int(m.group(1)), int(m.group(2)), axis
            )
    return len(plan) - 1


def _section_feedback(
    problems: list[str], ps: PlannedSection, axis: str
) -> list[str]:
    """Whole-level problems + a note translating them to the section's local
    coordinate frame (its own origin at 0,0)."""
    if axis == "vertical":
        note = (
            f"(WHOLE-LEVEL coordinates; THIS section occupies rows "
            f"{ps.y_off}..{ps.y_off + ps.length - 1}. Subtract {ps.y_off} from "
            "the row for your local row.)"
        )
    else:
        note = (
            f"(WHOLE-LEVEL coordinates; THIS section occupies columns "
            f"{ps.x_off}..{ps.x_off + ps.length - 1}. Subtract {ps.x_off} from "
            "the column for your local column.)"
        )
    return list(problems) + [note]


def _section_local_dims(
    ps: PlannedSection, width: int, height: int, axis: str
) -> tuple[int, int]:
    """A section's OWN (local_w, local_h) — horizontal sections are the level's
    height and their own width; vertical sections are the level's width and
    their own height."""
    return (ps.length, height) if axis == "horizontal" else (width, ps.length)


def _generate_sectioned_level(
    ctx: Any,
    *,
    level_id: str,
    brief: str,
    knobs: dict,
    width: int,
    height: int,
    axis: str,
    movement: PlayerMovementSpec,
    rules: GameRules,
    tiles: TileRegistry,
    seed: str,
    phase_name: str,
) -> tuple[StampResult, str, bool, list[dict], list[str], list[str]]:
    """Plan sections → generate each (seam-threaded) → stitch → whole-grid
    repair → whole-level check, regenerating only the owning section on a
    design failure. Handles BOTH axes: horizontal sections tile the width
    (exit at the right edge), vertical sections stack up the height (spawn at
    the bottom, exit at the summit). Returns the whole-level ``StampResult``
    plus the combined DSL record, the fallback flag, the aggregated attempt
    log, and the bridge/snap notes for logging."""
    difficulty = int(knobs.get("difficulty", 1))
    level_plan = plan_level(
        width, height, difficulty,
        derive_rng(seed, phase_name, level_id, "plan"), axis=axis,
    )
    plan = level_plan.sections
    total = len(plan)
    max_retries = getattr(ctx.config, "max_retries", 3)

    def gen(idx: int, ps: PlannedSection, seam, feedback, previous,
            handoff: dict | None = None):
        lw, lh = _section_local_dims(ps, width, height, axis)
        return _generate_one_section(
            ctx, level_id=level_id, brief=brief, ps=ps,
            arch=DEFAULT_VOCAB[ps.archetype], index=idx, total=total,
            local_w=lw, local_h=lh, axis=axis, movement=movement,
            rules=rules, tiles=tiles, seam=seam, phase_name=phase_name,
            initial_feedback=feedback, previous=previous,
            **(handoff or {}),
        )

    def _handoff_for(idx: int, states: list[_SectionState]) -> dict:
        """Sequential handoff (user-locked 2026-07-14): the immediate
        predecessor's full DSL rebased into ``idx``'s coordinates + the
        occupied seam band + one-line digests of every earlier section."""
        if idx == 0 or idx > len(states):
            return {}
        ps = plan[idx]
        prev = plan[idx - 1]
        shift = (prev.x_off - ps.x_off, prev.y_off - ps.y_off)
        out: dict = {
            "predecessor": rebase_dsl(states[idx - 1].dsl, *shift),
            "predecessor_occupied": overlap_occupancy(
                states[idx - 1].res, axis=axis, tiles=tiles, shift=shift
            ),
        }
        if idx >= 2:
            out["history"] = "; ".join(
                _section_digest(i, plan[i], states[i].dsl)
                for i in range(idx - 1)
            )
        return out

    # 1) Generate every section once, threading each section's outgoing seam
    #    summary (right edge / top) into the next section (text, never a
    #    grid) — with the coordinates TRANSLATED into the receiving section's
    #    local frame (the §1b seam frame bug left the raw giver-frame rows in,
    #    inviting an unclimbable void at every vertical seam) — plus the
    #    sequential handoff: the predecessor's full rebased DSL, the occupied
    #    seam band, and digests of everything built so far.
    states: list[_SectionState] = []
    seam: str | None = None
    for idx, ps in enumerate(plan):
        st = gen(idx, ps, seam, None, None, _handoff_for(idx, states))
        states.append(st)
        if idx + 1 < total:
            nxt = plan[idx + 1]
            seam = seam_summary(
                st.res, axis=axis, tiles=tiles,
                shift=(ps.x_off - nxt.x_off, ps.y_off - nxt.y_off),
            )

    # 2) Stitch + whole-grid repair; on a DESIGN failure regenerate ONLY the
    #    owning section with located feedback and re-stitch (bounded). v1
    #    sections meet on a continuous seam, so downstream sections are not
    #    re-generated (a v1 simplification).
    # Every stitch round's residue is RECORDED (stitch_rounds) — the
    # plat_seaside3 post-mortem had to REPLAY the paid traces to recover why
    # five levels fell back, because the whole-level problems were never
    # written anywhere (the per-section attempts all said "passed").
    whole_fallback = False
    stitch_rounds: list[dict[str, Any]] = []
    for attempt in range(max_retries + 1):
        result, bridges, snaps, problems = _stitch_and_repair(
            states, width, height, axis, movement, rules, tiles,
            level_plan.checkpoint_sections,
        )
        stitch_rounds.append(
            {
                "round": attempt,
                "problems": list(problems),
                "bridges": list(bridges),
                "snaps": list(snaps),
            }
        )
        if not problems:
            break
        if attempt == max_retries:
            # Terminal: DON'T nuke the sections that passed. Force ONLY the
            # section that owns the residual problem to its guaranteed-valid
            # fallback (a flat floor / climbable ladder) and re-stitch once.
            # Whole-flat is the LAST resort — only if a locally-repaired
            # composite still can't stitch a walkable level (a spawn/exit
            # section or a cross-section structural break).
            owner = _owner_of_problem(plan, problems, axis)
            lw, lh = _section_local_dims(plan[owner], width, height, axis)
            fb_dsl = _section_fallback_dsl(lw, lh, owner, axis)
            states[owner] = _SectionState(
                ps=plan[owner], dsl=fb_dsl,
                res=stamp(fb_dsl, lw, lh, tiles=tiles, validate_markers=False),
                fell_back=True, attempts=states[owner].attempts,
            )
            result, bridges, snaps, problems = _stitch_and_repair(
                states, width, height, axis, movement, rules, tiles,
                level_plan.checkpoint_sections,
            )
            stitch_rounds.append(
                {
                    "round": attempt,
                    "terminal_local_fallback": owner,
                    "problems": list(problems),
                    "bridges": list(bridges),
                    "snaps": list(snaps),
                }
            )
            if problems:
                # The single-owner rescue can lose to the NEIGHBOURS' overlap
                # content (parts paste in order, so a neighbour's cells win
                # over the flattened owner — the plat_seaside4 l2 terminal
                # flattened s0 while the blocking tower was s1's). Before
                # nuking the whole level, force the owner's seam neighbours
                # to their fallbacks too and re-stitch once more.
                neighbours = [
                    i for i in (owner - 1, owner + 1) if 0 <= i < total
                ]
                for i in neighbours:
                    lw_i, lh_i = _section_local_dims(
                        plan[i], width, height, axis
                    )
                    fb_i = _section_fallback_dsl(lw_i, lh_i, i, axis)
                    states[i] = _SectionState(
                        ps=plan[i], dsl=fb_i,
                        res=stamp(
                            fb_i, lw_i, lh_i, tiles=tiles,
                            validate_markers=False,
                        ),
                        fell_back=True, attempts=states[i].attempts,
                    )
                result, bridges, snaps, problems = _stitch_and_repair(
                    states, width, height, axis, movement, rules, tiles,
                    level_plan.checkpoint_sections,
                )
                stitch_rounds.append(
                    {
                        "round": attempt,
                        "terminal_neighbor_fallback": neighbours,
                        "problems": list(problems),
                        "bridges": list(bridges),
                        "snaps": list(snaps),
                    }
                )
            if problems:
                whole_fallback = True
                result = _whole_fallback(width, height, axis, tiles)
                bridges, snaps = [], []
            break
        owner = _owner_of_problem(plan, problems, axis)
        ps = plan[owner]
        prev_seam = (
            seam_summary(
                states[owner - 1].res, axis=axis, tiles=tiles,
                shift=(
                    plan[owner - 1].x_off - ps.x_off,
                    plan[owner - 1].y_off - ps.y_off,
                ),
            )
            if owner > 0
            else None
        )
        # A regenerated INTERIOR section must mesh with BOTH already-built
        # neighbours — the plat_seaside4 l8 trace showed a section
        # regenerated three times blind to the successor it had to meet.
        handoff = _handoff_for(owner, states)
        if owner + 1 < total:
            nxt = plan[owner + 1]
            handoff["successor_occupied"] = overlap_occupancy(
                states[owner + 1].res, axis=axis, tiles=tiles,
                shift=(nxt.x_off - ps.x_off, nxt.y_off - ps.y_off),
                side="incoming",
            )
        regen = gen(
            owner, ps, prev_seam,
            _section_feedback(problems, ps, axis), states[owner].dsl,
            handoff,
        )
        # KEEP the replaced state's attempt log — each gen() starts a fresh
        # list, and the old `states[owner] = gen(...)` silently discarded the
        # prior rounds' attempts (the trace under-reported the paid runs).
        for a in regen.attempts:
            a["regen_round"] = attempt + 1
        regen.attempts[:0] = states[owner].attempts
        states[owner] = regen

    section_fallbacks = [i for i, st in enumerate(states) if st.fell_back]
    attempts: list[dict[str, Any]] = [a for st in states for a in st.attempts]
    if whole_fallback:
        dsl_text = (
            _section_fallback_dsl(width, height, 0, "vertical")
            if axis == "vertical"
            else _fallback_dsl(width)
        )
    else:
        dsl_text = "\n".join(
            f"### SECTION {i}: {st.ps.archetype} "
            + (f"@x={st.ps.x_off}" if axis == "horizontal" else f"@y={st.ps.y_off}")
            + f" w={st.ps.length}\n{st.dsl}"
            for i, st in enumerate(states)
        )
        if bridges:
            dsl_text += "\n# auto-bridge (whole-level)\n" + "\n".join(bridges)
    fell_back = whole_fallback or bool(section_fallbacks)
    diag = {
        "whole_fallback": whole_fallback,
        "section_fallbacks": section_fallbacks,
        "stitch_rounds": stitch_rounds,
    }
    return result, dsl_text, fell_back, attempts, bridges, snaps, diag


def stamp_level_collision(
    ctx: Any,
    level_id: str,
    index: int,
    *,
    movement: PlayerMovementSpec = DEFAULT_MOVEMENT,
    rules: GameRules = DEFAULT_RULES,
    tiles: TileRegistry = DEFAULT_TILES,
    graphics: GraphicsSpec = DEFAULT_GRAPHICS,
    default_width: int = 48,
    default_height: int = 16,
    phase_name: str = "plat:layout",
) -> Level:
    """Layout Agents (one per SECTION) → stitch → collision.npz; creates the
    Level entity (registered in the Bible) carrying spawn/exit/hazards/
    triggers/brief. The level is a SEQUENCE of typed sections (sections.py):
    each is generated at its own local dims and composited into the full grid,
    then the whole level is repaired (bridge/snap) and validated as one."""
    stage = _stage_for_level(ctx, level_id)
    stage_id = stage.stage_id
    seed = str(getattr(ctx.config, "seed", ""))
    brief = _level_brief(ctx, level_id)
    knobs, width, height, axis = _roll_level_knobs(
        ctx, level_id, index, phase_name=phase_name,
        default_width=default_width, default_height=default_height,
    )
    # Per-level camera framing: a deliberate stage-plan exception
    # ("intimate"/"vista"), resolved to cells here so consumers read a
    # number, not a vocabulary. Resume path (stage phase skipped, hints
    # absent) keeps the prior level's framing, like the brief.
    hint = ctx.artifacts.get("level_views", {}).get(level_id, "")
    view_cells = graphics.view_for(hint)
    if not hint:
        prior = ctx.bible.levels.get(level_id)
        view_cells = prior.view_cells if prior is not None else None
    # A vertical level frames by HEIGHT (a tall shaft): show ~view_rows rows.
    view_rows = graphics.view_rows if axis == "vertical" else None
    result, dsl_text, fell_back, attempts, bridges, snaps, diag = (
        _generate_sectioned_level(
            ctx, level_id=level_id, brief=brief, knobs=knobs,
            width=width, height=height, axis=axis, movement=movement,
            rules=rules, tiles=tiles, seed=seed, phase_name=phase_name,
        )
    )
    if fell_back:
        scope = (
            "the WHOLE level is"
            if diag["whole_fallback"]
            else f"section(s) {diag['section_fallbacks']} are"
        )
        warn(
            ctx,
            f"layout {level_id}: {scope} FALLBACK layout content, not "
            f"generated design. Attempt trace: review/{stage_id}/"
            f"{level_id}_layout_attempts.json",
        )
    trace_rel = f"review/{stage_id}/{level_id}_layout_attempts.json"
    if fell_back or any(a["outcome"] != "passed" for a in attempts):
        # Post-mortem evidence beside the skinned renders. Content is
        # attempt-derived only (no timestamps) — the byte-identical
        # fake-run verification bar covers this file too. Written whenever
        # the level fell back EVEN IF every recorded section attempt passed:
        # the plat_seaside3 l3/l6 fallbacks deleted their own evidence
        # because this used to key on failed attempts alone, and the
        # whole-level stitch problems (diag["stitch_rounds"]) are exactly
        # what a post-mortem needs.
        ctx.adapter.write_json_singleton(
            trace_rel,
            {
                "level_id": level_id,
                "stage_id": stage_id,
                "grid_width": width,
                "grid_height": height,
                "difficulty": knobs.get("difficulty"),
                "axis": axis,
                "fallback": fell_back,
                "whole_fallback": diag["whole_fallback"],
                "section_fallbacks": diag["section_fallbacks"],
                "attempts": attempts,
                "stitch_rounds": diag["stitch_rounds"],
            },
        )
    else:
        # A clean re-roll invalidates any earlier failure trace — a
        # leftover "fallback": true would contradict the level it sits
        # beside.
        ctx.adapter.resolve_path(trace_rel).unlink(missing_ok=True)
    if bridges:
        logger.info(
            "Layout %s: auto-bridged %d reachability break(s) — %s "
            "(agent design kept; geometry is tool work).",
            level_id, len(bridges), ", ".join(bridges),
        )
    if snaps:
        logger.info(
            "Layout %s: snapped %d marker(s) to valid ground — %s "
            "(agent design kept; column lookup is tool work).",
            level_id, len(snaps), ", ".join(snaps),
        )
    for note in result.repairs:
        logger.info(
            "Layout %s: stamp repair — %s (agent design kept; the fix "
            "was arithmetic).",
            level_id, note,
        )

    level_dir = f"level/{stage_id}/{level_id}"
    collision_hash = ctx.adapter.write_numpy(
        f"{level_dir}/collision.npz", collision=result.grid
    )
    layout_aid = make_artifact_id("level", stage_id, level_id, "layout")
    collision_aid = make_artifact_id("level", stage_id, level_id, "collision")
    level = Level(
        level_id=level_id,
        stage_id=stage_id,
        grid_width=width,
        grid_height=height,
        view_cells=view_cells,
        layout_axis=axis,
        view_rows=view_rows,
        brief=brief,
        spawn=result.spawn,
        exit=result.exit,
        collision=f"{level_dir}/collision.npz",
        collision_hash=collision_hash,
        hazards=result.hazards,
        triggers=result.triggers,
        layout_fallback=fell_back,
        parents=[layout_aid, stage.tileset_ref],
        step_parents={
            "collision": [layout_aid],
            "hazards": [collision_aid],
            "triggers": [collision_aid],
        },
    )
    ctx.bible.levels[level_id] = level
    ctx.artifacts.setdefault("dsl_texts", {})[level_id] = dsl_text

    op_counts: dict[str, int] = {}
    # Lenient: the combined record is LLM-derived and may carry lines the
    # section stamps skipped (prose/truncation debris) — the op-count log
    # must not be the one place that still raises on them.
    for op, _args in parse_dsl(dsl_text, skipped=[]):
        op_counts[op] = op_counts.get(op, 0) + 1
    logger.info(
        "Layout %s (difficulty %s, %dx%d): %s; spawn %s -> exit %s, "
        "%d hazard cells, %d checkpoint(s)",
        level_id, knobs.get("difficulty"), width, height,
        ", ".join(f"{n}x {op}" for op, n in sorted(op_counts.items())),
        result.spawn, result.exit, len(result.hazards),
        len(result.triggers),
    )
    return level


def write_level_hazards(ctx: Any, level: Level) -> None:
    """Sparse hazards layer file (§6.4), from the stamp's records."""
    level_dir = f"level/{level.stage_id}/{level.level_id}"
    level.hazards_hash = ctx.adapter.write_json_singleton(
        f"{level_dir}/hazards.json",
        [h.model_dump(mode="json") for h in level.hazards],
    )


def write_level_triggers(ctx: Any, level: Level) -> None:
    """Sparse triggers layer file — carries the checkpoints (3b)."""
    level_dir = f"level/{level.stage_id}/{level.level_id}"
    level.triggers_hash = ctx.adapter.write_json_singleton(
        f"{level_dir}/triggers.json",
        [t.model_dump(mode="json") for t in level.triggers],
    )


def place_level_entities(
    ctx: Any,
    level: Level,
    *,
    max_enemies: int = 4,
    rules: GameRules = DEFAULT_RULES,
    tiles: TileRegistry = DEFAULT_TILES,
    variants: VariantSet = DEFAULT_VARIANTS,
    combat: CombatSpec = DEFAULT_COMBAT,
    phase_name: str = "plat:placement",
) -> None:
    """Entity Agent → validated placements → entities.json + Level.entities."""
    import numpy as np

    # The level's roster is its STAGE's (ecology: the world pool filtered
    # by biome — Stage.enemy_refs); an empty refs list (pre-ecology
    # bibles, direct tests) falls back to the whole pool.
    stage = ctx.bible.stages.get(level.stage_id)
    stage_refs = {
        ref.split(":", 1)[1]
        for ref in (stage.enemy_refs if stage else [])
    }
    roster = [
        {
            "id": e.enemy_id,
            "archetype": e.archetype,
            "size": float(getattr(e, "size", 1.0) or 1.0),
            "rarity": str(getattr(e, "rarity", "") or ""),
            "behavior": e.behavior,
        }
        for e in ctx.bible.enemy_definitions.values()
        if not stage_refs or e.enemy_id in stage_refs
    ]
    level_id = level.level_id
    with np.load(ctx.adapter.resolve_path(level.collision)) as data:
        grid = data["collision"]
    # Env feasibility (code, before the prompt): an enemy this TERRAIN
    # can't sustain is never offered — a swimmer needs water its whole body
    # fits (the first multi-stage run burned every retry trying to seat a
    # 1.5-body swimmer in 1-deep puddles), and a flyer needs open airspace
    # over ground (rejects fully-solid/ceilinged levels). Land archetypes
    # always have standable ground in a valid level.
    def _terrain_sustains(e: dict) -> bool:
        if e["archetype"] == "swimmer":
            return swimmer_spot_exists(
                grid, e["size"],
                str(e["behavior"].get("swim_style", "") or ""), tiles,
            )
        if e["archetype"] == "flyer":
            return flyer_spot_exists(grid, e["size"], tiles)
        return True

    infeasible = [e for e in roster if not _terrain_sustains(e)]
    if infeasible:
        roster = [e for e in roster if e not in infeasible]
        logger.info(
            "Placement %s: terrain can't sustain %s — excluded from the "
            "offered roster (env feasibility is code's call).",
            level_id,
            ", ".join(
                f"{e['id']} (size-{e['size']:g} {e['archetype']})"
                for e in infeasible
            ),
        )
    enemy_defs = {
        e["id"]: {
            "archetype": e["archetype"],
            "size": e["size"],
            "rarity": e["rarity"],
            "swim_style": str(e["behavior"].get("swim_style", "") or ""),
        }
        for e in roster
    }
    spawn = level.spawn or (0, 0)
    summary = _cells_summary(sorted(standable_cells(grid, tiles)))
    volume_summary = _volume_summary(grid, tiles)
    air_summary = _air_summary(grid, tiles)
    # The section ENCOUNTER map (recomputed deterministically — no persisted
    # field): steers the agent to concentrate enemies in combat/mixed stretches
    # and keep traversal stretches lighter. Chunk-B archetype character.
    encounter_summary = section_encounter_summary(
        level_section_plan(ctx, level), axis=level.layout_axis
    )
    brief = level.brief or _level_brief(ctx, level_id)
    accepted_holder: dict[str, list[dict]] = {"placements": []}
    last_attempt: dict[str, str | None] = {"content": None}

    def generate(
        feedback: list[str] | None = None, max_tokens: int | None = None
    ) -> str:
        request = ctx.prompts.placement_generation(
            level_id, brief, roster, summary, max_enemies,
            spawn=spawn, volume_summary=volume_summary,
            air_summary=air_summary, encounter_summary=encounter_summary,
            variants=variants, rules=rules, combat=combat,
            previous=last_attempt["content"], feedback=feedback,
        )
        if max_tokens is not None:
            request.max_tokens = max_tokens
        content = ctx.llm.generate(request, phase=f"{phase_name}:{level_id}")
        last_attempt["content"] = content
        return content

    last_problems: list[str] = []
    repairs_holder: dict[str, list[str]] = {"repairs": []}

    def validate(content: str) -> tuple[bool, list[str]]:
        obj = extract_json_object(content)
        if obj is None or not isinstance(obj.get("placements"), list):
            return False, ['Return {"placements": [...]} as bare JSON.']
        accepted, problems, repairs = check_placements(
            grid, obj["placements"], spawn, enemy_defs,
            rules=rules, tiles=tiles, variants=variants, combat=combat,
        )
        accepted_holder["placements"] = accepted
        repairs_holder["repairs"] = repairs
        last_problems[:] = problems
        # Kick back while anything is invalid; the final fallback
        # accepts whatever subset survived validation.
        return (not problems and bool(accepted)), problems or [
            "No valid placements proposed."
        ]

    retry_with_feedback(
        generate_fn=generate,
        validate_fn=validate,
        fallback="",  # accepted_holder already carries the survivors
        max_retries=getattr(ctx.config, "max_retries", 3),
        label=f"{phase_name}:{level_id}",
    )
    accepted = accepted_holder["placements"][:max_enemies]
    for note in repairs_holder["repairs"]:
        logger.info(
            "Placement %s: repair — %s (agent design kept; the column "
            "was arithmetic).",
            level_id, note,
        )
    if last_problems:
        warn(
            ctx,
            f"placement {level_id}: kept {len(accepted)} valid "
            f"placement(s), dropped invalid ones: {'; '.join(last_problems)}",
        )
    if not accepted:
        warn(ctx, f"placement {level_id}: level has NO enemies.")

    level.entities = [
        Placement(
            ref=make_artifact_id("enemy", p["enemy_id"]),
            pos=(p["x"], p["y"]),
            # Per-placement variation (§6.1): the definition stays
            # canonical; the variant NAME rides on the placement and
            # consumers resolve its meaning from the manifest's variant
            # vocabulary.
            overrides={"variant": p["variant"]} if p["variant"] else {},
        )
        for p in accepted
    ]
    level_dir = f"level/{level.stage_id}/{level_id}"
    level.entities_hash = ctx.adapter.write_json_singleton(
        f"{level_dir}/entities.json",
        [
            {
                "enemy_id": p["enemy_id"],
                "x": p["x"],
                "y": p["y"],
                "variant": p["variant"],
            }
            for p in accepted
        ],
    )
    # Placement validation reads each definition's SIZE (footprints), so
    # entities descends from every roster definition — an enemy regen
    # must cascade here or a grown body silently invalidates placements.
    level.step_parents["entities"] = [
        make_artifact_id("level", level.stage_id, level_id, "collision"),
        make_artifact_id("level", level.stage_id, level_id, "hazards"),
        *sorted(
            make_artifact_id("enemy", e["id"]) for e in roster
        ),
    ]
    logger.info(
        "Placement %s: %d enemies — %s",
        level_id, len(accepted),
        ", ".join(
            f"{p['enemy_id']}"
            f"{'*' + p['variant'].upper() + '*' if p['variant'] else ''}"
            f"@({p['x']},{p['y']})"
            for p in accepted
        )
        or "none",
    )


def place_level_items(
    ctx: Any,
    level: Level,
    max_items: int = 24,
    rules: GameRules = DEFAULT_RULES,
    tiles: TileRegistry = DEFAULT_TILES,
    movement: PlayerMovementSpec = DEFAULT_MOVEMENT,
    phase_name: str = "plat:item_placement",
) -> None:
    """The whole-map ITEM pass (Arc 2), AFTER enemy placement: the agent
    sees the finished stitched level + the enemy roster and places the
    world item pool — coins FREQUENT along the route and marking side
    areas (doctrine), power-ups staged around enemies, premium items at
    the layout's reward() alcove anchors, and BOX containers in open air.

    Boxes never rewrite collision.npz (a parent step): accepted box cells
    ride in items.json and every consumer OVERLAYS the solid box tile
    onto the grid at load. The beatability re-check here does the same
    overlay in memory, dropping any box that walls off the exit or a
    checkpoint (code repair, never an LLM loop)."""
    import numpy as np

    level_id = level.level_id
    pool = list(ctx.bible.items.values())
    if not pool:
        level.items = []
        level.step_parents["items"] = [
            make_artifact_id("level", level.stage_id, level_id, step)
            for step in ("collision", "hazards", "triggers", "entities")
        ]
        return
    with np.load(ctx.adapter.resolve_path(level.collision)) as data:
        grid = data["collision"]
    offer = [
        {
            "id": item.item_id,
            "kind": item.kind,
            "rarity": item.rarity,
            "params": item.params,
        }
        for item in pool
    ]
    item_defs = {
        item.item_id: {"kind": item.kind, "rarity": item.rarity}
        for item in pool
    }
    reward_anchors = [
        (t.x, t.y) for t in level.triggers if t.type == "reward"
    ]
    enemy_spots = [
        {"id": p.ref.split(":", 1)[1], "at": list(p.pos)}
        for p in level.entities
    ]
    standable_summary = _cells_summary(sorted(standable_cells(grid, tiles)))
    brief = _level_brief(ctx, level_id)

    last_attempt = {"content": ""}
    holders: dict[str, list] = {"accepted": [], "repairs": []}

    def generate(
        feedback: list[str] | None = None, max_tokens: int | None = None
    ) -> str:
        request = ctx.prompts.item_placement(
            level_id, brief, offer,
            standable_summary=standable_summary,
            spawn=level.spawn, exit_=level.exit,
            grid_width=level.grid_width, grid_height=level.grid_height,
            enemies=enemy_spots, reward_anchors=reward_anchors,
            rules=rules, max_items=max_items,
            has_box="box" in tiles.by_name,
            previous=last_attempt["content"], feedback=feedback,
        )
        if max_tokens is not None:
            request.max_tokens = max_tokens
        content = ctx.llm.generate(request, phase=f"{phase_name}:{level_id}")
        last_attempt["content"] = content
        return content

    def validate(content: str) -> tuple[bool, list[str]]:
        obj = extract_json_object(content)
        if obj is None or not isinstance(obj.get("placements"), list):
            return False, ['Return {"placements": [...]} as bare JSON.']
        accepted, problems, repairs = check_item_placements(
            grid, obj["placements"], level.spawn, item_defs, movement,
            rules=rules, tiles=tiles,
        )
        holders["accepted"], holders["repairs"] = accepted, repairs
        return not problems, problems

    retry_with_feedback(
        generate_fn=generate,
        validate_fn=validate,
        fallback="",
        max_retries=getattr(ctx.config, "max_retries", 3),
        label=f"{phase_name}:{level_id}",
    )
    accepted = holders["accepted"][:max_items]
    for note in holders["repairs"]:
        logger.info("Item placement %s repair: %s", level_id, note)

    # BOX beatability re-check (code-not-LLM): overlay each accepted box
    # tile onto a grid COPY, keep it only while the exit and every
    # checkpoint stay sim-reachable (a box is SOLID — it can seal a
    # corridor or block a jump arc mid-flight).
    box_id = tiles.by_name["box"].id if "box" in tiles.by_name else None
    boxes = [p for p in accepted if p["source"] == "box"]
    if boxes and box_id is not None and level.spawn and level.exit:
        overlay = grid.copy()
        checkpoints = [
            (t.x, t.y) for t in level.triggers if t.type == "checkpoint"
        ]
        for box in list(boxes):
            overlay[box["y"], box["x"]] = box_id
            reached = reachable_cells(overlay, level.spawn, movement, tiles)
            if level.exit not in reached or any(
                c not in reached for c in checkpoints
            ):
                overlay[box["y"], box["x"]] = grid[box["y"], box["x"]]
                accepted.remove(box)
                warn(
                    ctx,
                    f"items {level_id}: box at ({box['x']},{box['y']}) "
                    "would wall off the path — dropped (code repair).",
                )
    elif boxes and box_id is None:
        accepted = [p for p in accepted if p["source"] != "box"]
        warn(
            ctx,
            f"items {level_id}: this game's registry has no 'box' tile — "
            f"{len(boxes)} box placement(s) dropped.",
        )

    if not accepted:
        warn(
            ctx,
            f"items {level_id}: no valid item placements survived — the "
            "level ships without items.",
        )

    level.items = [
        Placement(
            ref=make_artifact_id("item", p["item_id"]),
            pos=(p["x"], p["y"]),
            overrides={"source": p["source"]},
        )
        for p in accepted
    ]
    level_dir = f"level/{level.stage_id}/{level_id}"
    level.items_hash = ctx.adapter.write_json_singleton(
        f"{level_dir}/items.json",
        [
            {
                "item_id": p["item_id"],
                "x": p["x"],
                "y": p["y"],
                "source": p["source"],
            }
            for p in accepted
        ],
    )
    # Rarity caps read each definition's rarity — an item re-roll must
    # cascade here (whole-pool edges, the enemy-roster precedent).
    level.step_parents["items"] = [
        make_artifact_id("level", level.stage_id, level_id, "collision"),
        make_artifact_id("level", level.stage_id, level_id, "hazards"),
        make_artifact_id("level", level.stage_id, level_id, "triggers"),
        make_artifact_id("level", level.stage_id, level_id, "entities"),
        *sorted(make_artifact_id("item", i.item_id) for i in pool),
    ]
    logger.info(
        "Items %s: %d placed — %s",
        level_id, len(accepted),
        ", ".join(
            f"{p['item_id']}@({p['x']},{p['y']})"
            + (f"[{p['source']}]" if p["source"] != "trail" else "")
            for p in accepted
        )
        or "none",
    )


class ItemsPlacementPhase:
    """Sequential twin of the DAG items step: place the world item pool
    into every level, after enemies."""

    name = "plat:item_placement"

    def __init__(
        self,
        max_items_per_level: int = 24,
        rules: GameRules = DEFAULT_RULES,
        tiles: TileRegistry = DEFAULT_TILES,
        movement: PlayerMovementSpec = DEFAULT_MOVEMENT,
    ) -> None:
        self.max_items = max_items_per_level
        self.rules = rules
        self.tiles = tiles
        self.movement = movement

    def run(self, ctx: Any) -> None:
        for stage in world_stages(ctx):
            for level_id in stage.level_ids:
                place_level_items(
                    ctx, ctx.bible.levels[level_id],
                    max_items=self.max_items, rules=self.rules,
                    tiles=self.tiles, movement=self.movement,
                    phase_name=self.name,
                )
        _stamp_metadata(ctx, self.name)


DECOR_TYPES = ("stalactite", "crystal", "vine", "moss")


def decorate_level(
    ctx: Any,
    level: Level,
    *,
    max_decor: int = 6,
    phase_name: str = "plat:decorator",
) -> None:
    """Foreground decoration (sparse layer the player passes in front
    of/behind) → foreground.json + Level.foreground."""
    level_id = level.level_id
    brief = level.brief or _level_brief(ctx, level_id)
    accepted_holder: dict[str, list[dict]] = {"decor": []}

    def generate(
        feedback: list[str] | None = None, max_tokens: int | None = None
    ) -> str:
        request = ctx.prompts.decor_generation(
            level_id, brief, level.grid_width, level.grid_height,
            DECOR_TYPES, max_decor, feedback=feedback,
        )
        if max_tokens is not None:
            request.max_tokens = max_tokens
        return ctx.llm.generate(request, phase=f"{phase_name}:{level_id}")

    def validate(content: str) -> tuple[bool, list[str]]:
        obj = extract_json_object(content)
        if obj is None or not isinstance(obj.get("decor"), list):
            return False, ['Return {"decor": [...]} as bare JSON.']
        accepted, problems = [], []
        for d in obj["decor"][:max_decor]:
            x, y, kind = d.get("x"), d.get("y"), d.get("type")
            if kind not in DECOR_TYPES:
                problems.append(
                    f"decor type {kind!r} unknown; pick from "
                    f"{list(DECOR_TYPES)!r}."
                )
                continue
            if (
                not isinstance(x, int)
                or not isinstance(y, int)
                or not (0 <= x < level.grid_width)
                or not (0 <= y < level.grid_height)
            ):
                problems.append(
                    f"decor at ({x}, {y}) is outside the "
                    f"{level.grid_width}x{level.grid_height} grid."
                )
                continue
            accepted.append({"x": x, "y": y, "type": kind})
        accepted_holder["decor"] = accepted
        return (not problems), problems

    retry_with_feedback(
        generate_fn=generate,
        validate_fn=validate,
        fallback="",  # decoration is optional; survivors suffice
        max_retries=getattr(ctx.config, "max_retries", 3),
        label=f"{phase_name}:{level_id}",
    )
    decor = accepted_holder["decor"]

    level.foreground = [
        SparseMaskEntry(x=d["x"], y=d["y"], type=d["type"]) for d in decor
    ]
    level_dir = f"level/{level.stage_id}/{level_id}"
    level.foreground_hash = ctx.adapter.write_json_singleton(
        f"{level_dir}/foreground.json", decor
    )
    stage = ctx.bible.stages[level.stage_id]
    level.step_parents["foreground"] = [
        make_artifact_id("level", level.stage_id, level_id, "collision"),
        stage.tileset_ref,
    ]
    logger.info(
        "Decor %s: %d pieces — %s",
        level_id, len(decor),
        ", ".join(f"{d['type']}@({d['x']},{d['y']})" for d in decor)
        or "none",
    )


def write_level_manifest(ctx: Any, level: Level) -> None:
    """level.json — the per-level entry point (§6.4), written LAST so it
    carries every layer hash. Its "level" step descends from every layer
    step, so any layer edit/regen re-freshens it via the stale cascade."""
    prefix = f"level:{level.stage_id}/{level.level_id}"
    level.step_parents["level"] = [f"{prefix}/{step}" for step in LEVEL_STEPS]
    level_dir = f"level/{level.stage_id}/{level.level_id}"
    content_hash = ctx.adapter.write_json_singleton(
        f"{level_dir}/level.json", level.model_dump(mode="json")
    )
    # One Level stamp covers three generation tasks — fold all three
    # (table-resolved) models, at the SAME per-level labels the calls
    # resolved under (deep per-level tier keys stamp truthfully). On
    # unwired/fake backends every part is the global model.
    stamp_provenance(
        ctx, level, content_hash,
        label=f"plat:layout:{level.level_id}",
        model_extra="+".join(
            resolved_model(ctx, f"{task}:{level.level_id}")
            for task in ("plat:placement", "plat:decorator")
        ),
    )


def _level_brief(ctx: Any, level_id: str) -> str:
    brief = ctx.artifacts.get("level_briefs", {}).get(level_id, "")
    if brief:
        return brief
    # Resume path: level_briefs is populated by the stage phase, which a
    # surgical regen skips — the persisted brief on the prior Level entity
    # is exactly what Level.brief exists to preserve.
    prior = ctx.bible.levels.get(level_id)
    return prior.brief if prior is not None else ""


def _volume_summary(grid, tiles: TileRegistry) -> str:
    """Per-volume-NAME cell ranges (swimmer targets) — descriptive context
    for the Entity Agent, named so the model knows which pools damage
    (never a raw grid dump; I3 applies to prompts)."""
    parts = []
    for tile in tiles.named("volume"):
        cells = sorted(
            (x, y)
            for x, y in volume_cells(grid, tiles)
            if int(grid[y, x]) == tile.id
        )
        if cells:
            parts.append(f"{tile.name}: {_cells_summary(cells)}")
    return " | ".join(parts) or "none"


def _air_summary(grid, tiles: TileRegistry, hover: int = 3) -> str:
    """Representative airborne anchors for flyers: one open cell ~``hover``
    rows above each column's ground surface — a hover-line following the
    terrain. Descriptive context for the Entity Agent (like the volume
    summary for swimmers), never a raw grid dump; the flyer's full
    feasibility is ``flyer_spot_exists``. 'none' on a level with no open
    airspace over ground."""
    height, width = grid.shape
    empty = tiles.empty_id
    support = tiles.ids("solid", "one_way")
    cells: list[tuple[int, int]] = []
    for x in range(width):
        surf = next(
            (
                y
                for y in range(height - 1)
                if int(grid[y, x]) == empty and int(grid[y + 1, x]) in support
            ),
            None,
        )
        if surf is None:
            continue
        ay = max(0, surf - hover)
        if (
            int(grid[ay, x]) == empty
            and ay + 1 < height
            and int(grid[ay + 1, x]) == empty
            and any(int(grid[yy, x]) in support for yy in range(ay + 1, height))
        ):
            cells.append((x, ay))
    return _cells_summary(sorted(cells))


def _cells_summary(cells: list[tuple[int, int]]) -> str:
    by_row: dict[int, list[int]] = {}
    for x, y in cells:
        by_row.setdefault(y, []).append(x)
    parts = []
    for y in sorted(by_row):
        xs = by_row[y]
        ranges, start = [], xs[0]
        for prev, cur in zip(xs, xs[1:] + [None]):
            if cur is None or cur != prev + 1:
                ranges.append(f"{start}-{prev}" if prev != start else str(start))
                if cur is not None:
                    start = cur
        parts.append(f"y={y}: x {', '.join(ranges)}")
    return "; ".join(parts) or "none"


def seam_summary(
    res: StampResult,
    axis: str = "horizontal",
    overlap: int = SECTION_OVERLAP,
    tiles: TileRegistry = DEFAULT_TILES,
    shift: tuple[int, int] = (0, 0),
) -> str:
    """One-line hand-off summary of the edge a section passes to its NEIGHBOUR
    — the standable footing near the boundary as TEXT, never a raw grid (I3).
    - horizontal: the RIGHT edge (rightmost ``overlap`` columns) → the next
      section's left.
    - vertical: the TOP edge (topmost ``overlap`` rows) → the footholds the
      climber reaches entering the section above.
    A boundary with no footing says so, so the neighbour doesn't assume ground.

    ``shift`` translates the printed coordinates into the RECEIVING section's
    local frame (``giver_offset - receiver_offset`` per axis). The §1b SEAM
    FRAME BUG: a climb section was told the section below "offers top
    footholds y=1: x 3-5" in the LOWER section's frame — y=1..5 reads as the
    receiver's own TOP, so a literal model built its lowest platforms a
    whole section-height above the seam and left an unclimbable void."""
    grid = res.grid
    height, width = grid.shape
    stand = standable_cells(grid, tiles)
    if axis == "vertical":
        hi = min(height, overlap)
        band = sorted((x, y) for (x, y) in stand if 0 <= y < hi)
        edge = "top"
    else:
        lo = max(0, width - overlap)
        band = sorted((x, y) for (x, y) in stand if lo <= x < width)
        edge = "right edge"
    if not band:
        return (
            f"the {edge} has NO footing — it is open air or a pit; do not "
            "assume the ground continues there"
        )
    dx, dy = shift
    band = [(x + dx, y + dy) for (x, y) in band]
    return f"standable footing near the {edge} — " + _cells_summary(band)


def overlap_occupancy(
    res: StampResult,
    axis: str = "horizontal",
    overlap: int = SECTION_OVERLAP,
    tiles: TileRegistry = DEFAULT_TILES,
    shift: tuple[int, int] = (0, 0),
    side: str = "outgoing",
) -> str:
    """EVERYTHING a section leaves in the shared seam band, by category, in
    the RECEIVING section's coordinates — the footing-only ``seam_summary``
    left the next section blind to walls/ledges/hazards/water already sitting
    in the overlap, and the plat_seaside4 l2 trap was built exactly there
    (a tower erected against an unseen capped ledge). Empty band says so.

    ``side`` picks WHICH band of the giving section is shared: ``outgoing``
    (default) is the band it hands its NEXT neighbour (right columns / top
    rows); ``incoming`` is the band it shares with its PREVIOUS neighbour
    (left columns / bottom rows) — used when a REGENERATED section needs to
    see its already-built successor."""
    grid = res.grid
    height, width = grid.shape
    if axis == "vertical":
        rows = (
            range(min(height, overlap))
            if side == "outgoing"
            else range(max(0, height - overlap), height)
        )
        cells = [(x, y) for y in rows for x in range(width)]
    else:
        cols = (
            range(max(0, width - overlap), width)
            if side == "outgoing"
            else range(min(width, overlap))
        )
        cells = [(x, y) for y in range(height) for x in cols]
    dx, dy = shift
    by_cat: dict[str, list[tuple[int, int]]] = {}
    for x, y in cells:
        v = int(grid[y, x])
        if v == 0:
            continue
        tile = tiles.by_id.get(v)
        cat = tile.category if tile else "?"
        label = {
            "solid": "SOLID (floor/ledge/wall)",
            "one_way": "one-way platform",
            "hazard": "hazard",
            "volume": "liquid",
        }.get(cat, cat)
        by_cat.setdefault(label, []).append((x + dx, y + dy))
    if not by_cat:
        return "the shared band is open air"
    return "; ".join(
        f"{label}: {_cells_summary(sorted(cells))}"
        for label, cells in sorted(by_cat.items())
    )


# ---------------------------------------------------------------------------
# Sequential phases (legacy run_pipeline path)
# ---------------------------------------------------------------------------


class LayoutStampPhase:
    name = "plat:layout"

    def __init__(
        self,
        width: int = 48,
        height: int = 16,
        movement: PlayerMovementSpec = DEFAULT_MOVEMENT,
        rules: GameRules = DEFAULT_RULES,
        tiles: TileRegistry = DEFAULT_TILES,
        graphics: GraphicsSpec = DEFAULT_GRAPHICS,
    ) -> None:
        self.width = width
        self.height = height
        self.movement = movement
        self.rules = rules
        self.tiles = tiles
        self.graphics = graphics

    def run(self, ctx: Any) -> None:
        for stage in world_stages(ctx):
            for index, level_id in enumerate(stage.level_ids):
                level = stamp_level_collision(
                    ctx, level_id, index,
                    movement=self.movement, rules=self.rules, tiles=self.tiles,
                    graphics=self.graphics,
                    default_width=self.width, default_height=self.height,
                    phase_name=self.name,
                )
                write_level_hazards(ctx, level)
                write_level_triggers(ctx, level)
        _stamp_metadata(ctx, self.name)


class PlacementPhase:
    name = "plat:placement"

    def __init__(
        self,
        max_enemies_per_level: int = 4,
        rules: GameRules = DEFAULT_RULES,
        tiles: TileRegistry = DEFAULT_TILES,
        variants: VariantSet = DEFAULT_VARIANTS,
        combat: CombatSpec = DEFAULT_COMBAT,
    ) -> None:
        self.max_enemies = max_enemies_per_level
        self.rules = rules
        self.tiles = tiles
        self.variants = variants
        self.combat = combat

    def run(self, ctx: Any) -> None:
        for stage in world_stages(ctx):
            for level_id in stage.level_ids:
                place_level_entities(
                    ctx, ctx.bible.levels[level_id],
                    max_enemies=self.max_enemies, rules=self.rules,
                    tiles=self.tiles, variants=self.variants,
                    combat=self.combat, phase_name=self.name,
                )
        _stamp_metadata(ctx, self.name)


class DecoratorPhase:
    """Foreground decoration + the level.json manifest, written last so it
    carries every layer hash and the placement list."""

    name = "plat:decorator"

    def __init__(self, max_decor: int = 6) -> None:
        self.max_decor = max_decor

    def run(self, ctx: Any) -> None:
        for stage in world_stages(ctx):
            for level_id in stage.level_ids:
                level = ctx.bible.levels[level_id]
                decorate_level(
                    ctx, level, max_decor=self.max_decor, phase_name=self.name
                )
                write_level_manifest(ctx, level)
        _stamp_metadata(ctx, self.name)
