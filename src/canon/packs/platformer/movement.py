"""PlayerMovementSpec (PRD §4.2) — the shared physics vocabulary.

One source of truth consumed by BOTH the reachability validator and the
pygame review harness, so "the validator says reachable" and "I can make
that jump" always agree. Values are in grid cells (and cells/second for
speeds); the harness converts to pixels via its render scale.
"""

from __future__ import annotations

from pydantic import BaseModel


class PlayerMovementSpec(BaseModel):
    run_speed: float = 8.0  # cells / second — TOP speed, reached holding RUN
    jump_height: int = 3  # max rise, in cells
    jump_width: int = 4  # max horizontal clearance (feedback/prompt vocab)
    gravity: float = 40.0  # cells / second^2

    # Horizontal ACCELERATION + MOMENTUM (run-up mechanic). The player has a
    # horizontal velocity vx that accelerates toward a target speed and
    # coasts (a slide) via friction. Holding RUN raises the target from
    # walk_speed to run_speed; a jump PRESERVES vx (a running jump flies
    # farther), and air control is weaker than ground control so speed must
    # be built on the GROUND. Tuned so ~run_up_cells of runway reaches
    # run_speed; a standing/walk jump clears ~jump_width, a full running jump
    # ~7. All of it is data — retune per game.
    walk_speed: float = 5.0  # cells / second — target with RUN not held
    ground_accel: float = 10.0  # cells/s^2 building toward target on the ground
    ground_friction: float = 40.0  # cells/s^2 decel to 0 when idle on ground
    # Separate DECEL when the held direction OPPOSES vx — a crisp turnaround
    # instead of the mushy single-accel skid (build-up stays gentle, braking
    # is decisive). The air-brake is 0.3x this (the only thing that sheds air
    # speed). Old manifests without the field fall back to ground_accel.
    brake_accel: float = 17.0  # cells/s^2 when input opposes momentum
    air_accel: float = 6.0  # cells/s^2 air control (build-up continues in the air)
    air_friction: float = 0.0  # in-air horizontal damping (0 = pure momentum)
    run_up_cells: float = 3.2  # runway to reach run_speed from rest (feel/doc)

    # Coyote time: a jump-forgiveness window (seconds) after running off a
    # ledge during which the jump input still fires. Play-feel ONLY — the
    # consumers become MORE forgiving than the reachability sim models,
    # never less, so validation is untouched. Both surfaces arm the latch
    # off the same deterministic foot probe (never the on_ground flag,
    # whose sub-cell flicker diverges between them). 0 disables.
    coyote_s: float = 0.08

    # In-volume modifiers (water/lava/mud) moved to the tile registry in
    # 3b — they are PER-VOLUME data (tile params on tileset slots), read
    # from there by every play surface. This spec stays the single source
    # for dry-land physics and the jump rule the validator enforces.


DEFAULT_MOVEMENT = PlayerMovementSpec()


def runway_speed(movement: PlayerMovementSpec, runway_cells: float) -> float:
    """Top horizontal speed the player can build over ``runway_cells`` of
    flat run-up from rest (holding RUN), from the ground-accel model:
    ``v = sqrt(2·a·d)`` capped at ``run_speed``. The reachability simulation
    uses this to gate a jump's takeoff speed on the RUNWAY behind it — a wide
    gap right after a wall (no runway) only gets a walk-speed jump."""
    if runway_cells <= 0:
        return 0.0
    return min(movement.run_speed, (2.0 * movement.ground_accel * runway_cells) ** 0.5)

#: Headroom the play surfaces add to the analytic apex (discrete
#: integration undershoots it) — keep in sync with the jump_v formula in
#: play.py and godot_template/godot/main.gd.
JUMP_HEADROOM = 0.4

#: Validators demand less than the theoretical maximum — cell
#: quantization and human imprecision eat the edges of the envelope.
COMFORT = 0.85

#: Comfort margin for the reachability SIMULATION (validate.py) ONLY — never
#: the play surfaces. The validator integrates the jump arc at
#: ``REACH_COMFORT * run_speed``, so it promises only jumps the player can
#: make WITHOUT a frame-perfect, full-speed approach. 1.0 = the raw consumer
#: physics (a max jump spans ~7 cells); dial DOWN for a cushion — e.g. 0.85
#: (~the classic feel) shrinks the widest approved flat gap and asks
#: ``auto_bridge`` for a stepping stone sooner. A per-game knob, sibling to
#: ``COMFORT``. (Once run-up momentum lands, reach is gated by RUNWAY instead
#: and this becomes a secondary cushion.)
REACH_COMFORT = 1.0


def jump_speed(movement: PlayerMovementSpec) -> float:
    """Initial upward speed the play surfaces launch a jump at — enough to
    clear ``jump_height`` cells PLUS ``JUMP_HEADROOM`` (discrete integration
    undershoots the analytic apex). This is the SINGLE source for the
    ``sqrt(2·g·(jump_height + 0.4))`` formula both consumers compute inline
    (play.py, godot_template/godot/main.gd) — the
    reachability simulation in validate.py reuses it so "the validator says
    reachable" and "I can make that jump" stay in lock-step."""
    return (2.0 * movement.gravity * (movement.jump_height + JUMP_HEADROOM)) ** 0.5


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
    v0 = jump_speed(movement)
    disc = v0 * v0 - 2.0 * g * rise
    if disc <= 0:  # pragma: no cover — rise <= jump_height keeps disc > 0
        return 0
    # Time until the arc comes back DOWN to the landing height.
    t_land = (v0 + disc**0.5) / g
    return min(
        movement.jump_width, int(movement.run_speed * t_land * COMFORT)
    )


def run_jump_width(movement: PlayerMovementSpec) -> int:
    """Columns a FULL running jump clears on the flat, from the same
    ballistic model the reachability simulation integrates: airtime
    ``t = 2·v0/g`` (up and back down to the takeoff row) at ``run_speed``,
    shaved by ``COMFORT`` like every other promised number, then rounded
    to the nearest column. This is the prompt vocabulary for the wide
    run-and-jump gap — it replaces the hardcoded '~6' literal, so a
    per-level movement override (e.g. a low-gravity finale) reshapes the
    advertised envelope instead of lying about it. Defaults land on the
    familiar 6."""
    t_land = 2.0 * jump_speed(movement) / movement.gravity
    return round(movement.run_speed * t_land * COMFORT)
