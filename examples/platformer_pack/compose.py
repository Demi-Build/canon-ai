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


class SliceManifestPhase:
    """Root manifest: what exists and where the harness should look.
    No timestamps — the slice tree is byte-deterministic by contract."""

    name = "plat:manifest"

    def __init__(
        self,
        rules: GameRules = DEFAULT_RULES,
        tiles: TileRegistry = DEFAULT_TILES,
        variants: VariantSet = DEFAULT_VARIANTS,
    ) -> None:
        self.rules = rules
        self.tiles = tiles
        self.variants = variants

    def run(self, ctx: Any) -> None:
        stage_id = ctx.artifacts["stage_id"]
        manifest = {
            "game": "platformer_slice",
            "seed": str(getattr(ctx.config, "seed", "")),
            "world": ctx.bible.world.title if ctx.bible.world else "",
            "stage_id": stage_id,
            "levels": list(ctx.bible.stages[stage_id].level_ids),
            "enemies": sorted(ctx.bible.enemy_definitions),
            "movement": DEFAULT_MOVEMENT.model_dump(),
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
            # Fallbacks and dropped content are failures wearing a suit —
            # they must survive the run and reach the reviewer.
            "warnings": list(ctx.artifacts.get("slice_warnings", [])),
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
) -> list:
    # Order enforces invariant I5: collision before every other layer;
    # hazards (stamped with collision) before entities; decoration last.
    phases = [
        WorldPhase(),
        StagePhase(num_levels=num_levels, num_enemies=num_enemies),
        EnemyGeneratorPhase(count=num_enemies),
        StyleGuidePhase(tiles=tiles),
        PlaceholderTilesetPhase(tiles=tiles),
        LayoutStampPhase(
            width=width, height=height, movement=movement, rules=rules,
            tiles=tiles,
        ),
        TileAssignmentPhase(),
        BackgroundPhase(),
        PlacementPhase(rules=rules, tiles=tiles, variants=variants),
        DecoratorPhase(),
        RenderPhase(variants=variants),
        SliceManifestPhase(rules=rules, tiles=tiles, variants=variants),
    ]
    if engine == "godot":
        from examples.platformer_pack.godot_export import GodotExportPhase

        phases.append(GodotExportPhase())
    return phases
