"""MazeworldPromptSet — mazeworld-flavored prompt overrides.

Extends DefaultPromptSet with the extension pattern for downstream packs.
For v0.2 all defaults are good enough — this class is a marker showing
how a consumer subclasses without re-implementing the full PromptSet ABC.

Future tweaks:
- Override ``npc_generation`` to enforce fantasy-adjacent phrasing.
- Override ``monster_generation`` to bias toward dungeon-crawl creature
  tropes (undead, beasts, constructs, aberrations).
- Override ``event_generation`` to prefer traps + ambushes over abstract
  puzzles.
"""

from canon.llm.prompts import DefaultPromptSet


class MazeworldPromptSet(DefaultPromptSet):
    """Mazeworld-flavored prompt overrides extending DefaultPromptSet.

    All DefaultPromptSet implementations are inherited unchanged for v0.2.
    Subclass this in your own pack to add domain-specific prompt framing
    without reimplementing the full PromptSet ABC.
    """
    # All defaults inherited.  Add overrides here as needed.
    pass
