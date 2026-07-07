"""Review renderer — per-level PNGs + a roster legend.

The "look at maps the moment they're generated" surface. Everything is
resolved from the databases: tile appearance comes from the TERRAIN layer
(slot indices into the tilesheet — the visual/physics split of §6.2),
background tint from the background layer bands, enemy colors and variant
markers from EnemyDefinitions + placement overrides + the game's variant
vocabulary, checkpoints from the triggers layer, decor from the foreground
layer.
"""

from __future__ import annotations

import io
import logging
from typing import Any

from canon.bible.platformer import EnemyDefinition, Level, Tileset
from examples.platformer_pack.phases import _stamp_metadata
from examples.platformer_pack.variants import DEFAULT_VARIANTS, VariantSet

SCALE = 16  # px per cell

#: Review styling for foreground decor types (closed set from DecoratorPhase).
DECOR_COLORS = {
    "stalactite": (150, 150, 165),
    "crystal": (170, 235, 240),
    "vine": (60, 140, 70),
    "moss": (110, 130, 60),
}

CHECKPOINT_COLOR = "#ffd24a"

logger = logging.getLogger(__name__)


def _slot_palette(tileset: Tileset, sheet) -> tuple[dict[int, tuple], dict[int, str]]:
    """Per-SLOT color samples + slot→category map. Consumers resolve
    appearance AND semantics through the Tileset artifact, never constants."""
    colors: dict[int, tuple] = {}
    categories: dict[int, str] = {}
    for slot in tileset.slots:
        x, y, _w, _h = slot.px_region or (0, 0, 1, 1)
        colors[slot.index] = sheet.getpixel((x + 1, y + 1))
        categories[slot.index] = slot.collision
    return colors, categories


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return tuple(int(color[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _band_shade(
    base: tuple[int, ...], band: int, bands: int = 3
) -> tuple[int, int, int]:
    """Subtle horizon gradient: the game's BACKGROUND color (empty-slot
    sample = style palette), lightened toward the top band. The last
    hardcoded gray left the palette agent unable to own the sky."""
    scale = 1.0 + 0.16 * (bands - 1 - int(band))
    return tuple(min(255, round(c * scale)) for c in base[:3])  # type: ignore[return-value]


def render_level(
    terrain,
    background,
    level: Level,
    enemies: dict[str, EnemyDefinition],
    tileset: Tileset,
    sheet,
    variants: VariantSet = DEFAULT_VARIANTS,
) -> bytes:
    from PIL import Image, ImageDraw

    height, width = terrain.shape
    slot_colors, slot_categories = _slot_palette(tileset, sheet)
    bg_base = next(
        (
            slot_colors[index][:3]
            for index, category in slot_categories.items()
            if category == "empty"
        ),
        (24, 24, 32),
    )
    img = Image.new("RGB", (width * SCALE, height * SCALE))
    draw = ImageDraw.Draw(img)

    for y in range(height):
        for x in range(width):
            slot = int(terrain[y, x])
            if slot_categories.get(slot) == "empty":
                color = _band_shade(bg_base, int(background[y, x]))
            else:
                color = tuple(slot_colors.get(slot, (255, 0, 255))[:3])
            draw.rectangle(
                (x * SCALE, y * SCALE, (x + 1) * SCALE - 1, (y + 1) * SCALE - 1),
                fill=color,
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
    # Checkpoints from the triggers layer (3b): amber markers.
    for trigger in level.triggers:
        if trigger.type == "checkpoint":
            _marker(trigger.x, trigger.y, CHECKPOINT_COLOR)

    for placement in level.entities:
        enemy_id = placement.ref.split(":", 1)[1]
        enemy = enemies.get(enemy_id)
        color = _hex_to_rgb(
            (enemy.stats.get("placeholder_color") if enemy else None) or "#ff00ff"
        )
        x, y = placement.pos
        variant = variants.by_name.get(str(placement.overrides.get("variant", "")))
        # Variant visuals resolve from the vocabulary (§6.1 overrides):
        # "scale" grows the body past the cell, "outline" frames it white.
        grow = 2 if variant and "scale" in variant.visual else 0
        draw.rectangle(
            (
                x * SCALE + 1 - grow, y * SCALE + 1 - grow,
                (x + 1) * SCALE - 2 + grow, (y + 1) * SCALE - 2 + grow,
            ),
            fill=color,
        )
        if variant and "outline" in variant.visual:
            draw.rectangle(
                (
                    x * SCALE - grow, y * SCALE - grow,
                    (x + 1) * SCALE - 1 + grow, (y + 1) * SCALE - 1 + grow,
                ),
                outline="#ffffff",
                width=2,
            )

    # Foreground decor drawn last — visually in front, like the game.
    for decor in level.foreground:
        color = DECOR_COLORS.get(decor.type, (200, 200, 200))
        cx, cy = decor.x * SCALE + SCALE // 2, decor.y * SCALE + SCALE // 2
        draw.polygon(
            [(cx, cy - 6), (cx + 5, cy), (cx, cy + 6), (cx - 5, cy)],
            fill=color,
        )

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def render_legend(enemies: dict[str, EnemyDefinition]) -> bytes:
    from PIL import Image, ImageDraw

    row_h, swatch, pad, width = 28, 18, 8, 720
    rows = max(len(enemies), 1) + 1  # + footer note
    img = Image.new("RGB", (width, pad * 2 + row_h * rows), (24, 24, 32))
    draw = ImageDraw.Draw(img)
    y = pad
    for enemy in enemies.values():
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
        y += row_h
    draw.text(
        (pad, y + 3),
        "white outline = variant placement (see manifest variants)   |   "
        "amber box = checkpoint   |   diamonds = foreground decor",
        fill=(170, 170, 180),
    )
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def render_level_review(
    ctx: Any, level: Level, variants: VariantSet = DEFAULT_VARIANTS
) -> None:
    """One level's review PNG — shared by RenderPhase and the DAG nodes."""
    import numpy as np
    from PIL import Image

    tileset = ctx.bible.tilesets[level.stage_id]
    sheet = Image.open(
        ctx.adapter.resolve_path(tileset.tilesheet_path)
    ).convert("RGBA")
    with np.load(ctx.adapter.resolve_path(level.terrain)) as data:
        terrain = data["terrain"]
    with np.load(ctx.adapter.resolve_path(level.background)) as data:
        background = data["background"]
    png = render_level(
        terrain, background, level, ctx.bible.enemy_definitions, tileset,
        sheet, variants=variants,
    )
    ctx.adapter.write_binary(
        f"review/{level.stage_id}/{level.level_id}.png", png
    )


def write_review_legend(ctx: Any) -> None:
    ctx.adapter.write_binary(
        "review/legend.png", render_legend(ctx.bible.enemy_definitions)
    )


class RenderPhase:
    name = "plat:render"

    def __init__(self, variants: VariantSet = DEFAULT_VARIANTS) -> None:
        self.variants = variants

    def run(self, ctx: Any) -> None:
        for level in ctx.bible.levels.values():
            render_level_review(ctx, level, variants=self.variants)
        write_review_legend(ctx)
        logger.info(
            "RenderPhase wrote %d level renders + legend to review/.",
            len(ctx.bible.levels),
        )
        _stamp_metadata(ctx, self.name)
