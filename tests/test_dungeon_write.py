"""Row P0-8 — the dungeon room WRITE path: ``apply_room_edit`` /
``import_room_grids`` behind ``canon grid apply-edit`` / ``import-grids``,
the per-step 🎲 rolls, and History's restore half.

Contract under test: P0 paper P.6.3's write table (which sparse key lands on
which ``maze.json`` key, and the fail-closed rules), P.6.4's P0-8 column,
P.9 G4 (monsters through an ENCOUNTER), G5 (door snap-to-gate), G7 (a wall
over a placement is refused), G8 (per-kind roll sub-seeds), M9 (dims are
read-only) and R1 (``room:<map_id>/<step>`` artifact ids).

Two invariants run through every case: a REFUSAL writes nothing (the pack's
file hashes are pinned before and after), and the platformer's own write
verbs are untouched (an A/B on a generated pack).
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from canon import provenance
from canon.adapters.dungeon_read import describe_room, export_room_bundle
from canon.adapters.dungeon_write import (
    apply_room_edit,
    import_room_grids,
    restore_room_step,
)
from canon.packs.dungeon.rolls import ROLL_STEPS, roll_room

REPO = Path(__file__).resolve().parents[1]
CANON = [sys.executable, "-m", "canon.cli.main"]

# 10 wide × 8 high. Start (1,1); door (8,6); the gate encounter 3000 sits at
# (8,5) — 4-adjacent to the door, the invariant P.9 G5 preserves.
_GRID = [
    [1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
    [1, 0, 0, -1, 0, 0, 0, 0, 0, 1],
    [1, 0, 1, 0, 0, 2000, 0, 1, 0, 1],
    [1, 0, 0, 0, 0, 0, 0, 0, 0, 1],
    [1, 0, 1, 0, 0, 0, 0, 1, 0, 1],
    [1, 0, 0, 0, 0, 0, 0, 0, -1, 1],
    [1, 0, 0, 0, 0, 0, 0, 0, 0, 1],
    [1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
]


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _tree(root: Path) -> dict[str, str]:
    """Every file under *root* → sha256 — the "a refusal writes nothing" pin."""
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def make_pack(root: Path) -> Path:
    """A minimal dungeon tree resolving by manifest stamp (P.4.1 tier 2),
    with enough open cells for the rolls to have somewhere to go."""
    maze = {
        "layout_type": "maze",
        "extra": {},
        "grid": copy.deepcopy(_GRID),
        "width": 10,
        "height": 8,
        "door_position": [8, 6],
        "door_revealed": False,
        "gate_encounter_id": 3000,
        "player_start": [1, 1],
        "npc_positions": {"1000": [1, 3]},
        "item_placements": [
            {"x": 5, "y": 2, "item_id": 2000, "name": "ration cube",
             "portrait_prompt": "", "profile_image": ""}
        ],
        "event_positions": [{"x": 8, "y": 5, "event_id": 3000}, {"x": 3, "y": 1, "event_id": 3001}],
        "quest_ids": [4000],
        "environment": "forest",
        "environment_name": "The Whispering Wood",
    }
    _write(root / "rooms" / "room_0" / "maze.json", maze)
    _write(root / "manifest.json", {"pack_type": "dungeon", "seed": 7, "num_rooms": 1})
    _write(
        root / "npcs" / "npcs.json",
        [
            {"id": 1000, "type": "RandomNPC", "name": "Mira", "environment": "forest",
             "profile_image": "", "x": 9, "y": 9},
            {"id": 1001, "type": "StaticNPC", "name": "Bram", "environment": "forest",
             "profile_image": "", "x": 9, "y": 9},
        ],
    )
    _write(
        root / "items" / "items.json",
        {
            "2000": {"category": "food", "name": "ration cube", "item_stats": {"uses": 1},
                     "portrait_prompt": "a cube", "profile_image": ""},
            "2001": {"category": "tool", "name": "pry bar", "item_stats": {"uses": 3},
                     "portrait_prompt": "a bar", "profile_image": ""},
        },
    )
    _write(
        root / "events" / "events.json",
        [
            {"id": 3000, "type": "combat", "name": "Ambush", "is_gate": True,
             "is_climax_boss": False, "monster_ids": [5000], "monster_count": 2, "x": 1, "y": 1},
            {"id": 3001, "type": "combat", "name": "Skirmish", "is_gate": False,
             "is_climax_boss": False, "monster_ids": [5001], "monster_count": 1, "x": 1, "y": 1},
        ],
    )
    _write(
        root / "monsters" / "monsters.json",
        {
            "5000": {"id": 5000, "name": "Wolf", "is_boss": True},
            "5001": {"id": 5001, "name": "Rat", "is_boss": False},
            "5002": {"id": 5002, "name": "Bat", "is_boss": False},
        },
    )
    _write(root / "quests" / "quests.json", [{"id": 4000, "type": "fetch", "title": "Find bread"}])
    _write(
        root / "classes" / "classes.json",
        [{"archetype": "warrior", "name": "Warrior", "stats": {"STR": 16}}],
    )

    def stub(kind: str, eid: str, name: str) -> dict:
        return {"entity_type": kind, "entity_id": eid, "name": name,
                "room_id": "room_0", "lore": "", "tags": []}

    room = {
        "environment": "forest", "environment_name": "The Whispering Wood", "level": 1,
        "story_beat": "", "boss_name": "", "boss_lore": "", "maze_ref": "",
        "npcs": [stub("npc", "1000", "Mira"), stub("npc", "1001", "Bram")],
        "items": [stub("item", "2000", "ration cube"), stub("item", "2001", "pry bar")],
        "monsters": [stub("monster", "5000", "Wolf"), stub("monster", "5001", "Rat"),
                     stub("monster", "5002", "Bat")],
        "encounters": ["3000", "3001"],
        "quests": ["4000"],
    }
    _write(root / "world_bible.json",
           {"story": {}, "rooms": {"room_0": room}, "player_classes": [], "entity_index": {}})
    _write(root / "rooms" / "rooms.json",
           {"room_0": {"id": "room_0", **room, "maze_ref": "rooms/room_0/maze.json"}})
    return root


@pytest.fixture
def pack(tmp_path: Path) -> Path:
    return make_pack(tmp_path / "dungeon")


def _maze(pack: Path) -> dict:
    return json.loads((pack / "rooms" / "room_0" / "maze.json").read_text(encoding="utf-8"))


def _events(pack: Path) -> dict[str, dict]:
    rows = json.loads((pack / "events" / "events.json").read_text(encoding="utf-8"))
    return {str(r["id"]): r for r in rows}


def _canon(*args: str) -> tuple[int, object]:
    result = subprocess.run(CANON + list(args), capture_output=True, text=True, cwd=REPO)
    stream = result.stdout if result.returncode == 0 else result.stderr
    try:
        return result.returncode, json.loads(stream)
    except json.JSONDecodeError:
        return result.returncode, stream


def _refuses(pack: Path, call, match: str) -> str:
    """A refusal must leave the tree byte-identical (doctrine 1: fail-closed
    BEFORE the write) — the pin every negative case below shares."""
    before = _tree(pack)
    with pytest.raises(ValueError, match=match) as excinfo:
        call()
    assert _tree(pack) == before, "a refused write touched the pack"
    return str(excinfo.value)


# ---------------------------------------------------------------------------
# P.6.3's write table — every sparse key round-trips
# ---------------------------------------------------------------------------


class TestSparseRoundTrip:
    def test_entities_land_on_npc_positions(self, pack: Path) -> None:
        rows_before = (pack / "npcs" / "npcs.json").read_bytes()
        result = apply_room_edit(
            pack, "room_0", {"entities": [{"enemy_id": "1000", "x": 4, "y": 3}]}, actor="user"
        )
        assert result["updated"] == ["entities"]
        assert _maze(pack)["npc_positions"] == {"1000": [4, 3]}
        # NPCs carry no grid encoding (P.3.2 grid_stamp null) …
        assert _maze(pack)["grid"][3][4] == 0
        # … and the row file is not the position's home (P.1.1: routed to grid).
        assert (pack / "npcs" / "npcs.json").read_bytes() == rows_before
        bundle = export_room_bundle(pack, "room_0")
        assert [(e["enemy_id"], e["x"], e["y"]) for e in bundle["entities"]] == [("1000", 4, 3)]
        assert bundle["warnings"] == []

    def test_items_rewrite_the_sidecar_and_stamp_the_grid(self, pack: Path) -> None:
        apply_room_edit(
            pack, "room_0",
            {"items": [{"item_id": "2000", "x": 6, "y": 3}, {"item_id": "2001", "x": 4, "y": 1}]},
            actor="user",
        )
        maze = _maze(pack)
        assert maze["grid"][2][5] == 0, "the old item cell is cleared"
        assert maze["grid"][3][6] == 2000 and maze["grid"][1][4] == 2001
        placements = {p["item_id"]: p for p in maze["item_placements"]}
        assert placements[2000]["x"] == 6 and placements[2000]["name"] == "ration cube"
        # A brand-new placement takes the sidecar shape the file already uses,
        # filled from the row (never from the wire).
        assert set(placements[2001]) == set(placements[2000])
        assert placements[2001]["name"] == "pry bar"
        bundle = export_room_bundle(pack, "room_0")
        assert bundle["warnings"] == []
        assert {(i["item_id"], i["x"], i["y"]) for i in bundle["items"]} == {
            ("2000", 6, 3), ("2001", 4, 1)
        }

    def test_triggers_rewrite_event_positions_and_stamp_minus_one(self, pack: Path) -> None:
        apply_room_edit(
            pack, "room_0",
            {"triggers": [
                {"x": 8, "y": 5, "type": "combat", "params": {"event_id": 3000}},
                {"x": 6, "y": 1, "type": "combat", "params": {"event_id": 3001}},
            ]},
            actor="user",
        )
        maze = _maze(pack)
        assert maze["grid"][1][3] == 0, "the old event cell is cleared"
        assert maze["grid"][1][6] == -1 and maze["grid"][5][8] == -1
        assert {(e["event_id"], e["x"], e["y"]) for e in maze["event_positions"]} == {
            (3000, 8, 5), (3001, 6, 1)
        }
        # The wire's chrome (`type`, `params`) never reaches maze.json.
        assert all(set(e) == {"x", "y", "event_id"} for e in maze["event_positions"])

    def test_spawn_and_exit_land_on_the_point_fields(self, pack: Path) -> None:
        result = apply_room_edit(pack, "room_0", {"spawn": [2, 6]}, actor="user")
        assert result["changed"]["spawn"] == {"from": [1, 1], "to": [2, 6]}
        assert _maze(pack)["player_start"] == [2, 6]
        # The door only moves next to the gate encounter (8,5) — (7,5) is.
        apply_room_edit(pack, "room_0", {"exit": [7, 5]}, actor="user")
        assert _maze(pack)["door_position"] == [7, 5]

    def test_a_no_op_edit_writes_and_journals_nothing(self, pack: Path) -> None:
        before, events = _tree(pack), len(provenance.all_events(pack))
        result = apply_room_edit(
            pack, "room_0", {"entities": [{"enemy_id": "1000", "x": 1, "y": 3}]}, actor="user"
        )
        assert result["no_change"] is True
        assert _tree(pack) == before
        assert len(provenance.all_events(pack)) == events


# ---------------------------------------------------------------------------
# The journal: one event per written file, R1's artifact ids
# ---------------------------------------------------------------------------


class TestJournal:
    def test_one_event_per_file_with_the_wire_s_own_kind(self, pack: Path) -> None:
        apply_room_edit(
            pack, "room_0", {"items": [{"item_id": "2000", "x": 6, "y": 3}]}, actor="alice"
        )
        events = provenance.all_events(pack)
        assert len(events) == 1
        event = events[0]
        assert event["artifact_id"] == "room:room_0/placements"
        assert event["op"] == "edit" and event["actor"] == "alice"
        assert event["detail"]["kind"] == "item_move"
        assert event["before_hash"] and event["after_hash"] != event["before_hash"]

    def test_several_wires_in_one_save_stay_one_event(self, pack: Path) -> None:
        apply_room_edit(
            pack, "room_0",
            {"entities": [{"enemy_id": "1000", "x": 4, "y": 3}],
             "items": [{"item_id": "2000", "x": 6, "y": 3}]},
            actor="user",
        )
        events = provenance.all_events(pack)
        assert len(events) == 1
        assert events[0]["detail"]["kind"] == "room_edit"
        assert set(events[0]["detail"]["kinds"]) == {"npc_move", "item_move"}

    def test_import_grids_journals_the_grid_step(self, pack: Path) -> None:
        rows = [[1 if v == 1 else 0 for v in row] for row in _maze(pack)["grid"]]
        rows[6][5] = 1
        import_room_grids(pack, "room_0", rows, actor="user")
        event = provenance.all_events(pack)[-1]
        assert event["artifact_id"] == "room:room_0/grid"
        assert event["detail"]["kind"] == "terrain_paint"


# ---------------------------------------------------------------------------
# P.9 G4 — monsters through an ENCOUNTER (the cross-file write)
# ---------------------------------------------------------------------------


class TestEncounters:
    def test_targeting_an_existing_encounter_writes_two_files_in_one_batch(self, pack: Path) -> None:
        result = apply_room_edit(
            pack, "room_0",
            {"encounters": [{"x": 4, "y": 4, "event_id": 3001, "monster_ids": [5001, 5002]}]},
            actor="user",
        )
        events = provenance.all_events(pack)
        assert len(events) == 2, "one journal event per written file"
        batches = {e.get("batchId") for e in events}
        assert len(batches) == 1 and None not in batches
        by_artifact = {e["artifact_id"]: e for e in events}
        assert set(by_artifact) == {"event:3001", "room:room_0/placements"}
        assert by_artifact["event:3001"]["detail"]["mirror_of"] == "room:room_0/placements"
        assert _events(pack)["3001"]["monster_ids"] == [5001, 5002]
        assert result["batch"] == batches.pop()
        # The placement moved with it.
        assert {(e["event_id"], e["x"], e["y"]) for e in _maze(pack)["event_positions"]} >= {
            (3001, 4, 4)
        }

    def test_a_new_encounter_allocates_an_event_row_and_places_it(self, pack: Path) -> None:
        apply_room_edit(
            pack, "room_0",
            {"encounters": [{"x": 4, "y": 4, "monster_ids": [5002]}]},
            actor="user",
        )
        rows = _events(pack)
        assert "3002" in rows, "the id came from the event kind's id_alloc (base 3000)"
        new = rows["3002"]
        assert new["type"] == "combat" and new["monster_ids"] == [5002]
        # Gate flags stay code-owned — the encounter surface never sets them.
        assert not new.get("is_gate")
        maze = _maze(pack)
        assert maze["grid"][4][4] == -1
        assert {"x": 4, "y": 4, "event_id": 3002} in maze["event_positions"]
        artifacts = {e["artifact_id"] for e in provenance.all_events(pack)}
        assert artifacts == {"event:3002", "room:room_0/placements"}

    def test_an_unknown_monster_is_refused_and_writes_nothing(self, pack: Path) -> None:
        _refuses(
            pack,
            lambda: apply_room_edit(
                pack, "room_0",
                {"encounters": [{"x": 4, "y": 4, "event_id": 3001, "monster_ids": [9999]}]},
                actor="user",
            ),
            "monster 9999 has no row",
        )
        assert provenance.all_events(pack) == []


# ---------------------------------------------------------------------------
# Fail-closed — every refusal writes NOTHING
# ---------------------------------------------------------------------------


class TestFailClosed:
    def test_unknown_row_id(self, pack: Path) -> None:
        _refuses(
            pack,
            lambda: apply_room_edit(pack, "room_0", {"entities": [{"enemy_id": "1234", "x": 4, "y": 3}]}),
            "npc 1234 has no row",
        )

    def test_out_of_bounds_cell(self, pack: Path) -> None:
        _refuses(
            pack,
            lambda: apply_room_edit(pack, "room_0", {"items": [{"item_id": "2000", "x": 40, "y": 3}]}),
            "outside the 10×8 grid",
        )

    def test_placement_on_a_wall(self, pack: Path) -> None:
        _refuses(
            pack,
            lambda: apply_room_edit(pack, "room_0", {"items": [{"item_id": "2000", "x": 0, "y": 0}]}),
            "is a wall",
        )

    def test_placement_on_the_start_or_the_door(self, pack: Path) -> None:
        _refuses(
            pack,
            lambda: apply_room_edit(pack, "room_0", {"items": [{"item_id": "2000", "x": 1, "y": 1}]}),
            "player_start cell",
        )
        _refuses(
            pack,
            lambda: apply_room_edit(pack, "room_0", {"items": [{"item_id": "2000", "x": 8, "y": 6}]}),
            "door_position cell",
        )

    def test_two_placements_on_one_cell(self, pack: Path) -> None:
        _refuses(
            pack,
            lambda: apply_room_edit(
                pack, "room_0",
                {"items": [{"item_id": "2000", "x": 4, "y": 3}, {"item_id": "2001", "x": 4, "y": 3}]},
            ),
            "share",
        )

    def test_a_free_door_drag_is_refused_with_the_reason(self, pack: Path) -> None:
        message = _refuses(
            pack,
            lambda: apply_room_edit(pack, "room_0", {"exit": [3, 3]}),
            "must stay next to the gate encounter",
        )
        assert "pass the boss" in message

    def test_a_wall_painted_over_a_placement_is_refused(self, pack: Path) -> None:
        rows = [[1 if v == 1 else 0 for v in row] for row in _maze(pack)["grid"]]
        rows[2][5] = 1  # the item's cell
        message = _refuses(
            pack, lambda: import_room_grids(pack, "room_0", rows), "holds item 2000"
        )
        assert "move it before painting a wall" in message

    def test_a_wall_over_the_start_is_refused(self, pack: Path) -> None:
        rows = [[1 if v == 1 else 0 for v in row] for row in _maze(pack)["grid"]]
        rows[1][1] = 1
        _refuses(pack, lambda: import_room_grids(pack, "room_0", rows), "holds player_start")

    def test_a_non_tile_cell_value_is_refused(self, pack: Path) -> None:
        rows = [[1 if v == 1 else 0 for v in row] for row in _maze(pack)["grid"]]
        rows[3][3] = 2000
        _refuses(pack, lambda: import_room_grids(pack, "room_0", rows), "is 2000")

    def test_resize_is_refused_with_the_reason(self, pack: Path) -> None:
        rows = [[0] * 12 for _ in range(8)]
        message = _refuses(pack, lambda: import_room_grids(pack, "room_0", rows), "cannot be resized")
        assert "10×8" in message

    def test_an_unknown_sparse_key_is_refused(self, pack: Path) -> None:
        _refuses(
            pack,
            lambda: apply_room_edit(pack, "room_0", {"hazards": [{"x": 1, "y": 1, "type": "spike"}]}),
            "no recognized layers",
        )


# ---------------------------------------------------------------------------
# Doctrine 10 — the reachability warning never blocks
# ---------------------------------------------------------------------------


def test_an_unreachable_door_warns_and_still_writes(pack: Path) -> None:
    rows = [[1 if v == 1 else 0 for v in row] for row in _maze(pack)["grid"]]
    for y in range(1, 7):  # wall off the right-hand corridor the door sits in
        rows[y][7] = 1
    rows[5][8] = 0  # keep the gate's own cell open
    result = import_room_grids(pack, "room_0", rows, actor="user")
    assert result["updated"] == ["grid"]
    assert any("not reachable" in w for w in result["warnings"])
    assert _maze(pack)["grid"][3][7] == 1, "the paint landed despite the warning"


# ---------------------------------------------------------------------------
# Per-step rolls (P.6.3) — deterministic, journaled, $0
# ---------------------------------------------------------------------------


class TestRolls:
    @pytest.mark.parametrize("step", ["whole", "layout", "npcs", "events", "items"])
    def test_each_step_is_deterministic_under_a_pinned_seed(
        self, tmp_path: Path, step: str
    ) -> None:
        a, b = make_pack(tmp_path / "a"), make_pack(tmp_path / "b")
        ra = roll_room(a, "room_0", step, seed="pin-1", actor="user")
        rb = roll_room(b, "room_0", step, seed="pin-1", actor="user")
        assert ra["seed"] == rb["seed"] == "pin-1"
        assert _maze(a) == _maze(b)
        assert _events(a) == _events(b)

    def test_a_different_seed_gives_a_different_maze(self, tmp_path: Path) -> None:
        a, b = make_pack(tmp_path / "a"), make_pack(tmp_path / "b")
        roll_room(a, "room_0", "layout", seed="pin-1")
        roll_room(b, "room_0", "layout", seed="pin-2")
        assert _maze(a)["grid"] != _maze(b)["grid"]

    def test_every_roll_journals_and_costs_nothing(self, pack: Path) -> None:
        result = roll_room(pack, "room_0", "items", seed="pin-1", actor="user")
        assert result["cost_usd"] == 0.0
        assert result["changed"] and result["changed_artifacts"] == ["room:room_0/placements"]
        event = provenance.all_events(pack)[-1]
        assert event["detail"]["kind"] == "item_roll" and event["op"] == "edit"
        assert "gen" not in event, "a code-only roll records no generation block"

    def test_a_single_kind_roll_leaves_the_other_kinds_alone(self, pack: Path) -> None:
        before = _maze(pack)
        roll_room(pack, "room_0", "npcs", seed="pin-1")
        after = _maze(pack)
        assert after["item_placements"] == before["item_placements"]
        assert after["event_positions"] == before["event_positions"]
        assert after["npc_positions"] != before["npc_positions"]
        # It never lands on another kind's cell, the start or the door.
        taken = {(5, 2), (8, 5), (3, 1), (1, 1), (8, 6)}
        assert not {tuple(xy) for xy in after["npc_positions"].values()} & taken

    def test_the_layout_roll_recarves_and_restamps(self, pack: Path) -> None:
        result = roll_room(pack, "room_0", "layout", seed="pin-1", actor="user")
        maze = _maze(pack)
        assert maze["grid"] != _GRID
        assert maze["width"] == 10 and maze["height"] == 8
        # Placements that survived the carve keep their stamp; the walled-in
        # ones are NAMED, never deleted (doctrine 6 / 10).
        for entry in maze["item_placements"]:
            cell = maze["grid"][entry["y"]][entry["x"]]
            assert cell in (entry["item_id"], 1)
        assert len(maze["item_placements"]) == 1
        walled = [w for w in result["warnings"] if "inside a wall" in w]
        for warning in walled:
            assert "re-roll that kind or drag it" in warning

    def test_the_whole_room_roll_writes_two_artifacts_in_one_batch(self, pack: Path) -> None:
        result = roll_room(pack, "room_0", "whole", seed="pin-1", actor="user")
        artifacts = set(result["changed_artifacts"])
        assert "room:room_0/grid" in artifacts
        maze = _maze(pack)
        assert maze["gate_encounter_id"] in (3000, 3001)
        gate = _events(pack)[str(maze["gate_encounter_id"])]
        assert gate["is_gate"] is True
        # The door ends up adjacent to the gate tile — the invariant the
        # placement phase enforces and the door drag preserves.
        cell = next(e for e in maze["event_positions"] if e["event_id"] == maze["gate_encounter_id"])
        door = maze["door_position"]
        assert abs(cell["x"] - door[0]) + abs(cell["y"] - door[1]) == 1
        if len(artifacts) > 1:
            batches = {e.get("batchId") for e in provenance.all_events(pack)}
            assert len(batches) == 1 and None not in batches

    def test_monsters_rolls_one_encounter_and_keeps_the_boss_first(self, pack: Path) -> None:
        result = roll_room(pack, "room_0", "monsters", encounter_id=3000, seed="pin-1", actor="user")
        rows = _events(pack)
        assert rows["3000"]["monster_ids"] == result["monster_ids"]
        assert rows["3000"]["monster_ids"][0] == 5000, "the gate keeps its boss first"
        assert rows["3001"]["monster_ids"] == [5001], "the other encounter is untouched"
        assert provenance.all_events(pack)[-1]["detail"]["kind"] == "monsters_roll"

    def test_monsters_without_an_encounter_is_refused_with_the_reason(self, pack: Path) -> None:
        message = _refuses(
            pack, lambda: roll_room(pack, "room_0", "monsters", seed="pin-1"), "select an encounter"
        )
        assert "P.9 G4" in message or "combat event" in message

    def test_an_unknown_step_is_refused(self, pack: Path) -> None:
        _refuses(pack, lambda: roll_room(pack, "room_0", "tilesets"), "unknown roll step")

    def test_the_step_vocabulary_is_open_data(self) -> None:
        assert set(ROLL_STEPS) == {"whole", "layout", "npcs", "events", "items", "monsters"}


# ---------------------------------------------------------------------------
# History / restore (P.6.4) — a new version, nothing deleted
# ---------------------------------------------------------------------------


class TestRestore:
    def test_restoring_the_grid_step_reverts_the_maze_and_writes_a_new_version(
        self, pack: Path
    ) -> None:
        original = copy.deepcopy(_maze(pack))
        rows = [[1 if v == 1 else 0 for v in row] for row in original["grid"]]
        rows[6][5] = 1
        import_room_grids(pack, "room_0", rows, actor="user")
        paint = provenance.all_events(pack)[-1]
        assert _maze(pack)["grid"][6][5] == 1

        result = restore_room_step(pack, "room_0", "grid", paint["before_hash"], actor="user")
        assert result["restored_step"] == "grid"
        assert _maze(pack) == original, "the maze reverted"
        events = provenance.all_events(pack)
        assert len(events) == 2, "restore is a NEW version, not a rewind of the journal"
        assert events[-1]["op"] == "restore"
        assert events[-1]["artifact_id"] == "room:room_0/grid"
        # Nothing is deleted: the painted version is still readable from the CAS.
        assert json.loads(provenance.read_object(pack, paint["after_hash"]))["grid"][6][5] == 1

    def test_a_hash_from_another_artifact_is_refused(self, pack: Path) -> None:
        apply_room_edit(
            pack, "room_0",
            {"encounters": [{"x": 4, "y": 4, "event_id": 3001, "monster_ids": [5002]}]},
            actor="user",
        )
        row_event = next(
            e for e in provenance.all_events(pack) if e["artifact_id"] == "event:3001"
        )
        _refuses(
            pack,
            lambda: restore_room_step(pack, "room_0", "grid", row_event["after_hash"]),
            "not part of",
        )

    def test_an_unknown_step_is_refused(self, pack: Path) -> None:
        _refuses(pack, lambda: restore_room_step(pack, "room_0", "sprite", "sha256:x"), "not a room step")


# ---------------------------------------------------------------------------
# The P0-8 carry-over: a restore is scoped to its STEP's keys (doctrine 10 —
# an edit may not disappear unannounced; doctrine 6 — a new version, nothing
# deleted). One file carries both steps, so the scope is the key partition.
# ---------------------------------------------------------------------------


class TestRestoreIsScopedToItsStep:
    def test_restoring_the_grid_keeps_placement_edits_made_since(self, pack: Path) -> None:
        """The verifier's repro: paint a cell, move an npc, restore the GRID
        step. Before the fix the whole stored maze.json came back and the npc
        move vanished unannounced."""
        painted = [[1 if v == 1 else 0 for v in row] for row in _maze(pack)["grid"]]
        painted[6][5] = 1
        import_room_grids(pack, "room_0", painted, actor="user")
        paint = provenance.all_events(pack)[-1]
        apply_room_edit(pack, "room_0", {"entities": [{"enemy_id": "1000", "x": 4, "y": 3}]},
                        actor="user")
        assert _maze(pack)["npc_positions"] == {"1000": [4, 3]}

        result = restore_room_step(pack, "room_0", "grid", paint["before_hash"], actor="user")
        maze = _maze(pack)
        assert maze["grid"][6][5] == 0, "the painted cell reverted"
        assert maze["npc_positions"] == {"1000": [4, 3]}, "the npc move survived the grid restore"
        assert result["changed"] == {"grid": {"from": "1 cells", "to": "restored"}}
        assert result["no_change"] is False
        events = provenance.all_events(pack)
        assert events[-1]["op"] == "restore"
        assert events[-1]["artifact_id"] == "room:room_0/grid"
        assert events[-1]["detail"]["keys"] == ["grid", "width", "height"]
        # Nothing is deleted: the version left behind is still in the CAS.
        assert json.loads(provenance.read_object(pack, events[-1]["before_hash"]))["grid"][6][5] == 1

    def test_restoring_the_placements_keeps_the_paint_and_re_stamps_the_grid(
        self, pack: Path
    ) -> None:
        before_placements = provenance.snapshot_file(
            pack, pack / "rooms" / "room_0" / "maze.json"
        )
        apply_room_edit(
            pack, "room_0",
            {"items": [{"item_id": "2000", "x": 6, "y": 3}]},
            actor="user",
        )
        painted = [[1 if v == 1 else 0 for v in row] for row in _maze(pack)["grid"]]
        painted[6][2] = 1
        import_room_grids(pack, "room_0", painted, actor="user")
        assert _maze(pack)["grid"][3][6] == 2000, "the item stamp moved with the item"

        result = restore_room_step(pack, "room_0", "placements", before_placements, actor="user")
        maze = _maze(pack)
        assert maze["item_placements"][0]["x"] == 5 and maze["item_placements"][0]["y"] == 2
        assert maze["grid"][6][2] == 1, "the paint made since survived the placements restore"
        assert maze["grid"][3][6] == 0 and maze["grid"][2][5] == 2000, "stamps re-derived"
        assert result["changed"]["items"]["moves"] == [{"id": "2000", "from": [6, 3], "to": [5, 2]}]

    def test_a_restore_that_walls_a_placement_warns_and_keeps_it(self, pack: Path) -> None:
        walled = [[1 if v == 1 else 0 for v in row] for row in _maze(pack)["grid"]]
        walled[3][4] = 1  # a wall where nothing stands yet
        import_room_grids(pack, "room_0", walled, actor="user")
        walled_version = provenance.all_events(pack)[-1]["after_hash"]
        opened = [list(row) for row in walled]
        opened[3][4] = 0
        import_room_grids(pack, "room_0", opened, actor="user")
        apply_room_edit(pack, "room_0", {"entities": [{"enemy_id": "1000", "x": 4, "y": 3}]},
                        actor="user")

        result = restore_room_step(pack, "room_0", "grid", walled_version, actor="user")
        assert _maze(pack)["npc_positions"] == {"1000": [4, 3]}, "the placement is KEPT"
        assert _maze(pack)["grid"][3][4] == 1, "and the restored wall is not repaired away"
        assert any(
            "cell (4, 3) holds npc 1000" in w and "stands in a wall" in w
            for w in result["warnings"]
        ), result["warnings"]

    def test_a_restore_that_walls_the_player_start_warns_too(self, pack: Path) -> None:
        """CASE B of the carry-over review: `_disturbance_warnings` walked the
        PLACEMENTS only, so a restored grid that walled the player start (or
        the door) said nothing at all — while `import_room_grids` refuses that
        exact cell forever after. Both halves of the paint check are walked
        now, so both halves warn."""
        walled = _collision(pack)
        walled[5][3] = 1  # a wall where nothing stands yet
        import_room_grids(pack, "room_0", walled, actor="user")
        walled_version = provenance.all_events(pack)[-1]["after_hash"]
        opened = [list(row) for row in walled]
        opened[5][3] = 0
        import_room_grids(pack, "room_0", opened, actor="user")
        apply_room_edit(pack, "room_0", {"spawn": [3, 5]}, actor="user")

        result = restore_room_step(pack, "room_0", "grid", walled_version, actor="user")
        assert _maze(pack)["player_start"] == [3, 5], "the marker is KEPT"
        assert any(
            "cell (3, 5) holds player_start" in w
            and "the restored grid walls it" in w
            and "every later paint / drag / marker save refuses (3, 5)" in w
            for w in result["warnings"]
        ), result["warnings"]

    def test_a_placements_restore_names_the_wall_that_was_already_there(
        self, pack: Path
    ) -> None:
        """One sentence served both steps, so a PLACEMENTS restore claimed
        "the restored grid walls it" for a grid it never touched."""
        original = provenance.snapshot_file(pack, pack / "rooms" / "room_0" / "maze.json")
        apply_room_edit(pack, "room_0", {"entities": [{"enemy_id": "1000", "x": 2, "y": 1}]},
                        actor="user")
        walled = _collision(pack)
        walled[3][1] = 1  # npc 1000's ORIGINAL cell, walled after it left
        import_room_grids(pack, "room_0", walled, actor="user")

        result = restore_room_step(pack, "room_0", "placements", original, actor="user")
        assert _maze(pack)["npc_positions"] == {"1000": [1, 3]}
        assert any(
            "cell (1, 3) holds npc 1000" in w
            and "a wall that was already there" in w
            and "the restored grid" not in w
            for w in result["warnings"]
        ), result["warnings"]

    def test_a_grid_restore_past_a_placement_only_edit_is_a_no_change(
        self, pack: Path
    ) -> None:
        """The dense layer carries the placement STAMPS as well as the tiles,
        and `_restamp` puts the current stamps straight back — so a rewind
        past an edit that only MOVED placements leaves the grid byte-identical
        and must report `no_change`. It used to count the stamp cells before
        the restamp and journal a version whose before_hash == after_hash."""
        original = provenance.snapshot_file(pack, pack / "rooms" / "room_0" / "maze.json")
        apply_room_edit(
            pack, "room_0",
            {"triggers": [{"event_id": "3000", "x": 8, "y": 5},
                          {"event_id": "3001", "x": 4, "y": 3}]},
            actor="user",
        )
        before = _tree(pack)
        events = len(provenance.all_events(pack))

        result = restore_room_step(pack, "room_0", "grid", original, actor="user")
        assert result["no_change"] is True and result["changed"] == {}
        assert result["before_hash"] is None and result["after_hash"] is None
        assert result["warnings"] == []
        assert len(provenance.all_events(pack)) == events, "nothing journaled"
        assert _tree(pack) == before, "nothing written"

    def test_restoring_a_step_whose_keys_are_unchanged_is_a_no_change(self, pack: Path) -> None:
        original = provenance.snapshot_file(pack, pack / "rooms" / "room_0" / "maze.json")
        painted = [[1 if v == 1 else 0 for v in row] for row in _maze(pack)["grid"]]
        painted[6][5] = 1
        import_room_grids(pack, "room_0", painted, actor="user")
        before = _tree(pack)
        events = len(provenance.all_events(pack))

        result = restore_room_step(pack, "room_0", "placements", original, actor="user")
        assert result["no_change"] is True and result["changed"] == {}
        assert result["before_hash"] is None and result["after_hash"] is None
        assert len(provenance.all_events(pack)) == events, "nothing journaled"
        assert _tree(pack) == before, "nothing written"

    def test_the_platformers_restore_stays_file_scoped(
        self, plat_pack: Path, tmp_path: Path
    ) -> None:
        """``restore_level_step`` is untouched by the room fix: the
        platformer's steps are per-step FILES, so it is scoped already."""
        from canon.adapters.platformer_write import apply_level_edit, restore_level_step

        plat = tmp_path / "plat_restore"
        shutil.copytree(plat_pack, plat)
        code, payload = _canon("level", "export", str(plat), "--level", "l1")
        assert code == 0, payload
        bundle = payload["level"]  # type: ignore[index]
        entities = [
            {"enemy_id": e["enemy_id"], "x": e["x"], "y": e["y"], "variant": e["variant"]}
            for e in bundle["entities"]
        ]
        stage = bundle["stage_id"]
        rel = Path("level") / stage / "l1"
        before = provenance.snapshot_file(plat, plat / rel / "entities.json")
        moved = copy.deepcopy(entities)
        moved[0]["x"] = int(moved[0]["x"]) + 1
        apply_level_edit(plat, "l1", {"entities": moved}, actor="user")
        items_before = (plat / rel / "items.json").read_bytes()

        restore_level_step(plat, "l1", "entities", before, actor="user")
        assert json.loads((plat / rel / "entities.json").read_text())[0]["x"] == entities[0]["x"]
        assert (plat / rel / "items.json").read_bytes() == items_before


# ---------------------------------------------------------------------------
# The CLI seam — the not-yets are gone; `level *` stays the alias
# ---------------------------------------------------------------------------


class TestCli:
    def test_apply_edit_import_grids_describe_and_roll_serve_rooms(self, pack: Path) -> None:
        code, payload = _canon(
            "grid", "apply-edit", str(pack), "--level", "room_0",
            "--json", json.dumps({"entities": [{"enemy_id": "1000", "x": 4, "y": 3}]}),
            "--actor", "user",
        )
        assert code == 0, payload
        assert payload["updated"] == ["entities"]  # type: ignore[index]

        rows = [[1 if v == 1 else 0 for v in row] for row in _maze(pack)["grid"]]
        rows[6][5] = 1
        code, payload = _canon(
            "level", "import-grids", str(pack), "--level", "room_0",
            "--json", json.dumps({"collision": rows}), "--actor", "user",
        )
        assert code == 0, payload
        assert payload["updated"] == ["grid"]  # type: ignore[index]

        code, payload = _canon("grid", "describe", str(pack), "--level", "room_0")
        assert code == 0, payload
        described = payload["level"]  # type: ignore[index]
        assert described["dims"] == {"width": 10, "height": 8, "axis": None}
        assert described["entities"]["count"] == 1 and described["items"]["count"] == 1
        assert described["room"]["environment"] == "forest"

        code, payload = _canon(
            "grid", "roll", str(pack), "--level", "room_0", "--step", "items",
            "--seed", "pin-1", "--actor", "user",
        )
        assert code == 0, payload
        assert payload["step"] == "items" and payload["cost_usd"] == 0.0  # type: ignore[index]

    def test_describe_matches_the_in_process_projection(self, pack: Path) -> None:
        code, payload = _canon("level", "describe", str(pack), "--level", "room_0")
        assert code == 0, payload
        assert payload["level"] == describe_room(pack, "room_0")  # type: ignore[index]

    def test_window_slices_the_room_bundle(self, pack: Path) -> None:
        code, payload = _canon(
            "grid", "export", str(pack), "--level", "room_0", "--window", "0,0,5,4"
        )
        assert code == 0, payload
        bundle = payload["level"]  # type: ignore[index]
        assert bundle["window"] == {"x0": 0, "y0": 0, "w": 5, "h": 4}
        assert len(bundle["grids"]["collision"]) == 4
        assert bundle["grid_width"] == 10, "the full dims stay"
        # The gate at (8,5) is outside the window; the other event is inside.
        assert [t["params"]["event_id"] for t in bundle["triggers"]] == [3001]

    def test_a_refusal_is_a_structured_error(self, pack: Path) -> None:
        before = _tree(pack)
        code, payload = _canon(
            "grid", "apply-edit", str(pack), "--level", "room_0",
            "--json", json.dumps({"exit": [3, 3]}), "--actor", "user",
        )
        assert code != 0
        assert "gate encounter" in json.dumps(payload)
        assert _tree(pack) == before


# ---------------------------------------------------------------------------
# The platformer's own write verbs are untouched (doctrine 7)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def plat_pack(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("p08_plat")
    subprocess.run(
        [
            sys.executable, "-m", "canon.packs.platformer.run_slice",
            "--backend", "fake", "--engine", "json", "--image-backend", "fake",
            "--music-backend", "none", "--sfx-backend", "none",
            "--num-stages", "1", "--num-levels", "1", "--num-enemies", "2",
            "--output-dir", str(out),
        ],
        check=True, capture_output=True, cwd=REPO,
    )
    return out


def test_platformer_apply_edit_and_import_grids_still_route_to_their_own_writer(
    plat_pack: Path,
) -> None:
    from canon.adapters import GRID_EDITORS, GRID_IMPORTERS, GRID_RESTORERS

    assert GRID_EDITORS["level"] == "canon.adapters.platformer_write:apply_level_edit"
    assert GRID_IMPORTERS["level"] == "canon.adapters.platformer_write:import_level_grids"
    assert GRID_RESTORERS["level"] == "canon.adapters.platformer_write:restore_level_step"
    code, payload = _canon("level", "export", str(plat_pack), "--level", "l1")
    assert code == 0, payload
    bundle = payload["level"]  # type: ignore[index]
    entities = [
        {"enemy_id": e["enemy_id"], "x": e["x"], "y": e["y"], "variant": e["variant"]}
        for e in bundle["entities"]
    ]
    code, result = _canon(
        "grid", "apply-edit", str(plat_pack), "--level", "l1",
        "--json", json.dumps({"entities": entities}), "--actor", "user",
    )
    # An identical placement list is a no-op write on the platformer path too.
    assert code == 0, result
    assert result["level_id"] == "l1" and result["stage_id"]  # type: ignore[index]


def test_a_platformer_level_has_no_code_only_roll_verb(plat_pack: Path) -> None:
    code, payload = _canon(
        "grid", "roll", str(plat_pack), "--level", "l1", "--step", "items", "--actor", "user"
    )
    assert code != 0
    assert "place-items" in json.dumps(payload)


# ---------------------------------------------------------------------------
# P0-8 fix pass — the room must still be EDITABLE after every code-only verb
# ---------------------------------------------------------------------------


def _bundle_edit(pack: Path) -> dict:
    """The sparse payload cradle re-sends after a write: the whole wire for
    every placement layer, straight off the exported bundle."""
    bundle = export_room_bundle(pack, "room_0")
    return {
        "entities": [dict(e) for e in bundle["entities"]],
        "items": [dict(i) for i in bundle["items"]],
        "triggers": [dict(t) for t in bundle["triggers"]],
    }


def _collision(pack: Path) -> list[list[int]]:
    return [[1 if v == 1 else 0 for v in row] for row in _maze(pack)["grid"]]


class TestARollLeavesAnEditableRoom:
    """A roll is code-only, so it must leave a document its OWN writer accepts
    (doctrine 1): the old ``_restamp`` warned about walled-in placements and
    left them there, which made every later paint / drag / marker save refuse."""

    @pytest.mark.parametrize("step", [s for s in ROLL_STEPS if s != "monsters"])
    def test_every_step_round_trips_back_through_the_writer(
        self, pack: Path, step: str
    ) -> None:
        roll_room(pack, "room_0", step, seed="fix", actor="user")
        maze = _maze(pack)
        # Every wire the editor sends, unchanged, is accepted.
        apply_room_edit(pack, "room_0", _bundle_edit(pack), actor="user")
        # The painted grid it exports is accepted.
        import_room_grids(pack, "room_0", _collision(pack), actor="user")
        # And so are the markers cradle always sends together.
        apply_room_edit(
            pack, "room_0",
            {"spawn": maze["player_start"], "exit": maze["door_position"]},
            actor="user",
        )

    def test_the_layout_roll_never_parks_the_door_on_a_placement(self, pack: Path) -> None:
        for seed in ("s1", "s2", "s3", "s4", "s5"):
            fresh = make_pack(pack.parent / f"door-{seed}")
            roll_room(fresh, "room_0", "layout", seed=seed, actor="user")
            maze = json.loads(
                (fresh / "rooms" / "room_0" / "maze.json").read_text(encoding="utf-8")
            )
            door = tuple(maze["door_position"])
            cells = {(e["x"], e["y"]) for e in maze["event_positions"]}
            cells |= {(i["x"], i["y"]) for i in maze["item_placements"]}
            cells |= {tuple(xy) for xy in maze["npc_positions"].values()}
            assert door not in cells, f"seed {seed}: the door landed on a placement"
            assert maze["grid"][door[1]][door[0]] != 1

    def test_a_relocated_placement_is_named_and_never_dropped(self, pack: Path) -> None:
        before = len(_maze(pack)["event_positions"])
        result = roll_room(pack, "room_0", "layout", seed="relocate", actor="user")
        assert len(_maze(pack)["event_positions"]) == before, "doctrine 6 — nothing deleted"
        for warning in result["warnings"]:
            assert "is inside a wall after the roll" not in warning


class TestRollRosterComesFromWhatIsPlaced:
    """``_stubs`` read the room ROW's lore buckets only, so a legacy tree (whose
    ``world_bible`` mirror lists no ``encounters``) had every event placement
    silently deleted by the 🎲 events roll — doctrine 6 and doctrine 10."""

    @staticmethod
    def _empty_buckets(pack: Path) -> None:
        for rel in ("world_bible.json", "rooms/rooms.json"):
            path = pack / rel
            data = json.loads(path.read_text(encoding="utf-8"))
            rooms = data["rooms"] if rel.endswith("world_bible.json") else data
            for row in rooms.values():
                row["encounters"] = []
                row["items"] = []
                row["quests"] = []
            _write(path, data)

    def test_an_events_roll_replaces_what_the_room_places(self, pack: Path) -> None:
        self._empty_buckets(pack)
        before = {str(e["event_id"]) for e in _maze(pack)["event_positions"]}
        roll_room(pack, "room_0", "events", seed="s1", actor="user")
        assert {str(e["event_id"]) for e in _maze(pack)["event_positions"]} == before

    def test_a_whole_roll_keeps_the_gate_and_the_quest_ids(self, pack: Path) -> None:
        self._empty_buckets(pack)
        quests = _maze(pack)["quest_ids"]
        roll_room(pack, "room_0", "whole", seed="s1", actor="user")
        maze = _maze(pack)
        assert maze["gate_encounter_id"] is not None
        assert set(maze["quest_ids"]) >= set(quests), "doctrine 6 — nothing deleted"
        assert maze["event_positions"], "the room's encounters survived the roll"

    def test_a_shortfall_is_named_in_the_warnings(self, pack: Path) -> None:
        """The roster is unioned, so a smaller result means the sampler ran out
        of room — and doctrine 10 says the loss is named, never silent."""
        maze = _maze(pack)
        maze["item_placements"] = [
            {"x": 3, "y": 3, "item_id": 2000, "name": "", "portrait_prompt": "",
             "profile_image": ""},
            {"x": 4, "y": 3, "item_id": 2000, "name": "", "portrait_prompt": "",
             "profile_image": ""},
            {"x": 6, "y": 3, "item_id": 2001, "name": "", "portrait_prompt": "",
             "profile_image": ""},
        ]
        _write(pack / "rooms" / "room_0" / "maze.json", maze)
        result = roll_room(pack, "room_0", "items", seed="s1", actor="user")
        assert any("placed 2 of the 3 item cells" in w for w in result["warnings"])


class TestOneRowManyCells:
    def test_the_same_item_may_sit_on_several_cells(self, pack: Path) -> None:
        """A dungeon scatters one item template over many squares (the reference
        world places 18 items on 77 cells), so the wire must round-trip it."""
        result = apply_room_edit(
            pack, "room_0",
            {"items": [
                {"item_id": "2000", "x": 3, "y": 3},
                {"item_id": "2000", "x": 4, "y": 3},
                {"item_id": "2001", "x": 6, "y": 3},
            ]},
            actor="user",
        )
        assert result["updated"] == ["items"]
        maze = _maze(pack)
        assert {(p["item_id"], p["x"], p["y"]) for p in maze["item_placements"]} == {
            (2000, 3, 3), (2000, 4, 3), (2001, 6, 3)
        }
        assert maze["grid"][3][3] == 2000 and maze["grid"][3][4] == 2000
        # Re-sending the file's own list is a no-op, not a refusal.
        again = apply_room_edit(pack, "room_0", {"items": maze["item_placements"]}, actor="user")
        assert again["no_change"]

    def test_the_dict_shaped_wire_still_refuses_a_repeated_id(self, pack: Path) -> None:
        """``npc_positions`` keys BY the id, so a repeat is an ambiguous payload
        rather than a second placement."""
        _refuses(
            pack,
            lambda: apply_room_edit(
                pack, "room_0",
                {"entities": [{"enemy_id": "1000", "x": 4, "y": 3},
                              {"enemy_id": "1000", "x": 5, "y": 3}]},
            ),
            "placed twice",
        )


class TestGateAdjacencyBothWays:
    def test_moving_the_gate_away_from_the_door_is_refused(self, pack: Path) -> None:
        message = _refuses(
            pack,
            lambda: apply_room_edit(
                pack, "room_0",
                {"triggers": [
                    {"x": 3, "y": 3, "type": "combat", "params": {"event_id": 3000}},
                    {"x": 3, "y": 1, "type": "combat", "params": {"event_id": 3001}},
                ]},
            ),
            "gate encounter 3000 must stay next to the door",
        )
        assert "pass the boss" in message

    def test_the_gate_may_move_to_another_cell_beside_the_door(self, pack: Path) -> None:
        result = apply_room_edit(
            pack, "room_0",
            {"triggers": [
                {"x": 7, "y": 6, "type": "combat", "params": {"event_id": 3000}},
                {"x": 3, "y": 1, "type": "combat", "params": {"event_id": 3001}},
            ]},
            actor="user",
        )
        assert result["updated"] == ["triggers"]

    def test_an_unchanged_marker_is_not_judged_again(self, pack: Path) -> None:
        """cradle sends spawn AND exit whenever the markers layer is dirty, so a
        marker the caller re-sends unchanged must not be held hostage by a
        pre-existing state it did not create."""
        maze = _maze(pack)
        maze["gate_encounter_id"] = 3001  # a gate that is nowhere near the door
        _write(pack / "rooms" / "room_0" / "maze.json", maze)
        result = apply_room_edit(
            pack, "room_0", {"spawn": [1, 6], "exit": maze["door_position"]}, actor="user"
        )
        assert result["updated"] == ["spawn"]
        assert _maze(pack)["player_start"] == [1, 6]


class TestEncountersAreFailClosed:
    def test_an_out_of_bounds_encounter_creates_no_orphan_row(self, pack: Path) -> None:
        _refuses(
            pack,
            lambda: apply_room_edit(
                pack, "room_0", {"encounters": [{"x": 99, "y": 99, "monster_ids": [5000]}]}
            ),
            "outside the",
        )

    def test_a_wall_or_marker_cell_is_refused_before_any_row_write(self, pack: Path) -> None:
        _refuses(
            pack,
            lambda: apply_room_edit(
                pack, "room_0", {"encounters": [{"x": 0, "y": 0, "monster_ids": []}]}
            ),
            "is a wall",
        )
        _refuses(
            pack,
            lambda: apply_room_edit(
                pack, "room_0", {"encounters": [{"x": 1, "y": 1, "monster_ids": []}]}
            ),
            "player_start",
        )

    def test_a_partially_valid_list_writes_nothing(self, pack: Path) -> None:
        """Entry 1 is fine and entry 2 names an unknown monster: the whole
        payload is judged first, so entry 1's row edit never lands."""
        _refuses(
            pack,
            lambda: apply_room_edit(
                pack, "room_0",
                {"encounters": [
                    {"x": 8, "y": 5, "event_id": 3000, "monster_ids": [5001]},
                    {"x": 4, "y": 3, "monster_ids": [9999]},
                ]},
            ),
            "monster 9999 has no row",
        )

    def test_encounters_and_triggers_in_one_edit_keep_both(self, pack: Path) -> None:
        """``_apply_encounters`` used to rebuild the event wire from DISK, so a
        caller that sent both keys silently lost its ``triggers`` payload."""
        result = apply_room_edit(
            pack, "room_0",
            {
                "triggers": [
                    {"x": 8, "y": 5, "type": "combat", "params": {"event_id": 3000}},
                    {"x": 6, "y": 1, "type": "combat", "params": {"event_id": 3001}},
                ],
                "encounters": [{"x": 8, "y": 5, "event_id": 3000, "monster_ids": [5000, 5002]}],
            },
            actor="user",
        )
        assert "triggers" in result["updated"]
        cells = {str(e["event_id"]): (e["x"], e["y"]) for e in _maze(pack)["event_positions"]}
        assert cells["3001"] == (6, 1), "the caller's own trigger move survived"
        assert _events(pack)["3000"]["monster_ids"] == [5000, 5002]
