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
        for level_id, dsl_text in _FAKE_LAYOUTS.items():
            result = stamp(dsl_text, W, H)
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

    def test_placement_rules(self) -> None:
        result = stamp(_FAKE_LAYOUTS["l1"], W, H)
        spawn = result.spawn
        accepted, problems = check_placements(
            result.grid,
            [
                {"enemy_id": "beetle", "x": 14, "y": 13},  # ok
                {"enemy_id": "beetle", "x": 3, "y": 13},  # too close to spawn
                {"enemy_id": "beetle", "x": 30, "y": 13},  # spike cell
                {"enemy_id": "ghost", "x": 14, "y": 13},  # unknown id
                {"enemy_id": "beetle", "x": 10, "y": 5},  # mid-air
            ],
            spawn,
            {"beetle"},
        )
        assert [p["x"] for p in accepted] == [14]
        assert len(problems) == 4


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
    output_dir: Path, seed: str = "emberfall_001", responder=None
) -> PipelineContext:
    ctx = PipelineContext(
        bible=Bible.empty(seed=seed),
        config=CanonConfig(seed=seed, output_dir=output_dir),
        rng=random.Random(seed),
        llm=LLMClient(FakeLLMBackend(responder or make_fake_responder())),
        prompts=PlatformerPrompts(),
    )
    run_pipeline(compose_pipeline(), ctx)
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
                cells = standable_cells(data["collision"])
            for placement in level.entities:
                assert tuple(placement.pos) in cells

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
        # The fallback levels are flat floors — spawn still standable.
        for level in ctx.bible.levels.values():
            assert level.spawn == (2, 13)

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
        assert len(ids) == len(set(ids)) == 3

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
