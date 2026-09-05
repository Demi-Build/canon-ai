# Deferred to-dos — the parking lot

Everything intentionally **outside** the September master PRD (`September_master_prd.md`, §8 A-6: the master carries only what Phases 0–2 need). Nothing here has a build row, a schedule, or a promise — each item waits for its trigger, usually the user raising it. When one activates, it gets its own scoping pass; don't build from this list.

## Design passes (user-led, whenever wanted)

- **Settings/keys screen** — pattern-built in P0-12 (rows-as-data); a real design pass would replace the pattern.
- **Library / reuse** — the "how should reuse feel" conversation, then the Screen F build decision. Package sandbox 08 + P7 exist, parked; backend untouched.
- **Project-start / template screens** — incl. the generation-run moment (the shell package's own named "biggest gap"), and the create wizard's key-precheck + seed/model Advanced states (pattern-built in P0-10).
- **Guided `bible synthesize` moment** — a banner/flow for opening a bible-less pack; the verb is CLI/dev-run in Phases 0–2 (master §8 A-4).
- **Text-editor / story-writer surface** — narrative prose and world-bible text editing; the writer specialist's human-side counterpart. Named 2026-09-01; no package covers it.

## Product features

- **Budget/spend warnings** — per-session / per-project thresholds that warn, **never block**. Hard caps are permanently rejected (master §8 A-2) — do not build caps.
- **User-created templates & saveable gameplay components** — save components (double jump, shooting, swim, …) as selectable pieces within a template; save world-bible/database changes, separately or together, as user templates and gameplay styles. `pack promote` grown up. The master's §3.0-F template-integrity rule (project expansions are journaled, project-scoped units that never mutate the template) is what keeps this possible.
- **Game-UI system** — shops / inventory / in-game menus, cross-template and cross-engine. W2.3 does only concept work seeded by the dungeon pull-in's shop/stock inventory; the system lands with a template that natively has shops (farming sim or the 3D era).
- **Phase 1's V1.5 remainder** — richer field-level regen targets as the `#field` grammar grows; JobTray/agent-runs unification polish; session-resume UX.
- **Ticket T2** — slab-capped one-way platforms (+ the remaining postmortem tickets on the ticket list).

## Next-era

- **The 3D fighter/shooter template** — dev-run fast-follow after Phases 0–2; brings the full any-engine matrix and a third template. W2.2 already builds toward it without depending on it: anchor artifacts (GLB + rig + poses + clips) are durable pack artifacts, and nothing in 0–2 bakes a 2D/engine/resolution assumption (W2.4's rule).
- **Farming-sim / beat-'em-up templates** — named next after the dungeon crawler proves template #2; template #3 should cost declarations only (the Phase 0 thesis).
- **October: web / multi-user era** — hosting, accounts, the Demi gateway id (a data entry, not a refactor), auto-updater when audience warrants. September's only obligation to it is the I1–I8 invariants, already in the master's doctrine.

## Later gates (also tracked in the master §5 open items)

- **pygame-LGPL legal review** — before *wide distribution* only: wheel replaceability, complete transitive notices, the AppImage/WebKitGTK case.
- **SDXL-Turbo Mac default swap** — before any bundled release of images-local (excluded from September bundles anyway).
