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

from examples.platformer_pack import ops  # noqa: E402

ENEMY_SCHEMA = REPO / "examples" / "platformer_pack" / "schemas" / "enemy.json"


@pytest.fixture(scope="module")
def pack(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("ops_pack")
    subprocess.run(
        [
            sys.executable,
            str(REPO / "examples" / "run_platformer_slice.py"),
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

    from examples.platformer_pack.phases import SCHEMAS_DIR, _schema_for

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

    from canon.adapters.platformer_read import asset_lineage
    from canon.adapters.platformer_write import replace_asset

    from PIL import Image

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
