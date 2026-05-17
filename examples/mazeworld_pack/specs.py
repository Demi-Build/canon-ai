"""MazeWorld SkeletonSpecs and ClassArchetype constants.

Defines the pre-roll shapes for every entity type mazeworld generates.
Copied/adapted from tests/reference/mazeworld_data.py (Wave 1-4 reference
data) and extended with NPC_SPEC, EVENT_SPEC, QUEST_SPEC, ABILITY_SPEC.

Design decisions:
- NPC_SPEC: minimal skeleton — NPCs are mostly LLM-generated prose. One
  ``behavior_type`` field guides the LLM toward the right NPC role and
  lets the parser set the mazeworld ``type`` field deterministically.
- EVENT_SPEC: ``event_type`` + ``difficulty`` are pre-rolled; monster_count
  is only relevant for combat events, rolled from a tight range.
- QUEST_SPEC: ``quest_type`` is pre-rolled from the four mazeworld types;
  ``reward_tier`` drives XP scaling in the parser without exposing exact
  numbers to the LLM.
- ABILITY_SPEC: ``purpose`` field guides the LLM toward offensive/defensive/
  utility ability names without over-constraining the narrative.
"""

from canon import ClassArchetype, SkeletonField, SkeletonSpec

# ---------------------------------------------------------------------------
# Weapon — carried over from tests/reference/mazeworld_data.py
# ---------------------------------------------------------------------------

WEAPON_SPEC = SkeletonSpec(
    entity_type="weapon",
    fields={
        "weapon_type": SkeletonField(
            choices=[
                ("heavy", 3),
                ("light", 3),
                ("sacred", 1),
                ("arcane", 1),
                ("enchanted", 1),
                ("wild", 1),
            ]
        ),
        "weapon_category": SkeletonField(
            choices=[("simple", 2), ("martial", 1)]
        ),
        "damage_type": SkeletonField(
            choices=[
                ("slashing", 1),
                ("piercing", 1),
                ("bludgeoning", 1),
            ]
        ),
        "stat_modifier": SkeletonField(
            lookup={
                "heavy": "STR",
                "light": "DEX",
                "sacred": "WIS",
                "arcane": "INT",
                "enchanted": "CHA",
                "wild": "CON",
            },
            depends_on="weapon_type",
        ),
        "attack_dice": SkeletonField(
            choices=[
                ("1d4", 2),
                ("1d6", 3),
                ("1d8", 2),
                ("1d10", 1),
                ("2d6", 1),
            ]
        ),
    },
    post=lambda skel: {**skel, "magic_element": "none"},
)

# ---------------------------------------------------------------------------
# Spell — carried over from tests/reference/mazeworld_data.py
# ---------------------------------------------------------------------------

SPELL_SPEC = SkeletonSpec(
    entity_type="spell",
    fields={
        "spell_type": SkeletonField(
            choices=[("damage", 3), ("heal", 1), ("buff", 1)]
        ),
        "element": SkeletonField(
            choices=[
                ("fire", 1),
                ("frost", 1),
                ("light", 1),
                ("dark", 1),
                ("nature", 1),
            ]
        ),
        "stat": SkeletonField(
            choices=[("INT", 3), ("WIS", 2), ("CHA", 1)]
        ),
        "stamina_cost": SkeletonField(range=(2, 8)),
    },
)

# ---------------------------------------------------------------------------
# Monster — carried over from tests/reference/mazeworld_data.py
# ---------------------------------------------------------------------------

MONSTER_SPEC = SkeletonSpec(
    entity_type="monster",
    fields={
        "tier": SkeletonField(
            choices=[("minion", 4), ("elite", 2), ("boss", 1)]
        ),
        "hp": SkeletonField(range=(8, 80)),
        "ac": SkeletonField(range=(10, 18)),
        "damage_die": SkeletonField(
            choices=[("1d4", 3), ("1d6", 2), ("1d8", 1), ("2d6", 1)]
        ),
    },
)

# ---------------------------------------------------------------------------
# Item (non-weapon consumables/trinkets) — from tests/reference/mazeworld_data.py
# ---------------------------------------------------------------------------

ITEM_SPEC = SkeletonSpec(
    entity_type="item",
    fields={
        "item_kind": SkeletonField(
            choices=[
                ("potion", 3),
                ("scroll", 2),
                ("trinket", 2),
                ("key", 1),
            ]
        ),
        "rarity": SkeletonField(
            choices=[("common", 4), ("uncommon", 2), ("rare", 1)]
        ),
    },
)

# ---------------------------------------------------------------------------
# NPC — minimal skeleton; NPCs are LLM-generated prose + a behavior hint
#
# Design: ``behavior_type`` is the one mechanical pre-roll we need to map
# the NPC to mazeworld's runtime type ("StaticNPC", "MerchantNPC", etc.).
# All rich narrative fields (name, job, hobby, backstory, etc.) come from
# the LLM via npc_generation(); the skeleton is deliberately sparse so the
# LLM has maximum creative latitude.
# ---------------------------------------------------------------------------

NPC_SPEC = SkeletonSpec(
    entity_type="npc",
    fields={
        "behavior_type": SkeletonField(
            choices=[
                ("static", 4),      # most NPCs are stationary
                ("wandering", 2),   # some wander their environment
                ("merchant", 2),    # vendors
                ("aggressive", 1),  # hostile on sight
            ]
        ),
    },
)

# ---------------------------------------------------------------------------
# Event — pre-roll type + difficulty; monster_count only used for combat
#
# Design: ``event_type`` determines whether the LLM generates a combat
# encounter or a puzzle encounter.  ``difficulty`` sets the DC/level band.
# ``monster_count`` is only consulted by parse_event for combat events;
# puzzle events ignore it.
# ---------------------------------------------------------------------------

EVENT_SPEC = SkeletonSpec(
    entity_type="event",
    fields={
        "event_type": SkeletonField(
            choices=[
                ("combat", 3),
                ("puzzle", 2),
            ]
        ),
        "difficulty": SkeletonField(
            choices=[
                ("easy", 3),
                ("medium", 2),
                ("hard", 1),
            ]
        ),
        "monster_count": SkeletonField(range=(1, 4)),
    },
)

# ---------------------------------------------------------------------------
# Quest — pre-roll type + reward tier
#
# Design: ``quest_type`` maps to mazeworld's four quest types (escort, fetch,
# solve, combat).  ``reward_tier`` is a numeric tier (1-3) that the parser
# converts to XP ranges; it keeps the LLM prompt cleaner than passing raw
# numbers.
# ---------------------------------------------------------------------------

QUEST_SPEC = SkeletonSpec(
    entity_type="quest",
    fields={
        "quest_type": SkeletonField(
            choices=[
                ("escort", 2),
                ("fetch", 3),
                ("solve", 2),
                ("combat", 2),
            ]
        ),
        "reward_tier": SkeletonField(range=(1, 3)),
    },
)

# ---------------------------------------------------------------------------
# Ability — guides LLM toward a purpose-appropriate active ability
#
# Design: ``purpose`` labels the intended use of the ability; ``stat`` is
# the primary stat the ability scales with (pre-rolled to match archetype
# expectations rather than leaving it to LLM guesswork).
# ---------------------------------------------------------------------------

ABILITY_SPEC = SkeletonSpec(
    entity_type="ability",
    fields={
        "purpose": SkeletonField(
            choices=[
                ("offensive", 3),
                ("defensive", 2),
                ("utility", 2),
                ("support", 1),
            ]
        ),
        "stat": SkeletonField(
            choices=[
                ("STR", 2),
                ("DEX", 2),
                ("CON", 1),
                ("INT", 1),
                ("WIS", 1),
                ("CHA", 1),
                ("LUCK", 1),
            ]
        ),
        "stamina_cost": SkeletonField(range=(2, 10)),
    },
)


# ---------------------------------------------------------------------------
# ClassArchetype constants — carried over from tests/reference/mazeworld_data.py
# ---------------------------------------------------------------------------

WARRIOR_ARCHETYPE = ClassArchetype(
    archetype_id="warrior",
    archetype="warrior",
    name="Warrior",
    description="Frontline melee combatant",
    category="combat",
    stat_template={
        "STR": 16,
        "CON": 14,
        "DEX": 12,
        "INT": 8,
        "WIS": 10,
        "CHA": 10,
        "LUCK": 10,
    },
    stat_roles={
        "primary": ["STR", "CON"],
        "secondary": ["DEX", "CHA"],
        "dump": ["INT", "WIS"],
    },
    role_tags=["tank", "melee_dps"],
)

MAGE_ARCHETYPE = ClassArchetype(
    archetype_id="mage",
    archetype="mage",
    name="Mage",
    description="Arcane caster",
    category="caster",
    stat_template={
        "STR": 8,
        "CON": 10,
        "DEX": 12,
        "INT": 16,
        "WIS": 14,
        "CHA": 10,
        "LUCK": 10,
    },
    stat_roles={
        "primary": ["INT"],
        "secondary": ["WIS", "DEX"],
        "dump": ["STR", "CON", "CHA"],
    },
    role_tags=["caster", "ranged_dps"],
)

HEALER_ARCHETYPE = ClassArchetype(
    archetype_id="healer",
    archetype="healer",
    name="Healer",
    description="Support caster",
    category="support",
    stat_template={
        "STR": 10,
        "CON": 12,
        "DEX": 10,
        "INT": 12,
        "WIS": 16,
        "CHA": 14,
        "LUCK": 10,
    },
    stat_roles={
        "primary": ["WIS"],
        "secondary": ["CHA", "INT"],
        "dump": ["STR", "DEX"],
    },
    role_tags=["support", "healer"],
)

JESTER_ARCHETYPE = ClassArchetype(
    archetype_id="jester",
    archetype="jester",
    name="Jester",
    description="Trickster",
    category="utility",
    stat_template={
        "STR": 10,
        "CON": 10,
        "DEX": 14,
        "INT": 12,
        "WIS": 10,
        "CHA": 16,
        "LUCK": 12,
    },
    stat_roles={
        "primary": ["CHA", "DEX"],
        "secondary": ["INT", "LUCK"],
        "dump": ["STR"],
    },
    role_tags=["utility", "trickster"],
)
