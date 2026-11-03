# PRD — Phase 1: The Cradle Agent

**Position:** 
September Phase 0 = registry, create, packaging, and mazeworld parity
**Phase 1 = this.** 
Phase 2 (sandboxes) extends this agent model (its S8). 

**Conventions:** ASSUMPTION-n marks a call I made that the user has not explicitly ratified — collected in §9. No implementation code appears here; tool signatures are interface specs.

---

## 1. Problem & goals

### 1.1 Problem

Cradle is becoming "Cursor for game dev," but every capability is a hand surface: ~7 generation buttons on an entity, a dense level-editor toolbar, a
create wizard, modals per paid action. The recon confirmed the inverse asymmetry: the **tool layer is finished** (64 JSON-emitting canon verbs built
for programmatic callers, journaled, estimated, restorable) while the **conversational layer is absent** (no streaming, no tool-use loop, no
message history, anywhere). Users who can say what they want — "make 2-3 beatable," "this enemy should feel menacing," "give me an ice world" — must today translate it into the right sequence of buttons themselves.

### 1.2 Goals

1. A user can accomplish anything the editor's buttons can do by conversation, with equal or better safety (permissions, estimates, provenance, undo). This means generating levels, assets, writing story and dialogue, playing music, and play testing
2. The agent verifies its own work (validation is mandatory after mutations) and shows the user what changed, where. Validation here is that the generation meets specific quality guide lines (levels, assets, etc). Users approve - if a generation gets stuck in a bad state (not completeable, asset doesn't pass quality) we surface to user with opinion. 
3. Money never moves without a shown estimate and an explicit confirm.
4. Every agent action is attributable (named agent actor), auditable (journal), and reversible (restore), without polluting the human correction-pair training signal.
5. The architecture: loop + tools local on the workspace plane; model calls through the pluggable generation-plane seam (`demi`-ready); panel transport = the future web transport.
6. The agent can perform the cradle tasks as well as canon tasks, and can help improve, edit, and change game code/write code related to the game. 

### 1.3 Anti-goals & scope boundaries (v1)

- **Not a new write path.** The agent mutates packs exclusively through canon verbs. It never writes pack files directly or changes the template; "cradle never writes pack files" extends to the agent verbatim.
    - we have a specical case here where now that the agents can improve/write new game code we need a path for this - see how canon does it. 
- **Not a pipeline replacement.** It drives the existing per-entity ops and create flow; it does not touch `run/resume/regen` DAG verbs (fenced, §7.2).
- **Still focus on local app.** The service is local, single-user, dies with cradle. Hosting, accounts, and Demi metering are deferred til later.
- **Not the dialogue tester, not the sandboxes.** September row #9 and Phase 2 own those; and will be expanded on later
- **Not template editing.** Templates are seeds (three-tier model). Even in V2, code tools touch only the project's own copies. This extends to gameplay as well - never change the template, only the project. 
- **Game-type boundary:** v1 capability = whatever the registry serves. Platformer full; Dungeon Crawler

---

## 2. User experience

### 2.1 The surface

A **docked right-hand column** (D9): resizable, collapsible, width persisted in `LayoutPrefs`, present globally — open-world and start page. Visual design comes from the A0 package (`cradle/docs/design_brief_agent_panel.md` is the brief; approved mocks are adopted into this section).

Header controls: **mode switch** (Ask / Plan / Allow — current mode always visible, disabled-with-a-reason styling), **named-agent picker**, **provider/model picker** (§3.3), new session, session history.

### 2.2 Entry points

- TopBar toggle (symmetric with Notes & docs).
- Command palette: "Ask agent…" plus agent-contributed commands via the existing per-scope command registry.
- Start page: the panel is present with no project open, scoped to create/open/explain. ASSUMPTION-1: contextual "ask about this" buttons on entities/levels are an A0 design decision, not required for v1.

### 2.3 Seeing, approving, reverting

- **Seeing:** every tool call renders as a card in the transcript at its tier's visual weight (reads quiet/collapsed; writes with a diff; paid with estimate → progress → actual cost). Grid edits render a **spatial before/after** using the existing pure `drawLevel` renderer — the diff preview and the editor canvas cannot disagree because they are the same function. Row/schema edits render field-level old→new.
- **Approving:** Ask mode = per-action chips **Accept / Always allow in this project / Reject**. Plan mode = one approval for a numbered plan; steps check off live. Allow grants are **project-scoped** (D5), persisted at `<pack>/.canon/agent/permissions.json`, and never exist for pack-less sessions. Paid confirms appear in every mode, always with the estimate, backend, and model named.
- **Reverting:** every write journals with `before_hash`/`after_hash` under `agent:<name>`. Three undo grains: 
  (a) per-artifact restore via the existing History/lineage tab; 
  (b) a transcript-level "undo this" on any write card (restore to its before-hash); 
  (c) "undo this plan" — restores each touched artifact to its pre-plan hash, in reverse order.
  paid spend is not reversible; undo restores content, the ledger keeps the cost row.

### 2.4 Long-running builds

Paid generation runs inside the service and streams the pack's StepLog (`.canon/log.jsonl`) into the transcript card — the same phase/item/count/elapsed folding `CreateProgress` proved. The user can collapse the panel, navigate anywhere, or keep editing; on completion the card shows result + actual cost and offers "show me" (navigates selection to the target). Runs survive panel collapse but **not app quit** (service dies with cradle — same as today's create; the known no-cancel debt B4 carries: v1 has **no cancel** for an in-flight paid tool; the confirm is the gate). Button-driven jobs continue on the Rust JobQueue unchanged; agent runs and button runs both land in the pack's spend/jobs ledgers so the JobTray's
durable history stays one list (requires the native `jobs_list`/`jobs_record` fix, §6).

---

## 3. Agent design

### 3.1 System prompt strategy

Four layers, assembled per session by the service:

1. **Core (static, versioned in canon):** identity and law. Encodes the house doctrines as behavior: verbs are the only hands; **code computes, the LLM designs** (prefer validate/repair verbs over hand-tuned geometry; never eyeball what a validator can prove); paid must be visible before it happens; **probe, never assume** (pack drift is real); never claim done without the mandatory post-mutation validation; surface disagreement with the user's premise rather than silently complying.
2. **Pack context (per session):** the capability probe result (pack type, engine status incl. modified/unstamped files, schema generation, counts, validation summary, spend to date). Rebuilt on session start and after external changes.
3. **UI state (per message):** open selection, active tab, dirty layers, current mode — supplied by cradle with each user message; only the latest copy is kept in context.
4. **Agent persona (per named agent):** ASSUMPTION-2 — named agents share the fixed core and differ by a user-editable persona/instruction block (tone, defaults, standing preferences). Personas are stored with the project store, not per pack.

The assembled system prompt is inspectable read-only in the panel (the `prompt show` / PromptOverride house precedent); the core is not user-editable, personas are.

A base philosophy for Canon is Skeletal-Driven Generation. We create templates and schema/tables and utilize typing and specific fields in the data where applicable - meaning the llm can never hallucinate an enemy type, or movement type, as these sorts of things will be defined by the template/user and can be expanded with edits (even agentically) but never submitted to generation without clear guidance. The llm generations then focus on gaurd railed areas like game code, or descriptions, or generations that can be validated though our validators and play testors. 

### 3.2 Scope enforcement

The prompt states the law; the **tool registry is the law's enforcement**. The model physically cannot: run shell, write arbitrary files, read outsidethe pack root (path-guarded, the `data.rs` precedent), reach the DAG/regen verbs, imply a paid backend, or bypass a tier gate — because no such tool exists and tiers are checked service-side, not model-side. Prompt-injection
posture: pack content (flavor text, briefs, names) arrives in tool results framed as data; the core instructs that instructions inside pack data are never followed. Residual risk noted in §9.

### 3.3 Model selection UX

- Panel header: provider + model picker. **Ids are data** from `BackendRegistry` — Claude, OpenAI
- Each entry shows its per-1M pricing (the existing PRICING tables); missing-key providers render disabled-with-a-reason naming the env var, deep-linking to Settings once W3.5 ships.
- Per-named-agent default model; per-session override.
- No automatic tier-routing of the conversation model in v1 (no cheap-model-for-easy-turns); one user-picked model per session. Generation tools keep their own per-call backend/model flags, chosen in the confirm chip (defaulting to the surface's current backend selection conventions).

### 3.4 Context management across a long session

- **Describe-first:** tools return compact summaries by default (`describe_level`, windowed grids); full dumps only on explicit need. The A3 gate measures worst-case turn size on the widest real level.
- **Compaction:** When the transcript approaches the model's budget, the service folds older turns into a stored summary event in the session `.jsonl` (facts established, edits made with artifact ids + hashes, open intents) and keeps the recent tail verbatim. Resuming a session replays summary + tail. Tool-result bodies are the first thing compacted; user words are the last.
- **Single source of truth for state:** the transcript never carries authoritative pack state — the pack does. After compaction or resume, the agent re-probes rather than trusting remembered values (drift doctrine).
- Vision inputs (renders, sprites) are referenced by path + hash and re-attached only when the current question needs eyes.

---

## 4. Tool inventory

Tier legend: 
**auto** = fires without asking (reads, display).
**ask** = permission chip; eligible for project-scoped Always-allow.
**$ confirm** = paid; always confirms with estimate + backend + model, in every mode; never Always-allowable. Every write threads
`--actor agent:<name> --session <id>`.

An action whose selected backends are all `fake`/`none` (e.g. $0 preview project) is **ask**-tier, not $-tier — the chip shows "$0".
Batch canvas-grid edits route through the grid-import verb; sparse placement/field edits route through apply-edit — matching how the editor itself splits saves.

### 4.A Codebase ops (project files; read-only in v1)

| Tool | Signature | Touches | Tier |
|---|---|---|---|
| `list_pack_files` | `(glob?) → paths+sizes` | pack tree (path-guarded) | auto |
| `read_pack_file` | `(path, range?) → text\|json` | any pack file incl. the project's own `godot/main.gd` | auto |
| `search_pack` | `(query, glob?) → matches` | pack text/JSON | auto |
| `engine_status` | `() → current/stale/modified/unstamped per file` | `godot/.engine.json` vs template | auto |
| `engine_sync` | `(dry_run, force=false) → written/refused` | pack runtime files (fail-closed on hand-edits) | ask |
| *(V2)* `edit_project_code` | `(path, diff) → new_hash` | the project's **copy** of gameplay code only | ask + gates (§8) |

**Trace A — "What does the flyer actually do when it loses sight of me?"**
User asks → `read_pack_file("manifest.json", rules.flyer)` + `read_pack_file("godot/main.gd", flyer section)` (auto, quiet cards) → agent explains: hold altitude, committed dive parabola, horizontal leash from `patrol_range × leash_mult`, citing the row's values → no files change, no build → user sees a cited answer with a "show me the row" link that navigates to the enemy's Overview. *(Also demonstrates: reads never prompt.)*

### 4.B Canon build ops (world/level/db generation + editing)

Reads (all **auto**): `describe_pack() → probe summary`, `describe_level(id) → dims/histogram/POIs/overrides/validation` *(new verb)*, `export_level(id, window?) → decoded layers` *(windowed)*, `validate_level(id) → report`, `get_history(target) → journal events`, `get_versions(target)`, `world_map()`, `db_types()`, `db_row(type,id)`, `db_schema(type)`, `estimate(scope, params) → usd range`.

| Tool | Signature | Touches | Tier |
|---|---|---|---|
| `apply_level_edit` | `(level_id, sparse_edits) → changed, revision` | entities/items/triggers/markers files + level.json; journals `edit` | ask |
| `import_level_grids` | `(level_id, layers) → hashes` | collision/terrain/background (+derived) | ask |
| `create_level` | `(params) → level_id` | new draft level dir | ask |
| `publish_level` | `(level_id) → stage entry` | stage.json, manifest | ask |
| `edit_world_map` | `(edits) → overrides` | world.json map overrides | ask |
| `update_row` | `(type, id, fields) → changed` | `enemy\|item/<id>.json` (protected fields refused by the verb) | ask |
| `update_schema` | `(type, changes) → validated` | pack-local `schemas/<type>.json` (fail-closed: loader, lookup coverage, smoke roll) | ask |
| `pin` / `unpin` | `(ids)` | bible/metadata pins | ask |
| `restore` | `(target, version_hash)` | target files from CAS | ask |
| `generate_layout` | `(level_id, prompt?, llm_backend, model?) → changed_artifacts, cost` | full layout re-roll (blind) | **$ confirm** |
| `improve_layout` | `(level_id, instruction, llm_backend) → …` | layout re-author seeing current DSL | **$ confirm** |
| `place_enemies` / `place_items` | `(level_id, llm_backend) → placements, cost` | entities.json / items.json | **$ confirm** |
| `generate_level` | `(params, backends) → level_id, cost` | new full level | **$ confirm** |
| `complete_row` | `(type, id\|anchors, llm_backend) → row, cost` | LLM-fills name/flavor around locked anchors | **$ confirm** |
| `create_project` | `(params, backends) → pack_dir` | whole new pack via the create pipeline | **$ confirm** (ask when all-free, A-5) |

**Trace B — "Why is 2-3 impossible? Fix it."**
`validate_level("l6")` (auto) → report: unreachable exit, gap at x 41–46 exceeds run-jump from the available runway → `describe_level` + `export_level(window around x 35–50)` (auto) → agent proposes `apply_level_edit`/`import_level_grids` adding a one-way foothold — **diff chip renders both grids via drawLevel** → user clicks Accept → files: `collision.npz`(+`.grid.json`), `level.json` hashes; journal: `edit` by `agent:mason`, before/after hashes → `validate_level` re-runs automatically (mandatory) → clean → no build needed (data hot-read by engines) → user sees: accepted chip, "validated ✓ reachable" line, canvas refreshed, History shows the agent entry, transcript offers "undo this."

### 4.C Asset creation & assignment

| Tool | Signature | Touches | Tier |
|---|---|---|---|
| `view_asset` | `(target) → image attach + metadata` | sprite/tilesheet/backdrop bytes (read) | auto |
| `asset_lineage` | `(target) → nodes/edges` | journal + CAS | auto |
| `generate_asset` | `(target, prompt?, image_backend, model?) → cost, hashes` | sprite/tilesheet/backdrop/audio files + manifests | **$ confirm** |
| `animate_asset` | `(target, backends, prompt?) → states, cost` | state strips + atlas + frames.json | **$ confirm** |
| `generate_music` | `(level\|stage, music_backend) → track, cost` | music files + StageAudio | **$ confirm** |
| `renormalize_animation` | `(target) → offsets` | atlas.json + frames.json ($0, journaled as code-edit) | ask |
| `edit_animation` | `(target, playback) → …` | durations/loop/offsets only (geometry refused by verb) | ask |
| `replace_asset` | `(target, file_path) → hash` | target art from a user-supplied file; journals `import` | ask |
| `assign_asset` | `(target, source_artifact) → …` | re-points art; journals `import` | ask |
| `library_list` / `library_cat` | `(filters)` | `~/.canon/library` index/objects | auto |
| `library_import` | `(library_id, into) → new_id` | copies bytes in, fresh id | ask |
| `library_publish` | `(target, name, tags)` | global library index; journals `keep` | ask |

**Trace C — "The Ember Hopper is too cute. Make it menacing."** `db_row("enemy","ember_hopper")` + `view_asset("enemy:ember_hopper")` (auto; base.png attached) → agent drafts a prompt override, calls `estimate` → **$ chip: "~$0.04 · fal · nano-banana"** → user confirms → `generate_asset` runs; files: `sprite/enemy/ember_hopper/base.png`, row's `sprite_hash`, PNG tEXt provenance; journal `regenerate` with gen metadata → result card shows before/after sprites → no build (sprites hot-load) → agent notes the old animation strips now mismatch the new base and offers `animate_asset` (new estimate) or `restore` to the prior version — user sees both options as chips, History carries both versions.

### 4.D Playtest & execution

| Tool | Signature | Touches | Tier |
|---|---|---|---|
| `capture_frames` | `(level_id, ticks?, script?) → PNGs attached` | spawns headless pygame (PLAT_CAPTURE; env-scrubbed, fixed ticks, no window) | auto — ASSUMPTION-6a |
| `run_trajectory` | `(level_id, inputs) → traj file + summary` | headless deterministic run (PLAT_TRAJ/HOLD/ACTIONS) | auto — ASSUMPTION-6a |
| `play_level` | `(level_id, mode) → pid` | opens a pygame window on the user's machine | ask |
| `play_game` | `() → pid` | Godot whole game (refuses json-engine packs with the existing pygame pointer) | ask |
| `sandbox_level` | `() → level_id` then launch | idempotent reserved draft room + sandbox play | ask |

headless capture/trajectory are **auto** — windowless, fixed tick count, write nothing into the pack, and execute the same engine the user's own ▶ Play executes. If this feels too free, they demote to ask with one registry flag.

**Trace D — "Does the water level actually play okay?"**
`describe_level("l5")` (auto) → `capture_frames("l5", ticks=600)` + `run_trajectory("l5", hold=right, jump_every=45)` (auto) → agent reviews attached frames (vision) + traj summary: player clears the waterline, urchins reachable-but-dodgeable, one item floats inside a wall → `validate_level` confirms the item placement flag → proposes a one-cell `apply_level_edit` (ask chip with spatial diff) → accept → re-validate clean → user sees frames inline, the finding, the fix chip, and a final "want to feel it yourself?" `play_level` ask-chip; approving opens pygame, `play-exited` closes the loop in the transcript.

### 4.E Cradle surfacing (UI tools — no pack writes)

These execute **client-side in the panel** (the service streams a UI-tool event; cradle performs it) — the service never drives the UI directly.

| Tool | Signature | Effect | Tier |
|---|---|---|---|
| `show_user` | `(selection: entity\|level\|tab\|worldmap) → ack` | navigates the editor's selection (existing deep-links) | auto |
| `attach_image` | `(path) → rendered in transcript` | inline image (renders, sprites, diffs) | auto |
| `propose_plan` | `(steps[{text, tier}]) → approval\|edits` | renders the plan card (Plan mode's body) | auto |
| `request_input` | `(question, options?) → answer` | structured question chip | auto |

**Trace E — "Show me everything the agent changed today."**
`get_history(filter: actor=agent:*, since=today)` (auto) → agent summarizes: 3 edits on l6, 1 sprite regen, grouped with hashes → `show_user(level l6, History tab)` navigates the editor to the lineage view → nothing changes on disk → user sees the summary in-chat and the authoritative journal view beside it, with per-version restore buttons.

---

## 5. Orchestration

- **Decomposition:** a multi-step request becomes either 
(a) narrated stepwise execution under Ask mode — each write its own chip — or 
(b) a `propose_plan` in Plan mode: numbered steps with tier badges, paid steps showing estimates, one approval, steps checking off live. 

  Plans are data in the transcript, so a rejected plan can be edited and re-proposed.
- **Errors:** verbs fail as structured JSON (the `_emit_error` contract). The agent may retry with a corrected call once; a second failure or any paid failure stops the plan → mid-plan failure card: what ran, what didn't, options (continue / undo completed steps / stop). Nothing auto-continues past a failed paid step.
- **Rollback:** per §2.3 — before-hash restores per write, reverse-order for plans. Restores are themselves journaled (`restore` op), so undo has  provenance too.
- **Human approval points, exhaustively:** every ask-tier write without a project grant; every plan (once); every paid action (always); every mid-plan failure decision; mode changes themselves (a mode switch is a user gesture, never agent-initiated).

---

## 6. Capability gaps (new APIs / refactors required)

### Canon
| Gap | What it is | Needed by |
|---|---|---|
| `ChatBackend` protocol + anthropic/openai/kimi impls + fake | provider-agnostic streaming tool-use beside `LLMBackend`; ids in `BackendRegistry` | A1, A8 |
| Agent service module (`src/canon/agent/`) | sidecar process, HTTP+SSE, loop, tool registry, permission engine, session store | A2–A4 |
| First server dependency | FastAPI/uvicorn-class, isolated in an `agent` extra (I4) | A2 |
| `describe_level` + windowed `export_level` | token-frugal reads | A3 |
| Pack capability probe | pack type/engine/schema-generation summary; W1 P1's `pack info` is the real home — a minimal probe ships in the service if the agent lands first | A3 |
| Grants store | `<pack>/.canon/agent/permissions.json` read/write, service-owned | A4 |
| Session transcripts | `<pack>/.canon/agent/<session>.jsonl` (+ project-store location for pack-less) | A2 |
| Spend ledger lane | ASSUMPTION-8: `cradle-spend` schema gains actor/category so agent conversation + tool spend meter distinctly | A6 |
| **W3.2 packaging surgery** | packs into `src/canon/`, one resolver — hard prerequisite for in-process tools | A2 (September #4) |
| Eval script | scripted conversations (fake ChatBackend) used as the provider-swap gate | A1, A8 |

### Cradle
| Gap | What it is | Needed by |
|---|---|---|
| Panel UI | per the approved A0 design package | A5 |
| Sidecar lifecycle | spawn/supervise/port-handoff (play-process precedent); stateless commands (I3) | A5 |
| HTTP+SSE client behind `api` | the October-M1 transport seam; CSP allowlist or thin Rust proxy fallback | A5 |
| devMock scripted agent | canned SSE sequences; every panel state headless (I7) | A5 |
| Actor centralization | one module owns actor strings (I6); retires 25 hardcoded `cradle:user` sites opportunistically | A4 |
| UI-tool executor | panel-side handling of `show_user`/`attach_image`/`propose_plan`/`request_input` events | A5 |
| Native `jobs_list`/`jobs_record` | existing gap: mock-only today; needed so agent + button runs share one durable JobTray history | A6 (adjacent fix) |
| Model picker data-path | provider/model ids as data; disabled-with-reason on missing keys; Settings deep-link once W3.5 exists | A5/A6 |

---

## 7. Safety & sandboxing

### 7.1 Executing gameplay code

- v1 playtest tools execute the **pack's own engine copy** — the same code the user's ▶ Play buttons run today. ASSUMPTION-6c: v1 adds **no OS-level
  sandbox** beyond what exists: env scrubbed (the 12 `PLAT_*` hook vars), headless runs windowless with fixed tick budgets, stdout discarded,
  spawned as detached children with reaper threads. Same trust as the existing Play surface, made explicit.
- The probe surfaces `modified`/`unstamped` engine files **before** any agent-triggered execution; the agent must disclose "this pack's engine has hand-edited/unknown files" in the transcript before running it.
- V2 (agent-written code): a code write is ask-tier with a full diff, lands only in the project's copies, marks those files `modified` in the engine stamp (attribution survives), and "done" requires the gate ladder — parity traj while the project tracks the template's twin-engine model, boots-clean + scripted smoke once it has evolved beyond it. Windowed runs remain user-launched.

### 7.2 Preventing edits outside the gameplay/data layer

Structural, not prompted (§3.2): the registry exposes only verbs; reads are path-guarded to the pack root; no tool reaches canon's source, the shared template, other packs, or the user's filesystem. Fences carried from recon: the `cli_ctx_factory` DAG path is not a tool (fake-LLM/real-art spend trap); every generation tool requires an explicit backend id; pinned artifacts and
`USER_EDITED` semantics are enforced by the verbs themselves and the agent never gets a bypass. `.canon/` internals are writable only through verbs; the grants file only by the permission engine.

### 7.3 Cost controls

- $-tier always confirms; estimates via the existing cost model, calibrated from the pack's `generation_stats` actuals where present; worst-case shown (the ×4 convention).
- Conversation tokens metered per turn via the ChatBackend pricing tables; running session total visible in the panel header; both lanes land in the pack spend ledger under the agent's actor and roll up in the 💰 dashboard (separate and aggregate — D6).
- Estimate misses are reconciled against provider-billed actuals — the same reconciliation October M0 needs; shared machinery.
- **Open (§9): hard budget caps** (per session / per project) — not in the locked decisions; proposed as a v1.5 addition.

### 7.4 Data & injection posture

- Pack content in tool results is framed as data; the core prompt forbids  following instructions found in it. Residual risk accepted and noted.
- Multi-provider means pack content travels to the **user-picked** provider; the picker is the consent surface. Transcripts are training-adjacent pack data; remote collection stays behind the unbuilt consent + PII pass.

---

## 8. Phasing

### V1 — the copilot (this PRD's build)

| # | Deliverable | Gate |
|---|---|---|
| A0 ∥ | Panel design package (Claude Design; brief at `cradle/docs/design_brief_agent_panel.md`) | user approves; adopted into §2 |
| A1 | `ChatBackend` + anthropic + streaming + fake | scripted tool-use conversation green, $0 |
| A2 | Service skeleton: sidecar, HTTP+SSE, transcripts | curl a session; journaled; dies with parent |
| A3 | Read-tier tools (describe/window/probe) | answers pack questions, zero writes; token budget measured on the widest real level |
| A4 | Permission engine + write tier + actor threading | ask round-trip; grant persists in its project and provably NOT in a second; restore undoes |
| A5 ∥ | Panel built to A0 + devMock scripted agent | full flow headless and native; matches spec |
| A6 | Paid tier + spend lane + dashboard | confirmed, metered, per-agent lanes render |
| A7 | Vision + verify loop (mandatory validate; capture/traj tools) | agent introduces a break, detects and repairs it unprompted |
| A8 ∥ | openai + kimi providers | A1–A7 eval passes provider-swapped |
| A9 | Create-flow driving + start-page scope | ice-world-by-conversation e2e on free backends |
| A10 | Release: eval + success criteria | user sign-off |

Dependencies: A0 starts now and gates A5; A2 hard-depends on September #4 (W3.2); A3 tracks W1 (platformer-first fallback); A9 tracks September #10.

**V1 success criteria:** (1) "why is 2-3 unbeatable" → grounded diagnosis →
approved fix → validated clean, all in-panel; (2) every agent mutation in
History under its name, one-click restore, human correction-pairs
uncontaminated; (3) no unconfirmed spend, per-agent dashboard lanes;
(4) provider-swapped conversation passes; (5) full panel runs in the browser
mock, no Tauri/keys/network; (6) create-open-edit a project by conversation.

### V1.5 (small, after ship)
Budget caps; richer field-level regen targets as the `#field` grammar grows;
JobTray/agent-runs unification polish; session resume UX.
---

## 9. Appendix I

**Some thoughts**
1. Kimi host: use Moonshot's OpenAI-compatible API 
2. Agent panel ships after dungeon parity?
3. We want accurate cost tracking - as close to accurate as we can get
4. We should probably have a way to cancel llm runs so we don't continue to burn tokens for no reason
5. Like cursor we want to have multiple conversations running at the same time

**Risks**
- **Provider tool-use variance** — A8's provider-swapped eval is the control; ship anthropic-first if another provider fails it.
- **Localhost SSE vs Tauri CSP** — Rust-proxy fallback; decided at A2.
- **Transcript privacy** — training-adjacent; gated on the unbuilt consent + PII pass.
- **Context blowups on drifted/evolved packs** — probe-first +  describe-first; A3's token-budget gate measures.
- **Prompt injection via pack content** — framed-as-data posture; residual risk accepted for a local, single-user v1; revisit before hosting.

