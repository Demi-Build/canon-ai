"""PlaceholderTilesetTool — a deterministic Tool (no LLM, no diffusion)
that emits a REAL Tileset artifact whose tilesheet happens to be solid
color squares.

This is the core of the slice's review story: every consumer (renderer,
pygame harness, Godot) resolves tile appearance through the Tileset model
+ tilesheet PNG. When diffusion-generated art arrives, only the tool
producing the sheet changes — nothing downstream does.

Since 3b the slots come from the game's tile registry (values in data):
one slot per registry tile, carrying the tile's name, physics category
(``collision``) and params, colored by ``color_role`` through the
placeholder palette below. The Phase 3b-deferred style-guide agent will
replace this palette with generated ones at the same seam.
"""

from __future__ import annotations

import io
import logging
from typing import Any

from canon.bible.artifacts import make_artifact_id
from canon.bible.platformer import Tileset, TileSlot
from examples.platformer_pack.phases import _stamp_metadata, stamp_provenance, warn
from examples.platformer_pack.tiles import DEFAULT_TILES, TileRegistry

TILE_PX = 16

logger = logging.getLogger(__name__)

#: Placeholder palette, keyed by registry ``color_role`` — muted terrain,
#: hazard red, liquid hues; enemy hues are assigned separately (saturated,
#: spaced, red and blue bands avoided) so nothing collides visually.
PLACEHOLDER_PALETTE: dict[str, tuple[int, int, int, int]] = {
    "background": (24, 24, 32, 255),
    "ground": (110, 110, 120, 255),
    "platform": (150, 120, 70, 255),
    "wall": (70, 70, 80, 255),
    "danger": (200, 40, 40, 255),
    "water": (40, 90, 200, 255),
    "lava": (235, 105, 25, 255),
    "mud": (95, 75, 55, 255),
    "ice": (170, 215, 240, 255),
    "basalt": (58, 52, 60, 255),
}

#: Loud placeholder for a color_role the palette doesn't know.
_UNKNOWN_ROLE_COLOR = (255, 0, 255, 255)


class PlaceholderTilesetPhase:
    name = "plat:tileset"

    def __init__(self, tiles: TileRegistry = DEFAULT_TILES) -> None:
        self.tiles = tiles

    def run(self, ctx: Any) -> None:
        from PIL import Image

        stage_id = ctx.artifacts["stage_id"]
        ordered = sorted(self.tiles.tiles, key=lambda t: t.id)
        # Style-guide palette (role → hex) when the style phase ran;
        # PLACEHOLDER_PALETTE fills any role it didn't cover.
        style: dict[str, str] = ctx.artifacts.get("palette", {})
        used: dict[str, str] = {}

        sheet = Image.new("RGBA", (TILE_PX * len(ordered), TILE_PX))
        slots: list[TileSlot] = []
        for i, tile in enumerate(ordered):
            styled = style.get(tile.color_role)
            if styled is not None:
                r, g, b = (int(styled.lstrip("#")[j : j + 2], 16) for j in (0, 2, 4))
                color = (r, g, b, 255)
            else:
                color = PLACEHOLDER_PALETTE.get(tile.color_role)
            if color is None:
                warn(
                    ctx,
                    f"tileset: tile {tile.name!r} has unknown color_role "
                    f"{tile.color_role!r} (palette roles: "
                    f"{sorted(PLACEHOLDER_PALETTE)}); rendered magenta.",
                )
                color = _UNKNOWN_ROLE_COLOR
            if tile.color_role:
                used[tile.color_role] = "#{:02x}{:02x}{:02x}".format(*color[:3])
            square = Image.new("RGBA", (TILE_PX, TILE_PX), color)
            sheet.paste(square, (i * TILE_PX, 0))
            slots.append(
                TileSlot(
                    index=i,
                    tile_type=tile.id,
                    name=tile.name,
                    px_region=(i * TILE_PX, 0, TILE_PX, TILE_PX),
                    collision=tile.category,
                    params=dict(tile.params),
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
            palette=used,
            parents=[make_artifact_id("stage", stage_id)],
        )
        manifest_hash = ctx.adapter.write_json_singleton(
            f"tileset/{stage_id}/manifest.json", tileset.model_dump(mode="json")
        )
        stamp_provenance(ctx, tileset, manifest_hash)
        ctx.bible.tilesets[stage_id] = tileset
        logger.info(
            "PlaceholderTilesetPhase wrote %s (%d slots: %s).",
            sheet_rel, len(slots), ", ".join(t.name for t in ordered),
        )
        _stamp_metadata(ctx, self.name)
