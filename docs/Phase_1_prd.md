> **⚠️ Superseded where it disagrees by `September_master_prd.md` (§6 is the collision table, §8 the decisions); this doc remains the spec-prose holder for un-flipped rows.** — signed off 2026-09-01

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
7. The chat is also a **thinking partner**: ask anything about the project (or about canon/cradle themselves), and brainstorm — gameplay ideas, story directions, level and boss concepts — **grounded in the real project state** via free reads. Pure ideation costs only conversation tokens; ideas become reality exclusively through the same gated write path ("do it" → plan → chips). Brainstorm transcripts persist with the session.

### 1.3 Anti-goals & scope boundaries (v1)

- **Not a new write path.** The agent mutates packs exclusively through canon verbs. It never writes pack files directly or changes the template; "cradle never writes pack files" extends to the agent verbatim.
    - we have a specical case here where now that the agents can improve/write new game code we need a path for this - see how canon does it. 
- **Not a pipeline replacement.** It drives the existing per-entity ops and create flow; it does not touch `run/resume/regen` DAG verbs (fenced, §7.2).
- **Still focus on local app.** The service is local, single-user, dies with cradle. Hosting, accounts, and Demi metering are deferred til later.
- **Not the dialogue tester, not the sandboxes.** September row #9 and Phase 2 own those; and will be expanded on later
- **Not template editing.** Templates are seeds (three-tier model). Even in V2, code tools touch only the project's own copies. This extends to gameplay as well - never change the template, only the project. 
- **Game-type boundary:** v1 capability = whatever the registry serves. Platformer full; Dungeon Crawler

---

## 2. User experience — ✅ adopted from the A0 design package (2026-08-29)

**Normative source: `cradle/design_handoff_agent_panel/`** (README = full
interaction spec with all state tables and copy; PLAN = implementation map,
component tree, store slices, and the 11-step build order A5 follows; 7
artboards, both themes, dark-first, panel drawn at 412px). This section
carries the adopted decisions; the package carries the pixels. Three
deliberate deviations are logged in Appendix I.

### 2.1 The column

Third shell column right of main: **default 412px, min 340, max 720**, width
persisted per user; 4px drag handle (accent while dragging, mono width
readout, double-click resets). **Collapsed is a 40px rail, not a hidden
panel** — expand button, one glyph per open conversation carrying its tab's
status dot, session cost rotated at the bottom. Toggle: TopBar button +
`⌘⇧A`; command palette "Ask agent…". The canvas absorbs all width loss (nav
and status bar never change); below 900px of remaining main width the
editor's floating panels reflow inward; below 720px the panel auto-collapses
to the rail with a one-time toast — a resize-only rule that never fights an
explicit re-expand. **NotesDrawer floats above the panel**, dimming it to
60% and blocking its input while open; `Esc` closes notes first, then stops
the agent. Focus mode hides the panel; the rail returns on exit.

**Header:** `Ask · Plan · Allow` segmented control (current mode filled,
always visible), mono model picker (grouped by provider, per-1M pricing on
every entry, unavailable entries at 50% with the reason + `Add key`,
model per conversation), running session cost, `⏹ Stop` while anything is in
flight. **No agent picker.**

### 2.2 Conversations

Cursor-style tabs with status dots: accent pulsing = streaming, amber =
waiting on an approval, red = errored unread, none = idle. Waiting tabs sort
ahead of idle; running tabs never re-sort mid-run. Middle-click closes (a
live run confirms first); `+` / `⌘⇧N` new; `⏱` opens per-project session
history with dates and costs. The status bar shows the active conversation's
specialist and `+N` when others run. **First run** seeds three prompts drawn
from the actual project and one sentence of law: *it reads everything, and
asks before it changes or spends anything.*

### 2.3 Transcript, context, approvals, undo

The transcript reads as a **log, not a chat app**: user messages
right-aligned in bordered bubbles, agent text flush-left under a mono agent
label, markdown rendered, streaming caret, hairline session rules. **It is a
conversation before it is a tool runner** — most turns touch nothing; a
reply that needed a read carries it as one dim mono line; a judged reply
ends in up to three follow-up chips (suggestions, never a menu). Hovering a
user message: ✎ edit-and-resend (branches — truncates below), ↻ retry, ⧉
copy. **Composer `@`-context picker**: levels, actors, docs, what's-on-screen;
the current level attaches by default in the editor, nothing on the start
page.

Tool tiers per the package: **reads** = one collapsed mono line, `▸` to
expand, >6 fold into `read N artifacts ▸`; **writes** = bordered card, diff
always visible before the chips — three renderers: *spatial* (side-by-side
mini-canvases via the same pure `drawLevel`/`canvasTheme` path as the
editor, so preview and canvas cannot disagree; integer scale,
nearest-neighbour), *fields* (old struck-red → new green, unchanged
hidden-count), *code* (real unified diff with hunk headers) — each with
**Show me**; **paid** = the only accent-outlined card, four states: estimate
(price inside the Accept button — `Accept · spend up to $X`; backend and
model named; today's spend for context), running (phase + item + `i/N`,
ticking elapsed, *spent so far $A of $B*, `⏹`), result (actuals, thumbnails,
Show me), **stopped** (*stopped by you at 0:52*, billed amount, what was
kept vs never started, `Finish the last one` / `Undo all`).

**Permission chips** sit inline where the action would happen, inside the
run card that wants it. Copy is always *"‹Specialist› wants to ‹verb›
‹target›."* Buttons: `Accept · Always allow in this project · Reject` — the
middle label is never shortened (the scope rides in the words), and its
footnote names the exact tool and project. States: already-granted = no
chip, a quiet `✓ …allowed in this project` mono line; rejected = collapses
to what did *not* happen + `Allow after all` / `Tell it why`; **in Ask mode
the middle button renders disabled-with-a-reason** (grants are made in Allow
mode); paid never shows it. Grants revoke in **Settings → Permissions**
(per-project list: tool, granting specialist, when; `Revoke` / `Revoke all`;
revoking undoes nothing already done). Grants persist at
`<pack>/.canon/agent/permissions.json` (deviation 1 — see Appendix).

**Undo**, three grains, unchanged: per-artifact restore in History; "undo
this" on any write card (before-hash); "undo this plan" (reverse-order,
one History entry via the batch id). Paid spend is not reversible — undo
restores content, the ledger keeps the cost row.

**"Agent changed this" — three sightings of one fact**, all carrying
`agent:<name>/<specialist>`: (a) the transcript — `Show me ↗` on every
write card and a **change feed** after a batch (one row per artifact, typed
prefix, deep-link that opens, selects, and pulses the target; footer
`▶ Play · Undo the batch · Open in History`); (b) the editor — a dismissible
accent pill over the canvas and an accent dot in the left nav on artifacts
touched this session; (c) History — normal rows attributed to the specialist,
paid rows carrying cost, a plan batch as one expandable undoable entry.

### 2.4 Long-running work, stop, start page, cost

Paid generation runs inside the service and streams the pack's StepLog (`.canon/log.jsonl`) into the transcript card — the same phase/item/count/elapsed folding `CreateProgress` proved. The user can collapse the panel, navigate anywhere, or keep editing; on completion the card shows result + actual cost and offers "show me" (navigates selection to the target). Runs survive panel collapse but **not app quit** (service dies with cradle — same as today's create). **Cancel is v1** (Appendix I.4; mechanics §5.5): ⏹ Stop aborts the provider stream immediately — no further token burn — and halts the loop before its next tool call. A multi-item paid verb already executing stops at the next item boundary (a cancel flag checked at the same loop points that emit `node_item` — a small canon hook). Completed items keep their files and their cost; cancelled runs journal as cancelled. **Stop covers button-started jobs too (decided 2026-08-29):** a queued JobQueue job cancels outright; a running one stops at the same item boundary, with the flag wired through the Rust worker; JobTray and CreateProgress gain Stop controls — the old B4 no-cancel debt closes in this phase. Button-driven jobs continue on the Rust JobQueue unchanged; agent runs and button runs both land in the pack's spend/jobs ledgers so the JobTray's
durable history stays one list (requires the native `jobs_list`/`jobs_record` fix, §6).

**Stop is one verb in three places, same contract** — start nothing new, keep
what landed, say what it cost: the conversation header (`esc` from the
composer stops the reply and every run beneath it), the per-run-card `⏹`
(stops that run only), and the job tray, where editor-launched and
agent-launched jobs share one tray with an attribution column and per-row
`⏹`.

**Start page (adopted):** same column over the hero. **Allow mode is
disabled with the reason in the header** (*no project open — grants are per
project*); Ask and Plan only. Create is a conversation, not a modal: at most
two clarifying questions, then a numbered plan whose button reads
`Create · up to $X` beside `Edit steps` / `Start blank instead`; *a folder
is written to disk before anything is spent — you can stop at any step and
keep what exists.* While creating, the recents rail shows a live project
card and the status bar mirrors it. NewProjectModal remains as the button
route; both feed CreateProgress.

**Cost dashboard (adopted):** counts **every generation in the project,
whatever launched it**. Four tiles (total / generation / conversation /
today), a you-vs-agent split bar, then three tables: **by kind** (image,
animation, video, code, audio — each naming backend + model, money split
you/agent/total, run counts), **by identity** (`you · editor buttons` and
the agent with specialists nested; **tokens and generation are separate
columns** — the cost of thinking vs the cost of making; human rows have no
token entry), and **by conversation**. Every row is one journal entry so the
tables always reconcile; unconfirmed estimates never count; stopped runs
count what they billed; a new generation kind is a field value, not a
schema change.

---

## 3. Agent design

### 3.1 System prompt strategy

Four layers, assembled per session by the service:

1. **Core (static, versioned in canon):** identity and law. Encodes the house doctrines as behavior: verbs are the only hands; **code computes, the LLM designs** (prefer validate/repair verbs over hand-tuned geometry; never eyeball what a validator can prove); paid must be visible before it happens; **probe, never assume** (pack drift is real); never claim done without the mandatory post-mutation validation; surface disagreement with the user's premise rather than silently complying.
2. **Pack context (per session):** the capability probe result (pack type, engine status incl. modified/unstamped files, schema generation, counts, validation summary, spend to date). Rebuilt on session start and after external changes.
3. **UI state (per message):** open selection, active tab, dirty layers, current mode — supplied by cradle with each user message; only the latest copy is kept in context. Plus the user's `@`-attached context (levels, actors, docs, what's-on-screen — the current level attaches by default in the editor, nothing on the start page).
4. **Specialist layer (per run):** the acting specialist's role prompt (§5.2) plus any matched **skills** (§5.3 — the user-editable layer). The conversation's name (e.g. `mason`) is an attribution label, not a behavior switch; behavior variety comes from specialists and skills, and the router — not the human — decides who acts.

The assembled prompt for any run is inspectable read-only in the panel (the `prompt show` / PromptOverride house precedent); the core and specialist roles are not user-editable, skills are (§5.3).

A base philosophy for Canon is Skeletal-Driven Generation. We create templates and schema/tables and utilize typing and specific fields in the data where applicable - meaning the llm can never hallucinate an enemy type, or movement type, as these sorts of things will be defined by the template/user and can be expanded with edits (even agentically) but never submitted to generation without clear guidance. The llm generations then focus on gaurd railed areas like game code, or descriptions, or generations that can be validated though our validators and play testors. 

### 3.2 Scope enforcement

The prompt states the law; the **tool registry is the law's enforcement**. The model physically cannot: run shell, write arbitrary files, read outsidethe pack root (path-guarded, the `data.rs` precedent), reach the DAG/regen verbs, imply a paid backend, or bypass a tier gate — because no such tool exists and tiers are checked service-side, not model-side. Prompt-injection
posture: pack content (flavor text, briefs, names) arrives in tool results framed as data; the core instructs that instructions inside pack data are never followed. Residual risk noted in §9.

### 3.3 Model selection UX

- Panel header: provider + model picker for the **foreman** (the conversation's model). **Ids are data** from `BackendRegistry` — Claude, OpenAI, Kimi (via Moonshot's OpenAI-compatible API — Appendix I.1), later `demi`.
- Each entry shows its per-1M pricing (the existing PRICING tables); missing-key providers render disabled-with-a-reason naming the env var, deep-linking to Settings once W3.5 ships.
- **Specialists carry their own model defaults in the roster config** (§5.2) — the `models.json` agent-tiers precedent applied to agents (cheap for writer re-completes, top for level design and code). Overridable in config, not per message.
- No automatic tier-routing of the foreman's model in v1; one user-picked conversation model per session. Generation tools keep their own per-call backend/model flags, chosen in the confirm chip (defaulting to the surface's current backend selection conventions).
- **Decided 2026-09-01 (user):** the chat backend's code default is Sonnet (`DEFAULT_CHAT_MODEL = "claude-sonnet-5"`, a data value); the *effective* default resolves **project settings → cradle (global) settings → code default**, built with the Settings screen (P0-12) and A5's picker. The picker also offers a per-model **reasoning** toggle (Cursor's model / model-with-reasoning pattern); the backends' `reasoning` (openai/kimi) and `effort` knobs are the data behind it. Refusal fallbacks are ON by default in the anthropic chat backend (server-side `fallbacks: "default"`), a constructor flag.

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

### 4.A Codebase ops (project files; code-write ships in v1 via `game_coder`)

| Tool | Signature | Touches | Tier |
|---|---|---|---|
| `list_pack_files` | `(glob?) → paths+sizes` | pack tree (path-guarded) | auto |
| `read_pack_file` | `(path, range?) → text\|json` | any pack file incl. the project's own `godot/main.gd` | auto |
| `search_pack` | `(query, glob?) → matches` | pack text/JSON | auto |
| `engine_status` | `() → current/stale/modified/unstamped per file` | `godot/.engine.json` vs template | auto |
| `engine_sync` | `(dry_run, force=false) → written/refused` | pack runtime files (fail-closed on hand-edits) | ask |
| `edit_project_code` | `(path, diff) → new_hash` | the project's **copy** of gameplay code (`godot/main.gd` etc.) — stamped `modified` for attribution; the shared template and canon's pygame harness are unreachable | ask + §7.1 gate ladder |

**Trace A — "What does the flyer actually do when it loses sight of me?"**
User asks → `read_pack_file("manifest.json", rules.flyer)` + `read_pack_file("godot/main.gd", flyer section)` (auto, quiet cards) → agent explains: hold altitude, committed dive parabola, horizontal leash from `patrol_range × leash_mult`, citing the row's values → no files change, no build → user sees a cited answer with a "show me the row" link that navigates to the enemy's Overview. *(Also demonstrates: reads never prompt.)*

**Trace A2 — "Give the double jump a floatier apex." (code, v1)**
Foreman delegates to `game_coder` → `read_pack_file("godot/main.gd", jump/gravity section)` (auto) → coder proposes an `edit_project_code` diff to the pack's own `godot/main.gd` — full-diff ask chip → accept → files: `godot/main.gd`; `godot/.engine.json` now lists it `modified` (attribution survives; `engine sync` will refuse to overwrite it) → **the pack is now code-evolved**: the probe flags that pygame surfaces show template physics for this project, so the gate ladder runs Godot-side — headless boot (grep `SCRIPT ERROR`, exit codes lie) + scripted smoke via the Godot `PLAT_*` mirror + `validate_level` on affected levels → green → user sees the diff, the gate results, and a `play_game`/`play_level` ask-chip to feel the apex; journal carries `edit` by `agent:<conv>/game_coder` with before/after hashes — one-click restore reverts the file and clears the evolved flag.

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

## 5. Orchestration — the multi-agent system

### 5.1 Shape: one loop, many configurations; a foreman and specialists

The service implements **one** agent loop. An "agent" is a configuration of
it: `(role prompt, tool subset, model default, actor name)`. Two kinds of
instance:

- **The foreman (orchestrator).** One per conversation; the only agent the
  user ever talks to. Owns the transcript, the plan, every permission chip,
  and the session's spend rollup. Tools: all reads, the UI tools,
  `propose_plan`, and `delegate`.
- **Specialists.** Never talk to the user, never to each other. A specialist
  executes a bounded **task run**: fresh context (core law + pack probe + a
  task brief + explicit refs — level ids, artifact ids, prior findings), a
  restricted tool subset, its own model default. It returns a structured
  result — summary, artifacts touched (ids + hashes), costs, attachments —
  which the foreman folds into the conversation.

**Routing is the foreman's tool choice.** Specialists are exposed to the
foreman *as tools*: `delegate(specialist, task, refs, budget?)`. The task
determines the agent — no human picks. Routing quality is therefore prompt +
eval work on the foreman, not new machinery. This is also the context win:
specialist runs absorb the token-heavy material (grids, frames, DSL) in
their own windows and hand back summaries, so the conversation stays lean.

### 5.2 v1 roster (config, not code)

| Specialist | Tool subset | Model default | Role |
|---|---|---|---|
| `foreman` | all reads + UI tools + plan + delegate | header-picked | the conversation; decompose, route, report |
| `level_designer` | 4.B build ops + validate + describe/export + capture | top tier | geometry, placements, per-level overrides |
| `artist` | 4.C asset ops + view/lineage (vision-heavy) | mid tier | sprites, tilesets, backdrops, audio gen |
| `writer` | db text fields (`update_row`/`complete_row` llm_fields); story/dialogue surfaces as W1 §7 lands | cheap/mid | names, flavor, briefs, dialogue |
| `playtester` | 4.D headless + validate + vision; **no writes** | mid | findings only — the QA voice behind goal 2's "surface with opinion" |
| `game_coder` **(v1)** | 4.A + `edit_project_code` + boot/smoke/traj gate tools | top tier | goal 6's code path; project copies only; operates under §7.1's gate ladder |

A roster entry is a **config file, not code** — role prompt + tool allowlist
+ model tier. Adding a specialist is a data change; that is what keeps
multi-agent affordable (§5.6). Model tiers reuse the `models.json`
agent-tiers precedent verbatim.

### 5.3 Skills — the user-buildable layer

A **skill** = a markdown instruction file + a declared tool allowlist (⊆ its
host specialist's) + optional model preference + a one-line trigger
description. Stored in the project store (`~/CradleProjects/.cradle/skills/`)
or per-project (`<pack>/.canon/agent/skills/`); project-local wins — the
`schemas/<type>.json` override idiom applied to agents.

Two attachment modes: **(a) augment** — matched skills load into the acting
specialist's prompt for that run ("our levels always leave 2 tiles of
headroom above platforms"); **(b) routable** — a skill with its own tool
allowlist appears in the foreman's delegation menu as a lightweight
specialist. A skill can never widen permissions: allowlists intersect and
tiers still apply. ASSUMPTION-13: this format + precedence — ratify.

### 5.4 Permissions, attribution, spend across agents

- **Chips bubble.** A specialist hitting an ungranted ask-tier write pauses
  its run; the chip renders in the one transcript, naming the specialist
  ("Level designer wants to import grids — diff"). Specialists can never
  self-approve; paid confirms always bubble.
- **Grants govern actions, not agents** — a project grant applies whichever
  specialist fires that action kind. Unchanged from D5.
- **Actors:** `agent:<conversation>/<specialist>` (e.g.
  `agent:mason/level_designer`). D6's tracking gets more granular for free —
  ledger and journal filter by conversation, by specialist, or aggregate.
  The conversation name is auto-assigned at session create and renamable; it
  is a label for attribution, **not a picker** (§2.1).
- Session spend rollup = foreman turns + every delegated run.

### 5.5 Concurrency, decomposition, errors, cancel

- **Within a conversation:** the foreman may run independent delegations in
  parallel (artist + writer; per-level fan-outs), capped (default 3). The
  service holds a **per-pack write gate**: verb writes serialize per
  artifact/level so two runs never interleave multi-step edits on one
  target; read/finding runs are unrestricted.
- **Across conversations (Appendix I.5):** sessions are independent
  transcripts + SSE streams over the same service; the same write gate
  serializes cross-session writes; grants and ledgers are per-project and
  shared. UI: conversation tabs (§2.1); a conversation's chips render only
  in its own transcript.
- **Decomposition:** a multi-step request becomes either (a) narrated
  stepwise execution under Ask mode — each write its own chip — or (b) a
  `propose_plan` in Plan mode: numbered steps with tier badges, paid steps
  showing estimates, one approval, steps checking off live. In multi-agent
  terms a plan step is either a foreman tool call or a delegation, and the
  plan card names the specialist per step. Plans are data in the transcript,
  so a rejected plan can be edited and re-proposed.
- **Errors:** verbs fail as structured JSON (the `_emit_error` contract).
  Inside a run, one corrected retry is allowed; a second failure or any paid
  failure stops the run. A failed delegation returns a structured failure to
  the foreman, which may retry-with-fix (once), re-route to a different
  specialist, or surface the mid-plan failure card: what ran, what didn't,
  options (continue / undo completed steps / stop). Nothing auto-continues
  past a failed paid step.
- **Rollback:** per §2.3 — before-hash restores per write, reverse-order for
  plans; a plan's writes may span specialists and "undo this plan" walks the
  same reverse-order hash list regardless of who wrote. Restores are
  journaled (`restore` op), so undo has provenance too.
- **Cancel (Appendix I.4):** ⏹ Stop = abort the provider stream + halt the
  loop before its next tool call — per conversation or per run, v1. An
  in-flight multi-item paid verb stops at the next item boundary via a
  cancel flag checked where `node_item` is emitted (small canon hook).
  Cancelled runs journal as cancelled; completed items keep files + cost.
- **Human approval points, exhaustively:** every ask-tier write without a
  project grant; every plan (once); every paid action (always); every
  mid-plan failure decision; mode changes themselves (a mode switch is a
  user gesture, never agent-initiated).

### 5.6 What multi-agent costs vs a single loop

The loop, service, transport, permissions, tiers, and transcripts are
identical to the single-agent build — a specialist **is** the same loop
under a different config. Genuinely new: `delegate` + a run manager
(parallel cap, write gate, run lifecycle) ≈ one build-order item (A4.5);
the roster and skills are config + a loader; routing quality is prompt +
eval on the foreman (A7 grows); the panel adds run cards, conversation
tabs, ⏹ Stop, and specialist-named chips (A5 grows). Net ≈ **+30–40% on the
service/panel items, zero new architecture.** **The full roster ships in v1
(decided 2026-08-29):** writer and playtester are pure config on the same
loop; `game_coder` additionally brings its §7.1 gate harness and code-diff
cards (A7.5).

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
| Eval script | scripted conversations (fake ChatBackend) used as the provider-swap + routing gate | A1, A7, A8 |
| Run manager | `delegate` + run lifecycle, parallel cap (3), per-pack write gate, cancel flags at the `node_item` loop points | A4.5 |
| Skills loader + roster configs | specialist configs as data; `.cradle/skills/` + `<pack>/.canon/agent/skills/`, pack-local wins, allowlists intersect | A4.5 |
| Code-gate harness | `edit_project_code` op + Godot-side boot/smoke/traj gate tools + the code-evolved probe flag, packaged for `game_coder` | A7.5 |

### Cradle
| Gap | What it is | Needed by |
|---|---|---|
| Panel UI | ✅ designed — build to `design_handoff_agent_panel/PLAN.md`'s 11-step order (column/rail/resize first — "prove the reflow, the NotesDrawer stacking and the auto-collapse threshold before content"); the paid card replaces every `window.confirm` cost gate in the same change so two gates never coexist | A5 |
| Journal/ledger schema additions | `identity` (`user` \| `agent:<name>/<specialist>`), `costCents`, `genKind` (image \| animation \| video \| code \| audio), `batchId` for plan-batch undo; **editor-launched generations journal identically** (today they bypass this granularity and the dashboard can't reconcile) | A6 |
| Estimate/progress contract | estimates return `{low, high, backend, model, unitCount}` before any spend; runs stream `{phase, item, index, total, spentCents}`; a stopped run's partial spend is backend-reported, never inferred client-side | A4.5/A6 |
| Settings → Permissions pane | per-project grant list (tool, granting specialist, when) with Revoke / Revoke all; deep-linked from chip footnotes | A6 |
| Sidecar lifecycle | spawn/supervise/port-handoff (play-process precedent); stateless commands (I3) | A5 |
| HTTP+SSE client behind `api` | the October-M1 transport seam; CSP allowlist or thin Rust proxy fallback | A5 |
| devMock scripted agent | canned SSE sequences; every panel state headless (I7) | A5 |
| Actor centralization | one module owns actor strings (I6); retires 25 hardcoded `cradle:user` sites opportunistically | A4 |
| UI-tool executor | panel-side handling of `show_user`/`attach_image`/`propose_plan`/`request_input` events | A5 |
| Conversation tabs + run cards + ⏹ Stop | multi-session panel UI; nested specialist-run cards with collapsed sub-logs; cancel affordance; code-diff cards (A7.5) | A5 |
| JobQueue cancel | cancel for queued + running button jobs (the same item-boundary flag, wired through the Rust worker); Stop controls in JobTray + CreateProgress | A4.5/A5 |
| Native `jobs_list`/`jobs_record` | existing gap: mock-only today; needed so agent + button runs share one durable JobTray history | A6 (adjacent fix) |
| Model picker data-path | provider/model ids as data; disabled-with-reason on missing keys; Settings deep-link once W3.5 exists | A5/A6 |

---

## 7. Safety & sandboxing

### 7.1 Executing gameplay code

- v1 playtest tools execute the **pack's own engine copy** — the same code the user's ▶ Play buttons run today. ASSUMPTION-6c: v1 adds **no OS-level
  sandbox** beyond what exists: env scrubbed (the 12 `PLAT_*` hook vars), headless runs windowless with fixed tick budgets, stdout discarded,
  spawned as detached children with reaper threads. Same trust as the existing Play surface, made explicit.
- The probe surfaces `modified`/`unstamped` engine files **before** any agent-triggered execution; the agent must disclose "this pack's engine has hand-edited/unknown files" in the transcript before running it.
- **Agent-written code (v1, via `game_coder` — decided 2026-08-29):** a code write is ask-tier with a full diff, lands only in the project's copies, and marks those files `modified` in the engine stamp (attribution survives; `engine sync` then refuses to overwrite them). **A pack contains only the Godot engine copy** — pygame is a shared harness in canon's repo, template territory the agent cannot touch — so the first code edit moves the project off the twin-engine model: the probe flags it code-evolved, pygame-based tools demote to "template physics" advisories for that pack, and "done" requires the Godot-side ladder — headless boot clean (grep `SCRIPT ERROR`; exit codes lie), scripted smoke via the Godot `PLAT_*` mirror, and `validate_level` on affected levels. Windowed runs remain user-launched.

### 7.2 Preventing edits outside the gameplay/data layer

Structural, not prompted (§3.2): the registry exposes only verbs; reads are path-guarded to the pack root; no tool reaches canon's source, the shared template, other packs, or the user's filesystem. Fences carried from recon: the `cli_ctx_factory` DAG path is not a tool (fake-LLM/real-art spend trap); every generation tool requires an explicit backend id; pinned artifacts and
`USER_EDITED` semantics are enforced by the verbs themselves and the agent never gets a bypass. `.canon/` internals are writable only through verbs; the grants file only by the permission engine.

### 7.3 Cost controls

- $-tier always confirms; estimates via the existing cost model, calibrated from the pack's `generation_stats` actuals where present; worst-case shown (the ×4 convention). **Estimates and actuals cover every modality, not just LLM text**: LLM tokens, image calls, animation (image-edit per state + VLM authoring), VLM QA, music, SFX — per-op cost blocks split `llm/image/audio`; code generation is metered as the `game_coder` run's tokens in its own specialist lane; a future video backend inherits the same pattern via the backend Protocol + pricing table.
- Conversation tokens metered per turn via the ChatBackend pricing tables; running session total visible in the panel header; both lanes land in the pack spend ledger under the agent's actor and roll up in the 💰 dashboard (separate and aggregate — D6).
- Estimate misses are reconciled against provider-billed actuals — the same reconciliation October M0 needs; shared machinery.
- **Accuracy (Appendix I.3):** LLM/VLM token counts are provider-reported per call — *measured*, not estimated. Image/audio varies: PixelLab and Retro report real cost in their responses; **fal reports none today** — so every ledger row carries a `measured|estimated` flag, the dashboard renders the two distinctly, and closing fal's gap is a per-model price table behind the loud `_pricing_for` idiom (never a silent $0).
- **Open (§9): hard budget caps** (per session / per project) — not in the locked decisions; proposed as a v1.5 addition.

### 7.4 Data & injection posture

- Pack content in tool results is framed as data; the core prompt forbids  following instructions found in it. Residual risk accepted and noted.
- Multi-provider means pack content travels to the **user-picked** provider; the picker is the consent surface. Transcripts are training-adjacent pack data; remote collection stays behind the unbuilt consent + PII pass.

---

## 8. Phasing

### V1 — the copilot (this PRD's build)

| # | Deliverable | Gate |
|---|---|---|
| A0 | ✅ **Adopted 2026-08-29** — package at `cradle/design_handoff_agent_panel/` (README interaction spec + PLAN implementation map + 7 artboards, both themes). §2 rewritten from it; A5 builds to PLAN's 11-step order; 3 deviations in Appendix I | done |
| A1 | `ChatBackend` + anthropic + streaming + fake — **built 2026-09-01, awaiting human test + approval** (test plan `docs/test_plans/P1-A1_chatbackend.md`; code: `canon.llm.chat`, `canon.backends.chat_anthropic`, `FakeChatBackend`, `canon.agent.{loop,evals,eval}`) | scripted tool-use conversation green, $0 — **met on the fake (5/5)** |
| A2 | Service skeleton: sidecar, HTTP+SSE, transcripts — **built 2026-09-01, awaiting approval** (`docs/test_plans/P1-A2_service.md`; `canon agent serve` / `python -m canon.agent.service`, `canon.agent.{registry,permissions,conversations,service,providers}`) | curl a session; journaled; dies with parent — **met** |
| A3 | Read-tier tools (describe/window/probe) — **built 2026-09-01, awaiting approval** (`docs/test_plans/P1-A3_read_tools.md`; `canon.agent.tools_read` 12 tools, `canon level describe`, `--window`) | answers pack questions, zero writes; token budget measured on the widest level — **met on a generated fake tree (user's choice)**: describe ≈456 tok, 24×16 window ≈2.3k, full export ≈8.2k on a 123×26 level |
| A4 | Permission engine + write tier + actor threading — **built 2026-09-01, awaiting approval** (`docs/test_plans/P1-A4_permissions.md`; grants at `<pack>/.canon/agent/permissions.json`, `tools_write`, `canon.agent.actors` + cradle `src/lib/actor.ts`) | ask round-trip; grant persists in its project and provably NOT in a second; restore undoes — **met** |
| A4.5 | Run manager + full roster configs (foreman, level_designer, artist, writer, playtester) + skills loader + ⏹ cancel for agent runs AND JobQueue jobs | two specialists complete one approved plan; parallel runs never interleave a target; Stop halts token burn mid-stream and cancels a button-started job — **built 2026-09-01, awaiting approval** (`docs/test_plans/P1-A4.5_runs_cancel.md`; roster + skills/recipes loader, delegate + write gate, plans with batch undo, the one Stop contract incl. the cancel file at `node_item` and Child retention) |
| A5 ∥ | Panel built to A0 + conversation tabs + run cards + ⏹ Stop + devMock scripted agent | full flow headless and native; matches spec — **built 2026-09-01, awaiting approval** (`docs/test_plans/P1-A5_panel.md`; PLAN steps 1–9, the paid card replacing all 13 `window.confirm` cost gates, devMock scripted agent, sidecar lifecycle) |
| A6 | Paid tier + spend lane + dashboard | confirmed, metered, per-conversation and per-specialist lanes render — **built 2026-09-01, awaiting approval** (`docs/test_plans/P1-A6_journal_dashboard.md`; the one journal shape in `provenance.py`, `canon journal list`, tools_paid, the README §12 dashboard, Settings→Permissions pane, native jobs commands) |
| A7 | Vision + verify loop + routing eval | agent introduces a break, detects and repairs it unprompted; a mixed design+art request routes to the right specialists unaided — **built 2026-09-01, awaiting approval** (`docs/test_plans/P1-A7_vision_verify.md`; capture/traj/view_asset auto-tier, the mandatory post-mutation verify with one corrected retry, the routing corpus + break-and-repair) |
| A7.5 | `game_coder`: `edit_project_code` + Godot-side gate ladder + code-diff cards + code-evolved probe flag | a requested gameplay tweak lands in the pack's `godot/main.gd`, boots clean, passes scripted smoke + validate, stamps `modified`, restores one-click — **built 2026-09-01, awaiting approval** (`docs/test_plans/P1-A7.5_game_coder.md`; `edit_project_code` fenced to the pack's own engine copy, `canon engine gate` ladder proven against real Godot 4.7, code-evolved probe flag, one-click restore) |
| A8 ∥ | openai + kimi providers — **begun 2026-09-01**: `canon.backends.chat_openai` (one impl, ids `openai` + `kimi`), `openai` extra, hermetic tests; the paid provider-swapped run is user-run (`docs/test_plans/P1-A8_openai_kimi.md`) | A1–A7 eval passes provider-swapped (closes at stage 6) |
| A9 | Create-flow driving + start-page scope | ice-world-by-conversation e2e on free backends — **built 2026-09-01, awaiting approval** (`docs/test_plans/P1-A9_create_by_conversation.md`; the start-page panel variant, `create_project` driving the ONE create pipeline, at most two clarifying questions, stop-keeps-what-exists) |
| A10 | Release: eval + success criteria | user sign-off |

Dependencies: A0 starts now and gates A5; A2 hard-depends on September #4 (W3.2); A3 tracks W1 (platformer-first fallback); A4.5 follows A4; A7.5 follows A7; A9 tracks September #10. **Decided 2026-08-29 (Appendix I.2): build A1–A8 in parallel with W1, hold the A10 release until W1 P3 lands** — the agent debuts game-agnostic, platformer AND dungeon crawler on day one.

**V1 success criteria:** (1) "why is 2-3 unbeatable" → grounded diagnosis →
approved fix → validated clean, all in-panel; (2) every agent mutation in
History under its name, one-click restore, human correction-pairs
uncontaminated; (3) no unconfirmed spend, per-agent dashboard lanes;
(4) provider-swapped conversation passes; (5) full panel runs in the browser
mock, no Tauri/keys/network; (6) create-open-edit a project by conversation;
(7) two concurrent conversations edit different targets without interleaving,
and ⏹ Stop provably ends token spend mid-run — including a button-started job;
(8) a code tweak (a floatier double-jump apex) ships through `game_coder`
with the Godot gate ladder green and one-click restore.

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

**Resolutions (2026-08-29)**
1. Kimi via Moonshot's OpenAI-compatible API → §3.3 (the impl likely rides the openai `ChatBackend` with a different base URL + model id).
2. Ship order → proposed in §8 dependencies: build A1–A8 in parallel with W1, release (A10) after W1 P3 so the agent debuts game-agnostic. Confirm.
3. Accurate cost tracking → §7.3: `measured|estimated` flag per ledger row; token counts are measured; fal's missing cost gets a loud price table.
4. Cancel → designed, v1: §2.4 + §5.5 (stream abort + item-boundary cancel flag).
5. Multiple concurrent conversations → designed, v1: §5.5 (independent sessions over one service, per-pack write gate) + §2.1 conversation tabs.

Also resolved per user (2026-08-29): the named-agent **picker is removed** (§2.1) — names remain as attribution labels; routing is automatic (§5.1). ASSUMPTION-2 (personas) is superseded by specialists + skills (§5.2–5.3); new ASSUMPTION-13 covers the skills file format + precedence.

Question round 2 (2026-08-29, all decided): **`game_coder` ships in v1** (A7.5; §7.1's Godot-side ladder — note a pack carries only the Godot engine copy, so a code edit moves the project off the twin-engine model and pygame tools demote to template-physics advisories); **the full roster is in the release gate**; **⏹ Stop covers button-started JobQueue jobs** (the B4 debt closes this phase); **ship gate confirmed** — build ∥ W1, release after W1 P3.

**A0 adoption (2026-08-29).** The Claude Design package
(`cradle/design_handoff_agent_panel/`) was reviewed against the locked
constraints and adopted into §2 with three deliberate deviations:
(1) grants persist at `<pack>/.canon/agent/permissions.json` — pack-resident
truth — not the package's `.cradle/permissions.toml`; identical UX,
different store. (2) The missing-key copy will name the real key sources
(env file today, the September keychain + Settings once W3.4/W3.5 land),
not the package's `~/.cradle/keys.toml`. (3) Specialist ids stay snake_case
in actor strings (`level_designer`); the display layer may hyphenate.
ASSUMPTION-14: the mocks name the agent **"Wick"** (after the demo project
The Wandering Wick) — adopted as: the default agent name derives from the
open project's title and is renamable. Also adopted from the package:
`genKind` treats **video** and **code** as first-class generation kinds in
the cost model; the transcript's branching-edit semantics (edit-and-resend
truncates below); the follow-up-chips pattern; and the first-run seeded
prompts.

**Risks**
- **Provider tool-use variance** — A8's provider-swapped eval is the control; ship anthropic-first if another provider fails it.
- **Routing quality** — a misrouted task wastes tokens; bounded by A7's routing eval and the one-retry / re-route rule.
- **Localhost SSE vs Tauri CSP** — Rust-proxy fallback; decided at A2.
- **Transcript privacy** — training-adjacent; gated on the unbuilt consent + PII pass.
- **Context blowups on drifted/evolved packs** — probe-first +  describe-first; A3's token-budget gate measures.
- **Prompt injection via pack content** — framed-as-data posture; residual risk accepted for a local, single-user v1; revisit before hosting.

