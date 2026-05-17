"""Declarative skeleton system — pre-roll mechanical fields for any entity.

Public API:

* :class:`SkeletonField` — declarative spec for a single rolled field.
* :class:`SkeletonSpec` — collection of fields plus an optional ``post``
  callback for derivations the declarative form can't yet express.
* :func:`roll_skeleton` — deterministic pre-roll given a seeded RNG.

See :mod:`canon.skeleton.core` for the full design rationale, including
``# TODO(v0.2)`` markers for the richer DSL planned in canon v0.2
(probability gates, conditional choices, computed fields, context-aware
lookups).
"""

from canon.skeleton.core import SkeletonField, SkeletonSpec, roll_skeleton

__all__ = [
    "SkeletonField",
    "SkeletonSpec",
    "roll_skeleton",
]
