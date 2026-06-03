"""MazeWorld-equivalent SkeletonSpecs, archetypes, and canned LLM responses
for the canon reference pipeline test.

These definitions REPLICATE MazeWorld's tables independently — they do not
import from or depend on MazeWorld. The shapes are what a downstream user
would write when adopting canon for a fantasy-dungeon-crawler.
"""
import json

from canon import (
    ClassArchetype,
    SkeletonField,
    SkeletonSpec,
)

# --- SkeletonSpecs ---

WEAPON_SPEC = SkeletonSpec(
    entity_type="weapon",
    fields={
        "weapon_type": SkeletonField(choices=[
            ("heavy", 3), ("light", 3), ("sacred", 1),
            ("arcane", 1), ("enchanted", 1), ("wild", 1),
        ]),
        "weapon_category": SkeletonField(choices=[("simple", 2), ("martial", 1)]),
        "damage_type": SkeletonField(choices=[
            ("slashing", 1), ("piercing", 1), ("bludgeoning", 1),
        ]),
        "stat_modifier": SkeletonField(
            lookup={"heavy": "STR", "light": "DEX", "sacred": "WIS",
                    "arcane": "INT", "enchanted": "CHA", "wild": "CON"},
            depends_on="weapon_type",
        ),
        "attack_dice": SkeletonField(choices=[
            ("1d4", 2), ("1d6", 3), ("1d8", 2), ("1d10", 1), ("2d6", 1),
        ]),
    },
    post=lambda skel: {**skel, "magic_element": "none"},
)

SPELL_SPEC = SkeletonSpec(
    entity_type="spell",
    fields={
        "spell_type": SkeletonField(choices=[("damage", 3), ("heal", 1), ("buff", 1)]),
        "element": SkeletonField(choices=[
            ("fire", 1), ("frost", 1), ("light", 1), ("dark", 1), ("nature", 1),
        ]),
        "stat": SkeletonField(choices=[("INT", 3), ("WIS", 2), ("CHA", 1)]),
        "stamina_cost": SkeletonField(range=(2, 8)),
    },
)

MONSTER_SPEC = SkeletonSpec(
    entity_type="monster",
    fields={
        "tier": SkeletonField(choices=[("minion", 4), ("elite", 2), ("boss", 1)]),
        "hp": SkeletonField(range=(8, 80)),
        "ac": SkeletonField(range=(10, 18)),
        "damage_die": SkeletonField(choices=[("1d4", 3), ("1d6", 2), ("1d8", 1), ("2d6", 1)]),
    },
)

ITEM_SPEC = SkeletonSpec(
    entity_type="item",
    fields={
        "item_kind": SkeletonField(choices=[
            ("potion", 3), ("scroll", 2), ("trinket", 2), ("key", 1),
        ]),
        "rarity": SkeletonField(choices=[("common", 4), ("uncommon", 2), ("rare", 1)]),
    },
)


# --- ClassArchetypes (warrior/mage/healer/jester) ---

WARRIOR_ARCHETYPE = ClassArchetype(
    archetype_id="warrior",
    name="Warrior",
    description="Frontline melee combatant",
    category="combat",
    stat_template={"STR": 16, "CON": 14, "DEX": 12, "INT": 8, "WIS": 10, "CHA": 10, "LUCK": 10},
    stat_roles={"primary": ["STR", "CON"], "secondary": ["DEX", "CHA"], "dump": ["INT", "WIS"]},
    role_tags=["tank", "melee_dps"],
)

MAGE_ARCHETYPE = ClassArchetype(
    archetype_id="mage",
    name="Mage",
    description="Arcane caster",
    category="caster",
    stat_template={"STR": 8, "CON": 10, "DEX": 12, "INT": 16, "WIS": 14, "CHA": 10, "LUCK": 10},
    stat_roles={"primary": ["INT"], "secondary": ["WIS", "DEX"], "dump": ["STR", "CON", "CHA"]},
    role_tags=["caster", "ranged_dps"],
)

HEALER_ARCHETYPE = ClassArchetype(
    archetype_id="healer",
    name="Healer",
    description="Support caster",
    category="support",
    stat_template={"STR": 10, "CON": 12, "DEX": 10, "INT": 12, "WIS": 16, "CHA": 14, "LUCK": 10},
    stat_roles={"primary": ["WIS"], "secondary": ["CHA", "INT"], "dump": ["STR", "DEX"]},
    role_tags=["support", "healer"],
)

JESTER_ARCHETYPE = ClassArchetype(
    archetype_id="jester",
    name="Jester",
    description="Trickster",
    category="utility",
    stat_template={"STR": 10, "CON": 10, "DEX": 14, "INT": 12, "WIS": 10, "CHA": 16, "LUCK": 12},
    stat_roles={"primary": ["CHA", "DEX"], "secondary": ["INT", "LUCK"], "dump": ["STR"]},
    role_tags=["utility", "trickster"],
)


# --- Canned LLM responses ---
# Order matches the order phases ask the LLM. List-mode FakeLLMBackend cycles
# through them in order. Each entry is the EXACT JSON the corresponding phase
# expects.


def _story_response() -> str:
    return json.dumps({
        "title": "The Convergence of Shadows",
        "synopsis": "A kingdom on the brink, hunted by an ancient Order.",
        "factions": [{
            "faction_id": "shadow_order",
            "name": "The Shadow Order",
            "description": "Ancient cult of darkness",
            "history": "Sealed for a thousand years",
            "leader": "The Whisper",
            "threat_level": 5,
        }],
        "primary_antagonist_faction_id": "shadow_order",
        "escalation_arc": ["arrival", "investigation", "confrontation", "climax"],
        "climax": "Battle with the Whisper.",
        "final_entity_id": "boss_whisper",
        "final_entity_lore": "The Whisper is the Order's prime.",
        "beats": [
            {"map_id": "room_0", "beat": "Hero arrives at the village.",
             "boss_name": "Cult Watchman", "boss_lore": "A scout."},
            {"map_id": "room_1", "beat": "Investigates the manor.",
             "boss_name": "Acolyte", "boss_lore": "Mid-tier cultist."},
            {"map_id": "room_2", "beat": "Final confrontation.",
             "boss_name": "The Whisper", "boss_lore": "The prime."},
        ],
        "key_character_names": ["Hero", "Innkeeper Marta", "Captain Ren"],
    })


def _archetype_response(archetype: ClassArchetype) -> str:
    return json.dumps({
        "archetype_id": archetype.archetype_id,
        "name": archetype.name,
        "description": archetype.description,
        "category": archetype.category,
        "stat_template": archetype.stat_template,
        "stat_roles": archetype.stat_roles,
        "role_tags": archetype.role_tags,
        "lore": f"{archetype.name}s of this world serve a specific purpose.",
    })


def _character_response(name: str, role: str, lore: str) -> str:
    return json.dumps({"name": name, "role": role, "lore": lore})


def _entity_response(name: str, lore: str) -> str:
    return json.dumps({"name": name, "lore": lore})


def _dialogue_response() -> str:
    return json.dumps({
        "entry_node_id": "start",
        "nodes": {
            "start": {
                "prompt": "Greetings, traveler.",
                "choices": [
                    {"text": "Hello.", "next_node_id": "end"},
                    {"text": "Goodbye.", "next_node_id": None},
                ],
            },
            "end": {"prompt": "Safe travels.", "choices": []},
        },
    })


def build_canned_responses(
    num_maps: int = 3,
    weapons_per_map: int = 2,
    monsters_per_map: int = 2,
    items_per_map: int = 1,
    characters_per_map: int = 2,
) -> list[str]:
    """Build the ordered list of canned LLM responses the pipeline will consume.

    Call order:
      1. StoryPhase           — 1 call
      2. ClassPhase           — archetype_count calls (4)
      3. CharacterPhase       — num_maps * characters_per_map calls
      4. EntityPhase(weapon)  — num_maps * weapons_per_map calls
      5. EntityPhase(spell)   — num_maps * weapons_per_map calls  (same count)
      6. EntityPhase(monster) — num_maps * monsters_per_map calls
      7. EntityPhase(item)    — num_maps * items_per_map calls
      8. DialoguePhase        — num_maps * characters_per_map calls (one per npc)
      9. NarrativePhase       — 1 synopsis + num_maps intros + 1 victory + 1 defeat
     10. ValidationPhase      — 0 LLM calls

    NarrativePhase expects JSON-wrapped prose: DefaultPromptSet uses keys
    "synopsis", "intro_text", "victory_text", "defeat_text".
    NarrativePhase._extract_prose unwraps them; raw strings also work because
    the fallthrough returns the raw string as-is, but JSON-wrapped form is
    canonical.
    """
    responses: list[str] = []

    # 1. StoryPhase (1 call)
    responses.append(_story_response())

    # 2. ClassPhase (4 calls — one per archetype)
    for arch in [WARRIOR_ARCHETYPE, MAGE_ARCHETYPE, HEALER_ARCHETYPE, JESTER_ARCHETYPE]:
        responses.append(_archetype_response(arch))

    # 3. CharacterPhase (per_map x characters_per_map calls)
    for m in range(num_maps):
        for c in range(characters_per_map):
            responses.append(_character_response(
                name=f"Char {m}_{c}",
                role="npc",
                lore=f"A figure in room {m}.",
            ))

    # 4-7. EntityPhases: weapon, spell (same count as weapon), monster, item
    for entity_type, count in [
        ("weapon", weapons_per_map),
        ("spell", weapons_per_map),
        ("monster", monsters_per_map),
        ("item", items_per_map),
    ]:
        for m in range(num_maps):
            for i in range(count):
                responses.append(_entity_response(
                    name=f"{entity_type.title()} {m}_{i}",
                    lore=f"{entity_type} #{i} for room {m}",
                ))

    # 8. DialoguePhase (one per character — all role="npc", all match default roles)
    for _ in range(num_maps * characters_per_map):
        responses.append(_dialogue_response())

    # 9. NarrativePhase: synopsis + per-map intros + victory + defeat
    #    JSON-wrapped so _extract_prose finds the expected key.
    responses.append(json.dumps({
        "synopsis": "A kingdom on the brink — shadows converge on the innocent."
    }))
    for m in range(num_maps):
        responses.append(json.dumps({
            "intro_text": f"Room {m} stretches before you, ancient and forbidding."
        }))
    responses.append(json.dumps({"victory_text": "You have triumphed!"}))
    responses.append(json.dumps({"defeat_text": "You have fallen."}))

    return responses
