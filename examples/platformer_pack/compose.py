"""Compose the platformer slice pipeline (sequential run_pipeline order).

World → Stage → Enemies → Tileset → Layout/Stamp → Placement → Render →
slice manifest. No orchestrator, no `requires` — deliberately Phase-2-free.

The 3b template knobs — GameRules, TileRegistry, VariantSet — enter here
and thread into every phase that enforces them; the manifest ships all
three so play surfaces resolve the same vocabulary the validators used.
"""

from __future__ import annotations

import logging
from typing import Any

from canon.bible.models import BibleMetadata
from examples.platformer_pack.art_phases import (
    BackdropArtPhase,
    SpriteArtPhase,
    TilesetArtPhase,
)
from examples.platformer_pack.graphics import DEFAULT_GRAPHICS, GraphicsSpec
from examples.platformer_pack.layers import BackgroundPhase, TileAssignmentPhase
from examples.platformer_pack.level import (
    DecoratorPhase,
    LayoutStampPhase,
    PlacementPhase,
)
from examples.platformer_pack.movement import DEFAULT_MOVEMENT, PlayerMovementSpec
from examples.platformer_pack.phases import EnemyGeneratorPhase, StagePhase, WorldPhase
from examples.platformer_pack.render import RenderPhase
from examples.platformer_pack.rules import DEFAULT_RULES, GameRules
from examples.platformer_pack.style import StyleGuidePhase
from examples.platformer_pack.tiles import DEFAULT_TILES, TileRegistry
from examples.platformer_pack.tileset import PlaceholderTilesetPhase
from examples.platformer_pack.variants import DEFAULT_VARIANTS, VariantSet

logger = logging.getLogger(__name__)


def _audio_block(ctx: Any, stage_id: str) -> dict[str, Any]:
    audio = getattr(ctx.bible, "audio", {}).get(stage_id)
    if audio is None:
        return {"music": None, "sfx": {}}
    return {
        "music": audio.music_path or None,
        "sfx": dict(audio.sfx_paths),
    }


def _run_warnings(ctx: Any) -> list[str]:
    """This run's warnings plus durable Bible-state notices. The manifest
    is rebuilt on every resume (always node) from ctx.artifacts, so a
    warning not re-derivable from the Bible is erased by the next resume
    — which is how the l3 fallback record vanished from the real run."""
    warnings = list(ctx.artifacts.get("slice_warnings", []))
    for level_id, level in sorted(ctx.bible.levels.items()):
        if not getattr(level, "layout_fallback", False):
            continue
        # Exact prefix, not substring — "l1" must not match inside "l10".
        # Both the warn() message and the durable notice share this prefix.
        if any(
            w.startswith(f"layout {level_id}:") and "FALLBACK" in w
            for w in warnings
        ):
            continue  # this run's warn() already recorded it
        warnings.append(
            f"layout {level_id}: level is the flat FALLBACK layout "
            f"(attempt trace: review/{level.stage_id}/"
            f"{level_id}_layout_attempts.json)."
        )
    return warnings


class SliceManifestPhase:
    """Root manifest: what exists and where the harness should look.
    No timestamps — the slice tree is byte-deterministic by contract."""

    name = "plat:manifest"

    def __init__(
        self,
        movement: PlayerMovementSpec = DEFAULT_MOVEMENT,
        rules: GameRules = DEFAULT_RULES,
        tiles: TileRegistry = DEFAULT_TILES,
        variants: VariantSet = DEFAULT_VARIANTS,
        graphics: GraphicsSpec = DEFAULT_GRAPHICS,
    ) -> None:
        self.movement = movement
        self.rules = rules
        self.tiles = tiles
        self.variants = variants
        self.graphics = graphics

    def run(self, ctx: Any) -> None:
        stage_id = ctx.artifacts["stage_id"]
        manifest = {
            "game": "platformer_slice",
            "seed": str(getattr(ctx.config, "seed", "")),
            "world": ctx.bible.world.title if ctx.bible.world else "",
            "stage_id": stage_id,
            "levels": list(ctx.bible.stages[stage_id].level_ids),
            "enemies": sorted(ctx.bible.enemy_definitions),
            # The physics the validators actually enforced — both play
            # surfaces read this, so a custom spec must ship here too.
            "movement": self.movement.model_dump(),
            # Per-game vocabulary (E.7 + 3b) — one source read by
            # validators at generation time and every play surface at
            # runtime. model_dump includes unknown (inert) keys: open
            # carriage. Tile params also ride on tileset slots; the
            # registry here is the reviewer-facing copy.
            "rules": self.rules.model_dump(),
            "tiles": [t.model_dump(mode="json") for t in self.tiles.tiles],
            # The palette the tilesheet was actually painted with (style
            # agent output, or placeholder fallback) — from the Tileset
            # artifact, the single source.
            "palette": ctx.bible.tilesets[stage_id].palette,
            "variants": [
                v.model_dump(mode="json") for v in self.variants.variants
            ],
            # Presentation spec (GraphicsSpec) — camera framing + actor
            # overdraw are per-game data the play surfaces read here.
            "graphics": self.graphics.model_dump(mode="json"),
            # Generated audio (late audio phase): a music theme path and
            # event-keyed SFX paths, or nulls/empty — silence is the
            # fallback both surfaces already play.
            "audio": _audio_block(ctx, stage_id),
            # Fallbacks and dropped content are failures wearing a suit —
            # they must survive the run and reach the reviewer.
            "warnings": _run_warnings(ctx),
        }
        ctx.adapter.write_json_singleton("manifest.json", manifest)

        # Generation report — the positive summary, MazeWorld-style.
        stage = ctx.bible.stages[stage_id]
        logger.info(
            "Slice complete: world %r / stage %r (%s) — %d levels, "
            "%d enemy definitions, %d placements, %d warning(s).",
            manifest["world"], stage_id, stage.theme,
            len(ctx.bible.levels), len(ctx.bible.enemy_definitions),
            sum(len(lv.entities) for lv in ctx.bible.levels.values()),
            len(manifest["warnings"]),
        )

        if not isinstance(getattr(ctx.bible, "metadata", None), BibleMetadata):
            ctx.bible.metadata = BibleMetadata()
        ctx.bible.metadata.phases_run.append(self.name)


def compose_pipeline(
    num_levels: int = 3,
    num_enemies: int = 4,
    width: int = 48,
    height: int = 16,
    movement: PlayerMovementSpec = DEFAULT_MOVEMENT,
    rules: GameRules = DEFAULT_RULES,
    tiles: TileRegistry = DEFAULT_TILES,
    variants: VariantSet = DEFAULT_VARIANTS,
    engine: str = "json",
    image_producer: Any = None,
    graphics: GraphicsSpec = DEFAULT_GRAPHICS,
    music_producer: Any = None,
    sfx_producer: Any = None,
) -> list:
    # Order enforces invariant I5: collision before every other layer;
    # hazards (stamped with collision) before entities; decoration last.
    # Style precedes Enemies: enemy hue reservations derive from the
    # palette's actual hazard/volume hues.
    from examples.platformer_pack.audio_phases import AudioPhase
    phases = [
        WorldPhase(),
        StagePhase(num_levels=num_levels, num_enemies=num_enemies),
        StyleGuidePhase(tiles=tiles),
        EnemyGeneratorPhase(count=num_enemies, tiles=tiles),
        PlaceholderTilesetPhase(tiles=tiles, graphics=graphics),
        LayoutStampPhase(
            width=width, height=height, movement=movement, rules=rules,
            tiles=tiles, graphics=graphics,
        ),
        TileAssignmentPhase(),
        BackgroundPhase(),
        PlacementPhase(rules=rules, tiles=tiles, variants=variants),
        DecoratorPhase(),
        # Art AT THE END (user rule): paid generation only after every
        # gameplay layer above validated; renders below see final art.
        TilesetArtPhase(tiles=tiles, producer=image_producer, graphics=graphics),
        SpriteArtPhase(producer=image_producer, graphics=graphics),
        BackdropArtPhase(tiles=tiles, producer=image_producer, graphics=graphics),
        AudioPhase(music_producer=music_producer, sfx_producer=sfx_producer),
        RenderPhase(variants=variants),
        SliceManifestPhase(
            movement=movement, rules=rules, tiles=tiles, variants=variants,
            graphics=graphics,
        ),
    ]
    if engine == "godot":
        from examples.platformer_pack.godot_export import GodotExportPhase

        phases.append(GodotExportPhase())
    return phases
