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

#: Columns/rows two adjacent sections share so the seam is continuous (the
#: overlap the seam summary describes; matches the handoff §1 ~6-col overlap).
SECTION_OVERLAP = 6


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


def plan_sections(
    width: int,
    height: int,
    difficulty: int,
    rng: Any,
    vocab: dict[str, SectionArchetype] = DEFAULT_VOCAB,
    axis: str = "horizontal",
) -> list[PlannedSection]:
    """Deterministically compose a level of ``width`` into an ordered list of
    sections that tile the axis with ``SECTION_OVERLAP`` shared columns.

    v1 (horizontal): section 0 is always a gentle ``runway`` (a safe spawn +
    room to build speed); the rest roll from the axis's archetypes, weighted so
    higher difficulty leans toward the intense ones. Deterministic in ``rng``;
    an LLM-planned composition is a later enhancement. Returns at least one
    section (a lone runway for a tiny level)."""
    pool = [n for n, a in vocab.items() if a.axis == axis]
    if not pool:
        pool = list(vocab)
    opener = "runway" if "runway" in vocab else pool[0]

    sections: list[PlannedSection] = []
    x = 0
    first = True
    # Reserve room so the last section reaches the level's right edge.
    while x < width - 1:
        remaining = width - x
        name = opener if first else _roll_archetype(pool, vocab, difficulty, rng)
        arch = vocab[name]
        length = _roll_len(arch, rng)
        # The final section takes whatever is left (so exit lands at the edge).
        if remaining <= arch.max_len + SECTION_OVERLAP:
            length = remaining
        length = max(2, min(length, remaining))
        sections.append(PlannedSection(name, length, x_off=x, y_off=0))
        x += length - SECTION_OVERLAP
        first = False
        if length >= remaining:
            break
    if not sections:
        sections.append(PlannedSection(opener, max(2, width), 0, 0))
    return sections


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

    from examples.platformer_pack.dsl import StampResult

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
