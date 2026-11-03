# Prompt — the Master September PRD session

**How to use:** open a fresh Claude Code session **in
`~/Documents/projects/canon-ai`** (project memory auto-loads there and carries
the whole decision history) and give it access to the cradle repo too —
`claude --add-dir ~/Documents/projects/cradle`, or just approve the read
prompts when they appear. **Two repos are needed: canon-ai and cradle.**
Poseforge is *not* — its role is settled in the Phase 2 PRD (vendored-copy-
only) and the decision record carries the details; nothing in the merge needs
to read it. Paste everything below the line. Expect a question round before
any writing — that is by design, and the session runs **three passes with
subagent reviewers**, not one.

---

You are consolidating three finished phase PRDs into **one master September
PRD**. The phases were authored in separate sessions, in order, and later
phases made decisions that re-shape earlier ones. Your job is to process,
review, **reorder, and re-inform** all three into a single consolidated build
plan from Phase 0 through Phase 2 in which **nothing is built that a later row
undoes, works around, or impedes** — and in which **every screen that gets
built has a named design source**.

## Inputs — read in this order

1. `docs/September_Phase_0_prd.md` — registry, create-from-template,
   packaging/keys, the dialogue system, mazeworld parity. 13 build rows;
   October's I1–I8 anti-lock-in invariants at the end bind everything.
2. `docs/Phase_1_prd.md` — the Cradle Agent. Deliverables A0–A10; its own
   sequencing note: build A1–A8 in parallel with Phase 0's W1, release after
   W1 P3.
3. `docs/Phase_2_prd.md` — sandboxes, the live game hook, the 2D/3D authoring
   system, full audio. Waves W2.0–W2.3; sequenced after Phases 0 and 1.
4. The design packages (binding for interaction, copy, and state tables —
   **never rewrite them**):
   - `~/Documents/projects/cradle/design_handoff_editor_worldmap_start/` (shell)
   - `~/Documents/projects/cradle/design_handoff_agent_panel/` (Phase 1 panel)
   - `~/Documents/projects/cradle/design_handoff_dialogue/` (Phase 0 dialogue)
   - `~/Documents/projects/cradle/design_handoff_sandbox/` (Phase 2 — 20
     screens + 9 flows; its PLAN = 26 build rows)
   - `~/Documents/projects/cradle/design_handoff_3d/` (Phase 2 3D — 11 boards;
     its PLAN = 12 build rows; README §2 gate thresholds are design-owned)
5. The decision record (every question round S/R/A/L/M/D, the recon, the
   supersedence trail):
   https://claude.ai/code/artifact/cf45e6e0-57d1-4081-b7af-b2f19e578987
6. Secondary references (context, not binding — consult when a claim needs
   grounding): `ROADMAP_HANDOFF.md` (canon-ai root),
   `~/Documents/projects/cradle/docs/HANDOFF_NEXT.md` (the locked dungeon
   epic Phase 0 absorbed), `docs/provenance_traceability_spec.md`,
   `docs/platformer_prd.md`, and the two design prompt docs
   (`docs/Phase_2_design_prompt.md`, `docs/Phase_2_design_prompt_3D_addendum.md`)
   which record what each package was *asked* for.

Project memory is background context; the documents above are the truth.
Where a document and the artifact disagree, the document is newer unless the
artifact's entry is dated later — flag any such case rather than guessing.

## Ground rules

- **The three PRDs are the decision baseline.** Reorder and re-inform freely;
  never silently drop a decision. Where a later phase supersedes an earlier
  one, the master carries the reconciled version **and names what changed**.
- Design READMEs win on interaction/copy; PRDs win on scope, sequencing, and
  verb/data contracts; the master PRD becomes the top of that stack.
- **No code. No git.** This is a documentation/planning session; the user
  handles all git operations.
- Doctrine that binds every row: extend existing machinery, never parallel
  systems · cradle never writes pack files (verbs only) · paid legs are
  user-run, estimates on the button · disabled-with-a-reason beats hidden ·
  free never confirms · the I1–I8 invariants **as summarized inline in
  Phase 0's W4 stub** (October's document itself is out of scope and not an
  input).
- Ask before inventing. If two phases genuinely conflict and no recorded
  decision resolves it, that is a question for the user, not a coin flip.

## Pass 1 — the cross-phase audit (no writing yet)

Fan this out to subagents rather than reading serially: one reader per phase
PRD, one per design package (returning that package's build rows + screens +
copy-level claims), and a dedicated **collision hunter** working the list in
B below plus its own sweep. You synthesize; the subagents read.

**A. Dependency map.** Every build item across all three phases (Phase 0's 13
rows, Phase 1's A0–A10, Phase 2's W2.0–W2.3 plus the two design PLAN orders it
wraps), with its true prerequisites — including cross-phase edges the
documents state (A2 ← W3.2; A10 ← W1 P3; Phase 2's dialogue live ← W1 P5;
mesh_smith ← Phase 1's roster machinery) and any they don't.

**B. Collision scan.** Find every place a later decision changes an earlier
build — the exact failure the master exists to prevent. Verify at least these
known candidates from the decision record, and hunt for more:

1. **Phase 1 §7.1's "a pack contains only the Godot engine copy — pygame is a
   shared harness"** vs Phase 2's R1 promotion (pygame becomes a per-project
   engine copy, `game_coder`-evolvable). The amendment is recorded; the master
   must carry the reconciled text so Phase 1's code-evolution rules (twin
   demotion, gate ladders) are written against engine *copies*, plural.
2. **The `engines` block in `PackSpec` / `.canon/registry.json`** — a Phase 2
   requirement that must exist from Phase 0's P0 paper (row 1) onward, or the
   registry gets rebuilt later. Confirm it is in Phase 0's row-1 inventory;
   if not, add it there in the master.
3. **`canon world update` (Phase 0, "new, small")** must ship generic enough
   to carry Phase 2's `tune set` pack-scope writes (manifest movement/combat/
   rules under the protected-wall discipline) — otherwise Phase 2 rebuilds it.
   Same check for `db schema --set` carrying Phase 2's band-widening.
4. **Phase 0's hard scope line "do not drift into physics"** is correct for
   Phase 0 *builds* but the master must state it as a phase boundary, not a
   product anti-goal — Phase 2 is exactly physics.
5. **Sessions and Stop.** Phase 1's ⏹ covers agent runs + JobQueue jobs;
   Phase 2 owns play-session registry/kill/live-channel. One session model
   must serve both — decide which rows build it and make the other consume it.
6. **The journal/ledger schema.** Phase 1 §6 adds identity/costCents/genKind/
   batchId; Phase 2 adds the gen-inputs manifest (v0) and the
   accepted-tuning detail kind. One schema change, made once, early.
7. **The price/constants source.** Phase 0's estimator-core extraction,
   Phase 1's measured-vs-estimated flags, Phase 2's price table — one
   constants module, built at the earliest row that needs it.
8. **The keys/Settings screen (Phase 0 W3.4–3.5)** must include rows Phase 2
   needs later: `MESHY_API_KEY` (with the paid-tier-for-commercial note) and
   the Environment pane listing `BLENDER_BIN` beside Godot detection — add
   the rows in the master so the screen is built once.
9. **`jobs_list`/`jobs_record` native fix** — named by Phase 1 (A6-adjacent)
   and relied on by Phase 2's job surfaces. One row, placed once.
10. **Progress events beyond `new_project`** — Phase 1's run cards need
    per-job progress; Phase 2's generation surfaces too. Decide the row.
11. **Skills format (Phase 1 ASSUMPTION-13)** vs Phase 2's
    recipes-are-mesh_smith's-skills — one loader, one precedence rule.
12. **`DialogueVariantSpec`** is superseded by Phase 0's selector model —
    confirm no Phase 1/2 text still references the old plan.
13. **Phase 0 W3.3's vendored runtime bundles pygame** — reconcile with
    Phase 2's ASSUMPTION-1 notice posture (generated notices, replaceable
    wheel, auto-notices on user game exports) so packaging is built
    compliant the first time.
14. **`--orchestrate` becomes the default `world new`** (Phase 2's staleness
    floor) — Phase 0's create flow (row 10 / W2) should ship it that way
    rather than Phase 2 retrofitting.
15. **CreateProgress phase labels** — Phase 0 W2 adds dungeon labels; check
    nothing later adds a third hardcoded list instead of data.

**C. Screen registry.** Every screen/surface the combined plan builds →
its design source (which package, which board) → its build row. **List every
screen with no design source** — known thin spots to check: the Settings/keys
screen (W3.5), the create-wizard template cards (`pack templates`-driven),
the dungeon room editor (P4 — level-canvas reuse may cover it), and anything
the collision scan surfaces. Unsourced screens either get a design task row
or an explicit "built from the pattern of X" note — never silence.

**D. The single sequence.** Propose one interleaved build order 0→2 with
parallel tracks marked, every row carrying: origin tag (P0-#, P1-A#,
P2-W#/row#), its gate, its design source, and — where a late decision
re-shaped it — a one-line "informed by" note. Respect the recorded
sequencing locks (Phase 2 after 0+1; A10 after W1 P3; W2.0's
dependency-free set leads Phase 2) unless the dependency map proves a lock
wrong — in which case ask.

**E. The ten most important questions** before writing — including at least:
whether the three phase PRDs get archived/marked superseded or stay as
appendices; where the ratified/pending assumptions land (A1–A4 ratified,
A5 is a noted bet pending the user's full read, D4 spike open); and any
collision the scan cannot resolve from the record.

**Report Pass 1, then stop and wait for answers.**

## Pass 2 — write the master (after answers)

Write `docs/September_master_prd.md`:

1. **Problem, thesis, doctrine** — unified once (the game is the primary
   lens; data is truth; verbs are the only writers; the standing doctrines).
2. **The consolidated anti-goals** — merged from all three, deduplicated,
   with phase-boundary lines (like "no physics") restated as sequencing, not
   product scope.
3. **The single build sequence** — the Pass 1 table, refined per answers.
   Adopt Phase 0's maintenance convention: every row is a deliverable with a
   gate; when built and human-reviewed, the row flips ✅ and its spec prose
   is replaced by a 2–5 line "Built" summary.
4. **The screen registry** as a first-class section.
5. **The risk & assumption register** — consolidated from all three phases +
   the assumption states above + D4 + the known risks (Meshy variance, bpy
   drift, audio engine mass, channel-vs-parity, scope mass, design/build
   drift).
6. **What the master supersedes** — an explicit table: each reconciled
   collision, what the old text said, what governs now.

Do not write implementation code. Flag every assumption you must make.

## Pass 3 — the subagent review panel (before sign-off)

Spawn **independent reviewers with fresh context** — each reads the draft
master plus only its own lens's sources, and each tries to *break* the draft,
not bless it:

1. **Sequencing reviewer** — walks the build table row by row asking one
   question per row: "does any later row modify, replace, or work around what
   this row builds?" Any yes is a finding. Also checks every stated gate is
   testable and every cross-phase edge from Pass 1 survived the reorder.
2. **Decision-fidelity reviewer** — diffs the draft against the three phase
   PRDs and the decision-record artifact: every recorded decision either
   appears in the master or appears in the supersedence table. A decision
   that silently vanished is a finding.
3. **Screen-sourcing reviewer** — walks the screen registry against the five
   design packages: every built screen has a real source (package + board),
   every unsourced screen has a design-task row or an explicit pattern note,
   and no package screen was dropped without a reason.
4. **Doctrine reviewer** — checks every row against the standing doctrines
   and I1–I8 (verbs-only writes, paid-user-run, free-never-confirms,
   disabled-with-reason, extend-don't-parallel, no video, three-tier 3D,
   sandbox-never-generates).

Fold the findings, fix the draft, list anything you chose *not* to fix and
why, and present the reconciliation to the user. The master ships when the
user signs off — it is the document the build sessions will run against.
