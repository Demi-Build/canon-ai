"""Tests for the platformer vertical slice (examples/platformer_pack).

Covers the deterministic core (DSL/stamp/validators/colors), the schema
files loading through canon.skeleton.loader, and the end-to-end fake-backend
run: tree shape, byte-determinism, and the §6.3 hash-recompute contract.
"""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("PIL")

from canon.backends.testing import FakeLLMBackend  # noqa: E402
from canon.bible.models import Bible  # noqa: E402
from canon.bible.platformer import TileType  # noqa: E402
from canon.config import CanonConfig  # noqa: E402
from canon.llm.client import LLMClient  # noqa: E402
from canon.pipeline.runner import PipelineContext, run_pipeline  # noqa: E402
from canon.skeleton.loader import load_skeleton_spec  # noqa: E402
from examples.platformer_pack import PlatformerPrompts, compose_pipeline  # noqa: E402
from examples.platformer_pack.dsl import DslError, parse_dsl, stamp  # noqa: E402
from examples.platformer_pack.movement import DEFAULT_MOVEMENT  # noqa: E402
from examples.platformer_pack.phases import SCHEMAS_DIR, placeholder_color  # noqa: E402
from examples.platformer_pack.validate import (  # noqa: E402
    check_level,
    check_placements,
    standable_cells,
)
from examples.run_platformer_slice import (  # noqa: E402
    _FAKE_LAYOUTS,
    make_fake_responder,
)

W, H = 48, 16
#: Schema-rolled dims per level (level_layout.json lookups).
LEVEL_DIMS = {"l1": (48, 16), "l2": (56, 16), "l3": (64, 18)}


# ---------------------------------------------------------------------------
# DSL + stamp
# ---------------------------------------------------------------------------


class TestDsl:
    def test_stamp_deterministic(self) -> None:
        a = stamp(_FAKE_LAYOUTS["l1"], W, H)
        b = stamp(_FAKE_LAYOUTS["l1"], W, H)
        assert (a.grid == b.grid).all()
        assert a.spawn == b.spawn and a.exit == b.exit

    def test_agents_never_touch_cells(self) -> None:
        """I3: the DSL string fully determines the grid; ops are the only
        surface. Sanity-check the semantic mapping."""
        result = stamp("floor(0,47)\nspike(10,11)\nspawn(2)\nexit(45)", W, H)
        assert int(result.grid[H - 2, 5]) == TileType.FLOOR
        assert int(result.grid[H - 3, 10]) == TileType.SPIKE
        assert result.spawn == (2, H - 3)
        assert len(result.hazards) == 2

    @pytest.mark.parametrize(
        ("text", "match"),
        [
            ("flor(0,4)", "unknown op"),
            ("floor(0)", "takes 2 args"),
            ("floor(0,x)", "must be integers"),
            ("floor(0,47)\nspawn(2)", "missing exit"),
            ("floor(0,47)\nexit(45)", "missing spawn"),
            ("floor(0,47)\nspawn(2)\nspawn(3)\nexit(4)", "more than once"),
            ("floor(0,5)\nspike(10,11)\nspawn(2)\nexit(4)", "no ground"),
            ("floor(0,5)\nwater(10,12,12)\nspawn(2)\nexit(4)", "no solid basin"),
            ("floor(0,47)\ngap(20,24)\nwater(20,24,12)\nspawn(2)\nexit(45)", "no solid basin"),
            ("floor(0,47)\nledge(5,9,15)\nspawn(2)\nexit(45)", "outside 1"),
            ("gibberish", "not a valid op"),
        ],
    )
    def test_strict_errors(self, text: str, match: str) -> None:
        with pytest.raises(DslError, match=match):
            stamp(text, W, H)

    def test_parse_accepts_semicolons_and_comments(self) -> None:
        ops = parse_dsl("# a comment\nfloor(0,10); spawn(2)\nexit(8)")
        assert [op for op, _ in ops] == ["floor", "spawn", "exit"]

    def test_all_canned_layouts_valid(self) -> None:
        # Canned layouts are authored against the schema's per-level dims.
        for level_id, dsl_text in _FAKE_LAYOUTS.items():
            width, height = LEVEL_DIMS[level_id]
            result = stamp(dsl_text, width, height)
            problems = check_level(
                result.grid, result.spawn, result.exit, DEFAULT_MOVEMENT
            )
            assert not problems, f"{level_id}: {problems}"


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


class TestValidators:
    def test_unreachable_exit_flagged(self) -> None:
        # A gap wider than jump_width with no stepping stones.
        result = stamp("floor(0,10)\nfloor(20,47)\nspawn(2)\nexit(45)", W, H)
        problems = check_level(result.grid, result.spawn, result.exit, DEFAULT_MOVEMENT)
        assert problems and "not reachable" in problems[0]

    def test_reachability_feedback_locates_the_break(self) -> None:
        """The message must name the frontier, the unreachable foothold, the
        failing constraint, and where to put the fix — a location-free
        'add platforms' sent the real model into fallback loops."""
        result = stamp("floor(0,10)\nfloor(20,47)\nspawn(2)\nexit(45)", W, H)
        problems = check_level(result.grid, result.spawn, result.exit, DEFAULT_MOVEMENT)
        message = problems[0]
        assert "as far as (10, 13)" in message  # frontier: edge of first floor
        assert "(20, 13)" in message  # nearest unreachable foothold
        assert "horizontal distance 10 exceeds max jump distance 4" in message
        assert "between columns 10 and 20" in message
        assert "wider than 3 columns" in message


    def test_unstandable_spawn_feedback_names_the_occupant(self) -> None:
        """Feedback must say WHY: a platform stamped over spawn previously
        produced three identical blind retries against the real backend."""
        result = stamp("floor(0,47)\nplatform(0,13,6)\nspawn(2)\nexit(45)", W, H)
        problems = check_level(result.grid, result.spawn, result.exit, DEFAULT_MOVEMENT)
        assert problems and "PLATFORM" in problems[0] and "spawn" in problems[0]

    def test_unstandable_spawn_feedback_names_missing_ground(self) -> None:
        # gap() after spawn removes the floor beneath it.
        result = stamp("floor(0,47)\nspawn(2)\nexit(45)\ngap(1,3)", W, H)
        problems = check_level(result.grid, result.spawn, result.exit, DEFAULT_MOVEMENT)
        assert problems and "no solid ground beneath" in problems[0]

    def test_layout_retry_prompt_carries_previous_attempt(
        self, tmp_path: Path
    ) -> None:
        """Repair, not re-roll: the retry prompt must contain the rejected
        DSL next to the diagnosis, so the model patches one design."""
        good = make_fake_responder()
        layout_calls: list[str] = []
        failed_once = {"done": False}
        bad_dsl = "floor(0,10)\nfloor(20,47)\nspawn(2)\nexit(45)"  # unreachable

        def responder(request):
            msg = request.user_message
            if "### TASK: layout" in msg:
                layout_calls.append(msg)
                if not failed_once["done"]:
                    failed_once["done"] = True
                    return bad_dsl
            return good(request)

        _run_slice(tmp_path / "run", responder=responder)

        retries = [m for m in layout_calls if "previous layout attempt" in m]
        assert retries, "no layout retry prompt captured"
        assert bad_dsl in retries[0]
        assert "rejected because" in retries[0]
        assert "changing as little as possible" in retries[0]
        # And the diagnosis rides along with the rejected output.
        assert "cannot reach the next foothold" in retries[0]

    def test_placement_retry_prompt_carries_previous_attempt(
        self, tmp_path: Path
    ) -> None:
        good = make_fake_responder()
        placement_calls: list[str] = []
        failed_once = {"done": False}
        bad_json = '{"placements": [{"enemy_id": "cinder_beetle", "x": 3, "y": 13}]}'

        def responder(request):
            msg = request.user_message
            if "### TASK: placement" in msg:
                placement_calls.append(msg)
                if not failed_once["done"]:
                    failed_once["done"] = True
                    return bad_json  # too close to spawn
            return good(request)

        _run_slice(tmp_path / "run", responder=responder)

        retries = [m for m in placement_calls if "previous placements attempt" in m]
        assert retries and bad_json in retries[0]
        assert "too close to spawn" in retries[0]

    def test_placement_prompt_includes_spawn(self, tmp_path: Path) -> None:
        """The 'stay away from spawn' rule is only followable if the prompt
        says where spawn is."""
        good = make_fake_responder()
        placement_prompts = []

        def spy(request):
            if "### TASK: placement" in request.user_message:
                placement_prompts.append(request.user_message)
            return good(request)

        _run_slice(tmp_path / "run", responder=spy)
        assert placement_prompts
        assert all("Player spawn: [" in m for m in placement_prompts)

    def test_placement_rules(self) -> None:
        result = stamp(_FAKE_LAYOUTS["l1"], W, H)
        spawn = result.spawn
        archetypes = {"beetle": "patroller", "fish": "swimmer"}
        accepted, problems = check_placements(
            result.grid,
            [
                {"enemy_id": "beetle", "x": 14, "y": 13},  # ok
                {"enemy_id": "beetle", "x": 3, "y": 13},  # too close to spawn
                {"enemy_id": "beetle", "x": 40, "y": 13},  # spike cell
                {"enemy_id": "ghost", "x": 14, "y": 13},  # unknown id
                {"enemy_id": "beetle", "x": 10, "y": 5},  # mid-air
                {"enemy_id": "beetle", "x": 33, "y": 12},  # land enemy in water
                {"enemy_id": "fish", "x": 33, "y": 12},  # swimmer in water: ok
                {"enemy_id": "fish", "x": 14, "y": 13},  # swimmer on land
                {"enemy_id": "fish", "x": 32, "y": 13, "elite": True},  # elite ok
            ],
            spawn,
            archetypes,
        )
        assert [(p["enemy_id"], p["x"]) for p in accepted] == [
            ("beetle", 14), ("fish", 33), ("fish", 32),
        ]
        assert accepted[2]["elite"] is True
        assert len(problems) == 6
        assert any("swimmers must be placed inside water" in p for p in problems)
        assert any("only swimmers go in water" in p for p in problems)


# ---------------------------------------------------------------------------
# Colors + schemas
# ---------------------------------------------------------------------------


class TestDatabasesDriveReview:
    def test_placeholder_colors_distinct_and_not_red(self) -> None:
        colors = [placeholder_color(i) for i in range(8)]
        assert len(set(colors)) == 8
        for color in colors:
            r, g, b = (int(color[i : i + 2], 16) for i in (1, 3, 5))
            # Reserved spike band: strongly-red hues are nudged away.
            assert not (r > 180 and g < 80 and b < 80), f"{color} reads as spike red"

    def test_schema_files_load_via_loader(self) -> None:
        for name in ("enemy.json", "level_layout.json"):
            spec = load_skeleton_spec(SCHEMAS_DIR / name)
            assert spec.fields  # non-empty, validated at load time

    def test_difficulty_escalates_by_level_position(self) -> None:
        """Difficulty keys off the level_number CONTEXT, not a roll —
        level 3 must be harder than level 1 for every seed."""
        from canon.pipeline.rng import derive_rng
        from canon.skeleton.core import roll_skeleton

        spec = load_skeleton_spec(SCHEMAS_DIR / "level_layout.json")
        for seed in ("a", "b", "emberfall_001"):
            knobs = [
                roll_skeleton(
                    spec,
                    derive_rng(seed, "plat:layout", f"l{n}"),
                    context={"level_number": n},
                )
                for n in (1, 2, 3)
            ]
            assert [k["difficulty"] for k in knobs] == [1, 2, 3]
            assert (
                knobs[0]["hazard_count"]
                < knobs[1]["hazard_count"]
                < knobs[2]["hazard_count"]
            )


# ---------------------------------------------------------------------------
# End-to-end (fake backend)
# ---------------------------------------------------------------------------


def _run_slice(
    output_dir: Path,
    seed: str = "emberfall_001",
    responder=None,
    engine: str = "json",
) -> PipelineContext:
    adapter = None
    if engine == "godot":
        from canon.adapters import GodotOutputAdapter

        adapter = GodotOutputAdapter(output_dir)
    ctx = PipelineContext(
        bible=Bible.empty(seed=seed),
        config=CanonConfig(seed=seed, output_dir=output_dir),
        rng=random.Random(seed),
        llm=LLMClient(FakeLLMBackend(responder or make_fake_responder())),
        prompts=PlatformerPrompts(),
        adapter=adapter,
    )
    run_pipeline(compose_pipeline(engine=engine), ctx)
    return ctx


class TestEndToEnd:
    def test_tree_shape_and_determinism(self, tmp_path: Path) -> None:
        run_a, run_b = tmp_path / "a", tmp_path / "b"
        _run_slice(run_a)
        _run_slice(run_b)

        files_a = sorted(p.relative_to(run_a) for p in run_a.rglob("*") if p.is_file())
        expected = {
            Path("world.json"),
            Path("manifest.json"),
            Path("stage/ashen_depths/stage.json"),
            Path("tileset/ashen_depths/tilesheet.png"),
            Path("review/legend.png"),
            Path("level/ashen_depths/l1/collision.npz"),
            Path("level/ashen_depths/l1/level.json"),
            Path("level/ashen_depths/l1/entities.json"),
        }
        assert expected.issubset(set(files_a))

        for rel in files_a:
            assert (run_a / rel).read_bytes() == (run_b / rel).read_bytes(), (
                f"{rel} differs between identical-seed runs"
            )

    def test_hash_recompute_contract(self, tmp_path: Path) -> None:
        """§6.3: stored content hashes must match a recompute from disk."""
        ctx = _run_slice(tmp_path / "run")
        for level in ctx.bible.levels.values():
            disk = (tmp_path / "run" / level.collision).read_bytes()
            assert level.collision_hash == "sha256:" + hashlib.sha256(disk).hexdigest()
        tileset = ctx.bible.tilesets["ashen_depths"]
        disk = (tmp_path / "run" / tileset.tilesheet_path).read_bytes()
        assert tileset.tilesheet_hash == "sha256:" + hashlib.sha256(disk).hexdigest()

    def test_placements_reference_enemy_database(self, tmp_path: Path) -> None:
        ctx = _run_slice(tmp_path / "run")
        assert ctx.bible.enemy_definitions
        for level in ctx.bible.levels.values():
            assert level.entities, f"{level.level_id} has no placements"
            for placement in level.entities:
                enemy_id = placement.ref.split(":", 1)[1]
                assert enemy_id in ctx.bible.enemy_definitions
        # Every enemy carries a placeholder color for the review surfaces.
        for enemy in ctx.bible.enemy_definitions.values():
            assert enemy.stats["placeholder_color"].startswith("#")

    def test_placements_stand_on_generated_grid(self, tmp_path: Path) -> None:
        from examples.platformer_pack.validate import water_cells

        ctx = _run_slice(tmp_path / "run")
        for level in ctx.bible.levels.values():
            with np.load(tmp_path / "run" / level.collision) as data:
                grid = data["collision"]
            stand, water = standable_cells(grid), water_cells(grid)
            for placement in level.entities:
                enemy_id = placement.ref.split(":", 1)[1]
                archetype = ctx.bible.enemy_definitions[enemy_id].archetype
                expected = water if archetype == "swimmer" else stand
                assert tuple(placement.pos) in expected, (
                    f"{enemy_id} ({archetype}) at {placement.pos}"
                )

    def test_spawn_exit_first_class_fields(self, tmp_path: Path) -> None:
        """spawn/exit are Level fields (not trigger records) and land on
        standable cells; triggers stay empty in the slice."""
        ctx = _run_slice(tmp_path / "run")
        for level in ctx.bible.levels.values():
            assert level.spawn is not None and level.exit is not None
            with np.load(tmp_path / "run" / level.collision) as data:
                cells = standable_cells(data["collision"])
            assert tuple(level.spawn) in cells
            assert tuple(level.exit) in cells
            assert level.triggers == []
        # And they round-trip through level.json for the harness.
        level_doc = json.loads(
            (tmp_path / "run/level/ashen_depths/l1/level.json").read_text()
        )
        assert level_doc["spawn"] is not None and level_doc["exit"] is not None

    def test_clean_run_has_no_warnings(self, tmp_path: Path) -> None:
        ctx = _run_slice(tmp_path / "run")
        assert ctx.artifacts.get("slice_warnings", []) == []
        manifest = json.loads((tmp_path / "run/manifest.json").read_text())
        assert manifest["warnings"] == []

    def test_fallback_is_loudly_surfaced(self, tmp_path: Path) -> None:
        """A run that only 'succeeds' via fallback content must say so in
        artifacts AND manifest.json — never silently."""
        good = make_fake_responder()

        def broken_layouts(request):
            if "### TASK: layout" in request.user_message:
                return "not a dsl at all"
            return good(request)

        ctx = _run_slice(tmp_path / "run", responder=broken_layouts)
        warnings = ctx.artifacts["slice_warnings"]
        assert any("FALLBACK layout" in w for w in warnings)
        manifest = json.loads((tmp_path / "run/manifest.json").read_text())
        assert manifest["warnings"] == warnings
        # Fallback levels are flat floors at each level's schema-rolled dims.
        for level in ctx.bible.levels.values():
            assert level.spawn == (2, level.grid_height - 3)

    def test_duplicate_enemy_names_prompted_and_deduped(
        self, tmp_path: Path
    ) -> None:
        """A model that reuses names gets told what's taken (used-names in
        the prompt + retry feedback); ids stay unique via numeric backstop."""
        good = make_fake_responder()
        enemy_calls = []

        def same_name_enemies(request):
            if "### TASK: enemy" in request.user_message:
                enemy_calls.append(request.user_message)
                return json.dumps({"name": "Wraith Moth", "flavor": "again"})
            return good(request)

        ctx = _run_slice(tmp_path / "run", responder=same_name_enemies)

        # Later enemy prompts must name what's already taken.
        later_calls = [m for m in enemy_calls if "### INDEX: 1" in m]
        assert later_calls and all("already taken" in m.lower() or "Wraith Moth" in m for m in later_calls)
        # Retry feedback fired for the stubborn duplicate.
        assert any("already taken" in m for m in enemy_calls)
        # IDs are still unique (fallback names or numeric suffix backstop).
        ids = list(ctx.bible.enemy_definitions)
        assert len(ids) == len(set(ids)) == 4

    def test_layer_files_and_hash_contract(self, tmp_path: Path) -> None:
        """3a core: the full §6.4 layer set per level, every hash on the
        Bible matching a recompute from disk."""
        run = tmp_path / "run"
        ctx = _run_slice(run)
        layer_files = (
            "collision.npz", "terrain.npz", "background.npz",
            "hazards.json", "triggers.json", "entities.json",
            "foreground.json", "level.json",
        )
        for level in ctx.bible.levels.values():
            level_dir = run / "level" / level.stage_id / level.level_id
            for name in layer_files:
                assert (level_dir / name).exists(), f"{level.level_id}/{name}"
            for rel, stored in (
                (level.collision, level.collision_hash),
                (level.terrain, level.terrain_hash),
                (level.background, level.background_hash),
                (f"level/{level.stage_id}/{level.level_id}/hazards.json",
                 level.hazards_hash),
                (f"level/{level.stage_id}/{level.level_id}/triggers.json",
                 level.triggers_hash),
                (f"level/{level.stage_id}/{level.level_id}/entities.json",
                 level.entities_hash),
                (f"level/{level.stage_id}/{level.level_id}/foreground.json",
                 level.foreground_hash),
            ):
                disk = (run / rel).read_bytes()
                assert stored == "sha256:" + hashlib.sha256(disk).hexdigest(), rel

    def test_step_parents_follow_the_chain(self, tmp_path: Path) -> None:
        """§6.1 within-level edges, recorded for the Phase 2 orchestrator."""
        ctx = _run_slice(tmp_path / "run")
        for level in ctx.bible.levels.values():
            sp = level.step_parents
            prefix = f"level:{level.stage_id}/{level.level_id}"
            assert sp["collision"] == [f"{prefix}/layout"]
            assert f"{prefix}/collision" in sp["terrain"]
            assert any(p.startswith("tileset:") for p in sp["terrain"])
            assert f"{prefix}/collision" in sp["hazards"]
            assert f"{prefix}/collision" in sp["entities"]
            assert f"{prefix}/hazards" in sp["entities"]
            assert f"{prefix}/collision" in sp["foreground"]

    def test_3a_features_land_in_layers(self, tmp_path: Path) -> None:
        """Water pool, ledge tier, swimmer-in-water, elite override, decor,
        and variable dims — each visible in the right artifact."""
        run = tmp_path / "run"
        ctx = _run_slice(run)
        levels = ctx.bible.levels

        # Variable dims from the schema.
        assert [(lv.grid_width, lv.grid_height) for lv in levels.values()] == [
            (48, 16), (56, 16), (64, 18),
        ]
        # Water present in every canned level's collision layer.
        for level in levels.values():
            with np.load(run / level.collision) as data:
                assert (data["collision"] == int(TileType.WATER)).any(), (
                    f"{level.level_id} has no water"
                )
        # The swimmer sits in water (also covered by placement test); the
        # roster rolled one at this seed.
        archetypes = {e.archetype for e in ctx.bible.enemy_definitions.values()}
        assert "swimmer" in archetypes
        # Exactly one elite per canned level, riding on overrides.
        for level in levels.values():
            elites = [p for p in level.entities if p.overrides.get("elite")]
            assert len(elites) == 1
            assert elites[0].overrides["hp_mult"] == 2
        # Foreground decor landed inline + in its layer file.
        for level in levels.values():
            assert level.foreground
            file_decor = json.loads(
                (run / f"level/{level.stage_id}/{level.level_id}/foreground.json")
                .read_text()
            )
            assert len(file_decor) == len(level.foreground)
        # Tileset slots carry collision semantics, including water.
        tileset = ctx.bible.tilesets["ashen_depths"]
        semantics = {s.collision for s in tileset.slots}
        assert semantics == {"none", "solid", "one_way", "hazard", "water"}

    def test_water_reachability_model(self) -> None:
        """A pool wider than jump_width is crossable by swimming; the same
        span as a dry gap is not."""
        from examples.platformer_pack.validate import reachable_cells

        contained = stamp(
            "floor(0,47)\nwall(19,12,13)\nwall(31,12,13)\n"
            "water(20,30,12)\nspawn(2)\nexit(45)", W, H,
        )
        assert not check_level(
            contained.grid, contained.spawn, contained.exit, DEFAULT_MOVEMENT
        )
        reached = reachable_cells(contained.grid, contained.spawn, DEFAULT_MOVEMENT)
        assert (25, 13) in reached  # swimming through the pool

        dry = stamp("floor(0,19)\nfloor(31,47)\nspawn(2)\nexit(45)", W, H)
        assert check_level(dry.grid, dry.spawn, dry.exit, DEFAULT_MOVEMENT)

    def test_water_containment_rule(self) -> None:
        """GameRules decides: open-sided pools fail 'contained' with a
        locate-and-instruct message, pass 'free' (waterfall games)."""
        from examples.platformer_pack.rules import GameRules

        open_pool = stamp(
            "floor(0,47)\nwater(20,30,12)\nspawn(2)\nexit(45)", W, H
        )
        problems = check_level(
            open_pool.grid, open_pool.spawn, open_pool.exit, DEFAULT_MOVEMENT
        )
        assert problems and "spills out" in problems[0] and "wall(" in problems[0]

        free_rules = GameRules(water_containment="free")
        assert not check_level(
            open_pool.grid, open_pool.spawn, open_pool.exit, DEFAULT_MOVEMENT,
            rules=free_rules,
        )

    def test_rules_are_template_data(self, tmp_path: Path) -> None:
        """E.7 split: values load from a per-game file; unknown keys are
        carried into manifest.json inert (open carriage); known keys stay
        validated (hardened enforcement)."""
        import pydantic

        from examples.platformer_pack.rules import (
            DEFAULT_RULES_PATH,
            GameRules,
            load_rules,
        )

        # The pack's template file is the source of DEFAULT_RULES.
        assert load_rules(DEFAULT_RULES_PATH).water_containment == "contained"

        # A future rule sketched in data rides through, inert.
        custom = tmp_path / "my_game_rules.json"
        custom.write_text(json.dumps({
            "water_containment": "free",
            "lava_swimmable": True,  # no enforcement exists yet
        }))
        rules = load_rules(custom)
        assert rules.water_containment == "free"
        assert rules.model_dump()["lava_swimmable"] is True

        # Known keys stay validated — a typo'd VALUE fails loudly.
        with pytest.raises(pydantic.ValidationError):
            GameRules.model_validate({"water_containment": "sideways"})

    def test_manifest_carries_composed_rules(self, tmp_path: Path) -> None:
        """The manifest reflects the rules the run actually used —
        including inert extras — not the pack defaults."""
        from examples.platformer_pack.rules import GameRules

        run = tmp_path / "run"
        custom = GameRules(water_containment="free", lava_swimmable=True)
        ctx = PipelineContext(
            bible=Bible.empty(seed="s"),
            config=CanonConfig(seed="s", output_dir=run),
            rng=random.Random(0),
            llm=LLMClient(FakeLLMBackend(make_fake_responder())),
            prompts=PlatformerPrompts(),
        )
        run_pipeline(compose_pipeline(rules=custom), ctx)
        manifest = json.loads((run / "manifest.json").read_text())
        assert manifest["rules"]["water_containment"] == "free"
        assert manifest["rules"]["lava_swimmable"] is True

    def test_enemy_water_policy_rules(self) -> None:
        from examples.platformer_pack.rules import GameRules

        pool = stamp(
            "floor(0,47)\nwall(19,12,13)\nwall(31,12,13)\n"
            "water(20,30,12)\nspawn(2)\nexit(45)", W, H,
        )
        archetypes = {"fish": "swimmer", "beetle": "patroller"}
        in_water = [{"enemy_id": "fish", "x": 25, "y": 12},
                    {"enemy_id": "beetle", "x": 25, "y": 12}]

        # forbidden: nobody in water.
        accepted, problems = check_placements(
            pool.grid, in_water, pool.spawn, archetypes,
            rules=GameRules(enemy_water_policy="forbidden"),
        )
        assert not accepted and len(problems) == 2
        # amphibious: everybody allowed.
        accepted, problems = check_placements(
            pool.grid, in_water, pool.spawn, archetypes,
            rules=GameRules(enemy_water_policy="amphibious"),
        )
        assert len(accepted) == 2 and not problems

    def test_godot_engine_output(self, tmp_path: Path) -> None:
        """--engine godot: playable project files + grid.json siblings,
        with the canonical tree unchanged (npz + hashes identical)."""
        json_run, godot_run = tmp_path / "json", tmp_path / "godot"
        ctx_json = _run_slice(json_run)
        ctx_godot = _run_slice(godot_run, engine="godot")

        # Project files present.
        assert (godot_run / "project.godot").exists()
        assert (godot_run / "godot/main.tscn").exists()
        assert (godot_run / "godot/main.gd").exists()
        assert not (json_run / "project.godot").exists()  # json engine: none

        # Grid siblings match the canonical npz content.
        for level in ctx_godot.bible.levels.values():
            sibling = godot_run / level.collision.replace(".npz", ".grid.json")
            with np.load(godot_run / level.collision) as data:
                canonical = data["collision"].tolist()
            assert json.loads(sibling.read_text())["collision"] == canonical

        # Canonical artifacts byte-identical across engines; hashes agree.
        for rel in ("world.json", "manifest.json"):
            assert (json_run / rel).read_bytes() == (godot_run / rel).read_bytes()
        for level_id, level in ctx_json.bible.levels.items():
            assert level.collision_hash == ctx_godot.bible.levels[level_id].collision_hash

    def test_godot_engine_deterministic(self, tmp_path: Path) -> None:
        run_a, run_b = tmp_path / "a", tmp_path / "b"
        _run_slice(run_a, engine="godot")
        _run_slice(run_b, engine="godot")
        files = sorted(p.relative_to(run_a) for p in run_a.rglob("*") if p.is_file())
        for rel in files:
            assert (run_a / rel).read_bytes() == (run_b / rel).read_bytes(), rel

    def test_positive_generation_logging(self, tmp_path: Path, caplog) -> None:
        """Successful generations are logged at INFO (MazeWorld parity) —
        reviewers need evidence of what worked, not just what fell back."""
        import logging

        with caplog.at_level(logging.INFO):
            _run_slice(tmp_path / "run")
        text = caplog.text
        assert "WorldPhase produced world" in text
        assert "StagePhase planned stage" in text
        assert "EnemyGeneratorPhase produced 4 definitions" in text
        assert "Layout l1 (difficulty 1, 48x16)" in text
        assert "Layout l3 (difficulty 3, 64x18)" in text
        assert "Placement l1: " in text
        assert "TileAssignmentPhase mapped 3 levels" in text
        assert "BackgroundPhase wrote 3" in text
        assert "Decor l1: " in text
        assert "PlaceholderTilesetPhase wrote" in text
        assert "RenderPhase wrote 3 level renders" in text
        assert "Slice complete" in text and "0 warning(s)" in text

    def test_provenance_stamped(self, tmp_path: Path) -> None:
        ctx = _run_slice(tmp_path / "run")
        for entity in (
            ctx.bible.world,
            *ctx.bible.stages.values(),
            *ctx.bible.enemy_definitions.values(),
            *ctx.bible.levels.values(),
            *ctx.bible.tilesets.values(),
        ):
            assert entity.provenance_hash.startswith("sha256:"), entity
