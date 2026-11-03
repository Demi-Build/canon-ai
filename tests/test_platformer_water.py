"""Water levels (Arc 4, W1): the deterministic topology roll, the
post-placement flood pass, the swim-membership safety check with
waterline-lowering escalation, room interactions (fully-submerged
suppression, dry entrances), and swim-collectible items.

Consumers are untouched in this chunk — total submersion was
recon-verified to work on both surfaces with zero changes."""

from __future__ import annotations

import json
import random
from pathlib import Path

import numpy as np
import pytest

from canon import CanonConfig, FakeLLMBackend, LLMClient, run_pipeline
from canon.bible.models import Bible
from canon.packs.platformer import PlatformerPrompts, compose_pipeline
from canon.packs.platformer.movement import DEFAULT_MOVEMENT
from canon.packs.platformer.run_slice import make_fake_responder
from canon.packs.platformer.sections import (
    WaterLevelConfig,
    load_water_level_config,
    roll_water_level,
)
from canon.packs.platformer.tiles import DEFAULT_TILES
from canon.packs.platformer.validate import (
    check_item_placements,
    flood_grid,
    reachable_cells,
)
from canon.pipeline.rng import derive_rng
from canon.pipeline.runner import PipelineContext

SEED = "emberfall_001"
WATER_ID = DEFAULT_TILES.by_name["water"].id


class TestWaterRoll:
    def test_deterministic_and_biome_steered(self) -> None:
        cfg = WaterLevelConfig(
            biome_odds={"*": 0.5, "sea": 1.0, "desert": 0.0}
        )
        a = roll_water_level("sea", derive_rng(SEED, "p", "l4", "water"), cfg)
        b = roll_water_level("sea", derive_rng(SEED, "p", "l4", "water"), cfg)
        assert a == b and a is not None
        assert (
            roll_water_level("desert", derive_rng(SEED, "p", "l4", "water"), cfg)
            is None
        )

    def test_topologies_and_depth_band(self) -> None:
        cfg = WaterLevelConfig(
            biome_odds={"*": 1.0}, waterline_depth_band=(4, 7)
        )
        seen = set()
        for i in range(60):
            spec = roll_water_level(
                "x", derive_rng(f"s{i}", "p", "l1", "water"), cfg
            )
            assert spec is not None
            seen.add(spec.topology)
            if spec.topology == "waterline":
                assert 4 <= spec.depth <= 7
            else:
                assert spec.depth == 0
        assert seen == {"fully_submerged", "waterline"}

    def test_pack_config_loads(self) -> None:
        cfg = load_water_level_config()
        assert cfg.enabled_stage_min == 2
        assert "*" in cfg.biome_odds


def _island_grid(width: int = 30, height: int = 14):
    """A dry grid with ground floor + a high island only reachable by a
    run-jump from the ground (a flood demotes the takeoff)."""
    grid = np.zeros((height, width), dtype=np.int8)
    grid[height - 2, :] = 1  # floor
    grid[height - 1, :] = 1  # bedrock
    # High island: a platform at row 6 (7+ rows above the standing row).
    grid[6, 20:24] = 1
    return grid


class TestFloodGrid:
    def test_flood_is_a_copy_and_fills_empty_only(self) -> None:
        grid = _island_grid()
        grid[10, 5] = 10  # a hazard on a pillar of air
        flooded, notes = flood_grid(grid, DEFAULT_TILES, "fully_submerged", 0)
        assert flooded is not grid
        assert int((grid == WATER_ID).sum()) == 0  # original untouched
        assert int(flooded[10, 5]) == 10  # hazard persists
        assert int(flooded[0, 0]) == WATER_ID
        # Every empty cell flooded; solids intact.
        assert not (np.asarray(flooded) == 0).any()
        assert notes and "fully submerged" in notes[0]

    def test_waterline_floods_bottom_rows_only(self) -> None:
        grid = _island_grid(height=14)
        flooded, _ = flood_grid(grid, DEFAULT_TILES, "waterline", 5)
        assert int(flooded[14 - 5, 0]) == WATER_ID  # first flooded row
        assert int(flooded[14 - 6, 0]) == 0  # above the line stays dry
        assert int(flooded[6, 20]) == 1  # island solid intact

    def test_fully_submerged_reachability_is_a_superset(self) -> None:
        """Free swim ⊇ the validated dry traversal — the property that
        lets fully_submerged skip the escalation path."""
        grid = _island_grid()
        spawn = (2, 11)
        dry = reachable_cells(grid, spawn, DEFAULT_MOVEMENT, DEFAULT_TILES)
        flooded, _ = flood_grid(grid, DEFAULT_TILES, "fully_submerged", 0)
        wet = reachable_cells(flooded, spawn, DEFAULT_MOVEMENT, DEFAULT_TILES)
        assert dry <= wet


class TestSwimCollectibleItems:
    def test_water_item_accepted_when_swim_reached(self) -> None:
        grid = _island_grid()
        flooded, _ = flood_grid(grid, DEFAULT_TILES, "fully_submerged", 0)
        accepted, problems, _rep = check_item_placements(
            flooded,
            [{"item_id": "coin_a", "x": 10, "y": 3, "source": "trail"}],
            (2, 11),
            {"coin_a": {"kind": "coin", "rarity": "common"}},
            DEFAULT_MOVEMENT,
            tiles=DEFAULT_TILES,
        )
        assert not problems
        assert [(p["x"], p["y"]) for p in accepted] == [(10, 3)]

    def test_boxes_stay_dry(self) -> None:
        grid = _island_grid()
        flooded, _ = flood_grid(grid, DEFAULT_TILES, "fully_submerged", 0)
        accepted, _problems, repairs = check_item_placements(
            flooded,
            [{"item_id": "w", "x": 10, "y": 3, "source": "box"}],
            (2, 11),
            {"w": {"kind": "shield", "rarity": "rare"}},
            DEFAULT_MOVEMENT,
            tiles=DEFAULT_TILES,
        )
        assert not accepted  # a submerged box is dropped (repair note)
        assert repairs


class TestAquaticArchetypes:
    """W2 — reef/trench pool gating + underwater hazard tiles."""

    def test_pool_gating_by_water_topology(self) -> None:
        from canon.packs.platformer.sections import DEFAULT_VOCAB, plan_sections

        assert DEFAULT_VOCAB["reef"].water == "submerged"
        assert DEFAULT_VOCAB["trench"].water == "submerged"
        aquatic = {"reef", "trench"}
        for i in range(30):
            rng = random.Random(i)
            dry = {ps.archetype for ps in plan_sections(100, 20, 2, rng)}
            assert not (dry & aquatic)
            rng = random.Random(i)
            wet = {
                ps.archetype
                for ps in plan_sections(
                    100, 20, 2, rng, water="fully_submerged"
                )
            }
            assert wet <= aquatic  # exclusively aquatic, incl. the opener
        # waterline mixes: across seeds both families appear.
        seen: set[str] = set()
        for i in range(60):
            seen |= {
                ps.archetype
                for ps in plan_sections(
                    100, 20, 2, random.Random(i), water="waterline"
                )
            }
        assert seen & aquatic and (seen - aquatic)

    def test_hazard_tiles_registered_with_damage(self) -> None:
        urchin = DEFAULT_TILES.by_name["urchin"]
        mine = DEFAULT_TILES.by_name["mine"]
        assert urchin.category == "hazard" and urchin.id == 11
        assert mine.category == "hazard" and mine.id == 12
        assert urchin.params["damage"] == 1
        assert mine.params["damage"] == 2

    def test_flooded_levels_use_aquatic_sections_with_hazards(
        self, water_world
    ) -> None:
        from canon.packs.platformer.level import (
            level_section_plan,
            level_water_spec,
        )

        ctx, out = water_world
        aquatic = {"reef", "trench"}
        seen_hazard = False
        for stage in ctx.bible.stages.values():
            for index, lid in enumerate(stage.level_ids):
                spec = level_water_spec(ctx, lid, index)
                level = ctx.bible.levels[lid]
                plan = level_section_plan(ctx, level)
                names = {ps.archetype for ps in plan}
                if spec is not None and spec.topology == "fully_submerged":
                    assert names <= aquatic, (lid, names)
                    with np.load(out / level.collision) as z:
                        g = z["collision"]
                    seen_hazard = seen_hazard or bool(
                        ((g == 11) | (g == 12)).any()
                    )
                elif spec is None:
                    assert not (names & aquatic), (lid, names)
        assert seen_hazard  # urchins/mines actually stamped underwater

    def test_placeholder_tileset_carries_the_new_hazards(
        self, water_world
    ) -> None:
        """Consumers derive HAZARDS + damage from tileset slots — the
        placeholder sheet must carry urchin/mine with their params."""
        ctx, _ = water_world
        tileset = next(iter(ctx.bible.tilesets.values()))
        by_name = {s.name: s for s in tileset.slots}
        assert by_name["urchin"].collision == "hazard"
        assert by_name["urchin"].params.get("damage") == 1
        assert by_name["mine"].params.get("damage") == 2


def _pool_grid(width: int = 30, height: int = 14):
    """Floor + a 3-wide pool: (10..12, 12) water over floor (wet flats),
    plus a DEEP 2-row pool at (16..18): top row water-over-water."""
    grid = np.zeros((height, width), dtype=np.int8)
    grid[height - 2, :] = 1
    grid[height - 1, :] = 1
    for x in (10, 11, 12):
        grid[height - 2, x] = WATER_ID  # shallow: water sits ON bedrock
    for x in (16, 17, 18):
        grid[height - 3, x] = WATER_ID
        grid[height - 2, x] = WATER_ID  # deep: water over water
    return grid


class TestSeabedPolicy:
    """W3 — the "seabed" enemy_water_policy accept/reject matrix."""

    ENEMIES = {
        "beetle": {"archetype": "patroller", "size": 1.0},
        "big_beetle": {"archetype": "patroller", "size": 2.0},
        "fish": {"archetype": "swimmer", "size": 1.0, "swim_style": "within"},
        "cruiser": {"archetype": "swimmer", "size": 1.0, "swim_style": "cruise"},
    }

    def _check(self, placements, policy: str):
        from canon.packs.platformer.rules import GameRules
        from canon.packs.platformer.validate import check_placements

        return check_placements(
            _pool_grid(), placements, (2, 11), self.ENEMIES,
            rules=GameRules(enemy_water_policy=policy), tiles=DEFAULT_TILES,
        )

    def test_wading_post_on_wet_flat_accepted(self) -> None:
        accepted, problems, _ = self._check(
            [{"enemy_id": "beetle", "x": 11, "y": 12}], "seabed"
        )
        assert not problems and len(accepted) == 1
        # The same post is rejected under the old swimmers_only policy.
        _, problems, _ = self._check(
            [{"enemy_id": "beetle", "x": 11, "y": 12}], "swimmers_only"
        )
        assert problems

    def test_deep_water_post_snaps_to_seabed(self) -> None:
        # A wader dropped in deep water is SNAPPED to the nearest submerged
        # flat (code repair, ticket 5a) rather than bounced back to the model.
        from canon.packs.platformer.validate import seabed_cells

        accepted, problems, repairs = self._check(
            [{"enemy_id": "beetle", "x": 17, "y": 11}], "seabed"
        )
        assert not problems and len(accepted) == 1
        assert repairs and "wader snapped" in repairs[0]
        seabed = seabed_cells(_pool_grid(), DEFAULT_TILES)
        assert (accepted[0]["x"], accepted[0]["y"]) in seabed

    def test_wader_with_no_reachable_seabed_still_rejected(self) -> None:
        # Fail-closed: with no submerged flat within reach, the wader still
        # kicks back for a retry — the snap never invents a seabed.
        from canon.packs.platformer.rules import GameRules
        from canon.packs.platformer.validate import check_placements

        grid = np.zeros((8, 24), dtype=np.int8)
        grid[7, :] = WATER_ID  # bottom row all water — no solid floor
        grid[6, 2] = WATER_ID  # a deep-water column at x=2 (no seabed near)
        grid[7, 22] = 1  # the only solid, far away…
        grid[6, 22] = WATER_ID  # …making one wet flat at (22, 6), dist 20 > 8
        _, problems, repairs = check_placements(
            grid, [{"enemy_id": "beetle", "x": 2, "y": 6}], (0, 6),
            self.ENEMIES, rules=GameRules(enemy_water_policy="seabed"),
            tiles=DEFAULT_TILES,
        )
        assert problems and not repairs
        assert "submerged ground" in problems[0]

    def test_swimmers_still_need_water(self) -> None:
        _, problems, _ = self._check(
            [{"enemy_id": "fish", "x": 4, "y": 12}], "seabed"
        )
        assert problems and "swimmers must be placed inside water" in problems[0]

    def test_cruise_placement_uses_the_within_rule(self) -> None:
        accepted, problems, _ = self._check(
            [{"enemy_id": "cruiser", "x": 17, "y": 11}], "seabed"
        )
        assert not problems and len(accepted) == 1

    def test_cruise_is_in_the_style_roll(self) -> None:
        from canon.packs.platformer.phases import SWIM_STYLES

        assert "cruise" in {s for s, _w in SWIM_STYLES}


def _run_world(output_dir: Path) -> PipelineContext:
    ctx = PipelineContext(
        bible=Bible.empty(seed=SEED),
        config=CanonConfig(seed=SEED, output_dir=output_dir),
        rng=random.Random(SEED),
        llm=LLMClient(FakeLLMBackend(make_fake_responder())),
        prompts=PlatformerPrompts(),
    )
    run_pipeline(compose_pipeline(num_stages=3, num_enemies=7), ctx)
    return ctx


@pytest.fixture(scope="module")
def water_world(tmp_path_factory) -> tuple[PipelineContext, Path]:
    out = tmp_path_factory.mktemp("water_world") / "run"
    return _run_world(out), out


def _water_fraction(out: Path, level) -> float:
    with np.load(out / level.collision) as z:
        g = z["collision"]
    return float((g == WATER_ID).sum()) / g.size


class TestWaterEndToEnd:
    def test_default_seed_rolls_both_topologies(self, water_world) -> None:
        """The canned $0 run must exercise the feature: at least one
        fully-submerged and one waterline level."""
        from canon.packs.platformer.level import level_water_spec

        ctx, out = water_world
        topologies = {}
        for stage in ctx.bible.stages.values():
            for index, lid in enumerate(stage.level_ids):
                spec = level_water_spec(ctx, lid, index)
                if spec is not None:
                    topologies[lid] = spec.topology
                    level = ctx.bible.levels[lid]
                    frac = _water_fraction(out, level)
                    if spec.topology == "fully_submerged":
                        assert frac > 0.5, (lid, frac)
                    else:
                        assert 0.05 < frac < 0.5, (lid, frac)
        assert "fully_submerged" in topologies.values()
        assert "waterline" in topologies.values()

    def test_water_never_rolls_on_stage1_or_vertical(self, water_world) -> None:
        from canon.packs.platformer.level import level_water_spec

        ctx, out = water_world
        stage1 = list(ctx.bible.stages.values())[0]
        for index, lid in enumerate(stage1.level_ids):
            assert level_water_spec(ctx, lid, index) is None
            assert _water_fraction(out, ctx.bible.levels[lid]) < 0.1
        for stage in ctx.bible.stages.values():
            for index, lid in enumerate(stage.level_ids):
                if ctx.bible.levels[lid].layout_axis == "vertical":
                    assert level_water_spec(ctx, lid, index) is None

    def test_fully_submerged_suppresses_rooms_at_both_sites(
        self, water_world
    ) -> None:
        """The durable Level.secret_rooms (stamp body's direct roll) and
        the recompute wrapper must agree — the multi-room determinism
        discipline extended to the water gate."""
        from canon.packs.platformer.level import (
            level_secret_rooms,
            level_water_spec,
        )

        ctx, _ = water_world
        suppressed = 0
        for stage in ctx.bible.stages.values():
            for index, lid in enumerate(stage.level_ids):
                level = ctx.bible.levels[lid]
                spec = level_water_spec(ctx, lid, index)
                wrapper_rooms = [
                    s.room_id for s in level_secret_rooms(ctx, lid, index)
                ]
                assert wrapper_rooms == level.secret_rooms
                if spec is not None and spec.topology == "fully_submerged":
                    assert level.secret_rooms == []
                    suppressed += 1
        assert suppressed >= 1  # the canned seed exercises the gate

    def test_rooms_never_flood_and_waterline_entrances_stay_dry(
        self, water_world
    ) -> None:
        from canon.packs.platformer.level import level_water_spec

        ctx, out = water_world
        for stage in ctx.bible.stages.values():
            for index, lid in enumerate(stage.level_ids):
                level = ctx.bible.levels[lid]
                for rid in level.secret_rooms:
                    room = ctx.bible.levels[rid]
                    assert _water_fraction(out, room) < 0.2  # clouds only
                spec = level_water_spec(ctx, lid, index)
                if spec is None or spec.topology != "waterline":
                    continue
                water_row = level.grid_height - spec.depth
                for t in level.triggers:
                    if t.type != "room_entrance":
                        continue
                    assert t.y < water_row
                    assert int(t.params["return_y"]) < water_row

    def test_flooded_levels_reach_exit_and_carry_items(
        self, water_world
    ) -> None:
        from canon.packs.platformer.level import level_water_spec

        ctx, out = water_world
        checked = 0
        for stage in ctx.bible.stages.values():
            for index, lid in enumerate(stage.level_ids):
                spec = level_water_spec(ctx, lid, index)
                if spec is None:
                    continue
                level = ctx.bible.levels[lid]
                with np.load(out / level.collision) as z:
                    g = z["collision"]
                reached = reachable_cells(
                    g, tuple(level.spawn), DEFAULT_MOVEMENT, DEFAULT_TILES
                )
                assert tuple(level.exit) in reached
                for t in level.triggers:
                    if t.type == "checkpoint":
                        assert (t.x, t.y) in reached
                items = json.loads(
                    (out / "level" / stage.stage_id / lid / "items.json")
                    .read_text()
                )
                assert items, f"{lid} shipped without items"
                checked += 1
        assert checked >= 2

    def test_flooded_fauna_flyers_out_waders_on_seabed(
        self, water_world
    ) -> None:
        """Fully-submerged fauna (W3, seabed policy): flyers never (no
        open air), swimmers in water, and LAND enemies allowed — but
        ONLY posted on wet-standable seabed flats (the wading rule)."""
        from canon.packs.platformer.level import level_water_spec
        from canon.packs.platformer.validate import seabed_cells

        ctx, out = water_world
        archetypes = {
            e.enemy_id: e.archetype
            for e in ctx.bible.enemy_definitions.values()
        }
        waders = 0
        for stage in ctx.bible.stages.values():
            for index, lid in enumerate(stage.level_ids):
                spec = level_water_spec(ctx, lid, index)
                if spec is None or spec.topology != "fully_submerged":
                    continue
                level = ctx.bible.levels[lid]
                with np.load(out / level.collision) as z:
                    g = z["collision"]
                seabed = seabed_cells(g, DEFAULT_TILES)
                for p in level.entities:
                    eid = p.ref.split(":", 1)[1]
                    arch = archetypes[eid]
                    assert arch != "flyer", (lid, eid)
                    if arch not in ("swimmer",):
                        assert tuple(p.pos) in seabed, (lid, eid, p.pos)
                        waders += 1
        assert waders >= 1  # the canned run exercises wading
