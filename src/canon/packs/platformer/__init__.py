"""Platformer vertical-slice pack (Phase 3-lite).

Generates a small platformer world end-to-end and renders it reviewable:
color-coded placeholder tileset + per-level PNGs. The pygame harness
(``canon.packs.platformer.play`` — ``python -m`` it; imported lazily, never
by this package) is a throwaway review surface; the real target is Godot
(Phase 4).
"""

from canon.packs.platformer.compose import compose_pipeline
from canon.packs.platformer.movement import DEFAULT_MOVEMENT, PlayerMovementSpec
from canon.packs.platformer.prompts import PlatformerPrompts

__all__ = [
    "compose_pipeline",
    "PlatformerPrompts",
    "PlayerMovementSpec",
    "DEFAULT_MOVEMENT",
]
