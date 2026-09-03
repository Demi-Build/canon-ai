"""Single-entity generation ops (cradle's seam): anchored rows + assets.

Covers: locked-value skeleton rolls (anchors + dependent lookups), db new /
complete against a real output tree with the fake LLM, sprite generation via
the real SpriteArtPhase on a filtered bible, and the multi-image animate path
(fake img2img + fake VLM) writing strips/frames/atlas + stats.animation.
"""

from __future__ import annotations

import json
import random
import subprocess
import sys
from pathlib import Path

import pytest

from canon.skeleton.core import roll_skeleton
from canon.skeleton.loader import load_skeleton_spec

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from canon.packs.platformer import ops  # noqa: E402

ENEMY_SCHEMA = Path(ops.__file__).parent / "schemas" / "enemy.json"


@pytest.fixture(scope="module")
def pack(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("ops_pack")
    subprocess.run(
        [
            sys.executable, "-m", "canon.packs.platformer.run_slice",
            "--backend", "fake", "--engine", "json", "--image-backend", "fake",
            "--music-backend", "none", "--sfx-backend", "none",
            "--num-stages", "1", "--num-levels", "1", "--num-enemies", "2",
            "--output-dir", str(out),
        ],
        check=True,
        capture_output=True,
        cwd=REPO,
    )
    return out


def test_roll_skeleton_locked_anchors_dependent_lookups():
    spec = load_skeleton_spec(ENEMY_SCHEMA)
    rolled = roll_skeleton(
        spec, random.Random(7), locked={"archetype": "flyer", "size": 2.0}
    )
    assert rolled["archetype"] == "flyer"
    assert rolled["speed"] == 2  # lookup resolved FROM the anchor
    assert rolled["size"] == 2.0
    assert 13 <= rolled["hp"] <= 18  # size-2.0 hp band, from the anchor


def test_roll_skeleton_locked_rejects_off_table_values():
    spec = load_skeleton_spec(ENEMY_SCHEMA)
    with pytest.raises(ValueError, match="not one of the spec's choices"):
        roll_skeleton(spec, random.Random(7), locked={"archetype": "dragon"})
    with pytest.raises(ValueError, match="outside the spec range"):
        roll_skeleton(spec, random.Random(7), locked={"patrol_range": 99})
    with pytest.raises(KeyError, match="not in the spec"):
        roll_skeleton(spec, random.Random(7), locked={"nonsense": 1})


def test_db_types_serializes_the_registry(pack: Path):
    types = ops.db_types(pack)
    assert set(types) == {"enemy", "item"}
    enemy = types["enemy"]
    archetype = next(
        f for f in enemy["skeleton_fields"] if f["name"] == "archetype"
    )
    assert archetype["mode"] == "choices" and "flyer" in archetype["choices"]
    assert enemy["llm_fields"] == ["name", "flavor"]


def test_db_new_anchored_and_completed(pack: Path):
    llm = ops.build_llm("fake")
    result = ops.new_db_row(
        pack, "enemy", {"archetype": "flyer", "name": "Anchor Wyrm"},
        complete=True, llm=llm, actor="test",
    )
    row = result["row"]
    assert result["completed"] is True
    assert row["archetype"] == "flyer"
    assert row["name"] == "Anchor Wyrm"  # anchored through LLM completion
    assert row["stats"]["speed"] == 2  # flyer lookup from the anchor
    on_disk = json.loads((pack / "enemy" / f"{result['id']}.json").read_text())
    assert on_disk["name"] == "Anchor Wyrm"
    events = [
        json.loads(line)
        for line in (pack / ".canon" / "journal.jsonl").read_text().splitlines()
    ]
    ev = [e for e in events if e.get("detail", {}).get("kind") == "db_new"][-1]
    assert ev["op"] == "generate" and ev["gen"]["llm_model"]
    assert "archetype" in ev["detail"]["locked"]


def test_db_new_without_complete_is_a_create(pack: Path):
    result = ops.new_db_row(pack, "item", {"kind": "shield"}, actor="test")
    assert result["completed"] is False
    assert result["row"]["kind"] == "shield"
    events = [
        json.loads(line)
        for line in (pack / ".canon" / "journal.jsonl").read_text().splitlines()
    ]
    ev = [e for e in events if e.get("detail", {}).get("kind") == "db_new"][-1]
    assert ev["op"] == "create" and ev["source"] == "user"


def test_db_complete_preserves_locked_fields(pack: Path):
    created = ops.new_db_row(
        pack, "enemy", {"archetype": "sentry", "name": "Locked Statue"},
        actor="test",
    )
    eid = created["id"]
    llm = ops.build_llm("fake")
    result = ops.complete_db_row(
        pack, "enemy", eid, ["name", "archetype"], llm=llm, actor="test"
    )
    assert result["row"]["name"] == "Locked Statue"
    assert result["row"]["archetype"] == "sentry"
    assert result["id"] == eid  # id never drifts on completion


def test_generate_sprite_fake_surfaces_the_fallback(pack: Path):
    """The fake image backend's sprite comes back near-empty after background
    removal — pipeline parity is keeping the rect AND saying so loudly."""
    enemy_id = sorted(p.stem for p in (pack / "enemy").glob("*.json"))[0]
    result = ops.generate_asset(
        pack, f"enemy:{enemy_id}", image_backend="fake", actor="test"
    )
    assert result["generated"] is False
    assert any("placeholder rect kept" in w for w in result["warnings"])


def test_animate_full_multiimage_path(pack: Path):
    from canon.adapters.platformer_write import replace_asset

    enemy_id = sorted(p.stem for p in (pack / "enemy").glob("*.json"))[0]
    # Install a real base sprite (the user-art path), then animate over it.
    png = pack / "red.png"
    from PIL import Image

    Image.new("RGBA", (32, 32), (200, 30, 30, 255)).save(png)
    replace_asset(pack, f"enemy:{enemy_id}", png, actor="test")

    result = ops.animate_asset(
        pack, f"enemy:{enemy_id}",
        image_backend="fake", vlm_backend="fake", actor="test",
    )
    assert result["animated"] is True
    assert set(result["states"]) >= {"idle", "walk"}
    sprite_dir = pack / "sprite" / "enemy" / enemy_id
    assert (sprite_dir / "frames.json").is_file()
    assert (sprite_dir / "atlas.png").is_file() and (sprite_dir / "atlas.json").is_file()
    on_disk = json.loads((pack / "enemy" / f"{enemy_id}.json").read_text())
    anim = on_disk["stats"]["animation"]
    assert set(anim["spec"]) == set(anim["states"])
    events = [
        json.loads(line)
        for line in (pack / ".canon" / "journal.jsonl").read_text().splitlines()
    ]
    ev = [e for e in events if e.get("detail", {}).get("kind") == "asset_animate"][-1]
    assert ev["gen"]["vlm_model"] and ev["gen"]["image_model"]


def _journal(pack: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (pack / ".canon" / "journal.jsonl").read_text().splitlines()
    ]


def test_db_update_routes_rehashes_and_journals(pack: Path):
    from canon import provenance

    created = ops.new_db_row(
        pack, "enemy", {"archetype": "patroller", "name": "Edit Target"},
        actor="test",
    )
    eid = created["id"]
    row_path = pack / "enemy" / f"{eid}.json"
    original_bytes = row_path.read_bytes()

    result = ops.update_db_row(
        pack, "enemy", eid,
        {"name": "Renamed", "hp": 9, "patrol_range": 5, "stats.custom_knob": 3},
        actor="test",
    )
    row = result["row"]
    # Flat names route into their nested homes; dotted paths reach new knobs.
    assert row["name"] == "Renamed"
    assert row["stats"]["hp"] == 9
    assert row["behavior"]["patrol_range"] == 5
    assert row["stats"]["custom_knob"] == 3
    assert row["status"] == "user_edited"
    on_disk = json.loads(row_path.read_text())
    assert on_disk == row

    ev = [e for e in _journal(pack) if e.get("detail", {}).get("kind") == "db_update"][-1]
    assert ev["op"] == "edit" and ev["source"] == "user"
    assert ev["detail"]["changed"]["hp"]["to"] == 9
    assert ev["before_hash"] and ev["after_hash"]
    assert ev["before_hash"] != ev["after_hash"]
    # The pre-edit version is recoverable from the object store.
    assert provenance.read_object(pack, ev["before_hash"]) == original_bytes


def test_db_update_rejects_protected_and_unknown(pack: Path):
    eid = sorted(p.stem for p in (pack / "enemy").glob("*.json"))[0]
    n_events = len(_journal(pack))
    with pytest.raises(ValueError, match="protected"):
        ops.update_db_row(pack, "enemy", eid, {"enemy_id": "sneaky"})
    with pytest.raises(ValueError, match="protected"):
        ops.update_db_row(pack, "enemy", eid, {"stats.animation": {}})
    with pytest.raises(ValueError, match="unknown field"):
        ops.update_db_row(pack, "enemy", eid, {"bogus_field": 1})
    assert len(_journal(pack)) == n_events  # nothing journaled on failure


def test_db_update_no_change_skips_journal(pack: Path):
    eid = sorted(p.stem for p in (pack / "enemy").glob("*.json"))[0]
    current = json.loads((pack / "enemy" / f"{eid}.json").read_text())
    n_events = len(_journal(pack))
    result = ops.update_db_row(pack, "enemy", eid, {"name": current["name"]})
    assert result.get("no_change") is True
    assert len(_journal(pack)) == n_events


def test_db_update_off_table_value_warns_but_lands(pack: Path):
    eid = sorted(p.stem for p in (pack / "enemy").glob("*.json"))[0]
    result = ops.update_db_row(
        pack, "enemy", eid, {"archetype": "dragon"}, actor="test"
    )
    assert result["row"]["archetype"] == "dragon"
    assert any("outside the roll table" in w for w in result["warnings"])


def test_tile_params_update_all_slots_of_the_name(pack: Path):
    stage_id = sorted(p.name for p in (pack / "tileset").iterdir() if p.is_dir())[0]
    ts_path = pack / "tileset" / stage_id / "manifest.json"

    result = ops.update_tile_slots(
        pack, f"{stage_id}/floor", {"params": {"friction": 0.5}}, actor="test"
    )
    assert result["slots"] > 1  # autotile: many slots share the name
    assert result["changed"]["params.friction"]["to"] == 0.5
    manifest = json.loads(ts_path.read_text())
    floor = [s for s in manifest["slots"] if s["name"] == "floor"]
    assert all(s["params"]["friction"] == 0.5 for s in floor)
    # Structural autotile masks survive the merge untouched (and distinct).
    assert len({s["params"]["autotile_mask"] for s in floor}) == len(floor)
    assert manifest["status"] == "user_edited"
    ev = [e for e in _journal(pack) if e.get("detail", {}).get("kind") == "tile_params"][-1]
    assert ev["artifact_id"] == f"tileset:{stage_id}" and ev["op"] == "edit"

    with pytest.raises(ValueError, match="structural"):
        ops.update_tile_slots(pack, f"{stage_id}/floor", {"params": {"autotile_mask": 3}})
    with pytest.raises(ValueError, match="not a category"):
        ops.update_tile_slots(pack, f"{stage_id}/floor", {"collision": "bouncy"})
    with pytest.raises(ValueError, match="accept only"):
        ops.update_tile_slots(pack, f"{stage_id}/floor", {"px_region": [0, 0, 8, 8]})


def test_db_schema_read_update_and_override_takes_effect(pack: Path):
    # Read: the repo default serves until a pack override exists.
    before = ops.read_db_schema(pack, "enemy")
    assert before["source"] == "default"
    assert "archetype" in before["schema"]["fields"]

    # A broken edit (choice with no lookup coverage) fails closed: no write.
    with pytest.raises(ValueError, match="no row for"):
        ops.update_db_schema(
            pack, "enemy",
            {"fields": {"archetype": {"choices": [["ghost", 1]]}}},
        )
    assert not (pack / "schemas" / "enemy.json").is_file()

    # A valid edit lands as a pack-local override…
    result = ops.update_db_schema(
        pack, "enemy",
        {"fields": {"archetype": {"choices": [["sentry", 1]]}}},
        actor="test",
    )
    assert result["source"] == "pack"
    assert (pack / "schemas" / "enemy.json").is_file()
    ev = [e for e in _journal(pack) if e.get("detail", {}).get("kind") == "db_schema"][-1]
    assert ev["artifact_id"] == "schema:enemy" and ev["op"] == "edit"

    # …and generation now rolls from the edited table.
    assert ops.db_types(pack)["enemy"]["schema_source"] == "pack"
    rolled = ops.new_db_row(pack, "enemy", {}, actor="test")
    assert rolled["row"]["archetype"] == "sentry"


def test_validate_level_passes_on_generated_pack(pack: Path):
    report = ops.validate_level(pack, "l1")
    assert report["ok"] is True
    assert [c["name"] for c in report["checks"]] == ["terrain", "enemies", "items"]
    assert all(c["problems"] == [] for c in report["checks"])
    assert report["movement"]["jump_height"] >= 1  # merged physics rides along


def test_describe_level_and_windowed_export_are_pure_reads(pack: Path):
    """Row A3's read verbs: ``describe_level`` is a projection of the bundle
    plus the validate verdict (never the grid), ``export_level_bundle(...,
    window=)`` slices the same document — and neither writes a byte."""
    import hashlib

    from canon.adapters.platformer_read import describe_level, export_level_bundle

    def tree() -> dict[str, str]:
        return {
            str(p.relative_to(pack)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(pack.rglob("*")) if p.is_file()
        }

    before = tree()
    summary = describe_level(pack, "l1")
    full = export_level_bundle(pack, "l1")
    assert summary["dims"] == {"width": full["grid_width"], "height": full["grid_height"], "axis": "horizontal"}
    assert sum(summary["tiles"]["by_category"].values()) == full["grid_width"] * full["grid_height"]
    assert summary["entities"]["count"] == len(full["entities"])
    assert summary["items"]["count"] == len(full["items"])
    assert summary["validation"] == {
        "ok": True, "problems": {"terrain": 0, "enemies": 0, "items": 0}, "repair_count": 0, "rooms": [],
    }
    assert summary["revision"] == full["revision"] and "grids" not in summary
    assert summary["platforms"] and all(band["rows"][0] <= band["rows"][1] for band in summary["platforms"])

    windowed = export_level_bundle(pack, "l1", window=(1, 1, 8, 6))
    assert windowed["window"] == {"x0": 1, "y0": 1, "w": 8, "h": 6}
    assert windowed["grid_width"] == full["grid_width"] and windowed["grid_height"] == full["grid_height"]
    assert windowed["grids"]["collision"] == [row[1:9] for row in full["grids"]["collision"][1:7]]
    assert all(1 <= e["x"] < 9 and 1 <= e["y"] < 7 for e in windowed["entities"])
    assert tree() == before


def test_validate_level_catches_hand_broken_edits(pack: Path, tmp_path: Path):
    import shutil

    import numpy as np

    broken = tmp_path / "broken_pack"
    shutil.copytree(pack, broken)
    stage = sorted(p.name for p in (broken / "level").iterdir())[0]
    level_dir = broken / "level" / stage / "l1"

    # Wall off the exit column floor-to-ceiling: unreachable, not unstandable.
    level = json.loads((level_dir / "level.json").read_text())
    ex, _ey = level["exit"]
    with np.load(level_dir / "collision.npz") as z:
        grid = z["collision"].copy()
    grid[:, max(0, ex - 2)] = 1  # solid wall two columns before the exit
    buf = {"collision": grid}
    np.savez_compressed(level_dir / "collision.npz", **buf)

    # Park an item in mid-air far above any foothold: fails base-reach.
    items = json.loads((level_dir / "items.json").read_text())
    items.append({"item_id": next(iter(items), {}).get("item_id", "coin"), "x": 2, "y": 1, "source": "trail"})
    (level_dir / "items.json").write_text(json.dumps(items))

    report = ops.validate_level(broken, "l1")
    assert report["ok"] is False
    terrain = next(c for c in report["checks"] if c["name"] == "terrain")
    assert terrain["problems"], "walled-off exit must surface a reachability problem"


def test_validate_level_flags_unknown_enemy_placement(pack: Path, tmp_path: Path):
    import shutil

    broken = tmp_path / "ghost_pack"
    shutil.copytree(pack, broken)
    stage = sorted(p.name for p in (broken / "level").iterdir())[0]
    level_dir = broken / "level" / stage / "l1"
    entities = json.loads((level_dir / "entities.json").read_text())
    entities.append({"enemy_id": "ghost_of_nowhere", "x": 5, "y": 5})
    (level_dir / "entities.json").write_text(json.dumps(entities))

    report = ops.validate_level(broken, "l1")
    enemies = next(c for c in report["checks"] if c["name"] == "enemies")
    assert any("ghost_of_nowhere" in p for p in enemies["problems"])
    assert report["ok"] is False


def test_db_update_rejects_whole_container_writes(pack: Path):
    """A top-level stats/behavior/params replace would smuggle protected keys
    (stats.animation) past the wall and wipe siblings — refused."""
    eid = sorted(p.stem for p in (pack / "enemy").glob("*.json"))[0]
    with pytest.raises(ValueError, match="container"):
        ops.update_db_row(pack, "enemy", eid, {"stats": {"hp": 1}})
    with pytest.raises(ValueError, match="container"):
        ops.update_db_row(pack, "enemy", eid, {"behavior": {}})


def test_tile_update_converges_divergent_slots(pack: Path):
    """Diffs consider every same-name slot: slot 0 already matching must not
    short-circuit the write for divergent siblings."""
    stage_id = sorted(p.name for p in (pack / "tileset").iterdir() if p.is_dir())[0]
    ts_path = pack / "tileset" / stage_id / "manifest.json"
    manifest = json.loads(ts_path.read_text())
    floor = [s for s in manifest["slots"] if s["name"] == "floor"]
    assert len(floor) > 1
    floor[0].setdefault("params", {})["grip"] = 0.9  # hand-divergent sibling
    ts_path.write_text(json.dumps(manifest))

    result = ops.update_tile_slots(
        pack, f"{stage_id}/floor", {"params": {"grip": 0.9}}, actor="test"
    )
    assert result.get("no_change") is not True
    manifest = json.loads(ts_path.read_text())
    floor = [s for s in manifest["slots"] if s["name"] == "floor"]
    assert all(s["params"].get("grip") == 0.9 for s in floor)


def test_validate_survives_malformed_box_records(pack: Path, tmp_path: Path):
    """OOB / negative / incomplete box records must yield a report, not an
    IndexError — and never stamp a phantom solid via negative indexing."""
    import shutil

    broken = tmp_path / "boxes_pack"
    shutil.copytree(pack, broken)
    stage = sorted(p.name for p in (broken / "level").iterdir())[0]
    level_dir = broken / "level" / stage / "l1"
    items = json.loads((level_dir / "items.json").read_text())
    items += [
        {"item_id": "mystery", "x": 9999, "y": 9999, "source": "box"},
        {"item_id": "mystery", "x": -1, "y": -1, "source": "box"},
        {"item_id": "mystery", "source": "box"},
    ]
    (level_dir / "items.json").write_text(json.dumps(items))

    report = ops.validate_level(broken, "l1")  # must not raise
    items_check = next(c for c in report["checks"] if c["name"] == "items")
    assert items_check["problems"] or items_check["repairs"]


def test_validate_reports_cyclic_room_links(pack: Path, tmp_path: Path):
    import shutil

    broken = tmp_path / "cycle_pack"
    shutil.copytree(pack, broken)
    stage = sorted(p.name for p in (broken / "level").iterdir())[0]
    lj = broken / "level" / stage / "l1" / "level.json"
    level = json.loads(lj.read_text())
    level["secret_rooms"] = ["l1"]  # self-link, the tightest cycle
    lj.write_text(json.dumps(level))

    report = ops.validate_level(broken, "l1")
    assert report["ok"] is False
    assert any(
        "cyclic" in p
        for room in report["rooms"]
        for c in room["checks"]
        for p in c["problems"]
    )


def test_generator_phases_resolve_pack_schema_override(pack: Path):
    """Pipeline regen/resume rolls the pack-local tables the user edited —
    the phases resolve schemas/<kind>.json under output_dir at run time."""
    from types import SimpleNamespace as NS

    from canon.packs.platformer.phases import SCHEMAS_DIR, _schema_for

    default = SCHEMAS_DIR / "enemy.json"
    (pack / "schemas").mkdir(exist_ok=True)
    override = pack / "schemas" / "enemy.json"
    if not override.is_file():
        override.write_text(default.read_text())
    ctx = NS(config=NS(output_dir=str(pack)))
    assert _schema_for(ctx, default, explicit=False) == override
    assert _schema_for(ctx, default, explicit=True) == default
    no_pack = NS(config=NS(output_dir=str(pack / "nowhere")))
    assert _schema_for(no_pack, default, explicit=False) == default


def test_lineage_prompt_facets_and_restore_round_trip(pack: Path, tmp_path: Path):
    """Library A core: journal+CAS → family tree (facets, prompts, current
    markers), and restore branches from the chosen node without deleting."""
    import shutil

    from canon.adapters.platformer_read import asset_lineage
    from canon.adapters.platformer_write import restore_asset

    p = tmp_path / "lineage_pack"
    shutil.copytree(pack, p)
    # The shared fixture carries a sentry-only schema override from the
    # schema test — drop it so this roll can anchor archetype=patroller.
    (p / "schemas" / "enemy.json").unlink(missing_ok=True)
    llm = ops.build_llm("fake")
    created = ops.new_db_row(
        p, "enemy", {"archetype": "patroller", "name": "Root Beast"},
        complete=True, llm=llm, actor="test",
    )
    eid = created["id"]
    ops.update_db_row(p, "enemy", eid, {"name": "Renamed Beast"}, actor="test")

    tree = asset_lineage(p, f"enemy:{eid}")
    assert len(tree["nodes"]) >= 2 and len(tree["edges"]) >= 1
    gen_node = next(n for n in tree["nodes"] if n["op"] == "generate")
    assert gen_node["facet"] == "row"
    assert (gen_node["gen"] or {}).get("prompt")  # "see its prompt if generated"
    current = next(
        n for n in tree["nodes"]
        if f"enemy:{eid}#row" in n["current_of"]
    )
    assert current["id"] == tree["requested_node_id"]

    # Restore the ORIGINAL generated version: newer versions stay, the new
    # node hangs off the restored-from node.
    restore_asset(p, f"enemy:{eid}", gen_node["id"], actor="test")
    on_disk = json.loads((p / "enemy" / f"{eid}.json").read_text())
    assert on_disk["name"] == "Root Beast"
    assert on_disk["status"] == "user_edited"
    tree2 = asset_lineage(p, f"enemy:{eid}")
    assert len(tree2["nodes"]) > len(tree["nodes"])  # nothing deleted
    assert any(
        e["op"] == "restore" and e["from"] == gen_node["id"]
        for e in tree2["edges"]
    )


def test_asset_restore_sprite_bytes(pack: Path, tmp_path: Path):
    import shutil

    from PIL import Image

    from canon.adapters.platformer_write import replace_asset, restore_asset

    p = tmp_path / "sprite_pack"
    shutil.copytree(pack, p)
    eid = sorted(q.stem for q in (p / "enemy").glob("*.json"))[0]
    red, green = p / "red.png", p / "green.png"
    Image.new("RGBA", (32, 32), (200, 30, 30, 255)).save(red)
    Image.new("RGBA", (32, 32), (30, 200, 90, 255)).save(green)
    replace_asset(p, f"enemy:{eid}", red, actor="test")
    events = [
        json.loads(line)
        for line in (p / ".canon" / "journal.jsonl").read_text().splitlines()
    ]
    red_hash = [
        e["after_hash"] for e in events
        if e.get("detail", {}).get("kind") == "sprite_replace"
    ][-1]
    replace_asset(p, f"enemy:{eid}", green, actor="test")

    result = restore_asset(p, f"enemy:{eid}", red_hash, actor="test")
    assert result["kind"] == "sprite_restore"
    row = json.loads((p / "enemy" / f"{eid}.json").read_text())
    assert (p / row["sprite_path"]).read_bytes() == red.read_bytes()
    assert row["sprite_hash"] == red_hash
    # Restore only rewinds an artifact's OWN lineage — a hash from another
    # artifact's history (or a nonexistent target) is refused up front.
    with pytest.raises(ValueError, match="not part of"):
        restore_asset(p, "enemy:someone_else", red_hash)
    other = ops.new_db_row(p, "enemy", {"name": "Other Beast"}, actor="test")
    with pytest.raises(ValueError, match="not part of"):
        restore_asset(p, f"enemy:{other['id']}", red_hash)


def test_lineage_survives_restore_cycles(pack: Path, tmp_path: Path):
    """A byte-identical restore journals after_hash == to_hash, closing a
    cycle through the ':replaced' edge — the tree must return (bounded
    layering over structural edges), never hang."""
    import shutil

    from canon.adapters.platformer_read import asset_lineage
    from canon.adapters.platformer_write import restore_asset

    p = tmp_path / "cycle_lineage"
    shutil.copytree(pack, p)
    (p / "schemas" / "enemy.json").unlink(missing_ok=True)
    created = ops.new_db_row(p, "enemy", {"name": "Loop Beast"}, actor="test")
    eid = created["id"]
    ops.update_db_row(p, "enemy", eid, {"hp": 7}, actor="test")   # A -> B
    ops.update_db_row(p, "enemy", eid, {"hp": 8}, actor="test")   # B -> C
    events = [
        json.loads(line)
        for line in (p / ".canon" / "journal.jsonl").read_text().splitlines()
    ]
    b_hash = next(
        e["after_hash"] for e in events
        if e.get("artifact_id") == f"enemy:{eid}"
        and e.get("detail", {}).get("changed", {}).get("hp", {}).get("to") == 7
    )
    # Restoring B: B was already user_edited, so bytes are identical and the
    # journal gains before=C after=B — the cycle.
    restore_asset(p, f"enemy:{eid}", b_hash, actor="test")

    tree = asset_lineage(p, f"enemy:{eid}")  # must terminate
    assert tree["requested_node_id"] == b_hash  # restored version is current
    assert all(n["depth"] <= len(tree["nodes"]) for n in tree["nodes"])


def test_lineage_requested_prefers_row_over_animation_facet(pack: Path, tmp_path: Path):
    """An animated enemy centers on its ROW, not its frames.json — facet
    priority, not alphabetical order."""
    import shutil

    from PIL import Image

    from canon.adapters.platformer_read import asset_lineage
    from canon.adapters.platformer_write import replace_asset

    p = tmp_path / "facet_pack"
    shutil.copytree(pack, p)
    eid = sorted(q.stem for q in (p / "enemy").glob("*.json"))[0]
    png = p / "base.png"
    Image.new("RGBA", (32, 32), (200, 30, 30, 255)).save(png)
    replace_asset(p, f"enemy:{eid}", png, actor="test")
    result = ops.animate_asset(
        p, f"enemy:{eid}", image_backend="fake", vlm_backend="fake", actor="test"
    )
    assert result["animated"] is True
    ops.update_db_row(p, "enemy", eid, {"hp": 5}, actor="test")

    tree = asset_lineage(p, f"enemy:{eid}")
    requested = next(n for n in tree["nodes"] if n["id"] == tree["requested_node_id"])
    assert requested["facet"] == "row"
    assert f"enemy:{eid}#row" in requested["current_of"]


def test_library_publish_import_assign_round_trip(pack: Path, tmp_path, monkeypatch):
    """Piece C: publish bundles + dedup, cross-pack import with fresh id +
    library_ref stamp, in-pack assign sharing lineage nodes."""
    import shutil

    from PIL import Image

    monkeypatch.setenv("CANON_LIBRARY", str(tmp_path / "lib"))
    from canon import library
    from canon.adapters.platformer_read import asset_lineage
    from canon.adapters.platformer_write import assign_asset, replace_asset

    src = tmp_path / "src_pack"
    dst = tmp_path / "dst_pack"
    shutil.copytree(pack, src)
    shutil.copytree(pack, dst)
    ids = sorted(p.stem for p in (src / "enemy").glob("*.json"))
    eid, other = ids[0], ids[1]
    png = src / "publish_me.png"
    Image.new("RGBA", (32, 32), (10, 60, 200, 255)).save(png)
    replace_asset(src, f"enemy:{eid}", png, actor="test")

    entry = library.publish(src, f"enemy:{eid}", tags=("test",), actor="test")
    assert entry["kind"] == "enemy_def"
    assert {"row", "sprite"} <= set(entry["objects"])
    assert library.publish(src, f"enemy:{eid}")["deduped"] is True
    assert library.list_entries(kind="enemy_def", tag="test")
    src_events = [
        json.loads(line)
        for line in (src / ".canon" / "journal.jsonl").read_text().splitlines()
    ]
    keep = [e for e in src_events if e.get("detail", {}).get("kind") == "library_publish"]
    assert keep and keep[-1]["op"] == "keep"

    result = library.import_entry(dst, entry["library_id"], actor="test")
    nid = result["id"]
    assert nid != eid  # dst already had that id — fresh mint, never overwrite
    row = json.loads((dst / "enemy" / f"{nid}.json").read_text())
    assert row["stats"]["library_ref"]["library_id"] == entry["library_id"]
    assert (dst / row["sprite_path"]).read_bytes() == png.read_bytes()
    dst_events = [
        json.loads(line)
        for line in (dst / ".canon" / "journal.jsonl").read_text().splitlines()
    ]
    imp = [e for e in dst_events if e.get("detail", {}).get("kind") == "library_import"]
    assert imp and imp[-1]["op"] == "import" and imp[-1]["source"] == "import"

    assign_asset(src, f"enemy:{eid}", f"enemy:{other}", actor="test")
    a = json.loads((src / "enemy" / f"{eid}.json").read_text())
    b = json.loads((src / "enemy" / f"{other}.json").read_text())
    assert a["sprite_hash"] == b["sprite_hash"]
    tree = asset_lineage(src, f"enemy:{eid}")
    shared = [n for n in tree["nodes"] if len(n["artifacts"]) > 1]
    assert any(
        {f"enemy:{eid}", f"enemy:{other}"} <= set(n["artifacts"]) for n in shared
    )


def test_library_import_rewrites_animation_manifests(pack: Path, tmp_path, monkeypatch):
    """Playback manifests (frames/atlas.json) embed source-dir paths and the
    play surfaces read THOSE — imports under a fresh id must rewrite them or
    the enemy animates with the wrong actor's art (or silently goes static)."""
    import shutil

    from PIL import Image

    monkeypatch.setenv("CANON_LIBRARY", str(tmp_path / "lib"))
    from canon import library
    from canon.adapters.platformer_write import replace_asset

    src = tmp_path / "anim_src"
    dst = tmp_path / "anim_dst"
    shutil.copytree(pack, src)
    shutil.copytree(pack, dst)
    eid = sorted(q.stem for q in (src / "enemy").glob("*.json"))[0]
    png = src / "b.png"
    Image.new("RGBA", (32, 32), (60, 60, 220, 255)).save(png)
    replace_asset(src, f"enemy:{eid}", png, actor="test")
    ops.animate_asset(
        src, f"enemy:{eid}", image_backend="fake", vlm_backend="fake", actor="test"
    )

    entry = library.publish(src, f"enemy:{eid}", actor="test")
    assert any(k.startswith("file:") for k in entry["objects"])  # bundle traveled
    result = library.import_entry(dst, entry["library_id"], actor="test")
    nid = result["id"]
    assert nid != eid
    new_dir = dst / "sprite" / "enemy" / nid
    for manifest_name in ("frames.json", "atlas.json"):
        mf = new_dir / manifest_name
        if not mf.is_file():
            continue
        text = mf.read_text()
        assert f"sprite/enemy/{nid}" in text
        assert f"sprite/enemy/{eid}/" not in text


def test_assign_static_clears_stale_animation(pack: Path, tmp_path):
    """Assigning a STATIC sprite over an animated row must clear the old
    animation block AND the leftover manifests, or the dest keeps playing
    the previous art."""
    import shutil

    from PIL import Image

    from canon.adapters.platformer_write import assign_asset, replace_asset

    p = tmp_path / "assign_static"
    shutil.copytree(pack, p)
    ids = sorted(q.stem for q in (p / "enemy").glob("*.json"))
    animated, static = ids[0], ids[1]
    png = p / "s.png"
    Image.new("RGBA", (32, 32), (220, 60, 60, 255)).save(png)
    replace_asset(p, f"enemy:{animated}", png, actor="test")
    ops.animate_asset(
        p, f"enemy:{animated}", image_backend="fake", vlm_backend="fake", actor="test"
    )
    png2 = p / "s2.png"
    Image.new("RGBA", (32, 32), (60, 220, 60, 255)).save(png2)
    replace_asset(p, f"enemy:{static}", png2, actor="test")

    # Static source over animated dest: animation gone, manifests gone.
    assign_asset(p, f"enemy:{static}", f"enemy:{animated}", actor="test")
    row = json.loads((p / "enemy" / f"{animated}.json").read_text())
    assert "animation" not in (row.get("stats") or {})
    dest_dir = p / "sprite" / "enemy" / animated
    assert not (dest_dir / "frames.json").exists()
    assert not (dest_dir / "atlas.json").exists()
    # And animated-over-static rewrites manifest paths to the dest dir.
    assign_asset(p, f"enemy:{animated}", f"enemy:{static}", actor="test")
    sdir = p / "sprite" / "enemy" / static
    if (sdir / "frames.json").is_file():
        assert f"sprite/enemy/{static}" in (sdir / "frames.json").read_text()


def test_generate_level_full_draft_and_journals(pack: Path, tmp_path):
    """generate_level writes a full playable DRAFT (terrain + enemies +
    items), validates ok, stays OUT of the manifest, and journals generate
    events."""
    import shutil

    p = tmp_path / "gen_full"
    shutil.copytree(pack, p)
    stage_id = sorted(q.name for q in (p / "tileset").iterdir() if q.is_dir())[0]
    manifest_before = (p / "manifest.json").read_text()

    r = ops.generate_level(
        p, stage_id=stage_id, brief="a test gauntlet", backend="fake",
        max_enemies=2, max_items=4, actor="test",
    )
    lid = r["level_id"]
    assert r["ok"] is True
    assert r["seed"]  # effective seed recorded for reproducibility

    level_dir = p / "level" / stage_id / lid
    for name in ("collision.npz", "entities.json", "items.json", "level.json"):
        assert (level_dir / name).is_file(), name

    # Draft doctrine: the pack manifest is untouched (publish is separate).
    assert (p / "manifest.json").read_text() == manifest_before
    assert lid not in json.loads(manifest_before)["levels"]

    # Provenance: the new level's steps journal op=generate.
    gen = [
        e for e in _journal(p)
        if e.get("op") == "generate" and f"/{lid}/" in e.get("artifact_id", "")
    ]
    assert gen, "generated level must journal generate events"


def test_generate_terrain_then_place_is_composable(pack: Path, tmp_path):
    """Terrain and placement are independent: generate-terrain leaves no
    placements; place_enemies then fills them on that grid (grid-driven, so it
    would equally serve a hand-painted level)."""
    import shutil

    p = tmp_path / "gen_compose"
    shutil.copytree(pack, p)
    stage_id = sorted(q.name for q in (p / "tileset").iterdir() if q.is_dir())[0]

    t = ops.generate_terrain(
        p, stage_id=stage_id, brief="terrain only", backend="fake", actor="test"
    )
    lid = t["level_id"]
    level_dir = p / "level" / stage_id / lid
    assert t["ok"] is True
    assert json.loads((level_dir / "entities.json").read_text()) == []

    e = ops.place_enemies(p, level_id=lid, backend="fake", max_enemies=2, actor="test")
    assert e["ok"] is True
    assert len(json.loads((level_dir / "entities.json").read_text())) >= 1


def test_generate_level_honors_full_control_pins(pack: Path, tmp_path):
    """Full-control width/height pins constrain the rolled dims."""
    import shutil

    import numpy as np

    p = tmp_path / "gen_pins"
    shutil.copytree(pack, p)
    stage_id = sorted(q.name for q in (p / "tileset").iterdir() if q.is_dir())[0]

    r = ops.generate_terrain(
        p, stage_id=stage_id, brief="pinned", backend="fake",
        width=64, height=18, axis="horizontal", actor="test",
    )
    level_dir = p / "level" / stage_id / r["level_id"]
    with np.load(level_dir / "collision.npz") as z:
        h, w = z["collision"].shape
    assert (h, w) == (18, 64)


# ---------------------------------------------------------------------------
# Context-aware IMPROVE: the layout LLM sees the current level + an instruction
# and re-authors it in place, keeping dims/axis. The fake responder reacts to
# "harder"/"easier" so the $0 path proves the improve is genuinely GUIDED.
# ---------------------------------------------------------------------------


def _improve_grid(pack: Path, level_id: str = "l1"):
    import numpy as np

    level_dir = next(pack.glob(f"level/*/{level_id}"))
    with np.load(level_dir / "collision.npz") as z:
        return z["collision"].copy()


def test_improve_terrain_is_guided_and_preserves_dims(pack: Path, tmp_path):
    """The same level improved 'harder' vs 'easier' yields DIFFERENT terrain
    (the model saw the instruction), both at $0, with the grid dims/axis kept."""
    import shutil

    import numpy as np

    orig = _improve_grid(pack)  # untouched module pack, read-only
    ph = tmp_path / "harder"
    pe = tmp_path / "easier"
    shutil.copytree(pack, ph)
    shutil.copytree(pack, pe)

    rh = ops.improve_terrain(
        ph, level_id="l1", instruction="make it harder", backend="fake",
        seed="pin", actor="test",
    )
    re_ = ops.improve_terrain(
        pe, level_id="l1", instruction="make it easier", backend="fake",
        seed="pin", actor="test",
    )
    gh, ge = _improve_grid(ph), _improve_grid(pe)

    assert not np.array_equal(gh, ge), "instruction must steer the re-author"
    assert rh["cost"]["usd"] == 0.0 and re_["cost"]["usd"] == 0.0  # fake = $0
    assert rh["improved"] is True
    # Improve keeps the current dims + axis (it refines, it doesn't resize).
    assert gh.shape == ge.shape == orig.shape
    prior_axis = json.loads(
        next(ph.glob("level/*/l1/level.json")).read_text()
    )["layout_axis"]
    assert prior_axis == json.loads(
        next(pack.glob("level/*/l1/level.json")).read_text()
    )["layout_axis"]


def test_improve_terrain_journals_regenerate_from_llm(pack: Path, tmp_path):
    """Improve is an LLM iteration on an existing artifact — op=regenerate,
    source=llm (distinct from generate/edit in the training taxonomy)."""
    import shutil

    p = tmp_path / "improve_journal"
    shutil.copytree(pack, p)
    ops.improve_terrain(
        p, level_id="l1", instruction="add a bridge", backend="fake", actor="test"
    )
    regen = [
        e for e in _journal(p)
        if e.get("op") == "regenerate" and "/l1/" in e.get("artifact_id", "")
    ]
    assert regen, "improve must journal op=regenerate for the level"
    assert all(e["source"] == "llm" for e in regen)


def test_improve_terrain_placement_toggle(pack: Path, tmp_path):
    """Keep (default) leaves entities/items.json BYTE-IDENTICAL so the user's
    placements survive; reroll re-runs the grid-driven place steps (extra
    calls) to re-adapt them to the improved terrain."""
    import shutil

    keep = tmp_path / "keep"
    shutil.copytree(pack, keep)
    ld = next(keep.glob("level/*/l1"))
    ent_before = (ld / "entities.json").read_bytes()
    itm_before = (ld / "items.json").read_bytes()
    rk = ops.improve_terrain(
        keep, level_id="l1", instruction="tweak", backend="fake",
        reroll_placements=False, seed="pin", actor="test",
    )
    assert (ld / "entities.json").read_bytes() == ent_before
    assert (ld / "items.json").read_bytes() == itm_before

    reroll = tmp_path / "reroll"
    shutil.copytree(pack, reroll)
    rr = ops.improve_terrain(
        reroll, level_id="l1", instruction="tweak", backend="fake",
        reroll_placements=True, seed="pin", actor="test",
    )
    # Reroll folds the two extra place ops' calls into the cost block.
    assert rr["cost"]["calls"] > rk["cost"]["calls"]
    # ...and the re-derived placements are valid on the new grid.
    assert isinstance(
        json.loads((next(reroll.glob("level/*/l1")) / "entities.json").read_text()),
        list,
    )


def test_improve_terrain_fix_problems_clears_a_terrain_problem(pack: Path, tmp_path):
    """With fix_problems the level's current validation problems ride the
    feedback list; the re-author + the standard repair tail (auto_bridge_grid
    etc.) yields a playable level with the terrain problem gone."""
    import shutil

    import numpy as np

    p = tmp_path / "improve_fix"
    shutil.copytree(pack, p)
    ld = next(p.glob("level/*/l1"))
    with np.load(ld / "collision.npz") as z:
        g = z["collision"].copy()
    h, w = g.shape
    g[:, w // 4:(3 * w) // 4] = 0  # punch a wide chasm → a terrain problem
    np.savez_compressed(ld / "collision.npz", collision=g)

    before = [
        c for c in ops.validate_level(p, "l1")["checks"] if c["name"] == "terrain"
    ][0]["problems"]
    assert before, "the damaged grid must report a terrain problem first"

    r = ops.improve_terrain(
        p, level_id="l1", instruction="repair the level", fix_problems=True,
        reroll_placements=True, backend="fake", seed="pin", actor="test",
    )
    after = [
        c for c in ops.validate_level(p, "l1")["checks"] if c["name"] == "terrain"
    ][0]["problems"]
    assert r["ok"] is True
    assert after == []


def test_improve_terrain_pinned_seed_is_deterministic(pack: Path, tmp_path):
    """A pinned seed + the same instruction reproduces the level byte-for-byte
    (terrain and re-rolled placements) — reproducible for A/B and fixtures."""
    import shutil

    import numpy as np

    a = tmp_path / "det_a"
    b = tmp_path / "det_b"
    shutil.copytree(pack, a)
    shutil.copytree(pack, b)
    kw = dict(
        level_id="l1", instruction="make it harder", backend="fake",
        seed="fixed-seed", reroll_placements=True, actor="test",
    )
    ops.improve_terrain(a, **kw)
    ops.improve_terrain(b, **kw)
    assert np.array_equal(_improve_grid(a), _improve_grid(b))
    ea = (next(a.glob("level/*/l1")) / "entities.json").read_bytes()
    eb = (next(b.glob("level/*/l1")) / "entities.json").read_bytes()
    assert ea == eb


def test_level_revision_and_last_change(pack: Path, tmp_path):
    """The export bundle carries a content REVISION (composite hash) that changes
    on every real edit but not on a byte-identical no-op, plus a LAST_CHANGE
    label naming how it last changed (generate → improve)."""
    import shutil

    from canon.adapters.platformer_read import export_level_bundle
    from canon.adapters.platformer_write import baseline_level

    p = tmp_path / "revpack"
    shutil.copytree(pack, p)
    baseline_level(p, "l1", actor="test", detail={"kind": "generate"})

    b0 = export_level_bundle(p, "l1")
    assert b0["revision"].startswith("sha256:")
    assert len(b0["revision_short"]) == 10
    assert b0["last_change"]["label"] == "Generated"

    ops.improve_terrain(
        p, level_id="l1", instruction="make it harder", backend="fake",
        seed="pin", actor="test",
    )
    b1 = export_level_bundle(p, "l1")
    assert b1["revision"] != b0["revision"]  # terrain changed → new identity
    assert b1["last_change"]["label"] == "Improved"
    assert b1["last_change"]["op"] == "regenerate"

    # A byte-identical re-improve is a no-op → the revision is stable.
    ops.improve_terrain(
        p, level_id="l1", instruction="make it harder", backend="fake",
        seed="pin", actor="test",
    )
    b2 = export_level_bundle(p, "l1")
    assert b2["revision"] == b1["revision"]


def test_improve_terrain_change_signal(pack: Path, tmp_path):
    """The op reports whether it actually changed anything (the job tray's 'did
    it change?' indicator): a real improve → changed=True with the touched
    artifacts; a byte-identical re-run → changed=False (baseline is idempotent,
    so no new journal event)."""
    import shutil

    p = tmp_path / "improve_change"
    shutil.copytree(pack, p)
    r1 = ops.improve_terrain(
        p, level_id="l1", instruction="make it harder", backend="fake",
        seed="pin", actor="test",
    )
    assert r1["changed"] is True
    assert any("collision" in a for a in r1["changed_artifacts"])

    # Re-authoring the exact same thing writes byte-identical files → no change.
    r2 = ops.improve_terrain(
        p, level_id="l1", instruction="make it harder", backend="fake",
        seed="pin", actor="test",
    )
    assert r2["changed"] is False
    assert r2["changed_artifacts"] == []


# ---------------------------------------------------------------------------
# Cost: pre-run estimate (backend/count aware), true per-op actuals, ledger.
# ---------------------------------------------------------------------------


def test_estimate_world_is_backend_and_count_aware():
    from canon.packs.platformer.estimate import estimate_cradle

    counts = {"num_stages": 3, "num_levels": 9, "num_enemies": 7, "num_items": 5}
    fake = estimate_cradle(
        "world", counts=counts,
        backends={"llm": "fake", "image": "fake", "music": "none",
                  "sfx": "none", "vlm": "none"},
    )
    # fake/none = $0, but the COUNTS survive so the UI can show what an upgrade
    # would cost.
    assert fake["total_usd"] == {"best": 0.0, "worst": 0.0}
    assert fake["assets"]["images"]["count"] > 0

    paid = estimate_cradle(
        "world", counts=counts,
        backends={"llm": "anthropic", "image": "fal", "music": "none",
                  "sfx": "none", "vlm": "none"},
    )
    assert paid["llm"]["usd"]["best"] > 0
    assert paid["assets"]["images"]["usd"] > 0
    # music/sfx/vlm were unpaid → zeroed even though a full run would price them
    assert paid["assets"]["music"]["usd"] == 0.0
    assert paid["assets"]["sfx"]["usd"] == 0.0
    assert (paid["assets"]["vlm"] or {}).get("usd", {"best": 0}).get("best", 0) == 0

    # Twice the levels ⇒ strictly more LLM spend (pure pricing math, no API).
    big = estimate_cradle(
        "world", counts={**counts, "num_levels": 18},
        backends={"llm": "anthropic"},
    )
    assert big["llm"]["usd"]["best"] > paid["llm"]["usd"]["best"]


def test_estimate_level_op_scopes(pack: Path):
    from canon.packs.platformer.estimate import estimate_cradle

    fake = estimate_cradle(
        "generate", pack_dir=pack, level_id="l1", backends={"llm": "fake"}
    )
    assert fake["total_usd"] == {"best": 0.0, "worst": 0.0}
    assert set(fake["llm"]["by_task"]) == {
        "plat:layout", "plat:placement", "plat:item_placement", "plat:decorator"
    }
    # Paid pricing is deterministic (token tables × PRICING) — no API call.
    gen = estimate_cradle(
        "generate", pack_dir=pack, level_id="l1", backends={"llm": "anthropic"}
    )
    enemies = estimate_cradle(
        "enemies", pack_dir=pack, level_id="l1", backends={"llm": "anthropic"}
    )
    assert gen["total_usd"]["best"] > enemies["total_usd"]["best"] > 0
    assert gen["assets"]["images"]["count"] == 0  # per-op is LLM-only


def test_ops_report_actual_cost_block(pack: Path):
    # Fake backend has no usage, so the op reports a real $0 with call counts —
    # the same field a paid run fills with measured token cost.
    r = ops.place_enemies(pack, level_id="l1", backend="fake")
    cost = r["cost"]
    assert cost["usd"] == 0.0
    assert cost["backend"] == "fake"
    assert cost["calls"] >= 1


def test_spend_ledger_round_trip(tmp_path: Path):
    from canon import spend

    p = tmp_path / "spend_pack"
    p.mkdir()
    spend.record_spend(p, {
        "op": "generate", "scope": "level", "level_id": "l1",
        "backends": {"llm": "anthropic"},
        "estimate": {"best": 0.08, "worst": 0.32}, "actual_usd": 0.07,
    })
    spend.record_spend(p, {
        "op": "enemies", "scope": "level", "backends": {"llm": "fake"},
        "actual_usd": 0.0,
    })
    s = spend.summarize(p)
    assert s["count"] == 2
    assert s["total_actual_usd"] == 0.07
    # estimate total is an INDEPENDENT comparison sum over ALL ops (not gated on
    # having an actual) — forecast $0.08 vs measured $0.07.
    assert s["total_estimate_usd"] == 0.08
    assert s["by_op"]["generate"]["actual_usd"] == 0.07
    assert s["by_op"]["generate"]["estimate_usd"] == 0.08
    assert s["by_op"]["enemies"]["count"] == 1
    # every stored line carries the authoritative schema + a ts
    assert all(e["schema"] == spend.SCHEMA and e.get("ts") for e in s["entries"])


def test_spend_read_skips_malformed_lines(tmp_path: Path):
    """A valid-JSON but non-object line (or garbage) is skipped, not fatal —
    `canon spend list` must survive a hand-tampered ledger."""
    from canon import spend

    p = tmp_path / "junk_pack"
    p.mkdir()
    spend.record_spend(p, {"op": "generate", "actual_usd": 0.05})
    ledger = p / ".canon" / "spend.jsonl"
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write("42\n")  # valid JSON, not a dict
        fh.write('"oops"\n')
        fh.write("{not json\n")  # not JSON at all
        fh.write("\n")  # blank
    s = spend.summarize(p)  # must not raise
    assert s["count"] == 1
    assert s["total_actual_usd"] == 0.05


# ---------------------------------------------------------------------------
# Per-level / per-section music: model, position resolver (parity), assign via
# apply-edit, generate (reroll) + audio cost capture.
# ---------------------------------------------------------------------------


def test_music_section_model_is_additive_and_roundtrips():
    from canon.bible.platformer import Level

    plain = Level(level_id="l1", stage_id="s1")  # old bibles: no music fields
    assert plain.music_path == "" and plain.music_sections == []
    lv = Level(
        level_id="l1", stage_id="s1", music_path="music/s1/l1/theme.mp3",
        music_sections=[{"start": 0, "end": 20, "music_path": "a.mp3", "name": "intro"}],
    )
    assert lv.music_sections[0].start == 0 and lv.music_sections[0].name == "intro"


def test_active_music_resolves_section_then_level_then_stage():
    # The parity-critical pure resolver — main.gd ports it verbatim.
    from canon.packs.platformer.play import _active_music

    level = {
        "music_path": "level.mp3",
        "music_sections": [
            {"start": 0, "end": 20, "music_path": "intro.mp3"},
            {"start": 40, "end": 60, "music_path": ""},  # deliberate silence
        ],
    }
    assert _active_music(level, 5, "stage.mp3") == "intro.mp3"   # in a section
    assert _active_music(level, 30, "stage.mp3") == "level.mp3"  # gap → level
    assert _active_music(level, 50, "stage.mp3") == ""           # silent section
    assert _active_music({}, 5, "stage.mp3") == "stage.mp3"      # nothing → stage


def test_apply_edit_music_path_and_sections_persist_and_journal(pack: Path, tmp_path: Path):
    import shutil

    from canon.adapters.platformer_write import apply_level_edit

    p = tmp_path / "music_edit"
    shutil.copytree(pack, p)
    r = apply_level_edit(p, "l1", {
        "music_path": "music/x/l1/theme.mp3", "music_hash": "sha256:abc",
        "music_sections": [{"start": 5, "end": 15, "music_path": "s.mp3", "name": "cave"}],
    }, actor="test")
    assert "music" in r["updated"] and "music_sections" in r["updated"]
    level = json.loads(next(p.glob("level/*/l1/level.json")).read_text())
    assert level["music_path"] == "music/x/l1/theme.mp3"
    assert level["music_sections"][0]["name"] == "cave"
    assert level["status"] == "user_edited"
    ev = [json.loads(x) for x in (p / ".canon" / "journal.jsonl").read_text().splitlines()]
    assert any(e.get("detail", {}).get("kind") == "level_edit" for e in ev)


def test_generate_level_music_writes_track_repoints_and_costs(pack: Path, tmp_path: Path):
    import shutil

    from canon.packs.platformer import ops

    p = tmp_path / "music_gen"
    shutil.copytree(pack, p)
    r = ops.generate_level_music(p, level_id="l1", brief="tense", backend="fake")
    assert r["music_path"].startswith("music/") and r["music_path"].endswith(".mp3")
    assert (p / r["music_path"]).exists()
    assert r["cost"]["usd"] == 0.0 and r["cost"]["audio_usd"] == 0.0  # fake = $0
    level = json.loads(next(p.glob("level/*/l1/level.json")).read_text())
    assert level["music_path"] == r["music_path"]


def test_generate_level_music_captures_real_audio_cost(pack: Path, tmp_path: Path, monkeypatch):
    """A backend that reports a per-call last_cost lands in the cost block —
    the wiring a real Lyria run exercises (fake reports $0)."""
    import shutil

    from canon.packs.platformer import ops

    class _StubMusic:
        model = "stub-music"
        last_cost = 0.08

        def generate(self, prompt, seconds):
            return b"ID3stub-mp3"

    monkeypatch.setattr(
        "canon.packs.platformer.audio_phases.build_music_producer",
        lambda kind: _StubMusic(),
    )
    p = tmp_path / "music_cost"
    shutil.copytree(pack, p)
    r = ops.generate_level_music(p, level_id="l1", backend="stub")
    assert r["cost"]["audio_usd"] == 0.08
    assert r["cost"]["usd"] == 0.08  # total = audio (no LLM/image)


def test_estimate_music_scope_is_backend_masked():
    from canon.packs.platformer.estimate import estimate_cradle

    assert estimate_cradle("music", backends={"music": "fake"})["total_usd"]["best"] == 0.0
    assert estimate_cradle("music", backends={"music": "lyria"})["total_usd"]["best"] > 0.0


# ---------------------------------------------------------------------------
# per-call prompt override — "see the prompt, edit it, run THIS call with it"
# ---------------------------------------------------------------------------


def _systems_seen(monkeypatch) -> list[str]:
    """Record the system prompt of every LLM request an op actually sends.

    Always wraps the PRISTINE ``build_llm`` (via the marker below) so calling
    this more than once in a test installs independent spies instead of
    stacking them — a stacked spy would also feed the earlier test's list.
    """
    seen: list[str] = []
    real_build = getattr(ops.build_llm, "_pristine", ops.build_llm)

    def spy(kind, model=None, stats=None):
        client = real_build(kind, model, stats=stats)
        if client is None:
            return None
        inner = client.backend.generate

        def wrapped(request):
            seen.append(str(getattr(request, "system", "")))
            return inner(request)

        client.backend.generate = wrapped
        return client

    spy._pristine = real_build  # type: ignore[attr-defined]
    monkeypatch.setattr(ops, "build_llm", spy)
    return seen


def test_preview_prompt_covers_every_kind(pack: Path):
    """Every editable generator can show its default prompt WITHOUT generating:
    LLM kinds split system/user, image/audio/vlm kinds return one prompt
    string."""
    enemy_id = next(iter(ops._load_defs(pack, "enemy")))
    item_id = next(iter(ops._load_defs(pack, "item")))
    cases = {
        "layout": {},
        "improve": {"level_id": "l1", "instruction": "add a bridge"},
        "enemy": {"target": enemy_id},
        "item": {"target": item_id},
        "sprite": {"target": f"enemy:{enemy_id}"},
        "animate": {"target": f"enemy:{enemy_id}"},
        "music": {"level_id": "l1"},
    }
    for kind, kwargs in cases.items():
        out = ops.preview_prompt(pack, kind, **kwargs)
        assert out["label"] == ops.PROMPT_KINDS[kind]["label"]
        if out["mode"] == "llm":
            assert out["system"].strip() and out["user_message"].strip()
        else:
            assert out["prompt"].strip()

    # The improve preview must actually carry the instruction + level context.
    imp = ops.preview_prompt(
        pack, "improve", level_id="l1", instruction="widen the final gap"
    )
    assert "widen the final gap" in imp["user_message"]
    # A targeted row preview shows only ROLLED spec fields (no artifact_id/status).
    row = ops.preview_prompt(pack, "enemy", target=enemy_id)
    assert "artifact_id" not in row["user_message"]
    # The animate preview is the VLM's AUTHORING prompt, built from the same
    # subject + state list the run derives (preview == what runs) — not the
    # per-state img2img sheet prompt, which is issued once per state.
    anim = ops.preview_prompt(pack, "animate", target=f"enemy:{enemy_id}")
    assert anim["mode"] == "vlm"
    spec_in = ops._animate_actor_spec(
        ops._sprite_bible(ops.load_pack(pack), "enemy", enemy_id), "enemy", enemy_id
    )
    for state in spec_in.states:
        assert f'"{state}"' in anim["prompt"]
    with pytest.raises(ValueError, match="unknown prompt kind"):
        ops.preview_prompt(pack, "nonsense")


def test_preview_prompt_is_pure_read(pack: Path, tmp_path):
    """Previewing costs nothing and mutates nothing — no journal, no writes."""
    import shutil

    p = tmp_path / "preview_pure"
    shutil.copytree(pack, p)
    before_events = len(_journal(p))
    before_tree = {
        f.relative_to(p): f.stat().st_mtime_ns
        for f in sorted(p.rglob("*")) if f.is_file()
    }
    for kind in ("layout", "improve", "music"):
        ops.preview_prompt(p, kind, level_id="l1")
    after_tree = {
        f.relative_to(p): f.stat().st_mtime_ns
        for f in sorted(p.rglob("*")) if f.is_file()
    }
    assert len(_journal(p)) == before_events
    assert before_tree == after_tree


def test_system_override_reaches_the_model_and_only_its_agent(pack: Path, tmp_path, monkeypatch):
    """The edited system prompt is what the layout agent receives — and the
    override is keyed by agent label, so a whole-level generate's PLACEMENT
    calls keep their own system prompt."""
    import shutil

    p = tmp_path / "override_generate"
    shutil.copytree(pack, p)
    stage_id = json.loads((p / "manifest.json").read_text())["stages"][0]["stage_id"]
    seen = _systems_seen(monkeypatch)
    ops.generate_level(
        p, stage_id=stage_id, brief="a test level", backend="fake", seed="pin",
        system_override="HOUSE-RULES-ONLY", actor="test",
    )
    assert "HOUSE-RULES-ONLY" in seen, "layout agent must receive the override"
    assert any(s != "HOUSE-RULES-ONLY" for s in seen), (
        "placement agents must keep their own system prompt"
    )


def test_no_override_is_byte_identical(pack: Path, tmp_path, monkeypatch):
    """The seam is ADDITIVE: absent an override, the op sends exactly today's
    prompts and produces exactly today's bytes."""
    import shutil

    base, none_, empty = (tmp_path / n for n in ("ov_base", "ov_none", "ov_empty"))
    for d in (base, none_, empty):
        shutil.copytree(pack, d)

    seen_default = _systems_seen(monkeypatch)
    ops.improve_terrain(
        base, level_id="l1", instruction="make it harder", backend="fake",
        seed="pin", actor="test",
    )
    seen_none = _systems_seen(monkeypatch)
    ops.improve_terrain(
        none_, level_id="l1", instruction="make it harder", backend="fake",
        seed="pin", system_override=None, actor="test",
    )
    seen_empty = _systems_seen(monkeypatch)
    ops.improve_terrain(
        empty, level_id="l1", instruction="make it harder", backend="fake",
        seed="pin", system_override="", actor="test",
    )
    # Same prompts sent...
    assert seen_default == seen_none == seen_empty
    # ...and the same bytes on disk.
    grids = [
        (d / next(d.glob("level/*/l1")).relative_to(d) / "collision.npz").read_bytes()
        for d in (base, none_, empty)
    ]
    assert grids[0] == grids[1] == grids[2]


def test_sprite_and_music_prompt_overrides_reach_the_producer(pack: Path, tmp_path, monkeypatch):
    """Image/audio generators have no system/user split — the whole prompt is
    editable, and the override is what the producer is asked to render."""
    import shutil

    prompts: list[str] = []

    class _SpyImage:
        model = "spy-image"
        last_cost = 0.0

        def generate(self, prompt, width, height):
            prompts.append(prompt)
            from canon.packs.platformer.tileset_art import build_image_producer

            return build_image_producer("fake").backend.generate(prompt, width, height)

    class _SpyMusic:
        model = "spy-music"
        last_cost = 0.0

        def generate(self, prompt, seconds):
            prompts.append(prompt)
            return b"ID3spy"

    p = tmp_path / "asset_override"
    shutil.copytree(pack, p)
    enemy_id = next(iter(ops._load_defs(p, "enemy")))

    monkeypatch.setattr(
        "canon.packs.platformer.tileset_art.build_image_producer",
        lambda *a, **k: __import__(
            "canon.packs.platformer.tileset_art", fromlist=["DiffusionSheetProducer"]
        ).DiffusionSheetProducer(_SpyImage()),
    )
    ops.generate_asset(
        p, f"enemy:{enemy_id}", image_backend="fake",
        prompt_override="A BRIGHT RED CUBE, nothing else", actor="test",
    )
    assert any("A BRIGHT RED CUBE" in s for s in prompts)

    prompts.clear()
    monkeypatch.setattr(
        "canon.packs.platformer.audio_phases.build_music_producer",
        lambda kind: _SpyMusic(),
    )
    ops.generate_level_music(
        p, level_id="l1", backend="stub",
        prompt_override="SOLO KAZOO MARCH", actor="test",
    )
    assert prompts == ["SOLO KAZOO MARCH"]


def _spy_animation_author(monkeypatch) -> list[str | None]:
    """Record the prompt_override each animate run hands the VLM author."""
    import canon.packs.platformer.vlm_qa as vq

    seen: list[str | None] = []

    def spy(judge, actor_id, subject, sprite_bytes, **kw):
        seen.append(kw.get("prompt_override"))
        return {"idle": {"frames": 2, "motion": "bob"}}

    monkeypatch.setattr(vq, "author_animation_spec", spy)
    return seen


def test_animate_prompt_override_reaches_the_vlm_author(pack: Path, tmp_path, monkeypatch):
    """The editable animate prompt is the VLM's motion-spec AUTHORING prompt
    (one call per run) — deliberately NOT the per-state img2img sheet prompt,
    which runs once per state per facing and whose per-state silhouette
    contract a single override would flatten."""
    import shutil

    p = tmp_path / "animate_override"
    shutil.copytree(pack, p)
    enemy_id = next(iter(ops._load_defs(p, "enemy")))
    seen = _spy_animation_author(monkeypatch)

    ops.animate_asset(
        p, f"enemy:{enemy_id}", image_backend="fake", vlm_backend="fake",
        prompt_override="MOVE LIKE A CRAB.", actor="test",
    )
    assert seen == ["MOVE LIKE A CRAB."]


def test_animate_without_override_is_byte_identical(pack: Path, tmp_path, monkeypatch):
    """ADDITIVE guarantee: no override, None, and "" all send the built prompt
    unchanged — the same promise the sprite/system overrides make."""
    from canon.packs.platformer.vlm_qa import animate_prompt, author_animation_spec

    sent: list[str] = []
    enemy_id = next(iter(ops._load_defs(pack, "enemy")))
    spec_in = ops._animate_actor_spec(
        ops._sprite_bible(ops.load_pack(pack), "enemy", enemy_id), "enemy", enemy_id
    )
    # A spec that VALIDATES first time: a retry would append rejection
    # feedback to the prompt and mask the comparison.
    valid = json.dumps({s: {"frames": 2, "motion": "bob"} for s in spec_in.states})

    class _Judge:
        model = "spy-vlm"

        def judge(self, text, images):
            sent.append(text)
            return valid

    args = (spec_in.actor_id, spec_in.subject, b"png")
    kwargs = {"states": spec_in.states, "frames_max": spec_in.frames_max}
    for override in (None, "", "   "):
        author_animation_spec(_Judge(), *args, prompt_override=override, **kwargs)

    built = animate_prompt(
        spec_in.actor_id, spec_in.subject, spec_in.states, spec_in.frames_max
    )
    assert sent == [built, built, built]


def test_animate_override_is_inert_for_reuse_spec(pack: Path, tmp_path, monkeypatch):
    """--reuse-spec replays the stored motion spec, so it never authors — the
    override has nothing to override (the CLI help says so)."""
    import shutil

    p = tmp_path / "animate_reuse"
    shutil.copytree(pack, p)
    enemy_id = next(iter(ops._load_defs(p, "enemy")))

    # Seed a stored spec so reuse_spec has something to replay.
    ops.animate_asset(
        p, f"enemy:{enemy_id}", image_backend="fake", vlm_backend="fake",
        actor="test",
    )
    seen = _spy_animation_author(monkeypatch)
    ops.animate_asset(
        p, f"enemy:{enemy_id}", image_backend="fake", vlm_backend="fake",
        reuse_spec=True, prompt_override="IGNORED.", actor="test",
    )
    assert seen == []


def test_cli_prompt_show_and_override_round_trip(pack: Path, tmp_path):
    """The user-facing loop: `prompt show` prints the default, an edited copy
    goes back in via --system-prompt-file, and the two flags are exclusive."""
    import shutil

    p = tmp_path / "cli_prompt"
    shutil.copytree(pack, p)

    shown = json.loads(
        subprocess.run(
            [sys.executable, "-m", "canon.cli.main", "prompt", "show", str(p),
             "--kind", "improve", "--level", "l1", "--instruction", "add a bridge"],
            check=True, capture_output=True, text=True, cwd=REPO,
        ).stdout
    )
    assert shown["mode"] == "llm" and shown["label"] == "plat:layout"

    edited = tmp_path / "system.txt"
    edited.write_text(shown["system"] + "\nEXTRA HOUSE RULE: leave a rest ledge.\n")
    out = json.loads(
        subprocess.run(
            [sys.executable, "-m", "canon.cli.main", "level", "improve", str(p),
             "--level", "l1", "--instruction", "make it harder", "--seed", "pin",
             "--llm-backend", "fake", "--system-prompt-file", str(edited)],
            check=True, capture_output=True, text=True, cwd=REPO,
        ).stdout
    )
    assert out["improved"] is True

    # Errors are emitted as JSON on STDERR with a non-zero exit code.
    refused = subprocess.run(
        [sys.executable, "-m", "canon.cli.main", "level", "improve", str(p),
         "--level", "l1", "--instruction", "x", "--system-prompt", "A",
         "--system-prompt-file", str(edited)],
        capture_output=True, text=True, cwd=REPO,
    )
    assert refused.returncode != 0
    assert "not both" in json.loads(refused.stderr)["error"]
