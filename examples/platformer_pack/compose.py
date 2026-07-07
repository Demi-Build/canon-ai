"""Compose the platformer slice pipeline (sequential run_pipeline order).

World → Stage → Enemies → Tileset → Layout/Stamp → Placement → Render →
slice manifest. No orchestrator, no `requires` — deliberately Phase-2-free.
"""

from __future__ import annotations

import logging
from typing import Any

from canon.bible.models import BibleMetadata
from examples.platformer_pack.level import LayoutStampPhase, PlacementPhase
from examples.platformer_pack.movement import DEFAULT_MOVEMENT, PlayerMovementSpec
from examples.platformer_pack.phases import EnemyGeneratorPhase, StagePhase, WorldPhase
from examples.platformer_pack.render import RenderPhase
from examples.platformer_pack.tileset import PlaceholderTilesetPhase

logger = logging.getLogger(__name__)


class SliceManifestPhase:
    """Root manifest: what exists and where the harness should look.
    No timestamps — the slice tree is byte-deterministic by contract."""

    name = "plat:manifest"

    def run(self, ctx: Any) -> None:
        stage_id = ctx.artifacts["stage_id"]
        manifest = {
            "game": "platformer_slice",
            "seed": str(getattr(ctx.config, "seed", "")),
            "world": ctx.bible.world.title if ctx.bible.world else "",
            "stage_id": stage_id,
            "levels": list(ctx.bible.stages[stage_id].level_ids),
            "enemies": sorted(ctx.bible.enemy_definitions),
            "movement": DEFAULT_MOVEMENT.model_dump(),
            # Fallbacks and dropped content are failures wearing a suit —
            # they must survive the run and reach the reviewer.
            "warnings": list(ctx.artifacts.get("slice_warnings", [])),
        }
        ctx.adapter.write_json_singleton("manifest.json", manifest)

        # Generation report — the positive summary, MazeWorld-style.
        stage = ctx.bible.stages[stage_id]
        logger.info(
            "Slice complete: world %r / stage %r (%s) — %d levels, "
            "%d enemy definitions, %d placements, %d warning(s).",
            manifest["world"], stage_id, stage.theme,
            len(ctx.bible.levels), len(ctx.bible.enemy_definitions),
            sum(len(lv.entities) for lv in ctx.bible.levels.values()),
            len(manifest["warnings"]),
        )

        if not isinstance(getattr(ctx.bible, "metadata", None), BibleMetadata):
            ctx.bible.metadata = BibleMetadata()
        ctx.bible.metadata.phases_run.append(self.name)


def compose_pipeline(
    num_levels: int = 3,
    num_enemies: int = 3,
    width: int = 48,
    height: int = 16,
    movement: PlayerMovementSpec = DEFAULT_MOVEMENT,
    engine: str = "json",
) -> list:
    phases = [
        WorldPhase(),
        StagePhase(num_levels=num_levels, num_enemies=num_enemies),
        EnemyGeneratorPhase(count=num_enemies),
        PlaceholderTilesetPhase(),
        LayoutStampPhase(width=width, height=height, movement=movement),
        PlacementPhase(),
        RenderPhase(),
        SliceManifestPhase(),
    ]
    if engine == "godot":
        from examples.platformer_pack.godot_export import GodotExportPhase

        phases.append(GodotExportPhase())
    return phases
