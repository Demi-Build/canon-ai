"""Unit tests for canon.bible.models.

Covers field defaults, accessor behavior, mutation methods, and the
persist/load round trip. Accessors raise `KeyError` on missing IDs;
`get_dialogue` returns `None` when no tree is registered (treated as a
present-or-absent lookup, not a hard error).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from canon.bible.models import (
    Ability,
    Bible,
    BibleMetadata,
    Character,
    CharacterClass,
    ClassArchetype,
    EntityLore,
    Faction,
    GenerationTrail,
    Map,
    Spell,
    StoryArc,
    StoryBeat,
    Zone,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_simple_bible(seed: str = "spike_001") -> Bible:
    """Construct a small but non-empty Bible useful for round-trip tests."""

    bible = Bible.empty(seed=seed)
    bible.story = StoryArc(
        title="The Spike",
        synopsis="A test world.",
        seed=seed,
        factions=[
            Faction(
                faction_id="f1",
                name="Order of Tests",
                description="They write the suite.",
                history="Long.",
                leader="Pytest",
                threat_level=2,
                aesthetic="green checkmarks",
            )
        ],
        beats=[StoryBeat(map_id="m1", beat="Opening", boss_name=None)],
        key_character_names=["Alyx", "Borin"],
    )
    bible.maps["m1"] = Map(
        map_id="m1",
        name="Mossy Hall",
        description="A damp hall.",
        environment="cave",
        level=1,
        story_beat="Opening",
        zones=[Zone(zone_id="z1", name="Entrance", parent_map_id="m1")],
        entities=[
            EntityLore(
                entity_id="e1",
                entity_type="weapon",
                name="Mossblade",
                map_id="m1",
                lore="Old.",
            )
        ],
        connections=["m2"],
    )
    bible.characters.append(
        Character(
            character_id="c1",
            name="Alyx",
            role="npc",
            primary_map_id="m1",
            lore="Born here.",
            class_data=CharacterClass(
                archetype_id="warrior",
                stats={"str": 14, "dex": 10},
                abilities=[Ability(name="Power Strike", description="A heavy blow.", stat="STR")],
                spells=[],
            ),
        )
    )
    bible.class_archetypes["warrior"] = ClassArchetype(
        archetype_id="warrior",
        name="Warrior",
        description="Front-line fighter.",
        stat_template={"str": 12, "con": 12},
        stat_roles={"primary": ["str", "con"], "secondary": ["dex"], "dump": ["int"]},
    )
    return bible


# ---------------------------------------------------------------------------
# Bible.empty + defaults
# ---------------------------------------------------------------------------


def test_bible_empty_produces_valid_minimal_bible() -> None:
    bible = Bible.empty(seed="hello")
    assert bible.canon_version == "0.1"
    assert bible.seed == "hello"
    assert bible.story.seed == "hello"
    assert bible.maps == {}
    assert bible.characters == []
    assert bible.class_archetypes == {}
    assert bible.dialogues == {}
    assert isinstance(bible.metadata, BibleMetadata)


def test_bible_default_collections_are_empty() -> None:
    """Constructing a Bible without any collections should still be valid."""

    bible = Bible(seed="x", story=StoryArc(title="t", synopsis="s", seed="x"))
    assert bible.maps == {}
    assert bible.characters == []
    assert bible.class_archetypes == {}
    assert bible.dialogues == {}
    assert bible.story.factions == []
    assert bible.story.escalation_arc == []
    assert bible.story.beats == []
    assert bible.story.key_character_names == []


# ---------------------------------------------------------------------------
# Field-level defaults required by the verification doc
# ---------------------------------------------------------------------------


def test_storyarc_key_character_names_defaults_empty() -> None:
    arc = StoryArc(title="t", synopsis="s", seed="x")
    assert arc.key_character_names == []


def test_classarchetype_stat_roles_defaults_empty() -> None:
    arch = ClassArchetype(archetype_id="a", name="A")
    assert arch.stat_roles == {}


def test_entitylore_requires_name_but_lore_defaults_empty() -> None:
    """`name` is required; `lore` defaults to `""`."""

    entity = EntityLore(entity_id="e1", entity_type="weapon", name="Sword")
    assert entity.lore == ""
    assert entity.skeleton == {}
    assert entity.tags == []
    assert entity.extra == {}

    with pytest.raises(Exception):  # pydantic.ValidationError
        EntityLore(entity_id="e1", entity_type="weapon")  # type: ignore[call-arg]


def test_extra_dict_fields_default_to_empty() -> None:
    """Every model with an `extra` field should default to `{}`."""

    entity = EntityLore(entity_id="e1", entity_type="t", name="n")
    character = Character(character_id="c1", name="c", role="npc")
    the_map = Map(map_id="m1", name="m")
    arch = ClassArchetype(archetype_id="a", name="A")
    assert entity.extra == {}
    assert character.extra == {}
    assert the_map.extra == {}
    assert arch.extra == {}


def test_storybeat_boss_fields_optional() -> None:
    beat = StoryBeat(map_id="m1", beat="Opening")
    assert beat.boss_name is None
    assert beat.boss_lore is None


def test_character_class_collections_default_empty() -> None:
    cls = CharacterClass(archetype_id="a")
    assert cls.stats == {}
    assert cls.abilities == []
    assert cls.spells == []
    assert cls.equipment == []
    assert cls.skeleton == {}


def test_generation_trail_defaults() -> None:
    trail = GenerationTrail()
    assert trail.prompt == ""
    assert trail.response == ""
    assert trail.validation_history == []
    assert trail.retry_count == 0
    assert trail.cost is None
    assert trail.model is None


# ---------------------------------------------------------------------------
# Accessors
# ---------------------------------------------------------------------------


def test_get_map_returns_correct_map() -> None:
    bible = _make_simple_bible()
    the_map = bible.get_map("m1")
    assert the_map.map_id == "m1"
    assert the_map.name == "Mossy Hall"


def test_get_map_raises_keyerror_on_missing() -> None:
    bible = _make_simple_bible()
    with pytest.raises(KeyError):
        bible.get_map("does_not_exist")


def test_get_character_returns_correct_character() -> None:
    bible = _make_simple_bible()
    character = bible.get_character("c1")
    assert character.character_id == "c1"
    assert character.name == "Alyx"


def test_get_character_raises_keyerror_on_missing() -> None:
    bible = _make_simple_bible()
    with pytest.raises(KeyError):
        bible.get_character("nobody")


def test_get_entity_returns_correct_entity() -> None:
    bible = _make_simple_bible()
    entity = bible.get_entity("m1", "e1")
    assert entity.entity_id == "e1"
    assert entity.name == "Mossblade"


def test_get_entity_raises_keyerror_on_missing_map() -> None:
    bible = _make_simple_bible()
    with pytest.raises(KeyError):
        bible.get_entity("does_not_exist", "e1")


def test_get_entity_raises_keyerror_on_missing_entity() -> None:
    bible = _make_simple_bible()
    with pytest.raises(KeyError):
        bible.get_entity("m1", "ghost")


def test_get_dialogue_returns_none_when_absent() -> None:
    bible = _make_simple_bible()
    assert bible.get_dialogue("c1") is None


# ---------------------------------------------------------------------------
# Mutation
# ---------------------------------------------------------------------------


def test_add_character_appends_to_characters() -> None:
    bible = _make_simple_bible()
    bible.add_character(Character(character_id="c2", name="Borin", role="merchant"))
    assert any(c.character_id == "c2" for c in bible.characters)
    assert bible.get_character("c2").name == "Borin"


def test_add_entity_appends_to_map_entities() -> None:
    bible = _make_simple_bible()
    bible.add_entity(
        "m1",
        EntityLore(entity_id="e2", entity_type="potion", name="Healing Draught"),
    )
    entity_ids = {e.entity_id for e in bible.maps["m1"].entities}
    assert entity_ids == {"e1", "e2"}


def test_add_entity_raises_when_map_missing() -> None:
    bible = _make_simple_bible()
    with pytest.raises(KeyError):
        bible.add_entity(
            "no_such_map",
            EntityLore(entity_id="e2", entity_type="potion", name="X"),
        )


def test_update_entity_mutates_fields() -> None:
    bible = _make_simple_bible()
    bible.update_entity("m1", "e1", lore="rewritten", tags=["legendary"])
    entity = bible.get_entity("m1", "e1")
    assert entity.lore == "rewritten"
    assert entity.tags == ["legendary"]


def test_update_entity_raises_when_missing() -> None:
    bible = _make_simple_bible()
    with pytest.raises(KeyError):
        bible.update_entity("m1", "ghost", lore="x")


def test_remove_entity_drops_entry() -> None:
    bible = _make_simple_bible()
    bible.remove_entity("m1", "e1")
    assert bible.maps["m1"].entities == []


def test_remove_entity_raises_when_missing() -> None:
    bible = _make_simple_bible()
    with pytest.raises(KeyError):
        bible.remove_entity("m1", "ghost")


# ---------------------------------------------------------------------------
# Context assembly — methods delegate to canon.bible.context
# ---------------------------------------------------------------------------


def test_context_methods_delegate_to_context_module() -> None:
    """`Bible.get_context` / `get_cumulative_context` produce real content
    via `canon.bible.context`. Detailed formatting is exercised in
    `tests/test_context.py`; here we only smoke-test the wiring."""

    bible = _make_simple_bible()

    single = bible.get_context("m1")
    assert "Title: The Spike" in single
    assert "Synopsis: A test world." in single
    assert "Faction: Order of Tests" in single
    assert "Beat: Opening" in single

    cumulative = bible.get_cumulative_context("m1")
    # m1 is the first map, so cumulative collapses to the single-map view.
    assert cumulative == single

    # Smoke-test the max_chars argument flows through. We pass a cap larger
    # than the truncation marker and confirm the call still produces a string.
    capped = bible.get_cumulative_context("m1", max_chars=10_000)
    assert isinstance(capped, str)


# ---------------------------------------------------------------------------
# Persistence round trip
# ---------------------------------------------------------------------------


def test_persist_and_load_round_trip(tmp_path) -> None:
    bible = _make_simple_bible()
    target = tmp_path / "world.json"
    bible.persist(target)

    loaded = Bible.load(target)
    assert loaded == bible


def test_persist_writes_indented_json(tmp_path) -> None:
    bible = Bible.empty(seed="x")
    target = tmp_path / "out.json"
    bible.persist(target)
    text = target.read_text(encoding="utf-8")
    # 2-space indent — easy to eyeball with a member field check.
    assert '\n  "canon_version": "0.1"' in text


def test_persist_creates_parent_directory(tmp_path) -> None:
    bible = Bible.empty(seed="x")
    target = tmp_path / "nested" / "deep" / "world.json"
    bible.persist(target)
    assert target.exists()


# ---------------------------------------------------------------------------
# A8: Ability typed model
# ---------------------------------------------------------------------------


def test_ability_builds_with_required_fields() -> None:
    ability = Ability(name="Power Strike", description="A heavy blow.", stat="STR")
    assert ability.name == "Power Strike"
    assert ability.description == "A heavy blow."
    assert ability.stat == "STR"
    assert ability.stamina_cost == 0


def test_ability_stamina_cost_default_zero() -> None:
    ability = Ability(name="Slash", stat="DEX")
    assert ability.stamina_cost == 0
    assert ability.description == ""


def test_ability_round_trip_json() -> None:
    ability = Ability(name="Battle Cry", description="Boosts morale.", stat="CHA", stamina_cost=5)
    payload = ability.model_dump_json()
    restored = Ability.model_validate_json(payload)
    assert restored == ability
    assert restored.stat == "CHA"
    assert restored.stamina_cost == 5


# ---------------------------------------------------------------------------
# A8: Spell typed model
# ---------------------------------------------------------------------------


def test_spell_builds_with_required_fields() -> None:
    spell = Spell(name="Fireball", spell_type="damage_single", element="fire", stat="INT")
    assert spell.name == "Fireball"
    assert spell.spell_type == "damage_single"
    assert spell.element == "fire"
    assert spell.stat == "INT"
    assert spell.targets == "single"
    assert spell.num_dice == 1
    assert spell.die_sides == 6
    assert spell.heal_amount == 0
    assert spell.buff_stat is None
    assert spell.buff_value == 0
    assert spell.buff_duration == 0
    assert spell.stamina_cost == 0
    assert spell.description == ""


def test_spell_round_trip_json() -> None:
    spell = Spell(
        name="Frost Nova",
        spell_type="damage_multi",
        element="frost",
        stat="INT",
        targets="multi",
        num_dice=2,
        die_sides=8,
        stamina_cost=6,
        description="Freezes all nearby enemies.",
    )
    payload = spell.model_dump_json()
    restored = Spell.model_validate_json(payload)
    assert restored == spell
    assert restored.targets == "multi"
    assert restored.num_dice == 2


def test_spell_buff_fields() -> None:
    spell = Spell(
        name="Haste",
        spell_type="buff_stat",
        element="light",
        stat="WIS",
        buff_stat="DEX",
        buff_value=4,
        buff_duration=3,
    )
    assert spell.buff_stat == "DEX"
    assert spell.buff_value == 4
    assert spell.buff_duration == 3


# ---------------------------------------------------------------------------
# A8: CharacterClass with typed abilities/spells
# ---------------------------------------------------------------------------


def test_character_class_accepts_typed_abilities() -> None:
    cls = CharacterClass(
        archetype_id="warrior",
        abilities=[Ability(name="Power Strike", description="Heavy blow.", stat="STR")],
    )
    assert len(cls.abilities) == 1
    assert isinstance(cls.abilities[0], Ability)
    assert cls.abilities[0].name == "Power Strike"


def test_character_class_accepts_typed_spells() -> None:
    cls = CharacterClass(
        archetype_id="mage",
        spells=[Spell(name="Fireball", spell_type="damage_single", element="fire", stat="INT")],
    )
    assert len(cls.spells) == 1
    assert isinstance(cls.spells[0], Spell)


def test_character_class_rejects_string_abilities() -> None:
    """Abilities are now typed; passing a raw string must fail validation."""
    with pytest.raises(ValidationError):
        CharacterClass(archetype_id="warrior", abilities=["ability_id_string"])


def test_character_class_abilities_default_empty() -> None:
    cls = CharacterClass(archetype_id="a")
    assert cls.abilities == []
    assert cls.spells == []


# ---------------------------------------------------------------------------
# A7: Character extension — new fields with defaults
# ---------------------------------------------------------------------------


def test_character_new_fields_all_default() -> None:
    char = Character(character_id="x", name="y", role="npc")
    assert char.job is None
    assert char.hobby is None
    assert char.opening_greeting is None
    assert char.portrait_prompt is None
    assert char.personality_notes == []
    assert char.exhausted_dialogue is None
    assert char.traits == {}
    assert char.dialogue_tree_complete_id is None
    assert char.dialogue_tree_failed_id is None
    assert char.dialogue_tree_incomplete_id is None


def test_character_job_and_hobby_round_trip() -> None:
    char = Character(
        character_id="npc_001",
        name="Jorik",
        role="npc",
        job="former pit fighter turned reluctant smuggler",
        hobby="mapping guard rotations",
        opening_greeting="You look like someone who can handle themselves.",
        portrait_prompt="a stocky man with scarred knuckles",
        personality_notes=["Gruff but protective", "Mistrusts authority"],
        exhausted_dialogue="That's all the planning I can handle for now.",
        traits={"is_story_npc": False, "max_exchanges": 5},
        dialogue_tree_complete_id="tree_1001_complete",
        dialogue_tree_failed_id="tree_1001_failed",
        dialogue_tree_incomplete_id="tree_1001_incomplete",
    )
    payload = char.model_dump_json()
    restored = Character.model_validate_json(payload)
    assert restored.job == "former pit fighter turned reluctant smuggler"
    assert restored.hobby == "mapping guard rotations"
    assert restored.opening_greeting == "You look like someone who can handle themselves."
    assert restored.portrait_prompt == "a stocky man with scarred knuckles"
    assert restored.personality_notes == ["Gruff but protective", "Mistrusts authority"]
    assert restored.exhausted_dialogue == "That's all the planning I can handle for now."
    assert restored.traits == {"is_story_npc": False, "max_exchanges": 5}
    assert restored.dialogue_tree_complete_id == "tree_1001_complete"
    assert restored.dialogue_tree_failed_id == "tree_1001_failed"
    assert restored.dialogue_tree_incomplete_id == "tree_1001_incomplete"


# ---------------------------------------------------------------------------
# A8: ClassArchetype — new typed ability/spell fields
# ---------------------------------------------------------------------------


def test_class_archetype_new_fields_all_default() -> None:
    arch = ClassArchetype(archetype_id="warrior", name="Warrior")
    assert arch.archetype == ""
    assert arch.flavor_text is None
    assert arch.starting_weapon is None
    assert arch.portrait_prompt is None
    assert arch.portrait_path is None
    assert arch.abilities == []
    assert arch.spells == []
    assert arch.ability_pool == []
    assert arch.spell_pool == []


def test_class_archetype_with_typed_abilities_and_pool() -> None:
    ability = Ability(name="Inferno Smash", description="...", stat="STR", stamina_cost=8)
    pool_ability = Ability(name="Ruin Climber", description="...", stat="STR", stamina_cost=4)
    arch = ClassArchetype(
        archetype_id="warrior_ruins",
        archetype="warrior",
        name="Flameguard Breaker",
        flavor_text="A veteran warrior who specializes in demolishing ancient barriers.",
        starting_weapon="Ember-Wreathed Maul",
        portrait_prompt="A muscular warrior in scorched plate armor.",
        abilities=[ability],
        ability_pool=[pool_ability],
    )
    assert arch.archetype == "warrior"
    assert arch.flavor_text is not None
    assert arch.starting_weapon == "Ember-Wreathed Maul"
    assert len(arch.abilities) == 1
    assert isinstance(arch.abilities[0], Ability)
    assert len(arch.ability_pool) == 1
    assert isinstance(arch.ability_pool[0], Ability)


def test_class_archetype_rejects_string_in_ability_pool() -> None:
    with pytest.raises(ValidationError):
        ClassArchetype(
            archetype_id="warrior",
            name="Warrior",
            ability_pool=["some_ability_id"],
        )


def test_class_archetype_with_spell_pool() -> None:
    spell = Spell(name="Fireball", spell_type="damage_single", element="fire", stat="INT")
    arch = ClassArchetype(
        archetype_id="mage_fire",
        archetype="mage",
        name="Fire Mage",
        spell_pool=[spell],
    )
    assert len(arch.spell_pool) == 1
    assert isinstance(arch.spell_pool[0], Spell)


def test_class_archetype_round_trip_json_with_abilities() -> None:
    ability = Ability(name="Battle Cry", description="Boosts allies.", stat="CHA", stamina_cost=5)
    arch = ClassArchetype(
        archetype_id="paladin",
        archetype="paladin",
        name="Holy Paladin",
        abilities=[ability],
    )
    payload = arch.model_dump_json()
    restored = ClassArchetype.model_validate_json(payload)
    assert restored.archetype == "paladin"
    assert len(restored.abilities) == 1
    assert restored.abilities[0].name == "Battle Cry"
