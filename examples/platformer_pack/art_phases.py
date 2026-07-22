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

from canon.backends.base import ImageEditBackend
from canon.bible.artifacts import make_artifact_id
from canon.bible.platformer import Backdrop, PlayerDefinition, StageProps
from canon.pipeline.orchestrator import pinned_ids
from examples.platformer_pack.graphics import DEFAULT_GRAPHICS, GraphicsSpec
from examples.platformer_pack.phases import _stamp_metadata, stamp_provenance, warn
from examples.platformer_pack.style import background_role
from examples.platformer_pack.tiles import DEFAULT_TILES, TileRegistry
from examples.platformer_pack.tileset_art import (
    apply_watermark,
    art_provenance,
    build_sheet_reference,
    dominant_hue,
    frames_to_strip,
    hue_distance,
    normalize_frames,
    pack_atlas,
    png_bytes,
    segment_frames,
    tint_to_color,
)

logger = logging.getLogger(__name__)

#: Parallax scroll factors far → near (0 = pinned to camera).
BAND_DEPTHS = (0.2, 0.5, 0.8)

#: The foreground occlusion band's scroll factor — depth > 1 is the
#: consumer contract for "draw IN FRONT of gameplay" (an extra
#: near-field occluder band; behind the UI/hud).
FOREGROUND_DEPTH = 1.15

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
    # No "exit" prop (postmortem ticket 3): the win zone is the whole right
    # edge / summit, so a single-cell doorway sprite was dead art. A future
    # finish-line (full-height marker over the exit column) is a different
    # asset, reserved here — not this prop.
    (
        "dust",
        "a small puff of dust kicked up from the ground, a few soft "
        "rounded clouds, mostly empty space",
        "#c9b9a2",
    ),
    (
        "splash",
        "a small water splash burst: white foam droplets arcing up, "
        "mostly empty space",
        "#9fd8ff",
    ),
    (
        "sparkle",
        "a bright four-pointed pickup sparkle glint, mostly empty space",
        "#fff2a8",
    ),
)

#: One-shot VFX props (Godot-only pool: dust on landing, splash on
#: volume entry, sparkle on collect). Wispy by design — QA's
#: sprite_bbox check relaxes its min-fill for these names.
VFX_PROP_NAMES = frozenset({"dust", "splash", "sparkle"})


def _stamped_png(
    ctx: Any, producer: Any, graphics: GraphicsSpec, img: Any, asset: str,
    *, watermark: bool = True,
) -> bytes:
    """Encode one producer-GENERATED image: the readable provenance
    stamp (png_bytes + art_provenance) on every write, plus the visible
    corner watermark when the spec asks. Tiles pass ``watermark=False``
    — their region means are palette-checked, so the mark would fight
    conformance and QA."""
    if watermark and graphics.visible_watermark:
        img = apply_watermark(img)
    return png_bytes(
        img,
        art_provenance(
            asset=asset,
            producer=producer,
            graphics=graphics,
            pipeline_seed=str(getattr(ctx.config, "seed", "")),
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
            drift_log = getattr(self.producer, "last_palette_drift", None)
            if drift_log is not None:
                drift_log.clear()  # per-stage snapshot (ticket 6)
            sheet = Image.new("RGBA", (tile_px * len(tileset.slots), tile_px))
            # One generation per tile NAME: the 16 autotile floor variant
            # slots share the floor art (paid runs stay at 10 unique
            # generations); a failed name isn't retried per slot.
            generated: dict[str, Any] = {}
            for slot in tileset.slots:
                tile = by_name.get(slot.name)
                role_hex = tileset.palette.get(
                    tile.color_role if tile else "", "#ff00ff"
                )
                if slot.name in generated:
                    square = generated[slot.name]
                else:
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
                    generated[slot.name] = square
                if square is None:
                    raw = role_hex.lstrip("#")
                    color = tuple(
                        int(raw[i : i + 2], 16) for i in (0, 2, 4)
                    ) + (255,)
                    square = Image.new("RGBA", (tile_px, tile_px), color)
                if slot.params.get("water_deep"):
                    # Interior water: the shared generated square minus
                    # its surface lip (code-derived, mean-preserved).
                    from examples.platformer_pack.tileset import (
                        derive_water_deep,
                    )

                    square = derive_water_deep(square.copy())
                mask = slot.params.get("autotile_mask")
                if mask is not None:
                    # Mean-preserving edge shading distinguishes the
                    # variants without moving any region average — the
                    # base art is shared per name, so shade a copy.
                    from examples.platformer_pack.tileset import (
                        shade_floor_variant,
                    )

                    square = shade_floor_variant(square.copy(), int(mask))
                x, y, _w, _h = slot.px_region
                sheet.paste(square, (x, y))

            if drift_log:
                # Ticket 6 data path (producer → phase → QA): the pre-conform
                # drift snapshot, read back by run_code_checks' palette_drift
                # meter. Real backends only — the fake records nothing, so
                # fake trees gain no file.
                ctx.adapter.write_json_singleton(
                    f"review/{stage_id}/palette_drift.json",
                    {k: drift_log[k] for k in sorted(drift_log)},
                )
            tileset.tilesheet_hash = ctx.adapter.write_binary(
                tileset.tilesheet_path,
                _stamped_png(
                    ctx, self.producer, self.graphics, sheet,
                    f"tilesheet:{stage_id}", watermark=False,
                ),
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


#: Behavior must READ in the still sprite — a stationary sentry looked
#: broken in the first paid run. Each LOCOMOTION archetype maps to a visual
#: stance the diffusion model can draw, so a sentry looks planted and a
#: swimmer aquatic. Aggro is orthogonal (AGGRO_LOOK below), folded in on top.
ARCHETYPE_LOOK = {
    "patroller": "ambling mid-stride, built to walk a beat back and forth",
    "sentry": (
        "rooted and planted in place — a squat, immobile, turret-like "
        "guardian with no legs for walking, clearly a creature that holds "
        "its ground rather than roams"
    ),
    "swimmer": "sleek and aquatic, finned or gilled for gliding through water",
    "flyer": (
        "airborne and hovering, built to drift and swoop through open air — "
        "wings optional (a weightless, floating body reads as a flyer too)"
    ),
    "hopper": (
        "coiled and spring-legged, crouched low on powerful haunches — a "
        "bouncy creature clearly built to launch itself in arcing hops"
    ),
}

#: Temperament, ORTHOGONAL to locomotion (schema `aggro` tier): an
#: aggressive enemy — a patroller, swimmer, or flyer that hunts the player —
#: reads as a predator regardless of how it moves. Passive enemies get no
#: extra trait (their locomotion stance is the whole story).
AGGRO_LOOK = "alert and predatory, poised to lunge, with a sharp hunting glare"

#: The player is a platformer MASCOT, not the diffusion default fantasy
#: knight (first paid run: 'we keep getting knights with swords'). Kept
#: theme-light so one sprite reads across every biome, and explicitly
#: weaponless.
PLAYER_DESCRIPTOR = (
    "a friendly platformer mascot hero: a small, round-bodied cartoon "
    "adventurer with a big expressive head, large readable eyes, and simple "
    "stubby limbs in a bouncy, ready pose — a bold, clear silhouette that "
    "reads at a small size. NOT a knight, no armor, no helmet, and no sword "
    "or any weapon"
)


def _enemy_art_descriptor(
    archetype: str, flavor: str, aggressive: bool = False
) -> str:
    """Fold the enemy's LOCOMOTION stance and its (orthogonal) AGGRO
    temperament into the sprite descriptor so the generated art matches the
    behavior — a planted sentry, a sleek swimmer, and a predatory glare on
    anything that hunts the player, whatever its locomotion."""
    traits = [
        t for t in (ARCHETYPE_LOOK.get(archetype), AGGRO_LOOK if aggressive else "")
        if t
    ]
    base = f"a {archetype} enemy" + (
        f" that is {', '.join(traits)}" if traits else ""
    )
    return f"{base} — {flavor}".strip(" —")


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
        # An explicitly-regenerated enemy/item definition gets fresh art
        # too; "player" makes `canon regen player` reschedule this phase,
        # and "props:<sid>" does the same for the gameplay-prop sprites.
        return [
            *(
                e.artifact_id or f"enemy:{eid}"
                for eid, e in getattr(ctx.bible, "enemy_definitions", {}).items()
            ),
            *(
                i.artifact_id or f"item:{iid}"
                for iid, i in getattr(ctx.bible, "items", {}).items()
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
            descriptor = _enemy_art_descriptor(
                enemy.archetype,
                str(enemy.stats.get("flavor", "")),
                aggressive=float(enemy.behavior.get("aggro_range", 0) or 0) > 0,
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
            enemy.sprite_hash = self._write(
                ctx, rel, sprite, f"sprite:enemy/{enemy_id}"
            )
            ctx.adapter.write_json_singleton(
                f"enemy/{enemy_id}.json", enemy.model_dump(mode="json")
            )
            # label keeps the TEXT model truthful (haiku authored the
            # enemy; this re-stamp only adds the art generators) — an
            # unlabeled stamp would revert it to the global model.
            stamp_provenance(
                ctx, enemy, enemy.sprite_hash,
                label="plat:enemies",
                model_extra=(
                    f"gfx:{self.graphics.digest()}+img:{self.producer.model}"
                ),
            )

        _ITEM_LOOK = {
            "coin": "a small round collectible coin, glinting",
            "heal": "a heart-shaped restorative charm",
            "shield": "a protective ward amulet",
            "double_jump": "a feather-light charm suggesting flight",
            "run_boost": "a swift wind-swirl draught",
        }
        for item_id, item in getattr(ctx.bible, "items", {}).items():
            # Same per-definition pin guard as the roster: one liked item
            # sprite survives a pool re-roll.
            if (item.artifact_id or f"item:{item_id}") in pinned:
                logger.info(
                    "SpriteArtPhase: item:%s is pinned — sprite kept.",
                    item_id,
                )
                continue
            color_hex = str(item.stats.get("placeholder_color", "#ffd700"))
            descriptor = (
                f"{_ITEM_LOOK.get(item.kind, 'a small collectible trinket')}"
                f" — {item.stats.get('flavor', '')}"
            ).strip(" —")
            sprite = self._generate(
                ctx, item.name or item_id, descriptor, color_hex,
                theme, world_title, (size, size),
            )
            if sprite is None:
                continue
            if dominant_hue(sprite) is None:
                warn(
                    ctx,
                    f"sprite art: item {item_id!r} came back colorless; "
                    f"tinted toward its assigned color {color_hex}.",
                )
                sprite = tint_to_color(sprite, color_hex)
            rel = f"sprite/item/{item_id}/base.png"
            item.sprite_path = rel
            item.sprite_hash = self._write(
                ctx, rel, sprite, f"sprite:item/{item_id}"
            )
            ctx.adapter.write_json_singleton(
                f"item/{item_id}.json", item.model_dump(mode="json")
            )
            stamp_provenance(
                ctx, item, item.sprite_hash,
                label="plat:items",
                model_extra=(
                    f"gfx:{self.graphics.digest()}+img:{self.producer.model}"
                ),
            )

        if "player" in pinned:
            logger.info("SpriteArtPhase: player is pinned — sprite kept.")
        else:
            player = self._generate(
                ctx, "the player", PLAYER_DESCRIPTOR, "#f0f0f0",
                theme, world_title, (size, size),
            )
            if player is not None:
                rel = "sprite/player/base.png"
                sprite_hash = self._write(ctx, rel, player, "sprite:player")
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
                props.prop_hashes[rel] = self._write(
                    ctx, rel, sprite, f"sprite:prop/{stage_id}/{prop_name}"
                )
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

    def _write(self, ctx: Any, rel: str, sprite: Any, asset: str) -> str:
        """Encode + stamp: every generated sprite carries the readable
        provenance chunks (and the visible watermark when the spec
        asks) — ``asset`` is its ``canon:asset`` address."""
        return ctx.adapter.write_binary(
            rel, _stamped_png(ctx, self.producer, self.graphics, sprite, asset)
        )


#: Minimum frames a segmented sheet must yield to count as an animation —
#: below this it is not a cycle, so the state keeps the static base.png.
ANIM_MIN_FRAMES = 2

#: Cap the img2img reference strip width so a high-frame sheet (the player's
#: ~9) stays a sensible aspect for the edit model — nano-banana/edit echoes the
#: input dims, so an extreme 9:1 canvas would come back distorted. The per-cell
#: size shrinks as the frame count grows to keep the total near this.
MAX_SHEET_REF_WIDTH = 2560


#: Character-drift ceiling (postmortem ticket 7): euclidean distance between
#: the base sprite's opaque mean RGB and a state's frames. On the paid
#: fixtures on-model states scored <= 5 and drifted states ~31, so ~18 is a
#: wide-margin cut. Warn-only — a state over it still ships, flagged.
SPRITE_DRIFT_MAX = 18.0
_DRIFT_NOTE = (
    " CRITICAL: keep the EXACT palette and colours of the attached reference "
    "character — same hues, same shading; do not recolour it."
)


def _opaque_mean_rgb(images: list) -> tuple:
    """Mean RGB over the OPAQUE pixels (alpha > 0) of one or more images — the
    palette signature used for character-drift comparison."""
    tot = [0.0, 0.0, 0.0]
    count = 0
    for img in images:
        rgba = img.convert("RGBA")
        pixels = list(rgba.convert("RGB").get_flattened_data())
        alphas = list(rgba.getchannel("A").get_flattened_data())
        for i, a in enumerate(alphas):
            if a > 0:
                p = pixels[i]
                tot[0] += p[0]
                tot[1] += p[1]
                tot[2] += p[2]
                count += 1
    if not count:
        return (0.0, 0.0, 0.0)
    return (tot[0] / count, tot[1] / count, tot[2] / count)


def _sprite_drift(base: Any, frames: list) -> float:
    """Euclidean distance between the base sprite's opaque mean RGB and the
    frames' — separates a DIFFERENT character (a palette shift) from the same
    character in a new pose (a pose leaves the mean ~unchanged)."""
    from math import dist

    return dist(_opaque_mean_rgb([base]), _opaque_mean_rgb(frames))


def _animation_sheet_prompt(
    state: str, motion: str, n_frames: int, facing: str = "right"
) -> str:
    """The img2img sheet prompt (proven template from the B0 spike): seed
    frame 1 with the real sprite, fill the rest with the state's cycle.
    ``facing`` flips the declared side view for an asymmetric second pass."""
    return (
        f"This image is the FIRST frame (leftmost cell) of a horizontal sprite "
        f"sheet made of {n_frames} equal-width cells. Keep that leftmost frame "
        f"as-is and fill the cells to its right with the {state} animation of "
        f"the SAME character: {motion}. The pose must PROGRESS across the cells "
        f"so the strip reads clearly as {state} — vary the body posture and "
        f"SILHOUETTE frame to frame, not just small details. Every frame: "
        f"identical character design, identical proportions and colors, side "
        f"view facing {facing}, exactly one character centered per cell, evenly "
        f"spaced left-to-right, on a plain solid white background. No text, no "
        f"numbers, no borders, no gridlines."
    )


class SpriteAnimationPhase:
    """VLM-authored, sheet-based per-state animation for every enemy that has
    a generated sprite. Runs AFTER SpriteArtPhase (it reads base.png).

    Per enemy: a vision judge authors a per-state motion spec from the ACTUAL
    sprite (``author_animation_spec``, B2), then ONE img2img SHEET per state is
    generated (``ImageEditBackend.edit``), segmented by CONTENT — the edit model
    ignores exact frame counts, so a fixed grid slicer would straddle frames —
    normalized to uniform square frames, and written as a per-state frame strip
    (``sprite/<kind>/<id>/<state>.png``) plus ``frames.json`` and a packed
    trimmed atlas (``atlas.png`` + ``atlas.json``) beside base.png. A hopper
    adds a ``jump`` state; an ``asymmetric`` actor gets real left-facing
    strips. The manifest is folded onto ``enemy.stats['animation']``.

    LOUD FALLBACK at every step: no img2img-capable backend, no judge, a pinned
    or sprite-less enemy, a failed edit, or a sheet that segments to <2 frames →
    that enemy/state keeps the static base.png (consumers already fall back to
    it). Ownership mirrors SpriteArtPhase (the same enemy ids) so a re-rolled
    sprite re-rolls its animation; the animation is DERIVED from the sprite, so
    it rides the enemy's provenance + pin — they cascade and pin together
    (individual frame files are not independently edit-tracked in v1)."""

    name = "plat:sprite_animation"

    def __init__(
        self,
        producer: Any = None,
        judge: Any = None,
        graphics: GraphicsSpec = DEFAULT_GRAPHICS,
    ) -> None:
        self.producer = producer
        self.judge = judge
        self.graphics = graphics

    def owns(self, ctx: Any) -> list[str]:
        # Same ids as SpriteArtPhase — a re-rolled sprite re-rolls its
        # animation. (Prop animation is the remaining "widen" step.)
        return [
            *(
                e.artifact_id or f"enemy:{eid}"
                for eid, e in getattr(ctx.bible, "enemy_definitions", {}).items()
            ),
            "player",
        ]

    def run(self, ctx: Any) -> None:
        from examples.platformer_pack.vlm_qa import (
            ANIM_FRAMES_MAX,
            PLAYER_ANIM_FRAMES_MAX,
            PLAYER_ANIMATION_STATES,
            enemy_animation_states,
            enemy_animation_subject,
        )

        # The ANIMATION img2img backend may differ from the sprite backend
        # (ticket 7): PixelLab can generate the character sheets while nano
        # (fal) animates them. edit_backend defaults to the sprite backend.
        backend = getattr(self.producer, "edit_backend", None)
        if not isinstance(backend, ImageEditBackend):
            logger.info(
                "SpriteAnimationPhase: no img2img (edit) backend available "
                "— static sprites kept."
            )
            _stamp_metadata(ctx, self.name)
            return
        if self.judge is None:
            logger.info(
                "SpriteAnimationPhase: no VLM judge (explicit --vlm-backend "
                "required to author motion) — static sprites kept."
            )
            _stamp_metadata(ctx, self.name)
            return

        pinned = pinned_ids(ctx.bible)
        for enemy_id, enemy in ctx.bible.enemy_definitions.items():
            if (enemy.artifact_id or f"enemy:{enemy_id}") in pinned:
                logger.info(
                    "SpriteAnimationPhase: enemy:%s is pinned — animation kept.",
                    enemy_id,
                )
                continue
            manifest = self._animate_one(
                ctx, f"enemy:{enemy_id}", enemy.sprite_path,
                enemy_animation_subject(enemy), enemy_animation_states(enemy),
                ANIM_FRAMES_MAX, asymmetric=enemy.asymmetric,
            )
            if manifest:
                enemy.stats["animation"] = manifest
                ctx.adapter.write_json_singleton(
                    f"enemy/{enemy_id}.json", enemy.model_dump(mode="json")
                )

        # The player (the mascot): a smoother cycle (higher frame ceiling) and
        # its own state set — it jumps and never plays a death cycle.
        player = getattr(ctx.bible, "player", None)
        if player is not None and "player" not in pinned:
            subject = (
                f"Character: the PLAYER hero — {PLAYER_DESCRIPTOR}. A small "
                f"bouncy platformer mascot, side view facing right."
            )
            manifest = self._animate_one(
                ctx, "player", player.sprite_path, subject,
                PLAYER_ANIMATION_STATES, PLAYER_ANIM_FRAMES_MAX,
                asymmetric=player.asymmetric,
            )
            if manifest:
                player.animation = manifest
        _stamp_metadata(ctx, self.name)

    def _animate_one(
        self,
        ctx: Any,
        actor_id: str,
        sprite_path: str,
        subject: str,
        states: tuple[str, ...],
        frames_max: int,
        asymmetric: bool = False,
    ) -> dict:
        """Author (B2) + generate + segment one actor's animation. Returns
        ``{"spec": ..., "states": ..., "atlas": ...}`` or ``{}`` — the loud
        fallback: no sprite, a spec that never validated, or nothing generated
        all leave the static base.png in place."""
        from examples.platformer_pack.vlm_qa import author_animation_spec

        if not sprite_path:
            logger.info(
                "SpriteAnimationPhase: %s has no sprite — static fallback.",
                actor_id,
            )
            return {}
        base_file = ctx.adapter.resolve_path(sprite_path)
        if not base_file.exists():
            return {}
        spec = author_animation_spec(
            self.judge, actor_id, subject, base_file.read_bytes(),
            states=states, frames_max=frames_max,
        )
        if spec is None:
            warn(
                ctx,
                f"animation: {actor_id} motion spec never validated; static "
                f"sprite kept.",
            )
            return {}
        result = self._animate_actor(ctx, sprite_path, spec, actor_id, asymmetric)
        if not result.get("states"):
            return {}
        logger.info(
            "SpriteAnimationPhase animated %s (%d state(s)) via %s.",
            actor_id, len(result["states"]), self.producer.model,
        )
        return {"spec": spec, **result}

    def _sheet_frames(
        self,
        ctx: Any,
        base: Any,
        actor_id: str,
        state: str,
        motion: str,
        n_frames: int,
        cell_px: int,
        facing: str = "right",
        kept: str = "state kept static",
    ) -> tuple[list, float] | None:
        """One img2img sheet → ``(frames, wander_frac)`` for one state and
        facing: content-segmented, normalized frames plus the PRE-normalize
        baseline wander (max-min of the raw crops' content bottoms, as a
        fraction of the sheet height — measured UPSTREAM of the normalize
        re-anchor, the honest registration signal; postmortem ticket 6). None
        (warned, ``kept`` naming the fallback) on a failed edit or a sheet
        that segments below ANIM_MIN_FRAMES."""
        from PIL import Image

        label = "sheet" if facing == "right" else f"{facing} sheet"
        prompt = _animation_sheet_prompt(state, motion, n_frames, facing=facing)
        ref = build_sheet_reference(base, n_frames, cell_px)
        ref_buf = io.BytesIO()
        ref.save(ref_buf, format="PNG")
        ref_bytes = ref_buf.getvalue()
        # Attach the CLEAN character sprite as an identity anchor so the edit
        # model keeps the SAME character across states (ticket 7): the sprite
        # backend (e.g. PixelLab) made base.png, and nano edits it WITH base.png
        # re-attached — "the animator gets the character sheet". A stock motion
        # sheet would prepend to this list once cradle supplies one.
        base_buf = io.BytesIO()
        base.save(base_buf, format="PNG")
        refs = [base_buf.getvalue()]
        edit_backend = self.producer.edit_backend

        def _edit(note: str = "") -> Any:
            out = edit_backend.edit(
                ref_bytes, prompt + note, ref.width, ref.height, references=refs
            )
            return Image.open(io.BytesIO(out)).convert("RGBA")

        try:
            sheet = _edit()
        except Exception as e:  # noqa: BLE001
            warn(
                ctx,
                f"animation: {actor_id!r} {state!r} {label} failed "
                f"({type(e).__name__}: {e}); {kept}.",
            )
            return None
        raw = segment_frames(sheet)
        frames = normalize_frames(raw)
        if len(frames) < ANIM_MIN_FRAMES:
            warn(
                ctx,
                f"animation: {actor_id!r} {state!r} {label} segmented to "
                f"{len(frames)} frame(s) (<{ANIM_MIN_FRAMES}); {kept}.",
            )
            return None
        # Pre-normalize baseline wander (ticket 6): the raw crops share the
        # sheet's vertical frame, so their content-bottom spread IS the
        # backend's registration drift — normalize_frames re-anchors it away,
        # which is exactly why the old post-normalize check couldn't fail.
        bottoms = [
            b[3] for c in raw if (b := c.getchannel("A").getbbox())
        ]
        wander_frac = (
            round((max(bottoms) - min(bottoms)) / max(1, sheet.height), 4)
            if len(bottoms) >= 2
            else 0.0
        )
        # Character-drift check (ticket 7) — skipped on the trusted fake (its
        # canned sheet is not a real sprite). Over threshold: retry ONCE with
        # the base re-attached + a palette note, then SHIP either way with a
        # durable warning (never blocks, never regenerates).
        if not getattr(edit_backend, "trusted_alpha", False):
            drift = _sprite_drift(base, frames)
            if drift > SPRITE_DRIFT_MAX:
                try:
                    retry_sheet = _edit(_DRIFT_NOTE)
                    retry_raw = segment_frames(retry_sheet)
                    retry = normalize_frames(retry_raw)
                    rdrift = (
                        _sprite_drift(base, retry)
                        if len(retry) >= ANIM_MIN_FRAMES
                        else float("inf")
                    )
                    if rdrift < drift:
                        frames, drift = retry, rdrift
                        rb = [
                            b[3]
                            for c in retry_raw
                            if (b := c.getchannel("A").getbbox())
                        ]
                        wander_frac = (
                            round(
                                (max(rb) - min(rb))
                                / max(1, retry_sheet.height),
                                4,
                            )
                            if len(rb) >= 2
                            else 0.0
                        )
                except Exception:  # noqa: BLE001
                    pass
                if drift > SPRITE_DRIFT_MAX:
                    warn(
                        ctx,
                        f"animation: {actor_id!r} {state!r} {label} drifted "
                        f"from the base sprite (dRGB {drift:.0f} > "
                        f"{SPRITE_DRIFT_MAX:.0f}); shipped — flag for manual "
                        "review.",
                    )
        return frames, wander_frac

    def _animate_actor(
        self,
        ctx: Any,
        base_rel: str,
        spec: dict,
        actor_id: str,
        asymmetric: bool = False,
    ) -> dict:
        """Generate + segment + write one frame strip per state (an
        asymmetric actor gets a REAL left-facing strip too, from the mirrored
        base), then pack every frame into one trimmed atlas (atlas.png +
        atlas.json beside frames.json — the self-contained shipping manifest).
        Writes ``frames.json`` beside base.png and returns ``{"states":
        {state: {path, hash, frames, frame_width, frame_height, duration_ms,
        loop, durations_ms[, path_left, hash_left, frames_left]}}, "atlas":
        {path, hash}}`` (``{}`` when nothing generated). States that fail any
        step are simply absent (static fallback); a failed left pass falls
        back to the consumers' horizontal flip."""
        from PIL import Image

        from examples.platformer_pack.vlm_qa import STATE_LOOP_MODES

        base = Image.open(ctx.adapter.resolve_path(base_rel)).convert("RGBA")
        sprite_dir = base_rel.rsplit("/", 1)[0]  # sprite/enemy/<id>
        actor_slug = actor_id.replace(":", "/")  # canon:asset address leaf
        size = self.graphics.sprite_size()
        frame_ms = self.graphics.anim_frame_ms
        resample = (
            Image.NEAREST if self.graphics.render_filter == "crisp" else Image.LANCZOS
        )
        states: dict[str, dict] = {}
        frame_lists: dict[str, list] = {}  # resized PIL frames, for the atlas
        for state, state_spec in spec.items():
            n_frames = int(state_spec.get("frames", ANIM_MIN_FRAMES))
            # Per-cell size shrinks as the frame count grows so a many-frame
            # sheet (the player's ~9) stays a sane aspect (see MAX_SHEET_REF_WIDTH).
            cell_px = min(
                self.graphics.gen_px, max(96, MAX_SHEET_REF_WIDTH // max(1, n_frames))
            )
            motion = str(state_spec.get("motion", ""))
            got = self._sheet_frames(
                ctx, base, actor_id, state, motion, n_frames, cell_px
            )
            if got is None:
                continue
            frames, wander_frac = got
            frames = [f.resize((size, size), resample) for f in frames]
            strip = frames_to_strip(frames)
            rel = f"{sprite_dir}/{state}.png"
            states[state] = {
                "path": rel,
                "hash": ctx.adapter.write_binary(
                    rel,
                    _stamped_png(
                        ctx, self.producer, self.graphics, strip,
                        f"strip:{actor_slug}/{state}",
                    ),
                ),
                "frames": len(frames),
                "frame_width": size,
                "frame_height": size,
                "duration_ms": frame_ms,
                # Playback keys (G4): uniform per-frame durations v1 — the
                # per-frame list is the user's hand-edit lever — plus the
                # state's loop mode.
                "loop": STATE_LOOP_MODES.get(state, "loop"),
                "durations_ms": [frame_ms] * len(frames),
                # Pre-normalize baseline wander (ticket 6): the upstream
                # registration signal, carried to QA's animation_wander meter.
                "raw_bottom_wander_frac": wander_frac,
            }
            frame_lists[state] = frames
            if not asymmetric:
                continue
            # Asymmetric designs (USER-set art flag) get a real left-facing
            # strip from the mirrored base; a failed or count-mismatched left
            # pass keeps the consumers' default horizontal flip.
            got_left = self._sheet_frames(
                ctx, base.transpose(Image.Transpose.FLIP_LEFT_RIGHT), actor_id,
                state, motion, n_frames, cell_px, facing="left",
                kept="right-facing flip kept",
            )
            if got_left is None:
                continue
            left, _left_wander = got_left
            if len(left) != len(frames):
                warn(
                    ctx,
                    f"animation: {actor_id!r} {state!r} left strip segmented "
                    f"to {len(left)} frame(s) vs {len(frames)} right; "
                    f"right-facing flip kept.",
                )
                continue
            left = [f.resize((size, size), resample) for f in left]
            left_rel = f"{sprite_dir}/{state}_left.png"
            states[state]["path_left"] = left_rel
            states[state]["hash_left"] = ctx.adapter.write_binary(
                left_rel,
                _stamped_png(
                    ctx, self.producer, self.graphics, frames_to_strip(left),
                    f"strip:{actor_slug}/{state}_left",
                ),
            )
            states[state]["frames_left"] = len(left)
            frame_lists[f"{state}__left"] = left
        if not states:
            return {}
        # frames.json (no hashes) is the consumers' per-actor playback
        # manifest, loaded beside the strips; hashes stay in the Bible.
        ctx.adapter.write_json_singleton(
            f"{sprite_dir}/frames.json",
            {
                st: {k: v for k, v in d.items() if k not in ("hash", "hash_left")}
                for st, d in states.items()
            },
        )
        # One packed atlas over every generated frame (both facings): trimmed
        # rects + each crop's (ox, oy) inside its untrimmed square, anchor =
        # the square's bottom-center — consumers reconstitute full squares so
        # draw math is unchanged.
        atlas_img, rects = pack_atlas(frame_lists)
        atlas_rel = f"{sprite_dir}/atlas.png"
        atlas_hash = ctx.adapter.write_binary(
            atlas_rel,
            _stamped_png(
                ctx, self.producer, self.graphics, atlas_img,
                f"atlas:{actor_slug}",
            ),
        )
        ctx.adapter.write_json_singleton(
            f"{sprite_dir}/atlas.json",
            {
                "path": atlas_rel,
                "frame_size": [size, size],
                "states": {
                    st: {
                        "loop": d["loop"],
                        "durations_ms": d["durations_ms"],
                        "frames": rects[st],
                        **(
                            {"frames_left": rects[f"{st}__left"]}
                            if f"{st}__left" in rects
                            else {}
                        ),
                    }
                    for st, d in states.items()
                },
            },
        )
        return {"states": states, "atlas": {"path": atlas_rel, "hash": atlas_hash}}


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
                backdrop.band_paths.append(rel)
                backdrop.band_hashes[rel] = ctx.adapter.write_binary(
                    rel,
                    _stamped_png(
                        ctx, self.producer, self.graphics, img,
                        f"band:{stage_id}/{band}",
                    ),
                )
            # The foreground occlusion band (RGBA occluders, depth > 1,
            # drawn in front of gameplay) — appended LAST: band order
            # stays far → near → foreground.
            if self.graphics.foreground_band:
                try:
                    img = self.producer.backdrop_image(
                        self.graphics.backdrop_bands, stage.theme,
                        world_title, palette, bg_hex, self.graphics,
                        foreground=True,
                    )
                except Exception as e:  # noqa: BLE001
                    warn(
                        ctx,
                        f"backdrop art: foreground band failed for stage "
                        f"{stage_id!r} ({type(e).__name__}: {e}); no "
                        f"occluder band.",
                    )
                else:
                    rel = f"backdrop/{stage_id}/band_fg.png"
                    backdrop.band_paths.append(rel)
                    backdrop.band_hashes[rel] = ctx.adapter.write_binary(
                        rel,
                        _stamped_png(
                            ctx, self.producer, self.graphics, img,
                            f"band:{stage_id}/fg",
                        ),
                    )
                    backdrop.depths.append(FOREGROUND_DEPTH)
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


class WorldArtPhase:
    """One key-art SPLASH per world (``splash/world.png``, 16:9): the
    boot screen the Godot shell letterboxes under its code-drawn title
    Label. The card is LEAF art addressed as ``splash`` (hash-tracked
    via ``World.splash_path/splash_hash`` on the World entity, but
    NEVER under the ``world`` id — nothing derives from splash pixels,
    so a pin or hand edit must not touch the world's descendant set);
    no producer or a failed generation leaves the paths empty — the
    engine's code-drawn title card IS the fallback."""

    name = "plat:world_art"

    def __init__(
        self,
        producer: Any = None,
        graphics: GraphicsSpec = DEFAULT_GRAPHICS,
    ) -> None:
        self.producer = producer
        self.graphics = graphics

    def owns(self, ctx: Any) -> list[str]:
        # `canon regen splash` re-rolls the card; `regen world` re-rolls
        # the WORLD (and everything under it) — deliberately distinct.
        return ["splash"]

    def run(self, ctx: Any) -> None:
        world = ctx.bible.world
        if self.producer is None or world is None:
            logger.info(
                "WorldArtPhase: no image producer/world — title card kept."
            )
            _stamp_metadata(ctx, self.name)
            return
        if "splash" in pinned_ids(ctx.bible):
            logger.info("WorldArtPhase: splash is pinned — card kept.")
            _stamp_metadata(ctx, self.name)
            return
        # Prompt seeds, in world order (deterministic): every stage's
        # theme, and the first painted stage's palette as the swatch.
        themes = [
            ctx.bible.stages[sid].theme
            for sid in world.stage_ids
            if sid in ctx.bible.stages
        ]
        palette: dict[str, str] = {}
        for sid in world.stage_ids:
            tileset = ctx.bible.tilesets.get(sid)
            if tileset is not None:
                palette = tileset.palette
                break
        try:
            img = self.producer.splash_image(
                world.title, themes, palette, self.graphics
            )
        except Exception as e:  # noqa: BLE001
            warn(
                ctx,
                f"world art: splash generation failed "
                f"({type(e).__name__}: {e}); title card kept.",
            )
            _stamp_metadata(ctx, self.name)
            return
        rel = "splash/world.png"
        world.splash_path = rel
        world.splash_hash = ctx.adapter.write_binary(
            rel,
            _stamped_png(ctx, self.producer, self.graphics, img, "splash:world"),
        )
        content_hash = ctx.adapter.write_json_singleton(
            "world.json", world.model_dump(mode="json")
        )
        # label keeps the TEXT model truthful (the world plan's author);
        # model_extra folds the art generators in, like the other art
        # phases' re-stamps.
        stamp_provenance(
            ctx, world, content_hash,
            label="plat:world",
            model_extra=(
                f"gfx:{self.graphics.digest()}+img:{self.producer.model}"
            ),
        )
        logger.info(
            "WorldArtPhase wrote %s via %s.", rel, self.producer.model
        )
        _stamp_metadata(ctx, self.name)
