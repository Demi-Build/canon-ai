# PRD — Platformer Generation in Canon

**Status:** v1 is SHIPPED end-to-end and paid-validated (three-run A/B, final run 0/9 layout fallbacks). This document is now largely a record of decisions plus the remaining deltas — see the dashboard below.
**Project:** Canon Game 2 — 2D Mario-like Platformer (Godot)
**Predecessor:** MazeWorld (Pygame, Game 1)
**Last grounded against code:** 2026-07-14 (`updated_level_const` @ sectioned levels H/P-series; suite 1825)

> **Convention used in this document.** Claims about Canon's *current* behavior cite `file:line`. New work introduced by this PRD is tagged **[NEW]**. Decisions that are settled but not yet built are tagged **[DECIDED]**. Sections were drafted before the build; the dashboard + per-section status lines mark what is now SHIPPED.

## Status dashboard (added 2026-07-14 — grounded against code)

| Section | Status | Note |
|---|---|---|
| §2.2 "Decided, not yet built" | ✅ SHIPPED | All six rows built (adapter, loader, provenance, DAG/resume, definitions/placements, Bible-complete platformer entities). |
| §3 User-facing surfaces | ✅ mostly | All live except `log.jsonl` (⏳). |
| §4 Schema system | ✅ mostly | JSON↔SkeletonSpec loader shipped (`skeleton/loader.py`); schemas are live pack files (App. D). BossSchema ⏳ v1.1; D-ii auto-migrate ⏳; DSL grammar grew far beyond §4.3 — App. F.4 is current. |
| §5 Pipelines | ✅ as-built | With recorded deltas (App. A): art/audio at END of loop, one world enemy pool, no bosses, LAYOUT step superseded by App. F (sectioned levels). §5.5 human gates ⏳ (VLM QA advisory shipped instead). |
| §6 Data layer | ✅ SHIPPED | Models, npz-by-reference, provenance hashes, `parents` cascade, artifact IDs, output tree — all live. `.canon/log.jsonl` ⏳. |
| §7 Orchestrator | ✅ SHIPPED | DAG + per-step resume/regen/stale/pins/edit-detection; CLI `run/status/resume/regen/pin/unpin/phase`. `estimate` ⏳; `log.jsonl` ⏳; `awaiting_review` ⏳. |
| §8 Adapter layer | ✅ SHIPPED | Json + Godot adapters live. §8.4 v2 adapters ⏳; §8.5 v1.5 Bible-completeness refactor ⏳. |
| §9 Models & cost | 🔶 partial | §9.1 tier TABLE drafted (doc); `model_tiers`/`agent_tiers` config NOT implemented (only global `--model`). §9.2 `estimate` ⏳. |
| §10–§11 | 📝 current | Contracts/deferred lists still accurate. |
| §12 Success criteria | ✅ mostly | Met end-to-end except cost forecasting (⏳) and bosses (v1.1). |
| §13 Risks | 🔶 updated | Tileability, sprite consistency, layout-at-scale all RESOLVED in practice; cost known (~$2/world). |
| §14 Phasing | ✅ done | Executed per App. E.6 ordering; Phase 5 partially (VLM QA ✅; estimate/log.jsonl ⏳). |
| App. E (3a additions) | ✅ SHIPPED | Water, layers, tile collision semantics, placement variants, variable dims, game-rules-as-data — all live. |
| App. F (sectioned levels) | ✅ SHIPPED | Through the H/P-series; paid-validated 0/9 fallbacks. |

---

## 0. Document Purpose & Scope

**Covers:** extending Canon to generate a 2D platformer's content — world graph, stages, levels (with collision/terrain/hazard mask stacks), enemies, bosses, tilesets, decoration — and exposing it to Godot through an output adapter.

**Does NOT cover:** Cradle's UX and chat orchestrator (separate project); the Schema Suggester agent (Game 3); Unity/Unreal adapters (v2); game-balance tuning; audio design beyond reuse of the existing music/SFX phase.

**The gastown clause:** anything not in this document is scope creep.

**Versioning:**
- **v1** — the platformer end-to-end: schemas → generation → Godot-consumable output.
- **v1.5** — the Bible-completeness refactor that lets MazeWorld and other stub-based packs participate in resume/stale machinery and enables a `materialize` step (see §8.5).
- **v2** — Schema Suggester agent, additional engine adapters (Unity, Unreal, an explicit Pygame adapter).

---

## 1. Background & Motivation

**Why a platformer next.** MazeWorld exercised Canon's RPG-database shape (per-room NPCs/items/monsters/events/quests as `EntityLore`, deterministic maze grids, portraits/music/SFX). A platformer breaks three assumptions at once, which is the point:

1. **Game structure** — spatial *levels* with continuous geometry, not discrete RPG rooms. Levels carry dense grids (collision, terrain) and sparse masks (hazards, triggers), not just an entity list.
2. **Engine target** — Godot, not Pygame. Forces the adapter abstraction Canon has so far avoided (output is currently written directly by phases; see §2 and §8).
3. **Asset types** — multi-part sprites with rig manifests (body + wings + anchors), tile sheets with pixel-range metadata, and binary mask grids referenced from Bible entities. None of these fit the current single-portrait-per-entity pattern.

**The MazeWorld → platformer delta.**
- *Reusable:* the phase/pipeline model, `SkeletonSpec` constraint engine, 3-stage validation, retry-with-token-escalation, `GenerationStats`, the asset backends.
- *New:* an output adapter layer; DAG-aware orchestration with resume; provenance + stale-marking; a global artifact-ID space; numpy mask grids + a DSL→grid stamp tool; a VLM backend for QA; define-once/place-many entity reuse.
- *Breaks:* "output files are derived from the Bible" is currently false (outputs are written in parallel to the Bible, which holds only stubs — see §2). The platformer fixes this locally by making its entities Bible-complete.

**Why Godot.** Free, 2D-first, data-driven, consumes JSON natively, and has a clean `.tres`/`.res` resource format an adapter can target.

**The bigger arc.** Canon as an engine-agnostic, genre-flexible content pipeline. The platformer is the second proof point and the forcing function for the adapter and orchestrator work that a third genre would otherwise demand anyway.

---

## 2. Architectural Principles

The PRD's load-bearing decisions, split by what **exists today** versus what this PRD **builds**. The split matters: the orchestrator (§7) assumes "outputs derive from the Bible," which is *not* true in current code — making that true for the platformer is part of the work, not a given.

### 2.1 Implemented today

| Principle | What it means | Evidence |
|---|---|---|
| **Skeletal-driven generation** | User-specified fields bound LLM output; the skeleton rolls mechanical values (dice, stats, closed-set picks) *before* the LLM writes flavor. | `SkeletonSpec`/`SkeletonField`, `src/canon/skeleton/core.py:118-172` |
| **Closed sets where it matters** | Agents pick from a defined set; they don't invent vocabulary. | `SkeletonField.choices` (weighted), `skeleton/core.py` |
| **Tools first, LLMs only when necessary** | Determinism wherever possible — maze layout, skeleton rolls, validation are pure code. | `generate_maze` (`layout/maze.py:43-91`); deterministic rolls (`skeleton/core.py`) |
| **Each step is its own phase** | Phases are independent units satisfying a minimal protocol (`name`, `run(ctx)`); packs compose them. | `Phase` protocol, `runner.py:17-27`; `compose_pipeline`, `mazeworld_pack/compose.py:191` |
| **3-stage validation** | Per-entity checkers → cross-entity validators → accumulated report. | `validation/checker.py`, `validation/validator.py`, `pipeline/phases/validation.py` |
| **Bounded retry with feedback** | 3 retries, max-tokens escalates 1.5×/attempt (cap 8192), failure reasons fed back into the prompt. | `retry.py:9-108` |

### 2.2 Decided, not yet built (this PRD builds them) — **✅ ALL SIX SHIPPED (2026-07-14)**

*(Kept as written for the rationale; every "Gap vs today" below has since been
closed: platformer entities are Bible-complete, `OutputAdapter` exists
(Json + Godot), the JSON↔SkeletonSpec loader round-trips
(`skeleton/loader.py`), provenance hashes + `parents` + edit detection +
stale-cascade live in the orchestrator, pipelines are DAG-resumable, and
definitions/placements are the shipped model.)*

| Principle | Status | Gap vs today |
|---|---|---|
| **Bible is source of truth; outputs are derived** | **[DECIDED]** | *False today.* `DatabasePhase` writes full entity data straight to disk (`database.py:408-418`); the Bible holds only `EntityLore` stubs. Platformer entities are **Bible-complete** so outputs can be derived; the global refactor is v1.5 (§8.5). |
| **Adapter pattern, not engine forks** | **[DECIDED]** (Option C) | No adapter exists. Phases call persistence helpers directly. §8 introduces `OutputAdapter` on the context. |
| **Schemas are user-editable data files** | **[DECIDED]** | `SkeletonSpec`s are Python literals in packs today (e.g. `NPC_SPEC`). §4 introduces a JSON ↔ `SkeletonSpec` loader so specs are editable files (via hand or Cradle). **Not** formal JSON Schema — Canon's own field-spec JSON. |
| **Provenance + stale-marking (user-controlled invalidation)** | **[DECIDED]** | `GenerationTrail` logs prompt/response/retry/cost (`models.py:182-191`) but there is no content hash, no `parents`, no `status`, no edit detection. §6.3/§7 build them. |
| **Pipelines are resumable** | **[DECIDED]** | `run_pipeline` is a bare sequential loop with an optional per-phase Bible checkpoint (`runner.py:67-90`). No DAG, no phase status, no resume. §7 builds it. |
| **Definitions are reusable; placements are references** | **[DECIDED]** | MazeWorld instantiates entities *per-map*. The platformer defines an entity once (global artifact) and *references* it from many levels (§6.1). |

---

## 3. User-Facing Surfaces (v1)

*Status: ✅ SHIPPED (all bullets live except `log.jsonl` — deferred).*

What a user actually touches:
- **Schema files** — Canon **field-spec JSON** (not formal JSON Schema), one per entity kind, in `data/schemas/`. Editable by hand or through Cradle's form UI (the form is an alternate transport over the same JSON). Deserialized into `SkeletonSpec`s. See §4.
- **Style-guide stub** — generated on init, user-editable.
- **`canon` CLI** — primary surface; subprocess-driven by Cradle; every verb emits structured JSON (`cli/main.py`).
- **Output tree** — `data/...` consumable by Godot via the adapter (§6.4).
- **Failure surfaces** — Bible-embedded phase/artifact status + `log.jsonl`, surfaced in Cradle (§7.6).
- **Edit-and-continue** — user fixes a stuck or stale artifact; the pipeline resumes and cascades staleness.

---

## 4. Schema System

*Status: ✅ SHIPPED in substance — the loader/serializer round-trips
(`skeleton/loader.py: load_skeleton_spec`/`dump_skeleton_spec`), the schemas
are live pack files (Appendix D). Still open: BossSchema (v1.1), D-ii
auto-migrate, DSL plugin pattern (v2). §4.3's DSL list is the ORIGINAL core —
the shipped vocabulary is far larger; Appendix F.4 is current.*

### 4.1 Schema philosophy
- **Canon field-spec JSON**, not formal JSON Schema (draft-07). Each schema is a serialization of a `SkeletonSpec`: an ordered set of fields, each either a **user-defined closed set** (`choices`) or **left open for the LLM** to generate, plus `range`/`lookup` field modes that already exist (`skeleton/core.py`).
- A genre template ships defaults; the user customizes.
- Schemas are validated at load/construction time, before any generation runs (`SkeletonSpec` dependency-order validation, `core.py:145-172`).
- **[NEW] JSON ↔ SkeletonSpec loader/serializer** — reads `data/schemas/*.json` into `SkeletonSpec`s and serializes back out so Cradle can render an editor. This bridge does not exist today (specs are Python literals) and is the work that makes schemas user-editable. **Belongs in Phase 1 (Data layer), not Phase 3.**
- Schema versioning: stale-mark dependents on change. **Option D-i** (stale-mark only; user re-runs) ships in v1; **Option D-ii** (auto-migrate with user intervention on failure) is a TODO. *(Inline these option definitions when drafting — they were referenced but undefined in the outline.)*

### 4.2 Required schemas for the platformer
*(Field bodies developed iteratively; names + purposes only here.)*
`ProjectConfig`, `WorldSchema`, `StageSchema`, `LevelSchema` (incl. **TileType int enum**), `EnemySchema`, `BossSchema` (extends Enemy with multi-phase + arena), `TilesetSchema`, `RigSchema`, `StyleGuideSchema`, **`PlayerMovementSpec`** (jump_height · run_speed · coyote_time), **`GridDims`** (x · y · pixels_per_cell). The last two are load-bearing inputs to Pipeline 2: the Layout Agent reads them to emit reachable geometry, and the **Reachability Validator runs A\* + jump physics against `PlayerMovementSpec`** (§5.2). `GridDims`/`pixels_per_cell` may live on `ProjectConfig` globally or be overridden per level.

### 4.3 Framework-level closed sets (Canon-internal, not user-extensible in v1)
DSL grammar (`platform()`, `gap()`, `pit()`, `floor()`, `wall()`, `spawn()`, `exit()`, `checkpoint()`); mask-type taxonomy (binary, indexed, sparse); model-call types (LLM, VLM, diffusion); phase status enum; artifact status enum. **TODO:** DSL plugin pattern in v2.

---

## 5. Pipelines

Two pipelines, transcribed from the diagrams in Appendix A:
- **Pipeline 1 — Macro game generation.** Project init → World → Stage → Level. Top-down; mostly parallel *within* a layer.
- **Pipeline 2 — Level generation with masks.** The inner loop, invoked once per level. Strictly ordered.

The top-level orchestrator (the **"Mayor"**, gastown pattern) is not a pipeline node — it's the runtime that owns the call graph, parallelism, retry budgets, and gate scheduling (§7).

### 5.0 Node taxonomy & invariants

| Node type | Meaning |
|---|---|
| **Agent** | LLM call with reasoning latitude. Reads its schema, emits schema-validated JSON. |
| **Tool** | Deterministic function or single-shot model call. No reasoning. |
| **Diffusion tool** | Pixel generation (sprites, tiles, backgrounds). |
| **VLM tool** | Vision-language review/QA. Advisory gate. |
| **Validator** | Non-LLM pass/fail gate; kicks back on fail. |
| **Human gate** | Cradle review / approve / edit (§5.5). |
| **Data artifact** | JSON / numpy / PNG written and consumed downstream. |
| **Sub-pipeline** | Reference to another pipeline. |

**Invariants** (hold across both pipelines):
- **I1 — Skeletal-driven.** Every agent reads its schema as part of its prompt and emits JSON validated against it. Schemas are locked before generation (the agents are muscle; schemas are bones).
- **I2 — Diffusion is tool-invoked, never agent-direct.** Agents *write* the prompts; tools *execute* them.
- **I3 — Agents never touch grid cells.** The Layout Agent emits a **DSL string**; the deterministic Stamp Tool expands it to numpy (P2 step 1). DSL is the generation format; numpy is the storage + runtime format.
- **I4 — Human gates are Cradle-surfaced pause points.** Canon pauses, exposes the artifact + a resume token, and waits; Cradle renders the review UI and returns approve/edit/reject. v1 may configure routine gates to **auto-approve** for unattended runs (§5.5).
- **I5 — Mask order is enforced.** Collision precedes everything; hazards precede entities (entities can't spawn on spikes); decoration comes after gameplay layers are locked.

### 5.1 Pipeline 1 — Macro Game Generation

**Structure.** Layer 0 — Project Init (once) → Layer 1 — World → Layer 2 — Stage (×N, parallel) → Layer 3 — Level (×L per stage). Within a stage, the **Enemy (×M), Tileset, and Boss** sub-pipelines run in parallel; Level pipelines start once that stage's **Tileset + EnemyRoster** are ready.

**Layer 0 scope [DECIDED].** The diagram's **Pitch Parser / Schema Drafter / Schema Diff** nodes are a **Cradle-side chat agent** that helps users author and understand schemas — *out of Canon scope* (§10), not Canon phases. In v1 the user **hand-writes** schemas. Canon's Layer 0 is therefore just: the **Schema Validator** gate (locks the hand-written schemas) and the **Style Guide Agent**. The Human Schema Review gate (HG1) stays — it gates hand-written schemas too.

**Brief-passing spine.** Each layer emits *briefs* that seed the next: World → `StageBriefs`; Stage → `LevelBriefs` + roster brief + boss brief. This is the top-down backbone.

**Canon nodes (spec):**

| Node | Type | Consumes | Produces | Owns / does NOT own |
|---|---|---|---|---|
| Style Guide Agent | Agent | UP (pitch, genre template) | `StyleGuide` (palette, tokens, refs) | Owns visual vocabulary. Not assets. |
| Schema Validator | Validator | hand-written schemas | `Locked Schemas` (World/Stage/Level/Enemy + TileType enum) | Owns the lock gate. Not drafting (Cradle). |
| World Agent | Agent | Locked Schemas, UP | `World` (stage graph, connectivity, unlock rules, `StageBriefs`) | Owns macro structure. Not stage internals. |
| Stage Agent | Agent | `World` (a StageBrief), Schemas | `Stage` (theme, roster brief, `LevelBriefs`, boss brief) | Owns stage theme + briefs. Not enemy/level content. |
| Enemy Generator Agent | Agent | `Stage` (roster brief), EnemySchema | `EnemyDefinition` (stats, rig, **per-part prompts**) | Owns stats/rig + diffusion prompts. Not pixels. |
| Sprite Generation Tool | Diffusion | `EnemyDefinition` prompts, `StyleGuide` | per-part PNGs | Owns pixel gen. Not prompts. |
| Sprite QA Tool | VLM | per-part PNGs, `StyleGuide` | pass/fail + notes | Style + silhouette QA. Advisory. |
| Tileset Agent | Agent | `Stage`, TilesetSchema | `TilesetManifest` (tile slots + per-tile prompts) | Owns tile taxonomy + prompts. |
| Tileset Generation Tool | Diffusion (tileable) | `TilesetManifest`, `StyleGuide` | `Tileset` (tilesheet PNG + metadata) | Owns tileable pixel gen. |
| Boss Agent | Agent | `Stage` (boss brief), BossSchema (extends Enemy) | `BossDefinition` (+ arena constraints: min vertical, …) | Owns boss spec + arena constraints. |
| Boss Sprite Gen Tool | Diffusion | `BossDefinition`, `StyleGuide` | `BossAsset` | Pixels. |
| Level Design Pipeline | Sub-pipeline → P2 | `Stage`, `EnemyRoster`, `Tileset`, `BossDefinition` | `Level` (masks + entity records) | See §5.2. |

`EnemyAsset` = per-part PNGs + rig manifest. The **EnemyRoster** handed to a level's Pipeline 2 is the stage's set of `EnemyDefinition`s — the *definitions* in the define-once/place-many model (§6.1); levels emit *placements* referencing them.

**Macro stale-cascade [DECIDED].** Confirmed by the diagram notes: re-running the **World Agent** invalidates all Stage outputs; re-running a **Stage Agent** invalidates that stage's enemies, tileset, and levels. This is the `parents`-edge cascade of §6.3/§7.4 at the macro layer.

### 5.2 Pipeline 2 — Level Generation with Masks

> **[SUPERSEDED for the LAYOUT step — see Appendix F, added 2026-07-14.]** The
> single whole-level "Layout Agent → Stamp → Reachability" flow below is now the
> **sectioned** model: a level is a SEQUENCE of typed SECTIONS rolled from a
> deterministic BLUEPRINT, each generated + stamped locally and STITCHED by
> sub-grid compositing, with the validators/repairs run on the whole grid under
> a **code-not-LLM** doctrine (the Stamp tool auto-repairs computable geometry
> instead of raising; a residual DESIGN failure routes to the owning section).
> The rest of the per-step chain (terrain → hazards → entities → triggers →
> decor) is unchanged. Read Appendix F for the current level-generation design.

The inner loop, invoked once per level, **strictly ordered** — each step's output feeds the next. Each step is a **per-step artifact** (§6.1); validators kick back to their agent on fail, and escalate to a human after N retries (§5.4).

**Inputs:** `LevelBrief` (from Stage Agent), `PlayerMovementSpec` (jump_height, run_speed, coyote_time), `GridDims` (x, y, pixels_per_cell), `EnemyRoster`, `LevelSchema` (incl. TileType enum), `TilesetManifest`.

| # | Node | Type | Consumes | Produces (artifact) | Gate / kickback |
|---|---|---|---|---|---|
| 1 | Layout Agent | Agent (DSL) | LevelBrief, PlayerMovementSpec, GridDims, LevelSchema | `…/layout` (Level DSL: `floor/platform/gap/pit/checkpoint/spawn/exit`) | — |
| 1b | Stamp Tool | Tool | `…/layout` DSL | `…/collision` (numpy int8 `(y,x)`; `0=empty,1=floor,2=platform,10=spike…`) | — |
| 2 | Reachability Validator | Validator | `…/collision`, PlayerMovementSpec | pass/fail | **fail → Layout Agent**; N retries → human |
| 3 | Tile Assignment Tool | Tool (adjacency) | `…/collision`, TilesetManifest | `…/terrain` (numpy int, tile IDs into manifest) | TODO: LLM "level-walker" variety pass |
| 4 | Hazard Agent | Agent | `…/collision`, LevelBrief | `…/hazards` (sparse `{x,y,type}`) | **Hazard Reachability** (re-run validator with hazards solid): fail → Hazard Agent |
| 5 | Entity Agent | Agent | `…/collision`, `…/hazards`, EnemyRoster | `…/entities` (sparse **placements** `{x,y,enemy_id,patrol_range}`) | **Entity Validator** (grounded? threat budget?): fail → Entity Agent |
| 6 | Trigger Agent | Agent (often trivial) | `…/collision` | `…/triggers` (sparse: exit, checkpoints, camera locks) | — |
| 7+8 | Decorator Agent | Agent | `…/collision`, TilesetManifest | `…/background` (numpy int, z-behind) + `…/foreground` (sparse: vines, overhangs, props) | — |
| 9 | Final QA Gate | VLM | terrain, hazards, entities, triggers, bg, fg | pass + notes → Human Review | **Optional; stub-able in v1** |
| 10 | Write Tool | Tool (via adapter §8) | all masks + metadata | `levels/{level_id}.json` + npz arrays | — |

**Intra-level order is strict; parallelism is cross-level.** The per-step DAG decision (§7.2) buys per-step **resume / regen / isolation granularity** — it does *not* parallelize steps within one level (they're a dependency chain). Concurrency comes from running many levels (×L), stages (×N), and the enemy/tileset/boss sub-pipelines in parallel. (Data-dependency-wise, `terrain` and the `hazards→entities→triggers` chain both branch off `collision` and *could* parallelize; v1 keeps the strict canonical order for simpler resume and validator kickback.)

### 5.3 Cross-cutting: Style-guide propagation
`StyleGuide` is read by every diffusion tool (Sprite, Tileset, Boss generation) and the Sprite-QA VLM in Pipeline 1, and by the Decorator in Pipeline 2. It is **pinned at generation time** to prevent drift-driven re-rolls (cost containment, §9.3). Per-stage style override is a TODO.

### 5.4 Cross-cutting: Retry & isolation
- **Exists today:** 3 retries, token escalation 1.5×/attempt (cap 8192), failure reasons fed back into context (`retry.py:9-108`). The pipeline's **validators are the `validate_fn`** (reachability, hazard-reachability, entity validator) — a clean fit for the existing loop.
- **[NEW]:** after the Nth failure, **isolate** the artifact, surface to the user, and allow **Retry / Edit-and-continue / Skip** (skip marks unresolved and cascades downstream). No isolation/escalation/edit-and-continue exists today — `retry_with_feedback` silently returns a fallback (`retry.py:108`).
- **Isolation unit = a single step artifact** (per the per-step DAG decision, §7.2). A failed step (e.g. `level:s1/l3/hazards`) is surfaced on its own; Edit-and-continue lets the user fix just that artifact, after which only its descendants re-run.
- **Retry budgets (defaults; per-node overrides in the pack):** LLM agents → the `retry.py` budget (3 + token escalation); validator-gated steps → N kickbacks before human escalation (N configurable, default 3); the Trigger Agent (often trivial) → low budget; diffusion tools → no LLM retry, regenerate on QA-fail or human reject.

### 5.5 Cross-cutting: Human gates **[NEW]**
The diagrams place **routine** human gates throughout — HG1 (schema), HG2 (sprite), HG3 (tileset), HG4 (boss), and the P2 final review — distinct from the *post-retry escalation* of §5.4. These are approve/review/edit checkpoints, not failure surfaces.
- **Canon-side contract:** at a gate the pipeline enters `awaiting_review` (§7.3), writes the artifact, and emits a structured record (artifact ID + resume token) on `log.jsonl`. Cradle renders the UI and calls back with approve / edit-and-continue / reject. The chat/review UX lives in Cradle (§10), not Canon.
- **v1 behavior:** routine gates are **configurable to auto-approve** for unattended runs; escalation gates (after N validator retries) always fire. This keeps a headless `canon run` viable while preserving the interactive path.

---

## 6. Data Layer

This section defines the Bible extensions, how artifacts are addressed and reused, how masks are stored, and the corrected provenance model. It is the foundation the orchestrator (§7) and adapter (§8) read from.

### 6.1 Bible model extensions

**New entity models [NEW]** (Pydantic, in `canon.bible.models` or a platformer submodule):

- **`World`** — the stage graph: stage list, connectivity edges, unlock rules. One per game.
- **`Stage`** — theme, enemy-roster references, boss reference, level references, tileset reference.
- **`Level`** — grid dimensions, references to its mask files (collision/terrain/background), inline sparse masks (hazards/triggers), and an **entity placement list** (see below). Designed **Bible-complete** from day 1.
- **`EnemyDefinition`** — stats, archetype (closed set), rig reference, behavior params, portrait path. A *definition*, addressed globally, reused across levels.
- **`BossDefinition`** — extends `EnemyDefinition` with phases + arena constraints.
- **`Tileset`** — tile slots, tile-type metadata (which pixel range is which `TileType`), tilesheet PNG path.
- **`RigManifest`** — typed model (NOT `extra`): rig type (closed set), part list (e.g. `body.png`, `wings.png`), anchor points, animation specs. Attached to enemy/boss/character entities.

**Definitions vs placements [DECIDED].** This is the model shift from MazeWorld (which instantiates entities per-map). An **`EnemyDefinition`** is authored once and addressed by a stable global artifact ID. A `Level` does not copy it — it holds a **placement**: `{ ref: "enemy:goblin_grunt", pos: [x, y], overrides: {...} }`. The same definition can appear in many levels as one definition + many placements. Per-instance `overrides` are allowed (an "elite" instance) but the canonical definition is the global artifact. Consequences:
- `canon regen enemy:goblin_grunt` regenerates the *definition*; every Level referencing it is marked **stale** via the reference graph (§6.3, §7.4).
- The stale-cascade follows reference edges; `parents` (below) records them.

**Within-level dependency chain [DECIDED — per-step granularity].** A level's step artifacts form a **strictly-ordered** chain (Pipeline 2 is ordered for safety — invariant I5): `layout → collision → terrain`, and `collision → hazards → entities → triggers → {background, foreground}`. The real data edges: `collision` ← `layout`; `terrain` ← `collision` + tileset; `hazards` ← `collision`; `entities` ← `collision` + `hazards` (entities can't spawn on spikes); `triggers` ← `collision`; decoration ← `collision` + tileset. These edges are recorded in each step artifact's `parents` and drive the stale-cascade (§6.3). Per-step granularity buys **resume / regen / isolation** per step — *not* intra-level parallelism (steps are a chain; parallelism is cross-level, §5.2, §7.2). A failed or user-edited step restarts/invalidates only itself and its descendants — not the whole level.

**Per-entity additions [NEW]** to the artifact base (`EntityLore` today; generalized):
- `artifact_id: str` — globally unique, **namespaced**: `world`, `stage:<id>`, `enemy:<id>`, `boss:<id>`, `tileset:<stage_id>`, `item:<id>`. **A level decomposes into sub-level step artifacts** (per the per-step DAG decision, §7.2): `level:<stage_id>/<level_id>/layout`, `.../collision`, `.../terrain`, `.../background`, `.../hazards`, `.../entities`, `.../triggers`, `.../foreground`. `level.json` is the manifest that references them. This is the address space for `regen` (§7.5) and `parents`. The existing map-scoped `(map_id, entity_id)` addressing stays for MazeWorld back-compat.
- `status: ArtifactStatus` — lifecycle enum (§7.3).
- `provenance_hash: str` — content hash (§6.3).
- `parents: list[str]` — artifact IDs this artifact was derived from / references. The edge set the stale-cascade walks.

**Bible metadata addition [NEW]:** `phase_status: dict[phase_name, PhaseStatus]` — coarse per-phase status alongside the fine per-artifact `status`.

**Grids by reference, not embedded.** Dense grids (collision, terrain) are stored as files and referenced by path from the owning `Level`; they are **not** serialized inline into the Bible. (Contrast MazeWorld's `MazeLayout.grid: list[list[int]]`, which *is* inline — see §6.2 for the rule reconciling the two.)

### 6.2 Mask storage formats

- **Dense grids → `.npz`** (numpy compressed), referenced by relative path from the `Level`. Collision, terrain, background.
- **Sparse masks → JSON record lists** (`[{x, y, type, params}, …]`), for layers where most cells are empty. Hazards, entities (placements), triggers, foreground decoration.
- **Collision int-enum.** The collision grid folds binary masks into one `int8` grid keyed by the LevelSchema TileType enum (`0=empty, 1=floor, 2=platform, 10=spike, …`) — one grid, not a stack of binary layers. **Mask order is enforced** (invariant I5, §5.0): collision before all; hazards before entities; decoration last.
- **Two-conventions rule [DECIDED].** MazeWorld embeds its grid inline in the Bible (`MazeLayout.grid`); the platformer references dense grids as `.npz`. This is intentional: a platformer collision grid (e.g. 200×60) embedded as JSON would bloat the Bible badly and defeat diff-based edit detection. **Rule:** dense grids above a size threshold (or any grid for a Bible-complete spatial entity) go to `.npz` by reference; small/legacy grids may remain inline. State the threshold when drafting; default proposal: any grid > 1024 cells references out.
- All level artifacts live under `data/level/{stage_id}/{level_id}/`.

### 6.3 Provenance model

- **Hash inputs:** SHA-256 over the artifact's canonicalized content **plus** schema version + prompt version + model name + seed. Two artifacts with identical content but different generation inputs hash differently (so a model/prompt bump invalidates correctly).
- **Where the hash lives [DECIDED — corrected from outline]:** on the **owning Bible entity**, *not* embedded in the artifact file. Embedding is impossible for binary artifacts (`.npz`, `.png`) and inconsistent for the rest. For file-backed artifacts the entity stores both the reference and the hash (e.g. a `Level` stores `collision: "collision.npz"` and `collision_hash: "sha256:…"`). This keeps edit detection a pure Bible operation and preserves Bible-as-source.
- **Who computes it [DECIDED]:** the **adapter computes** the content hash at write time and returns it; the **phase stamps** it onto the Bible entity. (The adapter is the only layer that touches the filesystem — §8.)
- **Edit detection:** at orchestrator start, recompute hashes from on-disk content and compare to the Bible's stored hashes. A mismatch flags the artifact `user_edited`.
- **Stale-cascade:** when an artifact changes (regen or user edit), every artifact whose `parents` include it is marked `stale`. Staleness is surfaced in Cradle and **not** auto-regenerated — user-controlled invalidation.

### 6.4 Output tree layout

Paths are **relative to `config.output_dir`** and resolved by the adapter (§6.5, §8.2). In a Cradle-managed project, `output_dir` maps to the project's `data/` root; standalone, Canon's default is `./canon_output` (`config.py`). The tree below is shown relative to that root.

```
data/
  world.json
  schemas/                      # user-editable Canon field-spec JSON (§4)
  style_guide.json
  stage/{stage_id}/stage.json
  level/{stage_id}/{level_id}/
    level.json
    collision.npz               # dense, by reference (§6.2)
    terrain.npz
    background.npz
    hazards.json                # sparse, inline records
    entities.json               # placements → enemy/item definitions (§6.1)
    foreground.json
    triggers.json
  enemy/{enemy_id}.json         # definition, addressed enemy:<id>
  boss/{boss_id}.json
  tileset/{stage_id}/
    tilesheet.png
    manifest.json               # pixel-range → TileType metadata
  portrait/enemy/  portrait/boss/
  music/  sfx/
  manifest.json
  .canon/
    log.jsonl                   # structured per-step log (§7.6)
```

### 6.5 Asset paths

The adapter stores **relative** paths in the Bible and resolves them to absolute on write. This fixes the current `AssetPhase` behavior, which stamps **absolute** paths onto `Character.portrait_path` (`asset.py`) — fine for a single-machine MazeWorld run, wrong for a portable, Cradle-managed project tree.

---

## 7. Orchestrator

*Status: ✅ SHIPPED (was the largest net-new build): per-step DAG with
expansion, topo-sort + bounded parallelism, per-artifact status, resume,
`regen` + stale-cascade, pins, hash-based edit detection; CLI
`run/status/resume/regen/pin/unpin/phase` all live (`cli/main.py`), proven by
the orch==seq test bar. Still open: `estimate` (§9.2), `.canon/log.jsonl`
(§7.6), the `awaiting_review` human-gate state (§5.5 — VLM QA advisory
shipped instead).*

### 7.1 Architecture
The orchestrator is the **"Mayor"** (gastown pattern) — the runtime that owns the call graph, parallelism, retry budgets, and gate scheduling; it is not itself a pipeline node. State-on-disk; no persistent process for v1. State lives in the (extended) Bible — coarse `phase_status` + fine per-artifact `status` — not a separate state file.

### 7.2 DAG specification **[NEW]** — per-step granularity
DAG **nodes are step artifacts**, not coarse phases. Each Pipeline-2 step is a Phase that the orchestrator expands across the level set, so a node is a `(step, level)` pair (e.g. `hazards @ level:s1/l3`); macro Pipeline-1 artifacts (world, stage, enemy definitions, tilesets) are also nodes. Each step declares `requires` in terms of the artifacts it consumes; the orchestrator topo-sorts the whole graph and runs independent nodes in parallel, bounded by a config-driven concurrency cap. (Today: no dependency declaration, coarse phases only; one sequential loop — `runner.py:67-90`. The `Phase` protocol must grow a `requires` declaration and the runner must expand per-collection nodes.)

### 7.3 Artifact lifecycle state machine **[NEW]**
Tracked **per step artifact**: `pending → running → done | failed → retrying → escalated`; `done → stale → regenerating`. Plus `running → awaiting_review → done | regenerating` for the routine human gates (§5.5) — a first-class pause state, distinct from `escalated` (which is post-retry failure). Transition table with triggers to be filled.

### 7.4 Resume semantics **[NEW]** — per-step
Resume is **per step artifact**: a failed step restarts from itself (no partial-progress recovery *within* a step), keeping all completed upstream steps' outputs. On start: read Bible, recompute hashes, mark `user_edited` on mismatch (§6.3), cascade staleness down the dependency edges (§6.1). Macro cascade example (from the diagram notes): regen `world` → every `stage:*` stale → each stage's enemies, tileset, and levels stale. **Note:** resume/edit-detection works only for **Bible-complete** artifacts; stub-based MazeWorld entities can't participate until the v1.5 refactor (§8.5).

### 7.5 CLI surface **[NEW]** (except `phase`)
- `canon run <pack>` — run pipeline, resume from state, respect concurrency cap. *(New — only single-phase `phase` exists today.)*
- `canon status` — print state-machine summary.
- `canon resume` — alias for `run` after failure.
- `canon regen <artifact_id>` — single-artifact reroll + stale cascade, addressed by the global ID space (§6.1). *(Existing `regenerate --map --entity` stays for MazeWorld.)*
- `canon phase <name>` — existing (`cli/main.py:255`).
- `canon estimate <pack>` — cost forecast (§9.2). *Depends on the DAG + per-phase cost models; see timing note in §9.*
- All emit structured JSON for Cradle.

### 7.6 Observability
- `data/.canon/log.jsonl` **[NEW]** — structured per-step log.
- `generation_stats.json` — **exists** (`manifest.py:220-222`, from `GenerationStats`, `stats.py`).
- State + log are Cradle's read surface.

### 7.7 Concurrency safety
Atomic writes exist (temp-file + rename, `persistence/__init__.py:44-66`). Phase-status writes serialized through the orchestrator; adapter calls thread-safe within one output dir.

---

## 8. Adapter Layer

*Status: ✅ SHIPPED — `OutputAdapter` on the context, `JsonOutputAdapter`
(default, MazeWorld-compatible) + `GodotOutputAdapter` both live; writes
return content hashes per §6.3. Open: §8.4 v2 adapters, §8.5 v1.5 refactor.*

### 8.1 Pattern — **[DECIDED] Option C**
`OutputAdapter` protocol on `PipelineContext`. Phases call `ctx.adapter.write_*` instead of persistence helpers directly. Default `JsonOutputAdapter` wraps the existing helpers (`persistence/__init__.py`) and preserves all current MazeWorld behavior. `GodotOutputAdapter` writes `.tres`/`.res` and engine-native formats.

### 8.2 Required adapter methods
`write_json_array`, `write_json_keyed`, `write_json_singleton`, `write_per_map`, `write_binary` (sprites/audio), `write_numpy` (mask grids → `.npz` or engine-native), `resolve_path` (relative → absolute). Each write returns the content hash for the phase to stamp onto the Bible (§6.3).

### 8.3 Adapters in scope for v1
`JsonOutputAdapter` (default, MazeWorld-compatible); `GodotOutputAdapter` (platformer target).

### 8.4 TODO: adapters for v2
- **PygameAdapter** — codify MazeWorld's current (implicit) output as a named adapter, for parity.
- **UnrealOutputAdapter** — `.uasset` DataTables, Unreal directory conventions.
- **UnityOutputAdapter** — ScriptableObjects, Newtonsoft-compatible JSON, Asset Database integration.
Develop after the platformer ships and there's real Bible→engine data to design against.

### 8.5 Concerns carried forward from the materialize investigation
- **The stubs problem** — v1.5 Bible-completeness audit before "materialize from a saved Bible" is possible. Platformer entities are Bible-complete from day 1 to avoid it locally.
- **Collision grids are generation, not materialization** — the stamp/grid phase is deterministic, runs in-pipeline, stores the grid in a Bible-referenced `.npz`; the adapter writes the engine format.
- **Rig manifests need a Bible model** — typed `RigManifest` (§6.1), not `extra`.

---

## 9. Generation Models & Cost

*Status: §9.1 drafted (2026-07-13); §9.2 / §9.3 remain outline.*

### 9.1 Model assignment per agent **[DRAFTED 2026-07-13]**

Every LLM node in §5.1 / §5.2 is assigned a **tier**, not a hard-coded model id. Tiers are the durable abstraction; the tier→id map is the single place a model bump lands (today model ids are scattered — `AnthropicBackend.DEFAULT_MODEL` + the `PRICING` table in `src/canon/backends/anthropic.py`; the code TODO there already flags "externalize the pricing/model table"). This section is the assignment table plus the config that realizes it.

**Tier vocabulary** (concrete ids = what the code prices today; swap in the map, not at call sites):

| Tier | v1 model id | $/1M in · out | Use where |
|---|---|---|---|
| `cheap` | `claude-haiku-4-5-20251001` | 0.80 · 4.00 | High-volume, and either (a) a deterministic validator backstops the output or (b) the task is naming / flavor / decoration only. |
| `mid` *(default)* | `claude-sonnet-4-6` | 3.00 · 15.00 | Output defines structure or playability and has **no** validator behind it. Current `DEFAULT_MODEL`. |
| `top` *(opt-in escalation)* | `claude-opus-4-8` | 15.00 · 75.00 | Not a default anywhere in v1 (cost). Reserved for one-shot max-leverage nodes and the hardest reasoning — enabled per-node in the pack/config, never blanket. |

**Assignment principle.** Spend tier where there is no deterministic gate and the output defines structure or playability; drop to `cheap` wherever a §5.2 validator backstops the LLM, or the task is only naming/flavor/decoration. This is the cost lever that pairs with §9.3 (style-guide pinning) and §5.4 (retry): the cheap tier is deliberately placed *behind* validators so a wrong-but-plausible answer is caught and kicked back rather than shipped.

**Pipeline 1 — Macro (§5.1).**

| Node | Type | Tier | Volume | Why this tier |
|---|---|---|---|---|
| Style Guide Agent | Agent | `mid` | ×1 / pack | Pins the palette/tokens every diffusion call reads (§5.3). One-shot, maximum leverage — a bad palette re-rolls art everywhere. |
| Schema Validator | Validator | — | — | Non-LLM gate. |
| World Agent | Agent | `mid` (`top` opt-in) | ×1 / pack | Whole-game macro structure, no validator behind it; one-shot, so `top` is affordable when quality matters. |
| Stage Agent | Agent | `mid` | ×N | Theme + briefs seed an entire stage; no validator. |
| Enemy Generator Agent | Agent | `cheap` | ×M×N | Stats/rig/aggro are **rolled in code**, not by the LLM (see the enemy system prompt: "mechanics are already rolled and fixed"); the agent only names, flavors, and writes per-part diffusion prompts (256-tok budget). |
| Tileset Agent | Agent | `cheap` | ×N | Bounded tile taxonomy + per-tile prompts against a fixed `TileType` enum. |
| Boss Agent | Agent | `mid` | ×N | Boss spec **and arena constraints** that gate level playability; no validator. |
| Sprite / Tileset / Boss Sprite Gen | Diffusion tool | *diffusion* | — | See **Diffusion** below (agents write prompts; tools pick backend — invariant I2). |
| Sprite QA | VLM tool | *vlm* | — | See **VLM** below. |

**Pipeline 2 — Level, per level (§5.2). This is the volume driver (×L).**

| Node | Type | Tier | Why this tier |
|---|---|---|---|
| Layout Agent | Agent (DSL) | `mid` (`top` opt-in) | The gameplay-critical agent: its DSL drives the reachability sim (step 2). Spend here. `top` is the opt-in for hard / sectioned levels (Appendix E — seam-stitched sections retry hardest). |
| Stamp · Reachability · Tile Assignment · Write | Tool / Validator | — | Deterministic (numpy stamp, jump-arc sim, adjacency, adapter write). |
| Hazard Agent | Agent | `cheap` | Hazard-reachability validator backstops it (step 4). |
| Entity Agent | Agent | `cheap` | Entity validator (grounded? threat budget?) backstops it (step 5). |
| Trigger Agent | Agent | `cheap` | Often trivial (exit, checkpoints, camera locks). |
| Decorator Agent | Agent | `cheap` | Cosmetic bg/fg; no gameplay effect (384-tok budget). |
| *(future)* Level-walker variety pass | Agent | `cheap` | The TODO LLM pass on step 3 — a `cheap`, gated refinement. |
| Final QA Gate | VLM tool | *vlm* | See **VLM** below. Optional / stub-able in v1. |

**VLM.** Tier = a vision-capable `mid`-class model. **Status correction to the §9 outline:** the VLM backend protocol is *no longer* new — `VLMBackend` (`src/canon/backends/base.py`), `AnthropicVLMBackend` (`vlm_anthropic.py`), and the `plat:vlm_qa` phase have shipped; `AnthropicVLMBackend` defaults to the text backend's `DEFAULT_MODEL` (all current Claude models accept images) and `--vlm-model` / `--vlm-backend {none,fake,anthropic}` override it. It is **advisory only** — never regenerates, may suggest mark-only targets, and paid Anthropic is used only under an explicit `--vlm-backend anthropic` (default `none`). Remaining VLM wiring: the Sprite QA consumer (§5.1); Final QA (§5.2 step 9) exists as the slice's render-vs-render QA loop.

**Diffusion.** Selected **per asset-class, not per agent** (agents only author prompts — I2). Current state:
- `fal-ai/nano-banana` (txt2img + `/edit` img2img) via `--image-backend fal` — **PAID, explicit opt-in only** (never used unless the flag says so); `local` = deterministic placeholder for the $0 path; `fake` for tests. 32px tiles, region-average sampling, and a palette-conform code tool own pixel snapping.
- The pixel-specialized backends named in the outline (**PixelLab, Retro Diffusion, Scenario**) are **evaluated candidates, NOT integrated in v1**. Each would register as an additional `ImageBackend` and slot into the same `--image-backend` selector; no code change to the agents.

**Config / doc realization.** Two levels, both landing with the pack (Phase 3):
1. `model_tiers` — the tier→id map (single bump point), replacing scattered `DEFAULT_MODEL` references.
2. `agent_tiers` — the per-node assignment above, defaulting to `mid`, overridable in the pack and by config. This is the concrete form of §5.4's "per-node overrides in the pack."

The existing coarse `CanonConfig.model` (global id override; `None` = backend default) stays as the blunt instrument; `agent_tiers` is the **[NEW]** refinement. `--image-model` / `--vlm-model` already cover the non-LLM backends. Proposed shape:

```jsonc
{
  "model_tiers": {
    "cheap": "claude-haiku-4-5-20251001",
    "mid":   "claude-sonnet-4-6",
    "top":   "claude-opus-4-8"
  },
  "agent_tiers": {
    "_default":         "mid",
    "enemy_generator":  "cheap",
    "tileset":          "cheap",
    "hazard":           "cheap",
    "entity":           "cheap",
    "trigger":          "cheap",
    "decorator":        "cheap",
    "world":            "mid",       // "top" to buy structural quality on a one-shot node
    "layout":           "mid"        // "top" for hard / sectioned levels
  },
  "vlm_tier": "mid"
}
```

**Interaction with retry (§5.4).** Tier selects the *model*; `retry.py` token-escalation (1.5×/attempt, cap 8192) escalates *within* a tier. **Forward hook (v2, not v1):** on the Nth validator kickback, *tier*-escalate (`cheap`→`mid`) instead of only token-escalating — the `cheap` agents are exactly the validator-gated ones, so an Nth-retry bump is the natural lever. Noted here so the `agent_tiers` shape leaves room for it (a per-node `escalate_to`).

### 9.2 Cost forecasting **[NEW]**
`canon estimate <pack>` returns expected calls/tokens/dollars before a run. **Timing tension to resolve:** the outline calls this "wanted in v1" but lists it under Phase 5 (polish), and it depends on the Phase 2 DAG + per-phase cost models (and now the §9.1 tier map — cost per node = node's tier price × token estimate). Resolve as: v1, but sequenced *after* the orchestrator (late v1), not pre-orchestrator.

### 9.3 Style guide as cost containment
Pinned at generation time; prevents drift-driven re-rolls (§5.3).

---

## 10. Cradle Integration Points

*Status: 🔶 partial — Canon-side contracts only; Cradle UX out of scope.*
Subprocess invocation ✅; structured JSON from all verbs ✅; Bible-embedded state format ✅ (phase_status + per-artifact status/hashes); provenance-hash surface for stale visualization ✅ (what the Cradle read-parity spike consumes). Still open: `log.jsonl` ⏳; the human-gate `awaiting_review` contract ⏳ (§5.5).

**Future Cradle capability (out of Canon scope):** the **schema chat agent** — Pitch Parser → Schema Drafter → Schema Diff (Pipeline 1, Layer 0) — that helps a user author and understand schemas conversationally, aware of the engine/genre template and the data structure. It *consumes* Canon's locked-schema format and the Schema Validator gate; it lives in Cradle, not Canon. Deferred (§11).

---

## 11. Out-of-Scope / Deferred

Schema Suggester / schema chat agent (Pitch Parser → Schema Drafter → Schema Diff) → a **Cradle-side** feature (§10), not a Canon phase; deferred (Game 3-era). Canon v1 consumes hand-written schemas; the Human Schema Review + Schema Validator gates stay. Cradle chat orchestrator → separate project. Unity/Unreal adapters → v2. Auto-migrate schema changes (D-ii) → TODO. Generation history/undo/version-compare → likely Cradle. DSL plugin pattern → v2. LLM level-walker review → TODO inside Pipeline 2 step 4. VLM final QA → optional/stub in v1. Schema-editor UX → JSON by hand for v1. Multi-user/server-side orchestration → not planned. Mid-step cancellation → between-steps only in v1.

---

## 12. Success Criteria

*Status: ✅ MET in substance (2026-07-14), with two open deltas.*
User writes platformer schemas + style-guide stub → `canon run platformer_pack` → generates 1 world, N stages, M enemies/stage, ~~1 boss/stage~~ *(bosses v1.1)*, L levels/stage with full mask stack, decorated bg/fg, one tilesheet/stage → Godot adapter produces consumable assets and the project loads them → on failure the user sees clear state and intervenes per-artifact → on user edit, downstream marked stale next run → ~~cost forecastable before~~ *(`estimate` open)*, logged after. As-built exceeds the criteria in places: run-up-momentum physics with a jump-arc reachability SIM, sectioned levels with code-not-LLM repair (final paid run: 0/9 layout fallbacks), VLM QA, sprite-sheet animation for enemies AND the player.

---

## 13. Risks & Open Questions

*Status: 🔶 updated 2026-07-14 — risks retrospected below; open questions
partially resolved inline.*

**Risks (2026-07-14 retrospect — all four landed manageable):** tileability →
handled by 32px tiles + region-average sampling + the palette-conform tool
(now landing the exact QA metric); sprite consistency → nano-banana img2img
sheets proved frame-consistent; Layout reliability at scale → SOLVED by
sectioned levels + sequential handoff + code-not-LLM repair (0/9 fallbacks on
the final paid run, levels up to 132 wide / 96 tall); cost ceiling → a full
paid world ≈ $2 (forecasting still unbuilt, but the ceiling is known).

**Open questions:** default v1 schema field bodies (iterate); DSL primitive set (iterate, Canon-internal in v1); ~~per-agent model-assignment table (§9)~~ **drafted, §9.1** (tunable: which nodes justify `top`); `.npz` inline-vs-reference size threshold (§6.2, proposed 1024 cells); whether `RigManifest` parts reference the same diffusion call or separate calls (consistency vs flexibility); **decoration is drawn in both Pipeline 1 (Layer 3 Decorator) and Pipeline 2 (steps 7+8)** — the PRD treats these as the *same* pass (P2 steps 7+8 canonical); confirm it's one pass, not two; **routine human gates** (sprite/tileset/boss) — mandatory in v1, or default auto-approve with opt-in review (§5.5)?

---

## 14. Implementation Phasing

*Status: ✅ EXECUTED (per the revised E.6 ordering): 0 ✅ · 1 ✅ · 2 ✅ ·
3 ✅ (bosses excepted — v1.1) · 4 ✅ · 5 🔶 (VLM QA ✅, docs ✅;
`estimate` + `log.jsonl` open).*

**Phase 0 — Adapter refactor (prerequisite, MazeWorld-only changes).** `OutputAdapter` protocol; `JsonOutputAdapter` wrapping existing helpers; refactor existing phases to `ctx.adapter.*`; verify MazeWorld runs identically. ~2 new files, ~8 one-line phase changes.

**Phase 1 — Bible model extensions + schema loader.** `World`/`Stage`/`Level`/`EnemyDefinition`/`BossDefinition`/`Tileset`/`RigManifest`; `artifact_id`/`status`/`provenance_hash`/`parents`; `phase_status`; the global artifact-ID space; **the JSON ↔ SkeletonSpec loader** (§4 — moved earlier from the outline's Phase 3). MazeWorld Bibles are throwaway (regeneratable); no migration.

**Phase 2 — Orchestrator.** Per-step DAG scheduler: grow the `Phase` protocol with `requires`, expand phases into `(step, level)` nodes, topo-sort, bounded parallelism; per-step state-aware resume from Bible; hash-based edit detection; stale-cascade down dependency edges; verbs `status`, `resume`. (`estimate` sequenced after this — §9.2.)

**Phase 3 — Platformer pack.** Schemas (user-supplied JSON); style guide; phases (Style Guide, World, Stage, Enemy Generator, Tileset, Boss, Layout, Stamp, Validators, Tile Assignment, Hazard, Entity Placement, Trigger, Decorator); tools (Sprite Generation, Tileset Generation, Sprite QA); `examples/platformer_pack/compose.py`.

**Phase 4 — Godot adapter.** `GodotOutputAdapter`; Godot project template consuming adapter output; generic enemy scene + behavior script driven by an enemy-definition resource; TileMap consumer for terrain/background; boss-arena scene.

**Phase 5 — Polish.** `canon estimate`; `log.jsonl`; VLM QA gates; user documentation.

---

## Appendices

### Appendix A — Pipeline diagrams (inlined)

The external `platformer_pipelines.md` was never written; the canonical
pipelines are transcribed here from §5.1 / §5.2.

**Pipeline 1 — Macro game generation** (top-down; parallel within a layer):

```
Layer 0  Project Init (once)
           ├─ Schema Validator gate (HG1) — locks hand-written schemas
           └─ Style Guide Agent → StyleGuide
Layer 1  World Agent → World (stage graph, connectivity, StageBriefs)
Layer 2  Stage Agent  (×N stages, parallel) → Stage (theme, LevelBriefs,
         │                                     roster brief, boss brief)
         │   within a stage, in parallel:
         │     Enemy Generator (×M) → EnemyDefinition
         │        → Sprite Gen (diffusion) → Sprite QA (VLM, advisory)
         │     Tileset Agent → TilesetManifest → Tileset Gen (diffusion)
         │     Boss Agent → BossDefinition → Boss Sprite Gen   [NOT BUILT — v1.1]
Layer 3  Level Design Pipeline (×L levels/stage) → Pipeline 2
             starts once the stage's Tileset + EnemyRoster are ready
```

**Pipeline 2 — Level generation with masks** (invoked per level, strictly
ordered; parallelism is cross-level, not intra-level — I5):

```
 1   Layout Agent (DSL)        ─► …/layout
 1b  Stamp Tool                ─► …/collision   (npz int8, TileType enum)
 2   Reachability Validator         [fail → Layout Agent; N retries → human]
 3   Tile Assignment Tool      ─► …/terrain
 4   Hazard Agent              ─► …/hazards     [Hazard Reachability gate]
 5   Entity Agent              ─► …/entities    (placements) [Entity Validator]
 6   Trigger Agent             ─► …/triggers
 7+8 Decorator Agent           ─► …/background + …/foreground
 9   Final QA Gate (VLM)            [optional; end-of-pipeline in as-built]
 10  Write Tool (via adapter)  ─► level.json + npz arrays
```

**As-built deltas (2026-07-12)** — the shipped pack diverges from the diagram
above; recorded so the diagram isn't mistaken for current reality:
- **Multi-stage worlds + ecology**: N biome *stages* share art; enemies are ONE
  world pool with rarity + habitats (native/worldwide anchors), not per-stage
  rosters generated fresh. Code-drawn world map + START/end screens.
- **Art & audio run at the END of the loop**, per stage (tilesheets, sprites,
  backdrops, props, one music theme + closed SFX set) — not inline per §5.1.
- **Bosses are NOT built** (BossDefinition is modeled; no boss phase). v1.1.
- **Reachability is a jump-arc SIMULATION** (reusing the consumers' exact
  physics incl. run-up momentum), not A*.
- **Sprite-sheet animation** (VLM-authored, enemies + player) is an added
  stack beyond the PRD's static RigManifest.
- **Sectioned levels** (typed sub-regions + stitcher) — SHIPPED IN FULL
  (chunks A→P, Appendix F), paid-validated to 0/9 layout fallbacks.

### Appendix B — Materialize investigation (key findings, inlined)

The investigation that set the adapter direction (§8). Its source docs were
never committed; the load-bearing conclusions:
- **Per-phase decentralized writes = the stubs problem.** Output files are
  written by each phase in parallel to the Bible, which holds only
  `EntityLore` stubs — so "materialize a game from a saved Bible" is impossible
  today (the Bible isn't complete). The platformer sidesteps this by making its
  entities **Bible-complete from day 1**; the global fix is the v1.5 refactor.
- **Collision grids are GENERATION, not materialization.** The stamp/grid step
  is deterministic, runs in-pipeline, and stores the grid as a Bible-referenced
  `.npz`; the adapter only writes the engine format. It is not a
  reconstruct-from-Bible step.
- **Rig manifests need a typed Bible model**, not `extra` — hence the typed
  `RigManifest` (§6.1).
- **Recommendation = Option C** (adopted): an `OutputAdapter` protocol on the
  context; phases call `ctx.adapter.write_*`; `JsonOutputAdapter` (default,
  MazeWorld-compatible) + `GodotOutputAdapter`. No `materialize` CLI verb in v1.

### Appendix C — Glossary

- **Bible** — the single source of truth (`bible.json`); every artifact's
  content hash lives here and hand-edits are detected against it.
- **EntityLore** — the per-entity record model MazeWorld uses; today it holds
  stubs (the "stubs problem", App. B). Generalized with `artifact_id` / `status`
  / `provenance_hash` / `parents` for the platformer.
- **GenerationTrail** — the existing per-generation log (prompt / response /
  retry / cost); precursor to provenance, but not a content hash.
- **SkeletonSpec / SkeletonField** — the constraint engine: an ordered set of
  fields, each a user-defined closed set (`choices`), a `range`/`lookup`, or
  left open for the LLM. Rolls mechanical values *before* the LLM writes flavor.
- **Mask** — a per-layer grid or record list for one level (collision, terrain,
  background dense; hazards, entities, triggers, foreground sparse).
- **DSL** — the layout language the Layout Agent emits (`floor`, `platform`,
  `gap`, `pit`, `water_wall`, `checkpoint`, `spawn`, `exit`, …); the Stamp Tool
  deterministically expands it to a numpy grid (agents never touch cells — I3).
- **Adapter** — the `OutputAdapter` that owns all filesystem writes and returns
  content hashes (App. B, §8).
- **Definition vs Placement** — an entity is authored once (a global artifact,
  e.g. `enemy:<id>`); a level holds *placements* that reference it, with
  optional per-instance `overrides` / `variant`.
- **Artifact ID** — the global, namespaced address (`world`, `stage:<id>`,
  `enemy:<id>`, `tileset:<stage>`, `level:<stage>/<lid>/<step>`, …) used by
  `regen`, `pin`, and the `parents` cascade.
- **Provenance hash** — SHA-256 over an artifact's canonical content plus schema
  / prompt / model / seed, stored on the owning Bible entity; a mismatch on
  re-read flags a `user_edited` artifact and cascades staleness.

### Appendix D — Schema templates

Schemas have hardened into shipped files under `examples/platformer_pack/`
(each documented in that pack's README, "Make it YOUR game"):
`schemas/enemy.json`, `schemas/level_layout.json`, plus the editable data files
`game_rules.json`, `tile_types.json`, `variants.json`, `combat.json`,
`graphics.json`, and `sections.json` (the section-archetype vocabulary). These
are the live templates; copy + edit + pass via the matching `--rules` /
`--tiles` / … flag.

---

## Appendix E — Phase 3a additions (added 2026-07-06, post-slice play test)

*Status: ✅ ALL SHIPPED (E.1 water — grown far beyond this spec into free
features/clouds, see App. F.4; E.2 layers; E.3 tile collision semantics;
E.4 placement variants; E.5 variable dims — banded to 132×96; E.6 sequencing
executed; E.7 game-rules-as-data).*

Requirements raised after playing the vertical slice; specced here so they
live in the PRD, not chat history. Detailed scope: `docs/phase3_kickoff.md`.

### E.1 Water **[NEW mechanic]**
- `TileType.WATER = 20` — traversable volume, not a hazard. The hazard
  int-band stays `>= 10, < 20`; volumes start at 20.
- `PlayerMovementSpec` gains water modifiers: `water_speed_factor`
  (slower horizontal movement — the "water hazard" ask), `water_gravity`,
  `swim_impulse`. One spec consumed by BOTH the reachability validator and
  every play surface (harness, Godot) — the established parity rule.
- Reachability model: vertical movement is free inside water; jump
  constraints apply at the surface/exit.
- Enemy closed set gains `swimmer` (water-bound patroller); `flyer`
  optional.
- DSL grows `water(x1, x2, y_surface)`; layout agents place pools/lakes,
  the stamp tool fills cells (I3 unchanged).

### E.2 Layered generation is v1-critical, not deferred polish
Reaffirms §6.1/§6.2 after the slice flattened them: one file per layer per
level (collision / terrain / background dense; hazards / entities /
triggers / foreground sparse), each produced by its own step with its own
`parents` edges and content hash. Motivations, in priority order:
1. **Asset import seam** — when generated art replaces placeholders, art
   layers swap without touching gameplay layers.
2. **Render collapsing** — foreground/background ordering lets the player
   pass in front of/behind features.
3. **Per-step regen** — re-roll one layer of one level; this is the
   workload the Phase 2 orchestrator schedules.

### E.3 Tile collision semantics **[NEW]** — pixel-fidelity groundwork
`TileSlot` gains `collision: "solid" | "one_way" | "none" | "hazard" |
"water"`. Consumers derive physics from the tileset manifest instead of
hardcoding tile-ID behavior. When real tilesets arrive (diffusion tools),
per-tile collision shapes (Godot TileSet physics layers) attach at this
same seam — pixel-accurate collision without changes to game code or
generation phases.

### E.4 Enemy variations via placement overrides
Exercises §6.1's existing `Placement.overrides` (modeled in Phase 1, never
used): per-instance variation (`{"elite": true, "hp_mult": 2}`) resolved by
consumers as stat multipliers + visual marker. The definition remains the
single canonical artifact; variation lives on the placement.

### E.5 Variable level dimensions
Per-level `GridDims` (§4.2) become schema-rolled (bounded ranges,
escalation-aware via the `level_number` context mechanism proven in the
slice). `pixels_per_cell` remains global in v1.

### E.6 Revised sequencing (supersedes §14 order)
Executed so far: Phase 0 → 0.5 (determinism) → 1 → **3-lite (vertical
slice)** → **4-lite (Godot adapter + template)**. Next: **3a (this
appendix + layer model)** → **2 (orchestrator — layers create the per-step
regen workload it schedules)** → 3b (bosses, style guide, real tilesets/
sprites via diffusion, full A* reachability, VLM QA) → art/audio hookups →
5 (polish).

### E.7 Game rules are data (added 2026-07-06, post-3a play test)
Behaviors like water containment, enemy/water interaction, and platform
pass-through are **game rules, not engine rules** — they vary per game (and
eventually per level). Rule VALUES are template data: a per-game `game_rules.json` (copy + edit + `--rules` to make a new game), shipped in
`manifest.json`, read by generation-time validators/prompts AND every play
surface, never hardcoded in consumers. v1 policies: `water_containment`
("contained" = pools need basin walls; "free" = waterfall-style open
water), `enemy_water_policy` ("swimmers_only" | "forbidden" |
"amphibious" — enforced at placement AND at runtime movement),
`platform_drop_through` (Down+jump through one-way platforms). Per-level
and per-instance rule overrides are 3b.
Rule KINDS are hardened code — a rule is only real if something enforces
it, so each typed key ships with its enforcement point; unknown keys in a
game file are carried into the manifest **inert** (open carriage) until
their enforcement lands.

## Appendix F — Sectioned Levels + Validation Hardening (added 2026-07-14)

Supersedes the whole-level LAYOUT step of §5.2. Everything here is SHIPPED
(pack-only, zero `src/canon` beyond two additive `Level` fields, zero consumer
change) across chunks E/F/G1–G7; §F.6 records what is NOT yet good. The design
stance is the PRD's core principle taken to its conclusion for level layout:
**the LLM makes bounded design choices inside one section; deterministic CODE
plans the level, stitches it, and repairs every computable geometry mistake —
it never bounces a computable fix back to the model.**

### F.1 The model: FEATURES → SECTIONS → LEVELS

- **Features** = the layout DSL ops (atoms). **Sections** = a typed sub-region
  (an *archetype*) with an axis, intensity, water level, encounter style, and a
  `feature_bias` (op-weight hint), that the Layout Agent fills with a MIX of
  features. **Levels** = a SEQUENCE of sections (for length + variety).
- Archetype VALUES are data — a per-game `sections.json` (open carrier), edited
  like `tiles.json`/`game_rules.json`. Shipped v1 archetypes: `runway` (H
  connector/opener), `gauntlet` (H), `cave` (H, hides secret alcoves), `islands`
  (H, floating clouds), `climb` (**vertical**). Each carries `min_len`/`max_len`,
  `intensity`, `water` (dry/optional/submerged), `encounter`, `feature_bias`,
  `flavor` (prompt text). The stitch + plan KINDS are code.

### F.2 The BLUEPRINT (rolled once, up front — G1)

`plan_level(width,height,difficulty,rng,axis) → LevelPlan` is a deterministic
roll (same seed → same plan; the placement phase recomputes it without a
persisted field). A `LevelPlan` carries: the `axis`, the ordered `sections`
(**capped at 5** — WIDTH drives the count so sections stay ~26 cells, the level
grows by adding sections not fattening them), the `checkpoint_sections` (derived
by rule: 0 for 1–2 sections, 1 for 3–4, 2 for 5 — interior only), and an
`exits` LIST (length 1 today; the list shape makes multiple exits at different
heights a later DATA change, not a rewrite). A level rolls **horizontal** or a
real **vertical climb** (`VERTICAL_FRACTION`, stage-2+ only; vertical dims are
recast tall+narrow, a shaft up to ~26 wide × ~96 tall).

### F.3 The stitcher (both axes)

Each section is stamped **in isolation** at its own local dims (so
`stamp`'s `ground_row = H-2` holds locally), then its sub-grid is composited
into the full grid at its `(x_off, y_off)` origin with **non-empty-wins**
semantics (a later section's empty overlap never erases an earlier section's
terrain — the ~6-cell seam stays continuous). Horizontal tiles the width
(exit at the right edge); vertical stacks the height (spawn at the bottom, exit
= the climb SUMMIT). Section N+1's prompt carries a **sequential handoff**
(never a raw grid): section N's full DSL mechanically REBASED into N+1's own
coordinates, a by-category description of everything occupying the shared seam
band, an explicit "already built — continue it, do not rebuild or collide"
contract, and one-line digests of all earlier sections; a REGENERATED interior
section additionally sees its successor's shared band. Section-0 declares the
spawn; NO section declares the exit — "goal mode" lives in the stitcher
(`place_exit`: rightmost body-standable cell for horizontal, elevated
right-edge terrain allowed; the summit for vertical), and **checkpoints are
STITCHER-placed** at reachable standable cells inside the assigned sections
(both axes; removed from the section DSL/prompt — guarantees reachable,
count-capped respawns incl. mid-climb).

### F.4 The layout DSL (current vocabulary)

Terrain: `floor`/`gap`/`pit`, `platform`/`ledge`, `wall`/`carve`,
`stairs_up`/`stairs_down`/`pyramid` (stepped slopes v1), `breakable(x1,x2)` (a
crumbling floor — solid to stand on, a both-surface fuse drops it a moment after
you step on it). Volumes: `pool`/`volume` (contained basins), and the
containment-EXEMPT free-water features `water_wall` (swim-up column/waterfall),
`water_block` (floating pocket), `water_cloud` (a rounded swim-up cloud) — each
with a registry-generic `volume_*(name,…)` spelling advertised to games without
a `water` tile. Hazards: `hazard_strip(name,x1,x2)`. Markers: `spawn`/`exit`/
`checkpoint`, and `reward(x,y)` (a hidden collectible marker → a new "reward"
trigger type, layout-only placeholder for a future item system; renders as a
gold gem). The prompt advertises exactly the game's registry vocabulary and
anchors worked-example coordinates to the section width.

### F.5 Validation & repair — the code-not-LLM doctrine

Failures split two ways, and the split shifted HARD toward code after two paid
runs proved the LLM cannot reliably avoid geometry mistakes:

- **Reachability** (`check_level`/`reachable_cells`) is a real jump-arc
  SIMULATION reusing the consumers' exact run-up-momentum physics (the BFS
  successor relation; free water swims for free; handles both axes). It is
  TARGET-RELATIVE (G2): the break is located relative to the unreachable target
  and emitted as a machine `[break@x,y]` gap tag, so a whole-level failure
  routes to the section OWNING the gap, not the far-off target coord.
- **Computable geometry → repaired in CODE, never raised** (the biggest lever):
  - **Stamp auto-repairs (G7 + H)** — the Stamp tool rewrites the model's
    repairable geometry and logs it to `result.repairs` instead of failing the
    section: OUT-OF-BOUNDS coordinates clamp into the grid (fully-outside ops
    drop); a hazard on any SOLID sits on it, over a GAP becomes a pit, over
    water is dropped; free-water ops CLIP to the open-air cells; `water_wall`
    clips its top down; a contained pour with no basin keeps its water as a
    FREE spout, an occupied-surface column drops, a pool CLIPS to its floored
    columns; `stairs` lay floor under the ramp; a `reward` on solid is dropped.
    LLM-authored content is parsed LENIENTLY (prose / truncated lines skip,
    recorded; an all-junk reply still fails); code-authored DSL stays strict.
  - **Reachability repair ESCALATION** (`auto_bridge_grid`): a one-way
    `platform` bridge; where none fits, a **mount-open** (a sealing solid
    support converts to one-way so the arc mounts through it), a **doorway**
    (a lateral walled span ≤8 cols gets a body-height door), or a **climb
    lane** (a 2-3 col vertical lane carved + rung-laddered, hazard records
    kept consistent) — all sim-verified, all capped (surgery is bounded; a
    ≥10-col solid block stays a design failure). Repair targets are
    HEADROOM-QUALIFIED (a standable cell under a solid ceiling is a 1-tall
    pocket no body fits — excluded from targets, exits, and bridge stands).
  - **Exit-relocate** — an exit blind-placed into sealed designed geometry
    moves to the farthest REACHED foothold in the exit's own section.
  - **Containment (G4)** — a pool that spills once composited is re-interpreted
    as free-standing water (its cells join the exempt set) rather than looped
    back to the model.
  - Markers snap to valid columns; a spawn nudges off a gap.
- **Genuine DESIGN failure → the owning section regenerates** with located
  feedback (its own rejected attempt + the reason, "change as little as
  possible"), bounded. Terminal fallback is LOCAL (G3) — only the failing
  section falls back to a guaranteed-valid flat/ladder stretch, then its SEAM
  NEIGHBOURS if the composite still fails (a trap can straddle the seam); the
  sections that passed SURVIVE. A whole-level fallback is the last resort and
  is axis-aware (a vertical level falls back to a climbable ladder). Every
  stitch round's residue is recorded in the level's attempts trace
  (`stitch_rounds`) — fallbacks can never delete their own evidence.

### F.6 Data model + status

- `Level` gains `layout_axis` and `view_rows` (additive src/canon). New pack
  types: `LevelPlan`/`ExitSpec` + `plan_level` in `sections.py`; `sections.json`.
  Horizontal dims are difficulty-banded ~48→132 wide; a vertical climb is
  ~16–26 × up to 96.
- **Shipped + verified** (suite 1825, byte-identical fake runs, orch==seq, godot
  `--check-only`, ruff; each fix has a $0 regression test replaying real paid
  traces, checked in as fixtures): E (clouds + alcoves), F (widened dims +
  README), G1–G7, the H-series (whole-level repair escalation + trace
  observability), the P-series (sequential handoff + seam-trap repairs).
- **Validated by a three-run paid A/B on the same seed (2026-07-14):**
  whole-level fallbacks 5/9 (all three climbs bare ladders) → 1/9 → **0/9,
  with zero section retries and every exit sim-reachable** on the final run.
  Remaining paid-run warnings are art cosmetics only.
- **Known reachability limitation:** the sim is HAZARD-BLIND (collides only
  against solids/one-ways), so "reachable" ≠ "damage-free" — a ground-spike path
  is traversable-with-damage under the hearts system, not death; a candidate v2
  hazard-aware pass.
