"""Tower-defense SkeletonSpecs, archetypes, and canned LLM responses
for the canon non-fantasy reference pipeline test.

These definitions show how a tower-defense developer would adopt canon —
purely declarative entity types with no characters, no dialogue, no narrative
beyond the story arc. Proves canon is game-agnostic.
"""
import json

from canon import (
    ClassArchetype,
    SkeletonField,
    SkeletonSpec,
)

# --- SkeletonSpecs ---

TOWER_SPEC = SkeletonSpec(
    entity_type="tower",
    fields={
        "tower_type": SkeletonField(choices=[
            ("archer", 3), ("mage", 2), ("trap", 1), ("artillery", 1),
        ]),
        "damage": SkeletonField(range=(10, 100)),
        "range": SkeletonField(range=(1, 5)),
        "attack_speed": SkeletonField(choices=[
            ("slow", 1), ("medium", 2), ("fast", 1),
        ]),
        "tier": SkeletonField(range=(1, 3)),
    },
)

ENEMY_WAVE_SPEC = SkeletonSpec(
    entity_type="enemy_wave",
    fields={
        "wave_size": SkeletonField(range=(5, 30)),
        "enemy_kind": SkeletonField(choices=[
            ("grunt", 4), ("elite", 2), ("flying", 2), ("boss", 1),
        ]),
        "speed": SkeletonField(choices=[("slow", 1), ("normal", 2), ("fast", 1)]),
        "armor": SkeletonField(range=(0, 5)),
    },
)


# --- ClassArchetypes for towers (no characters get instantiated) ---

ARCHER_ARCHETYPE = ClassArchetype(
    archetype_id="archer_tower",
    name="Archer Tower",
    description="Single-target ranged tower",
    category="combat",
    stat_template={"damage": 25, "range": 4, "attack_speed": 2},
    role_tags=["single_target", "ranged"],
)

MAGE_ARCHETYPE = ClassArchetype(
    archetype_id="mage_tower",
    name="Mage Tower",
    description="Area-effect tower",
    category="combat",
    stat_template={"damage": 40, "range": 3, "attack_speed": 1},
    role_tags=["aoe", "splash"],
)

TRAP_ARCHETYPE = ClassArchetype(
    archetype_id="trap_tower",
    name="Trap Tower",
    description="Slow / disable tower",
    category="utility",
    stat_template={"damage": 5, "range": 2, "attack_speed": 3},
    role_tags=["control", "slow"],
)


# --- Canned LLM responses (no characters, no dialogue, no narrative) ---

def _story_response():
    return json.dumps({
        "title": "Defend the Hold",
        "synopsis": "Waves of corrupted creatures assault the keep.",
        "factions": [{
            "faction_id": "defenders",
            "name": "Hold Defenders",
            "description": "Garrison of the keep",
            "history": "Stalwart for centuries.",
            "leader": "Captain Theyr",
            "threat_level": 1,
        }],
        "escalation_arc": ["scouts", "vanguard", "main force", "boss wave"],
        "climax": "The corruption's avatar arrives.",
        "beats": [
            {"map_id": "stage_0", "beat": "First skirmishes."},
            {"map_id": "stage_1", "beat": "Heavier waves."},
            {"map_id": "stage_2", "beat": "Final assault."},
        ],
    })


def _archetype_response(archetype):
    return json.dumps({
        "archetype_id": archetype.archetype_id,
        "name": archetype.name,
        "description": archetype.description,
        "category": archetype.category,
        "stat_template": archetype.stat_template,
        "role_tags": archetype.role_tags,
    })


def _entity_response(name, lore):
    return json.dumps({"name": name, "lore": lore})


def build_canned_responses(num_maps=3, towers_per_stage=3, waves_per_stage=4):
    """Build canned responses for: StoryPhase -> ClassPhase -> EntityPhase(tower) ->
    EntityPhase(enemy_wave). NO CharacterPhase, NO DialoguePhase, NO NarrativePhase.
    """
    responses = []
    # 1. StoryPhase
    responses.append(_story_response())
    # 2. ClassPhase (3 archetypes)
    for arch in [ARCHER_ARCHETYPE, MAGE_ARCHETYPE, TRAP_ARCHETYPE]:
        responses.append(_archetype_response(arch))
    # 3. EntityPhase(tower) per stage
    for m in range(num_maps):
        for i in range(towers_per_stage):
            responses.append(_entity_response(
                name=f"Tower {m}.{i}",
                lore=f"A defensive emplacement on stage {m}.",
            ))
    # 4. EntityPhase(enemy_wave) per stage
    for m in range(num_maps):
        for i in range(waves_per_stage):
            responses.append(_entity_response(
                name=f"Wave {m}.{i}",
                lore=f"An assault wave on stage {m}.",
            ))
    return responses
