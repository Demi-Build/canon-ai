"""Section archetypes + the level STITCHER (sectioned-levels phase v1).

A level is a SEQUENCE of typed SECTIONS. Each section is a sub-region with a
character (archetype) — an axis, intensity, water level, encounter style, and a
feature-weight bias — that the Layout Agent fills with a MIX of the existing DSL
features (floor/platform/gap/stairs/water/...). Sections join at an ENTRY/EXIT
height contract and are STITCHED into one level.

Design split (mirrors rules.py / tiles.py):
- **Archetype VALUES are data** — a per-game ``sections.json`` (open carrier,
  ``extra="allow"``) other games edit; ``feature_bias``/``flavor`` steer the
  per-section prompt, they are not engine truths.
- **The stitch + plan KINDS are code** — deterministic sub-grid compositing and
  section planning, tested here.

Stitch strategy: each section is stamped IN ISOLATION at its own local dims (so
``dsl.stamp``'s ``ground_row = height-2`` assumption holds locally), then its
sub-grid is composited into the full level grid at the section's ``(x_off,
y_off)`` origin. Non-empty cells win (a later section's empty overlap never
erases an earlier section's terrain), so the seam stays continuous. This handles
horizontal (vary x_off) and vertical (vary y_off, a later phase) uniformly,
without rebasing mixed-x/y op coordinates.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

#: The pack's default section vocabulary — the template other games copy.
DEFAULT_SECTIONS_PATH = Path(__file__).parent / "sections.json"

#: The pack's default secret-room roll numbers (multi-room arc) — data,
#: like sections.json: presence odds, type/verb/topology weights, dims.
DEFAULT_SECRET_ROOMS_PATH = Path(__file__).parent / "secret_rooms.json"

#: The pack's default water-level roll numbers (water arc) — per-biome
#: odds, topology weights, waterline depth band.
DEFAULT_WATER_LEVELS_PATH = Path(__file__).parent / "water_levels.json"

#: Columns/rows two adjacent sections share so the seam is continuous (the
#: overlap the seam summary describes; matches the handoff §1 ~6-col overlap).
SECTION_OVERLAP = 6

#: A level is a BLUEPRINT of at most this many sections (user-locked cap). Wider
#: levels get bigger sections, not more of them.
MAX_SECTIONS = 5

#: The smallest a partitioned section may be — keeps the fake's per-archetype
#: width guards (>=14/16/18) satisfiable and the seam meaningful.
MIN_SECTION_LEN = 12

#: Target section ADVANCE (length minus overlap): the count is chosen so
#: sections stay roughly this wide (~26 cells), then the level grows by adding
#: sections up to :data:`MAX_SECTIONS` — so a level is ~5 chunky sections, not 8
#: thin ones nor 3 sprawling ones. WIDTH drives the count (and width is already
#: difficulty-banded, so difficulty enters through it, not a separate term).
_TARGET_ADVANCE = 20


class SectionArchetype(BaseModel):
    """One section CHARACTER — data. Unknown keys ride through inert."""

    model_config = ConfigDict(extra="allow")

    axis: str = "horizontal"  # horizontal (x-stitch) | vertical (y-stitch)
    min_len: int = 12  # cells along the axis
    max_len: int = 24
    intensity: str = "medium"  # low | medium | high (pacing hint)
    water: str = "dry"  # dry | optional | submerged
    encounter: str = "traversal"  # traversal | combat | mixed
    feature_bias: dict[str, float] = {}  # feature -> weight (prompt steer)
    framing: str = "vista"  # camera preset hint
    flavor: str = ""  # prompt guidance for the Layout Agent


def load_section_vocab(
    path: str | Path = DEFAULT_SECTIONS_PATH,
) -> dict[str, SectionArchetype]:
    """Load a game's section vocabulary (name -> archetype)."""
    raw = json.loads(Path(path).read_text())
    return {name: SectionArchetype.model_validate(v) for name, v in raw.items()}


DEFAULT_VOCAB = load_section_vocab()


@dataclass
class PlannedSection:
    """A concrete section instance in a level's plan: which archetype, how big,
    and where it sits in the full grid. ``entry_height`` / ``exit_height`` (the
    seam contract) are filled in during generation, not planning."""

    archetype: str
    length: int  # extent along the level's axis (local width for horizontal)
    x_off: int  # origin column in the full grid
    y_off: int  # origin row in the full grid
    entry_height: int | None = None
    exit_height: int | None = None


@dataclass
class ExitSpec:
    """One exit in a level's blueprint. v1 levels have exactly ONE (the right
    edge / climb summit, ``section_idx = last``); the LIST shape is what makes
    multiple exits at different heights a later DATA change, not a rewrite.
    The concrete cell is resolved by :func:`place_exit` at stitch time."""

    section_idx: int


@dataclass
class LevelPlan:
    """A level's BLUEPRINT, rolled once up front (deterministic): the axis, the
    ordered sections (capped at :data:`MAX_SECTIONS`), which sections host a
    checkpoint, and the exits. The authoritative plan the generator fills and
    the validators route against — replaces ad-hoc per-section decisions."""

    axis: str
    sections: list[PlannedSection]
    checkpoint_sections: list[int]
    exits: list[ExitSpec]

    def owner_of(self, x: int, y: int) -> int:
        """Which section index owns a whole-level cell (routes a validator
        failure to the section that must fix it)."""
        return section_owner_of_cell(self.sections, x, y, self.axis)


class SecretRoomConfig(BaseModel):
    """The secret-room ROLL numbers — data (``secret_rooms.json``), so a
    different game reshapes its secrets without code. Unknown keys ride
    through inert (open carriage, like the archetypes)."""

    model_config = ConfigDict(extra="allow")

    #: Per-difficulty presence odds: ``count_odds[str(difficulty)]`` is a
    #: list of probabilities — entry k is the chance of rolling room k+1
    #: GIVEN k rooms already rolled (the roll stops at the first miss).
    count_odds: dict[str, list[float]] = {}
    room_types: dict[str, float] = {"shortcut": 1.0, "vault": 1.0, "lair": 1.0}
    #: Entry-verb weights. The vocabulary is pipe/door/vine; vine ships at
    #: weight 0.0 until its climb mechanic lands (a data flip, not code).
    entry_verbs: dict[str, float] = {"pipe": 1.0, "door": 1.0, "vine": 0.0}
    return_topology: dict[str, float] = {"detour": 1.0, "shortcut": 1.0}
    #: Chance a room is TWO stitched sections instead of one.
    two_section_odds: float = 0.3
    width_band: tuple[int, int] = (16, 28)
    two_section_width_band: tuple[int, int] = (30, 40)
    height_band: tuple[int, int] = (12, 14)
    #: Per-width difficulty CAP (postmortem ticket 3): a room inherits its
    #: parent's difficulty, but a cramped room can't carry a diff-3 challenge
    #: — so a room whose rolled WIDTH is ≤ the entry's threshold caps its
    #: content difficulty at the entry's value (first matching band wins,
    #: sorted ascending; a room wider than every band is uncapped). Empty =
    #: no cap (back-compat). Wider two-section rooms keep more bite.
    difficulty_cap_by_width: list[tuple[int, int]] = []
    #: Which section archetypes each room TYPE draws from.
    archetype_pools: dict[str, list[str]] = {}
    #: Camera hint for rooms (GraphicsSpec view preset name).
    framing: str = "intimate"


def load_secret_room_config(
    path: str | Path = DEFAULT_SECRET_ROOMS_PATH,
) -> SecretRoomConfig:
    """Load a game's secret-room roll numbers."""
    return SecretRoomConfig.model_validate(json.loads(Path(path).read_text()))


DEFAULT_SECRET_ROOM_CONFIG = load_secret_room_config()


@dataclass
class SecretRoomSpec:
    """One rolled secret room — everything the blueprint decided about it.
    Recomputed deterministically wherever it's needed (layout, placement,
    DAG expansion) — never persisted; the ``context`` field is the
    world-map hook (stored, unused by the v1 random roll)."""

    room_id: str
    room_type: str  # shortcut | vault | lair
    archetype_names: list[str]  # 1-2 section archetypes, in order
    entry_verb: str  # pipe | door | vine
    return_topology: str  # detour | shortcut
    width: int
    height: int
    host_section: int  # parent section index hosting the entrance
    difficulty: int  # the parent's rolled difficulty (contents steering)
    context: str = ""  # world-map/theme hook — v1 stores, never reads


class WaterLevelConfig(BaseModel):
    """The water-level ROLL numbers — data (``water_levels.json``), the
    secret_rooms.json pattern: a different game reshapes its seas
    without code. Unknown keys ride through inert."""

    model_config = ConfigDict(extra="allow")

    #: Water rolls only from this stage number on (intro stages stay dry).
    enabled_stage_min: int = 2
    #: Per-biome water odds; "*" is the default for unlisted biomes —
    #: the "seaside rolls more" steering hook.
    biome_odds: dict[str, float] = {"*": 0.45}
    #: Topology weights: fully_submerged (whole grid swims) vs waterline
    #: (high water table, dry islands above).
    topology: dict[str, float] = {"fully_submerged": 1.0, "waterline": 1.0}
    #: Waterline depth in ROWS of water measured up from the bottom
    #: (the flooded band is rows >= height - depth).
    waterline_depth_band: tuple[int, int] = (5, 9)


def load_water_level_config(
    path: str | Path = DEFAULT_WATER_LEVELS_PATH,
) -> WaterLevelConfig:
    """Load a game's water-level roll numbers."""
    return WaterLevelConfig.model_validate(json.loads(Path(path).read_text()))


DEFAULT_WATER_LEVEL_CONFIG = load_water_level_config()


@dataclass
class WaterSpec:
    """One rolled water topology — recomputed deterministically wherever
    needed (layout, room suppression, DAG expansion); never persisted.
    The flood OUTCOME (incl. any waterline-lowering repair) is baked
    into collision.npz — downstream reads the grid, not this spec."""

    topology: str  # fully_submerged | waterline
    depth: int  # waterline rows of water up from the bottom (0 for full)


def roll_water_level(
    biome: str,
    rng: Any,
    config: WaterLevelConfig = DEFAULT_WATER_LEVEL_CONFIG,
) -> WaterSpec | None:
    """Roll whether a level is a WATER level and which topology — CODE,
    not LLM (every number from data, deterministic in ``rng``). Stage
    and axis gating is the CALLER's job (the identity wrapper knows the
    level's stage number and rolled axis); this is the pure roll."""
    odds = config.biome_odds.get(biome, config.biome_odds.get("*", 0.0))
    if float(rng.random()) >= float(odds):
        return None
    topology = _weighted_pick(rng, config.topology, "waterline")
    depth = 0
    if topology == "waterline":
        depth = _roll_band(rng, config.waterline_depth_band)
    return WaterSpec(topology=topology, depth=depth)


def _weighted_pick(rng: Any, weights: dict[str, float], fallback: str) -> str:
    """Deterministic weighted pick over a data-driven weight table; zero
    weights never win (how vine ships rolled-off)."""
    entries = [(k, float(w)) for k, w in weights.items() if float(w) > 0]
    if not entries:
        return fallback
    total = sum(w for _, w in entries)
    r = float(rng.random()) * total
    upto = 0.0
    for name, w in entries:
        upto += w
        if r <= upto:
            return name
    return entries[-1][0]


def _roll_band(rng: Any, band: Any) -> int:
    lo, hi = int(band[0]), int(band[1])
    return lo if hi <= lo else int(rng.randint(lo, hi))


def roll_secret_rooms(
    level_id: str,
    difficulty: int,
    n_sections: int,
    rng: Any,
    config: SecretRoomConfig = DEFAULT_SECRET_ROOM_CONFIG,
    context: str = "",
) -> list[SecretRoomSpec]:
    """Roll a level's secret rooms — presence, type, archetype(s), entry
    verb, return topology, dims, host section — CODE, not LLM (the
    blueprint discipline: every number from data, deterministic in
    ``rng``). ``n_sections`` is the parent blueprint's section count (the
    host roll + shortcut-return space). ``context`` is stored on the spec
    for the future world-map-informed roll; v1 ignores it."""
    odds = [float(p) for p in config.count_odds.get(str(int(difficulty)), [])]
    specs: list[SecretRoomSpec] = []
    for k, p in enumerate(odds):
        if float(rng.random()) >= p:
            break
        room_type = _weighted_pick(rng, config.room_types, "shortcut")
        verb = _weighted_pick(rng, config.entry_verbs, "pipe")
        topology = _weighted_pick(rng, config.return_topology, "detour")
        two = float(rng.random()) < float(config.two_section_odds)
        width = _roll_band(
            rng, config.two_section_width_band if two else config.width_band
        )
        height = _roll_band(rng, config.height_band)
        # Cap the room's CONTENT difficulty by its rolled width — a cramped
        # room can't hold a diff-3 gauntlet (ticket 3). Adds no rng draw, so
        # the roll stream stays aligned; only the stamped value moves. The
        # presence odds above still key on the PARENT difficulty.
        room_difficulty = int(difficulty)
        for w_max, cap in sorted(config.difficulty_cap_by_width):
            if width <= int(w_max):
                room_difficulty = min(room_difficulty, int(cap))
                break
        pool = list(config.archetype_pools.get(room_type, [])) or ["cave"]
        names = [pool[int(rng.randint(0, len(pool) - 1))] for _ in range(2 if two else 1)]
        host = int(rng.randint(0, max(0, n_sections - 1)))
        specs.append(
            SecretRoomSpec(
                room_id=f"{level_id}r{k + 1}",
                room_type=room_type,
                archetype_names=names,
                entry_verb=verb,
                return_topology=topology,
                width=width,
                height=height,
                host_section=host,
                difficulty=room_difficulty,
                context=context,
            )
        )
    return specs


def plan_room(
    spec: SecretRoomSpec,
    vocab: dict[str, SectionArchetype] | None = None,
) -> LevelPlan:
    """A secret room's BLUEPRINT: 1-2 sections of the rolled archetype(s)
    tiling the room's width — the mini-level twin of :func:`plan_level`,
    minus the opener/checkpoint logic (rooms are small, horizontal, and
    checkpoint-free; the exit cell doubles as the return portal)."""
    names = list(spec.archetype_names) or ["cave"]
    width = int(spec.width)
    if len(names) == 1:
        sections = [PlannedSection(names[0], max(2, width), x_off=0, y_off=0)]
    else:
        first = (width + SECTION_OVERLAP) // 2
        second = width + SECTION_OVERLAP - first
        sections = [
            PlannedSection(names[0], first, x_off=0, y_off=0),
            PlannedSection(
                names[1], second, x_off=first - SECTION_OVERLAP, y_off=0
            ),
        ]
    return LevelPlan(
        axis="horizontal",
        sections=sections,
        checkpoint_sections=[],
        exits=[ExitSpec(section_idx=len(sections) - 1)],
    )


def _roll_section_count(extent: int, difficulty: int, rng: Any) -> int:
    """How many sections the level has — chosen so each stays ~``_TARGET_ADVANCE``
    wide (WIDTH drives the count; ``difficulty`` is accepted for signature
    parity with the other roll helpers but enters only via the already-banded
    width), capped at :data:`MAX_SECTIONS`, and clamped to what ``extent`` can
    fit (each section needs :data:`MIN_SECTION_LEN`, sharing
    :data:`SECTION_OVERLAP`). The roll adds ±1 jitter around the ideal count."""
    ideal = max(1, round((extent - SECTION_OVERLAP) / _TARGET_ADVANCE))
    max_fit = 1
    while max_fit < MAX_SECTIONS:
        k = max_fit + 1
        if k * MIN_SECTION_LEN - (k - 1) * SECTION_OVERLAP <= extent:
            max_fit = k
        else:
            break
    hi = min(MAX_SECTIONS, max_fit, ideal)
    lo = max(1, min(ideal - 1, hi))
    return lo if hi <= lo else int(rng.randint(lo, hi))


def _partition_extent(
    extent: int, names: list[str], vocab: dict[str, SectionArchetype], rng: Any
) -> list[int]:
    """Split ``extent`` into one length per section so the sections TILE it
    exactly (``sum(lengths) - overlap*(n-1) == extent``). Each length starts
    near an even share (jittered, clamped to the archetype's min/max); the LAST
    section absorbs the remainder so the tiling always reaches the far edge."""
    n = len(names)
    if n == 1:
        return [max(2, extent)]
    total_len = extent + SECTION_OVERLAP * (n - 1)
    base = total_len // n
    lengths: list[int] = []
    for i, name in enumerate(names):
        arch = vocab[name]
        # The opener (a runway breather) stays modest — capped at its own max so
        # it never sprawls; the rest centre on the even base (soft max only).
        target = min(base, int(arch.max_len)) if i == 0 else base
        jitter = int((rng.random() - 0.5) * 4)  # +/-2
        lengths.append(max(int(arch.min_len), target + jitter))
    # Reach the edge EXACTLY by spreading the leftover one cell at a time across
    # the non-opener sections (round-robin), so no single section sprawls; never
    # push a section below MIN_SECTION_LEN.
    diff = total_len - sum(lengths)
    step = 1 if diff >= 0 else -1
    i = 0
    guard = 0
    while diff != 0 and n > 1:
        idx = 1 + (i % (n - 1))
        i += 1
        guard += 1
        if guard > 10 * n + 100:  # safety: dump any residue on the last
            lengths[-1] = max(2, lengths[-1] + diff)
            break
        if step < 0 and lengths[idx] <= MIN_SECTION_LEN:
            continue
        lengths[idx] += step
        diff -= step
    return lengths


def _checkpoint_sections(n: int) -> list[int]:
    """Which section indices host a checkpoint, from the count rule (user-
    locked): 0 for 1-2 sections, 1 for 3-4, 2 for 5 — spread across INTERIOR
    sections only (never the spawn/section-0 or the exit/last section)."""
    count = 0 if n < 3 else (1 if n <= 4 else 2)
    interior = list(range(1, n - 1))
    if count == 0 or not interior:
        return []
    if count == 1:
        return [interior[len(interior) // 2]]
    return [interior[0], interior[-1]] if len(interior) >= 2 else [interior[0]]


def plan_sections(
    width: int,
    height: int,
    difficulty: int,
    rng: Any,
    vocab: dict[str, SectionArchetype] = DEFAULT_VOCAB,
    axis: str = "horizontal",
    water: str | None = None,
) -> list[PlannedSection]:
    """Deterministically compose a level into an ordered list of sections that
    TILE the axis with ``SECTION_OVERLAP`` shared cells and reach the far edge.

    The count is rolled first (``_roll_section_count`` — difficulty-banded,
    capped at :data:`MAX_SECTIONS`), then the extent is PARTITIONED among that
    many sections (``_partition_extent``) so wide levels get bigger sections,
    not more of them.
    - **horizontal**: section 0 is a gentle ``runway`` (safe spawn + run-up);
      the rest roll weighted by difficulty. ``x_off`` grows left→right.
    - **vertical**: section 0 is the BOTTOM (spawn, on a ground floor); the
      last is the TOP (exit — the climb summit). ``y_off`` bottom-aligns
      section 0, the last reaches ``y=0``.

    Deterministic in ``rng``. Returns at least one section.

    ``water`` is the level's rolled WATER TOPOLOGY (water arc), or None
    for a dry level: aquatic ``water_levels_only`` archetypes never roll
    on dry levels, a ``fully_submerged`` level composes EXCLUSIVELY from
    them (when any exist), and a ``waterline`` level mixes both pools."""
    # rooms_only archetypes (vault/lair — secret-room vocabulary) never
    # roll into a MAIN level's plan; plan_room draws them directly.
    pool = [
        n for n, a in vocab.items()
        if a.axis == axis and not getattr(a, "rooms_only", False)
    ]
    if water == "fully_submerged":
        aquatic = [
            n for n in pool if getattr(vocab[n], "water_levels_only", False)
        ]
        pool = aquatic or pool
    elif water is None:
        pool = [
            n for n in pool
            if not getattr(vocab[n], "water_levels_only", False)
        ]
    # waterline: the mixed pool (dry + aquatic) stands as-is.
    if not pool:
        pool = list(vocab)
    extent = width if axis == "horizontal" else height
    # A gentle opener only makes sense horizontally (the runway is a run-up)
    # and only when the pool actually offers one — a fully-submerged level
    # opens on an aquatic section instead.
    opener = "runway" if (axis == "horizontal" and "runway" in pool) else pool[0]

    n = _roll_section_count(extent, difficulty, rng)
    names = [opener] + [
        _roll_archetype(pool, vocab, difficulty, rng) for _ in range(n - 1)
    ]
    lengths = _partition_extent(extent, names, vocab, rng)

    sections: list[PlannedSection] = []
    pos = 0
    for name, length in zip(names, lengths):
        if axis == "horizontal":
            sections.append(PlannedSection(name, length, x_off=pos, y_off=0))
        else:
            # y grows DOWNWARD: distance-from-bottom pos maps to a top-origin
            # y_off so section 0 bottom-aligns and the last reaches y_off=0.
            sections.append(
                PlannedSection(name, length, x_off=0, y_off=extent - pos - length)
            )
        pos += length - SECTION_OVERLAP
    return sections


def plan_level(
    width: int,
    height: int,
    difficulty: int,
    rng: Any,
    vocab: dict[str, SectionArchetype] = DEFAULT_VOCAB,
    axis: str = "horizontal",
    water: str | None = None,
) -> LevelPlan:
    """Roll a level's full BLUEPRINT: the sections (``plan_sections``) plus the
    derived checkpoint assignment and the exits list. Deterministic in ``rng``
    so the placement phase recomputes the same plan without a persisted field.
    ``water`` steers the archetype pool (water arc) — every recompute site
    must pass the same rolled topology."""
    sections = plan_sections(width, height, difficulty, rng, vocab, axis, water)
    n = len(sections)
    return LevelPlan(
        axis=axis,
        sections=sections,
        checkpoint_sections=_checkpoint_sections(n),
        exits=[ExitSpec(section_idx=n - 1)],
    )


def section_owner_of_cell(
    plan: list[PlannedSection], x: int, y: int, axis: str = "horizontal"
) -> int:
    """Which section index a whole-level cell belongs to — routed by x-range
    (horizontal) or y-range (vertical). The LAST planned section whose range
    contains the coord wins the shared overlap (matching :func:`composite`'s
    non-empty-wins layering). Used to route a whole-level validator failure
    back to the section that owns it, so only THAT section regenerates."""
    coord = x if axis == "horizontal" else y
    owner = 0
    for i, ps in enumerate(plan):
        off = ps.x_off if axis == "horizontal" else ps.y_off
        if off <= coord < off + ps.length:
            owner = i
    return owner


def section_owner_of_x(plan: list[PlannedSection], x: int) -> int:
    """Horizontal-axis form of :func:`section_owner_of_cell` (kept for the
    existing horizontal call sites/tests)."""
    return section_owner_of_cell(plan, x, 0, "horizontal")


def _roll_len(arch: SectionArchetype, rng: Any) -> int:
    lo, hi = int(arch.min_len), int(arch.max_len)
    return lo if hi <= lo else int(rng.randint(lo, hi))


def _roll_archetype(
    pool: list[str], vocab: dict[str, SectionArchetype], difficulty: int, rng: Any
) -> str:
    """Weighted pick from the axis pool. Higher difficulty up-weights the
    'high' intensity archetypes; a runway is down-weighted so we don't string
    several breathers together."""
    intensity_w = {"low": 1.0, "medium": 2.0, "high": 1.0 + 0.8 * difficulty}
    weights = [
        (0.4 if n == "runway" else 1.0) * intensity_w.get(vocab[n].intensity, 1.0)
        for n in pool
    ]
    total = sum(weights)
    r = float(rng.random()) * total
    upto = 0.0
    for name, w in zip(pool, weights):
        upto += w
        if r <= upto:
            return name
    return pool[-1]


def composite(
    parts: list[tuple[Any, int, int]],
    width: int,
    height: int,
):
    """Stitch stamped section sub-grids into one whole-level ``StampResult``.

    ``parts`` = ``(StampResult, x_off, y_off)`` per section, in order. Each
    sub-grid is pasted at its origin with NON-EMPTY-WINS semantics (a later
    section's empty cells never erase an earlier section's terrain in the
    overlap — the seam stays solid). spawn comes from the first section that
    has one, exit from the LAST; hazards/triggers/free_volume are unioned and
    offset by their section origin.
    """
    import numpy as np

    from canon.packs.platformer.dsl import StampResult

    grid = np.zeros((height, width), dtype=np.int8)
    spawn: tuple[int, int] | None = None
    exit_: tuple[int, int] | None = None
    hazards: list = []
    triggers: list = []
    repairs: list[str] = []
    free_volume: set = set()

    for res, x_off, y_off in parts:
        sub = res.grid
        sh, sw = sub.shape
        for ly in range(sh):
            gy = y_off + ly
            if not 0 <= gy < height:
                continue
            for lx in range(sw):
                gx = x_off + lx
                if 0 <= gx < width and int(sub[ly, lx]) != 0:
                    grid[gy, gx] = sub[ly, lx]  # non-empty wins
        if res.spawn is not None and spawn is None:
            spawn = (res.spawn[0] + x_off, res.spawn[1] + y_off)
        if res.exit is not None:
            exit_ = (res.exit[0] + x_off, res.exit[1] + y_off)
        hazards.extend(_offset_entries(res.hazards, x_off, y_off))
        triggers.extend(_offset_entries(res.triggers, x_off, y_off))
        free_volume |= {(cx + x_off, cy + y_off) for (cx, cy) in res.free_volume}
        repairs.extend(res.repairs)

    return StampResult(
        grid=grid, spawn=spawn, exit=exit_, hazards=hazards,
        triggers=triggers, repairs=repairs, free_volume=free_volume,
    )


def _offset_entries(entries: list, x_off: int, y_off: int) -> list:
    """Shift sparse-mask entries (hazards/triggers) by a section origin."""
    out = []
    for e in entries:
        moved = e.model_copy(update={"x": e.x + x_off, "y": e.y + y_off})
        out.append(moved)
    return out
