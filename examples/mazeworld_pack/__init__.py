"""Mazeworld pack — canon consumer example.

Demonstrates how a downstream game adopts canon by composing DatabaseSpecs,
parsers, prompt overrides, and a compose_pipeline() helper.

Usage::

    from examples.mazeworld_pack import compose_pipeline
    phases, ctx = compose_pipeline(seed="shadowspire", num_maps=3)
    # Wire ctx.llm and optionally ctx.image_backend, then:
    # run_pipeline(phases, ctx)
"""

from examples.mazeworld_pack.compose import compose_mazeworld_specs, compose_pipeline
from examples.mazeworld_pack.parsers import (
    parse_event,
    parse_item,
    parse_monster,
    parse_npc,
    parse_quest,
)
from examples.mazeworld_pack.prompts import MazeworldPromptSet
from examples.mazeworld_pack.specs import (
    ABILITY_SPEC,
    EVENT_SPEC,
    HEALER_ARCHETYPE,
    ITEM_SPEC,
    JESTER_ARCHETYPE,
    MAGE_ARCHETYPE,
    MONSTER_SPEC,
    NPC_SPEC,
    QUEST_SPEC,
    SPELL_SPEC,
    WARRIOR_ARCHETYPE,
    WEAPON_SPEC,
)

__all__ = [
    # Compose
    "compose_pipeline",
    "compose_mazeworld_specs",
    # Specs
    "WEAPON_SPEC",
    "SPELL_SPEC",
    "MONSTER_SPEC",
    "ITEM_SPEC",
    "NPC_SPEC",
    "EVENT_SPEC",
    "QUEST_SPEC",
    "ABILITY_SPEC",
    "WARRIOR_ARCHETYPE",
    "MAGE_ARCHETYPE",
    "HEALER_ARCHETYPE",
    "JESTER_ARCHETYPE",
    # Parsers
    "parse_npc",
    "parse_item",
    "parse_monster",
    "parse_event",
    "parse_quest",
    # Prompts
    "MazeworldPromptSet",
]
