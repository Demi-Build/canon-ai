# Handoff — cradle design port (Track A), world-map fidelity pass

Paste this whole file as the opening prompt of a fresh Claude Code session.

---

## Start here

Read, in this order:

1. Persistent memory `project_cradle_platformer_editor.md` — the last ~6 entries
   cover this cycle. It is long; the entries you need are the ones titled
   **DESIGN PORT (TRACK A)**, **PHASE 3 …**, **PHASE 4 — WORLD MAP**, and
   **WORLD-MAP STUBS CLOSED**.
2. `~/.claude/plans/fuzzy-moseying-candy.md` — the approved plan for this cycle
   (phases 0–4). Phases 0, 1, 3, 4 are done; **phase 2 (start page) is not**.
3. `cradle/design_handoff_editor_worldmap_start/` — the design bundle.
   **`README.md` is the spec**, `PLAN.md` is the decision log, and the three
   HTML files are interactive prototypes. **Read `05 World map.html` directly**
   — its CSS and markup are the contract, and the README summary is not enough
   detail for the fidelity work below.

## Repos

| repo | branch | role |
|---|---|---|
| `/Users/wolfgangblack/Documents/projects/canon-ai` | `cradle_editor_surface` | generator (Python + Godot template) |
| `/Users/wolfgangblack/Documents/projects/cradle` | `feat/platformeditor` | editor (Tauri 2 / React 19 / Rust) |

Use `.venv/bin/python` and `.venv/bin/canon` in canon-ai.
**The user handles all git.** Stop at the staging boundary — never commit or push.

### Uncommitted right now
- **canon:** `compose.py`, `adapters/platformer_write.py`, `bible/platformer.py`,
  `cli/main.py`, new `tests/test_world_map.py`
- **cradle:** `App.css`, `App.tsx`, `DetailPane.tsx`, `LeftNav.tsx`, `store.ts`,
  `lib/invoke.ts`, `lib/devMock.ts`, `src-tauri/src/lib.rs`,
  `level/{LevelCanvas,LevelDetail,ToolRail,drawLevel}`, new
  `level/{AudioLane,Dock,Minimap}.tsx`, new `src/components/world/`

## Verification (run before claiming anything works)

```bash
# canon (from canon-ai)
.venv/bin/python -m pytest tests/test_world_map.py tests/test_platformer_ops.py -q   # 91 passed
.venv/bin/python -m pytest tests/ -q    # ~2204 pass; test_backend_lyria fails without GOOGLE_API_KEY (known, pre-existing)

# cradle (from cradle)
npx tsc --noEmit && npx vitest run && (cd src-tauri && cargo check)   # 168 tests
```

Browser mock: `preview_start {name: "cradle-mock"}` → port 5199, real pack data,
no Tauri. **Rust changes need a full `tauri dev` restart — HMR is frontend only.**

---

## THE WORK

### 1. World-map fidelity — the main ask

The user's words: *"we're really still off from how the design looks/operates,
especially in the world map with its tools."* They have a reference image of the
intended world map; the authoritative source is **`05 World map.html`**.

Known concrete gaps between what was built and the spec — verify each against the
HTML rather than trusting this list:

- **Tools are in the wrong place and incomplete.** The design has a *floating
  tool rail, top-right of the stage*: `Select V · Place level L · Draw path P ·
  Area A` — divider — `Path stops S · Player start`, each with a hover tooltip.
  What exists is a segmented control in the header with only Select/Place/Connect.
- **No world-map sidebar.** The design's 208px sidebar for this screen is
  area-centric: project name, `Areas · 3`, then per area a row (20px colour
  swatch, name, mono `N levels · <music>`) followed by its level rows, room
  children as `↳ l3r1` at 30px indent, an **Unassigned** group when non-empty,
  **Unplaced levels**, and a footer with **New area** + a Library count. Cradle
  currently shows its normal type-list nav.
- **Layout provenance is in the header, not a floating panel.** Design: a
  top-left float reading `Layout: agent · 3 human edits` with **Re-run** and a
  **Locked/Unlocked** toggle.
- **Missing chrome:** view chips (player / stops / world art), the bottom-left
  **Legend** documenting the five path and node states, and the zoom pill's
  full control set (`− / readout / + / fit / 1:1`).
- **Inspector is thinner than spec.** Design wants: 96px thumbnail overlaid with
  `<size> · <n> entities`; an **Inherited from area** block (Theme, Blocks,
  Music, Enemy pool) where each row carries an `area` or `override` badge plus an
  inline `override`/`reset` link; a **Connections** list with direction glyph +
  path-kind pill + "Add connection"; actions *Open in level editor* /
  *Duplicate as branch*.
- **Camera:** design specifies hold-**Space** to pan from anywhere, middle-drag
  to pan, wheel zoom toward the cursor (0.4–2×, 8%/notch), Shift/Alt-wheel to pan
  horizontally, and **a drag under 3px counts as a click** so selection still
  works. Only basic drag-pan and button zoom exist.

Node/area/edge *rendering* is close to spec already (`src/components/world/drawWorld.ts`)
— both canvas treatments, auto-hulled areas, typed edges, status dots,
thumbnails, planned nodes.

### 2. Collapsible side panels — new ask

Both the **left nav** and the **right inspector** must collapse, **by button and
by keyboard**. Persist alongside the existing prefs (see below). The level
editor's right-hand surface is the dock's tray pane; the world map's is
`.wm-inspector`.

### 3. Movable minimap — new ask

The tool rail is already drag-movable via a grip (`ToolRail.tsx`, `.tool-grip`) with
its position persisted and double-click to reset. **Do the same for the minimap**
(`level/Minimap.tsx`) — reuse the pattern rather than writing a second one; it is
worth extracting a shared `useDraggablePanel` hook.

### 4. Not yet done from the approved plan

**Phase 2 — start page (screen 06):** the card `⋯` menu with
**remove-from-recents ≠ delete** (the README calls this the point of the menu),
the tray slide-over (What's new / Updates / Links), the hero "World at a glance"
panel, and splitting the new-project modal into template → form.

---

## Conventions that already exist — extend, don't re-invent

- **Design tokens** are complete in `src/styles/tokens.css` (both themes). Never
  introduce a new colour; there are no legacy `--surface-*`/`--text-*` tokens left.
- **Chrome primitives** in `App.css`: `.btn` (+`.pri`/`.dang`), `.tool`, `.kbd`,
  `.tip`, `.chip`, `.segmented`/`.seg-btn`. Use them.
- **Tooltips**: `components/Tooltip.tsx` — 260ms delay, portaled, flips on
  overflow. Every tool-rail button gets one.
- **Keyboard**: `src/lib/keys.ts` — `isShortcut(e, "k")`, `kbd("K")`.
  **Cross-platform is mandatory** (a Windows contributor uses this): ⌘ on macOS,
  Ctrl elsewhere, and hints must render the key the reader presses.
- **Command palette**: ⌘K/Ctrl+K. Surfaces register commands into a store
  registry and withdraw on unmount (`registerCommands`/`unregisterCommands`).
  **Any new action should be registered there as well as having its button.**
- **Layout prefs**: `store.ts` `LayoutPrefs` in `cradle.layout.v1`
  (`focusMode`, `minimapCollapsed`, `toolRailPos`). `initialLayout()` merges over
  defaults so a newly-added pref is never `undefined`. Add panel-collapse and
  minimap position here.
- **Canvas split**: `drawX.ts` (pure draw fn) + `XCanvas.tsx` (React wrapper +
  camera). Followed by both the level and world canvases.
- **cradle never writes pack files** — everything goes through a canon CLI verb.

---

## Hard-won gotchas (each of these cost real time)

- **A devMock that reimplements a write can hide a real read/write split.** The
  world-map editor wrote `world.json` while the read verb read the *manifest*, so
  edits appeared to vanish until a recompose. The mock mutates its own copy and
  looked perfect. **Verify write-then-read round trips against the real CLI.**
- **Long coordinate sweeps hang the browser pane** (30s timeout). Never scan
  ~100 synthetic clicks across a canvas. Target the click, or assert on
  state/CLI instead of pixel-hunting.
- **Synthetic pointer events do not trigger React's `onPointerEnter`** — React
  synthesises enter/leave from `pointerover`/`pointerout`. Use the real
  `computer{action:"hover"}` (needs a prior screenshot; coordinates are
  screenshot-space, not CSS).
- **The console buffer is sticky across reloads and navigations.** Stale errors
  reappear with old `?t=` file hashes. **`tabs_create` + navigate is the only
  reliable clean-console check.**
- **Anything an observer or a pointer handler depends on must not be
  conditionally mounted or read from lagging state.** Three separate bugs this
  cycle: a `ResizeObserver` whose `[]` effect ran while the ref was null (early
  `return <Loading/>`) and never attached; a camera callback handed a fresh
  object every render causing an infinite loop; a drag handler reading `live`
  state that lags a render. Refs, and keep the observed element mounted.
- **Don't animate `height` on a flex row above a self-measuring canvas** — the
  canvas re-measures every frame and the transition never settles.
- `getBoundingClientRect` reports layout, not visual clipping.
- Nav groups start **collapsed**; a level button doesn't exist until expanded.

## Doctrine

- **Extend existing machinery; don't build parallel systems.** Say what each
  change extends.
- The design bundle **excludes animation and generation-run UI** by explicit
  instruction (`README.md:7`, `:246`) — do not infer direction for those from it.
- Deliberately deferred by the user: player token / Play route, freehand area
  drawing, the overmap extension. **Secret rooms are sub-rooms inside a level and
  correctly do NOT appear on the world map** — this is settled, don't "fix" it.
