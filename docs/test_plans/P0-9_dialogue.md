# Human test plan — row P0-9, W1 P5 dialogue

**Row:** master §3.1 stage 6, `P0-9 [A]` · **Gate:** author a branch gated on an item and a time
window, prove it in the tester, watch the selector pick the right tree, and see the engine honour
the gates it knows. **Met 2026-09-01**, proven on a copy of the reference dungeon world.

**Design source:** `cradle/design_handoff_dialogue` binds all of it. The README is the interaction
spec, the PLAN's thirteen-step order was the build order, and board 00 is the current-state
baseline rather than a target, so View mode ships unchanged.

What shipped: canon gained the selector model, `dialogue_trees` storage with the legacy four keys
written back so the engine keeps reading, scenes as a `scene` event type, the six verbs, and one
evaluator that the UI calls rather than reimplementing. Cradle gained the editor, the navigator
rail, structural editing, the grammar and entity picker, the docked tester, selectors, the
engine-lag layer, quest lanes, scenes, and improve.

## A. Suites

```bash
uv run python -m pytest tests/test_dialogue.py -q -p no:cacheprovider
```

```bash
cd ~/Documents/projects/cradle && npx vitest run --reporter=dot
```

- [x] **A1** both green (cradle is now 52 files, 602 tests).



## B. The gate, on the command line

Work on a copy so the fixture stays pristine:

```bash
cp -r tests/reference/fixtures/cradle_mazeworld_scifi /tmp/dlg && uv run canon dialogue show /tmp/dlg --npc 1001 | head -30
```

- [x] **B1 — author a gated branch.** Add a choice gated on both an item and a night window
      through `canon dialogue update`. The write journals with a per-field diff and a CAS pair.
- [x] **B2 — prove it in the tester.** `canon dialogue test` with a state that satisfies both
      conditions passes, and a state missing the item fails **with the failing condition named**.
      That naming is the whole point: the tester tells you which gate closed, not just that one did.
- [x] **B3 — the selector picks.** `canon dialogue select` returns the winning tree and explains
      why each other tree lost.
- [x] **B4 — the engine still reads it.** The legacy `dialogue_tree`* keys are written back in the
      shape MazeWorld expects, so an existing engine keeps working while the richer model lives
      alongside it.



## C. In the app

- [x] **C1 — the four modes** and the mode bar behave as the README's state table says; Esc backs
      out in the documented order.
- [ ] **C2 — the navigator rail and ⌘P** make a many-tree NPC usable, and the rail groups trees
      into would-play and blocked from canon's own explanation rather than a local guess.
- [ ] **C3 — structural editing:** add, remove and rewire nodes and choices; the delete preview
      shows what a destructive edit would orphan before you commit it.
- [ ] **C4 — the grammar reads from the world.** Conditions are picked from the pack's own
      vocabulary, with the raw token shown underneath so you can verify what the picker produced.
      Nothing builds a token by string concatenation.
- [ ] **C5 — the picker's two rules.** An NPC already added is disabled rather than filtered out,
      so searching for them does not make them look nonexistent; and picking an NPC outside the
      quest's rooms tells you it will append a room condition before you pick, then applies both as
      one undo.
- [ ] **C6 — the docked tester** drives any tree against simulated state, shows per-choice gate
      results, and supports checkpoints. It calls `canon dialogue test`; the UI never evaluates a
      gate itself.
- [ ] **C7 — the engine-lag layer is the doctrine made visible.** On this dungeon world the engine
      evaluates nothing at choice scope, so every gate reads amber, with the tree banner, the
      dashed choice row and the tray warning naming the namespace and what the engine does instead.
      It must look deliberate rather than broken, and it must never block authoring.
- [ ] **C8 — quest lanes and coverage** across NPCs, saving as one undo entry.
- [ ] **C9 — scenes:** actors, settings, trigger, once, on-finish effects and a numbered script.
      The scene-only `actor:` namespace is rejected inside trees, with its reason.
- [ ] **C10 — improve proposes, never writes.** It lands a per-field proposal in the unsaved
      buffer. It is a paid leg, so it shows an estimate; on a fake backend it is $0 and raises no
      spend card.



## D. Decisions to confirm

- [ ] A dungeon NPC row carries no `status` field, so a dialogue write does not add one: the engine
      file shape stays untouched and provenance lives in the journal instead. Confirm.
- [ ] Quest states are the engine's four and `time:` uses period names, so the design's `offered`,
      `turn-in` and hour windows render as unsupported rather than being faked. Confirm.



## E. On approval

- [ ] Say "P0-9 approved". Suite after Stage 6: canon 3327 passed, cradle 602 passed.