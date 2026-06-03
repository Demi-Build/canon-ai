"""Tests for the v0.2 extended PromptSet methods.

Covers:
  - DefaultPromptSet instantiates (all 14 new abstract methods are implemented).
  - Each new method returns an LLMRequest with non-empty system + user_message.
  - Skeleton dicts appear verbatim (or stringified) in user_message.
  - Role / category / type / element arguments appear in user_message.
  - _with_feedback variants include feedback reasons in user_message.
  - _with_feedback variants share the same system prompt as the base method.
  - Genre-neutrality: forbidden words absent from npc_generation output.
  - ABC compliance: all 14 new abstracts are implemented by DefaultPromptSet
    (PromptSet.__abstractmethods__ does NOT include them after DefaultPromptSet
    implements them).
"""

from __future__ import annotations

import pytest

from canon.llm.prompts import DefaultPromptSet, PromptSet
from canon.llm.request import LLMRequest

# ---------------------------------------------------------------------------
# Forbidden genre-specific words
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

# The 14 new abstract method names added in v0.2
NEW_METHOD_NAMES = {
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

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def ps() -> DefaultPromptSet:
    return DefaultPromptSet()


# ---------------------------------------------------------------------------
# 1. Instantiation — DefaultPromptSet covers all new abstract methods
# ---------------------------------------------------------------------------


class TestABCCompliance:
    def test_default_promptset_instantiates(self) -> None:
        """DefaultPromptSet can be instantiated (all abstracts are implemented)."""
        ps = DefaultPromptSet()
        assert ps is not None

    def test_new_methods_not_abstract_on_default(self) -> None:
        """None of the 14 new method names remain in PromptSet.__abstractmethods__
        after DefaultPromptSet implements them.

        We verify via DefaultPromptSet.__abstractmethods__ — if it's empty, all
        abstracts (original + v0.2) are implemented.
        """
        assert len(DefaultPromptSet.__abstractmethods__) == 0, (
            "DefaultPromptSet still has unimplemented abstract methods: "
            f"{sorted(DefaultPromptSet.__abstractmethods__)}"
        )

    def test_new_method_names_in_promptset_abc(self) -> None:
        """All 14 new method names appear in PromptSet.__abstractmethods__."""
        for name in NEW_METHOD_NAMES:
            assert name in PromptSet.__abstractmethods__, (
                f"Expected {name!r} in PromptSet.__abstractmethods__"
            )


# ---------------------------------------------------------------------------
# 2. Return type — every new method returns LLMRequest
# ---------------------------------------------------------------------------


class TestReturnTypes:
    def test_npc_generation_returns_llmrequest(self, ps: DefaultPromptSet) -> None:
        result = ps.npc_generation("ctx", {"a": 1}, "merchant")
        assert isinstance(result, LLMRequest)

    def test_npc_generation_with_feedback_returns_llmrequest(
        self, ps: DefaultPromptSet
    ) -> None:
        result = ps.npc_generation_with_feedback("ctx", {"a": 1}, "merchant", ["too short"])
        assert isinstance(result, LLMRequest)

    def test_monster_generation_returns_llmrequest(self, ps: DefaultPromptSet) -> None:
        result = ps.monster_generation("ctx", {"hp": 30}, level=2)
        assert isinstance(result, LLMRequest)

    def test_monster_generation_with_feedback_returns_llmrequest(
        self, ps: DefaultPromptSet
    ) -> None:
        result = ps.monster_generation_with_feedback("ctx", {"hp": 30}, 2, ["bad stats"])
        assert isinstance(result, LLMRequest)

    def test_item_generation_weapon_returns_llmrequest(
        self, ps: DefaultPromptSet
    ) -> None:
        result = ps.item_generation("ctx", {"weapon_type": "heavy"}, "weapon")
        assert isinstance(result, LLMRequest)

    def test_item_generation_food_returns_llmrequest(
        self, ps: DefaultPromptSet
    ) -> None:
        result = ps.item_generation("ctx", {"uses": 3}, "food")
        assert isinstance(result, LLMRequest)

    def test_item_generation_with_feedback_returns_llmrequest(
        self, ps: DefaultPromptSet
    ) -> None:
        result = ps.item_generation_with_feedback("ctx", {}, "tool", ["missing name"])
        assert isinstance(result, LLMRequest)

    def test_event_generation_combat_returns_llmrequest(
        self, ps: DefaultPromptSet
    ) -> None:
        result = ps.event_generation("ctx", {"difficulty": "hard"}, "combat")
        assert isinstance(result, LLMRequest)

    def test_event_generation_puzzle_returns_llmrequest(
        self, ps: DefaultPromptSet
    ) -> None:
        result = ps.event_generation("ctx", {}, "puzzle")
        assert isinstance(result, LLMRequest)

    def test_event_generation_with_feedback_returns_llmrequest(
        self, ps: DefaultPromptSet
    ) -> None:
        result = ps.event_generation_with_feedback("ctx", {}, "combat", ["bad loot"])
        assert isinstance(result, LLMRequest)

    def test_quest_generation_returns_llmrequest(self, ps: DefaultPromptSet) -> None:
        result = ps.quest_generation("ctx", {"quest_type": "escort"}, "escort")
        assert isinstance(result, LLMRequest)

    def test_quest_generation_with_feedback_returns_llmrequest(
        self, ps: DefaultPromptSet
    ) -> None:
        result = ps.quest_generation_with_feedback("ctx", {}, "fetch", ["no reward"])
        assert isinstance(result, LLMRequest)

    def test_spell_generation_returns_llmrequest(self, ps: DefaultPromptSet) -> None:
        result = ps.spell_generation("ctx", "ranger", "fire")
        assert isinstance(result, LLMRequest)

    def test_spell_generation_with_feedback_returns_llmrequest(
        self, ps: DefaultPromptSet
    ) -> None:
        result = ps.spell_generation_with_feedback("ctx", "ranger", "fire", ["wrong element"])
        assert isinstance(result, LLMRequest)

    def test_ability_generation_returns_llmrequest(self, ps: DefaultPromptSet) -> None:
        result = ps.ability_generation("ctx", "scout", "mobility")
        assert isinstance(result, LLMRequest)

    def test_ability_generation_with_feedback_returns_llmrequest(
        self, ps: DefaultPromptSet
    ) -> None:
        result = ps.ability_generation_with_feedback("ctx", "scout", "mobility", ["too cheap"])
        assert isinstance(result, LLMRequest)


# ---------------------------------------------------------------------------
# 3. Non-empty system + user_message for all new methods
# ---------------------------------------------------------------------------


class TestNonEmpty:
    def test_npc_generation_non_empty(self, ps: DefaultPromptSet) -> None:
        req = ps.npc_generation("ctx", {}, "guard")
        assert req.system
        assert req.user_message

    def test_monster_generation_non_empty(self, ps: DefaultPromptSet) -> None:
        req = ps.monster_generation("ctx", {}, level=3)
        assert req.system
        assert req.user_message

    def test_item_generation_non_empty(self, ps: DefaultPromptSet) -> None:
        req = ps.item_generation("ctx", {}, "drink")
        assert req.system
        assert req.user_message

    def test_event_generation_non_empty(self, ps: DefaultPromptSet) -> None:
        req = ps.event_generation("ctx", {}, "combat")
        assert req.system
        assert req.user_message

    def test_quest_generation_non_empty(self, ps: DefaultPromptSet) -> None:
        req = ps.quest_generation("ctx", {}, "solve")
        assert req.system
        assert req.user_message

    def test_spell_generation_non_empty(self, ps: DefaultPromptSet) -> None:
        req = ps.spell_generation("ctx", "caster", "light")
        assert req.system
        assert req.user_message

    def test_ability_generation_non_empty(self, ps: DefaultPromptSet) -> None:
        req = ps.ability_generation("ctx", "brawler", "crowd control")
        assert req.system
        assert req.user_message


# ---------------------------------------------------------------------------
# 4. Skeleton embedding
# ---------------------------------------------------------------------------


class TestSkeletonEmbedding:
    SKELETON = {"damage": 99, "type": "UNIQUE_SKEL_TOKEN_ABC"}

    def _contains_skeleton(self, req: LLMRequest) -> bool:
        return (
            "UNIQUE_SKEL_TOKEN_ABC" in req.user_message
            or str(self.SKELETON) in req.user_message
        )

    def test_npc_generation_embeds_skeleton(self, ps: DefaultPromptSet) -> None:
        req = ps.npc_generation("ctx", self.SKELETON, "trader")
        assert self._contains_skeleton(req), (
            f"Skeleton not found in user_message: {req.user_message!r}"
        )

    def test_monster_generation_embeds_skeleton(self, ps: DefaultPromptSet) -> None:
        req = ps.monster_generation("ctx", self.SKELETON, level=5)
        assert self._contains_skeleton(req)

    def test_item_generation_embeds_skeleton(self, ps: DefaultPromptSet) -> None:
        req = ps.item_generation("ctx", self.SKELETON, "weapon")
        assert self._contains_skeleton(req)

    def test_event_generation_embeds_skeleton(self, ps: DefaultPromptSet) -> None:
        req = ps.event_generation("ctx", self.SKELETON, "combat")
        assert self._contains_skeleton(req)

    def test_quest_generation_embeds_skeleton(self, ps: DefaultPromptSet) -> None:
        req = ps.quest_generation("ctx", self.SKELETON, "combat")
        assert self._contains_skeleton(req)


# ---------------------------------------------------------------------------
# 5. Role / category / type / element embedding
# ---------------------------------------------------------------------------


class TestArgEmbedding:
    def test_npc_role_in_user_message(self, ps: DefaultPromptSet) -> None:
        req = ps.npc_generation("ctx", {}, "UNIQUE_ROLE_XYZ")
        assert "UNIQUE_ROLE_XYZ" in req.user_message

    def test_monster_level_in_user_message(self, ps: DefaultPromptSet) -> None:
        req = ps.monster_generation("ctx", {}, level=42)
        assert "42" in req.user_message

    def test_item_category_in_user_message(self, ps: DefaultPromptSet) -> None:
        req = ps.item_generation("ctx", {}, "UNIQUE_ITEM_CAT")
        assert "UNIQUE_ITEM_CAT" in req.user_message

    def test_event_type_in_user_message(self, ps: DefaultPromptSet) -> None:
        req = ps.event_generation("ctx", {}, "UNIQUE_EVENT_TYPE")
        assert "UNIQUE_EVENT_TYPE" in req.user_message

    def test_quest_type_in_user_message(self, ps: DefaultPromptSet) -> None:
        req = ps.quest_generation("ctx", {}, "UNIQUE_QUEST_TYPE")
        assert "UNIQUE_QUEST_TYPE" in req.user_message

    def test_spell_element_in_user_message(self, ps: DefaultPromptSet) -> None:
        req = ps.spell_generation("ctx", "archetype", "UNIQUE_ELEMENT_XYZ")
        assert "UNIQUE_ELEMENT_XYZ" in req.user_message

    def test_spell_archetype_in_user_message(self, ps: DefaultPromptSet) -> None:
        req = ps.spell_generation("ctx", "UNIQUE_ARCH_XYZ", "fire")
        assert "UNIQUE_ARCH_XYZ" in req.user_message

    def test_ability_purpose_in_user_message(self, ps: DefaultPromptSet) -> None:
        req = ps.ability_generation("ctx", "archetype", "UNIQUE_PURPOSE_XYZ")
        assert "UNIQUE_PURPOSE_XYZ" in req.user_message

    def test_ability_archetype_in_user_message(self, ps: DefaultPromptSet) -> None:
        req = ps.ability_generation("ctx", "UNIQUE_ARCH_ABILITY", "mobility")
        assert "UNIQUE_ARCH_ABILITY" in req.user_message


# ---------------------------------------------------------------------------
# 6. _with_feedback variants: feedback in user_message, same system
# ---------------------------------------------------------------------------


class TestFeedbackVariants:
    FEEDBACK = ["name missing", "description contradicts world context"]

    def _assert_feedback_present(self, req: LLMRequest) -> None:
        for reason in self.FEEDBACK:
            assert reason in req.user_message, (
                f"Feedback reason {reason!r} not found in user_message"
            )

    def _assert_same_system(
        self, base: LLMRequest, with_fb: LLMRequest
    ) -> None:
        assert base.system == with_fb.system, (
            "_with_feedback changed the system prompt"
        )

    def test_npc_feedback_present(self, ps: DefaultPromptSet) -> None:
        req = ps.npc_generation_with_feedback("ctx", {}, "guard", self.FEEDBACK)
        self._assert_feedback_present(req)

    def test_npc_feedback_same_system(self, ps: DefaultPromptSet) -> None:
        base = ps.npc_generation("ctx", {}, "guard")
        with_fb = ps.npc_generation_with_feedback("ctx", {}, "guard", self.FEEDBACK)
        self._assert_same_system(base, with_fb)

    def test_monster_feedback_present(self, ps: DefaultPromptSet) -> None:
        req = ps.monster_generation_with_feedback("ctx", {}, 1, self.FEEDBACK)
        self._assert_feedback_present(req)

    def test_monster_feedback_same_system(self, ps: DefaultPromptSet) -> None:
        base = ps.monster_generation("ctx", {}, 1)
        with_fb = ps.monster_generation_with_feedback("ctx", {}, 1, self.FEEDBACK)
        self._assert_same_system(base, with_fb)

    def test_item_feedback_present(self, ps: DefaultPromptSet) -> None:
        req = ps.item_generation_with_feedback("ctx", {}, "weapon", self.FEEDBACK)
        self._assert_feedback_present(req)

    def test_item_feedback_same_system(self, ps: DefaultPromptSet) -> None:
        base = ps.item_generation("ctx", {}, "weapon")
        with_fb = ps.item_generation_with_feedback("ctx", {}, "weapon", self.FEEDBACK)
        self._assert_same_system(base, with_fb)

    def test_event_feedback_present(self, ps: DefaultPromptSet) -> None:
        req = ps.event_generation_with_feedback("ctx", {}, "combat", self.FEEDBACK)
        self._assert_feedback_present(req)

    def test_event_feedback_same_system(self, ps: DefaultPromptSet) -> None:
        base = ps.event_generation("ctx", {}, "combat")
        with_fb = ps.event_generation_with_feedback("ctx", {}, "combat", self.FEEDBACK)
        self._assert_same_system(base, with_fb)

    def test_quest_feedback_present(self, ps: DefaultPromptSet) -> None:
        req = ps.quest_generation_with_feedback("ctx", {}, "escort", self.FEEDBACK)
        self._assert_feedback_present(req)

    def test_quest_feedback_same_system(self, ps: DefaultPromptSet) -> None:
        base = ps.quest_generation("ctx", {}, "escort")
        with_fb = ps.quest_generation_with_feedback("ctx", {}, "escort", self.FEEDBACK)
        self._assert_same_system(base, with_fb)

    def test_spell_feedback_present(self, ps: DefaultPromptSet) -> None:
        req = ps.spell_generation_with_feedback("ctx", "mage", "ice", self.FEEDBACK)
        self._assert_feedback_present(req)

    def test_spell_feedback_same_system(self, ps: DefaultPromptSet) -> None:
        base = ps.spell_generation("ctx", "mage", "ice")
        with_fb = ps.spell_generation_with_feedback("ctx", "mage", "ice", self.FEEDBACK)
        self._assert_same_system(base, with_fb)

    def test_ability_feedback_present(self, ps: DefaultPromptSet) -> None:
        req = ps.ability_generation_with_feedback("ctx", "rogue", "escape", self.FEEDBACK)
        self._assert_feedback_present(req)

    def test_ability_feedback_same_system(self, ps: DefaultPromptSet) -> None:
        base = ps.ability_generation("ctx", "rogue", "escape")
        with_fb = ps.ability_generation_with_feedback("ctx", "rogue", "escape", self.FEEDBACK)
        self._assert_same_system(base, with_fb)

    def test_feedback_user_message_longer_than_base(
        self, ps: DefaultPromptSet
    ) -> None:
        """The _with_feedback user_message must be longer than the base."""
        base = ps.npc_generation("ctx", {}, "guard")
        with_fb = ps.npc_generation_with_feedback("ctx", {}, "guard", self.FEEDBACK)
        assert len(with_fb.user_message) > len(base.user_message)


# ---------------------------------------------------------------------------
# 7. Genre-neutrality spot-check on npc_generation
# ---------------------------------------------------------------------------


class TestGenreNeutrality:
    def test_no_forbidden_words_in_npc_system(self, ps: DefaultPromptSet) -> None:
        req = ps.npc_generation("", {}, "trader")
        system_lower = req.system.lower()
        for word in FORBIDDEN_WORDS:
            assert word not in system_lower, (
                f"Forbidden word {word!r} found in npc_generation system prompt"
            )

    def test_no_forbidden_words_in_npc_user_message(self, ps: DefaultPromptSet) -> None:
        req = ps.npc_generation("", {}, "trader")
        user_lower = req.user_message.lower()
        for word in FORBIDDEN_WORDS:
            assert word not in user_lower, (
                f"Forbidden word {word!r} found in npc_generation user_message"
            )

    def test_no_forbidden_words_in_monster_system(self, ps: DefaultPromptSet) -> None:
        req = ps.monster_generation("", {}, level=1)
        system_lower = req.system.lower()
        for word in FORBIDDEN_WORDS:
            assert word not in system_lower, (
                f"Forbidden word {word!r} found in monster_generation system prompt"
            )

    def test_no_forbidden_words_in_spell_system(self, ps: DefaultPromptSet) -> None:
        req = ps.spell_generation("", "mage", "light")
        system_lower = req.system.lower()
        for word in FORBIDDEN_WORDS:
            assert word not in system_lower, (
                f"Forbidden word {word!r} found in spell_generation system prompt"
            )

    def test_no_forbidden_words_in_ability_system(self, ps: DefaultPromptSet) -> None:
        req = ps.ability_generation("", "brawler", "offense")
        system_lower = req.system.lower()
        for word in FORBIDDEN_WORDS:
            assert word not in system_lower, (
                f"Forbidden word {word!r} found in ability_generation system prompt"
            )


# ---------------------------------------------------------------------------
# 8. Puzzle event has extra fields in user_message
# ---------------------------------------------------------------------------


class TestPuzzleEventShape:
    def test_puzzle_event_has_choices_in_prompt(self, ps: DefaultPromptSet) -> None:
        req = ps.event_generation("ctx", {}, "puzzle")
        assert "choices" in req.user_message

    def test_combat_event_does_not_have_choices_field(
        self, ps: DefaultPromptSet
    ) -> None:
        req = ps.event_generation("ctx", {}, "combat")
        assert "choices" not in req.user_message

    def test_puzzle_event_has_failure_damage_in_prompt(
        self, ps: DefaultPromptSet
    ) -> None:
        req = ps.event_generation("ctx", {}, "puzzle")
        assert "failure_damage" in req.user_message


# ---------------------------------------------------------------------------
# 9. Weapon item has different shape than non-weapon
# ---------------------------------------------------------------------------


class TestWeaponItemShape:
    def test_weapon_item_has_attack_dice(self, ps: DefaultPromptSet) -> None:
        req = ps.item_generation("ctx", {}, "weapon")
        assert "attack_dice" in req.user_message

    def test_non_weapon_item_has_health_value(self, ps: DefaultPromptSet) -> None:
        req = ps.item_generation("ctx", {}, "food")
        assert "health_value" in req.user_message

    def test_non_weapon_item_does_not_have_attack_dice(
        self, ps: DefaultPromptSet
    ) -> None:
        req = ps.item_generation("ctx", {}, "drink")
        assert "attack_dice" not in req.user_message
