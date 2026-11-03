# Design prompt — Phase 2 addendum v2: the three-tier 3D system (for Claude Design)

**What this is:** an addendum to the approved `cradle/design_handoff_sandbox/`
package, superseding the v1 addendum draft (which designed six in-app DCC
surfaces). The architecture changed on 2026-08-31: cradle does not hand-build
mesh surgery tools. The honest comparison ended it — the ceiling of a
homegrown weight painter is "a bad Blender," while the things Blender cannot
do are exactly the product's differentiators: the anchor→pose→render→generate
loop, clips keyed to the state vocabulary, PixEd's palette/QA discipline on
textures, and the ladder/NR/provenance integration around all of it.

**The three-tier model every screen in this addendum serves:**

| Tier | Who | Does |
|---|---|---|
| **1 — Cradle establishes & judges** | in-app surfaces | mesh generation, the anchor ladder, pose editor + library + sketch, state-clip timeline, PixEd-on-texture, and the judgment surfaces: test-pose deformation strip, proportion checks, silhouette-drift vs the 2D anchor |
| **2 — `mesh_smith` executes** | a Phase 1 roster specialist driving **headless Blender** (`blender --background --python`, agent-written bpy script per task) | the surgery cradle never hand-builds: rig generation from an intent spec, automatic weights + smoothing/transfer, decimation/cleanup, smart UV unwrap, mirroring, proportional adjustments, shape-key scaffolding, GLB export — each run returning through a **mesh gate ladder** as a before/after card |
| **3 — Manual Blender round-trip** | the user, in real Blender | actual artistry (a better nose is not an operation): export → edit → save → re-import as a journaled version |

**The honesty line, stated in copy wherever tier 2 is offered: the agent does
operations, not art.** Deterministic mesh math is "code computes"; aesthetic
geometry is tier 3 or generation territory.

Deliverable format: same trio as the parent package — README (interaction
spec, state tables, exact copy), PLAN (component map, build order), artboards
both themes + flow boards. Same tokens, shell, agent-panel coexistence,
doctrine (disabled-with-a-reason, paid-on-the-button, free-never-confirms,
three commit verbs, NR language). Interaction reference for posing remains
poseforge's proven patterns (gizmo, 15° snap, mirror, bone sliders,
sketch-to-pose, start-anywhere backfill), restated in cradle's idiom.

---

## The anchor ladder (unchanged, every screen respects it)

| Tier | Character has | Posing | Who |
|---|---|---|---|
| 1 | 2D anchor only | chained img2img "apply pose (2D)" | any creature |
| 2 | mesh, unrigged | turntable renders only | meshes Meshy can't rig |
| 3 | mesh + rig (auto, agent, or imported) | full pose editor | humanoids; creatures after `mesh_smith` rigs them |

Promotion up this ladder is now chiefly **mesh_smith's job** — "promote this
eel" is a product flow, not a DCC feature.

## Screen J′ — the 3D tab (Mesh · Pose · Render)

- Modes: **Mesh · Pose · Render**. Mesh mode hosts *review and agent-op entry
  points*, never paint tools. Pose and Render carry over from the parent
  Screen J intent: direct-manipulation posing, named per-state poses, free
  flat renders at sprite resolution on the neutral-gray stage (renders double
  as conditioning images — the stage never themes).
- Always visible: the ladder-tier chip + what would promote it; dimensions in
  sprite pixels; live proportion checks (height, head:body, fits-the-sheet,
  silhouette drift vs the locked 2D anchor).
- Start-anywhere: generate mesh (paid), upload GLB, upload rigged — skipped
  steps gray as "optional backfill."
- Paid here: mesh generation, rigging (auto), generate-from-pose. Everything
  else in this addendum is **free** — tier 2 costs conversation tokens only,
  and the copy should quietly celebrate that.

## Screen S1 — Rig intent (replaces the rig editor)

The user marks **intent, not armature mechanics**: click-place and name
joints on the mesh ("spine here, these are legs"), chain / biped / quadruped
presets as starting skeletons, mirror across the spine, reorder/reparent by
drag. The intent spec — not a rig — is what `mesh_smith` turns into a real
armature with automatic weights.

- Live validation preview: joints-inside-mesh, "pose library compatibility:
  12 of 15 poses map to this skeleton," unnamed-joint count.
- The handoff is the hero: `Send to mesh_smith` → the agent card names the
  ops it will run (rig from intent → auto-weights → smooth) — free, no
  confirm-fatigue, one Accept when results return.
- Convention help: Mixamo-style names offered, never forced (an eel is a
  chain, not a biped); the retarget math maps only what exists.

## Screen S2 — Deformation review + fix loop (replaces weight painting)

**Nobody paints weights.** The screen is the judge:

- The **test-pose strip** is the hero: bend / crouch / stretch / a pose from
  the library, live-deforming side by side. Problems are *described*, not
  painted: `the elbow collapses` typed or picked from common-failure chips.
- That description becomes a `mesh_smith` fix card (the bpy op it chose —
  weight smoothing, transfer, falloff adjustment) → **before/after strips
  playing the same test pose** → Accept / Reject / `Tell it more`.
- Gate results ride the card: weight coverage %, unweighted vertices,
  bones-in-limbs, silhouette render-diff.
- Escape hatch on the surface: `Open in Blender` (tier 3) for cases the loop
  can't fix.

## Screen S3 — Mesh ops + the Blender round-trip (replaces sculpt + UV screens)

Two halves, one screen:

1. **Agent-op cards** for deterministic operations: decimate/cleanup, mirror,
   proportional adjust ("shorten the snout 20%"), smart UV unwrap,
   shape-key scaffolding. Each card: the op + params → before/after (viewport
   + numbers: vertex delta, island count) → gate results → Accept. Free.
2. **The manual round-trip** (`Open in Blender`): export the GLB to a known
   path → a *watching* state (file watched, elapsed, `Cancel watch`) →
   on save, a **re-import diff card**: vertex-count delta, bone-name
   compatibility, weight sanity, silhouette render-diff, texture-map changes
   → `Import as new version` (journaled import op; NR flows downstream) /
   `Discard`. Structurally the asset-Replace flow, designed for meshes.
- Blender presence: a detected external install exactly like Godot
  (`$BLENDER_BIN` → PATH → /Applications). Not-installed state: every tier
  2/3 affordance disabled with the reason and an install pointer; pinned-LTS
  note (Blender 4.x LTS; recipes are version-gated).

## Screen S5 — Faces (reshaped)

The in-app half is **preview and assignment**: morph targets keyed to the
mood enum (8 canon defaults + project additions), per-mood preview strip,
fallback-to-neutral with the reason shown. Authoring the shapes is tier 2
(shape-key scaffolding + proportional ops) or tier 3 (sculpted in Blender,
returning through the round-trip). The 2D expression sheets and 3D shapes
remain two routes to one enum; the character editor's Moods strip shows
which route filled each mood.

## Screen S6 — 3D animation timeline (unchanged from v1 addendum)

Pose keyframes, client-side slerp scrub, CMU clips as keyframe batches,
non-destructive trim, **clips authored per ladder state** (the same closed
states the 2D sheets fill; a state with a sheet but no clip shows as a gap),
**GLB animation-track export** per state. No video generation — Veo does not
exist in cradle. Cross-link to the 2D Animation tab (same states, two sheet
halves), never a merged screen.

## PixEd-on-texture (unchanged, one clarification)

Painting a texture is a PNG job — PixEd does not need to own unwrapping.
Unwrap is a tier-2 op; the texture PNG then opens in PixEd with palette
roles, QA chips, and a live on-mesh preview beside it. Design the
click-mesh-to-focus-texture-region interaction.

## Flow boards

- **P9′ — mesh_smith promotes the eel** (the flagship): auto-rig failed →
  rig intent marked (chain preset, 6 joints named) → agent runs rig +
  auto-weights headless → gate ladder green, one warning → test-pose strip
  before/after → accept → pose from the library → renders → sprite states
  regenerate conditioned on the posed renders. Lanes: user / cradle+agent /
  spend & QA (spend lane reads: conversation tokens only).
- **P10 — texture round-trip**: unwrap op → PixEd paints with palette roles
  → QA chips → live on the mesh → renders pick it up.
- **P12 — mesh-edit consequences**: any accepted op or re-import → which
  renders/generations flag NR (conditioned on the old geometry) — and what
  never stales (the 2D anchor from mesh work; the mesh from 2D re-anchoring).
- **P13 — the manual round-trip**: export → Blender edit → save → watch
  catches it → diff card → import as version → History shows the import op
  with before/after hashes.

## Constraints & notes

- `mesh_smith` is one more Phase 1 roster entry — its cards speak the agent
  panel's existing plan/run-card language; nothing new is invented for
  progress or approval. Ops are journaled with the generating script +
  before/after hashes, restorable like every write.
- Licensing is clean and stays clean: cradle invokes Blender as an external
  process on interchange files (glTF); GPL covers Blender's code, not files
  it edits. If a tiny "send back to cradle" addon ever ships, it is its own
  GPL script, separate and harmless.
- The D4 spike (raw pixel art vs restyle-before-Meshy) remains the one open
  empirical; a disabled-with-reason slot for a "Restyle for meshing" step is
  enough.
- Undo: ⌘Z pre-save in the pose/timeline surfaces; agent ops and re-imports
  are versions (Restore); the parent package's three-verb rule holds.
- Keyboard map, empty states, failure states (Blender missing, gate failed,
  watch abandoned, headless run crashed — show the script + stderr tail),
  both themes, "Meshy paid tier for commercial use" on the key-gated
  actions — same bar as the parent README.
