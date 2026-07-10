"""Compose the platformer slice pipeline (sequential run_pipeline order).

World → Stage → Enemies → Tileset → Layout/Stamp → Placement → Render →
slice manifest. No orchestrator, no `requires` — deliberately Phase-2-free.

The 3b template knobs — GameRules, TileRegistry, VariantSet — enter here
and thread into every phase that enforces them; the manifest ships all
three so play surfaces resolve the same vocabulary the validators used.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from canon.bible.models import BibleMetadata
from examples.platformer_pack.art_phases import (
    BackdropArtPhase,
    SpriteArtPhase,
    TilesetArtPhase,
)
from examples.platformer_pack.combat import DEFAULT_COMBAT, CombatSpec
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


def _props_block(ctx: Any, stage_id: str) -> dict[str, str]:
    """Generated gameplay-prop sprite paths (checkpoint/exit), or empty —
    consumers draw their placeholder shapes for any absent prop."""
    props = getattr(ctx.bible, "props", {}).get(stage_id)
    return dict(props.prop_paths) if props is not None else {}


def _audio_block(ctx: Any, stage_id: str) -> dict[str, Any]:
    audio = getattr(ctx.bible, "audio", {}).get(stage_id)
    if audio is None:
        return {"music": None, "sfx": {}}
    return {
        "music": audio.music_path or None,
        "sfx": dict(audio.sfx_paths),
    }


def _world_map(ctx: Any, stages: list) -> dict[str, Any]:
    """The DK/SMW map layout, computed DETERMINISTICALLY in code: level
    nodes clustered per biome stage along a left→right path with a
    seeded zigzag, positions normalized 0..1 (consumers scale to their
    screen). Display names are "{stage#}-{level#}" — what players see;
    internal level ids stay the address space."""
    from canon.pipeline.rng import derive_rng

    rng = derive_rng(str(getattr(ctx.config, "seed", "")), "plat:world_map")
    total = sum(len(stage.level_ids) for stage in stages)
    # Inter-stage gap: half a slot between biome clusters.
    slots = max(total + 0.5 * max(len(stages) - 1, 0), 1.0)
    nodes: list[dict[str, Any]] = []
    edges: list[list[str]] = []
    cursor = 0.0
    previous: str | None = None
    for stage_number, stage in enumerate(stages, start=1):
        for level_index, level_id in enumerate(stage.level_ids, start=1):
            x = (cursor + 0.5) / slots
            y = 0.5 + (0.14 if len(nodes) % 2 else -0.14)
            y += (rng.random() - 0.5) * 0.06
            nodes.append(
                {
                    "level_id": level_id,
                    "display_name": f"{stage_number}-{level_index}",
                    "stage_id": stage.stage_id,
                    "pos": [round(x, 4), round(y, 4)],
                }
            )
            if previous is not None:
                edges.append([previous, level_id])
            previous = level_id
            cursor += 1.0
        cursor += 0.5  # biome cluster gap
    return {"nodes": nodes, "edges": edges}


def _run_warnings(ctx: Any) -> list[str]:
    """This run's warnings plus durable disk/Bible-state notices. The
    manifest is rebuilt on every resume (always node) from ctx.artifacts,
    so a warning not re-derivable from durable state is erased by the
    next resume — which is how the l3 fallback record vanished from the
    real run."""
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
    # QA findings re-derive from the on-disk report (same strings the QA
    # phase warned, so dedup is plain membership) — a resume that skips
    # the judge must not silently launder a failing report.
    import json as _json

    from examples.platformer_pack.vlm_qa import derive_qa_warnings, qa_report_rel

    for stage_id in sorted(getattr(ctx.bible, "stages", {})):
        path = ctx.adapter.resolve_path(qa_report_rel(stage_id))
        if not path.exists():
            continue
        try:
            report = _json.loads(path.read_text())
        except (OSError, ValueError):
            warnings.append(
                f"vlm_qa: {qa_report_rel(stage_id)} exists but is "
                "unreadable — re-run with --vlm-backend to regenerate it."
            )
            continue
        for message in derive_qa_warnings(report):
            if message not in warnings:
                warnings.append(message)
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
        combat: CombatSpec = DEFAULT_COMBAT,
    ) -> None:
        self.movement = movement
        self.rules = rules
        self.tiles = tiles
        self.variants = variants
        self.graphics = graphics
        self.combat = combat

    def run(self, ctx: Any) -> None:
        from examples.platformer_pack.level import world_stages

        stages = world_stages(ctx)
        world_title = ctx.bible.world.title if ctx.bible.world else ""
        # Play progress is keyed on the WORLD's CONTENT identity, not the
        # input seed: two paid runs of the same seed produce DIFFERENT worlds
        # (names, biomes, rosters), and each fresh world must start from
        # level 1 rather than inherit the previous run's save (playtest —
        # plat_kingdom3 opened on world 3 with the first levels beaten).
        # Content-derived so it stays deterministic: a byte-identical
        # regeneration of the SAME world keeps its save (correct for resume).
        world_key = "|".join(
            [
                world_title,
                *(s.stage_id for s in stages),
                *(lid for s in stages for lid in s.level_ids),
                *sorted(ctx.bible.enemy_definitions),
            ]
        )
        world_id = hashlib.md5(world_key.encode("utf-8")).hexdigest()[:12]
        manifest = {
            "game": "platformer_slice",
            "seed": str(getattr(ctx.config, "seed", "")),
            "world": world_title,
            # Save-file key (see world_key above) — the runtime reads this.
            "world_id": world_id,
            # World structure (multi-stage): biome stages in play order,
            # every level flattened in that same order, and the code-laid
            # map the world-map screen draws. Unlock policy is data.
            "stages": [
                {
                    "stage_id": stage.stage_id,
                    "biome": stage.biome,
                    "theme": stage.theme,
                    "levels": list(stage.level_ids),
                }
                for stage in stages
            ],
            "levels": [
                level_id for stage in stages for level_id in stage.level_ids
            ],
            "world_map": _world_map(ctx, stages),
            "unlock": dict(
                ctx.bible.world.unlock_rules
                if ctx.bible.world and ctx.bible.world.unlock_rules
                else {"type": "linear"}
            ),
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
            # Combat tuning (combat v1) — numbers only; combat POLICY
            # toggles live in rules. Same one-source contract: the
            # placement validator enforced these, the play surfaces obey
            # them.
            "combat": self.combat.model_dump(),
            "tiles": [t.model_dump(mode="json") for t in self.tiles.tiles],
            # The palettes each stage's tilesheet was actually painted
            # with (style agent output, or placeholder fallback) — from
            # the Tileset artifacts, keyed by stage. The world-map screen
            # tints biome regions with these.
            "palettes": {
                stage.stage_id: ctx.bible.tilesets[stage.stage_id].palette
                for stage in stages
                if stage.stage_id in ctx.bible.tilesets
            },
            "variants": [
                v.model_dump(mode="json") for v in self.variants.variants
            ],
            # Presentation spec (GraphicsSpec) — camera framing + actor
            # overdraw are per-game data the play surfaces read here.
            "graphics": self.graphics.model_dump(mode="json"),
            # Generated audio + prop sprites, keyed by stage (levels in a
            # biome share them) — nulls/empty mean silence / placeholder
            # shapes, the fallbacks both surfaces already handle.
            "audio": {
                stage.stage_id: _audio_block(ctx, stage.stage_id)
                for stage in stages
            },
            "props": {
                stage.stage_id: _props_block(ctx, stage.stage_id)
                for stage in stages
            },
            # Fallbacks and dropped content are failures wearing a suit —
            # they must survive the run and reach the reviewer.
            "warnings": _run_warnings(ctx),
        }
        ctx.adapter.write_json_singleton("manifest.json", manifest)

        # Generation report — the positive summary, MazeWorld-style.
        logger.info(
            "Slice complete: world %r — %d stage(s) (%s), %d levels, "
            "%d enemy definitions, %d placements, %d warning(s).",
            manifest["world"], len(stages),
            ", ".join(f"{s.stage_id}[{s.biome}]" for s in stages),
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
    num_stages: int = 1,
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
    combat: CombatSpec = DEFAULT_COMBAT,
    vlm_judge: Any = None,
) -> list:
    # Order enforces invariant I5: collision before every other layer;
    # hazards (stamped with collision) before entities; decoration last.
    # Style precedes Enemies: enemy hue reservations derive from the
    # palettes' actual hazard/volume hues (union across biomes).
    from examples.platformer_pack.audio_phases import AudioPhase
    from examples.platformer_pack.vlm_qa import VlmQaPhase
    phases = [
        WorldPhase(num_stages=num_stages),
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
        PlacementPhase(rules=rules, tiles=tiles, variants=variants, combat=combat),
        DecoratorPhase(),
        # Art AT THE END (user rule): paid generation only after every
        # gameplay layer above validated; renders below see final art.
        TilesetArtPhase(tiles=tiles, producer=image_producer, graphics=graphics),
        SpriteArtPhase(producer=image_producer, graphics=graphics),
        BackdropArtPhase(tiles=tiles, producer=image_producer, graphics=graphics),
        AudioPhase(music_producer=music_producer, sfx_producer=sfx_producer),
        RenderPhase(variants=variants, graphics=graphics),
        # VLM QA at the VERY END of generation: it judges the renders
        # above and its failing verdicts must land in the manifest below.
        VlmQaPhase(judge=vlm_judge, tiles=tiles, variants=variants),
        SliceManifestPhase(
            movement=movement, rules=rules, tiles=tiles, variants=variants,
            graphics=graphics, combat=combat,
        ),
    ]
    if engine == "godot":
        from examples.platformer_pack.godot_export import GodotExportPhase

        phases.append(GodotExportPhase())
    return phases
