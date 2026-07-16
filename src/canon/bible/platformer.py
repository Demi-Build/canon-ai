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
from typing import Any, Literal

from pydantic import BaseModel, Field

from canon.bible.artifacts import ArtifactMeta

# ---------------------------------------------------------------------------
# Tile types (§6.2 collision int-enum)
# ---------------------------------------------------------------------------


class TileType(IntEnum):
    """Collision-grid cell values — the FRAMEWORK DEFAULT vocabulary.

    Since Phase 3b the authoritative per-game set is a tile registry file
    (``tile_types.json``, values-in-data); this enum mirrors the default
    registry so framework code and tests have stable names. Numbering
    bands are deliberate and registry-enforced: solids < 10, hazards
    >= 10 < 20, traversable volumes >= 20 (PRD Appendix E.1)."""

    EMPTY = 0
    FLOOR = 1
    PLATFORM = 2
    WALL = 3
    BREAKABLE = 4  # solid foothold that crumbles after a fuse (sectioned-levels D)
    BOX = 5  # solid item container, broken by head-bump/stomp (items arc)
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
    """One slot of a tilesheet: which region is which tile id.

    ``collision`` (PRD Appendix E.3) carries the tile's physics CATEGORY
    so consumers derive behavior from the tileset manifest instead of
    hardcoding tile IDs — the seam where per-tile collision shapes attach
    when real art arrives. Since 3b the vocabulary is the tile-registry
    category set: "empty" | "solid" | "one_way" | "hazard" | "volume"
    (pre-3b manifests used "none"/"water" for the last two). ``params``
    carries the category's data knobs (volume speed_factor/gravity/
    impulse/damage_per_second, hazard damage, ...) — values in data,
    interpreters in code. ``tile_type`` is a plain int because registries
    define game-specific ids beyond the framework-default ``TileType``.
    """

    index: int
    tile_type: int
    name: str = ""  # registry tile name ("water", "lava", ...)
    px_region: tuple[int, int, int, int] | None = None  # x, y, w, h in pixels
    collision: str = ""  # category: "empty"|"solid"|"one_way"|"hazard"|"volume"
    params: dict[str, Any] = Field(default_factory=dict)


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
    #: One-word biome name ("forest", "caves", ...) — the world-map area
    #: this stage's levels cluster under, and the habitat vocabulary the
    #: enemy ecology filters on. Empty on pre-multi-stage bibles.
    biome: str = ""
    enemy_refs: list[str] = Field(default_factory=list)  # "enemy:<id>"
    boss_ref: str = ""  # "boss:<id>"
    level_ids: list[str] = Field(default_factory=list)
    tileset_ref: str = ""  # "tileset:<stage_id>"
    #: Ambient effect records ``{"name": ..., "params": {...}}`` — values
    #: are data (LLM-chosen from the effect vocabulary); the KINDS are
    #: code interpreters in the play surfaces. Unknown names ride inert.
    effects: list[dict] = Field(default_factory=list)


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
    #: Deliberate per-level camera framing override (cells across), set
    #: from the stage plan's view hint — None means the game-global
    #: GraphicsSpec.view_cells. Sparse by design: scale stays consistent
    #: within a game except where zooming out should instill emotion.
    view_cells: int | None = None
    #: Which AXIS the level is composed along (sectioned-levels phase):
    #: "horizontal" (side-scroll, exit at the right edge) or "vertical" (a
    #: climb, spawn at the BOTTOM, exit at the TOP). Consumers frame + judge
    #: the win by this. Additive — pre-sectioned bibles default to horizontal.
    layout_axis: str = "horizontal"
    #: Camera framing for a VERTICAL level: how many ROWS span the viewport
    #: (the height analogue of ``view_cells``). None → the game default. Only
    #: meaningful when ``layout_axis == "vertical"``.
    view_rows: int | None = None
    #: The stage-planning brief this level was generated against. Persisted
    #: so per-step REGENERATION (placement/decor re-rolls on a loaded
    #: Bible) prompts with the same context the original run had.
    brief: str = ""
    #: True when every layout attempt failed validation and the flat
    #: guaranteed-valid fallback was stamped instead of generated content —
    #: a fallback level must stay queryable, not pass as a success. The
    #: attempt trace lands in review/<stage>/<level>_layout_attempts.json.
    layout_fallback: bool = False

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
    items_hash: str = ""

    # Placements — references, never copies (§6.1)
    entities: list[Placement] = Field(default_factory=list)
    #: Item placements (Arc 2): ``ref item:<id>``, with
    #: ``overrides["source"]`` = "trail" | "reward" | "box". A "box"
    #: placement's cell also carries a solid BOX TILE that every consumer
    #: OVERLAYS onto the collision grid at load — the items layer never
    #: rewrites collision.npz (a parent step).
    items: list[Placement] = Field(default_factory=list)

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
    #: Body scale in CELLS — schema-rolled from a discrete set (v4:
    #: {1.0, 1.5, 2.0}); typed here because placement validation, touch
    #: boxes, and render scale all read it (combat v1). The EFFECTIVE
    #: size everywhere is ``definition.size * variant.size`` (the variant
    #: multiplier rides on the placement). Code tolerates any positive
    #: float so hand-edited definitions stay loadable.
    size: float = 1.0
    #: Ecology (multi-stage worlds): how often this creature should be
    #: met ("common" | "uncommon" | "rare" — schema v5 rolls it; rarity
    #: caps in GameRules bound per-level placements) and which biomes it
    #: inhabits (["*"] = the whole world; else stage-biome names). Both
    #: default to the pre-v5 behavior: everywhere, uncapped tier.
    rarity: str = "common"
    habitats: list[str] = Field(default_factory=lambda: ["*"])
    stats: dict[str, Any] = Field(default_factory=dict)
    behavior: dict[str, Any] = Field(default_factory=dict)
    rig: RigManifest | None = None
    portrait_path: str = ""  # output_dir-relative
    #: Generated sprite (art track). Addressed sprite/enemy/<id>/base.png —
    #: the /base leaf reserves the namespace for future SKINS (variants,
    #: powerups) and rigged animation frames beside it, no migration.
    sprite_path: str = ""  # output_dir-relative; "" = consumer placeholder
    sprite_hash: str = ""


class BossDefinition(EnemyDefinition):
    """Extends EnemyDefinition with phases + arena constraints.
    Addressed ``boss:<id>``."""

    phases: list[dict] = Field(default_factory=list)
    arena: dict[str, Any] = Field(default_factory=dict)


class ItemDefinition(ArtifactMeta):
    """A globally-addressed item definition (``item:<id>``), reused across
    levels via placements — coins, heals, and power-ups. The mechanical
    KIND set is closed in code (consumers implement each kind's effect);
    everything numeric rides in ``params`` (schema-rolled bands: timed
    power-up durations, boost multipliers, heal amounts), and the LLM
    authors only name/flavor. Developers review and evolve the generated
    JSON like any canon artifact."""

    item_id: str
    name: str = ""
    #: Closed mechanical set: "coin" | "heal" | "shield" | "double_jump"
    #: | "run_boost". Consumers switch on it; new kinds arrive with their
    #: both-surface effect code.
    kind: str = "coin"
    #: Placement-frequency tier ("common"|"uncommon"|"rare") — the item
    #: pass biases counts by it (coins common and FREQUENT by doctrine).
    rarity: str = "common"
    #: Rolled mechanics per kind: duration_s (timed power-ups),
    #: heal_amount, coin_value, boost_mult. Open dict — hand-tuned games
    #: can add knobs their consumers read.
    params: dict[str, Any] = Field(default_factory=dict)
    stats: dict[str, Any] = Field(default_factory=dict)
    sprite_path: str = ""  # output_dir-relative; "" = consumer placeholder
    sprite_hash: str = ""


class PlayerDefinition(ArtifactMeta):
    """The player character. Addressed ``player`` (one per game). Created
    by the sprite art phase to own ``sprite/player/base.png`` — before
    this entity existed the player sprite was untracked (no hash, no
    edit detection, unpinnable, re-rolled on every sprite_art run).
    Combat-era fields (hearts, movement extras) land here later."""

    sprite_path: str = ""  # output_dir-relative; "" = consumer placeholder
    sprite_hash: str = ""
    #: VLM-authored per-state animation manifest (art track): the player's
    #: analog of ``EnemyDefinition.stats["animation"]`` — states → {path, hash,
    #: frames, frame_width, frame_height, duration_ms}. Empty = static sprite.
    animation: dict[str, Any] = Field(default_factory=dict)


class StageProps(ArtifactMeta):
    """A stage's generated gameplay-prop sprites (checkpoint flag, exit
    goal). Addressed ``props:<stage_id>`` — its own artifact so a prop
    re-roll never cascades through levels, and one artifact per stage so
    props stay themed to the stage that shows them.

    ``prop_paths`` maps prop name → output-relative file; ``prop_hashes``
    maps each file path to its content hash (multi-file artifact, one
    owner — the ``field:key`` adoption pattern backdrop bands proved).
    The prop NAME set is closed in code (consumers interpret each name);
    absent entries mean the consumer draws its placeholder shape.
    """

    stage_id: str
    prop_paths: dict[str, str] = Field(default_factory=dict)
    prop_hashes: dict[str, str] = Field(default_factory=dict)


class StageAudio(ArtifactMeta):
    """A stage's generated audio: one looping music theme + a small SFX
    set. Addressed ``audio:<stage_id>`` — its own artifact (like
    Backdrop) so a re-rolled theme never cascades staleness through the
    stage's levels.

    ``sfx_paths`` maps event name → output-relative file; ``sfx_hashes``
    maps each file path to its content hash (multi-file artifact, one
    owner — the ``field:key`` adoption pattern backdrop bands proved).
    """

    stage_id: str
    music_path: str = ""
    music_hash: str = ""
    sfx_paths: dict[str, str] = Field(default_factory=dict)
    sfx_hashes: dict[str, str] = Field(default_factory=dict)


class Backdrop(ArtifactMeta):
    """A stage's parallax scenery bands. Addressed ``backdrop:<stage_id>``
    — its OWN artifact (not fields on Stage) so a hand-edited band PNG
    marks only the backdrop user-edited instead of cascading staleness
    through every level under the stage.

    ``band_hashes`` maps each band's output-relative path to its content
    hash (multi-file artifact, one owner). Bands are ordered far → near;
    ``depths`` are the parallax scroll factors consumers apply (0 = fixed
    to camera, 1 = moves with the world).
    """

    stage_id: str
    band_paths: list[str] = Field(default_factory=list)
    band_hashes: dict[str, str] = Field(default_factory=dict)
    depths: list[float] = Field(default_factory=list)


class Tileset(ArtifactMeta):
    """A stage's tilesheet + slot metadata. Addressed ``tileset:<stage_id>``.

    ``palette`` records the color_role → hex map the sheet was actually
    painted with (style-guide agent output, or the placeholder fallback)
    — Bible-complete: reviewers and future art tools read it from here.
    """

    stage_id: str
    tilesheet_path: str = ""  # output_dir-relative PNG
    tilesheet_hash: str = ""
    slots: list[TileSlot] = Field(default_factory=list)
    palette: dict[str, str] = Field(default_factory=dict)
    #: GraphicsSpec category consumers map to texture sampling: "crisp"
    #: → nearest-neighbor (pixel art stays chunky), "smooth" → linear.
    render_filter: Literal["crisp", "smooth"] = "crisp"
