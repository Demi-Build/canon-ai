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
from canon.packs.platformer.combat import effective_size, occupancy
from canon.packs.platformer.graphics import DEFAULT_GRAPHICS, GraphicsSpec
from canon.packs.platformer.phases import _stamp_metadata
from canon.packs.platformer.variants import DEFAULT_VARIANTS, VariantSet

SCALE = 16  # px per cell

#: Review styling for foreground decor types (closed set from DecoratorPhase).
DECOR_COLORS = {
    "stalactite": (150, 150, 165),
    "crystal": (170, 235, 240),
    "vine": (60, 140, 70),
    "moss": (110, 130, 60),
}

CHECKPOINT_COLOR = "#ffd24a"
#: Reward markers (secret alcoves, Chunk E) draw as a filled gold GEM — a
#: distinct shape from the outlined checkpoint boxes so they read at a glance.
REWARD_COLOR = (255, 208, 74)
#: Secret-room entrances/portals (multi-room arc): cyan TRIANGLES — filled
#: on the main map (entrance; points the verb's way: down = pipe, up =
#: door), outlined inside the room (the return portal). Inert on the play
#: surfaces until the consumer chunk lands.
ROOM_COLOR = (80, 220, 255)

logger = logging.getLogger(__name__)


def _slot_palette(tileset: Tileset, sheet) -> tuple[dict[int, tuple], dict[int, str]]:
    """Per-SLOT color samples + slot→category map. Consumers resolve
    appearance AND semantics through the Tileset artifact, never constants.
    The sample is the tile REGION's average — flat placeholder squares
    resolve unchanged, generated textures resolve to their palette-
    conformed mean instead of one arbitrary pixel."""
    from PIL import Image

    colors: dict[int, tuple] = {}
    categories: dict[int, str] = {}
    for slot in tileset.slots:
        x, y, w, h = slot.px_region or (0, 0, 1, 1)
        region = sheet.crop((x, y, x + w, y + h))
        colors[slot.index] = region.resize((1, 1), Image.BILINEAR).getpixel((0, 0))
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
    items: dict[str, Any] | None = None,
    graphics: GraphicsSpec = DEFAULT_GRAPHICS,
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
            category = slot_categories.get(slot)
            if category == "empty":
                color = _band_shade(bg_base, int(background[y, x]))
            elif category == "volume":
                # Translucent water (RB2): the slot mean composited at
                # water_alpha over this cell's band-shaded sky — the
                # analytic render shows the same truth the skinned/play
                # surfaces draw. Presentation only; terrain untouched.
                sky = _band_shade(bg_base, int(background[y, x]))
                mean = slot_colors.get(slot, (255, 0, 255))[:3]
                a = graphics.water_alpha
                color = tuple(
                    round(m * a + s * (1.0 - a)) for m, s in zip(mean, sky)
                )
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
    # Reward markers (secret alcoves, Chunk E): a filled gold gem — a distinct
    # SHAPE from the outlined checkpoint boxes. Inert on the play surfaces.
    for trigger in level.triggers:
        if trigger.type == "reward":
            cx = trigger.x * SCALE + SCALE // 2
            cy = trigger.y * SCALE + SCALE // 2
            draw.polygon(
                [(cx, cy - 6), (cx + 5, cy), (cx, cy + 6), (cx - 5, cy)],
                fill=REWARD_COLOR, outline="#ffffff",
            )
    # Secret-room markers (multi-room arc): cyan triangles pointing the
    # entry verb's way (down = pipe, up = door) — filled = the main-map
    # entrance, outlined = the room's return portal.
    for trigger in level.triggers:
        if trigger.type not in ("room_entrance", "room_portal"):
            continue
        cx = trigger.x * SCALE + SCALE // 2
        cy = trigger.y * SCALE + SCALE // 2
        down = str(trigger.params.get("verb", "pipe")) == "pipe"
        points = (
            [(cx - 6, cy - 5), (cx + 6, cy - 5), (cx, cy + 6)]
            if down
            else [(cx - 6, cy + 5), (cx + 6, cy + 5), (cx, cy - 6)]
        )
        if trigger.type == "room_entrance":
            draw.polygon(points, fill=ROOM_COLOR, outline="#ffffff")
        else:
            draw.polygon(points, outline=ROOM_COLOR)

    # Items layer (Arc 2). BOX placements carry a solid box tile every
    # consumer overlays onto the grid — the analytic render must show it
    # (it IS collision). Items draw as filled circles in their definition
    # color (a distinct shape from enemy rects, gems, and decor diamonds);
    # a boxed item's circle rides on its box cell.
    box_slot = next(
        (
            slot.index
            for slot in getattr(tileset, "slots", []) or []
            if bool((slot.params or {}).get("container"))
        ),
        None,
    )
    for placement in getattr(level, "items", []) or []:
        item_id = placement.ref.split(":", 1)[1]
        item = (items or {}).get(item_id)
        color = _hex_to_rgb(
            (item.stats.get("placeholder_color") if item else None)
            or "#ffd700"
        )
        x, y = placement.pos
        if str(placement.overrides.get("source", "")) == "box":
            box_color = (
                tuple(slot_colors[box_slot][:3])
                if box_slot is not None and box_slot in slot_colors
                else (190, 125, 45)
            )
            draw.rectangle(
                (x * SCALE, y * SCALE, (x + 1) * SCALE - 1, (y + 1) * SCALE - 1),
                fill=box_color, outline="#ffffff",
            )
            radius = 4
        else:
            radius = 6
        cx, cy = x * SCALE + SCALE // 2, y * SCALE + SCALE // 2
        draw.ellipse(
            (cx - radius, cy - radius, cx + radius, cy + radius),
            fill=color, outline="#ffffff",
        )

    for placement in level.entities:
        enemy_id = placement.ref.split(":", 1)[1]
        enemy = enemies.get(enemy_id)
        color = _hex_to_rgb(
            (enemy.stats.get("placeholder_color") if enemy else None) or "#ff00ff"
        )
        x, y = placement.pos
        variant = variants.by_name.get(str(placement.overrides.get("variant", "")))
        # EFFECTIVE size (definition.size x variant.size) — the review
        # render must show the same body the validator footprinted and
        # the play surfaces collide: bottom-anchored on the anchor cell,
        # centered over the occupied columns. Hitbox-true (no overdraw).
        eff = effective_size(
            float(getattr(enemy, "size", 1.0) or 1.0) if enemy else 1.0,
            variant.size if variant else 1.0,
        )
        cols, _rows = occupancy(eff)
        side = eff * SCALE - 2
        cx = (x + cols / 2.0) * SCALE
        left, bottom = cx - side / 2.0, (y + 1) * SCALE - 1
        draw.rectangle(
            (left + 1, bottom - side + 1, left + side - 1, bottom - 1),
            fill=color,
        )
        if variant and "outline" in variant.visual:
            draw.rectangle(
                (left, bottom - side, left + side, bottom),
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


def render_legend(
    enemies: dict[str, EnemyDefinition],
    items: dict[str, Any] | None = None,
) -> bytes:
    from PIL import Image, ImageDraw

    row_h, swatch, pad, width = 28, 18, 8, 720
    rows = max(len(enemies), 1) + len(items or {}) + 1  # + footer note
    img = Image.new("RGB", (width, pad * 2 + row_h * rows), (24, 24, 32))
    draw = ImageDraw.Draw(img)
    y = pad
    for enemy in enemies.values():
        draw.rectangle(
            (pad, y, pad + swatch, y + swatch),
            fill=_hex_to_rgb(enemy.stats.get("placeholder_color", "#ff00ff")),
        )
        behavior = ", ".join(f"{k}={v}" for k, v in enemy.behavior.items())
        habitats = list(getattr(enemy, "habitats", None) or ["*"])
        habitat = "everywhere" if habitats == ["*"] else "/".join(habitats)
        draw.text(
            (pad * 2 + swatch, y + 3),
            f"{enemy.name}  [{enemy.archetype}]  "
            f"{getattr(enemy, 'rarity', 'common')} @ {habitat}  "
            f"size={getattr(enemy, 'size', 1.0):g} "
            f"hp={enemy.stats.get('hp')} dmg={enemy.stats.get('damage')} "
            f"spd={enemy.stats.get('speed')}  {behavior}",
            fill=(230, 230, 230),
        )
        y += row_h
    for item in (items or {}).values():
        color = _hex_to_rgb(item.stats.get("placeholder_color", "#ffd700"))
        cx, cy = pad + swatch // 2, y + swatch // 2
        draw.ellipse(
            (cx - swatch // 2, cy - swatch // 2,
             cx + swatch // 2, cy + swatch // 2),
            fill=color, outline="#ffffff",
        )
        params = ", ".join(f"{k}={v}" for k, v in sorted(item.params.items()))
        draw.text(
            (pad * 2 + swatch, y + 3),
            f"{item.name}  [item: {item.kind}]  {item.rarity}"
            + (f"  {params}" if params else ""),
            fill=(230, 230, 230),
        )
        y += row_h
    draw.text(
        (pad, y + 3),
        "white outline = variant placement (see manifest variants)   |   "
        "amber box = checkpoint   |   gold gem = secret reward   |   "
        "cyan triangle = secret-room entrance/portal (down=pipe, up=door)"
        "   |   circles = items (bronze tile = item box)   |   "
        "diamonds = foreground decor",
        fill=(170, 170, 180),
    )
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def render_level_skinned(
    terrain,
    background,
    level: Level,
    enemies: dict[str, EnemyDefinition],
    tileset: Tileset,
    sheet,
    backdrop_bands: list,
    enemy_sprites: dict[str, Any],
    player_sprite: Any,
    variants: VariantSet = DEFAULT_VARIANTS,
    graphics: GraphicsSpec = DEFAULT_GRAPHICS,
    prop_sprites: dict[str, Any] | None = None,
    items: dict[str, Any] | None = None,
    item_sprites: dict[str, Any] | None = None,
) -> bytes:
    """The 'what it looks like' render: real tile regions, sprites, and
    parallax scenery composited flat (camera-less). This is the skinned
    half of the block-vs-skinned pair the VLM fidelity loop will judge —
    on the placeholder sheet it degrades to flat squares, so the fake
    path exercises it byte-identically. Actors draw at their EFFECTIVE
    size with the same actor_scale overdraw the Godot consumer applies —
    a review image that shrinks a 2.0 body to one cell is lying."""
    from PIL import Image, ImageDraw

    height, width = terrain.shape
    px = tileset.slots[0].px_region[2] if tileset.slots else 32
    slot_colors, slot_categories = _slot_palette(tileset, sheet)
    bg_base = next(
        (
            slot_colors[index][:3]
            for index, category in slot_categories.items()
            if category == "empty"
        ),
        (24, 24, 32),
    )
    img = Image.new("RGB", (width * px, height * px))
    draw = ImageDraw.Draw(img)

    # Sky gradient by background band rows, then scenery bands over it.
    for y in range(height):
        color = _band_shade(bg_base, int(background[y][0] if background.ndim > 1 else 0))
        draw.rectangle((0, y * px, width * px, (y + 1) * px - 1), fill=color)
    for band in backdrop_bands:
        scale = (height * px) / band.height
        band_w = max(1, round(band.width * scale))
        scaled = band.convert("RGB").resize((band_w, height * px))
        for x in range(0, width * px, band_w):
            img.paste(scaled, (x, 0))

    # Tiles: real px_region art (placeholder = flat squares, same path).
    regions: dict[int, Any] = {}
    for slot in tileset.slots:
        x0, y0, w, h = slot.px_region
        regions[slot.index] = sheet.crop((x0, y0, x0 + w, y0 + h)).resize((px, px))
    # Volume (water) tiles paste TRANSLUCENT — their alpha scaled by
    # water_alpha so the backdrop already composited underneath shows
    # through. The tile ART stays opaque on disk; presentation only.
    for index, category in slot_categories.items():
        if category == "volume" and index in regions:
            rgba = regions[index].convert("RGBA")
            rgba.putalpha(
                rgba.getchannel("A").point(
                    lambda a: round(a * graphics.water_alpha)
                )
            )
            regions[index] = rgba
    for y in range(height):
        for x in range(width):
            slot = int(terrain[y, x])
            if slot_categories.get(slot) == "empty":
                continue
            tile = regions.get(slot)
            if tile is not None:
                img.paste(tile, (x * px, y * px), tile.convert("RGBA"))

    # Items layer (Arc 2) — gameplay, so ALWAYS visible here (unlike the
    # deliberately-omitted decor): box tiles paste their slot art (they
    # ARE collision once consumers overlay them); items draw as colored
    # circles until item sprites land (chunk 6).
    box_slot_index = next(
        (
            slot.index
            for slot in tileset.slots
            if bool((slot.params or {}).get("container"))
        ),
        None,
    )
    for placement in getattr(level, "items", []) or []:
        item_id = placement.ref.split(":", 1)[1]
        item = (items or {}).get(item_id)
        x, y = placement.pos
        boxed = str(placement.overrides.get("source", "")) == "box"
        if boxed:
            tile = regions.get(box_slot_index)
            if tile is not None:
                img.paste(tile, (x * px, y * px), tile.convert("RGBA"))
        sprite = (item_sprites or {}).get(item_id)
        if sprite is not None and not boxed:
            side = int(px * 0.7)
            scaled = sprite.resize((side, side))
            img.paste(
                scaled,
                (x * px + (px - side) // 2, y * px + (px - side) // 2),
                scaled,
            )
            continue
        radius = px // 6 if boxed else px // 4
        color = _hex_to_rgb(
            (item.stats.get("placeholder_color") if item else None)
            or "#ffd700"
        )
        cx, cy = x * px + px // 2, y * px + px // 2
        draw.ellipse(
            (cx - radius, cy - radius, cx + radius, cy + radius),
            fill=color, outline="#ffffff",
        )

    # Gameplay props before actors (behind them, like both play
    # surfaces): the exit goal on the exit cell and an UNCLAIMED
    # checkpoint flag per trigger — sprite when the art track produced
    # one, drawn shape otherwise. The block render keeps its analytic
    # markers; this surface shows what the player sees.
    props = prop_sprites or {}
    # The end-of-level goal has NO visual here anymore (postmortem ticket 3):
    # the win ZONE is the whole right edge / summit, so a single-cell doorway
    # was a false "touch here" affordance decoupled from the win condition.
    # FUTURE finish-line seam: a full-height marker spanning the exit COLUMN,
    # drawn once — a different asset, not this prop. level.exit and every win
    # path stay untouched.
    for trigger in level.triggers:
        if trigger.type != "checkpoint":
            continue
        foot = ((trigger.x + 0.5) * px, (trigger.y + 1) * px)
        sprite = props.get("checkpoint")
        if sprite is not None:
            from PIL import ImageChops

            side = round(px * 1.5)
            scaled = sprite.resize((side, side))
            greyed = ImageChops.multiply(
                scaled, Image.new("RGBA", scaled.size, (150, 150, 160, 255))
            )
            img.paste(greyed, (round(foot[0] - side / 2), round(foot[1] - side)), greyed)
        else:
            draw.line(
                (foot[0], foot[1], foot[0], foot[1] - px * 1.5),
                fill=(107, 87, 71), width=2,
            )
            draw.polygon(
                [
                    (foot[0], foot[1] - px * 1.5),
                    (foot[0] + px * 0.55, foot[1] - px * 1.28),
                    (foot[0], foot[1] - px * 1.06),
                ],
                fill=(140, 140, 153),
            )

    # Actors: sprites when the art track produced them, rects otherwise —
    # bottom-anchored at effective size; sprites get the actor_scale
    # overdraw (mirrors main.gd's _actor_visual + vis_off math).
    for placement in level.entities:
        enemy_id = placement.ref.split(":", 1)[1]
        enemy = enemies.get(enemy_id)
        x, y = placement.pos
        variant = variants.by_name.get(str(placement.overrides.get("variant", "")))
        eff = effective_size(
            float(getattr(enemy, "size", 1.0) or 1.0) if enemy else 1.0,
            variant.size if variant else 1.0,
        )
        cols, _rows = occupancy(eff)
        cx = (x + cols / 2.0) * px
        feet = (y + 1) * px - 2
        sprite = enemy_sprites.get(enemy_id)
        if sprite is not None:
            vis = max(1, round((px - 4) * eff * graphics.actor_scale))
            scaled = sprite.resize((vis, vis))
            img.paste(
                scaled, (round(cx - vis / 2.0), feet - vis), scaled
            )
        else:
            color = _hex_to_rgb(
                (enemy.stats.get("placeholder_color") if enemy else None)
                or "#ff00ff"
            )
            side = (px - 2) * eff
            draw.rectangle(
                (cx - side / 2.0, feet - side, cx + side / 2.0, feet),
                fill=color,
            )
    if level.spawn is not None:
        sx, sy = level.spawn
        if player_sprite is not None:
            vis = max(1, round((px - 8) * graphics.actor_scale))
            scaled = player_sprite.resize((vis, vis))
            img.paste(
                scaled,
                (round((sx + 0.5) * px - vis / 2.0), (sy + 1) * px - 4 - vis),
                scaled,
            )
        else:
            draw.rectangle(
                (sx * px + 2, sy * px + 2, (sx + 1) * px - 3, (sy + 1) * px - 3),
                fill=(240, 240, 240),
            )

    # Placeholder decor diamonds are OMITTED here — the skinned surface
    # shows only art that exists (they read as bugs next to real tiles);
    # the block render above still shows every decor record.

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def _load_sprite(ctx: Any, rel: str) -> Any:
    from PIL import Image

    path = ctx.adapter.resolve_path(rel)
    if not rel or not path.exists():
        return None
    return Image.open(path).convert("RGBA")


def render_level_review(
    ctx: Any,
    level: Level,
    variants: VariantSet = DEFAULT_VARIANTS,
    graphics: GraphicsSpec = DEFAULT_GRAPHICS,
) -> None:
    """One level's review PNGs (block + skinned) — shared by RenderPhase
    and the DAG nodes."""
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
        sheet, variants=variants, items=getattr(ctx.bible, "items", {}),
        graphics=graphics,
    )
    ctx.adapter.write_binary(
        f"review/{level.stage_id}/{level.level_id}.png", png
    )

    backdrop = ctx.bible.backdrops.get(level.stage_id)
    bands = []
    if backdrop is not None:
        for rel in backdrop.band_paths:
            band = _load_sprite(ctx, rel)
            if band is not None:
                bands.append(band)
    enemy_sprites = {
        eid: sprite
        for eid, enemy in ctx.bible.enemy_definitions.items()
        if (sprite := _load_sprite(ctx, enemy.sprite_path)) is not None
    }
    stage_props = getattr(ctx.bible, "props", {}).get(level.stage_id)
    prop_sprites = {
        name: sprite
        for name, rel in (stage_props.prop_paths if stage_props else {}).items()
        if (sprite := _load_sprite(ctx, rel)) is not None
    }
    item_sprites = {
        iid: sprite
        for iid, item in getattr(ctx.bible, "items", {}).items()
        if (sprite := _load_sprite(ctx, item.sprite_path)) is not None
    }
    skinned = render_level_skinned(
        terrain, background, level, ctx.bible.enemy_definitions, tileset,
        sheet, bands, enemy_sprites,
        _load_sprite(ctx, "sprite/player/base.png"), variants=variants,
        graphics=graphics, prop_sprites=prop_sprites,
        items=getattr(ctx.bible, "items", {}),
        item_sprites=item_sprites,
    )
    ctx.adapter.write_binary(
        f"review/{level.stage_id}/{level.level_id}_skinned.png", skinned
    )


def write_review_legend(ctx: Any) -> None:
    ctx.adapter.write_binary(
        "review/legend.png",
        render_legend(
            ctx.bible.enemy_definitions,
            items=getattr(ctx.bible, "items", {}),
        ),
    )


class RenderPhase:
    name = "plat:render"

    def __init__(
        self,
        variants: VariantSet = DEFAULT_VARIANTS,
        graphics: GraphicsSpec = DEFAULT_GRAPHICS,
    ) -> None:
        self.variants = variants
        self.graphics = graphics

    def run(self, ctx: Any) -> None:
        for level in ctx.bible.levels.values():
            render_level_review(
                ctx, level, variants=self.variants, graphics=self.graphics
            )
        write_review_legend(ctx)
        logger.info(
            "RenderPhase wrote %d level renders + legend to review/.",
            len(ctx.bible.levels),
        )
        _stamp_metadata(ctx, self.name)
