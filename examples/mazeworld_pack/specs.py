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
from canon.pipeline.phases.class_archetype import ClassLoadoutSpec, LoadoutGroup
from canon.pipeline.phases.spell_pool import PoolSpec

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
# Monster — level-scaled via the roll_skeleton context seam.
#
# Mechanics (hp_range / ac_range / damage_die) are derived from the monster's
# tier AND the room_level passed into roll_skeleton, instead of a flat range.
# This is pack DATA: _MONSTER_LEVEL_BANDS ports MazeWorld's LEVEL_SCALING
# (src/models/monster.py:171); _scale_monster applies a tier modifier. Core
# only threads room_level through — it knows nothing about levels or tiers.
# ---------------------------------------------------------------------------

# room_level -> baseline bands (hp/ac ranges + damage dice), per MazeWorld's
# LEVEL_SCALING. instantiate_monster rolls live hp/ac within hp_range/ac_range.
_MONSTER_LEVEL_BANDS = {
    1: {"hp": (8, 12), "ac": (10, 12), "dice": ("1d4", "1d6")},
    2: {"hp": (12, 20), "ac": (11, 13), "dice": ("1d6", "1d8")},
    3: {"hp": (18, 25), "ac": (12, 15), "dice": ("1d6", "1d10")},
    4: {"hp": (25, 30), "ac": (13, 16), "dice": ("1d8", "1d12")},
}
# tier toughens the baseline band: hp multiplier, AC bonus, and which end of
# the level's dice pair the tier hits with.
_TIER_HP_MULT = {"minion": 1.0, "elite": 1.6, "boss": 2.5}
_TIER_AC_BONUS = {"minion": 0, "elite": 1, "boss": 2}


def _scale_monster(rolled: dict, context: dict) -> dict:
    """Derive hp_range/ac_range/damage_die from (tier, room_level).

    Composite scaling the declarative lookup can't express in one field, so it
    lives in this pack-side post callback. room_level is clamped into the band
    table (covers num_maps beyond the table and a missing/None level).
    """
    raw_level = context.get("room_level") or 1
    level = max(1, min(int(raw_level), max(_MONSTER_LEVEL_BANDS)))
    band = _MONSTER_LEVEL_BANDS[level]
    tier = rolled.get("tier", "minion")

    hp_mult = _TIER_HP_MULT.get(tier, 1.0)
    hp_lo, hp_hi = band["hp"]
    rolled["hp_range"] = [round(hp_lo * hp_mult), round(hp_hi * hp_mult)]

    ac_lo, ac_hi = band["ac"]
    ac_bonus = _TIER_AC_BONUS.get(tier, 0)
    rolled["ac_range"] = [ac_lo + ac_bonus, ac_hi + ac_bonus]

    easy_die, hard_die = band["dice"]
    rolled["damage_die"] = hard_die if tier in ("elite", "boss") else easy_die
    return rolled


MONSTER_SPEC = SkeletonSpec(
    entity_type="monster",
    fields={
        "tier": SkeletonField(
            choices=[("minion", 4), ("elite", 2), ("boss", 1)]
        ),
        # physical_type is the natural-attack damage shape (mazeworld vocab),
        # NOT the creature kind — that belongs in the LLM-generated `species`.
        "physical_type": SkeletonField(
            choices=[("slashing", 1), ("piercing", 1), ("bludgeoning", 1)]
        ),
        # When a monster is encounterable (mazeworld vocab); mostly always-on,
        # occasionally gated to night/day for day-night-cycle variety.
        "time_availability": SkeletonField(
            choices=[("always", 6), ("night_only", 2), ("day_only", 1)]
        ),
    },
    post=_scale_monster,
)

# ---------------------------------------------------------------------------
# Item — one spec across all mazeworld categories, level-scaled via the
# roll_skeleton context seam.
#
# item_kind IS the mazeworld category (weapon/food/drink/tool/spell_scroll), so
# the prompt (which reads skeleton["item_kind"]), the parser branch, and the
# loader all agree — no more weapon-prompt-vs-consumable-skeleton desync.
# Mechanics are pack DATA: weapon dice scale by (weapon_type, room_level) via
# MazeWorld's DIE_PROGRESSION; consumable restore scales by room_level. Core
# only threads room_level; _scale_item owns the tables.
# ---------------------------------------------------------------------------

# weapon_type -> governing stat (MazeWorld WEAPON_TYPES, src/models/weapon.py:10)
_WEAPON_TYPE_STAT = {
    "heavy": "STR", "light": "DEX", "sacred": "CON",
    "arcane": "INT", "enchanted": "WIS", "wild": "LUCK",
}
# Shared dice progression + per-type base tier (MazeWorld weapon.py:34,54).
_DIE_PROGRESSION = [
    (1, 4), (1, 6), (1, 8), (1, 10), (1, 12),
    (2, 6), (2, 8), (2, 10), (2, 12),
    (3, 6), (3, 8), (3, 10), (3, 12),
    (4, 6), (4, 8), (4, 10), (4, 12),
]
_WEAPON_TYPE_BASE_TIER = {
    "heavy": 2, "light": 1, "sacred": 1, "arcane": 0, "enchanted": 0, "wild": 1,
}
# Consumable magnitude multiplier by room level (MazeWorld _consumable_mult).
_CONSUMABLE_LEVEL_MULT = {1: 1.0, 2: 1.3, 3: 1.6, 4: 2.0}
_RARITY_PRICE_BONUS = {"common": 0, "uncommon": 10, "rare": 25}


def _scale_item(rolled: dict, context: dict) -> dict:
    """Derive level-scaled mechanics for the rolled item_kind.

    Weapons: attack_dice from (weapon_type, room_level) via DIE_PROGRESSION.
    Consumables (food/drink): restore_amount scaled by room_level. Tools: keep
    the rolled attribute. room_level is clamped (covers num_maps > table + None).
    """
    raw_level = context.get("room_level") or 1
    level = max(1, min(int(raw_level), 4))
    kind = rolled.get("item_kind", "tool")
    rarity_bonus = _RARITY_PRICE_BONUS.get(rolled.get("rarity"), 0)

    if kind == "weapon":
        wtype = rolled.get("weapon_type", "light")
        tier = min(
            _WEAPON_TYPE_BASE_TIER.get(wtype, 1) + (level - 1),
            len(_DIE_PROGRESSION) - 1,
        )
        num_dice, die_sides = _DIE_PROGRESSION[tier]
        rolled["attack_dice"] = f"{num_dice}d{die_sides}"
        rolled["price"] = 10 * level + rarity_bonus
    elif kind in ("food", "drink"):
        mult = _CONSUMABLE_LEVEL_MULT.get(level, 1.0)
        rolled["restore_amount"] = round(rolled.get("base_restore", 10) * mult)
        rolled["price"] = 5 * level + rarity_bonus
    else:  # tool, spell_scroll
        rolled["price"] = 8 * level + rarity_bonus
    return rolled


ITEM_SPEC = SkeletonSpec(
    entity_type="item",
    fields={
        # item_kind == mazeworld category. Weapons weighted up (combat-relevant).
        "item_kind": SkeletonField(
            choices=[
                ("weapon", 3), ("food", 2), ("drink", 2),
                ("tool", 2), ("spell_scroll", 1),
            ]
        ),
        "rarity": SkeletonField(
            choices=[("common", 4), ("uncommon", 2), ("rare", 1)]
        ),
        # weapon mechanics (parser reads these only when item_kind == "weapon")
        "weapon_type": SkeletonField(
            choices=[
                ("heavy", 18), ("light", 18), ("sacred", 18),
                ("arcane", 18), ("enchanted", 18), ("wild", 10),
            ]
        ),
        "weapon_category": SkeletonField(choices=[("simple", 60), ("martial", 40)]),
        "physical_type": SkeletonField(
            choices=[("slashing", 1), ("piercing", 1), ("bludgeoning", 1)]
        ),
        "weapon_stat": SkeletonField(
            lookup=_WEAPON_TYPE_STAT, depends_on="weapon_type"
        ),
        # tool mechanic (used when item_kind == "tool"): puzzle-solve attribute
        "tool_attribute": SkeletonField(
            choices=[("bludgeon", 1), ("cutting", 1), ("digging", 1), ("climbing", 1)]
        ),
        # consumable base magnitude, scaled by room_level in _scale_item
        "base_restore": SkeletonField(range=(5, 15)),
    },
    post=_scale_item,
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
                ("combat", 3),  # random encounters
                ("puzzle", 2),  # stat-check / ability / spell challenges
                ("event", 2),   # skill/narrative events (same interactive shape)
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

# ---------------------------------------------------------------------------
# Spell pools — mirrors mazeworld's POOL_CONFIGS + visible_pools.
#
# Four named pools (mage_damage, healer_damage, heal, buff) consumed by
# mazeworld's PlayerClass._load_spell_pool_choices. Mechanics are pinned via
# field_overrides to mazeworld-compatible Spell values (so Spell(**entry) loads
# and combat semantics work); SPELL_SPEC still supplies stamina_cost variety.
# prompt_kwargs only theme the LLM-generated name/description.
# ---------------------------------------------------------------------------

MAZEWORLD_POOL_SPECS = [
    PoolSpec(
        name="mage_damage",
        skeleton_spec=SPELL_SPEC,
        count=10,
        field_overrides={
            "spell_type": "damage_single",
            "element": "fire",
            "stat": "INT",
            "targets": "single",
            "num_dice": 1,
            "die_sides": 6,
        },
        prompt_kwargs={"archetype": "mage", "element": "fire"},
    ),
    PoolSpec(
        name="healer_damage",
        skeleton_spec=SPELL_SPEC,
        count=10,
        field_overrides={
            "spell_type": "damage_single",
            "element": "light",
            "stat": "WIS",
            "targets": "single",
            "num_dice": 1,
            "die_sides": 4,
        },
        prompt_kwargs={"archetype": "healer", "element": "light"},
    ),
    PoolSpec(
        name="heal",
        skeleton_spec=SPELL_SPEC,
        count=10,
        field_overrides={
            "spell_type": "heal",
            "element": "light",
            "stat": "WIS",
            "targets": "self",
            "heal_amount": 8,
        },
        prompt_kwargs={"archetype": "healer", "element": "light"},
    ),
    PoolSpec(
        name="buff",
        skeleton_spec=SPELL_SPEC,
        count=10,
        field_overrides={
            "spell_type": "buff_stat",
            "element": "nature",
            "stat": "WIS",
            "targets": "self",
            "buff_value": 2,
            "buff_duration": 3,
        },
        prompt_kwargs={"archetype": "healer", "element": "nature"},
    ),
]


# ---------------------------------------------------------------------------
# Class loadouts — full per-archetype generation specs for ClassPhase.
#
# Ports MazeWorld's SPELL_DISTRIBUTIONS (src/models/spell.py:94) and
# ABILITY_DISTRIBUTIONS (src/models/player.py:32) as pack data. Stat templates
# + roles are reused from the *_ARCHETYPE constants above; fix_stats
# redistributes them to STAT_BUDGET while preserving each archetype's emphasis
# (mage INT-high, warrior STR-high). This is the canonical "RPG class" template
# pattern other game packs can copy.
#
# Note: warriors get only quest/utility ABILITIES (no spells) — matched by name
# in puzzles; casters get spells. Per-slot dice are level-1 baselines (MazeWorld
# rescales at acquisition time).
# ---------------------------------------------------------------------------

_STAT_NAMES = ["STR", "DEX", "CON", "INT", "WIS", "CHA", "LUCK"]
_STAT_BUDGET = 95
_ROLE_RANGES = {"primary": (14, 18), "secondary": (12, 16), "dump": (8, 12)}
_JESTER_ROLE_RANGES = {"primary": (14, 18), "secondary": (11, 15), "dump": (8, 12)}


def _spell(spell_type, element, stat, targets, num_dice=0, die_sides=0, **extra):
    """Build a per-slot spell field_overrides dict (mechanics pinned; the LLM
    only names/describes)."""
    slot = {
        "spell_type": spell_type, "element": element, "stat": stat,
        "targets": targets, "num_dice": num_dice, "die_sides": die_sides,
    }
    slot.update(extra)
    return slot


def _abil(stat, stamina_cost):
    """Build a per-slot ability field_overrides dict."""
    return {"stat": stat, "stamina_cost": stamina_cost}


# Mage: INT damage + buffs (element=fire).
_MAGE_SPELLS = LoadoutGroup(
    skeleton_spec=SPELL_SPEC,
    starting=[
        _spell("damage_single", "fire", "INT", "single", 1, 6),
        _spell("damage_single", "fire", "INT", "single", 1, 6),
        _spell("damage_multi", "fire", "INT", "multi", 1, 4),
        _spell("buff_stat", "fire", "INT", "self", buff_stat="INT", buff_value=2, buff_duration=3),
    ],
    pool=[
        _spell("damage_single", "fire", "INT", "single", 1, 8),
        _spell("damage_multi", "fire", "INT", "multi", 1, 6),
        _spell("buff_stat", "fire", "INT", "self", buff_stat="INT", buff_value=2, buff_duration=3),
        _spell("buff_sustain", "fire", "INT", "self", buff_value=1, buff_duration=4),
    ],
    prompt_kwargs={"archetype": "mage", "element": "fire"},
)

# Healer: WIS heals + buffs + light damage (element=light).
_HEALER_SPELLS = LoadoutGroup(
    skeleton_spec=SPELL_SPEC,
    starting=[
        _spell("heal", "light", "WIS", "self", 1, 6, heal_amount=8),
        _spell("buff_stat", "light", "WIS", "self", buff_stat="WIS", buff_value=2, buff_duration=3),
        _spell("damage_single", "light", "WIS", "single", 1, 4),
        _spell("buff_sustain", "light", "WIS", "self", buff_value=1, buff_duration=4),
    ],
    pool=[
        _spell("heal", "light", "WIS", "self", 1, 8, heal_amount=12),
        _spell("damage_single", "light", "WIS", "single", 1, 6),
        _spell("buff_stat", "light", "WIS", "self", buff_stat="WIS", buff_value=3, buff_duration=3),
        _spell("damage_multi", "light", "WIS", "multi", 1, 4),
    ],
    prompt_kwargs={"archetype": "healer", "element": "light"},
)

# Warrior: STR/CHA quest & utility ABILITIES (no spells). Purposes mirror
# MazeWorld's ABILITY_DISTRIBUTIONS; the LLM names them in-world.
_WARRIOR_ABILITIES = LoadoutGroup(
    skeleton_spec=ABILITY_SPEC,
    starting=[_abil("STR", 8), _abil("CHA", 3), _abil("STR", 5), _abil("CHA", 5)],
    pool=[_abil("STR", 4), _abil("WIS", 3), _abil("STR", 6), _abil("CHA", 7)],
    prompt_kwargs={"archetype": "warrior", "purpose": "utility and combat maneuvers"},
)

# Jester: LUCK trickster — a full mixed loadout sampling both schools (damage +
# heal + buff), mirroring MazeWorld's "jester steals from mage & healer pools".
# LUCK-governed throughout; chaotic dark/light flavor.
_JESTER_SPELLS = LoadoutGroup(
    skeleton_spec=SPELL_SPEC,
    starting=[
        _spell("damage_multi", "dark", "LUCK", "multi", 1, 4),
        _spell("damage_single", "dark", "LUCK", "single", 1, 6),
        _spell("heal", "light", "LUCK", "self", 1, 4, heal_amount=6),
        _spell("buff_stat", "dark", "LUCK", "self", buff_stat="LUCK", buff_value=2, buff_duration=3),
    ],
    pool=[
        _spell("damage_single", "dark", "LUCK", "single", 1, 8),
        _spell("damage_multi", "dark", "LUCK", "multi", 1, 6),
        _spell("heal", "light", "LUCK", "self", 1, 6, heal_amount=10),
        _spell("buff_sustain", "light", "LUCK", "self", buff_value=1, buff_duration=4),
    ],
    prompt_kwargs={"archetype": "jester", "element": "chaos"},
)
_JESTER_ABILITIES = LoadoutGroup(
    skeleton_spec=ABILITY_SPEC,
    starting=[_abil("LUCK", 4), _abil("CHA", 3)],
    pool=[_abil("DEX", 5), _abil("LUCK", 6)],
    prompt_kwargs={"archetype": "jester", "purpose": "trickery, luck, and misdirection"},
)


def _loadout(arch_const, *, ranges=_ROLE_RANGES, weapon="", spells=None, abilities=None):
    """Build a ClassLoadoutSpec from one of the *_ARCHETYPE constants, reusing
    its stat_template + stat_roles so the archetype shape is single-sourced."""
    return ClassLoadoutSpec(
        archetype=arch_const.archetype,
        name=arch_const.name,
        stat_template=dict(arch_const.stat_template),
        stat_roles=dict(arch_const.stat_roles),
        stat_budget=_STAT_BUDGET,
        role_ranges=ranges,
        stat_names=_STAT_NAMES,
        starting_weapon=weapon,
        spells=spells,
        abilities=abilities,
    )


# Order matters: ClassPhase generates one class per loadout (a count override
# slices this list), and warrior/mage are the first two for the trial config.
MAZEWORLD_CLASS_LOADOUTS = [
    _loadout(WARRIOR_ARCHETYPE, weapon="Iron Sword", abilities=_WARRIOR_ABILITIES),
    _loadout(MAGE_ARCHETYPE, weapon="Oak Staff", spells=_MAGE_SPELLS),
    _loadout(HEALER_ARCHETYPE, weapon="Blessed Mace", spells=_HEALER_SPELLS),
    _loadout(
        JESTER_ARCHETYPE, ranges=_JESTER_ROLE_RANGES, weapon="Throwing Daggers",
        spells=_JESTER_SPELLS, abilities=_JESTER_ABILITIES,
    ),
]
