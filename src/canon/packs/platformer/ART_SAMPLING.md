# Art sampling — pick a look BEFORE you pay for a game's worth of it

This is the hand-off doc for the art-template system's **SAMPLE mode**.
The doctrine is simple: **all paid and credit-based generation is run by
YOU, at your terminal** — Claude preps commands, wires backends, and
stops at the spend boundary. Sampling exists so the first full-game art
run is a commitment you already believe in, not an experiment at $0.04
a tile.

The workflow: pick a lane → generate ONE exemplar per wired backend →
judge side by side → tune → **lock** → batch.

---

## 1. Lanes — the style guide IS a file

**One lane per project.** "SNES-quality" is not a style — it splits into
hand-drawn vs pre-rendered, and a prompt that targets both lands in a
muddy middle that reads as NES. A lane resolves the ambiguity up front;
the lane file is the editable style guide.

| Lane file (`src/canon/packs/platformer/graphics_specs/`) | The look | Density |
|---|---|---|
| `hand_drawn_16bit.json` | Super Mario World — bold outlines, flat cel shading, no dithering | 16px cell, 32px tile, posterize 16 |
| `prerendered_16bit.json` | Donkey Kong Country — glossy 3D-baked sprites, heavy ordered dithering, rim light | 16px cell, 32px tile, posterize 32 |
| `modern_hd.json` | Contemporary 2.5D — rounded toy-like forms, soft AO, smooth filtering | 32px cell, 64px tile, no posterize |

Switching lanes is one flag: `--graphics src/canon/packs/platformer/graphics_specs/<lane>.json`.
Tuning a lane is editing its JSON — the `aesthetic_tokens` list is the
prompt fragment set every asset prompt injects (via
`GraphicsSpec.effective_art_style()`), and the density/filter knobs
(`base_cell`, `tile_px`, `render_filter`, `posterize_levels`,
`gen_px`, `backdrop_bands`) are the code-enforced half. Don't fork the
code to restyle a game; copy the lane file and edit it.

One knob is a commitment: **`base_cell` freezes before batch
generation.** It is the art density root — pixel art cannot be upscaled
without smearing, so changing it later is a cheap schema edit but a
full, expensive regeneration. Sampling is where you decide it.

---

## 2. Backends — where the pixels come from

Backends are constructed only from an explicit flag, never implied, and
fail fast at launch on missing credentials (a `.env` file is NOT read
automatically — `set -a; source .env; set +a` first).

| Backend | Credential | Pricing | Character |
|---|---|---|---|
| **fal** (nano-banana default) | `FAL_KEY` (or `FAL_KEY_ID`+`FAL_KEY_SECRET`) | per image, ~$0.04 | General diffusion model. Strong texture/creature invention; knows nothing about pixel grids — the pipeline's post-process does the conforming (see §5). |
| **Retro Diffusion** | `RD_API_KEY` | credit-based — see provider pricing | Pixel-art-native. The `rd_pro__platformer` style targets exactly this genre; output arrives grid-aligned and palette-disciplined, so post-processing has less to repair. |
| **PixelLab** | `PIXELLAB_SECRET` (or `PIXELLAB_API_KEY`, the dashboard's name) | subscription — see provider pricing | Pixel-art-native with first-class **animation and tileset** endpoints — the strongest fit for sprite sheets and autotile work. Also exposes an MCP server at `https://api.pixellab.ai/mcp`; wiring that in as an inline generation path is a documented future upgrade, not part of this chunk. |

The rule of thumb the samples will confirm or refute for your project:
general models (fal) win on invention and the `modern_hd` lane; native
pixel backends win on grid discipline in the two 16-bit lanes. Don't
take the rule of thumb — take the samples.

---

## 3. Sample mode — one exemplar, judged side by side

Do NOT sample by running the pipeline. Sample mode generates **one
exemplar set** — the player sprite, one representative tile, and a
palette test — per wired backend, from the same lane file, into a
side-by-side directory. Three images per backend is enough to judge
everything that matters: silhouette quality (player), tiling/material
quality (tile), and whether the backend respects your palette (palette
test).

The loop, which **you** run (each iteration is a handful of images, not
a game):

1. **Generate** the exemplar through every backend you hold credentials
   for, same lane file, same seed:

   ```bash
   uv run python -m canon.packs.platformer.run_slice \
     --art-sample player --sample-backends fake,fal,retro,pixellab \
     --graphics src/canon/packs/platformer/graphics_specs/hand_drawn_16bit.json \
     --image-model <optional> --output-dir sample_out
   ```

   Subjects: `player`, `tile:<name>` (e.g. `tile:floor`), `palette`.
   The run writes `sample/<subject>/<backend>.png`, a
   `<backend>_griddrop.png` per backend (the exemplar composited into
   a real grid context with canvas/hitbox/anchor boxes drawn — passing
   image QA is not the same as working IN the grid), and a labelled
   `contact_sheet.png`. Backends with missing credentials report
   "unavailable" on the sheet instead of sinking the run.
2. **Judge side by side.** Open the contact sheet; look at the player
   sprites next to each other at play scale, and at the grid-drop. The
   questions: does the silhouette read? Do the tile's copies connect
   into one material or dotted mush? Did the palette survive? Does the
   canvas sit right on the anchor?
3. **Iterate on the lane, not the backend.** Edit `aesthetic_tokens`,
   `posterize_levels`, `gen_px`; regenerate the exemplars. Style
   problems are lane-file edits; grid problems are the post-process's
   job (§5).
4. **LOCK.** When one backend + lane combination wins, freeze it as
   `art_lock.json` — **backend + model(s) + graphics lane + palette +
   seed + `status: "approved"`, as data** (see `art_lock.py` for the
   full field set; hand-edit the file, the approval IS the status
   flip). Then batch:

   ```bash
   uv run python -m canon.packs.platformer.run_slice \
     --backend anthropic --art-lock art_lock.json ... --output-dir game
   ```

   Batch generation REFUSES a non-approved lock, and the lock's
   backend/model/seed override the CLI flags — a resumed run can't
   quietly regress your art to a different backend or density (the
   placeholder-over-paid-tilesheet failure mode, made structurally
   impossible). Failures land in `review/art_report.json` with the
   exact `canon regen ... --mark-only` command per asset — never a
   silent retry loop.

---

## 4. What a sample RUN costs (hand-computed)

When the exemplars have converged and you want to see the look on a real
playable slice, run a **trimmed static sample**: 1 stage, 2 levels, 2
enemies, 2 items. The image bill, by hand:

| Asset class | Count |
|---|---|
| Tiles (one per unique NAME — the 16 floor autotile variants are code-derived from the one floor generation, free) | ~10 |
| Backdrop bands (far, mid + the foreground occluder) | 3 |
| Props (checkpoint, exit + dust/splash/sparkle VFX) | 5 |
| Splash / studio card (per world) | 1 |
| Player | 1 |
| Enemy bases | 2 |
| Item bases | 2 |
| **Total** | **≈ 24 images** |

At fal's ~$0.04/image that is **≈ $1** per sample run — cheap enough
to run one per serious lane candidate. Retro Diffusion and PixelLab
price in credits/subscription tiers: see provider pricing before
batching. (`cost_model.json` carries the same numbers for `canon
estimate`: `images_per_stage: 18`, `images_world: 1`.)

**Inventory coverage (the v1 checklist).** Generated per game: tiles
(with autotile floor variants), backdrop bands + foreground band,
player + per-state animation strips/atlas, enemy bases + animations,
item sprites, props + VFX sprites, splash card, music + SFX. QA's
`coverage` record in every stage report names the ACCEPTED v1 gaps out
loud: the HUD pixel font and world-map art stay code-drawn (listed,
not generated).

Two riders:

- **Animation sheets are extra and need the VLM.** Sprite animation is
  VLM-authored (a vision model interrogates the base sprite before the
  sheet generates), so an animated sample additionally requires
  `--vlm-backend anthropic` (`ANTHROPIC_API_KEY`) on top of the image
  backend. Static-first is the intended sampling order.
- Text generation is billed separately (`--backend anthropic`); `canon
  estimate` forecasts the whole run — including images — before you
  spend anything.

---

## 5. Grid-snap — why nano output still lands on the grid

General diffusion models do not think in pixel grids: nano-banana
renders "pixel art" whose pseudo-pixels drift off any fixed lattice
(3.7-pixel "pixels", stair-stepped edges that shimmer when tiled). This
is expected, and it is not a reason to reject a fal sample — the
pipeline runs a **mandatory grid-snap post-process** on every generated
asset: resample to the lane's true grid, then palette-conform and
posterize (`conform_to_palette`), so what ships is genuinely on the
`base_cell` lattice with quantized shading that reads as real pixel
art.

Native pixel backends (Retro Diffusion, PixelLab) largely conform
already — the snap is a near-no-op on their output. Judge their samples
on style; judge fal's samples **after** the snap, never on the raw
generation.
