"""Row P0-5 — W1 P2 read: the dungeon EntityKind loaders (P0 paper P.3.1
``loader``, §8.2), ``skeleton_view``'s rename inverses (P.1.x maps), and the
one grid export serving both shapes (``export_room_bundle`` — P.6.3 / P.6.3a —
behind ``canon grid export`` with ``canon level export`` as its alias).

Every read here is pinned as a pure projection: the pack's file list and
hashes are snapshotted before and after (doctrine 1; P.6.4 "byte-untouched").
"""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from canon.adapters.dungeon_read import export_room_bundle
from canon.adapters.platformer_read import export_level_bundle
from canon.packs import PACKS, EntityKind
from canon.packs.dungeon.loaders import load_rows, skeleton_view
from canon.packs.dungeon.parsers import _NPC_TYPE_MAP

REPO = Path(__file__).resolve().parents[1]
DUNGEON_FIXTURE = REPO / "tests" / "reference" / "fixtures" / "cradle_mazeworld_scifi"
CANON = [sys.executable, "-m", "canon.cli.main"]

DUNGEON = PACKS["dungeon"]
PLATFORMER = PACKS["platformer"]

#: P.6.3a's key set, key for key (the platformer-only keys ride along neutral
#: so the set equals the level bundle's + ``room`` / ``warnings``).
P63A_KEYS = {
    "level_id", "stage_id", "display_name", "revision", "revision_short", "last_change",
    "grid_width", "grid_height", "spawn", "exit", "tile_px", "actor_scale", "water_alpha",
    "variants", "grids", "tileset", "tiles_by_type", "entities", "items", "triggers",
    "hazards", "foreground", "props", "backdrop", "music_path", "music_sections",
    "warnings", "room",
}


def _canon(*args: str) -> tuple[int, object]:
    result = subprocess.run(CANON + list(args), capture_output=True, text=True, cwd=REPO)
    stream = result.stdout if result.returncode == 0 else result.stderr
    try:
        return result.returncode, json.loads(stream)
    except json.JSONDecodeError:
        return result.returncode, stream


def _tree(root: Path) -> dict[str, str]:
    """Every file under *root* → sha256, the before/after pin for "read verbs
    write nothing"."""
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.fixture(scope="module")
def plat_pack(tmp_path_factory) -> Path:
    """A fresh $0 platformer tree — the other GridKind the one export serves."""
    out = tmp_path_factory.mktemp("p05_plat")
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


# ---------------------------------------------------------------------------
# A synthetic 6×5 dungeon pack: one npc, one event, one item (P.6.1 encodings)
# ---------------------------------------------------------------------------

#: 6 wide × 5 high; (1,1) start, (4,3) door; the event at (2,1) reads -1 and
#: the item at (3,2) reads its id — the engine's truth.
_GRID = [
    [1, 1, 1, 1, 1, 1],
    [1, 0, -1, 0, 0, 1],
    [1, 0, 1, 2000, 0, 1],
    [1, 0, 0, 0, 0, 1],
    [1, 1, 1, 1, 1, 1],
]


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def synthetic_pack(root: Path, *, with_index: bool = True, mismatch: bool = False) -> Path:
    """A minimal dungeon tree resolving by manifest stamp (P.4.1 tier 2).
    ``mismatch`` moves the sidecar lists off their grid stamps — the P.6.3
    disagreement the export must NAME and never block on."""
    maze = {
        "layout_type": "maze",
        "extra": {},
        "grid": copy.deepcopy(_GRID),
        "width": 6,
        "height": 5,
        "door_position": [4, 3],
        "door_revealed": False,
        "gate_encounter_id": 3000,
        "player_start": [1, 1],
        "npc_positions": {"1000": [1, 3]},
        "item_placements": [
            {"x": 3, "y": 2, "item_id": 2000, "name": "ration cube", "portrait_prompt": "", "profile_image": ""}
        ],
        "event_positions": [{"x": 2, "y": 1, "event_id": 3000}],
        "quest_ids": [4000],
        "environment": "forest",
        "environment_name": "The Whispering Wood",
    }
    if mismatch:
        maze["item_placements"][0]["x"] = 4  # grid still says (3, 2)
        maze["event_positions"][0]["y"] = 3  # grid still says (2, 1)
    _write(root / "rooms" / "room_0" / "maze.json", maze)
    _write(root / "manifest.json", {"pack_type": "dungeon", "seed": 1, "num_rooms": 1})
    _write(
        root / "npcs" / "npcs.json",
        [{"id": 1000, "type": "RandomNPC", "name": "Mira", "environment": "forest",
          "profile_image": "portraits/npcs/npc_1000.png", "x": 9, "y": 9}],
    )
    _write(
        root / "items" / "items.json",
        {"2000": {"category": "food", "name": "ration cube", "item_stats": {"uses": 1}, "profile_image": ""}},
    )
    _write(
        root / "events" / "events.json",
        [{"id": 3000, "type": "combat", "name": "Ambush", "is_gate": True, "is_climax_boss": False,
          "monster_ids": [5000], "x": 1, "y": 1}],
    )
    _write(root / "monsters" / "monsters.json", {"5000": {"id": 5000, "name": "Wolf", "is_boss": True}})
    _write(root / "quests" / "quests.json", [{"id": 4000, "type": "fetch", "title": "Find bread"}])
    _write(root / "classes" / "classes.json", [{"archetype": "warrior", "name": "Warrior", "stats": {"STR": 16}}])
    stub = {"entity_type": "monster", "entity_id": "5000", "name": "Wolf", "room_id": "room_0", "lore": "", "tags": []}
    room = {
        "environment": "forest", "environment_name": "The Whispering Wood", "level": 1,
        "story_beat": "", "boss_name": "", "boss_lore": "", "maze_ref": "",
        "npcs": [], "items": [], "monsters": [stub], "encounters": ["3000"], "quests": ["4000"],
    }
    bible = {"story": {}, "rooms": {"room_0": room}, "player_classes": [], "entity_index": {}}
    _write(root / "world_bible.json", bible)
    if with_index:
        index_row = {"id": "room_0", **room, "maze_ref": "rooms/room_0/maze.json"}
        _write(root / "rooms" / "rooms.json", {"room_0": index_row})
    return root


# ---------------------------------------------------------------------------
# Loaders (§8.2) + skeleton_view (P.1 rename inverses)
# ---------------------------------------------------------------------------


class TestLoaders:
    @pytest.mark.parametrize("kind", list(DUNGEON.entities))
    def test_every_dungeon_kind_loads_with_string_ids(self, kind: str) -> None:
        entity = DUNGEON.entities[kind]
        assert callable(entity.loader), f"{kind} has no seeded loader"
        rows = entity.loader(DUNGEON_FIXTURE)
        assert isinstance(rows, dict)
        assert all(isinstance(key, str) for key in rows)
        assert rows == load_rows(DUNGEON_FIXTURE, entity)

    def test_fixture_counts_and_keys_by_layout_format(self) -> None:
        npcs = load_rows(DUNGEON_FIXTURE, DUNGEON.entities["npc"])  # array → str(row.id)
        assert len(npcs) == 79 and npcs["1000"]["name"] == "Mira Dustcrawler"
        assert npcs["1000"]["id"] == 1000  # the on-disk row, untouched: int id stays int
        items = load_rows(DUNGEON_FIXTURE, DUNGEON.entities["item"])  # keyed_object → its keys
        assert len(items) == 95 and "id" not in items["2000"]
        classes = load_rows(DUNGEON_FIXTURE, DUNGEON.entities["class"])  # array_positional → archetype
        assert list(classes) == ["warrior", "mage", "healer", "jester"]
        # legacy tree: no rooms.json / music.json / sfx.json → empty kinds, never an error
        for absent in ("room", "music", "sfx"):
            assert load_rows(DUNGEON_FIXTURE, DUNGEON.entities[absent]) == {}

    def test_loaders_write_nothing(self) -> None:
        before = _tree(DUNGEON_FIXTURE)
        for entity in DUNGEON.entities.values():
            entity.loader(DUNGEON_FIXTURE)
        assert _tree(DUNGEON_FIXTURE) == before

    def test_loader_is_seed_only(self) -> None:
        for kind, entity in DUNGEON.entities.items():
            assert "loader" not in entity.stamped(), kind
        assert "loader" not in DUNGEON.stamped()["entities"]["npc"]

    def test_array_row_without_its_id_keys_by_position(self, tmp_path: Path) -> None:
        entity = EntityKind(
            kind="thing", layout={"mode": "collection", "path": "things.json", "format": "array"}, id_field="id",
        )
        _write(tmp_path / "things.json", [{"id": 7, "a": 1}, {"a": 2}])
        assert list(load_rows(tmp_path, entity)) == ["7", "1"]

    def test_per_file_kinds_are_not_collection_loads(self) -> None:
        with pytest.raises(ValueError, match="collection"):
            load_rows(DUNGEON_FIXTURE, PLATFORMER.entities["enemy"])

    def test_p1_rename_maps_are_pinned_literally(self) -> None:
        """The inversion test below iterates the spec's own maps, so the P.1
        tables are pinned HERE as literals — a shrunk map cannot silently
        shrink the coverage. Kinds without renames stay ``{}``."""
        assert DUNGEON.entities["npc"].renames == {"behavior_type": "type"}
        assert DUNGEON.entities["item"].renames == {
            "item_kind": "category",
            "physical_type": "damage_type",
            "weapon_stat": "item_stats.stat_modifier",
            "tool_attribute": "item_stats.attribute",
        }
        assert DUNGEON.entities["quest"].renames == {"quest_type": "type"}
        assert DUNGEON.entities["event"].renames == {"event_type": "type"}
        for kind in ("monster", "class", "room", "music", "sfx"):
            assert DUNGEON.entities[kind].renames == {}, kind

    @pytest.mark.parametrize("kind", [k for k, e in DUNGEON.entities.items() if e.renames])
    def test_skeleton_view_inverts_every_rename(self, kind: str) -> None:
        """Every P.1 rename ``skeleton → disk`` comes back under the skeleton
        name, dotted targets lifted from their container, the disk key gone."""
        entity = DUNGEON.entities[kind]
        row: dict = {"untouched": "stays"}
        for index, (skeleton, disk) in enumerate(entity.renames.items()):
            node = row
            parts = disk.split(".")
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = f"value-{index}"
        view = skeleton_view(row, entity)
        for index, (skeleton, disk) in enumerate(entity.renames.items()):
            expected = f"value-{index}"
            if kind == "npc" and disk == "type":
                continue  # the value-changing rename — asserted below
            assert view[skeleton] == expected, (kind, skeleton)
            node: dict = view
            for part in disk.split(".")[:-1]:
                node = node.get(part, {})
            assert disk.split(".")[-1] not in node, (kind, disk)
        assert view["untouched"] == "stays"
        assert row["untouched"] == "stays" and "behavior_type" not in row  # input never mutated

    def test_npc_type_inverts_through_the_writers_own_table(self) -> None:
        entity = DUNGEON.entities["npc"]
        for behavior, class_name in _NPC_TYPE_MAP.items():
            view = skeleton_view({"id": 1, "type": class_name}, entity)
            assert view["behavior_type"] == behavior and "type" not in view
        # an unmapped on-disk value passes through rather than vanishing
        assert skeleton_view({"type": "MysteryNPC"}, entity)["behavior_type"] == "MysteryNPC"

    def test_item_dotted_targets_lift_out_of_item_stats(self) -> None:
        row = {"category": "weapon", "damage_type": "slashing", "item_stats": {"stat_modifier": "STR", "price": 3}}
        view = skeleton_view(row, DUNGEON.entities["item"])
        assert view["item_kind"] == "weapon" and view["physical_type"] == "slashing"
        assert view["weapon_stat"] == "STR" and view["item_stats"] == {"price": 3}
        assert "tool_attribute" not in view  # absent on disk → absent in the view
        assert row["item_stats"] == {"stat_modifier": "STR", "price": 3}

    def test_class_stats_are_authoritative_over_stat_template(self) -> None:
        row = {"archetype": "warrior", "stats": {"STR": 16}, "stat_template": {"STR": 10}}
        view = skeleton_view(row, DUNGEON.entities["class"])
        assert view["stat_template"] == {"STR": 16} and view["stats"] == {"STR": 16}
        assert skeleton_view({"archetype": "mage"}, DUNGEON.entities["class"]) == {"archetype": "mage"}


# ---------------------------------------------------------------------------
# The one export — export_room_bundle (P.6.3 / P.6.3a)
# ---------------------------------------------------------------------------


class TestRoomBundle:
    def test_reference_fixture_bundle(self) -> None:
        before = _tree(DUNGEON_FIXTURE)
        b = export_room_bundle(DUNGEON_FIXTURE, "room_0")
        assert _tree(DUNGEON_FIXTURE) == before  # pure projection
        assert P63A_KEYS <= set(b)
        maze = json.loads((DUNGEON_FIXTURE / "rooms" / "room_0" / "maze.json").read_text())
        assert (b["level_id"], b["stage_id"]) == ("room_0", "")
        assert b["display_name"] == maze["environment_name"] == "Scavenger's Hollow"
        assert (b["grid_width"], b["grid_height"]) == (40, 30)
        assert b["spawn"] == maze["player_start"] and b["exit"] == maze["door_position"]
        assert (b["tile_px"], b["actor_scale"], b["water_alpha"], b["variants"]) == (20, 1, 1, [])
        # the grid normalises to 1 iff wall; terrain mirrors it; background is zeros
        col = b["grids"]["collision"]
        assert len(col) == 30 and all(len(row) == 40 for row in col)
        assert all(cell in (0, 1) for row in col for cell in row)
        assert all((cell == 1) == (src == 1) for row, srow in zip(col, maze["grid"]) for cell, src in zip(row, srow))
        assert b["grids"]["terrain"] == col and b["grids"]["background"] == [[0] * 40 for _ in range(30)]
        # tileset synthesised from tiles.json (P.6.3), palette from data (P.9 G2)
        assert [s["name"] for s in b["tileset"]["slots"]] == ["empty", "wall"]
        assert [s["collision"] for s in b["tileset"]["slots"]] == ["empty", "solid"]
        assert all(s["px_region"] == [0, 0, 20, 20] for s in b["tileset"]["slots"])
        assert b["tileset"]["palette"] == {"background": "--bg-sunken", "wall": "#645f55"}  # ruins
        assert b["tileset"]["render_filter"] == "nearest" and b["tileset"]["tilesheet_path_abs"] is None
        assert set(b["tiles_by_type"]) == {"0", "1"} and b["tiles_by_type"]["1"]["name"] == "wall"
        # entities ← npc_positions, one per key, stable by id, names from npcs.json
        assert len(b["entities"]) == len(maze["npc_positions"])
        assert [e["enemy_id"] for e in b["entities"]] == sorted(maze["npc_positions"], key=int)
        first = b["entities"][0]
        assert first == {
            "enemy_id": "1000", "x": 8, "y": 29, "variant": None, "name": "Mira Dustcrawler",
            "archetype": "StaticNPC", "size": 1, "placeholder_color": "#b48250",
            "sprite_path_abs": first["sprite_path_abs"],
        }
        assert first["sprite_path_abs"].endswith("npc_1000.png")
        # items ← the grid's cells >= 2000 (engine truth), names joined from items.json
        grid_items = [(x, y, v) for y, row in enumerate(maze["grid"]) for x, v in enumerate(row) if v >= 2000]
        assert [(i["x"], i["y"], int(i["item_id"])) for i in b["items"]] == grid_items
        assert all(isinstance(i["item_id"], str) for i in b["items"])
        assert b["items"][0]["name"] == "starfall cleaver" and b["items"][0]["kind"] == "weapon"
        # triggers ← event_positions in the triggers shape (P.9 G3), typed by the event row
        assert len(b["triggers"]) == len(maze["event_positions"]) == 96
        t0 = b["triggers"][0]
        assert t0 == {"x": 8, "y": 9, "type": "combat",
                      "params": {"event_id": 3000, "is_gate": False, "is_climax_boss": False, "monster_ids": [5003]}}
        gate = [t for t in b["triggers"] if t["params"]["event_id"] == maze["gate_encounter_id"]]
        assert len(gate) == 1 and gate[0]["params"]["is_gate"] is True
        # the room passthrough (P.6.3 step 4); the legacy tree answers monsters from the bible mirror
        assert b["room"] == {
            "environment": "ruins", "environment_name": "Scavenger's Hollow", "door_revealed": False,
            "gate_encounter_id": 3023, "quest_ids": maze["quest_ids"], "monsters": b["room"]["monsters"],
        }
        assert len(b["room"]["monsters"]) == 5 and b["room"]["monsters"][0]["entity_type"] == "monster"
        assert b["warnings"] == []  # the fixture's grid and sidecars agree
        # platformer-only layers ride along neutral (P.6.2 row 15)
        assert (b["hazards"], b["foreground"], b["props"], b["backdrop"]) == ([], [], {}, None)
        assert (b["music_path"], b["music_sections"]) == ("", [])
        assert b["revision"].startswith("sha256:") and b["revision_short"] == b["revision"][7:17]
        assert b["last_change"] is None  # no journal on the legacy tree
        json.dumps(b)  # JSON-serializable, whole

    def test_key_set_is_the_platformer_bundles_plus_room_and_warnings(self, plat_pack: Path) -> None:
        plat = export_level_bundle(plat_pack, "l1")
        room = export_room_bundle(DUNGEON_FIXTURE, "room_0")
        assert set(room) == set(plat) | {"room", "warnings"}
        # and the per-placement records share the platformer's literal keys (P.9 G9)
        assert set(room["entities"][0]) == set(plat["entities"][0])
        assert set(room["items"][0]) == set(plat["items"][0])
        assert set(room["tileset"]["slots"][0]) == set(plat["tileset"]["slots"][0])

    def test_revision_covers_maze_bytes_and_the_index_row(self, tmp_path: Path) -> None:
        pack = synthetic_pack(tmp_path / "p")
        b0 = export_room_bundle(pack, "room_0")
        maze_bytes = (pack / "rooms" / "room_0" / "maze.json").read_bytes()
        index = json.loads((pack / "rooms" / "rooms.json").read_text())["room_0"]
        h = hashlib.sha256(maze_bytes + json.dumps(index, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        assert b0["revision"] == f"sha256:{h}" and b0["revision_short"] == h[:10]
        # the index row joins the identity (P.9 R1): a story edit is a new revision, the maze untouched
        index["story_beat"] = "changed"
        _write(pack / "rooms" / "rooms.json", {"room_0": index})
        assert export_room_bundle(pack, "room_0")["revision"] != b0["revision"]
        # …and key ORDER on disk is not identity (canonical form)
        _write(pack / "rooms" / "rooms.json", {"room_0": dict(reversed(list(index.items())))})
        b2 = export_room_bundle(pack, "room_0")
        _write(pack / "rooms" / "rooms.json", {"room_0": index})
        assert export_room_bundle(pack, "room_0")["revision"] == b2["revision"]

    def test_synthetic_room(self, tmp_path: Path) -> None:
        pack = synthetic_pack(tmp_path / "p")
        before = _tree(pack)
        b = export_room_bundle(pack, "room_0")
        assert _tree(pack) == before
        assert (b["grid_width"], b["grid_height"]) == (6, 5)
        assert b["grids"]["collision"] == [[1 if v == 1 else 0 for v in row] for row in _GRID]
        assert b["spawn"] == [1, 1] and b["exit"] == [4, 3]
        assert b["display_name"] == "The Whispering Wood"
        assert b["tileset"]["palette"]["wall"] == "#225022"  # forest, from WALL_COLORS data
        assert b["entities"] == [{
            "enemy_id": "1000", "x": 1, "y": 3, "variant": None, "name": "Mira", "archetype": "RandomNPC",
            "size": 1, "placeholder_color": "#3c823c",  # ENV_TO_COLOR["forest"]
            "sprite_path_abs": str((pack / "portraits" / "npcs" / "npc_1000.png").resolve()),
        }]
        assert b["items"] == [{
            "item_id": "2000", "x": 3, "y": 2, "source": None, "name": "ration cube", "kind": "food",
            "placeholder_color": "#ffd700", "sprite_path_abs": None,
        }]
        assert b["triggers"] == [{
            "x": 2, "y": 1, "type": "combat",
            "params": {"event_id": 3000, "is_gate": True, "is_climax_boss": False, "monster_ids": [5000]},
        }]
        assert b["room"] == {
            "environment": "forest", "environment_name": "The Whispering Wood", "door_revealed": False,
            "gate_encounter_id": 3000, "quest_ids": [4000],
            "monsters": [{"entity_type": "monster", "entity_id": "5000", "name": "Wolf", "room_id": "room_0",
                          "lore": "", "tags": []}],
        }
        assert b["warnings"] == []

    def test_legacy_tree_without_an_index_answers_from_the_bible_mirror(self, tmp_path: Path) -> None:
        pack = synthetic_pack(tmp_path / "p", with_index=False)
        b = export_room_bundle(pack, "room_0")
        assert b["room"]["monsters"][0]["name"] == "Wolf"
        maze_bytes = (pack / "rooms" / "room_0" / "maze.json").read_bytes()
        assert b["revision"] == f"sha256:{hashlib.sha256(maze_bytes).hexdigest()}"

    def test_disagreement_is_a_named_warning_never_a_block(self, tmp_path: Path) -> None:
        pack = synthetic_pack(tmp_path / "p", mismatch=True)
        b = export_room_bundle(pack, "room_0")
        # the engine's truth renders: the item where the GRID says, the event where the LIST says
        assert [(i["x"], i["y"]) for i in b["items"]] == [(3, 2)]
        assert [(t["x"], t["y"]) for t in b["triggers"]] == [(2, 3)]
        text = "\n".join(b["warnings"])
        assert "item_placements: item 2000 at (4, 2) but the grid cell reads 0" in text
        assert "grid: cell (3, 2) reads item 2000 with no item_placements entry" in text
        assert "event_positions: event 3000 at (2, 3) but the grid cell reads 0 (expected -1)" in text
        assert "grid: cell (2, 1) reads -1 (event stamp) with no event_positions entry" in text
        assert len(b["warnings"]) == 4

    def test_placement_naming_a_missing_row_and_an_unplaced_gate_warn(self, tmp_path: Path) -> None:
        pack = synthetic_pack(tmp_path / "p")
        maze_path = pack / "rooms" / "room_0" / "maze.json"
        maze = json.loads(maze_path.read_text())
        maze["npc_positions"]["1099"] = [4, 1]
        maze["gate_encounter_id"] = 3999
        maze["grid"][3][3] = 77  # neither a tile nor a stamp
        _write(maze_path, maze)
        b = export_room_bundle(pack, "room_0")
        assert [e["enemy_id"] for e in b["entities"]] == ["1000", "1099"]
        assert b["entities"][1]["name"] == "1099" and b["entities"][1]["archetype"] is None
        assert "npc_positions: npc 1099 has no row in npcs/npcs.json" in b["warnings"]
        assert "gate_encounter_id 3999 is not placed in event_positions" in b["warnings"]
        assert "grid: cell (3, 3) reads 77 — not a tile type or a placement stamp" in b["warnings"]
        assert b["grids"]["collision"][3][3] == 0

    @pytest.mark.parametrize(
        "mutate, expected, total",
        [
            pytest.param(
                lambda m: m.update(width=7),
                "maze.json: width/height say 7×5 but the grid is 6×5 — rendering the grid's own size",
                1,
                id="dims-disagree",
            ),
            pytest.param(
                lambda m: m["npc_positions"].update({"1000": [9, 9]}),
                "npc_positions: npc 1000 at (9, 9) is outside the 6×5 grid",
                1,
                id="out-of-bounds",
            ),
            pytest.param(
                lambda m: m.update(event_positions=[{"x": 2}]),
                "event_positions: entry {'x': 2} lacks event_id/x/y",
                # …plus the grid's -1 stamp at (2, 1) now has no list entry, and the gate is unplaced
                3,
                id="malformed-list-entry",
            ),
            pytest.param(
                lambda m: m["npc_positions"].update({"1000": ["a", 3]}),
                "npc_positions: 1000 has a non-integer position ['a', 3]",
                1,
                id="non-integer-dict-entry",
            ),
            pytest.param(
                lambda m: m.update(event_positions=[{"x": "2", "y": None, "event_id": 3000}]),
                "event_positions: 3000 has a non-integer position ['2', None]",
                3,
                id="non-integer-list-entry",
            ),
            pytest.param(
                # twelve border cells → ten named, the rest counted in one cap line
                lambda m: [m["grid"][y].__setitem__(x, 77) for y in (0, 4) for x in range(6)],
                "grid: … and 2 more cells with unknown values",
                11,
                id="unknown-cell-cap",
            ),
        ],
    )
    def test_every_named_warning_renders_rather_than_blocks(
        self, tmp_path: Path, mutate, expected: str, total: int
    ) -> None:
        """P.6.3 warn-never-block: each malformed shape ``_positions`` /
        the dims pass / the unknown-cell cap can name is a warning on a
        bundle that still renders at the grid's own size."""
        pack = synthetic_pack(tmp_path / "p")
        maze_path = pack / "rooms" / "room_0" / "maze.json"
        maze = json.loads(maze_path.read_text())
        mutate(maze)
        _write(maze_path, maze)
        before = _tree(pack)
        b = export_room_bundle(pack, "room_0")
        assert expected in b["warnings"], b["warnings"]
        assert len(b["warnings"]) == total, b["warnings"]
        assert (b["grid_width"], b["grid_height"]) == (6, 5)
        assert len(b["grids"]["collision"]) == 5 and all(len(r) == 6 for r in b["grids"]["collision"])
        assert P63A_KEYS <= set(b)
        assert _tree(pack) == before

    def test_unknown_room_is_a_file_not_found(self, tmp_path: Path) -> None:
        pack = synthetic_pack(tmp_path / "p")
        with pytest.raises(FileNotFoundError, match="room_7"):
            export_room_bundle(pack, "room_7")
        with pytest.raises(FileNotFoundError):
            export_room_bundle(pack, "../room_0")

    def test_last_change_reads_the_room_artifact_family(self, tmp_path: Path) -> None:
        pack = synthetic_pack(tmp_path / "p")
        events = [
            {"ts": "2026-09-01T10:00:00+00:00", "op": "edit", "source": "user", "actor": "cradle:user",
             "artifact_id": "room:room_0/grid", "after_hash": "sha256:a", "detail": {"kind": "terrain_paint"}},
            {"ts": "2026-09-01T10:01:00+00:00", "op": "edit", "source": "user", "actor": "cradle:user",
             "artifact_id": "room:room_1/placements", "after_hash": "sha256:b", "detail": {"kind": "npc_move"}},
            {"ts": "2026-09-01T10:02:00+00:00", "op": "edit", "source": "user", "actor": "cradle:user",
             "artifact_id": "room:room_0/placements", "after_hash": "sha256:c", "detail": {"kind": "event_move"}},
        ]
        (pack / ".canon").mkdir()
        (pack / ".canon" / "journal.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
        last = export_room_bundle(pack, "room_0")["last_change"]
        assert last["label"] == "Moved an event" and last["hash"] == "sha256:c"
        assert last["kind"] == "event_move" and last["actor"] == "cradle:user"


# ---------------------------------------------------------------------------
# The CLI: `canon grid export` + its alias `canon level export`
# ---------------------------------------------------------------------------


class TestGridExportVerb:
    def test_grid_and_level_export_agree_on_a_dungeon_pack(self) -> None:
        before = _tree(DUNGEON_FIXTURE)
        code_g, grid_doc = _canon("grid", "export", str(DUNGEON_FIXTURE), "--level", "room_0")
        code_l, level_doc = _canon("level", "export", str(DUNGEON_FIXTURE), "--level", "room_0")
        assert code_g == 0 and code_l == 0, (grid_doc, level_doc)
        assert grid_doc == level_doc
        assert grid_doc["canon_version"] == "0.1"
        assert grid_doc["level"] == export_room_bundle(DUNGEON_FIXTURE, "room_0")
        assert _tree(DUNGEON_FIXTURE) == before

    def test_grid_and_level_export_agree_on_a_platformer_pack(self, plat_pack: Path) -> None:
        before = _tree(plat_pack)
        code_g, grid_doc = _canon("grid", "export", str(plat_pack), "--level", "l1")
        code_l, level_doc = _canon("level", "export", str(plat_pack), "--level", "l1")
        assert code_g == 0 and code_l == 0, (grid_doc, level_doc)
        assert grid_doc == level_doc
        assert grid_doc["level"]["level_id"] == "l1" and grid_doc["level"]["stage_id"] != ""
        assert "room" not in grid_doc["level"]  # the platformer reader is untouched
        assert _tree(plat_pack) == before

    def test_unknown_room_id_is_a_structured_error(self) -> None:
        for verb in ("grid", "level"):
            code, doc = _canon(verb, "export", str(DUNGEON_FIXTURE), "--level", "room_9")
            assert code == 1
            assert doc["error"].startswith("room 'room_9' not found")
            assert doc["level"] == "room_9" and doc["pack_dir"] == str(DUNGEON_FIXTURE)

    def test_non_pack_directory_is_a_structured_error(self, tmp_path: Path) -> None:
        code, doc = _canon("grid", "export", str(tmp_path), "--level", "room_0")
        assert code == 1 and "unknown pack type" in doc["error"]
        code, doc = _canon("grid", "export", str(tmp_path / "missing"), "--level", "room_0")
        assert code == 1 and doc["error"].startswith("Pack directory not found")
