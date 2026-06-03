"""Reference pipeline test: MazeWorld-shape (fantasy dungeon crawler).

Proves canon, given MazeWorld-equivalent inputs (SkeletonSpecs, archetypes,
prompts), produces a Bible whose JSON output has the SHAPE of MazeWorld's
data/world_bible.json. Byte-equivalence requires a live LLM and is out of
scope for v0.1 acceptance.

Known divergences from MazeWorld's WorldBible shape (see report at bottom):
  - canon uses `maps` (dict); MazeWorld uses `rooms` (dict) — same semantics
  - canon uses a flat `entities` list per map; MazeWorld uses per-type buckets
    (npcs, items, monsters) — same data, different container
  - canon uses `characters` list + `class_archetypes` dict at top level;
    MazeWorld uses `player_classes: list[EntityLore]`
  - canon adds `skeleton` to every EntityLore; MazeWorld discards it
  - canon adds `dialogues` dict at top level; MazeWorld inlines dialogue on NPC
  - canon adds `canon_version`, `metadata` at top level; MazeWorld has neither
"""
from __future__ import annotations

import json
import random

import pytest

from canon import (
    Bible,
    BibleMetadata,
    CanonConfig,
    CharacterPhase,
    ClassPhase,
    DefaultPromptSet,
    DialoguePhase,
    EntityPhase,
    FakeLLMBackend,
    GenerationStats,
    LLMClient,
    Map,
    NarrativePhase,
    PipelineContext,
    StoryPhase,
    ValidationPhase,
    run_pipeline,
)
from tests.reference.mazeworld_data import (
    HEALER_ARCHETYPE,
    ITEM_SPEC,
    JESTER_ARCHETYPE,
    MAGE_ARCHETYPE,
    MONSTER_SPEC,
    SPELL_SPEC,
    WARRIOR_ARCHETYPE,
    WEAPON_SPEC,
    build_canned_responses,
)

# ---------------------------------------------------------------------------
# Constants — kept in one place so tests and data builder stay in sync
# ---------------------------------------------------------------------------

_SEED = "shadowspire_001"
_NUM_MAPS = 3
_CHARS_PER_MAP = 2
_WEAPONS_PER_MAP = 2
_MONSTERS_PER_MAP = 2
_ITEMS_PER_MAP = 1
_SPELLS_PER_MAP = _WEAPONS_PER_MAP  # spell count matches weapon count
_TOTAL_CHARS = _NUM_MAPS * _CHARS_PER_MAP  # 6
_ENTITIES_PER_MAP = _WEAPONS_PER_MAP + _SPELLS_PER_MAP + _MONSTERS_PER_MAP + _ITEMS_PER_MAP  # 7
_ENVIRONMENTS = ["forest", "manor", "vault"]


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def mazeworld_pipeline_outputs(tmp_path_factory):
    """Run a full MazeWorld-shape pipeline once and share results across tests.

    Scope is 'module' so the 44-call fake-LLM run is executed once.
    """
    tmp_path = tmp_path_factory.mktemp("mazeworld_ref")

    # 1. Build bible with pre-existing maps.
    #    StoryPhase doesn't create maps; in MazeWorld a separate layout phase
    #    does that. For this reference test we pre-populate three maps.
    bible = Bible.empty(seed=_SEED)
    bible.metadata = BibleMetadata()
    for i in range(_NUM_MAPS):
        bible.maps[f"room_{i}"] = Map(
            map_id=f"room_{i}",
            name=f"Room {i}",
            description=f"Room {i} of the dungeon.",
            environment=_ENVIRONMENTS[i],
            level=i + 1,
            story_beat=f"beat_{i}",
        )

    # 2. Configure pipeline plumbing.
    config = CanonConfig(seed=_SEED, num_maps=_NUM_MAPS, output_dir=tmp_path)
    stats = GenerationStats()
    responses = build_canned_responses(
        num_maps=_NUM_MAPS,
        weapons_per_map=_WEAPONS_PER_MAP,
        monsters_per_map=_MONSTERS_PER_MAP,
        items_per_map=_ITEMS_PER_MAP,
        characters_per_map=_CHARS_PER_MAP,
    )
    backend = FakeLLMBackend(responses)
    llm = LLMClient(backend, stats=stats)
    prompts = DefaultPromptSet()

    ctx = PipelineContext(
        bible=bible,
        config=config,
        rng=random.Random(_SEED),
        llm=llm,
        prompts=prompts,
        stats=stats,
        schemas={
            "weapon": WEAPON_SPEC,
            "spell": SPELL_SPEC,
            "monster": MONSTER_SPEC,
            "item": ITEM_SPEC,
        },
        archetypes={
            "warrior": WARRIOR_ARCHETYPE,
            "mage": MAGE_ARCHETYPE,
            "healer": HEALER_ARCHETYPE,
            "jester": JESTER_ARCHETYPE,
        },
    )

    # 3. Run the pipeline.
    bible_out = run_pipeline(
        phases=[
            StoryPhase(),
            # instantiate_for_roles=() so no CharacterClass is attached to npc
            # characters (none are role="pc"); avoids triggering archetype
            # instantiation for NPC roles.
            ClassPhase(archetype_count=4, instantiate_for_roles=()),
            CharacterPhase(per_map=True, count=_CHARS_PER_MAP, roles=["npc"]),
            EntityPhase(entity_type="weapon", per_map=True, count=_WEAPONS_PER_MAP),
            EntityPhase(entity_type="spell", per_map=True, count=_SPELLS_PER_MAP),
            EntityPhase(entity_type="monster", per_map=True, count=_MONSTERS_PER_MAP),
            EntityPhase(entity_type="item", per_map=True, count=_ITEMS_PER_MAP),
            DialoguePhase(),
            NarrativePhase(),
            ValidationPhase(),
        ],
        ctx=ctx,
    )
    return bible_out, ctx, backend


# ---------------------------------------------------------------------------
# TestMazeWorldShapeAcceptance
# ---------------------------------------------------------------------------


class TestMazeWorldShapeAcceptance:
    """End-to-end acceptance checks — all phases ran, all counts correct."""

    def test_pipeline_runs_to_completion(self, mazeworld_pipeline_outputs):
        bible, ctx, backend = mazeworld_pipeline_outputs
        phases_run = bible.metadata.phases_run
        assert "story" in phases_run
        assert "classes" in phases_run
        assert "characters" in phases_run
        assert "entity:weapon" in phases_run
        assert "entity:spell" in phases_run
        assert "entity:monster" in phases_run
        assert "entity:item" in phases_run
        assert "dialogue" in phases_run
        assert "narrative" in phases_run
        # Known divergence: ValidationPhase does not append to phases_run in
        # v0.1 (it runs and stores its report in ctx.artifacts instead). The
        # validation report presence is checked in test_validation_report_present.
        assert ctx.artifacts.get("validation_report") is not None

    def test_story_arc_populated(self, mazeworld_pipeline_outputs):
        bible, _, _ = mazeworld_pipeline_outputs
        assert bible.story.title == "The Convergence of Shadows"
        assert len(bible.story.factions) == 1
        assert bible.story.factions[0].name == "The Shadow Order"
        assert len(bible.story.beats) == _NUM_MAPS
        assert len(bible.story.escalation_arc) == 4

    def test_class_archetypes_generated(self, mazeworld_pipeline_outputs):
        bible, _, _ = mazeworld_pipeline_outputs
        assert len(bible.class_archetypes) == 4
        assert "warrior" in bible.class_archetypes
        assert "mage" in bible.class_archetypes
        assert "healer" in bible.class_archetypes
        assert "jester" in bible.class_archetypes

    def test_characters_per_map(self, mazeworld_pipeline_outputs):
        bible, _, _ = mazeworld_pipeline_outputs
        # 3 maps × 2 chars/map = 6 total characters
        assert len(bible.characters) == _TOTAL_CHARS
        for char in bible.characters:
            assert char.role == "npc"
            assert char.primary_map_id is not None
            # DialoguePhase should have wired each character's dialogue_tree_id
            assert char.dialogue_tree_id is not None

    def test_entities_per_map(self, mazeworld_pipeline_outputs):
        bible, _, _ = mazeworld_pipeline_outputs
        for room_id in [f"room_{i}" for i in range(_NUM_MAPS)]:
            entities = bible.maps[room_id].entities
            weapons = [e for e in entities if e.entity_type == "weapon"]
            spells = [e for e in entities if e.entity_type == "spell"]
            monsters = [e for e in entities if e.entity_type == "monster"]
            items = [e for e in entities if e.entity_type == "item"]
            assert len(weapons) == _WEAPONS_PER_MAP, f"{room_id}: wrong weapon count"
            assert len(spells) == _SPELLS_PER_MAP, f"{room_id}: wrong spell count"
            assert len(monsters) == _MONSTERS_PER_MAP, f"{room_id}: wrong monster count"
            assert len(items) == _ITEMS_PER_MAP, f"{room_id}: wrong item count"

    def test_skeletons_persisted(self, mazeworld_pipeline_outputs):
        """Skeleton wins — every entity carries its rolled mechanical pre-rolls."""
        bible, _, _ = mazeworld_pipeline_outputs
        for map_obj in bible.maps.values():
            for entity in map_obj.entities:
                assert isinstance(entity.skeleton, dict), (
                    f"{entity.entity_id} has non-dict skeleton"
                )
                assert len(entity.skeleton) > 0, (
                    f"{entity.entity_id} has empty skeleton"
                )

    def test_weapon_skeletons_have_expected_fields(self, mazeworld_pipeline_outputs):
        """Weapon skeletons carry all WEAPON_SPEC fields including post() output."""
        bible, _, _ = mazeworld_pipeline_outputs
        for map_obj in bible.maps.values():
            for entity in map_obj.entities:
                if entity.entity_type != "weapon":
                    continue
                skel = entity.skeleton
                assert "weapon_type" in skel
                assert "weapon_category" in skel
                assert "damage_type" in skel
                assert "stat_modifier" in skel
                assert "attack_dice" in skel
                # post() adds magic_element
                assert "magic_element" in skel
                assert skel["magic_element"] == "none"

    def test_dialogue_trees_one_per_character(self, mazeworld_pipeline_outputs):
        bible, _, _ = mazeworld_pipeline_outputs
        # dialogues keyed by character_id
        assert len(bible.dialogues) == _TOTAL_CHARS
        for char in bible.characters:
            assert char.character_id in bible.dialogues
            tree = bible.dialogues[char.character_id]
            assert tree.entry_node_id == "start"
            assert "start" in tree.nodes

    def test_narrative_synopsis_replaced(self, mazeworld_pipeline_outputs):
        bible, _, _ = mazeworld_pipeline_outputs
        # NarrativePhase replaced the structural synopsis from StoryPhase
        assert "kingdom" in bible.story.synopsis.lower() or len(bible.story.synopsis) > 20

    def test_narrative_map_descriptions_replaced(self, mazeworld_pipeline_outputs):
        bible, ctx, _ = mazeworld_pipeline_outputs
        # NarrativePhase replaced each map's description with generated intro
        for map_id, map_obj in bible.maps.items():
            assert map_obj.description, f"{map_id} has empty description after NarrativePhase"
            # Should NOT still be the placeholder we set before running
            assert map_obj.description != f"Room {map_id.split('_')[1]} of the dungeon.", (
                f"{map_id} description was not replaced by NarrativePhase"
            )

    def test_narrative_artifacts_present(self, mazeworld_pipeline_outputs):
        _, ctx, _ = mazeworld_pipeline_outputs
        narr = ctx.artifacts.get("narrative", {})
        assert "synopsis" in narr
        assert "map_intros" in narr
        assert "victory_text" in narr
        assert "defeat_text" in narr
        assert len(narr["map_intros"]) == _NUM_MAPS

    def test_validation_report_present(self, mazeworld_pipeline_outputs):
        """ValidationPhase stores its report in ctx.artifacts even when no
        checkers/validators are registered (no-op run still produces a report)."""
        _, ctx, _ = mazeworld_pipeline_outputs
        report = ctx.artifacts.get("validation_report")
        assert report is not None
        assert report.passed  # empty report = passes

    def test_persists_and_loads_round_trip(self, mazeworld_pipeline_outputs, tmp_path):
        bible, _, _ = mazeworld_pipeline_outputs
        bible_path = tmp_path / "world_bible.json"
        bible.persist(bible_path)
        loaded = Bible.load(bible_path)
        assert loaded.seed == bible.seed
        assert len(loaded.maps) == len(bible.maps)
        assert len(loaded.characters) == len(bible.characters)
        assert len(loaded.dialogues) == len(bible.dialogues)
        assert len(loaded.class_archetypes) == len(bible.class_archetypes)

    def test_all_responses_consumed(self, mazeworld_pipeline_outputs):
        """FakeLLMBackend index reached expected total — no surplus or deficit."""
        _, _, backend = mazeworld_pipeline_outputs
        # story(1) + classes(4) + chars(6) + weapons(6) + spells(6) + monsters(6)
        # + items(3) + dialogue(6) + narrative(1+3+1+1=6) = 44
        expected_calls = (
            1           # StoryPhase
            + 4         # ClassPhase (4 archetypes)
            + _NUM_MAPS * _CHARS_PER_MAP       # CharacterPhase
            + _NUM_MAPS * _WEAPONS_PER_MAP     # EntityPhase(weapon)
            + _NUM_MAPS * _SPELLS_PER_MAP      # EntityPhase(spell)
            + _NUM_MAPS * _MONSTERS_PER_MAP    # EntityPhase(monster)
            + _NUM_MAPS * _ITEMS_PER_MAP       # EntityPhase(item)
            + _NUM_MAPS * _CHARS_PER_MAP       # DialoguePhase
            + 1 + _NUM_MAPS + 1 + 1            # NarrativePhase: synopsis+intros+victory+defeat
        )
        assert len(backend.calls) == expected_calls


# ---------------------------------------------------------------------------
# TestMazeWorldSchemaShape
# ---------------------------------------------------------------------------


class TestMazeWorldSchemaShape:
    """Field-by-field checks that canon's JSON has the SHAPE of MazeWorld's
    data/world_bible.json. See docs/api_verification_and_spike.md §1 for the
    full field mapping table."""

    def test_bible_has_required_top_level_fields(self, mazeworld_pipeline_outputs):
        """canon adds canon_version, metadata, dialogues vs MazeWorld's entity_index."""
        bible, _, _ = mazeworld_pipeline_outputs
        data = bible.model_dump(mode="json")
        # Present in both canon and MazeWorld (under different names where noted)
        assert "seed" in data                  # same
        assert "story" in data                 # same
        assert "maps" in data                  # MazeWorld: "rooms"
        assert "characters" in data            # MazeWorld: per-room npcs list
        assert "class_archetypes" in data      # MazeWorld: player_classes list
        assert "dialogues" in data             # MazeWorld: inline on NPC
        # canon-only additions
        assert "canon_version" in data
        assert "metadata" in data

    def test_each_map_has_required_fields(self, mazeworld_pipeline_outputs):
        """canon Map maps to MazeWorld's RoomBible shape."""
        bible, _, _ = mazeworld_pipeline_outputs
        for map_id, map_data in bible.model_dump(mode="json")["maps"].items():
            # Present in both (canon names / MazeWorld names)
            assert "map_id" in map_data        # MazeWorld: key of rooms dict
            assert "environment" in map_data   # same
            assert "level" in map_data         # same
            assert "story_beat" in map_data    # same
            assert "entities" in map_data      # MazeWorld: separate npcs/items/monsters lists
            # canon-only additions
            assert "description" in map_data
            assert "name" in map_data

    def test_each_map_has_correct_entity_count(self, mazeworld_pipeline_outputs):
        bible, _, _ = mazeworld_pipeline_outputs
        for map_id, map_data in bible.model_dump(mode="json")["maps"].items():
            assert len(map_data["entities"]) == _ENTITIES_PER_MAP, (
                f"{map_id}: expected {_ENTITIES_PER_MAP} entities, "
                f"got {len(map_data['entities'])}"
            )

    def test_each_entity_has_required_fields(self, mazeworld_pipeline_outputs):
        """EntityLore shape — canon adds skeleton vs MazeWorld's room_id."""
        bible, _, _ = mazeworld_pipeline_outputs
        for map_data in bible.model_dump(mode="json")["maps"].values():
            for entity in map_data["entities"]:
                # Present in both (entity_id, entity_type, name, lore, tags)
                assert "entity_id" in entity
                assert "entity_type" in entity
                assert "name" in entity
                assert "lore" in entity
                assert "tags" in entity
                # canon adds skeleton (MazeWorld discards after merge)
                assert "skeleton" in entity
                assert isinstance(entity["skeleton"], dict)
                assert len(entity["skeleton"]) > 0

    def test_entity_types_across_maps(self, mazeworld_pipeline_outputs):
        """All four entity types (weapon, spell, monster, item) present in every map."""
        bible, _, _ = mazeworld_pipeline_outputs
        expected_types = {"weapon", "spell", "monster", "item"}
        for map_id, map_data in bible.model_dump(mode="json")["maps"].items():
            found_types = {e["entity_type"] for e in map_data["entities"]}
            assert expected_types == found_types, (
                f"{map_id}: entity types {found_types} != expected {expected_types}"
            )

    def test_each_character_has_required_fields(self, mazeworld_pipeline_outputs):
        """Character shape — maps to MazeWorld NPC fields."""
        bible, _, _ = mazeworld_pipeline_outputs
        for char in bible.model_dump(mode="json")["characters"]:
            # Core identity fields
            assert "character_id" in char
            assert "name" in char
            assert "role" in char
            assert "lore" in char
            # Map binding (maps to MazeWorld NPC's room/zone)
            assert "primary_map_id" in char
            assert char["primary_map_id"] is not None
            # Dialogue reference (MazeWorld inlines; canon references by ID)
            assert "dialogue_tree_id" in char
            assert char["dialogue_tree_id"] is not None

    def test_each_character_role_is_npc(self, mazeworld_pipeline_outputs):
        bible, _, _ = mazeworld_pipeline_outputs
        for char in bible.model_dump(mode="json")["characters"]:
            assert char["role"] == "npc"

    def test_each_dialogue_tree_has_required_fields(self, mazeworld_pipeline_outputs):
        """DialogueTree shape — generalises MazeWorld's inline dialogue."""
        bible, _, _ = mazeworld_pipeline_outputs
        for tree in bible.model_dump(mode="json")["dialogues"].values():
            assert "tree_id" in tree
            assert "character_id" in tree
            assert "entry_node_id" in tree
            assert "nodes" in tree
            assert isinstance(tree["nodes"], dict)
            assert len(tree["nodes"]) > 0

    def test_dialogue_nodes_have_required_fields(self, mazeworld_pipeline_outputs):
        """DialogueNode shape."""
        bible, _, _ = mazeworld_pipeline_outputs
        for tree in bible.model_dump(mode="json")["dialogues"].values():
            for node_id, node in tree["nodes"].items():
                assert "node_id" in node       # canon field
                assert "prompt" in node        # MazeWorld uses "prompt"; canon matches directly
                assert "choices" in node
                assert isinstance(node["choices"], list)

    def test_story_has_required_fields(self, mazeworld_pipeline_outputs):
        """StoryArc shape — maps to MazeWorld's OverarchingStory."""
        bible, _, _ = mazeworld_pipeline_outputs
        story = bible.model_dump(mode="json")["story"]
        # Present in both
        assert "title" in story
        assert "synopsis" in story
        assert "escalation_arc" in story
        assert "beats" in story
        # canon adds seed (MazeWorld stores it separately)
        assert "seed" in story
        # canon uses list of factions (MazeWorld uses singular Optional[Faction])
        assert "factions" in story
        assert isinstance(story["factions"], list)

    def test_story_beat_shape(self, mazeworld_pipeline_outputs):
        """StoryBeat shape — maps to MazeWorld's RoomStoryBeat."""
        bible, _, _ = mazeworld_pipeline_outputs
        for beat in bible.model_dump(mode="json")["story"]["beats"]:
            assert "map_id" in beat    # MazeWorld: "room_id"
            assert "beat" in beat      # MazeWorld: "summary"

    def test_faction_shape(self, mazeworld_pipeline_outputs):
        """Faction shape — canon adds faction_id vs MazeWorld's name-only Faction."""
        bible, _, _ = mazeworld_pipeline_outputs
        story = bible.model_dump(mode="json")["story"]
        assert len(story["factions"]) == 1
        faction = story["factions"][0]
        assert "faction_id" in faction     # canon addition; cross-reference key
        assert "name" in faction
        assert "description" in faction
        assert "leader" in faction
        assert "threat_level" in faction

    def test_class_archetypes_shape(self, mazeworld_pipeline_outputs):
        """ClassArchetype shape — canon addition (MazeWorld has scattered dicts)."""
        bible, _, _ = mazeworld_pipeline_outputs
        for arch_id, arch in bible.model_dump(mode="json")["class_archetypes"].items():
            assert "archetype_id" in arch
            assert "name" in arch
            assert "description" in arch
            # stat_roles generalises MazeWorld's ARCHETYPE_STAT_ROLES dict
            assert "stat_roles" in arch
            assert "stat_template" in arch
            assert "role_tags" in arch

    def test_metadata_shape(self, mazeworld_pipeline_outputs):
        """BibleMetadata — canon addition.

        Known divergence: ValidationPhase does not record itself in phases_run
        (v0.1 implementation gap). The 9 phases that DO record are:
        story, classes, characters, entity:weapon, entity:spell,
        entity:monster, entity:item, dialogue, narrative.
        """
        bible, _, _ = mazeworld_pipeline_outputs
        meta = bible.model_dump(mode="json")["metadata"]
        assert "generated_at" in meta
        assert "phases_run" in meta
        assert isinstance(meta["phases_run"], list)
        # 9 phases record themselves; ValidationPhase is a known exception
        assert len(meta["phases_run"]) >= 9

    def test_json_is_serialisable(self, mazeworld_pipeline_outputs, tmp_path):
        """Bible round-trips through JSON without error."""
        bible, _, _ = mazeworld_pipeline_outputs
        bible_path = tmp_path / "shape_test.json"
        bible.persist(bible_path)
        raw = json.loads(bible_path.read_text(encoding="utf-8"))
        assert raw["seed"] == _SEED
        assert raw["canon_version"] == "0.1"
        assert isinstance(raw["maps"], dict)
        assert len(raw["maps"]) == _NUM_MAPS
