"""ID allocator for sequential per-entity-type integer IDs.

Mazeworld config: bases={"npc": 1000, "item": 2000, "event": 3000,
"quest": 4000, "monster": 5000, "class": 6000}.
Future games override via their own bases dict.
"""

from __future__ import annotations


class IDAllocator:
    """Allocates sequential IDs per entity type with type-specific bases.

    Each call to ``next(entity_type)`` returns ``base + count_so_far`` and
    increments the internal counter for that type.  Different entity types
    are fully independent.

    Mazeworld config: bases={"npc": 1000, "item": 2000, "event": 3000,
    "quest": 4000, "monster": 5000, "class": 6000}.
    Future games override.
    """

    def __init__(self, bases: dict[str, int]) -> None:
        self._bases: dict[str, int] = dict(bases)
        self._counters: dict[str, int] = {k: 0 for k in bases}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def next(self, entity_type: str) -> int:
        """Return ``base + count_so_far`` and increment the counter.

        Raises ``KeyError`` with a descriptive message if *entity_type* was
        not registered in the ``bases`` dict passed to the constructor.
        """
        if entity_type not in self._bases:
            registered = sorted(self._bases.keys())
            raise KeyError(
                f"Unknown entity type {entity_type!r}. "
                f"Registered types: {registered}. "
                "Pass a 'bases' dict that includes this type."
            )
        value = self._bases[entity_type] + self._counters[entity_type]
        self._counters[entity_type] += 1
        return value

    def peek(self, entity_type: str) -> int:
        """Return the value that the next ``next()`` call would return, without advancing.

        Raises ``KeyError`` with a descriptive message for unknown types.
        """
        if entity_type not in self._bases:
            registered = sorted(self._bases.keys())
            raise KeyError(
                f"Unknown entity type {entity_type!r}. "
                f"Registered types: {registered}. "
                "Pass a 'bases' dict that includes this type."
            )
        return self._bases[entity_type] + self._counters[entity_type]

    def reset(self, entity_type: str) -> None:
        """Reset the counter for *entity_type* to zero (next call returns base).

        Primarily a test helper.  Raises ``KeyError`` for unknown types.
        """
        if entity_type not in self._bases:
            registered = sorted(self._bases.keys())
            raise KeyError(
                f"Unknown entity type {entity_type!r}. "
                f"Registered types: {registered}."
            )
        self._counters[entity_type] = 0

    def reset_all(self) -> None:
        """Reset all counters.  Primarily a test helper."""
        for key in self._counters:
            self._counters[key] = 0
