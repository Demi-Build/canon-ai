"""Visual layer tools: Tile Assignment (terrain) and Background (PRD §5.2
nodes, deterministic v1).

These are the art-facing layers of the §6.2 decomposition. Both are pure
functions of upstream artifacts — no LLM:

- **terrain.npz** maps each collision cell to a tileset SLOT INDEX. It is
  the layer renderers draw; collision keeps the physics. This is the
  visual/physics split that later lets diffusion art replace appearance
  without touching gameplay (Appendix E.2 motivation #1).
- **background.npz** is a banded placeholder (horizon bands). The file and
  its provenance edges are the point; making it beautiful is Phase 3b.
"""

from __future__ import annotations

import logging
from typing import Any

from canon.bible.artifacts import make_artifact_id
from canon.bible.models import BibleMetadata

logger = logging.getLogger(__name__)


def _stamp_meta(ctx: Any, name: str) -> None:
    if not isinstance(getattr(ctx.bible, "metadata", None), BibleMetadata):
        ctx.bible.metadata = BibleMetadata()
    ctx.bible.metadata.phases_run.append(name)


class TileAssignmentPhase:
    name = "plat:terrain"

    def run(self, ctx: Any) -> None:
        import numpy as np

        stage_id = ctx.artifacts["stage_id"]
        tileset = ctx.bible.tilesets[stage_id]
        type_to_slot = {int(s.tile_type): s.index for s in tileset.slots}

        for level_id, level in ctx.bible.levels.items():
            with np.load(ctx.adapter.resolve_path(level.collision)) as data:
                collision = data["collision"]
            terrain = np.vectorize(type_to_slot.get, otypes=[np.int8])(collision)

            level_dir = f"level/{stage_id}/{level_id}"
            level.terrain = f"{level_dir}/terrain.npz"
            level.terrain_hash = ctx.adapter.write_numpy(
                level.terrain, terrain=terrain
            )
            level.step_parents["terrain"] = [
                make_artifact_id("level", stage_id, level_id, "collision"),
                tileset.artifact_id,
            ]
        logger.info(
            "TileAssignmentPhase mapped %d levels to tileset slots.",
            len(ctx.bible.levels),
        )
        _stamp_meta(ctx, self.name)


class BackgroundPhase:
    name = "plat:background"

    #: Number of horizon bands in the placeholder background.
    BANDS = 3

    def run(self, ctx: Any) -> None:
        import numpy as np

        stage_id = ctx.artifacts["stage_id"]
        for level_id, level in ctx.bible.levels.items():
            height, width = level.grid_height, level.grid_width
            rows = (np.arange(height, dtype=np.int8) * self.BANDS) // max(height, 1)
            background = np.repeat(rows[:, None], width, axis=1)

            level_dir = f"level/{stage_id}/{level_id}"
            level.background = f"{level_dir}/background.npz"
            level.background_hash = ctx.adapter.write_numpy(
                level.background, background=background
            )
            level.step_parents["background"] = [
                make_artifact_id("level", stage_id, level_id, "collision"),
                make_artifact_id("tileset", stage_id),
            ]
        logger.info(
            "BackgroundPhase wrote %d banded placeholder backgrounds.",
            len(ctx.bible.levels),
        )
        _stamp_meta(ctx, self.name)
