# Cleanup — code must not cite a planning document

**Rule:** master §1 doctrine **11** (added 2026-09-05 on the user's instruction).

**Why now:** these PRDs are archived and eventually removed from the repo. Every reference
below outlives its referent. The user hit the worst case in the hand-test pass: `db complete`
on `sfx` returned a JSON field named `row` whose value was a master PRD section.


## Tier 1 — user-visible runtime strings (canon). Fix these first.

32 string literals that are NOT docstrings, so they reach a user through an error,
refusal, warning, `--help` page, or agent prompt.


**`src/canon/adapters/__init__.py`**

- L82: `P0-8`

**`src/canon/adapters/dungeon_write.py`**

- L706: `— resizing is an engine constant until the runtime pull-in (P0 paper P.6.5 M9)`
- L812: `rows — an encounter IS a combat event (P0 paper P.9 G4), so a pack without one cannot place monsters`
- L983: `and cannot be resized — the dungeon engine sizes from its own constants (P0 paper P.6.5 M9); got`

**`src/canon/agent/eval.py`**

- L192: `); priced by the §3.0-C module from P0-7` — **cleared:** the note now reads
  `measured tokens in=…/out=… (cache read=…, creation=…); $… measured at <model>` (or
  `…; unpriced — <reason>`), priced through `canon.pricing` in `_cost_note`.

**`src/canon/agent/skills.py`**

- L79: `recipe bound/gate widening is never Always-allowable — it confirms per instance, like paid (master §3.0-F)`

**`src/canon/agent/tools_paid.py`**

- L146: `P0-10 (create flow + project store) / P1-A9 (the start-page create conversation)`

**`src/canon/agent/tools_read.py`**

- L445: `has no row loader (a `db define`d kind reads at row P0-6)`

**`src/canon/cli/main.py`**

- L988: `Run through the DAG scheduler (DEFAULT on every template that has one, master §8 Q6): persists bible.json so resume/regen and per-`
- L1330: `Pack registry: type, capabilities, entity kinds (P0 paper P.4.6).`
- L1383: `Provider rows as data: `providers list`, `providers test` (row P0-12).`
- L1435: `The pack registry as data: `registry set` (P0 paper P.7.4).`
- L2637: `The provenance journal — the cost dashboard's ONE source (row P1-A6).`
- L3235: `Partial EntityKind JSON — minimum label, layout, id_field; optional id_alloc, llm_fields, user_fields, …, and an inline "schema": `
- L3754: `NPC dialogue: selector-model trees, gates, the tester and the selector (Phase 0 §7.2; row P0-9).`
- L3760: `Group scenes — the `type: "scene"` event rows (P0 paper P.1.5 / P.9 S7).`

**`src/canon/db_ops.py`**

- L91: `Phase 0 §6 `canon generate / regenerate / reroll` registry wiring (unassigned in the master)`
- L1396: `type renames are v1.1 (Phase 0 §6: `db evolve` does field renames only) — define the new kind with `db define` and move rows by ha`

**`src/canon/dialogue/storage.py`**

- L152: `declares no 'dialogue' capability — enable it with `canon registry set` before authoring dialogue (Phase 0 §5.1a)`

**`src/canon/dialogue/verbs.py`**

- L927: `has an event_positions entry — a scene must never get one (P.9 S7), or the engine triggers it as a combat event`

**`src/canon/engine_ops.py`**

- L93: `This project's engine copy is code-evolved, so the pygame-side surfaces (capture_frames, run_trajectory, cradle's per-level ▶ Play`
- L172: `is inside canon's own source — the agent never edits canon, cradle or a shared template (Phase 1 §7.2). Only this project's engine`
- L629: `This pack's engine copy has agent- or hand-edited files. Say so in the transcript BEFORE running, capturing or launching anything `

**`src/canon/packs/dungeon/rolls.py`**

- L639: `select an encounter first — a monsters roll re-rolls ONE encounter's roster (P0 paper P.9 G4: monsters reach a room through a comb`

**`src/canon/provenance.py`**

- L377: `: a costed journal event needs an accuracy flag ('measured' | 'estimated' — canon.pricing.MEASURED / ESTIMATED); an unlabelled cos`

**`src/canon/providers.py`**

- L185: `Image-to-3D meshes, texturing, auto-rigging (the 3D lane arrives with W2.2).`

**`src/canon/registry_ops.py`**

- L88: `engines are `engine attach` territory (W2.2)`
- L295: `is refused: the tuning block stays `status: reserved` until W2.1 flips it`
- L325: `capabilities takes the map form {"<id>": true} (a stored list, P.4.2)`
- L329: `: only enabling (true) is supported in v1 — disabling a capability is v1.1 (P.9 R12)`
- L349: `: accepted as registry data — no verb reads tuning.keys yet (W2.1 flips the block)`

**`src/canon/world_ops.py`**

- L216: `is protected (identity / provenance / generation-owned / engine-owned — P.7.2)`


## Tier 2 — docstrings and comments

Not user-visible, but they go stale the day the docs are deleted, and they are the reason a
newcomer cannot read this code without a PRD open beside it.


- canon docstrings: **257** across 77 files. Worst offenders:

  -  31  `src/canon/cli/main.py`
  -  16  `src/canon/adapters/dungeon_write.py`
  -  12  `src/canon/db_ops.py`
  -  12  `src/canon/provenance.py`
  -  11  `src/canon/agent/tools_paid.py`
  -  10  `src/canon/packs/__init__.py`
  -  10  `src/canon/packs/spec.py`
  -   8  `src/canon/packs/platformer/ops.py`
  -   7  `src/canon/packs/dungeon/rolls.py`
  -   7  `src/canon/adapters/dungeon_read.py`

- cradle TypeScript/TSX comments: **212**
- cradle Rust doc comments: **83**


## The one judgment call

`src/canon/providers.py` is deliberately the ONE source of provider rows, and that was a design win —
rule 8's M0 rule says ids are data, never a hardcoded union, so the rows must stay in canon. The fix
is therefore NOT to move provider data into cradle. It is that a row carries **facts** (id, env var,
whether it is set, where it came from) while **prose** (`"unlocks": "…the 3D lane arrives with W2.2."`,
licensing summaries) is presentation. Either the prose moves to the layer that renders it, or it stays
as data but stops naming roadmap milestones.

## Suggested order

1. The `db complete` `row` field and its `COMPLETE_NOT_YET_ROW` constant — the one that reached the user.
2. The remaining error and refusal messages, which are where a citation hurts most: the reader is already stuck.
3. CLI `--help` text, which is the product's front door.
4. Agent prompt strings — an LLM cannot resolve `row P0-6` either, so it is pure token waste.
5. `providers.py` prose, per the judgment call above.
6. Tier 2, mechanically, whenever the surrounding file is next touched.
