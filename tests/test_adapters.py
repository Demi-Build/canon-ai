"""Tests for the OutputAdapter protocol and JsonOutputAdapter.

Covers:
- protocol conformance (JsonOutputAdapter satisfies OutputAdapter)
- path resolution rules (relative vs absolute)
- write behavior parity with the persistence helpers
- PipelineContext default adapter + backward compatibility
- fake-adapter injection: a phase routes its writes through ctx.adapter
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import pytest

from canon.adapters import JsonOutputAdapter, OutputAdapter
from canon.bible.models import Bible, Map
from canon.config import CanonConfig
from canon.pipeline.phases.maze_layout import MazeLayoutPhase
from canon.pipeline.runner import PipelineContext
from canon.pipeline.stats import GenerationStats

# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocol:
    def test_json_adapter_satisfies_protocol(self, tmp_path: Path) -> None:
        adapter = JsonOutputAdapter(tmp_path)
        assert isinstance(adapter, OutputAdapter)

    def test_importable_from_canon_adapters(self) -> None:
        from canon.adapters import JsonOutputAdapter, OutputAdapter  # noqa: F401


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


class TestResolvePath:
    def test_relative_joins_output_dir(self, tmp_path: Path) -> None:
        adapter = JsonOutputAdapter(tmp_path)
        assert adapter.resolve_path("npcs/npcs.json") == tmp_path / "npcs/npcs.json"

    def test_absolute_passes_through(self, tmp_path: Path) -> None:
        adapter = JsonOutputAdapter(tmp_path)
        absolute = tmp_path / "elsewhere" / "x.json"
        assert adapter.resolve_path(absolute) == absolute

    def test_accepts_str_and_path(self, tmp_path: Path) -> None:
        adapter = JsonOutputAdapter(tmp_path)
        assert adapter.resolve_path("a.json") == adapter.resolve_path(Path("a.json"))


# ---------------------------------------------------------------------------
# Write parity with persistence helpers
# ---------------------------------------------------------------------------


class TestJsonWrites:
    def test_write_json_array(self, tmp_path: Path) -> None:
        adapter = JsonOutputAdapter(tmp_path)
        adapter.write_json_array("npcs/npcs.json", [{"id": 1, "name": "A"}])
        data = json.loads((tmp_path / "npcs/npcs.json").read_text())
        assert data == [{"id": 1, "name": "A"}]

    def test_write_json_keyed(self, tmp_path: Path) -> None:
        adapter = JsonOutputAdapter(tmp_path)
        adapter.write_json_keyed("items.json", [{"id": 7, "name": "Key"}])
        data = json.loads((tmp_path / "items.json").read_text())
        assert data == {"7": {"id": 7, "name": "Key"}}

    def test_write_json_singleton(self, tmp_path: Path) -> None:
        adapter = JsonOutputAdapter(tmp_path)
        adapter.write_json_singleton("manifest.json", {"seed": "x"})
        assert json.loads((tmp_path / "manifest.json").read_text()) == {"seed": "x"}

    def test_write_per_map(self, tmp_path: Path) -> None:
        adapter = JsonOutputAdapter(tmp_path)
        adapter.write_per_map("rooms/{map_id}/maze.json", "room_0", {"w": 2})
        assert json.loads(
            (tmp_path / "rooms/room_0/maze.json").read_text()
        ) == {"w": 2}

    def test_indent_matches_persistence_helper(self, tmp_path: Path) -> None:
        """Adapter output must be byte-identical to a direct helper call."""
        from canon.persistence import write_array_db

        adapter = JsonOutputAdapter(tmp_path)
        adapter.write_json_array("via_adapter.json", [{"id": 1}])
        write_array_db(tmp_path / "direct.json", [{"id": 1}])
        assert (
            (tmp_path / "via_adapter.json").read_bytes()
            == (tmp_path / "direct.json").read_bytes()
        )


class TestContentHashReturns:
    """Every write_* returns "sha256:<hex>" of the exact written bytes
    (OutputAdapter contract, PRD §8.2)."""

    @staticmethod
    def _file_hash(path: Path) -> str:
        import hashlib

        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()

    def test_each_writer_returns_hash_of_written_file(self, tmp_path: Path) -> None:
        adapter = JsonOutputAdapter(tmp_path)
        cases = [
            (adapter.write_json_array("a.json", [{"id": 1}]), "a.json"),
            (adapter.write_json_keyed("k.json", [{"id": 2}]), "k.json"),
            (adapter.write_json_singleton("s.json", {"x": 1}), "s.json"),
            (adapter.write_per_map("r/{map_id}/m.json", "r0", {"y": 2}), "r/r0/m.json"),
            (adapter.write_binary("b.bin", b"\x00\x01"), "b.bin"),
        ]
        for returned, rel in cases:
            assert returned == self._file_hash(tmp_path / rel), rel

    def test_numpy_hash_matches_file(self, tmp_path: Path) -> None:
        numpy = pytest.importorskip("numpy")
        adapter = JsonOutputAdapter(tmp_path)
        returned = adapter.write_numpy("m.npz", grid=numpy.array([[1, 0]]))
        assert returned == self._file_hash(tmp_path / "m.npz")

    def test_identical_content_identical_hash(self, tmp_path: Path) -> None:
        adapter = JsonOutputAdapter(tmp_path)
        h1 = adapter.write_json_singleton("one.json", {"a": 1})
        h2 = adapter.write_json_singleton("two.json", {"a": 1})
        h3 = adapter.write_json_singleton("three.json", {"a": 2})
        assert h1 == h2 != h3


class TestBinaryWrites:
    def test_write_binary(self, tmp_path: Path) -> None:
        adapter = JsonOutputAdapter(tmp_path)
        adapter.write_binary("assets/blob.bin", b"\x00\x01\x02")
        assert (tmp_path / "assets/blob.bin").read_bytes() == b"\x00\x01\x02"

    def test_write_binary_leaves_no_temp_files(self, tmp_path: Path) -> None:
        adapter = JsonOutputAdapter(tmp_path)
        adapter.write_binary("blob.bin", b"data")
        leftovers = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
        assert leftovers == []

    def test_write_numpy(self, tmp_path: Path) -> None:
        numpy = pytest.importorskip("numpy")
        adapter = JsonOutputAdapter(tmp_path)
        grid = numpy.array([[0, 1], [1, 0]])
        adapter.write_numpy("grids/mask.npz", grid=grid)
        loaded = numpy.load(tmp_path / "grids/mask.npz")
        assert (loaded["grid"] == grid).all()


# ---------------------------------------------------------------------------
# PipelineContext integration
# ---------------------------------------------------------------------------


def _make_ctx(tmp_path: Path, adapter: Any = None) -> PipelineContext:
    bible = Bible.empty(seed="t")
    bible.maps["room_0"] = Map(
        map_id="room_0",
        name="Room 0",
        description="",
        environment="forest",
        story_beat="",
    )
    kwargs: dict[str, Any] = {}
    if adapter is not None:
        kwargs["adapter"] = adapter
    return PipelineContext(
        bible=bible,
        config=CanonConfig(seed="t", output_dir=tmp_path),
        rng=random.Random(42),
        stats=GenerationStats(),
        **kwargs,
    )


class TestContextDefaultAdapter:
    def test_default_adapter_is_json(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        assert isinstance(ctx.adapter, JsonOutputAdapter)
        assert ctx.adapter.output_dir == Path(tmp_path)

    def test_config_without_output_dir_falls_back_to_cwd(self) -> None:
        """Contexts built with configs lacking output_dir keep working
        (historical phase fallback was '.')."""

        class BareConfig:
            seed = "x"

        ctx = PipelineContext(
            bible=Bible.empty(seed="x"),
            config=BareConfig(),
            rng=random.Random(0),
        )
        assert isinstance(ctx.adapter, JsonOutputAdapter)
        assert ctx.adapter.output_dir == Path(".")


class RecordingAdapter:
    """Fake OutputAdapter that records calls instead of writing files."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def write_json_array(self, path, entities) -> None:
        self.calls.append(("write_json_array", str(path), entities))

    def write_json_keyed(self, path, entities, key_field="id") -> None:
        self.calls.append(("write_json_keyed", str(path), entities, key_field))

    def write_json_singleton(self, path, obj) -> None:
        self.calls.append(("write_json_singleton", str(path), obj))

    def write_binary(self, path, data) -> None:
        self.calls.append(("write_binary", str(path), data))

    def write_numpy(self, path, **arrays) -> None:
        self.calls.append(("write_numpy", str(path), sorted(arrays)))

    def write_per_map(self, template, map_id, obj) -> None:
        self.calls.append(("write_per_map", str(template), map_id))

    def resolve_path(self, relative) -> Path:
        return Path(relative)


class TestAdapterInjection:
    def test_fake_adapter_satisfies_protocol(self) -> None:
        assert isinstance(RecordingAdapter(), OutputAdapter)

    def test_phase_routes_writes_through_injected_adapter(
        self, tmp_path: Path
    ) -> None:
        """Smoke test for the injection point: MazeLayoutPhase writes go to
        the injected adapter, and nothing lands on disk."""
        fake = RecordingAdapter()
        ctx = _make_ctx(tmp_path, adapter=fake)

        MazeLayoutPhase(width=11, height=9).run(ctx)

        assert fake.calls == [
            ("write_per_map", "rooms/{map_id}/maze.json", "room_0")
        ]
        assert not (tmp_path / "rooms").exists()

    def test_pack_phase_routes_writes_through_injected_adapter(
        self, tmp_path: Path
    ) -> None:
        """The mazeworld pack respects injected adapters too: placement's
        maze.json rewrite goes to the fake, and nothing lands on disk."""
        from examples.mazeworld_pack.placement import MazeworldPlacementPhase

        fake = RecordingAdapter()
        ctx = _make_ctx(tmp_path, adapter=fake)

        # Layout phase populates map.layout in memory (write goes to fake).
        MazeLayoutPhase(width=11, height=9).run(ctx)
        MazeworldPlacementPhase().run(ctx)

        per_map_calls = [c for c in fake.calls if c[0] == "write_per_map"]
        assert per_map_calls == [
            ("write_per_map", "rooms/{map_id}/maze.json", "room_0"),  # layout
            ("write_per_map", "rooms/{map_id}/maze.json", "room_0"),  # placement
        ]
        assert not (tmp_path / "rooms").exists()
