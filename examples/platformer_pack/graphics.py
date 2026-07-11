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

from pydantic import BaseModel, ConfigDict, Field

#: The pack's default look — the template other games copy and edit.
DEFAULT_GRAPHICS_PATH = Path(__file__).parent / "graphics.json"


class GraphicsSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    #: Asset pixels per grid cell — sheet density, NOT display size.
    tile_px: int = Field(default=32, ge=8, le=256)
    #: Free-text style fragment injected into every asset prompt.
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
    #: Camera framing: cells visible across the screen. SNES showed ~16;
    #: chosen against frame renders of 16/22/30. This is the game-wide
    #: baseline — scale stays consistent within a game.
    view_cells: int = Field(default=20, ge=8, le=60)
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
    #: Per-frame duration (ms) for VLM-authored sprite animation. Per-game
    #: feel knob (data): lower = snappier cycles. The state frame COUNTS come
    #: from the VLM spec (clamped 2-6); this only sets playback tempo.
    anim_frame_ms: int = Field(default=120, ge=16, le=2000)

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
