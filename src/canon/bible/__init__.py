"""Canon Bible — the single source of truth for a generated world.

Re-exports the public model surface so callers can write::

    from canon.bible import Bible, Map, Character

The context-assembly helpers (`get_context`, `get_cumulative_context`)
currently return empty stubs; the real implementation lives in
`canon.bible.context` and is built by a parallel agent.
"""

from canon.bible.models import (
    Bible,
    BibleMetadata,
    Character,
    CharacterClass,
    CharacterRole,
    ClassArchetype,
    EntityLore,
    Faction,
    GenerationTrail,
    Map,
    StoryArc,
    StoryBeat,
    Zone,
)

__all__ = [
    "Bible",
    "BibleMetadata",
    "Character",
    "CharacterClass",
    "CharacterRole",
    "ClassArchetype",
    "EntityLore",
    "Faction",
    "GenerationTrail",
    "Map",
    "StoryArc",
    "StoryBeat",
    "Zone",
]
