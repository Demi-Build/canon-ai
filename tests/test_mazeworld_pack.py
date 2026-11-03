"""Unit tests for src/canon/packs/dungeon/phases.py — MazeworldManifestPhase.

Tests verify the observable output shape after field renames and type coercions.
No re-implementation of the phase logic.  Uses a fixture bible with known
story/room structure.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from canon import (
    Bible,
    CanonConfig,
    GenerationStats,
    Map,
    PipelineContext,
    StoryArc,
)
from canon.bible.models import (
    ClassArchetype,
    EntityLore,
    Faction,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _make_ctx(tmp_path: Path, *, num_maps: int = 2, seed: str = "test_seed") -> PipelineContext:
    """Build a minimal PipelineContext with story, rooms, and archetypes."""
    bible = Bible.empty(seed=seed)
    bible.story = StoryArc(
        title="Test Story",
        synopsis="A test.",
        seed=seed,
        factions=[
            Faction(
                faction_id="the_order",
                name="The Order",
                description="An ancient cult.",
                history="Old and cruel.",
                leader="The Whisper",
            )
        ],
        final_entity_id="boss_whisper",
        final_entity_lore="Controls the void.",
        key_character_names=["Hero", "Mentor"],
        beats=[
            {"map_id": f"room_{i}", "beat": f"Beat {i}", "boss_name": f"Boss{i}", "boss_lore": "lore"}
            for i in range(num_maps)
        ],
    )
    for i in range(num_maps):
        bible.maps[f"room_{i}"] = Map(
            map_id=f"room_{i}",
            name=f"Room {i}",
            description="",
            environment="ruins",
            level=i + 1,
            story_beat=f"beat {i}",
        )
    bible.class_archetypes["warrior"] = ClassArchetype(
        archetype_id="warrior",
        name="Warrior",
    )
    config = CanonConfig(seed=seed, output_dir=str(tmp_path))
    return PipelineContext(
        bible=bible,
        config=config,
        rng=random.Random(42),
        stats=GenerationStats(),
    )


def _add_entity(
    bible: Bible, map_id: str, entity_type: str, name: str = "X"
) -> EntityLore:
    entity = EntityLore(
        entity_id=f"{entity_type}_{len(bible.maps[map_id].entities):04d}",
        entity_type=entity_type,
        name=name,
        lore="lore",
        map_id=map_id,
    )
    bible.maps[map_id].entities.append(entity)
    return entity


# ---------------------------------------------------------------------------
# Import smoke
# ---------------------------------------------------------------------------


def test_mazeworld_manifest_phase_importable():
    from canon.packs.dungeon.phases import MazeworldManifestPhase  # noqa: F401


def test_mazeworld_manifest_phase_is_subclass_of_base():
    from canon.packs.dungeon.phases import MazeworldManifestPhase
    from canon.pipeline.phases.manifest import ManifestPhase

    assert issubclass(MazeworldManifestPhase, ManifestPhase)


def test_mazeworld_manifest_phase_name_is_manifest():
    from canon.packs.dungeon.phases import MazeworldManifestPhase

    assert MazeworldManifestPhase.name == "manifest"


# ---------------------------------------------------------------------------
# Fix 1 — seed coercion
# ---------------------------------------------------------------------------


class TestMazeworldSeedCoercion:
    def test_coerce_int_seed_unchanged(self):
        from canon.packs.dungeon.phases import MazeworldManifestPhase

        assert MazeworldManifestPhase._coerce_seed_to_int(42) == 42

    def test_coerce_numeric_string(self):
        from canon.packs.dungeon.phases import MazeworldManifestPhase

        assert MazeworldManifestPhase._coerce_seed_to_int("1234") == 1234

    def test_coerce_non_numeric_string_is_stable_int(self):
        from canon.packs.dungeon.phases import MazeworldManifestPhase

        result = MazeworldManifestPhase._coerce_seed_to_int("shadowspire_001")
        assert isinstance(result, int)
        # Must be deterministic: same input → same output
        assert result == MazeworldManifestPhase._coerce_seed_to_int("shadowspire_001")

    def test_coerce_different_strings_produce_different_ints(self):
        from canon.packs.dungeon.phases import MazeworldManifestPhase

        a = MazeworldManifestPhase._coerce_seed_to_int("seed_a")
        b = MazeworldManifestPhase._coerce_seed_to_int("seed_b")
        assert a != b, "Different seed strings should produce different ints"

    def test_coerce_fallback_for_unknown_type(self):
        from canon.packs.dungeon.phases import MazeworldManifestPhase

        assert MazeworldManifestPhase._coerce_seed_to_int(None) == 0

    def test_manifest_seed_is_int(self, tmp_path: Path):
        from canon.packs.dungeon.phases import MazeworldManifestPhase

        ctx = _make_ctx(tmp_path, seed="shadowspire_001")
        MazeworldManifestPhase().run(ctx)
        data = _load(tmp_path / "manifest.json")
        assert isinstance(data["seed"], int), (
            f"manifest.json 'seed' must be int, got {type(data['seed'])}"
        )

    def test_manifest_seed_is_deterministic(self, tmp_path: Path):
        from canon.packs.dungeon.phases import MazeworldManifestPhase

        ctx = _make_ctx(tmp_path, seed="shadowspire_001")
        MazeworldManifestPhase().run(ctx)
        first = _load(tmp_path / "manifest.json")["seed"]

        ctx2 = _make_ctx(tmp_path, seed="shadowspire_001")
        MazeworldManifestPhase().run(ctx2)
        second = _load(tmp_path / "manifest.json")["seed"]

        assert first == second, "Seed coercion must be deterministic"


# ---------------------------------------------------------------------------
# Fix 5 — world_bible.json field renames
# ---------------------------------------------------------------------------


class TestMazeworldWorldBibleFieldRenames:
    def test_world_bible_has_entity_index(self, tmp_path: Path):
        from canon.packs.dungeon.phases import MazeworldManifestPhase

        ctx = _make_ctx(tmp_path)
        _add_entity(ctx.bible, "room_0", "npc", "Guard")
        MazeworldManifestPhase().run(ctx)
        wb = _load(tmp_path / "world_bible.json")
        assert "entity_index" in wb, "world_bible.json missing 'entity_index'"
        assert isinstance(wb["entity_index"], dict)

    def test_world_bible_entity_index_is_empty_for_mazeworld_parity(self, tmp_path: Path):
        """entity_index must be {} to match MazeWorld production behavior.

        MazeWorld declares entity_index as "Legacy compat" and never populates
        it in the production pipeline.  Cradle and MazeWorld both use the
        per-room entity lists (rooms[].npcs, rooms[].monsters, etc.) as the
        authoritative catalog.  Emitting {} here matches that contract.
        """
        from canon.packs.dungeon.phases import MazeworldManifestPhase

        ctx = _make_ctx(tmp_path)
        # Add entities so there's something to index — should still be empty
        _add_entity(ctx.bible, "room_0", "npc", "Stormguard")
        _add_entity(ctx.bible, "room_0", "monster", "Shadowbeast")
        MazeworldManifestPhase().run(ctx)
        wb = _load(tmp_path / "world_bible.json")
        assert wb["entity_index"] == {}, (
            f"entity_index must be empty (MazeWorld parity), got {wb['entity_index']}"
        )

    def test_world_bible_has_player_classes(self, tmp_path: Path):
        from canon.packs.dungeon.phases import MazeworldManifestPhase

        ctx = _make_ctx(tmp_path)
        MazeworldManifestPhase().run(ctx)
        wb = _load(tmp_path / "world_bible.json")
        assert "player_classes" in wb, "world_bible.json missing 'player_classes'"
        assert isinstance(wb["player_classes"], list)
        assert len(wb["player_classes"]) == 1  # one archetype added in fixture

    def test_story_factions_renamed_to_faction(self, tmp_path: Path):
        from canon.packs.dungeon.phases import MazeworldManifestPhase

        ctx = _make_ctx(tmp_path)
        MazeworldManifestPhase().run(ctx)
        wb = _load(tmp_path / "world_bible.json")
        story = wb["story"]
        assert "faction" in story, "story missing 'faction' (renamed from 'factions')"
        assert "factions" not in story, "story should not have 'factions' (renamed to 'faction')"
        assert isinstance(story["faction"], dict)
        assert story["faction"]["name"] == "The Order"

    def test_story_final_entity_id_renamed(self, tmp_path: Path):
        from canon.packs.dungeon.phases import MazeworldManifestPhase

        ctx = _make_ctx(tmp_path)
        MazeworldManifestPhase().run(ctx)
        wb = _load(tmp_path / "world_bible.json")
        story = wb["story"]
        assert "final_boss_name" in story, "story missing 'final_boss_name'"
        assert "final_entity_id" not in story, "story should not have 'final_entity_id'"
        assert story["final_boss_name"] == "boss_whisper"

    def test_story_final_entity_lore_renamed(self, tmp_path: Path):
        from canon.packs.dungeon.phases import MazeworldManifestPhase

        ctx = _make_ctx(tmp_path)
        MazeworldManifestPhase().run(ctx)
        wb = _load(tmp_path / "world_bible.json")
        story = wb["story"]
        assert "final_boss_lore" in story, "story missing 'final_boss_lore'"
        assert "final_entity_lore" not in story, "story should not have 'final_entity_lore'"

    def test_story_key_character_names_renamed(self, tmp_path: Path):
        from canon.packs.dungeon.phases import MazeworldManifestPhase

        ctx = _make_ctx(tmp_path)
        MazeworldManifestPhase().run(ctx)
        wb = _load(tmp_path / "world_bible.json")
        story = wb["story"]
        assert "key_npc_names" in story, "story missing 'key_npc_names'"
        assert "key_character_names" not in story, "story should not have 'key_character_names'"
        assert "Hero" in story["key_npc_names"]

    def test_story_beats_map_id_renamed_to_room_id(self, tmp_path: Path):
        from canon.packs.dungeon.phases import MazeworldManifestPhase

        ctx = _make_ctx(tmp_path, num_maps=2)
        MazeworldManifestPhase().run(ctx)
        wb = _load(tmp_path / "world_bible.json")
        for i, beat in enumerate(wb["story"]["beats"]):
            assert "room_id" in beat, f"beat[{i}] missing 'room_id'"
            assert "map_id" not in beat, f"beat[{i}] should not have 'map_id'"

    def test_story_beats_beat_renamed_to_summary(self, tmp_path: Path):
        from canon.packs.dungeon.phases import MazeworldManifestPhase

        ctx = _make_ctx(tmp_path, num_maps=2)
        MazeworldManifestPhase().run(ctx)
        wb = _load(tmp_path / "world_bible.json")
        for i, beat in enumerate(wb["story"]["beats"]):
            assert "summary" in beat, f"beat[{i}] missing 'summary'"
            assert "beat" not in beat, f"beat[{i}] should not have 'beat'"

    def test_story_beats_have_faction_presence_and_escalation(self, tmp_path: Path):
        from canon.packs.dungeon.phases import MazeworldManifestPhase

        ctx = _make_ctx(tmp_path, num_maps=2)
        MazeworldManifestPhase().run(ctx)
        wb = _load(tmp_path / "world_bible.json")
        for i, beat in enumerate(wb["story"]["beats"]):
            assert "faction_presence" in beat, f"beat[{i}] missing 'faction_presence'"
            assert "escalation" in beat, f"beat[{i}] missing 'escalation'"
            assert isinstance(beat["escalation"], int), f"beat[{i}] 'escalation' must be int"

    def test_story_has_story_level_stubs(self, tmp_path: Path):
        from canon.packs.dungeon.phases import MazeworldManifestPhase

        ctx = _make_ctx(tmp_path)
        MazeworldManifestPhase().run(ctx)
        wb = _load(tmp_path / "world_bible.json")
        story = wb["story"]
        assert "story_npcs" in story
        assert "story_items" in story
        assert "story_monsters" in story
        assert isinstance(story["story_npcs"], list)
        assert isinstance(story["story_items"], list)
        assert isinstance(story["story_monsters"], list)

    def test_rooms_have_encounters_not_events(self, tmp_path: Path):
        from canon.packs.dungeon.phases import MazeworldManifestPhase

        ctx = _make_ctx(tmp_path)
        _add_entity(ctx.bible, "room_0", "event", "Battle")
        MazeworldManifestPhase().run(ctx)
        wb = _load(tmp_path / "world_bible.json")
        room = wb["rooms"]["room_0"]
        assert "encounters" in room, "room missing 'encounters' (renamed from 'events')"
        assert "events" not in room, "room should not have 'events' key"
        assert len(room["encounters"]) == 1

    def test_rooms_still_have_other_entity_buckets(self, tmp_path: Path):
        from canon.packs.dungeon.phases import MazeworldManifestPhase

        ctx = _make_ctx(tmp_path)
        _add_entity(ctx.bible, "room_0", "npc")
        _add_entity(ctx.bible, "room_0", "item")
        _add_entity(ctx.bible, "room_0", "monster")
        MazeworldManifestPhase().run(ctx)
        wb = _load(tmp_path / "world_bible.json")
        room = wb["rooms"]["room_0"]
        for bucket in ("npcs", "items", "monsters", "quests"):
            assert bucket in room, f"room missing bucket '{bucket}'"


# ---------------------------------------------------------------------------
# Fix 2 — events.difficulty int coercion in parse_event
# ---------------------------------------------------------------------------


class TestParseEventDifficultyCoercion:
    def test_string_difficulty_coerced_to_int(self):
        from canon.packs.dungeon.parsers import parse_event
        from canon.pipeline.phases.database import BuildContext

        ctx = BuildContext(
            map_id="room_0",
            map_obj=None,
            skeleton={"event_type": "combat", "difficulty": "medium", "monster_count": 1},
            llm_response={"name": "Battle", "description": "Enemies lurk."},
            entity_index=0,
            allocated_id=3000,
            bible=None,
        )
        result = parse_event(ctx)
        assert isinstance(result["difficulty"], int), (
            f"difficulty must be int, got {type(result['difficulty'])}"
        )
        assert result["difficulty"] == 2  # "medium" → 2

    def test_all_string_difficulties_map_correctly(self):
        from canon.packs.dungeon.parsers import _DIFFICULTY_INT, parse_event
        from canon.pipeline.phases.database import BuildContext

        for label, expected in _DIFFICULTY_INT.items():
            ctx = BuildContext(
                map_id="room_0",
                map_obj=None,
                skeleton={"event_type": "combat", "difficulty": label, "monster_count": 1},
                llm_response={},
                entity_index=0,
                allocated_id=3000,
                bible=None,
            )
            result = parse_event(ctx)
            assert result["difficulty"] == expected, (
                f"difficulty {label!r} should map to {expected}, got {result['difficulty']}"
            )

    def test_int_difficulty_passthrough(self):
        from canon.packs.dungeon.parsers import parse_event
        from canon.pipeline.phases.database import BuildContext

        ctx = BuildContext(
            map_id="room_0",
            map_obj=None,
            skeleton={"event_type": "combat", "difficulty": 3, "monster_count": 1},
            llm_response={},
            entity_index=0,
            allocated_id=3000,
            bible=None,
        )
        result = parse_event(ctx)
        assert result["difficulty"] == 3

    def test_unknown_string_difficulty_defaults_to_2(self):
        from canon.packs.dungeon.parsers import parse_event
        from canon.pipeline.phases.database import BuildContext

        ctx = BuildContext(
            map_id="room_0",
            map_obj=None,
            skeleton={"event_type": "combat", "difficulty": "unknown_level", "monster_count": 1},
            llm_response={},
            entity_index=0,
            allocated_id=3000,
            bible=None,
        )
        result = parse_event(ctx)
        assert result["difficulty"] == 2


# ---------------------------------------------------------------------------
# Fix 4 — events.monster_ids cross-reference
# ---------------------------------------------------------------------------


class TestParseEventMonsterIds:
    def test_combat_event_has_monster_ids_field(self):
        from canon.packs.dungeon.parsers import parse_event
        from canon.pipeline.phases.database import BuildContext

        ctx = BuildContext(
            map_id="room_0",
            map_obj=None,
            skeleton={"event_type": "combat", "difficulty": "easy", "monster_count": 1},
            llm_response={"name": "Encounter"},
            entity_index=0,
            allocated_id=3000,
            bible=None,
        )
        result = parse_event(ctx)
        assert "monster_ids" in result, "combat event must have 'monster_ids'"
        assert isinstance(result["monster_ids"], list)

    def test_combat_event_monster_ids_populated_from_map(self):
        """monster_ids are derived from monsters in ctx.map_obj.entities."""
        from canon.packs.dungeon.parsers import parse_event
        from canon.pipeline.phases.database import BuildContext

        class FakeEntity:
            def __init__(self, entity_type, entity_id):
                self.entity_type = entity_type
                self.entity_id = entity_id
                self.map_id = "room_0"

        class FakeMapObj:
            def __init__(self):
                self.entities = [
                    FakeEntity("monster", "monster_0001"),
                    FakeEntity("npc", "npc_0001"),  # should be ignored
                ]
                self.layout = None
                self.environment = "ruins"
                self.level = 1

        ctx = BuildContext(
            map_id="room_0",
            map_obj=FakeMapObj(),
            skeleton={"event_type": "combat", "difficulty": "easy", "monster_count": 1},
            llm_response={"name": "Encounter"},
            entity_index=0,
            allocated_id=3000,
            bible=None,
        )
        result = parse_event(ctx)
        # monster_0001 → suffix "0001" → int 1 + 5000 = 5001
        assert 5001 in result["monster_ids"]

    def test_puzzle_event_has_no_monster_ids(self):
        from canon.packs.dungeon.parsers import parse_event
        from canon.pipeline.phases.database import BuildContext

        ctx = BuildContext(
            map_id="room_0",
            map_obj=None,
            skeleton={"event_type": "puzzle", "difficulty": "medium", "monster_count": 0},
            llm_response={"name": "Riddle", "choices": []},
            entity_index=0,
            allocated_id=3001,
            bible=None,
        )
        result = parse_event(ctx)
        assert "monster_ids" not in result, "puzzle event should not have 'monster_ids'"

    def test_combat_event_has_extra_fields(self):
        """parse_event adds x, y, room_level, time_gate, portrait_prompt, profile_image."""
        from canon.packs.dungeon.parsers import parse_event
        from canon.pipeline.phases.database import BuildContext

        ctx = BuildContext(
            map_id="room_0",
            map_obj=None,
            skeleton={"event_type": "combat", "difficulty": "easy", "monster_count": 1},
            llm_response={"name": "Battle"},
            entity_index=0,
            allocated_id=3000,
            bible=None,
        )
        result = parse_event(ctx)
        assert "x" in result
        assert "y" in result
        assert "room_level" in result
        assert "time_gate" in result
        assert "portrait_prompt" in result
        assert "profile_image" in result


# ---------------------------------------------------------------------------
# TestParseEventMonsterIds — extended (Wave 5.7 Fix 2 verification)
# ---------------------------------------------------------------------------


class TestParseEventMonsterIdsExtended:
    """Verify parse_event returns correct int monster IDs from map entities."""

    def test_two_monsters_in_map_returns_correct_ids(self):
        """With 2 monsters in ctx.map_obj.entities, parse_event samples up to
        monster_count and converts entity_id suffixes to MazeWorld int IDs."""
        from canon.packs.dungeon.parsers import parse_event
        from canon.pipeline.phases.database import BuildContext

        class FakeEntity:
            def __init__(self, entity_type, entity_id):
                self.entity_type = entity_type
                self.entity_id = entity_id
                self.map_id = "room_0"

        class FakeMapObj:
            def __init__(self):
                self.entities = [
                    FakeEntity("monster", "monster_0002"),
                    FakeEntity("monster", "monster_0007"),
                    FakeEntity("npc", "npc_0000"),  # must be ignored
                ]
                self.layout = None
                self.environment = "ruins"
                self.level = 2

        ctx = BuildContext(
            map_id="room_0",
            map_obj=FakeMapObj(),
            skeleton={"event_type": "combat", "difficulty": "hard", "monster_count": 2},
            llm_response={"name": "Ambush"},
            entity_index=0,
            allocated_id=3000,
            bible=None,
        )
        result = parse_event(ctx)
        assert "monster_ids" in result
        # monster_0002 → suffix 2 + 5000 = 5002; monster_0007 → 5007
        assert 5002 in result["monster_ids"] or 5007 in result["monster_ids"], (
            f"Expected 5002 and/or 5007 in monster_ids, got {result['monster_ids']}"
        )
        # NPC must not be included
        for mid in result["monster_ids"]:
            assert mid >= 5000, f"Unexpected monster ID {mid} (NPC leaked in?)"


# ---------------------------------------------------------------------------
# TestParseQuestGiverNpcId (Wave 5.7 Fix 3)
# ---------------------------------------------------------------------------


class TestParseQuestGiverNpcId:
    """Verify parse_quest derives giver_npc_id from the first NPC in the room."""

    def test_giver_npc_id_from_first_npc(self):
        """When map has an NPC, giver_npc_id = NPC suffix int + 1000."""
        from canon.packs.dungeon.parsers import parse_quest
        from canon.pipeline.phases.database import BuildContext

        class FakeEntity:
            def __init__(self, entity_type, entity_id):
                self.entity_type = entity_type
                self.entity_id = entity_id
                self.map_id = "room_0"

        class FakeMapObj:
            map_id = "room_0"
            def __init__(self):
                self.entities = [
                    FakeEntity("npc", "npc_0003"),
                    FakeEntity("monster", "monster_0001"),
                ]

        ctx = BuildContext(
            map_id="room_0",
            map_obj=FakeMapObj(),
            skeleton={"quest_type": "fetch", "reward_tier": 1},
            llm_response={"title": "Fetch the Relic"},
            entity_index=0,
            allocated_id=4000,
            bible=None,
        )
        result = parse_quest(ctx)
        # npc_0003 → suffix "0003" → int(3) + 1000 = 1003
        assert result["giver_npc_id"] == 1003, (
            f"Expected giver_npc_id=1003, got {result['giver_npc_id']}"
        )

    def test_giver_npc_id_none_when_no_npcs(self):
        """When no NPCs in the room, giver_npc_id must be None (not crash)."""
        from canon.packs.dungeon.parsers import parse_quest
        from canon.pipeline.phases.database import BuildContext

        class FakeEntity:
            def __init__(self, entity_type, entity_id):
                self.entity_type = entity_type
                self.entity_id = entity_id
                self.map_id = "room_0"

        class FakeMapObj:
            map_id = "room_0"
            def __init__(self):
                self.entities = [
                    FakeEntity("monster", "monster_0001"),
                ]

        ctx = BuildContext(
            map_id="room_0",
            map_obj=FakeMapObj(),
            skeleton={"quest_type": "escort", "reward_tier": 2},
            llm_response={"title": "Escort the Merchant"},
            entity_index=0,
            allocated_id=4001,
            bible=None,
        )
        result = parse_quest(ctx)
        assert result["giver_npc_id"] is None, (
            f"Expected giver_npc_id=None when no NPCs, got {result['giver_npc_id']}"
        )

    def test_giver_npc_id_none_when_no_map_obj(self):
        """With no map_obj (global quest), giver_npc_id must be None."""
        from canon.packs.dungeon.parsers import parse_quest
        from canon.pipeline.phases.database import BuildContext

        ctx = BuildContext(
            map_id=None,
            map_obj=None,
            skeleton={"quest_type": "solve", "reward_tier": 3},
            llm_response={"title": "The Ancient Puzzle"},
            entity_index=0,
            allocated_id=4002,
            bible=None,
        )
        result = parse_quest(ctx)
        assert result["giver_npc_id"] is None

    def test_quest_has_room_id_from_map_obj(self):
        """parse_quest must set room_id to map_obj.map_id."""
        from canon.packs.dungeon.parsers import parse_quest
        from canon.pipeline.phases.database import BuildContext

        class FakeMapObj:
            map_id = "room_2"
            entities = []

        ctx = BuildContext(
            map_id="room_2",
            map_obj=FakeMapObj(),
            skeleton={"quest_type": "combat", "reward_tier": 1},
            llm_response={},
            entity_index=0,
            allocated_id=4003,
            bible=None,
        )
        result = parse_quest(ctx)
        assert result["room_id"] == "room_2", (
            f"Expected room_id='room_2', got {result['room_id']!r}"
        )
