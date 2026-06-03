"""Contract tests for MazeLayoutPhase.

Tests verify observable contracts at the phase boundary:
- Phase protocol compliance (isinstance check)
- File shape written to disk (the persistence contract)
- In-memory state after run()
- Determinism with seeded RNG
- No-maps edge case

Does NOT:
- Mirror the maze algorithm
- Assert on exact grid cell values (algorithm detail)
- Reach into private attributes
"""
from __future__ import annotations

import json
import random
from pathlib import Path

from canon import Bible, CanonConfig, GenerationStats, Map
from canon.layout import MazeLayout
from canon.pipeline.phases.maze_layout import MazeLayoutPhase
from canon.pipeline.runner import Phase, PipelineContext

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def make_ctx(
    tmp_path: Path,
    num_maps: int = 2,
    seed: int = 42,
) -> PipelineContext:
    """Build a minimal PipelineContext with pre-populated Map objects.

    Tests pass ``path_template`` directly to ``MazeLayoutPhase(path_template=...)``
    rather than via config, so the fixture doesn't depend on CanonConfig growing
    an ``output_paths`` field.  ``output_dir`` is set on the config so
    ``write_per_map_file`` resolves files under ``tmp_path``.
    """
    bible = Bible.empty(seed="t")
    for i in range(num_maps):
        bible.maps[f"room_{i}"] = Map(
            map_id=f"room_{i}",
            name=f"Room {i}",
            description="",
            environment=["forest", "ruins"][i % 2],
            story_beat="",
        )
    config = CanonConfig(seed="t", output_dir=tmp_path)
    return PipelineContext(
        bible=bible,
        config=config,
        rng=random.Random(seed),
        stats=GenerationStats(),
    )


# ---------------------------------------------------------------------------
# Phase protocol compliance
# ---------------------------------------------------------------------------

class TestPhaseProtocol:
    def test_satisfies_phase_protocol(self) -> None:
        phase = MazeLayoutPhase()
        assert isinstance(phase, Phase)

    def test_name_is_maze_layout(self) -> None:
        phase = MazeLayoutPhase()
        assert phase.name == "maze_layout"


# ---------------------------------------------------------------------------
# Per-map layout generation
# ---------------------------------------------------------------------------

class TestPerMapLayoutGeneration:
    def test_generates_one_layout_per_map(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path, num_maps=3)
        phase = MazeLayoutPhase(path_template="rooms/{map_id}/maze.json")
        phase.run(ctx)
        layouts = [m.layout for m in ctx.bible.maps.values()]
        assert all(layout is not None for layout in layouts)
        assert len(layouts) == 3

    def test_each_layout_type_is_maze(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path, num_maps=2)
        phase = MazeLayoutPhase(path_template="rooms/{map_id}/maze.json")
        phase.run(ctx)
        for map_obj in ctx.bible.maps.values():
            assert isinstance(map_obj.layout, MazeLayout)
            assert map_obj.layout.layout_type == "maze"

    def test_dimensions_match_constructor_defaults(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path, num_maps=2)
        phase = MazeLayoutPhase(path_template="rooms/{map_id}/maze.json")
        phase.run(ctx)
        for map_obj in ctx.bible.maps.values():
            layout = map_obj.layout
            assert layout.width == 40
            assert layout.height == 30
            assert len(layout.grid) == 30
            assert all(len(row) == 40 for row in layout.grid)

    def test_custom_dimensions(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path, num_maps=1)
        phase = MazeLayoutPhase(
            width=20,
            height=15,
            path_template="rooms/{map_id}/maze.json",
        )
        phase.run(ctx)
        layout = ctx.bible.maps["room_0"].layout
        assert layout.width == 20
        assert layout.height == 15
        assert len(layout.grid) == 15
        assert all(len(row) == 20 for row in layout.grid)

    def test_map_layout_set_after_run(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path, num_maps=2)
        # Confirm layout is None before run
        for map_obj in ctx.bible.maps.values():
            assert map_obj.layout is None
        phase = MazeLayoutPhase(path_template="rooms/{map_id}/maze.json")
        phase.run(ctx)
        for map_obj in ctx.bible.maps.values():
            assert map_obj.layout is not None

    def test_environment_fields_propagated(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path, num_maps=2)
        phase = MazeLayoutPhase(path_template="rooms/{map_id}/maze.json")
        phase.run(ctx)
        for map_obj in ctx.bible.maps.values():
            layout = map_obj.layout
            assert layout.environment == map_obj.environment
            assert layout.environment_name == map_obj.name


# ---------------------------------------------------------------------------
# File persistence contract
# ---------------------------------------------------------------------------

class TestFilePersistence:
    def test_maze_json_exists_for_each_map(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path, num_maps=2)
        phase = MazeLayoutPhase(path_template="rooms/{map_id}/maze.json")
        phase.run(ctx)
        for map_id in ctx.bible.maps:
            expected = tmp_path / "rooms" / map_id / "maze.json"
            assert expected.exists(), f"Missing {expected}"

    def test_persisted_json_has_required_fields(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path, num_maps=1)
        phase = MazeLayoutPhase(path_template="rooms/{map_id}/maze.json")
        phase.run(ctx)
        maze_path = tmp_path / "rooms" / "room_0" / "maze.json"
        data = json.loads(maze_path.read_text(encoding="utf-8"))
        required = {
            "grid",
            "width",
            "height",
            "door_position",
            "player_start",
            "environment",
            "environment_name",
        }
        missing = required - set(data.keys())
        assert not missing, f"maze.json missing fields: {missing}"

    def test_persisted_grid_is_2d_list(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path, num_maps=1)
        phase = MazeLayoutPhase(path_template="rooms/{map_id}/maze.json")
        phase.run(ctx)
        maze_path = tmp_path / "rooms" / "room_0" / "maze.json"
        data = json.loads(maze_path.read_text(encoding="utf-8"))
        grid = data["grid"]
        assert isinstance(grid, list)
        assert all(isinstance(row, list) for row in grid)

    def test_persisted_dimensions_match(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path, num_maps=1)
        phase = MazeLayoutPhase(
            width=20,
            height=15,
            path_template="rooms/{map_id}/maze.json",
        )
        phase.run(ctx)
        maze_path = tmp_path / "rooms" / "room_0" / "maze.json"
        data = json.loads(maze_path.read_text(encoding="utf-8"))
        assert data["width"] == 20
        assert data["height"] == 15
        assert len(data["grid"]) == 15
        assert all(len(row) == 20 for row in data["grid"])

    def test_persisted_json_has_placement_fields(self, tmp_path: Path) -> None:
        """Placement fields should be present and empty (populated by later phases)."""
        ctx = make_ctx(tmp_path, num_maps=1)
        phase = MazeLayoutPhase(path_template="rooms/{map_id}/maze.json")
        phase.run(ctx)
        maze_path = tmp_path / "rooms" / "room_0" / "maze.json"
        data = json.loads(maze_path.read_text(encoding="utf-8"))
        # These are skeletal defaults; later phases populate them.
        assert "npc_positions" in data
        assert "item_placements" in data
        assert "event_positions" in data
        assert "quest_ids" in data

    def test_persisted_environment_matches_map(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path, num_maps=1)
        phase = MazeLayoutPhase(path_template="rooms/{map_id}/maze.json")
        phase.run(ctx)
        maze_path = tmp_path / "rooms" / "room_0" / "maze.json"
        data = json.loads(maze_path.read_text(encoding="utf-8"))
        assert data["environment"] == "forest"
        assert data["environment_name"] == "Room 0"

    def test_multiple_maps_produce_separate_files(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path, num_maps=3)
        phase = MazeLayoutPhase(path_template="rooms/{map_id}/maze.json")
        phase.run(ctx)
        for i in range(3):
            assert (tmp_path / "rooms" / f"room_{i}" / "maze.json").exists()


# ---------------------------------------------------------------------------
# Metadata stamping
# ---------------------------------------------------------------------------

class TestMetadataStamping:
    def test_phases_run_contains_maze_layout(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path, num_maps=2)
        phase = MazeLayoutPhase(path_template="rooms/{map_id}/maze.json")
        phase.run(ctx)
        assert "maze_layout" in ctx.bible.metadata.phases_run

    def test_phase_name_in_metadata_with_no_maps(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path, num_maps=0)
        phase = MazeLayoutPhase(path_template="rooms/{map_id}/maze.json")
        phase.run(ctx)
        assert "maze_layout" in ctx.bible.metadata.phases_run


# ---------------------------------------------------------------------------
# No-maps edge case
# ---------------------------------------------------------------------------

class TestNoMapsEdgeCase:
    def test_no_maps_does_not_raise(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path, num_maps=0)
        phase = MazeLayoutPhase(path_template="rooms/{map_id}/maze.json")
        # Should complete without error
        phase.run(ctx)

    def test_no_maps_writes_no_files(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path, num_maps=0)
        phase = MazeLayoutPhase(path_template="rooms/{map_id}/maze.json")
        phase.run(ctx)
        rooms_dir = tmp_path / "rooms"
        # No rooms directory should be created
        assert not rooms_dir.exists()


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_seed_produces_same_grids(self, tmp_path: Path) -> None:
        tmp_a = tmp_path / "run_a"
        tmp_b = tmp_path / "run_b"
        tmp_a.mkdir()
        tmp_b.mkdir()

        ctx_a = make_ctx(tmp_a, num_maps=2, seed=42)
        phase_a = MazeLayoutPhase(path_template="rooms/{map_id}/maze.json")
        phase_a.run(ctx_a)

        ctx_b = make_ctx(tmp_b, num_maps=2, seed=42)
        phase_b = MazeLayoutPhase(path_template="rooms/{map_id}/maze.json")
        phase_b.run(ctx_b)

        for map_id in ctx_a.bible.maps:
            grid_a = ctx_a.bible.maps[map_id].layout.grid
            grid_b = ctx_b.bible.maps[map_id].layout.grid
            assert grid_a == grid_b, (
                f"Grids for {map_id} differ across runs with same seed"
            )

    def test_different_seeds_produce_different_grids(self, tmp_path: Path) -> None:
        tmp_a = tmp_path / "run_a"
        tmp_b = tmp_path / "run_b"
        tmp_a.mkdir()
        tmp_b.mkdir()

        ctx_a = make_ctx(tmp_a, num_maps=1, seed=1)
        phase_a = MazeLayoutPhase(path_template="rooms/{map_id}/maze.json")
        phase_a.run(ctx_a)

        ctx_b = make_ctx(tmp_b, num_maps=1, seed=999)
        phase_b = MazeLayoutPhase(path_template="rooms/{map_id}/maze.json")
        phase_b.run(ctx_b)

        grid_a = ctx_a.bible.maps["room_0"].layout.grid
        grid_b = ctx_b.bible.maps["room_0"].layout.grid
        assert grid_a != grid_b

    def test_different_map_ids_produce_different_grids(self, tmp_path: Path) -> None:
        """Per-map seed mixing ensures room_0 != room_1 even with the same parent RNG."""
        ctx = make_ctx(tmp_path, num_maps=2, seed=42)
        phase = MazeLayoutPhase(path_template="rooms/{map_id}/maze.json")
        phase.run(ctx)

        grid_0 = ctx.bible.maps["room_0"].layout.grid
        grid_1 = ctx.bible.maps["room_1"].layout.grid
        assert grid_0 != grid_1, (
            "room_0 and room_1 should have different mazes due to per-map seed mixing"
        )
