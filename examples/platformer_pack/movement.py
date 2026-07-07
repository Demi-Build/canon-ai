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
    gravity: float = 40.0  # cells / second^2

    # In-volume modifiers (water/lava/mud) moved to the tile registry in
    # 3b — they are PER-VOLUME data (tile params on tileset slots), read
    # from there by every play surface. This spec stays the single source
    # for dry-land physics and the jump rule the validator enforces.


DEFAULT_MOVEMENT = PlayerMovementSpec()

#: Headroom the play surfaces add to the analytic apex (discrete
#: integration undershoots it) — keep in sync with the jump_v formula in
#: examples/platformer_play.py and godot_template/godot/main.gd.
JUMP_HEADROOM = 0.4

#: Validators demand less than the theoretical maximum — cell
#: quantization and human imprecision eat the edges of the envelope.
COMFORT = 0.85


def max_dx_for_rise(movement: PlayerMovementSpec, rise: int) -> int:
    """Max horizontal cells clearable while RISING ``rise`` cells, from
    the same ballistic model the play surfaces integrate.

    The old box rule (dx <= jump_width AND rise <= jump_height,
    independently) approved platforms real players can't reach: a
    full-height jump spends nearly its whole flight rising, leaving
    almost no horizontal range ("platforms are too high" — round-4 play
    test). Rising costs range; this is that arithmetic.
    """
    if rise <= 0:
        return movement.jump_width  # walking off / dropping: box rule
    if rise > movement.jump_height:
        return -1
    g = movement.gravity
    v0 = (2.0 * g * (movement.jump_height + JUMP_HEADROOM)) ** 0.5
    disc = v0 * v0 - 2.0 * g * rise
    if disc <= 0:  # pragma: no cover — rise <= jump_height keeps disc > 0
        return 0
    # Time until the arc comes back DOWN to the landing height.
    t_land = (v0 + disc**0.5) / g
    return min(
        movement.jump_width, int(movement.run_speed * t_land * COMFORT)
    )
