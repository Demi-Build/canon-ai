"""Tileset phase — one Tileset artifact per stage, art-source pluggable.

This is the core of the slice's review story: every consumer (renderer,
pygame harness, Godot) resolves tile appearance through the Tileset model
+ tilesheet PNG, so only the sheet producer changes when art quality
does — nothing downstream.

This phase emits the PLACEHOLDER sheet only — deterministic solid-color
squares from the style palette (placeholder palette filling gaps): the
fake-backend look, byte-identical across runs, and what every gameplay
phase debugs against. Generated art arrives at the END of the run via
``art_phases.TilesetArtPhase``, which repaints the PNG in place with the
slot geometry frozen (art-at-the-end: no art money is spent before the
levels validate).

Since 3b the slots come from the game's tile registry (values in data):
one slot per registry tile, carrying the tile's name, physics category
(``collision``) and params, colored by ``color_role``.
"""

from __future__ import annotations

import io
import json
import logging
from typing import Any

from canon.bible.artifacts import make_artifact_id
from canon.bible.platformer import Tileset, TileSlot
from canon.pipeline.orchestrator import pinned_ids
from examples.platformer_pack.graphics import DEFAULT_GRAPHICS, GraphicsSpec
from examples.platformer_pack.phases import _stamp_metadata, stamp_provenance, warn
from examples.platformer_pack.tiles import DEFAULT_TILES, TileRegistry

logger = logging.getLogger(__name__)

#: Placeholder palette, keyed by registry ``color_role`` — muted terrain,
#: hazard red, liquid hues; enemy hues are assigned separately (saturated,
#: spaced, red and blue bands avoided) so nothing collides visually.
PLACEHOLDER_PALETTE: dict[str, tuple[int, int, int, int]] = {
    "background": (24, 24, 32, 255),
    "ground": (110, 110, 120, 255),
    "platform": (150, 120, 70, 255),
    "wall": (70, 70, 80, 255),
    "breakable": (205, 160, 75, 255),  # cracked ochre — a floor that crumbles

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

    def __init__(
        self,
        tiles: TileRegistry = DEFAULT_TILES,
        graphics: GraphicsSpec = DEFAULT_GRAPHICS,
    ) -> None:
        self.tiles = tiles
        self.graphics = graphics

    def owns(self, ctx: Any) -> list[str]:
        """The Tileset entity ids this node stamps — how a stale mark on
        ``tileset:<stage>`` (e.g. cascaded from a style regen) reschedules
        this phase, whose node id is ``phase:plat:tileset``."""
        return [
            make_artifact_id("tileset", sid)
            for sid in getattr(ctx.bible, "stages", {})
        ]

    def _style_palette(self, ctx: Any, stage_id: str) -> dict[str, str]:
        """Role → hex from the style-guide agent: in-process when the
        style phase ran this run, otherwise re-read from its artifact
        (``style/<stage>/style.json``) so a tileset-only regen still
        paints the generated palette, not the placeholder."""
        palette = ctx.artifacts.get("palettes", {}).get(stage_id)
        if palette is None:  # legacy single-palette artifacts shape
            palette = ctx.artifacts.get("palette")
        if palette is not None:
            return palette
        style_path = ctx.adapter.resolve_path(f"style/{stage_id}/style.json")
        if style_path.exists():
            return json.loads(style_path.read_text()).get("palette", {})
        return {}

    def run(self, ctx: Any) -> None:
        for stage_id in ctx.artifacts.get(
            "stage_ids", list(ctx.bible.stages)
        ):
            self._run_stage(ctx, stage_id)
        _stamp_metadata(ctx, self.name)

    def _run_stage(self, ctx: Any, stage_id: str) -> None:
        from PIL import Image

        if make_artifact_id("tileset", stage_id) in pinned_ids(ctx.bible):
            # This phase otherwise overwrites the tilesheet with flat
            # placeholder squares AND replaces the whole Tileset entity —
            # for a pinned (paid) tilesheet both must survive untouched.
            logger.info(
                "PlaceholderTilesetPhase: tileset:%s is pinned — kept.",
                stage_id,
            )
            return
        ordered = sorted(self.tiles.tiles, key=lambda t: t.id)
        # Style-guide palette (role → hex); PLACEHOLDER_PALETTE fills any
        # role it didn't cover.
        style: dict[str, str] = self._style_palette(ctx, stage_id)
        used: dict[str, str] = {}

        tile_px = self.graphics.tile_px
        sheet = Image.new("RGBA", (tile_px * len(ordered), tile_px))
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

            square = Image.new("RGBA", (tile_px, tile_px), color)
            sheet.paste(square, (i * tile_px, 0))
            slots.append(
                TileSlot(
                    index=i,
                    tile_type=tile.id,
                    name=tile.name,
                    px_region=(i * tile_px, 0, tile_px, tile_px),
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
            render_filter=self.graphics.render_filter,
            # §6.1 edges: the stage it belongs to, and the style guide
            # that seeds the art — a style regen cascades here.
            parents=[make_artifact_id("stage", stage_id), "phase:plat:style"],
        )
        manifest_hash = ctx.adapter.write_json_singleton(
            f"tileset/{stage_id}/manifest.json", tileset.model_dump(mode="json")
        )
        # The graphics spec is a generation input like the model: fold its
        # digest in so a spec swap invalidates even when (placeholder)
        # sheet bytes happen to match. TilesetArtPhase re-stamps with the
        # image model when it repaints at the end of the run.
        stamp_provenance(
            ctx, tileset, manifest_hash,
            model_extra=f"gfx:{self.graphics.digest()}",
        )
        ctx.bible.tilesets[stage_id] = tileset
        logger.info(
            "PlaceholderTilesetPhase wrote %s (%d slots: %s).",
            sheet_rel, len(slots), ", ".join(t.name for t in ordered),
        )
