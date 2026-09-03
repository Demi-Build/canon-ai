"""Tests for row A3 — the read-tier tools, ``describe_level`` and the
windowed ``export_level_bundle`` (Phase 1 §3.2, §3.4, §4.A/§4.B).

Hermetic and $0: the pack is a fake-backend tree generated ONCE per module
with the widest layout the slice makes quickly (2 stages × 3 levels, pinned
seed — the same runner the suite already uses), the dungeon side is the
reference fixture, and every conversation runs on ``FakeChatBackend``.

Read verbs write NOTHING: the generated tree is hashed once after
generation and an autouse fixture re-hashes it after EVERY test in this
module — a tool or verb that wrote a byte fails the test that ran it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("PIL")

from canon.adapters import GRID_DESCRIBERS, GRID_READERS, GRID_ROOM_ROW, grid_verb  # noqa: E402
from canon.adapters.platformer_read import (  # noqa: E402
    describe_level,
    export_level_bundle,
    normalize_window,
    platform_bands,
)
from canon.agent.loop import run_conversation  # noqa: E402
from canon.agent.registry import ToolRegistry  # noqa: E402
from canon.agent.tools_read import (  # noqa: E402
    LIST_CAP,
    READ_CAP_BYTES,
    READ_TIER,
    READ_TOOL_NAMES,
    SEARCH_CAP,
    ToolInputError,
    compact,
    grid_ids,
    guard_path,
    is_text_file,
    read_tool_specs,
    register_read_tools,
    validate_input,
)
from canon.backends.testing import FakeChatBackend  # noqa: E402
from canon.packs import pack_info  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
CANON = [sys.executable, "-m", "canon.cli.main"]
DUNGEON_FIXTURE = REPO / "tests" / "reference" / "fixtures" / "cradle_mazeworld_scifi"

#: The A3 gate's budgets, in approximate tokens (chars / 4).
DESCRIBE_BUDGET_TOKENS = 2_500
WINDOW_BUDGET_TOKENS = 6_000
WINDOW = (24, 16)

# ---------------------------------------------------------------------------
# Helpers + fixtures
# ---------------------------------------------------------------------------


def _tree(root: Path) -> dict[str, str]:
    """Every file under *root* → sha256: the before/after pin for "read verbs write nothing"."""
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _canon(*args: str) -> tuple[int, object]:
    result = subprocess.run(CANON + list(args), capture_output=True, text=True, cwd=REPO)
    stream = result.stdout if result.returncode == 0 else result.stderr
    try:
        return result.returncode, json.loads(stream)
    except json.JSONDecodeError:
        return result.returncode, stream


def _approx_tokens(text: str) -> int:
    """The gate's yardstick: chars / 4, stated as approximate everywhere."""
    return len(text) // 4


def _registry(pack: Path) -> ToolRegistry:
    registry = ToolRegistry()
    register_read_tools(registry, pack)
    return registry


def _run(registry: ToolRegistry, name: str, **tool_input) -> dict:
    """Execute a registered tool the way the service does and parse its JSON string."""
    out = registry.execute(name, tool_input, actor="agent:test", conversation="test")
    assert isinstance(out, str)
    assert out == json.dumps(json.loads(out), separators=(",", ":"), ensure_ascii=False)  # compact
    return json.loads(out)


@pytest.fixture(scope="module")
def wide_pack(tmp_path_factory) -> Path:
    """The widest fake tree the slice makes quickly: 2 stages × 3 levels,
    pinned seed (the same tree the A3 gate numbers were measured on)."""
    out = tmp_path_factory.mktemp("a3_wide")
    subprocess.run(
        [
            sys.executable, "-m", "canon.packs.platformer.run_slice",
            "--backend", "fake", "--engine", "json", "--image-backend", "fake",
            "--music-backend", "none", "--sfx-backend", "none",
            "--num-stages", "2", "--num-levels", "3", "--num-enemies", "4", "--num-items", "4",
            "--seed", "a3-wide", "--output-dir", str(out),
        ],
        check=True,
        capture_output=True,
        cwd=REPO,
    )
    return out


@pytest.fixture(scope="module")
def wide_snapshot(wide_pack: Path) -> dict[str, str]:
    return _tree(wide_pack)


@pytest.fixture(autouse=True)
def _pack_untouched(wide_pack: Path, wide_snapshot: dict[str, str]):
    """Doctrine 1 for reads: after every test the generated tree is byte-identical."""
    yield
    assert _tree(wide_pack) == wide_snapshot, "a read verb or tool wrote into the pack"


@pytest.fixture(scope="module")
def level_ids(wide_pack: Path) -> list[str]:
    return [entry["level_id"] for entry in grid_ids(wide_pack, "level/{stage_id}/{level_id}/")]


@pytest.fixture(scope="module")
def widest(wide_pack: Path, level_ids: list[str]) -> str:
    """The level with the largest ``grid_width`` — the gate's measurement subject."""

    def width(level_id: str) -> int:
        stage = next(p for p in (wide_pack / "level").iterdir() if (p / level_id / "level.json").is_file())
        return int(json.loads((stage / level_id / "level.json").read_text(encoding="utf-8"))["grid_width"])

    return max(level_ids, key=width)


# ---------------------------------------------------------------------------
# The canon verbs: describe_level + windowed export_level_bundle
# ---------------------------------------------------------------------------


class TestDescribeLevel:
    KEYS = {
        "level_id", "stage_id", "display_name", "brief", "dims", "spawn", "exit", "rooms", "parent_level",
        "tiles", "platforms", "entities", "items", "triggers", "hazards", "overrides", "validation",
        "revision", "revision_short", "last_change",
    }

    def test_shape_and_counts_agree_with_the_bundle(self, wide_pack: Path, level_ids: list[str]) -> None:
        for level_id in level_ids:
            summary = describe_level(wide_pack, level_id)
            bundle = export_level_bundle(wide_pack, level_id)
            assert set(summary) == self.KEYS
            assert summary["level_id"] == level_id and summary["stage_id"] == bundle["stage_id"]
            level = json.loads(
                (wide_pack / "level" / bundle["stage_id"] / level_id / "level.json").read_text(encoding="utf-8")
            )
            assert summary["dims"] == {
                "width": bundle["grid_width"], "height": bundle["grid_height"],
                "axis": level.get("layout_axis", "horizontal"),
            }
            assert summary["spawn"] == bundle["spawn"] and summary["exit"] == bundle["exit"]
            assert summary["revision"] == bundle["revision"] and summary["revision_short"] == bundle["revision_short"]
            # histogram covers every cell, categories are the registry's
            cells = bundle["grid_width"] * bundle["grid_height"]
            assert summary["tiles"]["cells"] == cells
            assert sum(summary["tiles"]["by_category"].values()) == cells
            assert set(summary["tiles"]["by_category"]) <= {"empty", "solid", "one_way", "hazard", "volume"}
            # placements: counts by archetype/kind + positions match the bundle
            assert summary["entities"]["count"] == len(bundle["entities"])
            assert sum(summary["entities"]["by_archetype"].values()) == len(bundle["entities"])
            assert [(e["id"], e["x"], e["y"]) for e in summary["entities"]["placed"]] == [
                (e["enemy_id"], e["x"], e["y"]) for e in bundle["entities"]
            ]
            assert summary["items"]["count"] == len(bundle["items"])
            assert sum(summary["items"]["by_kind"].values()) == len(bundle["items"])
            assert [(i["id"], i["kind"], i["source"]) for i in summary["items"]["placed"]] == [
                (i["item_id"], i["kind"], i["source"]) for i in bundle["items"]
            ]
            assert summary["triggers"]["count"] == len(bundle["triggers"])
            assert sum(summary["triggers"]["by_type"].values()) == len(bundle["triggers"])
            assert summary["hazards"]["count"] == len(bundle["hazards"])
            # per-level overrides are level.json's (the fake tree carries a gravity override on one level)
            assert summary["overrides"] == {
                "rules": level.get("rules_overrides") or {}, "movement": level.get("movement_overrides") or {},
            }
            # the verdict summary is validate_level's, counted
            assert summary["validation"]["ok"] is True
            assert set(summary["validation"]["problems"]) == {"terrain", "enemies", "items"}
            assert "grids" not in summary  # never the grid itself

    def test_secret_rooms_ride_along(self, wide_pack: Path) -> None:
        parent = describe_level(wide_pack, "l1")
        assert parent["rooms"] == ["l1r1"] and parent["parent_level"] is None
        assert parent["validation"]["rooms"] == [{"level_id": "l1r1", "ok": True}]
        assert parent["triggers"]["by_type"].get("room_entrance") == 1
        room = describe_level(wide_pack, "l1r1")
        assert room["parent_level"] == "l1" and room["rooms"] == []

    def test_platform_bands_are_run_length_spans_of_the_grid(self, wide_pack: Path, widest: str) -> None:
        summary = describe_level(wide_pack, widest)
        grid = export_level_bundle(wide_pack, widest)["grids"]["collision"]
        manifest = json.loads((wide_pack / "manifest.json").read_text(encoding="utf-8"))
        categories = {int(t["id"]): t["category"] for t in manifest["tiles"]}
        bands = summary["platforms"]
        assert bands == platform_bands(grid, categories)
        # every band is a run of adjacent rows; bands never overlap and are ordered
        last_row = -1
        for band in bands:
            y0, y1 = band["rows"]
            assert last_row < y0 <= y1
            last_row = y1
            for category, spans in band.items():
                if category == "rows":
                    continue
                for x0, x1 in spans:
                    assert 0 <= x0 <= x1 < len(grid[0])
                    for y in range(y0, y1 + 1):
                        assert {categories[grid[y][x]] for x in range(x0, x1 + 1)} == {category}
        # …and together they cover every non-empty cell exactly once
        covered = sum(
            (band["rows"][1] - band["rows"][0] + 1) * (x1 - x0 + 1)
            for band in bands
            for category, spans in band.items()
            if category != "rows"
            for x0, x1 in spans
        )
        non_empty = sum(1 for row in grid for value in row if categories[value] != "empty")
        assert covered == non_empty
        assert all(row for row in grid)  # a real level, not a stub

    def test_unknown_level_is_file_not_found(self, wide_pack: Path) -> None:
        with pytest.raises(FileNotFoundError, match="l99"):
            describe_level(wide_pack, "l99")


class TestWindowedExport:
    def test_no_window_is_the_pre_a3_document(self, wide_pack: Path) -> None:
        bundle = export_level_bundle(wide_pack, "l1")
        assert "window" not in bundle
        assert len(bundle["grids"]["collision"]) == bundle["grid_height"]
        assert len(bundle["grids"]["collision"][0]) == bundle["grid_width"]

    def test_window_slices_grids_and_filters_records_absolute_coordinates(self, wide_pack: Path, widest: str) -> None:
        full = export_level_bundle(wide_pack, widest)
        x0, y0, w, h = 20, 4, *WINDOW
        windowed = export_level_bundle(wide_pack, widest, window=(x0, y0, w, h))
        assert windowed["window"] == {"x0": x0, "y0": y0, "w": w, "h": h}
        assert windowed["grid_width"] == full["grid_width"] and windowed["grid_height"] == full["grid_height"]
        for name, grid in windowed["grids"].items():
            assert len(grid) == h and all(len(row) == w for row in grid)
            assert grid == [row[x0 : x0 + w] for row in full["grids"][name][y0 : y0 + h]]
        for layer in ("hazards", "triggers", "foreground", "entities", "items"):
            inside = [r for r in full[layer] if x0 <= r["x"] < x0 + w and y0 <= r["y"] < y0 + h]
            assert windowed[layer] == inside, layer
        # everything that is not per-cell rides along untouched
        for key in ("tileset", "tiles_by_type", "spawn", "exit", "revision", "props", "backdrop", "music_path"):
            assert windowed[key] == full[key]
        assert set(windowed) == set(full) | {"window"}

    def test_window_is_clamped_to_the_far_edge(self, wide_pack: Path, widest: str) -> None:
        full = export_level_bundle(wide_pack, widest)
        width, height = full["grid_width"], full["grid_height"]
        windowed = export_level_bundle(wide_pack, widest, window=(width - 5, height - 3, 24, 16))
        assert windowed["window"] == {"x0": width - 5, "y0": height - 3, "w": 5, "h": 3}
        assert len(windowed["grids"]["collision"]) == 3 and len(windowed["grids"]["collision"][0]) == 5

    @pytest.mark.parametrize(
        "window, message",
        [
            ((1, 2, 3), "four integers"),
            (("a", 0, 1, 1), "four integers"),
            ((0, 0, 0, 5), "positive"),
            ((-1, 0, 5, 5), "non-negative"),
            ((999, 0, 5, 5), "outside"),
        ],
    )
    def test_malformed_windows_are_value_errors(self, wide_pack: Path, window, message: str) -> None:
        with pytest.raises(ValueError, match=message):
            export_level_bundle(wide_pack, "l1", window=window)

    def test_normalize_window_alone(self) -> None:
        assert normalize_window([0, 0, 24, 16], 10, 10) == {"x0": 0, "y0": 0, "w": 10, "h": 10}
        assert normalize_window((3, 2, 4, 4), 10, 10) == {"x0": 3, "y0": 2, "w": 4, "h": 4}


class TestTokenBudget:
    """The A3 gate: worst-case turn size on the widest level of the widest
    fake tree. Approximate tokens = chars / 4 of the compact JSON the tool
    actually returns."""

    def test_describe_and_window_fit_the_budgets(self, wide_pack: Path, widest: str, capsys) -> None:
        registry = _registry(wide_pack)
        describe = registry.execute("describe_level", {"level_id": widest}, actor="a", conversation="c")
        window = registry.execute(
            "export_level", {"level_id": widest, "window": [0, 0, *WINDOW]}, actor="a", conversation="c"
        )
        full = registry.execute("export_level", {"level_id": widest}, actor="a", conversation="c")
        dims = json.loads(describe)["dims"]
        numbers = {
            "widest_level": widest,
            "dims": f"{dims['width']}x{dims['height']}",
            "describe_level": (len(describe), _approx_tokens(describe)),
            "export_level_full": (len(full), _approx_tokens(full)),
            f"export_level_{WINDOW[0]}x{WINDOW[1]}": (len(window), _approx_tokens(window)),
        }
        with capsys.disabled():
            print(f"\nA3 token budget (chars, ~tokens=chars/4): {numbers}")
        assert _approx_tokens(describe) <= DESCRIBE_BUDGET_TOKENS
        assert _approx_tokens(window) <= WINDOW_BUDGET_TOKENS
        # the window is the frugal read: strictly smaller than the full dump
        assert len(window) < len(full)


# ---------------------------------------------------------------------------
# The registered tools
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_every_read_tool_is_auto_tier_in_order(self, wide_pack: Path) -> None:
        registry = _registry(wide_pack)
        assert registry.names() == list(READ_TOOL_NAMES)
        assert READ_TIER == "auto"
        for name in READ_TOOL_NAMES:
            tool = registry.get(name)
            assert tool.tier == "auto" and tool.touches and "write" not in tool.touches
            assert tool.spec.description and tool.spec.input_schema["type"] == "object"
            assert tool.spec.input_schema.get("additionalProperties") is False
        assert [spec.name for spec in read_tool_specs()] == list(READ_TOOL_NAMES)

    def test_registration_reads_nothing_and_a_second_pack_gets_its_own_binding(self, tmp_path: Path) -> None:
        stub = tmp_path / "stub"
        stub.mkdir()
        registry = ToolRegistry()
        assert register_read_tools(registry, stub) == list(READ_TOOL_NAMES)
        assert sorted(p.name for p in stub.iterdir()) == []
        with pytest.raises(ValueError):  # PackTypeError at call time, never at registration
            registry.execute("describe_pack", {}, actor="a", conversation="c")

    def test_double_registration_is_refused(self, wide_pack: Path) -> None:
        registry = _registry(wide_pack)
        with pytest.raises(ValueError, match="already registered"):
            register_read_tools(registry, wide_pack)


class TestInputValidation:
    def test_missing_required_wrong_type_and_unknown_field(self, wide_pack: Path) -> None:
        registry = _registry(wide_pack)
        with pytest.raises(ToolInputError, match="level_id is required"):
            registry.execute("describe_level", {}, actor="a", conversation="c")
        with pytest.raises(ToolInputError, match="must be string"):
            registry.execute("describe_level", {"level_id": 5}, actor="a", conversation="c")
        with pytest.raises(ToolInputError, match="not an accepted field"):
            registry.execute("describe_level", {"level_id": "l1", "extra": 1}, actor="a", conversation="c")
        with pytest.raises(ToolInputError, match="at least 4 items"):
            registry.execute("export_level", {"level_id": "l1", "window": [1, 2, 3]}, actor="a", conversation="c")
        with pytest.raises(ToolInputError, match=r"window\[0\] must be >= 0"):
            registry.execute("export_level", {"level_id": "l1", "window": [-1, 0, 4, 4]}, actor="a", conversation="c")
        with pytest.raises(ToolInputError, match="must be integer"):
            registry.execute("get_history", {"limit": True}, actor="a", conversation="c")

    def test_validate_input_covers_its_subset(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "n": {"type": "integer", "minimum": 1, "maximum": 3},
                "s": {"type": "string", "enum": ["a", "b"]},
                "xs": {"type": "array", "items": {"type": "number"}, "maxItems": 2},
            },
            "required": ["n"],
            "additionalProperties": False,
        }
        validate_input("t", schema, {"n": 2, "s": "a", "xs": [1, 2.5]})
        for bad, message in [
            ({"n": 0}, ">= 1"),
            ({"n": 4}, "<= 3"),
            ({"n": 1, "s": "z"}, "one of"),
            ({"n": 1, "xs": [1, 2, 3]}, "at most 2"),
            ({"n": 1, "xs": ["x"]}, "must be number"),
            ({"n": 1.5}, "must be integer"),
            ([], "must be object"),
        ]:
            with pytest.raises(ToolInputError, match=message):
                validate_input("t", schema, bad)


class TestPackTools:
    def test_describe_pack_is_pack_info_plus_title_and_grid_ids(self, wide_pack: Path, level_ids: list[str]) -> None:
        registry = _registry(wide_pack)
        probe = _run(registry, "describe_pack")
        info = pack_info(wide_pack)
        assert {k: v for k, v in probe.items() if k not in ("title", "grid_ids")} == info
        assert probe["title"] == json.loads((wide_pack / "world.json").read_text(encoding="utf-8"))["title"]
        assert probe["grid_ids"] == {
            "level": [
                {"stage_id": stage, "level_id": level}
                for stage, level in sorted(
                    (p.parent.name, p.name) for p in (wide_pack / "level").glob("*/*") if p.is_dir()
                )
            ]
        }
        assert [e["level_id"] for e in probe["grid_ids"]["level"]] == level_ids
        assert probe["entities"]["enemy"]["count"] == 4 and probe["entities"]["item"]["count"] == 4

    def test_describe_export_validate_are_the_verbs(self, wide_pack: Path, widest: str) -> None:
        registry = _registry(wide_pack)
        summary = _run(registry, "describe_level", level_id=widest)
        assert summary == json.loads(compact(describe_level(wide_pack, widest)))
        window = _run(registry, "export_level", level_id=widest, window=[2, 1, 10, 6])
        assert window == json.loads(compact(export_level_bundle(wide_pack, widest, window=(2, 1, 10, 6))))
        full = _run(registry, "export_level", level_id=widest)
        assert full == json.loads(compact(export_level_bundle(wide_pack, widest)))
        from canon.packs.platformer.ops import validate_level

        report = _run(registry, "validate_level", level_id=widest)
        assert report == json.loads(compact(validate_level(wide_pack, widest)))
        assert report["ok"] is True and [c["name"] for c in report["checks"]] == ["terrain", "enemies", "items"]

    def test_db_types_schema_row(self, wide_pack: Path) -> None:
        registry = _registry(wide_pack)
        types = _run(registry, "db_types")
        assert set(types) == {"enemy", "item"}
        enemy = types["enemy"]
        assert enemy["id_field"] == "enemy_id" and enemy["llm_fields"] == ["name", "flavor"]
        assert enemy["schema"] == "schemas/enemy.json" and enemy["schema_source"] == "template"
        assert "enemy_id" in enemy["protected"] and enemy["count"] == 4 and enemy["placeable"] is True
        for key in ("label", "layout", "code_fields", "user_fields", "vocab"):
            assert key in enemy

        schema = _run(registry, "db_schema", type="enemy")
        assert schema["source"] == "template" and schema["type"] == "enemy"
        assert "archetype" in schema["schema"]["fields"]
        assert Path(schema["path"]).name == "enemy.json"
        with pytest.raises(ValueError, match="unknown db type"):
            registry.execute("db_schema", {"type": "dragon"}, actor="a", conversation="c")

        ids = sorted(p.stem for p in (wide_pack / "enemy").glob("*.json"))
        row = _run(registry, "db_row", type="enemy", id=ids[0])
        on_disk = json.loads((wide_pack / "enemy" / f"{ids[0]}.json").read_text(encoding="utf-8"))
        assert row["type"] == "enemy" and row["id"] == ids[0]
        assert row["row"]["enemy_id"] == ids[0] and row["row"]["archetype"] == on_disk["archetype"]
        assert row["row"]["name"] == on_disk["name"]
        with pytest.raises(ValueError, match=re.escape(f"known ids: {ids}")):
            registry.execute("db_row", {"type": "enemy", "id": "nope"}, actor="a", conversation="c")

    def test_db_schema_prefers_the_pack_override(self, wide_pack: Path, tmp_path: Path) -> None:
        copy = tmp_path / "override"
        shutil.copytree(wide_pack, copy)
        template = _run(_registry(copy), "db_schema", type="item")
        local = copy / "schemas" / "item.json"
        local.parent.mkdir()
        local.write_text(json.dumps(template["schema"]), encoding="utf-8")
        registry = _registry(copy)
        assert _run(registry, "db_schema", type="item")["source"] == "pack"
        assert _run(registry, "db_types")["item"]["schema_source"] == "pack"

    def test_history_and_versions_read_the_journal(self, wide_pack: Path, tmp_path: Path) -> None:
        registry = _registry(wide_pack)
        assert _run(registry, "get_history") == {
            "target": None, "total": 0, "returned": 0, "truncated": False, "events": [],
        }
        assert _run(registry, "get_versions", target="enemy:nope") == {
            "artifact_id": "enemy:nope", "count": 0, "versions": [],
        }
        # a journaled copy: baseline every step of l1, then read it back
        from canon.adapters.platformer_write import baseline_level

        copy = tmp_path / "journaled"
        shutil.copytree(wide_pack, copy)
        baseline_level(copy, "l1", actor="test", detail={"kind": "generate"})
        registry = _registry(copy)
        history = _run(registry, "get_history", target="level:ashen_depths/l1/")
        assert history["total"] == history["returned"] > 0 and history["truncated"] is False
        assert all(e["artifact_id"].startswith("level:ashen_depths/l1/") for e in history["events"])
        assert {e["kind"] for e in history["events"]} == {"generate"}
        assert set(history["events"][0]) >= {"ts", "artifact_id", "op", "source", "actor", "kind", "after_hash"}
        assert "detail" not in history["events"][0]  # compact: never the prompts
        limited = _run(registry, "get_history", target="level:", limit=2)
        assert limited["returned"] == 2 and limited["truncated"] is True
        assert limited["events"] == history["events"][-2:]
        assert _run(registry, "get_history", target="enemy:")["total"] == 0
        versions = _run(registry, "get_versions", target="level:ashen_depths/l1/entities")
        assert versions["count"] == 1 and set(versions["versions"][0]) == {"ts", "op", "source", "actor", "hash"}
        assert versions["versions"][0]["hash"] == history["events"][0]["after_hash"] or any(
            v["hash"] == e["after_hash"] for v in versions["versions"] for e in history["events"]
        )


class TestPathGuardedFiles:
    def test_list_files_paths_sizes_and_glob(self, wide_pack: Path, wide_snapshot: dict[str, str]) -> None:
        registry = _registry(wide_pack)
        listing = _run(registry, "list_pack_files")
        assert listing["glob"] == "**/*" and listing["truncated"] is (listing["count"] > LIST_CAP)
        paths = {f["path"] for f in listing["files"]}
        assert paths <= set(wide_snapshot)
        assert "manifest.json" in paths and "world.json" in paths
        for entry in listing["files"]:
            assert entry["size"] == (wide_pack / entry["path"]).stat().st_size
        enemies = _run(registry, "list_pack_files", glob="enemy/*.json")
        assert [f["path"] for f in enemies["files"]] == sorted(
            str(p.relative_to(wide_pack)) for p in (wide_pack / "enemy").glob("*.json")
        )
        assert _run(registry, "list_pack_files", glob="level/**/level.json")["count"] == 9

    def test_guard_refuses_escapes_and_hides_the_object_store(self, wide_pack: Path, tmp_path: Path) -> None:
        copy = tmp_path / "guarded"
        shutil.copytree(wide_pack, copy)
        outside = tmp_path / "outside.txt"
        outside.write_text("secret outside the pack", encoding="utf-8")
        (copy / "escape.txt").symlink_to(outside)
        (copy / "escape_dir").symlink_to(tmp_path)
        objects = copy / ".canon" / "objects"
        objects.mkdir(parents=True)
        (objects / "deadbeef").write_bytes(b"stored bytes")
        registry = _registry(copy)

        for bad, message in [
            ("../outside.txt", "'..'"),
            ("enemy/../../outside.txt", "'..'"),
            (str(outside), "absolute"),
            ("/etc/hosts", "absolute"),
            ("escape.txt", "escapes"),
            ("escape_dir/outside.txt", "escapes"),
            (".canon/objects/deadbeef", "object store"),
            ("", "non-empty"),
        ]:
            with pytest.raises(ValueError, match=message):
                registry.execute("read_pack_file", {"path": bad}, actor="a", conversation="c")
            with pytest.raises(ValueError, match=message):
                guard_path(copy, bad)
        listed = {f["path"] for f in _run(registry, "list_pack_files")["files"]}
        assert "escape.txt" not in listed and not any(p.startswith("escape_dir") for p in listed)
        assert not any(p.startswith(os.path.join(".canon", "objects")) for p in listed)
        canon_dir = _run(registry, "list_pack_files", glob=".canon/**/*")
        assert ".canon/objects/deadbeef" not in {f["path"] for f in canon_dir["files"]}
        # a glob that only reaches guarded regions says so: skipped counts, not a bare {count: 0}
        assert _run(registry, "list_pack_files", glob="escape_dir/*")["count"] == 0
        assert _run(registry, "list_pack_files", glob="escape_dir/*")["skipped"]["escapes"] >= 1
        store_only = _run(registry, "list_pack_files", glob=".canon/objects/*")
        assert store_only["count"] == 0 and store_only["skipped"] == {"escapes": 0, "object_store": 1}
        assert _run(registry, "list_pack_files", glob="enemy/*.json")["skipped"] == {"escapes": 0, "object_store": 0}
        guarded_search = _run(registry, "search_pack", query="stored bytes", glob=".canon/objects/*")
        assert guarded_search["count"] == 0 and guarded_search["skipped"]["object_store"] == 1
        with pytest.raises(ValueError, match="'..'"):
            registry.execute("list_pack_files", {"glob": "../*"}, actor="a", conversation="c")
        with pytest.raises(ValueError, match="relative to the pack root"):
            registry.execute("search_pack", {"query": "x", "glob": "/etc/*"}, actor="a", conversation="c")
        stored = _run(registry, "search_pack", query="stored bytes")
        assert not any(m["path"].startswith(".canon/objects") for m in stored["matches"])
        assert _run(registry, "search_pack", query="secret outside")["count"] == 0

    def test_read_pack_file_text_range_binary_and_cap(self, wide_pack: Path, tmp_path: Path) -> None:
        registry = _registry(wide_pack)
        whole = _run(registry, "read_pack_file", path="manifest.json")
        text = (wide_pack / "manifest.json").read_text(encoding="utf-8")
        assert whole == {"path": "manifest.json", "size": len(text.encode()), "truncated": False, "text": text}
        assert json.loads(whole["text"])["pack_type"] == "platformer"  # JSON files come back as text

        ranged = _run(registry, "read_pack_file", path="manifest.json", range=[2, 4])
        assert ranged["text"] == "\n".join(text.splitlines()[1:4])
        assert ranged["lines"] == [2, 4] and ranged["total_lines"] == len(text.splitlines())
        past_end = _run(registry, "read_pack_file", path="manifest.json", range=[1, 10_000])
        assert past_end["lines"] == [1, len(text.splitlines())]
        with pytest.raises(ValueError, match="1 <= start <= end"):
            registry.execute("read_pack_file", {"path": "manifest.json", "range": [4, 2]}, actor="a", conversation="c")

        binaries = [("level/ashen_depths/l1/collision.npz", "npz"), ("tileset/ashen_depths/tilesheet.png", "png")]
        for binary, suffix in binaries:
            with pytest.raises(ValueError, match=rf"is binary \(\.{suffix}"):
                registry.execute("read_pack_file", {"path": binary}, actor="a", conversation="c")
        with pytest.raises(FileNotFoundError, match="no such file"):
            registry.execute("read_pack_file", {"path": "nope.json"}, actor="a", conversation="c")

        copy = tmp_path / "capped"
        shutil.copytree(wide_pack, copy)
        (copy / "big.txt").write_text("x" * (READ_CAP_BYTES + 10), encoding="utf-8")
        (copy / "blob.bin").write_bytes(b"\x00\x01\x02 not text")
        registry = _registry(copy)
        big = _run(registry, "read_pack_file", path="big.txt")
        assert big["truncated"] is True and big["truncation"] == "size_cap" and big["cap_bytes"] == READ_CAP_BYTES
        assert len(big["text"]) == READ_CAP_BYTES and big["size"] == READ_CAP_BYTES + 10
        with pytest.raises(ValueError, match="is binary"):
            registry.execute("read_pack_file", {"path": "blob.bin"}, actor="a", conversation="c")
        assert is_text_file(copy / "manifest.json") and not is_text_file(copy / "blob.bin")

    def test_search_pack_matches_with_line_numbers(self, wide_pack: Path) -> None:
        registry = _registry(wide_pack)
        enemy_id = sorted(p.stem for p in (wide_pack / "enemy").glob("*.json"))[0]
        found = _run(registry, "search_pack", query=enemy_id.upper(), glob="level/**/entities.json")
        assert found["count"] > 0 and found["truncated"] is False and found["files_scanned"] > 0
        for match in found["matches"]:
            assert match["path"].endswith("entities.json")
            line = (wide_pack / match["path"]).read_text(encoding="utf-8").splitlines()[match["line"] - 1]
            assert enemy_id in line and match["text"] == line.strip()
        everywhere = _run(registry, "search_pack", query=enemy_id)
        assert everywhere["count"] >= found["count"]
        assert not any(m["path"].endswith((".npz", ".png")) for m in everywhere["matches"])
        capped = _run(registry, "search_pack", query='"')
        assert capped["truncated"] is True and capped["count"] == SEARCH_CAP
        assert _run(registry, "search_pack", query="no such needle anywhere")["count"] == 0
        with pytest.raises(ValueError, match="non-empty"):
            registry.execute("search_pack", {"query": ""}, actor="a", conversation="c")


class TestDungeonNotYet:
    """A dungeon pack: the probe, files, export, describe and window all work
    since row P0-8 filled the ``room`` dispatch entries; ``validate_level`` is
    the one tool still answering the structured "not yet" (a room has no
    validator — the dungeon validators run at generation)."""

    def test_room_tools_and_the_remaining_boundary(self) -> None:
        before = _tree(DUNGEON_FIXTURE)
        registry = _registry(DUNGEON_FIXTURE)
        probe = _run(registry, "describe_pack")
        assert probe["pack_type"] == "dungeon"
        assert probe["grid_ids"]["room"][0] == {"map_id": "room_0"}
        assert len(probe["grid_ids"]["room"]) == probe["entities"]["room"]["count"]
        bundle = _run(registry, "export_level", level_id="room_0")
        assert bundle["level_id"] == "room_0" and "window" not in bundle
        # Row P0-8: describe + window are real projections of the room bundle.
        described = _run(registry, "describe_level", level_id="room_0")
        assert described["room_id"] == "room_0" and described["dims"]["width"] > 0
        windowed = _run(registry, "export_level", level_id="room_0", window=[0, 0, 4, 4])
        assert windowed["window"] == {"x0": 0, "y0": 0, "w": 4, "h": 4}
        with pytest.raises(ValueError, match=f"not yet.*'room'.*{GRID_ROOM_ROW}") as info:
            registry.execute("validate_level", {"level_id": "room_0"}, actor="a", conversation="c")
        # JSON-bodied like UnknownTool / the CLI's _emit_error(grid=, row=): the model reads grid/row off it
        body = json.loads(str(info.value))
        assert body["error"] == "not_yet" and body["grid"] == "room" and body["row"] == GRID_ROOM_ROW
        assert body["tool"] == "validate_level" and "not yet" in body["message"]
        # A kind whose declared schema neither side ships (dungeon: schema_source null)
        # answers a structured "no roll table", not a FileNotFoundError.
        for kind, block in _run(registry, "db_types").items():
            if block.get("schema") and block.get("schema_source") is None:
                with pytest.raises(ValueError, match="no roll table"):
                    registry.execute("db_schema", {"type": kind}, actor="a", conversation="c")
        assert grid_verb(GRID_DESCRIBERS, "room") is not None
        assert grid_verb(GRID_READERS, "level") is export_level_bundle
        assert grid_verb(GRID_DESCRIBERS, "level") is describe_level
        assert _run(registry, "read_pack_file", path="manifest.json")["text"].startswith("{")
        assert _tree(DUNGEON_FIXTURE) == before


# ---------------------------------------------------------------------------
# The eval: a scripted conversation over the REAL tools
# ---------------------------------------------------------------------------


class TestScriptedConversation:
    def test_describe_pack_then_level_then_validate_returns_real_json(self, wide_pack: Path, widest: str) -> None:
        registry = _registry(wide_pack)
        fake = FakeChatBackend([
            [
                {"type": "text", "text": "Let me probe the pack first."},
                {"type": "tool_use", "name": "describe_pack", "input": {}},
            ],
            [{"type": "tool_use", "name": "describe_level", "input": {"level_id": widest}}],
            [{"type": "tool_use", "name": "validate_level", "input": {"level_id": widest}}],
            [{"type": "text", "text": f"{widest} validates clean; the exit is reachable."}],
        ])
        result = run_conversation(
            fake,
            system="You are the cradle agent. Probe, never assume.",
            tools=registry.specs(),
            tool_executor=lambda name, i: registry.execute(name, i, actor="agent:eval", conversation="eval"),
            user_messages=[f"Is {widest} beatable?"],
        )
        assert [step["tool"] for step in result.steps] == ["describe_pack", "describe_level", "validate_level"]
        assert not any(step["is_error"] for step in result.steps)
        assert result.stop_reasons == ["end_turn"] and "reachable" in result.texts[0]
        # the results are the verbs' own JSON, not canned
        from canon.packs.platformer.ops import validate_level

        probe, summary, report = (json.loads(step["result"]) for step in result.steps)
        assert probe["pack_type"] == "platformer" and widest in [e["level_id"] for e in probe["grid_ids"]["level"]]
        assert summary == json.loads(compact(describe_level(wide_pack, widest)))
        assert report == json.loads(compact(validate_level(wide_pack, widest)))
        # every request carried the twelve read specs, in registration order
        assert all([t.name for t in call.tools] == list(READ_TOOL_NAMES) for call in fake.calls)
        # tool results reached the model as user-turn tool_result blocks
        result_blocks = [
            b for m in result.messages if m["role"] == "user" and isinstance(m["content"], list)
            for b in m["content"] if b.get("type") == "tool_result"
        ]
        assert len(result_blocks) == 3 and all("is_error" not in b for b in result_blocks)
        assert json.loads(result_blocks[1]["content"])["level_id"] == widest


# ---------------------------------------------------------------------------
# The CLI: `canon level describe` / `grid describe` / `--window`
# ---------------------------------------------------------------------------


class TestCli:
    def test_level_and_grid_describe_are_one_verb(self, wide_pack: Path) -> None:
        code_l, level_doc = _canon("level", "describe", str(wide_pack), "--level", "l1")
        code_g, grid_doc = _canon("grid", "describe", str(wide_pack), "--level", "l1")
        assert code_l == 0 and code_g == 0
        expected = json.loads(json.dumps(describe_level(wide_pack, "l1"), default=str))
        assert level_doc["level"] == grid_doc["level"] == expected
        assert level_doc["canon_version"] == "0.1"
        code, err = _canon("level", "describe", str(wide_pack), "--level", "l99")
        assert code != 0 and "l99" in err["error"] and err["level"] == "l99"

    def test_window_flag_on_both_exports(self, wide_pack: Path, widest: str) -> None:
        code_l, level_doc = _canon("level", "export", str(wide_pack), "--level", widest, "--window", "3,2,24,16")
        code_g, grid_doc = _canon("grid", "export", str(wide_pack), "--level", widest, "--window", "3,2,24,16")
        assert code_l == 0 and code_g == 0
        expected = json.loads(json.dumps(export_level_bundle(wide_pack, widest, window=(3, 2, 24, 16)), default=str))
        assert level_doc["level"] == grid_doc["level"] == expected
        assert level_doc["level"]["window"] == {"x0": 3, "y0": 2, "w": 24, "h": 16}
        code, err = _canon("level", "export", str(wide_pack), "--level", widest, "--window", "1,2,3")
        assert code != 0 and "x0,y0,w,h" in err["error"]
        code, err = _canon("grid", "export", str(wide_pack), "--level", widest, "--window", "9999,0,4,4")
        assert code != 0 and "outside" in err["error"] and err["level"] == widest
        code, plain = _canon("level", "export", str(wide_pack), "--level", "l1")
        assert code == 0 and "window" not in plain["level"]

    def test_dungeon_rooms_describe_and_window_since_p08(self) -> None:
        """Row P0-8 replaced the three "not yet"s this pinned: describe (both
        names) and ``--window`` are projections of the room bundle now. Still
        pure reads — the legacy tree stays byte-identical."""
        before = _tree(DUNGEON_FIXTURE)
        code, doc = _canon("grid", "describe", str(DUNGEON_FIXTURE), "--level", "room_0")
        assert code == 0 and doc["level"]["room_id"] == "room_0"
        code, alias = _canon("level", "describe", str(DUNGEON_FIXTURE), "--level", "room_0")
        assert code == 0 and alias == doc
        code, doc = _canon("grid", "export", str(DUNGEON_FIXTURE), "--level", "room_0", "--window", "0,0,4,4")
        assert code == 0 and doc["level"]["window"] == {"x0": 0, "y0": 0, "w": 4, "h": 4}
        code, doc = _canon("grid", "export", str(DUNGEON_FIXTURE), "--level", "room_0")
        assert code == 0 and doc["level"]["level_id"] == "room_0"
        assert _tree(DUNGEON_FIXTURE) == before

    def test_help_lists_the_new_verbs(self) -> None:
        for group in ("grid", "level"):
            result = subprocess.run(CANON + [group, "--help"], capture_output=True, text=True, cwd=REPO)
            assert result.returncode == 0 and "describe" in result.stdout and "export" in result.stdout
        result = subprocess.run(CANON + ["level", "export", "--help"], capture_output=True, text=True, cwd=REPO)
        assert "--window" in result.stdout
