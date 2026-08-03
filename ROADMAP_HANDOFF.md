# Cradle / canon — roadmap handoff

Two repos, both on feature branches, both pushed:
- **canon-ai** `cradle_editor_surface` — the generator (Python + a Godot template)
- **cradle** `feat/platformeditor` — the editor (Tauri 2 / React 19 / Rust); Rust
  shells out to the `canon` CLI, never writes pack files itself

Run cradle: `CANON_BIN=<canon>/.venv/bin/canon npm run tauri dev`.
Paid keys now resolve automatically from `<canon repo>/.env`.
**Rust changes need a full `tauri dev` restart — HMR is frontend-only.**

Standing constraint: **extend existing machinery, don't build parallel systems.**
Each item below names what it extends. This repo almost always has an analogue
already — e.g. `db update`'s validate→rehash→journal→`user_edited` shape is the
template for any new write verb; `_Hooks`/`PLAT_*` is the template for any new
harness mode.

---

## TRACK A — design (Claude Design session)

Cradle grew feature-first over many cycles: every capability was bolted onto the
nearest surface. Nothing has had a UX pass. The goal is a coherent information
architecture and interaction model, **not** a reskin.

**Read first:** `src/components/LeftNav.tsx` (nav + type groups),
`DetailPane.tsx` (the tab host), `EntityOverview.tsx` (entity view — note
`GenActions`, which has accumulated 7 buttons), `level/LevelDetail.tsx` (the
level editor, the densest surface).

### A1. The generation-action surface — the sharpest problem
`GenActions` is now a flat row of ~7 buttons (Edit row · Publish · Generate
sprite · LLM re-complete · Animate · Preview pygame · Preview godot) plus two
collapsible prompt editors. No hierarchy, no separation of free vs paid vs
destructive. **Design question:** what is the right grouping — a toolbar? an
actions menu? split "view" from "generate"? Paid actions should read as paid
*before* the confirm dialog.

### A2. Level editor density
`LevelDetail` carries a toolbar (Validate · Play · Layout · Improve · Music ·
Enemies · Items · backend select · view modes · grid/labels · Save), a palette
rail, a canvas with pan/zoom, an inspector, and a revision chip. It works but is
crowded and mode-heavy (armed brush, view mode, selection are three separate
implicit states). **Design question:** how should mode be expressed so the
current state is always obvious?

### A3. Nav / information architecture
Just added an ACTORS group (Player + Enemies). The rest is a flat type list:
Levels · Actors · Items · Tilesets · Backdrops · Audio · Library. **Design
question:** is type-first right, or should it be stage-first / play-order-first?
Levels already nest secret rooms under parents with `↳`.

### A4. Job + cost feedback
Generation is async via a job tray (⚙ Jobs, Active/Completed, change badges) and
there's a 💰 Cost dashboard. Both are modal overlays, so progress is invisible
unless opened. **Design question:** should in-flight work be ambient?

### A5. Empty / failure / first-run states
Loud-fallback doctrine means missing art shows placeholder shapes and warnings
are surfaced rather than hidden — good, but presented plainly. Also worth a pass:
the paid-confirm dialogs (currently `window.confirm`), and the new
`missing FAL_KEY — not found in <path>` pre-flight message.

**Constraints for design:** desktop app, dark-first (there's a theme toggle);
pixel-art content — never smooth-scale sprites; existing CSS lives in `App.css`
with `--surface-*` / `--text-*` / `--border` custom properties; components use
inline styles in newer code and classNames in older code (consolidating that is
fair game). Browser mock for headless UI work: `VITE_CRADLE_MOCK=1` on port
5199, backed by a real pack via a symlink — so design work can run against real
data with no Tauri.

---

## TRACK B — engineering

### B1. Finish the animation cycle *(in progress; phase 1 of 5 done)*
Plan: `~/.claude/plans/toasty-foraging-moore.md`.

Shipped: the per-actor frame-scaling fix (the "half the frame vanishes in the
jump" bug), `--renormalize`, an `animation_scale` QA check, `PLAT_ANIM` viewers
in pygame + Godot, the Player entity, and a real Godot atlas-registration fix.

Remaining, in order:
1. **fal model + prompt access** *(smallest, highest value)*. canon is already
   fully parameterized — `ops.animate_asset` and `canon asset animate` take
   image/edit/vlm backend + model. Cradle's `animate_asset` drops 4 of 7 params
   and `EntityOverview` hardcodes `"fal"`/`"anthropic"`. Also add `--prompt` to
   `asset animate` (`asset generate` already has it) and an `animate` entry in
   `PROMPT_KINDS` → `vlm_qa.animate_prompt`. Mostly plumbing. Note only `fal`
   and `fake` implement `ImageEditBackend`, so only they can animate.
2. **In-cradle Animation tab** — playback + QA badges. Design calls for a canon
   `asset animation` read verb emitting a normalized manifest (atlas→strips→
   static ladder collapsed, both schema generations, QA merged), with timing
   ported to TS and pinned by a shared golden fixture.
3. **Per-actor custom animation states + swim.** The art pipeline is *already*
   data-driven. The unlock is `behavior.animation_states` + a `--states` flag;
   the one blocker is `_STATE_BRIEF[s]`, a bare subscript that `KeyError`s on any
   new state name. Real work = 5 hardcoded candidate lists across
   `platformer_play.py` and `main.gd`. Swim is currently overloaded onto `walk`.
4. **Dust / VFX toggles.** `dust`/`splash`/`sparkle` prop sprites exist but fire
   from only 3 Godot triggers (land/water/collect) and none in pygame;
   `effects.py` `KINDS` already carry clamped params. Needs a write verb for
   graphics/effects (none exists) — mirror `db update`.
5. **pygame/Godot player draw divergence** — pygame draws the player 24px
   top-left-anchored with no `actor_scale`; Godot 33.6px feet-anchored ×1.4.
   Cosmetic, so `PLAT_TRAJ` must stay byte-identical across the fix.

### B2. Game-feel tuning panel
Long-wanted: momentum / gravity / **local friction**. Seams exist (tile
`friction`, per-level movement + rule overrides from the combat-picks arc).
**Same work as postmortem ticket 1 (momentum/friction + ice) — do them together.**

### B3. Postmortem tickets — 8 confirmed on disk, none executed
Top 5: momentum/friction + ice · slab-capped one-way platforms + headroom · room
diff-cap + remove exit gate · `review_status` demote-on-VLM-fail · a grab-bag of
7. Bottom 3 are explainers: tautological gates → drift meters · animation
character-drift detector + base-ref · transient near-dupes. Cadence the user set:
per-ticket confirm → outline → approval → execute.

### B4. Job-queue follow-ups *(deferred v1 scope)*
Fold `new_project` + db ops into the queue · cancel (queued, then running) ·
finer change magnitude (cell counts, not just changed/unchanged) · a real
distributed queue (Celery) for the eventual server path — the `cradle-jobs/v1`
ledger and Job model are already backend-agnostic · music last-change still
labels "Saved edit".

### B5. Larger, unscheduled
- **Training-data export** — the payoff for all the provenance work
  (`.canon/journal.jsonl` + a content-addressed store already capture
  generate→edit pairs).
- **MazeWorld parity** — cradle's other pack, currently behind.
- **Unified create entry** — fast-follow from the level-generation cycle.
- **Library revisit** — built, then set aside 2026-07-27; the user disliked the
  UX and the interaction model is still open. **Good Track-A candidate.**

---

## Verification (any change)

- **canon:** `.venv/bin/python -m pytest tests/ -q` (~2073 pass; the one
  `test_backend_lyria` registration failure is a known env issue needing
  `GOOGLE_API_KEY`). Art suites are slow (~6 min) — run them in the background.
- **cradle:** `npx tsc --noEmit && npx vitest run && (cd src-tauri && cargo check)`
- **browser mock:** `VITE_CRADLE_MOCK=1`, port 5199 — real pack data, no Tauri.
- **Play surfaces:** pygame via `PLAT_CAPTURE` (headless PNG dump); Godot via
  `--write-movie --fixed-fps --quit-after` **windowed** (headless write-movie
  crashes on MoltenVK). Godot's exit code lies — grep output for `SCRIPT ERROR`.
- **Physics parity:** `PLAT_TRAJ` must stay byte-identical across cosmetic
  changes. It compares positions only, so it cannot see rendering bugs — that
  blind spot is exactly how the Godot atlas bug survived.

Use `python3 -m canon.cli.main` (cwd = repo root) for verbs importing
`examples.*` (the estimators); everything else works via the `canon` console
script.
