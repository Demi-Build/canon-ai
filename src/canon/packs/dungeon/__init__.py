"""Dungeon pack (``pack_type`` ``dungeon``) — canon consumer example.

Demonstrates how a downstream game adopts canon by composing DatabaseSpecs,
parsers, prompt overrides, and a compose_pipeline() helper.

The module is ``dungeon`` to match the registry id (renamed from the
``mazeworld`` package path 2026-09-01; only that path moved). The
MazeWorld-named classes and tests (``MazeworldManifestPhase``,
``MazeworldPromptSet``, ``tests/test_mazeworld_*.py``) describe the
engine's data shapes and rename with the W2.0 pull-in.

Usage::

    from canon.packs.dungeon import compose_pipeline
    phases, ctx = compose_pipeline(seed="shadowspire", num_maps=3)
    # Wire ctx.llm and optionally ctx.image_backend, then:
    # run_pipeline(phases, ctx)
"""

from canon.packs.dungeon.compose import compose_mazeworld_specs, compose_pipeline
from canon.packs.dungeon.dialogue import MazeworldDialoguePhase
from canon.packs.dungeon.parsers import (
    parse_event,
    parse_item,
    parse_monster,
    parse_npc,
    parse_quest,
)
from canon.packs.dungeon.phases import MazeworldManifestPhase
from canon.packs.dungeon.placement import MazeworldPlacementPhase
from canon.packs.dungeon.prompts import MazeworldPromptSet
from canon.packs.dungeon.specs import (
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
    # Phases
    "MazeworldManifestPhase",
    "MazeworldPlacementPhase",
    "MazeworldDialoguePhase",
]
