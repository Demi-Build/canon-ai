"""Tests for PromptSet ABC and DefaultPromptSet.

Covers:
  - PromptSet has exactly 31 abstract methods (17 original + 14 added in v0.2).
  - DefaultPromptSet instantiates without error.
  - Every method returns an LLMRequest.
  - Verbatim context / skeleton / feedback strings appear in the output.
  - Genre-neutrality: no forbidden words in system or user_message.
  - entity_type token changes the user_message but not the system prompt shape.
"""

from __future__ import annotations

import pytest

from canon.bible.models import Bible, Character, Map, StoryArc
from canon.llm.prompts import DefaultPromptSet, PromptSet
from canon.llm.request import LLMRequest

# ---------------------------------------------------------------------------
# Forbidden genre-specific words (must not appear in any default prompt output)
# ---------------------------------------------------------------------------

FORBIDDEN_WORDS = {
    "fantasy",
    "dungeon",
    "magic",
    "warrior",
    "wizard",
    "sword",
    "elf",
    "dragon",
}

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def ps() -> DefaultPromptSet:
    return DefaultPromptSet()


@pytest.fixture()
def simple_bible() -> Bible:
    """Minimal Bible instance for narrative methods."""
    bible = Bible.empty(seed="test_seed")
    bible.story = StoryArc(
        title="Test World",
        synopsis="A test world for unit tests.",
        seed="test_seed",
    )
    bible.maps["map_1"] = Map(
        map_id="map_1",
        name="Test Location",
        description="A nondescript test location.",
        environment="test",
        story_beat="Opening area.",
    )
    return bible


@pytest.fixture()
def simple_character() -> Character:
    """Minimal Character instance for dialogue methods."""
    return Character(
        character_id="char_1",
        name="Test NPC",
        role="npc",
        lore="A character created for tests.",
        personality="Helpful and neutral.",
    )


# ---------------------------------------------------------------------------
# 1. ABC contract
# ---------------------------------------------------------------------------


class TestPromptSetABC:
    def test_abstract_method_count(self) -> None:
        """PromptSet declares exactly 31 abstract methods (17 original + 14 v0.2)."""
        abstract_methods = PromptSet.__abstractmethods__
        assert len(abstract_methods) == 31, (
            f"Expected 31, got {len(abstract_methods)}: {sorted(abstract_methods)}"
        )

    def test_expected_method_names(self) -> None:
        """All 31 expected method names are present on PromptSet."""
        expected = {
            # original 17
            "story_generation",
            "story_generation_with_feedback",
            "map_generation",
            "map_generation_with_feedback",
            "character_generation",
            "character_generation_with_feedback",
            "class_archetype_generation",
            "class_archetype_generation_with_feedback",
            "entity_generation",
            "entity_generation_with_feedback",
            "dialogue_tree_generation",
            "dialogue_tree_generation_with_feedback",
            "narrative_synopsis",
            "map_intro",
            "victory_text",
            "defeat_text",
            "climax_entity_generation",
            # v0.2 additions (14 new)
            "npc_generation",
            "npc_generation_with_feedback",
            "monster_generation",
            "monster_generation_with_feedback",
            "item_generation",
            "item_generation_with_feedback",
            "event_generation",
            "event_generation_with_feedback",
            "quest_generation",
            "quest_generation_with_feedback",
            "spell_generation",
            "spell_generation_with_feedback",
            "ability_generation",
            "ability_generation_with_feedback",
        }
        assert PromptSet.__abstractmethods__ == expected

    def test_cannot_instantiate_abc(self) -> None:
        """PromptSet cannot be instantiated directly."""
        with pytest.raises(TypeError):
            PromptSet()  # type: ignore[abstract]

    def test_default_promptset_instantiates(self) -> None:
        """DefaultPromptSet can be instantiated without arguments."""
        ps = DefaultPromptSet()
        assert ps is not None


# ---------------------------------------------------------------------------
# 2. Return type — every method returns LLMRequest
# ---------------------------------------------------------------------------


class TestReturnTypes:
    def test_story_generation_returns_llmrequest(self, ps: DefaultPromptSet) -> None:
        result = ps.story_generation(seed="test_seed")
        assert isinstance(result, LLMRequest)

    def test_story_generation_with_feedback_returns_llmrequest(
        self, ps: DefaultPromptSet
    ) -> None:
        result = ps.story_generation_with_feedback("seed", None, ["too short"])
        assert isinstance(result, LLMRequest)

    def test_map_generation_returns_llmrequest(self, ps: DefaultPromptSet) -> None:
        result = ps.map_generation(story_context="context", map_index=1)
        assert isinstance(result, LLMRequest)

    def test_map_generation_with_feedback_returns_llmrequest(
        self, ps: DefaultPromptSet
    ) -> None:
        result = ps.map_generation_with_feedback("ctx", 1, None, ["bad name"])
        assert isinstance(result, LLMRequest)

    def test_character_generation_returns_llmrequest(
        self, ps: DefaultPromptSet
    ) -> None:
        result = ps.character_generation(context="ctx", skeleton={}, role="npc")
        assert isinstance(result, LLMRequest)

    def test_character_generation_with_feedback_returns_llmrequest(
        self, ps: DefaultPromptSet
    ) -> None:
        result = ps.character_generation_with_feedback("ctx", {}, "npc", ["no name"])
        assert isinstance(result, LLMRequest)

    def test_class_archetype_generation_returns_llmrequest(
        self, ps: DefaultPromptSet
    ) -> None:
        result = ps.class_archetype_generation(context="ctx")
        assert isinstance(result, LLMRequest)

    def test_class_archetype_generation_with_feedback_returns_llmrequest(
        self, ps: DefaultPromptSet
    ) -> None:
        result = ps.class_archetype_generation_with_feedback("ctx", None, ["missing lore"])
        assert isinstance(result, LLMRequest)

    def test_entity_generation_returns_llmrequest(self, ps: DefaultPromptSet) -> None:
        result = ps.entity_generation("tower", "ctx", {"damage": 10})
        assert isinstance(result, LLMRequest)

    def test_entity_generation_with_feedback_returns_llmrequest(
        self, ps: DefaultPromptSet
    ) -> None:
        result = ps.entity_generation_with_feedback("tower", "ctx", {}, ["missing name"])
        assert isinstance(result, LLMRequest)

    def test_dialogue_tree_generation_returns_llmrequest(
        self, ps: DefaultPromptSet, simple_character: Character
    ) -> None:
        result = ps.dialogue_tree_generation(simple_character, "ctx")
        assert isinstance(result, LLMRequest)

    def test_dialogue_tree_generation_with_feedback_returns_llmrequest(
        self, ps: DefaultPromptSet, simple_character: Character
    ) -> None:
        result = ps.dialogue_tree_generation_with_feedback(
            simple_character, "ctx", 5, ["no choices"]
        )
        assert isinstance(result, LLMRequest)

    def test_narrative_synopsis_returns_llmrequest(
        self, ps: DefaultPromptSet, simple_bible: Bible
    ) -> None:
        result = ps.narrative_synopsis(simple_bible)
        assert isinstance(result, LLMRequest)

    def test_map_intro_returns_llmrequest(
        self, ps: DefaultPromptSet, simple_bible: Bible
    ) -> None:
        result = ps.map_intro(simple_bible, "map_1")
        assert isinstance(result, LLMRequest)

    def test_victory_text_returns_llmrequest(
        self, ps: DefaultPromptSet, simple_bible: Bible
    ) -> None:
        result = ps.victory_text(simple_bible)
        assert isinstance(result, LLMRequest)

    def test_defeat_text_returns_llmrequest(
        self, ps: DefaultPromptSet, simple_bible: Bible
    ) -> None:
        result = ps.defeat_text(simple_bible)
        assert isinstance(result, LLMRequest)

    def test_climax_entity_generation_returns_llmrequest(
        self, ps: DefaultPromptSet, simple_bible: Bible
    ) -> None:
        result = ps.climax_entity_generation(simple_bible)
        assert isinstance(result, LLMRequest)


# ---------------------------------------------------------------------------
# 3. Verbatim context embedding
# ---------------------------------------------------------------------------


class TestContextEmbedding:
    CONTEXT = "UNIQUE_CONTEXT_STRING_XYZ"

    def test_map_generation_embeds_context(self, ps: DefaultPromptSet) -> None:
        result = ps.map_generation(self.CONTEXT, 1)
        assert self.CONTEXT in result.user_message

    def test_character_generation_embeds_context(self, ps: DefaultPromptSet) -> None:
        result = ps.character_generation(self.CONTEXT, {}, "npc")
        assert self.CONTEXT in result.user_message

    def test_class_archetype_generation_embeds_context(
        self, ps: DefaultPromptSet
    ) -> None:
        result = ps.class_archetype_generation(self.CONTEXT)
        assert self.CONTEXT in result.user_message

    def test_entity_generation_embeds_context(self, ps: DefaultPromptSet) -> None:
        result = ps.entity_generation("item", self.CONTEXT, {})
        assert self.CONTEXT in result.user_message

    def test_dialogue_tree_generation_embeds_context(
        self, ps: DefaultPromptSet, simple_character: Character
    ) -> None:
        result = ps.dialogue_tree_generation(simple_character, self.CONTEXT)
        assert self.CONTEXT in result.user_message


# ---------------------------------------------------------------------------
# 4. Skeleton embedding
# ---------------------------------------------------------------------------


class TestSkeletonEmbedding:
    SKELETON = {"damage": 42, "range": 3, "type": "UNIQUE_SKEL_VAL"}

    def test_entity_generation_embeds_skeleton(self, ps: DefaultPromptSet) -> None:
        result = ps.entity_generation("weapon", "ctx", self.SKELETON)
        # repr of the dict or its values should appear
        assert "UNIQUE_SKEL_VAL" in result.user_message or str(self.SKELETON) in result.user_message

    def test_character_generation_embeds_skeleton(self, ps: DefaultPromptSet) -> None:
        result = ps.character_generation("ctx", self.SKELETON, "npc")
        assert "UNIQUE_SKEL_VAL" in result.user_message or str(self.SKELETON) in result.user_message

    def test_entity_feedback_embeds_skeleton(self, ps: DefaultPromptSet) -> None:
        result = ps.entity_generation_with_feedback("weapon", "ctx", self.SKELETON, [])
        assert "UNIQUE_SKEL_VAL" in result.user_message or str(self.SKELETON) in result.user_message


# ---------------------------------------------------------------------------
# 5. Feedback variants — feedback reasons in prompt body
# ---------------------------------------------------------------------------


class TestFeedbackEmbedding:
    REASONS = ["name too short", "lore contradicts the setting"]

    def _assert_feedback(self, result: LLMRequest) -> None:
        for reason in self.REASONS:
            assert reason in result.user_message, (
                f"Feedback reason not found in prompt: {reason!r}"
            )

    def test_story_with_feedback(self, ps: DefaultPromptSet) -> None:
        self._assert_feedback(
            ps.story_generation_with_feedback("seed", None, self.REASONS)
        )

    def test_map_with_feedback(self, ps: DefaultPromptSet) -> None:
        self._assert_feedback(
            ps.map_generation_with_feedback("ctx", 1, None, self.REASONS)
        )

    def test_character_with_feedback(self, ps: DefaultPromptSet) -> None:
        self._assert_feedback(
            ps.character_generation_with_feedback("ctx", {}, "npc", self.REASONS)
        )

    def test_class_archetype_with_feedback(self, ps: DefaultPromptSet) -> None:
        self._assert_feedback(
            ps.class_archetype_generation_with_feedback("ctx", None, self.REASONS)
        )

    def test_entity_with_feedback(self, ps: DefaultPromptSet) -> None:
        self._assert_feedback(
            ps.entity_generation_with_feedback("tower", "ctx", {}, self.REASONS)
        )

    def test_dialogue_with_feedback(
        self, ps: DefaultPromptSet, simple_character: Character
    ) -> None:
        self._assert_feedback(
            ps.dialogue_tree_generation_with_feedback(
                simple_character, "ctx", 5, self.REASONS
            )
        )


# ---------------------------------------------------------------------------
# 6. Genre-neutrality — no forbidden words in any prompt template
# ---------------------------------------------------------------------------


def _all_prompts(ps: DefaultPromptSet, character: Character, bible: Bible) -> list[LLMRequest]:
    """Return one LLMRequest per method using generic/empty inputs."""
    return [
        ps.story_generation(seed="test"),
        ps.story_generation_with_feedback("test", None, []),
        ps.map_generation(story_context="", map_index=0),
        ps.map_generation_with_feedback("", 0, None, []),
        ps.character_generation(context="", skeleton={}, role="npc"),
        ps.character_generation_with_feedback("", {}, "npc", []),
        ps.class_archetype_generation(context=""),
        ps.class_archetype_generation_with_feedback("", None, []),
        ps.entity_generation("item", "", {}),
        ps.entity_generation_with_feedback("item", "", {}, []),
        ps.dialogue_tree_generation(character, ""),
        ps.dialogue_tree_generation_with_feedback(character, "", 3, []),
        ps.narrative_synopsis(bible),
        ps.map_intro(bible, "map_1"),
        ps.victory_text(bible),
        ps.defeat_text(bible),
        ps.climax_entity_generation(bible),
    ]


class TestGenreNeutrality:
    def test_no_forbidden_words_in_system_prompts(
        self,
        ps: DefaultPromptSet,
        simple_character: Character,
        simple_bible: Bible,
    ) -> None:
        prompts = _all_prompts(ps, simple_character, simple_bible)
        for req in prompts:
            system_lower = req.system.lower()
            for word in FORBIDDEN_WORDS:
                assert word not in system_lower, (
                    f"Forbidden word {word!r} found in system prompt: {req.system!r}"
                )

    def test_no_forbidden_words_in_user_messages(
        self,
        ps: DefaultPromptSet,
        simple_character: Character,
        simple_bible: Bible,
    ) -> None:
        prompts = _all_prompts(ps, simple_character, simple_bible)
        for req in prompts:
            user_lower = req.user_message.lower()
            for word in FORBIDDEN_WORDS:
                assert word not in user_lower, (
                    f"Forbidden word {word!r} found in user_message: {req.user_message!r}"
                )


# ---------------------------------------------------------------------------
# 7. entity_type token — system prompt shape stays constant
# ---------------------------------------------------------------------------


class TestEntityTypeToken:
    def test_system_prompt_identical_for_different_entity_types(
        self, ps: DefaultPromptSet
    ) -> None:
        """System prompt must not change when entity_type changes."""
        req_tower = ps.entity_generation("tower_defense_unit", "ctx", {})
        req_item = ps.entity_generation("crafting_recipe", "ctx", {})
        assert req_tower.system == req_item.system

    def test_entity_type_appears_in_user_message(self, ps: DefaultPromptSet) -> None:
        """The entity_type token must appear in the user_message."""
        entity_type = "tower_defense_unit"
        result = ps.entity_generation(entity_type, "ctx", {})
        assert entity_type in result.user_message

    def test_entity_type_changes_user_message(self, ps: DefaultPromptSet) -> None:
        """Different entity_type values produce different user_messages."""
        req_tower = ps.entity_generation("tower_defense_unit", "ctx", {})
        req_item = ps.entity_generation("crafting_recipe", "ctx", {})
        assert req_tower.user_message != req_item.user_message


# ---------------------------------------------------------------------------
# 8. With-feedback preserves system prompt and adds feedback section
# ---------------------------------------------------------------------------


class TestFeedbackVariantShape:
    def test_entity_feedback_preserves_system_prompt(
        self, ps: DefaultPromptSet
    ) -> None:
        base = ps.entity_generation("item", "ctx", {})
        with_fb = ps.entity_generation_with_feedback("item", "ctx", {}, ["reason A"])
        assert base.system == with_fb.system

    def test_story_feedback_preserves_system_prompt(
        self, ps: DefaultPromptSet
    ) -> None:
        base = ps.story_generation("seed")
        with_fb = ps.story_generation_with_feedback("seed", None, ["reason B"])
        assert base.system == with_fb.system

    def test_character_feedback_preserves_system_prompt(
        self, ps: DefaultPromptSet
    ) -> None:
        base = ps.character_generation("ctx", {}, "npc")
        with_fb = ps.character_generation_with_feedback("ctx", {}, "npc", ["reason C"])
        assert base.system == with_fb.system

    def test_feedback_user_message_longer_than_base(
        self, ps: DefaultPromptSet
    ) -> None:
        """The _with_feedback user_message must be longer than the base."""
        base = ps.entity_generation("item", "ctx", {})
        with_fb = ps.entity_generation_with_feedback("item", "ctx", {}, ["reason A"])
        assert len(with_fb.user_message) > len(base.user_message)


# ---------------------------------------------------------------------------
# 9. max_tokens sanity — all methods stay under 1000 tokens budget
# ---------------------------------------------------------------------------


class TestMaxTokens:
    def test_max_tokens_positive(
        self,
        ps: DefaultPromptSet,
        simple_character: Character,
        simple_bible: Bible,
    ) -> None:
        for req in _all_prompts(ps, simple_character, simple_bible):
            assert req.max_tokens > 0

    def test_max_tokens_reasonable_ceiling(
        self,
        ps: DefaultPromptSet,
        simple_character: Character,
        simple_bible: Bible,
    ) -> None:
        """No method should request more than 1024 tokens."""
        for req in _all_prompts(ps, simple_character, simple_bible):
            assert req.max_tokens <= 1024, (
                f"max_tokens={req.max_tokens} exceeds 1024 for system={req.system[:60]!r}"
            )
