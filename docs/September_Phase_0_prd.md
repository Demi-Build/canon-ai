> **⚠️ Superseded where it disagrees by `September_master_prd.md` (§6 is the collision table, §8 the decisions); this doc remains the spec-prose holder for un-flipped rows.** — signed off 2026-09-01

# PRD — September Phase 0

**Scope of this document:** the single tracking PRD for all four workstreams
from the four-front audit (2026-08-26,
https://claude.ai/code/artifact/553f4dd7-f2ca-4cad-867a-7a771f5d0393).
Items 2–4 are drafted into this same file as their review passes complete —
no separate PRDs.

| Workstream | Status |
|---|---|
| **W1 — Pack registry & universal editing** (dungeon-crawler parity, dialogue, project evolution) | drafted + review pass 1 applied — the body of this doc |
| **W2 — Create from template** (dungeon/platformer from scratch in cradle) | drafted 2026-08-27 — builds in parallel, releases with W1 editing (day-1-editing rule) |
| **W3 — Packaging + API-key screen** (bundled canon, "just works" sharing) | drafted 2026-08-27 — keychain-first keys, vendored runtime, small Settings screen |
| **W4 — Web app & teams** | **moved to `October_Phase_0_prd.md`** (the Cursor transformation); its 8 invariants + backend-ids-as-data rule bind September work — see the stub at end |

## How this document is maintained

- **Everything here is a deliverable.** There are no swing items and no
  silent de-scoping; if scope must change, that is a user decision, made
  explicitly. The phase ships when the deliverables ship.
- **The document shrinks as things get built.** When a build-order item
  lands and passes **human testing + review**, its row below flips to
  ✅ Built, and the corresponding spec text in the body is REPLACED by a
  2–5 line "Built" summary (what shipped, where the code lives, anything
  that diverged from spec). The detailed prose is deleted, not archived —
  once built, the code and tests are the spec. (`docs/` is gitignored, so
  removed text is gone; intended.)
- The October invariants (I1–I8, stub at end) apply in review to every item.

## Build order

Single sequence; ∥ marks items that may run in parallel with their
predecessor once it's underway. **Gate** = what human review approves before
the row flips to ✅ Built.

| # | Deliverable | Spec | Status | Gate |
|---|---|---|---|---|
| 1 | **P0 paper**: nine-schema field inventory (from `specs.py` + engine); condition-namespace inventory (from the MazeWorld engine's dialogue evaluation); `EntityKind`/`GridKind`/`DialogueSpec` shapes; `.canon/registry.json` format **incl. the engines block (§5.1b)**; music/sfx row schemas; room grid↔level-editor mapping; `world update` field list — all written INTO this PRD. (The `dialogue update` wire format is already DONE — the design package's `EditOp` union, adopted at approval.) | W1 §4–§8 | drafted 2026-09-01 — paper in the `# P0 paper` section (P.0–P.9) below; awaiting user approval | user reads + approves the additions |
| 2 | **Dialogue editor + tester design** | W1 §7 | ✅ **Built** (2026-08-27) — package at `cradle/design_handoff_dialogue/` (README = interaction spec, PLAN = implementation map, 9 mock screens); **approved at full scope**: selector model over 8+ axes, scenes as a new event type, quest-scoped dialogue surface, 6 canon verbs. The PLAN's 13-step build order is row #9's script; its `EditOp` union is the `dialogue update` wire format. | approved |
| 3 | **W1 P1**: `pack_type` stamp, `resolve_pack`, read-both shim, `canon pack info`; cradle `world_kind` replaces the 5 duck-typed heuristics | W1 §5, §9 | built 2026-09-01 — awaiting human test + approval (`docs/test_plans/P0-3_pack_info.md`; code `canon.packs`, `canon pack info`, cradle `world_kind`) | suites green; platformer byte-identical; hand-test both demo worlds load |
| 4 ∥ | **W3.2 packaging surgery**: packs → `src/canon/`, one resolver replaces `parents[2]`, `sys.executable` fix, retire `run_canon_module` | W3.2 | built 2026-09-01 — awaiting human test + approval (`docs/test_plans/P0-4_packaging.md`; packs at `src/canon/packs/`, `run_canon_module` retired) | wheel acceptance: fresh venv, no checkout, all verbs run — **met** (canon-ai 0.1.0 wheel, fresh venv, every verb $0) |
| 5 | **W1 P2 read**: `EntityKind` loaders, one export serving both shapes; cradle renders a maze room read-only via the level canvas path | W1 §8.2 | built 2026-09-01 — awaiting human test + approval (`docs/test_plans/P0-5_dungeon_read.md`; `canon grid export`, `canon.adapters.dungeon_read`, `canon.packs.dungeon.loaders`, cradle `LevelDetail readOnly`) | maze renders in Blocks view; suites green |
| 6 | **W1 P3 write**: core extraction of `db update/new/complete` + grid verbs, registry dispatch, dynamic schema-derived models, `db define` / `db evolve`, adopt-on-write journaling | W1 §5–§6 | built 2026-09-01 — awaiting human test + approval (`docs/test_plans/P0-6_write_core.md`; `canon.write_core`, `canon.db_ops`, `canon.registry_ops`, `canon.world_ops`, `db define`/`db evolve`/`registry set`/`world update`) | platformer byte-identical; success criterion 6 (add field + define type, zero code) demoed |
| 7 ∥ | **Estimator core extraction** + dungeon count formulas + calibrated `cost_model.json` | W2.1.2 | built 2026-09-01 — awaiting human test + approval (`docs/test_plans/P0-7_pricing.md`; `canon.pricing` = the one price source, `canon.estimator`, dungeon count formulas) | platformer estimates output-identical; dungeon estimate sanity vs $30/3-map anchor |
| 8 | **W1 P4 cradle surfaces**: RowEditor across all 9 types, room editor (level-canvas reskin + placements), per-step roll buttons, History/restore on dungeon worlds | W1 §3, §9 | built 2026-09-01 — awaiting human test + approval (`docs/test_plans/P0-8_cradle_surfaces.md`; RowEditor over all 9 kinds from `db schema` data, writable room editor incl. monsters-via-encounters, `canon grid roll`/`restore`, History on dungeon artifacts) | hand-test: edit every type end-to-end on a dungeon world |
| 9 | **W1 P5 dialogue** at the approved full scope: selector model + `dialogue_trees` storage, scenes, 6 verbs (canon); NPC + quest + scene surfaces with editor and docked tester (cradle) — built to the package's 13-step order | W1 §7 + `cradle/design_handoff_dialogue/PLAN.md` | built 2026-09-01 — awaiting human test + approval (`docs/test_plans/P0-9_dialogue.md`; selector model + `dialogue_trees` storage + scenes + the six verbs + ONE evaluator; the editor and docked tester to the package 13-step order) | author a gated branch (item + time), prove in tester; selector picks the right tree; engine honors known gates |
| 10 | **W2 create flow**: `world new --template` dispatch, StepLog + dungeon phase labels, `pack templates`-driven wizard, key precheck, seed/model Advanced, uniquify, recents-tile fix, project store | W2 | built 2026-09-01 — awaiting human test + approval (`docs/test_plans/P0-10_create_flow.md`; registry dispatch, `--orchestrate` default, wizard from `pack templates`, project store, engines seed, dungeon StepLog) | fresh dungeon created from wizard, opens editable (day-1-editing rule) |
| 11 ∥ | **W3 vendored runtime**: fetch script, bundle wiring, 3-platform CI, resolution order, startup probe, asset-scope tightening | W3.3, W3.6 | built 2026-09-01 — macOS proven locally; 3-platform CI written and awaiting the user's push (`docs/test_plans/P0-11_runtime.md`) | fresh machine (no Python): install → create free world |
| 12 | **W3 keys + Settings**: keychain storage + env injection, `provider_keys` sources, Settings screen (Keys + Environment), PixelLab fix, refusal deep links | W3.4–W3.5 | built 2026-09-01 — awaiting human test + approval (`docs/test_plans/P0-12_keys_settings.md`; keychain via the keyring crate, provider rows as data from `canon providers`, the two panes + A6 permissions, BLENDER_BIN as the one detector, deep links closing the P0-10 inversion) | fresh machine: add key in Settings → paid generation succeeds |
| 13 | **Phase release**: bundled build with the Dungeon crawler card live; run every success criterion end-to-end | W1 §11, W2.3 gate, W3.7 | pending | user sign-off on the release build |

Dependency notes: #3 unblocks #10's early bits; #4 gates #11; #6 gates #8;
#2 gates only #9's cradle half; #13 requires everything above it.

---

# W1 — Pack Registry & Universal Editing in Canon

**Status:** first pass, 2026-08-27. Drafted from the four-front audit
(2026-08-26), `cradle/docs/HANDOFF_NEXT.md`, the scoping notes in
`~/.claude/plans/dungeon-crawler-parity.md` (its four open questions are
answered; answers below supersede its older migration language), and four
decisions taken 2026-08-27 (§2.3). Companion to `docs/platformer_prd.md`,
which this PRD generalizes — it does not replace it.

**One-line thesis:** editing verbs live **once in canon core**; a game
template is a **declaration** (`PackSpec`) the core dispatches on. The dungeon
crawler is the second registrant and the proving milestone — template #3
should cost declarations, not another 1,719-line write layer.

**Second thesis (added after review):** **templates seed; projects own.** A
template stamps a starting registry into the pack at create time; from then on
the pack's own data is the source of truth. Projects can add columns, define
net-new entity types (`PlayerAbility`), and enable capabilities the template
didn't ship — because the intended lifecycle is divergence (a platformer
becoming a metroidvania), not conformance.

---

## 0. Document purpose & scope

Defines the pack registry, the universal read/write verb surface, the dialogue
editing + testing system, and the cradle-owned project store. Scope is **the
databases the engine reads** — create, edit, generate, journal.

**Hard scope line (user, explicit):** create-project and DB editing do **not**
touch gameplay. Game tuning (physics, momentum, friction) is a separate later
surface. Do not drift into physics.

## 1. Background & motivation

Audit findings that force this shape:

- Every editing verb built this year routes through `_pack_ops()` at
  `src/canon/cli/main.py:1735`, which hardcodes
  `from examples.platformer_pack import ops`. There is no game-type dispatch
  anywhere in canon.
- The write discipline is already ~95% generic. `update_db_row`
  (`examples/platformer_pack/ops.py:744`) is driven entirely by declarative
  tables (`DB_TYPES`, `_UPDATE_NESTING`, `_DICT_CONTAINERS`,
  `_PROTECTED_FIELDS`); the single hardcoded line is the model lookup.
  `DB_TYPES`'s own doc comment says "MazeWorld types join this table when
  their specs land as JSON."
- Canon can **generate** a mazeworld world (12-phase pipeline in
  `examples/mazeworld_pack/`) but cannot **read one back** — `parsers.py` is
  generation-direction only. `src/canon/adapters/` has a platformer read/write
  pair and no sibling.
- Mazeworld worlds ship no `.canon/` journal, so provenance, history, restore,
  jobs, spend, and library — all already pack-agnostic — have nothing to
  attach to.
- Cradle's side is thin by design: its Rust performs zero direct pack writes
  (every mutation is a canon verb), its read path already handles both pack
  shapes, and `RowEditor` renders from schemas. Cradle work is wiring, not
  architecture.

## 2. Architectural principles

### 2.1 Standing doctrine (unchanged)

- **Extend existing machinery, don't build parallel systems.** This epic is
  that principle applied to the whole verb surface.
- **Cradle never writes pack files** — everything goes through a canon verb.
- **One writer, one discipline:** resolve → protected-field wall → validate
  fail-closed → roll-table warnings (hand edits are authoritative, surfaced
  not blocked) → stamp `user_edited` → journal `op:"edit"` with per-field
  diff → CAS snapshot.
- **Paid legs are user-run.** Loud fallback beats hidden.
- **Platformer stays byte-identical throughout** — fake trees golden, suites
  green (canon 2374, cradle 176) at every phase.
- **Templates seed; projects own.** The pack's effective registry (types,
  schemas, capabilities) is pack-local data seeded at create time. Verbs read
  the pack's registry, never the template's. Precedent already in code:
  `_schema_path` prefers a pack-local `schemas/<kind>.json` ("user-edited
  distributions travel with the game") and `db schema --set` writes
  pack-local overrides, journaled, without ever touching the template.
- **Data may outrun the engine.** Authoring is never blocked by what the
  runtime can currently evaluate or render — new columns, new tables, new
  dialogue gates are legal the moment they validate; the editor warns loudly
  where the engine hasn't caught up, and engine support lands in its own
  arcs. This is what makes template divergence (and agent-driven redesign)
  possible at the data layer.

### 2.2 Locked decisions (2026-08-03, carried forward)

1. **Registry, not parallel verbs.** One `apply-edit`, one `db update`, one
   journal, dispatching on pack type. NOT a `dungeon_*` pair. Accepts an
   upfront refactor of working platformer code.
2. **Full spatial.** A maze grid is a tilemap and a room is a level; the
   level canvas machinery is reused, not duplicated.
3. **Per-step generation = the existing verb pattern** (whole level, or
   terrain → enemies → items separately). No new pipeline/pause capability.
4. **No migration.** Existing MazeWorld worlds stay as they are; only new
   creations are dungeon crawlers. A read-both compat shim, not a migrate
   verb. (Supersedes the older "artifact ids change / needs migration story"
   note in the scoping doc.)
5. **All 9 entity types get full create/edit/write** — npcs, items, monsters,
   quests, rooms, events, classes, music, sfx. No read-only tier.

### 2.3 Decisions taken 2026-08-27 (this PRD's four calls)

1. **Dialogue: full graph editing in v1**, plus a **tester**, plus gating on
   **items, quests, and game mechanics (time, player states)** via a typed
   condition grammar. See §7.
2. **Project store: decided now and wired into the create flow** from its
   first release (the dungeon-template wiring epic ships against it, not
   before it). See §8.4.
3. **Music/sfx: full rows + asset backing.** Real row schemas (title, mood,
   loop/trigger metadata) editable via `db update`, with the audio file as an
   asset attachment mutated via asset/music verbs. Two more schemas to author.
4. **On-disk schema names stay in v1** (`maze_ref`, `maze.json`,
   `maze_width/height`, `maze_*` music ids). `pack_type` says `dungeon`; the
   emitted tree stays engine-compatible. Renaming on disk is a coordinated
   later change with the engine repo.
5. **Per-project schema evolution is a requirement, not a variant**
   (2026-08-27 review): a project must be able to add fields to existing
   types, define net-new entity types, rename its own fields, and enable
   capabilities its template didn't ship — all through verbs, all journaled.
   Registry-as-pack-data (§5.1) and `db define`/`db evolve` (§6) exist to
   guarantee this.

## 3. User-facing surfaces (v1)

| Surface | What lands |
|---|---|
| Entity views (all 9 types) | Row editing via the existing spec-driven `RowEditor`; `+ new row` with 🎲 roll-from-skeleton and LLM complete; per-entity regenerate/reroll |
| Room editor | The level canvas rendering a 40×30 maze grid; paint walls/floor; drag placements (npcs, monsters, items, events); per-step roll buttons (whole room / layout only / 🎲 monsters / 🎲 items / 🎲 events) mirroring 🪄 Layout / 🎲 Enemies / 🎲 Items |
| Dialogue editor | Three scopes per the approved design: **NPC** (graph editor: inline prompt edits, choice rows, selector router node, navigator rail + ⌘P), **quest** (cross-NPC lanes with coverage grid), **scene** (script editor with actors/settings). Conditions/effects authored via the shared entity picker from the pack's vocabulary |
| Dialogue tester | A **docked tester** (collapsed strip / full-height expand) driving any tree against simulated state (inventory, quests, clock, player, flags, scenes seen, actor presence); per-choice gate results with the failing condition named; selector resolution shown live; checkpoints |
| Create flow | New project → Dungeon crawler generates for real (template dispatch); projects land in the cradle project store by default |
| Provenance | History / restore / lineage light up for dungeon worlds the moment `.canon/` exists (adopt-on-write) |

Not a surface in v1: world map for dungeons (rooms connect by doors/manifest
order; revisit if a floor-graph view earns its place).

## 4. Schema system

Full create on all nine means **nine schemas, not nine forms** — `RowEditor`
renders whatever `canon db schema` returns.

| Type | Schema today | Work |
|---|---|---|
| enemy, item (platformer) | `examples/platformer_pack/schemas/*.json` | none — the template registrants |
| npc, monster, item, quest, event, class (dungeon) | roll logic exists in `examples/mazeworld_pack/specs.py` (SkeletonSpecs in Python) | export/author as JSON skeleton schemas; P0 inventories which fields are rollable vs LLM vs code |
| room | `MazeLayoutPhase` + placement arrays | schema covers dims/env/theme knobs; grid edits go through grid verbs, not `db update` |
| music, sfx | none | **new** (decision 2.3.3): metadata rows (title, mood, loop points, trigger/env binding) + asset attachment |

Schema authoring is real work, priced in P0 — not a footnote. One addition
from the approved dialogue design: the **event schema gains type `scene`**
(actors, settings, trigger, once, on_finish, script lines) alongside
puzzle/choice — inventoried with the rest in build-order #1.

## 5. The pack registry

### 5.1 `PackSpec` — what a template declares

```python
PackSpec(
    pack_type="dungeon",                 # stamped into manifest.json on create
    label="Dungeon crawler",
    vocab=("floors", "rooms", "encounters"),
    entities={
      "npc": EntityKind(
          model=NpcDefinition,           # Pydantic, fail-closed validation
          layout=Collection("npcs/npcs.json"),   # vs PerFile("enemy/")
          schema="schemas/npc.json",
          id_field="npc_id",
          llm_fields=[...], code_fields=[...],
          nesting={...}, containers=(...), protected=frozenset({...}),
          loader=load_npc_row,           # tree → model (the read-back inverse)
      ),
      # monster, item, quest, room, event, class, music, sfx …
    },
    grids={"room": GridKind(ref_field="maze_ref", file="maze.json", ...)},
    dialogue=DialogueSpec(condition_namespaces={...}, variant_specs={...}),  # §7
    compose=compose_pipeline,            # generation DAG
    estimator=..., prompts=..., validators=...,
    capabilities={"grid", "dialogue", "per_step_roll", ...},
)
```

- **Registry location:** `src/canon/packs/` (core dispatch + the two built-in
  registrants). Moving pack code out of `examples/` is the same surgery the
  packaging epic needs; do it here once.
- **Resolution:** `resolve_pack(pack_dir)` reads `pack_type` from
  `manifest.json`; legacy packs (no stamp) fall back to shape detection — the
  read-both compat shim.
- **Extensibility:** third-party templates register via a `canon.packs`
  entry-point group. Because cradle's forms are schema-driven, a new template
  gets editor UIs without cradle changes.
- **The two layout modes are the one genuinely new core mechanism:** per-file
  rows (platformer) vs collection files (`npcs/npcs.json`). Read, write,
  rehash, and journal handle both; CAS snapshots the file, the journal diff
  stays per-field.

### 5.1a Seed vs instance — the registry is pack data

`PackSpec` in code is the **seed**. `world new` stamps the effective registry
into the pack (`.canon/registry.json`, alongside the pack-local `schemas/`
dir), and **every verb resolves against the pack's registry, not the
template's**. Consequences:

- **Add a column to enemies:** `db schema --set` — exists today, pack-local,
  journaled. Formalized here, not invented.
- **Define a net-new type** (`PlayerAbility`): `canon db define` (§6) appends
  an `EntityKind` entry to the pack registry — dir/layout, `id_field`, an
  initial schema — after which every generic verb and every cradle surface
  (`pack info` → LeftNav groups, `RowEditor` forms, `db new/complete/update`)
  serves it **with zero code changes anywhere**.
- **`EntityKind.model` is optional.** Built-in types keep their Pydantic
  models as a strictness upgrade; a type with no code model validates through
  a dynamic model derived from its skeleton schema (types, ranges, choices,
  lookups — the same SkeletonSpec machinery that bounds generation). This is
  the one design change that makes project-defined types possible at all.
- **Capabilities are instance data too**, seeded by the template: a
  platformer-descended project can enable `dialogue` when its redesign needs
  it.
- **No template-upgrade contract in v1.** A diverged project doesn't take
  template updates; divergence is the point — the intended arc runs
  template → project → arbitrary evolution (a platformer becoming a
  contra-like shooter) with no new template involved. The long-term path
  runs the other direction: **promoting a diverged project into a new
  template** (`pack promote` — its registry, schemas, and rules become a
  seed others create from). Real, and much later; explicitly out of scope
  for this phase.
- The registry file is journaled like any artifact — `db define` and
  capability changes appear in History with actor + diff.

### 5.1b The engines block (added 2026-08-31, from the Phase-2 design round)

`PackSpec` / `.canon/registry.json` carry an `engines` list — one entry per
attached engine:

- `id` (`pygame`, `godot`, …)
- `template` ref + **version stamp** — formalizes what `engine status`/
  `engine sync` already half-track; drift is a loud warn, never a block
- **launch contract** (`cmd`/`args`/`env`) — **templated, never literal**:
  placeholders (`{python}`, `{godot}`, `{pack}`) resolved host-side by the
  existing `CANON_BIN`/`GODOT_BIN` resolution order, so a shared pack never
  carries one machine's paths. Retires the hardcoded launch knowledge in
  cradle's `play_level`/`play_game`, which become "launch engine by id"
- `live_channel`: `none | hooks-v0 | live-vN` — the `PLAT_*` hook system is
  v0's honest description; the protocol-version slot waits for the real
  editor↔game channel
- per-engine **artifacts** (e.g. `.grid.json` siblings, `project.godot`)
- `exports`: `computer | web | mobile` — the data the wizard's distribution
  axis (W2.4) reads; Godot all three, pygame none
- `primary` flag — **the default for ▶ Play, nothing more**: both attached
  engines keep their preview surfaces, and the cross-engine parity doctrine
  (`PLAT_TRAJ` byte-identical; verify rendering on both) is unchanged
- `evaluable_namespaces` / `evaluable_bindings` — **added 2026-09-01 (P0 paper
  P.9 C1, user-decided):** additive, capability-gated per-engine evaluability
  blocks (scope → condition namespaces; row kind → binding/trigger kinds), seeded
  from the template's `DialogueSpec` / audio vocab (paper P.2.4, P.5.3); `pack
  info` surfaces the primary engine's as `engine_evaluable_namespaces` /
  `engine_evaluable_bindings` (P.4.6)

Lifecycle: the wizard's engine axis **seeds** one entry at create;
`engine attach` **appends** (later phase — September ships only the seed
path); a full engine **switch is a project fork**, keeping one journal per
engine-interpretation. Schema is specced in build-order #1's registry
format.

### 5.2 What stays per-template

Models, schemas, compose pipelines, prompts, estimators, validators, engine
export, and genuinely game-specific verbs (e.g. `level publish` progression)
behind capability flags. Centralize the **discipline**, not the **game**.

## 6. Verb surface

| Verb | Status | Notes |
|---|---|---|
| `canon pack info <pack>` | **new** | pack_type, entity kinds, capabilities, condition vocabulary. Kills cradle's five duck-typed detection heuristics; `load_world` gains a real `world_kind`. |
| `canon pack templates` | **new** | Installed templates + wizard metadata (labels, defaults, ranges). `NewProjectModal`'s hardcoded array becomes a render of this. |
| `canon db types / schema / new / complete / update` | move to core | `update_db_row` verbatim, tables injected from the **pack registry**. `db schema --set` (pack-local field add/change, fail-closed, journaled) already exists — it becomes the documented column-evolution path. |
| `canon db define <pack> --type <name> …` | **new** | Appends a net-new `EntityKind` to the pack registry (layout, id_field, initial schema). The project-evolution verb: new tables without touching template or core code. |
| `canon db evolve <pack> --type <t> --rename-field old:new` | **new, small** | Mechanical, journaled field rename across the type's rows + schema (code applies it; no LLM). Loud warning that the engine must follow. Type renames deferred to v1.1. |
| `canon db get / list` | new, optional | Matters for the web path; cradle reads disk directly today. |
| `canon grid export / apply-edit / import-grids` | generalize `level *` | `level` stays as alias. Room placements map onto apply-edit's sparse shape. |
| `canon dialogue show / update / test / improve` | **new** | §7. Capability-gated; platformer packs don't declare it. |
| `canon world new --template <t>` | exists → registry dispatch | The audit-2 wiring, through the registry. |
| `canon world update` | new, small | World/bible-level fields with the same protected-wall discipline. |
| `canon generate / regenerate / reroll` (per-entity LLM) | exists, unwired | The `canon/ops.py` trio finally wired through the registry. |
| `asset * / jobs / spend / library / object cat` | already generic | Untouched; attach the moment a world has `.canon/`. |

## 7. Dialogue: editing, gating, testing

**Binding spec: the approved design package** at
`cradle/design_handoff_dialogue/` (README = interaction spec answering the
ten design questions; PLAN = implementation map with the 13-step build order
and the `EditOp` wire format; 9 mock screens). Approved **full scope**
2026-08-27. This section holds only the canon-side contract.

### 7.1 Model (approved)

- **Selector model.** An NPC's dialogue is a list of trees; each carries an
  ordered **selector** (predicates over registered axes: quest, segment,
  time, flag, room, scene, player, custom) or none — the fallback. First
  match wins; rank order is data, edited as data. **New storage:**
  `dialogue_trees` list on the NPC, with the legacy four `dialogue_tree*`
  fields written back while the engine still reads them; existing
  four-variant NPCs map on mechanically (`quest:` selectors). This
  supersedes the old `DialogueVariantSpec` plan — same intent, richer model.
- **Scenes.** A new event type `scene`: actors (required/optional), its own
  gates, a trigger (`enter_room · talk_any_actor · quest_advance`), `once`,
  `on_finish` effects, and a numbered script (lines with per-line
  conditions, choice blocks). Referenced by NPC and quest surfaces, never
  embedded — one store of truth, three readers.
- **Grammar.** Conditions: `has_item · quest · time · player · flag ·
  segment · room · scene(seen/unseen) · event(solved/unsolved)` plus the
  scene-only `actor:present|absent` (rejected in trees, with the reason).
  Effects: `gives_item · takes_item · gives_quest · advance_quest ·
  set_flag`. Namespace legality is scope-aware; vocabulary is pack-registry
  data; no component builds tokens by concatenation.

### 7.2 Verbs (canon contract)

- `dialogue update <npc> --ops <json>` — the PLAN's `EditOp` list in one
  batch; fail-closed validation; journaled per op. Quest-scope saves = one
  update per touched NPC.
- `dialogue validate <npc>` — `{errors[], warnings[]}`; unreachable nodes
  and uncoverable selector rows are warnings, never blocks.
- `dialogue test --tree <payload> --state <json>` — takes the **unsaved
  buffer**, returns per-choice pass/fail/unevaluable (failing condition
  named) + post-effect state. One evaluator; the UI never reimplements
  gating.
- `dialogue select --npc <id> --state <json>` — which tree the state
  selects, and why each other tree didn't.
- `scene update / validate / test` — same shape; test takes actor presence.
- `dialogue improve` — returns a **proposal** (per-field before/after),
  never a write; applied rows land in the unsaved buffer.
- The **engine-evaluable-namespaces** field lives on the registry's `engines[]`
  entry (`evaluable_namespaces`, per scope — C1, decided 2026-09-01; not the
  pack manifest); `pack info` surfaces the primary engine's block. Engine lag
  stays the §2.1 doctrine — warn loudly, never block — now including the
  selector-level case (engine falls through to its next evaluable row while
  the tester picks the true tree; same loud treatment).

## 8. Data layer

### 8.1 Provenance adopt-on-write

First mutation to a legacy world creates `.canon/` (journal + CAS). No
migration, no backfill; history begins at adoption.

### 8.2 Read-back loaders

Each `EntityKind.loader` inverts the emitted tree into its model (the missing
organ the audit found). For collection layouts this is mostly generic JSON
plus the field-rename inverses (`stat_template`→`stats` etc. currently
one-way in `MazeworldManifestPhase`).

### 8.3 On-disk names

Unchanged in v1 (decision 2.3.4). The compat shim recognizes packs by
`pack_type` stamp or legacy shape; nothing on disk renames.

### 8.4 The cradle project store (decided)

- **Created in cradle** → lands under the cradle projects root, default
  `~/CradleProjects/<slug>/` (visible, user-browsable; app-data would hide
  user content). Configurable once the settings screen (packaging epic)
  exists. `$CANON_LIBRARY` (`~/.canon/library`) is the precedent for
  canon-owned state outside any pack.
- **Opened from elsewhere** → written back in place. Today's behavior,
  unchanged.
- Recents badge store vs external; the create wizard's location step becomes
  "Advanced — choose location" with the store as default.
- **Cross-epic wiring:** the dungeon-template create flow (audit 2) ships
  against the store from day one. Forward-looking: store ↔ server workspace
  is the mapping the web path will reuse.

## 9. Cradle integration points

- `pack info` replaces the five duck-typed world-detection sites
  (`data.rs:166`, `store.ts:467`, `LeftNav.tsx:299`, `EntityTable.tsx:75`,
  `RecentTile.tsx:27`) with one `world_kind`.
- `RowEditor`'s two-entry `DB_TYPE` map dissolves into `pack info`'s entity
  list; surfaces gate on declared capabilities instead of type sniffing.
- Room editor reuses `drawLevel`/`LevelCanvas` with a maze cell palette;
  invoke.ts + Rust wrappers follow the existing ~20-line command pattern.
- DialogueGraphMode (React Flow) gains edit mode; DialogueCardMode gains the
  tester's state panel.

## 10. Out of scope / deferred

Gameplay & physics tuning (hard line) · migration of existing worlds ·
on-disk schema rename · engine evaluation of new condition namespaces (own
arc, §7.4) · Godot export for dungeons · web/multi-user (separate roadmap) ·
world-map surface for dungeons.

## 11. Success criteria

1. Platformer byte-identical: golden fake trees unchanged, canon + cradle
   suites green at every phase.
2. In cradle: create a dungeon crawler → edit rows of all nine types → paint
   a room grid and move placements → per-step roll a room → play it in the
   pygame engine reading `data_canon/`, no translation step.
3. Author a dialogue branch gated on an item and a time window, prove it in
   the tester, and see the engine honor the item gate (time gate honored once
   the engine arc lands).
4. Every mutation journaled with actor + per-field diff; History/restore work
   on a dungeon world.
5. A third toy template can register with declarations only (schemas + models
   + compose), no core changes — the extensibility proof.
6. **In an existing project**, add a field to enemies via `db schema --set`
   and define a net-new `player_ability` type via `db define` — its rows then
   browsable and editable in cradle with **zero code changes** in canon or
   cradle. The template-divergence proof.

## 12. Risks & open questions (updated after 2026-08-27 review)

- **Schema authoring volume** (§4) — nine schemas, some derivable from
  `specs.py`, audio ones net-new. **Review addition:** per-project net-new
  data/capabilities is a requirement (decision 2.3.5) — covered by
  registry-as-pack-data + `db define`/`db evolve`; the risk narrows to
  authoring the nine *seed* schemas.
- **Collection-file journaling** — file-level restore granularity for
  collection types. **RESOLVED: accepted for v1.**
- **Dialogue editor** — **full graph editing is the v1 deliverable**
  (confirmed at review; not descope-able to card-mode). Card-mode text edits
  landing earlier inside P5 is build *order* only. Remains the largest
  cradle-side build.
- **Engine lag** (§7.4, rewritten in plain terms) — authoring/testing lead,
  engine evaluation follows per-namespace. Accepted as the intended
  data-first workflow.
- **Byte-identical guarantee during extraction** — mitigated by moving code
  verbatim with injected tables, golden fixtures in CI, and P3's "platformer
  first, dungeon second" order. **Understood/accepted.**
- **NEW — dynamic-model validation parity:** project-defined types validate
  through schema-derived models, which are weaker than hand-written Pydantic
  models (no cross-field invariants). Mitigation: `db define`d types start
  loose and can graduate to code models if they join a template later;
  cross-field rules can land in schemas as a v1.1 extension.
- **NEW — divergence vs template updates:** no upgrade contract (§5.1a);
  a diverged project is its own lineage. Revisit only if template-update
  demand appears.

## 13. Implementation phasing

- **P0 — Registry design + inventories.** This PRD is most of it. Remaining:
  schema inventory per type (§4), condition-namespace inventory against the
  engine, the `EntityKind`/`GridKind`/`DialogueSpec` dataclass shapes, and
  the pack-registry file format (`.canon/registry.json` — seed/instance
  semantics, §5.1a).
- **P1 — `pack info` + read-both shim.** Stamp `pack_type` on create; legacy
  shape detection; cradle `world_kind` replaces the five heuristics.
- **P2 — Generalize read.** Loaders per `EntityKind`; one export serving both
  shapes; cradle renders a maze the way it renders a level.
- **P3 — Generalize write.** Core extraction of `db update/new/complete` +
  grid verbs with registry dispatch; platformer byte-identical; dungeon types
  come online type-by-type as schemas land. Includes the dynamic
  schema-derived model path, `db define`, and `db evolve --rename-field` —
  the project-evolution verbs ship with the same discipline, not after it.
- **P4 — Cradle surfaces.** Room editor, per-step rolls, RowEditor widening,
  provenance surfaces on dungeon worlds.
- **P5 — Dialogue.** Grammar + `dialogue` verbs + variant-spec system + graph
  editing + tester. Card-mode text edits first, structural graph edits second.
- **P6 — Create flow + project store** land with the W2 template wiring
  (which can start as soon as P1's stamp exists — it only needs `world new`
  dispatch, the store, and the StepLog/estimator fixes).
- **Parallel / after — engine arc** for new condition namespaces.

---

# P0 paper — row 1 inventories & formats (drafted 2026-09-01, awaiting user approval)

**Reading rule:** the master (`September_master_prd.md`) §6 collision table and §8 decisions govern
wherever this paper and the older W1 prose disagree; its §1 doctrine and §3.0 spines bind every
format here. Nothing in this section is built at P0-1 — it is the contract rows P0-3/5/6/8/9/10,
P1-A6 and W2.1 build against. Every vocabulary named below (entity kinds, capabilities, engine ids,
template ids, namespaces, genKind, detail kinds, tile names) is **data: an open vocabulary with its
launch values listed, never a literal union** (doctrine 8, §3.0-B). Where a reader marked a fact
UNVERIFIED, or code and a PRD disagree, it is said in place and the decision sits in P.9.

## P.0 Scope & sources

This paper is master row P0-1: the seven inventories Phase 0's own row 1 names (nine-schema field
inventory, condition namespaces, `EntityKind`/`GridKind`/`DialogueSpec`/`PackSpec` shapes, the
`.canon/registry.json` format with the §5.1b engines block, music/sfx row schemas, room-grid ↔
level-editor mapping, `world update` field list) plus the master's two additions: the **reserved
tuning/bands section** (C4 — format only, registry data at W2.1) and the **full journal/ledger event
shape** (§3.0-B, implemented once at P1-A6). Six read-only inventories were taken against code and
emitted data on 2026-09-01; every table row and format field below traces to one of them, which
traces to a `file:line`. Path prefixes: `CA` = `~/Documents/projects/canon-ai`, `MW` =
`~/Documents/projects/MazeWorld` (the external dungeon engine checkout), `CR` =
`~/Documents/projects/cradle`; `DC` = `MW/data_canon` (canon-emitted dungeon tree, 2 rooms), `FX` =
`CA/tests/reference/fixtures/cradle_mazeworld_scifi` (legacy 5-room tree with assets). Unprefixed
`ops.py`/`specs.py`/`parsers.py`/`compose.py`/`placement.py`/`phases.py` mean the
`examples/platformer_pack` (ops) and `examples/mazeworld_pack` (the rest) modules.

| § | Subject | Primary code sources |
|---|---|---|
| P.1 | nine schemas | `CA/examples/mazeworld_pack/{specs,parsers,compose,dialogue,placement,phases}.py`; `CA/src/canon/skeleton/{core,loader}.py`; `CA/examples/platformer_pack/ops.py` (precedent); `MW/src/registry.py`, `MW/src/models/*`; `DC/*`, `FX/*` |
| P.2 | conditions | `MW/src/utils/conversation_utils.py`; `MW/src/systems/{quest_manager,day_night,music_director}.py`; `MW/src/models/{quest,time,encounter,save,npc}.py`; `CA/src/canon/dialogue/models.py`; `CR/design_handoff_dialogue/{README,PLAN}.md` |
| P.3–P.4 | shapes, registry, tuning | `CA/examples/platformer_pack/{ops,rules,movement,godot_export}.py`, `rule_overrides.json`, `game_rules.json`, `combat.json`; `CA/src/canon/pipeline/phases/database.py`, `runner.py`; `CR/src-tauri/src/lib.rs` (launch); `CR/src/components/start/NewProjectModal.tsx` |
| P.5 | music / sfx | `MW/src/systems/{music_controller,sfx_controller}.py`; `MW/src/generate/{music_client,sfx_client}.py`; `CA/src/canon/pipeline/phases/{asset,manifest}.py`; `CA/examples/platformer_pack/audio_phases.py`; `CA/src/canon/library.py`; `CA/src/canon/backends/{music_lyria,sfx_elevenlabs}.py` |
| P.6 | room grid | `CA/src/canon/layout/{__init__,maze}.py`; `CA/src/canon/adapters/{platformer_read,platformer_write,godot_adapter}.py`; `MW/src/models/maze.py`, `MW/config.py`, `MW/src/views/maze_view.py`; `CR/src/components/level/{drawLevel.ts,Dock.tsx,LevelCanvas.tsx,LevelDetail.tsx}` |
| P.7 | world update | `CA/examples/platformer_pack/{compose,phases,art_phases}.py`; `CA/src/canon/bible/platformer.py`; `CA/src/canon/pipeline/phases/{manifest,narrative}.py`; `CA/src/canon/cli/main.py` (`_set_world_name`); `DC/{manifest,world_bible,narrative}.json` |
| P.8 | journal / ledger | `CA/src/canon/{provenance,spend,jobs}.py`; `CA/src/canon/pipeline/stats.py`; `CA/src/canon/backends/*`; `CR/src/lib/{invoke.ts,jobs.ts,cost.ts}`; `CR/src/components/{CostDashboard,JobTray,LineagePanel}.tsx`; `CA/docs/provenance_traceability_spec.md` |

## P.1 Nine-schema field inventory

**Conventions.** Source codes: `ROLL` rolled from a skeleton · `LLM` LLM-authored · `CODE`
code-derived (parser / placement / dialogue / asset phase) · `ENGINE` overridden or ignored at
runtime · `USER` user-only (no generator writes it). Constraints: `choices[a w, b w]` ·
`range[lo,hi]` · `lookup(depends_on=f)` · `ref:<type>.<id_field>` · `text` · `dice`
(`^\d+d\d+$`) · `path`. "Protected" = refused by `db update`; "routed" = owned by another verb
(grid, dialogue, scene, asset) and also refused by `db update`; "derived" = recomputed by code from
another record (edit the source, not the mirror).

**Registry-entry conventions (bind every JSON block in P.1 and P.5; the shape is P.3.1):**

- `protected` is refused with the reason *identity / provenance / asset plumbing*; `routed` is a
  `{field: verb}` map refused with *owned by `<verb>` — use that surface*. `RowEditor` hides the
  first and renders the second as a link to the owning surface (grid / dialogue / scene / asset).
  A field is in one list, never both.
- Beyond `llm_fields` / `code_fields`, an entry carries `user_fields` (never generated; RowEditor
  renders them editable — the "free wins"), `hidden` (the P.9 S5 hide set) and `decorative`
  (editable; RowEditor shows "engine ignores this field"). `canon db schema` output gains
  `user_fields, hidden, decorative, protected, routed` beside today's four keys (P0-6) and
  `RowEditor` reads them instead of its `HIDDEN` literal (`RowEditor.tsx:35-44`; P0-8).
- `layout` is always the P.3.1 object `{"mode", "path", "format"}`; no `perfile` mode exists —
  the room's grid file is the `GridKind` in `grids.room` (P.3.2), not a second layout.
- **List-container addressing.** `update_db_row` supports only flat-name → dict container and one
  `<container>.<key>` level (`ops.py:701-726,795-801`). Dungeon rows add list containers
  (`shop_inventory`, `abilities`, `spells`, `loot_table`, `target_items`, `choices`,
  `monster_ids`), addressed `<container>[<index>].<key>` (0-based; the index must exist;
  `<container>[<index>] = null` deletes the item; `<container>[+]` appends a full item object
  validated against the sub-schema). `nesting` values are container names only; list-typed
  containers are declared in `containers` and the writer learns the type from the row. In
  `llm_fields`, `abilities[].name` is the *pattern* form — every item's `name`.
- P.1.1's JSON is the one canonical full entry; the other entries are abbreviated to the fields
  that differ. `loader` is seed-only (P.3.1) and never appears in stamped JSON.

**Facts every table leans on** (`CA/src/canon/skeleton/core.py:74-167`, `loader.py:8-53,189-194`;
`compose.py:105-112,131-204,294-303`; `database.py:391-417`; `asset.py:166-184`; `ops.py:57-61,
732-735,757-911`):

- A skeleton JSON file holds **rolled fields only** — each field has exactly one of `choices`
  (weighted `[value, weight]` pairs), `range` (inclusive ints), or `lookup` (+ `depends_on` an
  earlier field or `depends_on_context` an outer key such as `room_level`; opt-in `lookup_ranges`
  rolls a two-int lookup value as a band). Field order = roll order; unknown keys error. Specs with
  a `post` hook **cannot be dumped to JSON** — those derivations are `code_fields`, exactly as the
  platformer lists its archetype-dependent rolls. A "row schema" is therefore the skeleton file
  **plus** the registry `EntityKind` entry (llm/code/protected/id_field/layout — P.3).
- `db update` precedent: protected leaves refused (matched on the **last** dotted segment),
  whole-container writes refused, fail-closed model validation, **off-table values warn, never
  block**, `user_edited` stamp, `detail.kind: db_update` per-field diff. Cradle's `RowEditor` hides
  the same protected set and renders `{skeleton_fields, llm_fields, code_fields, schema_source}`
  from `canon db schema`.
- Dungeon ids are ints from `IDAllocator` bases `npc 1000 · item 2000 · event 3000 · quest 4000 ·
  monster 5000 · class 6000` (mirroring the engine's `DB_BASE`); `id` is the engine's key for
  npc/item/event/quest/monster. Layouts: keyed object for item + monster, array for
  npc/event/quest; keyed files key by `str(row["id"])`. Dependency order item → monster → npc →
  event → quest. Default per-room counts `npc 2 · item 3 · monster 2 · event 4 · quest 2 · class 4`;
  room environments cycle `ruins, wasteland, city, temple, fortress, forest, manor, vault`.
- The rolled skeleton is **not persisted** — rolled values survive only where the parser copies
  them onto the row, so a reroll-with-locks reconstructs the skeleton from the row via the rename
  inverses (§8.2).
- `AssetPhase` stamps no path back onto npc/item/monster/event rows (`profile_image` is `null` in
  DC; FX carries machine-absolute legacy paths); it stamps `portrait_path` on classes only. Runtime
  positions are authoritative in `maze.json`, never on rows.

### P.1.1 `npc` — `npcs/npcs.json` (array) · id_field `id` (int, base 1000)

Sources: `parsers.py:81-86,130-249`; `specs.py:279-291`; `dialogue.py:100-204`;
`MW/src/models/npc.py:13-46,180,234`; `MW/main.py:192-203`.

| field | type | source | constraint / on-disk note | `db update` |
|---|---|---|---|---|
| `id` | int | CODE | unique, base 1000 | **protected** |
| `type` | str | ROLL→CODE | rolled as `behavior_type` choices[static 4, wandering 2, merchant 2, aggressive 1]; renamed on disk to `StaticNPC/RandomNPC/MerchantNPC/AggressiveNPC` via `_NPC_TYPE_MAP`; `behavior_type` never emitted (P.9 S1) | editable (choices); changing it should re-derive `shop_inventory`/`npc_monster` |
| `name` | str | LLM | text; `cross_room_dedup=["name"]` | editable |
| `job` · `hobby` · `personality` · `backstory` · `opening_greeting` · `portrait_prompt` | str | LLM | text | editable |
| `environment` · `environment_name` | str | CODE (from the room) | engine overrides `environment` from the maze at `prepare()` | derived |
| `profile_image` | path\|null | CODE (asset) | `null` in DC | **protected** (asset verbs) |
| `dialogue_tree` · `_incomplete` · `_complete` · `_failed` | dict\|null | CODE (dialogue phase; quest-givers get all four) | `{nodes:{id:{prompt, choices[{text,next_node_id}]}}}`; canon's model carries per-choice `conditions`/`effects` that `_to_mazeworld_tree` **drops** on write | routed → `dialogue update` |
| `dialogue_trees` (**new**) | list | USER / LLM-improve | `{tree_id, character_id, label, axis, selector\|null, rank, entry_node_id, nodes}` (`PLAN.md:60-70`); legacy four written back while the engine reads them (P.9 S9) | routed → dialogue verbs |
| `quest_id` | int\|null | CODE | `ref:quest.id` | editable |
| `quest_type` · `quest_target_tile` · `is_story_npc` | str\|null · null · bool | CODE | decorative — not in the engine NPC model | `quest_target_tile` hidden, the rest decorative (P.9 S5) |
| `selected` | bool | CODE const True | **engine model field** (`npc.py:36`); `registry.get_active_npcs` (`registry.py:121-123`) drops rows where it is false, then it is popped before `cls(**kwargs)` (`main.py:202`) — an activation gate, not decoration | **protected + hidden** |
| `description` | str\|null | **USER** — engine-only (`npc.py:23`), never emitted (`parsers.py:181-218`) | text; no engine reader beyond the model found (UNVERIFIED) | editable — second free win |
| `max_dialogue_turns` | int | CODE const 5 | ≥ 1; FX uses legacy `max_exchanges` | editable |
| `x` · `y` | int | CODE | position lives in `maze.json.npc_positions`; engine ignores row values | routed → grid verbs |
| `exhausted_dialogue` · `personality_notes` | str · list[str] | LLM (optional) | text | editable |
| `color` | [r,g,b] | CODE (`ENV_TO_COLOR`) | row value overrides the subclass default | derived |
| `shop_inventory` | list[{item_id, price, stock}] | CODE (MerchantNPC only) | `item_id ref:item.id`; validated | container (knob-wise) |
| `npc_monster` | dict | CODE (AggressiveNPC only) | inline monster block | container |
| `availability` | str\|null | **USER** — engine-only, never emitted | choices[day, night, always] (`day_night.py:91-96`) | editable — a free win |

Engine-runtime fields that are **not row data**: `finished_dialogue` (set on tree swap,
`quest_manager.py:313,317`), `zone`, `move_interval`, `identity`, `has_met_player`,
`interaction_history`, `current_dc`.

The canonical full entry (every stamped `EntityKind` field, P.3.1):

```json
{"npc": {"label": "NPCs", "layout": {"mode": "collection", "path": "npcs/npcs.json", "format": "array"},
  "id_field": "id", "id_alloc": {"base": 1000}, "schema": "schemas/npc.json",
  "renames": {"behavior_type": "type"},
  "llm_fields": ["name","job","hobby","personality","backstory","opening_greeting","portrait_prompt","exhausted_dialogue","personality_notes"],
  "code_fields": ["id","type","environment","environment_name","x","y","color","selected","is_story_npc","max_dialogue_turns","quest_id","quest_type","shop_inventory","npc_monster","profile_image","dialogue_tree","dialogue_tree_incomplete","dialogue_tree_complete","dialogue_tree_failed"],
  "user_fields": ["availability","description"],
  "hidden": ["selected","quest_target_tile"], "decorative": ["quest_type","is_story_npc"],
  "nesting": {"item_id": "shop_inventory"}, "containers": ["shop_inventory","npc_monster"],
  "protected": ["id","profile_image","selected"],
  "routed": {"x": "grid", "y": "grid", "dialogue_tree": "dialogue", "dialogue_tree_incomplete": "dialogue", "dialogue_tree_complete": "dialogue", "dialogue_tree_failed": "dialogue", "dialogue_trees": "dialogue"},
  "refs": {"quest_id": "quest.id", "shop_inventory[].item_id": "item.id"},
  "phase_label": "db:npc", "per_map": true, "count_key": "npc", "dedup": ["name"],
  "asset": {"field": "profile_image", "kinds": ["image"], "targets": ["npc:<id>"]}, "vocab": {}}}
```

Rolled: `behavior_type` only. **Engine reads:** `id, type, name, job, hobby, personality, backstory,
description, environment (overridden), environment_name, opening_greeting, portrait_prompt,
profile_image, dialogue_tree*, quest_id, selected (registry filter), exhausted_dialogue,
personality_notes, max_dialogue_turns, color, shop_inventory, npc_monster, availability`; `x/y`
from `maze.json`. Decorative: `quest_type, quest_target_tile, is_story_npc`.

### P.1.2 `monster` — `monsters/monsters.json` (keyed object `{"5000": {…}}`) · id_field `id` (base 5000)

Sources: `parsers.py:364-428`; `specs.py:113-168`; `MW/src/models/monster.py:179-223,275-285`;
`MW/src/models/encounter.py:522-527`.

| field | type | source | constraint / note | `db update` |
|---|---|---|---|---|
| `id` | int | CODE | base 5000; also the object key | **protected** |
| `name` · `species` · `description` · `backstory` | str | LLM | text | editable |
| `tier` | str | ROLL (index 0 forced `boss`) | choices[minion 4, elite 2, boss 1]; drives `_scale_monster`; **engine ignores** | editable, decorative (P.9 S5) |
| `level` | int | CODE (`room_level`) | engine takes `room_level` from the encounter, not the row | derived |
| `hp_range` · `ac_range` | [lo,hi] | CODE (`_scale_monster`: level band × tier multiplier) | **engine live** — rolls hp/ac inside them | editable (warn off-band) |
| `damage_die` | dice | CODE (`_scale_monster`) | **engine ignores** — rolls from its `LEVEL_SCALING` | editable, decorative |
| `damage_type` | str | LLM | engine expects `physical\|fire\|water\|forest\|light\|dark` (comment only) | editable |
| `physical_type` | str | ROLL | choices[slashing 1, piercing 1, bludgeoning 1]; engine damage triangle | editable |
| `elemental_affinity` · `weakness` | str | LLM | text; `weakness` not read by `instantiate_monster` (UNVERIFIED elsewhere) | editable |
| `time_availability` | str | ROLL | choices[always 6, night_only 2, day_only 1]; engine stores it, never evaluates it (P.2) | editable |
| `abilities` | list[{name, effect_type, damage_dice, chance}] | LLM (coerced) | engine `MonsterAbility` adds `damage_type`, `duration` defaults | container |
| `is_boss` · `environment` | bool · str | CODE | `is_boss` = `tier == "boss"`, read by canon placement for the gate; engine ignores both | derived |
| `portrait_prompt` | str | LLM | text | editable |
| `profile_image` | path\|null | CODE (asset) | engine falls back to `portraits/monsters/mon_<name>.png` by name | **protected** |

The prompt also asks the LLM for `hp_range/ac_range/physical_type/is_boss`; the parser takes them
from skeleton/code — code wins, so they are `code_fields`/rolled, not `llm_fields`.

```json
{"monster": {"label": "Monsters", "layout": {"mode": "collection", "path": "monsters/monsters.json", "format": "keyed_object"}, "id_field": "id", "id_alloc": {"base": 5000}, "schema": "schemas/monster.json",
  "llm_fields": ["name","species","description","backstory","damage_type","elemental_affinity","weakness","abilities","portrait_prompt"],
  "code_fields": ["id","hp_range","ac_range","damage_die","level","is_boss","environment","profile_image"],
  "decorative": ["tier","level","damage_die","weakness","is_boss","environment"],
  "containers": ["abilities"], "protected": ["id","profile_image"], "phase_label": "db:monster", "per_map": true, "count_key": "monster"}}
```

Rolled: `tier, physical_type, time_availability`. **Engine reads:** `id, name, species, description,
backstory, hp_range, ac_range, damage_type, elemental_affinity, physical_type, time_availability,
abilities, profile_image, portrait_prompt`. Decorative: `tier, level, damage_die, weakness
(UNVERIFIED), is_boss, environment`.

### P.1.3 `item` — `items/items.json` (keyed object) · id_field `id` (base 2000)

Sources: `parsers.py:47-53,279-338`; `specs.py:183-267`; `MW/src/utils/dataloader_utils.py:6-43`;
`MW/src/models/items.py:36-160`, `weapon.py:194-255`. Grid cells carry the item id
(`placement.py:188`).

| field | type | source | constraint / note | `db update` |
|---|---|---|---|---|
| `id` | int | CODE | base 2000; also the object key | **protected** |
| `name` · `desc` | str | LLM | text | editable |
| `rarity` | str | ROLL | choices[common 4, uncommon 2, rare 1]; feeds price; **engine loader never passes it** | editable, decorative |
| `category` | str | ROLL→rename | rolled as `item_kind` choices[weapon 3, food 2, drink 2, tool 2, spell_scroll 1]; engine dispatches class on it | editable (changes the sub-shape) |
| `room_level` | int | CODE | engine scales by it | derived |
| `profile_image` · `portrait_prompt` | path\|null · str | CODE (asset) · LLM | `portrait_prompt` not read by the loader | **protected** · editable |
| weapon `weapon_type` | str | ROLL | choices[heavy 18, light 18, sacred 18, arcane 18, enchanted 18, wild 10] | editable |
| weapon `damage_type` | str | ROLL→rename | rolled as `physical_type` choices[slashing 1, piercing 1, bludgeoning 1] | editable |
| weapon `weapon_category` | str | ROLL | choices[simple 60, martial 40]; class access rules | editable |
| weapon `magic_element` | str | CODE const `"none"` | engine vocab in `weapon.py:25`; only the unused `WEAPON_SPEC.post` sets it | editable |
| weapon `item_stats.attack_dice` | dice | CODE (`_scale_item`: `DIE_PROGRESSION[base_tier + level - 1]`) | engine live | nested knob |
| weapon `item_stats.stat_modifier` | str | ROLL (`weapon_stat` lookup(depends_on=`weapon_type`)) → rename | engine live | nested knob |
| `item_stats.price` | int | CODE (`_scale_item`: `10·level + rarity` / `5·level…` / `8·level…`) | engine live; merchant sell price | nested knob |
| food/drink `item_stats.health_value` · `stamina_value` | int | CODE (`base_restore` range[5,15] × level mult; split food→health, drink→stamina) | engine live | nested knob |
| `item_stats.uses` | int | CODE const (1; tools 3) | engine live | nested knob |
| tool `item_stats.attribute` | str\|null | ROLL (`tool_attribute` choices[bludgeon, cutting, digging, climbing]) | engine puzzle solve | nested knob |
| tool `tags` | list[str] | CODE (`[attribute]`) | feeds puzzle wiring via the bible stub; engine ignores | derived |
| spell_scroll `spell_effect` | str | **never emitted** | engine reads with default `"generic"` | editable (USER) |

```json
{"item": {"label": "Items", "layout": {"mode": "collection", "path": "items/items.json", "format": "keyed_object"}, "id_field": "id", "id_alloc": {"base": 2000}, "schema": "schemas/item.json",
  "renames": {"item_kind": "category", "physical_type": "damage_type", "weapon_stat": "item_stats.stat_modifier", "tool_attribute": "item_stats.attribute"},
  "llm_fields": ["name","desc","portrait_prompt"],
  "code_fields": ["id","room_level","magic_element","attack_dice","price","health_value","stamina_value","uses","tags","profile_image"],
  "user_fields": ["spell_effect"], "decorative": ["rarity","portrait_prompt","tags"],
  "nesting": {"attack_dice":"item_stats","stat_modifier":"item_stats","price":"item_stats","health_value":"item_stats","stamina_value":"item_stats","uses":"item_stats","attribute":"item_stats"},
  "containers": ["item_stats"], "protected": ["id","profile_image"], "phase_label": "db:item", "per_map": true, "count_key": "item"}}
```

Rolled: `item_kind→category, rarity, weapon_type, weapon_category, physical_type→damage_type,
weapon_stat→item_stats.stat_modifier, tool_attribute→item_stats.attribute, base_restore` (the spec
rolls weapon fields for non-weapons too — no conditional rolls in v0.1). **Engine reads:**
`category, name, desc, item_stats.*, room_level, profile_image` + weapon `weapon_type, damage_type,
weapon_category, magic_element` + scroll `spell_effect`. Decorative: `rarity, portrait_prompt, tags`.

### P.1.4 `quest` — `quests/quests.json` (array) · id_field `id` (base 4000)

Sources: `parsers.py:92-96,695-815`; `specs.py:332-345`; `MW/src/models/quest.py:8-38,53-176`;
`validators.py:89-130`.

| field | type | source | constraint / note | `db update` |
|---|---|---|---|---|
| `id` | int | CODE | base 4000 | **protected** |
| `type` | str | ROLL→rename | rolled as `quest_type` choices[escort 2, fetch 3, solve 2, combat 2]; engine `QUEST_TYPE_MAP` (`solve`→`CombatQuest`) | editable (changes the type block) |
| `title` · `description` | str | LLM | text | editable |
| `success_dialogue` · `failure_dialogue` | str | LLM | **not in the engine Quest model** (UNVERIFIED raw-dict reader) | editable, decorative |
| `giver_npc_id` | int\|null | CODE (first room NPC) | `ref:npc.id`; dialogue phase picks quest-givers by it | editable |
| `room_id` | str | CODE | `ref:room.id` | derived |
| `is_story_quest` | bool | CODE (`type ∈ escort/combat/solve`) | engine live | editable |
| `prerequisite_quest_id` | int\|null | CODE None | `ref:quest.id`; engine gates the offer on it | editable |
| `portrait_prompt` · `profile_image` | null | CODE None | — | editable · **protected** |
| `reward.xp` | int | LLM-or-CODE | rolled `reward_tier` range[1,3] → `_QUEST_XP_BY_TIER`; **LLM value wins when present**; engine has no XP system | nested knob |
| `reward.item_id` | int\|null | CODE (real room item) | `ref:item.id`; validated | nested knob |
| `reward.money` · `story_info` · `door_reveal` · `failure_penalty.stamina_damage` · `time_gate` | — | **never emitted** | engine fields (`time_gate` choices[day, night] — defined, never read) | editable (USER) |
| `failure_penalty.hp_damage` | int | LLM (default 5) | ≥ 0 | nested knob |
| `is_complete` · `is_failed` | bool | CODE False | not in the engine model (engine tracks `status`) | hide (P.9 S5) |
| escort `escort_npc_id` · `target_zone` · `destination_room` | int · [x,y] · int | CODE | `ref:npc.id`; door/player_start tile; 0 | editable |
| fetch `target_items` | list[{item_id, count}] | CODE | `ref:item.id` | container |
| solve/combat `target_event_id` · combat `target_monster_name` | int · str\|null | CODE | `ref:event.id` · `ref:monster.name` | editable |

```json
{"quest": {"label": "Quests", "layout": {"mode": "collection", "path": "quests/quests.json", "format": "array"}, "id_field": "id", "id_alloc": {"base": 4000}, "schema": "schemas/quest.json",
  "renames": {"quest_type": "type"},
  "llm_fields": ["title","description","success_dialogue","failure_dialogue","reward.xp","failure_penalty.hp_damage"],
  "code_fields": ["id","giver_npc_id","room_id","is_story_quest","reward.item_id","escort_npc_id","target_zone","destination_room","target_items","target_event_id","target_monster_name","is_complete","is_failed"],
  "user_fields": ["reward.money","story_info","door_reveal","failure_penalty.stamina_damage","time_gate"],
  "hidden": ["is_complete","is_failed"], "decorative": ["success_dialogue","failure_dialogue"],
  "nesting": {"xp":"reward","item_id":"reward","money":"reward","hp_damage":"failure_penalty","stamina_damage":"failure_penalty"},
  "containers": ["reward","failure_penalty","target_items"], "protected": ["id","profile_image"],
  "refs": {"giver_npc_id": "npc.id", "prerequisite_quest_id": "quest.id", "reward.item_id": "item.id", "escort_npc_id": "npc.id", "target_items[].item_id": "item.id", "target_event_id": "event.id"},
  "phase_label": "db:quest", "per_map": true, "count_key": "quest"}}
```

Rolled: `quest_type→type, reward_tier` (`reward_tier` not emitted). **Engine reads:** every
`quest.py:21-38` field plus the subclass fields. Decorative: `success_dialogue, failure_dialogue,
is_complete, is_failed`.

### P.1.5 `event` (incl. the new `scene` type) — `events/events.json` (array) · id_field `id` (base 3000)

Sources: `parsers.py:102-108,443-671`; `specs.py:302-321`; `placement.py:318-350`;
`MW/src/models/encounter.py:9-41,115-188,486-527`;
`MW/src/controllers/game_controller.py:213-225,327-331`. The placement phase rewrites this file in
place to add gate flags.

| field | type | source | constraint / note | `db update` |
|---|---|---|---|---|
| `id` | int | CODE | base 3000 | **protected** |
| `type` | str | ROLL→rename | rolled as `event_type` choices[combat 3, puzzle 2, event 2]; **`scene` joins as a value** (§4); engine `EVENT_TYPE_MAP.get(type, CombatEvent)` — an unknown type loads as CombatEvent (P.9 S7) | editable (selects the sub-shape) |
| `name` · `description` | str | LLM | text | editable |
| `difficulty` | int | ROLL→coerce | rolled choices[easy 3, medium 2, hard 1] → int via `_DIFFICULTY_INT`; LLM value ignored | editable |
| `money_drop` | [min,max] | LLM-or-CODE | table by difficulty | editable |
| `loot_table` | list[{item_id, drop_chance}] | LLM, remapped by CODE to real room items | `ref:item.id`; `drop_chance` 0–1 | container |
| `x` · `y` | int | CODE const (1,1) | **stale** — DC shows `1,1` while `maze.json.event_positions` holds the tile; engine falls back to row `x/y` only when that map is empty | routed → grid verbs |
| `room_level` | int | CODE | monster instantiation | derived |
| `time_gate` | str\|null | CODE None | choices[day, night, always]; **engine live** at the trigger layer | editable |
| `portrait_prompt` · `profile_image` | str · null | LLM · CODE | — | editable · **protected** |
| combat `monster_count` | int | ROLL | range[1,4]; not in the engine `CombatEvent` | editable, decorative |
| combat `monster_ids` | list[int] | CODE (sampled; placement prepends the boss) | `ref:monster.id` | container |
| combat `is_gate` · `is_climax_boss` | bool | CODE (placement) | pairs with `maze.json.gate_encounter_id` | derived (placement recomputes) |
| puzzle/event `choices` | list[EventChoice] | CODE-assembled (LLM texts reused) | `{text, stat_check, dc, auto_success, success_text, tool_attribute?}`; always ends with a walk-away; engine folds placeholder choices into `ability_text/spell_text` | container |
| puzzle/event `correct_tool` · `correct_ability` · `correct_spell` | str\|null | CODE (`_resolve_solve_refs`) | tool = attribute vocab; ability/spell = names from generated classes; validated | editable |
| puzzle/event `failure_damage_type` · `failure_damage_range` | str · [min,max] | LLM · CODE (`[3+lvl, 8+2lvl]`) | — | editable · derived |
| puzzle `reward_chance` · `reward_item_id` · `required_tools` · `ability_text` · `spell_text` | — | never emitted (FX legacy carries `reward_chance`) | engine fields | editable (USER) |

**`scene` sub-shape** — USER-authored, no generator, no engine reader in Phase 0 (master §2); from
`PLAN.md:96-108`, `README.md:215-229`: `id` (event id space, P.9 S7) · `type:"scene"` · `title` ·
`actors[{character_id ref:npc.id, required}]` · `settings[Token]` (P.2 grammar + scene-only
`actor:`) · `trigger` (choices[enter_room, talk_any_actor, quest_advance] — data) · `once` ·
`on_finish[Token]` (effects) · `lines[]` = `{k:"line", n, speaker ref:npc.id|null, text,
conditions[]}` or `{k:"choice", n, options[{text, to:<line n>, conditions[]}]}`. All scene writes go
through `scene update / validate / test`, never `db update`. Cradle's existing event surface types
only puzzle-shaped rows (`CR/src/components/event/types.ts:1-27`; its `ability_attribute` /
`spell_attribute` keys do not exist in the emitted tree).

```json
{"event": {"label": "Events", "layout": {"mode": "collection", "path": "events/events.json", "format": "array"}, "id_field": "id", "id_alloc": {"base": 3000}, "schema": "schemas/event.json",
  "renames": {"event_type": "type"},
  "llm_fields": ["name","description","money_drop","loot_table","failure_damage_type","portrait_prompt"],
  "code_fields": ["id","difficulty","x","y","room_level","time_gate","monster_ids","is_gate","is_climax_boss","choices","correct_tool","correct_ability","correct_spell","failure_damage_range","profile_image"],
  "user_fields": ["reward_chance","reward_item_id","required_tools","ability_text","spell_text"],
  "decorative": ["monster_count"],
  "containers": ["loot_table","choices","monster_ids"], "protected": ["id","profile_image"],
  "routed": {"x": "grid", "y": "grid", "title": "scene", "actors": "scene", "settings": "scene", "trigger": "scene", "once": "scene", "on_finish": "scene", "lines": "scene"},
  "refs": {"loot_table[].item_id": "item.id", "monster_ids[]": "monster.id", "reward_item_id": "item.id", "actors[].character_id": "npc.id"},
  "phase_label": "db:event", "per_map": true, "count_key": "event"}}
```

Rolled: `event_type→type, difficulty (label→int), monster_count`. **Engine reads:** `Event`,
`CombatEvent`, `PuzzleEvent` fields; positions from `maze.json`. Decorative: `x, y` (when
`maze.json` has positions), `monster_count`.

### P.1.6 `class` — `classes/classes.json` (positional array, no id key) +
`classes/spell_pools.json` · id_field `archetype` (proposed, P.9 S2)

Sources: `CA/src/canon/pipeline/phases/class_archetype.py:45-66,194-206,315-397`;
`spell_pool.py:154-235`; `specs.py:79-99,355-379,488-683`; `phases.py:66-102`;
`MW/src/models/player.py:14-29,109-154,274-342`. No SkeletonSpec rolls the class row: generation =
`ClassLoadoutSpec` pack data (one LLM flavour call + per-slot `SPELL_SPEC`/`ABILITY_SPEC` rolls with
pinned overrides + batched LLM naming + `fix_stats` to budget 95), then `_augment_classes_json` adds
`stats`, `environment`, `flavor_text`. `ClassPhase._persist` pops `archetype_id`; the engine loads
positionally and ignores unknown keys.

| field | type | source | constraint / note | `db update` |
|---|---|---|---|---|
| `archetype` | str | CODE (loadout) | choices = loadout list (warrior, mage, healer, jester — data); engine keys class rules on it | **protected** (identity) |
| `name` · `description` · `lore` · `flavor_text` · `role_tags` · `category` · `portrait_prompt` | str / list | LLM | engine reads `name, flavor_text, portrait_prompt`; rest decorative | editable |
| `starting_weapon` | str | CODE (spec) | engine looks it up by item name, else `STARTER_WEAPONS` | editable |
| `portrait_path` | path\|null | CODE (asset) | engine live | **protected** |
| `stat_template` · `stats` | {STR..LUCK: int} | CODE (`fix_stats`) | per-stat bands by role: primary [14,18], secondary [12,16] (jester [11,15]), dump [8,12]; sum = 95 (engine `validate_guardrails`); **engine reads `stats`**; `stat_template` is canon's duplicate | container, knob-wise; budget ≠ 95 warns (P.9 S10) |
| `stat_budget` · `stat_roles` | int · {primary, secondary, dump} | CODE (spec) | engine ignores | derived |
| `abilities` · `ability_pool` | list[{name, description, stat, stamina_cost}] | ROLL (`purpose/stat/stamina_cost` + slot pins) + LLM (name, description) | `stat` choices[STR,DEX,CON,INT,WIS,CHA,LUCK]; `stamina_cost` range[2,10]; rolled `purpose` is dropped (not an `Ability` field) | container |
| `spells` · `spell_pool` | list[Spell] | ROLL (`spell_type/element/stat/stamina_cost` + pins) + LLM | `Spell` = `spell_type, element, stat, targets, num_dice, die_sides, heal_amount, buff_stat, buff_value, buff_duration, stamina_cost`; `stamina_cost` range[2,8]; dice rescaled by the engine at level-up | container |
| `starting_equipment` · `extra` | [] · {} | CODE | engine ignores | hide |
| `environment` | str | CODE (first map env) | engine live | derived |

`classes/spell_pools.json`: pools `mage_damage, healer_damage, heal, buff`, keyed by pool name; each
entry = rolled `SPELL_SPEC` + pins + LLM name/description; the engine reads it per archetype
visibility and pops `available_at_room`. Not one of the nine (P.9 S3).

```json
{"class": {"label": "Classes", "layout": {"mode": "collection", "path": "classes/classes.json", "format": "array_positional"}, "id_field": "archetype", "id_alloc": null, "schema": "schemas/class.json",
  "llm_fields": ["name","description","lore","flavor_text","role_tags","category","portrait_prompt","abilities[].name","abilities[].description","spells[].name","spells[].description"],
  "code_fields": ["archetype","starting_weapon","stat_template","stats","stat_budget","stat_roles","environment","starting_equipment","portrait_path"],
  "hidden": ["starting_equipment","extra"], "decorative": ["description","lore","role_tags","category","stat_template","stat_budget","stat_roles"],
  "containers": ["stats","stat_template","abilities","ability_pool","spells","spell_pool"], "protected": ["archetype","portrait_path"],
  "phase_label": "db:class", "per_map": false, "count_key": "class",
  "asset": {"field": "portrait_path", "kinds": ["image"], "targets": ["class:<archetype>"]}}}
```

**Engine reads (`PlayerClass`):** `name, archetype, flavor_text, environment, stats,
starting_weapon, abilities, spells, portrait_path, portrait_prompt, ability_pool, spell_pool`.
Decorative: `description, lore, role_tags, category, stat_template, stat_budget, stat_roles,
starting_equipment, extra`.

### P.1.7 `room` — index `rooms/rooms.json` + per-file grid `rooms/<id>/maze.json` · id_field `id`
(= `map_id`, `room_\d+`)

Sources: `phases.py:18-31,239-270,309-318`; `CA/src/canon/layout/__init__.py:31-67`;
`maze_layout.py:21-22,87-89`; `compose.py:70-92,253-266,321`; `MW/src/registry.py:221-229`;
`MW/src/models/maze.py:451-468`; `MW/main.py:150-261`. `rooms.json` is canon-only (cradle reads it;
the engine never does — it derives `room_{index}` from the directory). Layout = **collection index +
per-file grid** (the `GridKind` of §5.1; P.3).

`rooms.json` row:

| field | type | source | constraint / note | `db update` |
|---|---|---|---|---|
| `id` | str | CODE (`map_id`) | `room_\d+` — engine directory convention | **protected** |
| `environment` | str | CODE (cycled) | choices = pack list (canon's eight; the engine's own `ENVIRONMENT_TYPES` is a different six). `WALL_COLORS` (`maze.py:31-43`) names only `ruins`, `city`, `forest` of canon's eight; the other five fall back to the `dungeon` colour (`maze.py:105`) — cosmetic, never a crash. `JOBS`/`HOBBIES` (`world_data.py:52-67`) cover only `forest` and `city` of the eight, so an NPC row lacking `job`/`hobby` in any other environment raises at `npc.py:66-67` (P.9 S8). Copied at create onto npc/monster/item rows and `maze.json` | **the theme knob** (§4) |
| `environment_name` | str | CODE (`_ENV_PLACE_NAMES`; NarrativePhase rewrites) | text | editable |
| `level` | int | CODE (`i+1`) | ≥ 1 — the `room_level` roll context | editable (warn: re-scales nothing retroactively) |
| `story_beat` · `boss_name` · `boss_lore` | str | CODE ("" today; StoryPhase beats remapped by order) | text | editable |
| `maze_ref` | str | CODE | `rooms/{id}/maze.json` — name stays (decision 2.3.4) | **protected** |
| `npcs` · `items` · `monsters` | list[EntityLore stub] | CODE (bible stubs `{entity_type, entity_id, name, room_id, lore, tags}`) | mirror of row data | derived |
| `encounters` · `quests` | list[id] | CODE | `ref:event.id` / `ref:quest.id` | derived |

`maze.json` (`MazeLayout`; DC `room_0` keys match): `layout_type:"maze"` · `extra:{}` (not read by
the engine) · `grid` int[h][w] (cell vocab 0 path, 1 wall, −1 event, ≥ 2000 item id; −2 door is
runtime-only — P.6) · `width` · `height` (40×30; **the dims knob**; not read by the engine loader) ·
`door_position` [x,y] · `door_revealed` · `gate_encounter_id ref:event.id` · `player_start` [x,y] ·
`npc_positions {str id: [x,y]}` · `event_positions [{x,y,event_id}]` · `item_placements
[{x,y,item_id,name,portrait_prompt,profile_image}]` (**not read by the engine** — items come from
grid cell values) · `quest_ids [int]` · `environment` · `environment_name` (copied from the room).
Every `maze.json` key is **grid-verb owned** (§4: "grid edits go through grid verbs, not
`db update`"). The manifest also indexes rooms (`num_rooms, environments, environment_names,
maze_width, maze_height, rooms[{room_id, environment, environment_name, npc_count, event_count,
quest_count, environment_portrait}]`) and the engine reads `num_rooms` and `rooms` from it.

**Write targets for `db update --type room`.** `rooms/rooms.json[id]` is the row;
`world_bible.json.rooms.<id>` and `manifest.json.rooms[<entry by room_id>]` are mirrors written in
the same batch (`detail.mirror_of: "room:<id>"`, one journal event per file — the P.7.3 mirror
pattern); `maze.json.environment` / `environment_name` are mirrors written through the room grid
writer (artifact `room:<id>/grid`). Entity rows' `environment` is **not** rewritten by a room edit
— the pack validator warns on mismatch (doctrine 10). `level` re-scales nothing retroactively.
`width`/`height` are **read-only until the W2.0 pull-in** (engine constants; P.6.2 row 12), so
they are not user fields.

```json
{"room": {"label": "Rooms", "layout": {"mode": "collection", "path": "rooms/rooms.json", "format": "keyed_object"}, "id_field": "id", "id_alloc": null, "schema": "schemas/room.json",
  "llm_fields": ["environment_name","story_beat","boss_name","boss_lore"],
  "code_fields": ["id","maze_ref","npcs","items","monsters","encounters","quests"],
  "user_fields": ["environment","level"],
  "routed": {"grid": "grid", "door_position": "grid", "door_revealed": "grid", "gate_encounter_id": "grid", "player_start": "grid", "npc_positions": "grid", "event_positions": "grid", "item_placements": "grid", "quest_ids": "grid", "width": "grid", "height": "grid"},
  "protected": ["id","maze_ref"], "refs": {"encounters[]": "event.id", "quests[]": "quest.id"},
  "phase_label": "layout:maze", "per_map": true, "count_key": "rooms"}}
```

(`rooms.json` is a keyed object `{"room_0": {...}}` — `phases.py:314-318`; the `GridKind` in
`grids.room` (P.3.2) owns `maze.json`.)

### P.1.8–9 `music` and `sfx` — net-new rows

No rows exist on disk today: only `manifest.json.music` / `.sfx` = `{<stem>: <absolute path>}`
from a directory scan of `music/` and `sfx/` (`.mp3/.wav/.ogg`; a JSON row file inside those
directories is safe from the scan — `manifest.py:19,62-79,166-169,214-215`). DC has `{}` for both;
FX has 10 music + 28 sfx machine-absolute entries. The full row schemas, protected set and
attachment contract are P.5; the registry entries there are the P.1 rows for these two kinds.

### P.1.10 Cross-type summary, merged protected set, on-disk names

| type | id_field | layout | rolled (skeleton) | LLM | code / later phase |
|---|---|---|---|---|---|
| npc | `id` | array `npcs/npcs.json` | `behavior_type`→`type` | name, job, hobby, personality, backstory, opening_greeting, portrait_prompt (+exhausted_dialogue, personality_notes) | id, env, x/y, color, selected, is_story_npc, max_dialogue_turns, quest_id/type, shop_inventory, npc_monster, dialogue_tree*, profile_image |
| monster | `id` | keyed `monsters/monsters.json` | tier, physical_type, time_availability | name, species, description, backstory, damage_type, elemental_affinity, weakness, abilities, portrait_prompt | id, hp_range, ac_range, damage_die, level, is_boss, environment, profile_image |
| item | `id` | keyed `items/items.json` | item_kind→category, rarity, weapon_type, weapon_category, physical_type→damage_type, weapon_stat→stat_modifier, tool_attribute→attribute, base_restore | name, desc, portrait_prompt | id, room_level, magic_element, attack_dice, price, health/stamina_value, uses, tags, profile_image |
| quest | `id` | array `quests/quests.json` | quest_type→type, reward_tier→reward.xp | title, description, reward.xp (override), failure_penalty.hp_damage, success/failure_dialogue | id, giver_npc_id, room_id, is_story_quest, reward.item_id, type-specific targets, is_complete/failed |
| event | `id` | array `events/events.json` | event_type→type, difficulty, monster_count | name, description, money_drop, loot_table, failure_damage_type, portrait_prompt | id, difficulty int, x/y, room_level, time_gate, monster_ids, gate flags, choices, correct_*, failure_damage_range, profile_image; **scene: all user** |
| class | `archetype` (proposed) | positional array + `spell_pools.json` | per-slot SPELL/ABILITY specs | name, description, lore, flavor_text, role_tags, category, portrait_prompt, spell/ability names + descriptions | archetype, starting_weapon, stat_template/stats, stat_budget/roles, environment, portrait_path |
| room | `id` | index + per-file `maze.json` | — (knobs are net-new) | environment_name / story text via Story/Narrative phases | everything else; grid data owned by grid verbs |
| music / sfx | `track_id` / `sfx_id` (P.5; P.9 S6) | collection files (proposed) | role/category, mood, loop, duration_s (music) / duration_ms (sfx) | title, brief | binding/trigger, file, file_hash |

**Merged protected set** (mirrors `_PROTECTED_FIELDS` plus dungeon plumbing): `id, archetype
(class), maze_ref, profile_image, portrait_path, file, file_hash, artifact_id, provenance_hash,
parents, status, review_status, library_ref`, plus the npc-specific `selected` (engine activation
gate, P.1.1). **Routed-not-protected** (the `routed` maps above): row `x/y` and every `maze.json`
key → grid verbs; `dialogue_tree*` / `dialogue_trees` → dialogue verbs; scene fields → scene verbs;
audio bytes → asset verbs. **On-disk names are unchanged in v1** (decision 2.3.4): `maze_ref`,
`maze.json`, `maze_width/height`, `maze_*` music ids, the engine class names in `npc.type`, and
every rename above (`behavior_type→type`, `item_kind→category`, `physical_type→damage_type`,
`weapon_stat→stat_modifier`, `quest_type→type`, `event_type→type`) stays a **registry rename map**
the writer applies and the loader inverts — never a disk rename.

### P.1.11 Schema-authoring plan

| type | derivable from `specs.py`? | resists the JSON field-spec (why) | carried as | net-new authoring |
|---|---|---|---|---|
| npc | yes — one `choices` field, no `post` | the roll value is renamed on disk (`behavior_type`→`type`) | registry rename map (key per P.9 S1) | `availability` (engine-only); `dialogue_trees` (dialogue verbs) |
| monster | partially — `tier`, `physical_type`, `time_availability` dump cleanly | `hp_range/ac_range/damage_die` come from `post=_scale_monster` (tier × `room_level`, two inputs — the `TODO(v0.2)` at `core.py:65-71`); `dump_skeleton_spec` refuses the spec | `code_fields`; `_scale_monster` stays Python (platformer precedent) — P.9 S4 | — |
| item | partially — six `choices`/`range` + one `lookup` dump cleanly | `attack_dice/price/restore` and the kind-conditional branches come from `post=_scale_item` | `code_fields`; the JSON still bounds `category, rarity, weapon_type, weapon_category, damage_type, stat_modifier, attribute, base_restore` | — |
| quest | **yes, fully** — no `post` | `reward.xp` table and cross-refs are parser code | `code_fields` | — |
| event | **yes** for `type/difficulty/monster_count` | label→int coercion; solve-ref resolution is code | `code_fields` | **`scene`**: all fields net-new, no roll; schema needed only for validation + RowEditor read view |
| class | **no row spec exists**; `SPELL_SPEC`/`ABILITY_SPEC` dump cleanly as sub-schemas | loadouts are pack data with per-slot pins, not rolls; the sum-95 budget is a cross-field invariant the field-spec cannot express | per-stat bands **are** expressible (`STR: lookup(depends_on=archetype)` + `lookup_ranges`), so `schemas/class.json` bounds `archetype` + seven stats; budget → warn (§12 "cross-field rules v1.1"). `archetype` is the roll's **`locked` anchor** (`core.py:254-281`): `db new --type class` requires `--set archetype=<value>`; the skeleton lists the loadout archetypes as `choices` only so the seven stat lookups have a parent — it is never rolled unlocked (P.1.6: protected identity) | class row schema; spell/ability sub-rows reuse the two dumped specs |
| room | no — `MazeLayoutPhase` takes width/height args; `level` is `i+1`; env is cycled | grid content is not schema data | mirror the platformer's `level_layout.json` idiom (`depends_on_context`) for `width/height/level/environment` | **net-new** `schemas/room.json` |
| music / sfx | no | — | seed rows from the engine catalogs at create (P.5) | **net-new** `schemas/music.json`, `schemas/sfx.json` |

## P.2 Condition-namespace inventory

**The one fact that shapes everything:** the MazeWorld engine reads **no `conditions` and no
`effects`** from any dialogue choice today. The offline-static walker reads only `choice["text"]`
and `next_node_id` (`MW/src/utils/conversation_utils.py:229-237`); canon's emitter writes only
`{text, next_node_id}` (`dialogue.py:194-197`); every choice in both packs has exactly those keys
(83 NPCs inspected 2026-09-01). Canon's core model already carries the two lists as free-form tokens
"interpreted by the host game" (`CA/src/canon/dialogue/models.py:29-38`); **no grammar and no
evaluator exists in canon or cradle** (grep for the token names hits only that docstring). So at
tree/choice scope every namespace is "engine: no" today, and the gate-ribbon dot is amber for every
condition on a dungeon pack. What the engine does evaluate is three coarser layers: tree selection
(the legacy four-field swap on quest **completed/failed** only, `quest_manager.py:302-325`), NPC
availability by day/night period (`day_night.py:86-98`), and event trigger by `time_gate`
(`day_night.py:69-83`). The walker runs only in `GAME_MODE == "offline_static"` (the default); in
the LLM modes trees are prompt context only.

### P.2.1 Grammar table

Tokens per the approved package (`CR/design_handoff_dialogue/README.md:87-100`) and §7.1. The
operand is the entity's `id_field` value **verbatim, stringified** (ints for the dungeon pack:
`has_item:2000`, `quest:4000:completed`); the design's slug ids are illustrative (P.9 C8).
"Fall-through" = what the engine does with a token it cannot evaluate: today nothing — the choice
shows unconditionally (`README.md:274`'s doctrine line).

| namespace | token | operand vocabulary | engine today (dialogue scope) — evidence | tester simulates |
|---|---|---|---|---|
| `has_item` | `has_item:<item_id>` | item · `id` | **no** — inventory is keyed by item *name* (`inventory.py:22-27`); the id→name idiom exists for fetch/delivery quests (`quest_manager.py:109-114,332-341`) | `inventory {item_id: qty}` |
| `quest` | `quest:<quest_id>:<state>` | quest · `id`; state = quest status list (data) | **partial, selector layer only** — statuses are exactly `not_started · active · completed · failed` (`quest.py:34`); tree swap on `completed`/`failed` only; `not_started`/`active` share one tree (P.9 C2) | `quests {quest_id: status}` |
| `time` | `time:<window>` | no pack entity — engine periods `dawn · day · dusk · night` (`time.py:18-31`); gate values in data `day \| night \| always \| null` | **partial, NPC-availability and event-trigger layers only**; `Quest.time_gate` and `EventChoice.time_gate` are defined but never read; `Monster.time_availability` is copied onto the live monster (`monster.py:218`) but never evaluated; **no hour-of-day clock** exists (P.9 C3) | `clock {…}` (form per C3) |
| `player` | `player:<field>:<op>:<value>` | no pack entity — engine `PlayerCharacter` fields `level, health, max_health, stamina, money, player_class.archetype, STR…LUCK, learned_spells, equipped_weapon, title` (`player.py:143-240`) | **no** — the only player-derived gate is the CHA dismissal roll in the online LLM path | `player {field: value}`; the field list is registry data |
| `flag` | `flag:<key>` (arity P.9 C7) | author-named keys = every key referenced by a `set_flag` effect in the pack | **no** — no flag store anywhere; `SaveState` has no flags field (`save.py:19-79`) | `flags {key: bool}` |
| `segment` | `segment:<segment_id>` | no pack entity — nearest: `OverarchingStory.escalation_arc`, `RoomStoryBeat.escalation 1-5`, `narrative.json` keys; none is a segment table (P.9 C4) | **no** | `segment` |
| `room` | `room:<room_id>` | room · `id` (string `room_N` on quests; int index in `GameController.current_room`) | **no** — room is used for quest failure only (`quest_manager.py:204-224`) | `room` |
| `scene` | `scene:<event_id>:seen\|unseen` | event · `id`, `type == "scene"` | **no** — no scene type, no seen record, no `once` (`Event.resolved` is the only per-event state) | `scenes_seen []` |
| `event` | `event:<event_id>:solved\|unsolved` | event · `id` | **no** as a condition; the state exists as `Event.resolved` (`encounter.py:31`) — set on success **and on failure** (`event_input_handler.py:190-195`), no success flag persists (P.9 C5) | `events {event_id: solved\|unsolved}` |
| `actor` (scene-only) | `actor:<character_id>:present\|absent` | npc · `id`, restricted to the scene's `actors[]`; rejected in trees with the reason | **no** — no scene runtime; nearest facts are room membership + availability | `actors {character_id: present\|absent}` |

### P.2.2 Effects table

Grammar `gives_item · takes_item · gives_quest · advance_quest · set_flag` (§7.1). None is applied
from a dialogue choice today; the same state change exists elsewhere:

| effect | token | engine's own path for the same change |
|---|---|---|
| `gives_item` | `gives_item:<item_id>` | quest reward `registry.get_item → clone → add_to_inventory` (`quest_manager.py:171-175`); puzzle loot; shop purchase |
| `takes_item` | `takes_item:<item_id>` | fetch/delivery turn-in removal (`quest_manager.py:105-114,344-350`); tool consumption on a puzzle choice |
| `gives_quest` | `gives_quest:<quest_id>` | the engine offers **only `npc.quest_id`** after the conversation ends (`game_controller.py:749-760` → `accept_quest_from_npc`, sets `active`) |
| `advance_quest` | `advance_quest:<quest_id>[:<state>]` (arity P.9 C7) | `complete_quest` / `fail_quest` / `_advance_multi_step` (`quest_manager.py:162-252`) — triggered by turn-in, event resolution, escort zone, room progression, never a choice |
| `set_flag` | `set_flag:<key>[:<bool>]` | no flag store |

Engine effects the grammar does not name (awareness only): money, HP/stamina penalties, door
reveal, dialogue-tree swap.

### P.2.3 Runtime state the tester simulates

Tester sections per `README.md:144` (+ actor presence `:229`); engine fields and save persistence
(`MW/src/models/save.py:19-79`):

| tester section | engine class · field | vocabulary | in `SaveState`? |
|---|---|---|---|
| Inventory | `PlayerCharacter.inventory` via `InventoryManager` — `Dict[item_name, Item]`, `Item.quantity` | id lookup through `registry.item_registry` | `player_data` |
| Quests | `Quest.status`; `active/completed/failed_quests` lists; `MultiStepQuest.current_step` | `not_started · active · completed · failed` | `quest_states` |
| Clock | `DayNightCycle`: `ticks`, `cycle_length`, `elapsed_ms`; derived `current_period`, `period_progress`, `day_number`, `is_night`; dialogue pauses the clock | period ∈ `dawn · day · dusk · night` | `day_night_data` |
| Place | `GameController.current_room` (int), `total_rooms`; `Maze.environment`; `door_revealed` | int index; `room_N` strings on quests | `current_room`, maze fields |
| Player | `level, health/max_health, stamina, money, player_class.archetype, get_stat_modifier, learned_spells, equipped_weapon, title, followers` | — | `player_data` |
| Flags · Segment · Scenes seen | **none** | — | — |
| Events | `Event.resolved`; `CombatEvent.is_gate/is_climax_boss`; `event_position_map` | bool, no success flag | `event_states`, `event_position_map` |
| Actor presence | no scene runtime; NPC-level: `availability`, room membership, `AggressiveNPC.combat_defeated` | — | `npc_states` |
| Conversation cursor | `tree["_current"]`, `has_met_player`, `dialogue_exhausted`, `interaction_history`, `max_dialogue_turns`, `current_dc`; reset on tree swap / quest decline | — | `npc_states` |

The `--state` payload of `dialogue test` (§7.2) therefore carries at minimum `inventory`, `quests`,
`clock`, `room`, `player`, `flags`, `scenes_seen`, `events`, and for scene tests `actors` — none a
closed union; the evaluator reads the registry `DialogueSpec` for legal namespaces and the pack's
entity tables for legal operands.

### P.2.4 The `engine_evaluable_namespaces` field

Constraints (from §7.2, `PLAN.md:150-154,226`, §5.1b): **per engine** (a pack may carry more than
one engine; evaluability is a property of the engine copy, so it lives on the `engines[]` entry —
P.9 C1 settles the manifest-vs-registry wording); **per scope** (the five `DialogueSpec.scopes`
values `tree | selector | scene | effects | music` — `effects` is a scope whose keys are effect
tokens rather than condition namespaces; the same namespace can be evaluable at selector level and
not at choice level — `quest` today); **open vocabulary** (a map of strings, never an enum);
**absent ≠ empty** — a template create **seeds the field explicitly**, so a pack whose engine
evaluates nothing carries an explicit empty set and the lag layer is loud from day one. **Absent
resolution:** an absent `evaluable_namespaces` (a pre-registry pack — P0-3…P0-9 land before P0-10
stamps the first registry — or a legacy pack) resolves to the seed `DialogueSpec.engine_evaluable_seed`
for the primary engine, **never to "all supported"**; the PLAN's legacy "absent = all supported"
reading is superseded here (doctrine 10: warn loudly, never block — every gate stays amber on a
dungeon pack whose engine evaluates nothing). `canon pack info` surfaces the **primary** engine's
block under the manifest-level name `engine_evaluable_namespaces` so `engineSupports(ns, scope,
pack)` reads one place. A key present under a scope = evaluable; an optional object narrows the
honored operand values (the ribbon goes amber for an operand outside the narrowing, e.g.
`quest:4000:active` on a selector row). Audio slot evaluability (music `binding.kind`, sfx
`trigger.kind`) is a **sibling** field `evaluable_bindings` on the same engine entry (P.5.3) —
binding kinds are not condition namespaces and never nest under this key.

Concrete value, **dungeon `pygame` engine as it exists today** (from P.2.1–P.2.2):

```json
{
  "id": "pygame",
  "evaluable_namespaces": {
    "tree":     { },
    "selector": { "quest": { "states": ["completed", "failed"] } },
    "scene":    { },
    "effects":  { },
    "music":    { }
  }
}
```

`time` is deliberately not listed at selector level: the engine's time checks gate *talking* and
*event triggering*, not tree choice — if that NPC-level gate should be visible to authors it is the
`npc.availability` field, not a namespace.

Concrete value, **platformer**: the platformer pack does not declare the `dialogue` capability
(§6 "platformer packs don't declare it"), so its engines entries carry **no** `evaluable_namespaces`
key and no dialogue surface mounts. When a platformer-descended project enables `dialogue` via
§5.1a (the W2.2 proof case), enablement seeds `{"tree":{}, "selector":{}, "scene":{}, "effects":{},
"music":{}}` on every attached engine — every gate amber until engine work lands, never the
"absent = all supported" reading. Seed source: the template's `DialogueSpec` (P.3.3) carries the
legal namespace list, per-namespace operand descriptors (which entity type/field the picker reads;
the `player` field list; the `quest` status vocabulary; the `time` window vocabulary), scope
legality, and the per-engine evaluability seed.

### P.2.5 Music-section conditions — the scope-legal subset (§3.0-G)

A music section carries `conditions: Token[]` in the same grammar, validated by the same
`parseToken`/`legalIn` with a scope value `music` (scope names are `DialogueSpec` data, so adding
one is a data change) and evaluated against the same per-engine block under the `music` key.
Which namespaces are legal in `music` scope is W2.1's subset design (master row P2-W2.1), recorded
there — Phase 0 carries only the seam: the scope name is seeded and `"music": {}` says no namespace
is evaluated for sections today. Facts the subset will lean on: the engine keys ambient music by
room environment (`music_controller.py:161-165`); `MusicDirector` (`music_director.py:16-29`)
switches `combat_handler.active` → `combat`, `dialogue_box.event_active` → `puzzle_event`, neither
→ `restore_maze` — two transient runtime modes with no namespace (P.9 C6).

## P.3 `EntityKind` / `GridKind` / `DialogueSpec` / `PackSpec` shapes

Convention: **seed-only** = lives in Python (callables, Pydantic classes), never serialized;
**stamped** = written into `.canon/registry.json` at create and read by every verb thereafter
(§5.1a). Every `str` "kind" field is an open vocabulary. Sources: `ops.py:62-82,701-735`;
`database.py:76-116`; `runner.py:32-69`; `compose.py:105-112,139-204,281-311`;
`platformer_write.py:28-40,62-63,145-181,225`; `layout/__init__.py:41-64`; §5.1, §7.1–7.2.

### P.3.1 `EntityKind`

```python
@dataclass
class EntityKind:
    # identity / storage (stamped)
    kind: str                        # registry key: "enemy" | "npc" | "music" … (open)
    label: str = ""                  # display name (pack info → LeftNav groups / RowEditor)
    layout: Layout                   # {"mode":"per_file","dir":"enemy"} | {"mode":"collection","path":"npcs/npcs.json","format":"array"|"keyed_object"|"array_positional"}
    id_field: str                    # "enemy_id" (platformer) | "id" (dungeon rows) | "archetype" (class)
    id_alloc: dict | None = None     # {"base": 1000} — int-id allocation base (IDAllocator bases, compose.py:294-303); None for slug ids
    schema: str | None = None        # pack-relative skeleton path "schemas/<kind>.json"; None = no roll table
    # authoring split (stamped)
    llm_fields: list[str] = []
    code_fields: list[str] = []      # post-derived / archetype-dependent values the skeleton cannot express
    user_fields: list[str] = []      # never generated; RowEditor renders as editable (P.1 conventions)
    hidden: list[str] = []           # the P.9 S5 hide set
    decorative: list[str] = []       # editable; RowEditor shows "engine ignores this field"
    # write discipline (stamped)
    nesting: dict[str, str] = {}     # flat knob → container ("attack_dice" → "item_stats"); container names only
    containers: list[str] = []       # dotted-path containers allowed (knob-wise, never whole-replace); lists use <c>[<i>].<key>
    protected: list[str] = []        # per-kind additions to the CORE wall (artifact_id, provenance_hash, parents, status, review_status + asset plumbing)
    routed: dict[str, str] = {}      # field → owning verb ("x":"grid", "dialogue_trees":"dialogue", "lines":"scene") — real field names
    renames: dict[str, str] = {}     # skeleton name → on-disk name ("behavior_type":"type"); loader inverts
    refs: dict[str, str] = {}        # field path → "<kind>.<id_field>" cross-refs the validator resolves ("quest_id":"quest.id", "shop_inventory[].item_id":"item.id")
    # generation (stamped where data)
    phase_label: str = ""            # "plat:enemies" — the §3.0-E label-map key
    per_map: bool = False            # dungeon rows generate per room
    count_key: str | None = None     # wizard/estimator count name ("npc" | "enemies")
    dedup: list[str] = []            # cross_room_dedup fields
    asset: dict | None = None        # {"field":"profile_image","hash_field":…,"kinds":["image"],"targets":["npc:<id>"]} — the attachment slot (music/sfx in P.5)
    vocab: dict[str, list] = {}      # per-kind data vocabularies (binding kinds, trigger kinds, …)
    # seed-only (code)
    model: type[BaseModel] | None    # Pydantic strictness upgrade; None → dynamic model from `schema` (§5.1a)
    loader: Callable | None          # tree → model read-back inverse (§8.2) — MISSING today for dungeon kinds
    parser: Callable | None          # DatabaseSpec.parser (generation direction)
    prompt_method: str | None        # DatabaseSpec.prompt_method
    prompt_kwargs: dict = {}
```

`Layout` is a two-member open shape, not an enum — the "one genuinely new core mechanism" (§5.1).
`id_alloc` rides `EntityKind` (not `PackSpec`) so `db define` can allocate for a net-new dungeon
type; `db new` allocates `max(existing ids ≥ base, base − 1) + 1`. A `db define`d type has
`model=None`, `loader=None` and validates through the skeleton-derived dynamic model.

**Dynamic model rule (P0-6, every kind with `model=None`):** fields = the skeleton's rolled fields
under their on-disk names (`renames` applied), typed by mode — `choices` → the value type of the
first choice, `range` → `int`, `lookup` → the value type (`int` when `lookup_ranges`); off-table
values **warn** (the `update_db_row` precedent). `extra = allow`: non-rolled fields are unchecked
in v1 — P.1's type column documents, it does not enforce. Fail-closed = the collection file
re-parses in its `layout.format`, `id_field` values stay unique, and every `refs` path resolves
against the pack (`ref:<kind>.<id_field>`). `canon db schema` output (`{skeleton_fields,
llm_fields, code_fields, schema_source}` today) gains `user_fields, hidden, decorative, protected,
routed` (P0-6); `RowEditor` reads them (P0-8).

### P.3.2 `GridKind` (all stamped)

```python
@dataclass
class GridKind:
    kind: str                        # "room" | "level" (open)
    ref_field: str                   # "maze_ref" (on-disk name stays) | "level_id"
    path_template: str               # "rooms/{map_id}/maze.json" | "level/{stage_id}/{level_id}/"
    file: str | None                 # "maze.json" | None when the grid is a directory of step files
    steps: dict[str, str] = {}       # step → file: {"grid":"maze.json"} | {"collision":"collision.npz", … "level":"level.json"}
    dense: list[str] = []            # ["grid"] | ["collision","terrain","background"]
    sparse: list[str] = []           # [] | ["hazards","triggers","foreground"]
    placements: dict[str, dict] = {} # per placement key (stamped shape below): which EntityKind it places, its wire key, storage shape, id key, grid stamp, journal kind
    points: list[str] = []           # ["player_start","door_position"] | ["spawn","exit"]
    dims: dict = {}                  # {"width_field":"width","height_field":"height","default":[40,30]} — data, never a schema literal (W2.4)
    cell_vocab: str = ""             # ref to the tile registry: "tiles/maze_tiles.json" (P.6) | tileset slots + collision categories
    derived: list[str] = []          # steps recomputed on paint: [] | ["terrain","background","hazards"]
    restorable: list[str] = []       # ["entities","items","triggers","hazards","foreground"] (platformer)
    artifact_id: str = ""            # "level:{stage_id}/{level_id}/{step}" | "room:{map_id}/{step}" ← P.9 R1
```

Per-engine sibling files (`*.grid.json`) are declared on the engine entry (`engines[].artifacts`,
P.4.3), never on the grid — one home. The dungeon `placements` block, stamped:

```json
{"npc_positions":   {"kind": "npc",   "wire": "entities", "shape": "dict",  "id": "id",       "grid_stamp": null, "journal_kind": "npc_move"},
 "event_positions": {"kind": "event", "wire": "triggers", "shape": "list",  "id": "event_id", "grid_stamp": -1,   "journal_kind": "event_move"},
 "item_placements": {"kind": "item",  "wire": "items",    "shape": "list",  "id": "item_id",  "grid_stamp": "id", "journal_kind": "item_move"}}
```

`quest_ids` is not a placement (not spatial — a routed room field). Dock tabs = the `kind` of
every `placements` entry, in this order; the writer resolves the wire id key (`enemy_id` /
`item_id`, P.9 G9) from `wire` and the row id from the kind's `id_field`. Platformer:
`entities {kind: "enemy", wire: "entities", shape: "list", id: "enemy_id"}`, `items {kind:
"item", wire: "items", shape: "list", id: "item_id"}`.

### P.3.3 `DialogueSpec` (selector model; `variant_specs` is superseded — master S4)

```python
@dataclass
class DialogueSpec:
    storage: dict                     # {"on":"npc","field":"dialogue_trees","legacy_fields":["dialogue_tree","dialogue_tree_incomplete","dialogue_tree_complete","dialogue_tree_failed"]}
    condition_namespaces: list[str]   # has_item · quest · time · player · flag · segment · room · scene · event (§7.1, §3.0-G)
    scene_only_namespaces: list[str]  # ["actor"]
    effects: list[str]                # gives_item · takes_item · gives_quest · advance_quest · set_flag
    scopes: list[str]                 # ["tree","selector","scene","effects","music"] — scope names are data (P.2.4/P.2.5); "effects" evaluates effect tokens, not conditions
    operands: dict[str, dict]         # per namespace descriptor the picker + evaluator read — full dungeon seed below
    selector_axes: list[str]          # quest, segment, time, flag, room, scene, player, custom
    scene: dict                       # {"event_type":"scene","triggers":["enter_room","talk_any_actor","quest_advance"],"once":true,"on_finish":"effects"}
    engine_evaluable_seed: dict[str, dict]  # per engine id → the P.2.4 block, copied onto engines[].evaluable_namespaces at create
    tree_model: type | None = None    # seed-only: canon.dialogue.models.DialogueTree
```

All list fields are data ("vocabulary is pack-registry data; no component builds tokens by
concatenation", §7.1). Absent from the registry when `dialogue ∉ capabilities`.

The dungeon `operands` seed — one descriptor per namespace (every value list open; C2/C3/C4/C7
defaults applied):

```json
{"has_item": {"entity": "item", "field": "id"},
 "quest":    {"entity": "quest", "field": "id", "states": ["not_started","active","completed","failed"]},
 "time":     {"windows": ["dawn","day","dusk","night"]},
 "player":   {"fields": ["level","health","max_health","stamina","money","archetype","STR","DEX","CON","INT","WIS","CHA","LUCK"], "ops": ["<","<=","==",">=",">"]},
 "flag":     {"keys": "from set_flag effects", "values": ["true","false"]},
 "segment":  {"values": []},
 "room":     {"entity": "room", "field": "id"},
 "scene":    {"entity": "event", "field": "id", "filter": {"type": "scene"}, "states": ["seen","unseen"]},
 "event":    {"entity": "event", "field": "id", "states": ["solved","unsolved"]},
 "actor":    {"entity": "npc", "field": "id", "restrict_to": "scene.actors", "states": ["present","absent"]}}
```

The `dialogue test --state` payload (P.2.3) in the same vocabulary — keys are the tester sections,
values keyed by the stringified `id_field`:

```json
{"inventory": {"2000": 1}, "quests": {"4000": "active"}, "clock": {"period": "night", "day": 2},
 "room": "room_1", "player": {"health": 14}, "flags": {}, "scenes_seen": [], "events": {"3000": "solved"},
 "actors": {"1000": "present"}}
```

### P.3.4 `PackSpec` envelope

```python
@dataclass
class PackSpec:
    pack_type: str                    # "platformer" | "dungeon" — open; source of truth is .canon/registry.json, mirrored into manifest.json.pack_type on EVERY manifest write (P.4.1)
    label: str                        # "Dungeon crawler"
    description: str = ""
    vocab: list[str] = []             # ["floors","rooms","encounters"]
    entities: dict[str, EntityKind]
    grids: dict[str, GridKind] = {}
    dialogue: DialogueSpec | None = None
    capabilities: list[str] = []      # seed list, copied to the instance (§5.1a)
    counts: dict[str, int] = {}       # default wizard counts (MAZEWORLD_DEFAULT_COUNTS | world new defaults)
    wizard: WizardMeta                # P.4.4 — label/vocab/defaults/ranges/engine/dimension/distribution (W2.4)
    engines: list[EngineEntry] = []   # SEED entries (P.4.3); create stamps the pack's one entry (§3.0-H; platformer godot, dungeon pygame — P.9 R6)
    tuning_vocabulary: str | None     # path to the rule_overrides-style vocabulary: "rule_overrides.json" | None (dungeon) — recorded, not read until W2.1 (P.4.5)
    world_fields: dict[str, dict] = {}   # stamped: `world update` field table — dotted key → {file, path, mirrors[]} (P.7.1); tune set (W2.1) passes its own table
    phase_labels: dict[str, str] = {} # phase-id → label (§3.0-E; populated by P0-10)
    data_files: dict[str, str] = {}   # seed-only reference to template data the runner takes as flags today (P.9 R3)
    # seed-only callables
    compose: Callable                 # dungeon: compose_pipeline; platformer: the slice runner (UNVERIFIED which callable the registry names)
    estimator: Estimator              # (count_fn, cost_model path) per W2.1.2 — born at P0-7
    prompts: type                     # MazeworldPromptSet | platformer prompts
    validators: Callable | None
    archetypes: dict = {}             # dungeon class archetypes
    schemas: dict = {}                # ctx.schemas — weapon/spell/monster/item SkeletonSpecs
```

Stamped subset = `pack_type, label, description, vocab, capabilities, counts, wizard,
entities (stamped fields), grids, dialogue (data fields), engines, tuning, world_fields,
phase_labels`. Seed-only = every callable/class plus `data_files` and `tuning_vocabulary`.

## P.4 `.canon/registry.json` format

### P.4.1 Seed vs instance semantics (§5.1a)

- Code `PackSpec` = **seed**. `world new --template <t>` stamps the effective registry into
  `<pack>/.canon/registry.json`, beside the pack-local `schemas/` dir; every verb resolves against
  the pack's file, never the template's. Precedent already in code: `_schema_path` pack-local
  override (`ops.py:95-100`) and `db schema --set` (`ops.py:1070-1137`).
- **Resolution order — `resolve_pack(pack_dir)` (P0-3 owns all four tiers and the two mirror
  lines):** (1) `.canon/registry.json` → the stamped `PackSpec`; (2) `manifest.json.pack_type` →
  the code seed `PackSpec` for that id (the pre-registry tier: every pack P0-3/5/6/8/9 touch has a
  stamp but no registry until P0-10 writes the first one; the read-both shim covers the registry
  too — no migration, no synthesized file until the first registry-writing verb runs); (3) shape
  detection: `level/` dir ⇒ `platformer`, `rooms/` + `world_bible.json` ⇒ `dungeon` (cradle's
  heuristic today: `manifest.json` + `level/`, `data.rs:166-168`; both demo worlds resolve this
  way); (4) error `unknown pack type`. **No migration.**
- **`pack_type` mirror rule.** Both manifest writers rebuild `manifest.json` wholesale and carry no
  such key today (`ManifestPhase`, `manifest.py:186-220`, `write_json_singleton`; platformer
  `compose.py:130-134,240-252`, "rebuilt on every resume"), so a stamp written once would vanish on
  the first `--resume`/regen. Each writer copies `ctx.pack_spec.pack_type` into
  `manifest.json.pack_type` on **every** write; the registry (once present) is the source of truth
  and the manifest key is its mirror.
- **`.canon/` outputs and the golden fixtures.** `tests/treediff.py` excludes only `bible.json`,
  `log.jsonl`, `generation_stats.json` by basename, so a stamped `.canon/registry.json`, a create-run
  journal (P.9 J8) and the `manifest.json.pack_type` key are each a fixture delta beyond doctrine
  7's sanctioned `bible.json` — sanctioned explicitly or not at all: **P.9 R14**.
- The registry is journaled like any artifact: `db define`, `db evolve`, capability changes and
  band widening appear in History with actor + diff, `artifact_id = "registry"` (a new id family
  beside `schema:<type>`; P.7.3). For collection layouts the CAS unit is the **file**: `row_restore`
  on `<kind>:<id>` restores every row in that file and History labels it "restores `<file>` (N
  rows)" (the file-level granularity accepted at §12).
- `template.version` = `sha256` over the stamped subset serialized as canonical JSON (sorted keys,
  `separators=(",", ":")`, `ensure_ascii=False`), excluding the `template` block itself.
- Per-type roll tables stay files (`schemas/<kind>.json`) referenced by `entities.<kind>.schema`;
  the registry never inlines one (keeps `db schema --set` unchanged, §3.0-A).
- Divergence is the point: no template-upgrade contract; `pack promote` deferred.

### P.4.2 Top-level key layout — worked dungeon example

```jsonc
{
  "schema": "canon-registry/v1",          // mirrors "canon-engine/v1" (godot/.engine.json) and journal "schema": 1
  "pack_type": "dungeon",                  // same value stamped into manifest.json; open string
  "template": {                            // provenance of the seed (template ref + version stamp)
    "id": "dungeon",                       // template id — data (M0-readiness rule)
    "version": "sha256:…",                 // content hash of the seed PackSpec's stamped subset (hash, not semver — godot_export.py:21-24 precedent)
    "canon_version": "0.1",                // Bible.canon_version
    "created_at": "2026-09-01T12:00:00+00:00"
  },
  "label": "Dungeon crawler",
  "description": "Floors of rooms with encounters and loot tables.",
  "vocab": ["floors", "rooms", "encounters"],
  "capabilities": ["grid", "dialogue", "per_step_roll"],   // LIST, instance data; values open — P.4.2a
  "counts": { "npc": 2, "item": 3, "monster": 2, "event": 4, "quest": 2, "class": 4 },
  "entities": { "npc": { /* P.1.1 stamped fields */ }, "monster": {}, "item": {}, "quest": {}, "event": {}, "class": {}, "room": {}, "music": {}, "sfx": {} },
  "grids":    { "room": { /* P.3.2 */ } },
  "dialogue": { /* P.3.3 data fields */ },                  // absent when "dialogue" ∉ capabilities
  "engines":  [ /* P.4.3 entries — the pack's one entry at create (§3.0-H; P.9 R6) */ ],
  "tuning":   { "schema": "canon-tuning/v0", "status": "reserved", "keys": {} },   // P.4.5; every template stamps exactly this in Phase 0
  "world_fields": { /* P.7.1 field table — dungeon: story.*, narrative.* */ },
  "phase_labels": { "db:npc": "NPCs" /* … */ },             // §3.0-E slot; populated by P0-10
  "wizard":   { /* P.4.4 copy, or template-only — P.9 R4 */ }
}
```

**P.4.2a Capabilities as data.** `capabilities` is an instance list seeded by the template (`grid`,
`dialogue`, `per_step_roll` at launch — values, never a union); surfaces gate on declared
capabilities instead of type sniffing (§9). A platformer-descended project enables `dialogue` via
the `canon registry set` idiom (P.7.4); enablement is journaled (`detail.kind: capability_set`).
Whether disabling is allowed in v1 is P.9 R12.

### P.4.3 The engines block — §5.1b field list (format only; C18)

Entry fields in §5.1b order; sources `godot_export.py:41-75` (stamp), `lib.rs:991-1006,1255-1268,
1291-1436` (launch + hooks), `godot_adapter.py:34` (artifacts):

| field | type | meaning |
|---|---|---|
| `id` | str (open) | `pygame`, `godot`, … |
| `template` | `{ref: str, version: str\|null}` | which engine template was copied in + its stamp; drift is a loud warn, never a block; today `godot/.engine.json.template_hash` is the same hash (single source: P.9 R7) |
| `launch` | `{cmd: str, args: list[str], env: dict[str,str]}` | **templated, never literal** — placeholders `{python}`, `{godot}`, `{pack}` resolved host-side by the existing `CANON_BIN`/`GODOT_BIN` order (W3.3); `{level}` proposed as a fourth (P.9 R5) — both launches take a level today |
| `live_channel` | `{kind: str, protocol: str\|null}` | `none` \| `hooks-v0` (= the 12 `PLAT_*` env hooks) \| `live-vN`; the protocol slot waits for the real channel (W2.0) |
| `artifacts` | list[str] (pack-relative globs) | per-engine files the engine owns / keeps in sync |
| `exports` | list[str] (open) | `computer` \| `web` \| `mobile` — the wizard's distribution axis reads this |
| `primary` | bool | default for ▶ Play only; both engines keep preview surfaces |
| `evaluable_namespaces` | dict (P.2.4) | **additive; in §5.1b since 2026-09-01 (C1)**; present only when `dialogue ∈ capabilities`; scopes → condition namespaces |
| `evaluable_bindings` | dict (P.5.3) | **additive; in §5.1b since 2026-09-01 (C1)**; row kind → list of `binding.kind` / `trigger.kind` values the engine honours (`{"music": [...], "sfx": [...]}`); present whenever `music`/`sfx` kinds exist |

The actual rewrite of `play_level`/`play_game` into launch-by-engine-id, and dissolving the 15
engine-coupling sites, is W2.0's; `engine attach` and fork-per-switch are W2.2. Phase 0 ships only
the seed path — P0-10 stamps **the pack's one entry** (§3.0-H): `godot` on a platformer create,
`pygame` on a dungeon create. The un-promoted pygame play harness is not copied into the pack
(master S1) and is therefore **not an engines entry in Phase 0** — cradle's `play_level` keeps
its current code path until W2.0's launch-by-id rewrite, which is where the harness either gets
promoted or gets an entry (its would-be shape is recorded below for that row; P.9 R6).

**Worked entry 1 — `godot` on a platformer pack** (derived from `play_game` + `GodotExportPhase`):

```json
{
  "id": "godot",
  "template": { "ref": "platformer_pack/godot_template", "version": "sha256:<template_hash from godot/.engine.json>" },
  "launch": { "cmd": "{godot}", "args": ["--path", "{pack}"], "env": { "PLAT_LEVEL": "{level}" } },
  "live_channel": { "kind": "hooks-v0", "protocol": null },
  "artifacts": ["project.godot", "godot/main.tscn", "godot/main.gd", "godot/.engine.json", "level/*/*/*.grid.json"],
  "exports": ["computer", "web", "mobile"],
  "primary": true
}
```

`{godot}` resolves `GODOT_BIN` → `godot` on PATH → `/Applications/Godot.app/…`. `PLAT_LEVEL` is
optional (full game when absent); `PLAT_ANIM`/`PLAT_ANIM_MODE`/`PLAT_SANDBOX` are host-added mode
hooks, not part of the base contract. `primary: true` is a proposal (today ▶ Play-level is pygame
and ▶ Play-game is Godot; no flag exists). `exports` is the §5.1b declaration only — the template's
`project.godot` shows no export preset (UNVERIFIED that exports work). **Not stamped in Phase 0:**
the platformer's would-be `pygame` harness entry (`cmd "{python}"`, `args
["-m", "canon.packs.platformer.play", "{pack}", "{level}"]`, `template.version: null`,
`exports: []`, `primary: false`) — recorded for W2.0 only; the harness lives in the canon wheel
(moved 2026-09-01 to `canon.packs.platformer.play`; cradle spawns it by module), not in the pack
(master S1), so no repo placeholder is needed (P.9 R6).

**Worked entry 2 — `pygame` on a dungeon pack** (Phase 0 seeds it; nothing launches it until W2.0):

```json
{
  "id": "pygame",
  "template": { "ref": "dungeon_engine", "version": null },
  "launch": {
    "cmd": "{python}",
    "args": ["-m", "mazeworld", "--data-dir", "{pack}"],
    "env": { "GAME_MODE": "offline_static", "LLM_BACKEND": "local", "MUSIC_BACKEND": "none", "IMAGE_BACKEND": "local" }
  },
  "live_channel": { "kind": "none", "protocol": null },
  "artifacts": [],
  "exports": [],
  "primary": true,
  "evaluable_namespaces": { "tree": {}, "selector": { "quest": { "states": ["completed", "failed"] } }, "scene": {}, "effects": {}, "music": {} },
  "evaluable_bindings":   { "music": ["environment", "state", "screen"], "sfx": ["event", "environment"] }
}
```

`evaluable_bindings` is true today: the engine keys `maze_{env}`, `combat`/`puzzle_event` and
`game_over` slots (P.5) — bindings are honoured even though no condition namespace is evaluated
for sections (`"music": {}`). `pack info` surfaces both blocks from the primary engine (P.4.6).

Every element of `args` is **UNVERIFIED / not launchable today**: the external checkout hardcodes
`DATA_DIR = "data_canon/"` (`MW/config.py:17`), so `{pack}` cannot be honored until the W2.0 pull-in
parameterizes it; `env` values are what `MW/launcher.py:12-15` sets; `template.ref` is a placeholder
for the pulled-in engine template. `live_channel: none` is honest — no `PLAT_*`-style hooks.
Criterion 2's September play leg is the manual external-checkout run (master Q3).

### P.4.4 `pack templates` wizard metadata (W2.4; row P0-10)

Rendered from `canon pack templates`; replaces the hardcoded `TEMPLATES` array
(`NewProjectModal.tsx:15-30`: `id, name, desc, vocab, beta`). Template-side (read before a pack
exists); the instance stamps a copy under `wizard` only so `pack info` can show provenance (P.9 R4).

```jsonc
{
  "id": "dungeon",                          // = pack_type
  "label": "Dungeon crawler",
  "description": "Floors of rooms with encounters and loot tables.",
  "vocab": ["floors", "rooms", "encounters"],
  "defaults": { "rooms": 3, "npc": 2, "monster": 2, "item": 3, "event": 4, "quest": 2, "class": 4 },   // compose.py:105-112 + num_maps default
  "ranges":   { "rooms": [1, 8], "npc": [0, 8] /* … */ },      // UNVERIFIED — no ranges exist in code (P.9 R8)
  "advanced": ["event", "quest", "class"],                    // W2.1.1 primary vs Advanced counts
  "engine": ["pygame"],                                       // W2.4 axis data — NOT rendered in Phase 0; the cards render what board 06 shows
  "dimension": "2D",                                          // data, not schema: a 3D template sets "3D"; nothing else keys off it; not rendered
  "distribution": [],                                         // derived from engines[*].exports — never authored
  "beta": false,                                              // the dungeon card ships un-badged (W2.1.4)
  "phase_labels": { /* §3.0-E — the same map the registry stamps */ }
}
```

### P.4.5 RESERVED — the `tuning` / bands section (C4) — **spec-only at P0-1; registry data at W2.1**

Status ladder: reserved slot at P0-1 → `status: "active"` with the key vocabulary + bands as
registry data at W2.1 (master row P2-W2.1) → user-widenable via `canon registry set` (pack-local,
journaled, its own History row, §3.0-A). `tune set/clear` (W2.1) mounts on `world update`'s write
core; no verb is pulled forward (master A-3). No tuning *semantics* live in Phase 0 — the two
reservations are this slot and P.7's protected wall.

**What Phase 0 stamps:** P0-10 stamps `{"schema": "canon-tuning/v0", "status": "reserved", "keys":
{}}` for **every** template, platformer included. `PackSpec.tuning_vocabulary` (the platformer's
`rule_overrides.json`) is recorded but **not read until W2.1**, which populates `keys` from it and
flips `status`. A dungeon template has no vocabulary file (verified: `examples/mazeworld_pack/` has
no `rule_overrides.json` and no movement/rules/combat file; the dungeon's constants live in the
external engine, `MW/config.py:68-142`, inventoried by the W2.0 pull-in row).

**Block format** (the shape W2.1 populates; fixed now so W2.1 does not change the format — P.9 R10
resolved):

```jsonc
"tuning": {
  "schema": "canon-tuning/v0",
  "status": "reserved",                 // P0-1: reserved; W2.1 flips to "active"
  "keys": {
    "<key>": {
      "type": "float",                  // float | int | bool | choice — open string
      "min": 15.0, "max": 60.0,         // numeric types only — rule_overrides.json "band": [lo, hi]
      "choices": ["contained", "open"], // choice type only; neither for bool
      "target": "movement",             // which manifest block the key lives in (open: movement | rules | combat | …; rules.py:221-224)
      "default": 40.0,                  // the template's shipped value
      "unit": "cells/s^2"               // optional doc string; NEVER interpreted by code
    }
  }
}
```

Notes: keys use `<container>.<key>` for nested manifest dicts (`flyer.hover_amp`); no
`widened_by` — the `registry` artifact's History is the audit (P.9 R9 resolved).

**Format worked example — what W2.1 will seed from** (the platformer's shipped `rule_overrides.json:3-10`
vocabulary; defaults `game_rules.json`, `movement.py:15-18`; illustrative, not stamped in Phase 0):

```json
{"gravity":            {"type": "float", "min": 15.0, "max": 60.0, "target": "movement", "default": 40.0, "unit": "cells/s^2"},
 "jump_height":        {"type": "int",   "min": 2,    "max": 5,    "target": "movement", "default": 3},
 "platform_drop_through": {"type": "bool", "target": "rules", "default": true},
 "flyer.hover_amp":    {"type": "float", "min": 0.0,  "max": 1.0,  "target": "rules",    "default": 0.4},
 "water_containment":  {"type": "choice", "choices": ["contained", "open"], "target": "rules", "default": "contained"}}
```

**Today's per-level override behaviour — descriptive only** (`validate_overrides`,
`rules.py:177-225`): unknown key → dropped with a note; wrong type → dropped; outside `[min, max]`
→ dropped; values coerced; `target == "movement"` routes to `PlayerMovementSpec`, everything else to
`GameRules`; the vocabulary is closed per pack as data (`rule_overrides.json:2`,
`load_override_vocabulary`). The W2.1 verb's refuse/warn behaviour is W2.1's to spec on the §3.0-A
core (doctrine 1 fail-closed, doctrine 10 loud).

**W2.1 guard:** W2.1 authors the full key set and bands (master row P2-W2.1, incl. ticket T1) from
`movement.py:14-46`, `game_rules.json` and `combat.json`; the slot must therefore accept nested
`<container>.<key>` names and `type: "choice"` + `choices` (above) — nothing else about those keys
is decided here.

**Engine/dimension agnosticism:** the block carries keys, types, bands, targets only; units are
free-text doc strings, `target` is an open string naming a manifest block, and both play surfaces
read the same `manifest.movement/rules/combat`. A 3D template seeds different keys under the same
shape.

**The widening idiom — `canon registry set`** (spec'd here, built for capabilities at P0-6, for
bands at W2.1): `canon registry set <pack> --set '{"tuning": {"keys": {"gravity": {"min": 10.0,
"max": 80.0}}}}' --actor …` — read effective → merge per the **P.7.4 merge rule** (deep-merge to
the leaf; `min`/`max`/`choices` are the only user-writable tuning leaves) → fail-closed validate the
merged document → write pack-local → journal `op: edit`, `artifact_id: registry`, `detail.kind:
registry_set`, `changed: {"<dotted path>": {from, to}}` → return `{changed}` / `no_change`. The same
verb serves capability enablement (P.7.4); `db define` / `db evolve` are P.7.5.

### P.4.6 `canon pack info` output (P0-3's deliverable; read by P0-5, P0-8, P0-9 and cradle)

One JSON document; cradle's `world_kind` **is `pack_type` verbatim** — no second vocabulary.
Worked dungeon example (values open):

```json
{"pack_type": "dungeon", "label": "Dungeon crawler", "capabilities": ["grid", "dialogue", "per_step_roll"], "vocab": ["floors", "rooms", "encounters"],
 "entities": {"npc": {"label": "NPCs", "id_field": "id", "layout": {"mode": "collection", "path": "npcs/npcs.json", "format": "array"}, "count": 8, "placeable": true, "schema_source": "pack"}, "…": {}},
 "grids": {"room": {"placements": {"npc_positions": {"kind": "npc", "wire": "entities"}, "…": {}}, "points": ["player_start", "door_position"], "dims": {"default": [40, 30]}}},
 "dialogue": {"condition_namespaces": ["has_item", "quest", "time", "player", "flag", "segment", "room", "scene", "event"], "scene_only_namespaces": ["actor"], "effects": ["gives_item", "takes_item", "gives_quest", "advance_quest", "set_flag"], "scopes": ["tree", "selector", "scene", "effects", "music"], "operands": {"…": {}}},
 "engine_evaluable_namespaces": {"tree": {}, "selector": {"quest": {"states": ["completed", "failed"]}}, "scene": {}, "effects": {}, "music": {}},
 "engine_evaluable_bindings": {"music": ["environment", "state", "screen"], "sfx": ["event", "environment"]},
 "engines": [{"id": "pygame", "primary": true}],
 "template": {"id": "dungeon", "version": "sha256:…"}}
```

`placeable` = the kind appears in some `grids.*.placements` entry; `count` = rows on disk; the two
`engine_evaluable_*` blocks are the primary engine's (P.2.4, P.5.3). A pre-registry pack (P.4.1
tier 2/3) answers from the code seed with `template.version: null`.

## P.5 Music / SFX row schemas

**Engine contract the rows must preserve** (`music_controller.py:7-13,100-115,161-190`;
`sfx_controller.py:23,65-72,94-144`; `music_director.py:16-29`; `manifest.py:19,62-79`): the engine
reads only the flat maps `manifest.music` / `manifest.sfx` = `{id: path}`; a missing key is a silent
no-op; a missing absolute path re-anchors by its `/data/` suffix or falls back to
`DATA_DIR/<dir>/<id>.mp3` — **so the file stem must equal the row id**. Music ids: `start_screen ·
maze_{env} · combat · puzzle_event · victory · game_over` (`game_over` plays once, the rest loop;
`combat`/`puzzle_event` are switched by `MusicDirector` on runtime state). SFX ids (28):
`weapon_{light|heavy|simple}_{swing|hit}`, `spell_{heal|damage_single|damage_multi|buff|reveal}_cast`,
`spell_damage_{single|multi}_impact`, `ambience_{env}` (reserved channel, loops) and ten literal
one-shots (`dice_roll, door_open, door_reveal, event_complete, item_drink, item_food, item_pickup,
item_tool, player_death, player_take_damage`) — keys derived by string concatenation from gameplay
state, no data table. Volumes are engine constants; no loop points, crossfade or per-track volume
exist. Canon's generic `AssetPhase` is skipped for dungeons by default (`compose.py:330`) and its
fixed SFX list omits 12 of the 28 ids with diverging durations (P.9 A7); `maze_*` ids stay.

**Design rules applied:** (1) row id == manifest key == file stem; `manifest.music/sfx` become a
**code-owned projection** `{id: file}` of the rows, rewritten on every attachment change (extends
`library import`'s manifest patch, `library.py:505-509`); (2) `role / category / mood /
binding.kind / trigger.kind` values are skeleton `choices` + registry `vocab` — data; (3) asset
plumbing is protected and mutates only through `asset generate | replace --target
music:<id>|sfx:<id>` and `library import --into music:<id>|sfx:<id>` (widening the existing verbs'
target grammar, not a new verb — P.9 A8); (4) bindings/triggers are open `{kind, value}` objects and
the engine's evaluable set is declared per engine (`evaluable_bindings`, P.4.3) so unsupported values warn, never
block; (5) `loop_start_s`/`loop_end_s` are reserved user-only fields the engine ignores; per-row
volume is not a v1 field (P.9 A5). Naming: the schema inventory's draft used `id` / `path` /
`asset_hash` / `prompt`; this paper adopts the audio inventory's `track_id`/`sfx_id`, `file` /
`file_hash`, `brief` (P.9 A1).

### P.5.1 `schemas/music.json` (skeleton — rolled fields only)

```json
{"schema_version": "1", "entity_type": "music", "fields": {
  "role":       {"choices": [["exploration", 5], ["combat", 1], ["event", 1], ["screen", 1]]},
  "mood":       {"choices": [["tense", 3], ["mysterious", 3], ["somber", 2], ["triumphant", 1], ["serene", 2], ["ominous", 3]]},
  "loop":       {"lookup": [["exploration", true], ["combat", true], ["event", true], ["screen", true]], "depends_on": "role"},
  "duration_s": {"lookup": [["exploration", [110, 130]], ["combat", [110, 130]], ["event", [110, 130]], ["screen", [30, 130]]], "depends_on": "role", "lookup_ranges": true}
}}
```

Seed values mirror the engine's generator (2-minute tracks, `game_over` 30 s, `music_client.py:6-13`;
`asset.py:226-237`); `game_over`'s `loop: false` is set on the seed row, not the table. Weights are
placeholders for user tuning.

### P.5.2 `schemas/sfx.json`

```json
{"schema_version": "1", "entity_type": "sfx", "fields": {
  "category":   {"choices": [["weapon", 6], ["spell", 7], ["ambience", 5], ["player", 2], ["item", 4], ["door", 2], ["event", 1], ["ui", 1]]},
  "loop":       {"lookup": [["weapon", false], ["spell", false], ["ambience", true], ["player", false], ["item", false], ["door", false], ["event", false], ["ui", false]], "depends_on": "category"},
  "duration_ms": {"lookup": [["weapon", [500, 2000]], ["spell", [1000, 2000]], ["ambience", [12000, 15000]], ["player", [500, 2000]], ["item", [500, 1000]], ["door", [1500, 2000]], ["event", [1000, 1500]], ["ui", [500, 1000]]], "depends_on": "category", "lookup_ranges": true}
}}
```

Bands come from MazeWorld's prompt spec and fixed table (`sfx_client.py:44-130`,
`claude_prompts.py`); the 500 ms floor is the ElevenLabs limit (`audio_phases.py:45-51`).
**Resolved (was P.9 A4):** `lookup_ranges` rolls a band only when every element is an `int`
(`core.py:312-316` — a float pair is returned verbatim, which would hand `SFXBackend.generate` a
list), so sfx rows store integer `duration_ms` and the producer call converts to seconds; music
`duration_s` bands are already ints.

### P.5.3 Registry entries, protected fields, attachment contract

```json
{"music": {"label": "Music", "layout": {"mode": "collection", "path": "music/music.json", "format": "array"}, "schema": "schemas/music.json", "id_field": "track_id",
   "llm_fields": ["title", "brief"], "code_fields": ["track_id", "binding", "file", "file_hash", "duration_measured_s"],
   "user_fields": ["tags", "notes", "loop_start_s", "loop_end_s"],
   "protected": ["track_id", "artifact_id", "file", "file_hash", "duration_measured_s", "provenance_hash", "parents", "status", "review_status", "library_ref"],
   "containers": ["binding"],
   "asset": {"field": "file", "hash_field": "file_hash", "kinds": ["audio"], "targets": ["music:<track_id>"]},
   "vocab": {"binding_kinds": ["environment", "state", "screen"]}, "phase_label": "audio:music", "count_key": null},
 "sfx":   {"label": "Sound effects", "layout": {"mode": "collection", "path": "sfx/sfx.json", "format": "array"}, "schema": "schemas/sfx.json", "id_field": "sfx_id",
   "llm_fields": ["title", "brief"], "code_fields": ["sfx_id", "trigger", "file", "file_hash", "duration_measured_s"],
   "user_fields": ["tags", "notes"],
   "protected": ["sfx_id", "artifact_id", "file", "file_hash", "duration_measured_s", "provenance_hash", "parents", "status", "review_status", "library_ref"],
   "containers": ["trigger"],
   "asset": {"field": "file", "hash_field": "file_hash", "kinds": ["audio"], "targets": ["sfx:<sfx_id>"]},
   "vocab": {"trigger_kinds": ["event", "environment"]}, "phase_label": "audio:sfx", "count_key": null}}
```

(`loader` — `load_music_row` / `load_sfx_row` — is seed-only per P.3.1 and never stamped.) Engine
evaluability of binding/trigger kinds is the engine entry's **`evaluable_bindings`** block,
`{"music": ["environment","state","screen"], "sfx": ["event","environment"]}` — a sibling of
`evaluable_namespaces` (P.2.4, P.4.3), keyed by row kind, because binding kinds are not condition
namespaces (P.9 C1). The validator warns when a row's `binding.kind`/`trigger.kind` is outside the
primary engine's list; `pack info` surfaces it as `engine_evaluable_bindings` (P.4.6).

| field | owner | `db update` | note |
|---|---|---|---|
| `track_id` / `sfx_id` | code | refused | on-disk id = engine key; `maze_<env>` / `ambience_<env>` expanded from `manifest.environments` at create |
| `title` · `brief` | LLM, user-editable | yes | `brief` is the generation prompt; `asset generate` uses it unless `--prompt` overrides (mirrors `music_prompt_override`) |
| `role`/`category`, `mood`, `loop`, `duration_s` (music) / `duration_ms` (sfx) | rolled | yes, off-band warns | the **requested** length (int; the producer call converts `duration_ms` to `duration_seconds`); `loop` → `SFXBackend.generate(loop)`; for music it drives the engine's `loops` arg once the pulled-in engine reads rows |
| `binding` (music) `{kind, value}` — `{"kind":"environment","value":"ruins"}`, `{"kind":"state","value":"combat"}`, `{"kind":"screen","value":"game_over"}` | code-seeded, user-editable | knob-wise | warns when `kind` is not engine-evaluable or an `environment` value ∉ `manifest.environments` |
| `trigger` (sfx) `{kind, value}` — `{"kind":"event","value":"door_open"}`, `{"kind":"environment","value":"ruins"}` | code-seeded | knob-wise | v1 engine derives keys by concatenation, so `trigger.value == sfx_id` for `event` kinds until the pull-in reads rows |
| `tags`, `notes`, `loop_start_s`, `loop_end_s` | user-only | yes | reserved / library metadata; engine ignores |
| `file`, `file_hash`, `duration_measured_s` | asset plumbing | **refused** | `file` pack-relative (`music/<id>.<ext>`, ext by sniff as `audio_phases.py:70-78`); hash from `adapter.write_binary`; `duration_measured_s` needs a decoder (P.9 A6) |
| `artifact_id`, `provenance_hash`, `parents`, `status`, `review_status`, `library_ref` | provenance | refused | identical to `_PROTECTED_FIELDS` semantics |

Artifact ids `music:<track_id>`, `sfx:<sfx_id>` (one per row — dungeon tracks reroll independently;
the platformer's per-stage `audio:<stage>` is not copied). Journal: row edits `detail.kind:
db_update`; attachments `op: generate|regenerate` (`kind: asset_generate`) and `op: import`;
`genKind: "audio"`. Verb touchpoints, all extensions: `db *` registry dispatch adds the two kinds
(P0-6); `asset generate` `_parse_target` gains `music:`/`sfx:` and runs a per-row producer call
with `brief`, `duration_s`, `loop`; `asset replace` widens to audio bytes (the upload path that does
not exist today); `library.KINDS` already has `audio`; `prompt show --kind music|sfx`; `pack info`
lists the kinds + vocab.

### P.5.4 Seed rows and the manifest projection

Seed at create: music = the six-track vocabulary with `maze_<env>` per environment; sfx = the 28-id
catalog with `ambience_<env>` per environment (the registry seed replaces both `sfx_client.py`'s
and `asset.py`'s hardcoded lists — P.9 A7). Fixed briefs are lifted verbatim from
`music_client.py:41-74` / `sfx_client.py:44-130`; LLM-authored briefs (combat, `maze_*`, weapons,
spells, ambience) come from an LLM step ported from `claude_prompts.py` — that port belongs to
P0-10, not this schema.

```json
{"track_id": "maze_ruins", "artifact_id": "music:maze_ruins", "title": "", "brief": "", "role": "exploration", "mood": "mysterious", "loop": true, "duration_s": 120,
 "binding": {"kind": "environment", "value": "ruins"}, "loop_start_s": null, "loop_end_s": null, "tags": [], "notes": "", "file": "", "file_hash": "", "status": "pending"}
```
```json
{"sfx_id": "ambience_ruins", "artifact_id": "sfx:ambience_ruins", "category": "ambience", "loop": true, "duration_ms": 15000,
 "trigger": {"kind": "environment", "value": "ruins"}, "title": "", "brief": "", "tags": [], "notes": "", "file": "", "file_hash": "", "status": "pending"}
```

`status` uses the existing `ArtifactStatus` vocabulary (`artifacts.py:24-36`): seed rows are
`pending` (nothing generated yet, `file` empty); `asset generate` stamps `done`; `db update` stamps
`user_edited` (the P.1 precedent).

After any attachment write the writer regenerates `"music": {"maze_ruins": "<path>", …}` and
`"sfx": {"ambience_ruins": "<path>", …}`; rows with empty `file` are omitted (a missing key is
silence). Absolute vs pack-relative path form is P.9 A2. Rows file and projection are both
journaled; CAS snapshots the collection file (file-level restore granularity, accepted §12).

**Seam guard (Phase 2 attaches here; no design now):** `binding`/`trigger` stay open `{kind,
value}` objects to which additive keys may be appended; new kinds join as vocabulary values; music
sections, when they come, carry P.2.5's subset of the one grammar. Nothing here encodes a 2D cell
axis, a stage, or an engine — the platformer's `MusicSection.start/end` cell ranges remain a
platformer `Level` concern.

## P.6 Room grid ↔ level-editor mapping

### P.6.1 Encodings on both sides

**Maze side** (`layout/__init__.py:41-67`; `layout/maze.py:34-35,79`; `placement.py:37,125-211,
252-316,357-364`; `MW/config.py:68-77`; `MW/src/models/maze.py:29,100-102,114,171,234-238,
435-448`; `MW/main.py:158-198`; `game_controller.py:207-226`):

| fact | value |
|---|---|
| dims | 40×30 seeded (`compose.py:321`); `maze.json.width/height` + `manifest.maze_width/height`; **the engine does not read dims** — it sizes from `SCREEN_WIDTH // GRID_SIZE` constants (800/20 = 40, (700−100)/20 = 30, `GRID_SIZE` 20 px); its own writer omits `width/height/layout_type/extra` |
| file | `rooms/{map_id}/maze.json` via `write_per_map` (Pydantic `model_dump`); `rooms.json[id].maze_ref` points at it; cradle has no `maze_ref` follower today (P0-5 is the first real spatial read) |
| cell vocabulary | `1` wall (drawn WHITE) · `0` open path (BLACK) · `-1` event tile (cell zeroed after the event) · `>= 2000` item on the cell (`registry.is_item(cell)`) · `-2` door — **runtime-only**, written by `place_door_tile` after reveal, never emitted. Observed emitted values: `[-1, 0, 1, <item id>]` |
| coordinates | `(x, y)` = `(col, row)`, origin top-left, y-down, `grid[y][x]` on both sides; screen `x*GRID_SIZE`, `y*GRID_SIZE + HUD_HEIGHT` |
| `player_start` | `[x,y]`, default `(1,1)`; carving starts here |
| `door_position` | `[x,y]`; default `(w−2, h−2)`, moved by code to an open cell 4-adjacent to the gate event; marker only — cell stays `0` until runtime reveal |
| `door_revealed` · `gate_encounter_id` | runtime bool · id of the combat event guarding the door; cross-file side effect: `events.json` gains `is_gate`/`is_climax_boss` and the boss id is prepended to `monster_ids` |
| `npc_positions` | `{"<npc_id as str>": [x,y]}`; the NPC's own row `x/y` are ignored |
| `event_positions` | `[{x, y, event_id}]` (+ grid `-1`); authoritative; row `x/y` fallback only when empty |
| `item_placements` | `[{x, y, item_id, name, portrait_prompt, profile_image}]` (+ grid `item_id`); **no engine reader** — items come from grid values; cradle reads it |
| `quest_ids` | `[int]`, not spatial |
| monsters | **not placed** — no monster branch in `_place_one`; monsters reach a room only through combat events' `monster_ids` and the `rooms.json` lore bucket (M7) |
| placement RNG | one shuffled open-cell list (excluding start/door) consumed NPCs → events → items; seed `derive_seed(seed, "placement", map_id)`; layout `derive_rng(seed, "maze_layout", map_id)`; both pure code, no LLM |

**Platformer side** (`platformer.py:83-103,210-258,298-300`; `platformer_read.py:30-34,369-534`;
`platformer_write.py:28,62-66,101-249,463-574`; `drawLevel.ts:60-189,383-500`; `Dock.tsx:67-95`;
`LevelCanvas.tsx:41-47,255-280`): layers `collision.npz` (int8 tile-type ids: `empty=0`,
solids/one_way 1–9, hazards 10–19, volumes 20–127 — categories in code, values in
`tile_types.json`), derived `terrain.npz` / `background.npz`, sparse `hazards/triggers/foreground
.json` `[{x,y,type,params}]`, `entities.json` `[{enemy_id,x,y,variant}]`, `items.json`
`[{item_id,x,y,source}]`, markers `spawn`/`exit` `[x,y]`, dims `grid_width/grid_height` +
`layout_axis`. The export bundle (`LevelBundle`) carries `grids{collision,terrain,background}`,
`tileset{slots[{index,tile_type,name,px_region,collision,params}], palette}`, `tiles_by_type`,
`entities[]`, `items[]`, `triggers[]`, `spawn`, `exit`, `revision`, `last_change`, `tile_px`,
`variants`, music fields. Blocks mode draws `collision[y][x]` → `tileColor` via
`PALETTE_KEY[slot.name]` (`floor→ground, wall→wall, platform, spike→danger, water, empty→null`);
handles from `entities/items/triggers/spawn/exit`; only `triggers[].type === "checkpoint"` is drawn;
`drawBounds` (floor/kill plane/DROP) is opt-in gravity chrome; all edit callbacks are optional props
(no callbacks + `brush=null` = a pan/zoom viewer). Dock tabs are literal `tiles|enemies|items|play`
with rows from `listEntityRows(world, "enemies"|"items")`. `apply-edit` accepts a partial dict of
`entities / items / triggers / hazards / foreground / spawn / exit / music_path (+ music_hash,
stored on `level.json`, `platformer_write.py:203-208`) / music_sections` (no-op writes not journaled; `kind: enemy_move | item_move | <layer>_change | level_edit`);
`import-grids` takes `{"collision": [[…]]}` (int8 cast, ≥ 4×4, every value a registered tile type,
re-derives terrain/background/hazards, resize clamps placements; `kind: terrain_paint`).

### P.6.2 The mapping table

| # | maze concept | level-editor concept | read (P0-5, one export, both shapes) | write (P0-6/P0-8) | status |
|---|---|---|---|---|---|
| 1 | `grid == 1` wall | collision cell, category `solid` | pass through as tile-type `1` | `import-grids` → `maze.json.grid` | REUSE (needs the P.6.3 tile registry) |
| 2 | `grid == 0` path | collision cell, category `empty` | pass through as `0` | same | REUSE — eraser vs paintable "floor" swatch is P.9 G1 |
| 3 | `grid == -1` event tile | **not a tile** → an event placement | lift to a placements list; cell reads `0` | re-stamp `-1` from `event_positions` on write | NEW (event brush + a draw branch beyond `checkpoint`) |
| 4 | `grid >= 2000` item id | item placement | lift to `items[]` from `item_placements` (cross-checked against the grid, which the engine trusts); cell reads `0` | re-stamp `item_id` into the grid + rewrite `item_placements` | REUSE (Dock typeId `"items"` already matches) |
| 5 | `npc_positions {id:[x,y]}` | entity placement | `entities[]` one per key, sorted by id (stable index); `name`/`archetype` ← `npcs.json` `name`/`type`; sprite ← portrait if any | `apply-edit` `entities` → `npc_positions` dict | NEW tab "NPCs", reuse mechanics |
| 6 | `event_positions [{x,y,event_id}]` | placement typed by event `type` + gate flags | `triggers[]`-shaped `{x,y,type,params:{event_id,is_gate,is_climax_boss}}` or a dedicated list (P.9 G3) | `apply-edit` → `event_positions` + grid `-1` | NEW tab "Events" |
| 7 | `player_start` | `spawn` marker (white) | copy | `apply-edit.spawn` → `player_start` | REUSE |
| 8 | `door_position` (+ `door_revealed`) | `exit` marker (green) | copy | `apply-edit.exit` → `door_position` (cell stays `0`) — P.9 G5 | REUSE |
| 9 | `gate_encounter_id` | the flagged event placement adjacent to the door | `params.is_gate` / room passthrough | code-owned (`_designate_gate`); editing is a cross-file write to `events.json` | read-only tray field in P0-8 |
| 10 | `quest_ids` | not spatial | room passthrough / tray | `db update` on the room row | passthrough |
| 11 | `environment`, `environment_name` | `display_name`; palette hint | `display_name ← environment_name`; palette from data (P.9 G2) | `db update` room row | passthrough |
| 12 | `width`/`height` | `grid_width`/`grid_height` | copy | resize **disabled-with-reason** in v1 (engine constants) | gate write |
| 13 | monsters (combat `monster_ids`) | placement **via an encounter**: dropping a monster on a cell creates or targets the combat event there and adds it to `monster_ids` (Dock tab "Monsters") | encounter-typed `triggers[]` entry carrying `params.monster_ids` | `apply-edit` → `event_positions` + grid `-1` + `events.json[…].monster_ids` (cross-file, one journal event per file) | NEW (P.9 G4 — decided 2026-09-01) |
| 14 | one `maze.json` | nine step files | `revision` ← sha over `maze.json` (+ `rooms.json` entry?); `last_change` ← journal by a room artifact-id prefix (P.9 R1) | one file write, one CAS snapshot, per-field diff | needs the id grammar |
| 15 | terrain/background/hazards/foreground/props/backdrop/music/variants/tilesheet | platformer-only layers | emit `terrain = collision`, zeros/empties, `backdrop null`, `variants []`, `tilesheet_path_abs null` — blocks mode never reads them | never written for a room | art/overlay modes disabled-with-reason |
| 16 | `layout_axis`, bounds | gravity chrome | omit; `showBounds` off for `world_kind = dungeon` | — | gate by capability |
| 17 | `tile_px` | `slots[0].px_region[2]` else 32 | 20 (`GRID_SIZE`) as template data | — | data |

### P.6.3 Tile registry, read-path translation, write-path mapping

**Dungeon tile registry** — template data, sibling of `tile_types.json` with the same row shape;
event/item cell values are **not** tile types and never enter it; `-2` never appears on disk:

```json
{"tiles": [
  {"id": 0, "name": "empty", "category": "empty", "color_role": "background"},
  {"id": 1, "name": "wall",  "category": "solid", "color_role": "wall"}
]}
```

**Read path (P0-5):** (1) dispatch by `pack_type`/`GridKind`, never by sharing
`export_level_bundle`'s body — it hard-requires `manifest.stages` and `tileset/<stage>/manifest.json`;
the shared thing is the **output shape**, so `LevelCanvas`/`drawLevel` take it untouched; (2) grid
normalisation `collision[y][x] = 1 if v == 1 else 0`, every `-1` / `>= 2000` cell becomes a
placement — cross-check against `item_placements`/`event_positions` and warn, never block, on
disagreement, **rendering the engine's truth**: items from grid cells `>= 2000` (names joined from
`items.json`; `item_placements` only fills metadata), events from `event_positions`; the
disagreeing side is reported in the bundle's `warnings[]` and repaired by the next write; (3)
placements → lists with stable indices; (4) `spawn ← player_start`, `exit ←
door_position`; `door_revealed`, `gate_encounter_id`, `quest_ids`, `environment*`, monsters bucket
ride an additive `room` passthrough object; (5) synthesised tileset from the registry with
`px_region = [0,0,tile_px,tile_px]`, `tilesheet_path_abs = null`; (6) `revision` over `maze.json`
bytes, `last_change` by room artifact-id prefix; (7) `tile_px = 20`, `actor_scale = 1`, `water_alpha
= 1`, `variants = []`, no music fields; (8) `world_kind` (P0-3) forces `mode="blocks"`, `showBounds`
off, Dock tabs from the `GridKind.placements` kinds in `pack info` (P.3.2: npc, event, item).

**P.6.3a Room bundle — worked example.** Verb: `canon grid export <pack> --level room_0` (`level
export` stays the alias; registry dispatch on `grids`). `stage_id` is the empty string — never a
synthetic stage (M12); `LevelBundle.stage_id` stays `string` (`drawLevel.ts:60-62`). `revision` =
`sha256(maze.json bytes ‖ canonical rooms.json[id])` (P.9 R1); `last_change` = the newest journal
event with artifact prefix `room:room_0/`.

```json
{"level_id": "room_0", "stage_id": "", "display_name": "The Sunken Ruins",
 "revision": "sha256:…", "revision_short": "…", "last_change": null,
 "grid_width": 40, "grid_height": 30, "spawn": [1, 1], "exit": [10, 19],
 "tile_px": 20, "actor_scale": 1, "water_alpha": 1, "variants": [],
 "grids": {"collision": [[0, 1, "…"]], "terrain": "<= collision>", "background": "<zeros>"},
 "tileset": {"slots": [{"index": 0, "tile_type": 0, "name": "empty", "px_region": [0, 0, 20, 20], "collision": "empty", "params": {}},
                       {"index": 1, "tile_type": 1, "name": "wall",  "px_region": [0, 0, 20, 20], "collision": "solid", "params": {}}],
             "palette": {"background": "<cradle theme token>", "wall": "WALL_COLORS[environment] (P.9 G2)"},
             "render_filter": "nearest", "tilesheet_path_abs": null},
 "tiles_by_type": {"0": "empty", "1": "wall"},
 "entities": [{"enemy_id": "1000", "x": 35, "y": 16, "variant": null, "name": "…", "archetype": "StaticNPC", "size": 1, "placeholder_color": "<ENV_TO_COLOR[env]>", "sprite_path_abs": null}],
 "items":    [{"item_id": "2000", "x": 24, "y": 8, "source": null, "name": "…", "kind": "<category>", "placeholder_color": "…", "sprite_path_abs": null}],
 "triggers": [{"x": 1, "y": 13, "type": "combat", "params": {"event_id": 3000, "is_gate": false, "is_climax_boss": false}}],
 "hazards": [], "foreground": [], "props": {}, "backdrop": null, "music_path": "", "music_sections": [],
 "warnings": [],
 "room": {"environment": "ruins", "environment_name": "…", "door_revealed": false, "gate_encounter_id": 3003, "quest_ids": [4000], "monsters": ["<lore stubs>"]}}
```

`entities[].enemy_id` / `items[].item_id` are the shared bundle's literal key names (P.9 G9)
carrying `str(row id)`; `archetype ← npc.type`; `sprite_path_abs ← profile_image` when set.
`size` is the number `1` (the shared bundle's `LevelEntity.size` is a scalar the renderer
scales by — corrected 2026-09-01 at P0-5).

**Write path (P0-6/P0-8) — the wire shape stays the platformer's sparse shape; the `GridKind`
writer maps it** (§6: "Room placements map onto apply-edit's sparse shape"; one `apply-edit`, not
a `dungeon_*` pair):

| sparse key (unchanged) | room writer effect on `maze.json` | journal kind (open) |
|---|---|---|
| `entities: [{<id_key>, x, y}]` | `npc_positions = {str(id): [x,y]}` | `npc_move` |
| `items: [{item_id, x, y}]` | rewrite `item_placements` (name from `items.json`) **and** clear old / stamp new `grid[y][x] = item_id` | `item_move` |
| `triggers`-shaped `[{x,y,type,params:{event_id,…}}]` (or a dedicated key, P.9 G3) | rewrite `event_positions`; clear old / stamp new `grid[y][x] = -1` | `event_move` |
| `spawn` / `exit` | `player_start` / `door_position` | `level_edit`-style from/to |
| `collision` via `import-grids` | `grid` cells 0/1 only; placements re-stamped after paint (wall over a placement: P.9 G7); **no** int8 cast, **no** derived layers, **no** resize | `terrain_paint` |

The id key in `entities[]` comes from the EntityKind's `id_field` — the writer, not the wire,
resolves it (P.9 G9). Fail-closed before write: ids exist in `npcs/items/events.json`; cells inside
`[0,w)×[0,h)`; placements on open cells (never start/door). Reachability start→door is a candidate
warning (doctrine 10), not a blocker — no such validator exists today.

**Per-step rolls (all code-only, $0 — no spend dialog, doctrine 3):** whole room = `MazeLayoutPhase`
+ `MazeworldPlacementPhase` (also rewrites `events.json` gate flags → two journaled artifacts);
🪄 layout = `generate_maze(width, height, rng)`; 🎲 npcs / 🎲 events / 🎲 items = the `_place_one`
branches — single-kind rolls need a per-kind sub-seed `derive_seed(base, "placement", map_id,
kind)` (P.9 G8); 🎲 monsters = re-roll the selected encounter's `monster_ids` through the placement phase's
sampling (P0-8 builds the per-encounter roll; P.9 G4 decided 2026-09-01). Platformer analogues: `level generate`,
`regenerate`/`generate-terrain`, `place-enemies`, `place-items`.

### P.6.4 Read-only (P0-5) → writable (P0-8)

| surface | P0-5 ("maze renders in Blocks view") | P0-8 ("edit every type end-to-end") |
|---|---|---|
| grid | `mode="blocks"`, `showGrid` on, `showBounds` off; no `onPaint/onFill/onErase`, `brush=null` | paint wall/open (`paint\|fill\|erase`) → `import-grids` dispatch; resize disabled-with-reason |
| NPC / event / item placements | drawn; hit-testable + selectable for the tray only | drag / place from Dock tabs / erase cascade → `apply-edit` dispatch; tabs from `pack info` kinds |
| spawn / door markers | drawn | draggable → `player_start` / `door_position` (door: P.9 G5) |
| tray | passthrough facts (environment, gate link, quest ids, monsters bucket) | RowEditor-linked edits via `db update` on the room row |
| per-step rolls | absent or present-disabled with reason | 🪄 layout / 🎲 npcs / 🎲 events / 🎲 items / whole room, $0 estimates |
| History / restore | `last_change` chip needs the id grammar (P.9 R1); `LineagePanel` pattern | restore writes a new version through the same room writer |
| art / overlay / music lane / bounds / variants | disabled-with-reason (no tilesheet, no axis) | unchanged |
| engine parity | `data_canon/` byte-untouched (pure projection) | every write keeps `maze.json` engine-readable: the P.6.1 keys, `-1`/item-id stamps, `[x,y]` markers, 40×30 |

### P.6.5 Mismatches found

| # | check | finding |
|---|---|---|
| M1–M3 | row-major, origin, pair order | all match: `grid[y][x]`, top-left y-down, `[x, y]` pairs on both sides — no transpose |
| M4 | value semantics | ids coincide (0/1) but categories invert: maze `0` = walkable/`empty`, `1` = wall/`solid`; platformer `0` = air, `1` = floor — handled entirely by tile registry data, no code branch |
| M5 | non-tile grid values | maze carries `-1` and ids ≥ 2000; `import_level_grids` casts to int8 (2000 wraps) — must lift/re-stamp |
| M6 | gravity chrome | `drawBounds`, `one_way` strip, `layout_axis`, `music_sections`, sprite feet-anchoring are platformer assumptions; blocks mode + `showBounds=false` avoids all |
| M7 | **PRD vs code on monsters** | §3 says "drag placements (npcs, monsters, items, events)" and "🎲 monsters"; code places no monsters — they ride combat events. **Decided 2026-09-01:** the UI places monsters through encounters (row 13); P0-8 builds the encounter-at-cell path (P.9 G4) |
| M8 | door | platformer `exit` is a reached cell; maze door is a marker moved adjacent to the gate by code — free drag may break "must pass the boss" (P.9 G5) |
| M9 | dims source of truth | platformer resizable via `level.json`; maze dims are engine constants until the W2.0 pull-in — resize disabled-with-reason |
| M10 | minimum size | `import_level_grids` rejects < 4×4 — irrelevant at 40×30 |
| M11 | id type | maze ids are ints (`npc_positions` keys are `str(int)`); platformer slugs; `EntityLink`/`RowEditor` already `String(id)` |
| M12 | stage | platformer ids/paths need a `stage_id`; rooms have none and must not invent one (no "Floors" vocabulary, master §2) — P.9 R1 |

## P.7 `canon world update` field list

**Exclusions by decision:** `manifest.movement`, `manifest.combat`, `manifest.rules` are **not** in
this list — Phase 2's `canon tune set` mounts on the same write core (§3.0-A; master S3). Row-level
fields go through `db update`, grid fields through grid verbs, map layout through `world map-edit`
(`platformer_write.py:1440-1540`). Row P0-6 builds the verb on the reusable core (resolve → wall →
fail-closed validate → journal → CAS); this paper fixes the field list and the wall.

Sources: platformer `world.json` (`World(ArtifactMeta)`, `platformer.py:150-178`; writers
`phases.py:386-396`, `art_phases.py:1586-1594`) and `manifest.json` (`compose.py:118-127,233-320`,
rebuilt on every resume) — **UNVERIFIED against a generated tree** (`tests/fixtures/plat_ember/`
holds only layout attempts); dungeon `manifest.json` (`manifest.py:186-217`; DC observed),
`world_bible.json` (`manifest.py:81-129` + `phases.py:208-260`), `story/story.json` (the same story
dict — the engine reads **this** file), `narrative.json` (`narrative.py:73-74`).

### P.7.1 Editable fields per pack type (v1)

| pack_type | field (dotted `--set` key) | file(s) written | authored by | note |
|---|---|---|---|---|
| platformer | `title` | `world.json.title` + mirror `manifest.json.world` | LLM at create; user via `world new --name` | replaces `_set_world_name` (`main.py:813-826`), which writes both files **without a journal event** — a doctrine-1 gap this closes (P.9 R13) |
| platformer | `unlock_rules` (`unlock_rules.type` …) | `world.json.unlock_rules` + mirror `manifest.json.unlock` | code default `{"type":"linear"}` | value vocabulary is data ("Unlock policy is data", `compose.py:253`); fail-closed = must be a dict |
| dungeon | `story.title` | `world_bible.json.story.title` + `story/story.json.title` + mirror `manifest.json.story_title` | LLM (StoryPhase) | three files, one event batch |
| dungeon | `story.synopsis`, `story.climax`, `story.escalation_arc`, `story.final_boss_name`, `story.final_boss_lore`, `story.key_npc_names` | `world_bible.json` + `story/story.json` | LLM | |
| dungeon | `story.faction.name` (+ mirror `manifest.faction_name`), `story.faction.{description,history,leader,aesthetic,threat_level}` | as above | LLM | `faction_id` protected |
| dungeon | `story.beats.<room_id>.{summary,faction_presence,escalation,boss_name,boss_lore}` | as above | LLM | addressed by `room_id`, never by index |
| dungeon | `narrative.synopsis`, `narrative.game_over`, `narrative.victory`, `narrative.room_intro_<room_id>` | `narrative.json` | LLM (NarrativePhase) | engine-read prose |

No user-only fields in v1: every editable field is LLM-authored at create and user-corrected after
(the correction-pair signal). `map_nodes/map_edges/map_locked` keep their own verb.

**Where the table lives — `PackSpec.world_fields` (stamped, P.3.4)**, never a Python table: dotted
key → `{file, path, mirrors[]}`. The core resolves the key here, writes `file.path`, then each
mirror, one journal event per file (P.7.3). Worked entries:

```json
{"title":        {"file": "world.json", "path": "title", "mirrors": [{"file": "manifest.json", "path": "world"}]},
 "story.title":  {"file": "world_bible.json", "path": "story.title",
                  "mirrors": [{"file": "story/story.json", "path": "title"}, {"file": "manifest.json", "path": "story_title"}]},
 "story.beats.<room_id>.summary": {"file": "world_bible.json", "path": "story.beats[room_id=<room_id>].summary",
                  "mirrors": [{"file": "story/story.json", "path": "beats[room_id=<room_id>].summary"}]},
 "narrative.synopsis": {"file": "narrative.json", "path": "synopsis", "mirrors": []}}
```

**Address grammar:** segments are dotted; `<list>[<key>=<value>]` selects the one list item whose
`<key>` equals `<value>` (`beats` is a list keyed by `room_id`, `phases.py:158-167`); a numeric
index is **never** accepted for world fields. Keys with a `<room_id>`-style placeholder are
templates expanded against the pack's rooms at resolve time. Stage-level
fields (`stage.json` `theme/biome/effects`) are out of v1 (P.9 R11); room-level fields go through
`db update --type room` (`world_bible.json.rooms.<id>` is the room record, not world-level).

### P.7.2 The protected wall (world scope)

Union across pack types; matched on the last dotted segment like `_PROTECTED_FIELDS`, plus
whole-container refusal. **The wall is a parameter of the shared core** (`write_core(pack, target,
changes, wall=…, field_table=…)`), never a constant inside it: `world update` passes this union and
`PackSpec.world_fields`; `tune set` (W2.1) passes its own wall that admits `movement/rules/combat`
keys and excludes everything else, plus the tuning key table. The reusable part is the matcher
(last-dotted-segment + whole-container refusal) and the pipeline, not the set — the second format
reservation.

**Container vs leaf:** a container is a dict-valued field — refused whole, written knob-wise
(`unlock_rules` is a container: write `unlock_rules.type`; the bare key is refused). Lists of
scalars (`story.escalation_arc`, `story.key_npc_names`) are **leaves**: replaced wholesale,
journaled as one `{from, to}` diff. Lists of dicts (`story.beats`) are addressed by key (P.7.1).

- **Identity:** `artifact_id`, `seed`, `story_seed`, `world_id`, `game`, `pack_type`, `faction_id`,
  `primary_antagonist_faction_id`, `room_id`, `stage_ids`, `edges`.
- **Provenance:** `provenance_hash`, `parents`, `status`, `review_status`, `generated_at`,
  `validation_report`, `generation_stats`, `warnings`, `canon_version`.
- **Generation-owned / derived:** `stages`, `levels`, `world_map`, `enemies`, `items`, `rooms` (both
  files), `num_rooms`, `environments`, `environment_names`, `*_count`, `portraits_generated`,
  `player_classes`, `entity_index`, `story_npcs`, `story_items`, `story_monsters`, `beats[].room_id`,
  `maze_width`, `maze_height`.
- **Engine-owned:** `game_mode`, `movement`, `rules`, `combat` (Phase 2 `tune set`), `tiles`,
  `graphics`, `variants`, `palettes`.
- **Asset plumbing:** `splash`, `splash_path`, `splash_hash`, `*_portrait`, `music`, `sfx`, `audio`,
  `props`.
- **Other-verb-owned:** `map_nodes`, `map_edges`, `map_locked` (`world map-edit`).

### P.7.3 Artifact ids and detail kinds (pre-A6 shape; A6's fields are additive)

Existing families (`platformer_write.py:62-66,849,1012,1268,1417,1529`): `enemy:<id>` /
`item:<id>` (= `<kind>:<id>`), `player` (bare target: replace/restore/assign/animate),
`tileset:<stage>`, `backdrop:<stage>`, `stage:<stage>` (publish/unpublish), `schema:<type>`,
`level:<stage>/<id>/<step>`, `world` (the world-map edit), `engine:godot`, `audio:<stage>`. One
journal event + CAS snapshot per written file (the `apply_level_edit` per-step pattern).

| written file | `artifact_id` | `detail` |
|---|---|---|
| `world.json` (platformer) / `world_bible.json` (dungeon) | `world` (reuse — the family already means "the world record") | `{"kind":"world_update","changed":{"<field>":{"from":…,"to":…}}}` |
| `manifest.json` mirror | `manifest` | `{"kind":"world_update","mirror_of":"world","changed":{…}}` |
| `story/story.json` mirror (dungeon) | `story` | same, `mirror_of: "world"` |
| `narrative.json` (dungeon) | `narrative` | `{"kind":"world_update","changed":{…}}` |
| `.canon/registry.json` | `registry` | `{"kind": "registry_set" \| "capability_set" \| "db_define" \| "db_evolve", "changed": {"<dotted path>": {"from": …, "to": …}}}` — one shape for every registry event (the `db_schema` precedent); never a list |

`op: "edit"`, `source: "user"`, `actor` from `--actor` (I6). Dungeon grid events use the `room:`
family per P.9 R1.

### P.7.4 Capability-enablement idiom slot (§5.1a; built at P0-6; proof case W2.2)

Idiom = `db schema --set`'s, through `canon registry set <pack> --set '{"capabilities":
{"dialogue": true}}' --actor …` (the same verb as band widening, dispatching on top-level key).
Fail-closed rules for `dialogue` on a platformer pack: the capability id must have an implementing
seed in core (a default `DialogueSpec`); the registry gains a `dialogue` block seeded from it and
every `engines[]` entry gains the empty `evaluable_namespaces` block (P.2.4); an `EntityKind` with
`storage.on` (an `npc`-like kind) must exist or be `db define`d first — otherwise refuse with the
reason (doctrine 4). Journal `artifact_id: registry`, `detail.kind: capability_set`, `changed:
{"capabilities.dialogue": {"from": false, "to": true}}`. `db define` / `db evolve` write the same
file through the same core (P.7.5). Disable semantics: P.9 R12.

**Merge rule for `registry set`** (`db schema --set` replaces each named entry wholesale,
`ops.py:1091-1103` — the registry verb does not): JSON objects **deep-merge to the leaf**; a leaf
value replaces. Exceptions by top-level key: `capabilities` is a stored **list** (P.4.2) and takes
the map form `{"<id>": true}` = append `<id>` if absent (`false` = P.9 R12, refused in v1);
`tuning.keys.<key>` merges leaf-wise and only `min`, `max`, `choices` are user-writable — `type`,
`target`, `default` are refused with the reason; `entities.<kind>` is `db define` / `db evolve`
territory and is refused here; `engines`, `template`, `pack_type` are refused (W2.2 / identity).
Fail-closed validation runs on the merged document before write.

### P.7.5 `db define` / `db evolve` payloads (P0-6)

`canon db define <pack> --type <kind> --set '<json>' --actor …` — the payload is a partial
`EntityKind` (P.3.1 stamped fields; minimum `label`, `layout`, `id_field`), e.g.
`{"label": "Abilities", "layout": {"mode": "collection", "path": "abilities/abilities.json",
"format": "array"}, "id_field": "id", "id_alloc": {"base": 7000}, "llm_fields": ["name",
"description"], "schema": {"fields": {"tier": {"choices": [["minor", 3], ["major", 1]]}}}}`. It
writes `schemas/<kind>.json` (the inline `schema` object, or an empty `{"fields": {}}`), an empty
collection file in the declared `layout.format`, and the registry entry; refuses an existing kind;
journals one `db_define` event on `registry` (`changed: {"entities.<kind>": {"from": null, "to":
{…}}}`) plus a `create` event per new file. `db new` for the kind allocates per P.3.1's `id_alloc`
rule; a kind with `id_alloc: null` requires `--set <id_field>=<slug>`.

`canon db evolve <pack> --type <t> --rename-field old:new --actor …` rewrites every row, the
skeleton field name (via `renames`: the skeleton keeps its roll name, the map gains/updates the
on-disk name), and every registry list naming the field (`llm_fields / code_fields / user_fields /
hidden / decorative / protected / routed / refs / nesting`); journals one `db_evolve` event on
`registry` (`changed: {"entities.<t>.fields": {"from": "old", "to": "new"}}`) plus one `edit` event
per rewritten file; warns loudly that the engine must follow (§6). Type renames are v1.1.

## P.8 Journal / ledger event shape (§3.0-B)

Owner: **P1-A6 implements this once**; W2.1 populates the gen block and emits the tuning +
hand-pixel kinds; rows earlier than A6 journal in today's shape. Everything in P.8.1 is observed in
code (`provenance.py:20-22,35,87-192`; `spend.py:11-52,88-127`; `jobs.py:19-103`; `stats.py:9-159`;
27 `record()` sites across `platformer_write.py`, `ops.py`, `library.py`, `godot_export.py`).

### P.8.1 Today's shape, verbatim

`.canon/journal.jsonl` — `append_event` stamps `schema` + `ts` then splats the caller's dict;
`record()` includes optional keys only when truthy:

| field | type | note |
|---|---|---|
| `schema` | int `1` | `SCHEMA_VERSION` |
| `ts` | UTC ISO-8601 with microseconds | spend/jobs use `timespec="seconds"` |
| `artifact_id` | str, required | families in P.7.3 |
| `op` | str, required | docstring `generate \| edit \| keep \| delete \| import \| switch \| regenerate`; code also emits `create`, `restore`; `delete`/`switch` never emitted |
| `source` | str, required | docstring `llm \| user \| import`; code also emits `code` (local repair of bytes — kept out of LLM training pairs) |
| `actor` | str, default `"user"` | observed `user`, `cradle` (baseline), `cradle:user` (32 cradle sites) |
| `session?` | str | written only when truthy; CLI `--session` |
| `detail?` | dict | `detail.kind` = the sub-vocabulary below |
| `before_hash?` · `after_hash?` | `sha256:<hex>` | CAS refs; absent on generate/create (before) and on hash-less rows |
| `gen?` | dict | docstring `{model?, prompt_hash?, input_tokens?, output_tokens?, cost?}` — **no emitter writes those keys**; emitters write `llm_model`, full `prompt`, `fallback`, `image_model`, `vlm_model`, `music_model`, `sfx_model`, `renormalized`, `reused_spec` |

`detail.kind` values observed (all `snake_case`; every read-side lookup keys on the exact string
with a passthrough fallback, so unknown kinds already render): `enemy_move · item_move ·
triggers_change · hazards_change · foreground_change · level_edit · generate · place_enemies ·
place_items · regenerate · improve · terrain_paint · publish · unpublish · sprite_replace ·
tile_reskin · band_replace · row_restore · sprite_restore · tilesheet_restore · band_restore ·
asset_assign · frames_edit · world_map_edit · db_new · db_complete · db_update · tile_params ·
db_schema · asset_generate · asset_animate · animation_renormalize · library_publish ·
library_import · engine_sync`.

Side ledgers: `.canon/spend.jsonl` (`cradle-spend/v1`: `ts, op, scope, level_id, backends,
estimate{best,worst}, actual_usd, tokens{input,output,calls}`; "cradle owns its shape"; $0 rows
belong in the ledger) and `.canon/jobs.jsonl` (`cradle-jobs/v1`: `job_id, op, scope, target,
status ok|no_change|failed, backends, estimate, actual_usd, duration_ms, changed,
changed_artifacts, error`). Cost today lives in the op result's `_cost_block` `{usd, llm_usd,
image_usd, audio_usd, input_tokens, output_tokens, calls, backend}` → cradle → `spend.jsonl`; it is
**never journaled**. Backend ground truth: fal `last_cost` is always 0.0; PixelLab from
`usage.usd`; Retro Diffusion from `balance_cost`; Lyria `PRICING.get(model)` (table); ElevenLabs a
constant. Not journaled today: the `world new` create run (spend/jobs rows only). Where docs and
code disagree — `gen` keys, cost location, op/source vocabularies, "editor-launched generations
bypass the journal" (they journal, but with no cost/kind/identity) — the code column above governs.

### P.8.2 The additive target shape

Nothing removed, renamed or retyped; `schema` stays `1` (a bump is reserved for a breaking change
that never happens in Phases 0–2). The journal is outside the byte-determinism contract by intent
(`.canon/log.jsonl` already is); `tests/treediff.py` does not yet exclude `journal.jsonl`, so the
create-run journal (P.9 J8) rides P.9 R14.

| field | type | presence | rule |
|---|---|---|---|
| ＋ `identity` | str | every event from A6 on | `user` \| `agent:<conversation>/<specialist>` — a **pure function of `actor`** computed inside `record()` at write time (`identity = actor if actor.startswith("agent:") else "user"`) and re-computed at read time for events lacking it; no verb takes `--identity`; `cradle:user` / `cradle` / `user` all collapse to `user`; future `user:<uid>` is a value under the same field |
| ＋ `costCents` | int ≥ 0 | every event with a billable leg, incl. $0 fake runs and token turns | `round_half_up(gen.cost_usd × 100)` stamped by `record()`; the **only** number the dashboard sums (tiles, by-kind, by-identity, by-conversation all sum this field ⇒ they reconcile exactly); absent ⇒ not a costed event (P.9 J1 rounding) |
| ＋ `accuracy` | str | whenever `costCents` is present | `measured` iff every component came from a provider-reported quantity (LLM/VLM token counts × the §3.0-C table, PixelLab `usage.usd`, Retro `balance_cost`); `estimated` iff any component was priced from the table without a reported quantity (fal `$0.039`); mixed rows are `estimated` at top level (per-component in `gen.cost_breakdown.accuracy`). **Never a silent $0:** a paid backend with neither a reported cost nor a price-module row fails cost stamping loudly — **the event is still written** (hashes intact) with `costCents` absent and `detail.cost_error: "<backend>: no price row"`; the dashboard renders it as an unpriced row and the run surfaces the error; the write is never lost (doctrine 6). A fake backend is `costCents: 0, accuracy: measured`. Flat list-price backends: P.9 J3 |
| ＋ `genKind` | str, open | every generation event (`op ∈ {generate, regenerate}` + token rows) | values at launch `image · animation · video · code · audio`; `mesh` joins at W2.2; `tokens` for conversation turns; `text` proposed for LLM-authored data (P.9 J4). Assigned by the verb, not the backend; never a literal union |
| ＋ `batchId` | str, opaque | every event written under one approved plan | threaded like `session` (`--batch`); "undo this plan" restores each `before_hash` in reverse `ts` order, those restore events carry a **fresh** `batchId` + `detail.undoes: "<old>"`; single multi-event verbs get one only when a plan wraps them; editor buttons carry none |
| ＋ `gen.*` | dict | generation events | additive keys beside the existing ones (P.8.3) |
| ＋ `detail.kind` | str | | `accepted_tuning`, `pixel_edit` (P.8.4) — same open vocabulary |

**`session` gains a definition, not a rename:** it **means the conversation id** from A6 on (the
two id spaces of §3.0-D). When `identity` is `agent:<conversation>/<specialist>`, `<conversation>`
equals `session` — redundant by design so pre-A6 `--session` filters keep working. Editor-button
events have no `session`. **Play sessions never journal:** lifecycle is runtime state; a tuning
applied from inside a live session journals `session` = the conversation id if an agent made the
write, else nothing — the play-session id never enters any journal, spend, or jobs row.

### P.8.3 The `gen` block — existing keys kept, additive inputs manifest

```json
{
  "llm_model": "…", "prompt": "…", "fallback": true,
  "image_model": "…", "vlm_model": "…", "music_model": "…", "sfx_model": "…", "renormalized": true, "reused_spec": false,
  "backend": "fal",                 "model": "fal-ai/nano-banana",
  "prompt_hash": "sha256:…",
  "input_tokens": 0, "output_tokens": 0, "calls": 0,
  "cost_usd": 0.039,
  "cost_breakdown": {"llm_usd": 0, "image_usd": 0.039, "audio_usd": 0, "accuracy": {"image": "estimated"}},
  "refs":    [ {"tag": "@ember_ref", "role": "subject", "hash": "sha256:…", "origin": "board:boss_moods"} ],
  "context": {"entity": "enemy:ember_hopper", "level": "l3", "stage": "s1", "board": "boss_moods"},
  "params":  {"seed": "…", "size": "…", "n": 3},
  "lineage": {"parts": ["sha256:…", "sha256:…"], "meta": {}}
}
```

Line 1–2 = existing (untouched; `LineageNode.gen` and `LineagePanel` read `llm_model`/`prompt`).
`backend` / `model` / `prompt_hash` / tokens / `calls` / `cost_usd` / `cost_breakdown` = A6.
`refs` (displayed `@image_name` tag + role + hash + origin — **no `weight` key**, master A-5/S18;
roles `subject/object/style` are data), `context` (an **open dict keyed by the pack's own
vocabulary** — `entity` always; `level`/`stage` on a platformer, `room` on a dungeon, whatever a 3D
pack names; nothing reads the keys as a schema), `params` (provider params as sent, open dict),
`lineage` (splice parts; weights, if a UI ever records them, live only under `meta`) = W2.1's
inputs manifest.

### P.8.4 Detail-kind additions

- **`accepted_tuning`** — an agent-proposed tuning the human accepted, written by `canon tune set`
  on the §3.0-A core: `{"kind":"accepted_tuning","scope":"pack"|"level","level":"l3"?,"changed":
  {"gravity":{"from":…,"to":…}},"proposed_by":"agent:mason/level_designer","proposal_ref":"<run id>"}`
  with `identity: user`, `source: user`, `op: edit` — the (generated → human) pair extraction still
  finds it by `op:edit + identity:user`, and a corpus builder can **exclude** it by kind because the
  values were not human-authored. The master spells it `accepted-tuning`; every existing kind is
  snake_case (P.9 J2). The plain user tuning kind is W2.1's to name (suggest `tune_set`).
- **`pixel_edit`** — hand-pixel work saved by W2.1's `art save` (R8a): `op: edit`, `source: user`,
  `artifact_id` = the bare target (the `frames_edit`/`asset_animate` convention so lineage does not
  fork), `before/after_hash` of the saved frame file (UNVERIFIED which file — W2.1's frame layout),
  `detail: {"kind":"pixel_edit","state":"jump","frame":3,"changed_px":412,"off_palette_px":38,
  "mode":"sprite"|"tileset","slot":…?}`. **Never `import`:** the training-label table makes
  `import` = "user brought in external bytes → rejection", whereas a hand-pixel edit is the gold
  `edit` correction pair (same artifact, generated bytes before, human bytes after).

### P.8.5 Cancelled runs (the Stop contract: start nothing new, keep what landed, say what it cost)

Completed items are ordinary events with their own `costCents`. The in-flight item, if the backend
reports a charge, gets an event with `op` as normal, **no `after_hash`**, `costCents` = the reported
partial, `accuracy` per P.8.2, and `detail: {"kind": <the op's kind>, "cancelled": true}` (P.9 J7);
hash-less events are invisible to `artifact_versions`, `asset_lineage`, and restore by construction,
and `_change_signal` must ignore them. `jobs.jsonl.status` gains the value `cancelled`; the spend
row records the partial with `accuracy`; client-side inference of partial spend is forbidden. A
cancelled agent turn's token burn is a token row with `detail.cancelled: true`.

### P.8.6 Worked one-line examples

Today — a user field edit (cradle door):
```json
{"schema":1,"ts":"2026-09-01T14:02:11.482913+00:00","artifact_id":"enemy:ember_hopper","op":"edit","source":"user","actor":"cradle:user","detail":{"kind":"db_update","type":"enemy","changed":{"hp":{"from":12,"to":16}}},"before_hash":"sha256:9f1c…","after_hash":"sha256:4b7e…"}
```
Post-A6 — the same edit by an agent specialist (`identity` derived; `source` stays `user`, P.9 J5):
```json
{"schema":1,"ts":"2026-09-01T14:05:40.010221+00:00","artifact_id":"enemy:ember_hopper","op":"edit","source":"user","actor":"agent:mason/level_designer","identity":"agent:mason/level_designer","session":"mason","detail":{"kind":"db_update","type":"enemy","changed":{"hp":{"from":12,"to":16}}},"before_hash":"sha256:9f1c…","after_hash":"sha256:4b7e…"}
```
Editor-button paid image, fal (estimated):
```json
{"schema":1,"ts":"2026-09-01T14:10:02.550000+00:00","artifact_id":"enemy:ember_hopper","op":"regenerate","source":"llm","actor":"cradle:user","identity":"user","genKind":"image","costCents":4,"accuracy":"estimated","detail":{"kind":"asset_generate"},"before_hash":"sha256:4b7e…","after_hash":"sha256:c0de…","gen":{"image_model":"fal-ai/nano-banana","backend":"fal","model":"fal-ai/nano-banana","prompt_hash":"sha256:71aa…","cost_usd":0.039,"cost_breakdown":{"llm_usd":0,"image_usd":0.039,"audio_usd":0,"accuracy":{"image":"estimated"}}}}
```
Agent paid generation, PixelLab (measured) with the W2.1 inputs manifest:
```json
{"schema":1,"ts":"2026-09-02T09:31:17.203004+00:00","artifact_id":"enemy:ember_hopper","op":"regenerate","source":"llm","actor":"agent:mason/artist","identity":"agent:mason/artist","session":"mason","genKind":"image","costCents":2,"accuracy":"measured","detail":{"kind":"asset_generate"},"before_hash":"sha256:c0de…","after_hash":"sha256:e11a…","gen":{"image_model":"pixellab-pixflux","backend":"pixellab","model":"pixflux","prompt_hash":"sha256:8d20…","cost_usd":0.0169,"refs":[{"tag":"@ember_ref","role":"subject","hash":"sha256:5a5a…","origin":"board:boss_moods"},{"tag":"@ash_palette","role":"style","hash":"sha256:b2b2…","origin":"artifact:tileset:s1"}],"context":{"entity":"enemy:ember_hopper","level":"l3","stage":"s1","board":"boss_moods"},"params":{"size":64,"seed":"ember-7","n":3}}}
```
A plan batch of two edits, then its batch restore (reverse order):
```json
{"schema":1,"ts":"2026-09-02T10:00:01.000000+00:00","artifact_id":"level:s1/l3/entities","op":"edit","source":"user","actor":"agent:mason/level_designer","identity":"agent:mason/level_designer","session":"mason","batchId":"plan-7f3a","detail":{"kind":"enemy_move","moves":[{"id":"cinder_beetle","from":[19,13],"to":[10,8]}]},"before_hash":"sha256:aa01…","after_hash":"sha256:aa02…"}
{"schema":1,"ts":"2026-09-02T10:00:01.400000+00:00","artifact_id":"enemy:cinder_beetle","op":"edit","source":"user","actor":"agent:mason/level_designer","identity":"agent:mason/level_designer","session":"mason","batchId":"plan-7f3a","detail":{"kind":"db_update","type":"enemy","changed":{"speed":{"from":2,"to":3}}},"before_hash":"sha256:bb01…","after_hash":"sha256:bb02…"}
{"schema":1,"ts":"2026-09-02T10:04:30.000000+00:00","artifact_id":"enemy:cinder_beetle","op":"restore","source":"user","actor":"cradle:user","identity":"user","batchId":"undo-7f3a","detail":{"kind":"row_restore","to":"sha256:bb01…","undoes":"plan-7f3a"},"before_hash":"sha256:bb02…","after_hash":"sha256:bb01…"}
{"schema":1,"ts":"2026-09-02T10:04:30.300000+00:00","artifact_id":"level:s1/l3/entities","op":"restore","source":"user","actor":"cradle:user","identity":"user","batchId":"undo-7f3a","detail":{"kind":"enemy_move","moves":[{"id":"cinder_beetle","from":[10,8],"to":[19,13]}],"undoes":"plan-7f3a"},"before_hash":"sha256:aa02…","after_hash":"sha256:aa01…"}
```
An accepted tuning (W2.1; `artifact_id` = `manifest` per P.7.3 — `tune set` writes only
`manifest.json` until P.9 R3 decides pack-local copies; a pack-scope tuning is one event,
level-scope tuning journals `level:<stage>/<id>/level`):
```json
{"schema":1,"ts":"2026-09-10T16:20:05.000000+00:00","artifact_id":"manifest","op":"edit","source":"user","actor":"cradle:user","identity":"user","session":"mason","detail":{"kind":"accepted_tuning","scope":"pack","changed":{"movement.gravity":{"from":40.0,"to":32.0},"movement.coyote_s":{"from":0.08,"to":0.12}},"proposed_by":"agent:mason/level_designer","proposal_ref":"run-91c2"},"before_hash":"sha256:m001…","after_hash":"sha256:m002…"}
```
A hand-pixel edit (W2.1 `art save`, one event per saved frame):
```json
{"schema":1,"ts":"2026-09-11T11:47:52.000000+00:00","artifact_id":"enemy:ember_hopper","op":"edit","source":"user","actor":"cradle:user","identity":"user","detail":{"kind":"pixel_edit","mode":"sprite","state":"jump","frame":3,"changed_px":412,"off_palette_px":38},"before_hash":"sha256:e11a…","after_hash":"sha256:f2f2…"}
```
A conversation-token cost row (hash-less; `artifact_id: conversation:<id>` names the transcript,
a real pack-resident artifact, without snapshotting it — P.9 J6):
```json
{"schema":1,"ts":"2026-09-02T09:30:58.771000+00:00","artifact_id":"conversation:mason","op":"generate","source":"llm","actor":"agent:mason/foreman","identity":"agent:mason/foreman","session":"mason","genKind":"tokens","costCents":1,"accuracy":"measured","detail":{"kind":"turn","turn":14},"gen":{"backend":"anthropic","model":"<chat model id>","input_tokens":2140,"output_tokens":380,"calls":1,"cost_usd":0.01212}}
```

**Token spend is a journal event, not a spend-ledger-only row** — README §12 "every row is one
journal entry, so the two tables always reconcile"; a spend-only token row would force the
dashboard back onto two sources. Category mapping: `genKind == "tokens"` → the `tokens` column;
every other `genKind` → `generation`; human rows never carry `genKind: tokens`.

### P.8.7 Reconciliation and compat rules

- **The journal is authoritative for the cost dashboard.** Tiles and the by-kind / by-identity /
  by-conversation tables sum `costCents` over journal events filtered by `identity` / `genKind` /
  `session`. `spend.jsonl` becomes a **derived compat index** — still written best-effort with
  `journal_ref` (the `ts` of the op's first journal event) and the lane fields below, read only for
  pre-A6 history and for the `world new` create run until it journals (P.9 J8). `jobs.jsonl` is
  **run status only** (`ok / no_change / failed / cancelled`, duration, changed) — its `actual_usd`
  is informational and never summed. The manifest's `generation_stats.total_cost_usd` tile stays as
  "last full run". **One number:** total = Σ `costCents` over journal events (post-A6) + Σ
  `actual_usd` over spend rows without `journal_ref` (pre-A6); no row is in both sets.
- **Spend / jobs ledgers gain optional fields only** (ASSUMPTION-8): spend rows gain `actor`,
  `identity`, `category` (`tokens` | `generation`), `accuracy`, `genKind`, `session`, `batchId`,
  `journal_ref`; job rows gain `identity`, `session`, `batchId` and the `cancelled` status value.
  Schema strings unchanged; `summarize()` unchanged.
- **Reading pre-A6 events (defaults applied at read time, never by rewriting the file):** `identity`
  ← the function of `actor`; `costCents` absent → not a cost row (History yes, dashboard no);
  `accuracy` absent → not rendered (never default to `measured`); `genKind` absent → not a
  generation row even if `op` is generate/regenerate; `batchId` absent → single entry; `session`
  absent → editor door.
- **Additive only:** `append_event` already tolerates any key set; every reader uses `.get` with
  fallbacks; no test asserts an exact journal key set. `gen` keeps `llm_model`/`prompt` beside the
  new keys.
- **Minimal cradle read-side changes for A6:** a `JournalEvent` type + `journalList(path, filter?)`
  command wrapping a new canon verb natively (no shell-out, the C11 jobs precedent): `canon journal
  list <pack> [--identity <s>] [--session <s>] [--gen-kind <s>] [--since <iso>] [--artifact-prefix
  <s>] [--limit N]` → `{"events": [JournalEvent…]}` with the read-time defaults above applied
  (`level history` is level-only today); `CostDashboard` swaps its by-op source to journal events and drops the fal-$0
  footnote once `accuracy` renders; `handleJobEvent` passes the lane fields and writes a spend row
  for **cancelled** jobs (failed jobs get none today); `LineagePanel` / the last-change chip render
  `identity` (fallback `actor`), `costCents`, and batch grouping; I6 collapses the 32 `cradle:user`
  literals into one module (P1-A4); canon `record()` stamps `identity` + `costCents` when
  `gen.cost_usd` is present and `_change_signal` ignores hash-less events.

### P.8.8 Never-a-literal-union — implementation guidance for A6

- **Python:** `identity`, `genKind`, `accuracy`, `detail["kind"]`, `batchId`, `op`, `source`, the
  spend row's `category` (`tokens` | `generation` at launch), `engines[].live_channel.kind` (open;
  three launch values `none | hooks-v0 | live-vN`), `layout.mode` / `layout.format`, and every
  registry "kind" string are `str`. Validation is **by shape**: non-empty; `identity` is `user` or matches
  `^agent:[^/]+/[^/]+$`; `costCents` int ≥ 0; a costed row without `accuracy` is a write-time error.
  The two `accuracy` values are documented constants in the §3.0-C module compared by string —
  never a `Literal[...]`/`Enum` in a Pydantic model (a `Literal` would reject `mesh` at W2.2 and
  `text`/`tokens` the day they are emitted). Known-value lists live as **data** (a module-level
  tuple for UI labels/ordering, or a registry file), never as a type; unknown values are accepted
  on write and rendered on read as their own row/label (the `_CHANGE_LABELS` fallback pattern).
- **TypeScript:** `genKind`, `identity`, `accuracy`, `kind`, `category`, `live_channel.kind` are
  `string`; widen `JobStatus`
  (`invoke.ts:165`, a literal union today) to `string` with the five known values documented so
  `cancelled` and later statuses render without a type edit; group tables by field value with the
  label map as a plain `Record<string, string>` and a passthrough default.
- **Rust:** `serde_json::Value` passthrough (as `spend_record`/`jobs_record` do today) — no typed
  enums for these fields.

## P.9 Open questions for the user

Only decisions the inventories could not settle. Each carries a recommended default; approving the
paper approves the defaults unless a line is struck. Labels are cited from the sections above.
**Decisions the user took in chat on 2026-09-01 are marked DECIDED in place** (C1, R6, R14+J8, G4,
S7, C2+C3, J6); G4 was decided AGAINST the default and P.6 is amended below.

**Schemas (S)**

- **S1 NPC roll key.** Author `schemas/npc.json` with `behavior_type` (roll vocabulary
  `static/wandering/merchant/aggressive`) and a registry rename to on-disk `type`, or author `type`
  with the engine class names as choices? *Default: `behavior_type` + rename map* — the roll
  vocabulary stays readable, the disk name stays (decision 2.3.4), the map is data.
- **S2 Class identity.** `archetype` as `id_field`, or a new `class_id`? *Default: `archetype`* —
  unique today, engine-keyed, positional loader ignores nothing it needs.
- **S3 `classes/spell_pools.json`.** Container sub-collection of `class` or a tenth `EntityKind`?
  *Default: container in v1* (nine stay nine); a `spell_pool` kind can be `db define`d later.
- **S4 `post`-derived mechanics** (monster `hp/ac/damage_die`, item `attack_dice/price/restore`).
  Python `code_fields` per the platformer precedent, or fund the `SkeletonField` `TODO(v0.2)`
  multi-input lookup so the specs round-trip to JSON? *Default: `code_fields`* — the lookup is core
  work in no Phase 0 row.
- **S5 Decorative row fields** (npc/event `x,y`; monster `tier/level/damage_die`; item `rarity`;
  quest `is_complete/is_failed`; event `monster_count`). Protect-and-hide or editable-but-warned?
  *Default:* row `x/y` routed to grid verbs and hidden (positions live in `maze.json`); npc
  `selected` **protected + hidden** (an engine-live activation gate, P.1.1); `quest_target_tile`,
  `is_complete/is_failed`, `starting_equipment/extra` hidden; the rest editable with an "engine
  ignores this field" warning — all recorded as the per-kind `hidden` / `decorative` lists (P.3.1).
- **S6 Music/sfx `id_field`.** `track_id` / `sfx_id` (P.5) or `id` like the other dungeon rows?
  *Default: `track_id` / `sfx_id`* (self-describing; per-kind id names are the platformer idiom).
- **S7 Where scenes live.** *DECIDED 2026-09-01: the default.* `events/events.json` as `type:"scene"` (engine loads unknown types as
  `CombatEvent`) or a sibling `events/scenes.json` the engine never opens; shared 3000 id space?
  *Default: `events.json`, shared id space* — one store, three readers (§7.1); a scene never gets an
  `event_positions` entry so the engine never triggers it; the engine-lag layer warns.
- **S8 Room environment vocabulary.** Canon's eight vs the engine's six. *Default: seed the eight*
  — wall colour falls back harmlessly to the `dungeon` colour for the five `WALL_COLORS` does not
  name (P.1.7); the W2.0 pull-in extends `WALL_COLORS`/`JOBS`/`HOBBIES`; validator warns when an
  NPC row lacks `job/hobby` in a non-engine environment (a raise at `npc.py:66-67` otherwise).
- **S9 `dialogue_trees` write-back.** Confirm the legacy four keys and §7.1's mechanical mapping as
  the frozen on-disk contract. *Default: confirm.*
- **S10 `class.stats` budget.** Warn (never block) when the sum leaves 95, although the engine's
  `validate_guardrails` treats it as a violation? *Default: warn* (doctrine 10).

**Conditions (C)**

- **C1 Home of engine evaluability** — *DECIDED 2026-09-01: the default (Claude's recommendation:
  evaluability is a property of the engine copy, so it rides `engines[]`; `pack info` surfaces the
  primary's blocks); master §3.0-H, §5.1b and §7.2 AMENDED 2026-09-01.* (also registry R2, audio A3). §7.2 says "pack manifest";
  §5.1b's authoritative list has no such field. *Default: two additive, capability-gated sibling
  fields on the registry engine entry* — `engines[].evaluable_namespaces` (per scope → condition
  namespaces, P.2.4) and `engines[].evaluable_bindings` (row kind → binding/trigger kinds, P.5.3);
  binding kinds never nest under the namespaces key (two vocabularies, two fields). `pack info`
  surfaces the primary engine's blocks as `engine_evaluable_namespaces` /
  `engine_evaluable_bindings` (P.4.6). **On approval, amend three texts so they agree:** master
  §3.0-H (one clause: "+ per-engine `evaluable_namespaces` / `evaluable_bindings`, capability-gated,
  additive"), Phase 0 §5.1b's list, and §7.2's "pack manifest" wording — the master wins on
  contracts, so an unamended §3.0-H would leave the fields void.
- **C2 Quest state vocabulary.** *DECIDED 2026-09-01: the default.* *Default: seed the engine's four* (`not_started · active ·
  completed · failed`); the design's `offered` / `turn-in` are unsupported values (amber), no rename
  of the four, package copy untouched.
- **C3 `time:` operand.** *DECIDED 2026-09-01: the default (period names).* Period names (engine-honest) vs hour windows with a documented
  `hour = position / FULL_CYCLE_MS × 24` mapping the tester implements and the engine does not, vs
  both. *Default: period names* as the seed `windows` vocabulary in `DialogueSpec.operands.time`
  (template data — a farming sim defines its own); the `23:10` chip is prototype copy.
- **C4 `segment` source.** *Default: seed empty in Phase 0* (`segment` legal, no operands); a
  segment list joins `world_bible.json` when a template needs it — not a Phase 0 build.
- **C5 `event:solved`.** *Default: resolved-with-success* (correct semantics); the engine cannot
  honor it until it persists an outcome, so `event` stays out of the engine-evaluable set (already
  `{}`) and the tester implements it.
- **C6 Music runtime mode.** *Default: keep combat/event tracks as fixed engine slots outside
  sections* through Phase 2; no runtime-mode namespace now.
- **C7 Token arity.** *Default:* `flag:<key>` = truthy with `flag:<key>:<bool>` legal;
  `advance_quest:<quest_id>` = engine-defined next state with optional `:<state>` — both recorded
  as data in `DialogueSpec.operands`.
- **C8 Operands verbatim.** Confirm the operand is the `id_field` value stringified (`has_item:2000`);
  design slugs are illustrative. *Default: confirm.*

**Registry / shapes / world update (R)**

- **R1 Dungeon grid artifact ids** (also grid G6). `room:<map_id>/<step>` vs reusing `level:` with a
  synthetic stage. *Default: `room:<map_id>/<step>`* with steps `grid` and `placements`; the
  `rooms.json` entry joins the room `revision`; the `last_change` prefix filter reads
  `GridKind.artifact_id` — rooms must not invent a stage (master §2).
- **R2** merged into C1.
- **R3 Template data files** (`game_rules/combat/tile_types/variants/graphics/models/sections/
  water_levels/secret_rooms`). Pack-local copies at create, or the manifest copy is the owned
  instance? *Default: manifest copy in Phase 0* (byte-identical); pack-local copies are W2.1's call
  when `tune set` needs them.
- **R4 Wizard metadata.** *Default: stamp a copy under `template.wizard`* (cheap provenance).
- **R5 `{level}` placeholder.** *Default: add it* to the §5.1b set.
- **R6 Platformer engine entries at create.** *DECIDED 2026-09-01: the default.* Master §3.0-H says create stamps "the pack's one
  engines-block entry"; the platformer has two launches today (▶ Play-level = the pygame harness,
  ▶ Play-game = Godot). Stamp **one** entry (`godot`, primary) and leave the harness on cradle's
  current `play_level` code path until W2.0's launch-by-id rewrite, or stamp a second interim
  `pygame` entry now? *Default: one entry* — §3.0-H read literally; the harness is not in the pack
  (master S1) so it is not an attached engine; its would-be entry (`-m canon.packs.platformer.play`,
  in the wheel since 2026-09-01) is recorded in P.4.3 for W2.0. P.2.4's "every attached engine" then means one target in Phase 0.
- **R7 Engine version stamp.** *Default: registry `template.version` is derived from
  `godot/.engine.json` at read* (not stored twice) until W2.0 folds the stamp into the registry.
- **R8 Wizard `ranges`.** *Default: P0-10 authors them* (numeric, from today's defaults).
- **R9 Band-widening audit.** *Resolved in P.4.5 as journal-only* — no `widened_by`; the
  `registry` artifact's History is the audit. (Struck; kept for the record.)
- **R10 Tuning key shape.** *Resolved in P.4.5* — `type: "choice"` + `choices` and the dotted-key
  convention are in the block format (still spec-only) so W2.1 does not change the format.
  (Struck; kept for the record.)
- **R11 Stage-level fields** (`theme/biome/effects`). *Default: out of `world update` v1.*
- **R12 Disabling a capability.** *Default: one-way enable in v1*; data stays, disable is v1.1.
- **R13 `world new --name`.** *Default: route through the journaled core at P0-6* (closes the
  `_set_world_name` bypass).
- **R14 Create-emitted deltas beyond `bible.json` (doctrine 7 says "a new user decision").** *DECIDED
  2026-09-01: the default (with J8).* Three
  Phase 0 rows add files or keys to a fresh platformer tree that `tests/treediff.py` compares today
  (it excludes only `bible.json`, `log.jsonl`, `generation_stats.json` by basename): P0-3's
  `manifest.json.pack_type` key, P0-10's `.canon/registry.json`, and J8's create-run
  `.canon/journal.jsonl`. *Default: widen `treediff.DEFAULT_EXCLUDES` to the whole `.canon/`
  directory* (instance registry + observability are outside the byte-determinism contract, the
  same reasoning that already exempts `.canon/log.jsonl`; the fixtures compare the emitted pack
  tree only) *and sanction the one additive `manifest.json.pack_type` key* as P0-3's fixture delta
  (byte-identical reads "existing keys byte-identical; `pack_type` additive"). Striking this line
  means `pack_type` lives only in `.canon/` and P0-3 has no manifest delta.

**Room grid (G)**

- **G1 Open cell name.** `empty` (eraser carves) or `floor` (paintable swatch)? *Default: `empty`*
  — zero code, matches `Dock`/`tileColor` today.
- **G2 Room palette.** *Default: the per-environment `WALL_COLORS` table as template data*, open
  cell from cradle theme tokens; engine debug colours not used.
- **G3 Events on the wire.** *Default: reuse the `triggers` sparse shape* (`type` = event type,
  `params.event_id`) plus a draw branch for non-`checkpoint` types.
- **G4 "🎲 monsters" / dragging monsters.** Code has no monster placement (M7). *DECIDED 2026-09-01
  AGAINST the default (user): monsters ARE placeable.* For the dungeon crawler, placing a monster
  means building or placing an **encounter** — a combat event carrying `monster_ids` — on a square;
  NPCs, items, events/puzzles are placed the same way and quests attach to their giver NPC / room;
  *testing* the combat is the Phase 2 playtest outcome (master Q3). P0-8's room editor therefore
  gets a **Monsters** Dock tab: dropping a monster on a cell creates (or targets) the combat event
  at that cell via the `event_positions` + grid `-1` path and adds the monster to its
  `monster_ids` (a cross-file write to `events.json`, journaled per file); 🎲 monsters re-rolls the
  selected encounter's `monster_ids` through the placement phase's sampling. The platformer's
  enemy placement is unchanged (drop an enemy, play the level). P.6.2 row 13, P.6.3's per-step
  rolls and P.6.5 M7 are amended accordingly.
- **G5 Door drag.** *Default: snap-to-gate-adjacent* (preserves the code-enforced invariant);
  free drag refused with the reason.
- **G6** merged into R1.
- **G7 Wall painted over a placement.** *Default: refuse (fail-closed) with the reason*; the user
  moves the placement first.
- **G8 Per-kind re-roll seeds** `derive_seed(base, "placement", map_id, kind)`. *Default: confirm*
  as the P0-8 shape.
- **G9 Bundle field neutrality.** *Default: keep `enemy_id`/`item_id` literals in the shared
  bundle*; the room writer resolves via `id_field` (no `drawLevel`/`Dock`/devMock churn).

**Audio (A)**

- **A1 Rows file + naming.** *Default: `music/music.json` + `sfx/sfx.json`* beside the audio
  (mirrors `npcs/npcs.json`, safe from the manifest scan); field names `track_id`/`sfx_id`,
  `file`/`file_hash`, `brief` as in P.5.
- **A2 Projection path form.** *Default: pack-relative in rows; the projection emits absolute
  paths* (byte-compatible with both writers and `resolve_data_path`) until the W2.0 pull-in fixes
  resolution, then relative.
- **A3** merged into C1.
- **A4 Float duration bands.** *Resolved in P.5.2*: `lookup_ranges` rolls int bands only
  (`core.py:312-316`); sfx rows store integer `duration_ms`, the producer call converts to seconds.
  (Struck; kept for the record.)
- **A5 Per-row volume.** *Default: leave to Phase 2's audio system.*
- **A6 `duration_measured_s`.** *Default: drop it* unless a decoder is already a dependency.
- **A7 Seed catalog source of truth.** *Default: retire canon's generic `AssetPhase` fixed lists
  for dungeons*; the registry seed is the one copy.
- **A8 Audio upload owner.** *Default: widen `asset replace`* to audio targets; Phase 2's
  `audio_import` is the tool-name face of that verb, never a second path.

**Journal (J)**

- **J1 Sub-cent rounding.** *Default: keep integer `costCents`* (the design contract);
  `gen.cost_usd` holds the precise audit value.
- **J2 Spelling.** Master writes `accepted-tuning`; all kinds are snake_case. *Default:
  `accepted_tuning`.*
- **J3 Flat list-price backends** (Lyria table, ElevenLabs constant). *Default: `estimated`* under
  the literal provider-reported rule; only provider-reported amounts/quantities are `measured`.
- **J4 `genKind` for LLM-authored data** (levels, rows, dialogue). *Default: `text`* as a value;
  `code` stays `game_coder`'s.
- **J5 `source` for agent field edits.** *Default: keep `user`* (identity carries the agent); the
  correction-pair lane filters by `identity` when it needs to.
- **J6 Token rows.** *DECIDED 2026-09-01: the default.* Hash-less journal events with `artifact_id: conversation:<id>`. *Default:
  confirm* (one-source rule).
- **J7 Cancellation marker.** *Default: `detail.cancelled: true` + `jobs.jsonl.status: cancelled`*
  — keeps §3.0-B's top-level field list exact.
- **J8 Create-run journaling.** *DECIDED 2026-09-01 with R14: the default.* *Default: P0-10's orchestrated create journals per-artifact
  `generate` events in the pre-A6 shape (no cost)*; create-run cost stays spend-only until A6.

---

# W2 — Create From Template

**Status:** drafted 2026-08-27 from the W2 review Q&A. Audit facts it builds
on: the wizard's template id is decorative today; `canon world new` hardcodes
the platformer runner; the 12-phase dungeon pipeline exists with two wiring
blockers (no StepLog, no estimator); wizard papercuts (no key precheck, no
seed plumbing, name-collision hard error, recents "add" tile mis-wired to the
folder picker).

## W2.1 Decisions (2026-08-27)

1. **Wizard step 2 is honest to the generator.** Dungeon: **Rooms** + entity
   counts (NPCs, Monsters, Items primary; events/quests/classes at pack
   defaults, overridable under Advanced) — a 1:1 map onto
   `--num-maps/--npcs/--monsters/--items/--events/--quests/--classes`. No
   "Floors" vocabulary until rooms gain real stage-like grouping (W1 spatial
   work); the UI never promises structure the manifest doesn't have.
2. **Full estimator parity before the template ships — split core/pack.**
   The estimator ENGINE moves to canon core (~70% of today's module: the
   cost-model JSON schema, provider pricing, `_paid`/`_apply_backend_mask`
   masking, retry multipliers, summation + per-generator breakdown shape);
   the platformer refactors onto it output-identical. A pack then supplies
   only a **count function** (params → which nodes fire how many times) and
   its calibrated `cost_model.json` — that pair is `PackSpec.estimator`.
   Dungeon calibration: input tokens measured free from fake-mode prompts,
   output tokens schema-estimated, refined against the spend ledger's
   actuals after the first paid runs. Anchor: a 3-map full-API run ≈ $30.
   Future templates inherit the engine and pay only counts + calibration.
3. **Templates are game types — two cards now, real games next.**
   (Platformer, Dungeon crawler). Named future game-type templates:
   **farming sim** (the dialogue variant-spec design already anticipates its
   season/time/relationship axes) and **beat 'em up**. There is no third
   kind of card and no "variant/preset" wizard axis: anything like
   `examples/lava_world/` (a *game* built by evolving the platformer
   default's data — a Phase-3b acceptance fixture, not a template) is
   **project evolution**, which happens *after* create via W1 §5.1a — start
   from a template, then take the project anywhere (as far as turning a
   platformer into a contra-like shooter) with no template changed or
   created. Promoting a diverged project *into* a template is a real but
   much-later path (§5.1a).
4. **Editing is day 1 — there is no create-without-edit release.** When
   someone builds from a template, the databases land as generation emits
   them and must be immediately openable and editable. Consequences:
   - The dungeon card **goes live only when W1's editing surfaces are live**
     for its types (P3 db write + P4 rooms + P5 dialogue). No beta period of
     "generates but read-only"; the beta badge question dissolves — the card
     ships un-badged, complete.
   - The audit's "ship create-only as a ~1-week standalone win" sequencing is
     **rescinded**. W2's build proceeds in parallel (wizard, verbs,
     estimator), but its release is the capstone of the phase.
   - Per-step generation is the editing-native workflow: roll rooms → edit →
     roll monsters → edit → roll items (the locked "intercept any stage"
     model). The existing serial job queue already serializes generation vs
     edit writes on a pack; edits queue behind an in-flight step rather than
     colliding with it.

## W2.2 The flow (end state)

1. ＋ New project (all four entry points, plus the recents "add" tile fixed
   to open the modal per the design spec).
2. Step 1: two game-type cards from `canon pack templates` (label, vocab,
   defaults, ranges — no more hardcoded TS array).
3. Step 2: name + honest counts + five generator selects; **provider-key
   precheck runs here** (the same `missingKeysFor` gate the entity path has,
   linking to the W3 settings screen); live estimate from the pack's
   estimator; Advanced: seed, model, per-type extras, custom location.
4. Create: lands in `~/CradleProjects/<slug>/` (auto-uniquified on
   collision), stamps `pack_type`, runs `canon world new --template dungeon`
   with a StepLog attached; `CreateProgress` gains the dungeon phase-label
   block (today it hardcodes 22 `plat:*` ids).
5. World opens; every emitted type is immediately editable (W2.1.4); further
   generation is per-step from inside the editor.

## W2.3 Work items

| Item | Side | Size |
|---|---|---|
| `world new --template` dispatch via the registry | canon | S (rides W1 P1) |
| StepLog in the dungeon runner + phase labels in `CreateProgress` | canon + cradle | S |
| Dungeon estimate module + `cost_model.json` | canon | M (decision 2) |
| `pack templates` verb + registry-driven wizard cards | canon + cradle | S–M |
| Key precheck in the modal; seed/model Advanced fields; uniquify; recents-tile fix | cradle | S each |
| Project store default + location Advanced override | cradle + canon | S (decided in W1 §8.4) |

**Release gate:** all of the above **and** W1 P3–P5 editing live for dungeon
types. Platformer creation keeps working unchanged throughout.

## W2.4 North star — the wizard's future axes (recorded 2026-08-27)

The template picker's end state is not a flat card list. The wizard will
eventually select along independent axes:

- **Engine** (pygame · Godot today; Godot is the 3D-capable and
  export-capable one),
- **Game type** (platformer · dungeon crawler · farming sim · beat 'em up ·
  fighter/shooter…),
- **Dimension** (2D · 3D) — **the next project after this phase is a simple
  3D game (fighter or shooter)**, so the axes must not assume 2D,
- **Distribution target for the built game** — computer, web, and mobile.
  This is about distributing *games*, distinct from W4 (cradle itself as a
  web app). Godot 4 exports to all three; pygame effectively none — engine
  choice and distribution are coupled axes. `GodotExportPhase` / `engine
  sync` are the existing seam this grows from.

**What this costs now (cheap, do in W2):** `pack templates` metadata carries
`engine`, `dimension`, and `distribution` fields from day one — rendered as
informational chips on the two v1 cards, becoming selectable axes as real
choices appear. What it forbids: baking "2D" or "pygame" assumptions into the
registry schema or wizard data model. The engine axis lands as real pack
data: the registry's **engines block (§5.1b)**, seeded at create — its
per-engine `exports` list is where this distribution data lives.

# W3 — Packaging + API-Key Screen

**Status:** drafted 2026-08-27 from the W3 review Q&A. Audit facts it builds
on: Tauri bundle + signed 3-platform CI ready with a fetch-payload-at-build
pattern (`fetch-demo.sh`); the blocker is canon's editable-checkout
assumption (pipeline in unpackaged `examples/`, four
`Path(canon.__file__).parents[2]` sites, `sys.executable` re-entry,
`run_canon_module` hardcoding `.venv/bin/python`); no settings UI or config
store exists anywhere in cradle.

## W3.1 Decisions (2026-08-27)

1. **Keys live in the OS keychain from day one** (macOS Keychain / Windows
   Credential Manager / Linux secret-service).
2. **A small real Settings screen** — net-new surface; API Keys is its first
   pane, and it becomes the home for the W1 project-store location and
   environment paths.
3. **All API backends bundle** — `[cli,platformer,play,anthropic,images,
   audio]`, ≈150–200 MB installer. Torch extras (`huggingface`,
   `images-local`) stay excluded; power users point `CANON_BIN` at their own
   install.
4. **Distribution stays as-is** — GitHub Releases; macOS signed + notarized,
   Windows unsigned (SmartScreen), auto-updater stays deferred. Signing +
   updater are a later, audience-driven item.

## W3.2 Canon packaging surgery (prerequisite for everything)

- Promote pack code + data out of `examples/` into the installed package —
  the same move W1 makes for the registry (`src/canon/packs/`), covering the
  ~2.3 MB of schemas/specs/cost models and the 152 KB Godot template as
  package data.
- Replace the four `parents[2]` sites with one resolver; fix the
  `sys.executable` re-entry (`world new` shells a script — it becomes an
  in-process call or resolves the real interpreter); retire cradle's
  `run_canon_module` workaround once estimators import cleanly.
- **Acceptance:** in a fresh venv, `pip install` of the built wheel (no
  source checkout) runs `world new`, level verbs, db verbs, and estimators.
  This is also what W4's server path needs.

## W3.3 Vendored runtime & bundling

- **python-build-standalone CPython + installed wheels** as a Tauri bundle
  resource, fetched at build time by a script following `fetch-demo.sh`
  (idempotent, SHA-verified). Preferred over PyInstaller because canon
  re-spawns an interpreter and pygame play needs a real one.
- **Resolution order:** `CANON_BIN` env (dev override, unchanged) → bundled
  runtime (via `resource_dir()`, the `get_bundled_demo_path` precedent) →
  `canon` on PATH. Dev workflow untouched.
- CI notes: the resource path must exist for every cargo invocation (the
  `mkdir -p bibles/…` precedent in `ci.yml`); three platform runtimes fetched
  per release build; Godot remains an optional detected install.

## W3.4 Keys: keychain → child-process environment

- **Delivery:** canon's `_load_env_file` uses `os.environ.setdefault` —
  process env always wins. Cradle reads keys from the keychain and injects
  them as env vars on the spawned canon process. **No plaintext file at
  rest, zero canon changes.** `--env-file` plumbing stays for dev
  (harmless under setdefault); effective precedence: injected keychain env →
  `CANON_ENV_FILE`/repo `.env`.
- **Implementation:** the `keyring` Rust crate directly in cradle's Rust — no
  Tauri plugin, matching the existing raw-`std::process::Command` precedent.
  Service name `cradle`, one entry per provider var.
- **Status reporting:** extend `provider_keys()` to report each var's source
  (keychain / env / file) — still names-only, never values.
- **Per-provider rows:** `ANTHROPIC_API_KEY`, `FAL_KEY`, `GOOGLE_API_KEY`
  (Lyria), `ELEVENLABS_API_KEY`, `PIXELLAB_SECRET` (fixing cradle's
  `PIXELLAB_API_KEY` mismatch), `RD_API_KEY`. Each row: set/unset + source
  chip, paste field, and a **user-initiated test button** (cheapest possible
  API ping; paid-legs-are-user-run compliant).
- **Linux fallback risk:** secret-service may be absent (headless/minimal
  desktops) — fall back to an app-config env file with a loud "stored
  unencrypted" warning rather than failing.
- **macOS note:** first keychain access prompts per app signature — the
  signed .app makes this a one-time, well-labeled prompt.

## W3.5 The Settings screen

Small and real: gear entry in the TopBar + deep links from every "missing
key" refusal (W2's modal precheck, EntityOverview's gate). Two panes v1:

1. **API Keys** — the rows above.
2. **Environment** — effective canon (bundled version vs `CANON_BIN`
   override), Godot detection status, and the **project-store location**
   (W1 §8.4's `~/CradleProjects/`, relocatable here).

Theme stays where it is (TopBar). Nothing else moves in v1.

## W3.6 First-run & hardening

- Startup probe of the resolved canon; a failed probe gets a guided screen
  (what was tried, what to do) instead of today's raw
  "No such file or directory".
- Tighten `assetProtocol` scope from `["**"]` to the directories cradle
  actually opens (project store + user-opened roots) before the bundled
  build ships broadly.
- The bundled demo continues shipping via `bundle.resources` (unchanged).

## W3.7 Work items

| Item | Side | Size |
|---|---|---|
| Packaging surgery (§W3.2) | canon | M — 2–4 days, shared with W1 |
| Runtime fetch script + bundle wiring + CI (3 platforms) | cradle | L — ~1 wk |
| Keychain storage + env injection + `provider_keys` source | cradle (Rust) | M |
| Settings screen (2 panes) + refusal deep links | cradle (UI) | M — 2–3 days |
| PixelLab var fix; asset-scope tightening; startup probe | both | S each |

**Success criterion:** on a fresh machine with no Python installed — download
the release, open cradle, create a free platformer, add one key in Settings
(landing in the OS keychain), run one paid generation successfully. Dev
machines with `CANON_BIN` set notice nothing.

---

# W4 → moved to `October_Phase_0_prd.md`

The web path — transforming cradle into the Cursor shape (local editor,
Demi-hosted generation backend, then hosted workspaces and teams) — is its
own phase and its own PRD: **`docs/October_Phase_0_prd.md`**.

**What still binds THIS phase:** October's eight anti-lock-in invariants,
applied in review on all W1–W3 work, so nothing built in September deepens
desktop coupling. In one line each: **I1** all IPC through `invoke.ts` ·
**I2** no new direct `@tauri-apps` imports (adapterize on touch) · **I3**
new commands stateless (path + JSON) · **I4** canon capability wheel-clean
(W3.2 gate) · **I5** durable truth lives in `<pack>/.canon/`, never
frontend-owned · **I6** actor string centralized, every new verb takes
`--actor` · **I7** devMock parity for new commands · **I8** localStorage =
per-device convenience only. Plus one M0-readiness rule: backend-selection
UI treats backend ids as data, never a hardcoded union (so October's `demi`
gateway id is an entry, not a refactor). Full elaboration in the October
PRD §4.

