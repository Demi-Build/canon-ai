"""Slice validators (Validator nodes, PRD §5.0): pure functions over the
stamped grid. Kick back to the agent on fail — callers turn the returned
problem strings into retry feedback.

Reachability here is the *lite* version: BFS over standable cells where an
edge exists if the horizontal distance <= jump_width and the rise <=
jump_height (drops are unlimited). Full A* + jump-arc physics is Phase 3.
"""

from __future__ import annotations

from collections import deque

from canon.bible.platformer import TileType
from examples.platformer_pack.movement import PlayerMovementSpec

_SOLID = {int(TileType.FLOOR), int(TileType.PLATFORM), int(TileType.WALL)}


def standable_cells(grid) -> set[tuple[int, int]]:
    """Cells a body can stand in: empty (non-spike) with solid support below."""
    height, width = grid.shape
    out: set[tuple[int, int]] = set()
    for y in range(height - 1):
        for x in range(width):
            if int(grid[y, x]) == TileType.EMPTY and int(grid[y + 1, x]) in _SOLID:
                out.add((x, y))
    return out


def reachable_cells(
    grid, start: tuple[int, int], movement: PlayerMovementSpec
) -> set[tuple[int, int]]:
    """BFS over standable cells under the lite jump model."""
    stand = standable_cells(grid)
    if start not in stand:
        return set()
    seen = {start}
    queue = deque([start])
    while queue:
        cx, cy = queue.popleft()
        for nx, ny in stand:
            if (nx, ny) in seen:
                continue
            dx = abs(nx - cx)
            rise = cy - ny  # positive = jumping up
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
) -> list[str]:
    """Return problem strings (empty = valid). Messages are written to be
    fed back to the Layout Agent verbatim."""
    problems: list[str] = []
    stand = standable_cells(grid)
    if spawn not in stand:
        problems.append(_diagnose_unstandable(grid, spawn, "spawn"))
    if exit_ not in stand:
        problems.append(_diagnose_unstandable(grid, exit_, "exit"))
    if not problems:
        reached = reachable_cells(grid, spawn, movement)
        if exit_ not in reached:
            problems.append(
                _describe_reachability_break(spawn, exit_, movement, stand, reached)
            )
    return problems


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
    valid_ids: set[str],
) -> tuple[list[dict], list[str]]:
    """Split proposed placements into (accepted, problem strings).

    Rules: known enemy id; standable cell; not a spike cell; not within 3
    columns of spawn on the spawn row (no spawn-camping).
    """
    stand = standable_cells(grid)
    accepted: list[dict] = []
    problems: list[str] = []
    for p in placements:
        eid, x, y = p.get("enemy_id"), p.get("x"), p.get("y")
        if eid not in valid_ids:
            problems.append(f"unknown enemy_id {eid!r}; roster: {sorted(valid_ids)!r}.")
            continue
        if not isinstance(x, int) or not isinstance(y, int) or (x, y) not in stand:
            problems.append(
                f"{eid} at ({x}, {y}) is not a standable cell — enemies need "
                "solid ground below and a free cell to occupy."
            )
            continue
        if y == spawn[1] and abs(x - spawn[0]) <= 3:
            problems.append(f"{eid} at ({x}, {y}) is too close to spawn {spawn}.")
            continue
        accepted.append({"enemy_id": eid, "x": x, "y": y})
    return accepted, problems
