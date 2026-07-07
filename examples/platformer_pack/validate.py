"""Slice validators (Validator nodes, PRD §5.0): pure functions over the
stamped grid. Kick back to the agent on fail — callers turn the returned
problem strings into retry feedback.

Reachability here is the *lite* version: BFS over traversable cells —
standable cells connected by the jump rule (dx <= jump_width, rise <=
jump_height, drops unlimited), plus water cells (Appendix E.1: vertical
movement is free inside water; the jump rule applies when exiting it).
Full A* + jump-arc physics is Phase 3b.
"""

from __future__ import annotations

from collections import deque

from canon.bible.platformer import TileType
from examples.platformer_pack.movement import PlayerMovementSpec
from examples.platformer_pack.rules import DEFAULT_RULES, GameRules

_SOLID = {int(TileType.FLOOR), int(TileType.PLATFORM), int(TileType.WALL)}
#: Side containment for water: solid walls (one-way platforms don't hold
#: water; more water continues the pool).
_HOLDS_WATER = {int(TileType.FLOOR), int(TileType.WALL), int(TileType.WATER)}


def standable_cells(grid) -> set[tuple[int, int]]:
    """Cells a body can stand in: empty (non-spike) with solid support below."""
    height, width = grid.shape
    out: set[tuple[int, int]] = set()
    for y in range(height - 1):
        for x in range(width):
            if int(grid[y, x]) == TileType.EMPTY and int(grid[y + 1, x]) in _SOLID:
                out.add((x, y))
    return out


def water_cells(grid) -> set[tuple[int, int]]:
    height, width = grid.shape
    return {
        (x, y)
        for y in range(height)
        for x in range(width)
        if int(grid[y, x]) == TileType.WATER
    }


def reachable_cells(
    grid, start: tuple[int, int], movement: PlayerMovementSpec
) -> set[tuple[int, int]]:
    """BFS over traversable cells: stand->stand by the jump rule;
    water->water 4-adjacent (swimming is free movement); stand->water by
    entering/falling in (horizontal within jump_width, any drop); and
    water->stand by the jump rule from the water cell (surface exit)."""
    stand = standable_cells(grid)
    water = water_cells(grid)
    if start not in stand and start not in water:
        return set()
    seen = {start}
    queue = deque([start])
    while queue:
        cx, cy = queue.popleft()
        in_water = (cx, cy) in water
        for nx, ny in stand:
            if (nx, ny) in seen:
                continue
            dx = abs(nx - cx)
            rise = cy - ny  # positive = jumping up
            if dx <= movement.jump_width and rise <= movement.jump_height:
                seen.add((nx, ny))
                queue.append((nx, ny))
        for nx, ny in water:
            if (nx, ny) in seen:
                continue
            if in_water:
                if abs(nx - cx) + abs(ny - cy) == 1:  # swim: 4-adjacent
                    seen.add((nx, ny))
                    queue.append((nx, ny))
            else:
                # Enter water: walk/fall in — horizontal within jump reach,
                # drops unlimited (ny >= cy) or a small hop up.
                dx = abs(nx - cx)
                rise = cy - ny
                if dx <= movement.jump_width and rise <= movement.jump_height:
                    seen.add((nx, ny))
                    queue.append((nx, ny))
    return seen


def _tile_name(value: int) -> str:
    try:
        return TileType(int(value)).name
    except ValueError:
        return str(value)


def _diagnose_unstandable(grid, cell: tuple[int, int], label: str) -> str:
    """Say WHY a cell isn't standable — 'not standable' alone sends the
    Layout Agent in circles (observed: a platform stamped over spawn drew
    three identical retries)."""
    x, y = cell
    height = grid.shape[0]
    occupant = int(grid[y, x])
    if occupant != TileType.EMPTY:
        return (
            f"{label} at {cell} is covered by a {_tile_name(occupant)} tile — "
            f"nothing may occupy the {label} cell; move that "
            f"{_tile_name(occupant).lower()} or move the {label}."
        )
    below = int(grid[y + 1, x]) if y + 1 < height else int(TileType.EMPTY)
    return (
        f"{label} at {cell} has no solid ground beneath it (the cell below "
        f"is {_tile_name(below)}) — keep floor under the {label} column."
    )


def check_level(
    grid,
    spawn: tuple[int, int],
    exit_: tuple[int, int],
    movement: PlayerMovementSpec,
    rules: GameRules = DEFAULT_RULES,
) -> list[str]:
    """Return problem strings (empty = valid). Messages are written to be
    fed back to the Layout Agent verbatim."""
    problems: list[str] = []
    stand = standable_cells(grid)
    if spawn not in stand:
        problems.append(_diagnose_unstandable(grid, spawn, "spawn"))
    if exit_ not in stand:
        problems.append(_diagnose_unstandable(grid, exit_, "exit"))
    if rules.water_containment == "contained":
        problems.extend(check_water_containment(grid))
    if not problems:
        reached = reachable_cells(grid, spawn, movement)
        if exit_ not in reached:
            problems.append(
                _describe_reachability_break(spawn, exit_, movement, stand, reached)
            )
    return problems


def check_water_containment(grid) -> list[str]:
    """Under the 'contained' rule, every water cell's sides must be held
    by solid tiles, more water, or the level edge — pools sit in basins,
    they don't spill sideways. ('free' games skip this — waterfalls.)"""
    height, width = grid.shape
    problems: list[str] = []
    for x, y in sorted(water_cells(grid)):
        for nx in (x - 1, x + 1):
            if nx < 0 or nx >= width:
                continue  # level edge holds water
            if int(grid[y, nx]) not in _HOLDS_WATER:
                side = "left" if nx < x else "right"
                problems.append(
                    f"water at ({x}, {y}) spills out its {side} side — "
                    f"contain the pool with wall({nx},{y},{y}) / raised "
                    "ground, or extend the water to the level edge."
                )
                break
    return problems[:3]  # a few located examples beat a wall of repeats


def _describe_reachability_break(
    spawn: tuple[int, int],
    exit_: tuple[int, int],
    movement: PlayerMovementSpec,
    stand: set[tuple[int, int]],
    reached: set[tuple[int, int]],
) -> str:
    """Locate the break, don't just report it — 'add stepping platforms'
    without a location sent the real model through identical retries into
    fallback. Name the frontier cell, the nearest unreachable foothold,
    and the exact jump constraint that fails."""
    frontier = max(reached, key=lambda c: (c[0], -c[1]))
    unreached = stand - reached
    if not unreached:  # pragma: no cover — exit is standable, so nonempty
        return f"exit at {exit_} is not reachable from spawn {spawn}."
    nearest = min(
        unreached, key=lambda c: abs(c[0] - frontier[0]) + abs(c[1] - frontier[1])
    )
    dx = abs(nearest[0] - frontier[0])
    rise = frontier[1] - nearest[1]
    constraints = []
    if dx > movement.jump_width:
        constraints.append(
            f"horizontal distance {dx} exceeds max jump distance "
            f"{movement.jump_width}"
        )
    if rise > movement.jump_height:
        constraints.append(
            f"rise {rise} exceeds max jump height {movement.jump_height}"
        )
    detail = " and ".join(constraints) or "no standable path connects them"
    return (
        f"exit at {exit_} is not reachable from spawn {spawn}. The player "
        f"gets as far as {frontier} but cannot reach the next foothold at "
        f"{nearest}: {detail}. Add a stepping platform between columns "
        f"{min(frontier[0], nearest[0])} and {max(frontier[0], nearest[0])} "
        f"(within {movement.jump_height} rows above the lower surface). "
        f"Remember: a flat gap wider than {movement.jump_width - 1} columns "
        "is impossible to cross without a platform."
    )


def check_placements(
    grid,
    placements: list[dict],
    spawn: tuple[int, int],
    archetypes: dict[str, str],
    rules: GameRules = DEFAULT_RULES,
) -> tuple[list[dict], list[str]]:
    """Split proposed placements into (accepted, problem strings).

    Rules: known enemy id; the game's ``enemy_water_policy`` decides who
    may occupy water; not within 3 columns of spawn on the spawn row (no
    spawn-camping). An optional boolean ``elite`` rides through
    (per-placement variation, §6.1 overrides).
    """
    stand = standable_cells(grid)
    water = water_cells(grid)
    accepted: list[dict] = []
    problems: list[str] = []
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
        if rules.enemy_water_policy == "forbidden" and cell in water:
            problems.append(
                f"{eid} at {cell} is in water and this game forbids enemies "
                "in water — place it on land."
            )
            continue
        if rules.enemy_water_policy == "swimmers_only":
            if is_swimmer and cell not in water:
                problems.append(
                    f"{eid} is a swimmer and {cell} is not a water cell — "
                    "swimmers must be placed inside water."
                )
                continue
            if not is_swimmer and cell not in stand:
                hint = (
                    " (that's a water cell — only swimmers go in water)"
                    if cell in water
                    else ""
                )
                problems.append(
                    f"{eid} at {cell} is not a standable cell{hint} — land "
                    "enemies need solid ground below and a free cell to occupy."
                )
                continue
        elif cell not in stand and cell not in water:
            problems.append(
                f"{eid} at {cell} is neither standable nor in water."
            )
            continue
        if y == spawn[1] and abs(x - spawn[0]) <= 3:
            problems.append(f"{eid} at {cell} is too close to spawn {spawn}.")
            continue
        accepted.append(
            {"enemy_id": eid, "x": x, "y": y, "elite": bool(p.get("elite", False))}
        )
    return accepted, problems
