"""Row P0-6 — W1 P3 write: the write core, the ``db`` verbs in core, the
collection layout, the dynamic models, ``db define`` / ``db evolve``,
``registry set`` + capability enablement, ``world update``, the grid verb
dispatch, and success criterion 6 (P0 paper P.1, P.3.1, P.4.1, P.7; master
§3.0-A).

Two fixtures: a fresh $0 platformer tree (module-scoped; tests copy it when
they mutate) and a COPY of the reference dungeon fixture (function-scoped —
adopt-on-write creates ``.canon/`` on the copy, never on the checked-in
tree). No test calls a real provider (doctrine 3: ``fake`` / ``none`` only).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from canon import db_ops, registry_ops, world_ops
from canon.adapters.platformer_write import restore_asset
from canon.packs import PACKS, effective_spec, pack_info, resolve_pack
from canon.packs.platformer import ops
from canon.provenance import all_events, read_object
from canon.registry_ops import template_version
from canon.write_core import NotYetError, apply_changes, check_wall, leaf_of, parse_address, set_path

REPO = Path(__file__).resolve().parents[1]
DUNGEON_FIXTURE = REPO / "tests" / "reference" / "fixtures" / "cradle_mazeworld_scifi"
CANON = [sys.executable, "-m", "canon.cli.main"]

ABILITY = {
    "label": "Abilities",
    "layout": {"mode": "collection", "path": "abilities/abilities.json", "format": "array"},
    "id_field": "id",
    "id_alloc": {"base": 7000},
    "llm_fields": ["name", "description"],
    "schema": {"fields": {"tier": {"choices": [["minor", 3], ["major", 1]]}}},
}


def _canon(*args: str) -> tuple[int, object]:
    result = subprocess.run(CANON + list(args), capture_output=True, text=True, cwd=REPO)
    stream = result.stdout if result.returncode == 0 else result.stderr
    try:
        return result.returncode, json.loads(stream)
    except json.JSONDecodeError:
        return result.returncode, stream


def _tree(root: Path, *, skip_journal: bool = True) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(root))
        if skip_journal and rel in (".canon/journal.jsonl", ".canon/log.jsonl"):
            continue
        out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _events(pack: Path, kind: str | None = None) -> list[dict]:
    events = all_events(pack)
    if kind is None:
        return events
    return [e for e in events if (e.get("detail") or {}).get("kind") == kind]


def _sans_ts(events: list[dict]) -> list[dict]:
    return [{k: v for k, v in e.items() if k != "ts"} for e in events]


@pytest.fixture(scope="module")
def plat_pack(tmp_path_factory) -> Path:
    """A fresh $0 platformer tree — the byte-identity subject."""
    out = tmp_path_factory.mktemp("p06_plat")
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


@pytest.fixture
def plat(plat_pack: Path, tmp_path: Path) -> Path:
    copy = tmp_path / "plat"
    shutil.copytree(plat_pack, copy)
    return copy


@pytest.fixture
def dungeon(tmp_path: Path) -> Path:
    """A COPY of the legacy reference tree (no ``.canon/``, no registry, no
    ``rooms/rooms.json``, no ``story/story.json``) — adopt-on-write's subject."""
    copy = tmp_path / "dungeon"
    shutil.copytree(DUNGEON_FIXTURE, copy)
    assert not (copy / ".canon").exists()
    return copy


# ---------------------------------------------------------------------------
# The write core itself
# ---------------------------------------------------------------------------


class TestWriteCore:
    def test_address_grammar(self) -> None:
        assert parse_address("a.b[2].c[+]") == [("a", []), ("b", ["2"]), ("c", ["+"])]
        assert leaf_of("stats.animation") == "animation"
        assert leaf_of("abilities[0].name") == "name"
        assert leaf_of("monster_ids[2]") == "monster_ids"
        doc = {"a": {"list": [{"k": "x", "v": 1}, {"k": "y", "v": 2}]}}
        assert set_path(doc, "a.list[k=y].v", 9) == (2, 9)
        assert set_path(doc, "a.list[+]", {"k": "z"}) == (None, {"k": "z"})
        assert set_path(doc, "a.list[0]", None)[0] == {"k": "x", "v": 1}
        assert [i["k"] for i in doc["a"]["list"]] == ["y", "z"]
        with pytest.raises(ValueError, match="out of range"):
            set_path(doc, "a.list[9].v", 1)
        with pytest.raises(ValueError, match="no list item"):
            set_path(doc, "a.list[k=nope].v", 1)
        with pytest.raises(ValueError, match="top-level"):
            set_path(doc, "a", None)
        assert apply_changes({"x": 1}, {"x": 1}) == {}

    def test_wall_matcher_is_parameterized(self) -> None:
        check_wall("name", wall={"id"})
        with pytest.raises(ValueError, match="protected"):
            check_wall("stats.id", wall={"id"})
        with pytest.raises(ValueError, match="owned by grid"):
            check_wall("x", wall=set(), routed={"x": "grid"})
        with pytest.raises(ValueError, match="container"):
            check_wall("stats", wall=set(), containers=("stats",))
        with pytest.raises(ValueError, match="custom rule"):
            check_wall("anything", wall=set(), refuse=lambda n: "custom rule")


# ---------------------------------------------------------------------------
# A/B: the platformer wrappers ARE the core — same bytes, same journal
# ---------------------------------------------------------------------------


class TestPlatformerByteIdentity:
    def _drive(self, module, pack: Path) -> list[dict]:
        outputs = []
        created = module.new_db_row(
            pack, "enemy", {"archetype": "patroller", "name": "Edit Target"}, actor="ab",
        )
        eid = created["id"]
        outputs.append(created)
        outputs.append(module.update_db_row(
            pack, "enemy", eid, {"name": "Renamed", "hp": 9, "patrol_range": 5, "stats.custom_knob": 3}, actor="ab",
        ))
        outputs.append(module.new_db_row(pack, "item", {}, actor="ab"))
        llm = module.build_llm("fake", None)
        outputs.append(module.complete_db_row(pack, "enemy", eid, ["name"], llm=llm, actor="ab"))
        outputs.append(
            module.new_db_row(pack, "enemy", {"name": "Authored"}, complete=True, llm=llm, actor="ab")
        )
        outputs.append(module.update_db_schema(
            pack, "enemy", {"fields": {"archetype": {"choices": [["sentry", 1]]}}}, actor="ab",
        ))
        outputs.append(module.new_db_row(pack, "enemy", {"name": "Sentry Only"}, actor="ab"))
        outputs.append(module.db_types(pack))
        outputs.append(module.read_db_schema(pack, "item"))
        return outputs

    def test_same_call_same_bytes_same_journal(self, plat_pack: Path, tmp_path: Path) -> None:
        """The wrapper module (what cradle + the agent import) and
        ``canon.db_ops`` (what the CLI dispatches to) produce identical
        files, identical journal events (modulo ``ts``) and identical
        results on two copies of one tree."""
        a, b = tmp_path / "a", tmp_path / "b"
        shutil.copytree(plat_pack, a)
        shutil.copytree(plat_pack, b)
        out_a = self._drive(ops, a)
        out_b = self._drive(db_ops, b)
        # `read_db_schema` reports the pack-local path — normalize the copy dirs.
        # Row P1-A6 added `journal_ref` (the ts of the costed journal event, so
        # a derived spend row can point back at it): a wall-clock stamp, so it
        # is normalized like every other timestamp in this suite.
        def _dump(out, root: Path) -> str:
            text = json.dumps(out, sort_keys=True, default=str).replace(str(root), "<pack>")
            return re.sub(r'"journal_ref": "[^"]*"', '"journal_ref": "<ts>"', text)

        dump_a, dump_b = _dump(out_a, a), _dump(out_b, b)
        assert dump_a == dump_b
        assert _tree(a) == _tree(b)
        assert _sans_ts(all_events(a)) == _sans_ts(all_events(b))
        # the byte-identical rows are the ones the pre-extraction tests pin
        row = out_a[1]["row"]
        assert row["status"] == "user_edited" and row["stats"]["custom_knob"] == 3

    def test_db_update_event_shape_is_the_pre_extraction_shape(self, plat: Path) -> None:
        eid = sorted(p.stem for p in (plat / "enemy").glob("*.json"))[0]
        before_bytes = (plat / "enemy" / f"{eid}.json").read_bytes()
        result = ops.update_db_row(plat, "enemy", eid, {"hp": 7}, actor="test", session="conv-1")
        ev = _events(plat, "db_update")[-1]
        # ``identity`` joined the shape at row P1-A6 (master §3.0-B / P.8.2) —
        # additive, ``schema`` still 1, and a pure function of ``actor``.
        assert set(ev) == {
            "schema", "ts", "artifact_id", "op", "source", "actor", "identity",
            "session", "detail", "before_hash", "after_hash",
        }
        assert ev["identity"] == "user"
        assert ev["artifact_id"] == f"enemy:{eid}" and ev["op"] == "edit" and ev["source"] == "user"
        assert ev["detail"] == {
            "kind": "db_update", "type": "enemy",
            "changed": {"hp": {"from": result["changed"]["hp"]["from"], "to": 7}},
        }
        assert read_object(plat, ev["before_hash"]) == before_bytes
        assert read_object(plat, ev["after_hash"]) == (plat / "enemy" / f"{eid}.json").read_bytes()

    def test_db_types_and_schema_gain_the_p1_lists_additively(self, plat: Path) -> None:
        types = ops.db_types(plat)
        for kind in ("enemy", "item"):
            entry = types[kind]
            assert entry["dir"] == kind and entry["schema_source"] == "default"
            assert set(entry) >= {
                "dir", "id_field", "skeleton_fields", "llm_fields", "code_fields", "schema_source",
                "user_fields", "hidden", "decorative", "protected", "routed", "layout", "label",
            }
            assert f"{kind}_id" in entry["protected"] and "artifact_id" in entry["protected"]
        schema = ops.read_db_schema(plat, "enemy")
        assert set(schema) >= {
            "type", "source", "path", "schema", "user_fields", "hidden", "decorative", "protected", "routed",
        }
        assert schema["source"] == "default"

    def test_platformer_refusals_unchanged(self, plat: Path) -> None:
        eid = sorted(p.stem for p in (plat / "enemy").glob("*.json"))[0]
        n = len(all_events(plat))
        with pytest.raises(ValueError, match="protected"):
            ops.update_db_row(plat, "enemy", eid, {"enemy_id": "sneaky"})
        with pytest.raises(ValueError, match="protected"):
            ops.update_db_row(plat, "enemy", eid, {"stats.animation": {}})
        with pytest.raises(ValueError, match="unknown field"):
            ops.update_db_row(plat, "enemy", eid, {"bogus_field": 1})
        with pytest.raises(ValueError, match="container"):
            ops.update_db_row(plat, "enemy", eid, {"stats": {}})
        assert len(all_events(plat)) == n


# ---------------------------------------------------------------------------
# Collection layout — the dungeon kinds through the core
# ---------------------------------------------------------------------------


class TestCollectionLayout:
    def test_adopt_on_write_creates_canon_dir(self, dungeon: Path) -> None:
        result = db_ops.update_db_row(dungeon, "npc", "1000", {"name": "Mira Renamed"}, actor="test")
        assert result["changed"] == {"name": {"from": "Mira Dustcrawler", "to": "Mira Renamed"}}
        assert (dungeon / ".canon" / "journal.jsonl").is_file()
        assert (dungeon / ".canon" / "objects").is_dir()
        ev = _events(dungeon, "db_update")[-1]
        assert ev["artifact_id"] == "npc:1000" and ev["before_hash"] and ev["after_hash"]
        # the CAS unit is the FILE: the before-version is the whole collection
        assert json.loads(read_object(dungeon, ev["before_hash"]))[0]["name"] == "Mira Dustcrawler"
        rows = json.loads((dungeon / "npcs" / "npcs.json").read_text())
        # no `status` stamp: the engine's row shape is untouched
        assert rows[0]["name"] == "Mira Renamed" and "status" not in rows[0]

    def test_every_layout_format(self, dungeon: Path) -> None:
        # array (npc) is above; keyed_object (item) with a nested knob; positional (class)
        item = db_ops.update_db_row(dungeon, "item", "2000", {"price": 9, "name": "ration brick"}, actor="test")
        assert item["changed"]["price"] == {"from": 5, "to": 9}
        data = json.loads((dungeon / "items" / "items.json").read_text())
        assert data["2000"]["item_stats"]["price"] == 9 and list(data)[0] == "2000"
        cls = db_ops.update_db_row(dungeon, "class", "warrior", {"flavor_text": "Steel and grit"}, actor="test")
        assert cls["changed"]["flavor_text"]["to"] == "Steel and grit"
        classes = json.loads((dungeon / "classes" / "classes.json").read_text())
        assert [c["archetype"] for c in classes][:1] == ["warrior"]
        assert classes[0]["flavor_text"] == "Steel and grit"
        quest = db_ops.update_db_row(dungeon, "quest", "4000", {"xp": 50, "reward.money": 5}, actor="test")
        assert set(quest["changed"]) == {"xp", "reward.money"}

    def test_protected_routed_container_refusals_and_copy(self, dungeon: Path) -> None:
        db_ops.update_db_row(dungeon, "npc", "1000", {"name": "x"}, actor="test")
        n = len(all_events(dungeon))
        with pytest.raises(ValueError, match="'id' is protected \\(identity / provenance / asset plumbing"):
            db_ops.update_db_row(dungeon, "npc", "1000", {"id": 5})
        with pytest.raises(ValueError, match="'selected' is protected"):
            db_ops.update_db_row(dungeon, "npc", "1000", {"selected": False})
        with pytest.raises(ValueError, match="'x' is owned by grid — use that surface"):
            db_ops.update_db_row(dungeon, "npc", "1000", {"x": 3})
        with pytest.raises(ValueError, match="owned by dialogue"):
            db_ops.update_db_row(dungeon, "npc", "1000", {"dialogue_tree": {}})
        with pytest.raises(ValueError, match="container"):
            db_ops.update_db_row(dungeon, "npc", "1000", {"shop_inventory": []})
        # P.1: the hint names the grammar that WORKS — a list container is
        # addressed `<c>[<i>].<key>`, so the dict form would be a second refusal
        with pytest.raises(ValueError, match=r"list container — address items as 'shop_inventory\[<i>\]\.<key>'"):
            db_ops.update_db_row(dungeon, "npc", "1011", {"shop_inventory": []})
        with pytest.raises(ValueError, match="owned by scene"):
            db_ops.update_db_row(dungeon, "event", "3000", {"lines": []})
        with pytest.raises(FileNotFoundError, match="not found"):
            db_ops.update_db_row(dungeon, "npc", "424242", {"name": "x"})
        with pytest.raises(ValueError, match="unknown db type"):
            db_ops.update_db_row(dungeon, "dragon", "1", {"name": "x"})
        assert len(all_events(dungeon)) == n

    def test_list_container_addressing(self, dungeon: Path) -> None:
        before = json.loads((dungeon / "monsters" / "monsters.json").read_text())["5000"]["abilities"]
        result = db_ops.update_db_row(
            dungeon, "monster", "5000",
            {
                "abilities[0].name": "Bite!",
                "abilities[+]": {"name": "Roar", "effect_type": "buff", "damage_dice": "1d4", "chance": 0.5},
            },
            actor="test",
        )
        assert result["changed"]["abilities[0].name"]["from"] == before[0]["name"]
        after = json.loads((dungeon / "monsters" / "monsters.json").read_text())["5000"]["abilities"]
        assert after[0]["name"] == "Bite!" and after[-1]["name"] == "Roar" and len(after) == len(before) + 1
        last = f"abilities[{len(after) - 1}]"
        result = db_ops.update_db_row(dungeon, "monster", "5000", {last: None}, actor="test")
        assert result["changed"][last]["to"] is None
        remaining = json.loads((dungeon / "monsters" / "monsters.json").read_text())["5000"]["abilities"]
        assert len(remaining) == len(before)
        with pytest.raises(ValueError, match="out of range"):
            db_ops.update_db_row(dungeon, "monster", "5000", {"abilities[99].name": "x"})
        with pytest.raises(ValueError, match="declared container"):
            db_ops.update_db_row(dungeon, "monster", "5000", {"nope[0].name": "x"})

    def test_refs_refuse_new_dangling_only(self, dungeon: Path) -> None:
        with pytest.raises(ValueError, match="does not resolve to a npc row"):
            db_ops.update_db_row(dungeon, "quest", "4000", {"giver_npc_id": 999999})
        # a legitimate ref lands
        quest = json.loads((dungeon / "quests" / "quests.json").read_text())[0]
        npcs = json.loads((dungeon / "npcs" / "npcs.json").read_text())
        npc_id = next(r["id"] for r in npcs if r["id"] != quest["giver_npc_id"])
        assert db_ops.update_db_row(dungeon, "quest", "4000", {"giver_npc_id": npc_id}, actor="test")["changed"]

    def test_pre_existing_dangling_ref_warns_and_the_warning_reaches_the_caller(self, dungeon: Path) -> None:
        rows = json.loads((dungeon / "quests" / "quests.json").read_text())
        for row in rows:
            if str(row["id"]) == "4000":
                row["giver_npc_id"] = 999999
        (dungeon / "quests" / "quests.json").write_text(json.dumps(rows))
        # the edit does not touch the ref: it warns instead of blocking, and
        # the warning is appended inside `validate` — the write core re-reads
        # the caller's list after validate, so it must survive to the result
        result = db_ops.update_db_row(dungeon, "quest", "4000", {"xp": 77}, actor="test")
        assert result["changed"]["xp"]["to"] == 77
        assert [w for w in result["warnings"] if "does not resolve to a npc row" in w and "pre-existing" in w]

    def test_new_allocates_ids_and_slug_kinds_need_one(self, dungeon: Path) -> None:
        npcs = json.loads((dungeon / "npcs" / "npcs.json").read_text())
        top = max(int(r["id"]) for r in npcs)
        created = db_ops.new_db_row(dungeon, "npc", {"name": "New Guy", "job": "smith"}, actor="test")
        assert created["id"] == top + 1 and created["row"] == {"id": top + 1, "name": "New Guy", "job": "smith"}
        assert json.loads((dungeon / "npcs" / "npcs.json").read_text())[-1]["id"] == top + 1
        ev = _events(dungeon, "db_new")[-1]
        assert ev["op"] == "create" and ev["artifact_id"] == f"npc:{top + 1}" and ev["before_hash"]
        with pytest.raises(ValueError, match="is allocated"):
            db_ops.new_db_row(dungeon, "npc", {"id": 5})
        with pytest.raises(ValueError, match="no id allocation"):
            db_ops.new_db_row(dungeon, "class", {"name": "x"})
        with pytest.raises(ValueError, match="already exists"):
            db_ops.new_db_row(dungeon, "class", {"archetype": "warrior"})
        with pytest.raises(ValueError, match="protected"):
            db_ops.new_db_row(dungeon, "npc", {"profile_image": "x.png"})
        keyed = db_ops.new_db_row(dungeon, "item", {"name": "Widget", "category": "tool"}, actor="test")
        assert str(keyed["id"]) in json.loads((dungeon / "items" / "items.json").read_text())

    def test_complete_is_a_structured_not_yet(self, dungeon: Path) -> None:
        with pytest.raises(NotYetError) as info:
            db_ops.complete_db_row(dungeon, "npc", "1000")
        assert info.value.payload["not_yet"] is True and "row" in info.value.payload
        with pytest.raises(NotYetError):
            db_ops.new_db_row(dungeon, "npc", {"name": "x"}, complete=True)
        assert not (dungeon / ".canon").exists()  # nothing written

    def test_cli_dispatches_db_verbs_on_a_dungeon(self, dungeon: Path) -> None:
        code, doc = _canon("db", "types", str(dungeon))
        assert code == 0 and list(doc["types"]) == list(PACKS["dungeon"].entities)
        code, doc = _canon(
            "db", "update", str(dungeon), "--type", "npc", "--id", "1000", "--set", '{"name": "CLI"}', "--actor", "cli",
        )
        assert code == 0 and doc["changed"]["name"]["to"] == "CLI"
        code, doc = _canon("db", "complete", str(dungeon), "--type", "npc", "--id", "1000")
        assert code != 0 and doc["not_yet"] is True
        # the two not-yets whose payload and whose caller BOTH carry `type`:
        # a structured JSON error, never a Python traceback
        code, doc = _canon("db", "new", str(dungeon), "--type", "npc", "--complete", "--llm-backend", "fake")
        assert code != 0 and doc["not_yet"] is True and doc["type"] == "npc"
        code, doc = _canon("db", "evolve", str(dungeon), "--type", "npc", "--rename-type", "person")
        assert code != 0 and doc["not_yet"] is True and doc["row"] == "v1.1" and doc["type"] == "npc"
        code, doc = _canon("db", "update", str(dungeon), "--type", "tile", "--id", "x/y", "--set", "{}")
        assert code != 0 and "tileset" in doc["error"]


# ---------------------------------------------------------------------------
# The P0-8 carry-over: `db update --type room` on BOTH dungeon tree shapes.
# The room kind's layout points at `rooms/rooms.json`, which the legacy trees
# do not have (the same fact `pack info`'s room count already falls back on);
# the row resolves from the `world_bible` mirror there, and every mirror that
# exists is written in the same batch (P.1.7's write targets). No migration,
# no synthesized index (master §2: read-both shim, never a migrate verb).
# ---------------------------------------------------------------------------


def _index_from_bible(pack: Path) -> dict:
    """The `rooms/rooms.json` a fresh generation writes, from the bible rooms
    — `MazeworldPhase`'s own line (`packs/dungeon/phases.py`: `{id, **room,
    maze_ref}`). Turns the legacy fixture into the INDEXED shape."""
    rooms = json.loads((pack / "world_bible.json").read_text())["rooms"]
    index = {
        rid: {"id": rid, **room, "maze_ref": f"rooms/{rid}/maze.json"}
        for rid, room in rooms.items()
    }
    (pack / "rooms" / "rooms.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    return index


def _manifest_room(pack: Path, room_id: str) -> dict:
    rooms = json.loads((pack / "manifest.json").read_text())["rooms"]
    return next(r for r in rooms if r["room_id"] == room_id)


class TestRoomRowMirrors:
    def test_room_update_round_trips_on_a_legacy_tree_without_the_index(
        self, dungeon: Path
    ) -> None:
        assert not (dungeon / "rooms" / "rooms.json").exists()
        rooms_before = pack_info(dungeon)["entities"]["room"]["count"]

        result = db_ops.update_db_row(
            dungeon, "room", "room_0",
            {"environment_name": "Signal Hollow", "level": 4},
            actor="test",
        )
        assert set(result["changed"]) == {"environment_name", "level"}
        assert result["file"] == "world_bible.json", "the bible mirror stood in as the row"
        assert result["row"]["environment_name"] == "Signal Hollow"
        # read back from disk, not from the result
        bible = json.loads((dungeon / "world_bible.json").read_text())["rooms"]["room_0"]
        assert (bible["environment_name"], bible["level"]) == ("Signal Hollow", 4)
        # the mirrors that exist keep the keys they carry, and gain none
        manifest_row = _manifest_room(dungeon, "room_0")
        assert manifest_row["environment_name"] == "Signal Hollow"
        assert "level" not in manifest_row
        maze = json.loads((dungeon / "rooms" / "room_0" / "maze.json").read_text())
        assert maze["environment_name"] == "Signal Hollow" and "level" not in maze
        # NOTHING was migrated: no index synthesized, and the count is untouched
        assert not (dungeon / "rooms" / "rooms.json").exists()
        assert pack_info(dungeon)["entities"]["room"]["count"] == rooms_before

        # one journal event per file, one batch, mirrors marked as mirrors
        events = _events(dungeon, "db_update")
        assert [e["artifact_id"] for e in events] == [
            "world_bible", "manifest", "room:room_0/grid",
        ]
        assert {e["batchId"] for e in events} == {"db-update:room:room_0"}
        assert [(e["detail"] or {}).get("mirror_of") for e in events] == [
            None, "room:room_0", "room:room_0",
        ]
        # The primary event is published under the MIRROR's artifact name here
        # (the CAS unit is the whole `world_bible.json`, which is what a
        # restore would write back), so the ROW id has to ride in the detail —
        # otherwise two edits to two different rooms are indistinguishable
        # whenever the batch is unbound.
        assert (events[0]["detail"] or {}).get("id") == "room_0"
        # an identical re-write is a no_change: nothing written, nothing journaled
        again = db_ops.update_db_row(
            dungeon, "room", "room_0", {"environment_name": "Signal Hollow"}, actor="test"
        )
        assert again["no_change"] is True and again["mirrors"] == []
        assert len(_events(dungeon, "db_update")) == len(events)

    def test_room_update_round_trips_on_a_tree_that_has_the_index(
        self, dungeon: Path
    ) -> None:
        _index_from_bible(dungeon)
        result = db_ops.update_db_row(
            dungeon, "room", "room_0", {"environment": "city", "level": 3}, actor="test"
        )
        assert result["file"] == "rooms/rooms.json", "the index is the row when it exists"
        index = json.loads((dungeon / "rooms" / "rooms.json").read_text())["room_0"]
        bible = json.loads((dungeon / "world_bible.json").read_text())["rooms"]["room_0"]
        maze = json.loads((dungeon / "rooms" / "room_0" / "maze.json").read_text())
        assert (index["environment"], index["level"]) == ("city", 3)
        assert (bible["environment"], bible["level"]) == ("city", 3), "mirrors stay consistent"
        assert _manifest_room(dungeon, "room_0")["environment"] == "city"
        assert maze["environment"] == "city" and "level" not in maze
        events = _events(dungeon, "db_update")
        assert [e["artifact_id"] for e in events] == [
            "room:room_0", "world_bible", "manifest", "room:room_0/grid",
        ]
        assert {e["batchId"] for e in events} == {"db-update:room:room_0"}
        # The artifact id already names the row here, so the primary detail
        # keeps the frozen pre-extraction shape and gains no `id`.
        assert "id" not in (events[0]["detail"] or {})

    def test_a_room_edit_still_walls_protected_and_routed_fields(self, dungeon: Path) -> None:
        before = _tree(dungeon)
        for field, message in (
            ("id", "'id' is protected"),
            ("maze_ref", "'maze_ref' is protected"),
            ("grid", "'grid' is owned by grid — use that surface"),
            ("npc_positions", "owned by grid"),
        ):
            with pytest.raises(ValueError, match=re.escape(message)):
                db_ops.update_db_row(dungeon, "room", "room_0", {field: "x"}, actor="test")
        with pytest.raises(FileNotFoundError, match="room 'room_404' not found"):
            db_ops.update_db_row(dungeon, "room", "room_404", {"level": 2}, actor="test")
        assert _tree(dungeon) == before, "a refused room edit wrote nothing"

    def test_a_room_whose_grid_file_is_absent_writes_the_mirrors_that_exist(
        self, dungeon: Path
    ) -> None:
        # room_1 is in the bible + manifest but has no rooms/room_1/ dir here.
        assert not (dungeon / "rooms" / "room_1").exists()
        result = db_ops.update_db_row(
            dungeon, "room", "room_1", {"environment_name": "Bonefield"}, actor="test"
        )
        assert [m["file"] for m in result["mirrors"]] == ["manifest.json"]
        assert not (dungeon / "rooms" / "room_1").exists()


# ---------------------------------------------------------------------------
# Dynamic models (P.3.1)
# ---------------------------------------------------------------------------


class TestDynamicModel:
    def test_off_table_warns_type_mismatch_blocks(self, dungeon: Path) -> None:
        db_ops.db_define(dungeon, "player_ability", ABILITY, actor="test")
        row = db_ops.new_db_row(dungeon, "player_ability", {"name": "Dash"}, actor="test")
        assert row["row"]["tier"] in ("minor", "major") and row["id"] == 7000
        result = db_ops.update_db_row(
            dungeon, "player_ability", "7000", {"tier": "legendary", "description": "zoom"}, actor="test",
        )
        assert result["row"]["tier"] == "legendary"
        assert any("outside the roll table" in w for w in result["warnings"])
        with pytest.raises(ValueError, match="fails validation"):
            db_ops.update_db_row(dungeon, "player_ability", "7000", {"tier": 5})
        # an unknown top-level key on a schema-less row lands with a warning (extra=allow)
        result = db_ops.update_db_row(dungeon, "player_ability", "7000", {"cooldown_s": 3}, actor="test")
        assert result["row"]["cooldown_s"] == 3 and any("not in the schema" in w for w in result["warnings"])

    def test_pack_local_schema_types_a_seed_kind(self, dungeon: Path) -> None:
        # `db schema --set` on a kind with no template schema creates the pack-local table
        schema = db_ops.read_db_schema(dungeon, "quest")
        assert schema["source"] is None and schema["schema"]["fields"] == {}
        result = db_ops.update_db_schema(
            dungeon, "quest", {"fields": {"reward_tier": {"range": [1, 3]}}}, actor="test",
        )
        assert result["source"] == "pack" and (dungeon / "schemas" / "quest.json").is_file()
        assert db_ops.db_types(dungeon)["quest"]["schema_source"] == "pack"
        assert pack_info(dungeon)["entities"]["quest"]["schema_source"] == "pack"
        ev = _events(dungeon, "db_schema")[-1]
        assert ev["artifact_id"] == "schema:quest" and ev["detail"]["was"] is None
        created = db_ops.new_db_row(dungeon, "quest", {"title": "Fetch the thing"}, actor="test")
        assert 1 <= created["row"]["reward_tier"] <= 3
        off = db_ops.update_db_row(dungeon, "quest", str(created["id"]), {"reward_tier": 9}, actor="test")
        assert off["warnings"]


# ---------------------------------------------------------------------------
# Registry synthesis, registry set, capability enablement
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_first_registry_verb_synthesizes_and_tier1_answers(self, dungeon: Path) -> None:
        assert resolve_pack(dungeon).source == "shape"
        result = registry_ops.registry_set(dungeon, {"label": "My Crawler"}, actor="test")
        assert result["synthesized"]
        assert result["changed"] == {"label": {"from": "Dungeon crawler", "to": "My Crawler"}}
        doc = json.loads((dungeon / ".canon" / "registry.json").read_text())
        assert list(doc)[:3] == ["schema", "pack_type", "template"]
        template = doc["template"]
        assert template["id"] == "dungeon" and template["canon_version"] == "0.1" and template["created_at"]
        # P.4.1's rule, recomputed from the seed's stamped subset — the input
        # set is the stamped subset minus `template`, nothing else, so the
        # version is recomputable from the written document too
        assert doc["template"]["version"] == template_version(PACKS["dungeon"].stamped())
        fresh = registry_ops.synthesize_registry(PACKS["dungeon"], "dungeon")
        assert fresh["template"]["version"] == template_version(
            {k: v for k, v in fresh.items() if k not in ("schema", "template")}
        )
        assert doc["tuning"] == {"schema": "canon-tuning/v0", "status": "reserved", "keys": {}}
        resolved = resolve_pack(dungeon)
        assert resolved.source == "registry" and resolved.spec.label == "My Crawler"
        info = pack_info(dungeon)
        assert info["source"] == "registry" and info["label"] == "My Crawler"
        assert info["template"]["version"] == doc["template"]["version"]
        kinds = [(e["artifact_id"], e["op"], e["detail"]["kind"]) for e in all_events(dungeon)]
        assert kinds == [("registry", "create", "registry_synthesize"), ("registry", "edit", "registry_set")]
        # the second verb merges — no second synthesis
        again = registry_ops.registry_set(dungeon, {"description": "mine"}, actor="test")
        assert not again["synthesized"] and len(all_events(dungeon)) == 3

    def test_registry_set_merge_rules_and_refusals(self, dungeon: Path) -> None:
        result = registry_ops.registry_set(
            dungeon, {"tuning": {"keys": {"speed": {"min": 1, "max": 9}}}}, actor="test",
        )
        assert result["changed"] == {
            "tuning.keys.speed.min": {"from": None, "to": 1}, "tuning.keys.speed.max": {"from": None, "to": 9},
        }
        assert any("no verb reads tuning.keys yet" in w for w in result["warnings"])
        assert json.loads((dungeon / ".canon" / "registry.json").read_text())["tuning"]["status"] == "reserved"
        n = len(all_events(dungeon))
        for bad, why in (
            ({"pack_type": "x"}, "identity"),
            ({"template": {"id": "x"}}, "stamped at create"),
            ({"engines": []}, "engine attach"),
            ({"entities": {"npc": {"label": "x"}}}, "db define"),
            ({"tuning": {"keys": {"speed": {"type": "int"}}}}, "template-owned"),
            ({"tuning": {"status": "live"}}, "reserved"),
            ({"capabilities": {"dialogue": False}}, "v1.1"),
            ({"capabilities": {"bogus": True}}, "no implementing seed"),
            ({"capabilities": ["dialogue"]}, "map form"),
        ):
            with pytest.raises(ValueError, match=why):
                registry_ops.registry_set(dungeon, bad)
        assert len(all_events(dungeon)) == n
        # a no-op merge journals nothing
        assert registry_ops.registry_set(dungeon, {"label": "Dungeon crawler"})["no_change"]
        assert len(all_events(dungeon)) == n

    def test_registry_events_are_one_map_never_a_list(self, dungeon: Path) -> None:
        registry_ops.registry_set(dungeon, {"vocab": ["floors", "rooms"]}, actor="test")
        for ev in all_events(dungeon):
            if ev["artifact_id"] == "registry" and ev["op"] == "edit":
                assert isinstance(ev["detail"]["changed"], dict)
                assert all(set(v) == {"from", "to"} for v in ev["detail"]["changed"].values())

    def test_a_refused_registry_writing_verb_synthesizes_nothing(self, dungeon: Path) -> None:
        """Doctrine 1's order: synthesis IS a write, so it runs after the wall
        — a refused verb must not flip the pack to tier-1 resolution."""
        for call in (
            lambda: registry_ops.registry_set(dungeon, {"pack_type": "x"}),
            lambda: registry_ops.registry_set(dungeon, {"tuning": {"status": "live"}}),
            lambda: registry_ops.registry_set(dungeon, {"capabilities": {"bogus": True}}),
            lambda: db_ops.db_define(dungeon, "npc", ABILITY),
            lambda: db_ops.db_define(dungeon, "gadget", {"label": "G"}),
            lambda: db_ops.db_evolve(dungeon, "npc", rename_field="id:ident"),
            lambda: db_ops.db_evolve(dungeon, "dragon", rename_field="a:b"),
        ):
            with pytest.raises(ValueError):
                call()
            assert not (dungeon / ".canon").exists()
        assert resolve_pack(dungeon).source == "shape"

    def test_enable_dialogue_on_a_platformer(self, plat: Path) -> None:
        with pytest.raises(ValueError, match="needs an EntityKind named 'npc'"):
            registry_ops.registry_set(plat, {"capabilities": {"dialogue": True}})
        assert "dialogue" not in pack_info(plat)["capabilities"]
        # the seed's own precondition also answers before the synthesis
        assert not (plat / ".canon" / "registry.json").exists()
        db_ops.db_define(plat, "npc", {
            "label": "NPCs", "layout": {"mode": "collection", "path": "npcs/npcs.json", "format": "array"},
            "id_field": "id", "id_alloc": {"base": 1000},
        }, actor="test")
        result = registry_ops.registry_set(plat, {"capabilities": {"dialogue": True}}, actor="test")
        assert result["changed"]["capabilities.dialogue"] == {"from": False, "to": True}
        assert result["changed"]["dialogue"]["from"] is None
        assert result["changed"]["dialogue"]["to"]["storage"]["on"] == "npc"
        assert result["changed"]["engines[id=godot].evaluable_namespaces"]["to"] == {
            "tree": {}, "selector": {}, "scene": {}, "effects": {}, "music": {},
        }
        ev = _events(plat, "capability_set")[-1]
        assert ev["artifact_id"] == "registry"
        assert ev["detail"]["changed"]["capabilities.dialogue"] == {"from": False, "to": True}
        info = pack_info(plat)
        assert info["capabilities"] == ["grid", "dialogue"]
        assert info["dialogue"]["condition_namespaces"][0] == "has_item"
        assert info["engine_evaluable_namespaces"] == {
            "tree": {}, "selector": {}, "scene": {}, "effects": {}, "music": {},
        }
        assert registry_ops.registry_set(plat, {"capabilities": {"dialogue": True}})["no_change"]
        # the registry rebuilds through the seed overlay (fail-closed round trip)
        doc = json.loads((plat / ".canon" / "registry.json").read_text())
        spec = effective_spec(PACKS["platformer"], doc)
        assert set(spec.entities) == {"enemy", "item", "npc"} and spec.entities["enemy"].builder is not None
        assert spec.entities["npc"].loader is not None and spec.entities["npc"].loader(plat) == {}

    def test_malformed_registry_entry_is_fail_closed(self, dungeon: Path) -> None:
        registry_ops.registry_set(dungeon, {"label": "x"}, actor="test")
        path = dungeon / ".canon" / "registry.json"
        doc = json.loads(path.read_text())
        doc["entities"]["npc"]["bogus_field"] = 1
        path.write_text(json.dumps(doc))
        with pytest.raises(Exception, match="unknown field"):
            resolve_pack(dungeon)


# ---------------------------------------------------------------------------
# db define / db evolve + restore through the existing path
# ---------------------------------------------------------------------------


class TestDefineEvolve:
    def test_define_new_update_evolve_journal_and_restore(self, dungeon: Path) -> None:
        defined = db_ops.db_define(dungeon, "player_ability", ABILITY, actor="test")
        assert defined["files"] == ["schemas/player_ability.json", "abilities/abilities.json"]
        assert json.loads((dungeon / "abilities" / "abilities.json").read_text()) == []
        schema = json.loads((dungeon / "schemas" / "player_ability.json").read_text())
        assert schema["fields"]["tier"]["choices"] == [["minor", 3], ["major", 1]]
        ev = _events(dungeon, "db_define")
        assert [(e["artifact_id"], e["op"]) for e in ev] == [
            ("schema:player_ability", "create"), ("collection:player_ability", "create"), ("registry", "edit"),
        ]
        assert ev[-1]["detail"]["changed"]["entities.player_ability"]["from"] is None
        assert ev[-1]["detail"]["changed"]["entities.player_ability"]["to"]["schema"] == "schemas/player_ability.json"
        with pytest.raises(ValueError, match="already exists"):
            db_ops.db_define(dungeon, "player_ability", ABILITY)
        with pytest.raises(ValueError, match="missing"):
            db_ops.db_define(dungeon, "spell_pool", {"label": "x"})
        with pytest.raises(ValueError, match="payload"):
            db_ops.db_define(dungeon, "spell_pool", {**ABILITY, "not_a_field": 1})

        created = db_ops.new_db_row(dungeon, "player_ability", {"name": "Dash", "description": "zoom"}, actor="test")
        db_ops.update_db_row(dungeon, "player_ability", "7000", {"name": "Dash!"}, actor="test")
        pre_evolve = _events(dungeon, "db_update")[-1]["after_hash"]
        evolved = db_ops.db_evolve(dungeon, "player_ability", rename_field="description:blurb", actor="test")
        assert evolved["renamed"] == {"from": "description", "to": "blurb"}
        assert evolved["rewritten"][0]["rows"] == 1
        assert any("engine must follow" in w for w in evolved["warnings"])
        rows = json.loads((dungeon / "abilities" / "abilities.json").read_text())
        assert rows == [{"id": 7000, "tier": created["row"]["tier"], "name": "Dash!", "blurb": "zoom"}]
        entry = json.loads((dungeon / ".canon" / "registry.json").read_text())["entities"]["player_ability"]
        assert entry["llm_fields"] == ["name", "blurb"] and entry["renames"] == {"description": "blurb"}
        ev = _events(dungeon, "db_evolve")
        assert [(e["artifact_id"], e["op"]) for e in ev] == [
            ("collection:player_ability", "edit"), ("registry", "edit"),
        ]
        assert ev[-1]["detail"]["changed"] == {
            "entities.player_ability.fields": {"from": "description", "to": "blurb"},
        }
        assert resolve_pack(dungeon).spec.entities["player_ability"].llm_fields == ["name", "blurb"]
        with pytest.raises(NotYetError):
            db_ops.db_evolve(dungeon, "player_ability", rename_type="ability")
        with pytest.raises(ValueError, match="protected"):
            db_ops.db_evolve(dungeon, "player_ability", rename_field="id:ident")

        # restore: the collection is the CAS unit — the row comes back with the file
        restored = restore_asset(dungeon, "player_ability:7000", pre_evolve, actor="test")
        assert restored["kind"] == "row_restore"
        assert restored["label"] == "restores abilities/abilities.json (1 rows)"
        assert json.loads((dungeon / "abilities" / "abilities.json").read_text())[0]["description"] == "zoom"
        # and the registry restores through the same path
        registry_before = ev[-1]["before_hash"]
        restored = restore_asset(dungeon, "registry", registry_before, actor="test")
        assert restored["kind"] == "document_restore"
        entry = json.loads((dungeon / ".canon" / "registry.json").read_text())["entities"]["player_ability"]
        assert entry["llm_fields"] == ["name", "description"]
        with pytest.raises(ValueError, match="own lineage"):
            restore_asset(dungeon, "npc:1000", pre_evolve)

    def test_define_refuses_a_layout_outside_the_pack(self, dungeon: Path, tmp_path: Path) -> None:
        """`pack / "/abs"` is `/abs` in pathlib, so an unchecked layout path
        would put a kind's rows outside the pack (and outside `.canon/`)."""
        outside = tmp_path / "outside" / "rows.json"
        for layout in (
            {"mode": "collection", "path": str(outside), "format": "array"},
            {"mode": "collection", "path": "../rows.json", "format": "array"},
            {"mode": "collection", "path": "a/../../rows.json", "format": "array"},
            {"mode": "per_file", "dir": str(tmp_path / "outside")},
            {"mode": "per_file", "dir": "~/rows"},
        ):
            with pytest.raises(ValueError, match="stay inside the pack"):
                db_ops.db_define(dungeon, "gadget", {"label": "G", "id_field": "id", "layout": layout})
        assert not outside.exists() and not (tmp_path / "outside").exists()
        assert not (dungeon / ".canon").exists()
        # a relative path with a dot in a NAME is still fine
        db_ops.db_define(dungeon, "gadget", {
            "label": "G", "id_field": "id",
            "layout": {"mode": "collection", "path": "gadgets/v1.2.json", "format": "array"},
        }, actor="test")
        assert (dungeon / "gadgets" / "v1.2.json").is_file()

    def test_defined_kind_protects_its_id_field(self, dungeon: Path) -> None:
        """CORE_PROTECTED names no id field — every seeded kind adds its own,
        so a defined kind gets the same default or its id drifts out of sync
        with the filename (per_file) / the collection key."""
        db_ops.db_define(dungeon, "trap", {
            "label": "Traps", "id_field": "trap_id", "layout": {"mode": "per_file", "dir": "traps"},
        }, actor="test")
        entry = json.loads((dungeon / ".canon" / "registry.json").read_text())["entities"]["trap"]
        assert entry["protected"] == ["trap_id"]
        db_ops.new_db_row(dungeon, "trap", {"trap_id": "spike"}, actor="test")
        with pytest.raises(ValueError, match="'trap_id' is protected"):
            db_ops.update_db_row(dungeon, "trap", "spike", {"trap_id": "pit"})
        assert [p.name for p in (dungeon / "traps").iterdir()] == ["spike.json"]
        # an explicit `protected` list is respected as given
        db_ops.db_define(dungeon, "rune", {
            "label": "Runes", "id_field": "rune_id", "protected": ["rune_id", "sigil"],
            "layout": {"mode": "collection", "path": "runes/runes.json", "format": "array"},
        }, actor="test")
        assert json.loads(
            (dungeon / ".canon" / "registry.json").read_text()
        )["entities"]["rune"]["protected"] == ["rune_id", "sigil"]

    def test_evolve_refuses_a_field_the_seed_model_declares(self, plat: Path) -> None:
        """`renames` moves the name on disk; a Pydantic-modeled kind still
        DECLARES the old one, so the next `db update` would re-add it with the
        model's default and the row would carry both."""
        with pytest.raises(ValueError, match="declared field of EnemyDefinition"):
            db_ops.db_evolve(plat, "enemy", rename_field="rarity:scarcity")
        row = json.loads(sorted((plat / "enemy").glob("*.json"))[0].read_text())
        assert "rarity" in row and "scarcity" not in row

    def test_off_table_warnings_follow_a_rename(self, dungeon: Path) -> None:
        """The skeleton keeps its ROLL name after `db evolve`; the disk leaf is
        translated back before the roll-table lookup."""
        db_ops.db_define(dungeon, "player_ability", ABILITY, actor="test")
        db_ops.new_db_row(dungeon, "player_ability", {"name": "Dash"}, actor="test")
        before = db_ops.update_db_row(dungeon, "player_ability", "7000", {"tier": "legendary"}, actor="test")
        assert any("outside the roll table" in w for w in before["warnings"])
        db_ops.db_evolve(dungeon, "player_ability", rename_field="tier:grade", actor="test")
        after = db_ops.update_db_row(dungeon, "player_ability", "7000", {"grade": "mythic"}, actor="test")
        assert any("grade='mythic' is outside the roll table" in w for w in after["warnings"])

    def test_evolve_a_seed_kind_rewrites_every_row(self, dungeon: Path) -> None:
        result = db_ops.db_evolve(dungeon, "npc", rename_field="hobby:pastime", actor="test")
        assert result["rewritten"][0]["rows"] == len(json.loads((dungeon / "npcs" / "npcs.json").read_text()))
        rows = json.loads((dungeon / "npcs" / "npcs.json").read_text())
        assert "pastime" in rows[0] and "hobby" not in rows[0]
        entry = json.loads((dungeon / ".canon" / "registry.json").read_text())["entities"]["npc"]
        assert "pastime" in entry["llm_fields"] and "hobby" not in entry["llm_fields"]
        assert entry["renames"]["hobby"] == "pastime"
        renamed = db_ops.update_db_row(dungeon, "npc", "1000", {"pastime": "knitting"}, actor="test")
        assert renamed["changed"]["pastime"]["to"] == "knitting"


# ---------------------------------------------------------------------------
# world update (P.7)
# ---------------------------------------------------------------------------


class TestWorldUpdate:
    def test_dungeon_mirrors_addresses_and_wall(self, dungeon: Path) -> None:
        result = world_ops.update_world(dungeon, {
            "story.title": "New Title",
            "story.beats.room_0.summary": "Fresh beat",
            "story.key_npc_names": ["A", "B"],
            "narrative.room_intro_room_1": "hello",
        }, actor="test", session="conv-2")
        assert set(result["changed"]) == {
            "story.title", "story.beats.room_0.summary", "story.key_npc_names", "narrative.room_intro_room_1",
        }
        assert [(f["file"], f["artifact_id"], f.get("mirror_of")) for f in result["files"]] == [
            ("world_bible.json", "world", None),
            ("manifest.json", "manifest", "world"),
            ("narrative.json", "narrative", None),
        ]
        assert any("story/story.json is absent" in w for w in result["warnings"])  # legacy tree: no story dir
        bible = json.loads((dungeon / "world_bible.json").read_text())
        assert bible["story"]["title"] == "New Title" and bible["story"]["beats"][0]["summary"] == "Fresh beat"
        assert bible["story"]["key_npc_names"] == ["A", "B"]
        assert json.loads((dungeon / "manifest.json").read_text())["story_title"] == "New Title"
        assert json.loads((dungeon / "narrative.json").read_text())["room_intro_room_1"] == "hello"
        events = [e for e in all_events(dungeon) if e["detail"]["kind"] == "world_update"]
        assert [(e["artifact_id"], e["session"]) for e in events] == [
            ("world", "conv-2"), ("manifest", "conv-2"), ("narrative", "conv-2"),
        ]
        assert events[1]["detail"]["mirror_of"] == "world"
        assert events[1]["detail"]["changed"] == {"story.title": {"from": "The Silent Gospel", "to": "New Title"}}
        # a list of scalars is one leaf
        assert events[0]["detail"]["changed"]["story.key_npc_names"]["to"] == ["A", "B"]
        n = len(all_events(dungeon))
        for bad, why in (
            ({"story.beats[0].summary": "x"}, "numeric index"),
            ({"story.beats.0.summary": "x"}, "numeric index"),
            ({"story.faction_id": 1}, "protected"),
            ({"story.seed": "x"}, "protected"),
            ({"num_rooms": 1}, "protected"),
            ({"story.beats.room_99.summary": "x"}, "unknown room"),
            ({"story.nope": "x"}, "unknown world field"),
        ):
            with pytest.raises(ValueError, match=why):
                world_ops.update_world(dungeon, bad)
        assert len(all_events(dungeon)) == n
        # restore `world` through the existing path
        restore_asset(dungeon, "world", events[0]["before_hash"], actor="test")
        assert json.loads((dungeon / "world_bible.json").read_text())["story"]["title"] == "The Silent Gospel"

    def test_platformer_title_container_and_validation(self, plat: Path) -> None:
        result = world_ops.update_world(plat, {"title": "Blue World", "unlock_rules.type": "gated"}, actor="test")
        assert result["changed"]["title"]["to"] == "Blue World"
        assert result["changed"]["unlock_rules.type"]["to"] == "gated"
        world = json.loads((plat / "world.json").read_text())
        assert world["title"] == "Blue World" and world["unlock_rules"]["type"] == "gated"
        assert world["status"] == "user_edited"
        manifest = json.loads((plat / "manifest.json").read_text())
        assert manifest["world"] == "Blue World" and manifest["unlock"]["type"] == "gated"
        assert list(manifest)[0] == "pack_type"
        assert [f["artifact_id"] for f in result["files"]] == ["world", "manifest"]
        with pytest.raises(ValueError, match="container"):
            world_ops.update_world(plat, {"unlock_rules": {"type": "x"}})
        with pytest.raises(ValueError, match="protected"):
            world_ops.update_world(plat, {"stage_ids": []})
        with pytest.raises(ValueError, match="protected"):
            world_ops.update_world(plat, {"map_nodes": {}})
        with pytest.raises(Exception, match="title"):
            world_ops.update_world(plat, {"title": ["not", "a", "string"]})  # World model: fail-closed
        assert world_ops.update_world(plat, {"title": "Blue World"})["no_change"]

    def test_world_new_name_routes_through_the_core(self, tmp_path: Path) -> None:
        out = tmp_path / "named"
        code, doc = _canon("world", "new", str(out), "--name", "Journaled World", "--seed", "p06")
        assert code == 0, doc
        world = json.loads((out / "world.json").read_text())
        assert world["title"] == "Journaled World"
        # R13 routes the naming through the journaled core; it does NOT stamp
        # the world as human-corrected — a generated world is born `pending`,
        # and only an explicit `world update` flips it
        assert world["status"] == "pending"
        assert json.loads((out / "manifest.json").read_text())["world"] == "Journaled World"
        events = [e for e in all_events(out) if e["detail"]["kind"] == "world_update"]
        assert [e["artifact_id"] for e in events] == ["world", "manifest"] and events[0]["actor"] == "user"
        code, doc = _canon("world", "update", str(out), "--set", '{"title": "CLI World"}', "--actor", "cli")
        assert code == 0 and doc["changed"]["title"]["to"] == "CLI World"
        assert json.loads((out / "world.json").read_text())["status"] == "user_edited"
        code, doc = _canon("world", "update", str(out), "--set", '{"seed": "x"}')
        assert code != 0 and "protected" in doc["error"]


# ---------------------------------------------------------------------------
# Grid verbs dispatch (adapters' GRID_EDITORS / GRID_IMPORTERS)
# ---------------------------------------------------------------------------


class TestGridVerbs:
    def test_grid_and_level_forms_share_one_writer(self, plat_pack: Path, tmp_path: Path) -> None:
        a, b = tmp_path / "a", tmp_path / "b"
        shutil.copytree(plat_pack, a)
        shutil.copytree(plat_pack, b)
        level = json.loads(next((a / "level").glob("*/*/level.json")).read_text())
        edit = json.dumps({"spawn": [level["spawn"][0] + 1, level["spawn"][1]]})
        lid = level["level_id"]
        code_a, doc_a = _canon("grid", "apply-edit", str(a), "--level", lid, "--json", edit, "--actor", "t")
        code_b, doc_b = _canon("level", "apply-edit", str(b), "--level", lid, "--json", edit, "--actor", "t")
        assert code_a == 0 and code_b == 0, (doc_a, doc_b)
        assert _tree(a) == _tree(b) and _sans_ts(all_events(a)) == _sans_ts(all_events(b))
        assert all_events(a)  # journaled
        code, doc = _canon("grid", "apply-edit", str(a), "--level", level["level_id"])
        assert code != 0 and "--json" in doc["error"]

    def test_room_grid_writes_route_to_the_room_writer(self, dungeon: Path) -> None:
        """Row P0-8 filled the ``room`` entries this test pinned as a
        structured "not yet": both verbs now reach ``dungeon_write`` and
        refuse on their OWN rules (a 1×1 collision cannot resize a room; a
        marker cannot land on a wall), never on a missing dispatch entry."""
        for verb, payload, reason in (
            ("apply-edit", '{"spawn": [0, 0]}', "wall"),
            ("import-grids", '{"collision": [[0]]}', "cannot be resized"),
        ):
            code, doc = _canon("grid", verb, str(dungeon), "--level", "room_0", "--json", payload)
            assert code != 0, doc
            assert "row" not in doc and reason in doc["error"], doc
        assert not (dungeon / ".canon").exists(), "a refused write adopts nothing"


# ---------------------------------------------------------------------------
# Success criterion 6 — zero code changes
# ---------------------------------------------------------------------------


def _transcript_step(lines: list[str], *args: str) -> object:
    code, doc = _canon(*args)
    lines.append("$ canon " + " ".join(a if " " not in a else repr(a) for a in args))
    lines.append(json.dumps(doc, indent=1)[:600] if isinstance(doc, (dict, list)) else str(doc))
    assert code == 0, doc
    return doc


def test_success_criterion_6_add_field_and_define_type(plat: Path) -> None:
    """In an existing project: add a field to enemies via ``db schema --set``
    and define a net-new ``player_ability`` type via ``db define`` — its rows
    then browsable (``pack info`` / ``db types``) and editable (``db new`` /
    ``db update``) with zero code changes. Printed as the demo transcript
    (``pytest -s``)."""
    lines: list[str] = []
    schema = _transcript_step(
        lines, "db", "schema", str(plat), "--type", "enemy", "--actor", "demo",
        "--set", '{"fields": {"temperament": {"choices": [["calm", 3], ["feral", 1]]}}}',
    )
    assert schema["source"] == "pack" and "temperament" in schema["schema"]["fields"]
    defined = _transcript_step(
        lines, "db", "define", str(plat), "--type", "player_ability", "--actor", "demo", "--set", json.dumps(ABILITY),
    )
    assert defined["files"] == ["schemas/player_ability.json", "abilities/abilities.json"]
    created = _transcript_step(
        lines, "db", "new", str(plat), "--type", "player_ability", "--actor", "demo", "--fields", '{"name": "Dash"}',
    )
    assert created["id"] == 7000 and created["row"]["name"] == "Dash"
    updated = _transcript_step(
        lines, "db", "update", str(plat), "--type", "player_ability", "--id", "7000", "--actor", "demo",
        "--set", '{"description": "A quick burst of speed", "tier": "major"}',
    )
    assert updated["changed"]["description"]["to"] == "A quick burst of speed"
    types = _transcript_step(lines, "db", "types", str(plat))
    assert "player_ability" in types["types"] and types["types"]["enemy"]["schema_source"] == "pack"
    assert any(f["name"] == "temperament" for f in types["types"]["enemy"]["skeleton_fields"])
    info = _transcript_step(lines, "pack", "info", str(plat))
    assert info["entities"]["player_ability"] == {
        "label": "Abilities", "id_field": "id",
        "layout": {"mode": "collection", "path": "abilities/abilities.json", "format": "array"},
        "count": 1, "placeable": False, "schema_source": "pack",
    }
    assert info["source"] == "registry"
    history = _transcript_step(lines, "level", "history", str(plat))
    kinds = [e["detail"]["kind"] for e in history["events"]]
    assert kinds == ["db_schema", "registry_synthesize", "db_define", "db_define", "db_define", "db_new", "db_update"]
    # zero code changes: the platformer rows still generate byte-identically
    rolled = ops.new_db_row(plat, "enemy", {}, actor="demo")
    assert rolled["row"]["archetype"] in ("patroller", "sentry", "swimmer", "flyer", "hopper")
    print("\n".join(["", "=== success criterion 6 — demo transcript ===", *lines]))
