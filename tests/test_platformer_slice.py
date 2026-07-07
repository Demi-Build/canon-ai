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
from examples.platformer_pack.tiles import TileRegistry, load_tiles  # noqa: E402
from examples.platformer_pack.validate import (  # noqa: E402
    check_level,
    check_placements,
    standable_cells,
    volume_cells,
)
from examples.platformer_pack.variants import load_variants  # noqa: E402
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
    def test_rising_costs_horizontal_range(self) -> None:
        """Arc-aware jump rule (round-4 play test: 'platforms too high,
        can't jump onto them'): the box rule approved rise-3 + dx-4
        diagonals the real arc can't make. Values from the shared
        ballistic model."""
        from examples.platformer_pack.movement import max_dx_for_rise

        assert max_dx_for_rise(DEFAULT_MOVEMENT, 3) == 3  # full rise: close
        assert max_dx_for_rise(DEFAULT_MOVEMENT, 2) == 4
        assert max_dx_for_rise(DEFAULT_MOVEMENT, 1) == 4  # capped at width
        assert max_dx_for_rise(DEFAULT_MOVEMENT, 0) == 4  # flat: box rule
        assert max_dx_for_rise(DEFAULT_MOVEMENT, 4) == -1  # over max rise

        # A rise-3 platform 4 columns out passes the OLD rule, fails now —
        # and the message teaches the rising-costs-range constraint.
        # (platform row 11 -> stand atop row 10 = rise 3 from row 13.)
        result = stamp(
            "floor(0,10)\nplatform(14,11,4)\nledge(20,26,9)\nfloor(30,47)\n"
            "spawn(2)\nexit(45)", W, H,
        )
        problems = check_level(
            result.grid, result.spawn, result.exit, DEFAULT_MOVEMENT
        )
        assert problems and "rising costs range" in problems[0]
        # The same step one column closer (dx 3 at rise 3) is makeable.
        near = stamp(
            "floor(0,10)\nplatform(13,11,4)\nledge(19,26,9)\n"
            "floor(30,47)\nspawn(2)\nexit(45)", W, H,
        )
        assert not check_level(
            near.grid, near.spawn, near.exit, DEFAULT_MOVEMENT
        )

    def test_validator_approved_jumps_land_in_harness_physics(self) -> None:
        """Validator ↔ play-surface PARITY, proven by simulation: replay
        the pygame/Godot integration (same constants, same frame order)
        for every boundary jump the arc rule approves — each must land.
        This is the guard against round-2/4/5's recurring 'platforms are
        too high': the validator may only promise jumps the physics
        delivers."""
        m = DEFAULT_MOVEMENT

        def simulate(dx_cells: int, rise: int) -> bool:
            # Mirrors examples/platformer_play.py: jump event sets vy,
            # horizontal moves, gravity applies, then vertical+landing.
            g, s, dt = m.gravity, m.run_speed, 1.0 / 60.0
            v0 = (2.0 * g * (m.jump_height + 0.4)) ** 0.5
            platform_row = 13 - rise + 1  # stand atop = 13 - rise
            px, py, vy = 0.0, 13.0, -v0
            for _ in range(240):
                px += s * dt  # holding toward the platform
                vy += g * dt
                prev_bottom = py + 0.99
                new_y = py + vy * dt
                feet = new_y + 0.99
                over = (px + 0.85 >= dx_cells) and (px + 0.15 < dx_cells + 4)
                if (
                    vy > 0
                    and over
                    and int(feet) == platform_row
                    and prev_bottom <= float(platform_row)
                ):
                    return True
                if new_y > 13.0:  # fell back without landing
                    return False
                py = new_y
            return False

        from examples.platformer_pack.movement import max_dx_for_rise

        # Every rise the validator allows, at its maximum approved dx,
        # must land in the integrated physics.
        for rise in range(1, m.jump_height + 1):
            dx = max_dx_for_rise(m, rise)
            assert simulate(dx, rise), (
                f"validator approves rise {rise} at dx {dx} but the "
                "harness physics cannot land it — parity broken"
            )
        # And clearly-outside jumps must fail in sim too (sanity).
        assert not simulate(6, 3)

    def test_marker_error_names_floor_columns(self) -> None:
        """A real model probed spawn columns 2, 3, 4... into fallback —
        the error must say where floor actually is."""
        with pytest.raises(DslError, match=r"columns 20-30, 40-42"):
            stamp("floor(20,30)\nfloor(40,42)\nspawn(2)\nexit(25)", W, H)
        with pytest.raises(DslError, match=r"no ground floor exists yet"):
            stamp("spawn(2)\nexit(25)", W, H)

    def test_unreachable_exit_flagged(self) -> None:
        # A gap wider than jump_width with no stepping stones.
        result = stamp("floor(0,10)\nfloor(20,47)\nspawn(2)\nexit(45)", W, H)
        problems = check_level(result.grid, result.spawn, result.exit, DEFAULT_MOVEMENT)
        assert problems and "not reachable" in problems[0]

    def test_reachability_feedback_hands_over_the_fix(self) -> None:
        """The message must name the frontier, the unreachable foothold,
        the failing constraint, AND the literal op to add — location-free
        'add platforms' looped the real model into fallback, and a
        located-but-do-the-arithmetic version still did."""
        result = stamp("floor(0,10)\nfloor(20,47)\nspawn(2)\nexit(45)", W, H)
        problems = check_level(result.grid, result.spawn, result.exit, DEFAULT_MOVEMENT)
        message = problems[0]
        assert "as far as (10, 13)" in message  # frontier: edge of first floor
        assert "(20, 13)" in message  # nearest unreachable foothold
        assert "horizontal distance 10 exceeds max jump distance 4" in message
        assert "ADD THIS ONE LINE" in message
        assert "platform(13,12,2)" in message  # jumpable: dx 3, rise 2
        assert "wider than 3 columns" in message

    def test_auto_bridge_repairs_reachability_in_code(self) -> None:
        """Code-for-computation: bridging a located break is arithmetic,
        so the TOOL appends the platforms — no LLM round-trip. Both
        observed break shapes converge to a valid level."""
        from examples.platformer_pack.validate import auto_bridge

        cases = (
            # The exact l3 real-run loop: a 5-wide gap on the ground row.
            "floor(0,32)\nfloor(38,47)\nspawn(2)\nexit(45)",
            # Vertical breaks (the l2 attempt-2 shape): the only route is
            # over a high ledge bridge — rises of 7 need stacked steps.
            "floor(0,10)\nledge(15,25,7)\nfloor(30,47)\nspawn(2)\nexit(45)",
        )
        for dsl_text in cases:
            repaired, added, problems = auto_bridge(
                dsl_text, W, H, DEFAULT_MOVEMENT
            )
            assert not problems, f"auto_bridge never converged: {problems}"
            assert added and all(op.startswith("platform(") for op in added)
            assert "# auto-bridge" in repaired  # authorship visible in DSL
            result = stamp(repaired, W, H)
            assert not check_level(
                result.grid, result.spawn, result.exit, DEFAULT_MOVEMENT
            )

    def test_auto_bridge_never_touches_design_problems(self) -> None:
        """A covered spawn has many valid fixes — that's the agent's
        decision. The tool must return it untouched for LLM feedback."""
        from examples.platformer_pack.validate import auto_bridge

        covered_spawn = "floor(0,47)\nplatform(0,13,6)\nspawn(2)\nexit(45)"
        repaired, added, problems = auto_bridge(
            covered_spawn, W, H, DEFAULT_MOVEMENT
        )
        assert repaired == covered_spawn and not added
        assert problems and "covered by a PLATFORM" in problems[0]

    def test_unreachable_layout_is_bridged_not_retried(
        self, tmp_path: Path
    ) -> None:
        """E2E: an unreachable-but-well-designed layout is accepted on
        attempt 1 with tool bridges — no retry, no fallback, playable."""
        good = make_fake_responder()
        layout_calls: list[str] = []
        gap_dsl = (
            "floor(0,32)\nfloor(38,47)\ncheckpoint(20)\nspawn(2)\nexit(45)"
        )

        def responder(request):
            msg = request.user_message
            if "### TASK: layout" in msg and "### LEVEL: l1" in msg:
                layout_calls.append(msg)
                return gap_dsl
            return good(request)

        ctx = _run_slice(tmp_path / "run", responder=responder)
        assert len(layout_calls) == 1  # accepted first try — tool repaired
        # No layout fallback. (Placement warnings are expected here: the
        # canned spots were hand-verified for the ORIGINAL l1 geometry.)
        assert not any(
            "layout" in w for w in ctx.artifacts.get("slice_warnings", [])
        )
        dsl_text = ctx.artifacts["dsl_texts"]["l1"]
        assert dsl_text.startswith(gap_dsl) and "# auto-bridge" in dsl_text
        # The stored collision layer IS the bridged, traversable level.
        with np.load(tmp_path / "run" / ctx.bible.levels["l1"].collision) as d:
            grid = d["collision"]
        level = ctx.bible.levels["l1"]
        assert not check_level(grid, level.spawn, level.exit, DEFAULT_MOVEMENT)


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
        DSL next to the diagnosis, so the model patches one design.
        (A DESIGN failure — covered spawn. Reachability breaks no longer
        reach the model at all; the bridge tool repairs those.)"""
        good = make_fake_responder()
        layout_calls: list[str] = []
        failed_once = {"done": False}
        bad_dsl = "floor(0,47)\nplatform(0,13,6)\nspawn(2)\nexit(45)"

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
        assert "covered by a PLATFORM" in retries[0]

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
                # variant rides through; legacy elite bool maps to "elite"
                {"enemy_id": "fish", "x": 32, "y": 13, "elite": True},
            ],
            spawn,
            archetypes,
        )
        assert [(p["enemy_id"], p["x"]) for p in accepted] == [
            ("beetle", 14), ("fish", 33), ("fish", 32),
        ]
        assert accepted[2]["variant"] == "elite"
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
    **compose_kwargs,
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
    run_pipeline(compose_pipeline(engine=engine, **compose_kwargs), ctx)
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
        ctx = _run_slice(tmp_path / "run")
        for level in ctx.bible.levels.values():
            with np.load(tmp_path / "run" / level.collision) as data:
                grid = data["collision"]
            stand, volume = standable_cells(grid), volume_cells(grid)
            for placement in level.entities:
                enemy_id = placement.ref.split(":", 1)[1]
                archetype = ctx.bible.enemy_definitions[enemy_id].archetype
                expected = volume if archetype == "swimmer" else stand
                assert tuple(placement.pos) in expected, (
                    f"{enemy_id} ({archetype}) at {placement.pos}"
                )

    def test_spawn_exit_first_class_fields(self, tmp_path: Path) -> None:
        """spawn/exit are Level fields (not trigger records) and land on
        standable cells; checkpoints are the trigger records (3b) and are
        standable too."""
        ctx = _run_slice(tmp_path / "run")
        for level in ctx.bible.levels.values():
            assert level.spawn is not None and level.exit is not None
            with np.load(tmp_path / "run" / level.collision) as data:
                cells = standable_cells(data["collision"])
            assert tuple(level.spawn) in cells
            assert tuple(level.exit) in cells
            checkpoints = [t for t in level.triggers if t.type == "checkpoint"]
            assert checkpoints, f"{level.level_id} has no checkpoint"
            for t in checkpoints:
                assert (t.x, t.y) in cells
        # And they round-trip through level.json for the harness.
        level_doc = json.loads(
            (tmp_path / "run/level/ashen_depths/l1/level.json").read_text()
        )
        assert level_doc["spawn"] is not None and level_doc["exit"] is not None
        assert level_doc["triggers"][0]["type"] == "checkpoint"
        # The triggers LAYER file carries the same records (§6.4).
        triggers_doc = json.loads(
            (tmp_path / "run/level/ashen_depths/l1/triggers.json").read_text()
        )
        assert triggers_doc == level_doc["triggers"]

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
        """Water pool, ledge tier, swimmer-in-water, variant placements,
        checkpoints, decor, and variable dims — each visible in the right
        artifact."""
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
        # Exactly one elite + one champion per canned level, riding on
        # overrides as NAMES the manifest vocabulary resolves (3b).
        for level in levels.values():
            named = [
                p.overrides["variant"]
                for p in level.entities
                if p.overrides.get("variant")
            ]
            assert sorted(named) == ["champion", "elite"]
            # entities.json carries the variant field for consumers.
            entities_doc = json.loads(
                (run / f"level/{level.stage_id}/{level.level_id}/entities.json")
                .read_text()
            )
            assert [e["variant"] for e in entities_doc if e["variant"]] == [
                "elite", "champion",
            ]
        # Foreground decor landed inline + in its layer file.
        for level in levels.values():
            assert level.foreground
            file_decor = json.loads(
                (run / f"level/{level.stage_id}/{level.level_id}/foreground.json")
                .read_text()
            )
            assert len(file_decor) == len(level.foreground)
        # Tileset slots carry category semantics + named tiles + params.
        tileset = ctx.bible.tilesets["ashen_depths"]
        semantics = {s.collision for s in tileset.slots}
        assert semantics == {"empty", "solid", "one_way", "hazard", "volume"}
        water_slot = next(s for s in tileset.slots if s.name == "water")
        assert water_slot.params["speed_factor"] == 0.55
        # Manifest ships the full game vocabulary for play surfaces.
        manifest = json.loads((run / "manifest.json").read_text())
        assert [t["name"] for t in manifest["tiles"]] == [
            "empty", "floor", "platform", "wall", "spike", "water",
        ]
        assert [v["name"] for v in manifest["variants"]] == ["elite", "champion"]

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

    def test_edit_detection_and_stale_cascade(self, tmp_path: Path) -> None:
        """Phase 2 §6.3 on real 3a data: a hand-edited collision file marks
        that step user_edited, its §6.1 descendants stale, ancestors and
        sibling levels untouched."""
        from canon.pipeline.orchestrator import detect_edits

        run = tmp_path / "run"
        ctx = _run_slice(run)

        # Pristine tree: nothing flagged.
        clean = detect_edits(ctx.bible, run)
        assert not clean.user_edited and not clean.stale and not clean.missing

        # Simulate a user editing l2's collision mask on disk.
        target = run / "level/ashen_depths/l2/collision.npz"
        target.write_bytes(target.read_bytes() + b"edited")
        report = detect_edits(ctx.bible, run)

        prefix = "level:ashen_depths/l2"
        assert report.user_edited == [f"{prefix}/collision"]
        # Descendants through step_parents edges — including transitive
        # (entities <- hazards <- collision).
        for step in ("terrain", "background", "hazards", "triggers",
                     "entities", "foreground"):
            assert f"{prefix}/{step}" in report.stale, step
        # Ancestors and sibling levels untouched.
        assert not any("l1" in aid or "l3" in aid for aid in report.stale)
        assert not any(aid.startswith("tileset") for aid in report.user_edited)
        # Statuses landed on the Bible: entity + node_status.
        from canon.bible.artifacts import ArtifactStatus

        assert ctx.bible.levels["l2"].status is ArtifactStatus.USER_EDITED
        assert (
            ctx.bible.metadata.node_status[f"{prefix}/collision"]
            is ArtifactStatus.USER_EDITED
        )
        assert (
            ctx.bible.metadata.node_status[f"{prefix}/terrain"]
            is ArtifactStatus.STALE
        )
        assert ctx.bible.levels["l1"].status is not ArtifactStatus.STALE

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


# ---------------------------------------------------------------------------
# Phase 3b: tile registry, generic ops, checkpoints, variants, lava world
# ---------------------------------------------------------------------------


def _registry(tiles: list[dict]) -> TileRegistry:
    return TileRegistry.model_validate({"tiles": tiles})


_BASE_TILES = [
    {"id": 0, "name": "empty", "category": "empty"},
    {"id": 1, "name": "floor", "category": "solid"},
    {"id": 2, "name": "platform", "category": "one_way"},
    {"id": 3, "name": "wall", "category": "solid"},
]


class TestTileRegistry:
    def test_default_registry_mirrors_tiletype(self) -> None:
        """The framework-default enum and the pack's registry file must
        agree — the enum exists so framework code has stable names."""
        from examples.platformer_pack.tiles import DEFAULT_TILES

        assert {t.name.upper(): t.id for t in DEFAULT_TILES.tiles} == {
            m.name: int(m) for m in TileType
        }

    def test_band_violation_fails_loudly(self) -> None:
        import pydantic

        with pytest.raises(pydantic.ValidationError, match="band"):
            _registry(
                _BASE_TILES
                + [{"id": 5, "name": "acid", "category": "volume"}]  # <20
            )

    def test_missing_structural_tile_fails(self) -> None:
        import pydantic

        with pytest.raises(pydantic.ValidationError, match="floor"):
            _registry([t for t in _BASE_TILES if t["name"] != "floor"])

    def test_duplicate_ids_fail(self) -> None:
        import pydantic

        with pytest.raises(pydantic.ValidationError, match="duplicate"):
            _registry(
                _BASE_TILES
                + [{"id": 3, "name": "girder", "category": "solid"}]
            )

    def test_empty_tile_contract(self) -> None:
        import pydantic

        with pytest.raises(pydantic.ValidationError, match="empty"):
            _registry([t for t in _BASE_TILES if t["name"] != "empty"])

    def test_new_content_is_a_data_entry(self) -> None:
        """The template claim: swimmable lava and a laser hazard exist the
        moment they're written down — same categories, zero new code."""
        tiles = _registry(
            _BASE_TILES
            + [
                {"id": 11, "name": "laser", "category": "hazard"},
                {
                    "id": 21, "name": "lava", "category": "volume",
                    "params": {"damage_per_second": 1.5, "speed_factor": 0.4},
                },
            ]
        )
        result = stamp(
            "floor(0,47)\nwall(19,12,13)\nwall(31,12,13)\n"
            "volume(lava,20,30,12)\nhazard_strip(laser,40,41)\n"
            "spawn(2)\nexit(45)",
            W, H, tiles=tiles,
        )
        assert int(result.grid[12, 25]) == 21
        assert int(result.grid[13, 40]) == 11
        assert result.hazards[0].type == "floor_laser"
        assert not check_level(
            result.grid, result.spawn, result.exit, DEFAULT_MOVEMENT,
            tiles=tiles,
        )


class TestGenericOps:
    def test_aliases_resolve_through_registry(self) -> None:
        """water()/spike() are ergonomic spellings of volume()/
        hazard_strip() — identical grids either way."""
        via_alias = stamp(_FAKE_LAYOUTS["l1"], W, H)
        via_generic = stamp(
            _FAKE_LAYOUTS["l1"]
            .replace("volume(water,", "water(")
            .replace("hazard_strip(spike,", "spike("),
            W, H,
        )
        assert (via_alias.grid == via_generic.grid).all()

    def test_alias_without_tile_names_the_vocabulary(self) -> None:
        """A game without water must tell the agent what it has instead."""
        lava_only = _registry(
            _BASE_TILES
            + [
                {"id": 10, "name": "spike", "category": "hazard"},
                {"id": 20, "name": "lava", "category": "volume"},
            ]
        )
        with pytest.raises(DslError, match=r"no tile named 'water'.*lava"):
            stamp("floor(0,47)\nwater(20,30,12)\nspawn(2)\nexit(45)",
                  W, H, tiles=lava_only)

    def test_category_mismatch_is_rejected(self) -> None:
        with pytest.raises(DslError, match="'spike' is a hazard tile"):
            stamp("floor(0,47)\nvolume(spike,20,30,12)\nspawn(2)\nexit(45)",
                  W, H)

    def test_name_arg_must_be_identifier(self) -> None:
        with pytest.raises(DslError, match="tile NAME first"):
            stamp("floor(0,47)\nvolume(7,20,30,12)\nspawn(2)\nexit(45)", W, H)

    def test_pool_op_carves_a_flush_contained_pool(self) -> None:
        """pool() sinks a volume INTO the ground: surface flush with the
        walking row, floor banks contain it, bedrock is the basin — the
        construction real models kept attempting via volume()-over-pit."""
        result = stamp(
            "floor(0,47)\npool(water,20,25)\nspawn(2)\nexit(45)", W, H
        )
        assert int(result.grid[H - 2, 22]) == TileType.WATER  # flush
        assert int(result.grid[H - 1, 22]) == TileType.WALL  # bedrock basin
        assert int(result.grid[H - 2, 19]) == TileType.FLOOR  # bank
        # Contained + reachable with no extra ops.
        assert not check_level(
            result.grid, result.spawn, result.exit, DEFAULT_MOVEMENT
        )
        # Swimmers live in it (flush pools are volume cells like any).
        accepted, problems = check_placements(
            result.grid,
            [{"enemy_id": "fish", "x": 22, "y": 14}],
            result.spawn,
            {"fish": "swimmer"},
        )
        assert accepted and not problems

    def test_pool_needs_ground_floor(self) -> None:
        with pytest.raises(DslError, match="no ground floor to sink into"):
            stamp("floor(0,10)\npool(water,20,25)\nspawn(2)\nexit(8)", W, H)

    def test_no_basin_message_teaches_pool_op(self) -> None:
        """The no-basin loop (volume poured over a pit, three identical
        real-model retries) must point at the op that DOES build sunken
        pools."""
        with pytest.raises(DslError, match=r"use pool\(water,20,24\)"):
            stamp(
                "floor(0,47)\ngap(20,24)\nwater(20,24,12)\nspawn(2)\nexit(45)",
                W, H,
            )

    def test_pour_on_ground_row_message_carries_the_recipe(self) -> None:
        """The observed real-model failure loop: surface poured ON the
        ground floor row, three identical retries into fallback. The
        message must teach the on-top-of-floor recipe, not just diagnose
        (validator messages are prompts)."""
        with pytest.raises(DslError) as exc_info:
            # H=16 → ground row 14; pouring at 14 hits the floor itself.
            stamp("floor(0,47)\nwater(20,25,14)\nspawn(2)\nexit(45)", W, H)
        message = str(exc_info.value)
        assert "IS the ground floor row" in message
        assert "ON TOP of the floor" in message
        assert "wall(19,12,13)" in message
        assert "water(20,25,13)" in message

    def test_layout_prompt_carries_pool_recipe(self, tmp_path: Path) -> None:
        """Teach the recipe up front, not only in retry feedback."""
        good = make_fake_responder()
        prompts = []

        def spy(request):
            if "### TASK: layout" in request.user_message:
                prompts.append(request.user_message)
            return good(request)

        _run_slice(tmp_path / "run", responder=spy)
        assert prompts
        for message in prompts:
            assert "Pool recipe" in message
            assert "OPEN AIR" in message


class TestCheckpoints:
    def test_checkpoint_lands_in_triggers(self) -> None:
        result = stamp(
            "floor(0,47)\ncheckpoint(20)\nspawn(2)\nexit(45)", W, H
        )
        assert [(t.x, t.y, t.type) for t in result.triggers] == [
            (20, H - 3, "checkpoint")
        ]

    def test_checkpoint_needs_floor(self) -> None:
        with pytest.raises(DslError, match="checkpoint.*no floor"):
            stamp("floor(0,10)\ncheckpoint(20)\nspawn(2)\nexit(8)", W, H)

    def test_duplicate_checkpoint_column_rejected(self) -> None:
        with pytest.raises(DslError, match="more than once"):
            stamp(
                "floor(0,47)\ncheckpoint(20)\ncheckpoint(20)\n"
                "spawn(2)\nexit(45)", W, H,
            )

    def test_unreachable_checkpoint_flagged(self) -> None:
        """check_level validates checkpoints like spawn/exit — standable
        AND reachable, with the locate-and-instruct message."""
        result = stamp(
            "floor(0,47)\nledge(20,22,5)\ncheckpoint(2)\nplatform(1,13,3)\n"
            "spawn(6)\nexit(45)", W, H,
        )
        # A later platform() covered the checkpoint's cell.
        problems = check_level(
            result.grid, result.spawn, result.exit, DEFAULT_MOVEMENT,
            triggers=result.triggers,
        )
        assert problems and "checkpoint" in problems[0]
        assert "PLATFORM" in problems[0]


class TestVariantCaps:
    def test_unknown_variant_names_vocabulary(self) -> None:
        result = stamp(_FAKE_LAYOUTS["l1"], W, H)
        accepted, problems = check_placements(
            result.grid,
            [{"enemy_id": "beetle", "x": 14, "y": 13, "variant": "mega"}],
            result.spawn,
            {"beetle": "patroller"},
        )
        assert not accepted
        assert problems and "unknown variant 'mega'" in problems[0]
        assert "champion" in problems[0] and "elite" in problems[0]

    def test_caps_from_game_rules_enforced(self) -> None:
        from examples.platformer_pack.rules import GameRules

        result = stamp(_FAKE_LAYOUTS["l1"], W, H)
        accepted, problems = check_placements(
            result.grid,
            [
                {"enemy_id": "beetle", "x": 14, "y": 13, "variant": "elite"},
                {"enemy_id": "beetle", "x": 20, "y": 13, "variant": "elite"},
            ],
            result.spawn,
            {"beetle": "patroller"},
            rules=GameRules(variant_caps={"elite": 1}),
        )
        assert [p["x"] for p in accepted] == [14]
        assert problems and "at most 1 'elite'" in problems[0]

    def test_uncapped_variant_rides_free(self) -> None:
        from examples.platformer_pack.rules import GameRules

        result = stamp(_FAKE_LAYOUTS["l1"], W, H)
        accepted, problems = check_placements(
            result.grid,
            [
                {"enemy_id": "beetle", "x": 14, "y": 13, "variant": "champion"},
                {"enemy_id": "beetle", "x": 20, "y": 13, "variant": "champion"},
            ],
            result.spawn,
            {"beetle": "patroller"},
            rules=GameRules(variant_caps={}),  # no cap on champion
        )
        assert len(accepted) == 2 and not problems

    def test_placement_prompt_offers_vocabulary(self, tmp_path: Path) -> None:
        good = make_fake_responder()
        prompts = []

        def spy(request):
            if "### TASK: placement" in request.user_message:
                prompts.append(request.user_message)
            return good(request)

        _run_slice(tmp_path / "run", responder=spy)
        assert prompts
        for message in prompts:
            assert '"variant": one of' in message
            assert "at most 1 per level" in message


class TestStyleGuide:
    """The palette agent: theme → role palette, enforced for coverage
    and readability, painted onto the tilesheet through the color_role
    seam — no consumer changes."""

    def test_palette_validators_locate_and_instruct(self) -> None:
        from examples.platformer_pack.style import check_palette
        from examples.platformer_pack.tiles import DEFAULT_TILES

        good = {
            "background": "#2b2331", "ground": "#6e5a4e",
            "platform": "#b8804a", "wall": "#5b4d5e",
            "danger": "#e0453a", "water": "#3a6ea5",
        }
        assert check_palette(good, DEFAULT_TILES) == []

        missing = {k: v for k, v in good.items() if k != "water"}
        problems = check_palette(missing, DEFAULT_TILES)
        assert problems and "missing role 'water'" in problems[0]

        bad_hex = dict(good, wall="grayish")
        problems = check_palette(bad_hex, DEFAULT_TILES)
        assert problems and '"#rrggbb"' in problems[0]

        invisible = dict(good, ground="#2c2432")  # ~= background
        problems = check_palette(invisible, DEFAULT_TILES)
        assert problems and "nearly invisible" in problems[0]
        assert "lighten or darken" in problems[0]

        cold_hazard = dict(good, danger="#3a45e0")  # blue spikes
        problems = check_palette(cold_hazard, DEFAULT_TILES)
        assert problems and "doesn't read as dangerous" in problems[0]

    def test_palette_paints_the_sheet_and_ships_everywhere(
        self, tmp_path: Path
    ) -> None:
        from PIL import Image

        run = tmp_path / "run"
        ctx = _run_slice(run)
        tileset = ctx.bible.tilesets["ashen_depths"]
        # Recorded on the artifact, in the manifest, and as the seed file.
        assert tileset.palette["ground"] == "#6e5a4e"
        manifest = json.loads((run / "manifest.json").read_text())
        assert manifest["palette"] == tileset.palette
        style_doc = json.loads(
            (run / "style/ashen_depths/style.json").read_text()
        )
        assert style_doc["palette"]["danger"] == "#e0453a"
        # And the sheet is actually painted with it: sample the floor slot.
        sheet = Image.open(run / tileset.tilesheet_path).convert("RGB")
        floor_slot = next(s for s in tileset.slots if s.name == "floor")
        x, y, _w, _h = floor_slot.px_region
        assert sheet.getpixel((x + 1, y + 1)) == (0x6E, 0x5A, 0x4E)

    def test_fallback_palette_is_loud(self, tmp_path: Path) -> None:
        good = make_fake_responder()

        def broken_style(request):
            if "### TASK: style" in request.user_message:
                return "definitely not json"
            return good(request)

        ctx = _run_slice(tmp_path / "run", responder=broken_style)
        warnings = ctx.artifacts["slice_warnings"]
        assert any("PLACEHOLDER palette" in w for w in warnings)
        # Fallback still paints the sheet with the hardcoded colors.
        tileset = ctx.bible.tilesets["ashen_depths"]
        assert tileset.palette["ground"] == "#6e6e78"

    def test_contrast_is_repaired_in_code_not_retried(self) -> None:
        """Code-for-computation: luminance distance is arithmetic. A too-
        dark color is shifted to the readability bar (hue kept); passing
        colors are untouched. This killed a real 3-retry loop of the
        model nudging dark water against a dark dusk background."""
        from examples.platformer_pack.style import (
            MIN_CONTRAST,
            _luminance,
            check_palette,
            enforce_contrast,
        )
        from examples.platformer_pack.tiles import DEFAULT_TILES

        # The exact palette shape the real model looped on.
        palette = {
            "background": "#2b1f2e", "ground": "#7a5c3a",
            "platform": "#c8843a", "wall": "#c0a882",
            "danger": "#e84210", "water": "#1a4a6b",  # distance ~32: fails
        }
        repaired, adjusted = enforce_contrast(palette, DEFAULT_TILES)
        assert set(adjusted) == {"water"}
        assert check_palette(repaired, DEFAULT_TILES) == []
        bg_lum = _luminance(repaired["background"])
        assert abs(_luminance(repaired["water"]) - bg_lum) >= MIN_CONTRAST
        # Hue survives: still a blue (b > r).
        r, _g, b = (
            int(repaired["water"][i : i + 2], 16) for i in (1, 3, 5)
        )
        assert b > r
        # Untouched roles pass through byte-identical.
        assert repaired["danger"] == palette["danger"]

    def test_low_contrast_palette_accepted_e2e(self, tmp_path: Path) -> None:
        """A palette failing only on contrast must NOT retry or fall back
        — the tool repairs it and the run stays warning-free."""
        good = make_fake_responder()
        style_calls: list[str] = []

        def moody_style(request):
            if "### TASK: style" in request.user_message:
                style_calls.append(request.user_message)
                return json.dumps({"palette": {
                    "background": "#2b1f2e", "ground": "#7a5c3a",
                    "platform": "#c8843a", "wall": "#c0a882",
                    "danger": "#e84210", "water": "#1a4a6b",
                }})
            return good(request)

        ctx = _run_slice(tmp_path / "run", responder=moody_style)
        assert len(style_calls) == 1  # accepted first try — tool repaired
        assert ctx.artifacts.get("slice_warnings", []) == []
        manifest = json.loads((tmp_path / "run/manifest.json").read_text())
        from examples.platformer_pack.style import check_palette
        from examples.platformer_pack.tiles import DEFAULT_TILES

        assert check_palette(manifest["palette"], DEFAULT_TILES) == []
        assert manifest["palette"]["water"] != "#1a4a6b"  # repaired
        assert manifest["palette"]["danger"] == "#e84210"  # untouched

    def test_style_prompt_carries_constraints(self, tmp_path: Path) -> None:
        """I1: the agent reads its constraints in the prompt."""
        good = make_fake_responder()
        prompts = []

        def spy(request):
            if "### TASK: style" in request.user_message:
                prompts.append(request.user_message)
            return good(request)

        _run_slice(tmp_path / "run", responder=spy)
        assert len(prompts) == 1
        message = prompts[0]
        assert "### ROLES: background,ground,platform,wall,danger,water" in message
        assert "luminance distance >= 40" in message
        assert "dangerous at a glance" in message


class TestLavaWorld:
    """The 3b acceptance test: an alternate game folder (copied rules +
    tiles with lava-world entries) produces an observably different
    playable game — data only, no code."""

    LAVA_DIR = Path(__file__).parent.parent / "examples" / "lava_world"

    def _run(self, output_dir: Path) -> PipelineContext:
        from examples.platformer_pack.rules import load_rules

        return _run_slice(
            output_dir,
            rules=load_rules(self.LAVA_DIR / "game_rules.json"),
            tiles=load_tiles(self.LAVA_DIR / "tile_types.json"),
            variants=load_variants(),
        )

    def test_lava_world_generates_clean_and_different(
        self, tmp_path: Path
    ) -> None:
        run = tmp_path / "lava"
        ctx = self._run(run)
        # No fallbacks: the canned responder adapted to the lava
        # vocabulary offered by the registry-driven prompt.
        assert ctx.artifacts.get("slice_warnings", []) == []

        manifest = json.loads((run / "manifest.json").read_text())
        assert manifest["rules"]["enemy_water_policy"] == "amphibious"
        assert manifest["rules"]["platform_drop_through"] is False
        assert "lava" in [t["name"] for t in manifest["tiles"]]
        # The style agent styles THIS game's roles too (basalt ground,
        # lava pool) — data-driven end to end.
        assert manifest["palette"]["lava"] == "#e8722c"
        assert manifest["palette"]["basalt"] == "#5a4f5c"

        # The volume in the collision grid IS lava, damaging by data.
        tileset = ctx.bible.tilesets["ashen_depths"]
        lava_slot = next(s for s in tileset.slots if s.collision == "volume")
        assert lava_slot.name == "lava"
        assert lava_slot.params["damage_per_second"] == 1.0
        for level in ctx.bible.levels.values():
            with np.load(run / level.collision) as data:
                assert (data["collision"] == lava_slot.tile_type).any()

    def test_lava_world_differs_from_default_on_disk(
        self, tmp_path: Path
    ) -> None:
        default_run, lava_run = tmp_path / "default", tmp_path / "lava"
        _run_slice(default_run)
        self._run(lava_run)
        # Same seed, same template — different game where the data says
        # so (tilesheet colors, manifest vocabulary), identical where it
        # doesn't (world lore comes from the same canned responder).
        assert (
            (default_run / "tileset/ashen_depths/tilesheet.png").read_bytes()
            != (lava_run / "tileset/ashen_depths/tilesheet.png").read_bytes()
        )
        assert (
            (default_run / "world.json").read_bytes()
            == (lava_run / "world.json").read_bytes()
        )

    def test_lava_world_deterministic(self, tmp_path: Path) -> None:
        run_a, run_b = tmp_path / "a", tmp_path / "b"
        self._run(run_a)
        self._run(run_b)
        for rel in sorted(
            p.relative_to(run_a) for p in run_a.rglob("*") if p.is_file()
        ):
            assert (run_a / rel).read_bytes() == (run_b / rel).read_bytes(), rel
