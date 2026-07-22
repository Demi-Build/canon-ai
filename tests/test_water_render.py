"""Translucent water rendering (readability arc, RB2): ``water_alpha``
is presentation data on GraphicsSpec (``manifest["graphics"]`` is its
dump), the block render draws the EXACT per-channel blend of the water
slot mean over the band-shaded sky, the skinned render pastes volume
tiles at scaled alpha so the backdrop shows through, and both renders
stay byte-deterministic. Draw only — physics and the tile ART on disk
are untouched."""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image
from pydantic import ValidationError

from canon.bible.platformer import Level, Tileset, TileSlot
from examples.platformer_pack.graphics import GraphicsSpec
from examples.platformer_pack.render import (
    SCALE,
    _band_shade,
    render_level,
    render_level_skinned,
)

EMPTY_MEAN = (24, 24, 32)
SOLID_MEAN = (100, 80, 60)
WATER_MEAN = (40, 90, 200)
BAND_COLOR = (200, 40, 160)
TILE = 8  # px_region side — the skinned render's cell size


def _fixture():
    """A 3x3 level: one water cell over open sky, a solid floor row.
    Flat-color slot regions make every sampled mean exact."""
    sheet = Image.new("RGBA", (TILE * 3, TILE))
    for i, color in enumerate((EMPTY_MEAN, SOLID_MEAN, WATER_MEAN)):
        sheet.paste((*color, 255), (i * TILE, 0, (i + 1) * TILE, TILE))
    tileset = Tileset(
        stage_id="s1",
        slots=[
            TileSlot(index=0, tile_type=0, name="empty", collision="empty",
                     px_region=(0, 0, TILE, TILE)),
            TileSlot(index=1, tile_type=1, name="floor", collision="solid",
                     px_region=(TILE, 0, TILE, TILE)),
            TileSlot(index=2, tile_type=20, name="water", collision="volume",
                     px_region=(TILE * 2, 0, TILE, TILE),
                     params={"speed_factor": 0.55}),
        ],
    )
    terrain = np.array([[0, 0, 0], [0, 2, 0], [1, 1, 1]])
    background = np.zeros((3, 3), dtype=int)
    level = Level(level_id="l1", stage_id="s1")
    return terrain, background, level, tileset, sheet


def _blend(fg: tuple, bg: tuple, alpha: float) -> tuple:
    return tuple(round(f * alpha + b * (1.0 - alpha)) for f, b in zip(fg, bg))


def _pixel(png: bytes, x: int, y: int) -> tuple:
    return Image.open(io.BytesIO(png)).getpixel((x, y))


def test_manifest_graphics_block_carries_water_alpha() -> None:
    # manifest["graphics"] is the spec's model_dump (compose.py) — the
    # field's presence in the dump IS the manifest carrier.
    assert GraphicsSpec().model_dump(mode="json")["water_alpha"] == 0.55
    with pytest.raises(ValidationError):
        GraphicsSpec(water_alpha=1.5)
    with pytest.raises(ValidationError):
        GraphicsSpec(water_alpha=-0.1)


def test_block_water_cell_is_exact_blend() -> None:
    terrain, background, level, tileset, sheet = _fixture()
    png = render_level(
        terrain, background, level, {}, tileset, sheet,
        graphics=GraphicsSpec(),
    )
    water_px = (1 * SCALE + SCALE // 2, 1 * SCALE + SCALE // 2)
    sky = _band_shade(EMPTY_MEAN, 0)
    assert _pixel(png, *water_px) == _blend(WATER_MEAN, sky, 0.55)
    # Solid tiles keep their opaque slot mean — only the volume path blends.
    assert _pixel(png, SCALE // 2, 2 * SCALE + SCALE // 2) == SOLID_MEAN
    # Boundary: water_alpha=1.0 degenerates to the opaque mean.
    opaque = render_level(
        terrain, background, level, {}, tileset, sheet,
        graphics=GraphicsSpec(water_alpha=1.0),
    )
    assert _pixel(opaque, *water_px) == WATER_MEAN


def test_skinned_water_shows_backdrop_through() -> None:
    terrain, background, level, tileset, sheet = _fixture()
    band = Image.new("RGB", (TILE, 3 * TILE), BAND_COLOR)
    png = render_level_skinned(
        terrain, background, level, {}, tileset, sheet, [band], {}, None,
        graphics=GraphicsSpec(),
    )
    water_px = _pixel(png, 1 * TILE + TILE // 2, 1 * TILE + TILE // 2)
    # Translucent: neither the opaque slot mean nor the bare backdrop —
    # a channel-wise mix strictly between the two.
    assert water_px != WATER_MEAN
    assert water_px != BAND_COLOR
    for p, w, b in zip(water_px, WATER_MEAN, BAND_COLOR):
        assert min(w, b) <= p <= max(w, b)
    # Solid tiles still paste fully opaque over the backdrop.
    assert _pixel(png, TILE // 2, 2 * TILE + TILE // 2) == SOLID_MEAN


def test_renders_are_byte_deterministic() -> None:
    terrain, background, level, tileset, sheet = _fixture()
    graphics = GraphicsSpec()
    block = [
        render_level(
            terrain, background, level, {}, tileset, sheet, graphics=graphics
        )
        for _ in range(2)
    ]
    assert block[0] == block[1]
    band = Image.new("RGB", (TILE, 3 * TILE), BAND_COLOR)
    skinned = [
        render_level_skinned(
            terrain, background, level, {}, tileset, sheet, [band], {}, None,
            graphics=graphics,
        )
        for _ in range(2)
    ]
    assert skinned[0] == skinned[1]
