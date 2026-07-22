"""Slice validators (Validator nodes, PRD §5.0): pure functions over the
stamped grid. Kick back to the agent on fail — callers turn the returned
problem strings into retry feedback.

Since 3b every tile judgment resolves through the game's TILE REGISTRY
(categories in code, values in data): solids/one-ways support standing,
volume tiles (water, lava, mud) are swimmable, hazards are neither.

Reachability is a BFS whose successor relation is a REAL jump-arc
SIMULATION: from each foothold we integrate the player's exact physics
(the same jump velocity, gravity, run speed, dt, and ceiling/wall/land
collision the two play surfaces run — examples/platformer_play.py and its
byte-identical twin godot_template/godot/main.gd) and record every cell the
player can actually come to rest in. This replaced the old dx/rise +
"scan the columns between the footholds" heuristic, which silently passed
UNBEATABLE levels: it never checked the takeoff column, so a jump straight
UP through a capping platform, or a mount onto a slab from a sealed pocket
below, read as reachable. Simulating the arc catches takeoff blocks,
up-through-platforms, mount-from-below, and flattened arcs that fall short.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from canon.bible.platformer import SparseMaskEntry
from examples.platformer_pack.combat import (
    DEFAULT_COMBAT,
    CombatSpec,
    effective_size,
    occupancy,
)
from examples.platformer_pack.movement import (
    REACH_COMFORT,
    PlayerMovementSpec,
    jump_speed,
    max_dx_for_rise,
    runway_speed,
)
from examples.platformer_pack.rules import DEFAULT_RULES, GameRules
from examples.platformer_pack.tiles import DEFAULT_TILES, TileRegistry
from examples.platformer_pack.variants import DEFAULT_VARIANTS, VariantSet


def standable_cells(grid, tiles: TileRegistry = DEFAULT_TILES) -> set[tuple[int, int]]:
    """Cells a body can stand in: empty with solid/one-way support below.

    A ``breakable`` tile is ``solid`` category, so it counts as support here —
    reachability treats a crumbling floor as a PERMANENT foothold. v1 does NOT
    model the break fuse (a consumer-only mechanic), so this is deliberately
    conservative: a level is never rejected for footing that can break, even
    though a slow player could fall through it."""
    height, width = grid.shape
    empty = tiles.empty_id
    support = tiles.ids("solid", "one_way")
    out: set[tuple[int, int]] = set()
    for y in range(height - 1):
        for x in range(width):
            if int(grid[y, x]) == empty and int(grid[y + 1, x]) in support:
                out.add((x, y))
    return out


def seabed_cells(
    grid, tiles: TileRegistry = DEFAULT_TILES
) -> set[tuple[int, int]]:
    """Wet-standable cells (water arc): a VOLUME cell with solid/one-way
    support directly below — the underwater flats where a WADING land
    enemy may be posted under the ``"seabed"`` enemy_water_policy (and
    the cells the placement prompt lists as submerged ground)."""
    height, width = grid.shape
    volumes = tiles.ids("volume")
    support = tiles.ids("solid", "one_way")
    return {
        (x, y)
        for y in range(height - 1)
        for x in range(width)
        if int(grid[y, x]) in volumes and int(grid[y + 1, x]) in support
    }


def hazard_stand_cells(
    grid, tiles: TileRegistry = DEFAULT_TILES
) -> set[tuple[int, int]]:
    """Hazard cells with solid/one-way support directly below — where a
    HAZARD-IMMUNE variant (combat arc) may be posted: standing ON the
    spikes. The seabed_cells mirror for the hazard band."""
    height, width = grid.shape
    hazards = tiles.ids("hazard")
    support = tiles.ids("solid", "one_way")
    return {
        (x, y)
        for y in range(height - 1)
        for x in range(width)
        if int(grid[y, x]) in hazards and int(grid[y + 1, x]) in support
    }


def volume_cells(
    grid, tiles: TileRegistry = DEFAULT_TILES
) -> set[tuple[int, int]]:
    """Cells inside any volume tile (water, lava, mud — all swimmable)."""
    height, width = grid.shape
    volumes = tiles.ids("volume")
    return {
        (x, y)
        for y in range(height)
        for x in range(width)
        if int(grid[y, x]) in volumes
    }


# --------------------------------------------------------------------------
# Jump-arc SIMULATION — the reachability successor relation. These constants
# and the integration loop below MUST stay identical to the two play surfaces
# (examples/platformer_play.py's update loop and godot_template/godot/main.gd's
# _physics_process, which are byte-identical to each other). The ballistic
# apex margin lives in movement.jump_speed / movement.JUMP_HEADROOM.
# --------------------------------------------------------------------------

#: Player body span in cells — the two corners collision samples, matching
#: BODY_L/BODY_R in both consumers.
BODY_L, BODY_R = 0.15, 0.85

#: The consumers integrate at a fixed 60 FPS; the simulation matches it.
_SIM_DT = 1.0 / 60.0

#: Frame cap per plan — a full jump (~0.8 s) plus a fall from the top of any
#: real level to the kill plane fits easily; a plan that never resolves is
#: treated as going nowhere.
_MAX_SIM_FRAMES = 400

#: Cells a walk plan tracks before yielding to the BFS. Walking is
#: transitive, so a short hop keeps each simulation cheap and the BFS chains
#: the rest.
_WALK_REACH = 8

#: Max runway cells the probe counts behind a takeoff — beyond the run-up
#: needed to reach run_speed, extra cells don't add takeoff velocity.
_RUNWAY_MAX = 8


def _drop_targets(
    grid, stand: set, volume: set, tiles: TileRegistry
) -> list[list[int | None]]:
    """Per column, the foothold a straight-down drop from each row lands on
    (or ``None``). Used only for the CONTROLLED step-off (walk to a ledge edge
    and drop nearly straight down — a tap is always makeable); mid-air drops
    are NOT sound under run-up momentum (you can't stop horizontally in the
    air). Solids, one-ways, and water all stop a fall; a fall into water has
    no dry landing (the swim edges cover the water)."""
    height, width = grid.shape
    solids = tiles.ids("solid")
    one_ways = tiles.ids("one_way")
    cols: list[list[int | None]] = [[None] * height for _ in range(width)]
    for x in range(width):
        target: int | None = None
        for y in range(height - 1, -1, -1):
            if (x, y) in volume:
                target = None  # water: an incoming fall has no dry landing
            elif int(grid[y, x]) in solids or int(grid[y, x]) in one_ways:
                target = None  # solid/one-way floor blocks passage below
            elif (x, y) in stand:
                target = y  # empty foothold: a fall from here/above lands
            cols[x][y] = target
    return cols


def _runway(stand: set, tx: int, ty: int, direction: int, max_cells: int) -> int:
    """Flat run-up available for a jump in ``direction`` from ``(tx, ty)``:
    the count of contiguous standable cells at row ``ty`` on the APPROACH side
    (behind the takeoff), capped at ``max_cells``. A wide gap right after a
    wall gets 0 (only a standing/walk jump); a gap after open ground gets a
    full runway (the run-and-jump)."""
    n = 0
    x = tx - direction
    while n < max_cells and (x, ty) in stand:
        n += 1
        x -= direction
    return n


#: Takeoff-speed sampling step (cells/s). A jump's landing column is ~ vx ×
#: airtime, so ~1 cell/s keeps consecutive landings under a cell apart — dense
#: enough that no makeable jump falls between samples (which would over-bridge).
_TAKEOFF_STEP = 1.0


def _takeoff_speeds(max_vx: float) -> list[float]:
    """Horizontal takeoff speeds to sample for a jump, from a standstill up to
    ``max_vx`` (the top speed the RUNWAY allows). Sampling the whole range —
    densely — catches intermediate-distance landings a full-speed jump would
    overshoot, so the successor neither over- nor under-bridges. With no runway
    ``max_vx`` is ~0, so only the standing jump is offered."""
    if max_vx <= 0.5:
        return [0.0]
    n = int(max_vx / _TAKEOFF_STEP)
    speeds = [i * _TAKEOFF_STEP for i in range(n + 1)]
    if not speeds or speeds[-1] < max_vx - 1e-6:
        speeds.append(max_vx)
    return speeds


def _simulate_plan(
    grid,
    src: tuple[int, int],
    vx0: float,
    hold: int,
    jump: bool,
    is_walk: bool,
    out: set,
    *,
    jv: float,
    gravity: float,
    walk_speed: float,
    run_speed: float,
    ground_accel: float,
    ground_friction: float,
    air_accel: float,
    air_friction: float,
    solids: frozenset | set,
    one_ways: frozenset | set,
    volume: set,
    stand: set,
    width: int,
    height: int,
    extra_solid: frozenset,
    extra_one_way: frozenset,
    extra_stand: frozenset,
) -> None:
    """Integrate ONE plan from ``src`` with the consumers' RUN-UP MOMENTUM
    physics and add every foothold the player lands on (plus volume cells
    entered) to ``out``. ``vx0`` is the horizontal velocity at the start (the
    run-up speed already built for a jump); vx then accelerates toward
    ``hold * run_speed`` — weakly in the air (``air_accel``), strongly on the
    ground (``ground_accel``) — with friction when idle. No straight-down drop
    shortcut: momentum means you can't stop in mid-air, so only real arc
    landings count. ``extra_solid`` / ``extra_one_way`` / ``extra_stand``
    inject a proposed bridge (a ONE-WAY: lands from above, never blocks from
    below) so the same code answers "would this bridge be reachable"."""

    def _solid_at(ix: int, iy: int) -> bool:
        if (ix, iy) in extra_solid:
            return True
        if 0 <= ix < width and 0 <= iy < height:
            return int(grid[iy, ix]) in solids
        return False

    def _support_below(x: float, y: float) -> bool:
        # Deterministic ground probe (matches the consumers' `gmove`): support
        # directly under the feet — robust to the sub-cell landing flicker.
        foot = int(y + 1.01)
        for cx in (x + BODY_L, x + BODY_R):
            ix = int(cx)
            if (ix, foot) in extra_one_way:
                return True
            if 0 <= ix < width and 0 <= foot < height and (
                int(grid[foot, ix]) in solids or int(grid[foot, ix]) in one_ways
            ):
                return True
        return False

    def _blocked(x: float, y: float) -> bool:
        for cy in (y, y + 0.99):
            iy = int(cy)
            if _solid_at(int(x + BODY_L), iy) or _solid_at(int(x + BODY_R), iy):
                return True
        return False

    def _landing(x: float, y: float, prev_bottom: float) -> bool:
        iy = int(y)
        floor_row = float(iy)
        for cx in (x + BODY_L, x + BODY_R):
            ix = int(cx)
            if _solid_at(ix, iy):
                return True
            on_one_way = (ix, iy) in extra_one_way or (
                0 <= ix < width
                and 0 <= iy < height
                and int(grid[iy, ix]) in one_ways
            )
            if on_one_way and prev_bottom <= floor_row:
                return True
        return False

    px, py = float(src[0]), float(src[1])
    vx = float(vx0)
    vy = -jv if jump else 0.0
    airborne_seen = jump
    sx = src[0]
    for _ in range(_MAX_SIM_FRAMES):
        prev_px = px
        gmove = vy >= -0.01 and _support_below(px, py)
        accel = ground_accel if gmove else air_accel
        if hold != 0:
            target = hold * run_speed  # RUN held = max reach (is-it-possible)
            step = accel * _SIM_DT
            vx = min(vx + step, target) if vx < target else max(vx - step, target)
        else:
            fric = (ground_friction if gmove else air_friction) * _SIM_DT
            vx = max(0.0, vx - fric) if vx > 0 else min(0.0, vx + fric)
        new_x = px + vx * _SIM_DT
        if _blocked(new_x, py):
            vx = 0.0
        else:
            px = min(max(new_x, 0.0), width - 1.0)
        vy += gravity * _SIM_DT
        prev_bottom = py + 0.99
        new_y = py + vy * _SIM_DT
        if vy > 0 and _landing(px, new_y + 0.99, prev_bottom):
            py = float(int(new_y + 0.99) - 1)
            vy = 0.0
            grounded = True
        elif vy < 0 and _blocked(px, new_y):
            vy = 0.0
            grounded = False
        else:
            py = new_y
            grounded = False
        iy = int(py)
        for cx in (px + BODY_L, px + BODY_R):
            ix = int(cx)
            cell = (ix, iy)
            if cell in volume:
                out.add(cell)
            if grounded and (cell in stand or cell in extra_stand):
                out.add(cell)
        if not grounded:
            airborne_seen = True
        if py > height + 2:
            return  # fell off the bottom
        if grounded and airborne_seen:
            return  # a jump / walk-off resolves once it lands
        if is_walk and grounded:
            if abs(px - prev_px) < 1e-9:
                return  # walked into a wall / clamped at the edge
            if abs(px - sx) >= _WALK_REACH:
                return  # far enough; the BFS carries on from these cells


def _successors(
    grid,
    cell: tuple[int, int],
    movement: PlayerMovementSpec,
    tiles: TileRegistry = DEFAULT_TILES,
    *,
    stand: set | None = None,
    volume: set | None = None,
    drop: list | None = None,
    extra_solid: frozenset = frozenset(),
    extra_one_way: frozenset = frozenset(),
    extra_stand: frozenset = frozenset(),
) -> set:
    """Cells reachable from ``cell`` in one move by simulating the player's
    run-up momentum physics: per direction, a jump at several takeoff speeds
    gated by the RUNWAY behind the takeoff (a wide gap needs a run-up); walk
    connectivity; a controlled step-off drop; and free swimming in a volume.
    The successor relation the reachability BFS and the bridge tool share, so
    "reachable" and "bridgeable" stay consistent."""
    height, width = grid.shape
    if stand is None:
        stand = standable_cells(grid, tiles)
    if volume is None:
        volume = volume_cells(grid, tiles)
    if drop is None:
        drop = _drop_targets(grid, stand, volume, tiles)
    solids = tiles.ids("solid")
    one_ways = tiles.ids("one_way")
    comfort = REACH_COMFORT
    walk_vx = movement.walk_speed * comfort
    run_cap = movement.run_speed * comfort
    kw = dict(
        jv=jump_speed(movement), gravity=movement.gravity,
        walk_speed=movement.walk_speed * comfort,
        run_speed=movement.run_speed * comfort,
        ground_accel=movement.ground_accel, ground_friction=movement.ground_friction,
        air_accel=movement.air_accel, air_friction=movement.air_friction,
        solids=solids, one_ways=one_ways, volume=volume, stand=stand,
        width=width, height=height, extra_solid=extra_solid,
        extra_one_way=extra_one_way, extra_stand=extra_stand,
    )
    out: set = set()
    cx, cy = cell
    if cell in volume:
        # Free swim: vertical movement is free inside a volume; the jump rule
        # applies only when leaving it.
        for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
            if (nx, ny) in volume:
                out.add((nx, ny))
        # Surface exit: open air directly above turns a stroke into a full
        # jump (no runway underwater — walk/run takeoff, both directions).
        air_above = (
            cy - 1 >= 0
            and (cx, cy - 1) not in volume
            and int(grid[cy - 1, cx]) not in solids
        )
        if air_above:
            # No runway underwater: a surface exit is a low-speed jump.
            for hold in (-1, 0, 1):
                for vx0 in _takeoff_speeds(walk_vx):
                    _simulate_plan(
                        grid, cell, hold * vx0, hold, True, False, out, **kw
                    )
    else:
        for hold in (-1, 1):
            runway = _runway(stand, cx, cy, hold, _RUNWAY_MAX)
            run_vx = min(run_cap, runway_speed(movement, runway) * comfort)
            for vx0 in _takeoff_speeds(run_vx):
                _simulate_plan(
                    grid, cell, hold * vx0, hold, True, False, out, **kw
                )
        # Straight-up jump (vertical), no horizontal.
        _simulate_plan(grid, cell, 0.0, 0, True, False, out, **kw)
        # Walk connectivity (accelerates on the ground, falls off ledges).
        for hold in (-1, 1):
            _simulate_plan(grid, cell, 0.0, hold, False, True, out, **kw)
        # Controlled step-off: tap to an adjacent open column and drop nearly
        # straight down (a slow step is always makeable, unlike a mid-air stop).
        for nx in (cx - 1, cx + 1):
            if 0 <= nx < width and (nx, cy) not in stand and int(grid[cy, nx]) == 0:
                r = drop[nx][cy]
                if r is not None:
                    out.add((nx, r))
    out.discard(cell)
    return out


def can_reach(
    grid,
    src: tuple[int, int],
    dst: tuple[int, int],
    movement: PlayerMovementSpec,
    tiles: TileRegistry = DEFAULT_TILES,
) -> bool:
    """True if the player can move ``src`` -> ``dst`` in one jump/walk/swim,
    by simulation. The single-edge form of :func:`reachable_cells`, handy for
    tests and callers reasoning about a specific jump."""
    return dst in _successors(grid, src, movement, tiles)


def reachable_cells(
    grid,
    start: tuple[int, int],
    movement: PlayerMovementSpec,
    tiles: TileRegistry = DEFAULT_TILES,
) -> set[tuple[int, int]]:
    """BFS over traversable cells whose edges are the jump-arc SIMULATION
    (see the module docstring). Standable and volume cells both flow through
    :func:`_successors`; the start must itself be standable or in a volume."""
    stand = standable_cells(grid, tiles)
    volume = volume_cells(grid, tiles)
    if start not in stand and start not in volume:
        return set()
    drop = _drop_targets(grid, stand, volume, tiles)
    seen = {start}
    queue = deque([start])
    while queue:
        cur = queue.popleft()
        for nxt in _successors(
            grid, cur, movement, tiles, stand=stand, volume=volume, drop=drop
        ):
            if nxt not in seen and (nxt in stand or nxt in volume):
                seen.add(nxt)
                queue.append(nxt)
    return seen


def _tile_name(value: int, tiles: TileRegistry) -> str:
    tile = tiles.by_id.get(int(value))
    return tile.name.upper() if tile else str(value)


def _diagnose_unstandable(
    grid, cell: tuple[int, int], label: str, tiles: TileRegistry
) -> str:
    """Say WHY a cell isn't standable — 'not standable' alone sends the
    Layout Agent in circles (observed: a platform stamped over spawn drew
    three identical retries)."""
    x, y = cell
    height = grid.shape[0]
    occupant = int(grid[y, x])
    if occupant != tiles.empty_id:
        name = _tile_name(occupant, tiles)
        return (
            f"{label} at {cell} is covered by a {name} tile — "
            f"nothing may occupy the {label} cell; move that "
            f"{name.lower()} or move the {label}."
        )
    below = int(grid[y + 1, x]) if y + 1 < height else tiles.empty_id
    return (
        f"{label} at {cell} has no solid ground beneath it (the cell below "
        f"is {_tile_name(below, tiles)}) — keep floor under the {label} column."
    )


def check_level(
    grid,
    spawn: tuple[int, int],
    exit_: tuple[int, int],
    movement: PlayerMovementSpec,
    rules: GameRules = DEFAULT_RULES,
    tiles: TileRegistry = DEFAULT_TILES,
    triggers: list[SparseMaskEntry] | None = None,
    free_volume: set | None = None,
) -> list[str]:
    """Return problem strings (empty = valid). Messages are written to be
    fed back to the Layout Agent verbatim. ``triggers`` (checkpoints) are
    validated like spawn/exit: standable and reachable. ``free_volume``
    cells (deliberate water FEATURES) skip the containment rule."""
    problems: list[str] = []
    stand = standable_cells(grid, tiles)
    if spawn not in stand:
        problems.append(_diagnose_unstandable(grid, spawn, "spawn", tiles))
    if exit_ not in stand:
        problems.append(_diagnose_unstandable(grid, exit_, "exit", tiles))
    checkpoints = [
        (t.x, t.y) for t in (triggers or []) if t.type == "checkpoint"
    ]
    for cell in checkpoints:
        if cell not in stand:
            problems.append(
                _diagnose_unstandable(grid, cell, "checkpoint", tiles)
            )
    if rules.water_containment == "contained":
        problems.extend(
            check_volume_containment(grid, tiles, exempt=free_volume)
        )
    if not problems:
        reached = reachable_cells(grid, spawn, movement, tiles)
        if exit_ not in reached:
            problems.append(
                _describe_reachability_break(
                    grid, spawn, exit_, movement, stand, reached, "exit",
                    tiles,
                )
            )
        for cell in checkpoints:
            if cell not in reached:
                problems.append(
                    _describe_reachability_break(
                        grid, spawn, cell, movement, stand, reached,
                        "checkpoint", tiles,
                    )
                )
    return problems


def check_volume_containment(
    grid, tiles: TileRegistry = DEFAULT_TILES,
    exempt: frozenset | set | None = None,
) -> list[str]:
    """Under the 'contained' rule, every volume cell's sides must be held
    by solid tiles, more volume, or the level edge — pools sit in basins,
    they don't spill sideways. ('free' games skip this — waterfalls.)
    One-way platforms don't hold liquid; any volume cell continues a pool.
    ``exempt`` cells (the free-water FEATURE ops: water_wall/water_block)
    are deliberately uncontained and skipped."""
    height, width = grid.shape
    holds = tiles.ids("solid") | tiles.ids("volume")
    exempt = exempt or frozenset()
    problems: list[str] = []
    for x, y in sorted(volume_cells(grid, tiles)):
        if (x, y) in exempt:
            continue
        for nx in (x - 1, x + 1):
            if nx < 0 or nx >= width:
                continue  # level edge holds the pool
            if int(grid[y, nx]) not in holds:
                side = "left" if nx < x else "right"
                name = tiles.by_id[int(grid[y, x])].name
                problems.append(
                    f"{name} at ({x}, {y}) spills out its {side} side — "
                    f"contain the pool with wall({nx},{y},{y}) / raised "
                    "ground, or extend it to the level edge."
                )
                break
    return problems[:3]  # a few located examples beat a wall of repeats


def _spilling_cells(
    grid, tiles: TileRegistry = DEFAULT_TILES, exempt: set | None = None
) -> set[tuple[int, int]]:
    """The volume cells that SPILL under the 'contained' rule — a non-exempt
    volume cell with an open (non-solid, non-volume) horizontal neighbour inside
    the grid. (An exempt cell still HOLDS its neighbours; it is only skipped as
    a subject.) Shared by :func:`check_volume_containment` and the repair."""
    height, width = grid.shape
    holds = tiles.ids("solid") | tiles.ids("volume")
    exempt = exempt or frozenset()
    out: set[tuple[int, int]] = set()
    for x, y in volume_cells(grid, tiles):
        if (x, y) in exempt:
            continue
        for nx in (x - 1, x + 1):
            if 0 <= nx < width and int(grid[y, nx]) not in holds:
                out.add((x, y))
                break
    return out


def repair_containment_grid(
    grid, tiles: TileRegistry = DEFAULT_TILES, free_volume: set | None = None
) -> tuple[set[tuple[int, int]], list[str]]:
    """Code-not-LLM containment repair: re-interpret a spilling CONTAINED pool
    as a free-standing water FEATURE — its spilling cells join ``free_volume``,
    which the containment rule exempts. The water STAYS (still swimmable, same
    tile), no basin is forced, and no LLM round-trip / whole-level fallback is
    burned. Mirrors :func:`auto_bridge_grid`'s doctrine (auto_bridge computes a
    reachability fix; this computes a containment fix). Returns the newly-
    exempted cells + a repair note (empty set / list when nothing spills)."""
    spilled = _spilling_cells(grid, tiles, free_volume or set())
    if not spilled:
        return set(), []
    return spilled, [
        f"containment: {len(spilled)} spilling pool cell(s) re-interpreted as "
        "free-standing water (a swim-up feature) — no basin required."
    ]


def _headroom_ok(grid, x: int, y: int, solids) -> bool:
    """A 2-tall body needs the cell ABOVE the standing cell to be non-solid
    (one-way platforms don't block). A standable cell under a solid ceiling
    is a 1-TALL POCKET the simulator can never land in — the plat_seaside4
    l2 run burned every repair round aiming at one."""
    return y - 1 < 0 or int(grid[y - 1, x]) not in solids


def _body_stand(
    grid, stand: set[tuple[int, int]], tiles: TileRegistry
) -> set[tuple[int, int]]:
    """The standable cells a 2-tall body can actually OCCUPY — repair
    targets and marker placements must come from this set, not the raw
    1-cell ``standable_cells``."""
    solids = tiles.ids("solid")
    return {
        (x, y) for (x, y) in stand if _headroom_ok(grid, x, y, solids)
    }


def repair_capped_oneways_grid(
    grid,
    tiles: TileRegistry,
    *,
    hazards: Any = frozenset(),
    free_volume: Any = frozenset(),
    protected: Any = frozenset(),
) -> list[str]:
    """A one-way platform is a PROMISE it can be stood on, so wherever one
    exists it must be usable (ticket 2). A one-way at row R whose standing
    cell ``(x, R-1)`` is EMPTY but whose head cell ``(x, R-2)`` is SOLID —
    Pattern B, a real standing pocket sealed by a fallen-in slab — gets the
    slab CARVED so the platform tops a 2-tall pocket. If that slab is
    LOAD-BEARING (water rests on it, its own top is a usable 2-tall foothold,
    or it is a hazard/protected cell) the one-way is DELETED instead — its top
    was a 1-tall pocket, so no usable foothold is lost. A one-way whose
    standing cell is itself SOLID — Pattern A, flush against the platform top,
    no pocket — is DECORATIVE and left alone. SOLID geometry (walls, 1-tall
    solid corridors, traps) is NEVER touched. Mutates ``grid`` in place and
    returns repair notes. Runs BEFORE auto_bridge so any reachability
    consequence is caught and re-bridged."""
    solids = tiles.ids("solid")
    one_ways = tiles.ids("one_way")
    empty_id = tiles.empty_id
    height, width = grid.shape
    notes: list[str] = []

    def _carve_unsafe(x: int, slab_y: int) -> bool:
        # Carving the cap is unsafe only when it would spill CONTAINED water
        # resting on the slab, or the slab is a hazard / protected cell — then
        # the dead one-way is DELETED instead. A carve that merely removes
        # airspace above is fine: the one-way below becomes the foothold, and
        # reachability is re-validated by auto_bridge right after.
        if slab_y - 1 >= 0 and (x, slab_y - 1) in free_volume:
            return True
        return (x, slab_y) in hazards or (x, slab_y) in protected

    for r in range(height):
        for x in range(width):
            if int(grid[r, x]) not in one_ways or (x, r) in protected:
                continue
            stand_y = r - 1
            if stand_y < 0 or int(grid[stand_y, x]) in solids:
                # top row (no pocket) OR Pattern A (standing cell itself
                # solid — flush against the platform top): DECORATIVE, skip.
                continue
            head_y = r - 2
            if head_y < 0 or int(grid[head_y, x]) not in solids:
                continue  # already >=2 open above — usable, nothing to do
            # Pattern B: a real standing pocket sealed by a slab.
            if not _carve_unsafe(x, head_y):
                grid[head_y, x] = empty_id
                notes.append(
                    f"capped one-way at ({x},{r}): carved the cap at "
                    f"({x},{head_y}) so the platform tops a 2-tall pocket "
                    "(jump-up restored)."
                )
            else:
                grid[r, x] = empty_id
                notes.append(
                    f"capped one-way at ({x},{r}) removed — carving its cap "
                    "would spill water or hit a hazard; the dead platform is "
                    "dropped (its 1-tall top was unusable, no foothold lost)."
                )
    return notes


def _locate_break(
    stand: set[tuple[int, int]],
    reached: set[tuple[int, int]],
    target: tuple[int, int],
) -> tuple[tuple[int, int], tuple[int, int] | None]:
    """(frontier, nearest unreachable foothold) of a reachability break, located
    RELATIVE TO ``target`` (the unreachable exit/checkpoint) so it works on BOTH
    axes and for any future multi-exit: the frontier is the reached cell nearest
    the target (the edge of progress toward it), the nearest is the unreached
    foothold closest to that frontier. A fixed max-column frontier used to pin
    the break at the bottom-right FLOOR corner on a vertical climb and build a
    junk bridge there."""
    def _md(a: tuple[int, int], b: tuple[int, int]) -> int:
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    if not reached:  # pragma: no cover — spawn unstandable is caught earlier
        return target, None
    frontier = min(reached, key=lambda c: (_md(c, target), c[0], -c[1]))
    unreached = stand - reached
    if not unreached:
        return frontier, None
    nearest = min(unreached, key=lambda c: (_md(c, frontier), _md(c, target)))
    return frontier, nearest


def _suggest_bridge(
    grid,
    frontier: tuple[int, int],
    nearest: tuple[int, int],
    movement: PlayerMovementSpec,
    tiles: TileRegistry = DEFAULT_TILES,
) -> str | None:
    """A concrete platform op that bridges from the frontier toward the
    unreachable foothold — the arithmetic is ours, never the model's
    (code-for-computation: LLMs choose designs, tools compute).

    Candidates are verified against the GRID: the platform's two cells
    AND the standing cells above them must be open air, and standing on
    it must be reachable from the frontier BY SIMULATION — the same jump-arc
    physics :func:`reachable_cells` uses, with the proposed one-way platform
    injected, so a bridge this tool accepts is guaranteed to reconnect the
    level when auto_bridge stamps it (no more dead ops re-emitted to the
    bound). The first real run's fallback levels traced to the old blind
    formula proposing a platform whose standing cell was structurally dead.
    Returns None when no valid bridge exists (a design problem)."""
    height, width = grid.shape
    fx, fy = frontier
    nx, ny = nearest
    stand = standable_cells(grid, tiles)
    volume = volume_cells(grid, tiles)
    drop = _drop_targets(grid, stand, volume, tiles)

    def _free(x: int, y: int) -> bool:
        return 0 <= x < width and 0 <= y < height and int(grid[y, x]) == 0

    def _ok(col: int, row: int) -> bool:
        if not (1 <= row <= height - 3) or not (0 <= col <= width - 2):
            return False
        # Never pave over the break's own endpoints (both are open standing
        # cells, so the free-air check alone would happily fill them).
        if {(col, row), (col + 1, row)} & {frontier, nearest}:
            return False
        # Platform cells and the cells stood in above them: open air — and
        # the HEAD cells above those: non-solid, or the stone is a 1-tall
        # pocket no body fits into.
        if not all(_free(col + i, row) and _free(col + i, row - 1) for i in range(2)):
            return False
        solids = tiles.ids("solid")
        if not all(
            _headroom_ok(grid, col + i, row - 1, solids) for i in range(2)
        ):
            return False
        stand_rise = fy - (row - 1)
        if stand_rise > movement.jump_height:
            return False
        # Cheap geometric prefilter (rising costs range) before the sim.
        reach = max(1, max_dx_for_rise(movement, max(stand_rise, 0)))
        if min(abs(col - fx), abs(col + 1 - fx)) > reach:
            return False
        # Final gate: standing on the proposed platform must be physically
        # reachable from the frontier. Inject the platform as a one-way and
        # its two standing cells, then simulate — a platform tucked behind a
        # wall or capped above simply won't be reached, and is rejected.
        platform = frozenset({(col, row), (col + 1, row)})
        stands = frozenset({(col, row - 1), (col + 1, row - 1)})
        succ = _successors(
            grid, frontier, movement, tiles, stand=stand, volume=volume,
            drop=drop, extra_one_way=platform, extra_stand=stands,
        )
        return bool(stands & succ)

    # Rows whose STANDING level lands closest to the foothold's row first
    # (flat gaps get flat steps, tiers get risers), columns spiralling
    # out from between frontier and target.
    mid = (fx + nx) // 2
    cols = sorted(
        range(max(0, min(fx, nx) - 2), min(width - 2, max(fx, nx) + 2) + 1),
        key=lambda c: (abs(c - mid), abs(c - fx)),
    )
    top = max(1, fy - movement.jump_height + 1)
    rows = sorted(
        range(top, min(height - 3, fy - 1) + 1),
        key=lambda r: abs((r - 1) - ny),
    )
    # FALLBACK preference: stones at/just below the frontier's own row — a
    # LATERAL break between narrow zero-runway footholds (a plat_seaside3
    # climb killer: same-row footholds two columns apart, dead-stop jump ~1
    # cell) needs a same-level stepping stone the classic above-frontier
    # window can never offer. Tried only after every classic row fails, so
    # historical suggestions are unchanged.
    rows += sorted(
        range(max(1, fy), min(height - 3, fy + 2) + 1),
        key=lambda r: abs((r - 1) - ny),
    )
    for row in rows:
        for col in cols:
            if _ok(col, row):
                return f"platform({col},{row},2)"
    return None


def _describe_reachability_break(
    grid,
    spawn: tuple[int, int],
    target: tuple[int, int],
    movement: PlayerMovementSpec,
    stand: set[tuple[int, int]],
    reached: set[tuple[int, int]],
    label: str = "exit",
    tiles: TileRegistry = DEFAULT_TILES,
) -> str:
    """Locate the break AND hand over the fix — 'add stepping platforms'
    without a location sent the real model into fallback loops, and a
    located-but-arithmetic fix ('between columns 32 and 37, within 3
    rows above') still did. Name the frontier cell, the unreachable
    foothold, the failing constraint, and the exact op to add."""
    frontier, nearest = _locate_break(
        _body_stand(grid, stand, tiles), reached, target
    )
    if nearest is None:
        return (
            f"{label} at {target} is not reachable from spawn {spawn}. "
            f"[break@{target[0]},{target[1]}]"
        )
    # The FRONTIER cell (the reached foothold where progress stalls) as a
    # machine-readable tag so the section router regenerates the section that
    # owns the stall point, not the one holding the far-off target coord
    # printed first. The tag used to carry the frontier/nearest MIDPOINT —
    # on a TALL vertical break that average can land in a section containing
    # NEITHER endpoint (the plat_seaside3 l7 misroute); the frontier is
    # always a real endpoint.
    gap = frontier
    dx = abs(nearest[0] - frontier[0])
    rise = frontier[1] - nearest[1]
    constraints = []
    if rise > movement.jump_height:
        constraints.append(
            f"rise {rise} exceeds max jump height {movement.jump_height}"
        )
        allowed = movement.jump_width
    else:
        allowed = (
            max_dx_for_rise(movement, rise)
            if rise > 0
            else movement.jump_width
        )
    if dx > allowed:
        constraints.append(
            f"horizontal distance {dx} exceeds max jump distance {allowed}"
            + (
                f" at rise {rise} (rising costs range — keep high steps "
                "close)"
                if 0 < rise <= movement.jump_height
                else ""
            )
        )
    detail = " and ".join(constraints) or "no standable path connects them"
    base = (
        f"{label} at {target} is not reachable from spawn {spawn}. The player "
        f"gets as far as {frontier} but cannot reach the next foothold at "
        f"{nearest}: {detail}."
    )
    bridge = _suggest_bridge(grid, frontier, nearest, movement, tiles)
    if bridge is None:
        # No platform placement can bridge this — a DESIGN problem: the
        # surrounding geometry must change, not just gain a step.
        return (
            f"{base} No stepping platform fits here — open up the "
            f"terrain between {frontier} and {nearest} (widen the "
            f"passage or lower the ledge) so a path can exist. "
            f"[break@{gap[0]},{gap[1]}]"
        )
    return (
        f"{base} Fix: ADD THIS ONE LINE to your layout, "
        f"changing nothing else: {bridge} — it is jumpable from "
        f"{frontier} and bridges toward {nearest}. "
        f"Remember: a flat gap wider than {movement.jump_width - 1} columns "
        f"is impossible to cross without a stepping platform. "
        f"[break@{gap[0]},{gap[1]}]"
    )


def swimmer_spot_exists(
    grid,
    size: float,
    swim_style: str = "",
    tiles: TileRegistry = DEFAULT_TILES,
) -> bool:
    """True if ANY cell in the level can seat a swimmer of ``size`` with
    this ``swim_style`` — the env-feasibility question the roster
    pre-filter asks (an enemy the terrain can't sustain is never offered
    to the placement agent; the first multi-stage run burned all three
    retries per level trying to seat a 1.5-body swimmer in 1-deep
    pools). Mirrors ``check_placements``' footprint rules; the parity
    test cross-checks them."""
    from examples.platformer_pack.combat import occupancy

    volume = volume_cells(grid, tiles)
    if not volume:
        return False
    height, width = grid.shape
    empty_id = tiles.empty_id
    cols, rows = occupancy(size)

    def _fits(x: int, y: int) -> bool:
        for cx in range(x, x + cols):
            if not (0 <= cx < width and 0 <= y < height):
                return False
            if (cx, y) not in volume:
                return False
            if swim_style == "surface" and (cx, y - 1) in volume:
                return False
            for cy in range(y - 1, y - rows, -1):
                if cy < 0:
                    return False
                if swim_style == "surface":
                    if int(grid[cy, cx]) != empty_id:
                        return False
                elif (cx, cy) not in volume:
                    return False
        if swim_style == "float":
            return any(
                all(
                    (px_, py_) in volume
                    for px_ in (bx, bx + 1)
                    for py_ in (by, by + 1)
                )
                for bx in (x - 1, x)
                for by in (y - 1, y)
            )
        return True

    return any(_fits(x, y) for x, y in volume)


def flyer_spot_exists(
    grid,
    size: float,
    tiles: TileRegistry = DEFAULT_TILES,
) -> bool:
    """True if ANY cell can seat an airborne ``flyer`` of ``size`` — an
    open-air anchor whose whole body is empty, with a clear cell directly
    BELOW it (genuinely aloft, not a ground stand) and solid terrain
    somewhere further down the column (airspace OVER ground, not off the top
    or across a bottomless void). Nearly always true; rejects fully-solid or
    fully-ceilinged levels. The env-feasibility gate the roster pre-filter
    asks (mirrors ``_footprint_problem``'s flyer branch; parity-tested)."""
    from examples.platformer_pack.combat import occupancy

    height, width = grid.shape
    empty_id = tiles.empty_id
    support = tiles.ids("solid", "one_way")
    cols, rows = occupancy(size)

    def _fits(x: int, y: int) -> bool:
        for cx in range(x, x + cols):
            if not (0 <= cx < width):
                return False
            for cy in range(y, y - rows, -1):  # whole body in open air
                if cy < 0 or int(grid[cy, cx]) != empty_id:
                    return False
            if y + 1 >= height or int(grid[y + 1, cx]) != empty_id:
                return False  # aloft: open cell directly below the anchor
        # over terrain: solid ground somewhere below an occupied column
        return any(
            int(grid[yy, cx]) in support
            for cx in range(x, x + cols)
            for yy in range(y + 1, height)
        )

    return any(
        _fits(x, y) for y in range(height) for x in range(width)
    )


#: How far a checkpoint may be snapped to the nearest valid column — a
#: checkpoint's exact column within a few tiles carries no design intent.
MAX_CHECKPOINT_SNAP = 8


def snap_checkpoints(
    text: str,
    width: int,
    height: int,
    tiles: TileRegistry = DEFAULT_TILES,
) -> tuple[str, list[str]]:
    """Repair TOOL for misplaced checkpoints: rewrite ``checkpoint(x)``
    ops whose column has no floor beneath it (or whose standing cell is
    occupied, e.g. by a spike) to the nearest valid column within
    ``MAX_CHECKPOINT_SNAP``.

    Code-for-computation: the second real run's l3 burned all three
    attempts on checkpoint placement while the validator recited the
    exact list of valid columns — a lookup code can do. WHERE roughly a
    checkpoint goes is the agent's design; WHICH exact column has floor
    under it is arithmetic. Ops with no valid column nearby are left
    untouched so the informative stamp error reaches the agent.

    Returns ``(text, moves)`` — ``moves`` describes each snap for the log.
    """
    import re

    from examples.platformer_pack.dsl import DslError, parse_dsl, stamp

    ops = parse_dsl(text)  # may raise DslError — caller's feedback path
    checkpoint_xs = [args[0] for name, args in ops if name == "checkpoint"]
    if not checkpoint_xs:
        return text, []
    without = "\n".join(
        line
        for line in text.splitlines()
        if not re.match(r"\s*checkpoint\(", line)
    )
    try:
        result = stamp(without, width, height, tiles=tiles)
    except DslError:
        return text, []  # deeper problems; let the real stamp explain
    grid = result.grid
    ground_row, standing_row = height - 2, height - 3
    floor_id = next(t.id for t in tiles.tiles if t.name == "floor")

    def _valid(col: int, taken: set[int]) -> bool:
        return (
            0 <= col < width
            and col not in taken
            and int(grid[ground_row, col]) == floor_id
            and int(grid[standing_row, col]) == 0
        )

    moves: list[str] = []
    taken: set[int] = set()
    for x in checkpoint_xs:
        if _valid(x, taken):
            taken.add(x)
            continue
        snapped = next(
            (
                c
                for d in range(1, MAX_CHECKPOINT_SNAP + 1)
                for c in (x - d, x + d)
                if _valid(c, taken)
            ),
            None,
        )
        if snapped is None:
            taken.add(x)  # unfixable here — stamp's error is the feedback
            continue
        taken.add(snapped)
        text = re.sub(
            rf"checkpoint\(\s*{x}\s*\)", f"checkpoint({snapped})", text, count=1
        )
        moves.append(f"checkpoint({x}) -> checkpoint({snapped})")
    return text, moves


#: How far the spawn may be snapped to the nearest valid column. Wider
#: than the checkpoint bound: the first real multi-stage run lost THREE
#: levels to fallback where the final attempt failed ONLY on spawn
#: placement (the agent kept building terrain over its own spawn; the
#: nearest floored column was up to 8 away). The spawn's exact column
#: carries little design intent — the level flows left→right regardless.
MAX_SPAWN_SNAP = 16


def snap_spawn(
    text: str,
    width: int,
    height: int,
    tiles: TileRegistry = DEFAULT_TILES,
) -> tuple[str, list[str]]:
    """Repair TOOL for a misplaced spawn: rewrite ``spawn(x)`` when its
    standing cell is occupied or its column has no ground floor, to the
    nearest valid column within ``MAX_SPAWN_SNAP`` (skipping the exit's
    column — spawning on the exit would insta-win the level).

    Same contract as :func:`snap_checkpoints`: WHERE roughly the level
    starts is the agent's design; WHICH exact column is standable is
    arithmetic. An unfixable spawn is left untouched so the informative
    stamp error reaches the agent. Returns ``(text, moves)``.
    """
    import re

    from examples.platformer_pack.dsl import DslError, parse_dsl, stamp

    ops = parse_dsl(text)  # may raise DslError — caller's feedback path
    spawn_xs = [args[0] for name, args in ops if name == "spawn"]
    exit_xs = {args[0] for name, args in ops if name == "exit"}
    if len(spawn_xs) != 1:
        return text, []  # missing/duplicated spawn: stamp's error explains
    try:
        # Terrain-only probe (markers unvalidated): the point is to see
        # what the program BUILT so a valid spawn column can be chosen.
        result = stamp(text, width, height, tiles=tiles, validate_markers=False)
    except DslError:
        return text, []  # deeper problems; let the real stamp explain
    grid = result.grid
    ground_row, standing_row = height - 2, height - 3
    floor_id = next(t.id for t in tiles.tiles if t.name == "floor")

    def _valid(col: int) -> bool:
        return (
            0 <= col < width
            and col not in exit_xs
            and int(grid[ground_row, col]) == floor_id
            and int(grid[standing_row, col]) == 0
        )

    x = spawn_xs[0]
    if _valid(x):
        return text, []
    snapped = next(
        (
            c
            for d in range(1, MAX_SPAWN_SNAP + 1)
            for c in (x - d, x + d)
            if _valid(c)
        ),
        None,
    )
    if snapped is None:
        return text, []
    text = re.sub(rf"spawn\(\s*{x}\s*\)", f"spawn({snapped})", text, count=1)
    return text, [f"spawn({x}) -> spawn({snapped})"]


#: Bound on deterministic bridge insertions per level — far above any
#: real break count; a layout needing more is a design failure.
MAX_AUTO_BRIDGES = 8


def auto_bridge(
    text: str,
    width: int,
    height: int,
    movement: PlayerMovementSpec,
    rules: GameRules = DEFAULT_RULES,
    tiles: TileRegistry = DEFAULT_TILES,
    max_bridges: int = MAX_AUTO_BRIDGES,
) -> tuple[str, list[str], list[str]]:
    """Deterministically repair reachability by appending computed
    ``platform`` ops to the DSL until ``check_level`` passes.

    Code-for-computation: bridging a located break is arithmetic, and
    routing arithmetic through the LLM ("add a platform between columns
    32 and 37, within 3 rows above...") looped a real model into
    fallback. The agent keeps DESIGN authorship; this tool guarantees
    the traversability invariant, like the stamp guarantees cells.

    Returns ``(dsl_text, added_ops, problems)`` — ``problems`` is empty
    on success. Design problems (unstandable spawn/exit/checkpoint,
    spilled pools) are NEVER repaired here: those are content decisions
    with many valid answers, returned untouched for LLM feedback.
    """
    from examples.platformer_pack.dsl import stamp

    added: list[str] = []
    while True:
        result = stamp(text, width, height, tiles=tiles)
        problems = check_level(
            result.grid, result.spawn, result.exit, movement,
            rules=rules, tiles=tiles, triggers=result.triggers,
            free_volume=result.free_volume,
        )
        if not problems:
            return text, added, []
        if not all("is not reachable from spawn" in p for p in problems):
            # Design problems present — the agent's to fix, not ours.
            # (If one appeared AFTER our bridges, the caller's feedback
            # includes it and the model sees the bridged layout.)
            return text, added, problems
        if len(added) >= max_bridges:
            return text, added, problems
        stand = standable_cells(result.grid, tiles)
        reached = reachable_cells(result.grid, result.spawn, movement, tiles)
        frontier, nearest = _locate_break(stand, reached, result.exit)
        if nearest is None:  # pragma: no cover — guarded by check_level
            return text, added, problems
        op = _suggest_bridge(result.grid, frontier, nearest, movement, tiles)
        if op is None or op in added:
            # No valid bridge exists, or the last one changed nothing —
            # give the problems to the agent instead of burning the bound
            # on a dead op (the first real run's l1/l3 failure mode).
            return text, added, problems
        text = f"{text}\n# auto-bridge\n{op}"
        added.append(op)


# --------------------------------------------------------------------------
# Grid-native repair — the sectioned-levels path composites section sub-grids
# into ONE whole-level grid (sections.composite), so there is no whole-level
# DSL text for the text tools above to rewrite. These twins run the SAME
# computable repairs directly on the composited grid + markers, reusing the
# grid-based cores (check_level / reachable_cells / _locate_break /
# _suggest_bridge). Same doctrine: geometry/columns are arithmetic (code
# fixes them), design stays the agent's.
# --------------------------------------------------------------------------


def _floor_id(tiles: TileRegistry) -> int:
    return next(t.id for t in tiles.tiles if t.name == "floor")


def place_exit(
    grid,
    tiles: TileRegistry = DEFAULT_TILES,
    exclude: set[tuple[int, int]] | None = None,
    axis: str = "horizontal",
) -> tuple[int, int] | None:
    """Where the stitcher plants the whole-level exit (sections never declare
    their own — the "goal mode" lives here, not in the DSL).

    - ``horizontal``: the RIGHTMOST body-standable cell; at the same column
      the LOWEST (ground level when it exists — the classic side-scroller
      exit). The old rule considered GROUND-ROW floor only, so a level whose
      right side is built up (plat_seaside5 l8: raised terrain covering the
      entire ground-level standing row from x=34 on) exited at a third of
      its width — elevated right-edge terrain is a legal exit spot now (the
      horizontal analogue of the vertical summit).
    - ``vertical``: the climb SUMMIT — the TOPMOST standable cell (smallest y),
      leftmost on ties. A vertical level exits at the top, not the right edge.

    ``exclude`` is a set of CELLS to skip (e.g. the spawn cell) so the exit never
    lands ON the spawn — a per-CELL exclude (not per-column) so a vertical summit
    directly ABOVE the spawn is not wrongly dropped. None when no such cell
    exists (a design failure the caller surfaces)."""
    exclude = exclude or set()
    # Candidates need HEADROOM — a 1-tall pocket is a goal no body can
    # stand in (see _body_stand).
    stand = _body_stand(grid, standable_cells(grid, tiles), tiles)
    cands = [c for c in stand if c not in exclude]
    if not cands:
        return None
    if axis == "vertical":
        return min(cands, key=lambda c: (c[1], c[0]))
    return max(cands, key=lambda c: (c[0], c[1]))


def snap_spawn_grid(
    grid,
    spawn: tuple[int, int] | None,
    exit_: tuple[int, int] | None,
    tiles: TileRegistry = DEFAULT_TILES,
    max_snap: int = MAX_SPAWN_SNAP,
) -> tuple[tuple[int, int] | None, list[str]]:
    """Grid twin of :func:`snap_spawn`: move a stranded spawn to the nearest
    ground-floored, open column within ``max_snap`` (skipping the exit's
    column). Returns ``(spawn, moves)`` — ``spawn`` unchanged when already
    valid or unfixable."""
    if spawn is None:
        return spawn, []
    height, width = grid.shape
    ground_row, standing_row = height - 2, height - 3
    floor_id = _floor_id(tiles)
    exit_col = exit_[0] if exit_ else None

    def _valid(col: int) -> bool:
        return (
            0 <= col < width
            and col != exit_col
            and int(grid[ground_row, col]) == floor_id
            and int(grid[standing_row, col]) == 0
        )

    x = spawn[0]
    if _valid(x):
        return spawn, []
    snapped = next(
        (c for d in range(1, max_snap + 1) for c in (x - d, x + d) if _valid(c)),
        None,
    )
    if snapped is None:
        return spawn, []
    return (snapped, standing_row), [f"spawn({x}) -> spawn({snapped})"]


def clear_spawn_grid(
    grid,
    spawn: tuple[int, int] | None,
    tiles: TileRegistry = DEFAULT_TILES,
    hazards: list | None = None,
) -> list[str]:
    """Last-resort twin of :func:`snap_spawn_grid` for the covered-spawn
    case it cannot fix (paid l8r1: the section's own ``stairs_up`` stamped
    floor over the spawn cell, every column within ``MAX_SPAWN_SNAP`` was
    invalid, and the byte-identical problem burned all three regen rounds
    — one cleared stair cell validates the whole room). Clears the
    covering cell — and the headroom cell above when solid — as pure
    geometry, bounded at 2 cells; an OPEN spawn cell with no ground under
    it needs an ADDITIVE fix and is left alone. A cleared hazard cell
    drops its sparse record (the climb-lane precedent — no invisible
    damage cell may survive). Returns note strings for the ``snaps``
    record; empty when nothing was cleared."""
    if spawn is None:
        return []
    x, y = spawn
    if int(grid[y, x]) == tiles.empty_id:
        return []
    cleared = [(x, y)]
    grid[y, x] = tiles.empty_id
    if y > 0 and int(grid[y - 1, x]) in tiles.ids("solid"):
        grid[y - 1, x] = tiles.empty_id
        cleared.append((x, y - 1))
    if hazards is not None:
        gone = set(cleared)
        hazards[:] = [e for e in hazards if (e.x, e.y) not in gone]
    return [f"spawn-clear: cleared ({cx},{cy})" for cx, cy in cleared]


def snap_checkpoints_grid(
    grid,
    triggers: list[SparseMaskEntry],
    tiles: TileRegistry = DEFAULT_TILES,
    max_snap: int = MAX_CHECKPOINT_SNAP,
) -> tuple[list[SparseMaskEntry], list[str]]:
    """Grid twin of :func:`snap_checkpoints`: move each checkpoint trigger
    whose column lost its floor (or whose standing cell is occupied) to the
    nearest valid column within ``max_snap``, never colliding two onto one
    column. Non-checkpoint triggers ride through untouched. Returns
    ``(triggers, moves)``."""
    height, width = grid.shape
    ground_row, standing_row = height - 2, height - 3
    floor_id = _floor_id(tiles)

    def _valid(col: int, taken: set[int]) -> bool:
        return (
            0 <= col < width
            and col not in taken
            and int(grid[ground_row, col]) == floor_id
            and int(grid[standing_row, col]) == 0
        )

    out: list[SparseMaskEntry] = []
    moves: list[str] = []
    taken: set[int] = set()
    for t in triggers:
        if t.type != "checkpoint":
            out.append(t)
            continue
        x = t.x
        if _valid(x, taken):
            taken.add(x)
            out.append(t)
            continue
        snapped = next(
            (
                c
                for d in range(1, max_snap + 1)
                for c in (x - d, x + d)
                if _valid(c, taken)
            ),
            None,
        )
        if snapped is None:
            taken.add(x)  # unfixable here — check_level's error is the feedback
            out.append(t)
            continue
        taken.add(snapped)
        out.append(t.model_copy(update={"x": snapped, "y": standing_row}))
        moves.append(f"checkpoint({x}) -> checkpoint({snapped})")
    return out, moves


def place_checkpoints_grid(
    grid,
    spawn: tuple[int, int] | None,
    exit_: tuple[int, int] | None,
    sections: list,
    checkpoint_sections: list[int],
    axis: str,
    movement: PlayerMovementSpec,
    tiles: TileRegistry = DEFAULT_TILES,
) -> list[SparseMaskEntry]:
    """The blueprint owns checkpoint COUNT + which sections host them; the
    stitcher PLACES each at a REACHABLE standable cell inside its section's
    range (a column for horizontal, a platform ROW for vertical). This unifies
    both axes, guarantees reachable + count-capped checkpoints by construction
    (no snap needed — the sectioned analogue of :func:`place_exit`), and lets a
    vertical climb carry mid-climb respawns. Returns the checkpoint triggers.

    Skips a section whose range holds no reachable standable cell (a checkpoint
    there would be unfair); never places two on the same cell."""
    if not checkpoint_sections or spawn is None:
        return []
    stand = standable_cells(grid, tiles)
    reached = reachable_cells(grid, spawn, movement, tiles)

    def _coord(c: tuple[int, int]) -> int:
        return c[0] if axis == "horizontal" else c[1]

    out: list[SparseMaskEntry] = []
    used: set[tuple[int, int]] = set()
    for si in checkpoint_sections:
        if not 0 <= si < len(sections):
            continue
        ps = sections[si]
        lo = ps.x_off if axis == "horizontal" else ps.y_off
        hi = lo + ps.length
        center = (lo + hi) // 2
        cands = [
            c
            for c in stand
            if c in reached
            and lo <= _coord(c) < hi
            and c != spawn
            and c != exit_
            and c not in used
        ]
        if not cands:
            continue
        # Nearest the section centre; deterministic tie-break.
        pick = min(cands, key=lambda c: (abs(_coord(c) - center), c[0], c[1]))
        used.add(pick)
        out.append(SparseMaskEntry(x=pick[0], y=pick[1], type="checkpoint"))
    return out


def flood_grid(
    grid,
    tiles: TileRegistry = DEFAULT_TILES,
    topology: str = "fully_submerged",
    depth: int = 0,
) -> tuple[object, list[str]]:
    """The WATER-LEVEL flood pass (water arc) — deterministic CODE, not
    LLM: returns a COPY of ``grid`` with EMPTY cells filled by the
    ``water`` volume tile (solids/one-ways/hazards persist — the
    ``_stamp_volume`` empty-only rule), plus repair-style notes.

    - ``fully_submerged``: every empty cell floods (the whole level
      swims).
    - ``waterline``: empty cells in the bottom ``depth`` rows flood
      (rows >= height - depth); islands above stay dry.

    A copy so the caller's waterline-lowering escalation can re-flood
    the pristine dry grid per attempt — flood water is otherwise
    indistinguishable from DSL-authored pools, which must persist."""
    out = grid.copy()
    height, width = out.shape
    water = tiles.by_name.get("water")
    if water is None:
        return out, ["flood skipped — this game's registry has no water tile"]
    empty = tiles.empty_id
    top = 0 if topology == "fully_submerged" else max(0, height - int(depth))
    filled = 0
    for y in range(top, height):
        for x in range(width):
            if int(out[y, x]) == empty:
                out[y, x] = water.id
                filled += 1
    label = (
        "fully submerged"
        if topology == "fully_submerged"
        else f"waterline at row {top} ({int(depth)} rows deep)"
    )
    return out, [f"water level: {label} — {filled} cells flooded"]


def place_room_entrances(
    grid,
    spawn: tuple[int, int] | None,
    exit_: tuple[int, int] | None,
    sections: list,
    specs: list,
    axis: str,
    movement: PlayerMovementSpec,
    tiles: TileRegistry = DEFAULT_TILES,
    triggers: list[SparseMaskEntry] | None = None,
    restrict_to: set[tuple[int, int]] | None = None,
) -> tuple[list[SparseMaskEntry], list[str]]:
    """STITCHER-placed secret-room entrances (multi-room arc, user-locked):
    per rolled room, a reachable body-standable cell in the blueprint's
    host section becomes a ``room_entrance`` trigger carrying the room id,
    the entry verb, and the RETURN cell (where the room's portal drops the
    player back). Placement escalates so the roll and the triggers never
    diverge: host section → any section → a ``shortcut`` return with no
    later-section foothold demotes to ``detour``. Returns
    ``(entrance_triggers, notes)`` — notes record every escalation.

    Entrances stand apart from spawn/exit/checkpoints/each other; the
    consumers' verb press (Down = pipe, Up = door) happens ON the cell, so
    body-standable (headroom-qualified) is the candidate set, like
    :func:`place_exit`."""
    if spawn is None or not specs:
        return [], []
    stand = _body_stand(grid, standable_cells(grid, tiles), tiles)
    reached = reachable_cells(grid, spawn, movement, tiles)
    cands = stand & reached
    # Caller-restricted candidates (water arc: entrance AND shortcut-
    # return cells must stay DRY above a waterline — verb presses are
    # gated on not-swimming in both consumers).
    if restrict_to is not None:
        cands &= restrict_to
    taken: set[tuple[int, int]] = {spawn}
    if exit_ is not None:
        taken.add(exit_)
    taken |= {(t.x, t.y) for t in (triggers or [])}

    def _coord(c: tuple[int, int]) -> int:
        return c[0] if axis == "horizontal" else c[1]

    out: list[SparseMaskEntry] = []
    notes: list[str] = []
    for spec in specs:
        host = int(getattr(spec, "host_section", 0))
        host = min(max(host, 0), len(sections) - 1) if sections else 0
        if sections:
            ps = sections[host]
            lo = ps.x_off if axis == "horizontal" else ps.y_off
            hi = lo + ps.length
        else:
            lo, hi = 0, grid.shape[1] if axis == "horizontal" else grid.shape[0]
        center = (lo + hi) // 2
        pool = [
            c for c in cands if c not in taken and lo <= _coord(c) < hi
        ]
        if not pool:
            pool = [c for c in cands if c not in taken]
            if pool:
                notes.append(
                    f"room {spec.room_id}: host section {host} offers no "
                    "reachable standing cell — entrance escalated to the "
                    "whole level (code repair)."
                )
        if not pool:
            notes.append(
                f"room {spec.room_id}: NO reachable standing cell for an "
                "entrance anywhere — room left without an entrance."
            )
            continue
        pick = min(pool, key=lambda c: (abs(_coord(c) - center), c[0], c[1]))
        taken.add(pick)

        topology = str(getattr(spec, "return_topology", "detour"))
        ret = pick
        if topology == "shortcut":
            # "Further along the main map": past the host section's far
            # edge — larger x for a side-scroller, HIGHER (smaller y) for
            # a climb. Farthest-along wins (the shortcut's payoff).
            if axis == "horizontal":
                later = [c for c in cands if c not in taken and c[0] >= hi]
                key = lambda c: (c[0], -c[1])  # noqa: E731
            else:
                later = [c for c in cands if c not in taken and c[1] < lo]
                key = lambda c: (-c[1], c[0])  # noqa: E731
            if later:
                ret = max(later, key=key)
            else:
                topology = "detour"
                notes.append(
                    f"room {spec.room_id}: no reachable foothold past "
                    f"section {host} — shortcut return demoted to detour "
                    "(code repair)."
                )
        out.append(
            SparseMaskEntry(
                x=pick[0],
                y=pick[1],
                type="room_entrance",
                params={
                    "room_id": spec.room_id,
                    "verb": str(getattr(spec, "entry_verb", "pipe")),
                    "room_type": str(getattr(spec, "room_type", "")),
                    "return": topology,
                    "return_x": int(ret[0]),
                    "return_y": int(ret[1]),
                },
            )
        )
    return out, notes


#: Reachability-repair escalation caps (code-not-LLM, §1b): how many solid
#: supports may be surgically opened into one-way platforms and how many
#: climb lanes may be carved per level before the residue goes back to the
#: owning section as a design problem.
MAX_MOUNT_CONVERSIONS = 4
MAX_CLIMB_LANES = 4

#: Overflow rung for a bridge suggestion that arrives AFTER the bridge
#: budget is spent: the op is simulation-verified either way, so stamping
#: it in code beats returning it as prose for an LLM round (the paid l8
#: round-3 'Fix: ADD THIS ONE LINE ... platform(71,12,2)' fell to the
#: bridge cap and burned a terminal fallback instead). The cap is tiny and
#: separate so a deliberate design failure still goes back to its section.
MAX_FIX_LINE_STAMPS = 3


def _convert_support_to_one_way(
    grid,
    nearest: tuple[int, int],
    tiles: TileRegistry,
    protect: set[tuple[int, int]] | frozenset = frozenset(),
    free_volume: set | frozenset = frozenset(),
) -> list[str]:
    """Mount-from-below repair: when the unreached foothold's SUPPORT is a
    solid the jump arc can only bonk into (a real model builds climb rungs
    from solid ``ledge`` strips — the plat_seaside3 l4 break was a foothold
    two rows up whose own support sealed the shaft), convert a short strip of
    that support to the ONE-WAY platform tile so the player can jump up
    THROUGH it and land on it. Surgical (≤3 cells), never the ground/bedrock
    rows, never cells adjacent to a volume (a converted basin wall would
    spill the pool), never ``protect`` cells (spawn/exit + their supports).
    Returns a repair note, or [] when not applicable."""
    height, width = grid.shape
    nx, ny = nearest
    sup_row = ny + 1
    if sup_row >= height - 2:  # the walking floor / bedrock stay solid
        return []
    solids = tiles.ids("solid")
    if int(grid[sup_row, nx]) not in solids:
        return []
    volume = tiles.ids("volume")
    lo = nx
    while lo - 1 >= 0 and int(grid[sup_row, lo - 1]) in solids and nx - lo < 1:
        lo -= 1
    hi = nx
    while hi + 1 < width and int(grid[sup_row, hi + 1]) in solids and hi - nx < 1:
        hi += 1
    cells = [(x, sup_row) for x in range(lo, hi + 1)]
    for x, y in cells:
        if (x, y) in protect:
            return []
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            ax, ay = x + dx, y + dy
            if 0 <= ax < width and 0 <= ay < height:
                # Contained liquid vetoes (a converted basin wall would
                # spill it); free clouds/spouts are exempt.
                if int(grid[ay, ax]) in volume and (ax, ay) not in free_volume:
                    return []
    one_way = tiles.by_name["platform"].id
    for x, y in cells:
        grid[y, x] = one_way
    return [
        f"# repair mount-open: solid support ({lo}..{hi},{sup_row}) under "
        f"foothold ({nx},{ny}) -> one-way platform (jump up through it)"
    ]


def _carve_door(
    grid,
    frontier: tuple[int, int],
    nearest: tuple[int, int],
    tiles: TileRegistry,
    protect: set[tuple[int, int]] | frozenset = frozenset(),
    hazards: list | None = None,
    max_cols: int = 8,
    free_volume: set | frozenset = frozenset(),
) -> list[str]:
    """The climb lane's HORIZONTAL analogue: a LATERAL break whose span is
    walled (the plat_seaside4 l8 stall — a six-tall wall stack on the ground
    between two same-level footholds, unjumpable and unbridgeable) gets a
    body-height DOORWAY carved through it at the footholds' level. Solids
    and hazard cells clear (hazard records dropped); basin walls, ``protect``
    cells, and the ground/bedrock rows stay. Returns a repair note, or []
    when not a lateral-walled case / nothing to carve."""
    height, width = grid.shape
    fx, fy = frontier
    nx, ny = nearest
    if abs(fy - ny) > 1:
        return []
    lo, hi = sorted((fx, nx))
    if hi - lo < 2 or hi - lo > max_cols:
        return []
    solids = tiles.ids("solid")
    hazard_ids = tiles.ids("hazard")
    volume = tiles.ids("volume")

    def _volume_adjacent(x: int, y: int) -> bool:
        # Only CONTAINED liquid vetoes a carve (protecting basin walls);
        # free features (clouds/spouts) are containment-exempt and must not
        # deadlock the repair.
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            ax, ay = x + dx, y + dy
            if 0 <= ax < width and 0 <= ay < height:
                if int(grid[ay, ax]) in volume and (ax, ay) not in free_volume:
                    return True
        return False

    top = max(1, min(fy, ny) - 2)
    bottom = min(height - 3, max(fy, ny))
    carved = 0
    cleared_hazards: set[tuple[int, int]] = set()
    for y in range(top, bottom + 1):
        for x in range(lo + 1, hi):
            if (x, y) in protect:
                continue
            v = int(grid[y, x])
            if v in solids and not _volume_adjacent(x, y):
                grid[y, x] = 0
                carved += 1
            elif v in hazard_ids:
                grid[y, x] = 0
                carved += 1
                cleared_hazards.add((x, y))
    if cleared_hazards and hazards is not None:
        hazards[:] = [
            e for e in hazards if (e.x, e.y) not in cleared_hazards
        ]
    if not carved:
        return []
    return [
        f"# repair doorway: cols {lo + 1}..{hi - 1} rows {top}..{bottom} "
        f"(cleared {carved} cell(s)) between ({fx},{fy}) and ({nx},{ny})"
    ]


def _carve_climb_lane(
    grid,
    frontier: tuple[int, int],
    nearest: tuple[int, int],
    tiles: TileRegistry,
    protect: set[tuple[int, int]] | frozenset = frozenset(),
    hazards: list | None = None,
    max_rows: int = 32,
    free_volume: set | frozenset = frozenset(),
) -> list[str]:
    """Last-resort computable reachability repair: carve a 2-column LANE of
    open air from the frontier's row up to the unreached foothold and stamp
    one-way rungs every 2 rows, PARITY-ANCHORED at the foothold so the top
    hop lands exactly on it — the repair that can fix what an ADDITIVE
    bridge cannot (a rise beyond jump height inside dense terrain, the class
    that whole-laddered every plat_seaside3 climb). Rung → standing → rung
    is rise-2/dx≤1 throughout: feasible from a dead stop, no runway needed.

    Solids AND hazards in the lane band are cleared (a full-width spike row
    sealed the l9 shaft; clearing a 2-cell notch in it is the pass-through
    gap a designer would leave) — cleared hazard cells also drop their
    ``hazards`` RECORDS so no invisible damage cell survives. Volumes stay
    (already swimmable), ``protect`` cells stay, the ground/bedrock rows
    stay. Bounded to ``max_rows``. Returns a repair note, or [] when the
    lane would change nothing."""
    height, width = grid.shape
    fx, fy = frontier
    nx, ny = nearest
    cx = max(0, min(width - 2, nx))
    solids = tiles.ids("solid")
    hazard_ids = tiles.ids("hazard")
    volume = tiles.ids("volume")
    one_way = tiles.by_name["platform"].id
    # The lane band: headroom above the foothold down to the frontier's
    # standing row (never the walking floor / bedrock rows). The carve
    # covers the rung columns AND the TAKEOFF column when it adjoins —
    # the plat_seaside4 l2 trap survived a 2-col lane because the bonk
    # ceiling sat over the FRONTIER's column, one outside it.
    lane_cols = {cx, cx + 1}
    if abs(fx - nx) <= 2 and 0 <= fx < width:
        lane_cols.add(fx)
    top = max(1, ny - 2)
    bottom = min(height - 3, max(fy, ny + 1))
    if bottom - top > max_rows:
        bottom = top + max_rows
    def _volume_adjacent(x: int, y: int) -> bool:
        # A solid 4-adjacent to CONTAINED volume may be a basin wall —
        # clearing it would spill the pool into a containment problem
        # mid-repair. FREE features (clouds/spouts) are containment-exempt
        # and must not deadlock the lane (the l4 stall: its own clouds).
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            ax, ay = x + dx, y + dy
            if 0 <= ax < width and 0 <= ay < height:
                if int(grid[ay, ax]) in volume and (ax, ay) not in free_volume:
                    return True
        return False

    carved = 0
    cleared_hazards: set[tuple[int, int]] = set()
    for y in range(top, bottom + 1):
        for x in sorted(lane_cols):
            if (x, y) in protect:
                continue
            v = int(grid[y, x])
            if v in solids and not _volume_adjacent(x, y):
                grid[y, x] = 0
                carved += 1
            elif v in hazard_ids:
                grid[y, x] = 0
                carved += 1
                cleared_hazards.add((x, y))
    if cleared_hazards and hazards is not None:
        hazards[:] = [
            e for e in hazards if (e.x, e.y) not in cleared_hazards
        ]
    # Rungs every 2 rows, anchored at ny+1 (standing row ny == the foothold's
    # row) and descending to the frontier's row — each hop is rise 2, dx ≤ 1.
    rungs = 0
    r = ny + 1
    while r <= bottom:
        placed = False
        for x in (cx, cx + 1):
            if (x, r) in protect or (x, r) in {frontier, nearest}:
                continue
            if int(grid[r, x]) == 0:
                grid[r, x] = one_way
                placed = True
        rungs += 1 if placed else 0
        r += 2
    if not carved and not rungs:
        return []
    cols_desc = "-".join(str(c) for c in sorted(lane_cols))
    return [
        f"# repair climb-lane: cols {cols_desc} rows {top}..{bottom} "
        f"(cleared {carved} cell(s), {rungs} rung row(s)) from "
        f"({fx},{fy}) toward foothold ({nx},{ny})"
    ]


def auto_bridge_grid(
    grid,
    spawn: tuple[int, int] | None,
    exit_: tuple[int, int] | None,
    movement: PlayerMovementSpec,
    rules: GameRules = DEFAULT_RULES,
    tiles: TileRegistry = DEFAULT_TILES,
    triggers: list[SparseMaskEntry] | None = None,
    free_volume: set | None = None,
    max_bridges: int = MAX_AUTO_BRIDGES,
    hazards: list | None = None,
) -> tuple[object, list[str], list[str]]:
    """Grid twin of :func:`auto_bridge`: stamp computed ``platform`` bridges
    DIRECTLY onto the composited grid (non-empty-wins) until ``check_level``
    passes. Mutates and returns ``grid``. ``added`` lists the bridge ops and
    ``# repair …`` notes for the log; ``problems`` is non-empty only when a
    DESIGN problem remains (never repaired here — that is the owning
    section's to fix). ``hazards`` (the sparse hazard RECORDS) is kept
    consistent when a climb lane clears a hazard cell.

    Reachability is COMPUTABLE, so it escalates rather than giving up
    (code-not-LLM, the §1b climb fix): an additive one-way bridge first;
    where no platform fits, a surgical mount-open (solid support → one-way);
    finally a carved climb lane. The paid runs proved the old
    additive-only version could not repair a walled climb — every
    plat_seaside3 vertical burned its regen rounds against 'No stepping
    platform fits here' and shipped the whole-ladder fallback."""
    from examples.platformer_pack.dsl import stamp

    height, width = grid.shape
    added: list[str] = []
    bridges = 0
    fix_lines = 0
    conversions = 0
    lanes = 0
    seen_ops: set[str] = set()

    def _stamp_op(op_str: str) -> None:
        sub = stamp(op_str, width, height, tiles=tiles, validate_markers=False)
        mask = sub.grid != 0
        grid[mask] = sub.grid[mask]  # non-empty wins (bridge lands in air)

    # Two protection tiers: the lane may not CLEAR the spawn/exit cells or
    # the supports under them (clearing removes footing); the mount-open
    # only avoids the marker cells themselves — converting a support to
    # one-way PRESERVES standability, so converting the exit's own support
    # is legal (and is exactly the fix when a sealing ledge is the summit).
    protect_cells = {c for c in (spawn, exit_) if c}
    protect_carve = protect_cells | {(x, y + 1) for (x, y) in protect_cells}
    while True:
        problems = check_level(
            grid, spawn, exit_, movement, rules=rules, tiles=tiles,
            triggers=triggers, free_volume=free_volume,
        )
        if not problems:
            return grid, added, []
        if not all("is not reachable from spawn" in p for p in problems):
            return grid, added, problems
        # Repair targets must be footholds a 2-tall body can occupy — the
        # raw standable set includes 1-tall pockets that fixate the break
        # locator on an impossible cell (the plat_seaside4 l2 loop).
        stand = _body_stand(grid, standable_cells(grid, tiles), tiles)
        reached = reachable_cells(grid, spawn, movement, tiles)
        frontier, nearest = _locate_break(stand, reached, exit_)
        if nearest is None:
            return grid, added, problems
        op = _suggest_bridge(grid, frontier, nearest, movement, tiles)
        if op is not None and op not in seen_ops:
            if bridges < max_bridges:
                seen_ops.add(op)
                _stamp_op(op)
                added.append(op)
                bridges += 1
                continue
            if fix_lines < MAX_FIX_LINE_STAMPS:
                # FIX-LINE overflow rung: the bridge budget is exhausted but
                # the suggestion is verified — stamp it under its own small
                # cap instead of shipping it as 'Fix: ADD THIS ONE LINE'
                # text for the LLM to transcribe.
                seen_ops.add(op)
                _stamp_op(op)
                added.append(
                    f"# repair fix-line: {op} "
                    "(validator-verified, stamped in code)"
                )
                fix_lines += 1
                continue
        rise = frontier[1] - nearest[1]
        if conversions < MAX_MOUNT_CONVERSIONS and rise <= movement.jump_height:
            # Opening the foothold's support only helps when the arc can
            # already REACH its row — a rise beyond jump height needs the
            # lane's intermediate rungs, not a mount.
            notes = _convert_support_to_one_way(
                grid, nearest, tiles, protect_cells,
                free_volume=free_volume or frozenset(),
            )
            if notes:
                conversions += 1
                added.extend(notes)
                continue
        if lanes < MAX_CLIMB_LANES:
            # A lateral walled span gets a doorway; a vertical break gets
            # the climb lane. Both count against the same cap.
            notes = _carve_door(
                grid, frontier, nearest, tiles, protect_carve,
                hazards=hazards, free_volume=free_volume or frozenset(),
            )
            if not notes:
                notes = _carve_climb_lane(
                    grid, frontier, nearest, tiles, protect_carve,
                    hazards=hazards, free_volume=free_volume or frozenset(),
                )
            if notes:
                lanes += 1
                added.extend(notes)
                continue
        return grid, added, problems


#: How far a spawn-crowding placement may be column-nudged to the nearest
#: valid cell — WHERE an enemy roughly goes is design; the exact clear
#: column is arithmetic (the snap_checkpoints precedent).
MAX_PLACEMENT_NUDGE = 8

#: How far a wader dropped in deep water may be snapped to reach a
#: submerged-ground flat (seabed) before it kicks back to LLM feedback.
_WADER_SNAP_RADIUS = 8


def check_placements(
    grid,
    placements: list[dict],
    spawn: tuple[int, int],
    enemies: dict[str, dict],
    rules: GameRules = DEFAULT_RULES,
    tiles: TileRegistry = DEFAULT_TILES,
    variants: VariantSet = DEFAULT_VARIANTS,
    combat: CombatSpec = DEFAULT_COMBAT,
) -> tuple[list[dict], list[str], list[str]]:
    """Split proposed placements into (accepted, problems, repairs).

    ``enemies`` maps enemy id → ``{"archetype": str, "size": float}``
    plus optional ecology keys ``"swim_style"`` ("within"/"surface"/
    "float" — swimmer sub-behavior: surface-riders anchor on the water's
    TOP row with open air above, floaters need a 2x2 water pocket to
    drift in) and ``"rarity"`` (per-level at-most-N caps from
    ``GameRules.rarity_caps`` — what makes rares rare on the ground).
    Rules: known enemy id; the game's ``enemy_water_policy`` decides who
    may occupy volume cells; the FULL footprint of the effective size
    (definition size × variant size — ``combat.occupancy``: two supported
    columns at 2.0, ``ceil(size)`` rows of clearance) must fit, with
    failures naming the located cell; ``variant`` names must come from
    the game's variant vocabulary and respect ``GameRules.variant_caps``
    (per level). A legacy boolean ``elite`` maps to ``variant: "elite"``.

    Spawn safety (no enemy within ``combat.spawn_safety_columns`` of the
    spawn column on the spawn row) is a CODE REPAIR, never LLM feedback:
    violators are column-nudged to the nearest cell that fits their whole
    body outside the radius (up to ``MAX_PLACEMENT_NUDGE``), recorded in
    ``repairs``; only an un-nudgeable placement kicks back.
    """
    stand = standable_cells(grid, tiles)
    volume = volume_cells(grid, tiles)
    wet_stand = seabed_cells(grid, tiles)
    hazard_stand = hazard_stand_cells(grid, tiles)
    height, width = grid.shape
    empty_id = tiles.empty_id
    support = tiles.ids("solid", "one_way")
    accepted: list[dict] = []
    problems: list[str] = []
    repairs: list[str] = []
    variant_counts: dict[str, int] = {}
    rarity_counts: dict[str, int] = {}

    def _cell_name(cx: int, cy: int) -> str:
        tile = tiles.by_id.get(int(grid[cy, cx]))
        return tile.name.upper() if tile else str(int(grid[cy, cx]))

    def _footprint_problem(
        eid: str, x: int, y: int, archetype: str, eff: float,
        swim_style: str = "",
        hazard_immune: bool = False,
    ) -> str | None:
        """None if a body of effective size ``eff`` fits anchored at
        (x, y) — anchor row cells per column, clearance rows above.
        Messages name the LOCATED failing cell. ``hazard_immune`` (a
        variant behavior flag, combat arc) lets a LAND anchor sit ON a
        supported hazard cell — standing on the spikes."""
        cols, rows = occupancy(eff)
        cell = (x, y)
        is_swimmer = archetype == "swimmer"
        is_surface = is_swimmer and swim_style == "surface"
        policy = rules.enemy_water_policy
        if archetype == "flyer":
            # Airborne anchor: whole body in open air, a clear cell directly
            # below (aloft, not a ground stand), solid terrain somewhere
            # below the column (airspace over ground). Mirrors
            # flyer_spot_exists; the parity test cross-checks them.
            for cx in range(x, x + cols):
                if not (0 <= cx < width and 0 <= y < height):
                    return (
                        f"{eid} at {cell} (size {eff:g}) does not fit: column "
                        f"{cx} is outside the {width}x{grid.shape[0]} level."
                    )
                for cy in range(y, y - rows, -1):
                    if cy < 0:
                        # Body runs off the TOP — report that, never a clamped
                        # row-0 cell (the old max(cy,0) produced the
                        # self-contradictory "cell (x,0) is EMPTY, not empty").
                        return (
                            f"{eid} is a FLYER but its body needs {rows} row(s) "
                            f"and column {cx} runs off the TOP of the level — "
                            "place it lower so the whole body fits in airspace."
                        )
                    if int(grid[cy, cx]) != empty_id:
                        return (
                            f"{eid} is a FLYER — its body hovers in open air, but "
                            f"cell ({cx}, {cy}) is {_cell_name(cx, cy)}, "
                            "not empty. Place it in clear airspace."
                        )
                if y + 1 >= height or int(grid[y + 1, cx]) != empty_id:
                    return (
                        f"{eid} is a FLYER and must be AIRBORNE, but ({cx}, {y + 1}) "
                        "directly below it is not open air — lift it off the ground "
                        "into the airspace above."
                    )
            if not any(
                int(grid[yy, cx]) in support
                for cx in range(x, x + cols)
                for yy in range(y + 1, height)
            ):
                return (
                    f"{eid} is a FLYER, but {cell} has no ground anywhere below it "
                    "(open sky / bottomless gap) — place it in airspace over solid "
                    "terrain the player can reach."
                )
            return None
        if is_surface:
            # Surface-riders anchor ON the water's top row (open above).
            for cx in range(x, x + cols):
                if not (0 <= cx < width and 0 <= y < height):
                    return (
                        f"{eid} at {cell} (size {eff:g}) does not fit: "
                        f"column {cx} is outside the level."
                    )
                if (cx, y) not in volume or (cx, y - 1) in volume:
                    return (
                        f"{eid} is a SURFACE swimmer — it rides the top row "
                        f"of the water, but ({cx}, {y}) is not a water "
                        "surface cell (water with open air above). Place it "
                        "on the water's top row."
                    )
                for cy in range(y - 1, y - rows, -1):
                    if cy < 0 or int(grid[cy, cx]) != empty_id:
                        return (
                            f"{eid} at {cell} (size {eff:g}) rides the "
                            f"surface and needs open air above, but cell "
                            f"({cx}, {max(cy, 0)}) blocks it."
                        )
            return None
        if is_swimmer and swim_style == "float" and policy != "forbidden":
            # Floaters drift diagonally — they need a 2x2 water pocket
            # around the anchor, plus the normal full-body-in-water rule
            # below.
            has_pocket = any(
                all(
                    (px_, py_) in volume
                    for px_ in (bx, bx + 1)
                    for py_ in (by, by + 1)
                )
                for bx in (x - 1, x)
                for by in (y - 1, y)
            )
            if not has_pocket:
                return (
                    f"{eid} is a FLOATING swimmer — it drifts diagonally "
                    f"and needs a 2x2 pocket of water around {cell}, but "
                    "the water there is too shallow or narrow. Place it in "
                    "a deeper, wider body of water."
                )
        big = f" Its size-{eff:g} body spans columns {x}-{x + cols - 1}" \
              f" and {rows} row(s) up." if (cols, rows) != (1, 1) else ""
        for cx in range(x, x + cols):
            if not (0 <= cx < width and 0 <= y < height):
                return (
                    f"{eid} at {cell} (size {eff:g}) does not fit: column "
                    f"{cx} is outside the {width}x{grid.shape[0]} level."
                )
            base = (cx, y)
            if policy == "forbidden" and base in volume:
                return (
                    f"{eid} at {cell} is in water and this game forbids "
                    "enemies in water — place it on land." + big
                )
            if policy in ("swimmers_only", "seabed") and is_swimmer:
                if base not in volume:
                    where = (
                        ""
                        if base == cell
                        else f" (cell ({cx}, {y}) of its body)"
                    )
                    return (
                        f"{eid} is a swimmer and {cell} is not a water "
                        f"cell{where} — swimmers must be placed inside "
                        "water, with room for their whole body." + big
                    )
            elif policy in ("swimmers_only", "forbidden", "seabed"):
                # "seabed" (water arc): a land enemy may be POSTED on an
                # underwater flat — a volume cell with solid support
                # below. Dry standable cells stay valid everywhere.
                # HOPPERS are exempt from wading (combat arc): a hop is a
                # dry-land arc — underwater they'd pogo in place against
                # the volume-blocked drift.
                if (
                    base not in stand
                    and not (
                        policy == "seabed"
                        and base in wet_stand
                        and archetype != "hopper"
                    )
                    and not (hazard_immune and base in hazard_stand)
                ):
                    hint = (
                        (
                            " (that water has no solid seabed below — "
                            "wading enemies stand on submerged ground)"
                            if policy == "seabed"
                            else " (that's a water cell — only swimmers "
                            "go in water)"
                        )
                        if base in volume
                        else ""
                    )
                    where = (
                        ""
                        if base == cell
                        else f" — its size needs column {cx} standable too,"
                        f" and ({cx}, {y}) is {_cell_name(cx, y)}"
                    )
                    return (
                        f"{eid} at {cell} is not a standable cell{hint}"
                        f"{where} — land enemies need solid ground below "
                        "and a free cell to occupy." + big
                    )
            elif (
                base not in stand
                and base not in volume
                and not (hazard_immune and base in hazard_stand)
            ):
                return (
                    f"{eid} at {cell} is neither standable nor in water."
                    + big
                )
            # Clearance rows above the anchor row, this column.
            for cy in range(y - 1, y - rows, -1):
                if cy < 0:
                    return (
                        f"{eid} at {cell} (size {eff:g}) needs {rows} rows "
                        f"of clearance and column {cx} runs off the top of "
                        "the level — place it lower."
                    )
                val = int(grid[cy, cx])
                if policy == "amphibious":
                    clear = val == empty_id or (cx, cy) in volume
                elif is_swimmer and policy in ("swimmers_only", "seabed"):
                    clear = (cx, cy) in volume
                elif policy == "seabed" and (cx, y) in volume:
                    # A WADING land enemy's body rows sit in the water
                    # above its submerged flat — water counts as clear
                    # for it (dry placements keep the strict rule).
                    clear = val == empty_id or (cx, cy) in volume
                else:
                    clear = val == empty_id
                if not clear:
                    need = (
                        "water"
                        if is_swimmer and policy in ("swimmers_only", "seabed")
                        else "open air"
                    )
                    return (
                        f"{eid} at {cell} (size {eff:g}) needs {need} for "
                        f"{rows} rows, but cell ({cx}, {cy}) is "
                        f"{_cell_name(cx, cy)} — that located cell blocks "
                        "its body; move it to more open ground." + big
                    )
        return None

    for p in placements:
        eid, x, y = p.get("enemy_id"), p.get("x"), p.get("y")
        if eid not in enemies:
            problems.append(
                f"unknown enemy_id {eid!r}; roster: {sorted(enemies)!r}."
            )
            continue
        if not isinstance(x, int) or not isinstance(y, int):
            problems.append(f"{eid} placement needs integer x and y; got {p!r}.")
            continue
        cell = (x, y)
        archetype = str(enemies[eid].get("archetype", ""))
        swim_style = str(enemies[eid].get("swim_style", "") or "")
        variant = str(p.get("variant") or "")
        if not variant and p.get("elite"):
            variant = "elite"  # pre-3b spelling rides through
        if variant and variant not in variants.by_name:
            problems.append(
                f"{eid} at {cell} names unknown variant {variant!r}; "
                f"this game's variants: {sorted(variants.by_name)!r}."
            )
            continue
        eff = effective_size(
            float(enemies[eid].get("size", 1.0) or 1.0),
            variants.by_name[variant].size if variant else 1.0,
        )
        hazard_immune = bool(
            variants.by_name[variant].behavior.get("hazard_immune")
            if variant
            else False
        )
        issue = _footprint_problem(
            eid, x, y, archetype, eff, swim_style, hazard_immune
        )
        if issue is not None:
            # Wader snap (water arc, code-not-LLM): a LAND enemy the model
            # dropped into deep water under the seabed policy is relocated to
            # the nearest submerged-ground flat that seats its whole body,
            # rather than bounced back for a retry (mirrors the spawn-safety
            # nudge). Tightly gated — swimmers/flyers/hoppers, dry-land
            # misplacements, and waders with no seabed within reach all still
            # route to LLM feedback unchanged. (postmortem ticket 5a)
            snap = None
            if (
                rules.enemy_water_policy == "seabed"
                and archetype not in ("swimmer", "flyer", "hopper")
                and (x, y) in volume
            ):
                snap = min(
                    (
                        c
                        for c in wet_stand
                        if abs(c[0] - x) + abs(c[1] - y) <= _WADER_SNAP_RADIUS
                        and _footprint_problem(
                            eid, c[0], c[1], archetype, eff, swim_style,
                            hazard_immune,
                        )
                        is None
                    ),
                    key=lambda c: (abs(c[0] - x) + abs(c[1] - y), c),
                    default=None,
                )
            if snap is None:
                problems.append(issue)
                continue
            repairs.append(
                f"{eid}: ({x}, {y}) -> {snap} — wader snapped to the nearest "
                "submerged-ground flat (the model posted it in deep water; "
                "the seabed cell is arithmetic)."
            )
            x, y = snap
        rarity = str(enemies[eid].get("rarity", "") or "")
        rarity_cap = getattr(rules, "rarity_caps", {}).get(rarity)
        if rarity_cap is not None and rarity_counts.get(rarity, 0) >= rarity_cap:
            problems.append(
                f"{eid} at {cell}: at most {rarity_cap} {rarity!r} "
                "enemy placement(s) allowed per level (rarity cap) — "
                "pick a more common creature for this spot."
            )
            continue
        if rarity:
            rarity_counts[rarity] = rarity_counts.get(rarity, 0) + 1
        radius = combat.spawn_safety_columns
        if y == spawn[1] and abs(x - spawn[0]) <= radius:
            # Column nudge outward on the side the agent chose, falling
            # back to the other side — the first column where the WHOLE
            # body fits outside the radius wins.
            direction = 1 if x >= spawn[0] else -1
            nudged = next(
                (
                    nx
                    for d in range(1, MAX_PLACEMENT_NUDGE + 1)
                    for nx in (x + direction * d, x - direction * d)
                    if 0 <= nx < width
                    and abs(nx - spawn[0]) > radius
                    and _footprint_problem(
                        eid, nx, y, archetype, eff, swim_style, hazard_immune
                    )
                    is None
                ),
                None,
            )
            if nudged is None:
                problems.append(
                    f"{eid} at {cell} is within {radius} columns of the "
                    f"player spawn {spawn} and no clear column within "
                    f"{MAX_PLACEMENT_NUDGE} fits its size-{eff:g} body — "
                    "place it further from spawn."
                )
                continue
            repairs.append(
                f"{eid}: ({x}, {y}) -> ({nudged}, {y}) — spawn-safety "
                f"nudge (enemies keep {radius} columns clear of spawn; "
                "the spot was the agent's, the column is arithmetic)."
            )
            x = nudged
        if variant:
            cap = rules.variant_caps.get(variant)
            if cap is not None and variant_counts.get(variant, 0) >= cap:
                problems.append(
                    f"{eid} at {cell}: at most {cap} {variant!r} "
                    "placement(s) allowed per level — drop the variant "
                    "marker or pick a different enemy."
                )
                continue
            variant_counts[variant] = variant_counts.get(variant, 0) + 1
        accepted.append(
            {"enemy_id": eid, "x": x, "y": y, "variant": variant}
        )
    return accepted, problems, repairs


# ---------------------------------------------------------------------------
# Item placement validation (Arc 2) — code-not-LLM: computable geometry is
# snapped/dropped as repairs; only unknown ids/malformed records go back to
# the model as feedback.
# ---------------------------------------------------------------------------

#: How far the snap search wanders when an item cell is occupied or
#: uncollectible (columns/rows, nearest-first).
_ITEM_SNAP_RADIUS = 2


def _item_collectible(
    cell: tuple[int, int],
    reached: set[tuple[int, int]],
    reached_body: set[tuple[int, int]],
    jump_height: int,
) -> bool:
    """Fail-closed base-moveset collectibility: the cell IS a reached
    stand/swim position (walked or swum through), or it hangs within the
    vertical jump envelope directly above a reached BODY-standable
    foothold (the anchor passes through it on a jump). Never trusts
    reward anchors — they were layout-authored without reachability."""
    x, y = cell
    if cell in reached:
        return True
    return any(
        (x, y + h) in reached_body for h in range(1, jump_height + 1)
    )


def _box_bumpable(
    cell: tuple[int, int],
    reached_body: set[tuple[int, int]],
    jump_height: int,
) -> bool:
    """A box must be openable from BELOW with the base moveset: a reached
    body-standable foothold in the same column, 2..jump_height+1 rows
    beneath it (standing directly under a box is a 1-tall pocket the
    body-stand filter already excludes)."""
    x, y = cell
    return any(
        (x, y + h) in reached_body for h in range(2, jump_height + 2)
    )


def check_item_placements(
    grid,
    placements: list[dict],
    spawn: tuple[int, int],
    item_defs: dict[str, dict],
    movement: PlayerMovementSpec,
    rules: GameRules = DEFAULT_RULES,
    tiles: TileRegistry = DEFAULT_TILES,
) -> tuple[list[dict], list[str], list[str]]:
    """Validate an item-placement attempt. Returns (accepted, problems,
    repairs): ``problems`` are retry feedback (unknown ids, malformed
    records); everything computable — occupied cells, uncollectible
    spots, rarity overflow — is REPAIRED (snapped to the nearest valid
    cell or dropped, logged) per the G7 doctrine. The caller stamps BOX
    tiles onto a grid copy afterward and re-checks beatability."""
    height, width = grid.shape
    empty_id = tiles.by_name["empty"].id if "empty" in tiles.by_name else 0
    stand = standable_cells(grid, tiles)
    reached = reachable_cells(grid, spawn, movement, tiles)
    reached_body = reached & _body_stand(grid, stand, tiles)
    jump_h = int(movement.jump_height)

    volumes = tiles.ids("volume")

    def _open(x: int, y: int) -> bool:
        return (
            0 <= x < width and 0 <= y < height
            and int(grid[y, x]) == empty_id
        )

    def _in_water(x: int, y: int) -> bool:
        return (
            0 <= x < width and 0 <= y < height
            and int(grid[y, x]) in volumes
        )

    def _valid(cell: tuple[int, int], source: str) -> bool:
        if source == "box":
            # Boxes stay in open air (a box overlays a SOLID tile — a
            # submerged container is out of scope for water v1).
            return _open(*cell) and _box_bumpable(
                cell, reached_body, jump_h
            )
        if _open(*cell):
            return _item_collectible(cell, reached, reached_body, jump_h)
        # WATER (water arc): an item may FLOAT in a volume cell — the
        # player swims through it, so collectible = swim-reached.
        if _in_water(*cell):
            return cell in reached
        return False

    def _snap(cell: tuple[int, int], source: str) -> tuple[int, int] | None:
        candidates = sorted(
            (
                (abs(dx) + abs(dy), (cell[0] + dx, cell[1] + dy))
                for dx in range(-_ITEM_SNAP_RADIUS, _ITEM_SNAP_RADIUS + 1)
                for dy in range(-_ITEM_SNAP_RADIUS, _ITEM_SNAP_RADIUS + 1)
                if dx or dy
            ),
        )
        for _, cand in candidates:
            if _valid(cand, source):
                return cand
        return None

    accepted: list[dict] = []
    problems: list[str] = []
    repairs: list[str] = []
    rarity_counts: dict[str, int] = {}
    taken: set[tuple[int, int]] = set()

    for p in placements:
        item_id = str(p.get("item_id", ""))
        definition = item_defs.get(item_id)
        if definition is None:
            problems.append(
                f"unknown item {item_id!r} — place only ids from the pool: "
                f"{sorted(item_defs)}"
            )
            continue
        try:
            x, y = int(p["x"]), int(p["y"])
        except (KeyError, TypeError, ValueError):
            problems.append(
                f"{item_id}: x and y must be integer grid coordinates."
            )
            continue
        source = str(p.get("source", "trail") or "trail")
        if source not in ("trail", "reward", "box"):
            source = "trail"

        rarity = str(definition.get("rarity", "common"))
        cap = rules.rarity_caps.get(rarity)
        if cap is not None and rarity_counts.get(rarity, 0) >= cap:
            repairs.append(
                f"{item_id} at ({x},{y}): over the {rarity!r} cap ({cap} "
                "per level) — dropped."
            )
            continue

        cell = (x, y)
        if cell in taken or not _valid(cell, source):
            snapped = None if cell in taken else _snap(cell, source)
            if snapped is None or snapped in taken:
                what = (
                    "not openable from below"
                    if source == "box"
                    else "not collectible with the base moveset"
                )
                repairs.append(
                    f"{item_id} at ({x},{y}): occupied or {what}; no valid "
                    "cell nearby — dropped."
                )
                continue
            repairs.append(
                f"{item_id} at ({x},{y}): snapped to {snapped} "
                f"({'openable' if source == 'box' else 'collectible'})."
            )
            cell = snapped
        taken.add(cell)
        rarity_counts[rarity] = rarity_counts.get(rarity, 0) + 1
        accepted.append(
            {"item_id": item_id, "x": cell[0], "y": cell[1], "source": source}
        )
    return accepted, problems, repairs
