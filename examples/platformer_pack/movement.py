"""PlayerMovementSpec (PRD §4.2) — the shared physics vocabulary.

One source of truth consumed by BOTH the reachability validator and the
pygame review harness, so "the validator says reachable" and "I can make
that jump" always agree. Values are in grid cells (and cells/second for
speeds); the harness converts to pixels via its render scale.
"""

from __future__ import annotations

from pydantic import BaseModel


class PlayerMovementSpec(BaseModel):
    run_speed: float = 8.0  # cells / second
    jump_height: int = 3  # max rise, in cells
    jump_width: int = 4  # max horizontal clearance, in cells
    gravity: float = 40.0  # cells / second^2 (harness feel; validator ignores)

    # Water modifiers (PRD Appendix E.1). One spec feeds BOTH the
    # reachability validator and every play surface — they must agree.
    water_speed_factor: float = 0.55  # horizontal speed multiplier in water
    water_gravity: float = 8.0  # slow sink, cells / second^2
    swim_impulse: float = 5.0  # upward velocity per swim stroke, cells / s


DEFAULT_MOVEMENT = PlayerMovementSpec()
