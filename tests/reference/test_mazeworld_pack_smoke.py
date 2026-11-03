"""Smoke test for canon.packs.dungeon composition.

Does NOT run the pipeline (no LLM calls, no asset generation).
Just verifies imports work and compose_pipeline() returns sensible output.

The full end-to-end test lives in tests/reference/test_mazeworld_full.py
(Wave 5 M2).
"""



def test_specs_importable():
    from canon.packs.dungeon.specs import (
        ABILITY_SPEC,
        EVENT_SPEC,
        HEALER_ARCHETYPE,
        JESTER_ARCHETYPE,
        MAGE_ARCHETYPE,
        NPC_SPEC,
        QUEST_SPEC,
        WARRIOR_ARCHETYPE,
        WEAPON_SPEC,
    )

    assert WEAPON_SPEC.entity_type == "weapon"
    assert NPC_SPEC.entity_type == "npc"
    assert EVENT_SPEC.entity_type == "event"
    assert QUEST_SPEC.entity_type == "quest"
    assert ABILITY_SPEC.entity_type == "ability"
    assert WARRIOR_ARCHETYPE.archetype == "warrior" or WARRIOR_ARCHETYPE.name
    assert MAGE_ARCHETYPE.archetype == "mage"
    assert HEALER_ARCHETYPE.archetype == "healer"
    assert JESTER_ARCHETYPE.archetype == "jester"


def test_parsers_callable():
    from canon.packs.dungeon.parsers import (
        parse_event,
        parse_item,
        parse_monster,
        parse_npc,
        parse_quest,
    )

    # Just verify they're functions; behaviour is tested via DatabasePhase tests
    assert callable(parse_npc)
    assert callable(parse_item)
    assert callable(parse_monster)
    assert callable(parse_event)
    assert callable(parse_quest)


def test_prompt_set_importable():
    from canon.llm.prompts import DefaultPromptSet, PromptSet
    from canon.packs.dungeon.prompts import MazeworldPromptSet

    ps = MazeworldPromptSet()
    assert isinstance(ps, DefaultPromptSet)
    assert isinstance(ps, PromptSet)


def test_compose_pipeline_returns_phases_and_ctx():
    from canon.packs.dungeon.compose import compose_pipeline

    phases, ctx = compose_pipeline(seed="test", num_maps=2)
    assert len(phases) >= 8  # story, class, maze, char, 5+ db, dialogue, asset, manifest
    assert ctx.bible.seed == "test"
    assert len(ctx.bible.maps) == 2


def test_compose_pipeline_includes_all_database_specs():
    from canon.packs.dungeon.compose import compose_pipeline
    from canon.pipeline.phases import DatabasePhase

    phases, ctx = compose_pipeline(seed="test", num_maps=2)
    db_phases = [p for p in phases if isinstance(p, DatabasePhase)]
    db_types = {p.spec.entity_type for p in db_phases}
    assert {"npc", "item", "monster", "event", "quest"} <= db_types


def test_compose_pipeline_phase_order():
    from canon.packs.dungeon.compose import compose_pipeline

    phases, _ = compose_pipeline(seed="test", num_maps=2)
    names = [p.name for p in phases]

    assert names.index("story") < names.index("classes")
    assert names.index("manifest") == len(names) - 1  # Last phase


def test_compose_mazeworld_specs_returns_list():
    from canon.packs.dungeon.compose import compose_mazeworld_specs
    from canon.pipeline.phases.database import DatabaseSpec

    specs = compose_mazeworld_specs(num_maps=3)
    assert isinstance(specs, list)
    assert len(specs) == 5
    for spec in specs:
        assert isinstance(spec, DatabaseSpec)
        assert callable(spec.parser)


def test_npc_spec_has_behavior_type_field():
    from canon.packs.dungeon.specs import NPC_SPEC

    assert "behavior_type" in NPC_SPEC.fields


def test_event_spec_has_event_type_and_difficulty():
    from canon.packs.dungeon.specs import EVENT_SPEC

    assert "event_type" in EVENT_SPEC.fields
    assert "difficulty" in EVENT_SPEC.fields
    assert "monster_count" in EVENT_SPEC.fields


def test_quest_spec_has_quest_type_and_reward_tier():
    from canon.packs.dungeon.specs import QUEST_SPEC

    assert "quest_type" in QUEST_SPEC.fields
    assert "reward_tier" in QUEST_SPEC.fields


def test_ability_spec_has_purpose_stat_stamina():
    from canon.packs.dungeon.specs import ABILITY_SPEC

    assert "purpose" in ABILITY_SPEC.fields
    assert "stat" in ABILITY_SPEC.fields
    assert "stamina_cost" in ABILITY_SPEC.fields


def test_compose_pipeline_maps_have_environments():
    from canon.packs.dungeon.compose import compose_pipeline

    phases, ctx = compose_pipeline(seed="test", num_maps=4)
    maps = list(ctx.bible.maps.values())
    assert len(maps) == 4
    for m in maps:
        assert m.environment, f"Map {m.map_id} has no environment"


def test_compose_pipeline_context_has_all_archetypes():
    from canon.packs.dungeon.compose import compose_pipeline

    _, ctx = compose_pipeline(seed="test", num_maps=1)
    assert set(ctx.archetypes.keys()) == {"warrior", "mage", "healer", "jester"}


def test_parse_npc_returns_expected_keys():
    """parse_npc returns a dict with all required mazeworld NPC keys."""
    from canon.packs.dungeon.parsers import parse_npc
    from canon.pipeline.phases.database import BuildContext

    ctx = BuildContext(
        map_id="room_0",
        map_obj=None,
        skeleton={"behavior_type": "static"},
        llm_response={
            "name": "Tara",
            "job": "herbalist",
            "personality": "calm",
            "hobby": "foraging",
            "backstory": "Came from the mountains.",
            "opening_greeting": "Welcome.",
            "portrait_prompt": "a calm woman",
        },
        entity_index=0,
        allocated_id=1000,
        bible=None,
    )
    result = parse_npc(ctx)

    required_keys = {
        "id", "type", "name", "job", "personality", "hobby",
        "backstory", "environment", "environment_name",
        "opening_greeting", "portrait_prompt", "profile_image",
        "dialogue_tree", "quest_id", "max_dialogue_turns", "is_story_npc",
        "x", "y", "selected", "exhausted_dialogue", "personality_notes", "color",
    }
    assert required_keys <= set(result.keys()), (
        f"Missing keys: {required_keys - set(result.keys())}"
    )
    assert result["id"] == 1000
    assert result["type"] == "StaticNPC"
    assert result["name"] == "Tara"
    assert isinstance(result["color"], list)
    assert len(result["color"]) == 3


def test_parse_item_weapon_has_item_stats():
    """parse_item for a weapon returns item_stats with attack_dice."""
    from canon.packs.dungeon.parsers import parse_item
    from canon.pipeline.phases.database import BuildContext

    ctx = BuildContext(
        map_id="room_0",
        map_obj=None,
        skeleton={
            "item_kind": "weapon",
            "weapon_type": "heavy",
            "weapon_category": "martial",
            "damage_type": "slashing",
            "stat_modifier": "STR",
            "attack_dice": "1d8",
            "magic_element": "none",
            "rarity": "common",
        },
        llm_response={
            "name": "Iron Maul",
            "desc": "A heavy bludgeoning weapon.",
            "weapon_type": "heavy",
            "damage_type": "bludgeoning",
            "weapon_category": "martial",
            "item_stats": {"attack_dice": "1d8", "stat_modifier": "STR", "price": 25},
        },
        entity_index=0,
        allocated_id=2000,
        bible=None,
    )
    result = parse_item(ctx)

    assert result["id"] == 2000
    assert result["category"] == "weapon"
    assert "item_stats" in result
    assert "attack_dice" in result["item_stats"]


def test_parse_monster_returns_hp_and_ac_ranges():
    """parse_monster synthesises hp_range and ac_range from skeleton fallback."""
    from canon.packs.dungeon.parsers import parse_monster
    from canon.pipeline.phases.database import BuildContext

    ctx = BuildContext(
        map_id="room_0",
        map_obj=None,
        skeleton={"tier": "minion", "hp": 20, "ac": 12, "damage_die": "1d6"},
        llm_response={"name": "Goblin Scout", "species": "goblin"},
        entity_index=1,  # not index 0 — index 0 is forced to boss per room
        allocated_id=5000,
        bible=None,
    )
    result = parse_monster(ctx)

    assert result["id"] == 5000
    assert isinstance(result["hp_range"], list) and len(result["hp_range"]) == 2
    assert isinstance(result["ac_range"], list) and len(result["ac_range"]) == 2
    assert result["tier"] == "minion"
    assert result["is_boss"] is False


def test_parse_monster_forces_one_boss_at_index_zero():
    """The first monster per room (entity_index 0) is forced to the boss tier so
    every room has exactly one is_boss monster, with boss-scaled bands."""
    from canon.bible.models import Map
    from canon.packs.dungeon.parsers import parse_monster
    from canon.pipeline.phases.database import BuildContext

    ctx = BuildContext(
        map_id="room_0",
        map_obj=Map(map_id="room_0", name="Room", level=1),
        skeleton={"tier": "minion", "physical_type": "slashing"},
        llm_response={"name": "Dread Warden", "species": "revenant"},
        entity_index=0,
        allocated_id=5000,
        bible=None,
    )
    result = parse_monster(ctx)
    assert result["tier"] == "boss"
    assert result["is_boss"] is True
    # boss bands exceed a level-1 minion's (8-12)
    assert result["hp_range"][1] > 12


def test_monster_skeleton_scales_hp_by_room_level():
    """MONSTER_SPEC derives level-appropriate hp/ac bands from room_level via the
    roll_skeleton context seam — a level-1 minion is in the 8-12 band, NOT the
    old flat 8-80 roll (regression: a level-1 minion previously rolled 60-78 HP).
    """
    import random

    from canon.packs.dungeon.specs import MONSTER_SPEC
    from canon.skeleton.core import roll_skeleton

    lvl1 = roll_skeleton(MONSTER_SPEC, random.Random(0), context={"room_level": 1})
    lvl4 = roll_skeleton(MONSTER_SPEC, random.Random(0), context={"room_level": 4})

    # bands are real pairs derived by tier+level
    assert lvl1["hp_range"][1] <= 30, lvl1["hp_range"]  # level 1 stays small
    assert lvl4["hp_range"][0] >= lvl1["hp_range"][0]   # higher level >= lower
    # physical_type is a real mechanical value, not creature prose
    assert lvl1["physical_type"] in {"slashing", "piercing", "bludgeoning"}


def test_parse_monster_uses_scaled_skeleton_and_sets_level():
    """parse_monster takes mechanics from the level-scaled skeleton and stamps
    the room level onto the template (mazeworld scales from it at combat time)."""
    import random

    from canon.bible.models import Map
    from canon.packs.dungeon.parsers import parse_monster
    from canon.packs.dungeon.specs import MONSTER_SPEC
    from canon.pipeline.phases.database import BuildContext
    from canon.skeleton.core import roll_skeleton

    skel = roll_skeleton(MONSTER_SPEC, random.Random(3), context={"room_level": 1})
    ctx = BuildContext(
        map_id="room_0",
        map_obj=Map(map_id="room_0", name="Room", level=1),
        skeleton=skel,
        llm_response={"name": "Cinderling", "species": "ashling",
                      "physical_type": "undead"},  # LLM prose must NOT win
        entity_index=1,  # not index 0 — index 0 is re-scaled to boss
        allocated_id=5000,
        bible=None,
    )
    result = parse_monster(ctx)
    assert result["level"] == 1
    assert result["hp_range"] == skel["hp_range"]          # skeleton is source of truth
    assert result["physical_type"] in {"slashing", "piercing", "bludgeoning"}
    assert result["physical_type"] != "undead"             # LLM kind rejected


def test_item_skeleton_is_internally_consistent_and_scaled():
    """item_kind drives the mechanics: a rolled weapon carries scaled attack_dice
    + a correct stat (MazeWorld vocab); a consumable carries a scaled restore.
    Regression: items used to come out inert (stamina/health all 0) because the
    skeleton rolled a consumable while the prompt asked for a weapon.
    """
    from canon.packs.dungeon.specs import _WEAPON_TYPE_STAT, _scale_item

    w = _scale_item(
        {"item_kind": "weapon", "weapon_type": "heavy", "weapon_stat": "STR", "rarity": "common"},
        {"room_level": 4},
    )
    assert "d" in w["attack_dice"]                 # real dice expr
    assert _WEAPON_TYPE_STAT["heavy"] == "STR"     # correct stat vocab (not WIS)

    d = _scale_item({"item_kind": "drink", "base_restore": 10, "rarity": "common"}, {"room_level": 4})
    assert d["restore_amount"] == 20               # 10 * 2.0 level mult, not 0


def test_parse_item_weapon_uses_scaled_skeleton_mechanics():
    """parse_item takes weapon mechanics from the (scaled) skeleton, not the LLM,
    and stamps room_level. category equals the rolled item_kind."""
    import random

    from canon.bible.models import Map
    from canon.packs.dungeon.parsers import parse_item
    from canon.packs.dungeon.specs import ITEM_SPEC
    from canon.pipeline.phases.database import BuildContext
    from canon.skeleton.core import roll_skeleton

    # roll until we get a weapon (item_kind is weighted toward weapon)
    skel = None
    for s in range(50):
        cand = roll_skeleton(ITEM_SPEC, random.Random(s), context={"room_level": 3})
        if cand["item_kind"] == "weapon":
            skel = cand
            break
    assert skel is not None
    ctx = BuildContext(
        map_id="room_0",
        map_obj=Map(map_id="room_0", name="Room", level=3),
        skeleton=skel,
        llm_response={"name": "Emberbrand", "desc": "A blade.",
                      "item_stats": {"attack_dice": "9d99"}},  # LLM mechanics must NOT win
        entity_index=0,
        allocated_id=2000,
        bible=None,
    )
    result = parse_item(ctx)
    assert result["category"] == "weapon"
    assert result["room_level"] == 3
    assert result["item_stats"]["attack_dice"] == skel["attack_dice"]  # skeleton wins
    assert result["item_stats"]["attack_dice"] != "9d99"
    assert result["item_stats"]["stat_modifier"] in {"STR", "DEX", "CON", "INT", "WIS", "LUCK"}


def test_parse_event_combat_has_monster_count():
    from canon.packs.dungeon.parsers import parse_event
    from canon.pipeline.phases.database import BuildContext

    ctx = BuildContext(
        map_id="room_0",
        map_obj=None,
        skeleton={"event_type": "combat", "difficulty": "easy", "monster_count": 2},
        llm_response={"name": "Ambush", "description": "Goblins lurk ahead."},
        entity_index=0,
        allocated_id=3000,
        bible=None,
    )
    result = parse_event(ctx)
    assert result["type"] == "combat"
    assert result["monster_count"] == 2


def test_parse_event_difficulty_is_int():
    """Fix 2: parse_event must emit difficulty as an int, never a string."""
    from canon.packs.dungeon.parsers import parse_event
    from canon.pipeline.phases.database import BuildContext

    for diff_label in ("easy", "medium", "hard", "very_hard", "boss"):
        ctx = BuildContext(
            map_id="room_0",
            map_obj=None,
            skeleton={"event_type": "combat", "difficulty": diff_label, "monster_count": 1},
            llm_response={"name": "Test Event"},
            entity_index=0,
            allocated_id=3000,
            bible=None,
        )
        result = parse_event(ctx)
        assert isinstance(result["difficulty"], int), (
            f"difficulty for label {diff_label!r} should be int, got {type(result['difficulty'])}"
        )


def test_parse_event_combat_has_monster_ids():
    """Fix 4: combat events include monster_ids list."""
    from canon.packs.dungeon.parsers import parse_event
    from canon.pipeline.phases.database import BuildContext

    ctx = BuildContext(
        map_id="room_0",
        map_obj=None,
        skeleton={"event_type": "combat", "difficulty": "easy", "monster_count": 1},
        llm_response={"name": "Ambush"},
        entity_index=0,
        allocated_id=3000,
        bible=None,
    )
    result = parse_event(ctx)
    assert "monster_ids" in result, "combat event missing 'monster_ids'"
    assert isinstance(result["monster_ids"], list)


def test_parse_event_puzzle_has_choices():
    from canon.packs.dungeon.parsers import parse_event
    from canon.pipeline.phases.database import BuildContext

    ctx = BuildContext(
        map_id="room_0",
        map_obj=None,
        skeleton={"event_type": "puzzle", "difficulty": "medium", "monster_count": 0},
        llm_response={
            "name": "Ancient Lock",
            "description": "A sealed door with runes.",
            "choices": [
                {"text": "Force it", "stat_check": "STR", "dc": 15, "auto_success": False,
                 "success_text": "You break it open."},
            ],
        },
        entity_index=0,
        allocated_id=3001,
        bible=None,
    )
    result = parse_event(ctx)
    assert result["type"] == "puzzle"
    assert "choices" in result
    # New solvable-puzzle model: at least a stat choice + a guaranteed walk-away
    # auto-success (no map/bible here, so no tool/ability/spell solve paths).
    assert len(result["choices"]) >= 2
    assert any(c.get("auto_success") for c in result["choices"]), "needs a walk-away"
    assert "monster_count" not in result


def test_parse_quest_returns_required_fields():
    from canon.packs.dungeon.parsers import parse_quest
    from canon.pipeline.phases.database import BuildContext

    ctx = BuildContext(
        map_id="room_0",
        map_obj=None,
        skeleton={"quest_type": "fetch", "reward_tier": 2},
        llm_response={
            "title": "The Lost Relic",
            "description": "Recover a stolen artifact.",
            "success_dialogue": "You found it!",
            "failure_dialogue": "You failed me.",
        },
        entity_index=0,
        allocated_id=4000,
        bible=None,
    )
    result = parse_quest(ctx)
    assert result["id"] == 4000
    assert result["type"] == "fetch"
    assert "reward" in result
    assert result["reward"]["xp"] == 100  # tier 2
    assert "failure_penalty" in result
