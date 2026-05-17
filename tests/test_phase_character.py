"""Tests for CharacterPhase.

Covers:
- per_map=True with N maps generates count*N characters, each bound to its map_id
- per_map=False generates exactly `count` global characters with primary_map_id=None
- Multiple roles cycle: roles=("npc","merchant"), count=2, one of each per map
- Each character has a generation_trail
- `count` callable returns dynamic count per map
- Phase protocol satisfaction (has name + run)
- "characters" appears in metadata.phases_run
- Fallback character is added when LLM responses are all invalid
- Empty maps with per_map=True produces no characters (safe no-op)
- LLM-supplied character_id is preserved; auto-allocation fires when absent
"""

from __future__ import annotations

import json
import random

from canon.backends import FakeLLMBackend
from canon.bible.models import Bible, Map, StoryArc
from canon.llm import LLMClient
from canon.llm.prompts import DefaultPromptSet
from canon.pipeline.phases.character import CharacterPhase
from canon.pipeline.runner import Phase, PipelineContext

# ---------------------------------------------------------------------------
# Minimal helpers
# ---------------------------------------------------------------------------


def _make_bible(*map_ids: str) -> Bible:
    """Return a Bible pre-populated with one Map per map_id."""
    bible = Bible.empty(seed="test_seed")
    bible.story = StoryArc(title="Test World", synopsis="A test.", seed="test_seed")
    for mid in map_ids:
        bible.maps[mid] = Map(
            map_id=mid,
            name=f"Map {mid}",
            description="A location.",
            environment="generic",
            story_beat="Intro.",
        )
    return bible


class FakeConfig:
    seed = "test_seed"
    num_maps = 2
    max_retries = 3


def _char_json(**overrides) -> str:
    """Return a JSON string that passes CharacterPhase validation."""
    data = {
        "name": "Alira the Wanderer",
        "lore": "A traveller from distant lands who seeks purpose.",
        "personality": "Calm and curious.",
        "appearance": "Tall, with silver hair.",
    }
    data.update(overrides)
    return json.dumps(data)


def _make_ctx(
    bible: Bible,
    responses: list[str] | None = None,
    schemas: dict | None = None,
) -> PipelineContext:
    """Build a PipelineContext wired to a FakeLLMBackend."""
    if responses is None:
        # Default: infinite valid responses via a callable backend
        fake_backend = FakeLLMBackend(lambda _req: _char_json())
    else:
        fake_backend = FakeLLMBackend(responses)

    llm = LLMClient(backend=fake_backend)
    return PipelineContext(
        bible=bible,
        config=FakeConfig(),
        rng=random.Random(42),
        llm=llm,
        prompts=DefaultPromptSet(),
        schemas=schemas or {},
    )


# ---------------------------------------------------------------------------
# Phase protocol
# ---------------------------------------------------------------------------


class TestCharacterPhaseProtocol:
    def test_satisfies_phase_protocol(self):
        phase = CharacterPhase()
        assert isinstance(phase, Phase)

    def test_name_attribute(self):
        assert CharacterPhase.name == "characters"
        assert CharacterPhase().name == "characters"


# ---------------------------------------------------------------------------
# per_map=True
# ---------------------------------------------------------------------------


class TestCharacterPhasePerMap:
    def test_two_maps_count_2_yields_four_characters(self):
        bible = _make_bible("map_0", "map_1")
        ctx = _make_ctx(bible)
        CharacterPhase(per_map=True, count=2).run(ctx)
        assert len(bible.characters) == 4

    def test_three_maps_count_3_yields_nine_characters(self):
        bible = _make_bible("map_0", "map_1", "map_2")
        ctx = _make_ctx(bible)
        CharacterPhase(per_map=True, count=3).run(ctx)
        assert len(bible.characters) == 9

    def test_each_character_bound_to_its_map(self):
        bible = _make_bible("map_0", "map_1")
        ctx = _make_ctx(bible)
        CharacterPhase(per_map=True, count=2).run(ctx)
        map0_chars = [c for c in bible.characters if c.primary_map_id == "map_0"]
        map1_chars = [c for c in bible.characters if c.primary_map_id == "map_1"]
        assert len(map0_chars) == 2
        assert len(map1_chars) == 2

    def test_empty_maps_produces_no_characters(self):
        """per_map=True with an empty maps dict is a safe no-op."""
        bible = _make_bible()  # no maps
        ctx = _make_ctx(bible)
        CharacterPhase(per_map=True, count=2).run(ctx)
        assert len(bible.characters) == 0

    def test_count_one_per_map(self):
        bible = _make_bible("map_0", "map_1", "map_2")
        ctx = _make_ctx(bible)
        CharacterPhase(per_map=True, count=1).run(ctx)
        assert len(bible.characters) == 3


# ---------------------------------------------------------------------------
# per_map=False (global cast)
# ---------------------------------------------------------------------------


class TestCharacterPhaseGlobal:
    def test_global_generates_exact_count(self):
        bible = _make_bible("map_0", "map_1")  # maps exist but are irrelevant
        ctx = _make_ctx(bible)
        CharacterPhase(per_map=False, count=5).run(ctx)
        assert len(bible.characters) == 5

    def test_global_characters_have_no_map(self):
        bible = _make_bible("map_0")
        ctx = _make_ctx(bible)
        CharacterPhase(per_map=False, count=3).run(ctx)
        assert all(c.primary_map_id is None for c in bible.characters)

    def test_global_with_no_maps(self):
        """Global generation doesn't require any maps to be present."""
        bible = _make_bible()
        ctx = _make_ctx(bible)
        CharacterPhase(per_map=False, count=2).run(ctx)
        assert len(bible.characters) == 2

    def test_global_count_zero_produces_no_characters(self):
        bible = _make_bible()
        ctx = _make_ctx(bible)
        CharacterPhase(per_map=False, count=0).run(ctx)
        assert len(bible.characters) == 0


# ---------------------------------------------------------------------------
# Role cycling
# ---------------------------------------------------------------------------


class TestCharacterPhaseRoles:
    def test_single_role_all_characters_share_role(self):
        bible = _make_bible("map_0")
        ctx = _make_ctx(bible)
        CharacterPhase(per_map=True, count=3, roles=("npc",)).run(ctx)
        assert all(c.role == "npc" for c in bible.characters)

    def test_two_roles_cycle_per_map(self):
        """roles=("npc","merchant"), count=2, one of each per map."""
        bible = _make_bible("map_0")
        ctx = _make_ctx(bible)
        CharacterPhase(per_map=True, count=2, roles=("npc", "merchant")).run(ctx)
        roles_seen = {c.role for c in bible.characters}
        assert roles_seen == {"npc", "merchant"}

    def test_two_roles_two_maps_four_characters(self):
        bible = _make_bible("map_0", "map_1")
        ctx = _make_ctx(bible)
        CharacterPhase(per_map=True, count=2, roles=("npc", "merchant")).run(ctx)
        assert len(bible.characters) == 4
        npc_count = sum(1 for c in bible.characters if c.role == "npc")
        merchant_count = sum(1 for c in bible.characters if c.role == "merchant")
        assert npc_count == 2
        assert merchant_count == 2

    def test_roles_cycle_wraps_correctly(self):
        """roles=("npc","merchant"), count=4 → npc,merchant,npc,merchant."""
        bible = _make_bible("map_0")
        ctx = _make_ctx(bible)
        CharacterPhase(per_map=True, count=4, roles=("npc", "merchant")).run(ctx)
        expected_roles = ["npc", "merchant", "npc", "merchant"]
        actual_roles = [c.role for c in bible.characters]
        assert actual_roles == expected_roles

    def test_global_role_cycling(self):
        bible = _make_bible()
        ctx = _make_ctx(bible)
        CharacterPhase(per_map=False, count=3, roles=("npc", "merchant", "boss")).run(ctx)
        assert [c.role for c in bible.characters] == ["npc", "merchant", "boss"]


# ---------------------------------------------------------------------------
# Callable count
# ---------------------------------------------------------------------------


class TestCharacterPhaseCallableCount:
    def test_callable_count_per_map(self):
        """Count callable receives the map object; returns varying counts."""
        bible = _make_bible("map_0", "map_1")
        # map_0 → 1 character, map_1 → 3 characters
        def dynamic_count(map_obj):
            return 1 if map_obj.map_id == "map_0" else 3

        ctx = _make_ctx(bible)
        CharacterPhase(per_map=True, count=dynamic_count).run(ctx)
        map0_chars = [c for c in bible.characters if c.primary_map_id == "map_0"]
        map1_chars = [c for c in bible.characters if c.primary_map_id == "map_1"]
        assert len(map0_chars) == 1
        assert len(map1_chars) == 3

    def test_callable_count_global(self):
        """Callable count is called with None for global generation."""
        called_with = []

        def dynamic_count(map_obj):
            called_with.append(map_obj)
            return 2

        bible = _make_bible()
        ctx = _make_ctx(bible)
        CharacterPhase(per_map=False, count=dynamic_count).run(ctx)
        assert len(bible.characters) == 2
        assert called_with == [None]


# ---------------------------------------------------------------------------
# GenerationTrail
# ---------------------------------------------------------------------------


class TestCharacterPhaseGenerationTrail:
    def test_each_character_has_generation_trail(self):
        bible = _make_bible("map_0")
        ctx = _make_ctx(bible)
        CharacterPhase(per_map=True, count=2).run(ctx)
        for char in bible.characters:
            assert char.generation_trail is not None

    def test_generation_trail_has_prompt_and_response(self):
        bible = _make_bible("map_0")
        ctx = _make_ctx(bible)
        CharacterPhase(per_map=True, count=1).run(ctx)
        trail = bible.characters[0].generation_trail
        assert isinstance(trail.prompt, str) and trail.prompt != ""
        assert isinstance(trail.response, str) and trail.response != ""


# ---------------------------------------------------------------------------
# metadata.phases_run
# ---------------------------------------------------------------------------


class TestCharacterPhaseMetadata:
    def test_phases_run_contains_characters(self):
        bible = _make_bible("map_0")
        ctx = _make_ctx(bible)
        CharacterPhase(per_map=True, count=1).run(ctx)
        assert "characters" in bible.metadata.phases_run

    def test_phases_run_appended_even_with_no_maps(self):
        """phases_run is updated even when per_map=True but maps is empty."""
        bible = _make_bible()
        ctx = _make_ctx(bible)
        CharacterPhase(per_map=True, count=2).run(ctx)
        assert "characters" in bible.metadata.phases_run

    def test_phases_run_appended_once(self):
        bible = _make_bible("map_0")
        ctx = _make_ctx(bible)
        CharacterPhase(per_map=True, count=1).run(ctx)
        assert bible.metadata.phases_run.count("characters") == 1


# ---------------------------------------------------------------------------
# Fallback behaviour
# ---------------------------------------------------------------------------


class TestCharacterPhaseFallback:
    def test_fallback_character_added_when_all_responses_invalid(self):
        """All LLM responses are invalid JSON → fallback character is still added."""
        bible = _make_bible("map_0")
        # Every response is invalid JSON
        bad_responses = ["not json"] * 20  # enough for retries
        ctx = _make_ctx(bible, responses=bad_responses)
        phase = CharacterPhase(per_map=True, count=1, roles=("npc",))
        phase.run(ctx)
        # The fallback produces a character named "Character 0"
        assert len(bible.characters) == 1
        assert bible.characters[0].name == "Character 0"
        assert bible.characters[0].lore == "An unwritten figure."

    def test_fallback_character_has_correct_map_id(self):
        bible = _make_bible("dungeon_1")
        bad_responses = ["{}"] * 20
        ctx = _make_ctx(bible, responses=bad_responses)
        CharacterPhase(per_map=True, count=1).run(ctx)
        assert bible.characters[0].primary_map_id == "dungeon_1"

    def test_fallback_character_global_has_no_map_id(self):
        bible = _make_bible()
        bad_responses = ["{}"] * 20
        ctx = _make_ctx(bible, responses=bad_responses)
        CharacterPhase(per_map=False, count=1).run(ctx)
        assert bible.characters[0].primary_map_id is None

    def test_fallback_still_records_phases_run(self):
        bible = _make_bible("map_0")
        bad_responses = ["bad json"] * 20
        ctx = _make_ctx(bible, responses=bad_responses)
        CharacterPhase(per_map=True, count=1).run(ctx)
        assert "characters" in bible.metadata.phases_run


# ---------------------------------------------------------------------------
# character_id allocation
# ---------------------------------------------------------------------------


class TestCharacterIdAllocation:
    def test_llm_supplied_character_id_is_preserved(self):
        bible = _make_bible("map_0")
        response = json.dumps({
            "character_id": "my_custom_id",
            "name": "Elara",
            "lore": "A seasoned guard.",
        })
        ctx = _make_ctx(bible, responses=[response])
        CharacterPhase(per_map=True, count=1).run(ctx)
        assert bible.characters[0].character_id == "my_custom_id"

    def test_auto_allocated_id_when_llm_omits_it(self):
        bible = _make_bible("map_0")
        ctx = _make_ctx(bible)
        CharacterPhase(per_map=True, count=1).run(ctx)
        assert bible.characters[0].character_id == "char_0000"

    def test_auto_allocation_is_globally_unique_across_maps(self):
        """Two maps × 2 characters = char_0000 … char_0003, no duplicates."""
        bible = _make_bible("map_0", "map_1")
        ctx = _make_ctx(bible)
        CharacterPhase(per_map=True, count=2).run(ctx)
        ids = [c.character_id for c in bible.characters]
        assert len(ids) == len(set(ids)), "character_ids must be unique"
        assert ids == ["char_0000", "char_0001", "char_0002", "char_0003"]


# ---------------------------------------------------------------------------
# Character fields populated from LLM response
# ---------------------------------------------------------------------------


class TestCharacterFieldPopulation:
    def test_name_and_lore_from_response(self):
        bible = _make_bible("map_0")
        response = json.dumps({"name": "Tova", "lore": "An old healer."})
        ctx = _make_ctx(bible, responses=[response])
        CharacterPhase(per_map=True, count=1).run(ctx)
        char = bible.characters[0]
        assert char.name == "Tova"
        assert char.lore == "An old healer."

    def test_backstory_accepted_as_lore_alias(self):
        bible = _make_bible("map_0")
        response = json.dumps({"name": "Renn", "backstory": "A former soldier."})
        ctx = _make_ctx(bible, responses=[response])
        CharacterPhase(per_map=True, count=1).run(ctx)
        assert bible.characters[0].lore == "A former soldier."

    def test_optional_fields_populated_when_present(self):
        bible = _make_bible("map_0")
        response = json.dumps({
            "name": "Kessa",
            "lore": "A scout.",
            "personality": "Bold.",
            "appearance": "Short, dark hair.",
            "faction_id": "rebels",
        })
        ctx = _make_ctx(bible, responses=[response])
        CharacterPhase(per_map=True, count=1).run(ctx)
        char = bible.characters[0]
        assert char.personality == "Bold."
        assert char.appearance == "Short, dark hair."
        assert char.faction_id == "rebels"

    def test_optional_fields_are_none_when_absent(self):
        bible = _make_bible("map_0")
        response = json.dumps({"name": "Wren", "lore": "Unknown origins."})
        ctx = _make_ctx(bible, responses=[response])
        CharacterPhase(per_map=True, count=1).run(ctx)
        char = bible.characters[0]
        assert char.personality is None
        assert char.appearance is None
        assert char.faction_id is None


# ---------------------------------------------------------------------------
# v0.2 new Character fields: job, hobby, opening_greeting, etc.
# ---------------------------------------------------------------------------


class TestCharacterPhaseNewFields:
    """Verify v0.2 CharacterPhase populates the new mazeworld-shaped fields."""

    def _rich_response(self, **overrides) -> str:
        data = {
            "name": "Jorik Steelknuckle",
            "lore": "A pit fighter turned smuggler.",
            "job": "former pit fighter turned reluctant smuggler",
            "hobby": "mapping guard rotations",
            "opening_greeting": "You look like someone who can handle themselves.",
            "portrait_prompt": "a stocky man with scarred knuckles, pixel art",
            "personality_notes": ["Gruff but protective", "Distrusts magic"],
            "exhausted_dialogue": "That's all the planning I can handle for now.",
        }
        data.update(overrides)
        return json.dumps(data)

    def test_job_populated_from_response(self):
        bible = _make_bible("map_0")
        ctx = _make_ctx(bible, responses=[self._rich_response()])
        CharacterPhase(per_map=True, count=1).run(ctx)
        assert bible.characters[0].job == "former pit fighter turned reluctant smuggler"

    def test_hobby_populated_from_response(self):
        bible = _make_bible("map_0")
        ctx = _make_ctx(bible, responses=[self._rich_response()])
        CharacterPhase(per_map=True, count=1).run(ctx)
        assert bible.characters[0].hobby == "mapping guard rotations"

    def test_opening_greeting_populated_from_response(self):
        bible = _make_bible("map_0")
        ctx = _make_ctx(bible, responses=[self._rich_response()])
        CharacterPhase(per_map=True, count=1).run(ctx)
        assert bible.characters[0].opening_greeting == "You look like someone who can handle themselves."

    def test_portrait_prompt_populated_from_response(self):
        bible = _make_bible("map_0")
        ctx = _make_ctx(bible, responses=[self._rich_response()])
        CharacterPhase(per_map=True, count=1).run(ctx)
        assert bible.characters[0].portrait_prompt == "a stocky man with scarred knuckles, pixel art"

    def test_personality_notes_populated_as_list(self):
        bible = _make_bible("map_0")
        ctx = _make_ctx(bible, responses=[self._rich_response()])
        CharacterPhase(per_map=True, count=1).run(ctx)
        char = bible.characters[0]
        assert isinstance(char.personality_notes, list)
        assert "Gruff but protective" in char.personality_notes

    def test_exhausted_dialogue_populated_from_response(self):
        bible = _make_bible("map_0")
        ctx = _make_ctx(bible, responses=[self._rich_response()])
        CharacterPhase(per_map=True, count=1).run(ctx)
        assert bible.characters[0].exhausted_dialogue == "That's all the planning I can handle for now."

    def test_new_fields_default_to_none_when_absent(self):
        """When the LLM response omits new fields, they default to None / empty list."""
        bible = _make_bible("map_0")
        response = json.dumps({"name": "Bare Minimum", "lore": "Just a person."})
        ctx = _make_ctx(bible, responses=[response])
        CharacterPhase(per_map=True, count=1).run(ctx)
        char = bible.characters[0]
        assert char.job is None
        assert char.hobby is None
        assert char.opening_greeting is None
        assert char.portrait_prompt is None
        assert char.personality_notes == []
        assert char.exhausted_dialogue is None

    def test_personality_notes_as_string_coerced_to_list(self):
        """If the LLM returns personality_notes as a string, it's wrapped in a list."""
        bible = _make_bible("map_0")
        response = json.dumps({
            "name": "Nina",
            "lore": "A healer.",
            "personality_notes": "Compassionate and quiet.",
        })
        ctx = _make_ctx(bible, responses=[response])
        CharacterPhase(per_map=True, count=1).run(ctx)
        char = bible.characters[0]
        assert isinstance(char.personality_notes, list)
        assert len(char.personality_notes) == 1
        assert char.personality_notes[0] == "Compassionate and quiet."


# ---------------------------------------------------------------------------
# v0.2 cross-room name deduplication
# ---------------------------------------------------------------------------


class TestCrossRoomDedup:
    """Verify names are deduplicated across rooms."""

    def test_same_name_in_two_maps_gets_renamed(self):
        """When the LLM returns the same name twice across two maps, the second gets a suffix."""
        bible = _make_bible("map_0", "map_1")
        # Both maps get a character named "Duplicate Dan"
        responses = [
            json.dumps({"name": "Duplicate Dan", "lore": "First."}),
            json.dumps({"name": "Duplicate Dan", "lore": "Second."}),
        ]
        ctx = _make_ctx(bible, responses=responses)
        CharacterPhase(per_map=True, count=1).run(ctx)
        names = [c.name for c in bible.characters]
        assert len(set(names)) == 2, f"Expected unique names, got: {names}"

    def test_unique_names_across_maps_not_renamed(self):
        """When the LLM returns distinct names, they are preserved as-is."""
        bible = _make_bible("map_0", "map_1")
        responses = [
            json.dumps({"name": "Alara", "lore": "Unique."}),
            json.dumps({"name": "Brynn", "lore": "Also unique."}),
        ]
        ctx = _make_ctx(bible, responses=responses)
        CharacterPhase(per_map=True, count=1).run(ctx)
        names = [c.name for c in bible.characters]
        assert "Alara" in names
        assert "Brynn" in names

    def test_dedup_hint_sent_in_prompt_after_first_character(self):
        """After the first character is generated, existing names appear in subsequent prompts."""
        bible = _make_bible("map_0", "map_1")
        responses = [
            json.dumps({"name": "First One", "lore": "L1."}),
            json.dumps({"name": "Second One", "lore": "L2."}),
        ]
        ctx = _make_ctx(bible, responses=responses)
        CharacterPhase(per_map=True, count=1).run(ctx)
        # The second LLM call should have "First One" somewhere in the prompt.
        second_call = ctx.llm.backend.calls[1]
        assert "First One" in second_call.user_message

    def test_global_generation_dedup_works(self):
        """Cross-dedup also works for per_map=False global generation."""
        bible = _make_bible()
        responses = [
            json.dumps({"name": "Duplicate Dan", "lore": "One."}),
            json.dumps({"name": "Duplicate Dan", "lore": "Two."}),
        ]
        ctx = _make_ctx(bible, responses=responses)
        CharacterPhase(per_map=False, count=2).run(ctx)
        names = [c.name for c in bible.characters]
        assert len(set(names)) == 2, f"Expected unique names globally, got: {names}"
