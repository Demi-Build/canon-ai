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
from typing import Any

from canon.bible.artifacts import make_artifact_id
from canon.bible.platformer import Level, Placement, SparseMaskEntry
from canon.llm.parsing import extract_json_object
from canon.pipeline.retry import default_token_escalation, retry_with_feedback
from canon.pipeline.rng import derive_rng
from canon.skeleton.core import roll_skeleton
from canon.skeleton.loader import load_skeleton_spec
from examples.platformer_pack.combat import DEFAULT_COMBAT, CombatSpec
from examples.platformer_pack.dsl import DslError, StampResult, parse_dsl, stamp
from examples.platformer_pack.graphics import DEFAULT_GRAPHICS, GraphicsSpec
from examples.platformer_pack.movement import DEFAULT_MOVEMENT, PlayerMovementSpec
from examples.platformer_pack.phases import (
    SCHEMAS_DIR,
    _stamp_metadata,
    stamp_provenance,
    warn,
)
from examples.platformer_pack.rules import DEFAULT_RULES, GameRules
from examples.platformer_pack.tiles import DEFAULT_TILES, TileRegistry
from examples.platformer_pack.validate import (
    auto_bridge,
    check_placements,
    snap_checkpoints,
    standable_cells,
    volume_cells,
)
from examples.platformer_pack.variants import DEFAULT_VARIANTS, VariantSet

logger = logging.getLogger(__name__)

#: All per-level layer steps, in generation order — the "level" finalize
#: step (level.json) descends from every one of them in the §6.1 edge set.
LEVEL_STEPS = (
    "collision", "hazards", "triggers", "terrain", "background",
    "entities", "foreground",
)


def _fallback_dsl(width: int) -> str:
    """A guaranteed-valid layout so a fully misbehaving LLM still yields a
    walkable (if boring) level instead of a dead pipeline."""
    return f"floor(0,{width - 1})\nspawn(2)\nexit({width - 3})"


# ---------------------------------------------------------------------------
# Per-level bodies (shared by sequential phases and DAG nodes)
# ---------------------------------------------------------------------------


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
    """Layout Agent → stamp → collision.npz; creates the Level entity
    (registered in the Bible) carrying spawn/exit/hazards/triggers/brief."""
    spec = load_skeleton_spec(SCHEMAS_DIR / "level_layout.json")
    stage_id = ctx.artifacts["stage_id"]
    stage = ctx.bible.stages[stage_id]
    seed = str(getattr(ctx.config, "seed", ""))
    brief = _level_brief(ctx, level_id)

    # Difficulty escalates by level POSITION, not by roll — the schema
    # keys it off this context value (depends_on_context). Clamped to the
    # schema's 1..3 table for longer level lists.
    roll_context = {"level_number": min(index + 1, 3)}
    knobs = roll_skeleton(
        spec, derive_rng(seed, phase_name, level_id), context=roll_context
    )
    # Variable dims (GridDims, §4.2): schema-rolled per level; the
    # defaults are the fallback for schemas without them.
    width = int(knobs.get("grid_width", default_width))
    height = int(knobs.get("grid_height", default_height))
    # Per-level camera framing: a deliberate stage-plan exception
    # ("intimate"/"vista"), resolved to cells here so consumers read a
    # number, not a vocabulary. Resume path (stage phase skipped, hints
    # absent) keeps the prior level's framing, like the brief.
    hint = ctx.artifacts.get("level_views", {}).get(level_id, "")
    view_cells = graphics.view_for(hint)
    if not hint:
        prior = ctx.bible.levels.get(level_id)
        view_cells = prior.view_cells if prior is not None else None
    last_attempt: dict[str, str | None] = {"content": None}
    accepted: dict[str, Any] = {"dsl": None, "bridges": []}

    def generate(
        feedback: list[str] | None = None, max_tokens: int | None = None
    ) -> str:
        request = ctx.prompts.layout_generation(
            level_id, brief, knobs, width, height,
            movement, rules=rules, tiles=tiles,
            previous=last_attempt["content"], feedback=feedback,
        )
        if max_tokens is not None:
            request.max_tokens = max_tokens
        content = ctx.llm.generate(request, phase=f"{phase_name}:{level_id}")
        last_attempt["content"] = content
        return content

    def validate(content: str) -> tuple[bool, list[str]]:
        # Design problems (DSL errors, covered spawn, spilled pools) go
        # back to the agent; reachability breaks and checkpoint columns
        # are ARITHMETIC — the bridge/snap tools repair them in code
        # (never an LLM round-trip).
        try:
            content, snaps = snap_checkpoints(
                content, width, height, tiles=tiles
            )
            repaired, bridges, problems = auto_bridge(
                content, width, height, movement, rules=rules, tiles=tiles
            )
        except DslError as exc:
            # Final-grid marker checks report every problem at once —
            # feed them back as separate items, not one blob (the l3
            # trace showed one-error-per-attempt serializing discovery).
            return False, list(exc.problems)
        if problems:
            return False, problems
        accepted["dsl"], accepted["bridges"] = repaired, bridges
        accepted["snaps"] = snaps
        return True, []

    fallback_dsl = _fallback_dsl(width)
    attempts: list[dict[str, Any]] = []
    raw_text = retry_with_feedback(
        generate_fn=generate,
        validate_fn=validate,
        fallback=fallback_dsl,
        max_retries=getattr(ctx.config, "max_retries", 3),
        label=f"{phase_name}:{level_id}",
        attempt_log=attempts,
        # Wide difficulty-3 grids need more ops than the prompt's 512-token
        # default allows — a truncated program is a guaranteed DslError, and
        # identical caps across retries made that failure unrecoverable.
        token_escalation=default_token_escalation,
        initial_max_tokens=768,
    )
    fell_back = raw_text == fallback_dsl
    if fell_back:
        warn(
            ctx,
            f"layout {level_id}: LLM output never validated; level is "
            "the flat FALLBACK layout, not generated content. Attempt "
            f"trace: review/{stage_id}/{level_id}_layout_attempts.json",
        )
    trace_rel = f"review/{stage_id}/{level_id}_layout_attempts.json"
    if any(a["outcome"] != "passed" for a in attempts):
        # Post-mortem evidence beside the skinned renders. Content is
        # attempt-derived only (no timestamps) — the byte-identical
        # fake-run verification bar covers this file too.
        ctx.adapter.write_json_singleton(
            trace_rel,
            {
                "level_id": level_id,
                "stage_id": stage_id,
                "grid_width": width,
                "grid_height": height,
                "difficulty": knobs.get("difficulty"),
                "fallback": fell_back,
                "attempts": attempts,
            },
        )
    else:
        # A clean re-roll invalidates any earlier failure trace — a
        # leftover "fallback": true would contradict the level it sits
        # beside.
        ctx.adapter.resolve_path(trace_rel).unlink(missing_ok=True)
    dsl_text = accepted["dsl"] or raw_text
    bridges: list[str] = accepted["bridges"]
    if bridges:
        logger.info(
            "Layout %s: auto-bridged %d reachability break(s) — %s "
            "(agent design kept; geometry is tool work).",
            level_id, len(bridges), ", ".join(bridges),
        )
    snaps: list[str] = accepted.get("snaps") or []
    if snaps:
        logger.info(
            "Layout %s: snapped %d checkpoint(s) to valid ground — %s "
            "(agent design kept; column lookup is tool work).",
            level_id, len(snaps), ", ".join(snaps),
        )
    result: StampResult = stamp(dsl_text, width, height, tiles=tiles)
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
    for op, _args in parse_dsl(dsl_text):
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

    roster = [
        {
            "id": e.enemy_id,
            "archetype": e.archetype,
            "size": float(getattr(e, "size", 1.0) or 1.0),
            "behavior": e.behavior,
        }
        for e in ctx.bible.enemy_definitions.values()
    ]
    enemy_defs = {
        e["id"]: {"archetype": e["archetype"], "size": e["size"]}
        for e in roster
    }
    level_id = level.level_id
    with np.load(ctx.adapter.resolve_path(level.collision)) as data:
        grid = data["collision"]
    spawn = level.spawn or (0, 0)
    summary = _cells_summary(sorted(standable_cells(grid, tiles)))
    volume_summary = _volume_summary(grid, tiles)
    brief = level.brief or _level_brief(ctx, level_id)
    accepted_holder: dict[str, list[dict]] = {"placements": []}
    last_attempt: dict[str, str | None] = {"content": None}

    def generate(
        feedback: list[str] | None = None, max_tokens: int | None = None
    ) -> str:
        request = ctx.prompts.placement_generation(
            level_id, brief, roster, summary, max_enemies,
            spawn=spawn, volume_summary=volume_summary,
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
    stamp_provenance(ctx, level, content_hash)


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
        stage_id = ctx.artifacts["stage_id"]
        stage = ctx.bible.stages[stage_id]
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
        stage_id = ctx.artifacts["stage_id"]
        stage = ctx.bible.stages[stage_id]
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
        stage_id = ctx.artifacts["stage_id"]
        stage = ctx.bible.stages[stage_id]
        for level_id in stage.level_ids:
            level = ctx.bible.levels[level_id]
            decorate_level(
                ctx, level, max_decor=self.max_decor, phase_name=self.name
            )
            write_level_manifest(ctx, level)
        _stamp_metadata(ctx, self.name)
