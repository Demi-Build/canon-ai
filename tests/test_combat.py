"""Combat v1 tests: CombatSpec-as-data, the shared combat arithmetic,
size-aware placement validation (real 2x2 occupancy), the spawn-safety
nudge repair, and the regen edge (enemy definitions → entities steps).

The arithmetic tested here is the SINGLE SOURCE both play surfaces
consume (pygame directly, main.gd as a documented mirror) — a change
that breaks these tests desynchronizes the game from its validator.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("PIL")

from canon.backends.testing import FakeLLMBackend  # noqa: E402
from canon.bible.models import Bible  # noqa: E402
from canon.config import CanonConfig  # noqa: E402
from canon.llm.client import LLMClient  # noqa: E402
from canon.pipeline.orchestrator import mark_stale  # noqa: E402
from canon.pipeline.runner import PipelineContext, run_pipeline  # noqa: E402
from examples.platformer_pack import PlatformerPrompts, compose_pipeline  # noqa: E402
from examples.platformer_pack.combat import (  # noqa: E402
    DEFAULT_COMBAT,
    CombatSpec,
    contact_hearts,
    effective_size,
    load_combat,
    occupancy,
    stomps_to_kill,
)
from examples.platformer_pack.dsl import stamp  # noqa: E402
from examples.platformer_pack.rules import GameRules, load_rules  # noqa: E402
from examples.platformer_pack.validate import check_placements  # noqa: E402
from examples.run_platformer_slice import make_fake_responder  # noqa: E402

W, H = 48, 16


def _slice(output_dir: Path, **kwargs) -> PipelineContext:
    ctx = PipelineContext(
        bible=Bible.empty(seed="emberfall_001"),
        config=CanonConfig(seed="emberfall_001", output_dir=output_dir),
        rng=random.Random("emberfall_001"),
        llm=LLMClient(FakeLLMBackend(make_fake_responder())),
        prompts=PlatformerPrompts(),
    )
    run_pipeline(compose_pipeline(**kwargs), ctx)
    return ctx


class TestCombatSpec:
    def test_template_loads_with_defaults(self) -> None:
        spec = load_combat()
        assert spec.player_max_hearts == 3
        assert spec.stomp_damage == 6
        assert spec.spawn_safety_columns == 3
        assert spec.stomp_bounce_factor == pytest.approx(0.7)
        assert spec.hurt_iframes_s == pytest.approx(1.0)
        # Post-move spawn SHIELD (play-test round 1: respawning next to
        # a checkpoint-camping hound was an instant unavoidable hit).
        assert spec.spawn_grace_s == pytest.approx(4.0)

    def test_unknown_keys_ride_inert(self, tmp_path: Path) -> None:
        path = tmp_path / "combat.json"
        path.write_text(json.dumps(
            {"player_max_hearts": 5, "future_knob": "carried"}
        ))
        spec = load_combat(path)
        assert spec.player_max_hearts == 5
        assert spec.model_dump()["future_knob"] == "carried"

    def test_rules_gain_combat_policies(self) -> None:
        rules = load_rules()
        assert rules.checkpoint_enemy_reset is True
        assert rules.spawn_grace == "until_move"


class TestCombatArithmetic:
    def test_effective_size_multiplies(self) -> None:
        assert effective_size(2.0, 1.3) == pytest.approx(2.6)
        assert effective_size(1.0) == 1.0
        assert effective_size(0, 0) == 1.0  # degenerate data → sane body

    def test_occupancy_tiers(self) -> None:
        assert occupancy(1.0) == (1, 1)
        assert occupancy(1.5) == (1, 2)  # one column, headroom above
        assert occupancy(2.0) == (2, 2)  # the REAL 2x2
        assert occupancy(2.6) == (2, 3)  # champion on a big body
        assert occupancy(1.3) == (1, 2)

    def test_stomps_to_kill_tiers(self) -> None:
        d = DEFAULT_COMBAT.stomp_damage
        assert stomps_to_kill(4, 1.0, d) == 1  # v4 small band
        assert stomps_to_kill(12, 1.0, d) == 2  # v4 mid band
        assert stomps_to_kill(18, 1.0, d) == 3  # v4 big band
        assert stomps_to_kill(24, 1.0, d) == 4  # legacy v3 roll stays playable
        assert stomps_to_kill(12, 2.0, d) == 4  # elite hp x2
        assert stomps_to_kill(1, 1.0, d) == 1  # never 0

    def test_contact_hearts_integer_and_floored_at_one(self) -> None:
        assert contact_hearts(1) == 1
        assert contact_hearts(2, 2.0) == 4  # champion damage x2
        assert contact_hearts(0.3, 1.0) == 1  # never free


class TestSizedPlacements:
    def _defs(self, **sizes: float) -> dict:
        return {
            eid: {"archetype": "patroller", "size": size}
            for eid, size in sizes.items()
        }

    def test_two_by_two_needs_both_columns(self) -> None:
        # floor 0-20 then a gap: column 21+ unsupported.
        result = stamp("floor(0,20)\nspawn(2)\nexit(18)", W, H)
        accepted, problems, _ = check_placements(
            result.grid,
            [{"enemy_id": "brute", "x": 20, "y": 13}],  # x+1=21 has no floor
            result.spawn,
            self._defs(brute=2.0),
        )
        assert not accepted
        assert "column 21" in problems[0]  # the LOCATED failing cell
        assert "(21, 13)" in problems[0]

    def test_two_by_two_fits_on_open_ground(self) -> None:
        result = stamp("floor(0,47)\nspawn(2)\nexit(45)", W, H)
        accepted, problems, _ = check_placements(
            result.grid,
            [{"enemy_id": "brute", "x": 20, "y": 13}],
            result.spawn,
            self._defs(brute=2.0),
        )
        assert accepted and not problems

    def test_headroom_blocked_names_the_cell(self) -> None:
        # A platform directly above the standing row blocks a 1.5 body.
        result = stamp(
            "floor(0,47)\nplatform(19,12,4)\nspawn(2)\nexit(45)", W, H
        )
        accepted, problems, _ = check_placements(
            result.grid,
            [{"enemy_id": "tall", "x": 20, "y": 13}],
            result.spawn,
            self._defs(tall=1.5),
        )
        assert not accepted
        assert "(20, 12)" in problems[0]  # located blocking cell
        assert "PLATFORM" in problems[0]

    def test_variant_size_multiplies_definition_size(self) -> None:
        # 1.5 x champion 1.3 = 1.95 → still needs 2 rows of clearance.
        result = stamp(
            "floor(0,47)\nplatform(19,12,4)\nspawn(2)\nexit(45)", W, H
        )
        accepted, problems, _ = check_placements(
            result.grid,
            [{"enemy_id": "tall", "x": 20, "y": 13, "variant": "champion"}],
            result.spawn,
            self._defs(tall=1.5),
            rules=GameRules(variant_caps={}),
        )
        assert not accepted and "size 1.95" in problems[0]

    def test_sized_swimmer_needs_deep_water(self) -> None:
        # Raised basin: two rows of water (12-13); flush pool: one (14).
        text = (
            "floor(0,47)\nwall(25,12,13)\nwater(26,30,12)\nwall(31,12,13)\n"
            "pool(water,5,7)\nspawn(2)\nexit(45)"
        )
        result = stamp(text, W, H)
        defs = {"fish": {"archetype": "swimmer", "size": 1.5}}
        shallow, problems, _ = check_placements(
            result.grid, [{"enemy_id": "fish", "x": 6, "y": 14}],
            result.spawn, defs,
        )
        assert not shallow and "water" in problems[0]
        deep, problems, _ = check_placements(
            result.grid, [{"enemy_id": "fish", "x": 27, "y": 13}],
            result.spawn, defs,
        )
        assert deep and not problems

    def test_spawn_nudge_respects_footprint(self) -> None:
        # A 2.0 body crowding spawn must nudge to a column where BOTH
        # columns stand — the nudge is a repair, not a kickback.
        result = stamp("floor(0,47)\nspawn(2)\nexit(45)", W, H)
        accepted, problems, repairs = check_placements(
            result.grid,
            [{"enemy_id": "brute", "x": 4, "y": 13}],
            result.spawn,
            self._defs(brute=2.0),
        )
        assert not problems
        assert len(repairs) == 1 and "spawn-safety nudge" in repairs[0]
        assert accepted[0]["x"] == 6  # first column outside radius 3

    def test_spawn_safety_radius_is_combat_data(self) -> None:
        result = stamp("floor(0,47)\nspawn(2)\nexit(45)", W, H)
        accepted, _, repairs = check_placements(
            result.grid,
            [{"enemy_id": "imp", "x": 8, "y": 13}],
            result.spawn,
            self._defs(imp=1.0),
            combat=CombatSpec(spawn_safety_columns=10),
        )
        assert repairs and accepted[0]["x"] == 13  # 2 + 10 + 1


class TestCombatShipsInOutputs:
    def test_manifest_combat_block_and_enemy_sizes(self, tmp_path: Path) -> None:
        ctx = _slice(tmp_path / "run")
        manifest = json.loads((tmp_path / "run" / "manifest.json").read_text())
        assert manifest["combat"]["player_max_hearts"] == 3
        assert manifest["combat"]["stomp_damage"] == 6
        assert manifest["rules"]["checkpoint_enemy_reset"] is True
        assert manifest["rules"]["spawn_grace"] == "until_move"
        # Every enemy definition ships a discrete schema-rolled size.
        sizes = set()
        for enemy_id in manifest["enemies"]:
            spec = json.loads(
                (tmp_path / "run" / "enemy" / f"{enemy_id}.json").read_text()
            )
            assert spec["size"] in (1.0, 1.5, 2.0)
            sizes.add(spec["size"])
        assert len(sizes) > 1  # emberfall_001 rolls a mixed roster
        del ctx

    def test_entities_step_requires_enemy_definitions(self, tmp_path: Path) -> None:
        """The regen edge: placement reads definition SIZE, so an enemy
        re-roll must cascade into every entities step — without this a
        grown body silently invalidates stored placements."""
        ctx = _slice(tmp_path / "run")
        level = next(iter(ctx.bible.levels.values()))
        enemy_refs = {
            f"enemy:{eid}" for eid in ctx.bible.enemy_definitions
        }
        assert enemy_refs <= set(level.step_parents["entities"])

        first_enemy = sorted(ctx.bible.enemy_definitions)[0]
        plan = mark_stale(ctx.bible, [f"enemy:{first_enemy}"])
        marked = set(plan.marked)
        for level_id in ctx.bible.levels:
            assert any(
                s.endswith(f"{level_id}/entities") for s in marked
            ), f"enemy regen did not cascade to {level_id} entities: {marked}"

    def test_dag_entities_node_requires_enemies_phase(self, tmp_path: Path) -> None:
        from examples.platformer_pack.dag import LevelStepsDagPhase

        ctx = _slice(tmp_path / "run")
        nodes = LevelStepsDagPhase().expand(ctx)
        entities_nodes = [n for n in nodes if n.node_id.endswith("/entities")]
        assert entities_nodes
        for node in entities_nodes:
            assert "phase:plat:enemies" in node.requires
