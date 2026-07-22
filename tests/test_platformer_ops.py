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
