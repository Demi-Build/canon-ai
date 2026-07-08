# Canon Platformer Pack

Generate a complete, playable 2D platformer — world lore, levels, enemies,
combat tuning, tile art, parallax scenery, sprites, music, and SFX — from
one command. Built on [canon](../../README.md)'s World-Bible +
skeleton-roll + validate-and-retry machinery.

The design stance that makes it work: **the LLM makes design choices,
code does everything computable.** Agents emit tiny programs (a layout
DSL) and JSON records; deterministic tools expand, validate, and — where
the fix is arithmetic — repair them. Numbers live in data files you can
edit, not in code. Everything regenerates piece by piece.

```
world → stage plan → style guide → enemy definitions → tileset
      → per level: layout (DSL → stamp → validators/repair) → terrain
        → background → enemy placement → decor
      → ART AT THE END: tilesheet, sprites, backdrop, audio
      → review renders → manifest → (optional) Godot project export
```

Key vocabulary:

- **Bible** (`bible.json`) — the single source of truth. Every artifact's
  content hash lives here; hand-edits are detected against it.
- **Definitions vs placements** — an enemy is authored once
  (`enemy/<id>.json`); levels hold *placements* that reference it, with
  optional per-instance `variant` markers.
- **Validator messages are prompts** — when generated content is
  rejected, the rejection text is written to be fed straight back to the
  model, with located cells and concrete ops to add.
- **Repair, not re-roll** — reachability breaks get auto-bridged
  platforms, misplaced checkpoints snap to valid columns, pools poured on
  the ground row snap one row up, spawn-crowding enemies get column-nudged.
  The agent keeps design authorship; geometry is tool work.

---

## Quickstart ($0, fully deterministic)

```bash
# generate a game with placeholder art (flat colored tiles)
uv run python examples/run_platformer_slice.py \
  --backend fake --engine godot --output-dir /tmp/my_game --seed emberfall_001

# look at it: per-level review PNGs (block + skinned pairs)
open /tmp/my_game/review/

# play it in Godot (4.3+)
godot --path /tmp/my_game
PLAT_LEVEL=l3 godot --path /tmp/my_game     # jump straight to one level

# or play it in the pygame harness (quick pre-art surface)
uv run --extra platformer --extra play \
  python examples/platformer_play.py /tmp/my_game l1
```

The `fake` backend is a canned responder that exercises every feature of
the pipeline; the same seed always produces byte-identical output. Swap
`--seed` for a different game.

Three ways to look at a generated game:

| Surface | What it is | Art |
|---|---|---|
| `review/*.png` | Flat renders of every level, block (analytic) + skinned (what it looks like) + roster legend | whatever exists |
| pygame harness | Throwaway gameplay/physics/music checker — combat parity, no camera/view features | placeholder-friendly |
| Godot project | The art surface of record: camera zoom framing, parallax, audio, per-level views | the real game |

---

## Real generation (paid backends — explicit flags ONLY)

No paid backend is ever implied. Each one is opt-in by flag and
fail-fast on missing credentials:

| Flag | Backend | Credential |
|---|---|---|
| `--backend anthropic` | Claude for all text generation | `ANTHROPIC_API_KEY` |
| `--image-backend fal` | Diffusion tiles/sprites/backdrops (nano-banana) | `FAL_KEY` (or `FAL_KEY_ID`+`FAL_KEY_SECRET`) |
| `--music-backend lyria` | One looping stage theme | `GOOGLE_API_KEY` |
| `--sfx-backend elevenlabs` | The closed SFX event set | `ELEVENLABS_API_KEY` |

A `.env` file is **not** read automatically — export first:

```bash
set -a; source .env; set +a
uv run python examples/run_platformer_slice.py \
  --backend anthropic --image-backend fal \
  --music-backend lyria --sfx-backend elevenlabs \
  --engine godot --orchestrate \
  --output-dir ~/my_real_game --seed my_seed_001
```

Every paid backend also has a `fake` twin (`--image-backend fake`, etc.)
that exercises the identical code path deterministically at $0.

---

## Make it YOUR game — the data files

A different game is a set of edited JSON files, not a fork. Each file
below is a template next to this README; copy it, edit it, point the
runner at it. Values are data; the interpreters (categories, rule kinds,
combat arithmetic) are hardened code with tests.

### Combat tuning — `combat.json` (`--combat`)

```json
{
  "player_max_hearts": 3,
  "stomp_damage": 6,
  "stomp_bounce_factor": 0.7,
  "hurt_iframes_s": 1.0,
  "spawn_grace_s": 1.0,
  "spawn_safety_columns": 3
}
```

- `player_max_hearts` — heart pool; refills on (re)spawn
- `stomp_damage` — hp removed per stomp; enemies die after
  `ceil(hp × variant_mults / stomp_damage)` stomps
- `stomp_bounce_factor` — bounce after a stomp, × jump velocity
- `hurt_iframes_s` — post-hit invulnerability window
- `spawn_grace_s` — you spawn fully invincible until your first move;
  this is the ONE extra second of shield after it (fair respawns next
  to a camping enemy, no god mode)
- `spawn_safety_columns` — no enemy this close to spawn at placement
  time; violators are column-nudged by the tool, never kicked to the LLM

Contact costs `damage × variant_mults` hearts. Hazard tiles cost their
registry `params.damage` (default 1); damaging volumes (lava) drain
hearts continuously through `params.damage_per_second`.

### Rules of the game — `game_rules.json` (`--rules`)

Policy toggles, each enforced by validators and both play surfaces:

- `water_containment`: `"contained"` (pools need basin walls) or `"free"`
- `enemy_water_policy`: `"swimmers_only"` | `"forbidden"` | `"amphibious"`
- `platform_drop_through`: Down+jump drops through one-way platforms
- `variant_caps`: per-level caps by variant name (`{"champion": 1}`)
- `checkpoint_enemy_reset`: killed enemies return when you die and respawn
- `spawn_grace`: `"until_move"` — blink untouchable at spawn, chasers hold
  still until your first input, then `spawn_grace_s` seconds of shield —
  or `"off"`

Unknown keys ride through to the manifest **inert** (open carriage): you
can sketch a future rule today; it starts working when its enforcement
code lands.

### Tiles — `tile_types.json` (`--tiles`)

The tile registry: `{id, name, category, color_role, params}` per tile.
Categories are the code-enforced physics (`empty`/`solid`/`one_way`/
`hazard`/`volume`); adding a new tile of an existing category is a data
edit — a swimmable lava, a laser hazard, a mud pool:

```json
{ "id": 21, "name": "lava", "category": "volume", "color_role": "lava",
  "params": { "speed_factor": 0.4, "gravity": 6.0, "impulse": 4.0,
              "damage_per_second": 1.0 } }
```

Tile names are DSL vocabulary: the layout agent can now write
`volume(lava, 20, 26, 12)` and the prompt advertises it automatically.
Id bands are enforced: solids/one-way 1–9, hazards 10–19, volumes 20+.

### Enemy variants — `variants.json` (`--variants`)

Named upgrades a placement opts into (`{"variant": "champion"}`):
`stat_mults` (hp/damage), `speed_mult`, `size` (multiplies the
definition's size), `visual` (`outline` / `scale` / `outline_scale`),
`behavior` overrides. A champion guarding a chokepoint is the pack's
mini-boss story.

### Enemy stats — `schemas/enemy.json`

Skeleton schema: mechanical properties are **pre-rolled deterministically**
from these tables; the LLM only invents name + flavor. As shipped:
archetypes (`patroller`/`chaser`/`sentry`/`swimmer`, weighted), body
`size` rolled from {1.0, 1.5, 2.0} (weighted 4/2/1), hp and contact
damage looked up by size tier (small 4–6 hp / 1 heart … big 13–18 hp /
2 hearts), speed and aggro by archetype. Edit the weights and bands to
reshape your bestiary; sizes are real — a 2.0 body needs two supported
columns and two rows of clearance at placement, collides at full size,
and renders at full size everywhere.

### Level shapes — `schemas/level_layout.json`

Difficulty is keyed off level POSITION; grid width/height and the
count knobs (platforms, hazards, gaps, pools) are difficulty-banded
ranges. Widen the bands for sprawling finales, tighten for a puzzle game.

### Graphics — `--graphics <spec.json>`

Target resolution and art direction as data: `tile_px`, `art_style`
(the diffusion prompt's style clause), `render_filter`
(`crisp`=nearest / `smooth`=linear), `view_cells` (camera framing —
zoom at a stable window, never window resizing), `actor_scale` (sprite
overdraw), per-level view presets (`intimate`/`vista`). Two shipped
examples prove the swap: `examples/graphics_specs/snes_pixel.json` and
`rendered_hd.json`.

---

## Audio

One looping music theme per stage plus a closed SFX event set
(`jump`, `checkpoint`, `death`, `win`), generated **at the end of the
pipeline** with the art phases:

```bash
--music-backend none|fake|lyria      # theme  → music/<stage>/theme.<ext>
--sfx-backend   none|fake|elevenlabs # events → sfx/<stage>/<event>.<ext>
```

`audio/<stage>/manifest.json` is the reviewer's index; the game manifest
ships an `"audio"` block both play surfaces read. Silence is always the
fallback — a game without audio flags plays fine. Audio is its own
artifact (`audio:<stage>`), so re-rolling a theme never cascades through
levels: `canon regen <dir>/bible.json phase:plat:audio --mark-only`,
then resume.

---

## Regenerate exactly what you want

Run with `--orchestrate` to get per-step state (persisted in
`bible.json`) and a real dependency graph. Then:

**Hand-edit anything.** Edit a layer file (`level/<stage>/<id>/…`), an
enemy JSON, even a PNG — the next run detects the edit (content hash
mismatch), **adopts your version**, and re-runs only the steps that
depend on it. Editing an enemy definition re-validates every level's
placements (a grown body can invalidate a spot); editing a collision
layer re-rolls that level's downstream layers only.

**Mark and re-roll.** `--mark-only` writes staleness into the Bible and
touches nothing else (no API keys needed):

```bash
uv run canon regen /path/to/game/bible.json l3 --mark-only          # one level
uv run canon regen /path/to/game/bible.json enemy:ash_crawler --mark-only
uv run canon regen /path/to/game/bible.json phase:plat:sprite_art --mark-only
```

Then **resume = re-run the original runner command** (flags are not
persisted — pass the same ones, *especially* `--image-backend fal` if
your art is real; resuming real art without it regenerates placeholder
tiles over your paid tilesheet).

**Failure forensics.** If every layout attempt for a level fails
validation, the level ships a guaranteed-valid flat fallback, the
manifest carries a warning, and
`review/<stage>/<id>_layout_attempts.json` records every attempt with
its content and every rejection reason — a re-roll that succeeds deletes
the trace.

---

## Pins — protect the art you like

Re-rolling a roster or an art phase doesn't have to sacrifice the one
sprite you love. Pins live on the Bible and halt the stale cascade:

```bash
uv run canon pin   /path/to/game/bible.json enemy:ash_crawler tileset:ashen_grove
uv run canon pin   /path/to/game/bible.json --list
uv run canon unpin /path/to/game/bible.json enemy:ash_crawler
```

Pinnable = art artifacts (`tileset:*`, `enemy:*`, `backdrop:*`,
`audio:*`, `player`). Semantics: staleness cascades halt at a pin, the
scheduler skips pinned ids, art phases guard **per asset** (one pinned
sprite survives a full roster re-roll), and explicitly regenerating a
pinned id is an error that names `canon unpin`. Pinned files you
hand-edit are still adopted and reported.

---

## Determinism contract

Same command + same seed → byte-identical output tree, including review
PNGs and failure traces. The orchestrated path at default concurrency
produces the same bytes as the sequential path (minus `bible.json`).
This is load-bearing: tests diff whole trees, and any nondeterminism is
a bug.

## Runner flags reference

| Flag | Default | Purpose |
|---|---|---|
| `--backend fake\|anthropic` | `fake` | text generation |
| `--seed <str>` | `emberfall_001` | determinism root |
| `--output-dir <path>` | `./plat_slice_output` | the game tree |
| `--num-levels / --num-enemies` | 3 / 4 | content counts |
| `--engine json\|godot` | `json` | godot = playable project export |
| `--orchestrate` | off | DAG scheduling, resume, per-step regen |
| `--rules / --tiles / --variants / --combat / --graphics` | pack templates | your game's data files |
| `--image-backend none\|fake\|fal\|local` | `none` | tile/sprite/backdrop art |
| `--image-model <id>` | backend default | diffusion model override |
| `--music-backend none\|fake\|lyria` | `none` | stage theme |
| `--sfx-backend none\|fake\|elevenlabs` | `none` | SFX events |

Godot 4.3+ is required for the exported project (`godot --path <dir>`);
`PLAT_LEVEL=<level id>` starts on any level — also the frame-capture
hook: `PLAT_LEVEL=l3 godot --path <dir> --write-movie shots/l3.png
--fixed-fps 10 --quit-after 10`.
