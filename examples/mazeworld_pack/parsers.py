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
from examples.mazeworld_pack.specs import _scale_monster

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

# ITEM_SPEC rolls item_kind ∈ {potion, scroll, trinket, key, weapon}, but
# mazeworld's item loader only knows {food, drink, tool, weapon, spell_scroll}
# and falls back to an inert base Item for anything else. Map canon's kinds
# onto mazeworld's known categories so items keep their consumable/usable
# behavior. Unknown kinds default to "tool" (a usable, non-consumable item).
ITEM_KIND_TO_CATEGORY: dict[str, str] = {
    "potion": "drink",
    "scroll": "spell_scroll",
    "trinket": "tool",
    "key": "tool",
    "weapon": "weapon",
}

# Tool attributes a puzzle can be solved with (mazeworld vocab). Pack data —
# canon core has no notion of these. parse_item tags tool items with their
# attribute so parse_event can wire a real, solvable tool path into puzzles.
_TOOL_ATTRIBUTES = {"bludgeon", "cutting", "digging", "climbing"}


def _entity_int_id(entity: Any, base: int) -> int:
    """Coerce an entity_id to its int form, mirroring DatabasePhase's scheme.

    entity_id is normally the allocated int as a string (e.g. ``"1000"``);
    fall back to the legacy ``"<type>_NNNN"`` suffix + ``base`` offset.
    """
    try:
        return int(entity.entity_id)
    except (ValueError, TypeError):
        try:
            return int(entity.entity_id.rsplit("_", 1)[-1]) + base
        except (ValueError, IndexError, AttributeError):
            return base

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

    npc = {
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
        # mazeworld's NPC model field is max_dialogue_turns (max_exchanges was
        # silently dropped → dialogue cap defaulted).
        "max_dialogue_turns": 5,
        # The first NPC per room is story-relevant (mazeworld flags 2-5 quest
        # NPCs per room as story NPCs; canon marks one deterministically).
        "is_story_npc": ctx.entity_index == 0,
        "x": x,
        "y": y,
        "selected": True,
        "exhausted_dialogue": llm.get(
            "exhausted_dialogue", "I have nothing more to say."
        ),
        "personality_notes": llm.get("personality_notes", []),
        "color": list(color),
    }

    # MerchantNPC needs a non-empty shop_inventory of REAL item ids. Items are
    # generated before NPCs (see compose db_spec order), so this room's items
    # are already in map_obj.entities.
    if npc_type == "MerchantNPC" and map_obj is not None:
        room_items = [e for e in map_obj.entities if e.entity_type == "item"]
        npc["shop_inventory"] = [
            {"item_id": _entity_int_id(e, 2000), "price": 15, "stock": 1}
            for e in room_items
        ]

    # AggressiveNPC fights the player — give it an inline npc_monster stat block
    # (mazeworld reads npc.npc_monster; it has a runtime fallback, but a real
    # level-scaled block is closer to MazeWorld). Mirrors pipeline.py:1330.
    if npc_type == "AggressiveNPC":
        room_level = getattr(map_obj, "level", None) or 1
        npc["npc_monster"] = {
            "id": allocated_id,  # shares the NPC's id namespace; unique per NPC
            "name": npc["name"],
            "species": "npc",
            "description": npc["backstory"] or f"A hostile {npc['job']}.",
            "hp_range": [10 + room_level * 3, 15 + room_level * 5],
            "ac_range": [9 + room_level, 12 + room_level],
            "damage_type": "physical",
            "elemental_affinity": None,
            "weakness": None,
            "abilities": [],
            "is_boss": False,
            "portrait_prompt": npc["portrait_prompt"],
        }

    return npc


# ---------------------------------------------------------------------------
# parse_item
# ---------------------------------------------------------------------------


def parse_item(ctx: BuildContext) -> dict:
    """Parse LLM response + skeleton into a mazeworld-shape item dict.

    item_kind from the (level-scaled) skeleton IS the mazeworld category and
    selects the shape; mechanics come from the skeleton (LLM = name/desc only):
    - ``weapon``         → weapon dict; attack_dice/stat scaled by level+type.
    - ``food``/``drink`` → consumable; restore_amount scaled by level.
    - ``tool``           → carries a puzzle-solve ``attribute``.
    - ``spell_scroll``   → minimal consumable.

    Legacy skeletons (scalar, no scaled fields) still parse via fallbacks.
    """
    llm = ctx.llm_response
    skel = ctx.skeleton
    map_obj = ctx.map_obj

    item_kind = skel.get("item_kind", "tool")
    rarity = skel.get("rarity", "common")
    # item_kind is already a valid mazeworld category; map any legacy kinds.
    category = ITEM_KIND_TO_CATEGORY.get(item_kind, item_kind)
    room_level = getattr(map_obj, "level", None) or 1

    allocated_id = ctx.allocated_id if ctx.allocated_id is not None else ctx.entity_index + 2000

    base = {
        "id": allocated_id,
        "name": llm.get("name", f"Item_{allocated_id}"),
        "desc": llm.get("desc", llm.get("description", "")),
        "rarity": rarity,
        "category": category,
        "room_level": room_level,
        "profile_image": None,          # AssetPhase fills later
        "portrait_prompt": llm.get("portrait_prompt", ""),
    }

    if item_kind == "weapon":
        # Mechanics from the skeleton (scaled by level+type); LLM only names it.
        base.update(
            {
                "weapon_type": skel.get("weapon_type", "light"),
                "damage_type": skel.get("physical_type", "slashing"),
                "weapon_category": skel.get("weapon_category", "simple"),
                "magic_element": skel.get("magic_element", "none"),
                "item_stats": {
                    "attack_dice": skel.get("attack_dice", "1d6"),
                    "stat_modifier": skel.get("weapon_stat", skel.get("stat_modifier", "STR")),
                    "price": skel.get("price", 10),
                },
            }
        )
    elif item_kind in ("food", "drink"):
        # Level-scaled restore amount from the skeleton. food restores health;
        # drink restores stamina (mazeworld consumable split).
        restore = skel.get("restore_amount", 0)
        base["item_stats"] = {
            "stamina_value": restore if item_kind == "drink" else 0,
            "health_value": restore if item_kind == "food" else 0,
            "uses": 1,
            "price": skel.get("price", 5),
            "attribute": None,
        }
    elif item_kind == "tool":
        # Tools carry the attribute a puzzle can be solved with. Expose it via
        # `tags` too, so the EntityLore stub DatabasePhase builds carries it and
        # parse_event can wire a real tool-solve path into puzzles in this room.
        tool_attr = skel.get("tool_attribute")
        base["item_stats"] = {
            "stamina_value": 0,
            "health_value": 0,
            "uses": skel.get("uses", 3),
            "price": skel.get("price", 8),
            "attribute": tool_attr,
        }
        if tool_attr:
            base["tags"] = [tool_attr]
    else:  # spell_scroll / unknown — minimal consumable
        base["item_stats"] = {
            "stamina_value": 0,
            "health_value": 0,
            "uses": 1,
            "price": skel.get("price", 8),
            "attribute": None,
        }

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

    # Mechanics come from the (level-scaled) skeleton, not the LLM — the
    # skeleton's post-roll derived hp_range/ac_range/damage_die from the room's
    # level + the monster's tier. The LLM only supplies flavor. Fall back to the
    # legacy scalar shape if an older skeleton without bands is passed.
    hp_range = skel.get("hp_range")
    ac_range = skel.get("ac_range")
    if not (isinstance(hp_range, list) and len(hp_range) == 2):
        skel_hp = skel.get("hp", 20)
        hp_range = [max(1, skel_hp - 5), skel_hp + 5]
    if not (isinstance(ac_range, list) and len(ac_range) == 2):
        skel_ac = skel.get("ac", 12)
        ac_range = [max(8, skel_ac - 2), skel_ac + 2]

    # room_level for the template (mazeworld's instantiate_monster scales from
    # it at combat time); default to 1 when the map carries no level.
    room_level = getattr(map_obj, "level", None) or 1

    # Guarantee exactly one boss per room: force the first monster (index 0) to
    # the boss tier and re-scale its bands accordingly (mazeworld requires one
    # is_boss monster per room; a 2-monster room would otherwise often roll none).
    tier = skel.get("tier", "minion")
    damage_die = skel.get("damage_die", "1d6")
    if ctx.entity_index == 0:
        tier = "boss"
        scaled = _scale_monster({**skel, "tier": "boss"}, {"room_level": room_level})
        hp_range, ac_range, damage_die = (
            scaled["hp_range"], scaled["ac_range"], scaled["damage_die"]
        )

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
        "tier": tier,
        "level": room_level,
        "hp_range": hp_range,
        "ac_range": ac_range,
        "damage_die": damage_die,
        "damage_type": llm.get("damage_type", "slashing"),
        # physical_type (slashing/piercing/bludgeoning) is a skeleton roll — a
        # real mechanical value, not the LLM's creature-kind prose.
        "physical_type": skel.get("physical_type", "slashing"),
        "elemental_affinity": llm.get("elemental_affinity", "none"),
        "weakness": llm.get("weakness", "none"),
        "time_availability": skel.get("time_availability", "always"),
        "abilities": abilities,
        "is_boss": tier == "boss",
        "environment": env,
        "portrait_prompt": llm.get(
            "portrait_prompt",
            f"a {tier} creature in {env}" if env else "a dangerous creature",
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

    # loot_table: cross-reference item_ids against items that actually exist in
    # this room (same pattern as monster_ids below). The LLM invents item_ids
    # that reference nothing, so loot would silently never drop; remap each
    # entry onto a real room item id (round-robin), dropping loot entirely if
    # the room has no items. Always reference ids, never names.
    real_item_ids = (
        [_entity_int_id(e, 2000) for e in map_obj.entities if e.entity_type == "item"]
        if map_obj is not None
        else []
    )
    loot_table_raw = llm.get("loot_table", [])
    loot_table = []
    if real_item_ids:
        for i, entry in enumerate(loot_table_raw):
            if not isinstance(entry, dict):
                continue
            raw_id = entry.get("item_id")
            item_id = raw_id if raw_id in real_item_ids else real_item_ids[i % len(real_item_ids)]
            loot_table.append(
                {
                    "item_id": item_id,
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

    elif event_type in ("puzzle", "event"):
        # Solvable-puzzle assembly. Solve paths reference ENTITIES THAT ACTUALLY
        # EXIST (tool attributes from real room items; ability/spell names from
        # generated class pools), never LLM-invented names. The LLM only supplies
        # choice narrative. Every puzzle ends with a walk-away auto-success, so it
        # is always completable regardless of the player's class/inventory.
        tool_attr, ability_name, spell_name = _resolve_solve_refs(ctx, map_obj)

        # LLM choice texts are reused for narrative; mechanics are set here.
        llm_choices = [c for c in (llm.get("choices") or []) if isinstance(c, dict)]

        def _ctext(i: int, default: str) -> str:
            return llm_choices[i].get("text", default) if i < len(llm_choices) else default

        dc = {1: 10, 2: 13, 3: 16, 4: 18, 5: 20}.get(difficulty_int, 13)
        choices: list[dict] = [
            {
                "text": _ctext(0, "Solve it by wits"),
                "stat_check": "INT",
                "dc": dc,
                "auto_success": False,
                "success_text": "Your insight cracks the puzzle.",
            }
        ]
        ci = 1
        if tool_attr:
            choices.append({
                "text": _ctext(ci, f"Use a {tool_attr} tool"),
                "tool_attribute": tool_attr,
                "stat_check": None, "dc": dc, "auto_success": False,
                "success_text": "The right tool makes short work of it.",
            })
            ci += 1
        if ability_name:
            choices.append({
                "text": _ctext(ci, f"Use {ability_name}"),
                "stat_check": None, "dc": 0, "auto_success": False,
                "success_text": f"{ability_name} carries the day.",
            })
            ci += 1
        if spell_name:
            choices.append({
                "text": _ctext(ci, f"Cast {spell_name}"),
                "stat_check": None, "dc": 0, "auto_success": False,
                "success_text": f"{spell_name} resolves it.",
            })
            ci += 1
        choices.append({"text": "Walk away", "auto_success": True})

        base["choices"] = choices
        # correct_tool is the ATTRIBUTE (matches an item's item_stats.attribute),
        # not an item name; correct_ability/spell are real generated names.
        base["correct_tool"] = tool_attr
        base["correct_ability"] = ability_name
        base["correct_spell"] = spell_name
        base["failure_damage_type"] = llm.get("failure_damage_type", "health")
        base["failure_damage_range"] = [3 + room_level, 8 + room_level * 2]

    return base


def _resolve_solve_refs(
    ctx: BuildContext, map_obj: Any
) -> tuple[str | None, str | None, str | None]:
    """Return (tool_attribute, ability_name, spell_name) for a puzzle, drawn
    from entities that actually exist so each solve path is real:

    - tool_attribute: an attribute carried by a real item in this room (tagged
      by parse_item), or None if the room has no tool.
    - ability_name / spell_name: names from the generated class pools (union
      across archetypes), so a matching class can actually solve it.

    Picks deterministically by entity_index (BuildContext carries no rng).
    """
    idx = ctx.entity_index
    bible = ctx.bible

    tool_attrs: list[str] = []
    if map_obj is not None:
        for e in map_obj.entities:
            if e.entity_type == "item":
                tool_attrs += [t for t in (getattr(e, "tags", None) or []) if t in _TOOL_ATTRIBUTES]

    ability_names: list[str] = []
    spell_names: list[str] = []
    for arch in getattr(bible, "class_archetypes", {}).values():
        ability_names += [a.name for a in (arch.abilities or [])]
        ability_names += [a.name for a in (arch.ability_pool or [])]
        spell_names += [s.name for s in (arch.spells or [])]
        spell_names += [s.name for s in (arch.spell_pool or [])]

    tool_attr = tool_attrs[idx % len(tool_attrs)] if tool_attrs else None
    ability_name = ability_names[idx % len(ability_names)] if ability_names else None
    spell_name = spell_names[idx % len(spell_names)] if spell_names else None
    return tool_attr, ability_name, spell_name


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
    reward_tier = int(skel.get("reward_tier") or 1)

    xp = _QUEST_XP_BY_TIER.get(reward_tier, 50)

    allocated_id = ctx.allocated_id if ctx.allocated_id is not None else ctx.entity_index + 4000

    reward_raw = llm.get("reward", {})
    if not isinstance(reward_raw, dict):
        reward_raw = {}

    failure_raw = llm.get("failure_penalty", {})
    if not isinstance(failure_raw, dict):
        failure_raw = {}

    # Cross-reference sibling entities in this room. DatabasePhase populates
    # bible.maps[map_id].entities (Fix 1), so npc/item/event lookups work once
    # those phases have run before the quest phase.
    map_obj = ctx.map_obj
    room_npcs = []
    room_items = []
    room_events = []
    room_monsters = []
    if map_obj is not None:
        room_npcs = [e for e in map_obj.entities if e.entity_type == "npc"]
        room_items = [e for e in map_obj.entities if e.entity_type == "item"]
        room_events = [e for e in map_obj.entities if e.entity_type == "event"]
        room_monsters = [e for e in map_obj.entities if e.entity_type == "monster"]

    # First NPC is the quest giver.
    giver_npc_id = _entity_int_id(room_npcs[0], 1000) if room_npcs else None

    # reward.item_id must reference a real item — the LLM invents ids that point
    # at nothing. Keep the LLM's id only if it matches a real room item; else use
    # a real room item id, or null when the room has no items.
    real_item_ids = [_entity_int_id(e, 2000) for e in room_items]
    llm_reward_id = reward_raw.get("item_id")
    if llm_reward_id in real_item_ids:
        reward_item_id = llm_reward_id
    elif real_item_ids:
        reward_item_id = real_item_ids[0]
    else:
        reward_item_id = None

    quest = {
        "id": allocated_id,
        "type": quest_type,
        "title": llm.get("title", f"Quest_{allocated_id}"),
        "description": llm.get("description", ""),
        "giver_npc_id": giver_npc_id,
        "room_id": map_obj.map_id if map_obj is not None else None,
        # escort/combat/solve quests advance the world's conflict → story quests;
        # fetch quests are filler (mirrors mazeworld's is_story_quest tagging).
        "is_story_quest": quest_type in ("escort", "combat", "solve"),
        "prerequisite_quest_id": None,
        "portrait_prompt": None,
        "profile_image": None,
        "reward": {
            "xp": reward_raw.get("xp", xp),
            "item_id": reward_item_id,
        },
        "failure_penalty": {
            "hp_damage": failure_raw.get("hp_damage", 5),
        },
        "success_dialogue": llm.get("success_dialogue", "Well done."),
        "failure_dialogue": llm.get("failure_dialogue", "You have failed."),
        "is_complete": False,
        "is_failed": False,
    }

    # Quest-type-specific fields. Without these, mazeworld's Escort/Fetch/Combat
    # quest subclasses fall back to uncompletable defaults (target_zone=[0,0],
    # target_items=[], target_event_id=0). Populate them from sibling entities.
    quest.update(_quest_type_fields(quest_type, map_obj, room_npcs, room_items,
                                    room_events, room_monsters, giver_npc_id))
    return quest


def _quest_type_fields(
    quest_type: str,
    map_obj: Any,
    room_npcs: list,
    room_items: list,
    room_events: list,
    room_monsters: list,
    giver_npc_id: int | None,
) -> dict:
    """Build the quest-type-specific fields mazeworld's Quest subclasses need."""
    if quest_type == "escort":
        # Escort a room NPC (prefer one other than the giver) to the maze exit.
        escortee = None
        for npc in room_npcs:
            nid = _entity_int_id(npc, 1000)
            if nid != giver_npc_id:
                escortee = nid
                break
        if escortee is None:
            escortee = giver_npc_id if giver_npc_id is not None else 1000
        # target_zone = the maze's door/exit tile. npc/event placements are not
        # assigned at quest-parse time (a later phase fills them), but the
        # layout's door_position IS set at generation; fall back to player_start.
        layout = getattr(map_obj, "layout", None)
        tile = (
            getattr(layout, "door_position", None)
            or getattr(layout, "player_start", None)
            or [1, 1]
        )
        return {
            "escort_npc_id": escortee,
            "target_zone": [int(tile[0]), int(tile[1])],
            "destination_room": 0,
        }
    if quest_type == "fetch":
        target_items = []
        if room_items:
            target_items = [{"item_id": _entity_int_id(room_items[0], 2000), "count": 1}]
        return {"target_items": target_items}
    if quest_type in ("solve", "combat"):
        target_event_id = _entity_int_id(room_events[0], 3000) if room_events else 0
        fields: dict = {"target_event_id": target_event_id}
        if quest_type == "combat":
            # Reference a real generated monster by name (or None if the room
            # has none) instead of leaving it unresolved.
            fields["target_monster_name"] = (
                room_monsters[0].name if room_monsters else None
            )
        return fields
    return {}
