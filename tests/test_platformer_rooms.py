"""Multi-room secret levels (Arc 3, chunk C1): the deterministic room
roll, ``plan_room``, stitcher-placed entrances (+ escalation), the room
build through the SAME section machinery, and the artifact plumbing
(Level linkage, manifest exclusion, QA rows, recompute agreement).

Consumers are untouched in this chunk — the new trigger types ride
inert through both play surfaces (the ``reward`` precedent)."""

from __future__ import annotations

import json
import random
from pathlib import Path

import numpy as np
import pytest

from canon import CanonConfig, FakeLLMBackend, LLMClient, run_pipeline
from canon.bible.models import Bible
from canon.pipeline.rng import derive_rng
from canon.pipeline.runner import PipelineContext
from examples.platformer_pack import PlatformerPrompts, compose_pipeline
from examples.platformer_pack.movement import DEFAULT_MOVEMENT
from examples.platformer_pack.rules import DEFAULT_RULES
from examples.platformer_pack.sections import (
    SECTION_OVERLAP,
    SecretRoomConfig,
    SecretRoomSpec,
    plan_room,
    plan_sections,
    roll_secret_rooms,
)
from examples.platformer_pack.tiles import DEFAULT_TILES
from examples.platformer_pack.validate import (
    _body_stand,
    place_room_entrances,
    reachable_cells,
    standable_cells,
)
from examples.run_platformer_slice import make_fake_responder

SEED = "emberfall_001"


def _roll(level_id: str, difficulty: int = 2, n_sections: int = 4,
          seed: str = SEED):
    return roll_secret_rooms(
        level_id, difficulty, n_sections,
        derive_rng(seed, "plat:layout", level_id, "secret"),
    )


class TestSecretRoomRoll:
    def test_deterministic(self) -> None:
        a = [vars(s) for s in _roll("l5")]
        b = [vars(s) for s in _roll("l5")]
        assert a == b

    def test_difficulty_capped_by_room_width(self) -> None:
        # Ticket 3: a room's CONTENT difficulty is capped by its rolled
        # width — width <= 30 caps a diff-3 parent at 2 (a cramped room can't
        # carry a diff-3 gauntlet); a wider room keeps the diff-3 bite.
        def _roll_w(width: int):
            cfg = SecretRoomConfig(
                count_odds={"3": [1.0]}, two_section_odds=0.0,
                width_band=(width, width), height_band=(13, 13),
                difficulty_cap_by_width=[(30, 2), (42, 3)],
                room_types={"vault": 1.0},
                archetype_pools={"vault": ["vault"]},
            )
            return roll_secret_rooms(
                "l3", 3, 4,
                derive_rng(SEED, "plat:layout", "l3", "secret"), config=cfg,
            )

        capped = _roll_w(30)
        assert capped and all(
            s.width == 30 and s.difficulty == 2 for s in capped
        )
        uncapped = _roll_w(31)  # just over the cap band → keeps diff-3
        assert uncapped and all(
            s.width == 31 and s.difficulty == 3 for s in uncapped
        )

    def test_vine_never_rolls_while_weighted_zero(self) -> None:
        """The verb table ships vine at 0.0 — a data flip later, never a
        rolled outcome now (the climb mechanic doesn't exist)."""
        for i in range(60):
            for spec in _roll(f"l{i % 9 + 1}", seed=f"sweep_{i}",
                              difficulty=i % 3 + 1):
                assert spec.entry_verb in ("pipe", "door")

    def test_all_room_types_reachable_by_the_roll(self) -> None:
        seen: set[str] = set()
        for i in range(120):
            for spec in _roll("l3", seed=f"types_{i}", difficulty=3):
                seen.add(spec.room_type)
        assert seen == {"shortcut", "vault", "lair"}

    def test_spec_shape(self) -> None:
        for i in range(40):
            for k, spec in enumerate(
                _roll("l7", seed=f"shape_{i}", difficulty=3, n_sections=5)
            ):
                assert spec.room_id == f"l7r{k + 1}"
                assert 0 <= spec.host_section < 5
                assert 13 <= spec.height <= 16
                assert len(spec.archetype_names) in (1, 2)
                if len(spec.archetype_names) == 2:
                    assert 32 <= spec.width <= 42
                    # wide two-section rooms keep the diff-3 bite (ticket 3)
                    assert spec.difficulty == 3
                else:
                    assert 18 <= spec.width <= 30
                    # a cramped single-section room caps its content at 2
                    assert spec.difficulty == 2
                assert spec.return_topology in ("detour", "shortcut")

    def test_room_archetypes_match_type_pools(self) -> None:
        pools = {
            "shortcut": {"gauntlet", "cave"},
            "vault": {"vault"},
            "lair": {"lair"},
        }
        for i in range(60):
            for spec in _roll("l2", seed=f"pool_{i}", difficulty=2):
                assert set(spec.archetype_names) <= pools[spec.room_type]


class TestPlanRoom:
    def _spec(self, names: list[str], width: int) -> SecretRoomSpec:
        return SecretRoomSpec(
            room_id="l1r1", room_type="vault", archetype_names=names,
            entry_verb="pipe", return_topology="detour", width=width,
            height=12, host_section=0, difficulty=1,
        )

    def test_single_section_spans_the_room(self) -> None:
        plan = plan_room(self._spec(["vault"], 20))
        assert len(plan.sections) == 1
        assert plan.sections[0].length == 20
        assert plan.checkpoint_sections == []
        assert plan.exits[0].section_idx == 0

    def test_two_sections_tile_exactly(self) -> None:
        plan = plan_room(self._spec(["gauntlet", "cave"], 34))
        s0, s1 = plan.sections
        assert s0.x_off == 0
        assert s1.x_off == s0.length - SECTION_OVERLAP
        assert s1.x_off + s1.length == 34

    def test_rooms_only_archetypes_never_roll_into_main_levels(self) -> None:
        rng = random.Random(7)
        for _ in range(40):
            for ps in plan_sections(120, 20, 3, rng):
                assert ps.archetype not in ("vault", "lair")


def _flat_grid(width: int = 60, height: int = 14):
    """A flat floored grid (every column standable + reachable)."""
    grid = np.zeros((height, width), dtype=np.int8)
    grid[height - 2, :] = 1  # floor
    grid[height - 1, :] = 1  # bedrock
    return grid


def _sections(width: int = 60, n: int = 3):
    """Hand-built deterministic sections tiling ``width`` exactly (the
    roll's jitter would make the count flaky for these placement tests)."""
    from examples.platformer_pack.sections import PlannedSection

    if n == 1:
        return [PlannedSection("gauntlet", width, x_off=0, y_off=0)]
    seg = (width + SECTION_OVERLAP * (n - 1)) // n
    out, pos = [], 0
    for i in range(n):
        length = seg if i < n - 1 else width - pos
        out.append(PlannedSection("gauntlet", length, x_off=pos, y_off=0))
        pos += length - SECTION_OVERLAP
    return out


class TestPlaceRoomEntrances:
    def _spec(self, room_id="l1r1", host=1, topology="detour", verb="pipe"):
        return SecretRoomSpec(
            room_id=room_id, room_type="vault", archetype_names=["vault"],
            entry_verb=verb, return_topology=topology, width=20, height=12,
            host_section=host, difficulty=1,
        )

    def test_entrance_lands_in_the_host_section(self) -> None:
        grid = _flat_grid()
        sections = _sections()
        spec = self._spec(host=1)
        out, notes = place_room_entrances(
            grid, (2, 11), (58, 11), sections, [spec], "horizontal",
            DEFAULT_MOVEMENT, tiles=DEFAULT_TILES,
        )
        assert len(out) == 1 and not notes
        t = out[0]
        ps = sections[1]
        assert ps.x_off <= t.x < ps.x_off + ps.length
        assert t.type == "room_entrance"
        assert t.params["room_id"] == "l1r1"
        assert t.params["verb"] == "pipe"
        # Detour returns to the entrance cell itself.
        assert (t.params["return_x"], t.params["return_y"]) == (t.x, t.y)
        # Standable + reachable on the real sets.
        stand = _body_stand(grid, standable_cells(grid, DEFAULT_TILES),
                            DEFAULT_TILES)
        reached = reachable_cells(grid, (2, 11), DEFAULT_MOVEMENT,
                                  DEFAULT_TILES)
        assert (t.x, t.y) in stand and (t.x, t.y) in reached

    def test_escalates_to_whole_level_when_host_has_no_footing(self) -> None:
        grid = _flat_grid()
        sections = _sections()
        ps = sections[2]
        # Turn the host section's floor into a pit — no standing cell.
        grid[:, ps.x_off:ps.x_off + ps.length] = 0
        spec = self._spec(host=2)
        out, notes = place_room_entrances(
            grid, (2, 11), (10, 11), sections, [spec], "horizontal",
            DEFAULT_MOVEMENT, tiles=DEFAULT_TILES,
        )
        assert len(out) == 1
        assert any("escalated to the whole level" in n for n in notes)

    def test_shortcut_demotes_to_detour_at_the_last_section(self) -> None:
        grid = _flat_grid()
        sections = _sections()
        spec = self._spec(host=2, topology="shortcut")
        out, notes = place_room_entrances(
            grid, (2, 11), (58, 11), sections, [spec], "horizontal",
            DEFAULT_MOVEMENT, tiles=DEFAULT_TILES,
        )
        assert out[0].params["return"] == "detour"
        assert any("demoted to detour" in n for n in notes)

    def test_shortcut_returns_land_further_along(self) -> None:
        grid = _flat_grid()
        sections = _sections()
        spec = self._spec(host=0, topology="shortcut")
        out, _ = place_room_entrances(
            grid, (2, 11), (58, 11), sections, [spec], "horizontal",
            DEFAULT_MOVEMENT, tiles=DEFAULT_TILES,
        )
        t = out[0]
        ps = sections[0]
        assert t.params["return"] == "shortcut"
        assert t.params["return_x"] >= ps.x_off + ps.length
        # Never the exit cell itself (that would insta-win on return).
        assert (t.params["return_x"], t.params["return_y"]) != (58, 11)

    def test_two_rooms_take_distinct_cells(self) -> None:
        grid = _flat_grid()
        sections = _sections()
        specs = [self._spec("l1r1", host=1), self._spec("l1r2", host=1)]
        out, _ = place_room_entrances(
            grid, (2, 11), (58, 11), sections, specs, "horizontal",
            DEFAULT_MOVEMENT, tiles=DEFAULT_TILES,
        )
        assert len(out) == 2
        assert (out[0].x, out[0].y) != (out[1].x, out[1].y)

    def test_avoids_spawn_exit_and_existing_triggers(self) -> None:
        from canon.bible.platformer import SparseMaskEntry

        grid = _flat_grid(width=24)
        sections = _sections(width=24, n=1)
        cp = SparseMaskEntry(x=12, y=11, type="checkpoint")
        out, _ = place_room_entrances(
            grid, (2, 11), (22, 11), sections, [self._spec(host=0)],
            "horizontal", DEFAULT_MOVEMENT, tiles=DEFAULT_TILES,
            triggers=[cp],
        )
        assert (out[0].x, out[0].y) not in {(2, 11), (22, 11), (12, 11)}


class TestVaultAndLairGeneration:
    """The rooms-only archetypes generate + validate end-to-end at $0
    through the REAL section machinery (``plan_override`` path) — covers
    vault even on seeds whose e2e run never rolls one."""

    def _ctx(self, tmp_path: Path) -> PipelineContext:
        return PipelineContext(
            bible=Bible.empty(seed=SEED),
            config=CanonConfig(seed=SEED, output_dir=tmp_path),
            rng=random.Random(SEED),
            llm=LLMClient(FakeLLMBackend(make_fake_responder())),
            prompts=PlatformerPrompts(),
        )

    @pytest.mark.parametrize("room_type,arch", [
        ("vault", "vault"), ("lair", "lair"),
    ])
    def test_room_archetype_builds_reachable(
        self, tmp_path: Path, room_type: str, arch: str
    ) -> None:
        from examples.platformer_pack.level import _generate_sectioned_level

        spec = SecretRoomSpec(
            room_id="l1r1", room_type=room_type, archetype_names=[arch],
            entry_verb="pipe", return_topology="detour", width=22,
            height=12, host_section=0, difficulty=2,
        )
        ctx = self._ctx(tmp_path)
        result, dsl_text, fell_back, _att, _br, _sn, diag = (
            _generate_sectioned_level(
                ctx, level_id="l1r1", brief="a hidden chamber",
                knobs={"difficulty": 2}, width=22, height=12,
                axis="horizontal", movement=DEFAULT_MOVEMENT,
                rules=DEFAULT_RULES, tiles=DEFAULT_TILES, seed=SEED,
                phase_name="plat:layout", plan_override=plan_room(spec),
            )
        )
        assert not fell_back
        assert diag["plan"].sections[0].archetype == arch
        assert result.spawn is not None and result.exit is not None
        reached = reachable_cells(
            result.grid, result.spawn, DEFAULT_MOVEMENT, DEFAULT_TILES
        )
        assert result.exit in reached
        if room_type == "vault":
            # The vault fake tucks reward anchors — the loot hook.
            assert any(t.type == "reward" for t in result.triggers)


class TestRoomContents:
    """C4 — room-type contents steering: vault = deterministic zero-enemy
    fast path, lair/shortcut = prompt directives, estimate prices rooms."""

    def test_vault_room_places_no_enemies(self, tmp_path: Path) -> None:
        from canon.bible.platformer import Level as LevelModel
        from examples.platformer_pack.level import place_level_entities

        ctx = PipelineContext(
            bible=Bible.empty(seed=SEED),
            config=CanonConfig(seed=SEED, output_dir=tmp_path),
            rng=random.Random(SEED),
            llm=LLMClient(FakeLLMBackend(make_fake_responder())),
            prompts=PlatformerPrompts(),
        )
        level = LevelModel(
            level_id="l1r1", stage_id="s1", parent_level="l1",
            grid_width=20, grid_height=12,
        )
        place_level_entities(ctx, level, room_type="vault")
        assert level.entities == []
        assert json.loads(
            (tmp_path / "level/s1/l1r1/entities.json").read_text()
        ) == []
        # No roster edges on an empty vault — an enemy re-roll must not
        # cascade into it.
        assert not any(
            p.startswith("enemy:")
            for p in level.step_parents["entities"]
        )

    def test_room_directives_reach_the_prompts(self) -> None:
        p = PlatformerPrompts()
        req = p.placement_generation(
            "l1r1", "brief", [], "none", 4, directive="LAIR DIRECTIVE",
        )
        assert "ROOM DIRECTIVE: LAIR DIRECTIVE" in req.user_message
        req2 = p.item_placement(
            "l1r1", "brief", [], standable_summary="none",
            spawn=(1, 1), exit_=(2, 2), grid_width=20, grid_height=12,
            enemies=[], reward_anchors=[], directive="VAULT DIRECTIVE",
        )
        assert "ROOM DIRECTIVE: VAULT DIRECTIVE" in req2.user_message
        # No directive → no directive line (main levels unchanged).
        req3 = p.placement_generation("l1", "brief", [], "none", 4)
        assert "ROOM DIRECTIVE" not in req3.user_message

    def test_fresh_estimate_prices_secret_rooms(self) -> None:
        from examples.platformer_pack.estimate import (
            _fresh_nodes,
            _sections_for_level,
        )

        nodes, bible = _fresh_nodes(
            {"num_levels": 9, "secret_rooms_avg": 0.6}
        )
        ids = {
            n.node_id.split("/")[-2]
            for n in nodes
            if n.node_id.startswith("level:")
        }
        rooms = {i for i in ids if "r" in i}
        assert len(rooms) == 5  # round(9 * 0.6)
        assert len(ids - rooms) == 9
        # Room layouts price at the 1-2 section room rate, not the
        # full-level average.
        assert _sections_for_level(bible, "l1r1", 4.2) == 1.3
        assert _sections_for_level(bible, "l99", 4.2) == 4.2


def _run_world(output_dir: Path, **kwargs) -> PipelineContext:
    ctx = PipelineContext(
        bible=Bible.empty(seed=SEED),
        config=CanonConfig(seed=SEED, output_dir=output_dir),
        rng=random.Random(SEED),
        llm=LLMClient(FakeLLMBackend(make_fake_responder())),
        prompts=PlatformerPrompts(),
    )
    run_pipeline(compose_pipeline(num_stages=3, num_enemies=7, **kwargs), ctx)
    return ctx


@pytest.fixture(scope="module")
def rooms_world(tmp_path_factory) -> tuple[PipelineContext, Path]:
    out = tmp_path_factory.mktemp("rooms_world") / "run"
    return _run_world(out), out


class TestRoomsEndToEnd:
    def test_default_seed_rolls_rooms(self, rooms_world) -> None:
        """The canned $0 run must exercise the feature — at least one
        room, at least one shortcut-ahead return, both verbs."""
        ctx, _ = rooms_world
        rooms = [
            rid
            for level in ctx.bible.levels.values()
            for rid in level.secret_rooms
        ]
        assert rooms, "default seed rolled no secret rooms"
        entrances = [
            t
            for level in ctx.bible.levels.values()
            for t in level.triggers
            if t.type == "room_entrance"
        ]
        assert {str(t.params["verb"]) for t in entrances} == {"pipe", "door"}
        assert "shortcut" in {str(t.params["return"]) for t in entrances}

    def test_rooms_are_levels_with_linkage(self, rooms_world) -> None:
        ctx, out = rooms_world
        for level in list(ctx.bible.levels.values()):
            for rid in level.secret_rooms:
                room = ctx.bible.levels[rid]
                assert room.parent_level == level.level_id
                assert room.stage_id == level.stage_id
                assert room.layout_axis == "horizontal"
                room_dir = out / "level" / room.stage_id / rid
                for name in (
                    "collision.npz", "hazards.json", "triggers.json",
                    "terrain.npz", "background.npz", "entities.json",
                    "items.json", "foreground.json", "level.json",
                ):
                    assert (room_dir / name).exists(), f"{rid}/{name}"
                manifest = json.loads((room_dir / "level.json").read_text())
                assert manifest["parent_level"] == level.level_id

    def test_entrances_reference_rolled_rooms_and_stand_reached(
        self, rooms_world
    ) -> None:
        ctx, out = rooms_world
        for level in ctx.bible.levels.values():
            if level.parent_level:
                continue
            entrances = [
                t for t in level.triggers if t.type == "room_entrance"
            ]
            assert {str(t.params["room_id"]) for t in entrances} == set(
                level.secret_rooms
            )
            if not entrances:
                continue
            with np.load(out / level.collision) as data:
                grid = data["collision"]
            stand = _body_stand(
                grid, standable_cells(grid, DEFAULT_TILES), DEFAULT_TILES
            )
            reached = reachable_cells(
                grid, level.spawn, DEFAULT_MOVEMENT, DEFAULT_TILES
            )
            for t in entrances:
                assert (t.x, t.y) in stand
                assert (t.x, t.y) in reached
                ret = (int(t.params["return_x"]), int(t.params["return_y"]))
                assert ret in stand and ret in reached
                assert ret != tuple(level.exit)

    def test_rooms_validate_entry_to_exit_with_a_portal(
        self, rooms_world
    ) -> None:
        ctx, out = rooms_world
        rooms = [
            lv for lv in ctx.bible.levels.values() if lv.parent_level
        ]
        assert rooms
        for room in rooms:
            with np.load(out / room.collision) as data:
                grid = data["collision"]
            reached = reachable_cells(
                grid, room.spawn, DEFAULT_MOVEMENT, DEFAULT_TILES
            )
            assert tuple(room.exit) in reached
            portals = [
                t for t in room.triggers if t.type == "room_portal"
            ]
            assert len(portals) == 1
            assert (portals[0].x, portals[0].y) == tuple(room.exit)
            parent = ctx.bible.levels[room.parent_level]
            entrance = next(
                t
                for t in parent.triggers
                if t.type == "room_entrance"
                and t.params["room_id"] == room.level_id
            )
            # The portal answers to the SAME verb the entrance used.
            assert portals[0].params["verb"] == entrance.params["verb"]

    def test_rooms_hidden_from_manifest_and_world_map(
        self, rooms_world
    ) -> None:
        ctx, out = rooms_world
        manifest = json.loads((out / "manifest.json").read_text())
        room_ids = {
            rid
            for level in ctx.bible.levels.values()
            for rid in level.secret_rooms
        }
        assert room_ids
        assert not room_ids & set(manifest["levels"])
        assert not room_ids & {
            n["level_id"] for n in manifest["world_map"]["nodes"]
        }
        for stage in manifest["stages"]:
            assert not room_ids & set(stage["levels"])

    def test_rooms_render_and_get_qa_rows(self, rooms_world) -> None:
        ctx, out = rooms_world
        for level in ctx.bible.levels.values():
            for rid in level.secret_rooms:
                for suffix in ("", "_skinned"):
                    assert (
                        out / "review" / level.stage_id / f"{rid}{suffix}.png"
                    ).exists()

    def test_recompute_agreement_across_phases(self, rooms_world) -> None:
        """The DAG-expansion / placement recompute must agree with what
        the layout body actually built — the zero-persisted-field
        discipline that keeps resume/regen honest."""
        from examples.platformer_pack.level import (
            level_secret_rooms,
            level_section_plan,
        )

        ctx, _ = rooms_world
        for stage in ctx.bible.stages.values():
            for index, lid in enumerate(stage.level_ids):
                level = ctx.bible.levels[lid]
                specs = level_secret_rooms(ctx, lid, index)
                assert [s.room_id for s in specs] == level.secret_rooms
                for spec in specs:
                    room = ctx.bible.levels[spec.room_id]
                    assert room.grid_width == spec.width
                    assert room.grid_height == spec.height
                    plan = level_section_plan(ctx, room)
                    assert [ps.archetype for ps in plan] == (
                        spec.archetype_names
                    )

    def test_pygame_stage_resolution_and_action_hook(self) -> None:
        """The play harness's pure seams for rooms (C3): room ids resolve
        to the parent's stage, and the PLAT_ACTIONS env parses to the
        frame->action map both surfaces key scripted entries on."""
        from examples.platformer_play import _Hooks, _stage_for

        manifest = {
            "stages": [
                {"stage_id": "ashen_depths", "levels": ["l1", "l2", "l3"]},
            ],
            "levels": ["l1", "l2", "l3"],
        }
        assert _stage_for(manifest, "l3") == "ashen_depths"
        assert _stage_for(manifest, "l3r1") == "ashen_depths"
        with pytest.raises(SystemExit):
            _stage_for(manifest, "nope")

        import os

        os.environ["PLAT_ACTIONS"] = "110:down, 350:up"
        try:
            hooks = _Hooks()
            assert hooks.actions == {110: "down", 350: "up"}
        finally:
            del os.environ["PLAT_ACTIONS"]

    def test_room_ids_regen_addressable(self, rooms_world) -> None:
        """``canon regen l6r1 --mark-only`` semantics: a bare room id
        expands to its steps and marks them stale (rooms are Levels —
        mark_stale resolves them via bible.levels)."""
        from canon.pipeline.orchestrator import mark_stale

        ctx, _ = rooms_world
        room_id = next(
            rid
            for level in ctx.bible.levels.values()
            for rid in level.secret_rooms
        )
        bible = ctx.bible.model_copy(deep=True)
        plan = mark_stale(bible, [room_id])
        assert any(room_id in aid for aid in plan.marked)
