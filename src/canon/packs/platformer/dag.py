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

from canon.packs.platformer.combat import DEFAULT_COMBAT, CombatSpec
from canon.packs.platformer.godot_export import GodotExportPhase
from canon.packs.platformer.graphics import DEFAULT_GRAPHICS, GraphicsSpec
from canon.packs.platformer.layers import (
    assign_level_terrain,
    paint_level_background,
)
from canon.packs.platformer.level import (
    LEVEL_STEPS,
    decorate_level,
    place_level_entities,
    place_level_items,
    stamp_level_collision,
    write_level_hazards,
    write_level_manifest,
    write_level_triggers,
)
from canon.packs.platformer.movement import DEFAULT_MOVEMENT, PlayerMovementSpec
from canon.packs.platformer.phases import (
    EnemyGeneratorPhase,
    ItemGeneratorPhase,
    StagePhase,
    WorldPhase,
)
from canon.packs.platformer.render import (
    render_level_review,
    write_review_legend,
)
from canon.packs.platformer.rules import DEFAULT_RULES, GameRules
from canon.packs.platformer.tiles import DEFAULT_TILES, TileRegistry
from canon.packs.platformer.tileset import PlaceholderTilesetPhase
from canon.packs.platformer.variants import DEFAULT_VARIANTS, VariantSet
from canon.pipeline.orchestrator import Node, OrchestratorReport, orchestrate

logger = logging.getLogger(__name__)


def _stages(ctx: Any) -> list:
    """The world's stages in play order, or [] before the macro phases
    ran (fresh Bible) — expansion is deferred to the next pass then."""
    from canon.packs.platformer.level import world_stages

    stages = world_stages(ctx)
    if not stages:
        logger.info(
            "Platformer DAG: no stages in the Bible yet — per-level nodes "
            "expand on the next run, after the macro phases complete."
        )
    return stages


def _stage_level_entries(
    ctx: Any, stage: Any, width: int = 48, height: int = 16
) -> list[tuple[int, str, list[str]]]:
    """``[(index, level_id, [room_ids])]`` for one stage — the DAG's
    secret-room recompute. Expansion runs BEFORE any level body, so it
    cannot read ``Level.secret_rooms``; it recomputes the same
    deterministic roll the bodies use (``level_secret_rooms``). The dims
    defaults must match the layout phase's (they only matter for schemas
    without grid bands)."""
    from canon.packs.platformer.level import level_secret_rooms

    out: list[tuple[int, str, list[str]]] = []
    for index, lid in enumerate(stage.level_ids):
        rooms = [
            s.room_id
            for s in level_secret_rooms(
                ctx, lid, index, default_width=width, default_height=height
            )
        ]
        out.append((index, lid, rooms))
    return out


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
        max_items_per_level: int = 24,
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
        self.max_items = max_items_per_level

    def expand(self, ctx: Any) -> list[Node]:
        stages = _stages(ctx)
        if not stages:
            return []

        def aid(sid: str, level_id: str, step: str) -> str:
            return f"level:{sid}/{level_id}/{step}"

        def collision_body(level_id: str, index: int, room_of: str | None = None):
            def run(c: Any) -> None:
                level = stamp_level_collision(
                    c, level_id, index, room_of=room_of,
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

        # Step-major node order ACROSS ALL STAGES (all collisions in
        # world order, then all hazards, ...) so a cap-1 orchestrated run
        # executes in the same order as the sequential phases — identical
        # warning order, identical logs, identical bytes. Secret rooms
        # interleave PER PARENT inside every step loop (parent first,
        # then its rooms — the sequential phases' exact order).
        per_stage = [
            (
                stage.stage_id,
                stage.tileset_ref,
                _stage_level_entries(ctx, stage, self.width, self.height),
            )
            for stage in stages
        ]
        nodes: list[Node] = []
        # collision creates the Level entity; everything else reads it.
        # A room's collision requires its parent's (linkage + warn order
        # stay correct even above cap 1).
        for sid, _ts, entries in per_stage:
            for index, lid, rooms in entries:
                nodes.append(
                    Node(
                        node_id=aid(sid, lid, "collision"),
                        run=collision_body(lid, index),
                    )
                )
                for rid in rooms:
                    nodes.append(
                        Node(
                            node_id=aid(sid, rid, "collision"),
                            run=collision_body(rid, index, room_of=lid),
                            requires=[aid(sid, lid, "collision")],
                        )
                    )
        for sid, _ts, entries in per_stage:
            for _index, lid, rooms in entries:
                for xid in (lid, *rooms):
                    nodes.append(
                        Node(
                            node_id=aid(sid, xid, "hazards"),
                            run=level_body(xid, write_level_hazards),
                            requires=[aid(sid, xid, "collision")],
                        )
                    )
        for sid, _ts, entries in per_stage:
            for _index, lid, rooms in entries:
                for xid in (lid, *rooms):
                    nodes.append(
                        Node(
                            node_id=aid(sid, xid, "triggers"),
                            run=level_body(xid, write_level_triggers),
                            requires=[aid(sid, xid, "collision")],
                        )
                    )
        for sid, tileset_aid, entries in per_stage:
            for _index, lid, rooms in entries:
                for xid in (lid, *rooms):
                    nodes.append(
                        Node(
                            node_id=aid(sid, xid, "terrain"),
                            run=level_body(xid, assign_level_terrain),
                            requires=[aid(sid, xid, "collision"), tileset_aid],
                        )
                    )
        for sid, _ts, entries in per_stage:
            for _index, lid, rooms in entries:
                for xid in (lid, *rooms):
                    nodes.append(
                        Node(
                            node_id=aid(sid, xid, "background"),
                            run=level_body(xid, paint_level_background),
                            requires=[aid(sid, xid, "collision")],
                        )
                    )
        for sid, _ts, entries in per_stage:
            for _index, lid, rooms in entries:
                for xid in (lid, *rooms):
                    nodes.append(
                        Node(
                            node_id=aid(sid, xid, "entities"),
                            run=level_body(
                                xid, place_level_entities,
                                max_enemies=self.max_enemies, rules=self.rules,
                                tiles=self.tiles, variants=self.variants,
                                combat=self.combat,
                                phase_name="plat:placement",
                            ),
                            # Placement reads each definition's SIZE
                            # (footprint validation) — an enemy re-roll must
                            # schedule before and cascade into entities, or a
                            # grown body silently invalidates the stored
                            # placements.
                            requires=[
                                aid(sid, xid, "collision"),
                                aid(sid, xid, "hazards"),
                                "phase:plat:enemies",
                            ],
                        )
                    )
        for sid, _ts, entries in per_stage:
            for _index, lid, rooms in entries:
                for xid in (lid, *rooms):
                    nodes.append(
                        Node(
                            node_id=aid(sid, xid, "items"),
                            run=level_body(
                                xid, place_level_items,
                                max_items=self.max_items, rules=self.rules,
                                tiles=self.tiles, movement=self.movement,
                                phase_name="plat:item_placement",
                            ),
                            # Items place AFTER enemies (power-ups stage
                            # around them), read reward anchors from
                            # triggers, and validate against the collision
                            # grid; rarity caps read the pool definitions
                            # (whole-pool edges).
                            requires=[
                                aid(sid, xid, "collision"),
                                aid(sid, xid, "hazards"),
                                aid(sid, xid, "triggers"),
                                aid(sid, xid, "entities"),
                                "phase:plat:items",
                            ],
                        )
                    )
        for sid, tileset_aid, entries in per_stage:
            for _index, lid, rooms in entries:
                for xid in (lid, *rooms):
                    nodes.append(
                        Node(
                            node_id=aid(sid, xid, "foreground"),
                            run=level_body(
                                xid, decorate_level,
                                max_decor=self.max_decor,
                                phase_name="plat:decorator",
                            ),
                            requires=[aid(sid, xid, "collision"), tileset_aid],
                        )
                    )
        for sid, _ts, entries in per_stage:
            for _index, lid, rooms in entries:
                for xid in (lid, *rooms):
                    # The per-level manifest descends from EVERY layer step
                    # (LEVEL_STEPS), so any layer edit/regen re-freshens it.
                    nodes.append(
                        Node(
                            node_id=aid(sid, xid, "level"),
                            run=level_body(xid, write_level_manifest),
                            requires=[
                                aid(sid, xid, step) for step in LEVEL_STEPS
                            ],
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
        stages = _stages(ctx)
        if not stages:
            return []

        def render_body(level_id: str):
            def run(c: Any) -> None:
                render_level_review(
                    c, c.bible.levels[level_id], variants=self.variants,
                    graphics=self.graphics,
                )

            return run

        nodes = [
            Node(
                node_id=f"review:{stage.stage_id}/{lid}",
                run=render_body(lid),
                requires=[
                    f"level:{stage.stage_id}/{lid}/{step}"
                    for step in ("terrain", "background", "entities", "foreground")
                ],
                always=True,
            )
            for stage in stages
            for _index, parent, rooms in _stage_level_entries(ctx, stage)
            # Rooms render like levels (per-parent interleave — the
            # sequential RenderPhase iterates bible.levels in exactly this
            # insertion order, and orch==seq is a byte contract).
            for lid in (parent, *rooms)
        ]
        nodes.append(
            Node(
                node_id="review:legend",
                run=lambda c: write_review_legend(c),
                requires=["phase:plat:enemies"],
                always=True,
            )
        )
        return nodes


class VlmQaDagPhase:
    """VLM QA judgment over the review renders — an ``always`` node: it
    re-runs on every orchestration and its body is a no-op unless a judge
    was built from an explicit ``--vlm-backend`` flag. Re-judging is
    STALENESS-AWARE (QA v2): unchanged levels carry their previous
    verdicts from the on-disk report without a VLM call, so a flagged
    resume only pays for what actually changed. Sits between the renders
    it judges and the manifest that ships its warnings."""

    name = "plat:vlm_qa"

    def __init__(
        self,
        judge: Any = None,
        tiles: TileRegistry = DEFAULT_TILES,
        variants: VariantSet = DEFAULT_VARIANTS,
        graphics: Any = None,
    ) -> None:
        from canon.packs.platformer.graphics import DEFAULT_GRAPHICS
        from canon.packs.platformer.vlm_qa import VlmQaPhase

        self._phase = VlmQaPhase(
            judge=judge, tiles=tiles, variants=variants,
            graphics=graphics or DEFAULT_GRAPHICS,
        )

    def expand(self, ctx: Any) -> list[Node]:
        stages = _stages(ctx)
        if not stages:
            return []
        requires = [
            f"review:{stage.stage_id}/{lid}"
            for stage in stages
            for _index, parent, rooms in _stage_level_entries(ctx, stage)
            for lid in (parent, *rooms)
        ]
        requires.append("review:legend")
        return [
            Node(
                node_id="plat:vlm_qa",
                run=self._phase.run,
                requires=requires,
                always=True,
            )
        ]


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
        from canon.packs.platformer.compose import SliceManifestPhase

        self._phase = SliceManifestPhase(
            movement=movement, rules=rules, tiles=tiles, variants=variants,
            graphics=graphics, combat=combat,
        )

    def expand(self, ctx: Any) -> list[Node]:
        stages = _stages(ctx)
        if not stages:
            return []
        entries = {
            stage.stage_id: _stage_level_entries(ctx, stage)
            for stage in stages
        }
        requires = [
            f"level:{stage.stage_id}/{lid}/level"
            for stage in stages
            for _index, parent, rooms in entries[stage.stage_id]
            for lid in (parent, *rooms)
        ]
        requires += [
            f"review:{stage.stage_id}/{lid}"
            for stage in stages
            for _index, parent, rooms in entries[stage.stage_id]
            for lid in (parent, *rooms)
        ]
        requires.append("review:legend")
        # QA verdicts become manifest warnings — the report must be
        # written (or the no-op stamped) before the manifest reads it.
        requires.append("plat:vlm_qa")
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


def macro_phases(num_levels: int = 3, num_enemies: int = 4,
                 num_stages: int = 1, *,
                 num_items: int = 5,
                 tiles: TileRegistry = DEFAULT_TILES,
                 graphics: GraphicsSpec = DEFAULT_GRAPHICS) -> list:
    """The bootstrap prefix: world/stages/style/enemies/items/tilesets as
    legacy nodes (style first — enemy hues avoid every palette's
    hazard/volume hues)."""
    from canon.packs.platformer.style import StyleGuidePhase

    return [
        WorldPhase(num_stages=num_stages),
        StagePhase(num_levels=num_levels, num_enemies=num_enemies),
        StyleGuidePhase(tiles=tiles),
        EnemyGeneratorPhase(count=num_enemies, tiles=tiles),
        ItemGeneratorPhase(count=num_items, tiles=tiles),
        PlaceholderTilesetPhase(tiles=tiles, graphics=graphics),
    ]


def compose_dag_pipeline(
    num_levels: int = 3,
    num_enemies: int = 4,
    num_items: int = 5,
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
    """The full orchestrated pipeline. Per-level nodes expand only when
    the Bible already has a stage plan (see module docstring)."""
    from canon.packs.platformer.art_phases import (
        BackdropArtPhase,
        SpriteAnimationPhase,
        SpriteArtPhase,
        TilesetArtPhase,
        WorldArtPhase,
    )
    from canon.packs.platformer.audio_phases import AudioPhase

    items = [
        *macro_phases(
            num_levels, num_enemies, num_stages,
            num_items=num_items, tiles=tiles, graphics=graphics,
        ),
        LevelStepsDagPhase(
            width=width, height=height, movement=movement, rules=rules,
            tiles=tiles, variants=variants, graphics=graphics, combat=combat,
        ),
        # Art AT THE END: legacy barrier edges make these depend on every
        # level node above and gate the renders below — paid generation
        # never runs before the levels validate.
        TilesetArtPhase(tiles=tiles, producer=image_producer, graphics=graphics),
        SpriteArtPhase(producer=image_producer, graphics=graphics),
        SpriteAnimationPhase(
            producer=image_producer, judge=vlm_judge, graphics=graphics
        ),
        BackdropArtPhase(tiles=tiles, producer=image_producer, graphics=graphics),
        WorldArtPhase(producer=image_producer, graphics=graphics),
        AudioPhase(music_producer=music_producer, sfx_producer=sfx_producer),
        RenderDagPhase(variants=variants, graphics=graphics),
        VlmQaDagPhase(
            judge=vlm_judge, tiles=tiles, variants=variants,
            graphics=graphics,
        ),
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
            for k in (
                "num_levels", "num_enemies", "num_items", "num_stages",
                "tiles", "graphics",
            )
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
    from canon.packs.platformer.models import load_models
    from canon.packs.platformer.prompts import PlatformerPrompts
    from canon.packs.platformer.run_slice import make_fake_responder
    from canon.pipeline.runner import PipelineContext
    from canon.pipeline.stats import GenerationStats
    from canon.pipeline.steplog import StepLog

    seed = os.environ.get("CANON_PLAT_SEED", "emberfall_001")
    out = os.environ.get("CANON_PLAT_OUT", ".")
    stats = GenerationStats(
        llm_backend="fake",
        image_backend=os.environ.get("CANON_PLAT_IMAGE_BACKEND", ""),
        music_backend=os.environ.get("CANON_PLAT_MUSIC_BACKEND", ""),
        sfx_backend=os.environ.get("CANON_PLAT_SFX_BACKEND", ""),
    )
    # CANON_PLAT_MODELS mirrors --models (inert here: FakeLLM never honors
    # per-request models) — wired so the CLI path can't drift from the
    # runner's when a real-backend factory lands.
    models_path = os.environ.get("CANON_PLAT_MODELS")
    model_table = load_models(models_path) if models_path else load_models()
    from canon.packs.platformer.spec import PACK_SPEC

    return PipelineContext(
        bible=bible,
        config=CanonConfig(seed=seed, output_dir=Path(out)),
        rng=random.Random(seed),
        stats=stats,
        llm=LLMClient(
            FakeLLMBackend(make_fake_responder()),
            stats=stats,
            model_resolver=model_table.resolve,
        ),
        prompts=PlatformerPrompts(),
        steplog=StepLog(Path(out)),
        pack_type=PACK_SPEC.pack_type,
    )


def cli_phases_factory(ctx: Any) -> list:
    from canon.packs.platformer.audio_phases import (
        build_music_producer,
        build_sfx_producer,
    )

    # CANON_PLAT_IMAGE_BACKEND: "", "fake", "fal", or "local" — same
    # explicit opt-in as the runner's --image-backend; empty stays on the
    # deterministic placeholder sheet. CANON_PLAT_MUSIC_BACKEND /
    # CANON_PLAT_SFX_BACKEND mirror --music-backend/--sfx-backend (empty =
    # silent). CANON_PLAT_VLM_BACKEND mirrors --vlm-backend (empty = no
    # QA). CANON_PLAT_GRAPHICS: path to a graphics.json (the runner's
    # --graphics; default = pack spec). CANON_PLAT_COMBAT: path to a
    # combat.json (the runner's --combat; default = pack spec).
    from canon.packs.platformer.combat import load_combat
    from canon.packs.platformer.graphics import load_graphics
    from canon.packs.platformer.tileset_art import build_image_producer
    from canon.packs.platformer.vlm_qa import build_vlm_judge

    producer = build_image_producer(
        os.environ.get("CANON_PLAT_IMAGE_BACKEND", ""),
        os.environ.get("CANON_PLAT_IMAGE_MODEL") or None,
        os.environ.get("CANON_PLAT_IMAGE_EDIT_MODEL") or None,
        edit_kind=os.environ.get("CANON_PLAT_IMAGE_EDIT_BACKEND") or None,
    )
    music = build_music_producer(os.environ.get("CANON_PLAT_MUSIC_BACKEND", ""))
    sfx = build_sfx_producer(os.environ.get("CANON_PLAT_SFX_BACKEND", ""))
    vlm = build_vlm_judge(
        os.environ.get("CANON_PLAT_VLM_BACKEND", ""),
        os.environ.get("CANON_PLAT_VLM_MODEL") or None,
    )
    graphics_path = os.environ.get("CANON_PLAT_GRAPHICS")
    graphics = load_graphics(graphics_path) if graphics_path else DEFAULT_GRAPHICS
    combat_path = os.environ.get("CANON_PLAT_COMBAT")
    combat = load_combat(combat_path) if combat_path else DEFAULT_COMBAT
    return compose_dag_pipeline(
        image_producer=producer, graphics=graphics,
        music_producer=music, sfx_producer=sfx, combat=combat,
        vlm_judge=vlm,
    )


# ---------------------------------------------------------------------------
# Field-level regen — "parts of rows" (`canon regen <bible>
# enemy:<id>#flavor --field-ops canon.packs.platformer.dag:regen_field`).
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
    from canon.packs.platformer.phases import llm_json, stamp_provenance

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
        # Label under the plat:enemies prefix: the regen re-authors enemy
        # content, so it must resolve to the SAME tier the enemy task is
        # assigned (and match the plat:enemies provenance stamp below).
        data = llm_json(
            ctx,
            f"plat:enemies:regen:{ident}",
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
        stamp_provenance(ctx, enemy, content_hash, label="plat:enemies")
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
