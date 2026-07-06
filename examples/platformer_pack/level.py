"""Level phases: LayoutStampPhase (Layout Agent → DSL → Stamp Tool →
collision.npz) and PlacementPhase (Entity Agent → validated placements).

Invariants exercised: I3 (agent emits DSL, the deterministic stamp writes
cells), I5-lite (placements validated against the stamped grid — nothing
spawns on spikes), and the §6.3 hash contract (adapter's content hash for
collision.npz lands on Level.collision_hash).
"""

from __future__ import annotations

from typing import Any

from canon.bible.artifacts import make_artifact_id
from canon.bible.platformer import Level, Placement, SparseMaskEntry
from canon.llm.parsing import extract_json_object
from canon.pipeline.retry import retry_with_feedback
from canon.pipeline.rng import derive_rng
from canon.skeleton.core import roll_skeleton
from canon.skeleton.loader import load_skeleton_spec
from examples.platformer_pack.dsl import DslError, StampResult, stamp
from examples.platformer_pack.movement import DEFAULT_MOVEMENT, PlayerMovementSpec
from examples.platformer_pack.phases import (
    SCHEMAS_DIR,
    _stamp_metadata,
    stamp_provenance,
)
from examples.platformer_pack.validate import (
    check_level,
    check_placements,
    standable_cells,
)


def _fallback_dsl(width: int) -> str:
    """A guaranteed-valid layout so a fully misbehaving LLM still yields a
    walkable (if boring) level instead of a dead pipeline."""
    return f"floor(0,{width - 1})\nspawn(2)\nexit({width - 3})"


class LayoutStampPhase:
    name = "plat:layout"

    def __init__(
        self,
        width: int = 48,
        height: int = 16,
        movement: PlayerMovementSpec = DEFAULT_MOVEMENT,
    ) -> None:
        self.width = width
        self.height = height
        self.movement = movement

    def run(self, ctx: Any) -> None:
        spec = load_skeleton_spec(SCHEMAS_DIR / "level_layout.json")
        stage_id = ctx.artifacts["stage_id"]
        stage = ctx.bible.stages[stage_id]
        seed = str(getattr(ctx.config, "seed", ""))

        for level_id in stage.level_ids:
            brief = ctx.artifacts.get("level_briefs", {}).get(level_id, "")
            knobs = roll_skeleton(spec, derive_rng(seed, self.name, level_id))

            def generate(
                feedback: list[str] | None = None,
                max_tokens: int | None = None,
                _lid: str = level_id,
                _brief: str = brief,
                _knobs: dict = knobs,
            ) -> str:
                request = ctx.prompts.layout_generation(
                    _lid, _brief, _knobs, self.width, self.height,
                    self.movement, feedback=feedback,
                )
                if max_tokens is not None:
                    request.max_tokens = max_tokens
                return ctx.llm.generate(request, phase=f"{self.name}:{_lid}")

            def validate(content: str) -> tuple[bool, list[str]]:
                try:
                    result = stamp(content, self.width, self.height)
                except DslError as exc:
                    return False, [str(exc)]
                problems = check_level(
                    result.grid, result.spawn, result.exit, self.movement
                )
                return (not problems), problems

            dsl_text = retry_with_feedback(
                generate_fn=generate,
                validate_fn=validate,
                fallback=_fallback_dsl(self.width),
                max_retries=getattr(ctx.config, "max_retries", 3),
                label=f"{self.name}:{level_id}",
            )
            result: StampResult = stamp(dsl_text, self.width, self.height)

            level_dir = f"level/{stage_id}/{level_id}"
            collision_hash = ctx.adapter.write_numpy(
                f"{level_dir}/collision.npz", collision=result.grid
            )
            level = Level(
                level_id=level_id,
                stage_id=stage_id,
                grid_width=self.width,
                grid_height=self.height,
                collision=f"{level_dir}/collision.npz",
                collision_hash=collision_hash,
                hazards=result.hazards,
                # spawn/exit ride in triggers: they are point markers, and
                # Level has no dedicated fields for them (noted for Phase 3).
                triggers=[
                    SparseMaskEntry(x=result.spawn[0], y=result.spawn[1], type="spawn"),
                    SparseMaskEntry(x=result.exit[0], y=result.exit[1], type="exit"),
                ],
                parents=[
                    make_artifact_id("level", stage_id, level_id, "layout"),
                    stage.tileset_ref,
                ],
            )
            ctx.bible.levels[level_id] = level
            ctx.artifacts.setdefault("dsl_texts", {})[level_id] = dsl_text

        _stamp_metadata(ctx, self.name)


class PlacementPhase:
    name = "plat:placement"

    def __init__(self, max_enemies_per_level: int = 4) -> None:
        self.max_enemies = max_enemies_per_level

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
        valid_ids = {e["id"] for e in roster}

        for level_id in stage.level_ids:
            level = ctx.bible.levels[level_id]
            grid = self._load_grid(ctx, level, np)
            spawn = next(
                (t.x, t.y) for t in level.triggers if t.type == "spawn"
            )
            summary = self._standable_summary(grid)
            brief = ctx.artifacts.get("level_briefs", {}).get(level_id, "")
            accepted_holder: dict[str, list[dict]] = {"placements": []}

            def generate(
                feedback: list[str] | None = None,
                max_tokens: int | None = None,
                _lid: str = level_id,
                _brief: str = brief,
                _summary: str = summary,
            ) -> str:
                request = ctx.prompts.placement_generation(
                    _lid, _brief, roster, _summary, self.max_enemies,
                    feedback=feedback,
                )
                if max_tokens is not None:
                    request.max_tokens = max_tokens
                return ctx.llm.generate(request, phase=f"{self.name}:{_lid}")

            def validate(content: str) -> tuple[bool, list[str]]:
                obj = extract_json_object(content)
                if obj is None or not isinstance(obj.get("placements"), list):
                    return False, ['Return {"placements": [...]} as bare JSON.']
                accepted, problems = check_placements(
                    grid, obj["placements"], spawn, valid_ids
                )
                accepted_holder["placements"] = accepted
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

            level.entities = [
                Placement(ref=make_artifact_id("enemy", p["enemy_id"]), pos=(p["x"], p["y"]))
                for p in accepted
            ]
            level_dir = f"level/{stage_id}/{level_id}"
            ctx.adapter.write_json_singleton(
                f"{level_dir}/entities.json",
                [
                    {"enemy_id": p["enemy_id"], "x": p["x"], "y": p["y"]}
                    for p in accepted
                ],
            )
            # level.json is the level manifest — written last, after masks
            # and placements, so its dump carries the collision hash and
            # entity list (the §6.4 per-level entry point).
            content_hash = ctx.adapter.write_json_singleton(
                f"{level_dir}/level.json", level.model_dump(mode="json")
            )
            stamp_provenance(ctx, level, content_hash)

        _stamp_metadata(ctx, self.name)

    @staticmethod
    def _load_grid(ctx: Any, level: Level, np: Any):
        with np.load(ctx.adapter.resolve_path(level.collision)) as data:
            return data["collision"]

    @staticmethod
    def _standable_summary(grid) -> str:
        """Compact per-row ranges of standable cells — descriptive context
        for the Entity Agent (never a raw grid dump; I3 applies to prompts)."""
        by_row: dict[int, list[int]] = {}
        for x, y in sorted(standable_cells(grid)):
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
        return "; ".join(parts)
