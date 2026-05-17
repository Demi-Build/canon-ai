"""Maze generation algorithm — port of MazeWorld's recursive-backtracking algorithm.

Canon's port intentionally takes a seeded ``random.Random`` instance rather
than using ``random.*`` module-level globals.  This makes the algorithm
deterministic-with-seed for testing without relying on test-order or global
RNG state.

Key behavioral properties carried over from mazeworld:
  - Recursive backtracking (DFS) starting from ``player_start``.
  - 40% chance per direction of choosing a wider hallway; otherwise
    ``MIN_HALLWAY_SIZE`` is used.
  - Hallway-width distribution is biased toward ``MIN_HALLWAY_SIZE`` via
    descending integer weights.
  - The recursive approach can hit Python's default recursion limit on very
    large grids (e.g. 200×200). For typical mazeworld-sized grids (40×30)
    the depth stays well within Python's default 1000-frame limit, so no
    ``sys.setrecursionlimit`` call is needed.  If callers use large grids
    they should set the limit themselves before calling ``generate_maze``.
"""

from __future__ import annotations

import random as _random_module

from canon.layout import MazeLayout

# ---------------------------------------------------------------------------
# Constants — match mazeworld's values exactly
# ---------------------------------------------------------------------------

DIRECTIONS: list[tuple[int, int]] = [(0, 1), (1, 0), (0, -1), (-1, 0)]
MIN_HALLWAY_SIZE: int = 1
MAX_HALLWAY_SIZE: int = 3
WALL: int = 1
PATH: int = 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_maze(
    width: int,
    height: int,
    rng: _random_module.Random,
    *,
    player_start: tuple[int, int] = (1, 1),
    door_position: tuple[int, int] | None = None,
) -> MazeLayout:
    """Generate a maze of the given dimensions using a seeded RNG.

    Returns a :class:`~canon.layout.MazeLayout`.  The placement-related
    fields (``npc_positions``, ``item_placements``, ``event_positions``,
    ``quest_ids``) are left at their empty defaults — those get populated by
    later pipeline phases.

    Algorithm: recursive backtracking starting at ``player_start``.  40%
    chance of a wider hallway per direction step, biased toward
    ``MIN_HALLWAY_SIZE``.

    Parameters
    ----------
    width:
        Number of columns in the grid.
    height:
        Number of rows in the grid.
    rng:
        A seeded ``random.Random`` instance.  Caller is responsible for
        seeding; this keeps the function side-effect-free with respect to
        the global RNG.
    player_start:
        ``(x, y)`` cell where carving begins.  Defaults to ``(1, 1)``
        matching mazeworld's convention.
    door_position:
        ``(x, y)`` of the exit door.  Defaults to the bottom-right
        interior corner ``(width - 2, height - 2)``.
    """
    grid: list[list[int]] = [[WALL] * width for _ in range(height)]
    _carve_passages_from(grid, player_start[0], player_start[1], width, height, rng)

    if door_position is None:
        door_position = (width - 2, height - 2)

    return MazeLayout(
        grid=grid,
        width=width,
        height=height,
        door_position=door_position,
        player_start=player_start,
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _carve_passages_from(
    grid: list[list[int]],
    x: int,
    y: int,
    width: int,
    height: int,
    rng: _random_module.Random,
) -> None:
    """Recursive backtracking carver.  Modifies ``grid`` in place."""
    directions = DIRECTIONS[:]
    rng.shuffle(directions)

    for dx, dy in directions:
        if rng.random() < 0.4:
            weights = [
                MAX_HALLWAY_SIZE + 1 - w
                for w in range(MIN_HALLWAY_SIZE, MAX_HALLWAY_SIZE + 1)
            ]
            hallway_width = rng.choices(
                population=list(range(MIN_HALLWAY_SIZE, MAX_HALLWAY_SIZE + 1)),
                weights=weights,
                k=1,
            )[0]
        else:
            hallway_width = MIN_HALLWAY_SIZE

        nx, ny = x + dx * 2, y + dy * 2
        if 0 <= nx < width and 0 <= ny < height and grid[ny][nx] == WALL:
            _carve_hallway(grid, x, y, dx, dy, hallway_width, width, height)
            _carve_passages_from(grid, nx, ny, width, height, rng)


def _carve_hallway(
    grid: list[list[int]],
    x: int,
    y: int,
    dx: int,
    dy: int,
    hallway_width: int,
    width: int,
    height: int,
) -> None:
    """Carve a hallway of ``hallway_width`` cells wide.  Modifies ``grid`` in place.

    Iterates over 3 cells (current, intermediate, destination) along the
    ``(dx, dy)`` direction, then fans out perpendicular to the direction by
    ``hallway_width // 2`` on each side.
    """
    half = hallway_width // 2
    for i in range(3):  # current cell + intermediate + destination
        mx = x + dx * i
        my = y + dy * i
        for w in range(-half, half + 1):
            if dy != 0:
                # Moving vertically — fan out horizontally
                wx, wy = mx + w, my
            else:
                # Moving horizontally — fan out vertically
                wx, wy = mx, my + w
            if 0 <= wx < width and 0 <= wy < height:
                grid[wy][wx] = PATH
