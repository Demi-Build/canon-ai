"""Layout DSL + Stamp Tool (PRD §4.3 grammar subset, invariant I3).

The Layout Agent emits a DSL *string*; this module deterministically expands
it into the int8 collision grid. Agents never touch grid cells.

Grammar (one op per line or semicolon-separated; ints only)::

    floor(x1, x2)        ground segment: FLOOR at row H-2, bedrock below
    gap(x1, x2)          removes ground (fall = respawn)
    pit(x1, x2)          gap with SPIKE at the bottom row (visible death pit)
    platform(x, y, len)  one-way PLATFORM at row y, cols x..x+len-1
    ledge(x1, x2, y)     solid FLOOR segment at arbitrary row y (a tier)
    wall(x, y1, y2)      solid WALL column
    spike(x1, x2)        SPIKE on the standing row above ground
    water(x1, x2, y)     fill WATER from surface row y down to the first
                         solid below (needs a basin — errors over gaps)
    spawn(x)             player start, standing on ground at column x
    exit(x)              level exit, standing on ground at column x

Rows count from the top (row 0). Ground floor row is ``H-2``; the standing
row above it is ``H-3``. Ops apply in order; later ops overwrite earlier
cells. The parser is strict: unknown ops, bad arity, or non-int args raise
``DslError`` naming the offending line — the retry-with-feedback loop turns
that into LLM feedback.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from canon.bible.platformer import SparseMaskEntry, TileType

# Args capture everything up to the close-paren so bad values reach the
# int parser and get the clearer "must be integers" message.
_OP_RE = re.compile(r"^\s*([a-z_]+)\s*\(\s*([^)]*?)\s*\)\s*$")

_ARITY = {
    "floor": 2,
    "gap": 2,
    "pit": 2,
    "platform": 3,
    "ledge": 3,
    "wall": 3,
    "spike": 2,
    "water": 3,
    "spawn": 1,
    "exit": 1,
}


class DslError(ValueError):
    """A DSL string failed to parse or stamp. Message names the line."""


@dataclass
class StampResult:
    """Deterministic expansion of a DSL string."""

    grid: object  # numpy int8 array, shape (height, width)
    spawn: tuple[int, int] | None = None  # (x, y) standing cell
    exit: tuple[int, int] | None = None
    hazards: list[SparseMaskEntry] = field(default_factory=list)


def parse_dsl(text: str) -> list[tuple[str, list[int]]]:
    """Parse a DSL string into ``(op, args)`` tuples. Strict."""
    ops: list[tuple[str, list[int]]] = []
    # Accept newline- and semicolon-separated ops; ignore blanks + comments.
    raw_lines = [
        part
        for line in text.strip().splitlines()
        for part in line.split(";")
    ]
    for lineno, raw in enumerate(raw_lines, start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _OP_RE.match(stripped)
        if match is None:
            raise DslError(
                f"line {lineno}: {stripped!r} is not a valid op. "
                f"Expected name(int, ...) with name in {sorted(_ARITY)!r}."
            )
        name, argstr = match.group(1), match.group(2)
        if name not in _ARITY:
            raise DslError(
                f"line {lineno}: unknown op {name!r}. Known: {sorted(_ARITY)!r}."
            )
        args = [a.strip() for a in argstr.split(",")] if argstr.strip() else []
        if len(args) != _ARITY[name]:
            raise DslError(
                f"line {lineno}: {name} takes {_ARITY[name]} args, got {len(args)}."
            )
        try:
            int_args = [int(a) for a in args]
        except ValueError:
            raise DslError(
                f"line {lineno}: {name} args must be integers, got {args!r}."
            ) from None
        ops.append((name, int_args))
    return ops


def stamp(text: str, width: int, height: int) -> StampResult:
    """Expand a DSL string into the collision grid (int8, TileType-keyed).

    Deterministic: same string + dims → same grid, always. Raises DslError
    for out-of-bounds coordinates or missing/duplicate spawn/exit — the
    messages are written to be fed back to the Layout Agent verbatim.
    """
    import numpy as np

    ops = parse_dsl(text)
    grid = np.zeros((height, width), dtype=np.int8)
    ground_row = height - 2
    standing_row = height - 3
    result = StampResult(grid=grid)

    def _check_x(op: str, *xs: int) -> None:
        for x in xs:
            if not 0 <= x < width:
                raise DslError(f"{op}: column {x} outside 0..{width - 1}.")

    for name, args in ops:
        if name == "floor":
            x1, x2 = args
            _check_x(name, x1, x2)
            grid[ground_row, x1 : x2 + 1] = TileType.FLOOR
            grid[height - 1, x1 : x2 + 1] = TileType.WALL  # bedrock
        elif name in ("gap", "pit"):
            x1, x2 = args
            _check_x(name, x1, x2)
            grid[ground_row, x1 : x2 + 1] = TileType.EMPTY
            grid[height - 1, x1 : x2 + 1] = (
                TileType.SPIKE if name == "pit" else TileType.EMPTY
            )
            if name == "pit":
                for x in range(x1, x2 + 1):
                    result.hazards.append(
                        SparseMaskEntry(x=x, y=height - 1, type="pit_spike")
                    )
        elif name == "platform":
            x, y, length = args
            _check_x(name, x, x + length - 1)
            if not 1 <= y <= height - 3:
                raise DslError(
                    f"platform: row {y} outside 1..{height - 3} "
                    "(leave headroom above, ground below)."
                )
            if length < 1:
                raise DslError(f"platform: length must be >= 1, got {length}.")
            grid[y, x : x + length] = TileType.PLATFORM
        elif name == "ledge":
            x1, x2, y = args
            _check_x(name, x1, x2)
            if not 1 <= y <= height - 3:
                raise DslError(
                    f"ledge: row {y} outside 1..{height - 3} "
                    "(leave headroom above, ground below)."
                )
            grid[y, x1 : x2 + 1] = TileType.FLOOR
        elif name == "water":
            x1, x2, y_surface = args
            _check_x(name, x1, x2)
            if not 1 <= y_surface <= height - 2:
                raise DslError(
                    f"water: surface row {y_surface} outside 1..{height - 2}."
                )
            solid = (int(TileType.FLOOR), int(TileType.WALL))
            for x in range(x1, x2 + 1):
                # Fill EMPTY cells from the surface down until solid ground.
                # Water over a gap/pit would drain — demand a basin.
                y = y_surface
                filled = False
                while y < height and int(grid[y, x]) == TileType.EMPTY:
                    grid[y, x] = TileType.WATER
                    filled = True
                    y += 1
                if y >= height or int(grid[y, x]) not in solid:
                    raise DslError(
                        f"water: column {x} has no solid basin beneath the "
                        "surface — water needs floor under it; don't pour "
                        "water over gaps or pits."
                    )
                if not filled:
                    raise DslError(
                        f"water: column {x} at surface row {y_surface} is "
                        "already occupied — pick an open surface row."
                    )
        elif name == "wall":
            x, y1, y2 = args
            _check_x(name, x)
            if y1 > y2:
                y1, y2 = y2, y1
            grid[max(y1, 0) : min(y2, height - 1) + 1, x] = TileType.WALL
        elif name == "spike":
            x1, x2 = args
            _check_x(name, x1, x2)
            for x in range(x1, x2 + 1):
                if grid[ground_row, x] != TileType.FLOOR:
                    raise DslError(
                        f"spike: column {x} has no ground under it — spikes "
                        "sit on floor; use pit() for bottomless hazards."
                    )
                grid[standing_row, x] = TileType.SPIKE
                result.hazards.append(
                    SparseMaskEntry(x=x, y=standing_row, type="floor_spike")
                )
        elif name in ("spawn", "exit"):
            (x,) = args
            _check_x(name, x)
            if grid[ground_row, x] != TileType.FLOOR:
                raise DslError(
                    f"{name}: column {x} has no floor under it — place "
                    f"{name}() on a floor segment."
                )
            marker = (x, standing_row)
            if name == "spawn":
                if result.spawn is not None:
                    raise DslError("spawn: declared more than once.")
                result.spawn = marker
            else:
                if result.exit is not None:
                    raise DslError("exit: declared more than once.")
                result.exit = marker

    if result.spawn is None:
        raise DslError("missing spawn(x) — every level needs exactly one.")
    if result.exit is None:
        raise DslError("missing exit(x) — every level needs exactly one.")
    return result
