"""Layout DSL + Stamp Tool (PRD §4.3 grammar subset, invariant I3).

The Layout Agent emits a DSL *string*; this module deterministically expands
it into the int8 collision grid against the game's TILE REGISTRY (Phase 3b:
values in data, categories in code). Agents never touch grid cells.

Grammar (one op per line or semicolon-separated)::

    floor(x1, x2)        ground segment: floor at row H-2, bedrock below
    gap(x1, x2)          removes ground (fall = respawn)
    pit(x1, x2)          gap with a hazard at the bottom row (visible death)
    platform(x, y, len)  one-way platform at row y, cols x..x+len-1
    ledge(x1, x2, y)     solid floor segment at arbitrary row y (a tier)
    wall(x, y1, y2)      solid wall column
    checkpoint(x)        mid-level respawn point, standing on ground (3b)
    spawn(x)             player start, standing on ground at column x
    exit(x)              level exit, standing on ground at column x

    volume(name, x1, x2, y_surface)   fill a volume tile (water, lava, mud)
                         from the surface row down to the first solid below
                         (needs a basin — errors over gaps)
    pool(name, x1, x2)   a FLUSH pool sunk into the ground: replaces the
                         floor surface itself; the remaining floor banks
                         contain it automatically (real models kept trying
                         to build this by pouring volume() over pits)
    hazard_strip(name, x1, x2)        a hazard tile (spike, laser) on the
                         standing row above ground
    water(x1, x2, y)     alias for volume(water, ...) — errors if the
                         game's registry has no "water" tile
    spike(x1, x2)        alias for hazard_strip(spike, ...)

``name`` args are registry tile names; everything else is ints. Rows count
from the top (row 0). Ground floor row is ``H-2``; the standing row above
it is ``H-3``. Ops apply in order; later ops overwrite earlier cells. The
parser is strict: unknown ops, bad arity, or bad args raise ``DslError``
naming the offending line — the retry-with-feedback loop turns that into
LLM feedback.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from canon.bible.platformer import SparseMaskEntry
from examples.platformer_pack.tiles import DEFAULT_TILES, TileDef, TileRegistry

# Args capture everything up to the close-paren so bad values reach the
# arg parser and get the clearer per-op message.
_OP_RE = re.compile(r"^\s*([a-z_]+)\s*\(\s*([^)]*?)\s*\)\s*$")
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

#: Op signatures: "i" = int arg, "n" = registry tile name arg.
_SIGNATURES: dict[str, str] = {
    "floor": "ii",
    "gap": "ii",
    "pit": "ii",
    "platform": "iii",
    "ledge": "iii",
    "wall": "iii",
    "spike": "ii",
    "water": "iii",
    "volume": "niii",
    "pool": "nii",
    "hazard_strip": "nii",
    "checkpoint": "i",
    "spawn": "i",
    "exit": "i",
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
    triggers: list[SparseMaskEntry] = field(default_factory=list)


def parse_dsl(text: str) -> list[tuple[str, list]]:
    """Parse a DSL string into ``(op, args)`` tuples. Strict. Name args
    stay strings; int args become ints."""
    ops: list[tuple[str, list]] = []
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
                f"Expected name(arg, ...) with name in {sorted(_SIGNATURES)!r}."
            )
        name, argstr = match.group(1), match.group(2)
        if name not in _SIGNATURES:
            raise DslError(
                f"line {lineno}: unknown op {name!r}. Known: {sorted(_SIGNATURES)!r}."
            )
        signature = _SIGNATURES[name]
        args = [a.strip() for a in argstr.split(",")] if argstr.strip() else []
        if len(args) != len(signature):
            raise DslError(
                f"line {lineno}: {name} takes {len(signature)} args, got {len(args)}."
            )
        parsed: list = []
        for kind, arg in zip(signature, args):
            if kind == "n":
                # Strip optional quotes — models sometimes quote names.
                bare = arg.strip("'\"")
                if not _NAME_RE.match(bare):
                    raise DslError(
                        f"line {lineno}: {name} takes a tile NAME first "
                        f"(like water or spike), got {arg!r}."
                    )
                parsed.append(bare)
            else:
                try:
                    parsed.append(int(arg))
                except ValueError:
                    raise DslError(
                        f"line {lineno}: {name} args must be integers, got {args!r}."
                    ) from None
        ops.append((name, parsed))
    return ops


def _resolve(
    tiles: TileRegistry, op: str, name: str, category: str
) -> TileDef:
    """Resolve a tile-name arg through the registry, category-checked.
    Errors name the vocabulary — they are fed back to the Layout Agent."""
    tile = tiles.by_name.get(name)
    available = sorted(t.name for t in tiles.named(category))
    if tile is None:
        raise DslError(
            f"{op}: this game has no tile named {name!r}. "
            f"Available {category} tiles: {available!r}."
        )
    if tile.category != category:
        raise DslError(
            f"{op}: tile {name!r} is a {tile.category} tile, not a "
            f"{category}. Available {category} tiles: {available!r}."
        )
    return tile


def stamp(
    text: str, width: int, height: int, tiles: TileRegistry = DEFAULT_TILES
) -> StampResult:
    """Expand a DSL string into the collision grid (int8, registry-id
    valued).

    Deterministic: same string + dims + registry → same grid, always.
    Raises DslError for out-of-bounds coordinates, unknown tile names, or
    missing/duplicate spawn/exit — the messages are written to be fed back
    to the Layout Agent verbatim.
    """
    import numpy as np

    ops = parse_dsl(text)
    grid = np.zeros((height, width), dtype=np.int8)
    ground_row = height - 2
    standing_row = height - 3
    result = StampResult(grid=grid)

    by_name = tiles.by_name
    floor_id = by_name["floor"].id
    platform_id = by_name["platform"].id
    wall_id = by_name["wall"].id
    empty_id = tiles.empty_id
    solid_ids = tiles.ids("solid")
    hazard_tiles = tiles.named("hazard")

    def _check_x(op: str, *xs: int) -> None:
        for x in xs:
            if not 0 <= x < width:
                raise DslError(f"{op}: column {x} outside 0..{width - 1}.")

    def _stamp_hazard_strip(op: str, tile: TileDef, x1: int, x2: int) -> None:
        _check_x(op, x1, x2)
        for x in range(x1, x2 + 1):
            if grid[ground_row, x] != floor_id:
                raise DslError(
                    f"{op}: column {x} has no ground under it — hazards "
                    "sit on floor; use pit() for bottomless hazards."
                )
            grid[standing_row, x] = tile.id
            result.hazards.append(
                SparseMaskEntry(
                    x=x, y=standing_row, type=f"floor_{tile.name}",
                    params=dict(tile.params),
                )
            )

    def _stamp_volume(
        op: str, tile: TileDef, x1: int, x2: int, y_surface: int
    ) -> None:
        _check_x(op, x1, x2)
        if not 1 <= y_surface <= height - 2:
            raise DslError(
                f"{op}: surface row {y_surface} outside 1..{height - 2}."
            )
        for x in range(x1, x2 + 1):
            # Fill EMPTY cells from the surface down until solid ground.
            # A volume over a gap/pit would drain — demand a basin.
            y = y_surface
            filled = False
            while y < height and int(grid[y, x]) == empty_id:
                grid[y, x] = tile.id
                filled = True
                y += 1
            if y >= height or int(grid[y, x]) not in solid_ids:
                raise DslError(
                    f"{op}: column {x} has no solid basin beneath the "
                    f"surface — {tile.name} needs floor under it; don't "
                    "pour it over gaps or pits. For a pool sunk INTO the "
                    f"ground use pool({tile.name},{x1},{x2}) on solid "
                    "floor instead — it carves the surface and the banks "
                    "contain it."
                )
            if not filled:
                occupant = tiles.by_id.get(int(grid[y_surface, x]))
                occ_name = (
                    occupant.name if occupant else str(int(grid[y_surface, x]))
                )
                # Observed real-model failure loop: surface row poured ON
                # the ground floor row, three identical retries into
                # fallback. The message must carry the recipe, not just
                # the diagnosis (validator messages are prompts).
                if y_surface == ground_row:
                    pour = (
                        f"volume({tile.name},{x1},{x2},{y_surface - 1})"
                        if op == "volume"
                        else f"{op}({x1},{x2},{y_surface - 1})"
                    )
                    hint = (
                        f" Row {y_surface} IS the ground floor row — pools "
                        f"sit ON TOP of the floor. Use surface row "
                        f"{y_surface - 1} and wall both sides: "
                        f"wall({x1 - 1},{y_surface - 2},{y_surface - 1})  "
                        f"{pour}  "
                        f"wall({x2 + 1},{y_surface - 2},{y_surface - 1})."
                    )
                else:
                    hint = " Pick an open surface row above the terrain."
                raise DslError(
                    f"{op}: column {x} at surface row {y_surface} is "
                    f"occupied by {occ_name} — the surface row must be "
                    f"open air.{hint}"
                )

    def _floor_ranges() -> str:
        """Compact 'a-b, c, d-e' of ground-floor columns at this point in
        the stamp — markers failing blind sent a real model probing
        columns 2, 3, 4... into fallback; tell it where floor IS."""
        xs = [x for x in range(width) if int(grid[ground_row, x]) == floor_id]
        if not xs:
            return ""
        ranges, start = [], xs[0]
        for prev, cur in zip(xs, xs[1:] + [None]):
            if cur is None or cur != prev + 1:
                ranges.append(f"{start}-{prev}" if prev != start else str(start))
                if cur is not None:
                    start = cur
        return ", ".join(ranges)

    def _standing_marker(name: str, x: int) -> tuple[int, int]:
        _check_x(name, x)
        if grid[ground_row, x] != floor_id:
            ranges = _floor_ranges()
            where = (
                f"ground floor currently exists at columns {ranges} — "
                f"put {name}() on one of those, or lay floor under "
                f"column {x} first"
                if ranges
                else f"no ground floor exists yet — start with "
                f"floor(0,{width - 1}) and carve"
            )
            raise DslError(
                f"{name}: column {x} has no floor under it — {where}."
            )
        return (x, standing_row)

    for name, args in ops:
        if name == "floor":
            x1, x2 = args
            _check_x(name, x1, x2)
            grid[ground_row, x1 : x2 + 1] = floor_id
            grid[height - 1, x1 : x2 + 1] = wall_id  # bedrock
        elif name in ("gap", "pit"):
            x1, x2 = args
            _check_x(name, x1, x2)
            grid[ground_row, x1 : x2 + 1] = empty_id
            if name == "pit":
                if not hazard_tiles:
                    raise DslError(
                        "pit: this game has no hazard tiles — use gap() "
                        "for plain drops."
                    )
                # Deterministic pick: the lowest-id hazard is the pit floor.
                pit_tile = min(hazard_tiles, key=lambda t: t.id)
                grid[height - 1, x1 : x2 + 1] = pit_tile.id
                for x in range(x1, x2 + 1):
                    result.hazards.append(
                        SparseMaskEntry(
                            x=x, y=height - 1, type=f"pit_{pit_tile.name}",
                            params=dict(pit_tile.params),
                        )
                    )
            else:
                grid[height - 1, x1 : x2 + 1] = empty_id
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
            grid[y, x : x + length] = platform_id
        elif name == "ledge":
            x1, x2, y = args
            _check_x(name, x1, x2)
            if not 1 <= y <= height - 3:
                raise DslError(
                    f"ledge: row {y} outside 1..{height - 3} "
                    "(leave headroom above, ground below)."
                )
            grid[y, x1 : x2 + 1] = floor_id
        elif name == "wall":
            x, y1, y2 = args
            _check_x(name, x)
            if y1 > y2:
                y1, y2 = y2, y1
            grid[max(y1, 0) : min(y2, height - 1) + 1, x] = wall_id
        elif name == "volume":
            tile_name, x1, x2, y_surface = args
            tile = _resolve(tiles, name, tile_name, "volume")
            _stamp_volume(name, tile, x1, x2, y_surface)
        elif name == "pool":
            tile_name, x1, x2 = args
            tile = _resolve(tiles, name, tile_name, "volume")
            _check_x(name, x1, x2)
            for x in range(x1, x2 + 1):
                if grid[ground_row, x] != floor_id:
                    raise DslError(
                        f"pool: column {x} has no ground floor to sink "
                        "into — pool() replaces solid floor; lay "
                        f"floor({x1},{x2}) there first or move the pool."
                    )
            # Replace the walking surface; bedrock below is the basin and
            # the surrounding floor forms the banks — contained by shape.
            grid[ground_row, x1 : x2 + 1] = tile.id
        elif name == "water":
            x1, x2, y_surface = args
            tile = _resolve(tiles, name, "water", "volume")
            _stamp_volume(name, tile, x1, x2, y_surface)
        elif name == "hazard_strip":
            tile_name, x1, x2 = args
            tile = _resolve(tiles, name, tile_name, "hazard")
            _stamp_hazard_strip(name, tile, x1, x2)
        elif name == "spike":
            x1, x2 = args
            tile = _resolve(tiles, name, "spike", "hazard")
            _stamp_hazard_strip(name, tile, x1, x2)
        elif name == "checkpoint":
            (x,) = args
            marker = _standing_marker(name, x)
            if any(
                t.x == marker[0] and t.type == "checkpoint"
                for t in result.triggers
            ):
                raise DslError(
                    f"checkpoint: column {x} declared more than once."
                )
            result.triggers.append(
                SparseMaskEntry(x=marker[0], y=marker[1], type="checkpoint")
            )
        elif name in ("spawn", "exit"):
            (x,) = args
            marker = _standing_marker(name, x)
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
