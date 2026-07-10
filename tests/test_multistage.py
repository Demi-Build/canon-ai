"""Multi-stage worlds M1 — biome stages, global level numbering, the
enemy ecology (world pool + habitat/rarity + swim styles), manifest v2 +
world map layout, and the multi-stage DAG."""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

import numpy as np
import pytest

from canon import CanonConfig, FakeLLMBackend, LLMClient, run_pipeline
from canon.bible.models import Bible
from canon.pipeline.runner import PipelineContext
from examples.platformer_pack import PlatformerPrompts, compose_pipeline
from examples.platformer_pack.phases import roll_habitats
from examples.platformer_pack.rules import DEFAULT_RULES
from examples.platformer_pack.validate import check_placements
from examples.run_platformer_slice import make_fake_responder

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
        assert sorted(ctx.bible.levels) == sorted(f"l{i}" for i in range(1, 10))
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
        from examples.platformer_pack.phases import EnemyGeneratorPhase

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
        from examples.platformer_pack.dsl import stamp

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
        from examples.platformer_pack.dsl import stamp

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
        from examples.platformer_pack.dsl import stamp
        from examples.platformer_pack.validate import swimmer_spot_exists

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


class TestBehaviorDoctrine:
    """Tracks first, leashed chasing, no hazard entry — the arithmetic
    both play surfaces mirror (same style as the jump-physics parity
    test: the sim below IS the consumer branch logic)."""

    def test_chaser_leash_returns_to_home_track(self) -> None:
        home, leash, aggro, speed, dt = 10.0, 4.0, 20.0, 3.0, 1 / 60
        x, player_x = 10.0, 40.0

        def step(x: float) -> float:
            chasing = abs(player_x - x) <= aggro and (
                leash <= 0 or abs(x - home) < leash
            )
            if chasing:
                return x + (1.0 if player_x > x else -1.0) * speed * dt
            if abs(x - home) > 0.1:
                return x + (1.0 if home > x else -1.0) * speed * dt
            return x

        # Player far away: out of aggro — the chaser stays on its track.
        for _ in range(200):
            x = step(x)
        assert abs(x - home) <= 0.2
        # Player inside aggro: pursue, but never past the leash...
        player_x = 25.0
        for _ in range(600):
            x = step(x)
            assert abs(x - home) <= leash + speed * dt
        assert x > home  # it DID give chase
        # ...and when the player leaves aggro, walk home again.
        player_x = 60.0
        for _ in range(600):
            x = step(x)
        assert abs(x - home) <= 0.2

    def test_relentless_variant_overrides_the_leash(self) -> None:
        from examples.platformer_pack.variants import load_variants

        relentless = load_variants().by_name["relentless"]
        assert float(relentless.behavior["leash_range"]) >= 999
        assert float(relentless.behavior["aggro_range"]) >= 99
        # And it is capped to one per level in the default rules.
        assert DEFAULT_RULES.variant_caps.get("relentless") == 1

    def test_enemies_never_enter_hazards_or_solids(self) -> None:
        """The can-occupy rule both surfaces share: hazard cells and
        solid/one-way cells block; a patroller reverses at a spike strip
        instead of strolling into it."""
        from examples.platformer_pack.dsl import stamp
        from examples.platformer_pack.tiles import DEFAULT_TILES

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
        files = sorted(
            p.relative_to(out) for p in out.rglob("*") if p.is_file()
        )
        for rel in files:
            assert (out / rel).read_bytes() == (tmp_path / "b" / rel).read_bytes(), (
                f"{rel} differs between identical-seed runs"
            )

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
        from canon.pipeline.orchestrator import mark_stale
        from examples.platformer_pack.dag import run_orchestrated

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
