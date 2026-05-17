"""Unit tests for ClassPhase.

Covers:
- Generates archetype_count archetypes from canned LLM responses
- Each archetype lands in both ctx.bible.class_archetypes and ctx.archetypes
- instantiate_for_roles=() means no characters get class_data
- instantiate_for_roles=("pc",) instantiates only PC characters
- archetype_id is auto-derived from name when not explicit (slug rule)
- A character with role="pc" gets class_data populated after instantiation
- A character with role="npc" does NOT get class_data when instantiate_for_roles=("pc",)
- Phase protocol satisfaction
- "classes" appears in ctx.bible.metadata.phases_run
- classes/classes.json is written as a flat array without archetype_id field
"""

from __future__ import annotations

import json
import random

from canon import (
    Bible,
    CanonConfig,
    Character,
    DefaultPromptSet,
    FakeLLMBackend,
    GenerationStats,
    LLMClient,
    PipelineContext,
)
from canon.pipeline.phases.class_archetype import ClassPhase, _slugify
from canon.pipeline.runner import Phase

# ---------------------------------------------------------------------------
# Canned responses
# ---------------------------------------------------------------------------

ARCHETYPE_RESPONSES = [
    json.dumps({
        "name": "Iron Sentinel",
        "description": "A heavily armored defender who holds the line.",
        "lore": "Born of conflict, the Iron Sentinel embodies resilience.",
        "role_tags": ["tank", "defender"],
        "ability_pool": ["shield_bash", "taunt"],
        "spell_pool": [],
        "starting_equipment": ["tower_shield", "plate_armor"],
        "stat_template": {"str": 16, "con": 14, "dex": 8},
    }),
    json.dumps({
        "name": "Arcane Weaver",
        "description": "A scholar of the hidden arts, bending reality through study.",
        "lore": "Magic is language; the Arcane Weaver speaks it fluently.",
        "role_tags": ["dps", "support"],
        "ability_pool": ["arcane_bolt"],
        "spell_pool": ["fireball", "ice_shard"],
        "starting_equipment": ["staff", "spellbook"],
        "stat_template": {"int": 18, "wis": 12, "str": 6},
    }),
    json.dumps({
        "name": "Verdant Mender",
        "description": "A healer who draws power from nature to restore allies.",
        "lore": "Life finds a way; the Verdant Mender ensures it.",
        "role_tags": ["healer", "support"],
        "ability_pool": ["nature_heal"],
        "spell_pool": ["regenerate", "cleanse"],
        "starting_equipment": ["herb_pouch", "wooden_staff"],
        "stat_template": {"wis": 16, "cha": 14, "str": 8},
    }),
    json.dumps({
        "name": "Shadow Trickster",
        "description": "A mercurial figure who thrives in chaos and misdirection.",
        "lore": "The best trick is the one they never see coming.",
        "role_tags": ["dps", "utility"],
        "ability_pool": ["vanish", "pocket_sand"],
        "spell_pool": [],
        "starting_equipment": ["throwing_knives", "smoke_bomb"],
        "stat_template": {"dex": 18, "cha": 14, "con": 8},
    }),
]


def make_ctx(
    responses: list[str],
    seed: str = "t",
    characters: list[Character] | None = None,
) -> PipelineContext:
    """Build a minimal PipelineContext with a FakeLLMBackend."""
    bible = Bible.empty(seed=seed)
    if characters:
        for char in characters:
            bible.add_character(char)
    backend = FakeLLMBackend(responses)
    llm = LLMClient(backend, stats=GenerationStats())
    return PipelineContext(
        bible=bible,
        config=CanonConfig(seed=seed),
        rng=random.Random(seed),
        llm=llm,
        prompts=DefaultPromptSet(),
    )


def make_character(character_id: str, name: str, role: str) -> Character:
    return Character(character_id=character_id, name=name, role=role, lore="A person.")


# ---------------------------------------------------------------------------
# Tests: archetype generation
# ---------------------------------------------------------------------------


class TestClassPhaseGeneratesArchetypes:
    def test_generates_archetype_count_archetypes(self):
        ctx = make_ctx(ARCHETYPE_RESPONSES)
        phase = ClassPhase(archetype_count=4)
        phase.run(ctx)
        assert len(ctx.bible.class_archetypes) == 4

    def test_all_archetypes_land_in_ctx_archetypes(self):
        ctx = make_ctx(ARCHETYPE_RESPONSES)
        phase = ClassPhase(archetype_count=4)
        phase.run(ctx)
        assert len(ctx.archetypes) == 4

    def test_bible_and_ctx_archetypes_share_same_keys(self):
        ctx = make_ctx(ARCHETYPE_RESPONSES)
        phase = ClassPhase(archetype_count=4)
        phase.run(ctx)
        assert set(ctx.bible.class_archetypes.keys()) == set(ctx.archetypes.keys())

    def test_archetype_objects_are_identical(self):
        ctx = make_ctx(ARCHETYPE_RESPONSES)
        phase = ClassPhase(archetype_count=4)
        phase.run(ctx)
        for key in ctx.archetypes:
            assert ctx.archetypes[key] is ctx.bible.class_archetypes[key]

    def test_fewer_than_four_archetypes(self):
        ctx = make_ctx(ARCHETYPE_RESPONSES[:2])
        phase = ClassPhase(archetype_count=2)
        phase.run(ctx)
        assert len(ctx.bible.class_archetypes) == 2

    def test_archetype_fields_populated(self):
        ctx = make_ctx(ARCHETYPE_RESPONSES[:1])
        phase = ClassPhase(archetype_count=1)
        phase.run(ctx)
        archetype = next(iter(ctx.bible.class_archetypes.values()))
        assert archetype.name == "Iron Sentinel"
        assert archetype.role_tags == ["tank", "defender"]
        # ability_pool is now typed Ability objects; check by ability name
        ability_names = [a.name for a in archetype.ability_pool]
        assert "shield_bash" in ability_names
        assert archetype.stat_template == {"str": 16, "con": 14, "dex": 8}

    def test_category_passed_to_archetype_from_response(self):
        response = json.dumps({"name": "Combat Ace", "description": "A fighter.", "category": "combat"})
        ctx = make_ctx([response])
        phase = ClassPhase(archetype_count=1, category="combat")
        phase.run(ctx)
        archetype = next(iter(ctx.bible.class_archetypes.values()))
        assert archetype.category == "combat"

    def test_category_falls_back_to_phase_category_when_response_omits_it(self):
        response = json.dumps({"name": "Mystery Archer", "description": "Shoots things."})
        ctx = make_ctx([response])
        phase = ClassPhase(archetype_count=1, category="ranged")
        phase.run(ctx)
        archetype = next(iter(ctx.bible.class_archetypes.values()))
        assert archetype.category == "ranged"


# ---------------------------------------------------------------------------
# Tests: archetype_id derivation
# ---------------------------------------------------------------------------


class TestArchetypeIdDerivation:
    def test_archetype_id_from_response_when_explicit(self):
        response = json.dumps({
            "archetype_id": "custom_sentinel_id",
            "name": "Iron Sentinel",
            "description": "Tank.",
        })
        ctx = make_ctx([response])
        phase = ClassPhase(archetype_count=1)
        phase.run(ctx)
        assert "custom_sentinel_id" in ctx.bible.class_archetypes

    def test_archetype_id_slugified_from_name_when_absent(self):
        response = json.dumps({"name": "Iron Sentinel", "description": "Tank."})
        ctx = make_ctx([response])
        phase = ClassPhase(archetype_count=1)
        phase.run(ctx)
        # "Iron Sentinel" -> "iron_sentinel"
        assert "iron_sentinel" in ctx.bible.class_archetypes

    def test_archetype_id_slugify_spaces_become_underscores(self):
        assert _slugify("Iron Sentinel") == "iron_sentinel"

    def test_archetype_id_slugify_special_chars(self):
        # Trailing ")" -> "_", then strip("_") removes it: "blade_master__elite"
        assert _slugify("Blade-Master (Elite)") == "blade_master__elite"

    def test_archetype_id_slugify_all_special(self):
        # When all chars are non-alphanumeric the fallback "archetype" kicks in
        assert _slugify("---") == "archetype"

    def test_archetype_id_slugify_empty_string(self):
        assert _slugify("") == "archetype"

    def test_archetype_id_slugify_lowercase(self):
        assert _slugify("MAGE") == "mage"

    def test_multiple_archetypes_get_distinct_ids(self):
        ctx = make_ctx(ARCHETYPE_RESPONSES)
        phase = ClassPhase(archetype_count=4)
        phase.run(ctx)
        ids = list(ctx.bible.class_archetypes.keys())
        assert len(ids) == len(set(ids)), "Archetype IDs must be unique"


# ---------------------------------------------------------------------------
# Tests: instantiate_for_roles behaviour
# ---------------------------------------------------------------------------


class TestInstantiateForRoles:
    def test_no_instantiation_when_roles_empty(self):
        pc = make_character("pc_1", "Hero", "pc")
        ctx = make_ctx(ARCHETYPE_RESPONSES, characters=[pc])
        phase = ClassPhase(archetype_count=4, instantiate_for_roles=())
        phase.run(ctx)
        hero = ctx.bible.characters[0]
        assert hero.class_data is None

    def test_pc_gets_class_data_with_default_roles(self):
        pc = make_character("pc_1", "Hero", "pc")
        ctx = make_ctx(ARCHETYPE_RESPONSES, characters=[pc])
        phase = ClassPhase(archetype_count=4, instantiate_for_roles=("pc",))
        phase.run(ctx)
        hero = ctx.bible.characters[0]
        assert hero.class_data is not None

    def test_npc_not_instantiated_when_only_pc_in_roles(self):
        npc = make_character("npc_1", "Tavern Keeper", "npc")
        ctx = make_ctx(ARCHETYPE_RESPONSES, characters=[npc])
        phase = ClassPhase(archetype_count=4, instantiate_for_roles=("pc",))
        phase.run(ctx)
        keeper = ctx.bible.characters[0]
        assert keeper.class_data is None

    def test_both_pc_and_npc_instantiated_when_both_in_roles(self):
        pc = make_character("pc_1", "Hero", "pc")
        npc = make_character("npc_1", "Guard", "npc")
        ctx = make_ctx(ARCHETYPE_RESPONSES, characters=[pc, npc])
        phase = ClassPhase(archetype_count=4, instantiate_for_roles=("pc", "npc"))
        phase.run(ctx)
        hero = ctx.bible.get_character("pc_1")
        guard = ctx.bible.get_character("npc_1")
        assert hero.class_data is not None
        assert guard.class_data is not None

    def test_class_data_archetype_id_is_a_known_archetype(self):
        pc = make_character("pc_1", "Hero", "pc")
        ctx = make_ctx(ARCHETYPE_RESPONSES, characters=[pc])
        phase = ClassPhase(archetype_count=4, instantiate_for_roles=("pc",))
        phase.run(ctx)
        hero = ctx.bible.characters[0]
        assert hero.class_data is not None
        assert hero.class_data.archetype_id in ctx.bible.class_archetypes

    def test_class_data_stats_copied_from_archetype_template(self):
        """CharacterClass.stats starts as a copy of the archetype's stat_template."""
        pc = make_character("pc_1", "Hero", "pc")
        ctx = make_ctx(ARCHETYPE_RESPONSES[:1], characters=[pc])
        phase = ClassPhase(archetype_count=1, instantiate_for_roles=("pc",))
        phase.run(ctx)
        hero = ctx.bible.characters[0]
        archetype = ctx.bible.class_archetypes[hero.class_data.archetype_id]
        assert hero.class_data.stats == archetype.stat_template

    def test_class_data_stats_is_a_copy_not_reference(self):
        """Mutating class_data.stats does not affect the archetype template."""
        pc = make_character("pc_1", "Hero", "pc")
        ctx = make_ctx(ARCHETYPE_RESPONSES[:1], characters=[pc])
        phase = ClassPhase(archetype_count=1, instantiate_for_roles=("pc",))
        phase.run(ctx)
        hero = ctx.bible.characters[0]
        archetype = ctx.bible.class_archetypes[hero.class_data.archetype_id]
        hero.class_data.stats["str"] = 99
        assert archetype.stat_template.get("str") != 99

    def test_class_data_abilities_copied_from_archetype(self):
        pc = make_character("pc_1", "Hero", "pc")
        ctx = make_ctx(ARCHETYPE_RESPONSES[:1], characters=[pc])
        phase = ClassPhase(archetype_count=1, instantiate_for_roles=("pc",))
        phase.run(ctx)
        hero = ctx.bible.characters[0]
        archetype = ctx.bible.class_archetypes[hero.class_data.archetype_id]
        assert hero.class_data.abilities == archetype.ability_pool

    def test_instantiation_without_skeleton_spec_gives_empty_skeleton(self):
        """When no SkeletonSpec exists for the archetype, skeleton is {}."""
        pc = make_character("pc_1", "Hero", "pc")
        ctx = make_ctx(ARCHETYPE_RESPONSES[:1], characters=[pc])
        # ctx.schemas is empty by default
        phase = ClassPhase(archetype_count=1, instantiate_for_roles=("pc",))
        phase.run(ctx)
        hero = ctx.bible.characters[0]
        assert hero.class_data is not None
        assert hero.class_data.skeleton == {}

    def test_instantiation_uses_skeleton_spec_when_available(self):
        """When a SkeletonSpec is keyed by archetype_id, roll_skeleton is called."""
        from canon.skeleton.core import SkeletonField, SkeletonSpec

        pc = make_character("pc_1", "Hero", "pc")
        ctx = make_ctx(ARCHETYPE_RESPONSES[:1], characters=[pc])
        phase = ClassPhase(archetype_count=1, instantiate_for_roles=("pc",))
        phase.run(ctx)

        hero = ctx.bible.characters[0]
        archetype_id = hero.class_data.archetype_id

        # Re-run with a spec registered under the archetype_id
        pc2 = make_character("pc_2", "Hero Two", "pc")
        spec = SkeletonSpec(
            entity_type="character_class",
            fields={"level": SkeletonField(range=(1, 5))},
        )
        ctx2 = make_ctx(ARCHETYPE_RESPONSES[:1], characters=[pc2])
        ctx2.schemas[archetype_id] = spec
        phase.run(ctx2)

        hero2 = ctx2.bible.characters[0]
        assert hero2.class_data is not None
        assert "level" in hero2.class_data.skeleton

    def test_generic_character_class_spec_used_as_fallback(self):
        """ctx.schemas['character_class'] is used when no archetype-id-specific spec exists."""
        from canon.skeleton.core import SkeletonField, SkeletonSpec

        pc = make_character("pc_1", "Hero", "pc")
        ctx = make_ctx(ARCHETYPE_RESPONSES[:1], characters=[pc])
        ctx.schemas["character_class"] = SkeletonSpec(
            entity_type="character_class",
            fields={"level": SkeletonField(range=(1, 10))},
        )
        phase = ClassPhase(archetype_count=1, instantiate_for_roles=("pc",))
        phase.run(ctx)
        hero = ctx.bible.characters[0]
        assert "level" in hero.class_data.skeleton

    def test_no_characters_with_roles_leaves_all_untouched(self):
        """instantiate_for_roles=("pc",) is a no-op when all characters are NPCs."""
        npc1 = make_character("npc_1", "Guard A", "npc")
        npc2 = make_character("npc_2", "Guard B", "npc")
        ctx = make_ctx(ARCHETYPE_RESPONSES, characters=[npc1, npc2])
        phase = ClassPhase(archetype_count=4, instantiate_for_roles=("pc",))
        phase.run(ctx)
        for char in ctx.bible.characters:
            assert char.class_data is None

    def test_instantiate_with_empty_bible_characters(self):
        """No characters — phase completes without error."""
        ctx = make_ctx(ARCHETYPE_RESPONSES)
        phase = ClassPhase(archetype_count=4, instantiate_for_roles=("pc",))
        phase.run(ctx)
        assert len(ctx.bible.class_archetypes) == 4


# ---------------------------------------------------------------------------
# Tests: partial failure / edge cases
# ---------------------------------------------------------------------------


class TestPartialFailureHandling:
    def test_invalid_json_response_is_skipped_with_fallback_name(self):
        """When LLM returns non-JSON, retry_with_feedback uses the fallback.

        The fallback is {"name": "archetype_0", "description": ""} which is
        valid and produces an archetype named archetype_0.
        """
        ctx = make_ctx(["not json at all"])
        phase = ClassPhase(archetype_count=1)
        phase.run(ctx)
        # Fallback is still valid — produces one archetype
        assert len(ctx.bible.class_archetypes) == 1
        archetype = next(iter(ctx.bible.class_archetypes.values()))
        assert archetype.name == "archetype_0"

    def test_missing_name_retries_and_falls_back(self):
        """When LLM returns JSON without 'name', it fails validation and uses fallback."""
        bad_response = json.dumps({"description": "No name here."})
        ctx = make_ctx([bad_response, bad_response, bad_response])  # all retries fail
        phase = ClassPhase(archetype_count=1)
        phase.run(ctx)
        # fallback {"name": "archetype_0", ...} used after retries exhausted
        assert len(ctx.bible.class_archetypes) == 1
        archetype = next(iter(ctx.bible.class_archetypes.values()))
        assert archetype.name == "archetype_0"

    def test_partial_generation_still_records_successful_archetypes(self):
        """If 2 of 4 responses are invalid, 2 valid archetypes are still registered."""
        bad = json.dumps({"description": "no name"})
        good = json.dumps({"name": "Solid Archetype", "description": "Works fine."})
        ctx = make_ctx([good, bad, bad, bad, good, bad, bad, bad])
        phase = ClassPhase(archetype_count=2)
        phase.run(ctx)
        # Both archetypes fall back: bad JSON -> fallback name used
        assert len(ctx.bible.class_archetypes) == 2


# ---------------------------------------------------------------------------
# Tests: metadata / protocol
# ---------------------------------------------------------------------------


class TestClassPhaseMetadata:
    def test_phases_run_includes_classes(self):
        ctx = make_ctx(ARCHETYPE_RESPONSES)
        phase = ClassPhase(archetype_count=4)
        phase.run(ctx)
        assert "classes" in ctx.bible.metadata.phases_run

    def test_phases_run_appends_on_each_run(self):
        ctx = make_ctx(ARCHETYPE_RESPONSES * 2)
        phase = ClassPhase(archetype_count=4)
        phase.run(ctx)
        phase.run(ctx)
        assert ctx.bible.metadata.phases_run.count("classes") == 2

    def test_phase_name_attribute(self):
        phase = ClassPhase()
        assert phase.name == "classes"


class TestClassPhaseProtocol:
    def test_satisfies_phase_protocol(self):
        phase = ClassPhase(archetype_count=4)
        assert isinstance(phase, Phase)

    def test_has_name_attribute(self):
        assert hasattr(ClassPhase, "name")
        assert ClassPhase.name == "classes"

    def test_has_run_method(self):
        assert callable(ClassPhase().run)

    def test_run_accepts_context(self):
        ctx = make_ctx(ARCHETYPE_RESPONSES)
        phase = ClassPhase(archetype_count=4)
        # Must not raise
        phase.run(ctx)


# ---------------------------------------------------------------------------
# Tests: constructor parameter handling
# ---------------------------------------------------------------------------


class TestClassPhaseConstructor:
    def test_default_archetype_count(self):
        phase = ClassPhase()
        assert phase.archetype_count == 4

    def test_custom_archetype_count(self):
        phase = ClassPhase(archetype_count=7)
        assert phase.archetype_count == 7

    def test_default_instantiate_for_roles(self):
        phase = ClassPhase()
        assert phase.instantiate_for_roles == ("pc",)

    def test_instantiate_for_roles_as_list_coerced_to_tuple(self):
        phase = ClassPhase(instantiate_for_roles=["pc", "npc"])
        assert isinstance(phase.instantiate_for_roles, tuple)
        assert phase.instantiate_for_roles == ("pc", "npc")

    def test_default_category_is_none(self):
        phase = ClassPhase()
        assert phase.category is None

    def test_category_stored(self):
        phase = ClassPhase(category="support")
        assert phase.category == "support"


# ---------------------------------------------------------------------------
# v0.2 file persistence: classes/classes.json
# ---------------------------------------------------------------------------


_RICH_ARCHETYPE_RESPONSE = json.dumps({
    "name": "Flameguard Breaker",
    "archetype": "warrior",
    "description": "A veteran warrior.",
    "flavor_text": "Fire and fury made flesh.",
    "starting_weapon": "Ember-Wreathed Maul",
    "portrait_prompt": "A muscular warrior in scorched plate armor.",
    "stats": {"STR": 18, "DEX": 14, "CON": 17, "INT": 10, "WIS": 10, "CHA": 16, "LUCK": 10},
    "abilities": [
        {"name": "Inferno Smash", "description": "Crush.", "stat": "STR", "stamina_cost": 8},
    ],
    "spells": [],
    "ability_pool": [
        {"name": "Ruin Climber", "description": "Scale ruins.", "stat": "STR", "stamina_cost": 4},
    ],
    "spell_pool": [],
})


def make_ctx_with_output(responses, tmp_path, seed="t"):
    bible = Bible.empty(seed=seed)
    backend = FakeLLMBackend(responses)
    llm = LLMClient(backend, stats=GenerationStats())
    config = CanonConfig(seed=seed, output_dir=tmp_path)
    return PipelineContext(
        bible=bible,
        config=config,
        rng=random.Random(seed),
        llm=llm,
        prompts=DefaultPromptSet(),
    )


class TestClassPhaseFilePersistence:
    def test_classes_json_written(self, tmp_path):
        """ClassPhase writes classes/classes.json after run."""
        ctx = make_ctx_with_output([_RICH_ARCHETYPE_RESPONSE], tmp_path)
        ClassPhase(archetype_count=1).run(ctx)
        classes_path = tmp_path / "classes" / "classes.json"
        assert classes_path.exists(), f"Expected {classes_path} to exist"

    def test_classes_json_is_array(self, tmp_path):
        """classes.json is a JSON array (not an object)."""
        ctx = make_ctx_with_output([_RICH_ARCHETYPE_RESPONSE], tmp_path)
        ClassPhase(archetype_count=1).run(ctx)
        import json as _json
        data = _json.loads((tmp_path / "classes" / "classes.json").read_text())
        assert isinstance(data, list)

    def test_classes_json_length_matches_archetype_count(self, tmp_path):
        """classes.json has one entry per generated archetype."""
        # Use two distinct responses so the archetypes have distinct IDs
        response_2 = json.dumps({
            "name": "Shadow Trickster",
            "archetype": "rogue",
            "description": "A mercurial figure.",
            "abilities": [],
            "spells": [],
            "ability_pool": [],
            "spell_pool": [],
        })
        ctx = make_ctx_with_output([_RICH_ARCHETYPE_RESPONSE, response_2], tmp_path)
        ClassPhase(archetype_count=2).run(ctx)
        import json as _json
        data = _json.loads((tmp_path / "classes" / "classes.json").read_text())
        assert len(data) == 2

    def test_classes_json_entry_has_no_archetype_id(self, tmp_path):
        """classes.json entries do NOT contain archetype_id (mazeworld is positional)."""
        ctx = make_ctx_with_output([_RICH_ARCHETYPE_RESPONSE], tmp_path)
        ClassPhase(archetype_count=1).run(ctx)
        import json as _json
        data = _json.loads((tmp_path / "classes" / "classes.json").read_text())
        entry = data[0]
        assert "archetype_id" not in entry, "archetype_id must not appear in classes.json entries"

    def test_classes_json_entry_has_name(self, tmp_path):
        """classes.json entries contain the 'name' field."""
        ctx = make_ctx_with_output([_RICH_ARCHETYPE_RESPONSE], tmp_path)
        ClassPhase(archetype_count=1).run(ctx)
        import json as _json
        data = _json.loads((tmp_path / "classes" / "classes.json").read_text())
        assert data[0]["name"] == "Flameguard Breaker"

    def test_classes_json_entry_has_abilities_list(self, tmp_path):
        """classes.json entries include abilities as a list of objects."""
        ctx = make_ctx_with_output([_RICH_ARCHETYPE_RESPONSE], tmp_path)
        ClassPhase(archetype_count=1).run(ctx)
        import json as _json
        data = _json.loads((tmp_path / "classes" / "classes.json").read_text())
        entry = data[0]
        assert "abilities" in entry
        assert isinstance(entry["abilities"], list)
        if entry["abilities"]:
            ab = entry["abilities"][0]
            assert "name" in ab
            assert "stat" in ab

    def test_classes_json_new_mazeworld_fields_present(self, tmp_path):
        """classes.json entries contain mazeworld-shape fields like flavor_text and starting_weapon."""
        ctx = make_ctx_with_output([_RICH_ARCHETYPE_RESPONSE], tmp_path)
        ClassPhase(archetype_count=1).run(ctx)
        import json as _json
        data = _json.loads((tmp_path / "classes" / "classes.json").read_text())
        entry = data[0]
        assert entry.get("flavor_text") == "Fire and fury made flesh."
        assert entry.get("starting_weapon") == "Ember-Wreathed Maul"
        assert entry.get("archetype") == "warrior"
        assert entry.get("portrait_prompt") == "A muscular warrior in scorched plate armor."

    def test_classes_json_respects_custom_output_paths(self, tmp_path):
        """Custom output_paths['classes'] changes where classes.json is written."""
        ctx = make_ctx_with_output([_RICH_ARCHETYPE_RESPONSE], tmp_path)
        object.__setattr__(ctx.config, "output_paths", {"classes": "custom/my_classes.json"})
        ClassPhase(archetype_count=1).run(ctx)
        assert (tmp_path / "custom" / "my_classes.json").exists()

    def test_archetype_fields_on_bible_object(self, tmp_path):
        """The ClassArchetype stored in ctx.bible has the new mazeworld fields populated."""
        ctx = make_ctx_with_output([_RICH_ARCHETYPE_RESPONSE], tmp_path)
        ClassPhase(archetype_count=1).run(ctx)
        arch = next(iter(ctx.bible.class_archetypes.values()))
        assert arch.archetype == "warrior"
        assert arch.flavor_text == "Fire and fury made flesh."
        assert arch.starting_weapon == "Ember-Wreathed Maul"
        assert arch.portrait_prompt == "A muscular warrior in scorched plate armor."
        # abilities should be typed Ability objects
        assert len(arch.abilities) >= 1
        assert arch.abilities[0].name == "Inferno Smash"
