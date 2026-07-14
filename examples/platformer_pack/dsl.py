"""Layout DSL + Stamp Tool (PRD §4.3 grammar subset, invariant I3).

The Layout Agent emits a DSL *string*; this module deterministically expands
it into the int8 collision grid against the game's TILE REGISTRY (Phase 3b:
values in data, categories in code). Agents never touch grid cells.

Grammar (one op per line or semicolon-separated)::

    floor(x1, x2)        ground segment: floor at row H-2, bedrock below
    breakable(x1, x2)    a crumbling floor: solid footing at row H-2 over a
                         bottomless pit — a consumer fuse removes it a moment
                         after the player stands on it (reachability treats it
                         as a permanent foothold; v1 ignores the fuse timing)
    gap(x1, x2)          removes ground (fall = respawn)
    pit(x1, x2)          gap with a hazard at the bottom row (visible death)
    platform(x, y, len)  one-way platform at row y, cols x..x+len-1
    ledge(x1, x2, y)     solid floor segment at arbitrary row y (a tier)
    wall(x, y1, y2)      solid wall column
    stairs_up(x1, x2)    stepped slope on the ground: column x1 is one
                         solid block tall, each column one taller (slopes
                         v1 — stacked flat solids, 1-riser jumpable steps;
                         NO new collision category)
    stairs_down(x1, x2)  the mirror: tallest at x1, descending right
    pyramid(x1, x2)      rises to the middle, falls after — a stepped hill
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

    water_cloud(x1, y1, x2, y2)       a floating CLOUD of free water (a
                         swim-up pocket): like water_block but puffed into
                         a rounded silhouette (its four corners trimmed when
                         big enough) — jump in from below and swim UP through
                         it. Containment-exempt free water.
    volume_cloud(name, x1, y1, x2, y2)  the registry-generic form
    reward(x, y)         a hidden REWARD marker (a placeholder for a future
                         collectible) sitting in open air inside a niche —
                         lands in the triggers layer; the play surfaces
                         ignore it for now (layout-only, secret alcoves)

``name`` args are registry tile names; everything else is ints. Rows count
from the top (row 0). Ground floor row is ``H-2``; the standing row above
it is ``H-3``. Ops apply in order; later ops overwrite earlier cells. The
parser is strict: unknown ops, bad arity, or bad args raise ``DslError``
naming the offending line — the retry-with-feedback loop turns that into
LLM feedback.

Point MARKERS (spawn/checkpoint/exit) are validated against the FINAL
grid, like the hazard records: a marker declared before the floor it
stands on is fine as long as the finished level supports it (a real
model burned two of three l3 attempts on ``spawn(2)`` written one line
above ``floor(0,63)``). All marker problems are reported together on
``DslError.problems`` — one attempt surfaces every marker failure, not
just the first. A ``volume`` poured ON the ground floor row is snapped
one row up in code when the span above is open (the validator message
already computed that fix; computable fixes are tool work, not LLM
round-trips) and recorded on ``StampResult.repairs``.
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
    "breakable": "ii",
    "gap": "ii",
    "pit": "ii",
    "platform": "iii",
    "ledge": "iii",
    "wall": "iii",
    "stairs_up": "ii",
    "stairs_down": "ii",
    "pyramid": "ii",
    "spike": "ii",
    "carve": "iiii",
    "water": "iii",
    "volume": "niii",
    "pool": "nii",
    "water_wall": "iii",
    "volume_wall": "niii",
    "water_block": "iiii",
    "volume_block": "niiii",
    "water_cloud": "iiii",
    "volume_cloud": "niiii",
    "hazard_strip": "nii",
    "checkpoint": "i",
    "reward": "ii",
    "spawn": "i",
    "exit": "i",
}

#: Human parameter templates for the arg-count error. The model conflated
#: wall (3 args) with the 4-arg rectangle ops (carve/water_block) and
#: emitted a fourth argument three times into fallback — a rejection that
#: recites the EXACT signature (not just the count) is what breaks the loop.
_ARG_NAMES: dict[str, str] = {
    "floor": "x1, x2",
    "breakable": "x1, x2",
    "gap": "x1, x2",
    "pit": "x1, x2",
    "platform": "x, y, len",
    "ledge": "x1, x2, y",
    "wall": "x, y1, y2",
    "stairs_up": "x1, x2",
    "stairs_down": "x1, x2",
    "pyramid": "x1, x2",
    "spike": "x1, x2",
    "carve": "x1, y1, x2, y2",
    "water": "x1, x2, y_surface",
    "volume": "name, x1, x2, y_surface",
    "pool": "name, x1, x2",
    "water_wall": "x1, x2, y_top",
    "volume_wall": "name, x1, x2, y_top",
    "water_block": "x1, y1, x2, y2",
    "volume_block": "name, x1, y1, x2, y2",
    "water_cloud": "x1, y1, x2, y2",
    "volume_cloud": "name, x1, y1, x2, y2",
    "hazard_strip": "name, x1, x2",
    "checkpoint": "x",
    "reward": "x, y",
    "spawn": "x",
    "exit": "x",
}


class DslError(ValueError):
    """A DSL string failed to parse or stamp. Message names the line.

    ``problems`` carries the individual problem strings when several are
    found in one pass (final-grid marker checks) — retry feedback can
    then list them all instead of serializing discovery across attempts.
    """

    def __init__(self, message: str, problems: list[str] | None = None):
        super().__init__(message)
        self.problems: list[str] = problems or [message]


@dataclass
class StampResult:
    """Deterministic expansion of a DSL string."""

    grid: object  # numpy int8 array, shape (height, width)
    spawn: tuple[int, int] | None = None  # (x, y) standing cell
    exit: tuple[int, int] | None = None
    hazards: list[SparseMaskEntry] = field(default_factory=list)
    triggers: list[SparseMaskEntry] = field(default_factory=list)
    #: Deterministic in-code repairs applied during the stamp (volume
    #: surface snapped off the ground row, ...) — loud, for the log.
    repairs: list[str] = field(default_factory=list)
    #: Cells placed by the FREE-water ops (water_wall / water_block —
    #: deliberate uncontained features: waterfalls, climb shafts,
    #: floating pockets). The containment rule exempts exactly these.
    free_volume: set = field(default_factory=set)


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
                f"line {lineno}: {name} takes {len(signature)} args "
                f"({_ARG_NAMES.get(name, '...')}), got {len(args)}. Emit "
                f"exactly {len(signature)} — do not add or drop an argument."
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


def _cloud_cells(
    x1: int, y1: int, x2: int, y2: int
) -> list[tuple[int, int]]:
    """The cells of a water CLOUD — a filled rectangle with its four corners
    trimmed to give a rounded, puffy silhouette. Corners are removed only when
    the rectangle is at least 3x3, so the vertical centre column (the swim-up
    path) and a full-width middle row always survive; a 2-tall or narrow cloud
    stays a plain rectangle. Deterministic, order-stable (row-major)."""
    trim = (x2 - x1) >= 2 and (y2 - y1) >= 2
    corners = {(x1, y1), (x2, y1), (x1, y2), (x2, y2)} if trim else set()
    return [
        (x, y)
        for y in range(y1, y2 + 1)
        for x in range(x1, x2 + 1)
        if (x, y) not in corners
    ]


def stamp(
    text: str, width: int, height: int, tiles: TileRegistry = DEFAULT_TILES,
    validate_markers: bool = True,
) -> StampResult:
    """Expand a DSL string into the collision grid (int8, registry-id
    valued).

    Deterministic: same string + dims + registry → same grid, always.
    Raises DslError for out-of-bounds coordinates, unknown tile names, or
    missing/duplicate spawn/exit — the messages are written to be fed back
    to the Layout Agent verbatim.

    ``validate_markers=False`` skips the final-grid spawn/exit/checkpoint
    checks (and their missing-marker requirements) — for the snap repair
    TOOLS, which probe the terrain a program builds in order to CHOOSE
    valid marker columns; the real stamp afterwards still validates.
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
        # CODE-NOT-LLM repair (was the #1 real-run fallback driver): a hazard
        # needs something solid beneath it — rather than hard-raise, adapt each
        # column. On SOLID ground (floor OR a wall the model stacked over the
        # floor) the hazard sits on the standing row; over a GAP it auto-becomes
        # a PIT (hazard at the bottom edge — a visible death, the fix the old
        # message only SUGGESTED); over open water it is dropped (a hazard can't
        # float on liquid). Every adaptation is logged in ``repairs``.
        _check_x(op, x1, x2)
        pitted: list[int] = []
        dropped: list[int] = []
        for x in range(x1, x2 + 1):
            below = int(grid[ground_row, x])
            if below in solid_ids:
                grid[standing_row, x] = tile.id
                result.hazards.append(
                    SparseMaskEntry(
                        x=x, y=standing_row, type=f"floor_{tile.name}",
                        params=dict(tile.params),
                    )
                )
            elif below == empty_id:
                grid[height - 1, x] = tile.id
                result.hazards.append(
                    SparseMaskEntry(
                        x=x, y=height - 1, type=f"pit_{tile.name}",
                        params=dict(tile.params),
                    )
                )
                pitted.append(x)
            else:
                dropped.append(x)
        if pitted:
            result.repairs.append(
                f"{op}({tile.name},{x1},{x2}): column(s) {pitted} had no ground "
                "— auto-converted to a pit (hazard at the bottom of the gap)."
            )
        if dropped:
            result.repairs.append(
                f"{op}({tile.name},{x1},{x2}): column(s) {dropped} sit over "
                "liquid — hazard dropped there (it can't float on water)."
            )

    def _stamp_volume(
        op: str, tile: TileDef, x1: int, x2: int, y_surface: int
    ) -> None:
        _check_x(op, x1, x2)
        if not 1 <= y_surface <= height - 2:
            raise DslError(
                f"{op}: surface row {y_surface} outside 1..{height - 2}."
            )
        # Pour aimed AT the ground floor row over solid floor: the fix is
        # arithmetic (the old error message literally computed it — "use
        # surface row N-1"), so when the row above is open air the tool
        # applies it instead of burning an LLM attempt. When that row is
        # blocked, following the old recipe would ALSO have failed (the
        # third real l3 run poured 24-30 under its own spike strip at
        # 28-30) — so the error names the located conflict instead of
        # reciting a recipe that cannot work. Mixed floor/gap spans fall
        # through to the basin checks below.
        if y_surface == ground_row and all(
            int(grid[y_surface, x]) == floor_id for x in range(x1, x2 + 1)
        ):
            blockers = [
                x
                for x in range(x1, x2 + 1)
                if int(grid[y_surface - 1, x]) != empty_id
            ]
            if not blockers:
                y_surface -= 1
                result.repairs.append(
                    f"{op}({tile.name},{x1},{x2},{y_surface + 1}): surface "
                    f"was the ground floor row — snapped to open row "
                    f"{y_surface}; the pool now sits on top of the floor."
                )
            else:
                named: dict[str, list[int]] = {}
                for x in blockers:
                    occupant = tiles.by_id.get(int(grid[y_surface - 1, x]))
                    occ = occupant.name if occupant else "?"
                    named.setdefault(occ, []).append(x)
                where = "; ".join(
                    f"{occ} at column(s) {', '.join(map(str, xs))}"
                    for occ, xs in named.items()
                )
                raise DslError(
                    f"{op}: row {y_surface} IS the ground floor row — "
                    f"pools sit ON TOP of the floor, at surface row "
                    f"{y_surface - 1} — but that row is not open air over "
                    f"columns {x1}-{x2}: {where}. Move the pool span or "
                    "clear those cells; they cannot share the surface."
                )
        # General terrain snap (playtest l4: the model poured a surface onto
        # a hill/wall, three rejects into fallback). Finding the first row
        # that is open air across the whole span is arithmetic, so the tool
        # lifts the surface there instead of burning retries — as long as
        # such a row exists below the ceiling. (The ground_row case above
        # already resolved; an empty surface row falls straight through.)
        if any(int(grid[y_surface, x]) != empty_id for x in range(x1, x2 + 1)):
            lifted = y_surface
            while lifted >= 1 and any(
                int(grid[lifted, x]) != empty_id for x in range(x1, x2 + 1)
            ):
                lifted -= 1
            if lifted >= 1 and lifted != y_surface:
                result.repairs.append(
                    f"{op}({tile.name},{x1},{x2},{y_surface}): surface row "
                    f"{y_surface} was blocked by terrain — snapped up to the "
                    f"first open row {lifted}."
                )
                y_surface = lifted

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
        """Compact 'a-b, c, d-e' of ground-floor columns — markers
        failing blind sent a real model probing columns 2, 3, 4... into
        fallback; tell it where floor IS."""
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

    def _marker_problem(name: str, x: int) -> str | None:
        """Floor-support problem for a point marker, against the FINAL
        grid (markers are order-independent; the l3 real run burned two
        attempts on spawn declared one line above its floor)."""
        if grid[ground_row, x] == floor_id:
            return None
        ranges = _floor_ranges()
        where = (
            f"ground floor exists at columns {ranges} — "
            f"put {name}() on one of those, or lay floor under "
            f"column {x} first"
            if ranges
            else f"no ground floor exists yet — start with "
            f"floor(0,{width - 1}) and carve"
        )
        return f"{name}: column {x} has no floor under it — {where}."

    # Point markers record columns during the op walk and validate at the
    # end, against the FINAL grid (only duplicates are op-order facts).
    spawn_col: int | None = None
    checkpoint_cols: list[int] = []
    #: Reward markers (x, y) — a niche collectible placed in open air; kept
    #: only if the FINAL grid leaves the cell empty (mirrors hazards).
    reward_cells: list[tuple[int, int]] = []

    for name, args in ops:
        if name == "floor":
            x1, x2 = args
            _check_x(name, x1, x2)
            grid[ground_row, x1 : x2 + 1] = floor_id
            grid[height - 1, x1 : x2 + 1] = wall_id  # bedrock
        elif name == "breakable":
            # A crumbling bridge: a SOLID foothold on the ground row (so the
            # player walks/stands on it and reachability treats it as normal
            # footing), but with a BOTTOMLESS PIT below — when a consumer's
            # break fuse fires the tile is removed and the player falls. Keep
            # spans SHORT: v1 reachability assumes it is crossable and does
            # NOT model the fuse timing.
            x1, x2 = args
            _check_x(name, x1, x2)
            if "breakable" not in by_name:
                raise DslError(
                    "breakable: this game's tile registry has no 'breakable' "
                    "tile."
                )
            grid[ground_row, x1 : x2 + 1] = by_name["breakable"].id
            grid[height - 1, x1 : x2 + 1] = empty_id  # pit below — fall on break
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
        elif name in ("stairs_up", "stairs_down", "pyramid"):
            # Slopes v1: STEPPED slopes — stacked flat solids on the
            # ground, one riser per column (jumpable with the existing
            # physics; smooth slopes are a v2+ collision category).
            x1, x2 = args
            if x1 > x2:
                x1, x2 = x2, x1
            _check_x(name, x1, x2)
            span = x2 - x1 + 1
            # Keep at least two air rows above the tallest stack so a
            # slope can never seal the level shut.
            h_cap = max(1, ground_row - 2)
            # CODE-NOT-LLM repair: a stepped slope stacks ON the ground, so lay
            # floor under any column of the span that lacks it (a gap, or a cell
            # a prior op overwrote) rather than raising — the ramp gets its
            # ground, the fix the old message only suggested.
            laid = [
                x for x in range(x1, x2 + 1)
                if int(grid[ground_row, x]) != floor_id
            ]
            for x in laid:
                grid[ground_row, x] = floor_id
                grid[height - 1, x] = wall_id  # bedrock
            if laid:
                result.repairs.append(
                    f"{name}({x1},{x2}): laid floor under column(s) {laid} so "
                    "the slope has ground to stack on."
                )
            for i in range(span):
                if name == "stairs_up":
                    h = i + 1
                elif name == "stairs_down":
                    h = span - i
                else:  # pyramid: rise to the middle, fall after
                    h = min(i, span - 1 - i) + 1
                h = min(h, h_cap)
                x = x1 + i
                grid[ground_row - h : ground_row, x] = floor_id
        elif name == "volume":
            tile_name, x1, x2, y_surface = args
            tile = _resolve(tiles, name, tile_name, "volume")
            _stamp_volume(name, tile, x1, x2, y_surface)
        elif name == "pool":
            tile_name, x1, x2 = args
            tile = _resolve(tiles, name, tile_name, "volume")
            _check_x(name, x1, x2)
            # Name what ACTUALLY occupies each bad column — the old "lay
            # floor there first" hint was unfollowable when the program's
            # own wall/gap had overwritten/removed floor it DID lay (the
            # sunlit run's l3 repeated the same rejected pool verbatim
            # three times against it).
            id_names = {t.id: t.name for t in tiles.tiles}
            bad = [
                f"{x} ({id_names.get(int(grid[ground_row, x]), 'unknown')})"
                for x in range(x1, x2 + 1)
                if grid[ground_row, x] != floor_id
            ]
            if bad:
                raise DslError(
                    f"pool: ground-row column(s) {', '.join(bad)} are not "
                    f"solid floor, so the pool cannot sink in — an earlier "
                    f"op of yours removed or overwrote the floor there "
                    f"(gap/pit leaves 'empty'; wall/volume replace it). "
                    f"Move the pool to columns whose ground row is still "
                    f"floor, or stop overlapping it with those ops."
                )
            # Replace the walking surface; bedrock below is the basin and
            # the surrounding floor forms the banks — contained by shape.
            grid[ground_row, x1 : x2 + 1] = tile.id
        elif name == "water":
            x1, x2, y_surface = args
            tile = _resolve(tiles, name, "water", "volume")
            _stamp_volume(name, tile, x1, x2, y_surface)
        elif name in ("water_wall", "volume_wall"):
            # FREE water feature: a vertical wall/waterfall of liquid the
            # player can swim UP and leap out of. Each column fills from
            # y_top DOWN through open air to the first solid — or all the
            # way out the BOTTOM edge (a spout: sinking past the bottom
            # is a fall death, deliberately). Exempt from containment.
            if name == "water_wall":
                x1, x2, y_top = args
                tile = _resolve(tiles, name, "water", "volume")
            else:
                tile_name, x1, x2, y_top = args
                tile = _resolve(tiles, name, tile_name, "volume")
            if x1 > x2:
                x1, x2 = x2, x1
            _check_x(name, x1, x2)
            if not 0 <= y_top < height:
                raise DslError(
                    f"{name}: top row {y_top} outside 0..{height - 1}."
                )
            # CODE-NOT-LLM repair: if the wall's top row is occupied, CLIP it
            # down to the first open cell rather than raising — the waterfall
            # simply starts below the terrain instead of failing the level.
            clipped_cols: list[int] = []
            for x in range(x1, x2 + 1):
                y = y_top
                while y < height and grid[y, x] != empty_id:
                    y += 1  # skip solid/occupied cells at/under the top
                if y != y_top and y < height:
                    clipped_cols.append(x)
                while y < height and grid[y, x] == empty_id:
                    grid[y, x] = tile.id
                    result.free_volume.add((x, y))
                    y += 1
            if clipped_cols:
                result.repairs.append(
                    f"{name}({x1},{x2},{y_top}): column(s) {clipped_cols} had "
                    "terrain at the top — the waterfall was clipped to start "
                    "below it."
                )
        elif name in ("water_block", "volume_block", "water_cloud", "volume_cloud"):
            # FREE water feature: a floating pocket of liquid (whimsy is
            # allowed — some worlds float their water). Every target cell
            # must be open air. Exempt from containment. A *_cloud puffs the
            # rectangle into a rounded silhouette (a swim-up pocket); a
            # *_block fills the whole rectangle.
            is_cloud = name in ("water_cloud", "volume_cloud")
            if name in ("water_block", "water_cloud"):
                x1, y1, x2, y2 = args
                tile = _resolve(tiles, name, "water", "volume")
            else:
                tile_name, x1, y1, x2, y2 = args
                tile = _resolve(tiles, name, tile_name, "volume")
            if x1 > x2:
                x1, x2 = x2, x1
            if y1 > y2:
                y1, y2 = y2, y1
            _check_x(name, x1, x2)
            if not 0 <= y1 <= y2 < height:
                raise DslError(
                    f"{name}: rows {y1}..{y2} outside 0..{height - 1}."
                )
            cells = (
                _cloud_cells(x1, y1, x2, y2)
                if is_cloud
                else [
                    (x, y)
                    for y in range(y1, y2 + 1)
                    for x in range(x1, x2 + 1)
                ]
            )
            # CODE-NOT-LLM repair: rather than reject a pocket that overlaps
            # terrain (a top real-run fallback driver), CLIP it to the open-air
            # cells — the water fills the empty part of the shape and the solid
            # part stays solid. Dropped entirely (with a note) if nothing is open.
            open_cells = [(x, y) for (x, y) in cells if int(grid[y, x]) == empty_id]
            clipped = len(cells) - len(open_cells)
            for (x, y) in open_cells:
                grid[y, x] = tile.id
                result.free_volume.add((x, y))
            if clipped:
                shape = "cloud" if is_cloud else "block"
                result.repairs.append(
                    f"{name}(...): {clipped} of {len(cells)} {shape} cell(s) "
                    "overlapped terrain — clipped to the open-air part"
                    + ("" if open_cells else " (nothing open — dropped)")
                    + "."
                )
        elif name == "carve":
            x1, y1, x2, y2 = args
            if x1 > x2:
                x1, x2 = x2, x1
            if y1 > y2:
                y1, y2 = y2, y1
            _check_x(name, x1, x2)
            if not 0 <= y1 <= y2 <= standing_row:
                raise DslError(
                    f"carve: rows {y1}..{y2} outside 0..{standing_row} — "
                    "carve clears air/structure above the ground row only; "
                    "break the ground with gap() or pit() instead."
                )
            grid[y1 : y2 + 1, x1 : x2 + 1] = empty_id
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
            _check_x(name, x)
            if x in checkpoint_cols:
                raise DslError(
                    f"checkpoint: column {x} declared more than once."
                )
            checkpoint_cols.append(x)
        elif name == "reward":
            # A hidden collectible marker (secret alcoves, Chunk E): it sits
            # in OPEN AIR (inside a carved niche), lands in the triggers layer,
            # and is inert on the play surfaces for now (future item system).
            x, y = args
            _check_x(name, x)
            if not 0 <= y < height:
                raise DslError(
                    f"reward: row {y} outside 0..{height - 1}."
                )
            # CODE-NOT-LLM: a reward sits in open air. If the cell is occupied,
            # DROP the marker (a cosmetic placeholder — never worth a fallback)
            # rather than raise; the final-grid filter also drops any a later op
            # buries.
            if int(grid[y, x]) != empty_id:
                occ = tiles.by_id.get(int(grid[y, x]))
                result.repairs.append(
                    f"reward({x},{y}): cell was occupied by "
                    f"{occ.name if occ else '?'} — reward marker dropped "
                    "(it needs open air)."
                )
            else:
                reward_cells.append((x, y))
        elif name in ("spawn", "exit"):
            (x,) = args
            if name == "spawn":
                _check_x(name, x)
                if spawn_col is not None:
                    raise DslError("spawn: declared more than once.")
                spawn_col = x
            else:
                # exit(x)'s x is ADVISORY: the exit relocates to the
                # rightmost floored column after all ops (below) — levels
                # exit off the right edge, side-scroller style. The op
                # stays required so a layout still DECLARES its exit.
                if result.exit is not None:
                    raise DslError("exit: declared more than once.")
                _check_x(name, x)
                result.exit = (x, standing_row)

    # ---- Final-grid post-pass: records mirror the FINISHED level. ----
    # All problems found here report TOGETHER (DslError.problems) — the
    # l3 real run serialized discovery one error per attempt into
    # fallback: fix the spawn complaint, die on the volume one it hid.
    if not validate_markers:
        # Repair-tool probe: terrain only. Records still mirror the
        # final grid; markers land unvalidated (or not at all).
        hazard_ids = {t.id for t in hazard_tiles}
        result.hazards = [
            h for h in result.hazards if int(grid[h.y, h.x]) in hazard_ids
        ]
        if spawn_col is not None:
            result.spawn = (spawn_col, standing_row)
        result.triggers.extend(
            SparseMaskEntry(x=x, y=standing_row, type="checkpoint")
            for x in checkpoint_cols
        )
        result.triggers.extend(
            SparseMaskEntry(x=x, y=y, type="reward")
            for (x, y) in reward_cells
            if int(grid[y, x]) == empty_id
        )
        return result
    problems: list[str] = []
    if spawn_col is None:
        problems.append("missing spawn(x) — every level needs exactly one.")
    elif (issue := _marker_problem("spawn", spawn_col)) is not None:
        problems.append(issue)
    for x in checkpoint_cols:
        if (issue := _marker_problem("checkpoint", x)) is not None:
            problems.append(issue)
    # Ops apply in order and a later carve can clear a stamped hazard
    # strip — records must mirror the FINAL grid, not the op history.
    hazard_ids = {t.id for t in hazard_tiles}
    result.hazards = [
        h for h in result.hazards if int(grid[h.y, h.x]) in hazard_ids
    ]
    if result.exit is None:
        problems.append("missing exit(x) — every level needs exactly one.")
    else:
        # Relocate the exit to the rightmost column with ground floor and
        # an open standing cell — consumers treat that whole COLUMN,
        # bottom to top, as the exit zone (no exit graphic; you leave to
        # the right).
        exit_x = next(
            (
                x
                for x in range(width - 1, -1, -1)
                if grid[ground_row, x] == floor_id
                and grid[standing_row, x] == 0
            ),
            None,
        )
        if exit_x is None:
            ranges = _floor_ranges()
            problems.append(
                "exit: no column has open ground floor to exit onto — "
                + (
                    f"floor exists at columns {ranges} but every standing "
                    "cell is occupied; clear one near the right edge."
                    if ranges
                    else f"start with floor(0,{width - 1}) and carve."
                )
            )
        else:
            result.exit = (exit_x, standing_row)
    if problems:
        raise DslError(" ".join(problems), problems)
    result.spawn = (spawn_col, standing_row)
    result.triggers.extend(
        SparseMaskEntry(x=x, y=standing_row, type="checkpoint")
        for x in checkpoint_cols
    )
    result.triggers.extend(
        SparseMaskEntry(x=x, y=y, type="reward")
        for (x, y) in reward_cells
        if int(grid[y, x]) == empty_id
    )
    return result
