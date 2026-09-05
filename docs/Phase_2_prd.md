> **⚠️ Superseded where it disagrees by `September_master_prd.md` (§6 is the collision table, §8 the decisions); this doc remains the spec-prose holder for un-flipped rows.** — signed off 2026-09-01

# PRD — Phase 2: Game Enhancement Sandboxes

**Position:**
September Phase 0 = registry, create, packaging, dialogue system, mazeworld parity (`September_Phase_0_prd.md`).
Phase 1 = the Cradle Agent (`Phase_1_prd.md`).
**Phase 2 = this** — the sandbox stages, the live game hook, the 2D/3D authoring system, and the full audio system. **Sequenced after Phases 0 and 1** (locked: dependencies + "it's a graphical overhaul"); only its design phase ran concurrently, and both design packages are complete and binding.

**Normative sources (binding, not repeated here):**
- `cradle/design_handoff_sandbox/` — 20 screens + 9 flows; README = interaction spec (state tables, exact copy, price table, undo rule), PLAN = 26-row build order, store slices, verb wrappers.
- `cradle/design_handoff_3d/` — the three-tier 3D system; README §2 = gate thresholds (design-owned, shipped as configured constants), PLAN = 12-row build order.
- The decision record (every S/R/A/L/M/D round, cited recon): https://claude.ai/code/artifact/cf45e6e0-57d1-4081-b7af-b2f19e578987

---

## 1. Problem & goals

### 1.1 Problem

Canon generates a game; cradle is where a human makes it good. Today, post-generation cradle shows data and assets, and the distance between *noticing* a problem while playing and *fixing* it is the entire product problem: physics constants are frozen into a manifest no verb writes, play sessions are fire-and-forget processes with their output discarded, art is generated but not paintable, characters have no 3D existence, audio has no authoring surface, and "what does this edit break" has no answer on packs cradle itself created.

### 1.2 Thesis and goals

**The game is the primary lens. Pack data remains the single source of truth, and every write is a canon verb.** (The first sentence is UX; the second is architecture. The PRD holds both — the thesis never inverts the write path.)

1. **The feel loop.** Play → feel something wrong → tune against the real level in a live session → apply → feel it again, inside the latency contract: generation legs are API-bound (honest elapsed/counts, no promise); the local loop is ours (constant swap ≤ 250 ms, relaunch ≤ 3 s, local rebuild ≤ 1 s); on-disk to on-screen ≤ 100 ms.
2. **Trace A′ end to end** (§2.9), every write journaled and restorable, on a platformer pack — and the same session shape works on a dungeon-crawler pack.
3. **Spatial authoring**: typed rect zones (physics/music/event; camera present-disabled) painted in build mode change a live session; the stepped kill plane replaces the flat death line.
4. **The character system**: anchor ladder (2D → unrigged mesh → rigged mesh), locks with re-anchor, expression sheets and skins, the 2D pixel editor (sprites + tilesets + joins + region recolour), and the three-tier 3D system — cradle establishes and judges, `mesh_smith` executes headless Blender surgery, manual Blender is the artistry escape hatch.
5. **Full audio**: music as level-cell timelines that *are* the zones, SFX bound by frame/event/place, in-place generation while the loop plays, Music/SFX editors as asset pages.
6. **The dialogue live scene**: Phase 0's docked tester drives a running game session in both runtimes; speaker portraits with moods are v1.
7. **Provenance completeness (v0)**: every generation's full input set — references by `@name` tag, entity/level context, prompt, params — recorded in the journal's gen block and visible in History, regardless of which door or actor initiated it.
8. **Staleness with a floor**: `--orchestrate` by default on new packs, `bible synthesize` for existing ones, animation sheets hash-tracked, cascades rendered as Phase 1 plan cards with blast-radius rollups. UI language is NR/behind, mapped onto canon's unchanged internal vocabulary.

### 1.3 Anti-goals & scope boundaries

- **The agent edits pack data and the project's own engine copies. Never canon's source, never cradle's source, never the shared templates.** Canon-core gameplay code is *ported into* a project at create/attach and evolves there (Phase 1's `game_coder`/`edit_project_code` model, now covering every engine copy). Shared-template and canon-core changes route through the dev.
- **No in-app mesh surgery — the three-tier commitment.** Weight painting, sculpting, and manual UV seam editing are not built; each appears only as a disabled control with its reason. Reversing this is an architecture change, not an implementation detail. `the agent does operations, not art` is load-bearing copy.
- **Nothing regenerates on its own.** An affected artifact is NR — it keeps working and keeps shipping. Cascades are proposed as plan cards, confirmed per step, cancellable with what-landed kept.
- **The sandbox never generates.** Editors make; the sandbox tunes and plays.
- **The asset library is designed, not built.** Backend untouched; the "how reuse should feel" conversation is a possible fast-follow.
- **Off-topic requests are declined** (inherited Phase 1 posture; Trace C is a system-prompt + tool-scope fact, not a feature).
- **Merge/save is always a human act.** Apply/Save/Keep are user gestures; the agent proposes - also edit screens are stateful and should presist between sessions/step aways - but final merge/update/save of a project belongs to the user. That is, we can save edits/states but to push those to the `current` or `final` game state must be a user action
- Out entirely: multi-user/web (October), the 3D shooter template (real fast-follow *after* Phases 0–2, dev-run — the wizard axes stay 3D-clean),  NPC mood state machines, camera-zone runtime, auto-restart after crash, agent-initiated unconfirmed spend.

### 1.4 The boundary, engines, and licenses

- Canon writes and builds; cradle reads, surfaces, plays. Everything cradle does is invocable headless through verbs — recon confirmed "UI-only functionality is essentially nil" and Phase 2 keeps it true: every new surface lands as verbs first.
- **Engines are per-project, selectable at create, portable by fork.** The pack registry's `engines` block (handed to Phase 0's P0 paper) carries per engine copy: id, template ref + stamp, launch contract, live-channel capability + protocol version, per-engine artifacts, `primary` flag. A full engine switch copies the project, suffixes the name, attaches the new engine copy, and rebuilds with the agent surfacing porting issues.
  **pygame is promoted from shared harness to a per-project engine copy** (stamped, `game_coder`-evolvable), gaining a camera + resizable window in the promotion; it ships dungeon crawlers. A project with two engine copies names a **primary**; the twin demotes visually, never hides.
- Licenses: pygame's LGPL rides per-project engine copies distributed as data-with-source (interchange posture; ASSUMPTION-1 legal review before wide distribution). Blender is invoked as an external GPL process on glTF files — no obligation crosses. Meshy outputs require a **paid tier for commercial use** — stated on the key screen. The `images-local` Mac default  swaps off SDXL-Turbo (Stability-licensed) before any bundled release; tqdm (MPL, unused) drops from base deps.

### 1.5 Users

v1: the author. v1.5: two more devs (can open Blender — tier 3 is real for them). The never-leaves-the-app audience is the web era's problem.

---

## 2. User experience

Interaction truth lives in the two design packages. This section fixes the model, the vocabulary, and what each surface *is*; state tables, exact copy, keyboard maps, empty/failure states are the READMEs'.

### 2.1 The two halves and the four destinations

**Editors** (character editor, Animate, PixEd, 3D tab, zones, dialogue graph, Music/SFX editors) are where content is made — generation lives on the page with the estimate on the button. **The sandbox** (character mode, sessions, dialogue live scene) is where content is tuned and played — it never generates. Generation has **many doors, four destinations** — Composer (inline on the thing), Animate (modal), Audio (dock + editors), Layout (existing modals) — with invariants: the estimate is on the door; a door never runs a paid job silently; results land where the thing lives; **the agent drives the same destination a user would**, watched there, not in chat. A chat agent can spin up a job, get permission from the user/surface up the parts of a job that need to run, and then run the job from chat. However, those animate/generate/edit jobs should be seen in the same editor spaces as what the user would do. 

### 2.2 Vocabulary (product-wide)

- **Four commit verbs**: Save (writes an edit) · Apply (writes *and* hot-swaps live sessions) · Keep/Approve (choosing among candidates). Lock prevents the agent/human from editing an asset - can be locked at any point within the pipeline but should show a locked icon so we know it's locked. Locked assets can be cloned, the clones then start unlocked
- **Three influence verbs** (references): curated (on a board, free, changes nothing) · steering (in the prompt at a weight, per kind) · source (the img2img input; costs the generation).
- **NR — needs review** (artifact affected upstream; keeps working; never auto-regenerates) vs **behind** (a session running older data/code than the pack; restart fixes free). Mapping onto canon internals (unchanged): `stale`→NR · `user_edited`→hand-edited marker · `awaiting_review`→QA-flagged (distinct from NR) · `escalated`→attention · `pending`→queued. "Behind" is session-store-only.
- **`current` is a pointer, not a position**: restore re-points without reordering; History shows both facts per row.
- **Undo, three mechanisms, no fourth**: ⌘Z (pre-save, in-surface) · Revert (discards staged) · Restore (a written version, as a new version).

### 2.3 The spatial stage

- **Character mode** (the spine): a 372px spec-driven tuning panel (372px is
  editor chrome — the level editor's existing inspector width; it says nothing
  about game resolution or fidelity, which are per-project data — see §2.10) —
  knob rows generated from the pack registry (never hardcoded key lists), four densities
  incl. **advisory** for code-owned keys (probe-flagged; inert slider, reason,
  `Open in agent →`); scope toggle (this level / whole game) with
  override-shadowing badges; **bands are pack-registry data, user-widenable**
  via a journaled sheet; derived readouts (jump velocity, apex, distance, gap
  margin) with the **arc overlay** drawn from the spawn point; the **live
  session strip** (engine chips with primary/twin, five states, measured swap
  latency as a number); the **spawn picker** (session state, never journaled;
  teleports live sessions). Dragging is pre-save; **Apply** = `canon tune
  set` → journal → hot-swap every live session on the level → free validator,
  chips cleared first. Traversal contexts are a **filter** over the flat list
  (context chips + follow-the-player), not a taxonomy.
- **Zones** (build mode): typed rects on the grid (tool `Z`, type chosen
  after), payload editor in the Dock tray (spec-driven, banded), a Zones
  palette tab, z-order conflict resolution per key (warn, never block),
  music regions migrated in as the degenerate full-height case. **The kill
  plane is one stepped per-level system** — a `level.json` field edited via
  the Bounds tool, validator re-runs on change.
- **The test-panel pattern — Add ▸ Place ▸ Adjust.** Not a separate editor:
  the shape every play/test surface's side panel uses (character mode's
  panel included). **Add** lists what the level can contain — enemy, item,
  tile, zone, dialogue, timer, kill-plane step — and picking one **arms it
  as the brush** (the level editor's armed-brush concept; nothing opens a
  form). **Place** shows the armed thing's variants (`+ new` routes to
  generation elsewhere — the sandbox never generates). **Adjust** edits
  whatever is selected — three or four knobs, the rest behind Advanced,
  with subject rows (player feel · zones · kill plane · timers) so you
  switch what you're tuning without hunting on the canvas. This is how a
  test *situation* gets composed — drop two hoppers by the gap, raise the
  kill-plane step, then feel it — while feel itself is tuned in character
  mode (above) and animations are judged on their own surfaces: the Animation tab's in-level playback + physics link (§2.4, jump animation vs real airtime), the sandbox HUD naming the live animation state and why, and S2's test-pose strip for 3D deformation.

### 2.4 The character system

- **Character editor**: on-page composer (brief, gap tickboxes from
  `GapDetector` — nothing existing pre-ticked, one estimate, live job panel),
  anchor strip (turnaround views + 3D proxy card + the lock explained),
  state cards, moods strip (route badges: 3D/2D/fallback), skins strip.
- **Anchor ladder**: (1) 2D anchor — chained img2img posing works alone;
  (2) mesh, unrigged — turntable renders; (3) mesh + rig — full posing.
  Anchor tier is selectable per generation and remembered per asset kind.
  **Locks**: approve the anchor once; every batch conditions on it;
  **re-anchor** is a named action opening a plan card. Kept candidates
  replace-with-version-history.
- **PixEd**: DetailPane tab; sprite mode (frame-aware via the Animation tab's
  own selector/filmstrip, onion skin from the real frame map, binary alpha,
  role-named palette from `style.json`, off-palette metered not blocked);
  tileset mode (slot bounds frozen, autotile variant grid, **joins** as the
  four-step diagnose→generate→hand-edit→approve panel); **region recolour**
  (`R`, PixEd-only): select by role/rect/lasso → remap with ramps preserved →
  save as **skin** or **new character** (fresh id, `parents` → base). Save =
  one version per edited frame + QA chips.
- **Clone** — the reason-free fork primitive: a Clone button on any entity
  runs `entity_clone` — full copy, fresh id, history inherited as a branch of
  the heritage tree; every edit surface then applies to the clone alone
  (name, size, stats, behavior, art — and every field the schema grows
  later). Un-disables the two existing "Duplicate" affordances.
- **Animation**: the tab gains in-level playback (loop beside a level
  fragment on the tuned arc) and the **physics link** (duration vs airtime,
  mismatch named — where the two halves of the product meet). AnimateModal:
  guidance stacks (stored spec / your frames / another asset's motion),
  frame count may exceed current, 3 candidates default, compare at real fps
  with measured facts and **no ranking**, `Keep none` first-class,
  flagged-frame handoff into PixEd. Custom per-actor states + the
  `_STATE_BRIEF` fix + swim's art leg are in v1.
- **The 3D system** (three tiers; `design_handoff_3d/` binding): J′
  (Mesh · Pose · Render; ladder chip always; proportions in sprite pixels;
  the neutral stage never themes), S1 rig intent (place/name joints,
  chain/biped/quad presets, live validation, `Send to mesh_smith`), S2
  deformation review (test-pose strip hero; describe, don't paint; fix cards
  with before/after + gate ladder), S3 op cards + the Blender round-trip
  (export → watch → re-import diff card → version), S4 PixEd-on-texture
  (atlas is a PNG; island overlay; click-to-focus both ways), S5 faces
  (mood preview + assignment; authoring is tier 2/3), S6 state clips
  (closed vocabulary rail, gaps named, CMU batches, GLB track per state).
  Gate thresholds are the README's numbers as configured constants; a failed
  gate colours, never blocks.

### 2.5 References & moodboards

One home for references: boards are named sets; a reference is on several
boards, on in one and off in another, one copy of the file. Three origins
(uploads, kept generations — rejects included, pack assets — the style
palette visible and unswitchable). **Steering is per kind with a weight**;
**audio references are curation-for-humans in v1** (not passed to any model;
audio-kind switches disabled with that reason). **Sources** fill a tray:
tags (internal `@image1…N`, displayed `@image_name`) that the user references
freely in their own brief — **no enforced prompt language, no numeric-weight
theatre**; per-source **role** (subject / object / style — the taxonomy the
provider honors; nano-banana-class models take up to 14 references, no weight
parameter). Several sources = a **splice**; lineage names the parts, and
`Send to SFX / Music` carries the parts into those briefs. Everything here is
free and project-local; the library is the only cross-project path.

### 2.6 Audio

Two clocks, dock panes never modals. **Music**: x-axis is cells; regions are
the music zones (one object, two views); lanes music/ambience/stingers;
candidates audition in place; silence is a valid region. **SFX**: three
bindings, one pane — frame (the filmstrip axis; events move with the
animation, and the panel states measurable mismatches), event (the entity's
own closed event list), place (emitters with radius/falloff). **In-place
generation is the rule**: make a sound where you hear it needed, while the
loop plays; keep one and it becomes a real asset; keep none, nothing written.
Music/SFX editors: tracks and sounds are pack assets with pages, sections
(the only music structure the engine understands, with a small closed
condition set), variants, versions, publish. Both engine copies implement the
runtime half (§6).

### 2.7 Dialogue live scene

Phase 0 owns authoring + the docked tester. Phase 2 adds `Play in game`: the
tester's simulated state injects into a running session (the channel's
`set_state`), booting to the NPC's room; the tester stays the authority
(viewport is a window; `Re-inject` after edits; the session never writes
back). **Both runtimes in v1**: the dungeon engine (pulled into canon-core,
§6) and the platformer via **capability enablement** — the proof case for
Phase 0 §5.1a. Speaker portraits with moods are v1 (closed enum, eight canon
defaults: neutral · happy · sad · angry · surprised · worried · tired ·
damaged; per-project extendable; fallback-to-neutral states its reason in
the tester, not the game).

### 2.8 Sessions & long-running work

Sessions are first-class: TopBar chip + registry popover (engine, level,
state, elapsed, spawn; restart/stop/make-live; crash keeps a log tail,
nothing auto-restarts). One live channel per engine; Apply swaps into every
live session on the affected level. Long-running generation runs as Phase 1
run cards / JobTray with honest progress; cancel keeps what landed and
re-prices the rest.

### 2.9 Canonical traces

**Trace A′ — the flagship (spatial + agent).** Playing l3, the jump feels
floaty → character mode; the level relaunches in sandbox (same geometry,
spawn set at the ledge cluster) → drags `gravity` and `coyote_s` (staged) →
`Apply 2 changes` → `canon tune set` journals; the strip shows `swap 180 ms`;
the arc overlay updates; validator re-runs clean → "add a double jump with
coyote time" in the panel → the foreman plans: `game_coder` diffs the
project's engine copies (ask chip, full diff; gate ladder incl. the tuning
smoke green), `artist` flags the missing mid-air flip and offers generation
(`$0.08–$0.30` on the chip) → three candidates compared at real fps; keeps
one, nudges two frames in PixEd (one version, QA chips pass) → replays the
same session → Save; the plan card warns the old jump animation is NR with
the blast-radius rollup → accepts. History shows every write under its
actor; each is restorable.

**Trace P9′ — mesh_smith promotes the eel (3D).** Auto-rig failed on
`blind_eel` → S1: chain preset, six joints placed and named, validation
green except one unnamed joint (warn) → `Send to mesh_smith` (free) → the
agent runs rig-from-intent + auto-weights + smoothing headless; gate ladder:
coverage 99.7% (warn, named), bones-in-limbs pass → S2 before/after strips
play the same bend → Accept · new mesh version → poses from the library
retarget (chain-mapped subset) → renders → the swim states regenerate
conditioned on the posed renders (estimate chip) → the eel is tier 3.

**Trace B′ — the live scene (dialogue).** In the dungeon pack, the tester
has `has rusted key` on and the branch selected → `Play in game` attaches a
session, injects state, boots to Whisper-Tam's room → the line reads wrong
in place → edited in the graph (Phase 0 surface) → `Re-inject`, replays →
Save. The platformer version of the same trace runs once dialogue capability
is enabled on a platformer pack.

**Trace R — a boss from parts (references).** Five photos + a tone note onto
a new board → two enemies generated steered by the board, one from prompt
alone → two enemies + a photo picked as **sources** (roles: subject,
subject, style) → splice (`$0.08–$0.30`), candidate kept, anchor locked →
`Send to SFX`: three attack sounds generated in place on the frames where
each attack lands → `Send to Music`: the area theme written against the boss
and its parts. Every generation's inputs manifest names the board, the tags,
and the lineage.

**Trace Z — zones, live.** Build mode: a rect drawn over the pool, type
`physics`, `gravity ×0.45, speed ×0.55` → Apply hot-swaps the live session →
the player floats through it immediately → the kill plane's east step
raised; validator warns two placements became unreachable (named) → the step
adjusted → clean.

### 2.10 Resolution & fidelity — data, not platform constants

Editor chrome numbers (372px panel, 412px agent column, 1440 boards) are
ergonomics of the cradle shell and cap nothing about the games. Game
resolution and fidelity are **per-project data** and this PRD sets no limits
on them: `GraphicsSpec` already carries `tile_px`, `gen_px`, `sprite_px`,
`render_filter`, and lanes up to `modern_hd`; the engine copy owns its window
and render settings as ordinary project data (Godot's project file — 720p
default today, 4K-capable by editing data); pixel lanes deliberately render
at small native resolution and integer-scale crisply to any display. Higher
fidelity is a lane/template choice, not a Phase 2 capability change — and
Phase 0 W2.4's rule stands: nothing bakes a resolution, dimension, or engine
assumption into the registry. Distribution targets remain computer / web /
mobile via Godot's first-party exports; **consoles (PS5-class) are a
partner-gated port** (console SDKs are NDA'd; Godot ships there via porting
partners), a roadmap line for after the 3D era, never a resolution setting
in this document.

---

## 3. Agent design

**Inherited wholesale from Phase 1** (§3–§5 of that PRD binding): the loop,
foreman + specialists-as-tools, permissions/chips/grants, plan cards, cost
lanes, skills, cancel, context management, injection posture. Phase 2 adds
configuration and tools, not machinery:

- **`mesh_smith` joins the roster** (config, not code): role prompt +
  bpy-recipe skills + tool subset (§4.E) + the **mesh gate ladder** as its
  verification harness — the `game_coder` §7.1 pattern with Blender for an
  engine. Ask-tier results as before/after op cards (wrapping Phase 1 run
  cards). Refusal behavior is copy: non-operations ("make the face kinder")
  are declined toward tier 3.
- **`game_coder` gains the tuning smoke** in its gate ladder: a verb-changed
  constant must change the trajectory; keys the code legitimately takes over
  are probe-marked `code-owned`, demoting their sliders to advisory.
- Existing specialists gain Phase 2 tool packs: `level_designer` (+ zones,
  kill plane, tune), `artist` (+ PixEd-adjacent verbs, splice, joins, moods,
  anchor ops), `writer` (dialogue surfaces per Phase 0), `playtester`
  (+ session channel reads, capture on the promoted engine copies).
- **Routing additions**: the four destinations are the agent's destinations;
  a repetition diagnostic or cascade may be *raised* as a paid suggestion
  card — accepting is always the user's act.
- Scope enforcement stays structural: the verb registry is the law; no
  filesystem tools; reads path-guarded; the DAG/regen hazard path is not a
  tool. Provenance lane: agent-proposed, human-accepted tunings journal with
  their own detail kind for training extraction.

---

## 4. Tool inventory

Tier legend (Phase 1's): **auto** (fires without asking — reads, display) ·
**ask** (permission chip; project-grantable) · **$ confirm** (paid; always
confirms with estimate + backend; never grantable). Every write threads
`--actor agent:<conv>/<specialist> --session <id>`. All-fake backends = ask
with "$0". Existing Phase 1 tools are not repeated.

### 4.A Feel & spatial data

| Tool | Wraps | Touches | Tier | Status |
|---|---|---|---|---|
| `tune_set(level\|null, keys)` | `canon tune set` | manifest movement/combat/rules (pack scope via `world update` discipline) or level overrides | ask | new |
| `tune_clear(level, key)` | `canon tune clear` | level override | ask | new |
| `band_widen(key, min, max)` | `canon registry set` | pack registry bands; own History row | ask | new |
| `zone_write(level, zones)` | `canon zone set` | level zones (incl. migrated music regions) | ask | new |
| `killplane_set(level, steps)` | `canon zone set` (kill-plane field) | level.json; validator re-runs | ask | new (ASSUMPTION-2: rides the zone verb) |
| `describe_tuning(level)` | registry + manifest reads | — | auto | new |

### 4.B Sessions & the live channel

Runtime layer (cradle Rust + the engine copies), not canon verbs; journaled
where they touch pack state (nothing here does — spawn is session state).

| Tool | Mechanism | Tier | Status |
|---|---|---|---|
| `session_launch(engine, level, spawn?)` | spawn with channel armed; returns id | ask | new |
| `session_control(id, restart\|stop\|attach)` | registry + kill (Child retained) | ask | new |
| `session_swap(id, keys)` | `.canon/control.jsonl` append; engine hot-reads; **returns measured ms** | auto (rides an Apply) | new |
| `session_set_state(id, state)` | channel `set_state` (dialogue spoofing, teleport) | ask | new |
| `capture_frames / run_trajectory` | promoted engine copies, headless | auto | inherited, re-pointed |

### 4.C Art, animation, characters (2D)

| Tool | Wraps | Tier | Status |
|---|---|---|---|
| `pixel_save(entity, frames)` | `canon art save` — one version per frame + QA | ask | new |
| `recolour(entity, region, map, as: skin\|entity)` | `canon art skin` — pure code | ask | new |
| `joins_generate(tileset, slot, kinds)` | `canon art joins` | $ confirm | new |
| `expression_sheet(entity, moods)` | `canon art moods` — on demand, cancellable | $ confirm | new |
| `splice_entity(sources[@tags+roles], brief)` | `canon art splice` — multi-image edit; lineage names parts | $ confirm | new |
| `anchor_generate(entity, tier)` | composer path — base / turnaround / one-shot | $ confirm | new |
| `anchor_lock / re_anchor(entity)` | lock = gate; re-anchor opens a plan card | ask / plan | new |
| `animate_asset(…, guidance[@tags], candidates)` | `asset animate` extended | $ confirm | extended |
| `refs_write(board, refs, steering)` | `canon refs set` — free, journaled | ask | new |

### 4.D Audio

| Tool | Wraps | Tier | Status |
|---|---|---|---|
| `music_generate(track, brief, refs)` | `canon audio track` — candidates audition locally | $ confirm | new |
| `sfx_generate(binding, brief)` | `canon audio sfx` — frame/event/place; in-place candidates | $ confirm | new |
| `audio_bind(target, sfx\|track, opts)` | `canon audio bind` — one verb, three bindings + regions/sections | ask | new |
| `audio_record / audio_import` | mic / file → asset | ask | new, free |

### 4.E Mesh (the three tiers)

| Tool | Wraps | Tier | Status |
|---|---|---|---|
| `mesh_generate(entity, views)` | `MeshBackend` (Meshy image/multi-image-to-3d) | $ confirm | new |
| `mesh_rig_auto(entity)` | Meshy rigging | $ confirm | new |
| `rig_intent_save(entity, spec)` | intent artifact (joints, names, roles, preset) | ask | new |
| `mesh_op(entity, op, params\|description)` | headless Blender run — bpy script journaled with hashes; gate ladder on return | ask (free) | new |
| `mesh_roundtrip_export / import` | exchange path + watch → re-import diff → version | ask (free) | new (Replace-shaped) |
| `pose_save / clip_save(entity, state, data)` | pose/clip artifacts (vendored pose math) | ask | new |
| `clip_export_glb(entity, states)` | GLB tracks, names = state ids | auto (free) | new |
| `generate_from_pose(entity, render, prompt)` | image edit conditioned on the posed render | $ confirm | new |

### 4.F Staleness, provenance, structure

| Tool | Wraps | Tier | Status |
|---|---|---|---|
| `bible_synthesize(pack)` | one-time adopt-on-write stamp from the template graph | ask | new |
| `stale_graph(artifact)` | provenance walk — feeds blast-radius headers | auto | new |
| `cascade_plan / resume` | Phase 1 plan card over S5 edges; per-step confirm; pausable | plan | new (UI = existing cards) |
| `nr_dismiss(artifact)` | journaled judgement ("looked, it's fine") | ask | new |
| `engine_attach(pack, engine)` | port an engine copy in; write per-engine artifacts; stamp; register | ask | new |
| `project_fork_engine(pack, engine)` | the R9 switch flow: copy, suffix, attach, rebuild w/ agent triage | ask | new |
| `entity_clone(type, id)` | **the reason-free fork primitive** — full copy under a fresh id; inherits history (lineage links the clone as a branch of the source's heritage tree); every edit surface then applies to the clone alone | ask | new |

Dialogue tools are Phase 0's six verbs, driven unchanged; the level/db/asset
tier-(a) verbs are Phase 1 §4.B/§4.C, unchanged.

---

## 5. Orchestration

- **Decomposition and approval** are Phase 1 §5.5 verbatim (Ask stepwise or
  Plan cards; chips bubble; paid never self-approves).
- **Cascades**: an edit's downstream set comes from `stale_graph`; the plan
  card carries the blast-radius header (artifact count, steps, cost rollup),
  per-step confirms, pause/resume pinned in History with spend-to-date.
  Abandoned = downstream stays NR; nothing is half-written.
- **NR propagation** follows the design's P12 matrix (weights→clips;
  geometry→renders→sprites→clips; decimation invalidates the atlas — stated
  on the button; re-import stales broadly; **the 2D anchor and the mesh never
  stale each other**). Clearing is free everywhere except sprite regeneration;
  `Dismiss` is journaled.
- **Apply ordering**: write → journal → hot-swap every live session on the
  affected level → clear + re-run free validation. The per-pack write gate
  (Phase 1) serializes agent writes per target.
- **Errors**: verb failures are structured; one corrected retry; paid stops.
  Headless Blender crash = script + exit code + stderr tail, mesh untouched.
  A generation leg dying mid-batch keeps what landed and re-prices the rest.

---

## 6. Capability gaps

### Canon

| Gap | What it is | Wave |
|---|---|---|
| **Dungeon engine pull-in** | the external MazeWorld pygame engine into canon-core as the dungeon engine template (+ rename pass); at pull-in, inventory its shop/stock data (deferred Game-UI seed) and its tunable constants | W2.0 (leads — "so much depends on it after") |
| **pygame engine-copy promotion** | per-project copies, stamped, `.engine.json`'d; **camera + resizable window** land in the promotion | W2.0 |
| **The live channel** | `.canon/control.jsonl` command file + telemetry sibling per session; engine copies poll (the `relay_step_log` pattern inverted); commands: reload keys/level/manifest, `set_state`, teleport/spawn, quit; `sessionSwap` reports measured ms | W2.0 |
| **Session runtime** | Child retention, kill, registry, `PLAT_SPAWN`-style spawn hook in both engine copies | W2.0 |
| **Staleness floor** | `--orchestrate` default on `world new`; `bible synthesize`; animation sheets + atlases into `_iter_hashed_files` | W2.0 |
| **Tuning surface** | `tune set/clear` (pack scope on `world update` discipline; level on overrides); full 12-key vocabulary; bands as registry data; per-tile friction read side in both engine copies | W2.1 |
| **Zones + kill plane** | typed-rect resolver in both engine copies; kill-plane field + stepped reads; validator updates | W2.1 |
| **Gen-inputs manifest** | journal `gen` block gains the full input set (refs @tags + roles + hashes, context, prompt, params) on every generation door — v0, before the first generation surface | W2.1 |
| **References** | `refs set`; project-local store; kept-generation promotion into the CAS; @tag composition per provider (role taxonomy) | W2.1 |
| **Multi-source edit** | `ImageEditBackend.edit` widens to an image list; `multi_source` capability flag; splice = capability-gated | W2.1 |
| **Art verbs** | `art save / skin / joins / moods / splice`; `_STATE_BRIEF` fix; swim unlock; emotion enum in core, per-project extendable | W2.1 |
| **Audio system** | `audio track / sfx / bind`; sections + small closed condition set; frame-fire, event hooks, emitters w/ falloff, ambience, stingers — **in both engine copies** (the largest engine build; spread across the wave) | W2.1→W2.2 |
| **Dialogue enablement** | platformer `dialogue` capability (Phase 0 §5.1a machinery) + engine-side scene rendering + `set_state` injection in both runtimes | W2.2 (after Phase 0 P5) |
| **MeshBackend + Blender** | Meshy backend (mesh/rig, task-resume); Blender detection (`$BLENDER_BIN`→PATH→/Applications), 4.x LTS pin + version gate; the bpy recipe library; mesh gates as configured constants; vendored poseforge modules (copy-only) | W2.2 |
| **engines block + attach/fork** | registry field (co-owned with Phase 0 P0 — the one-liner is already handed over); `engine attach`; the fork-per-switch flow | W2.0 seam, W2.2 flow |
| **Estimator rows** | every new paid action into `cost_model.json`; the constants module is the single price source; measured-vs-estimated flags per Phase 1 §7.3 | W2.1 |

### Cradle

The two design PLANs are the component-level truth (26 + 12 rows, component
maps, store slices — `sessions/tune/zones/pixels/references/audio/cascade` +
`mesh/rig/ops/blender/texture/moods/clips`). Cradle-side gaps beyond them:
the session chip replaces the floating play note; the Dock gains the Zones
tab and audio panes; DetailPane gains PixEd and 3D tabs; EngineChip's
vocabulary extends to per-engine-copy primary/twin; devMock parity + I1–I8
invariants hold for every new command.

---

## 7. Safety & sandboxing

- **Enforcement is structural** (inherited): the tool registry is the law;
  no shell, no arbitrary writes, path-guarded reads, hazard paths absent.
- **Executing gameplay code**: sessions run the pack's own engine copies —
  the same trust as ▶ Play, with the probe disclosing modified/unstamped
  engine files before agent-triggered runs (Phase 1 §7.1). The code path
  (`game_coder`) is unchanged; its gate ladder gains the tuning smoke.
- **Headless Blender**: an external process on pack files, agent-scripted;
  every run journals the script + before/after hashes; crashes write
  nothing. Recipes are canon-authored templates the agent parameterizes
  (ASSUMPTION-4: scripts are recipe-bounded, not free-form bpy — the same
  skeleton-bounds-the-LLM doctrine applied to mesh ops).
- **Spend**: paid is $-tier with the range on the button from the single
  constants source; tier 2/3 and the whole judgment layer are free and say
  so; the agent may raise paid suggestion cards, never accept them.
  Meshy = paid tier for commercial use, stated at the key screen.
- **Provenance**: 100% write coverage by construction; the gen-inputs
  manifest closes the inputs gap; accepted agent tunings carry their own
  detail kind so the correction-pair training signal stays clean.

---

## 8. Phasing

Sequenced **after Phases 0 and 1 ship** (L1). Waves; each row leaves the app
usable; the two design PLANs' internal orders are binding within their waves.

| Wave | Contents | Gate |
|---|---|---|
| **W2.0 — foundations** | dungeon engine pull-in · pygame promotion + camera/window · the channel + session runtime (kill, registry, spawn) · staleness floor (orchestrate default, synthesize, sheet hashing) · engines-block seam | a platformer *and* a dungeon pack each launch a tracked, killable session; a constant applied from the CLI hot-swaps both; `bible synthesize` gives an old pack a working `canon status` |
| **W2.1 — the sandbox package** | parent PLAN rows 1–26 in its order (rows 1–5 = Screen A end to end first; **row 22 moodboards pulled forward** per its HANDOFF); tuning verbs, zones/kill plane, gen-inputs manifest, references/@tags, art verbs, audio system engine halves landing throughout | Trace A′ (minus the code leg if Phase 1 ships later than planned — ASSUMPTION-5 it doesn't); Trace Z; Trace R; the parent package's snapshot/property tests green (spec-driven rendering, save boundary, latency assertions) |
| **W2.2 — the 3D package + dialogue live** | 3D PLAN rows 1–12 in its order (1–3 infra, 5 = promotion flow, 8 = round-trip safety net); dialogue capability enablement + live scene both runtimes; expression sheets | Trace P9′; Trace B′ on the dungeon pack; GLB tracks export and reimport clean in Blender |
| **W2.3 — polish + the parked** | four-segmented-modes inconsistency revisit if it itches; library build **only after** the reuse conversation; Game-UI system concept work (shops inventory from the pull-in inventory) | user sign-off against §8.1 |

### 8.1 Success criteria

1. Trace A′ end to end, every write journaled/restorable, slider→feel ≤ 3 s
   (v0 relaunch) and ≤ 250 ms (v1 swap, measured and displayed).
2. The same feel-tuning session shape works on a dungeon-crawler pack.
3. A zone painted in build mode changes physics in a live session; the
   stepped kill plane ships and validates.
4. Trace P9′: a creature reaches tier 3 without the user opening Blender —
   and the manual round-trip imports a Blender edit as a version with a
   correct diff card.
5. Trace R: a spliced boss's inputs manifest names every part, board, and
   tag; History answers "what went into this generation" for any output.
6. Trace B′ in both runtimes; a mood-portrait line renders in-engine.
7. An SFX generated in place lands on its frame and survives a `run_speed`
   change with the mismatch named.
8. No unconfirmed spend anywhere; every paid button carries its range from
   the constants source; the cost dashboard reconciles.
9. NR: decimation invalidates the atlas with the warning on the button;
   the 2D anchor and mesh never stale each other; Dismiss is journaled.
10. The full panel suite runs in the browser mock; both design packages'
    test hooks green.

---

## 9. Open questions, assumptions, risks

**Open (one):** the **D4 spike** — raw upscaled pixel art vs a
restyle-before-Meshy step (`scripts/spike_meshy_anchor.py`, user-run).
Nothing in the twelve 3D rows depends on it; the verdict adjusts the mesh
input step and two price rows. The "Restyle for meshing" slot ships disabled
with its reason until then.

**Assumptions:**
- ASSUMPTION-1 — ✅ **ratified 2026-08-31**: the pygame-LGPL posture —
  pygame ships unmodified as a separable wheel with generated notices, our
  engine-copy code stays Apache-2.0, and the user-export path auto-includes
  the notices — with a legal-review checklist item before wide distribution
  (wheel replaceability, complete transitive notices, the AppImage/WebKitGTK
  case).
- ASSUMPTION-4 — ✅ **ratified 2026-08-31**: `mesh_smith`'s bpy scripts are
  **recipe-bounded** — canon-authored, version-pinned templates with
  fail-closed parameter bounds and per-recipe gate configs; the agent picks
  and fills, never authors bpy; free text routes to the nearest recipe or
  refuses toward tier 3. Recipes are mesh_smith's skills.
- ASSUMPTION-2 — ✅ **ratified 2026-08-31** (user, in-file): the kill plane
  rides the zone verb rather than its own.
- ASSUMPTION-3 — ✅ **resolved by revision 2026-08-31**: clone is a
  **reason-free primitive** — "we clone to clone." A Clone button on any
  entity runs `entity_clone`; the clone is a full copy under a fresh id that
  **inherits history** as a named branch of the source's heritage tree; from
  then on every ordinary edit surface applies to the clone alone — name,
  size, stats, behavior, art, and every field the schema grows later. The
  verb constrains nothing post-clone and couples to no workflow;
  recolour-save-as-new-character and splice remain their own minting paths,
  not the fork story. Expandable by construction.
- ASSUMPTION-5 — **noted as the scheduling bet** (Phase 1 ships before W2.1
  completes; W2.1's gate degrades to Trace A′-minus-the-code-leg if it
  slips). Ratification lands with the user's full read of this PRD, ahead of
  the master-PRD merge session.

**Risks:**
- **Meshy variance on stylized/pixel inputs** — bounded by the D4 spike and
  the ladder's tiers 1–2 (every character is shippable at every tier).
- **bpy API drift** — LTS pin + version gate + recipe tests on golden meshes.
- **The audio engine build is the largest engine-copy work in the phase** —
  spread across W2.1→W2.2 by design; frame-SFX and music regions land first.
- **Channel vs parity** — hot-swap must not perturb tick order; the parity
  bar (traj byte-diff) becomes a CI-able gate on the promoted engine copies
  rather than a human-run ritual.
- **Scope mass** — Phase 2 is deliberately large ("break the build across
  the PRD"); the wave gates exist so it ships in usable slices, and nothing
  outside a wave blocks the wave before it.
- **Design/build drift** — gate thresholds, prices, and copy live in
  configured constants and the design READMEs; components never hardcode
  them (both PLANs carry snapshot tests for exactly this).
