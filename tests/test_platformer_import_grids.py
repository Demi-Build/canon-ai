"""Grid import (terrain paint / resize) + level lifecycle (create / publish).

The load-bearing invariant: cradle's write-back re-derives terrain (autotile /
water_deep), background, and hazards EXACTLY as canon's own phases do — an
unedited grid round-trips byte-identically. Everything else builds on that.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from canon.adapters.platformer_write import (
    _derive_background,
    _derive_hazards,
    _derive_terrain,
    create_level,
    import_level_grids,
    publish_level,
)

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def pack(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("plat_pack")
    subprocess.run(
        [
            sys.executable, "-m", "canon.packs.platformer.run_slice",
            "--backend", "fake", "--engine", "json", "--image-backend", "fake",
            "--music-backend", "none", "--sfx-backend", "none",
            "--num-stages", "1", "--num-levels", "2", "--num-enemies", "2",
            "--output-dir", str(out),
        ],
        check=True,
        capture_output=True,
        cwd=REPO,
    )
    return out


def _level_dir(pack: Path, lid: str) -> Path:
    return next(p for p in pack.glob(f"level/*/{lid}") if (p / "level.json").is_file())


def _stage_of(pack: Path, lid: str) -> str:
    return _level_dir(pack, lid).parent.name


def test_derivations_roundtrip_byte_identical(pack: Path):
    """Unedited collision → derived layers byte-equal canon's own output."""
    for lvl_dir in sorted(pack.glob("level/*/*")):
        if not (lvl_dir / "level.json").is_file():
            continue
        tileset = json.loads(
            (pack / "tileset" / lvl_dir.parent.name / "manifest.json").read_text()
        )
        level = json.loads((lvl_dir / "level.json").read_text())
        with np.load(lvl_dir / "collision.npz") as data:
            col = data["collision"]
        with np.load(lvl_dir / "terrain.npz") as data:
            assert np.array_equal(_derive_terrain(col, tileset), data["terrain"])
        with np.load(lvl_dir / "background.npz") as data:
            assert np.array_equal(_derive_background(*col.shape), data["background"])
        assert _derive_hazards(col, col, tileset, level.get("hazards") or []) == json.loads(
            (lvl_dir / "hazards.json").read_text()
        )


def test_import_grids_paint_and_journal(pack: Path):
    lvl_dir = _level_dir(pack, "l1")
    with np.load(lvl_dir / "collision.npz") as data:
        col = data["collision"].tolist()
    # Paint a spike (hazard band) on a floor-adjacent empty cell + a wall block.
    col[10][5] = 10
    col[9][8] = 3
    result = import_level_grids(pack, "l1", col, actor="test")
    assert result["updated"] == ["collision", "terrain", "background", "hazards"]
    assert result["changed_cells"] == 2

    level = json.loads((lvl_dir / "level.json").read_text())
    assert level["status"] == "user_edited"
    # Painted hazard cell landed in the hazards layer with the registry name.
    assert {"x": 5, "y": 10, "type": "spike", "params": {}} in level["hazards"]
    # Terrain re-derived over the new grid, in sync on disk.
    tileset = json.loads((pack / "tileset" / _stage_of(pack, "l1") / "manifest.json").read_text())
    with np.load(lvl_dir / "collision.npz") as data:
        new_col = data["collision"]
    with np.load(lvl_dir / "terrain.npz") as data:
        assert np.array_equal(_derive_terrain(new_col, tileset), data["terrain"])
    # Journalled with before/after.
    events = [
        json.loads(line) for line in (pack / ".canon" / "journal.jsonl").read_text().splitlines()
    ]
    ev = [e for e in events if e["op"] == "edit" and e["detail"].get("kind") == "terrain_paint"]
    assert ev and ev[-1]["before_hash"] != ev[-1]["after_hash"]


def test_import_grids_rejects_unknown_tile(pack: Path):
    lvl_dir = _level_dir(pack, "l1")
    with np.load(lvl_dir / "collision.npz") as data:
        col = data["collision"].tolist()
    col[0][0] = 99
    with pytest.raises(ValueError, match="unknown tile"):
        import_level_grids(pack, "l1", col)


def test_import_grids_resize_clamps(pack: Path):
    lvl_dir = _level_dir(pack, "l2")
    level_before = json.loads((lvl_dir / "level.json").read_text())
    with np.load(lvl_dir / "collision.npz") as data:
        col = data["collision"]
    h, w = col.shape
    # Shrink width by a third — placements beyond must clamp inside.
    new_w = max(8, w * 2 // 3)
    result = import_level_grids(pack, "l2", col[:, :new_w].tolist(), actor="test")
    assert result["dims"] == [new_w, h]
    level = json.loads((lvl_dir / "level.json").read_text())
    assert level["grid_width"] == new_w
    assert level["exit"][0] <= new_w - 1
    for placement in level["entities"] + level["items"]:
        assert placement["pos"][0] <= new_w - 1
    # entities.json stayed in sync when clamping happened.
    flat = json.loads((lvl_dir / "entities.json").read_text())
    assert [p["pos"] for p in level["entities"]] == [[e["x"], e["y"]] for e in flat]
    assert level_before["grid_width"] == w  # sanity: it actually shrank


def test_create_is_draft_and_playable_shape(pack: Path):
    stage = _stage_of(pack, "l1")
    result = create_level(pack, stage, width=24, height=12, actor="test")
    lid = result["level_id"]
    assert result["draft"] is True
    lvl_dir = _level_dir(pack, lid)
    level = json.loads((lvl_dir / "level.json").read_text())
    # Scaffold: flat floor + wall base, spawn/exit on the ground.
    with np.load(lvl_dir / "collision.npz") as data:
        col = data["collision"]
    assert col.shape == (12, 24)
    assert set(col[10].tolist()) == {1} and set(col[11].tolist()) == {3}
    assert level["spawn"] == [2, 9] and level["exit"] == [23, 9]
    # Draft: on disk, not in the manifest.
    manifest = json.loads((pack / "manifest.json").read_text())
    assert lid not in manifest["levels"]
    # Journalled as user-created.
    events = [
        json.loads(line) for line in (pack / ".canon" / "journal.jsonl").read_text().splitlines()
    ]
    assert any(e["op"] == "create" and lid in e["artifact_id"] for e in events)
    # Derived layers hold the same invariant as generated ones.
    tileset = json.loads((pack / "tileset" / stage / "manifest.json").read_text())
    with np.load(lvl_dir / "terrain.npz") as data:
        assert np.array_equal(_derive_terrain(col, tileset), data["terrain"])


def test_publish_inserts_at_position_and_renumbers(pack: Path):
    stage = _stage_of(pack, "l1")
    created = create_level(pack, stage, width=20, height=10, actor="test")
    lid = created["level_id"]
    result = publish_level(pack, lid, position=2, actor="test")
    assert result["published"] is True
    manifest = json.loads((pack / "manifest.json").read_text())
    stage_json = json.loads((pack / "stage" / stage / "stage.json").read_text())
    assert stage_json["level_ids"][1] == lid
    assert manifest["levels"] == stage_json["level_ids"]
    nodes = manifest["world_map"]["nodes"]
    assert [n["level_id"] for n in nodes] == manifest["levels"]
    # Display names renumbered: the inserted level IS X-2 now.
    assert nodes[1]["display_name"] == "1-2"
    assert nodes[2]["display_name"] == "1-3"
    # Unpublish returns it to draft.
    publish_level(pack, lid, remove=True, actor="test")
    manifest = json.loads((pack / "manifest.json").read_text())
    assert lid not in manifest["levels"]
