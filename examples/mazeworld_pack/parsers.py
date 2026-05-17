"""Mazeworld-shape parsers for each entity type.

Each parser receives a BuildContext (from DatabasePhase) and returns a plain
dict whose shape matches mazeworld's per-type JSON file exactly.

Design decisions:
- All missing LLM fields fall back to safe defaults (never raise KeyError).
  The LLM may return incomplete JSON on truncation; the parser must always
  produce a valid dict that mazeworld's registry.py can load.
- Runtime fields (x, y, color) are synthesised from MazeLayout + env-to-color
  map.  If no layout is present (e.g. smoke tests without MazeLayoutPhase),
  they fall back to (1, 1) and a neutral grey.
- ``profile_image``, ``dialogue_tree``, and asset-dependent fields are set to
  None; AssetPhase and DialoguePhase fill them in after this phase runs.
- No imports from MazeWorld. All shapes are derived from the plan's mw-data
  reference snippets and the deep-finding-noodle.md field inventory.
"""

from __future__ import annotations

from typing import Any

from canon.pipeline.phases.database import BuildContext

# ---------------------------------------------------------------------------
# Environment → (R, G, B) colour map
# Matches mazeworld's per-environment NPC tint scheme.
# ---------------------------------------------------------------------------

ENV_TO_COLOR: dict[str, tuple[int, int, int]] = {
    "ruins": (180, 130, 80),
    "wasteland": (200, 180, 100),
    "city": (100, 100, 130),
    "temple": (200, 200, 220),
    "fortress": (90, 90, 90),
    "forest": (60, 130, 60),
    "manor": (120, 80, 60),
    "vault": (60, 60, 100),
}

_DEFAULT_COLOR: tuple[int, int, int] = (128, 128, 128)

# ---------------------------------------------------------------------------
# NPC type map: behavior_type (from skeleton) → mazeworld runtime type string
# ---------------------------------------------------------------------------

_NPC_TYPE_MAP: dict[str, str] = {
    "static": "StaticNPC",
    "wandering": "RandomNPC",
    "merchant": "MerchantNPC",
    "aggressive": "AggressiveNPC",
}

# ---------------------------------------------------------------------------
# Quest reward tiers: reward_tier (int 1-3) → XP value
# ---------------------------------------------------------------------------

_QUEST_XP_BY_TIER: dict[int, int] = {
    1: 50,
    2: 100,
    3: 200,
}

# ---------------------------------------------------------------------------
# Event difficulty: string label → int (mazeworld combat scaler expects int)
# ---------------------------------------------------------------------------

_DIFFICULTY_INT: dict[str, int] = {
    "easy": 1,
    "medium": 2,
    "hard": 3,
    "very_hard": 4,
    "boss": 5,
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_environment(map_obj: Any) -> str:
    """Return the environment string from a map object, or empty string."""
    if map_obj is not None and hasattr(map_obj, "environment"):
        return map_obj.environment or ""
    return ""


def _get_environment_name(map_obj: Any) -> str:
    """Return the human-readable environment name from a map object."""
    if map_obj is not None and hasattr(map_obj, "name"):
        return map_obj.name or ""
    return ""


def _lookup_position(map_obj: Any, entity_id: int | str | None) -> tuple[int, int]:
    """Try to find this entity's tile in the map's MazeLayout.npc_positions.

    Falls back to (1, 1) when layout is absent or the entity has no registered
    position yet.  MazeLayoutPhase populates npc_positions with NPC IDs; if
    the DatabasePhase for NPCs ran before MazeLayoutPhase the positions will
    be absent and the runtime pipeline can rewire them.
    """
    if map_obj is not None and hasattr(map_obj, "layout") and map_obj.layout is not None:
        layout = map_obj.layout
        if hasattr(layout, "npc_positions") and entity_id is not None:
            pos = layout.npc_positions.get(str(entity_id))
            if pos is not None:
                return (int(pos[0]), int(pos[1]))
    return (1, 1)


def _env_color(map_obj: Any) -> tuple[int, int, int]:
    """Return the RGB colour tuple for the map's environment."""
    env = _get_environment(map_obj)
    return ENV_TO_COLOR.get(env, _DEFAULT_COLOR)


# ---------------------------------------------------------------------------
# parse_npc
# ---------------------------------------------------------------------------


def parse_npc(ctx: BuildContext) -> dict:
    """Parse LLM response + skeleton into a mazeworld-shape NPC dict.

    Output matches mazeworld's data/npcs/npcs.json entry shape exactly.
    Synthesises runtime fields (x, y, color) from layout + env-to-color map.

    Missing LLM fields fall back gracefully; the dict is always complete
    so mazeworld's NPC registry never chokes on KeyError.
    """
    llm = ctx.llm_response
    skel = ctx.skeleton
    map_obj = ctx.map_obj

    env = _get_environment(map_obj)
    env_name = _get_environment_name(map_obj)
    color = _env_color(map_obj)

    # Behavior type comes from the pre-rolled skeleton; default to "static".
    behavior = skel.get("behavior_type", "static")
    npc_type = _NPC_TYPE_MAP.get(behavior, "StaticNPC")

    x, y = _lookup_position(map_obj, ctx.allocated_id)

    allocated_id = ctx.allocated_id if ctx.allocated_id is not None else ctx.entity_index + 1000

    return {
        "id": allocated_id,
        "type": npc_type,
        "name": llm.get("name", f"NPC_{allocated_id}"),
        "job": llm.get("job", "wanderer"),
        "personality": llm.get("personality", "stoic"),
        "hobby": llm.get("hobby", "watching"),
        "backstory": llm.get("backstory", ""),
        "environment": env,
        "environment_name": env_name,
        "opening_greeting": llm.get("opening_greeting", "Hello, traveler."),
        "portrait_prompt": llm.get(
            "portrait_prompt", f"a person in {env}" if env else "a mysterious traveler"
        ),
        "profile_image": None,          # AssetPhase fills later
        "dialogue_tree": None,          # DialoguePhase fills later
        "dialogue_tree_incomplete": None,
        "dialogue_tree_complete": None,
        "dialogue_tree_failed": None,
        "quest_id": None,
        "quest_type": None,
        "quest_target_tile": None,
        "max_exchanges": 5,
        "is_story_npc": False,
        "x": x,
        "y": y,
        "selected": True,
        "exhausted_dialogue": llm.get(
            "exhausted_dialogue", "I have nothing more to say."
        ),
        "personality_notes": llm.get("personality_notes", []),
        "color": list(color),
    }


# ---------------------------------------------------------------------------
# parse_item
# ---------------------------------------------------------------------------


def parse_item(ctx: BuildContext) -> dict:
    """Parse LLM response + skeleton into a mazeworld-shape item dict.

    Handles two shapes based on ``item_kind`` from the skeleton:
    - ``weapon``  → weapon-flavored dict with weapon_type, damage_type, etc.
    - everything else → consumable dict with stamina/health values.

    Output matches mazeworld's data/items/items.json value shape.
    """
    llm = ctx.llm_response
    skel = ctx.skeleton

    item_kind = skel.get("item_kind", "potion")
    rarity = skel.get("rarity", "common")

    allocated_id = ctx.allocated_id if ctx.allocated_id is not None else ctx.entity_index + 2000

    base = {
        "id": allocated_id,
        "name": llm.get("name", f"Item_{allocated_id}"),
        "desc": llm.get("desc", llm.get("description", "")),
        "rarity": rarity,
        "category": item_kind,
        "profile_image": None,          # AssetPhase fills later
        "portrait_prompt": llm.get("portrait_prompt", ""),
    }

    if item_kind == "weapon":
        # Weapon variant — uses weapon_type / damage_type / item_stats shape
        item_stats_raw = llm.get("item_stats", {})
        base.update(
            {
                "weapon_type": llm.get(
                    "weapon_type", skel.get("weapon_type", "light")
                ),
                "damage_type": llm.get(
                    "damage_type", skel.get("damage_type", "slashing")
                ),
                "weapon_category": llm.get("weapon_category", "simple"),
                "magic_element": skel.get("magic_element", "none"),
                "item_stats": {
                    "attack_dice": item_stats_raw.get(
                        "attack_dice", skel.get("attack_dice", "1d6")
                    ),
                    "stat_modifier": item_stats_raw.get(
                        "stat_modifier", skel.get("stat_modifier", "STR")
                    ),
                    "price": item_stats_raw.get("price", 10),
                },
            }
        )
    else:
        # Consumable/trinket/key variant
        item_stats_raw = llm.get("item_stats", {})
        base.update(
            {
                "item_stats": {
                    "stamina_value": item_stats_raw.get("stamina_value", 0),
                    "health_value": item_stats_raw.get("health_value", 0),
                    "uses": item_stats_raw.get("uses", 1),
                    "price": item_stats_raw.get("price", 5),
                    "attribute": item_stats_raw.get("attribute", None),
                }
            }
        )

    return base


# ---------------------------------------------------------------------------
# parse_monster
# ---------------------------------------------------------------------------


def parse_monster(ctx: BuildContext) -> dict:
    """Parse LLM response + skeleton into a mazeworld-shape monster dict.

    Output matches mazeworld's data/monsters/monsters.json value shape.
    Mechanical fields (hp, ac, damage_die, tier) come from the skeleton;
    narrative fields come from the LLM.
    """
    llm = ctx.llm_response
    skel = ctx.skeleton
    map_obj = ctx.map_obj

    env = _get_environment(map_obj)

    allocated_id = ctx.allocated_id if ctx.allocated_id is not None else ctx.entity_index + 5000

    # hp_range and ac_range from LLM OR fall back to skeleton scalar values
    llm_hp_range = llm.get("hp_range", None)
    llm_ac_range = llm.get("ac_range", None)

    skel_hp = skel.get("hp", 20)
    skel_ac = skel.get("ac", 12)

    hp_range = llm_hp_range if isinstance(llm_hp_range, list) and len(llm_hp_range) == 2 else [
        max(1, skel_hp - 5),
        skel_hp + 5,
    ]
    ac_range = llm_ac_range if isinstance(llm_ac_range, list) and len(llm_ac_range) == 2 else [
        max(8, skel_ac - 2),
        skel_ac + 2,
    ]

    abilities_raw = llm.get("abilities", [])
    # Ensure each ability entry has the expected shape
    abilities = []
    for ab in abilities_raw:
        if isinstance(ab, dict):
            abilities.append(
                {
                    "name": ab.get("name", "Attack"),
                    "effect_type": ab.get("effect_type", "damage"),
                    "damage_dice": ab.get("damage_dice", skel.get("damage_die", "1d6")),
                    "chance": float(ab.get("chance", 1.0)),
                }
            )

    return {
        "id": allocated_id,
        "name": llm.get("name", f"Monster_{allocated_id}"),
        "species": llm.get("species", "unknown creature"),
        "description": llm.get("description", ""),
        "backstory": llm.get("backstory", ""),
        "tier": skel.get("tier", "minion"),
        "hp_range": hp_range,
        "ac_range": ac_range,
        "damage_die": skel.get("damage_die", "1d6"),
        "damage_type": llm.get("damage_type", "slashing"),
        "physical_type": llm.get("physical_type", "beast"),
        "elemental_affinity": llm.get("elemental_affinity", "none"),
        "weakness": llm.get("weakness", "none"),
        "abilities": abilities,
        "is_boss": skel.get("tier") == "boss",
        "environment": env,
        "portrait_prompt": llm.get(
            "portrait_prompt",
            f"a {skel.get('tier', 'minion')} creature in {env}" if env else "a dangerous creature",
        ),
        "profile_image": None,          # AssetPhase fills later
    }


# ---------------------------------------------------------------------------
# parse_event
# ---------------------------------------------------------------------------


def _lookup_event_position(ctx: BuildContext) -> tuple[int, int]:
    """Return an (x, y) tile position for this event.

    Checks ``ctx.map_obj.layout.event_positions`` for a tuple keyed by the
    entity's allocated_id (as string).  Falls back to (1, 1).

    ``event_positions`` may be a dict (preferred) or a list (legacy MazeLayout
    shape) — both are handled safely.
    """
    map_obj = ctx.map_obj
    if map_obj is not None and hasattr(map_obj, "layout") and map_obj.layout is not None:
        layout = map_obj.layout
        event_positions = getattr(layout, "event_positions", None)
        if event_positions is not None and isinstance(event_positions, dict) and ctx.allocated_id is not None:
            pos = event_positions.get(str(ctx.allocated_id))
            if pos is not None:
                return (int(pos[0]), int(pos[1]))
    return (1, 1)


def parse_event(ctx: BuildContext) -> dict:
    """Parse LLM response + skeleton into a mazeworld-shape event dict.

    Two sub-shapes:
    - ``combat`` events include a monster_count and monster_ids from the
      room's monster entities.
    - ``puzzle`` events include choices, correct_tool, correct_ability,
      correct_spell, and failure fields.

    Output matches mazeworld's data/events/events.json entry shape.
    """
    llm = ctx.llm_response
    skel = ctx.skeleton
    map_obj = ctx.map_obj

    event_type = skel.get("event_type", "combat")

    # Coerce difficulty to int — mazeworld's combat scaler divides by it.
    raw_difficulty = skel.get("difficulty", "medium")
    if isinstance(raw_difficulty, int):
        difficulty_int = raw_difficulty
    else:
        difficulty_int = _DIFFICULTY_INT.get(str(raw_difficulty).lower(), 2)

    allocated_id = ctx.allocated_id if ctx.allocated_id is not None else ctx.entity_index + 3000

    # money_drop: LLM may return [min, max] list or we derive from difficulty
    _difficulty_money = {1: [5, 20], 2: [10, 40], 3: [20, 80], 4: [30, 100], 5: [50, 150]}
    money_drop_raw = llm.get("money_drop", None)
    money_drop = (
        money_drop_raw
        if isinstance(money_drop_raw, list) and len(money_drop_raw) == 2
        else _difficulty_money.get(difficulty_int, [10, 30])
    )

    loot_table_raw = llm.get("loot_table", [])
    loot_table = []
    for entry in loot_table_raw:
        if isinstance(entry, dict):
            loot_table.append(
                {
                    "item_id": entry.get("item_id", 0),
                    "drop_chance": float(entry.get("drop_chance", 0.25)),
                }
            )

    # Position from maze layout
    x, y = _lookup_event_position(ctx)

    # room_level from map
    room_level = getattr(map_obj, "level", 1) if map_obj is not None else 1

    base: dict = {
        "id": allocated_id,
        "type": event_type,
        "name": llm.get("name", f"Event_{allocated_id}"),
        "description": llm.get("description", ""),
        "difficulty": difficulty_int,
        "money_drop": money_drop,
        "loot_table": loot_table,
        "x": x,
        "y": y,
        "room_level": room_level,
        "time_gate": None,
        "portrait_prompt": llm.get("portrait_prompt", "an event scene"),
        "profile_image": None,  # AssetPhase fills
    }

    if event_type == "combat":
        base["monster_count"] = skel.get("monster_count", 1)

        # Cross-reference: find monsters in this room from the bible
        monster_ids: list[int] = []
        if map_obj is not None:
            room_monsters = [e for e in map_obj.entities if e.entity_type == "monster"]
            if room_monsters:
                count = skel.get("monster_count", 1)
                ctx_rng = getattr(ctx, "rng", None)
                if ctx_rng is not None:
                    chosen = ctx_rng.sample(room_monsters, min(count, len(room_monsters)))
                else:
                    chosen = room_monsters[:count]
                for m in chosen:
                    # entity_id is the allocated int ID as a string (e.g. "5001"),
                    # set by DatabasePhase.  Parse it directly; fall back to the
                    # legacy "monster_NNNN" suffix+base pattern if the entity_id
                    # is not a plain integer string.
                    try:
                        raw_id = int(m.entity_id)
                        monster_ids.append(raw_id)
                    except (ValueError, TypeError):
                        # Legacy format "monster_NNNN" — extract suffix + base
                        try:
                            monster_ids.append(
                                int(m.entity_id.rsplit("_", 1)[-1]) + 5000
                            )
                        except (ValueError, IndexError):
                            monster_ids.append(5000)
        base["monster_ids"] = monster_ids

    elif event_type == "puzzle":
        choices_raw = llm.get("choices", [])
        choices = []
        for ch in choices_raw:
            if isinstance(ch, dict):
                choices.append(
                    {
                        "text": ch.get("text", "Attempt"),
                        "stat_check": ch.get("stat_check", "INT"),
                        "dc": int(ch.get("dc", 12)),
                        "auto_success": bool(ch.get("auto_success", False)),
                        "success_text": ch.get("success_text", "Success."),
                    }
                )
        base["choices"] = choices
        base["correct_tool"] = llm.get("correct_tool", None)
        base["correct_ability"] = llm.get("correct_ability", None)
        base["correct_spell"] = llm.get("correct_spell", None)
        base["failure_damage_type"] = llm.get("failure_damage_type", "bludgeoning")
        fdmg_raw = llm.get("failure_damage_range", None)
        base["failure_damage_range"] = (
            fdmg_raw
            if isinstance(fdmg_raw, list) and len(fdmg_raw) == 2
            else [1, 6]
        )

    return base


# ---------------------------------------------------------------------------
# parse_quest
# ---------------------------------------------------------------------------


def parse_quest(ctx: BuildContext) -> dict:
    """Parse LLM response + skeleton into a mazeworld-shape quest dict.

    ``quest_type`` from the skeleton maps to mazeworld's four types (escort,
    fetch, solve, combat).  ``reward_tier`` (1-3) scales XP values.

    Output matches mazeworld's data/quests/quests.json entry shape.
    """
    llm = ctx.llm_response
    skel = ctx.skeleton

    quest_type = skel.get("quest_type", "fetch")
    reward_tier = int(skel.get("reward_tier", 1))

    xp = _QUEST_XP_BY_TIER.get(reward_tier, 50)

    allocated_id = ctx.allocated_id if ctx.allocated_id is not None else ctx.entity_index + 4000

    reward_raw = llm.get("reward", {})
    if not isinstance(reward_raw, dict):
        reward_raw = {}

    failure_raw = llm.get("failure_penalty", {})
    if not isinstance(failure_raw, dict):
        failure_raw = {}

    # Cross-reference: pick the first NPC in this room as the quest giver.
    # DatabasePhase populates bible.maps[map_id].entities (Fix 1), so this
    # cross-reference works once the NPC phase has run before the quest phase.
    map_obj = ctx.map_obj
    giver_npc_id = None
    if map_obj is not None:
        room_npcs = [e for e in map_obj.entities if e.entity_type == "npc"]
        if room_npcs:
            chosen = room_npcs[0]
            # entity_id is the allocated int ID as a string (e.g. "1000"),
            # set by DatabasePhase.  Parse it directly; fall back to the
            # legacy "npc_NNNN" suffix+base pattern.
            try:
                giver_npc_id = int(chosen.entity_id)
            except (ValueError, TypeError):
                try:
                    giver_npc_id = int(chosen.entity_id.rsplit("_", 1)[-1]) + 1000
                except (ValueError, IndexError):
                    giver_npc_id = 1000

    return {
        "id": allocated_id,
        "type": quest_type,
        "title": llm.get("title", f"Quest_{allocated_id}"),
        "description": llm.get("description", ""),
        "giver_npc_id": giver_npc_id,
        "room_id": map_obj.map_id if map_obj is not None else None,
        "is_story_quest": False,
        "prerequisite_quest_id": None,
        "portrait_prompt": None,
        "profile_image": None,
        "reward": {
            "xp": reward_raw.get("xp", xp),
            "item_id": reward_raw.get("item_id", None),
        },
        "failure_penalty": {
            "hp_damage": failure_raw.get("hp_damage", 5),
        },
        "success_dialogue": llm.get("success_dialogue", "Well done."),
        "failure_dialogue": llm.get("failure_dialogue", "You have failed."),
        "is_complete": False,
        "is_failed": False,
    }
