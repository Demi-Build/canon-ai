"""Slice validators (Validator nodes, PRD §5.0): pure functions over the
stamped grid. Kick back to the agent on fail — callers turn the returned
problem strings into retry feedback.

Since 3b every tile judgment resolves through the game's TILE REGISTRY
(categories in code, values in data): solids/one-ways support standing,
volume tiles (water, lava, mud) are swimmable, hazards are neither.

Reachability here is the *lite* version: BFS over traversable cells —
standable cells connected by the jump rule (dx <= jump_width, rise <=
jump_height, drops unlimited), plus volume cells (Appendix E.1: vertical
movement is free inside a volume; the jump rule applies when exiting it).
Full A* + jump-arc physics is a later 3b-phase item.
"""

from __future__ import annotations

from collections import deque

from canon.bible.platformer import SparseMaskEntry
from examples.platformer_pack.combat import (
    DEFAULT_COMBAT,
    CombatSpec,
    effective_size,
    occupancy,
)
from examples.platformer_pack.movement import PlayerMovementSpec, max_dx_for_rise
from examples.platformer_pack.rules import DEFAULT_RULES, GameRules
from examples.platformer_pack.tiles import DEFAULT_TILES, TileRegistry
from examples.platformer_pack.variants import DEFAULT_VARIANTS, VariantSet


def standable_cells(grid, tiles: TileRegistry = DEFAULT_TILES) -> set[tuple[int, int]]:
    """Cells a body can stand in: empty with solid/one-way support below."""
    height, width = grid.shape
    empty = tiles.empty_id
    support = tiles.ids("solid", "one_way")
    out: set[tuple[int, int]] = set()
    for y in range(height - 1):
        for x in range(width):
            if int(grid[y, x]) == empty and int(grid[y + 1, x]) in support:
                out.add((x, y))
    return out


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


def jump_ok(dx: int, rise: int, movement: PlayerMovementSpec) -> bool:
    """The arc-aware jump rule shared by reachability, feedback, and the
    bridge tool: rise caps height, and RISING COSTS HORIZONTAL RANGE
    (max_dx_for_rise) — the box rule approved unmakeable diagonals."""
    if rise > movement.jump_height:
        return False
    if dx > movement.jump_width:
        return False
    return rise <= 0 or dx <= max_dx_for_rise(movement, rise)


def arc_clear(
    grid,
    src: tuple[int, int],
    dst: tuple[int, int],
    movement: PlayerMovementSpec,
    tiles: TileRegistry = DEFAULT_TILES,
) -> bool:
    """Conservative jump-arc clearance: can the player travel ``src`` ->
    ``dst`` without a SOLID column in between rising into its flight path?

    ``jump_ok`` only checks the two endpoints' dx/rise, so a jump that
    clears the envelope but flies THROUGH a cliff was wrongly called
    reachable — the first paid run shipped an unbeatable level whose exit
    was 'reachable' only via ``(35,12)->(38,9)``, a jump straight into a
    5-cell wall. This is the code-side guard: for every column strictly
    between the footholds, reject if any solid tile sits in the band from
    the highest point the feet can reach (apex = the higher foothold minus
    ``jump_height``) down to just above the LOWER foothold. Ground at or
    below the lower foothold never blocks (the player is above it as it
    travels); a protrusion into that band does. One-way platforms
    (pass-through), hazards (flown over), and volumes (swum through) are
    not blockers — only ``solid``.

    Conservative by design (playability batch): it may reject a jump a
    perfect arc could thread, which merely asks ``auto_bridge`` for a
    stepping platform — never the reverse (accepting an impossible jump).
    Per-column parabola sampling is the deferred 'A* + jump-arc' item.
    """
    sx, sy = src
    dx, dy = dst
    if sx == dx:
        return True  # vertical move (swim / volume entry): no columns between
    solids = tiles.ids("solid")
    top = min(sy, dy) - movement.jump_height  # highest the feet can reach
    bottom = max(sy, dy) - 1  # just above the lower foothold; ground is free
    if bottom < top:
        return True
    top = max(0, top)
    step = 1 if dx > sx else -1
    for x in range(sx + step, dx, step):
        for row in range(top, bottom + 1):
            if int(grid[row, x]) in solids:
                return False
    return True


def reachable_cells(
    grid,
    start: tuple[int, int],
    movement: PlayerMovementSpec,
    tiles: TileRegistry = DEFAULT_TILES,
) -> set[tuple[int, int]]:
    """BFS over traversable cells: stand->stand by the arc-aware jump
    rule; volume->volume 4-adjacent (swimming is free movement);
    stand->volume by entering/falling in; and volume->stand by the jump
    rule from the volume cell (surface exit)."""
    stand = standable_cells(grid, tiles)
    volume = volume_cells(grid, tiles)
    if start not in stand and start not in volume:
        return set()
    seen = {start}
    queue = deque([start])
    while queue:
        cx, cy = queue.popleft()
        in_volume = (cx, cy) in volume
        for nx, ny in stand:
            if (nx, ny) in seen:
                continue
            if jump_ok(abs(nx - cx), cy - ny, movement) and arc_clear(
                grid, (cx, cy), (nx, ny), movement, tiles
            ):
                seen.add((nx, ny))
                queue.append((nx, ny))
        for nx, ny in volume:
            if (nx, ny) in seen:
                continue
            if in_volume:
                if abs(nx - cx) + abs(ny - cy) == 1:  # swim: 4-adjacent
                    seen.add((nx, ny))
                    queue.append((nx, ny))
            else:
                # Enter a volume: walk/fall in — same arc rule + clearance.
                if jump_ok(abs(nx - cx), cy - ny, movement) and arc_clear(
                    grid, (cx, cy), (nx, ny), movement, tiles
                ):
                    seen.add((nx, ny))
                    queue.append((nx, ny))
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


def _locate_break(
    stand: set[tuple[int, int]], reached: set[tuple[int, int]]
) -> tuple[tuple[int, int], tuple[int, int] | None]:
    """(frontier, nearest unreachable foothold) of a reachability break."""
    frontier = max(reached, key=lambda c: (c[0], -c[1]))
    unreached = stand - reached
    if not unreached:
        return frontier, None
    nearest = min(
        unreached, key=lambda c: abs(c[0] - frontier[0]) + abs(c[1] - frontier[1])
    )
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
    it must be arc-jumpable from the frontier. The first real run's
    fallback levels traced to the old blind formula: with a foothold
    directly above the frontier (dx=0, rise 4) it proposed a platform
    whose standing cell was the foothold's own supporting solid — a
    structurally dead op that auto_bridge re-emitted until its bound.
    Returns None when no valid bridge exists (a design problem)."""
    height, width = grid.shape
    fx, fy = frontier
    nx, ny = nearest

    def _free(x: int, y: int) -> bool:
        return 0 <= x < width and 0 <= y < height and int(grid[y, x]) == 0

    def _ok(col: int, row: int) -> bool:
        if not (1 <= row <= height - 3) or not (0 <= col <= width - 2):
            return False
        # Platform cells and the cells stood in above them: open air.
        if not all(_free(col + i, row) and _free(col + i, row - 1) for i in range(2)):
            return False
        stand_rise = fy - (row - 1)
        if stand_rise > movement.jump_height:
            return False
        # Reachable from the frontier under the arc rule (falling is free).
        reach = max(1, max_dx_for_rise(movement, max(stand_rise, 0)))
        if min(abs(col - fx), abs(col + 1 - fx)) > reach:
            return False
        # And the landing cell must actually be arc-reachable — a platform
        # tucked behind a wall is a dead op auto_bridge would re-emit to
        # its bound. Check the nearer of the two standing cells.
        land = (col, row - 1) if abs(col - fx) <= abs(col + 1 - fx) else (col + 1, row - 1)
        return arc_clear(grid, frontier, land, movement, tiles)

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
    frontier, nearest = _locate_break(stand, reached)
    if nearest is None:  # pragma: no cover — target is standable, so nonempty
        return f"{label} at {target} is not reachable from spawn {spawn}."
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
            f"passage or lower the ledge) so a path can exist."
        )
    return (
        f"{base} Fix: ADD THIS ONE LINE to your layout, "
        f"changing nothing else: {bridge} — it is jumpable from "
        f"{frontier} and bridges toward {nearest}. "
        f"Remember: a flat gap wider than {movement.jump_width - 1} columns "
        "is impossible to cross without a stepping platform."
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
        frontier, nearest = _locate_break(stand, reached)
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


#: How far a spawn-crowding placement may be column-nudged to the nearest
#: valid cell — WHERE an enemy roughly goes is design; the exact clear
#: column is arithmetic (the snap_checkpoints precedent).
MAX_PLACEMENT_NUDGE = 8


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
    ) -> str | None:
        """None if a body of effective size ``eff`` fits anchored at
        (x, y) — anchor row cells per column, clearance rows above.
        Messages name the LOCATED failing cell."""
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
                    if cy < 0 or int(grid[cy, cx]) != empty_id:
                        return (
                            f"{eid} is a FLYER — its body hovers in open air, but "
                            f"cell ({cx}, {max(cy, 0)}) is {_cell_name(cx, max(cy, 0))}, "
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
            if policy == "swimmers_only" and is_swimmer:
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
            elif policy in ("swimmers_only", "forbidden"):
                if base not in stand:
                    hint = (
                        " (that's a water cell — only swimmers go in water)"
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
            elif base not in stand and base not in volume:
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
                elif is_swimmer and policy == "swimmers_only":
                    clear = (cx, cy) in volume
                else:
                    clear = val == empty_id
                if not clear:
                    need = (
                        "water"
                        if is_swimmer and policy == "swimmers_only"
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
        issue = _footprint_problem(eid, x, y, archetype, eff, swim_style)
        if issue is not None:
            problems.append(issue)
            continue
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
                    and _footprint_problem(eid, nx, y, archetype, eff, swim_style)
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
