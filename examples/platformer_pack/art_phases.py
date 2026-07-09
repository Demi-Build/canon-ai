"""Late art phases — paid generation AT THE END of the loop (user rule).

Map building, validation, and debugging always run on placeholder slots;
these phases run after every level's manifest step has validated and
before the renders, so a real-backend run never spends art money on a
layout that might get rejected, and `canon regen phase:plat:<x>_art`
re-rolls art without touching one byte of gameplay.

All three follow the asset-artifact rules (content hashes + §6.1 edges,
deterministic fake path, loud per-asset fallback) and are no-ops without
an image producer — the placeholder look IS the fake/fallback path.

- ``plat:tileset_art`` — repaints the tilesheet in place (slots frozen).
- ``plat:sprite_art`` — one transparent sprite per enemy definition
  (``sprite/enemy/<id>/base.png`` — the /base leaf reserves room for
  future SKINS: variants, powerups, rigged animation frames) + the
  player (``sprite/player/base.png``, owned by the ``player``
  PlayerDefinition entity: hash-tracked, edit-detected, pinnable).
- ``plat:backdrop_art`` — parallax scenery bands per stage, their own
  ``Backdrop`` artifact so a hand-edited band never cascades staleness
  through the stage's levels.
"""

from __future__ import annotations

import io
import logging
from typing import Any

from canon.bible.artifacts import make_artifact_id
from canon.bible.platformer import Backdrop, PlayerDefinition, StageProps
from canon.pipeline.orchestrator import pinned_ids
from examples.platformer_pack.graphics import DEFAULT_GRAPHICS, GraphicsSpec
from examples.platformer_pack.phases import _stamp_metadata, stamp_provenance, warn
from examples.platformer_pack.style import background_role
from examples.platformer_pack.tiles import DEFAULT_TILES, TileRegistry
from examples.platformer_pack.tileset_art import (
    dominant_hue,
    hue_distance,
    tint_to_color,
)

logger = logging.getLogger(__name__)

#: Parallax scroll factors far → near (0 = pinned to camera).
BAND_DEPTHS = (0.2, 0.5, 0.8)

#: A sprite whose opaque area falls below this after background removal
#: is a failed generation (or the fake 1×1) — consumers keep their rects.
MIN_OPAQUE_RATIO = 0.02

#: Dominant hue may drift this far (degrees) from the enemy's assigned
#: color before we warn. Wide on purpose: sprites need internal detail.
HUE_TOLERANCE_DEG = 75.0

#: Closed gameplay-prop vocabulary: (name, descriptor, color hex). The
#: NAMES are code-interpreted — each has a draw + trigger point in BOTH
#: play surfaces (checkpoint flag flips state when claimed; the exit goal
#: marks the leave-right column), so a new prop only becomes real with
#: its consumer code. Colors match the review-render markers.
PROP_SPECS: tuple[tuple[str, str, str], ...] = (
    (
        "checkpoint",
        "a checkpoint marker: a small triangular pennant flag on a short "
        "pole planted in the ground",
        "#ffd24a",
    ),
    (
        "exit",
        "a level exit goal: an ornate free-standing doorway with a "
        "glowing arch, closed set piece",
        "#40ff70",
    ),
)


def _hue_of(hex_color: str) -> float:
    import colorsys

    raw = hex_color.lstrip("#")
    r, g, b = (int(raw[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return colorsys.rgb_to_hsv(r, g, b)[0] * 360.0


def _opaque_ratio(img: Any) -> float:
    alpha = img.convert("RGBA").getchannel("A")
    opaque = sum(1 for a in alpha.get_flattened_data() if a >= 128)
    return opaque / (img.size[0] * img.size[1])


class TilesetArtPhase:
    """Repaint the placeholder tilesheet with generated textures. Slot
    geometry, categories, and params are FROZEN — pixels only, so nothing
    gameplay-side can change under a repaint."""

    name = "plat:tileset_art"

    def __init__(
        self,
        tiles: TileRegistry = DEFAULT_TILES,
        producer: Any = None,
        graphics: GraphicsSpec = DEFAULT_GRAPHICS,
    ) -> None:
        self.tiles = tiles
        self.producer = producer
        self.graphics = graphics

    def owns(self, ctx: Any) -> list[str]:
        # Shared with plat:tileset: a stale tileset (style regen cascade)
        # re-runs the slots AND the repaint.
        return [
            make_artifact_id("tileset", sid)
            for sid in getattr(ctx.bible, "stages", {})
        ]

    def run(self, ctx: Any) -> None:
        from PIL import Image

        if self.producer is None:
            logger.info("TilesetArtPhase: no image producer — placeholder kept.")
            _stamp_metadata(ctx, self.name)
            return

        world_title = ctx.bible.world.title if ctx.bible.world else ""
        by_name = {t.name: t for t in self.tiles.tiles}
        pinned = pinned_ids(ctx.bible)
        for stage_id, tileset in ctx.bible.tilesets.items():
            # Status never gates a phase body — the pin guard must live
            # here too, or a re-run for another stage repaints pinned art.
            if (tileset.artifact_id or f"tileset:{stage_id}") in pinned:
                logger.info(
                    "TilesetArtPhase: tileset:%s is pinned — kept as-is.",
                    stage_id,
                )
                continue
            stage = ctx.bible.stages[stage_id]
            tile_px = self.graphics.tile_px
            sheet = Image.new("RGBA", (tile_px * len(tileset.slots), tile_px))
            for slot in tileset.slots:
                tile = by_name.get(slot.name)
                role_hex = tileset.palette.get(
                    tile.color_role if tile else "", "#ff00ff"
                )
                square = None
                if tile is not None:
                    try:
                        square = self.producer.tile_image(
                            tile, role_hex, stage.theme, world_title,
                            self.graphics,
                        )
                    except Exception as e:  # noqa: BLE001
                        warn(
                            ctx,
                            f"tileset art: generation failed for tile "
                            f"{slot.name!r} ({type(e).__name__}: {e}); "
                            f"placeholder square used.",
                        )
                if square is None:
                    raw = role_hex.lstrip("#")
                    color = tuple(
                        int(raw[i : i + 2], 16) for i in (0, 2, 4)
                    ) + (255,)
                    square = Image.new("RGBA", (tile_px, tile_px), color)
                x, y, _w, _h = slot.px_region
                sheet.paste(square, (x, y))

            buffer = io.BytesIO()
            sheet.save(buffer, format="PNG")
            tileset.tilesheet_hash = ctx.adapter.write_binary(
                tileset.tilesheet_path, buffer.getvalue()
            )
            manifest_hash = ctx.adapter.write_json_singleton(
                f"tileset/{stage_id}/manifest.json",
                tileset.model_dump(mode="json"),
            )
            stamp_provenance(
                ctx, tileset, manifest_hash,
                model_extra=(
                    f"gfx:{self.graphics.digest()}+img:{self.producer.model}"
                ),
            )
            logger.info(
                "TilesetArtPhase repainted %s via %s.",
                tileset.tilesheet_path, self.producer.model,
            )
        _stamp_metadata(ctx, self.name)


class SpriteArtPhase:
    """Generated sprites for every enemy definition + the player.
    Definitions keep their placeholder color and variant markers — the
    sprite is presentation; a failed/empty generation leaves the entity's
    sprite_path empty and consumers keep their rects (loud fallback).

    NOTE (user requirement, deliberately deferred): rigging/animation
    frames and reskins (variants, powerups) land beside ``base.png`` in
    each sprite directory — the addressing anticipates them."""

    name = "plat:sprite_art"

    def __init__(
        self,
        producer: Any = None,
        graphics: GraphicsSpec = DEFAULT_GRAPHICS,
    ) -> None:
        self.producer = producer
        self.graphics = graphics

    def owns(self, ctx: Any) -> list[str]:
        # An explicitly-regenerated enemy definition gets fresh art too;
        # "player" makes `canon regen player` reschedule this phase, and
        # "props:<sid>" does the same for the gameplay-prop sprites.
        return [
            *(
                e.artifact_id or f"enemy:{eid}"
                for eid, e in getattr(ctx.bible, "enemy_definitions", {}).items()
            ),
            "player",
            *(
                make_artifact_id("props", sid)
                for sid in getattr(ctx.bible, "stages", {})
            ),
        ]

    def run(self, ctx: Any) -> None:
        if self.producer is None:
            logger.info("SpriteArtPhase: no image producer — rects kept.")
            _stamp_metadata(ctx, self.name)
            return

        world_title = ctx.bible.world.title if ctx.bible.world else ""
        stage = next(iter(ctx.bible.stages.values()), None)
        theme = stage.theme if stage else ""
        size = self.graphics.sprite_size()
        pinned = pinned_ids(ctx.bible)

        for enemy_id, enemy in ctx.bible.enemy_definitions.items():
            # The phase re-rolls the WHOLE roster whenever it runs (no
            # per-asset staleness) — the pin guard is what makes a single
            # liked sprite survive a roster re-roll.
            if (enemy.artifact_id or f"enemy:{enemy_id}") in pinned:
                logger.info(
                    "SpriteArtPhase: enemy:%s is pinned — sprite kept.",
                    enemy_id,
                )
                continue
            color_hex = str(enemy.stats.get("placeholder_color", "#ff00ff"))
            descriptor = (
                f"a {enemy.archetype} enemy — "
                f"{enemy.stats.get('flavor', '')}".strip(" —")
            )
            sprite = self._generate(
                ctx, enemy.name or enemy_id, descriptor, color_hex,
                theme, world_title, (size, size),
            )
            if sprite is None:
                continue
            hue = dominant_hue(sprite)
            if hue is None:
                # COLORLESS sprite: it can't satisfy the hue reservations
                # that keep enemies readable — tint it the assigned hue
                # (brightness/shading kept). The pale hound slipped past
                # the hue check exactly because gray has no hue.
                warn(
                    ctx,
                    f"sprite art: {enemy_id!r} came back colorless; tinted "
                    f"toward its assigned color {color_hex} (shading kept).",
                )
                sprite = tint_to_color(sprite, color_hex)
            elif hue_distance(hue, _hue_of(color_hex)) > HUE_TOLERANCE_DEG:
                warn(
                    ctx,
                    f"sprite art: {enemy_id!r} dominant hue {hue:.0f}° is far "
                    f"from its assigned color {color_hex} — readability "
                    f"(hue reservations) may suffer; sprite kept.",
                )
            rel = f"sprite/enemy/{enemy_id}/base.png"
            enemy.sprite_path = rel
            enemy.sprite_hash = self._write(ctx, rel, sprite)
            ctx.adapter.write_json_singleton(
                f"enemy/{enemy_id}.json", enemy.model_dump(mode="json")
            )
            stamp_provenance(
                ctx, enemy, enemy.sprite_hash,
                model_extra=(
                    f"gfx:{self.graphics.digest()}+img:{self.producer.model}"
                ),
            )

        if "player" in pinned:
            logger.info("SpriteArtPhase: player is pinned — sprite kept.")
        else:
            player = self._generate(
                ctx, "the player", "the heroic player character", "#f0f0f0",
                theme, world_title, (size, size),
            )
            if player is not None:
                rel = "sprite/player/base.png"
                sprite_hash = self._write(ctx, rel, player)
                entity = ctx.bible.player
                if entity is None:
                    entity = PlayerDefinition(
                        artifact_id="player",
                        parents=[stage.artifact_id] if stage else [],
                    )
                    ctx.bible.player = entity
                entity.sprite_path = rel
                entity.sprite_hash = sprite_hash
                stamp_provenance(
                    ctx, entity, sprite_hash,
                    model_extra=(
                        f"gfx:{self.graphics.digest()}"
                        f"+img:{self.producer.model}"
                    ),
                )

        # Gameplay props (closed PROP_SPECS set), themed per stage. A
        # failed/empty generation leaves the entry absent — consumers
        # keep their drawn placeholder shapes (loud fallback).
        for stage_id, st in ctx.bible.stages.items():
            aid = make_artifact_id("props", stage_id)
            if aid in pinned:
                logger.info(
                    "SpriteArtPhase: props:%s is pinned — props kept.",
                    stage_id,
                )
                continue
            props = StageProps(
                artifact_id=aid,
                stage_id=stage_id,
                parents=[
                    make_artifact_id("stage", stage_id), "phase:plat:style",
                ],
            )
            for prop_name, descriptor, color_hex in PROP_SPECS:
                sprite = self._generate(
                    ctx, f"{prop_name} prop", descriptor, color_hex,
                    st.theme, world_title, (size, size),
                )
                if sprite is None:
                    continue
                rel = f"sprite/prop/{stage_id}/{prop_name}.png"
                props.prop_paths[prop_name] = rel
                props.prop_hashes[rel] = self._write(ctx, rel, sprite)
            if not props.prop_paths:
                continue  # nothing generated — no empty artifact
            manifest_hash = ctx.adapter.write_json_singleton(
                f"sprite/prop/{stage_id}/manifest.json",
                props.model_dump(mode="json"),
            )
            stamp_provenance(
                ctx, props, manifest_hash,
                model_extra=(
                    f"gfx:{self.graphics.digest()}+img:{self.producer.model}"
                ),
            )
            ctx.bible.props[stage_id] = props
            logger.info(
                "SpriteArtPhase wrote %d prop sprite(s) for stage %s.",
                len(props.prop_paths), stage_id,
            )
        _stamp_metadata(ctx, self.name)

    def _generate(
        self, ctx: Any, name: str, descriptor: str, color_hex: str,
        theme: str, world_title: str, size: tuple[int, int],
    ) -> Any:
        """One sprite, or None after a loud fallback."""
        try:
            sprite = self.producer.sprite_image(
                name, descriptor, color_hex, theme, world_title,
                self.graphics, size,
            )
        except Exception as e:  # noqa: BLE001
            warn(
                ctx,
                f"sprite art: generation failed for {name!r} "
                f"({type(e).__name__}: {e}); placeholder rect kept.",
            )
            return None
        if _opaque_ratio(sprite) < MIN_OPAQUE_RATIO:
            warn(
                ctx,
                f"sprite art: {name!r} came back (near-)empty after "
                f"background removal; placeholder rect kept.",
            )
            return None
        return sprite

    @staticmethod
    def _write(ctx: Any, rel: str, sprite: Any) -> str:
        buffer = io.BytesIO()
        sprite.save(buffer, format="PNG")
        return ctx.adapter.write_binary(rel, buffer.getvalue())


class BackdropArtPhase:
    """Parallax scenery bands per stage (far → near), atmosphere-blended
    toward the palette background so the playfield stays legible. The
    existing gradient sky remains underneath — and remains the entire
    fake/fallback look."""

    name = "plat:backdrop_art"

    def __init__(
        self,
        tiles: TileRegistry = DEFAULT_TILES,
        producer: Any = None,
        graphics: GraphicsSpec = DEFAULT_GRAPHICS,
    ) -> None:
        self.tiles = tiles
        self.producer = producer
        self.graphics = graphics

    def owns(self, ctx: Any) -> list[str]:
        return [
            make_artifact_id("backdrop", sid)
            for sid in getattr(ctx.bible, "stages", {})
        ]

    def run(self, ctx: Any) -> None:
        if self.producer is None or self.graphics.backdrop_bands == 0:
            logger.info("BackdropArtPhase: no producer/bands — gradient sky kept.")
            _stamp_metadata(ctx, self.name)
            return

        world_title = ctx.bible.world.title if ctx.bible.world else ""
        bg_role = background_role(self.tiles)
        pinned = pinned_ids(ctx.bible)
        for stage_id, stage in ctx.bible.stages.items():
            if make_artifact_id("backdrop", stage_id) in pinned:
                logger.info(
                    "BackdropArtPhase: backdrop:%s is pinned — bands kept.",
                    stage_id,
                )
                continue
            tileset = ctx.bible.tilesets.get(stage_id)
            palette = tileset.palette if tileset else {}
            bg_hex = palette.get(bg_role, "#181820")
            backdrop = Backdrop(
                artifact_id=make_artifact_id("backdrop", stage_id),
                stage_id=stage_id,
                depths=list(BAND_DEPTHS[: self.graphics.backdrop_bands]),
                parents=[
                    make_artifact_id("stage", stage_id), "phase:plat:style",
                ],
            )
            for band in range(self.graphics.backdrop_bands):
                try:
                    img = self.producer.backdrop_image(
                        band, stage.theme, world_title, palette, bg_hex,
                        self.graphics,
                    )
                except Exception as e:  # noqa: BLE001
                    warn(
                        ctx,
                        f"backdrop art: band {band} failed for stage "
                        f"{stage_id!r} ({type(e).__name__}: {e}); gradient "
                        f"sky covers it.",
                    )
                    continue
                rel = f"backdrop/{stage_id}/band_{band}.png"
                buffer = io.BytesIO()
                img.save(buffer, format="PNG")
                backdrop.band_paths.append(rel)
                backdrop.band_hashes[rel] = ctx.adapter.write_binary(
                    rel, buffer.getvalue()
                )
            manifest_hash = ctx.adapter.write_json_singleton(
                f"backdrop/{stage_id}/manifest.json",
                backdrop.model_dump(mode="json"),
            )
            stamp_provenance(
                ctx, backdrop, manifest_hash,
                model_extra=(
                    f"gfx:{self.graphics.digest()}+img:{self.producer.model}"
                ),
            )
            ctx.bible.backdrops[stage_id] = backdrop
            logger.info(
                "BackdropArtPhase wrote %d band(s) for stage %s.",
                len(backdrop.band_paths), stage_id,
            )
        _stamp_metadata(ctx, self.name)
