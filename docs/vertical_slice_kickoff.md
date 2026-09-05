# Claude Code Kickoff — Platformer Vertical Slice ("Phase 3-Lite")

## Re-sequencing note (decided 2026-07-05)

The original phasing (PRD §14) ran Phase 2 (orchestrator) before the pack.
We are **reordering**: this vertical slice comes first, because the project's
priority is *reviewable platformer output* and the orchestrator generates no
game data. What this changes and what it doesn't:

- **Phase 2 (orchestrator) is deferred, not descoped.** Its kickoff
  (`docs/phase2_kickoff.md`) is already drafted and remains valid. The slice
  runs on the existing sequential `run_pipeline` — packs have never needed
  the orchestrator to execute.
- **Godot (Phase 4) remains the real target and remains important.** The
  platformer is a Godot game. Everything in this slice writes through the
  adapter seam precisely so `GodotOutputAdapter` can be swapped in later
  without touching generation.
- **The pygame harness in this slice is FILLER CODE.** It exists to simulate
  and review what the generated databases look like — nothing more. It is
  throwaway by design: no menus, no art, no sound, no polish, and it must
  never grow features that belong in the Godot build. If a change to the
  harness doesn't help you review generated data, don't make it.
- **MazeWorld is not the yardstick anymore.** We know Canon can make a
  MazeWorld; the point is proving Canon can make a *platformer*. MazeWorld's
  only remaining role is regression ballast: its suite and byte-parity must
  keep passing, but no slice effort goes toward it.

## Your task

Build the smallest end-to-end platformer generation slice: hand-written
schemas → LLM-driven world/enemy/layout generation → stamped `.npz` masks →
entity placements → **a color-coded placeholder tileset** → per-level PNG
renders → a throwaway pygame review harness. One world, one stage, 2–3
levels, 2–3 enemy types. Runs on the sequential runner with `--backend fake`
(deterministic, $0) and `--backend anthropic` (real content).

This slice dogfoods everything Phases 0–1 built: schemas load through
`canon.skeleton.loader`, models are the Phase 1 platformer models, every
write goes through `ctx.adapter` and its returned content hash gets stamped
onto the owning entity, and all randomness derives via `canon.pipeline.rng`.

## The placeholder-tileset concept (core of this slice)

The LLMs decide layout, enemy placement, and enemy behavior — and we want to
*see* those decisions the moment they're generated, long before real art
exists. The mechanism, mirroring MazeWorld's color-coded squares:

- **`PlaceholderTilesetTool`** (deterministic Tool, no LLM, no diffusion):
  programmatically renders a tilesheet PNG of solid-color squares and emits
  a real `Tileset` model — slots mapping `TileType` → sheet region, written
  through the adapter like any artifact. Tile colors are fixed framework
  defaults (floor/platform/wall/spike/empty).
- **Enemy colors come from the database.** Each `EnemyDefinition` gets a
  placeholder color derived deterministically from its ID
  (`derive_seed(enemy_id)` → hue), stored on the definition (e.g. in
  `stats["placeholder_color"]` or a dedicated field). Same enemy = same
  color across every level, every run, every review surface.
- **Everything downstream keys off the databases, not hardcoded colors.**
  The renderer and the pygame harness read `Tileset` + `EnemyDefinition` +
  `Level.entities` placements and resolve refs to draw. When real art
  arrives (Phase 3 full / diffusion tools), the *only* change is which tool
  produced the tilesheet and sprites — every consumer already resolves
  through the same `Tileset`/`RigManifest` path. Placeholders are a tileset
  implementation, not a special case.
- **Behavior is reviewable, not just position.** Enemy behavior params
  (archetype, patrol_range, speed, aggro_range — skeleton-rolled closed
  sets) are *executed* by the pygame harness: a patroller patrols its rolled
  range, a chaser chases within its rolled radius. Watching a colored square
  move wrong IS the review.

## Read first

1. This file, then PRD §5.1–5.2 (node tables — you are building the lite
   versions), §4.3 (DSL grammar closed set), §6.2/§6.4 (mask formats +
   output tree — follow the `data/level/{stage}/{level}/` layout exactly).
2. `docs/phase1_kickoff.md` + `canon/bible/platformer.py`,
   `canon/bible/artifacts.py`, `canon/skeleton/loader.py` — the models and
   loader this slice must exercise unmodified.
3. `examples/mazeworld_pack/` — the pack pattern to mirror (compose.py
   composing phases; pack-owned specs and prompts).

## What to build

### 1. Pack: `examples/platformer_pack/`

- **Schemas as data files** (`schemas/*.json`, loaded via
  `load_skeleton_spec` — this is the loader's first real consumer):
  `enemy.json` (archetype/hp/speed/patrol_range/aggro_range — closed sets
  and ranges), `level_layout.json` (chunk-count/difficulty knobs). Plus
  `PlayerMovementSpec` values (jump height, run speed) in the pack config —
  consumed by the stamp validator and the harness so physics agree.
- **Phases** (plain sequential `Phase`s — NO `requires`, NO DagPhase):
  1. `WorldPhase` — LLM: title, 1-stage graph, stage brief.
  2. `StagePhase` — LLM: theme, level briefs, enemy-roster brief.
  3. `EnemyGeneratorPhase` — skeleton-rolls mechanics from `enemy.json`,
     LLM writes name/flavor/behavior selection from closed sets; emits
     `EnemyDefinition`s with placeholder colors; writes
     `data/enemy/{id}.json` via adapter, stamps hashes + `artifact_id`.
  4. `LayoutAgentPhase` — LLM emits a **DSL string** per level (I3: agents
     never touch grid cells). Grammar subset: `floor()`, `platform()`,
     `gap()`, `pit()`, `spawn()`, `exit()` (+ `spike()` hazard marker).
  5. `StampTool` — deterministic DSL → `int8` collision grid (TileType
     enum) → `collision.npz` via `adapter.write_numpy`; hash stamped onto
     `Level.collision_hash`.
  6. `EntityPlacementPhase` — LLM picks which enemies where (refs into the
     enemy DB + positions on walkable cells); writes placements to
     `Level.entities` + `entities.json`.
  7. `PlaceholderTilesetTool` — as described above.
  8. Slice validators (Validator nodes, kick back once then accept-with-
     warning): stamp output has exactly one spawn + one exit; placements
     sit on standable cells and not on spikes (I5 spirit); **reachability-
     lite**: greedy flood-fill with max-jump-height/width from
     `PlayerMovementSpec` proves spawn→exit reachable. Full A* + physics
     stays in Phase 3 proper.
- `compose.py` + `examples/run_platformer_slice.py` mirroring the MazeWorld
  runner (`--backend fake|anthropic`, `--output-dir`, `--seed`).

### 2. Review surfaces

- **Renderer** (`examples/platformer_pack/render.py`, Tool not Phase, also
  runnable standalone): per-level PNG — collision tiles colored via the
  placeholder tileset, enemy placements as their DB colors, spawn/exit
  markers — plus `legend.png`/section: color ↔ enemy name, archetype,
  behavior summary. Written into the data tree (`data/review/`) via the
  adapter. This is the "look at maps as soon as they're generated" surface.
- **Pygame harness** (`examples/platformer_play.py` — FILLER, see
  re-sequencing note): loads a level dir + enemy DB, renders placeholder
  colors, player move/jump/collide against the collision grid using
  `PlayerMovementSpec` values, enemies execute their behavior params.
  Quit key, level-select by CLI arg. Nothing else. Keep it under a few
  hundred lines; it earns zero tests beyond an import smoke test.

### 3. Dependencies

Add a `platformer` extra to pyproject: `numpy` (masks) + `Pillow`
(tilesheet/render PNGs). `pygame` goes in a separate `play` extra so the
generation path never depends on it. Core deps unchanged.

## Acceptance criteria

1. `run_platformer_slice.py --backend fake --seed X` twice → byte-identical
   trees (the Phase 0.5 determinism bar, now for the platformer); tree
   matches §6.4 layout (world.json, stage/, level/{s}/{l}/ with
   collision.npz + entities.json, enemy/, tileset/, review/).
2. `--backend anthropic` produces a coherent themed world (manual review —
   attach a render to the summary).
3. Every file-backed artifact's hash on the Bible matches a recompute from
   disk (Phase 1's edit-detection contract, proven by test).
4. Renders exist per level; an enemy's color in the render matches its
   color in the legend and in the harness.
5. Reachability-lite passes on generated levels (or the validator's
   kickback is visible in logs and the retry succeeds).
6. Harness: `uv run --extra play python examples/platformer_play.py
   <data_dir> <level_id>` → playable colored-square platformer; enemies
   visibly patrol/chase per their rolled params.
7. Schemas load from JSON files via the Phase 1 loader — zero Python
   literal specs in the pack for enemy/layout mechanics.
8. MazeWorld: full suite green + two-run byte-parity unchanged.

## What NOT to do

- No orchestrator, `requires`, resume, or status verbs (deferred Phase 2).
- No diffusion, no real sprites/tiles, no VLM QA, no music/sfx, no rigs
  beyond the model fields that already exist.
- No boss pipeline, no multi-stage worlds, no triggers/foreground layers —
  the models support them; the slice doesn't populate them.
- No Godot anything (Phase 4) — but never write a file the Godot adapter
  couldn't also have produced from the same Bible (relative paths, §6.4
  tree, everything through the adapter).
- No pygame polish. It is filler. It dies when Godot lives.
- No new CLI verbs; the runner script is an example, like MazeWorld's.
- No `canon/` core changes beyond (if truly needed) small additive fixes —
  if the slice forces a core change, flag it in the summary as Phase 1
  feedback rather than silently expanding scope.

## Concerns to watch for

- **DSL ambiguity.** Keep the grammar tiny and the parser strict — reject
  unknown ops with the offending token named; the retry-with-feedback loop
  handles LLM malformations. Determinism: same DSL string → same grid,
  always.
- **Placement grounding.** LLMs will place enemies in walls. Validate
  against the stamped grid and kick back with the standable-cell list
  summarized, not the whole grid (I3 applies to prompts too — describe,
  don't dump numpy).
- **Color collisions.** Derived hues for enemies must be visually distinct
  for small rosters — reserve tile colors (floor/spike/etc.) and space
  enemy hues (e.g. golden-angle steps over the hue wheel seeded by ID
  order), don't purely hash.
- **Harness physics vs validator physics.** Both must read the same
  `PlayerMovementSpec`; if the validator says reachable but the harness
  can't make the jump, the slice has failed its review purpose — test one
  canonical jump case against both.
- **Seed flow.** Every phase derives per-level/per-entity RNG via
  `derive_rng(config.seed, phase_name, ...)` — the Phase 0.5 rule; no
  shared-RNG draws in loops.

## Summary of your delivery

1. Files created/modified, one line each.
2. Determinism + hash-recompute + MazeWorld regression results.
3. A rendered level PNG (attach/send it) and the command to play that level.
4. Deviations, and any Phase 1 model friction discovered (feeds Phase 3).
5. Handoff notes: what Phase 3 proper should harden (full reachability,
   real tilesets, boss, triggers), and what Phase 2 should know about how
   these phases would decompose into DAG nodes.

## Scope commitment

The slice succeeds when you can generate a world with one command, open a
PNG and understand every LLM decision at a glance, and walk a colored
square through a level while other colored squares behave as their database
says they should. Anything that doesn't serve those three sentences is out.
