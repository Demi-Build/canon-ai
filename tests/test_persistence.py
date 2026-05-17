"""Contract tests for canon.persistence file-shape helpers.

Tests verify the file shapes produced by each writer — the observable
boundary that mazeworld's registry.py and cradle's data.rs consume.

Uses pytest's ``tmp_path`` fixture for filesystem isolation.
No re-implementation of the writer logic.
"""

import json
from pathlib import Path

from canon.persistence import (
    write_array_db,
    write_keyed_db,
    write_per_map_file,
    write_singleton,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _raw(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# write_array_db
# ---------------------------------------------------------------------------

class TestWriteArrayDb:
    def test_produces_json_array(self, tmp_path: Path) -> None:
        dest = tmp_path / "entities.json"
        write_array_db(dest, [{"id": 1}, {"id": 2}])
        data = _load(dest)
        assert isinstance(data, list)

    def test_array_length_matches_input(self, tmp_path: Path) -> None:
        dest = tmp_path / "entities.json"
        write_array_db(dest, [{"id": 1}, {"id": 2}])
        assert len(_load(dest)) == 2

    def test_array_contents_preserved(self, tmp_path: Path) -> None:
        dest = tmp_path / "entities.json"
        entities = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
        write_array_db(dest, entities)
        data = _load(dest)
        assert data[0]["name"] == "Alice"
        assert data[1]["id"] == 2

    def test_empty_list_produces_empty_array(self, tmp_path: Path) -> None:
        dest = tmp_path / "empty.json"
        write_array_db(dest, [])
        assert _load(dest) == []

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        dest = tmp_path / "a" / "b" / "c" / "npcs.json"
        write_array_db(dest, [{"id": 1}])
        assert dest.exists()

    def test_uses_two_space_indent(self, tmp_path: Path) -> None:
        dest = tmp_path / "npcs.json"
        write_array_db(dest, [{"id": 1}])
        raw = _raw(dest)
        # 2-space indent means array element lines start with two spaces
        lines = raw.splitlines()
        # At least one line has exactly 2-space indent (not 4-space)
        assert any(ln.startswith("  ") for ln in lines)

    def test_accepts_pydantic_model(self, tmp_path: Path) -> None:
        from pydantic import BaseModel

        class Thing(BaseModel):
            id: int
            label: str

        dest = tmp_path / "things.json"
        write_array_db(dest, [Thing(id=10, label="x"), Thing(id=11, label="y")])
        data = _load(dest)
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["label"] == "x"


# ---------------------------------------------------------------------------
# write_keyed_db
# ---------------------------------------------------------------------------

class TestWriteKeyedDb:
    def test_produces_json_object(self, tmp_path: Path) -> None:
        dest = tmp_path / "items.json"
        write_keyed_db(dest, [{"id": 2000, "name": "a"}, {"id": 2001, "name": "b"}], key_field="id")
        data = _load(dest)
        assert isinstance(data, dict)

    def test_keys_are_stringified_ids(self, tmp_path: Path) -> None:
        dest = tmp_path / "items.json"
        write_keyed_db(dest, [{"id": 2000, "name": "a"}, {"id": 2001, "name": "b"}], key_field="id")
        data = _load(dest)
        assert "2000" in data
        assert "2001" in data

    def test_values_contain_entity_data(self, tmp_path: Path) -> None:
        dest = tmp_path / "items.json"
        write_keyed_db(dest, [{"id": 2000, "name": "sword"}], key_field="id")
        data = _load(dest)
        assert data["2000"]["name"] == "sword"

    def test_accepts_dict_directly(self, tmp_path: Path) -> None:
        dest = tmp_path / "monsters.json"
        pre_keyed = {"5000": {"name": "goblin"}, "5001": {"name": "orc"}}
        write_keyed_db(dest, pre_keyed, key_field="id")
        data = _load(dest)
        assert set(data.keys()) == {"5000", "5001"}
        assert data["5000"]["name"] == "goblin"

    def test_empty_list_produces_empty_object(self, tmp_path: Path) -> None:
        dest = tmp_path / "items.json"
        write_keyed_db(dest, [], key_field="id")
        assert _load(dest) == {}

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        dest = tmp_path / "deep" / "nested" / "items.json"
        write_keyed_db(dest, [{"id": 2000}], key_field="id")
        assert dest.exists()

    def test_uses_two_space_indent(self, tmp_path: Path) -> None:
        dest = tmp_path / "items.json"
        write_keyed_db(dest, [{"id": 2000, "name": "a"}], key_field="id")
        raw = _raw(dest)
        assert "  " in raw

    def test_accepts_pydantic_model(self, tmp_path: Path) -> None:
        from pydantic import BaseModel

        class Item(BaseModel):
            id: int
            name: str

        dest = tmp_path / "items.json"
        write_keyed_db(dest, [Item(id=2000, name="blade")], key_field="id")
        data = _load(dest)
        assert "2000" in data
        assert data["2000"]["name"] == "blade"

    def test_custom_key_field(self, tmp_path: Path) -> None:
        dest = tmp_path / "items.json"
        entities = [{"item_id": 99, "name": "potion"}]
        write_keyed_db(dest, entities, key_field="item_id")
        data = _load(dest)
        assert "99" in data


# ---------------------------------------------------------------------------
# write_singleton
# ---------------------------------------------------------------------------

class TestWriteSingleton:
    def test_round_trips_dict(self, tmp_path: Path) -> None:
        dest = tmp_path / "manifest.json"
        original = {"a": 1, "b": [1, 2, 3]}
        write_singleton(dest, original)
        assert _load(dest) == original

    def test_produces_json_object_not_array(self, tmp_path: Path) -> None:
        dest = tmp_path / "story.json"
        write_singleton(dest, {"title": "The Dark Hour"})
        data = _load(dest)
        assert isinstance(data, dict)

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        dest = tmp_path / "x" / "y" / "z" / "narrative.json"
        write_singleton(dest, {"text": "hello"})
        assert dest.exists()

    def test_uses_two_space_indent(self, tmp_path: Path) -> None:
        dest = tmp_path / "manifest.json"
        write_singleton(dest, {"key": "value", "nested": {"a": 1}})
        raw = _raw(dest)
        assert "  " in raw

    def test_accepts_pydantic_model(self, tmp_path: Path) -> None:
        from pydantic import BaseModel

        class Manifest(BaseModel):
            version: str
            seed: int

        dest = tmp_path / "manifest.json"
        write_singleton(dest, Manifest(version="0.2", seed=42))
        data = _load(dest)
        assert data["version"] == "0.2"
        assert data["seed"] == 42

    def test_nested_structures_preserved(self, tmp_path: Path) -> None:
        dest = tmp_path / "world_bible.json"
        payload = {
            "maps": [{"id": "room_0", "level": 1}],
            "settings": {"difficulty": "normal"},
        }
        write_singleton(dest, payload)
        data = _load(dest)
        assert data["maps"][0]["id"] == "room_0"
        assert data["settings"]["difficulty"] == "normal"


# ---------------------------------------------------------------------------
# write_per_map_file
# ---------------------------------------------------------------------------

class TestWritePerMapFile:
    def test_creates_file_at_formatted_path(self, tmp_path: Path) -> None:
        template = str(tmp_path / "rooms" / "{map_id}" / "maze.json")
        write_per_map_file(template, "room_0", {"grid": []})
        expected = tmp_path / "rooms" / "room_0" / "maze.json"
        assert expected.exists()

    def test_different_map_ids_create_different_paths(self, tmp_path: Path) -> None:
        template = str(tmp_path / "rooms" / "{map_id}" / "maze.json")
        write_per_map_file(template, "room_0", {"level": 1})
        write_per_map_file(template, "room_1", {"level": 2})
        assert (tmp_path / "rooms" / "room_0" / "maze.json").exists()
        assert (tmp_path / "rooms" / "room_1" / "maze.json").exists()

    def test_file_contents_are_singleton_json(self, tmp_path: Path) -> None:
        template = str(tmp_path / "rooms" / "{map_id}" / "maze.json")
        payload = {"grid": [[0, 1], [1, 0]], "door_position": [1, 0]}
        write_per_map_file(template, "room_0", payload)
        data = _load(tmp_path / "rooms" / "room_0" / "maze.json")
        assert data["door_position"] == [1, 0]

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        template = str(tmp_path / "deep" / "rooms" / "{map_id}" / "maze.json")
        write_per_map_file(template, "room_42", {"x": 1})
        assert (tmp_path / "deep" / "rooms" / "room_42" / "maze.json").exists()

    def test_uses_two_space_indent(self, tmp_path: Path) -> None:
        template = str(tmp_path / "rooms" / "{map_id}" / "maze.json")
        write_per_map_file(template, "room_0", {"a": {"b": 1}})
        raw = _raw(tmp_path / "rooms" / "room_0" / "maze.json")
        assert "  " in raw

    def test_accepts_pydantic_model(self, tmp_path: Path) -> None:
        from pydantic import BaseModel

        class MazeFile(BaseModel):
            environment: str
            level: int

        template = str(tmp_path / "rooms" / "{map_id}" / "maze.json")
        write_per_map_file(template, "room_0", MazeFile(environment="ruins", level=1))
        data = _load(tmp_path / "rooms" / "room_0" / "maze.json")
        assert data["environment"] == "ruins"
