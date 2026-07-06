"""Review renderer — per-level PNGs + a roster legend.

The "look at maps the moment they're generated" surface. Everything is
resolved from the databases: tile colors are *sampled from the tilesheet*
via the Tileset slots (not hardcoded), enemy colors come from each
EnemyDefinition's placeholder color, placements from Level.entities.
"""

from __future__ import annotations

import io
import logging
from typing import Any

from canon.bible.platformer import EnemyDefinition, Level, Tileset, TileType
from examples.platformer_pack.phases import _stamp_metadata

SCALE = 16  # px per cell

logger = logging.getLogger(__name__)


def _tile_palette(tileset: Tileset, sheet) -> dict[int, tuple]:
    """Sample one pixel per slot region from the tilesheet — consumers
    resolve appearance through the Tileset artifact, never constants."""
    palette: dict[int, tuple] = {}
    for slot in tileset.slots:
        x, y, _w, _h = slot.px_region or (0, 0, 1, 1)
        palette[int(slot.tile_type)] = sheet.getpixel((x + 1, y + 1))
    return palette


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return tuple(int(color[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def render_level(
    grid,
    level: Level,
    enemies: dict[str, EnemyDefinition],
    tileset: Tileset,
    sheet,
) -> bytes:
    from PIL import Image, ImageDraw

    height, width = grid.shape
    palette = _tile_palette(tileset, sheet)
    img = Image.new("RGB", (width * SCALE, height * SCALE))
    draw = ImageDraw.Draw(img)

    for y in range(height):
        for x in range(width):
            color = palette.get(int(grid[y, x]), palette[int(TileType.EMPTY)])
            draw.rectangle(
                (x * SCALE, y * SCALE, (x + 1) * SCALE - 1, (y + 1) * SCALE - 1),
                fill=tuple(color[:3]),
            )

    def _marker(x: int, y: int, outline: str) -> None:
        draw.rectangle(
            (x * SCALE + 2, y * SCALE + 2, (x + 1) * SCALE - 3, (y + 1) * SCALE - 3),
            outline=outline,
            width=2,
        )

    if level.spawn is not None:
        _marker(level.spawn[0], level.spawn[1], "#ffffff")
    if level.exit is not None:
        _marker(level.exit[0], level.exit[1], "#40ff70")

    for placement in level.entities:
        enemy_id = placement.ref.split(":", 1)[1]
        enemy = enemies.get(enemy_id)
        color = _hex_to_rgb(
            (enemy.stats.get("placeholder_color") if enemy else None) or "#ff00ff"
        )
        x, y = placement.pos
        draw.rectangle(
            (x * SCALE + 1, y * SCALE + 1, (x + 1) * SCALE - 2, (y + 1) * SCALE - 2),
            fill=color,
        )

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def render_legend(enemies: dict[str, EnemyDefinition]) -> bytes:
    from PIL import Image, ImageDraw

    row_h, swatch, pad, width = 28, 18, 8, 640
    img = Image.new("RGB", (width, pad * 2 + row_h * max(len(enemies), 1)), (24, 24, 32))
    draw = ImageDraw.Draw(img)
    for i, enemy in enumerate(enemies.values()):
        y = pad + i * row_h
        draw.rectangle(
            (pad, y, pad + swatch, y + swatch),
            fill=_hex_to_rgb(enemy.stats.get("placeholder_color", "#ff00ff")),
        )
        behavior = ", ".join(f"{k}={v}" for k, v in enemy.behavior.items())
        draw.text(
            (pad * 2 + swatch, y + 3),
            f"{enemy.name}  [{enemy.archetype}]  "
            f"hp={enemy.stats.get('hp')} spd={enemy.stats.get('speed')}  {behavior}",
            fill=(230, 230, 230),
        )
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


class RenderPhase:
    name = "plat:render"

    def run(self, ctx: Any) -> None:
        import numpy as np
        from PIL import Image

        stage_id = ctx.artifacts["stage_id"]
        tileset = ctx.bible.tilesets[stage_id]
        sheet = Image.open(ctx.adapter.resolve_path(tileset.tilesheet_path)).convert(
            "RGBA"
        )
        enemies = ctx.bible.enemy_definitions

        for level_id, level in ctx.bible.levels.items():
            with np.load(ctx.adapter.resolve_path(level.collision)) as data:
                grid = data["collision"]
            png = render_level(grid, level, enemies, tileset, sheet)
            ctx.adapter.write_binary(f"review/{stage_id}/{level_id}.png", png)

        ctx.adapter.write_binary("review/legend.png", render_legend(enemies))
        logger.info(
            "RenderPhase wrote %d level renders + legend to review/.",
            len(ctx.bible.levels),
        )
        _stamp_metadata(ctx, self.name)
