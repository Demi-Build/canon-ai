"""GraphicsSpec — per-GAME target graphics, as data.

Resolution and art style are design decisions that vary per game, not
engine truths: the same grid plays as 16px SNES pixel art or as a 128px
pre-rendered-3D look (the Donkey Kong Country trick — a 3D *look* in
flat textures; real geometry is an engine change, out of scope). The
GameRules split applies verbatim:

- **Values are template data.** They load from a per-game JSON file
  (``graphics.json`` next to the schemas; ``--graphics`` on the runner).
  A different look edits a file, not code. Example specs proving the
  swap: ``examples/graphics_specs/{snes_pixel,rendered_hd}.json``.
- **Categories are code.** ``render_filter`` names an interpreter:
  ``crisp`` → NEAREST downscale + nearest-neighbor sampling in Godot
  (chunky pixels stay chunky); ``smooth`` → LANCZOS + linear filtering.
- **The carrier is open** (``extra="allow"``): unknown keys ride through
  inert until an enforcement point lands (per-asset-type resolutions,
  sprite scales — they join here the day those phases exist).

The spec reaches every asset phase (tilesheet today; sprites/portraits/
backgrounds when they land) so one template edit re-themes the game's
whole art direction. Display size is a SEPARATE knob: play surfaces
render cells at their own size and scale assets to fit — ``tile_px``
only sets asset density.

Deferred, deliberately: autotiling (grass-top/dirt-interior edge
variants — the real SMW-quality lever). That is registry surgery
(variant slots + adjacency → Godot terrain sets), scoped as its own
phase; this spec is where its per-style knobs will live.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: The pack's default look — the template other games copy and edit.
DEFAULT_GRAPHICS_PATH = Path(__file__).parent / "graphics.json"


class GraphicsSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    # ---- ART LANE (Arc 6: the style guide AS SCHEMA) -----------------
    #: The pinned art lane — a NAME that resolves the aesthetic
    #: ambiguity up front ("SNES-quality" splits into hand-drawn vs
    #: pre-rendered; a prompt targeting both lands in a muddy middle
    #: that reads as NES). One lane per project; the shipped lane
    #: templates in examples/graphics_specs/ are the editable style
    #: guides a project copies and tunes.
    lane: str = "hand_drawn_16bit"
    #: Color-depth target ("16-bit/256", "24-bit") — prompt context now,
    #: the quantization-policy hook later.
    color_depth: str = "16-bit/256"
    #: Reference-aesthetic TOKENS — the structured prompt fragments the
    #: lane is made of (edit these to restyle the game). Joined by
    #: ``effective_art_style()``; a non-empty ``art_style`` free-text
    #: overrides them (back-compat).
    aesthetic_tokens: list[str] = Field(default_factory=list)
    #: The ART DENSITY root: pixels per BASE CELL (the SNES grid unit).
    #: PHYSICS IS UNTOUCHED — 1 collision cell = 1 world tile = 32
    #: engine units everywhere; base_cell only sets how many true
    #: pixels of art a base cell carries. Changing it later is a cheap
    #: schema edit but an expensive REGENERATION (pixel art can't be
    #: upscaled without smearing) — freeze it before batch-generating.
    base_cell: int = Field(default=16, ge=8, le=64)
    #: How many base cells one WORLD TILE's texture spans per side.
    #: ``tile_px`` (the sheet density) must equal
    #: ``base_cell * cells_per_tile`` — validated, so the derivation is
    #: real while tile_px stays the field every phase already reads.
    cells_per_tile: int = Field(default=2, ge=1, le=8)
    #: The player's ART CANVAS in base cells (bottom-center anchored,
    #: drawn chunky OVER the existing tight ~1-cell physics hitbox —
    #: canvas and collision are deliberately separate). 4x4 at a 16px
    #: base cell = a 64px canvas.
    player_footprint: tuple[int, int] = (4, 4)
    # ------------------------------------------------------------------
    #: Asset pixels per grid cell — sheet density, NOT display size.
    tile_px: int = Field(default=32, ge=8, le=256)
    #: Free-text style OVERRIDE. Empty = derived from aesthetic_tokens
    #: (the lane way); non-empty wins verbatim (pre-lane specs keep
    #: working unchanged).
    art_style: str = "crisp 16-bit pixel art, clean dithering, hard pixel edges"
    #: Code-interpreted category: resampling + play-surface texture filter.
    render_filter: Literal["crisp", "smooth"] = "crisp"
    #: Diffusion request size; HD tiles want more source detail.
    gen_px: int = Field(default=512, ge=64, le=2048)
    #: Brightness levels per tile after palette conformance (crisp looks
    #: read as real pixel art, not shrunken photos). None = off.
    posterize_levels: int | None = Field(default=16, ge=2, le=64)
    #: Sprite asset pixels (enemies square, player 1x2). None = tile_px.
    sprite_px: int | None = Field(default=None, ge=8, le=512)
    #: Parallax scenery bands per stage, far → near. 0 = gradient sky only.
    backdrop_bands: int = Field(default=2, ge=0, le=3)
    #: An extra near-field occluder band with depth > 1 that consumers
    #: draw IN FRONT of gameplay (sparse vines/branches/rock edges
    #: framing the play area — behind the UI, over the world).
    #: OFF by default (first paid run): a layer drawn over gameplay is a
    #: CURATED art call — what it's made of and where it sits wants a
    #: human eye per level, and the generated one shipped as the same
    #: stage-wide occluder everywhere, blocking the playfield. A game
    #: opts in via its graphics.json when someone is directing that
    #: layer; per-level deliberate picks are the future shape. DIRECTION
    #: (postmortem ticket 5g): the occluder is a curated layer cradle is
    #: expected to author POST base-generation — canon keeps the opt-in
    #: producer, cradle owns the per-level call; canon never composites it
    #: blind.
    foreground_band: bool = False
    #: Visible generated-content mark: a small deterministic corner
    #: checker stamped on generated SPRITES/BANDS/SPLASH — never tiles,
    #: whose region means are palette-checked (conform + QA would fight
    #: the mark). Off by default; folds into digest() like any knob.
    visible_watermark: bool = False
    #: Camera framing: cells visible across the screen. SNES showed ~16;
    #: chosen against frame renders of 16/22/30. This is the game-wide
    #: baseline — scale stays consistent within a game.
    view_cells: int = Field(default=20, ge=8, le=60)
    #: Camera framing for VERTICAL (climb) levels: ROWS visible down the
    #: screen (the height analogue of ``view_cells``). A tall shaft frames by
    #: height so the player sees where they're jumping to. Sectioned-levels
    #: phase (chunk C).
    view_rows: int = Field(default=16, ge=8, le=60)
    #: Deliberate per-level framing exceptions, keyed by the stage plan's
    #: view hint ("intimate" = tight claustrophobic moments, "vista" =
    #: zoomed-out reveals meant to instill awe). Values are cells-across,
    #: used SPARINGLY — the stage prompt says most levels stay standard.
    view_presets: dict[str, int] = Field(
        default_factory=lambda: {"intimate": 14, "vista": 30}
    )
    #: Actor overdraw: sprites drawn this many times their hitbox
    #: (uniform, feet-anchored, physics untouched) — the SNES trick of
    #: heroes taller than a tile. 1.0 = sprites stay inside their cell.
    actor_scale: float = Field(default=1.4, ge=1.0, le=2.5)
    #: Volume (water) tile opacity on every DRAW surface — presentation
    #: only: the tile ART stays opaque on disk and physics/collision
    #: never read it. Consumers composite the tile at this alpha over
    #: whatever sits behind (backdrop bands / sky gradient).
    water_alpha: float = Field(default=0.55, ge=0.0, le=1.0)
    #: Per-frame duration (ms) for VLM-authored sprite animation. Per-game
    #: feel knob (data): lower = snappier cycles. The state frame COUNTS come
    #: from the VLM spec (clamped 2-6); this only sets playback tempo.
    anim_frame_ms: int = Field(default=120, ge=16, le=2000)

    @model_validator(mode="after")
    def _reconcile_density(self) -> GraphicsSpec:
        """``tile_px`` is what every phase reads; ``base_cell`` is the
        ART root. A legacy spec that declares only ``tile_px`` gets
        ``base_cell`` back-derived (tile_px / cells_per_tile, falling
        back to a 1:1 tile) so the lane fields are always coherent —
        pre-lane specs load unchanged, lane specs that declare both
        must already agree."""
        if self.tile_px != self.base_cell * self.cells_per_tile:
            if (
                self.tile_px % self.cells_per_tile == 0
                and 8 <= self.tile_px // self.cells_per_tile <= 64
            ):
                self.base_cell = self.tile_px // self.cells_per_tile
            else:
                self.cells_per_tile = 1
                self.base_cell = max(8, min(64, self.tile_px))
        return self

    def effective_art_style(self) -> str:
        """The style clause every asset prompt injects: the lane's
        aesthetic tokens joined, unless the free-text ``art_style``
        override is non-empty (back-compat — pre-lane specs declare it)."""
        if self.art_style:
            return self.art_style
        return ", ".join(self.aesthetic_tokens)

    def art_footprint(self, size: float) -> tuple[int, int]:
        """An actor's ART CANVAS in base cells from its physics size
        tier (1.0→2x2, 1.5→3x3, 2.0→4x4 at cells_per_tile=2): the
        canvas scales in WHOLE base cells at one shared density —
        bigger means more cells authored natively, never a runtime
        stretch."""
        import math

        side = max(1, math.ceil(float(size) * self.cells_per_tile))
        return (side, side)

    def sprite_size(self) -> int:
        return self.sprite_px if self.sprite_px is not None else self.tile_px

    def view_for(self, hint: str) -> int | None:
        """Resolve a stage-plan view hint to a per-level cells-across
        override. "standard" / unknown → None (game-global view_cells);
        presets clamp to the view_cells bounds so a hand-edited spec
        can't produce an unrenderable framing."""
        cells = self.view_presets.get(hint)
        if cells is None:
            return None
        return max(8, min(60, int(cells)))

    def digest(self) -> str:
        """Stable short digest of the spec — folded into tileset
        provenance so a graphics swap invalidates like a model bump even
        when the (fake/placeholder) sheet bytes happen to match."""
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def load_graphics(path: str | Path = DEFAULT_GRAPHICS_PATH) -> GraphicsSpec:
    """Load a game's graphics spec. Unknown keys are preserved (inert
    until an enforcement point exists); known keys are validated."""
    return GraphicsSpec.model_validate(json.loads(Path(path).read_text()))


DEFAULT_GRAPHICS = load_graphics()
