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
world (biome stages, in play order) → per-stage plans → per-stage style
      → WORLD enemy pool (rarity + habitats) → per-stage tilesets
      → per level: layout (DSL → stamp → validators/repair) → terrain
        → background → enemy placement (stage roster) → decor
      → ART AT THE END: tilesheets, sprites, backdrops, audio (per stage)
      → review renders → VLM QA → manifest (world map) → Godot export
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
  platforms, misplaced spawns AND checkpoints snap to valid columns,
  pools poured on the ground row snap one row up, spawn-crowding enemies
  get column-nudged. The agent keeps design authorship; geometry is tool
  work.

---

## Quickstart ($0, fully deterministic)

```bash
# generate a full 3-biome world with placeholder art — every feature of
# the pipeline exercised at $0, including the VLM QA loop (fake judge)
uv run python -m canon.packs.platformer.run_slice \
  --backend fake \
  --image-backend fake --music-backend fake --sfx-backend fake \
  --vlm-backend fake \
  --engine godot --orchestrate \
  --output-dir /tmp/my_game --seed emberfall_001

# look at it: per-level review PNGs (block + skinned pairs) + QA verdicts
open /tmp/my_game/review/
cat /tmp/my_game/review/*/qa_report.json

# play it in Godot (4.3+) — opens on the world map
godot --path /tmp/my_game
PLAT_LEVEL=l5 godot --path /tmp/my_game     # jump straight to one level

# or play one level in the pygame harness (quick pre-art surface)
uv run --extra platformer --extra play \
  python -m canon.packs.platformer.play /tmp/my_game l1
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
| `--vlm-backend anthropic` | Claude vision judge for end-of-pipeline QA | `ANTHROPIC_API_KEY` |

A `.env` file is **not** read automatically — export first. The
everything-on run — world plan + levels + diffusion art + music + SFX +
the VLM QA judge over every level's renders:

```bash
set -a; source .env; set +a
uv run python -m canon.packs.platformer.run_slice \
  --backend anthropic \
  --image-backend fal \
  --music-backend lyria \
  --sfx-backend elevenlabs \
  --vlm-backend anthropic \
  --num-stages 3 --num-levels 3 --num-enemies 7 \
  --engine godot --orchestrate \
  --output-dir ~/my_real_game \
  --seed "a cheerful island archipelago, tidepools and cliff winds"

# then: review the QA verdicts + warnings before you even open Godot
cat ~/my_real_game/review/*/qa_report.json
python3 -c "import json; print(*json.load(open('$HOME/my_real_game/manifest.json'))['warnings'], sep='\n')"

godot --path ~/my_real_game        # world map → START → play → Congrats
```

Ballpark cost at the 3×3 defaults: ~40 text calls + ~38 images (~$1.50
fal) + 3 music themes + the SFX set + ~9 vision judgments (cents). Drop
any flag to skip that spend — the game plays fine with placeholders and
silence. Every paid backend has a `fake` twin (`--image-backend fake`,
`--vlm-backend fake`, etc.) that exercises the identical code path
deterministically at $0 — the Quickstart above is exactly that.

If QA flags something, regen surgically (never automatically):

```bash
# e.g. the report suggested enemy:tide_skitter and l5's layout
uv run canon regen ~/my_real_game/bible.json l5 enemy:tide_skitter --mark-only
set -a; source .env; set +a
uv run python -m canon.packs.platformer.run_slice \
  --backend anthropic --image-backend fal --vlm-backend anthropic \
  --engine godot --orchestrate \
  --output-dir ~/my_real_game --seed "<the ORIGINAL seed>"
```

(Resume = the original runner command with the original flags —
especially `--image-backend fal`, or stale art regenerates as
placeholder squares over your paid tilesheet.)

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
- `spawn_grace`: `"until_move"` — blink untouchable at spawn until your
  first input, then `spawn_grace_s` seconds of shield (enemies keep moving;
  the untouchable window + spawn-safety radius keep it fair) — or `"off"`
- `enemy_sight`: eyesight FOV per locomotion (`omni`/`hemisphere`/`forward`
  +`vband`/`none`); `chase_speed_mult`: aggressive-chase speed multiplier;
  `flyer`: hover/swoop tuning (bob, scan-sway, dive period/depth)
- `rarity_caps`: per-level at-most-N caps per enemy rarity tier
  (`{"rare": 1, "uncommon": 2}`) — what keeps rares rare on the ground

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

**Water is optional, and when it appears it's a FEATURE.** Beyond
contained pools/basins, the DSL has free-standing water:
`water_wall(x1,x2,y_top)` drops a full column from `y_top` to the
terrain — a waterfall/shaft the player swims up and leaps out of (over a
bottomless gap it runs out the bottom: sinking too deep is a fall death,
a deliberate spout hazard) — `water_block(x1,y1,x2,y2)` floats a
pocket of water in open air, and `water_cloud(x1,y1,x2,y2)` puffs that
pocket into a rounded **swim-up cloud** the player rises through to reach
heights a jump alone can't (or drifts across a gap). All three are exempt
from containment by design; the generic spellings `volume_wall`/
`volume_block`/`volume_cloud` take any volume tile, so a game without a
`water` tile is offered those (with its own liquid) instead. Airy
jump-gauntlet levels are encouraged to skip water entirely.

### Water LEVELS — `water_levels.json`

Beyond water features, a whole level can roll a **water topology**
(stage 2+, horizontal levels; per-biome odds are data — a seaside biome
rolls more than peaks): **fully submerged** (every open cell floods —
the player swims from frame 1, the level composes from aquatic
`reef`/`trench` section archetypes, seabed `urchin`/`mine` hazard
strips replace ground gauntlets) or **waterline** (a high water table
floods the bottom rows; dry islands poke out and the player moves in
and out of the water). The flood is a deterministic CODE pass after the
level validates dry — markers stay validated, then a swim-reachability
check confirms every exit/checkpoint/entrance survived (a waterline
that strands an island lowers itself row by row). Items float in water
(collected by swimming through them); item boxes stay on dry land.
Secret rooms don't roll on fully-submerged levels (pipe/door presses
are dry-land verbs); waterline levels keep them, entrances on dry
ground.

**Water fauna** (`enemy_water_policy: "seabed"`, the default): the same
world roster serves wet levels — swimmers swim (styles `within` /
`surface` / `float` / **`cruise`**, the last an unbounded Cheep-Cheep
that crosses the whole body of water and turns only at walls), flyers
sit water levels out, and LAND enemies **wade**: one posted on a
submerged flat patrols its underwater beat, but a land enemy placed on
dry ground never walks into a pool. Free swimming v1 — no breath meter;
currents/flow are a future mechanic.

### Combat picks — per-level rules, hoppers, hold-bounce

**Per-level rule twists** (`rule_overrides.json`): the stage plan may
flag one level with a rule/movement override where its brief begs for
it — a no-drop-through cave, a faster-chase gauntlet, a **low-gravity**
vault. The vocabulary is CLOSED data (allowed keys + numeric bands);
code validates fail-closed (unknown keys and out-of-band values are
dropped loudly), the winners persist on the level (`level.json`
`rules_overrides` / `movement_overrides`), secret rooms inherit their
parent's, and — the load-bearing part — **a level with movement
overrides is generated and VALIDATED under its own physics** (the
reachability sim runs with the overridden spec). Both play surfaces
re-derive effective rules/movement per level.

**Hoppers**: a jumping locomotion in the archetype roll — grounded it
ticks a hop cadence (`hop_height`/`hop_period_s`, rolled per
definition), then arcs ballistically over gaps and hazard strips,
bonks on ceilings, and lands anchor-only on support. Dry land only (no
wading). **Hold-jump bounce**: stomping with jump HELD is a full jump
off the enemy's head (chainable by skill); tapping gives the damped
`stomp_bounce_factor` hop.

### Enemy variants — `variants.json` (`--variants`)

Named upgrades a placement opts into (`{"variant": "champion"}`):
`stat_mults` (hp/damage), `speed_mult`, `size` (multiplies the
definition's size), `visual` (`outline` / `scale` / `outline_scale`),
`behavior` overrides. A champion guarding a chokepoint is the pack's
mini-boss story; the `relentless` variant overrides the chase leash —
the ONE enemy per level that never gives up; the `emberborn` variant is
**hazard-immune** (an occupancy exemption, not a damage stat): it may
be POSTED standing on a spike strip and patrols across hazard tiles —
the placement prompt lists the footed hazard cells it may take.

**Behavior doctrine** (enforced in both play surfaces): every mover patrols
its beat. **Aggro is an orthogonal behavior tier** (not an enemy type): an
aggressive enemy — of ANY locomotion — spots the player in its FOV cone
(`enemy_sight`), commits a chase up to its `leash_range` (= a multiple of its
patrol beat; a `hunter` has no tether), then returns to patrol. The old
`chaser` is just a patroller + aggressive; `relentless` is the
one-per-level hunter-grade variant. No enemy walks into a hazard tile or
clips through solids; swimmers respect their swim style (`within`, `surface`,
`float`); flyers stay airborne (hover+swoop or altitude-patrol+dive).
Enemies the terrain can't sustain are never offered to the placement agent
(a swimmer needs water its body fits; a flyer needs open airspace).

### Enemy stats — `schemas/enemy.json`

Skeleton schema: mechanical properties are **pre-rolled deterministically**
from these tables; the LLM only invents name + flavor. As shipped: LOCOMOTION
`archetype` (`patroller`/`swimmer`/`flyer`/`sentry`, weighted) and an
orthogonal `aggro` tier (`passive`/`stalker`/`pursuer`/`hunter`, 80/12/6/2)
whose `aggro_mult`/`leash_mult` scale the enemy's `patrol_range` into eyesight
and tether; body `size` rolled from {1.0, 1.5, 2.0} (weighted 4/2/1), hp and
contact damage looked up by size tier (small 4–6 hp / 1 heart … big 13–18 hp /
2 hearts), speed by locomotion. Edit the weights and bands to
reshape your bestiary; sizes are real — a 2.0 body needs two supported
columns and two rows of clearance at placement, collides at full size,
and renders at full size everywhere.

### Items — `schemas/item.json` (`--num-items`)

The WORLD ITEM POOL (like the enemy pool): each definition rolls a
mechanical **kind** from the closed set `coin` / `heal` / `shield`
(absorbs one hit, then breaks) / `double_jump` / `run_boost` (both timed
— `duration_s` rolls from a band you tune), plus rarity and per-kind
params; the LLM authors only name + flavor, themed to the world. Slots
0/1 are **guaranteed** coin + heal so every world has its currency and
its heal; the rest roll free (`--num-items`, default 5). Definitions
land as reviewable `item/<id>.json` artifacts (`item:<id>` regen
targets) a developer can hand-evolve like any canon output.

An LLM **item-placement pass** runs after enemy placement with the
finished level in view: coins are FREQUENT and guide the route (trails,
arcs over gaps, side-area markers), power-ups stage around enemy
clusters, premium items land at the layout's secret `reward()` alcoves,
and **item boxes** float in open air. The validator is fail-closed —
every item must be collectible with the BASE moveset (snap/drop
repairs), and a box that would wall off the path is dropped by a
reachability re-check. Box cells ride in `items.json` as an overlay
(collision files never carry them); both play surfaces open a box by
**head-bump from below or stomp** — it flips to a spent block and its
item pops out and auto-collects. Pickups: coins count on the HUD, heals
restore a heart, and power-ups fill ONE held slot (a new pickup
replaces it, death clears it): the shield absorbs one hit then breaks,
double-jump grants one mid-air jump, run-boost raises top speed — the
timed ones tick down on the HUD. Collected items stay collected across
checkpoint respawns. The reachability validator never assumes a
power-up: every level stays beatable barehanded.

### Level shapes — `schemas/level_layout.json`

Difficulty is keyed off level POSITION; grid width/height and the
count knobs (platforms, hazards, gaps, pools) are difficulty-banded
ranges. As shipped, horizontal levels span **~48 columns (intro) to
~132 (finale)**, 14–26 tall; widen the bands for sprawling finales,
tighten for a puzzle game.

### Sections — `sections.json`

A level is not one blob — it's a **sequence of typed SECTIONS**, each an
archetype with its own character, generated independently at its own
local dims and **stitched by sub-grid compositing** (each stamped
sub-grid pasted at its origin, non-empty cells winning the seam). A wide
level is ~6–8 stitched sections; the shipped archetypes:

| archetype | axis | character |
|---|---|---|
| `runway` | horizontal | flat run-up breather (always section 0) |
| `gauntlet` | horizontal | platforms + gaps + hazards + breakable floors |
| `cave` | horizontal | walls/carve, low ceilings, hides secret alcoves |
| `islands` | horizontal | hop platforms over a pit/water, floating clouds |
| `climb` | **vertical** | a tall ascent (spawn at the base, exit at the summit) |

Each archetype's `feature_bias` (op-weight hints), `intensity`, `water`
level, and `encounter` style steer the section's prompt — a gauntlet
leans platform/gap, a cave leans wall/carve + `secret`, a climb leans
platform/`cloud`. `plan_sections` composes the list deterministically
(section counts scale with level size); the LLM only arranges KNOWN
features inside each bounded section. A level rolls **horizontal** or a
real **vertical climb** (`VERTICAL_FRACTION`, stage 2+ only); vertical
levels are recast tall + narrow (a shaft up to ~26 wide × ~96 tall) and
framed by height.

The layout DSL the agent emits (all coordinates are grid cells, row 0 at
the top): `floor`/`gap`/`pit`, `platform`/`ledge`, `wall`/`carve`,
`stairs_up`/`stairs_down`/`pyramid`, `pool`/`volume`, the free-water
`water_wall`/`water_block`/`water_cloud`, `hazard_strip`,
**`breakable(x1,x2)`** (a crumbling floor that gives way a moment after
you stand on it — keep spans short), and the markers `spawn`/`exit`/
`checkpoint` plus **`reward(x,y)`** (a hidden collectible marker for a
**secret alcove**, a niche carved behind a wall — layout-only, a
placeholder for a future item system). Deterministic tools stamp the DSL
to the collision grid, validate reachability with the run-up-momentum
jump simulation, and repair computable breaks (bridge/snap) in code.

### Models — `models.json` (`--models`)

Which Claude serves which agent (PRD §9.1 realized as data): `model_tiers`
maps tier names to model ids (the single place a model bump lands) and
`agent_tiers` assigns a tier per phase-label prefix — validator-backstopped
agents (`plat:enemies`, `plat:placement`, `plat:decorator`) ride `cheap`,
structural ones (`plat:world/stage/style/layout`) ride `mid`, `top` is
opt-in per node. Applies only to real text backends (`fake` ignores models
entirely, so $0 runs are untouched); an explicit `--model` without
`--models` overrides the default table. Each artifact's provenance stamps
the model that actually authored it, so changing one agent's tier
invalidates exactly that agent's artifacts on a `regen`.

### Graphics — `--graphics <spec.json>`: the art-template system

Art direction is DATA. A `GraphicsSpec` JSON is a **style lane** — the
full recipe for a look: `lane` name, `aesthetic_tokens` (the prompt's
style clauses), `color_depth`, `base_cell` (the ART density root in
px — physics never moves), `cells_per_tile`/`tile_px`,
`player_footprint` (art canvas in base cells over the unchanged
hitbox — chunky canvas, tight hitbox), `render_filter`
(`crisp`=nearest+grid-snap / `smooth`=linear), `posterize_levels`,
`view_cells` camera framing, `actor_scale`. Three shipped lanes in
`src/canon/packs/platformer/graphics_specs/`:

| lane | look | density |
|---|---|---|
| `hand_drawn_16bit.json` | bold flat shading, clean outlines (SMW) | 16px cells, crisp |
| `prerendered_16bit.json` | glossy 3D-baked, dithered (DKC) | 16px cells, crisp |
| `modern_hd.json` | smooth painterly HD | 32px cells, smooth |

Swapping `--graphics` restyles every generated asset; collision bytes
are lane-independent (tested). Editing the JSON IS restyling the game
— no code.

**Backends** (all behind one capability-declared interface, graceful
degradation, fake twins for $0): fal/nano-banana (default — general
models drift off-grid, so crisp lanes get a MANDATORY `grid_snap`
post-process), Retro Diffusion (`--image-backend retro`, `RD_API_KEY`,
native pixel grids), PixelLab (`--image-backend pixellab`,
`PIXELLAB_SECRET`). `--image-model` / `--image-edit-model` pick models
per leg.

**Sample → lock → batch** (see `ART_SAMPLING.md` for the full
walkthrough + costs): generate ONE exemplar through every wired
backend (`--art-sample player|tile:<name>|palette
--sample-backends fake,retro,...` — contact sheet + an in-context
grid-drop check), judge with your eyes, freeze the winner as an
approved `art_lock.json`, then batch generation consumes the lock
(`--art-lock <path>`; refuses non-approved locks; failures land in
`review/art_report.json` with targeted regen commands — never a
silent loop).

**Autotiling**: the floor ships 16 code-resolved variants (a 4-bit
exposure mask over solid neighbors) with mean-preserving edge shading
— visible even on the $0 placeholder sheet — plus a
`tileset/<stage>/autotile.json` manifest (bitmask → sheet region), the
input for a future Godot TileMap exporter. **Backdrops** are layered:
far/mid scenery bands plus a foreground occluder band (depth > 1)
drawn in front of gameplay; a seam QA check verifies horizontal
tiling. **Animation** ships as a deterministic packed atlas
(`atlas.json`, bottom-center registration, loop modes + per-frame
durations — all hand-editable); player states cover
idle/walk/jump/fall/land/skid, hoppers add jump; `asymmetric: true`
on a definition generates true left-facing sheets. **Effects**:
dust/splash/sparkle one-shot VFX (Godot) + `canvas_tint` /
`player_light` / `glow` lighting kinds rollable per stage.

**Readability**: palettes are clamped at birth (swimmable water never
wears hazard hues — damaging lava keeps them; enemy/item swatches
clear every stage background by 40 luminance), water draws as a
translucent overlay (`water_alpha` in the graphics spec, 0.55) so
terrain reads through volumes, and QA runs an ADVISORY
`composite_contrast` check — it samples the skinned render behind
every placed enemy/item and notes camouflage risks in
`qa_report.json` + the logs. Advisory means advisory: it never blocks
a build, never flips review status, never becomes a manifest warning.

**Provenance**: every generated PNG carries deterministic `canon:*`
tEXt chunks (asset id, backend/model, seeds, graphics digest — no
timestamps, byte-determinism is a feature); `visible_watermark: true`
in a graphics spec adds a small corner mark (off by default). A
durable C2PA layer is deliberately deferred (zero-dependency posture);
`c2pa-python` is the named upgrade path. **Splash**: a generated
studio card (`splash/world.png`) boots the Godot build —
card → hold → fade → world map, any key skips, and a tree without one
boots straight to the map.

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

## The world: biomes, the world map, and the enemy ecology

A game is a **world of biome stages** (`--num-stages`, default 3), each
with its own palette, tileset, backdrop, props, and (if flagged) music —
levels within a biome share them, which keeps generation cost linear in
stages, not levels. Level ids are global (`l1..l9`); players see
stage-local **display names** (`1-1`…`3-3`).

The Godot game opens on a **DK/SMW-style world map** (code-drawn v1):
biome regions tinted with their own palettes, a path of level nodes,
linear unlock, beaten nodes gold. Entering a level shows a **START
overlay** (scene frozen until any input — the timer starts then);
finishing shows **Congratulations! + your level time**, returns you to
the map, and saves progress per seed (`user://plat_save_<hash>.json`).
`PLAT_LEVEL=<lid>` still boots straight into a level (the frame-capture
hook, no map/overlays).

Enemies are a **world pool** (`--num-enemies`, default 7), not per-stage
rosters: each definition rolls a **rarity** (common/uncommon/rare —
schema v5) and gets **habitats** (some roam everywhere, rares bind to
one biome). A stage's roster is the pool filtered by its biome, and
per-level `rarity_caps` (game_rules.json) keep rares actually rare on
the ground. Swimmers additionally roll a **swim style**: `within` (the
classic body-bound patrol), `surface` (rides the water's top row), or
`float` (drifts diagonally, bouncing off the basin) — enforced at
placement and in both play surfaces.

---

## Secret rooms — `secret_rooms.json`

A level MAY hide **secret sub-rooms**: mini-levels (own small grid, own
full file set at `level/<stage>/<id>r<k>/`) built by the SAME section
machinery (1–2 sections) and validated entry→exit like any level.
Everything about them is CODE-ROLLED in the blueprint from
`secret_rooms.json` — presence odds per difficulty, room **type**
(`shortcut` = a light skirmish that pays in coins, `vault` = a
no-enemies treasure chamber stacked with premium loot, `lair` = one
dangerous champion-grade encounter with a prize), **entry verb**
(`pipe` = press Down, `door` = press Up; `vine` ships at weight 0 until
its climb mechanic lands), **return topology** (`detour` = come back
where you entered, `shortcut` = re-emerge further along the level), and
dims. The spec carries a `context` hook so the world map can inform the
roll later; v1 rolls randomly.

Entrances are **stitcher-placed** (like exits and checkpoints): a
reachable standing cell in the blueprint's host section gets a
`room_entrance` trigger (and a pipe/door prop on both play surfaces);
the room's exit cell doubles as its return portal. In play, the switch
**carries everything** — timer, hearts, coins, the held power-up mid
countdown — and each map remembers what you did to it (collected items,
spent boxes, crumbled floors, claimed checkpoints) for the return trip.
Dying inside a room ejects you to the parent's last checkpoint with
normal death semantics. Rooms are invisible on the world map, get their
own review renders and QA rows, and regen like any level
(`canon regen <bible> l6r1 --mark-only`). Base-moveset beatability is
unchanged — rooms are optional secrets, never on the required path.

---

## Checkpoint flags & the exit goal

Every level shows its gameplay props: a **checkpoint flag** per trigger
(grey pennant until claimed, gold after — aggressive enemies may still camp
it, the spawn shield is the fairness fix) and an **exit goal doorway** on the
exit cell, so the level visibly ends instead of teleporting you off the
right edge (the exit zone is still the whole column).

Both surfaces draw placeholder shapes by default. With
`--image-backend`, the sprite art phase also generates themed prop
sprites (`sprite/prop/<stage>/{checkpoint,exit}.png`) owned by a
`props:<stage>` artifact — hash-tracked, edit-detected, and pinnable
like any art. The prop *name set* is closed in code: a new prop only
becomes real with its draw + trigger point in both play surfaces.

---

## VLM QA — a vision judge over the review renders

Every level already ships a **block render** (analytic truth) and a
**skinned render** (what the player sees). QA also crops two
**play-scale views** from the skinned render — the camera's actual
`view_cells x view_rows` window around the spawn and the exit
(`review/<stage>/<lid>_play_{spawn,exit}.png`) — so readability is
judged at the zoom the player plays at, not just zoomed out. With an
explicit flag, a vision model judges each level's five images (plus the
palette and roster facts) at the very end of the pipeline:

```bash
--vlm-backend none|fake|anthropic    # default none = no QA
--vlm-model <id>                     # optional model override
```

Per level it returns structured verdicts on three dimensions —
**fidelity** (the skinned render matches the block truth: every
placement present, effective sizes right, nothing missing or extra),
**readability** (player/enemies/hazards distinguishable against tiles
and backdrop), and **style coherence** (palette adherence, sprites match
the tileset style) — plus short notes.

The report is `review/<stage>/qa_report.json` (deterministic shape, no
timestamps). Failing verdicts become **manifest warnings** that survive
resumes — the manifest re-derives them from the on-disk report, so a
later run without the flag never launders a failing report. The report
may *suggest* mark-only regen targets; it never regenerates anything —
invalidation stays user-controlled.

The code-not-LLM split applies here too: everything computable is a
**code check** feeding the same report — missing sprite files, a
sprite's opaque bounding box vs its canvas (a corner-hugging sprite
renders smaller than its hitbox), tile-region palette conformance. The
VLM judges only what code can't: does it *read* right.

`--vlm-backend anthropic` is PAID (`ANTHROPIC_API_KEY`, fail-fast);
`fake` exercises the entire loop deterministically at $0, including one
canned failing verdict so the warning path stays covered.

**Re-judging is staleness-aware.** Each report entry records sha256
hashes of exactly what the judge saw (`judged_inputs`); a flagged
re-run re-judges only levels whose renders (or the judge model)
actually changed — unchanged verdicts carry from the on-disk report
byte-identically, at zero VLM cost. The carry is logged, never written
into the report, so reports stay deterministic.

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

**Failure forensics.** Most geometry mistakes never fail a level at all —
the stamp auto-repairs them (out-of-bounds coordinates clamp, hazards over
gaps become pits, spilling water becomes a free feature, …) and the
whole-level repair escalates (bridges, mounts, doorways, climb lanes,
exit relocation), each fix recorded in the run log. When a section still
exhausts its attempts, it (then, if needed, its seam neighbours) falls
back to a guaranteed-valid stretch; a whole-level fallback is the last
resort. Whenever anything failed or fell back,
`review/<stage>/<id>_layout_attempts.json` records every section attempt
with its rejection reasons AND every whole-level stitch round's residual
problems (`stitch_rounds`) — a fully-clean re-roll deletes the trace.

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

Three observability files are the documented exemptions (they carry
wall-clock timestamps or scheduler-shaped call ordering; nothing in the
pipeline reads them back): `bible.json` (`generated_at` + node state),
`.canon/log.jsonl`, and `generation_stats.json`.

## Observability

Every run appends a structured step log to `.canon/log.jsonl` — one
JSON event per line (`run_start`, `node_start`/`node_done`/
`node_failed`, `node_skipped` with its reason on resumes, `run_end`
with the rollup) — and snapshots `generation_stats.json` at manifest
time: LLM calls, tokens, and cost per phase label (`plat:layout:l5:s2`
granularity), plus image/audio counters. The stats feed `canon
estimate`'s calibration; the log is Cradle's (and your) first stop for
"what did this run actually do".

**Forecast before you pay.** `canon estimate` walks the same DAG a run
would execute — including the exact resume/skip logic — and prices the
would-run nodes through `models.json` + `cost_model.json` (every number
is data you can edit). It never writes: regen targets are marked on a
copy. With no bible yet it forecasts a full run from scratch
(`fresh_plan` in cost_model.json); on a tree carrying real
`generation_stats.json` actuals it calibrates tokens from them instead
of the defaults:

```bash
CANON_PLAT_OUT=~/my_real_game uv run python -m canon.cli.main \
  estimate ~/my_real_game/bible.json l5 enemy:tide_skitter \
  --pipeline canon.packs.platformer.dag:cli_ctx_factory \
  --phases canon.packs.platformer.dag:cli_phases_factory \
  --estimator canon.packs.platformer.estimate:estimate_run
```

## Runner flags reference

| Flag | Default | Purpose |
|---|---|---|
| `--backend fake\|anthropic` | `fake` | text generation |
| `--seed <str>` | `emberfall_001` | determinism root |
| `--output-dir <path>` | `./plat_slice_output` | the game tree |
| `--num-stages` | 3 | biome stages (world-map areas) |
| `--num-levels` | 3 | levels PER stage |
| `--num-enemies` | 7 | WORLD enemy pool size (ecology) |
| `--engine json\|godot` | `json` | godot = playable project export |
| `--orchestrate` | off | DAG scheduling, resume, per-step regen |
| `--rules / --tiles / --variants / --combat / --graphics / --models` | pack templates | your game's data files |
| `--image-backend none\|fake\|fal\|local` | `none` | tile/sprite/backdrop art |
| `--image-model <id>` | backend default | diffusion model override |
| `--music-backend none\|fake\|lyria` | `none` | stage theme |
| `--sfx-backend none\|fake\|elevenlabs` | `none` | SFX events |
| `--vlm-backend none\|fake\|anthropic` | `none` | end-of-pipeline visual QA judge |
| `--vlm-model <id>` | backend default | VLM judge model override |

Godot 4.3+ is required for the exported project (`godot --path <dir>`);
`PLAT_LEVEL=<level id>` starts on any level — also the frame-capture
hook: `PLAT_LEVEL=l3 godot --path <dir> --write-movie shots/l3.png
--fixed-fps 10 --quit-after 10`.
