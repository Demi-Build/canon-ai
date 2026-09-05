# Human test plan — row P1-A5, the agent panel

**Row:** master §3.1 stage 4, `P1-A5 [B2]` · **Gate:** full flow headless and native; matches spec.
**Design source binds this row:** `cradle/design_handoff_agent_panel/README.md` is the interaction
spec and `PLAN.md` the build order. Steps 1 through 9 shipped; step 10 (start page) is A9's and
step 11 (cost dashboard) is A6's, landing in Stage 5.

The headless pass below needs no Tauri, no keys and no network: the devMock scripted agent drives
every panel state. That is deliberate, and it is one of Phase 1's release criteria.

## A. Suites

```bash
cd ~/Documents/projects/cradle && npx vitest run --reporter=dot && npx tsc --noEmit && npx eslint src && (cd src-tauri && cargo check)
```

- [x] **A1** vitest green (34 files), tsc clean, eslint silent (0 errors and 0 warnings, the
      cleanup you asked for), cargo clean.
- [x] **A2 — every cost gate is the paid card now.** The old `window.confirm` spend dialogs are
      gone from all 13 sites; the two remaining `window.confirm` calls are the tab-close guard the
      README requires.



## B. Headless walkthrough (the bulk of the gate)

```bash
cd ~/Documents/projects/cradle && VITE_CRADLE_MOCK=1 npm run dev
```

Open the app in a browser, then work down the README section by section.

- [x] **B1 — the column (§1).** Toggle with the top-bar button, ⌘⇧A, or the palette's "Ask agent…".
      412px default; drag the handle (accent while dragging, a mono px readout, clamps 340 to 720);
      double-click resets. Reload: width and open state persist. Collapse to the 40px rail: expand
      button, one glyph per conversation carrying its status dot, session cost rotated at the
      bottom. Narrow the window below 720px of main: it auto-collapses once with a toast, and
      re-expanding at that width sticks. Open Notes: the panel dims to 60% and ignores input; Esc
      closes notes first, then stops the agent. Focus mode hides it; the rail returns on exit.
- [x] **B2 — tabs and header (§2, §9).** Status dots: pulsing while streaming, amber when waiting
      on you, red when errored and unread. Waiting tabs sort ahead of idle; a running tab never
      re-sorts under you. Overflow folds into +N. The model picker groups by provider with
      per-1M pricing on every entry and greys unavailable ones with the reason and where the key
      comes from. First run seeds three prompts from the real project plus the one line of law.
- [x] **B3 — the transcript (§3).** It reads as a log, not a chat app. Reads collapse to one dim
      mono line, expand with ▸, and fold into "read N artifacts" past six. Hover a message for
      edit, retry, copy; editing branches and truncates below. The `@` picker offers levels,
      actors, docs and what is on screen, with the current level attached by default.
- [x] **B4 — write cards and the three diff renderers (§5).** Spatial diffs draw through the same
      code the editor's canvas uses, so a preview cannot disagree with the canvas. Fields show old
      struck red and new green with a hidden-count. Code shows a real unified diff. Each has
      "Show me", which opens, selects and pulses the target.
- [x] **B5 — permission chips (§6).** Copy reads "‹Specialist› wants to ‹verb› ‹target›." with
      Accept, Always allow in this project, Reject. The middle label is never shortened and is
      disabled with its reason in Ask mode. A rejected chip collapses to what did not happen with
      "Allow after all" and "Tell it why".
- [x] **B6 — the paid card, four states (§5).** Estimate with the price inside the Accept button
      and the backend and model named; running with phase, item, i of N, elapsed and spent so far;
      result with actuals; stopped with the billed amount and what was kept versus never started.
      A $0 all-fake selection never raises it.
- [x] **B7 — run cards and plan mode (§4, §7).** Nested specialist cards with collapsed sub-logs
      and their own ⏹. Plans render proposed, running and halted, steps check off live, and the
      change feed after a batch deep-links each artifact with Play, Undo the batch, and Open in
      History.
- [x] **B8 — Stop in three places (§10).** The header stops the reply and every run beneath it;
      each run card stops itself; the job tray stops a row. The tray shows editor-launched and
      agent-launched jobs in one list with an attribution column.



## C. Native

`CANON_BIN` is no longer needed for a checkout: `npm run tauri dev` finds a sibling
`canon-ai/.venv/bin/canon` on its own (cradle's resolver leg `checkout`, debug builds only,
overridable with `CANON_BIN`/`CANON_REPO`). Set it only to point somewhere else.

- [x] **C1** launch cradle (`npm run tauri dev`), open a project, open the panel. Cradle spawns
      the sidecar, reads its port, and the panel connects. Settings → Environment should name
      `checkout` as the origin and show the path to this checkout.
- [x] **C2** quit the app and confirm no `canon agent serve` process survives.
- [x] **C3** the integration test proves the same path automatically:

```bash
cd ~/Documents/projects/cradle && CANON_BIN=~/Documents/projects/canon-ai/.venv/bin/canon CRADLE_TEST_PACK=/tmp/a4pack npx vitest run src/lib/agent.test.ts
```



## D. Decisions to confirm

- [ ] The conversation history menu shows turn counts, not costs, until A6 lands the cost lane.
      Declared as a deviation rather than faked. Confirm.
- [ ] The model picker's choice takes effect on the next sidecar start, since the backend is
      chosen when the service spawns. A per-conversation live switch is not built. Confirm.



## E. On approval

- [ ] Say "A5 approved". Steps 10 and 11 of the package remain with A9 and A6 by design.