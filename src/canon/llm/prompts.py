"""PromptSet ABC and DefaultPromptSet — world-aware prompt factory.

Consolidated from MazeWorld's 28-method PromptSet down to 17 abstract methods
by merging methods that shared the same LLM task (e.g., character generation
for NPC vs. merchant vs. hostile → single `character_generation` with a `role`
param) and by collapsing separate narrative methods (synopsis, intro, victory,
defeat) into typed dispatch calls rather than four nearly-identical methods.

The 17 methods cover:
  2 × story/world setup (story, map)
  2 × character (generation, +feedback)
  1 × class archetype
  2 × entity (generation, +feedback)
  2 × dialogue (generation, +feedback)
  4 × narrative (synopsis, map_intro, victory_text, defeat_text)
  3 × _with_feedback variants (story, map, class_archetype)
  1 × climax entity

# TODO(v0.2): prompt-set introspection so cradle can show
#             "this prompt was used to generate this entity"
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from canon.llm.request import LLMRequest

if TYPE_CHECKING:
    from canon.bible.models import Bible, Character

    CharacterRole = str  # Literal at type-check time, str at runtime


class PromptSet(ABC):
    """Domain-specific prompt factory.

    Canon ships ``DefaultPromptSet`` with world-aware prompts that pull genre,
    tone, and setting from the bible's accumulated context rather than
    hardcoding genre-specific tropes.  Users subclass only when their domain
    needs structural changes to prompts, not cosmetic ones.

    ~17 abstract methods, consolidated from MazeWorld's 28-method PromptSet.
    """

    # ------------------------------------------------------------------
    # Story / world setup
    # ------------------------------------------------------------------

    @abstractmethod
    def story_generation(
        self,
        seed: str,
        structure: dict | None = None,
        map_ids: list[str] | None = None,
    ) -> LLMRequest: ...

    @abstractmethod
    def story_generation_with_feedback(
        self,
        seed: str,
        structure: dict | None,
        feedback: list[str],
        map_ids: list[str] | None = None,
    ) -> LLMRequest: ...

    @abstractmethod
    def map_generation(
        self,
        story_context: str,
        map_index: int,
        environment: str | None = None,
    ) -> LLMRequest: ...

    @abstractmethod
    def map_generation_with_feedback(
        self,
        story_context: str,
        map_index: int,
        environment: str | None,
        feedback: list[str],
    ) -> LLMRequest: ...

    # ------------------------------------------------------------------
    # Characters
    # ------------------------------------------------------------------

    @abstractmethod
    def character_generation(
        self,
        context: str,
        skeleton: dict,
        role: CharacterRole,
    ) -> LLMRequest: ...

    @abstractmethod
    def character_generation_with_feedback(
        self,
        context: str,
        skeleton: dict,
        role: CharacterRole,
        feedback: list[str],
    ) -> LLMRequest: ...

    # ------------------------------------------------------------------
    # Class archetypes
    # ------------------------------------------------------------------

    @abstractmethod
    def class_archetype_generation(
        self,
        context: str,
        category: str | None = None,
    ) -> LLMRequest: ...

    @abstractmethod
    def class_archetype_generation_with_feedback(
        self,
        context: str,
        category: str | None,
        feedback: list[str],
    ) -> LLMRequest: ...

    # ------------------------------------------------------------------
    # Generic entity (weapon, tower, item, ability, anything user-defined)
    # ------------------------------------------------------------------

    @abstractmethod
    def entity_generation(
        self,
        entity_type: str,
        context: str,
        skeleton: dict,
    ) -> LLMRequest: ...

    @abstractmethod
    def entity_generation_with_feedback(
        self,
        entity_type: str,
        context: str,
        skeleton: dict,
        feedback: list[str],
    ) -> LLMRequest: ...

    # ------------------------------------------------------------------
    # Dialogue
    # ------------------------------------------------------------------

    @abstractmethod
    def dialogue_tree_generation(
        self,
        character: Character,
        context: str,
        nodes_per_tree: int = 5,
    ) -> LLMRequest: ...

    @abstractmethod
    def dialogue_tree_generation_with_feedback(
        self,
        character: Character,
        context: str,
        nodes_per_tree: int,
        feedback: list[str],
    ) -> LLMRequest: ...

    # ------------------------------------------------------------------
    # Narrative wrap-up
    # ------------------------------------------------------------------

    @abstractmethod
    def narrative_synopsis(self, bible: Bible) -> LLMRequest: ...

    @abstractmethod
    def map_intro(self, bible: Bible, map_id: str) -> LLMRequest: ...

    @abstractmethod
    def victory_text(self, bible: Bible) -> LLMRequest: ...

    @abstractmethod
    def defeat_text(self, bible: Bible) -> LLMRequest: ...

    # ------------------------------------------------------------------
    # Final boss / climax entity
    # ------------------------------------------------------------------

    @abstractmethod
    def climax_entity_generation(self, bible: Bible) -> LLMRequest: ...

    # ------------------------------------------------------------------
    # v0.2 — typed entity generation (world-aware, genre-neutral)
    # ------------------------------------------------------------------

    @abstractmethod
    def npc_generation(
        self,
        context: str,
        skeleton: dict,
        role: str,
    ) -> LLMRequest: ...

    @abstractmethod
    def npc_generation_with_feedback(
        self,
        context: str,
        skeleton: dict,
        role: str,
        feedback: list[str],
    ) -> LLMRequest: ...

    @abstractmethod
    def monster_generation(
        self,
        context: str,
        skeleton: dict,
        level: int,
    ) -> LLMRequest: ...

    @abstractmethod
    def monster_generation_with_feedback(
        self,
        context: str,
        skeleton: dict,
        level: int,
        feedback: list[str],
    ) -> LLMRequest: ...

    @abstractmethod
    def item_generation(
        self,
        context: str,
        skeleton: dict,
        item_category: str,
    ) -> LLMRequest: ...

    @abstractmethod
    def item_generation_with_feedback(
        self,
        context: str,
        skeleton: dict,
        item_category: str,
        feedback: list[str],
    ) -> LLMRequest: ...

    @abstractmethod
    def event_generation(
        self,
        context: str,
        skeleton: dict,
        event_type: str,
    ) -> LLMRequest: ...

    @abstractmethod
    def event_generation_with_feedback(
        self,
        context: str,
        skeleton: dict,
        event_type: str | None = None,
        feedback: list[str] | None = None,
    ) -> LLMRequest: ...

    @abstractmethod
    def quest_generation(
        self,
        context: str,
        skeleton: dict,
        quest_type: str,
    ) -> LLMRequest: ...

    @abstractmethod
    def quest_generation_with_feedback(
        self,
        context: str,
        skeleton: dict,
        quest_type: str,
        feedback: list[str],
    ) -> LLMRequest: ...

    @abstractmethod
    def spell_generation(
        self,
        context: str,
        archetype: str,
        element: str,
    ) -> LLMRequest: ...

    @abstractmethod
    def spell_generation_with_feedback(
        self,
        context: str,
        archetype: str,
        element: str,
        feedback: list[str],
    ) -> LLMRequest: ...

    @abstractmethod
    def spell_pool_generation(
        self,
        context: str,
        count: int,
        prompt_kwargs: dict,
    ) -> LLMRequest: ...

    @abstractmethod
    def spell_pool_generation_with_feedback(
        self,
        context: str,
        count: int,
        prompt_kwargs: dict,
        feedback: list[str],
    ) -> LLMRequest: ...

    @abstractmethod
    def ability_generation(
        self,
        context: str,
        archetype: str,
        purpose: str,
    ) -> LLMRequest: ...

    @abstractmethod
    def ability_generation_with_feedback(
        self,
        context: str,
        archetype: str,
        purpose: str,
        feedback: list[str],
    ) -> LLMRequest: ...


# ---------------------------------------------------------------------------
# Shared wording constants — tweak here to change all default prompts at once
# ---------------------------------------------------------------------------

_JSON_ONLY = (
    "Respond with raw JSON only. "
    "No prose before or after the object. "
    "Do not wrap in markdown code fences."
)

_WORLD_AWARE_SYSTEM = (
    "You are a world-builder generating content for a game. "
    "The content you produce must be consistent with the world "
    "described in the context below. Match its tone, setting, and "
    "aesthetic. Do not introduce themes or elements that contradict "
    "the established world.\n"
    + _JSON_ONLY
)


def _feedback_section(feedback: list[str]) -> str:
    reasons = "\n".join(f"- {reason}" for reason in feedback)
    return (
        "\n\nA previous attempt failed validation for these reasons:\n"
        + reasons
        + "\nFix these issues in your response."
    )


class DefaultPromptSet(PromptSet):
    """World-aware default prompts.

    Prompts reference the seed, story, and accumulated bible context to
    produce content consistent with whatever the user is building —
    fantasy dungeon, scifi station, suburban farm, tower defense map,
    visual novel scene.  Override individual methods for domain-specific
    structural needs.
    """

    # ------------------------------------------------------------------
    # story_generation
    # ------------------------------------------------------------------

    def story_generation(
        self,
        seed: str,
        structure: dict | None = None,
        map_ids: list[str] | None = None,
    ) -> LLMRequest:
        structure_hint = (
            f"\n\nOptional story structure constraints:\n{structure}"
            if structure
            else ""
        )
        # When the world's maps are known, bind beats to them 1:1 — one beat per
        # real map, using these exact map_ids — so the narrative matches the
        # rooms that actually exist (no phantom beats for rooms that don't).
        if map_ids:
            beats_instruction = (
                f"Produce EXACTLY {len(map_ids)} beats, one for each of these "
                f"map_ids in order — use these exact ids, no others: {map_ids}. "
            )
            escalation_n = len(map_ids)
        else:
            beats_instruction = "Produce one beat per location in the arc. "
            escalation_n = 3
        return LLMRequest(
            system=(
                "You are a narrative designer creating the foundational story "
                "for a game world. Use the seed provided to establish a unique "
                "tone, setting, and conflict that will guide all subsequent "
                "content generation. Keep the narrative open-ended enough that "
                "diverse types of encounters, characters, and locations can "
                "emerge from it.\n"
                + _JSON_ONLY
            ),
            user_message=(
                f"World seed: {seed}{structure_hint}\n\n"
                "Generate a story for this world. "
                f"{beats_instruction}"
                f"The escalation_arc should have {escalation_n} entries (one per beat). "
                f"{_JSON_ONLY}\n"
                'Expected fields: {"title": "<world or story title>", '
                '"synopsis": "<2-4 sentence overview of the setting and central conflict>", '
                '"factions": [{"faction_id": "<id>", "name": "<name>", '
                '"description": "<short description>", "history": "<brief history>", '
                '"leader": "<leader name or role>"}], '
                '"escalation_arc": ["<beat 1>", "<beat 2>", "..."], '
                '"climax": "<brief description of the climax>", '
                '"final_entity_id": "<proper name of the final antagonist/boss>", '
                '"final_entity_lore": "<2-3 sentences on the final antagonist>", '
                '"key_character_names": ["<named character 1>", "<named character 2>"], '
                '"beats": [{"map_id": "<map_id>", "beat": "<narrative role of this map>", '
                '"boss_name": "<proper name of this location\'s notable adversary>", '
                '"boss_lore": "<1-2 sentences on that adversary>"}]}'
            ),
            max_tokens=2000,
        )

    def story_generation_with_feedback(
        self,
        seed: str,
        structure: dict | None,
        feedback: list[str],
        map_ids: list[str] | None = None,
    ) -> LLMRequest:
        base = self.story_generation(seed, structure, map_ids)
        return LLMRequest(
            system=base.system,
            user_message=base.user_message + _feedback_section(feedback),
            examples=base.examples,
            max_tokens=base.max_tokens,
        )

    # ------------------------------------------------------------------
    # map_generation
    # ------------------------------------------------------------------

    def map_generation(
        self,
        story_context: str,
        map_index: int,
        environment: str | None = None,
    ) -> LLMRequest:
        env_hint = f"\nEnvironment type hint: {environment}" if environment else ""
        return LLMRequest(
            system=_WORLD_AWARE_SYSTEM,
            user_message=(
                f"World context:\n{story_context}{env_hint}\n\n"
                f"Generate map #{map_index} for this world. "
                "This map should feel like a natural part of the world described "
                "above, appropriate to the setting's tone and progression. "
                f"{_JSON_ONLY}\n"
                'Expected fields: {"name": "<location name>", '
                '"description": "<2-3 sentence prose description of the location>", '
                '"environment": "<environment keyword, e.g. forest/station/village>", '
                '"story_beat": "<this map\'s role in the overall narrative arc>"}'
            ),
            max_tokens=500,
        )

    def map_generation_with_feedback(
        self,
        story_context: str,
        map_index: int,
        environment: str | None,
        feedback: list[str],
    ) -> LLMRequest:
        base = self.map_generation(story_context, map_index, environment)
        return LLMRequest(
            system=base.system,
            user_message=base.user_message + _feedback_section(feedback),
            examples=base.examples,
            max_tokens=base.max_tokens,
        )

    # ------------------------------------------------------------------
    # character_generation
    # ------------------------------------------------------------------

    def character_generation(
        self,
        context: str,
        skeleton: dict,
        role: CharacterRole,
    ) -> LLMRequest:
        return LLMRequest(
            system=_WORLD_AWARE_SYSTEM,
            user_message=(
                f"World context:\n{context}\n\n"
                f"Generate a character with role '{role}' for this world. "
                "The mechanical properties have been pre-rolled — do not change "
                "them, only name and describe the character.\n\n"
                f"Pre-rolled skeleton:\n{skeleton}\n\n"
                f"{_JSON_ONLY}\n"
                'Expected fields: {"name": "<character name>", '
                '"lore": "<2-3 sentence backstory>", '
                '"personality": "<1-2 sentence personality description>", '
                '"appearance": "<1-2 sentence physical description>"}'
            ),
            max_tokens=500,
        )

    def character_generation_with_feedback(
        self,
        context: str,
        skeleton: dict,
        role: CharacterRole,
        feedback: list[str],
    ) -> LLMRequest:
        base = self.character_generation(context, skeleton, role)
        return LLMRequest(
            system=base.system,
            user_message=base.user_message + _feedback_section(feedback),
            examples=base.examples,
            max_tokens=base.max_tokens,
        )

    # ------------------------------------------------------------------
    # class_archetype_generation
    # ------------------------------------------------------------------

    def class_archetype_generation(
        self,
        context: str,
        category: str | None = None,
    ) -> LLMRequest:
        category_hint = (
            f"\nArchetype category: {category}" if category else ""
        )
        return LLMRequest(
            system=_WORLD_AWARE_SYSTEM,
            user_message=(
                f"World context:\n{context}{category_hint}\n\n"
                "Generate a class or archetype for this world. It should be "
                "thematically consistent with the world's setting and tone. "
                f"{_JSON_ONLY}\n"
                'Expected fields: {"name": "<archetype name>", '
                '"description": "<2-3 sentence description of the archetype\'s role>", '
                '"lore": "<1-2 sentence narrative flavor for the archetype>", '
                '"role_tags": ["<tag1>", "<tag2>"]}'
            ),
            max_tokens=400,
        )

    def class_archetype_generation_with_feedback(
        self,
        context: str,
        category: str | None,
        feedback: list[str],
    ) -> LLMRequest:
        base = self.class_archetype_generation(context, category)
        return LLMRequest(
            system=base.system,
            user_message=base.user_message + _feedback_section(feedback),
            examples=base.examples,
            max_tokens=base.max_tokens,
        )

    # ------------------------------------------------------------------
    # entity_generation
    # ------------------------------------------------------------------

    def entity_generation(
        self,
        entity_type: str,
        context: str,
        skeleton: dict,
    ) -> LLMRequest:
        return LLMRequest(
            system=_WORLD_AWARE_SYSTEM,
            user_message=(
                f"World context:\n{context}\n\n"
                f"Generate a {entity_type} for this world. The mechanical "
                "properties have been pre-rolled — do not change them, only "
                "name and describe.\n\n"
                f"Pre-rolled skeleton:\n{skeleton}\n\n"
                f"{_JSON_ONLY}\n"
                'Expected fields: {"name": "<short name>", '
                '"lore": "<2-3 sentence flavor description>"}'
            ),
            max_tokens=400,
        )

    def entity_generation_with_feedback(
        self,
        entity_type: str,
        context: str,
        skeleton: dict,
        feedback: list[str],
    ) -> LLMRequest:
        base = self.entity_generation(entity_type, context, skeleton)
        return LLMRequest(
            system=base.system,
            user_message=base.user_message + _feedback_section(feedback),
            examples=base.examples,
            max_tokens=base.max_tokens,
        )

    # ------------------------------------------------------------------
    # dialogue_tree_generation
    # ------------------------------------------------------------------

    def dialogue_tree_generation(
        self,
        character: Character,
        context: str,
        nodes_per_tree: int = 5,
    ) -> LLMRequest:
        char_summary = (
            f"Name: {character.name}\n"
            f"Role: {character.role}\n"
            f"Personality: {character.personality or 'not specified'}\n"
            f"Lore: {character.lore or 'not specified'}"
        )
        return LLMRequest(
            system=(
                "You are a dialogue writer generating branching conversation "
                "trees for a game. The dialogue you produce must fit the "
                "character described below and the world context provided. "
                "Keep the tone consistent with the world's established "
                "aesthetic. Each node should feel like a natural beat in a "
                "conversation.\n"
                + _JSON_ONLY
            ),
            user_message=(
                f"World context:\n{context}\n\n"
                f"Character:\n{char_summary}\n\n"
                f"Generate a dialogue tree with approximately {nodes_per_tree} "
                "nodes. The tree must have a 'start' entry node. "
                f"{_JSON_ONLY}\n"
                'Expected format: {"nodes": {"start": {"prompt": "<opening line>", '
                '"choices": [{"text": "<player choice>", "next_node_id": "<node_id or null>"}]}, '
                '"<node_id>": {"prompt": "<NPC line>", "choices": [...]}}}'
            ),
            max_tokens=1500,
        )

    def dialogue_tree_generation_with_feedback(
        self,
        character: Character,
        context: str,
        nodes_per_tree: int,
        feedback: list[str],
    ) -> LLMRequest:
        base = self.dialogue_tree_generation(character, context, nodes_per_tree)
        return LLMRequest(
            system=base.system,
            user_message=base.user_message + _feedback_section(feedback),
            examples=base.examples,
            max_tokens=base.max_tokens,
        )

    # ------------------------------------------------------------------
    # Narrative wrap-up
    # ------------------------------------------------------------------

    def narrative_synopsis(self, bible: Bible) -> LLMRequest:
        story = bible.story
        map_names = [m.name for m in bible.maps.values()]
        character_names = [c.name for c in bible.characters]
        return LLMRequest(
            system=_WORLD_AWARE_SYSTEM,
            user_message=(
                f"World title: {story.title}\n"
                f"Seed: {story.seed}\n"
                f"Synopsis so far: {story.synopsis}\n"
                f"Locations: {', '.join(map_names) or 'none'}\n"
                f"Characters: {', '.join(character_names) or 'none'}\n\n"
                "Write a polished narrative synopsis for this world — a 3-5 "
                "sentence overview that captures the setting, tone, and central "
                "conflict as they have been developed through generation. "
                f"{_JSON_ONLY}\n"
                'Expected fields: {"synopsis": "<polished prose synopsis>"}'
            ),
            max_tokens=400,
        )

    def map_intro(self, bible: Bible, map_id: str) -> LLMRequest:
        story = bible.story
        the_map = bible.maps.get(map_id)
        map_desc = (
            f"Name: {the_map.name}\n"
            f"Description: {the_map.description}\n"
            f"Environment: {the_map.environment}\n"
            f"Narrative role: {the_map.story_beat}"
            if the_map
            else f"Map ID: {map_id} (no map data available)"
        )
        return LLMRequest(
            system=_WORLD_AWARE_SYSTEM,
            user_message=(
                f"World context: {story.title} — {story.synopsis}\n\n"
                f"Location:\n{map_desc}\n\n"
                "Write a short in-world introductory text (2-4 sentences) that "
                "a player would see when they enter this location. Match the "
                "world's tone and aesthetic. "
                f"{_JSON_ONLY}\n"
                'Expected fields: {"intro_text": "<location introduction prose>"}'
            ),
            max_tokens=300,
        )

    def victory_text(self, bible: Bible) -> LLMRequest:
        story = bible.story
        return LLMRequest(
            system=_WORLD_AWARE_SYSTEM,
            user_message=(
                f"World context: {story.title} — {story.synopsis}\n"
                f"Climax: {story.climax or 'the final challenge has been overcome'}\n\n"
                "Write a short victory message (2-4 sentences) for when the "
                "player completes this world's central challenge. It should "
                "feel earned and consistent with the world's tone. "
                f"{_JSON_ONLY}\n"
                'Expected fields: {"victory_text": "<victory prose>"}'
            ),
            max_tokens=300,
        )

    def defeat_text(self, bible: Bible) -> LLMRequest:
        story = bible.story
        return LLMRequest(
            system=_WORLD_AWARE_SYSTEM,
            user_message=(
                f"World context: {story.title} — {story.synopsis}\n\n"
                "Write a short defeat message (2-4 sentences) for when the "
                "player fails. It should acknowledge the loss while staying "
                "consistent with the world's tone — neither dismissive nor "
                "overwrought. "
                f"{_JSON_ONLY}\n"
                'Expected fields: {"defeat_text": "<defeat prose>"}'
            ),
            max_tokens=300,
        )

    # ------------------------------------------------------------------
    # climax_entity_generation
    # ------------------------------------------------------------------

    def climax_entity_generation(self, bible: Bible) -> LLMRequest:
        story = bible.story
        faction_names = [f.name for f in story.factions]
        return LLMRequest(
            system=_WORLD_AWARE_SYSTEM,
            user_message=(
                f"World context: {story.title} — {story.synopsis}\n"
                f"Climax description: {story.climax or 'the final confrontation'}\n"
                f"Factions: {', '.join(faction_names) or 'none'}\n\n"
                "Generate the climax entity — the final obstacle or antagonist "
                "the player faces at the end of this world. It should be "
                "thematically consistent with the established narrative and "
                "feel like the culmination of the world's central conflict. "
                f"{_JSON_ONLY}\n"
                'Expected fields: {"name": "<entity name>", '
                '"lore": "<3-5 sentence description establishing threat and backstory>", '
                '"role": "<narrative role in the climax>"}'
            ),
            max_tokens=600,
        )

    # ------------------------------------------------------------------
    # v0.2 — npc_generation
    # ------------------------------------------------------------------

    def npc_generation(
        self,
        context: str,
        skeleton: dict,
        role: str,
    ) -> LLMRequest:
        return LLMRequest(
            system=(
                "You are a world-builder creating a named character for a game. "
                "The character must fit the world described in the context. "
                f"{_JSON_ONLY}"
            ),
            user_message=(
                f"World context:\n{context}\n\n"
                f"Generate a character with role '{role}'.\n"
                f"Pre-rolled skeleton: {skeleton}\n\n"
                "Respond with JSON containing exactly these fields:\n"
                '{"name": "<short name>", "job": "<role/profession in this world>", '
                '"hobby": "<personal interest or pastime>", '
                '"personality": "<2-3 word descriptor>", '
                '"backstory": "<2-3 sentences connecting the character to this world>", '
                '"opening_greeting": "<first line spoken when meeting the player>", '
                '"portrait_prompt": "<visual description for portrait generation>"}'
            ),
            max_tokens=600,
        )

    def npc_generation_with_feedback(
        self,
        context: str,
        skeleton: dict,
        role: str,
        feedback: list[str],
    ) -> LLMRequest:
        base = self.npc_generation(context, skeleton, role)
        return LLMRequest(
            system=base.system,
            user_message=base.user_message + _feedback_section(feedback),
            examples=base.examples,
            max_tokens=base.max_tokens,
        )

    # ------------------------------------------------------------------
    # v0.2 — monster_generation
    # ------------------------------------------------------------------

    def monster_generation(
        self,
        context: str,
        skeleton: dict,
        level: int,
    ) -> LLMRequest:
        return LLMRequest(
            system=(
                "You are a world-builder designing an adversarial creature for a game. "
                "The creature must fit the world described in the context. "
                f"{_JSON_ONLY}"
            ),
            user_message=(
                f"World context:\n{context}\n\n"
                f"Design a creature appropriate for challenge level {level}.\n"
                f"Pre-rolled skeleton: {skeleton}\n\n"
                "Respond with JSON containing exactly these fields:\n"
                '{"name": "<creature name>", "species": "<species or type>", '
                '"description": "<2-3 sentences>", '
                '"backstory": "<origin or lore connecting it to this world>", '
                '"hp_range": [<min_hp>, <max_hp>], '
                '"ac_range": [<min_ac>, <max_ac>], '
                '"damage_type": "<damage type>", '
                '"physical_type": "<physical category>", '
                '"elemental_affinity": "<element>", '
                '"weakness": "<vulnerability>", '
                '"abilities": [{"name": "<name>", "effect_type": "<type>", '
                '"damage_dice": "<XdY>", "chance": <0.0-1.0>}], '
                '"is_boss": <true|false>, '
                '"portrait_prompt": "<visual description>"}'
            ),
            max_tokens=700,
        )

    def monster_generation_with_feedback(
        self,
        context: str,
        skeleton: dict,
        level: int,
        feedback: list[str],
    ) -> LLMRequest:
        base = self.monster_generation(context, skeleton, level)
        return LLMRequest(
            system=base.system,
            user_message=base.user_message + _feedback_section(feedback),
            examples=base.examples,
            max_tokens=base.max_tokens,
        )

    # ------------------------------------------------------------------
    # v0.2 — item_generation
    # ------------------------------------------------------------------

    def item_generation(
        self,
        context: str,
        skeleton: dict,
        item_category: str,
    ) -> LLMRequest:
        # The rolled skeleton is the source of truth for the item kind — the
        # item_category kwarg is only a fallback (mirrors event_generation).
        item_category = skeleton.get("item_kind", item_category) or item_category
        if item_category == "weapon":
            shape = (
                '{"name": "<item name>", "desc": "<1-2 sentence description>", '
                '"weapon_type": "<weapon type>", '
                '"damage_type": "<damage type>", '
                '"weapon_category": "<simple|martial>", '
                '"item_stats": {"attack_dice": "<XdY>", '
                '"stat_modifier": "<STR|DEX|etc>", "price": <int>}}'
            )
        else:
            shape = (
                '{"name": "<item name>", "desc": "<1-2 sentence description>", '
                '"item_stats": {"stamina_value": <int>, "health_value": <int>, '
                '"uses": <int>, "price": <int>, "attribute": "<effect or null>"}}'
            )
        return LLMRequest(
            system=(
                "You are a world-builder designing an item for a game. "
                "The item must fit the world described in the context. "
                f"{_JSON_ONLY}"
            ),
            user_message=(
                f"World context:\n{context}\n\n"
                f"Generate a '{item_category}' item.\n"
                f"Pre-rolled skeleton: {skeleton}\n\n"
                f"Respond with JSON containing exactly these fields:\n{shape}"
            ),
            max_tokens=500,
        )

    def item_generation_with_feedback(
        self,
        context: str,
        skeleton: dict,
        item_category: str,
        feedback: list[str],
    ) -> LLMRequest:
        base = self.item_generation(context, skeleton, item_category)
        return LLMRequest(
            system=base.system,
            user_message=base.user_message + _feedback_section(feedback),
            examples=base.examples,
            max_tokens=base.max_tokens,
        )

    # ------------------------------------------------------------------
    # v0.2 — event_generation
    # ------------------------------------------------------------------

    def event_generation(
        self,
        context: str,
        skeleton: dict,
        event_type: str = "combat",
    ) -> LLMRequest:
        # The rolled skeleton is the source of truth for the event type — the
        # event_type kwarg is only a fallback. (Callers shouldn't hard-code a
        # type that disagrees with the roll, or combat-vs-puzzle content and
        # the parser's branch will diverge.)
        event_type = skeleton.get("event_type", event_type) or event_type
        base_shape = (
            '"name": "<event name>", '
            '"description": "<2-3 sentences>", '
            '"difficulty": "<easy|medium|hard>", '
            '"money_drop": [<min>, <max>], '
            '"loot_table": [{"item_id": <int>, "drop_chance": <0.0-1.0>}]'
        )
        if event_type in ("puzzle", "event"):
            extra_shape = (
                ', "choices": [{"text": "<choice text>", "stat_check": "<STAT>", '
                '"dc": <int>, "auto_success": <true|false>, '
                '"success_text": "<outcome text>"}], '
                '"correct_tool": "<item name or null>", '
                '"correct_ability": "<ability name or null>", '
                '"correct_spell": "<spell name or null>", '
                '"failure_damage_type": "<damage type>", '
                '"failure_damage_range": [<min>, <max>]'
            )
        else:
            extra_shape = ""
        full_shape = "{" + base_shape + extra_shape + "}"
        return LLMRequest(
            system=(
                "You are a world-builder designing an encounter event for a game. "
                "The event must fit the world described in the context. "
                f"{_JSON_ONLY}"
            ),
            user_message=(
                f"World context:\n{context}\n\n"
                f"Generate a '{event_type}' event.\n"
                f"Pre-rolled skeleton: {skeleton}\n\n"
                f"Respond with JSON containing exactly these fields:\n{full_shape}"
            ),
            max_tokens=700,
        )

    def event_generation_with_feedback(
        self,
        context: str,
        skeleton: dict,
        event_type: str | None = None,
        feedback: list[str] | None = None,
    ) -> LLMRequest:
        # Mirror event_generation: skeleton is the source of truth; event_type
        # is the fallback when the skeleton didn't pre-roll a type.
        resolved = skeleton.get("event_type", event_type or "combat") or "combat"
        base = self.event_generation(context, skeleton, resolved)
        return LLMRequest(
            system=base.system,
            user_message=base.user_message + _feedback_section(feedback or []),
            examples=base.examples,
            max_tokens=base.max_tokens,
        )

    # ------------------------------------------------------------------
    # v0.2 — quest_generation
    # ------------------------------------------------------------------

    def quest_generation(
        self,
        context: str,
        skeleton: dict,
        quest_type: str,
    ) -> LLMRequest:
        return LLMRequest(
            system=(
                "You are a world-builder designing a quest for a game. "
                "The quest must fit the world described in the context. "
                f"{_JSON_ONLY}"
            ),
            user_message=(
                f"World context:\n{context}\n\n"
                f"Generate a '{quest_type}' quest.\n"
                f"Pre-rolled skeleton: {skeleton}\n\n"
                "Respond with JSON containing exactly these fields:\n"
                '{"title": "<quest title>", '
                '"description": "<2-3 sentences describing the quest>", '
                '"reward": {"xp": <int>, "item_id": <int_or_null>}, '
                '"failure_penalty": {"hp_damage": <int>}, '
                '"success_dialogue": "<short line spoken on success>", '
                '"failure_dialogue": "<short line spoken on failure>"}'
            ),
            max_tokens=600,
        )

    def quest_generation_with_feedback(
        self,
        context: str,
        skeleton: dict,
        quest_type: str,
        feedback: list[str],
    ) -> LLMRequest:
        base = self.quest_generation(context, skeleton, quest_type)
        return LLMRequest(
            system=base.system,
            user_message=base.user_message + _feedback_section(feedback),
            examples=base.examples,
            max_tokens=base.max_tokens,
        )

    # ------------------------------------------------------------------
    # v0.2 — spell_generation
    # ------------------------------------------------------------------

    def spell_generation(
        self,
        context: str,
        archetype: str,
        element: str,
    ) -> LLMRequest:
        return LLMRequest(
            system=(
                "You are a world-builder designing a learnable power for a game. "
                "The power must fit the world described in the context. "
                f"{_JSON_ONLY}"
            ),
            user_message=(
                f"World context:\n{context}\n\n"
                f"Generate a '{element}'-element power for the '{archetype}' archetype.\n\n"
                "Respond with JSON containing exactly these fields:\n"
                '{"name": "<power name>", '
                '"description": "<1-2 sentences>", '
                '"spell_type": "<damage_single|damage_multi|heal|buff_stat|buff_sustain>", '
                '"element": "<element>", '
                '"stat": "<INT|WIS>", '
                '"targets": "<single|multi|self>", '
                '"num_dice": <int>, '
                '"die_sides": <int>, '
                '"stamina_cost": <int>, '
                '"heal_amount": <int>, '
                '"buff_stat": "<stat or null>", '
                '"buff_value": <int>, '
                '"buff_duration": <int>}'
            ),
            max_tokens=500,
        )

    def spell_generation_with_feedback(
        self,
        context: str,
        archetype: str,
        element: str,
        feedback: list[str],
    ) -> LLMRequest:
        base = self.spell_generation(context, archetype, element)
        return LLMRequest(
            system=base.system,
            user_message=base.user_message + _feedback_section(feedback),
            examples=base.examples,
            max_tokens=base.max_tokens,
        )

    # ------------------------------------------------------------------
    # spell_pool_generation — batched: one call yields `count` spells
    # ------------------------------------------------------------------

    def spell_pool_generation(
        self,
        context: str,
        count: int,
        prompt_kwargs: dict,
    ) -> LLMRequest:
        """Batch-generate a themed pool of ``count`` learnable powers.

        Game-agnostic: the caller passes whatever descriptors its game uses via
        ``prompt_kwargs`` (e.g. ``archetype``, ``element``, ``theme``). The LLM
        supplies only name + description per spell; all mechanics are pre-rolled
        from a skeleton by the calling phase and merged afterward. Returns a
        JSON **array** of ``count`` objects.
        """
        descriptors = ", ".join(
            f"{k}={v!r}" for k, v in prompt_kwargs.items() if v is not None
        )
        descriptor_line = f" ({descriptors})" if descriptors else ""
        return LLMRequest(
            system=(
                "You are a world-builder designing a set of learnable powers for "
                "a game. The powers must fit the world described in the context "
                "and share a coherent theme. "
                f"{_JSON_ONLY}"
            ),
            user_message=(
                f"World context:\n{context}\n\n"
                f"Generate {count} distinct but thematically related powers"
                f"{descriptor_line}.\n\n"
                "Respond with a JSON array of exactly "
                f"{count} objects, each with exactly these fields:\n"
                '[{"name": "<power name>", '
                '"description": "<1-2 sentences of flavor>"}]'
            ),
            max_tokens=max(400, count * 80),
        )

    def spell_pool_generation_with_feedback(
        self,
        context: str,
        count: int,
        prompt_kwargs: dict,
        feedback: list[str],
    ) -> LLMRequest:
        base = self.spell_pool_generation(context, count, prompt_kwargs)
        return LLMRequest(
            system=base.system,
            user_message=base.user_message + _feedback_section(feedback),
            examples=base.examples,
            max_tokens=base.max_tokens,
        )

    # ------------------------------------------------------------------
    # v0.2 — ability_generation
    # ------------------------------------------------------------------

    def ability_generation(
        self,
        context: str,
        archetype: str,
        purpose: str,
    ) -> LLMRequest:
        return LLMRequest(
            system=(
                "You are a world-builder designing an active ability for a game. "
                "The ability must fit the world described in the context. "
                f"{_JSON_ONLY}"
            ),
            user_message=(
                f"World context:\n{context}\n\n"
                f"Generate an ability for the '{archetype}' archetype "
                f"with the purpose: '{purpose}'.\n\n"
                "Respond with JSON containing exactly these fields:\n"
                '{"name": "<ability name>", '
                '"description": "<1-2 sentences>", '
                '"stat": "<STR|DEX|CON|INT|WIS|CHA|LUCK>", '
                '"stamina_cost": <int>}'
            ),
            max_tokens=400,
        )

    def ability_generation_with_feedback(
        self,
        context: str,
        archetype: str,
        purpose: str,
        feedback: list[str],
    ) -> LLMRequest:
        base = self.ability_generation(context, archetype, purpose)
        return LLMRequest(
            system=base.system,
            user_message=base.user_message + _feedback_section(feedback),
            examples=base.examples,
            max_tokens=base.max_tokens,
        )
