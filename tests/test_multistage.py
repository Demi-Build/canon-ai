"""Multi-stage worlds M1 — biome stages, global level numbering, the
enemy ecology (world pool + habitat/rarity + swim styles), manifest v2 +
world map layout, and the multi-stage DAG."""

from __future__ import annotations

import hashlib
import json
import math
import random
from pathlib import Path

import numpy as np
import pytest

from canon import CanonConfig, FakeLLMBackend, LLMClient, run_pipeline
from canon.bible.models import Bible
from canon.packs.platformer import PlatformerPrompts, compose_pipeline
from canon.packs.platformer.phases import roll_habitats
from canon.packs.platformer.rules import DEFAULT_RULES
from canon.packs.platformer.run_slice import make_fake_responder
from canon.packs.platformer.validate import check_placements
from canon.pipeline.runner import PipelineContext
from tests.treediff import assert_trees_byte_identical

SEED = "emberfall_001"
STAGES = ("ashen_depths", "bloom_terraces", "frostspire_peaks")


def _run_world(output_dir: Path, num_stages: int = 3, **kwargs) -> PipelineContext:
    ctx = PipelineContext(
        bible=Bible.empty(seed=SEED),
        config=CanonConfig(seed=SEED, output_dir=output_dir),
        rng=random.Random(SEED),
        llm=LLMClient(FakeLLMBackend(make_fake_responder())),
        prompts=PlatformerPrompts(),
    )
    run_pipeline(
        compose_pipeline(num_stages=num_stages, num_enemies=7, **kwargs), ctx
    )
    return ctx


@pytest.fixture(scope="module")
def world(tmp_path_factory) -> tuple[PipelineContext, Path]:
    out = tmp_path_factory.mktemp("world") / "run"
    return _run_world(out), out


class TestWorldPlan:
    def test_stages_biomes_and_linear_edges(self, world) -> None:
        ctx, _ = world
        assert ctx.bible.world.stage_ids == list(STAGES)
        assert ctx.bible.world.edges == [
            ("ashen_depths", "bloom_terraces"),
            ("bloom_terraces", "frostspire_peaks"),
        ]
        assert ctx.bible.world.unlock_rules == {"type": "linear"}
        biomes = [ctx.bible.stages[s].biome for s in STAGES]
        assert biomes == ["caverns", "meadow", "peaks"]

    def test_global_level_numbering(self, world) -> None:
        """Level ids are globally unique across stages — the invariant
        that keeps bible.levels keying, regen addressing, and PLAT_LEVEL
        working unchanged."""
        ctx, _ = world
        assert ctx.bible.stages["ashen_depths"].level_ids == ["l1", "l2", "l3"]
        assert ctx.bible.stages["bloom_terraces"].level_ids == ["l4", "l5", "l6"]
        assert ctx.bible.stages["frostspire_peaks"].level_ids == ["l7", "l8", "l9"]
        # bible.levels = the 9 main-map levels PLUS their secret rooms
        # (multi-room arc) — rooms are Levels linked by parent_level,
        # never members of any stage.level_ids.
        mains = {
            lid for lid, lv in ctx.bible.levels.items() if not lv.parent_level
        }
        assert sorted(mains) == sorted(f"l{i}" for i in range(1, 10))
        for lid, level in ctx.bible.levels.items():
            if level.parent_level:
                assert level.parent_level in mains
                assert lid.startswith(level.parent_level)
        for stage_id in STAGES:
            for lid in ctx.bible.stages[stage_id].level_ids:
                assert ctx.bible.levels[lid].stage_id == stage_id

    def test_difficulty_escalates_across_the_world(self, world) -> None:
        """Stage 2 opens at difficulty 2, the finale stage is all 3s —
        the world ramps, not just each stage internally. Dims are
        difficulty-banded, so widths reflect the ramp."""
        ctx, _ = world
        w = {lid: level.grid_width for lid, level in ctx.bible.levels.items()}
        # difficulty = min(3, stage_number + level_index):
        # stage1 → 1,2,3; stage2 → 2,3,3; stage3 → 3,3,3
        assert w["l1"] < w["l3"]  # diff 1 vs 3 bands
        assert w["l7"] >= 64 and w["l8"] >= 64 and w["l9"] >= 64  # all diff 3

    def test_per_stage_palettes_differ(self, world) -> None:
        ctx, _ = world
        backgrounds = {
            sid: ctx.bible.tilesets[sid].palette["background"]
            for sid in STAGES
        }
        assert len(set(backgrounds.values())) == 3, backgrounds


class TestEcology:
    def test_pool_is_global_and_rosters_filter_by_biome(self, world) -> None:
        ctx, _ = world
        assert len(ctx.bible.enemy_definitions) == 7
        for stage_id in STAGES:
            stage = ctx.bible.stages[stage_id]
            assert len(stage.enemy_refs) >= 3
            for ref in stage.enemy_refs:
                enemy = ctx.bible.enemy_definitions[ref.split(":", 1)[1]]
                assert enemy.habitats == ["*"] or stage.biome in enemy.habitats

    def test_worldwide_commons_exist_and_rares_are_bound(self, world) -> None:
        ctx, _ = world
        pool = list(ctx.bible.enemy_definitions.values())
        assert any(e.habitats == ["*"] for e in pool), "no worldwide enemies"
        for enemy in pool:
            assert enemy.rarity in ("common", "uncommon", "rare")
            if enemy.rarity == "rare":
                assert enemy.habitats != ["*"] and len(enemy.habitats) == 1

    def test_every_biome_has_a_guaranteed_native(self, world) -> None:
        """The first M pool slots bind one creature per biome — no biome
        plays another biome's roster wholesale (first-real-run finding:
        forest and ruins were identical meadow critters)."""
        ctx, _ = world
        pool = list(ctx.bible.enemy_definitions.values())
        for stage_id in STAGES:
            biome = ctx.bible.stages[stage_id].biome
            assert any(e.habitats == [biome] for e in pool), (
                f"no native for {biome}"
            )
        # And the guaranteed natives make stage rosters DIFFER.
        rosters = {
            sid: tuple(sorted(ctx.bible.stages[sid].enemy_refs))
            for sid in STAGES
        }
        assert len(set(rosters.values())) > 1, rosters

    def test_placements_come_from_the_stage_roster(self, world) -> None:
        ctx, _ = world
        for stage_id in STAGES:
            stage = ctx.bible.stages[stage_id]
            allowed = set(stage.enemy_refs)
            for lid in stage.level_ids:
                for placement in ctx.bible.levels[lid].entities:
                    assert placement.ref in allowed, (
                        f"{placement.ref} placed in {lid} but not in "
                        f"{stage_id}'s roster"
                    )

    def test_rarity_caps_hold_on_every_level(self, world) -> None:
        ctx, _ = world
        caps = DEFAULT_RULES.rarity_caps
        for lid, level in ctx.bible.levels.items():
            counts: dict[str, int] = {}
            for placement in level.entities:
                enemy = ctx.bible.enemy_definitions[
                    placement.ref.split(":", 1)[1]
                ]
                counts[enemy.rarity] = counts.get(enemy.rarity, 0) + 1
            for tier, cap in caps.items():
                assert counts.get(tier, 0) <= cap, (lid, counts)

    def test_roll_habitats_contract(self) -> None:
        biomes = ["caverns", "meadow", "peaks"]
        rng = random.Random("x")
        for _ in range(50):
            assert roll_habitats("rare", biomes, rng) != ["*"]
        assert roll_habitats("common", [], rng) == ["*"]  # single-biome world
        for rarity in ("common", "uncommon", "rare"):
            picks = roll_habitats(rarity, biomes, random.Random("y"))
            assert picks == ["*"] or set(picks) <= set(biomes)

    def test_habitat_repair_widens_thin_rosters(self, tmp_path: Path) -> None:
        """A biome the pool never rolled residents for gets the nearest
        enemies' habitats widened — loud code repair, never a re-roll."""
        from canon.packs.platformer.phases import EnemyGeneratorPhase

        ctx = _run_world(tmp_path / "run", num_stages=3)
        stage = ctx.bible.stages["bloom_terraces"]
        # Rebuild rosters after artificially binding everyone elsewhere.
        for enemy in ctx.bible.enemy_definitions.values():
            enemy.habitats = ["peaks"]
        stage.enemy_refs = []
        phase = EnemyGeneratorPhase(count=7)
        ctx.artifacts.setdefault("slice_warnings", []).clear()
        phase._assign_stage_rosters(ctx, [stage])
        assert len(stage.enemy_refs) >= phase.MIN_STAGE_ROSTER
        assert any(
            "ecology:" in w and "widened" in w
            for w in ctx.artifacts["slice_warnings"]
        )


class TestSwimStyles:
    GRID_W, GRID_H = 12, 8

    def _grid(self):
        """floor with a 4-wide, 2-deep raised basin at x 4-7: walls form
        the lip, water fills rows 4-5 (surface row = 4)."""
        from canon.packs.platformer.dsl import stamp

        text = (
            "floor(0,11)\nwall(3,3,5)\nvolume(water,4,7,4)\nwall(8,3,5)\n"
            "spawn(0)\nexit(11)"
        )
        return stamp(text, self.GRID_W, self.GRID_H).grid

    def _check(self, grid, x, y, swim_style):
        enemies = {
            "fish": {
                "archetype": "swimmer", "size": 1.0,
                "swim_style": swim_style, "rarity": "common",
            }
        }
        accepted, problems, _ = check_placements(
            grid, [{"enemy_id": "fish", "x": x, "y": y}], (0, 4), enemies,
        )
        return accepted, problems

    def test_surface_swimmer_needs_the_top_row(self) -> None:
        grid = self._grid()
        accepted, _ = self._check(grid, 5, 4, "surface")  # water top row
        assert accepted
        _, problems = self._check(grid, 5, 5, "surface")  # submerged
        assert problems and "SURFACE swimmer" in problems[0]

    def test_floater_needs_a_2x2_pocket(self) -> None:
        grid = self._grid()
        accepted, _ = self._check(grid, 5, 5, "float")  # inside the basin
        assert accepted

    def test_floater_rejected_in_shallow_strip(self) -> None:
        from canon.packs.platformer.dsl import stamp

        text = "floor(0,11)\npool(water,4,7)\nspawn(0)\nexit(11)"
        grid = stamp(text, self.GRID_W, self.GRID_H).grid  # 1-deep pool
        _, problems = self._check(grid, 5, self.GRID_H - 2, "float")
        assert problems and "FLOATING swimmer" in problems[0]
        accepted, _ = self._check(grid, 5, self.GRID_H - 2, "within")
        assert accepted  # the classic style still fits a shallow pool

    def test_env_prefilter_matches_placement_rules(self) -> None:
        """swimmer_spot_exists is the roster gate: a 1-deep puddle can't
        hold a 1.5 body (the sunlit run's wasted-retries class), a dry
        level holds no swimmer at all, and the deep basin holds all
        three styles it validated for placement."""
        from canon.packs.platformer.dsl import stamp
        from canon.packs.platformer.validate import swimmer_spot_exists

        shallow = stamp(
            "floor(0,11)\npool(water,4,7)\nspawn(0)\nexit(11)",
            self.GRID_W, self.GRID_H,
        ).grid
        assert swimmer_spot_exists(shallow, 1.0, "within")
        assert not swimmer_spot_exists(shallow, 1.5, "within")
        assert not swimmer_spot_exists(shallow, 1.0, "float")
        deep = self._grid()
        assert swimmer_spot_exists(deep, 1.5, "within")
        assert swimmer_spot_exists(deep, 1.0, "float")
        assert swimmer_spot_exists(deep, 1.0, "surface")
        dry = stamp(
            "floor(0,11)\nspawn(0)\nexit(11)", self.GRID_W, self.GRID_H
        ).grid
        assert not swimmer_spot_exists(dry, 1.0, "within")

    def test_pygame_float_parity_bounces_inside_water(self, tmp_path: Path) -> None:
        """The float drift must stay inside the water body — same
        invariant the validator placed against (mechanics parity is
        checked in code here; main.gd mirrors the same arithmetic)."""
        grid = self._grid()
        volumes = {
            (x, y)
            for y in range(self.GRID_H)
            for x in range(self.GRID_W)
            if int(grid[y, x]) == 20
        }
        x, y, dx, dy, speed = 5.0, 5.0, 1.0, 1.0, 2.0
        for _ in range(400):  # simulate the pygame/main.gd stepping
            step = speed * 0.7 * (1 / 60)
            nx = x + dx * step
            if abs(nx - 5.0) >= 4 or (int(nx), int(y)) not in volumes:
                dx *= -1.0
            else:
                x = nx
            ny = y + dy * step
            if (int(x), int(ny)) not in volumes:
                dy *= -1.0
            else:
                y = ny
            assert (int(x), int(y)) in volumes


def _in_sight_sim(archetype, facing, rel_x, rel_y, aggro_range, sight):
    """Faithful copy of the consumers' `_in_sight` (main.gd /
    platformer_play.py) — the eyesight-cone gate the parity sims below
    share with both play surfaces."""
    if math.hypot(rel_x, rel_y) > aggro_range:
        return False
    cfg = sight.get(archetype, {})
    fov = str(cfg.get("fov", "omni"))
    if fov == "none":
        return False
    if fov == "omni":
        return True
    if rel_x * facing < 0:
        return False
    if fov == "forward":
        return abs(rel_y) <= float(cfg.get("vband", 2))
    return True


def _ground_aggro_step(st, behavior, sight, player, speed, chase_mult, dt):
    """One step of the consumers' aggressive-PATROLLER branch on an open
    field (no walls): FOV-gated detection, an `alerted` lock that commits by
    RANGE until the tether snaps, chase at chase_mult speed, else return /
    patrol. Mutates st = {x, home_x, y, dir, alerted}; returns the mode."""
    aggro = float(behavior.get("aggro_range", 0) or 0)
    leash = float(behavior.get("leash_range", 0) or 0)
    patrol = float(behavior.get("patrol_range", 4))
    px, py = player
    rel_x, rel_y = px - st["x"], py - st.get("y", 0.0)
    home_dist = abs(st["x"] - st["home_x"])
    if st["alerted"]:
        if math.hypot(rel_x, rel_y) > aggro or (leash > 0 and home_dist >= leash):
            st["alerted"] = False
    elif aggro > 0 and _in_sight_sim(
        "patroller", st["dir"], rel_x, rel_y, aggro, sight
    ):
        st["alerted"] = True
    if st["alerted"]:
        mode = "chase"
    elif home_dist > patrol:
        mode = "return"
    else:
        mode = "patrol"
    if mode == "chase":
        step = speed * chase_mult * dt
        d = 1.0 if px > st["x"] else (-1.0 if px < st["x"] else 0.0)
        if d != 0.0:
            st["dir"] = d
        st["x"] = px if abs(px - st["x"]) < step else st["x"] + d * step
    elif mode == "return":
        step = speed * dt
        d = 1.0 if st["home_x"] > st["x"] else (-1.0 if st["home_x"] < st["x"] else 0.0)
        if d != 0.0:
            st["dir"] = d
        st["x"] = (
            st["home_x"] if abs(st["home_x"] - st["x"]) < step else st["x"] + d * step
        )
    else:
        step = speed * dt
        nx = st["x"] + st["dir"] * step
        if abs(nx - st["home_x"]) >= patrol:
            st["dir"] *= -1.0
        else:
            st["x"] = nx
    return mode


class TestBehaviorDoctrine:
    """Aggro is an ORTHOGONAL behavior tier (not a unique archetype): FOV-
    gated, leashed pursuit that composes with any locomotion. Tracks-first,
    no-hazard-entry doctrine holds. The sims here ARE the consumer branch
    logic both play surfaces mirror (same style as the jump-physics parity
    test); the frame captures cross-check Godot against pygame."""

    #: Default game FOV config (rules.enemy_sight): ground sees a forward
    #: cone within vband rows; swimmers are omnidirectional; sentries blind.
    SIGHT = {
        "patroller": {"fov": "forward", "vband": 2},
        "swimmer": {"fov": "omni"},
        "sentry": {"fov": "none"},
    }

    def test_passive_never_chases(self) -> None:
        """aggro_range 0 (passive tier) — patrols its beat and never breaks
        off toward the player, however close."""
        st = {"x": 10.0, "home_x": 10.0, "y": 7.0, "dir": 1.0, "alerted": False}
        behavior = {"aggro_range": 0, "leash_range": 0, "patrol_range": 4}
        for _ in range(600):
            _ground_aggro_step(st, behavior, self.SIGHT, (11.0, 7.0), 2.0, 1.5, 1 / 60)
            assert not st["alerted"]
        assert abs(st["x"] - 10.0) <= 4.0 + 0.1  # stayed on the beat

    def test_ground_fov_only_sees_in_front_at_its_level(self) -> None:
        """A ground walker's 'forward' cone: ignores a player behind it or
        well above/below, alerts only on one in front, in band, in range —
        while a swimmer (omni) sees the same player behind it."""
        s = self.SIGHT
        assert not _in_sight_sim("patroller", 1.0, -3.0, 0.0, 8.0, s)  # behind
        assert not _in_sight_sim("patroller", 1.0, 3.0, -5.0, 8.0, s)  # too high
        assert _in_sight_sim("patroller", 1.0, 3.0, 1.0, 8.0, s)  # in the cone
        assert not _in_sight_sim("patroller", 1.0, 9.0, 0.0, 8.0, s)  # out of range
        assert _in_sight_sim("swimmer", 1.0, -3.0, 0.0, 8.0, s)  # omni sees behind

    def test_aggressive_leashes_and_returns_to_beat(self) -> None:
        """The dissolved 'chaser' = patroller + aggressive: spots the player
        in its forward cone, chases, never strays past its tether, and walks
        home to resume patrol once the player is gone."""
        behavior = {"aggro_range": 12.0, "leash_range": 6.0, "patrol_range": 4}
        st = {"x": 10.0, "home_x": 10.0, "y": 7.0, "dir": 1.0, "alerted": False}
        for _ in range(200):  # player far right, out of range: just patrols
            _ground_aggro_step(st, behavior, self.SIGHT, (40.0, 7.0), 2.0, 1.5, 1 / 60)
        assert not st["alerted"] and abs(st["x"] - 10.0) <= 4.0 + 0.1
        max_reach = st["x"]
        for _ in range(1200):  # player in the cone: chase, never past leash
            _ground_aggro_step(st, behavior, self.SIGHT, (18.0, 7.0), 2.0, 1.5, 1 / 60)
            max_reach = max(max_reach, st["x"])
            assert st["x"] - st["home_x"] <= behavior["leash_range"] + 0.5
        assert max_reach > 12.0  # it gave chase well past its home beat
        for _ in range(1200):  # player leaves: un-alert, walk home, patrol
            _ground_aggro_step(st, behavior, self.SIGHT, (80.0, 7.0), 2.0, 1.5, 1 / 60)
        assert not st["alerted"] and abs(st["x"] - 10.0) <= 4.0 + 0.1

    def test_hunter_has_no_tether(self) -> None:
        """leash_range <= 0 (hunter / the relentless variant) chases across
        the whole map — never breaks off while the player stays in sight."""
        behavior = {"aggro_range": 30.0, "leash_range": 0, "patrol_range": 4}
        st = {"x": 10.0, "home_x": 10.0, "y": 7.0, "dir": 1.0, "alerted": False}
        for _ in range(2000):
            _ground_aggro_step(st, behavior, self.SIGHT, (34.0, 7.0), 3.0, 1.5, 1 / 60)
        assert st["alerted"] and st["x"] > 30.0  # ran far past any beat

    def test_aggressive_swimmer_pursues_in_2d_within_water(self) -> None:
        """An aggressive swimmer (omni sight) chases in X AND Y toward the
        player but never leaves the water volume — mirrors _swim_toward's
        per-axis in-water occupancy gate."""
        def in_water(x, y):
            return 4 <= x <= 12 and 4 <= y <= 10

        x, y = 6.0, 6.0
        px, py = 20.0, 20.0  # player outside the box, down-right
        aggro, speed, chase_mult, dt = 30.0, 2.0, 1.5, 1 / 60
        alerted = False
        for _ in range(2000):
            if not alerted and math.hypot(px - x, py - y) <= aggro:
                alerted = True  # omni: no facing gate
            step = speed * chase_mult * dt
            dx = 1.0 if px > x else (-1.0 if px < x else 0.0)
            nx = px if abs(px - x) < step else x + dx * step
            if in_water(nx, y):
                x = nx
            dy = 1.0 if py > y else (-1.0 if py < y else 0.0)
            ny = py if abs(py - y) < step else y + dy * step
            if in_water(x, ny):
                y = ny
            assert in_water(x, y)  # never left the volume
        assert alerted and x >= 11.0 and y >= 9.0  # pressed to the near corner

    def test_relentless_variant_overrides_the_leash(self) -> None:
        from canon.packs.platformer.variants import load_variants

        relentless = load_variants().by_name["relentless"]
        assert float(relentless.behavior["leash_range"]) >= 999
        assert float(relentless.behavior["aggro_range"]) >= 99
        # And it is capped to one per level in the default rules.
        assert DEFAULT_RULES.variant_caps.get("relentless") == 1

    def test_enemies_never_enter_hazards_or_solids(self) -> None:
        """The can-occupy rule both surfaces share: hazard cells and
        solid/one-way cells block; a patroller reverses at a spike strip
        instead of strolling into it."""
        from canon.packs.platformer.dsl import stamp
        from canon.packs.platformer.tiles import DEFAULT_TILES

        result = stamp(
            "floor(0,23)\nhazard_strip(spike,12,14)\nspawn(2)\nexit(22)",
            24, 10,
        )
        grid = result.grid
        hazards = DEFAULT_TILES.ids("hazard")
        blocking = DEFAULT_TILES.ids("solid")
        one_way = DEFAULT_TILES.ids("one_way")

        def can_occupy(x: float, y: float) -> bool:
            cell = int(grid[int(y), int(x)])
            below = int(grid[int(y) + 1, int(x)])
            if cell in hazards:
                return False
            if cell in blocking or cell in one_way:
                return False
            return below in blocking or below in one_way

        # Patrol walk from x=8 rightward: reverses before the spikes.
        x, direction, y = 8.0, 1.0, 10 - 3
        for _ in range(600):
            nx = x + direction * 2.0 * (1 / 60)
            if not can_occupy(nx, y):
                direction *= -1.0
            else:
                x = nx
        assert x < 12.0  # never entered the strip


def _flyer_step(st, behavior, sight, fcfg, player, speed, chase_mult, dt):
    """One step of the consumers' flyer branch on an open sky (no occupancy):
    the shared aggro decision, then chase (2D swoop) / return / hover
    (bob + scan-sway), or passive x-patrol + periodic dive when aggro_range is
    0. Mutates st = {x, y, home_x, home_y, dir, alerted, bob_t}; returns the
    mode. Faithful to main.gd / platformer_play.py."""
    aggro = float(behavior.get("aggro_range", 0) or 0)
    leash = float(behavior.get("leash_range", 0) or 0)
    patrol = float(behavior.get("patrol_range", 4))
    px, py = player
    rel_x, rel_y = px - st["x"], py - st["y"]
    home_dist = abs(st["x"] - st["home_x"])  # flyer territory is HORIZONTAL
    if aggro > 0:
        if st["alerted"]:
            if math.hypot(rel_x, rel_y) > aggro or (leash > 0 and home_dist >= leash):
                st["alerted"] = False
        elif _in_sight_sim("flyer", st["dir"], rel_x, rel_y, aggro, sight):
            st["alerted"] = True
        mode = (
            "chase" if st["alerted"] else ("return" if home_dist > patrol else "patrol")
        )
    else:
        mode = "patrol"
    # Flyer clocks advance every frame (deterministic; matches the consumers).
    st["bob_t"] += dt
    st["swoop_t"] += dt
    bob = math.sin(st["bob_t"] * fcfg["hover_freq"]) * fcfg["hover_amp"]
    if mode == "chase":  # dive-bomb: committed plunge then recover on the plane
        period, dur = fcfg["swoop_period"], fcfg["swoop_duration"]
        phase = math.fmod(st["swoop_t"], period)
        if phase < dur:  # committed dive
            u = phase / dur
            st["x"] += st["swoop_dir"] * speed * chase_mult * dt
            st["y"] = st["home_y"] + st["swoop_dep"] * 4.0 * u * (1.0 - u)
            if st["swoop_dir"] != 0.0:
                st["dir"] = st["swoop_dir"]
        else:  # recover on the plane: track player at altitude, aim next dive
            st["swoop_dir"] = 1.0 if px > st["x"] else (-1.0 if px < st["x"] else 0.0)
            st["swoop_dep"] = max(0.0, py - st["home_y"])
            step = speed * dt
            st["x"] = px if abs(px - st["x"]) < step else st["x"] + st["swoop_dir"] * step
            st["y"] = st["home_y"] + bob
            if px != st["x"]:
                st["dir"] = 1.0 if px > st["x"] else -1.0
    elif mode == "return":
        step = speed * dt
        dx = 1.0 if st["home_x"] > st["x"] else (-1.0 if st["home_x"] < st["x"] else 0.0)
        if dx != 0.0:
            st["dir"] = dx
        st["x"] = (
            st["home_x"] if abs(st["home_x"] - st["x"]) < step else st["x"] + dx * step
        )
        dy = 1.0 if st["home_y"] > st["y"] else (-1.0 if st["home_y"] < st["y"] else 0.0)
        st["y"] = (
            st["home_y"] if abs(st["home_y"] - st["y"]) < step else st["y"] + dy * step
        )
    elif aggro > 0:  # hover: vertical bob + scanning sway (drifts back into zone)
        nx = st["x"] + st["dir"] * fcfg["sway_speed"] * dt
        if nx > st["home_x"] + fcfg["hover_sway"]:
            st["dir"] = -1.0
        elif nx < st["home_x"] - fcfg["hover_sway"]:
            st["dir"] = 1.0
        st["x"] += st["dir"] * fcfg["sway_speed"] * dt
        st["y"] = st["home_y"] + bob
    else:  # passive: x-patrol at altitude + periodic ambient dive
        nx = st["x"] + st["dir"] * speed * dt
        if abs(nx - st["home_x"]) >= patrol:
            st["dir"] *= -1.0
        else:
            st["x"] = nx
        phase = math.fmod(st["swoop_t"], fcfg["swoop_period"])
        dip = (
            fcfg["swoop_depth"] * math.sin(math.pi * phase / fcfg["swoop_duration"])
            if phase < fcfg["swoop_duration"]
            else 0.0
        )
        st["y"] = st["home_y"] + dip
    return mode


class TestFlyer:
    """Flyer locomotion (airborne) + its two flight styles: env feasibility,
    airborne placement, and the hover/swoop/return + patrol/dive movement both
    play surfaces mirror. Aggro composes on top exactly as for ground/water."""

    GRID_W, GRID_H = 16, 12
    SIGHT = {"flyer": {"fov": "hemisphere"}}
    FCFG = {
        "hover_amp": 0.4, "hover_freq": 3.0, "hover_sway": 2.0, "sway_speed": 1.5,
        "swoop_period": 3.0, "swoop_duration": 1.0, "swoop_depth": 3.0,
    }

    def _open_grid(self):
        from canon.packs.platformer.dsl import stamp

        return stamp(
            "floor(0,15)\nspawn(0)\nexit(15)", self.GRID_W, self.GRID_H
        ).grid

    def test_flyer_spot_exists_needs_airspace_over_ground(self) -> None:
        """flyer_spot_exists is the roster gate: open airspace over ground is
        feasible; a fully-solid level (no air) and a groundless void (no
        terrain below) are not."""
        from canon.packs.platformer.validate import flyer_spot_exists

        g = self._open_grid()
        assert flyer_spot_exists(g, 1.0)
        assert flyer_spot_exists(g, 2.0)
        assert not flyer_spot_exists(np.ones((self.GRID_H, self.GRID_W), int), 1.0)
        assert not flyer_spot_exists(np.zeros((self.GRID_H, self.GRID_W), int), 1.0)

    def test_flyer_placed_aloft_not_on_the_ground(self) -> None:
        """A flyer validates in open air above the terrain and is REJECTED on
        the ground surface (an airborne creature never stands)."""
        from canon.packs.platformer.validate import standable_cells

        g = self._open_grid()
        surf = min(y for _, y in standable_cells(g))
        col = self.GRID_W // 2
        enemies = {"bat": {"archetype": "flyer", "size": 1.0, "rarity": "common"}}

        def _check(x, y):
            return check_placements(
                g, [{"enemy_id": "bat", "x": x, "y": y}], (0, surf), enemies
            )

        accepted, _, _ = _check(col, surf - 3)  # aloft in open air
        assert accepted
        _, problems, _ = _check(col, surf)  # on the ground surface
        assert problems and "AIRBORNE" in problems[0]

    def test_aggressive_flyer_dive_bombs_and_returns_to_plane(self) -> None:
        """The hunt-from-above flyer: hovers on its altitude plane until it
        spots the player, then DIVE-BOMBS in parabolic plunges that dip toward
        the player and CLIMB BACK to the plane (never descending to a
        ground-chase); horizontal reach is leash-bounded; returns home when the
        player is gone."""
        behavior = {"aggro_range": 12.0, "leash_range": 5.0, "patrol_range": 3}
        st = {
            "x": 20.0, "y": 6.0, "home_x": 20.0, "home_y": 6.0, "dir": 1.0,
            "alerted": False, "bob_t": 0.0, "swoop_t": 0.0,
            "swoop_dir": 1.0, "swoop_dep": 0.0,
        }
        for _ in range(300):  # player far: hover on the plane, never alert
            _flyer_step(st, behavior, self.SIGHT, self.FCFG, (40.0, 6.0), 2.0, 1.5, 1 / 60)
            assert not st["alerted"]
            assert abs(st["y"] - 6.0) <= self.FCFG["hover_amp"] + 1e-6
        max_dip = max_hx = 0.0
        back_on_plane = alerted_ever = False
        for _ in range(1800):  # player in range below: dive-bomb, leash-bound
            _flyer_step(st, behavior, self.SIGHT, self.FCFG, (16.0, 12.0), 2.0, 1.5, 1 / 60)
            max_dip = max(max_dip, st["y"] - 6.0)
            max_hx = max(max_hx, abs(st["x"] - 20.0))
            alerted_ever = alerted_ever or st["alerted"]
            if st["alerted"] and abs(st["y"] - 6.0) <= 0.5:
                back_on_plane = True  # climbed back to the plane mid-engagement
            assert abs(st["x"] - 20.0) <= behavior["leash_range"] + 0.5  # H tether
        assert alerted_ever
        assert max_dip >= 3.0  # it DID dive down toward the player
        assert back_on_plane  # and returned to its plane (never stuck at ground)
        assert max_hx > behavior["patrol_range"]  # hunted horizontally past its beat
        for _ in range(1500):  # player gone: un-alert, return home, hover
            _flyer_step(st, behavior, self.SIGHT, self.FCFG, (80.0, 6.0), 2.0, 1.5, 1 / 60)
        assert not st["alerted"]
        assert abs(st["x"] - 20.0) <= self.FCFG["hover_sway"] + 0.3
        assert abs(st["y"] - 6.0) <= self.FCFG["hover_amp"] + 0.1  # back on the plane

    def test_passive_flyer_patrols_and_dives(self) -> None:
        """The patrol+swoop flyer: patrols horizontally at altitude and dives
        on a periodic ambient swoop, and NEVER targets the player."""
        behavior = {"aggro_range": 0, "leash_range": 0, "patrol_range": 4}
        st = {
            "x": 20.0, "y": 6.0, "home_x": 20.0, "home_y": 6.0, "dir": 1.0,
            "alerted": False, "bob_t": 0.0, "swoop_t": 0.0,
            "swoop_dir": 1.0, "swoop_dep": 0.0,
        }
        min_y = max_y = 6.0
        for _ in range(600):
            _flyer_step(st, behavior, self.SIGHT, self.FCFG, (20.0, 11.0), 2.0, 1.5, 1 / 60)
            assert not st["alerted"]  # passive: no aggro, ever
            assert abs(st["x"] - 20.0) <= behavior["patrol_range"] + 0.1
            min_y, max_y = min(min_y, st["y"]), max(max_y, st["y"])
        assert min_y == pytest.approx(6.0, abs=0.01)  # altitude is the dive's top
        assert max_y >= 6.0 + self.FCFG["swoop_depth"] - 0.2  # it DID dive


def _hopper_step(st, behavior, grid, gravity, player_x, speed, dt, mode="patrol"):
    """One step of the consumers' HOPPER branch (the 4th mirror beside
    platformer_play.py and main.gd): grounded = the hop clock ticks and
    launches at cadence (ballistic, hop_height + landing margin);
    airborne = gravity + X drift under the AIRBORNE occupancy mode
    (solids/one-ways/volumes flip it, hazards pass, no footing) +
    ANCHOR-ONLY landing on support below; off the world = dead.
    Mutates st = {x, y, dir, vy, hop_t, grounded, alive}."""
    h, w = grid.shape
    block, oneway, vol = {1, 3, 4, 5}, {2}, {20}

    def tile(x, y):
        xi, yi = int(x), int(y)
        return int(grid[yi, xi]) if 0 <= xi < w and 0 <= yi < h else 0

    hop_h = float(behavior.get("hop_height", 2))
    period = float(behavior.get("hop_period_s", 1.0))
    if st["grounded"]:
        st["hop_t"] += dt
        if mode == "chase":
            st["dir"] = 1.0 if player_x >= st["x"] else -1.0
        if st["hop_t"] >= period:
            st["hop_t"] = 0.0
            st["vy"] = -math.sqrt(2.0 * gravity * (hop_h + 0.25))
            st["grounded"] = False
    else:
        # 1e-3 lattice quantization (both consumers): tile-boundary
        # decisions agree across float32/float64 surfaces.
        nx = round(st["x"] + st["dir"] * speed * dt, 3)
        c = tile(nx, st["y"])
        if not (c in block or c in oneway or c in vol):
            st["x"] = nx
        else:
            st["dir"] *= -1.0
        st["vy"] += gravity * dt
        ny = round(st["y"] + st["vy"] * dt, 3)
        below = tile(st["x"], ny + 1.0)
        if st["vy"] < 0 and tile(st["x"], ny) in block:
            st["vy"] = 0.0
        elif st["vy"] > 0 and (below in block or below in oneway):
            target = float(int(ny + 1.0) - 1)
            if ny >= target:
                st["y"], st["vy"], st["grounded"] = target, 0.0, True
            else:
                st["y"] = ny
        else:
            st["y"] = ny
        if st["y"] > h + 2:
            st["alive"] = False


class TestHopperBehavior:
    """The hopper locomotion (combat/level-picks arc) via the faithful
    stepper re-implementation — launch cadence, arc height, gap
    crossing under the airborne occupancy mode, ceiling bonk."""

    def _fresh(self, x=6.0, y=10.0):
        return {
            "x": x, "y": y, "dir": 1.0, "vy": 0.0, "hop_t": 0.0,
            "grounded": True, "alive": True,
        }

    def _flat(self, width=30, height=14):
        grid = np.zeros((height, width), dtype=np.int8)
        grid[12, :] = 1
        grid[13, :] = 1
        return grid

    def test_hop_cycle_launches_arcs_and_lands(self) -> None:
        grid = self._flat()
        behavior = {"hop_height": 2, "hop_period_s": 1.0}
        st = self._fresh(y=11.0)
        min_y = 11.0
        landings = 0
        was_grounded = True
        for _ in range(600):  # 10s: multiple full cycles
            _hopper_step(st, behavior, grid, 40.0, 0.0, 2.0, 1 / 60)
            min_y = min(min_y, st["y"])
            if st["grounded"] and not was_grounded:
                landings += 1
                assert st["y"] == 11.0  # anchor-only snap to the floor row
            was_grounded = st["grounded"]
        assert st["alive"]
        assert landings >= 3
        assert min_y <= 11.0 - 1.8  # the arc actually rose ~hop_height

    def test_hopper_crosses_a_gap_a_walker_cannot(self) -> None:
        grid = self._flat()
        grid[12, 10] = 0
        grid[13, 10] = 0  # a 1-wide bottomless gap
        behavior = {"hop_height": 2, "hop_period_s": 0.8}
        st = self._fresh(x=7.0, y=11.0)
        crossed = False
        for _ in range(2400):
            _hopper_step(st, behavior, grid, 40.0, 0.0, 2.0, 1 / 60)
            if not st["alive"]:
                break
            if st["x"] > 11.5 and st["grounded"]:
                crossed = True
                break
        assert st["alive"] and crossed

    def test_ceiling_bonk_stops_the_rise(self) -> None:
        grid = self._flat()
        grid[9, :] = 1  # a ceiling 2 rows above the standing row
        behavior = {"hop_height": 3, "hop_period_s": 0.5}
        st = self._fresh(y=11.0)
        min_y = 11.0
        for _ in range(300):
            _hopper_step(st, behavior, grid, 40.0, 0.0, 2.0, 1 / 60)
            min_y = min(min_y, st["y"])
        assert st["alive"]
        assert min_y >= 9.5  # never clipped through the ceiling row


class TestManifestV2:
    def test_stages_levels_and_display_names(self, world) -> None:
        _, out = world
        manifest = json.loads((out / "manifest.json").read_text())
        assert [s["stage_id"] for s in manifest["stages"]] == list(STAGES)
        assert manifest["levels"] == [f"l{i}" for i in range(1, 10)]
        nodes = manifest["world_map"]["nodes"]
        assert [n["display_name"] for n in nodes] == [
            "1-1", "1-2", "1-3", "2-1", "2-2", "2-3", "3-1", "3-2", "3-3",
        ]
        # Linear path, monotone x, positions normalized.
        xs = [n["pos"][0] for n in nodes]
        assert xs == sorted(xs) and all(0 <= x <= 1 for x in xs)
        assert manifest["world_map"]["edges"] == [
            [f"l{i}", f"l{i + 1}"] for i in range(1, 9)
        ]
        assert manifest["unlock"] == {"type": "linear"}
        assert set(manifest["palettes"]) == set(STAGES)
        assert set(manifest["audio"]) == set(STAGES)
        assert set(manifest["props"]) == set(STAGES)

    def test_world_id_keys_the_save_on_content_not_seed(self, world) -> None:
        # The Godot save is keyed on world_id so a freshly generated world
        # starts from level 1 instead of inheriting a same-seed run's
        # progress. It must be content-derived, NOT the input-seed hash.
        _, out = world
        manifest = json.loads((out / "manifest.json").read_text())
        wid = manifest["world_id"]
        assert wid and len(wid) == 12
        seed_hash = hashlib.md5(
            manifest["seed"].encode("utf-8")
        ).hexdigest()[:12]
        assert wid != seed_hash, "world_id must not key on the seed"

    def test_per_stage_trees_on_disk(self, world) -> None:
        _, out = world
        for stage_id in STAGES:
            assert (out / f"stage/{stage_id}/stage.json").exists()
            assert (out / f"tileset/{stage_id}/tilesheet.png").exists()
            assert (out / f"style/{stage_id}/style.json").exists()
        assert (out / "level/bloom_terraces/l4/collision.npz").exists()
        assert (out / "review/frostspire_peaks/l9_skinned.png").exists()

    def test_byte_determinism(self, tmp_path: Path, world) -> None:
        _, out = world
        _run_world(tmp_path / "b")
        assert_trees_byte_identical(out, tmp_path / "b")

    def test_terrain_uses_the_stage_tileset(self, world) -> None:
        """A stage-2 level's terrain must resolve through its own stage's
        tileset — the cross-stage seam the consumers rely on."""
        ctx, out = world
        level = ctx.bible.levels["l5"]
        assert level.terrain.startswith("level/bloom_terraces/")
        with np.load(out / level.collision) as data:
            assert data["collision"].shape[0] == level.grid_height


class TestMultiStageRegen:
    def test_bare_level_id_marks_one_stage2_level(self, tmp_path: Path) -> None:
        """`canon regen l5 --mark-only` addressing works across stages —
        global numbering keeps the regen grammar unchanged."""
        from canon.packs.platformer.dag import run_orchestrated
        from canon.pipeline.orchestrator import mark_stale

        out = tmp_path / "run"
        ctx = PipelineContext(
            bible=Bible.empty(seed=SEED),
            config=CanonConfig(seed=SEED, output_dir=out),
            rng=random.Random(SEED),
            llm=LLMClient(FakeLLMBackend(make_fake_responder())),
            prompts=PlatformerPrompts(),
        )
        report = run_orchestrated(
            ctx, persist_path=out / "bible.json",
            num_levels=3, num_enemies=7, num_stages=3,
        )
        assert report.ok
        plan = mark_stale(ctx.bible, ["l5"])
        assert any(
            marked.startswith("level:bloom_terraces/l5/") for marked in plan.marked
        )
        assert not any("/l4/" in marked for marked in plan.marked)
