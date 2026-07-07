"""Platformer Bible models (PRD §6.1) — Game 2's entity shapes.

Design rules these models encode:

- **Bible-complete from day 1**: everything a downstream adapter needs to
  materialize the entity lives on the model (or is referenced by relative
  path + content hash). No stubs.
- **Definitions vs placements**: an ``EnemyDefinition`` is authored once and
  addressed globally (``enemy:<id>``); a ``Level`` holds *placements* that
  reference it — never copies.
- **Grids by reference** (§6.2): dense masks (collision/terrain/background)
  are ``.npz`` files referenced by output_dir-relative path, with a sibling
  ``*_hash`` field on the owning ``Level`` (§6.3 — the hash lives on the
  Bible entity, not in the file). Never add an inline ``list[list[int]]``
  grid here.
- **Relative paths only** (§6.5): every path field is output_dir-relative;
  the adapter resolves to absolute at write time.

Fields are deliberately lean — Phase 3's generation phases richen them
against real data. Closed sets (archetype, rig_type) are enforced by the
user-editable schemas (§4), not hardcoded in the models.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Any

from pydantic import BaseModel, Field

from canon.bible.artifacts import ArtifactMeta

# ---------------------------------------------------------------------------
# Tile types (§6.2 collision int-enum)
# ---------------------------------------------------------------------------


class TileType(IntEnum):
    """Collision-grid cell values. The LevelSchema (§4.2) owns the
    authoritative, user-extensible set; these are the framework defaults.
    Numbering bands are deliberate: solids < 10, hazards >= 10 < 20,
    traversable volumes >= 20 (PRD Appendix E.1)."""

    EMPTY = 0
    FLOOR = 1
    PLATFORM = 2
    WALL = 3
    SPIKE = 10
    WATER = 20


# ---------------------------------------------------------------------------
# Component models (attached, not globally addressed)
# ---------------------------------------------------------------------------


class RigPart(BaseModel):
    """One image part of a rig (e.g. body.png, wings.png)."""

    name: str
    image_path: str = ""  # output_dir-relative
    anchor: tuple[int, int] | None = None  # pixel anchor within the part


class RigManifest(BaseModel):
    """Typed rig description attached to enemy/boss/character entities
    (§6.1 — explicitly NOT ``extra``). No ``rig:`` namespace exists, so
    rigs are components, not addressable artifacts."""

    rig_type: str  # closed set via RigSchema
    parts: list[RigPart] = Field(default_factory=list)
    anchors: dict[str, tuple[int, int]] = Field(default_factory=dict)
    animations: dict[str, dict] = Field(default_factory=dict)


class Placement(BaseModel):
    """A reference to a definition, positioned in a level (§6.1).

    ``ref`` is a global artifact ID (``enemy:<id>``, ``item:<id>``).
    ``overrides`` carries per-instance tweaks (an "elite" spawn); the
    canonical definition is the global artifact.
    """

    ref: str
    pos: tuple[int, int]  # grid cells
    overrides: dict[str, Any] = Field(default_factory=dict)


class SparseMaskEntry(BaseModel):
    """One record of a sparse mask layer (§6.2): hazards, triggers,
    foreground decoration."""

    x: int
    y: int
    type: str
    params: dict[str, Any] = Field(default_factory=dict)


class TileSlot(BaseModel):
    """One slot of a tilesheet: which region is which TileType.

    ``collision`` (PRD Appendix E.3) carries the tile's physics semantics
    so consumers derive behavior from the tileset manifest instead of
    hardcoding tile IDs — the seam where per-tile collision shapes attach
    when real art arrives. Empty string = derive from tile_type (back-compat
    for pre-3a manifests).
    """

    index: int
    tile_type: TileType
    px_region: tuple[int, int, int, int] | None = None  # x, y, w, h in pixels
    collision: str = ""  # "solid" | "one_way" | "none" | "hazard" | "water"


# ---------------------------------------------------------------------------
# Addressable artifacts
# ---------------------------------------------------------------------------


class World(ArtifactMeta):
    """The stage graph — one per game. Addressed ``world``."""

    title: str = ""
    stage_ids: list[str] = Field(default_factory=list)
    edges: list[tuple[str, str]] = Field(default_factory=list)  # connectivity
    unlock_rules: dict[str, Any] = Field(default_factory=dict)


class Stage(ArtifactMeta):
    """A themed group of levels. Addressed ``stage:<id>``."""

    stage_id: str
    theme: str = ""
    enemy_refs: list[str] = Field(default_factory=list)  # "enemy:<id>"
    boss_ref: str = ""  # "boss:<id>"
    level_ids: list[str] = Field(default_factory=list)
    tileset_ref: str = ""  # "tileset:<stage_id>"


class Level(ArtifactMeta):
    """One playable level. Bible-complete; decomposes into step artifacts
    (``level:<stage_id>/<level_id>/<step>``) for regen/resume addressing.

    Dense masks are referenced (path + hash); sparse masks are inline
    record lists; entities are placements referencing global definitions.
    """

    level_id: str
    stage_id: str
    grid_width: int = 0  # cells
    grid_height: int = 0
    pixels_per_cell: int | None = None  # None → ProjectConfig global (§4.2)

    # First-class point markers (standing-cell grid coords). These are level
    # structure, not gameplay triggers — every consumer (validators, render,
    # harness, Godot) needs them, so they don't ride in ``triggers``.
    spawn: tuple[int, int] | None = None
    exit: tuple[int, int] | None = None

    # Dense masks — .npz by relative path, hash on the entity (§6.2, §6.3)
    collision: str = ""
    collision_hash: str = ""
    terrain: str = ""
    terrain_hash: str = ""
    background: str = ""
    background_hash: str = ""

    # Sparse masks — inline records (§6.2), mirrored to per-layer JSON
    # files whose content hashes live below (§6.3).
    hazards: list[SparseMaskEntry] = Field(default_factory=list)
    triggers: list[SparseMaskEntry] = Field(default_factory=list)
    foreground: list[SparseMaskEntry] = Field(default_factory=list)
    hazards_hash: str = ""
    triggers_hash: str = ""
    foreground_hash: str = ""
    entities_hash: str = ""

    # Placements — references, never copies (§6.1)
    entities: list[Placement] = Field(default_factory=list)

    # Per-step parent edges (§6.1 within-level chain), recorded now so the
    # Phase 2 orchestrator has real edges to walk. Keyed by step name
    # ("collision", "terrain", …) → parent artifact IDs.
    step_parents: dict[str, list[str]] = Field(default_factory=dict)


class EnemyDefinition(ArtifactMeta):
    """A globally-addressed enemy definition (``enemy:<id>``), reused
    across levels via placements."""

    enemy_id: str
    name: str = ""
    archetype: str = ""  # closed set via EnemySchema
    stats: dict[str, Any] = Field(default_factory=dict)
    behavior: dict[str, Any] = Field(default_factory=dict)
    rig: RigManifest | None = None
    portrait_path: str = ""  # output_dir-relative


class BossDefinition(EnemyDefinition):
    """Extends EnemyDefinition with phases + arena constraints.
    Addressed ``boss:<id>``."""

    phases: list[dict] = Field(default_factory=list)
    arena: dict[str, Any] = Field(default_factory=dict)


class Tileset(ArtifactMeta):
    """A stage's tilesheet + slot metadata. Addressed ``tileset:<stage_id>``."""

    stage_id: str
    tilesheet_path: str = ""  # output_dir-relative PNG
    tilesheet_hash: str = ""
    slots: list[TileSlot] = Field(default_factory=list)
