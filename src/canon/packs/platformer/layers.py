"""Visual layer tools: Tile Assignment (terrain) and Background (PRD §5.2
nodes, deterministic v1).

These are the art-facing layers of the §6.2 decomposition. Both are pure
functions of upstream artifacts — no LLM:

- **terrain.npz** maps each collision cell to a tileset SLOT INDEX. It is
  the layer renderers draw; collision keeps the physics. This is the
  visual/physics split that later lets diffusion art replace appearance
  without touching gameplay (Appendix E.2 motivation #1).
- **background.npz** is a banded placeholder (horizon bands). The file and
  its provenance edges are the point; making it beautiful comes with the
  style-guide work.

Per-level bodies are module functions shared by the sequential phases and
the DAG nodes (3b-2) — one body, two schedulers, byte-identical output.
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


def assign_level_terrain(ctx: Any, level: Any) -> None:
    """Map the level's collision cells to tileset slot indices.

    Slots carrying an ``autotile_mask`` param are variant slots (G5):
    their cells resolve through the 4-bit N=1/E=2/S=4/W=8 exposure mask
    computed from the collision grid — a bit is set when that neighbor
    is not collision-"solid"; out-of-grid neighbors count solid so level
    borders stay interior (mask 0). Slots carrying ``water_deep`` are a
    volume tile's submerged-interior variant, resolved by the air-above
    probe (water-striping fix). Every other slot keeps the 1:1
    tile_type → index map, so a pinned pre-autotile tileset (no variant
    slots) resolves exactly as before.
    """
    import numpy as np

    tileset = ctx.bible.tilesets[level.stage_id]
    # 1:1 map from unmasked slots + each autotiled type's mask-0 slot;
    # per-type mask → index tables ride separately.
    type_to_slot: dict[int, int] = {}
    variants: dict[int, dict[int, int]] = {}
    deep_slots: dict[int, int] = {}
    for slot in tileset.slots:
        if slot.params.get("water_deep"):
            # Submerged-interior variant (water-striping fix): resolved
            # by the air-above probe below, never the 1:1 default.
            deep_slots[int(slot.tile_type)] = slot.index
            continue
        mask_param = slot.params.get("autotile_mask")
        if mask_param is None:
            type_to_slot[int(slot.tile_type)] = slot.index
            continue
        variants.setdefault(int(slot.tile_type), {})[int(mask_param)] = slot.index
        if int(mask_param) == 0:
            type_to_slot[int(slot.tile_type)] = slot.index
    with np.load(ctx.adapter.resolve_path(level.collision)) as data:
        collision = data["collision"]
    terrain = np.vectorize(type_to_slot.get, otypes=[np.int8])(collision)

    if variants:
        # Support = collision category "solid" ONLY (one_way/hazard/
        # volume/empty all read as exposed); borders pad solid.
        solid_ids = sorted(
            {int(s.tile_type) for s in tileset.slots if s.collision == "solid"}
        )
        padded = np.pad(
            np.isin(collision, solid_ids), 1, constant_values=True
        )
        mask = (
            (~padded[:-2, 1:-1]).astype(np.int8)  # N = 1
            + (~padded[1:-1, 2:]).astype(np.int8) * 2  # E
            + (~padded[2:, 1:-1]).astype(np.int8) * 4  # S
            + (~padded[1:-1, :-2]).astype(np.int8) * 8  # W
        )
        for tile_type in sorted(variants):
            lut = np.zeros(16, dtype=np.int8)
            for m, index in sorted(variants[tile_type].items()):
                lut[m] = index
            cells = collision == tile_type
            terrain[cells] = lut[mask[cells]]

    if deep_slots:
        # A volume cell with NO air (collision "empty") directly above
        # is INTERIOR water — only the real air-water interface keeps
        # the tile art's bright surface lip. Out-of-grid counts non-air
        # (the floors' borders-stay-interior rule), so water touching
        # the grid top draws depth, not a lip. First paid run: l4/l6
        # fully-submerged levels re-drew the lip in every flooded cell
        # and the water read as a wall of stacked blocks.
        empty_ids = sorted(
            {int(s.tile_type) for s in tileset.slots if s.collision == "empty"}
        )
        air_above = np.pad(
            np.isin(collision, empty_ids), ((1, 0), (0, 0)),
            constant_values=False,
        )[:-1, :]
        for tile_type, deep_index in sorted(deep_slots.items()):
            cells = (collision == tile_type) & ~air_above
            terrain[cells] = deep_index

    level_dir = f"level/{level.stage_id}/{level.level_id}"
    level.terrain = f"{level_dir}/terrain.npz"
    level.terrain_hash = ctx.adapter.write_numpy(level.terrain, terrain=terrain)
    level.step_parents["terrain"] = [
        make_artifact_id("level", level.stage_id, level.level_id, "collision"),
        tileset.artifact_id,
    ]


#: Number of horizon bands in the placeholder background.
BACKGROUND_BANDS = 3


def paint_level_background(ctx: Any, level: Any) -> None:
    """Banded placeholder background for the level."""
    import numpy as np

    height, width = level.grid_height, level.grid_width
    rows = (np.arange(height, dtype=np.int8) * BACKGROUND_BANDS) // max(height, 1)
    background = np.repeat(rows[:, None], width, axis=1)

    level_dir = f"level/{level.stage_id}/{level.level_id}"
    level.background = f"{level_dir}/background.npz"
    level.background_hash = ctx.adapter.write_numpy(
        level.background, background=background
    )
    level.step_parents["background"] = [
        make_artifact_id("level", level.stage_id, level.level_id, "collision"),
        make_artifact_id("tileset", level.stage_id),
    ]


class TileAssignmentPhase:
    name = "plat:terrain"

    def run(self, ctx: Any) -> None:
        for level in ctx.bible.levels.values():
            assign_level_terrain(ctx, level)
        logger.info(
            "TileAssignmentPhase mapped %d levels to tileset slots.",
            len(ctx.bible.levels),
        )
        _stamp_meta(ctx, self.name)


class BackgroundPhase:
    name = "plat:background"

    #: Kept for back-compat with earlier imports.
    BANDS = BACKGROUND_BANDS

    def run(self, ctx: Any) -> None:
        for level in ctx.bible.levels.values():
            paint_level_background(ctx, level)
        logger.info(
            "BackgroundPhase wrote %d banded placeholder backgrounds.",
            len(ctx.bible.levels),
        )
        _stamp_meta(ctx, self.name)
