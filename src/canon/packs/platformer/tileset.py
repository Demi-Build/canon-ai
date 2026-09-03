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
(``collision``) and params, colored by ``color_role``. The ``floor``
tile expands to 16 autotile variant slots (4-bit exposure mask, G5) —
mask semantics in ``AUTOTILE_BIT_SEMANTICS``, lookup table written to
``tileset/<stage>/autotile.json`` for engine exporters.
"""

from __future__ import annotations

import io
import json
import logging
from typing import Any

from canon.bible.artifacts import make_artifact_id
from canon.bible.platformer import Tileset, TileSlot
from canon.packs.platformer.graphics import DEFAULT_GRAPHICS, GraphicsSpec
from canon.packs.platformer.phases import _stamp_metadata, stamp_provenance, warn
from canon.packs.platformer.tiles import DEFAULT_TILES, TileRegistry
from canon.pipeline.orchestrator import pinned_ids

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
    "box": (190, 125, 45, 255),  # bronze item container (bump/stomp to open)

    "danger": (200, 40, 40, 255),
    # Underwater hazards (water arc): warm hues — check_palette's hazard
    # warmth rule (r > b) applies to every hazard role.
    "urchin": (185, 70, 110, 255),  # spiny magenta-rose
    "mine": (170, 85, 55, 255),  # rusted shell
    "water": (40, 90, 200, 255),
    "lava": (235, 105, 25, 255),
    "mud": (95, 75, 55, 255),
    "ice": (170, 215, 240, 255),
    "basalt": (58, 52, 60, 255),
}

#: Loud placeholder for a color_role the palette doesn't know.
_UNKNOWN_ROLE_COLOR = (255, 0, 255, 255)

#: Autotile mask contract, written verbatim into autotile.json. A set bit
#: means that side is EXPOSED (the neighbor is not collision-"solid");
#: out-of-grid neighbors count solid so level borders stay interior.
AUTOTILE_BIT_SEMANTICS = (
    "N=1,E=2,S=4,W=8; set bit = exposed (neighbor not solid); "
    "out-of-grid counts solid"
)


def shade_floor_variant(img: Any, mask: int, delta: int = 18) -> Any:
    """Edge-shade one floor autotile variant; region mean preserved EXACTLY.

    Per EXPOSED edge (set mask bit) a 2px band at that edge is shifted
    (top-left light: N +delta, W +delta/2, S -delta, E -delta/2) PAIRED
    with the equal-pixel-count adjacent band shifted the opposite sign,
    so every region-average consumer (block render 1x1 sample, pygame
    tile colors, QA palette_conformance) still resolves the flat base
    color. Clamp guard: a pair's shift shrinks until no pixel would
    leave 0..255, keeping the pair balanced. mask 0 returns the image
    untouched (the flat interior square the slice test pins).
    """
    if mask == 0:
        return img
    import numpy as np
    from PIL import Image

    px = np.asarray(img, dtype=np.int16).copy()
    height, width = px.shape[:2]
    band = 2
    half = delta // 2
    # (bit, edge shift, edge band, adjacent band) in N/E/S/W bit order.
    ops = (
        (1, delta, np.s_[0:band, :], np.s_[band : 2 * band, :]),
        (2, -half, np.s_[:, width - band :], np.s_[:, width - 2 * band : width - band]),
        (4, -delta, np.s_[height - band :, :], np.s_[height - 2 * band : height - band, :]),
        (8, half, np.s_[:, 0:band], np.s_[:, band : 2 * band]),
    )
    for bit, shift, edge, adjacent in ops:
        if not mask & bit:
            continue
        plus, minus = (edge, adjacent) if shift > 0 else (adjacent, edge)
        effective = min(
            abs(shift),
            int(255 - px[plus][..., :3].max()),
            int(px[minus][..., :3].min()),
        )
        if effective <= 0:
            continue
        px[plus][..., :3] += effective  # basic-slice views: mutates px
        px[minus][..., :3] -= effective
    return Image.fromarray(px.astype(np.uint8), "RGBA")


def derive_water_deep(img: Any) -> Any:
    """Submerged-INTERIOR variant of a volume tile, derived in CODE from
    the surface tile (water-striping fix): the top half — where the art
    direction paints the bright surface lip — is replaced by the bottom
    half stacked twice, then the region mean is recentered onto the
    source's (clamp-guarded constant shift), so every region-average
    consumer (block render 1x1 sample, pygame tile colors, QA palette
    conformance) resolves both variants to the same palette color.
    Deterministic, $0: no second generation — first paid run, l4/l6
    fully-submerged levels re-drew the lip in EVERY flooded cell and the
    water read as a wall of stacked blocks. The placeholder's flat
    square passes through visually unchanged."""
    import numpy as np
    from PIL import Image

    px = np.asarray(img.convert("RGBA"), dtype=np.int16)
    height = px.shape[0]
    bottom = px[height // 2 :]
    out = np.vstack([bottom, bottom])[:height].copy()
    for ch in range(3):
        delta = int(round(float(px[..., ch].mean()) - float(out[..., ch].mean())))
        if delta:
            out[..., ch] = np.clip(out[..., ch] + delta, 0, 255)
    return Image.fromarray(out.astype(np.uint8), "RGBA")


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
        # "floor" expands to 16 autotile variants; volume tiles carry a
        # submerged-interior sibling; total stays int8-safe.
        total_slots = sum(
            16 if t.name == "floor" else 2 if t.category == "volume" else 1
            for t in ordered
        )
        sheet = Image.new("RGBA", (tile_px * total_slots, tile_px))
        slots: list[TileSlot] = []
        autotile_type = 0
        autotile_variants: dict[str, dict[str, Any]] = {}
        for tile in ordered:
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
            if tile.name == "floor":
                # 16 CONSECUTIVE variant slots in mask order 0..15. Mask 0
                # (interior) comes FIRST and stays the untouched flat
                # square: the slice test samples the first "floor" slot's
                # interior pixel, and mean-preserving shading keeps every
                # variant's region average == this flat color.
                autotile_type = tile.id
                for m in range(16):
                    i = len(slots)
                    region = (i * tile_px, 0, tile_px, tile_px)
                    sheet.paste(shade_floor_variant(square, m), (i * tile_px, 0))
                    slots.append(
                        TileSlot(
                            index=i,
                            tile_type=tile.id,
                            name=tile.name,
                            px_region=region,
                            collision=tile.category,
                            params={**tile.params, "autotile_mask": m},
                        )
                    )
                    autotile_variants[str(m)] = {
                        "index": i, "px_region": list(region),
                    }
                continue
            i = len(slots)
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
            if tile.category == "volume":
                # Submerged-interior SIBLING (water-striping fix): same
                # type/category/params + the water_deep marker, resolved
                # by assign_level_terrain's air-above probe so only the
                # real air-water interface draws the surface lip. The
                # placeholder's flat square needs no lip removal; the
                # art repaint derives its pixels via derive_water_deep.
                j = len(slots)
                sheet.paste(square, (j * tile_px, 0))
                slots.append(
                    TileSlot(
                        index=j,
                        tile_type=tile.id,
                        name=tile.name,
                        px_region=(j * tile_px, 0, tile_px, tile_px),
                        collision=tile.category,
                        params={**tile.params, "water_deep": True},
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
        # Mask → slot lookup for engine exporters (Godot TileMap): the
        # registry contract guarantees "floor" exists, so this always
        # writes beside the manifest.
        ctx.adapter.write_json_singleton(
            f"tileset/{stage_id}/autotile.json",
            {
                "tile": "floor",
                "tile_type": autotile_type,
                "bit_semantics": AUTOTILE_BIT_SEMANTICS,
                "variants": autotile_variants,
            },
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
