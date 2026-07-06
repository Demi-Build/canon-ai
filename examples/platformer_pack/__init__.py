"""Platformer vertical-slice pack (Phase 3-lite).

Generates a small platformer world end-to-end and renders it reviewable:
color-coded placeholder tileset + per-level PNGs. The pygame harness
(examples/platformer_play.py) is a throwaway review surface; the real
target is Godot (Phase 4).
"""

from examples.platformer_pack.compose import compose_pipeline
from examples.platformer_pack.movement import DEFAULT_MOVEMENT, PlayerMovementSpec
from examples.platformer_pack.prompts import PlatformerPrompts

__all__ = [
    "compose_pipeline",
    "PlatformerPrompts",
    "PlayerMovementSpec",
    "DEFAULT_MOVEMENT",
]
