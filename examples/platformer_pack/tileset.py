"""PlaceholderTilesetTool — a deterministic Tool (no LLM, no diffusion)
that emits a REAL Tileset artifact whose tilesheet happens to be solid
color squares.

This is the core of the slice's review story: every consumer (renderer,
pygame harness, later Godot) resolves tile appearance through the Tileset
model + tilesheet PNG. When diffusion-generated art arrives, only the tool
producing the sheet changes — nothing downstream does.
"""

from __future__ import annotations

import io
import logging
from typing import Any

from canon.bible.artifacts import make_artifact_id
from canon.bible.platformer import Tileset, TileSlot, TileType
from examples.platformer_pack.phases import _stamp_metadata, stamp_provenance

TILE_PX = 16

logger = logging.getLogger(__name__)

#: Placeholder tile colors — muted terrain vs. hazard red vs. water blue;
#: enemy hues are assigned separately (saturated, spaced hues, red and blue
#: bands avoided) so nothing collides visually.
TILE_COLORS: dict[TileType, tuple[int, int, int, int]] = {
    TileType.EMPTY: (24, 24, 32, 255),  # background
    TileType.FLOOR: (110, 110, 120, 255),
    TileType.PLATFORM: (150, 120, 70, 255),
    TileType.WALL: (70, 70, 80, 255),
    TileType.SPIKE: (200, 40, 40, 255),
    TileType.WATER: (40, 90, 200, 255),
}

#: Physics semantics per tile (PRD Appendix E.3) — consumers read these
#: from the tileset manifest instead of hardcoding tile IDs.
TILE_COLLISION: dict[TileType, str] = {
    TileType.EMPTY: "none",
    TileType.FLOOR: "solid",
    TileType.PLATFORM: "one_way",
    TileType.WALL: "solid",
    TileType.SPIKE: "hazard",
    TileType.WATER: "water",
}


class PlaceholderTilesetPhase:
    name = "plat:tileset"

    def run(self, ctx: Any) -> None:
        from PIL import Image

        stage_id = ctx.artifacts["stage_id"]
        ordered = list(TILE_COLORS)  # TileType order, stable

        sheet = Image.new("RGBA", (TILE_PX * len(ordered), TILE_PX))
        slots: list[TileSlot] = []
        for i, tile_type in enumerate(ordered):
            tile = Image.new("RGBA", (TILE_PX, TILE_PX), TILE_COLORS[tile_type])
            sheet.paste(tile, (i * TILE_PX, 0))
            slots.append(
                TileSlot(
                    index=i,
                    tile_type=tile_type,
                    px_region=(i * TILE_PX, 0, TILE_PX, TILE_PX),
                    collision=TILE_COLLISION[tile_type],
                )
            )

        buffer = io.BytesIO()
        sheet.save(buffer, format="PNG")
        sheet_rel = f"tileset/{stage_id}/tilesheet.png"
        sheet_hash = ctx.adapter.write_binary(sheet_rel, buffer.getvalue())

        tileset = Tileset(
            artifact_id=make_artifact_id("tileset", stage_id),
            stage_id=stage_id,
            tilesheet_path=sheet_rel,
            tilesheet_hash=sheet_hash,
            slots=slots,
            parents=[make_artifact_id("stage", stage_id)],
        )
        manifest_hash = ctx.adapter.write_json_singleton(
            f"tileset/{stage_id}/manifest.json", tileset.model_dump(mode="json")
        )
        stamp_provenance(ctx, tileset, manifest_hash)
        ctx.bible.tilesets[stage_id] = tileset
        logger.info(
            "PlaceholderTilesetPhase wrote %s (%d slots).",
            sheet_rel, len(slots),
        )
        _stamp_metadata(ctx, self.name)
