"""Platformer pack → DagPhase conversion (Phase 3b-2).

The Phase 2 orchestrator schedules per-(step, level) nodes; this module
expands the pack into that graph. Node ids ARE the §6.1 step artifact ids
(``level:<stage>/<lid>/<step>``), which is what makes per-step regen work:
``detect_edits`` marks those same ids STALE, and the scheduler re-runs
exactly them — nothing else.

Node bodies are the SAME per-level functions the sequential phases call
(level.py / layers.py / render.py), so an orchestrated run at cap 1 is
byte-identical to a ``run_pipeline`` run.

Graph shape (macro phases stay legacy — strictly ordered anyway):

    world → stage → enemies → tileset   (legacy nodes, barrier edges)
      └→ per level: collision → {hazards, triggers, terrain, background}
                    → entities (← hazards) → foreground → level(.json)
      └→ review:<s>/<lid> (always) ← terrain/background/entities/foreground
      └→ review:<s>/legend (always), plat:manifest (always), godot (always)

Review renders and root manifests are ``always`` nodes: cheap and
deterministic, they re-derive every run so any regen is reflected without
needing their own stale-cascade entries.

Bootstrap: the per-level graph can only expand once the stage plan exists
(level list = runtime data). ``run_orchestrated`` therefore runs the macro
phases first when the Bible is empty, then the full graph — "resume IS
run" makes the second pass skip everything already DONE. Via the raw CLI
(`canon run`) a fresh Bible needs two invocations for the same reason; an
existing Bible (the regen workload) needs one.

Caveat carried from Phase 2: the pack's node bodies mutate shared context
(Bible entities, warning list, LLM stats), so run this graph at the
default ``max_concurrency=1``. Raising the cap is safe only for nodes
touching disjoint state — not audited for this pack yet.
"""

from __future__ import annotations

import logging
import os
import random
from pathlib import Path
from typing import Any

from canon.pipeline.orchestrator import Node, OrchestratorReport, orchestrate
from examples.platformer_pack.combat import DEFAULT_COMBAT, CombatSpec
from examples.platformer_pack.godot_export import GodotExportPhase
from examples.platformer_pack.graphics import DEFAULT_GRAPHICS, GraphicsSpec
from examples.platformer_pack.layers import (
    assign_level_terrain,
    paint_level_background,
)
from examples.platformer_pack.level import (
    LEVEL_STEPS,
    decorate_level,
    place_level_entities,
    stamp_level_collision,
    write_level_hazards,
    write_level_manifest,
    write_level_triggers,
)
from examples.platformer_pack.movement import DEFAULT_MOVEMENT, PlayerMovementSpec
from examples.platformer_pack.phases import (
    EnemyGeneratorPhase,
    StagePhase,
    WorldPhase,
)
from examples.platformer_pack.render import (
    render_level_review,
    write_review_legend,
)
from examples.platformer_pack.rules import DEFAULT_RULES, GameRules
from examples.platformer_pack.tiles import DEFAULT_TILES, TileRegistry
from examples.platformer_pack.tileset import PlaceholderTilesetPhase
from examples.platformer_pack.variants import DEFAULT_VARIANTS, VariantSet

logger = logging.getLogger(__name__)


def _stage(ctx: Any):
    """The single stage of the slice, or None before the macro phases ran
    (fresh Bible) — expansion is deferred to the next pass in that case."""
    stages = getattr(ctx.bible, "stages", {})
    if not stages:
        logger.info(
            "Platformer DAG: no stages in the Bible yet — per-level nodes "
            "expand on the next run, after the macro phases complete."
        )
        return None
    return next(iter(stages.values()))


class LevelStepsDagPhase:
    """Expands the per-level layer steps into (step, level) nodes.

    Step-major node order (all collisions, then all hazards, ...) so a
    cap-1 orchestrated run executes in the same order as the sequential
    phases — identical warning order, identical logs, identical bytes.
    """

    name = "plat:level_steps"

    def __init__(
        self,
        width: int = 48,
        height: int = 16,
        movement: PlayerMovementSpec = DEFAULT_MOVEMENT,
        rules: GameRules = DEFAULT_RULES,
        tiles: TileRegistry = DEFAULT_TILES,
        variants: VariantSet = DEFAULT_VARIANTS,
        graphics: GraphicsSpec = DEFAULT_GRAPHICS,
        combat: CombatSpec = DEFAULT_COMBAT,
        max_enemies_per_level: int = 4,
        max_decor: int = 6,
    ) -> None:
        self.width = width
        self.height = height
        self.movement = movement
        self.rules = rules
        self.tiles = tiles
        self.variants = variants
        self.graphics = graphics
        self.combat = combat
        self.max_enemies = max_enemies_per_level
        self.max_decor = max_decor

    def expand(self, ctx: Any) -> list[Node]:
        stage = _stage(ctx)
        if stage is None:
            return []
        # Regen/resume runs start from a loaded Bible with empty scratch
        # artifacts — node bodies resolve the stage through here.
        ctx.artifacts.setdefault("stage_id", stage.stage_id)
        sid = stage.stage_id
        tileset_aid = stage.tileset_ref

        def aid(level_id: str, step: str) -> str:
            return f"level:{sid}/{level_id}/{step}"

        def collision_body(level_id: str, index: int):
            def run(c: Any) -> None:
                level = stamp_level_collision(
                    c, level_id, index,
                    movement=self.movement, rules=self.rules,
                    tiles=self.tiles, graphics=self.graphics,
                    default_width=self.width,
                    default_height=self.height, phase_name="plat:layout",
                )
                del level  # registered in the Bible by the body

            return run

        def level_body(level_id: str, fn, **kwargs):
            def run(c: Any) -> None:
                fn(c, c.bible.levels[level_id], **kwargs)

            return run

        nodes: list[Node] = []
        level_ids = list(stage.level_ids)
        # collision creates the Level entity; everything else reads it.
        for index, lid in enumerate(level_ids):
            nodes.append(
                Node(node_id=aid(lid, "collision"), run=collision_body(lid, index))
            )
        for lid in level_ids:
            nodes.append(
                Node(
                    node_id=aid(lid, "hazards"),
                    run=level_body(lid, write_level_hazards),
                    requires=[aid(lid, "collision")],
                )
            )
        for lid in level_ids:
            nodes.append(
                Node(
                    node_id=aid(lid, "triggers"),
                    run=level_body(lid, write_level_triggers),
                    requires=[aid(lid, "collision")],
                )
            )
        for lid in level_ids:
            nodes.append(
                Node(
                    node_id=aid(lid, "terrain"),
                    run=level_body(lid, assign_level_terrain),
                    requires=[aid(lid, "collision"), tileset_aid],
                )
            )
        for lid in level_ids:
            nodes.append(
                Node(
                    node_id=aid(lid, "background"),
                    run=level_body(lid, paint_level_background),
                    requires=[aid(lid, "collision")],
                )
            )
        for lid in level_ids:
            nodes.append(
                Node(
                    node_id=aid(lid, "entities"),
                    run=level_body(
                        lid, place_level_entities,
                        max_enemies=self.max_enemies, rules=self.rules,
                        tiles=self.tiles, variants=self.variants,
                        combat=self.combat, phase_name="plat:placement",
                    ),
                    # Placement reads each definition's SIZE (footprint
                    # validation) — an enemy re-roll must schedule before
                    # and cascade into entities, or a grown body silently
                    # invalidates the stored placements.
                    requires=[
                        aid(lid, "collision"),
                        aid(lid, "hazards"),
                        "phase:plat:enemies",
                    ],
                )
            )
        for lid in level_ids:
            nodes.append(
                Node(
                    node_id=aid(lid, "foreground"),
                    run=level_body(
                        lid, decorate_level,
                        max_decor=self.max_decor, phase_name="plat:decorator",
                    ),
                    requires=[aid(lid, "collision"), tileset_aid],
                )
            )
        for lid in level_ids:
            # The per-level manifest descends from EVERY layer step
            # (LEVEL_STEPS), so any layer edit/regen re-freshens it.
            nodes.append(
                Node(
                    node_id=aid(lid, "level"),
                    run=level_body(lid, write_level_manifest),
                    requires=[aid(lid, step) for step in LEVEL_STEPS],
                )
            )
        return nodes


class RenderDagPhase:
    """Per-level review renders + the roster legend — ``always`` nodes."""

    name = "plat:render"

    def __init__(
        self,
        variants: VariantSet = DEFAULT_VARIANTS,
        graphics: GraphicsSpec = DEFAULT_GRAPHICS,
    ) -> None:
        self.variants = variants
        self.graphics = graphics

    def expand(self, ctx: Any) -> list[Node]:
        stage = _stage(ctx)
        if stage is None:
            return []
        sid = stage.stage_id

        def render_body(level_id: str):
            def run(c: Any) -> None:
                render_level_review(
                    c, c.bible.levels[level_id], variants=self.variants,
                    graphics=self.graphics,
                )

            return run

        nodes = [
            Node(
                node_id=f"review:{sid}/{lid}",
                run=render_body(lid),
                requires=[
                    f"level:{sid}/{lid}/{step}"
                    for step in ("terrain", "background", "entities", "foreground")
                ],
                always=True,
            )
            for lid in stage.level_ids
        ]
        nodes.append(
            Node(
                node_id=f"review:{sid}/legend",
                run=lambda c: write_review_legend(c),
                requires=["phase:plat:enemies"],
                always=True,
            )
        )
        return nodes


class ManifestDagPhase:
    """Root manifest.json — an ``always`` node behind every level + render."""

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
        from examples.platformer_pack.compose import SliceManifestPhase

        self._phase = SliceManifestPhase(
            movement=movement, rules=rules, tiles=tiles, variants=variants,
            graphics=graphics, combat=combat,
        )

    def expand(self, ctx: Any) -> list[Node]:
        stage = _stage(ctx)
        if stage is None:
            return []
        sid = stage.stage_id
        requires = [f"level:{sid}/{lid}/level" for lid in stage.level_ids]
        requires += [f"review:{sid}/{lid}" for lid in stage.level_ids]
        requires.append(f"review:{sid}/legend")
        return [
            Node(
                node_id="plat:manifest",
                run=self._phase.run,
                requires=requires,
                always=True,
            )
        ]


class GodotExportDagPhase:
    """Godot project template copy — ``always`` so consumer-template
    changes in the pack reach existing output trees on the next run."""

    name = "plat:godot_export"

    def __init__(self) -> None:
        self._phase = GodotExportPhase()

    def expand(self, ctx: Any) -> list[Node]:
        return [
            Node(
                node_id="plat:godot_export",
                run=self._phase.run,
                requires=["plat:manifest"],
                always=True,
            )
        ]


def macro_phases(num_levels: int = 3, num_enemies: int = 4, *,
                 tiles: TileRegistry = DEFAULT_TILES,
                 graphics: GraphicsSpec = DEFAULT_GRAPHICS) -> list:
    """The bootstrap prefix: world/stage/style/enemies/tileset as legacy
    nodes (style first — enemy hues avoid the palette's hazard/volume
    hues)."""
    from examples.platformer_pack.style import StyleGuidePhase

    return [
        WorldPhase(),
        StagePhase(num_levels=num_levels, num_enemies=num_enemies),
        StyleGuidePhase(tiles=tiles),
        EnemyGeneratorPhase(count=num_enemies, tiles=tiles),
        PlaceholderTilesetPhase(tiles=tiles, graphics=graphics),
    ]


def compose_dag_pipeline(
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
    combat: CombatSpec = DEFAULT_COMBAT,
) -> list:
    """The full orchestrated pipeline. Per-level nodes expand only when
    the Bible already has a stage plan (see module docstring)."""
    from examples.platformer_pack.art_phases import (
        BackdropArtPhase,
        SpriteArtPhase,
        TilesetArtPhase,
    )
    from examples.platformer_pack.audio_phases import AudioPhase

    items = [
        *macro_phases(num_levels, num_enemies, tiles=tiles, graphics=graphics),
        LevelStepsDagPhase(
            width=width, height=height, movement=movement, rules=rules,
            tiles=tiles, variants=variants, graphics=graphics, combat=combat,
        ),
        # Art AT THE END: legacy barrier edges make these depend on every
        # level node above and gate the renders below — paid generation
        # never runs before the levels validate.
        TilesetArtPhase(tiles=tiles, producer=image_producer, graphics=graphics),
        SpriteArtPhase(producer=image_producer, graphics=graphics),
        BackdropArtPhase(tiles=tiles, producer=image_producer, graphics=graphics),
        AudioPhase(music_producer=music_producer, sfx_producer=sfx_producer),
        RenderDagPhase(variants=variants, graphics=graphics),
        ManifestDagPhase(
            movement=movement, rules=rules, tiles=tiles, variants=variants,
            graphics=graphics, combat=combat,
        ),
    ]
    if engine == "godot":
        items.append(GodotExportDagPhase())
    return items


def run_orchestrated(
    ctx: Any,
    *,
    persist_path: str | Path,
    max_concurrency: int | None = None,
    **compose_kwargs,
) -> OrchestratorReport:
    """Generate (or resume/regen) through the orchestrator.

    Fresh Bible → two passes: macro phases first (they invent the stage
    plan the per-level graph expands against), then the full graph, which
    skips the already-DONE macro nodes. Existing Bible → one pass.
    """
    if not getattr(ctx.bible, "stages", {}):
        bootstrap_kwargs = {
            k: compose_kwargs[k]
            for k in ("num_levels", "num_enemies", "tiles", "graphics")
            if k in compose_kwargs
        }
        report = orchestrate(
            macro_phases(**bootstrap_kwargs), ctx,
            max_concurrency=max_concurrency, persist_path=persist_path,
        )
        if not report.ok:
            return report
    return orchestrate(
        compose_dag_pipeline(**compose_kwargs), ctx,
        max_concurrency=max_concurrency, persist_path=persist_path,
    )


# ---------------------------------------------------------------------------
# `canon run/resume/status` factories (fake backend — demo + tests).
# Env knobs: CANON_PLAT_OUT (output dir), CANON_PLAT_SEED (default
# emberfall_001). A fresh Bible needs `canon run` twice (bootstrap);
# the regen workload — an existing tree with a hand-edited layer — is
# one `canon resume`.
# ---------------------------------------------------------------------------


def cli_ctx_factory(bible: Any):
    from canon.backends.testing import FakeLLMBackend
    from canon.config import CanonConfig
    from canon.llm.client import LLMClient
    from canon.pipeline.runner import PipelineContext
    from examples.platformer_pack.prompts import PlatformerPrompts
    from examples.run_platformer_slice import make_fake_responder

    seed = os.environ.get("CANON_PLAT_SEED", "emberfall_001")
    out = os.environ.get("CANON_PLAT_OUT", ".")
    return PipelineContext(
        bible=bible,
        config=CanonConfig(seed=seed, output_dir=Path(out)),
        rng=random.Random(seed),
        llm=LLMClient(FakeLLMBackend(make_fake_responder())),
        prompts=PlatformerPrompts(),
    )


def cli_phases_factory(ctx: Any) -> list:
    from examples.platformer_pack.audio_phases import (
        build_music_producer,
        build_sfx_producer,
    )

    # CANON_PLAT_IMAGE_BACKEND: "", "fake", "fal", or "local" — same
    # explicit opt-in as the runner's --image-backend; empty stays on the
    # deterministic placeholder sheet. CANON_PLAT_MUSIC_BACKEND /
    # CANON_PLAT_SFX_BACKEND mirror --music-backend/--sfx-backend (empty =
    # silent). CANON_PLAT_GRAPHICS: path to a graphics.json (the runner's
    # --graphics; default = pack spec). CANON_PLAT_COMBAT: path to a
    # combat.json (the runner's --combat; default = pack spec).
    from examples.platformer_pack.combat import load_combat
    from examples.platformer_pack.graphics import load_graphics
    from examples.platformer_pack.tileset_art import build_image_producer

    producer = build_image_producer(
        os.environ.get("CANON_PLAT_IMAGE_BACKEND", ""),
        os.environ.get("CANON_PLAT_IMAGE_MODEL") or None,
    )
    music = build_music_producer(os.environ.get("CANON_PLAT_MUSIC_BACKEND", ""))
    sfx = build_sfx_producer(os.environ.get("CANON_PLAT_SFX_BACKEND", ""))
    graphics_path = os.environ.get("CANON_PLAT_GRAPHICS")
    graphics = load_graphics(graphics_path) if graphics_path else DEFAULT_GRAPHICS
    combat_path = os.environ.get("CANON_PLAT_COMBAT")
    combat = load_combat(combat_path) if combat_path else DEFAULT_COMBAT
    return compose_dag_pipeline(
        image_producer=producer, graphics=graphics,
        music_producer=music, sfx_producer=sfx, combat=combat,
    )


# ---------------------------------------------------------------------------
# Field-level regen — "parts of rows" (`canon regen <bible>
# enemy:<id>#flavor --field-ops examples.platformer_pack.dag:regen_field`).
# File-backed assets get node-level regen for free once their phases
# stamp hashes; FIELDS inside an entity need surgical ops like these.
# ---------------------------------------------------------------------------


def regen_field(ctx: Any, target: str) -> dict:
    """Re-roll one FIELD of one entity: ``<artifact_id>#<field>``.

    Supported today: ``enemy:<id>#flavor`` (name + mechanics locked; the
    entity file is rewritten and provenance re-stamped). Image/audio
    fields (``enemy:<id>#portrait``, stage music) join this registry as
    their generation phases land — same grammar, same routing.
    """
    from examples.platformer_pack.phases import llm_json, stamp_provenance

    artifact_id, _, field = target.partition("#")
    kind, _, ident = artifact_id.partition(":")

    if kind == "enemy" and field == "flavor":
        enemy = ctx.bible.enemy_definitions.get(ident)
        if enemy is None:
            raise KeyError(
                f"unknown enemy {ident!r}; roster: "
                f"{sorted(ctx.bible.enemy_definitions)}"
            )
        stage = next(iter(ctx.bible.stages.values()), None)
        theme = stage.theme if stage else ""
        old = str(enemy.stats.get("flavor", ""))
        data = llm_json(
            ctx,
            f"plat:regen:{target}",
            lambda fb: ctx.prompts.enemy_flavor(
                enemy.name, enemy.archetype, theme, old, feedback=fb
            ),
            required_keys=("flavor",),
            fallback={"flavor": old},  # keep the row intact on failure
        )
        enemy.stats["flavor"] = str(data["flavor"])
        content_hash = ctx.adapter.write_json_singleton(
            f"enemy/{ident}.json", enemy.model_dump(mode="json")
        )
        stamp_provenance(ctx, enemy, content_hash)
        logger.info(
            "Field regen %s: %r -> %r", target, old, enemy.stats["flavor"]
        )
        return {
            "target": target,
            "old": old,
            "new": enemy.stats["flavor"],
            "changed": enemy.stats["flavor"] != old,
        }

    raise KeyError(
        f"no field op for {target!r} — supported: enemy:<id>#flavor "
        "(asset fields arrive with their generation phases)."
    )
