# Handoff — Platformer-in-Canon (updated 2026-07-17, post GRAPHICS ARC —
# ALL SIX ARCS SHIPPED; deep per-chunk history lives in MEMORY)

Paste-able cold-start context for a new Claude Code session. Read this, then
Claude memory (MEMORY.md pointers — `project_sectioned_levels` holds the full
A→P chunk history and every gotcha), then `src/canon/packs/platformer/README.md`
(user-facing, current) and `docs/platformer_prd.md` §6/§7 (architecture).
Trust DISK STATE over any doc's claims, always.

## Where things stand (2026-07-16)

- **Repo**: `~/Documents/projects/canon-ai`, branch `pipeline_items_arcs`
  (pushed; user merges via PR). The branch carries FOUR uncommitted-to-
  main arcs: PIPELINE (steplog/stats, models.json, canon estimate, QA
  v2), ITEMS (pool/placement/boxes/power-ups/HUD), MULTI-ROOM SECRET
  LEVELS (Arc 3, memory `project_multiroom_arc` — rooms as mini-Levels,
  pipe/door switch, carry-everything, vine deferred), and **WATER
  LEVELS (Arc 4, 2026-07-16, memory `project_waterlevels_arc`)** —
  stage-2+ horizontal levels roll fully_submerged/waterline from
  `water_levels.json` (biome-steered odds); the flood is a code pass
  AFTER dry validation (membership re-check, waterline self-lowers);
  aquatic `reef`/`trench` archetypes + `urchin`/`mine` hazards; items
  float in water; `enemy_water_policy: "seabed"` (land enemies WADE
  where posted) + unbounded `cruise` swimmers — same world roster
  serves wet levels. Flow/current, breath meter, vertical water, rooms
  on fully-submerged: DEFERRED.
- **ARC 5 COMBAT/LEVEL PICKS (2026-07-16, memory
  `project_combatpicks_arc`)**: per-level rule/movement overrides
  (stage-plan flags → `rule_overrides.json` closed vocabulary,
  fail-closed → persisted Level fields → each level VALIDATES under its
  own physics — the canned finale is a low-gravity vault; rooms inherit;
  both consumers re-derive per level), the HOPPER archetype (first enemy
  vertical physics: ballistic hops over gaps/hazards, airborne occupancy
  mode, 1e-3-lattice cross-surface parity), the `emberborn`
  hazard-immune variant (occupancy exemption — posts ON spike strips),
  and hold-jump = full stomp bounce (damped when tapped). Two
  pre-existing parity holes fixed en route: pygame's scripted-jump
  `volume is None` over-gate (silenced ALL underwater scripted input)
  and the float32-vs-float64 tile-boundary flip (hopper lattice
  quantization).
- **ARC 6 GRAPHICS = the ART-TEMPLATE SYSTEM (2026-07-17, memory
  `project_graphics_arc`) — ALL SEVEN CHUNKS G1-G7 SHIPPED.** The
  user's "Graphics & Assets — Generation Guidance" notes reframed the
  bake-off into an editable template system: **G1** style-lane
  GraphicsSpec fields + 3 lane templates (`src/canon/packs/platformer/graphics_specs/`:
  hand_drawn_16bit / prerendered_16bit / modern_hd — the `--graphics`
  swap IS the lane switch); **G2** capability-declaring backend
  interface + RetroDiffusionBackend + PixelLabBackend beside fal, and
  `grid_snap` (crisp lanes, non-native_pixels backends); **G3**
  sample→lock→batch (`--art-sample`/`--sample-backends` contact sheet +
  grid-drop, `art_lock.json` approved-only gate, `review/
  art_report.json`) + review_status/grid-conformance QA; **G4**
  animation: fall/land/skid + hopper jump states, loop modes +
  per-frame durations, deterministic packed atlas (atlas.json,
  AtlasTexture+margin / pygame reconstitute), state-change latch,
  registration-jitter QA, asymmetric two-facing sheets, gd-only
  squash/stretch; **G5** 16-variant floor autotiling (code-resolved
  N/E/S/W exposure mask, mean-preserving shading, autotile.json
  manifest), foreground occlusion band (depth 1.15, in front of
  gameplay), backdrop seam QA, dust/splash/sparkle VFX pool (gd-only),
  canvas_tint/player_light/glow lighting kinds; **G6** readable PNG
  tEXt provenance on every generated asset (deterministic — no
  timestamps; C2PA deferred honestly), visible_watermark toggle
  (default off), splash/studio boot card (WorldArtPhase +
  GameState.SPLASH card→hold→fade→map). NO mass generation; NO lane
  locked by Claude — sampling/locking is the USER's lever.
- **Scoping session (2026-07-14/15): six arcs locked** — memory
  `project_feature_arcs_scope`. **ALL SIX ARCS ARE NOW SHIPPED.**
- **Suite**: 2035 passed / 4 skipped with the lyria deselect; ruff
  clean. Never edit product/data files while the suite runs — data files
  (cost_model.json, sections.json) are read at TEST time and race.
  Godot gotcha: `--check-only` rc is 0 even for broken scripts — grep
  the output for `SCRIPT ERROR` instead of trusting rc.
- **`docs/` is gitignored on purpose.** Never force-add anything in it.

### The game the pipeline builds today (all verified end-to-end)

- **Multi-stage worlds**: `--num-stages` biome stages (default 3), each with
  its own theme/palette/tileset/backdrop/props/music; levels numbered l1..lN,
  displayed "1-1".."3-3"; difficulty ramps across the world.
- **Godot world flow**: code-drawn world map → START overlay → play →
  congratulations + time → map; progress saved per seed. `PLAT_LEVEL=<lid>`
  skips straight into a level. pygame surface = mechanics-parity checker ONLY.
- **SECTIONED LEVELS (the 2026-07-12→14 arc, chunks A→P all shipped):** a
  level is a BLUEPRINT (`plan_level`: axis, ≤5 typed sections, checkpoint
  sections, exits) filled one section at a time. Horizontal levels tile the
  width; stage-2+ levels may roll VERTICAL (tall shaft, spawn bottom, summit
  exit, up to ~26×96). **Sequential handoff**: each section's prompt carries
  the previous section's full DSL rebased into ITS coordinates, everything
  occupying the shared seam band, a "do not rebuild" contract, and digests of
  earlier sections; regenerations also see the successor's band. Checkpoints
  and the exit are STITCHER-placed (reachable by construction; horizontal
  exits may sit on elevated right-edge terrain; vertical = the summit).
- **Validation & repair (code-not-LLM, the core doctrine):** the validator
  COMPUTES fixes, never bounces geometry to the LLM. `stamp()` auto-repairs
  bad op geometry (OOB clamp/drop, hazard→pit, water clips, pool clip /
  no-basin→free-water spout, stairs floor-lay, reward drop) and leniently
  skips prose/truncation debris in LLM content, recording everything in
  `result.repairs`. Whole-level reachability ESCALATES: one-way bridge →
  mount-open (solid support → one-way) → doorway carve (lateral walled span)
  / climb lane (vertical, carves solids+hazards w/ record cleanup) →
  exit-relocate (a sealed designed corner moves the exit to the farthest
  REACHED foothold in the last section). Residue routes to the owning section
  (frontier-tagged `[break@x,y]`); terminal = owner fallback, then BOTH seam
  neighbours, then whole-level fallback (axis-aware ladder/floor). Repair
  targets are headroom-qualified (no 1-tall pockets). Caps are deliberate:
  MAX_AUTO_BRIDGES=8, mounts 4, lanes/doors 4, doorway ≤8 cols.
- **Observability:** every level's trace (`review/<stage>/<lid>_layout_
  attempts.json`) records per-section attempts (accumulated across regen
  rounds) AND per-round whole-level `stitch_rounds` (problems, bridges,
  snaps, terminal decisions); written whenever anything failed or fell back,
  never self-deletes on fallback.
- **Reachability** = a real jump-arc SIMULATION of the consumers' exact
  physics incl. RUN-UP MOMENTUM (runway-gated takeoff speeds, jump preserves
  vx, weak air control). `max_dx_for_rise` is only the conservative prompt
  vocabulary; the sim decides.
- **Enemy ecology + behavior**: one world pool; archetype = locomotion
  (patroller/sentry/swimmer/flyer) × orthogonal aggro tier (passive/stalker/
  pursuer/hunter); habitats, rarity caps, swim styles; FOV/chase/leash all
  data. Placement is WHOLE-MAP (post-stitch) with per-section encounter
  density hints. No enemy enters hazards or clips solids; env-infeasible
  enemies are never offered.
- **Water doctrine**: contained pools/basins PLUS free features (water_wall /
  water_block / water_cloud swim-up pockets), containment-exempt
  (`free_volume`); spilling pools re-interpret as free water (G4). Optional
  in prompts; registry-generic op spellings for non-water liquid games.
- **Water LEVELS (Arc 4)**: stage-2+ horizontal levels roll
  fully_submerged / waterline (`water_levels.json` biome odds); code
  flood AFTER dry validation + swim-membership re-check (waterline
  self-lowers, terminal ships dry loudly); aquatic reef/trench
  archetypes (`water_levels_only`), urchin/mine seabed hazards, items
  float in water (boxes stay dry), `"seabed"` policy waders + `cruise`
  swimmers; fully-submerged suppresses secret rooms at BOTH roll sites.
- **Animation (G4)**: VLM-authored per-state sheets for enemies
  (idle/walk/hurt/death, +jump for hoppers) and the player (idle/walk/
  jump/fall/land/skid); loop modes (jump/land/hurt/death play ONCE) +
  per-frame `durations_ms` (uniform v1 — hand-editing frames.json is
  the lever); a deterministic packed ATLAS (`atlas.png`+`atlas.json`,
  trimmed rects + untrimmed-square offsets, bottom-center anchor) is
  the shipping format — consumers load atlas-first, strips stay the
  sample/back-compat artifact; `asymmetric: true` (user-only field)
  generates+plays real left-facing sheets; registration-jitter QA;
  Godot-only landing squash / rising stretch.
- **Art-template system (Arc 6)**: THREE editable lane templates in
  `src/canon/packs/platformer/graphics_specs/` — restyling the game = editing/swapping a
  GraphicsSpec JSON (`--graphics`), physics untouched (`base_cell` is
  art density only). Backends: fal/nano (default; MANDATORY `grid_snap`
  on crisp lanes), Retro Diffusion (`RD_API_KEY`), PixelLab
  (`PIXELLAB_SECRET`) — capability-declared, graceful degradation,
  fake twins for $0. Workflow: SAMPLE one exemplar across backends
  (`--art-sample player|tile:<n>|palette`, contact sheet + grid-drop) →
  the USER approves an `art_lock.json` → BATCH consumes the lock.
  Floor autotiling: 16 code-resolved variants (exposure-mask), emitted
  `autotile.json` = the future Godot TileMap exporter's input.
  Backdrops: far/mid scenery + a FOREGROUND occlusion band (depth>1,
  drawn in front of gameplay on both surfaces). Provenance: every
  generated PNG carries deterministic `canon:*` tEXt (asset, backend,
  seeds, gfx digest; no timestamps by byte-determinism doctrine; C2PA
  = named deferred upgrade); `visible_watermark` graphics toggle
  (default off). Splash: `splash/world.png` + Godot boot card
  (skippable; absent = straight to map). Pins protect all art.
- **Audio**: end-of-loop, per-stage; lyria music + elevenlabs SFX.
- **VLM QA v2**: `--vlm-backend` judges 5 images per level (block, skinned,
  legend + play-scale spawn/exit crops at view_cells framing) + code
  checks; failures = durable manifest warnings; suggests mark-only
  re-rolls, never auto-regens. STALENESS-AWARE: `judged_inputs` hashes in
  the report; unchanged levels carry verdicts byte-identically, no VLM
  call.
- **Pipeline observability (2026-07-15)**: `.canon/log.jsonl` step events
  (both schedulers) + `generation_stats.json` per-label token/cost
  actuals; `models.json` per-agent model tiers (fake unaffected —
  `supports_request_model` gate; bare `--model` suppresses the default
  table); `canon estimate` forecasts a run/regen without writing
  (estimator + `cost_model.json` are pack data; calibrates from stats
  actuals). Byte-determinism exemptions are exactly 3 basenames:
  bible.json, log.jsonl, generation_stats.json (tests/treediff.py).
- **SECRET ROOMS (Arc 3, 2026-07-16)**: a level may hide 1-2 mini-level
  rooms (README "Secret rooms"); everything rolled in code from
  `secret_rooms.json` on the `"secret"` rng key (recomputed identically
  by layout/placement/DAG expansion — zero persisted fields); entrances
  stitcher-placed on reachable footholds (escalation host→whole-level,
  shortcut→detour demotion); rooms invisible on the world map, priced by
  `canon estimate` (fresh_plan.secret_rooms_avg), regen-addressable
  (`canon regen <bible> l6r1 --mark-only`). Contents by type: vault =
  zero enemies (code fast path) + premium anchors, lair = champion
  directive, shortcut = coin-trail directive. In play: pipe = Down
  alone, door = Up (swallows the jump); carry-everything; per-map
  caches (collected/spent/crumbled/dead/claimed); death ejects to the
  parent checkpoint; PLAT_LEVEL=<room_id> boots a room directly;
  PLAT_ACTIONS="<frame>:<down|up>,..." scripts entries for parity runs.

### Generation reliability — the three-run paid arc (same seed, 2026-07-14)

| run | code state | result |
|---|---|---|
| `plat_seaside3` | G7 | 5/9 whole-fallbacks (all 3 climbs = bare ladders) |
| `plat_seaside4` | + H-series | 1/9 (l2 seam corridor trap; l8 one flat section) |
| `plat_seaside5` | + P-series | **0/9 — fully clean; zero section retries; zero traces written; all exits sim-reachable** |

seaside5's only warnings are art cosmetics (3 spike palette-conformance — now
FIXED in code; 4 animation-readability; 1 sprite hue). The paid traces are
checked-in regression fixtures; the failing classes each have $0 oracle tests.

### Real runs on disk (check actual state before believing this)

- `~/Documents/projects/plat_seaside3|4|5` — the A/B chain above. **Seed
  gotcha:** their bible seed is LITERALLY the string `<same hostile seed>` (a
  kickoff-template placeholder pasted verbatim into the original command);
  reuse it verbatim to reproduce the knob rolls. seaside4/5 have real fal art
  + elevenlabs SFX + VLM QA but FAKE music (`GOOGLE_API_KEY` is not in `.env`
  — lyria needs the user's own shell export). Backfill: `canon regen
  <dir>/bible.json audio:<stage> --mark-only`, then the USER resumes with the
  original flags + `--music-backend lyria`.
- `~/Documents/projects/plat_orchard` — older full paid run (pre-sections).
- `~/Documents/projects/plat_validate_paid` / `plat_validate` — flyer/aggro
  validation pair (paid / $0 twin).

## House rules (non-negotiable, also in memory)

- Claude NEVER runs git; the user commits/pushes/merges. (One-off exceptions
  only when the user explicitly directs.)
- Paid backends are USER-run unless the user explicitly directs otherwise;
  `set -a; source .env; set +a` first. Claude may always run fake/$0.
- `canon resume`/`regen` WITHOUT `--mark-only` hardwires FakeLLM — only
  mark-only is safe against real dirs. Resume = the runner command with the
  ORIGINAL flags (especially `--image-backend fal`).
- Code-not-LLM: computable fixes are TOOL jobs; LLM/VLM feedback is for
  design judgments only.
- Art/audio/VLM at the END of the loop. Camera = zoom at stable 1280x720.
- Validator messages are prompts: located cells + the concrete op.
- pygame = pre-art mechanics surface (parity mandatory); presentation is
  Godot-only.

## Verification bar (per change, non-negotiable)

Suite green (lyria deselect below) · two same-command fake runs byte-identical
(exempt basenames: bible.json, log.jsonl, generation_stats.json — the
observability files; tests/treediff.py is the single exclusion list) ·
orchestrated == sequential minus the same exemptions · MazeWorld untouched ·
canned fake exercises every new feature (paid-only paths get module-test
coverage — fake e2e trees have NO sprites: the bare fake's 1×1 transparent
placeholder fails sprite generation by design) ·
`godot --headless --check-only --path <TREE ROOT> --script <tree>/godot/main.gd`
with output grepped for `SCRIPT ERROR` (rc is 0 even for broken scripts;
the `--path` must be the dir holding project.godot) · ruff clean · for any
consumer/art change: frame captures on both surfaces, ACTUALLY LOOKED at.

## Commands

- Fake ($0, full features): `uv run python -m canon.packs.platformer.run_slice
  --backend fake [--image-backend fake] [--music-backend fake] [--sfx-backend
  fake] [--vlm-backend fake] [--engine godot] [--orchestrate] --output-dir X
  --seed emberfall_001` (defaults: 3 stages × 3 levels, 7-enemy pool)
- Real (USER runs, ~$2): same with `--backend anthropic --image-backend fal
  --music-backend lyria --sfx-backend elevenlabs --vlm-backend anthropic`.
- Mark-only (safe on real dirs): `uv run canon regen <dir>/bible.json
  <targets...> --mark-only` — targets: l5, enemy:<id>, tileset:<sid>,
  backdrop:<sid>, audio:<sid>, props:<sid>, player, splash,
  phase:plat:<name>. (`splash` is the world card's LEAF id — pin/edit
  it as `splash`, never `world`: a hand-edited card adopts with ZERO
  stale descendants, matching the backdrop-band leaf doctrine.)
- Pins: `uv run canon pin <dir>/bible.json <ids...>` / `--list` / `unpin`.
- Estimate ($0, never writes): `CANON_PLAT_OUT=<dir> uv run python -m
  canon.cli.main estimate <dir>/bible.json [targets...] --pipeline
  canon.packs.platformer.dag:cli_ctx_factory --phases
  canon.packs.platformer.dag:cli_phases_factory --estimator
  canon.packs.platformer.estimate:estimate_run` (missing bible = fresh
  forecast).
- Tests: `uv run python -m pytest tests/ -q --deselect
  tests/test_backend_lyria.py::TestLyriaMusicBackendRegister::test_register_adds_lyria_to_registry`
- Lint: `uv run ruff check src/ examples/ tests/`
- Play: `uv run --extra platformer --extra play python
  -m canon.packs.platformer.play <dir> l1`; Godot: `godot --path <dir>`
  (`PLAT_LEVEL=<lid or room id>` to skip the map). Verification hooks:
  `PLAT_TRAJ`, `PLAT_CAPTURE`, `PLAT_HOLD` (+`_JUMP_EVERY`),
  `PLAT_ACTIONS="<frame>:<down|up>,..."` (scripted room entries; frames =
  traj line numbers), `--fixed-fps 60` for headless parity runs (never
  `--write-movie` headless — MoltenVK crash, env not us; WINDOWED
  --write-movie works for visual captures).

## Key file map (current)

- Pack `src/canon/packs/platformer/`: level.py (section loop, stitch+repair,
  handoff threading, traces), sections.py (blueprint/archetypes/composite),
  dsl.py (ops, stamp auto-repairs, lenient parse, rebase_dsl), validate.py
  (jump-arc sim, check_level, repair escalation incl. mount/lane/door,
  place_exit/checkpoints, placement checks), prompts.py (section_layout w/
  handoff blocks, per-task system prompts), movement.py (momentum vocabulary),
  phases.py (world/stage/enemy ecology), art_phases.py (sprites + animation),
  tileset_art.py (conform_to_palette + segmentation), audio_phases.py,
  vlm_qa.py (QA + animation authoring/QA), render.py, compose.py (pipeline +
  manifest warnings), dag.py (orchestration), sections.json / game_rules.json /
  tiles.json / graphics.json (data), godot_template/godot/main.gd.
- Pack pipeline-arc files: models.py + models.json (per-agent tiers),
  estimate.py + cost_model.json (forecasting).
- Pack graphics-arc files: graphics specs `src/canon/packs/platformer/graphics_specs/*.json`
  (the 3 lanes), art_sampling.py (+ ART_SAMPLING.md walkthrough),
  art_lock.py, tileset_art.py (grid_snap, pack_atlas/reconstitute_frame,
  shade_floor_variant, png_bytes/art_provenance, apply_watermark,
  splash_image), tileset.py (16-slot floor + autotile.json), layers.py
  (mask resolution), art_phases.py (WorldArtPhase, VFX PROP_SPECS,
  foreground band), effects.py (lighting kinds), backends
  image_retro_diffusion.py / image_pixellab.py + base.py capabilities.
- Core: canon/pipeline/orchestrator.py (+ initial_skips shared with
  estimate), canon/pipeline/steplog.py, canon/backends/ (anthropic,
  vlm_anthropic, image_fal + ImageEditBackend, testing fakes).
- Tests to know: test_platformer_slice.py (dsl/validators/e2e/oracles),
  test_platformer_sections.py (blueprint/stitch/repair escalation + paid-trace
  oracles), test_platformer_dag.py (orch==seq), test_multistage.py,
  test_vlm_qa.py, test_art_phases.py. Fixtures: tests/fixtures/plat_seaside*.

## Locked decisions (memory has details — do not relitigate)

Biomes share art; world map code-drawn v1; ecology = pool + habitat/rarity;
start/end screens; chasing rare + leashed; no hazard entry / no clipping;
water optional + as features; env feasibility is code's call; pygame stays
lean; per-level view = deliberate exception; stomp-only combat + hearts;
checkpoint enemy reset; slopes stepped v1; sections capped at 5; repair caps
deliberate (≥10-col solid blocks stay design failures); breakable = permanent
foothold for reachability; exit relocation prefers the last section.

# =====================================================================
# THE SIX-ARC ROADMAP IS COMPLETE — hand-over state
# =====================================================================

## RELIABILITY CHUNK (2026-07-18, postmortem tickets 3+4 — F1/F2)

**F1 repair economics** (validate.py/level.py): fix-line overflow rung
(the validator's simulation-VERIFIED suggested op stamps in code past
the bridge cap, MAX_FIX_LINE_STAMPS=3 — the l8 case where it printed
the fix then burned an LLM round); verbatim-repeat short-circuit
(identical problems after a regen round → straight to escalation —
reclaims the 19% dead retry spend; l8's moving break correctly spared);
op-naming feedback (break rebased to SECTION-LOCAL coords + the
section's own ops named — the model was doing this subtraction in
wasted prose); spawn-clear rung (snap-exhausted covered spawn cleared
as geometry, ≤2 cells — l8r1 shipped 100% canned over one stair cell).
The four paid traces are fixtures (tests/fixtures/plat_ember/) with
oracle tests. **F2 physics contract** (movement.py/prompts.py):
_physics_guidance states the validator's own vocabulary (max jump
distance foothold-to-foothold + per-rise table), the '~6' running-jump
literal is DERIVED (run_jump_width — low-g levels get honest numbers),
and difficulty≥3 sections are told to build challenge INSIDE the
simulator's numbers. Expectation for the next paid run: 4/43 fallback
sections → ~0-1, retry tax down sharply.

## READABILITY ARC (2026-07-18, post-postmortem ticket 2 — RB1/RB2/RB3)

Composite readability, three rungs (memory `project_paid_run_lessons`):
**RB1** palette-time clamps — `separate_safe_volumes_from_hazards`
(style.py repair chain, FIRST: a swimmable volume within 40° of a
hazard hue snaps to the safe-liquid band, luminance preserved;
DAMAGING volumes like lava deliberately exempt) + actor placeholder
luminance floor (`placeholder_color` background_lums, ΔL ≥ 40 vs every
stage background, hue-exact shift); shared math in the new leaf
`color.py`. **RB2** translucent water on all four surfaces
(`graphics.water_alpha` = 0.55 default; skinned/block renders blend,
pygame SRCALPHA, godot modulate.a; art stays opaque on disk; traj
byte-identical — draw-only). **RB3** `composite_contrast` — an
ADVISORY-ONLY code check (USER-locked: NEVER blocks a build, never
flips review_status, never a manifest warning — structural: records
ship `passed: true` hard-coded + name-skips in both
_flip_review_status and derive_qa_warnings): samples the skinned
render behind each placed enemy/item across its patrol strip, flags
ΔL < 30 with no hue rescue into qa_report.json + logger only.
NOTE: water_alpha + new records change skinned bytes → ONE staleness
re-judge on the next paid VLM run (expected).

## 0. USER actions (nothing for Claude here)

- **Commit** the branch — it now carries ALL SIX ARCS uncommitted:
  pipeline, items, multi-room, water, combat picks, graphics
  (G1-G7). `git status` is large by design.
- **Sample the lanes** (the Arc 6 paid lever, entirely yours):
  `--art-sample player --sample-backends fake,fal,retro,pixellab`
  against each `src/canon/packs/platformer/graphics_specs/*.json`, judge the contact
  sheet + grid-drop, hand-edit an `art_lock.json` to `status:
  approved`, then batch with `--art-lock`. ART_SAMPLING.md is the
  walkthrough (keys, costs, commands).
- **Playtest queue** (unchanged + new): rooms (pipe Down / door Up,
  die inside), water (l4/l6 swim + waders + urchins, l9 waterline),
  low-gravity finale, hoppers, emberborn on spikes, hold-vs-tap
  bounce; NEW — fall/land/skid + squash on landing, autotiled floor
  edges, the foreground band parallax, dust/splash/sparkle (needs
  generated art), lighting kinds (roll or hand-add to stage.json),
  the splash card (needs a generated splash; skips cleanly without).
- **Feel-tune**: momentum numbers (`PlayerMovementSpec`) still await
  real play; `anim_frame_ms`/`durations_ms`/loop modes are hand-edit
  levers.
- Optional: lyria backfill onto seaside5; mark-only re-rolls for
  cosmetic QA warnings.

## 1. Deferred mechanics ledger (by decision — the next scope pool)

Vine (verb weight 0), flow/current push, breath meter, vertical water,
rooms on fully-submerged levels, melee, stomp SFX, smooth slopes,
attack-windup anim state, props animation, per-sprite color caps,
47-blob autotiling (data swap on the 16-variant scheme), HUD pixel
font + world-map art (accepted coverage gaps, listed in QA), PixelLab
MCP inline upgrade, durable C2PA provenance layer (c2pa-python is the
named path; readable tEXt ships today).

## 2. DEFERRED (by decision — not "to do")

- Bosses → v1.1 (modeled, no generator phase; v1 story = champion variant at
  a chokepoint).
- v1.5 Bible-completeness (MazeWorld joins resume/regen); Unity/Unreal/pygame
  output adapters; DSL plugin pattern; auto-migrate schema; per-band pins;
  GeminiImageBackend; branching world graph (data seam exists, map is linear).

## Session protocol reminders for the next Claude

1. Read memory FIRST (MEMORY.md pointers) — locked decisions + the full
   sectioned-levels history live there.
2. Check real-dir disk state before believing any doc's claims about it.
3. Plan mode + user sign-off for any phase-sized feature; scope BEFORE code.
4. Land in verifiable chunks; run the FULL bar per chunk. Frame captures must
   actually be LOOKED at.
5. Update this handoff + memory at session end; remind the user to commit
   (Claude never runs git). Keep this document SHORT — history goes to
   memory, not here.
