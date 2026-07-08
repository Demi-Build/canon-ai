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
            if jump_ok(abs(nx - cx), cy - ny, movement):
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
                # Enter a volume: walk/fall in — same arc rule.
                if jump_ok(abs(nx - cx), cy - ny, movement):
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
) -> list[str]:
    """Return problem strings (empty = valid). Messages are written to be
    fed back to the Layout Agent verbatim. ``triggers`` (checkpoints) are
    validated like spawn/exit: standable and reachable."""
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
        problems.extend(check_volume_containment(grid, tiles))
    if not problems:
        reached = reachable_cells(grid, spawn, movement, tiles)
        if exit_ not in reached:
            problems.append(
                _describe_reachability_break(
                    grid, spawn, exit_, movement, stand, reached, "exit"
                )
            )
        for cell in checkpoints:
            if cell not in reached:
                problems.append(
                    _describe_reachability_break(
                        grid, spawn, cell, movement, stand, reached,
                        "checkpoint",
                    )
                )
    return problems


def check_volume_containment(
    grid, tiles: TileRegistry = DEFAULT_TILES
) -> list[str]:
    """Under the 'contained' rule, every volume cell's sides must be held
    by solid tiles, more volume, or the level edge — pools sit in basins,
    they don't spill sideways. ('free' games skip this — waterfalls.)
    One-way platforms don't hold liquid; any volume cell continues a pool."""
    height, width = grid.shape
    holds = tiles.ids("solid") | tiles.ids("volume")
    problems: list[str] = []
    for x, y in sorted(volume_cells(grid, tiles)):
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
        return min(abs(col - fx), abs(col + 1 - fx)) <= reach

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
    bridge = _suggest_bridge(grid, frontier, nearest, movement)
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
        op = _suggest_bridge(result.grid, frontier, nearest, movement)
        if op is None or op in added:
            # No valid bridge exists, or the last one changed nothing —
            # give the problems to the agent instead of burning the bound
            # on a dead op (the first real run's l1/l3 failure mode).
            return text, added, problems
        text = f"{text}\n# auto-bridge\n{op}"
        added.append(op)


def check_placements(
    grid,
    placements: list[dict],
    spawn: tuple[int, int],
    archetypes: dict[str, str],
    rules: GameRules = DEFAULT_RULES,
    tiles: TileRegistry = DEFAULT_TILES,
    variants: VariantSet = DEFAULT_VARIANTS,
) -> tuple[list[dict], list[str]]:
    """Split proposed placements into (accepted, problem strings).

    Rules: known enemy id; the game's ``enemy_water_policy`` decides who
    may occupy volume cells; not within 3 columns of spawn on the spawn
    row (no spawn-camping). ``variant`` names must come from the game's
    variant vocabulary and respect ``GameRules.variant_caps`` (per level).
    A legacy boolean ``elite`` maps to ``variant: "elite"``.
    """
    stand = standable_cells(grid, tiles)
    volume = volume_cells(grid, tiles)
    accepted: list[dict] = []
    problems: list[str] = []
    variant_counts: dict[str, int] = {}
    for p in placements:
        eid, x, y = p.get("enemy_id"), p.get("x"), p.get("y")
        if eid not in archetypes:
            problems.append(
                f"unknown enemy_id {eid!r}; roster: {sorted(archetypes)!r}."
            )
            continue
        if not isinstance(x, int) or not isinstance(y, int):
            problems.append(f"{eid} placement needs integer x and y; got {p!r}.")
            continue
        cell = (x, y)
        is_swimmer = archetypes[eid] == "swimmer"
        if rules.enemy_water_policy == "forbidden" and cell in volume:
            problems.append(
                f"{eid} at {cell} is in water and this game forbids enemies "
                "in water — place it on land."
            )
            continue
        if rules.enemy_water_policy == "swimmers_only":
            if is_swimmer and cell not in volume:
                problems.append(
                    f"{eid} is a swimmer and {cell} is not a water cell — "
                    "swimmers must be placed inside water."
                )
                continue
            if not is_swimmer and cell not in stand:
                hint = (
                    " (that's a water cell — only swimmers go in water)"
                    if cell in volume
                    else ""
                )
                problems.append(
                    f"{eid} at {cell} is not a standable cell{hint} — land "
                    "enemies need solid ground below and a free cell to occupy."
                )
                continue
        elif cell not in stand and cell not in volume:
            problems.append(
                f"{eid} at {cell} is neither standable nor in water."
            )
            continue
        if y == spawn[1] and abs(x - spawn[0]) <= 3:
            problems.append(f"{eid} at {cell} is too close to spawn {spawn}.")
            continue
        variant = str(p.get("variant") or "")
        if not variant and p.get("elite"):
            variant = "elite"  # pre-3b spelling rides through
        if variant:
            if variant not in variants.by_name:
                problems.append(
                    f"{eid} at {cell} names unknown variant {variant!r}; "
                    f"this game's variants: {sorted(variants.by_name)!r}."
                )
                continue
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
    return accepted, problems
