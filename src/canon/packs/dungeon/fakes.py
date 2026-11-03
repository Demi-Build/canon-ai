"""Deterministic $0 responder for the dungeon pack — the fake LLM every free
create runs on (row P0-10).

Moved verbatim into the wheel from ``examples/run_mazeworld_full.py`` (which
now imports it) for the same reason row P0-4 moved the platformer's slice
runner: ``canon world new --template dungeon`` must work from an installed
wheel with no source checkout. The platformer's twin is
``canon.packs.platformer.run_slice.make_fake_responder``.

Shape-correct JSON for every prompt the dungeon pipeline sends, routed by
keywords in the user message — the routing ORDER is load-bearing and is
documented at each branch.
"""

from __future__ import annotations

import json

__all__ = ["make_fake_responder"]


def make_fake_responder(num_maps: int = 3):
    """Callable for FakeLLMBackend that returns shape-correct JSON for any
    prompt the pipeline sends. Inspects request.user_message keywords.
    """
    counters = {
        "npc": 0,
        "item": 0,
        "monster": 0,
        "event": 0,
        "quest": 0,
        "character": 0,
        "class": 0,
        "dialogue": 0,
    }

    def respond(request):  # noqa: PLR0911,PLR0912
        msg_lower = request.user_message.lower()

        # Dialogue trees — checked FIRST. The dialogue prompt embeds the
        # character summary ("Role: npc"), so it would otherwise be caught by
        # the character/npc branches below and never produce a tree.
        if "dialogue" in msg_lower or "conversation" in msg_lower:
            counters["dialogue"] += 1
            return json.dumps({
                "entry_node_id": "start",
                "nodes": {
                    "start": {
                        "prompt": "Hello, traveler. What brings you here?",
                        "choices": [
                            {"text": "Who are you?", "next_node_id": "about"},
                            {"text": "Just passing through.", "next_node_id": "end"},
                            {"text": "Farewell.", "next_node_id": None},
                        ],
                    },
                    "about": {
                        "prompt": "Someone trying to survive in these ruins.",
                        "choices": [
                            {"text": "Good luck.", "next_node_id": "end"},
                        ],
                    },
                    "end": {"prompt": "Safe travels.", "choices": []},
                },
            })

        # Events — routed near the TOP by "money_drop", a marker unique to the
        # event JSON shape. The event prompt embeds the growing cumulative world
        # context, so keyword routing on the whole prompt is unreliable (context
        # words like "npc"/"monster" hijack later event prompts). "money_drop"
        # only appears in the event shape, so it's immune; "stat_check"
        # distinguishes puzzle/skill events (need choices) from combat (doesn't).
        if "money_drop" in msg_lower:
            counters["event"] += 1
            n = counters["event"]
            if "stat_check" in msg_lower:
                return json.dumps({
                    "name": f"Sealed Mechanism {n}",
                    "description": "An ancient device blocks the way, humming with latent power.",
                    "difficulty": "medium",
                    "money_drop": [10, 40],
                    "loot_table": [],
                    "choices": [
                        {
                            "text": "Force the mechanism open",
                            "stat_check": "STR",
                            "dc": 12,
                            "auto_success": False,
                            "success_text": "Metal shrieks and gives way.",
                        },
                        {
                            "text": "Study the glowing runes",
                            "stat_check": "INT",
                            "dc": 12,
                            "auto_success": False,
                            "success_text": "The sequence resolves in your mind.",
                        },
                    ],
                    # Ability/spell solutions that match generated classes
                    # (warrior has Bulwark; mage has Firebolt) so they're usable.
                    "correct_tool": None,
                    "correct_ability": "Bulwark",
                    "correct_spell": "Firebolt",
                    "failure_damage_type": "arcane",
                    "failure_damage_range": [2, 8],
                })
            return json.dumps({
                "name": f"Ambush {n}",
                "description": "Something hostile lunges from the shadows.",
                "difficulty": "medium",
                "money_drop": [10, 40],
                "loot_table": [],
            })

        # Story (look for specific cues)
        if (
            "overarching story" in msg_lower
            or "world synopsis" in msg_lower
            or ("title" in msg_lower and "factions" in msg_lower)
        ):
            return json.dumps({
                "title": "The Convergence",
                "synopsis": "A test world.",
                "factions": [
                    {
                        "faction_id": "f1",
                        "name": "The Order",
                        "description": "test",
                        "history": "old",
                        "leader": "boss",
                        "threat_level": 5,
                    }
                ],
                "escalation_arc": ["arrive", "explore", "confront"],
                "climax": "Final battle.",
                "beats": [
                    {"map_id": f"room_{i}", "beat": f"beat {i}", "boss_name": "Boss", "boss_lore": "lore"}
                    for i in range(num_maps)
                ],
                "key_character_names": ["Hero", "Mentor"],
            })

        # Class archetype
        if "archetype" in msg_lower or ("class" in msg_lower and "stats" in msg_lower):
            n = counters["class"]
            counters["class"] += 1
            archetypes = ["warrior", "mage", "healer", "jester"]
            class_archetype = archetypes[n % 4]
            # Populate spell_pool for archetypes that cast spells
            spell_pool: list[dict] = []
            if class_archetype == "mage":
                spell_pool = [
                    {
                        "name": "Firebolt",
                        "spell_type": "damage_single",
                        "element": "fire",
                        "stat": "INT",
                        "targets": "single",
                        "num_dice": 1,
                        "die_sides": 6,
                        "stamina_cost": 4,
                        "description": "A bolt of flame.",
                    },
                    {
                        "name": "Frost Spike",
                        "spell_type": "damage_single",
                        "element": "frost",
                        "stat": "INT",
                        "targets": "single",
                        "num_dice": 1,
                        "die_sides": 6,
                        "stamina_cost": 4,
                        "description": "A shard of ice.",
                    },
                    {
                        "name": "Mass Shock",
                        "spell_type": "damage_multi",
                        "element": "light",
                        "stat": "INT",
                        "targets": "multi",
                        "num_dice": 1,
                        "die_sides": 4,
                        "stamina_cost": 6,
                        "description": "Lightning fans out.",
                    },
                ]
            elif class_archetype == "healer":
                spell_pool = [
                    {
                        "name": "Mend",
                        "spell_type": "heal",
                        "element": "light",
                        "stat": "WIS",
                        "targets": "single",
                        "num_dice": 1,
                        "die_sides": 8,
                        "stamina_cost": 3,
                        "heal_amount": 8,
                        "description": "Restore HP.",
                    },
                    {
                        "name": "Bless",
                        "spell_type": "buff_stat",
                        "element": "light",
                        "stat": "WIS",
                        "targets": "single",
                        "num_dice": 0,
                        "die_sides": 0,
                        "stamina_cost": 4,
                        "buff_stat": "STR",
                        "buff_value": 2,
                        "buff_duration": 3,
                        "description": "Boost an ally's strength.",
                    },
                    {
                        "name": "Solar Flare",
                        "spell_type": "damage_single",
                        "element": "light",
                        "stat": "WIS",
                        "targets": "single",
                        "num_dice": 1,
                        "die_sides": 6,
                        "stamina_cost": 5,
                        "description": "Searing light damage.",
                    },
                ]
            # Per-archetype abilities so classes aren't identical in fake mode.
            def _ab(name, desc, stat, cost):
                return {"name": name, "description": desc, "stat": stat, "stamina_cost": cost}

            ability_sets = {
                "warrior": [
                    _ab("Cleave", "A heavy swing hitting adjacent foes.", "STR", 6),
                    _ab("Shield Bash", "Slam a foe to stun them.", "STR", 4),
                    _ab("Bulwark", "Brace to reduce incoming damage.", "CON", 3),
                ],
                "mage": [
                    _ab("Arcane Bolt", "A focused dart of raw magic.", "INT", 3),
                    _ab("Mana Shield", "Turn stamina into a damage ward.", "INT", 5),
                ],
                "healer": [
                    _ab("Soothing Word", "Calm an ally, clearing fear.", "WIS", 3),
                    _ab("Sanctuary", "Ward an ally from the next blow.", "WIS", 5),
                ],
                "jester": [
                    _ab("Mock", "Taunt a foe into a rash attack.", "CHA", 3),
                    _ab("Sleight of Hand", "Filch an item mid-combat.", "DEX", 4),
                    _ab("Tumble", "Roll clear of danger.", "DEX", 4),
                ],
            }
            stat_templates = {
                "warrior": {"STR": 16, "DEX": 12, "CON": 15, "INT": 8, "WIS": 10, "CHA": 11, "LUCK": 10},
                "mage": {"STR": 8, "DEX": 12, "CON": 10, "INT": 16, "WIS": 13, "CHA": 11, "LUCK": 10},
                "healer": {"STR": 9, "DEX": 11, "CON": 12, "INT": 12, "WIS": 16, "CHA": 12, "LUCK": 10},
                "jester": {"STR": 10, "DEX": 16, "CON": 11, "INT": 12, "WIS": 10, "CHA": 15, "LUCK": 13},
            }
            weapons = {
                "warrior": "Iron Greatsword", "mage": "Oak Staff",
                "healer": "Blessed Mace", "jester": "Twin Daggers",
            }
            abilities = ability_sets.get(class_archetype, ability_sets["warrior"])
            # Casters start knowing their spells, not just a level-up pool.
            spells = spell_pool if class_archetype in ("mage", "healer") else []
            return json.dumps({
                "archetype_id": class_archetype,
                "archetype": class_archetype,
                "name": f"Class {class_archetype.title()}",
                "description": f"A {class_archetype} archetype for testing.",
                "flavor_text": "Test flavor.",
                "starting_weapon": weapons.get(class_archetype, "Test Weapon"),
                "stat_template": stat_templates.get(class_archetype, stat_templates["warrior"]),
                "stat_roles": {"primary": ["STR"], "secondary": ["CON"], "dump": ["INT"]},
                "abilities": abilities,
                "spells": spells,
                "ability_pool": [_ab("Second Wind", "Recover stamina.", "CON", 0)],
                "spell_pool": spell_pool,
                "portrait_prompt": f"a {class_archetype} class portrait",
            })

        # Character
        if "character" in msg_lower and "role" in msg_lower:
            n = counters["character"]
            counters["character"] += 1
            return json.dumps({
                "name": f"Char {n}",
                "lore": "A test character.",
                "personality": "stoic",
                "job": "wanderer",
                "hobby": "watching",
                "opening_greeting": "Hello.",
                "portrait_prompt": "a person",
                "personality_notes": ["quiet"],
                "exhausted_dialogue": "Goodbye.",
            })

        # NPC (DatabasePhase)
        if "npc" in msg_lower or "named character" in msg_lower:
            n = counters["npc"]
            counters["npc"] += 1
            return json.dumps({
                "name": f"NPC {n}",
                "job": "merchant",
                "hobby": "trading",
                "personality": "wary",
                "backstory": "Sells things.",
                "opening_greeting": "Welcome.",
                "portrait_prompt": "a merchant",
            })

        # Monster
        if "monster" in msg_lower or "creature" in msg_lower:
            n = counters["monster"]
            counters["monster"] += 1
            return json.dumps({
                "name": f"Monster {n}",
                "species": "creature",
                "description": "A test monster.",
                "backstory": "Lurks.",
                "hp_range": [10, 20],
                "ac_range": [10, 12],
                "damage_type": "physical",
                "physical_type": "bludgeoning",
                "abilities": [{"name": "Bite", "effect_type": "damage", "damage_dice": "1d6", "chance": 0.5}],
                "is_boss": False,
                "portrait_prompt": "a monster",
            })

        # Quest
        if "quest" in msg_lower or "task" in msg_lower:
            n = counters["quest"]
            counters["quest"] += 1
            return json.dumps({
                "title": f"Quest {n}",
                "description": "Do a thing.",
                "reward": {"xp": 100, "item_id": None},
                "failure_penalty": {"hp_damage": 5},
                "success_dialogue": "Well done.",
                "failure_dialogue": "Too bad.",
            })

        # Item / weapon — checked LAST among entity types: event and quest
        # prompts also contain "item" (item_id in loot/reward shapes), so their
        # unique "event"/"quest" keywords must match first above. Only true item
        # prompts (which carry "weapon") reach here.
        if "item" in msg_lower or "weapon" in msg_lower:
            n = counters["item"]
            counters["item"] += 1
            return json.dumps({
                "name": f"Item {n}",
                "desc": "A test item.",
                "category": "weapon",
                "weapon_type": "heavy",
                "damage_type": "slashing",
                "weapon_category": "martial",
                "item_stats": {"attack_dice": "1d8", "stat_modifier": "STR", "price": 25},
            })

        # Narrative prose (synopsis, victory, defeat, room intros)
        if "synopsis" in msg_lower:
            return "A test synopsis. Heroes face a great challenge."
        if "victory" in msg_lower:
            return "Victory! You have prevailed."
        if "defeat" in msg_lower or "game over" in msg_lower:
            return "Defeat. You have fallen."
        if "intro" in msg_lower or "introduction" in msg_lower:
            return "You enter a new place."

        # Fallback: empty JSON
        return "{}"

    return respond
