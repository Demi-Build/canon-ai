"""Level phases: LayoutStampPhase (Layout Agent → DSL → Stamp Tool →
collision.npz) and PlacementPhase (Entity Agent → validated placements).

Invariants exercised: I3 (agent emits DSL, the deterministic stamp writes
cells), I5-lite (placements validated against the stamped grid — nothing
spawns on spikes), and the §6.3 hash contract (adapter's content hash for
collision.npz lands on Level.collision_hash).
"""

from __future__ import annotations

import logging
from typing import Any

from canon.bible.artifacts import make_artifact_id
from canon.bible.platformer import Level, Placement, SparseMaskEntry
from canon.llm.parsing import extract_json_object
from canon.pipeline.retry import retry_with_feedback
from canon.pipeline.rng import derive_rng
from canon.skeleton.core import roll_skeleton
from canon.skeleton.loader import load_skeleton_spec
from examples.platformer_pack.dsl import DslError, StampResult, parse_dsl, stamp
from examples.platformer_pack.movement import DEFAULT_MOVEMENT, PlayerMovementSpec
from examples.platformer_pack.phases import (
    SCHEMAS_DIR,
    _stamp_metadata,
    stamp_provenance,
)
from examples.platformer_pack.rules import DEFAULT_RULES, GameRules
from examples.platformer_pack.validate import (
    check_level,
    check_placements,
    standable_cells,
    water_cells,
)

logger = logging.getLogger(__name__)


def _fallback_dsl(width: int) -> str:
    """A guaranteed-valid layout so a fully misbehaving LLM still yields a
    walkable (if boring) level instead of a dead pipeline."""
    return f"floor(0,{width - 1})\nspawn(2)\nexit({width - 3})"


def warn(ctx: Any, message: str) -> None:
    """Record a generation warning where reviewers will actually see it:
    the log now, and ctx.artifacts["slice_warnings"] → manifest.json +
    end-of-run summary. Fallbacks must never be silent — a run that
    "succeeds" on fallback content is a failed generation wearing a suit."""
    logger.warning(message)
    ctx.artifacts.setdefault("slice_warnings", []).append(message)


class LayoutStampPhase:
    name = "plat:layout"

    def __init__(
        self,
        width: int = 48,
        height: int = 16,
        movement: PlayerMovementSpec = DEFAULT_MOVEMENT,
        rules: GameRules = DEFAULT_RULES,
    ) -> None:
        self.width = width
        self.height = height
        self.movement = movement
        self.rules = rules

    def run(self, ctx: Any) -> None:
        spec = load_skeleton_spec(SCHEMAS_DIR / "level_layout.json")
        stage_id = ctx.artifacts["stage_id"]
        stage = ctx.bible.stages[stage_id]
        seed = str(getattr(ctx.config, "seed", ""))

        for index, level_id in enumerate(stage.level_ids):
            brief = ctx.artifacts.get("level_briefs", {}).get(level_id, "")
            # Difficulty escalates by level POSITION, not by roll — the
            # schema keys it off this context value (depends_on_context).
            # Clamped to the schema's 1..3 table for longer level lists.
            roll_context = {"level_number": min(index + 1, 3)}
            knobs = roll_skeleton(
                spec, derive_rng(seed, self.name, level_id), context=roll_context
            )
            # Variable dims (GridDims, §4.2): schema-rolled per level;
            # constructor values are the fallback for schemas without them.
            width = int(knobs.get("grid_width", self.width))
            height = int(knobs.get("grid_height", self.height))
            last_attempt: dict[str, str | None] = {"content": None}

            def generate(
                feedback: list[str] | None = None,
                max_tokens: int | None = None,
                _lid: str = level_id,
                _brief: str = brief,
                _knobs: dict = knobs,
                _last: dict = last_attempt,
                _w: int = width,
                _h: int = height,
            ) -> str:
                request = ctx.prompts.layout_generation(
                    _lid, _brief, _knobs, _w, _h,
                    self.movement, rules=self.rules,
                    previous=_last["content"], feedback=feedback,
                )
                if max_tokens is not None:
                    request.max_tokens = max_tokens
                content = ctx.llm.generate(request, phase=f"{self.name}:{_lid}")
                _last["content"] = content
                return content

            def validate(
                content: str, _w: int = width, _h: int = height
            ) -> tuple[bool, list[str]]:
                try:
                    result = stamp(content, _w, _h)
                except DslError as exc:
                    return False, [str(exc)]
                problems = check_level(
                    result.grid, result.spawn, result.exit, self.movement,
                    rules=self.rules,
                )
                return (not problems), problems

            fallback_dsl = _fallback_dsl(width)
            dsl_text = retry_with_feedback(
                generate_fn=generate,
                validate_fn=validate,
                fallback=fallback_dsl,
                max_retries=getattr(ctx.config, "max_retries", 3),
                label=f"{self.name}:{level_id}",
            )
            if dsl_text == fallback_dsl:
                warn(
                    ctx,
                    f"layout {level_id}: LLM output never validated; level is "
                    "the flat FALLBACK layout, not generated content.",
                )
            result: StampResult = stamp(dsl_text, width, height)

            level_dir = f"level/{stage_id}/{level_id}"
            collision_hash = ctx.adapter.write_numpy(
                f"{level_dir}/collision.npz", collision=result.grid
            )
            layout_aid = make_artifact_id("level", stage_id, level_id, "layout")
            collision_aid = make_artifact_id(
                "level", stage_id, level_id, "collision"
            )
            level = Level(
                level_id=level_id,
                stage_id=stage_id,
                grid_width=width,
                grid_height=height,
                spawn=result.spawn,
                exit=result.exit,
                collision=f"{level_dir}/collision.npz",
                collision_hash=collision_hash,
                hazards=result.hazards,
                parents=[layout_aid, stage.tileset_ref],
                step_parents={
                    "collision": [layout_aid],
                    "hazards": [collision_aid],
                    "triggers": [collision_aid],
                },
            )
            # Sparse layer files (§6.4) — hazards from the stamp; triggers
            # reserved-but-present so the layer set is complete.
            level.hazards_hash = ctx.adapter.write_json_singleton(
                f"{level_dir}/hazards.json",
                [h.model_dump(mode="json") for h in level.hazards],
            )
            level.triggers_hash = ctx.adapter.write_json_singleton(
                f"{level_dir}/triggers.json", []
            )
            ctx.bible.levels[level_id] = level
            ctx.artifacts.setdefault("dsl_texts", {})[level_id] = dsl_text

            op_counts: dict[str, int] = {}
            for op, _args in parse_dsl(dsl_text):
                op_counts[op] = op_counts.get(op, 0) + 1
            logger.info(
                "Layout %s (difficulty %s, %dx%d): %s; spawn %s -> exit %s, "
                "%d hazard cells",
                level_id, knobs.get("difficulty"), width, height,
                ", ".join(f"{n}x {op}" for op, n in sorted(op_counts.items())),
                result.spawn, result.exit, len(result.hazards),
            )

        _stamp_metadata(ctx, self.name)


class PlacementPhase:
    name = "plat:placement"

    def __init__(
        self,
        max_enemies_per_level: int = 4,
        rules: GameRules = DEFAULT_RULES,
    ) -> None:
        self.max_enemies = max_enemies_per_level
        self.rules = rules

    def run(self, ctx: Any) -> None:
        import numpy as np

        stage_id = ctx.artifacts["stage_id"]
        stage = ctx.bible.stages[stage_id]
        roster = [
            {
                "id": e.enemy_id,
                "archetype": e.archetype,
                "behavior": e.behavior,
            }
            for e in ctx.bible.enemy_definitions.values()
        ]
        archetypes = {e["id"]: e["archetype"] for e in roster}

        for level_id in stage.level_ids:
            level = ctx.bible.levels[level_id]
            grid = self._load_grid(ctx, level, np)
            spawn = level.spawn or (0, 0)
            summary = self._standable_summary(grid)
            water_summary = self._cells_summary(sorted(water_cells(grid)))
            brief = ctx.artifacts.get("level_briefs", {}).get(level_id, "")
            accepted_holder: dict[str, list[dict]] = {"placements": []}
            last_attempt: dict[str, str | None] = {"content": None}

            def generate(
                feedback: list[str] | None = None,
                max_tokens: int | None = None,
                _lid: str = level_id,
                _brief: str = brief,
                _summary: str = summary,
                _water: str = water_summary,
                _spawn: tuple[int, int] = spawn,
                _last: dict = last_attempt,
            ) -> str:
                request = ctx.prompts.placement_generation(
                    _lid, _brief, roster, _summary, self.max_enemies,
                    spawn=_spawn, water_summary=_water,
                    previous=_last["content"], feedback=feedback,
                )
                if max_tokens is not None:
                    request.max_tokens = max_tokens
                content = ctx.llm.generate(request, phase=f"{self.name}:{_lid}")
                _last["content"] = content
                return content

            last_problems: list[str] = []

            def validate(content: str) -> tuple[bool, list[str]]:
                obj = extract_json_object(content)
                if obj is None or not isinstance(obj.get("placements"), list):
                    return False, ['Return {"placements": [...]} as bare JSON.']
                accepted, problems = check_placements(
                    grid, obj["placements"], spawn, archetypes, rules=self.rules
                )
                accepted_holder["placements"] = accepted
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
                label=f"{self.name}:{level_id}",
            )
            accepted = accepted_holder["placements"][: self.max_enemies]
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
                    # canonical; the elite marker rides on the placement.
                    overrides={"elite": True, "hp_mult": 2} if p["elite"] else {},
                )
                for p in accepted
            ]
            level_dir = f"level/{stage_id}/{level_id}"
            level.entities_hash = ctx.adapter.write_json_singleton(
                f"{level_dir}/entities.json",
                [
                    {
                        "enemy_id": p["enemy_id"],
                        "x": p["x"],
                        "y": p["y"],
                        "elite": p["elite"],
                    }
                    for p in accepted
                ],
            )
            level.step_parents["entities"] = [
                make_artifact_id("level", stage_id, level_id, "collision"),
                make_artifact_id("level", stage_id, level_id, "hazards"),
            ]
            logger.info(
                "Placement %s: %d enemies — %s",
                level_id, len(accepted),
                ", ".join(
                    f"{p['enemy_id']}{'*ELITE*' if p['elite'] else ''}"
                    f"@({p['x']},{p['y']})"
                    for p in accepted
                )
                or "none",
            )

        _stamp_metadata(ctx, self.name)

    @staticmethod
    def _load_grid(ctx: Any, level: Level, np: Any):
        with np.load(ctx.adapter.resolve_path(level.collision)) as data:
            return data["collision"]

    @classmethod
    def _standable_summary(cls, grid) -> str:
        """Compact per-row ranges of standable cells — descriptive context
        for the Entity Agent (never a raw grid dump; I3 applies to prompts)."""
        return cls._cells_summary(sorted(standable_cells(grid)))

    @staticmethod
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


DECOR_TYPES = ("stalactite", "crystal", "vine", "moss")


class DecoratorPhase:
    """Foreground decoration (sparse layer the player passes in front
    of/behind) + the level.json manifest, written last so it carries every
    layer hash and the placement list (the §6.4 per-level entry point)."""

    name = "plat:decorator"

    def __init__(self, max_decor: int = 6) -> None:
        self.max_decor = max_decor

    def run(self, ctx: Any) -> None:
        stage_id = ctx.artifacts["stage_id"]
        stage = ctx.bible.stages[stage_id]

        for level_id in stage.level_ids:
            level = ctx.bible.levels[level_id]
            brief = ctx.artifacts.get("level_briefs", {}).get(level_id, "")
            accepted_holder: dict[str, list[dict]] = {"decor": []}

            def generate(
                feedback: list[str] | None = None,
                max_tokens: int | None = None,
                _lid: str = level_id,
                _brief: str = brief,
                _level: Level = level,
            ) -> str:
                request = ctx.prompts.decor_generation(
                    _lid, _brief, _level.grid_width, _level.grid_height,
                    DECOR_TYPES, self.max_decor, feedback=feedback,
                )
                if max_tokens is not None:
                    request.max_tokens = max_tokens
                return ctx.llm.generate(request, phase=f"{self.name}:{_lid}")

            def validate(
                content: str, _level: Level = level
            ) -> tuple[bool, list[str]]:
                obj = extract_json_object(content)
                if obj is None or not isinstance(obj.get("decor"), list):
                    return False, ['Return {"decor": [...]} as bare JSON.']
                accepted, problems = [], []
                for d in obj["decor"][: self.max_decor]:
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
                        or not (0 <= x < _level.grid_width)
                        or not (0 <= y < _level.grid_height)
                    ):
                        problems.append(
                            f"decor at ({x}, {y}) is outside the "
                            f"{_level.grid_width}x{_level.grid_height} grid."
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
                label=f"{self.name}:{level_id}",
            )
            decor = accepted_holder["decor"]

            level.foreground = [
                SparseMaskEntry(x=d["x"], y=d["y"], type=d["type"]) for d in decor
            ]
            level_dir = f"level/{stage_id}/{level_id}"
            level.foreground_hash = ctx.adapter.write_json_singleton(
                f"{level_dir}/foreground.json", decor
            )
            level.step_parents["foreground"] = [
                make_artifact_id("level", stage_id, level_id, "collision"),
                stage.tileset_ref,
            ]

            # level.json — the manifest, last, carrying every layer hash.
            content_hash = ctx.adapter.write_json_singleton(
                f"{level_dir}/level.json", level.model_dump(mode="json")
            )
            stamp_provenance(ctx, level, content_hash)
            logger.info(
                "Decor %s: %d pieces — %s",
                level_id, len(decor),
                ", ".join(f"{d['type']}@({d['x']},{d['y']})" for d in decor)
                or "none",
            )

        _stamp_metadata(ctx, self.name)
