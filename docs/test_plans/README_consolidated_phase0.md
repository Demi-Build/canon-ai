# Testing the September build — all eight stages, one ordered pass

**Status 2026-09-01:** stages 1 through 8 of `September_master_prd.md` are built and verified by
machine. Twenty-two rows are waiting on your approval. Stage 9's two rows, the agent release and
the Phase 0 release, are your sign-off by definition and are what this pass leads to.

Everything mechanical already passes. Canon runs 3403 tests, cradle 703 across 59 files, with ruff,
tsc, eslint at zero warnings, cargo check and a nine-conversation agent eval all green, and the
platformer's byte-identity comparisons intact through every extraction. What no machine here could
check is whether the thing feels right, whether the copy tells the truth, and whether the choices I
defaulted are the ones you want.

Budget about three hours for passes 0 through 3, which need no money and no other machine. Pass 4
costs a few dollars. Pass 5 needs your push and a second machine.

---



## Before you start

```bash
cd ~/Documents/projects/canon-ai && export CANON_BIN=$PWD/.venv/bin/canon
```

Nothing is staged in git; every change sits in the working tree of both repos for you to review.
The per-row checklists live beside this file and are named for their rows. This document is the
order to work through them in, not a replacement for them.

---



## Pass 0 — mechanical, 30 minutes, mostly waiting

Run these yourself rather than taking my word for the numbers.

```bash
cd ~/Documents/projects/canon-ai && uv run python -m pytest tests/ -q -p no:cacheprovider --deselect tests/test_backend_lyria.py::TestLyriaMusicBackendRegister::test_register_adds_lyria_to_registry
```

```bash
cd ~/Documents/projects/cradle && npx vitest run --reporter=dot && npx tsc --noEmit && npx eslint src && (cd src-tauri && cargo check)
```

```bash
cd ~/Documents/projects/canon-ai && uv run python -m canon.agent.eval --backend fake
```

- [x] canon: 3403 passed, 4 skipped, 1 deselected, no failures
- [x] cradle: 703 passed, tsc silent, eslint silent, cargo clean
- [x] eval: 9 of 9 at $0

---



## Pass 1 — the two sanity checks, 30 minutes

These are the two I most want a second opinion on, because they are where a wrong call compounds.

### 1a. The room editor's write model (from `P0-8_cradle_surfaces.md`, section B)

Open cradle on `bibles/mazeworld_scifi`, go to Rooms, open `room_0`.

- [x] Paint walls and floor; drag an NPC; place an item from the Dock; drag spawn and the door.
- [ ] **Drop a monster on a cell.** This is the decision you reversed: rather than dropping a
      monster directly, it creates or targets the combat encounter at that cell and adds the monster
      to it. Two files change together. Does that model match how you want to build encounters?
- [x] Try the refusals: paint a wall over a placement, drag the door away from its gate, place on
      the spawn cell. Each should refuse with a reason and change nothing.
- [x] Roll each step. Every one should read "$0, code only" and raise no spend card.
- [x] Restore an earlier version from History and confirm the other step's edits survive. That was
      a real defect two stages ago; it is worth seeing work.



### 1b. The cost dashboard reconciles (from `P1-A6_journal_dashboard.md`, section B)

```bash
cd ~/Documents/projects/canon-ai && uv run canon journal list /tmp/a3_pack --summary | python3 -m json.tool | head -40
```

- [x] Every total sums one field over journal events, so the by-kind, by-identity and
      by-conversation tables agree by construction rather than by coincidence.
- [x] Measured and estimated are visibly different. A fal image is priced from the table and says
      so; a PixelLab image reports what the provider billed. Neither is ever a silent zero.
- [x] In the app, the dashboard's four tiles, split bar and three tables match those numbers.

---



## Pass 2 — canon on the command line, 45 minutes

Work through these row checklists in order. Each is short and each has its own commands.


| Order | Checklist                  | What you are proving                                                  |
| ----- | -------------------------- | --------------------------------------------------------------------- |
| 1     | `P0-4_packaging.md`        | the wheel runs every verb from a fresh venv with no checkout          |
| 2     | `P0-3_pack_info.md`        | one vocabulary for pack type, resolved four ways, writing nothing     |
| 3     | `P0-6_write_core.md`       | **success criterion 6**: add a field and define a type with zero code |
| 4     | `P0-5_dungeon_read.md`     | one export serving both pack shapes                                   |
| 5     | `P0-9_dialogue.md` § B     | a branch gated on an item and a time window, proven in the tester     |
| 6     | `P0-7_pricing.md`          | one price source; the figures that changed and why                    |
| 7     | `P0-10_create_flow.md` § B | a dungeon created from the wizard's own command, opening editable     |


The one I would not skip is criterion 6. It is the whole thesis of the phase: a new entity type,
browsable and editable, without a line of code in either repo.

---



## Pass 3 — the app, 60 to 90 minutes

Launch cradle with `CANON_BIN` set.


| Order | Checklist                             | Surface                                                    |
| ----- | ------------------------------------- | ---------------------------------------------------------- |
| 1     | `P0-3_pack_info.md` § B               | both demo worlds open and show the right groups            |
| 2     | `P0-8_cradle_surfaces.md` § B         | editing all nine kinds; the room editor                    |
| 3     | `P0-9_dialogue.md` § C                | the dialogue editor, tester, selectors, engine-lag layer   |
| 4     | `P1-A5_panel.md` § B                  | the agent panel, headless first with the mock, then native |
| 5     | `P1-A9_create_by_conversation.md` § B | creating a world by conversation from the start page       |
| 6     | `P1-A6_journal_dashboard.md` § C      | the dashboard and the permissions pane                     |
| 7     | `P0-12_keys_settings.md` § C          | Settings, and the four deep links landing on the right row |


Two things to watch for specifically, because they are easy to get wrong and hard to notice:

- **The engine-lag layer** in the dialogue editor. On this dungeon world the engine evaluates no
gates at all, so every condition reads amber. It must look deliberate and informative rather than
broken, and it must never stop you authoring.
- **Free never spend-confirms.** Anywhere backends are all fake or none, you should see "$0" on an
ordinary chip and never the accent spend card. If a free action raises a money dialog, that is a
doctrine violation worth reporting.

---



## Pass 4 — the paid legs, a few dollars, run when you want the numbers

None of these are needed to approve the build. They are the legs I am forbidden to run.

- [ ] **The provider-swapped eval** (row A8's gate). Tool order stays strict, wording is freed:

```bash
cd ~/Documents/projects/canon-ai && OPENAI_API_KEY=... uv run python -m canon.agent.eval --backend openai --only just-talking
```

```bash
cd ~/Documents/projects/canon-ai && MOONSHOT_API_KEY=... uv run python -m canon.agent.eval --backend kimi --only just-talking
```

      A few cents each. Add `--only unbeatable-level` to prove tool round-tripping on a real model.
      Running the whole corpus on both providers is what actually closes row A8.

- [ ] **One real generation run.** This is what turns the dungeon estimator's $30 anchor from a
      calibrated guess into a measured number, and it is the first time the `measured` versus
      `estimated` distinction has real data behind it:

```bash
cd ~/Documents/projects/canon-ai && uv run canon world estimate --template dungeon --rooms 3 --llm-backend anthropic --image-backend fal --music-backend lyria --sfx-backend elevenlabs
```

      Read the estimate first, then run the same shape for real through the wizard, then compare
      against `canon journal list --summary`. Estimate versus actual is the number the ledger exists
      to produce.

- [ ] **One paid** `dialogue improve` to see a proposal land in the buffer without writing.
- [ ] **The Settings Test button** against one real provider, which is user-initiated by design.

---



## Pass 5 — needs your push or another machine

- [ ] **Three-platform CI.** `release.yml`'s matrix is written and the runtime manifest pins a
      checksum for all five target triples, but the workflow is tag-triggered. Push the branch, then
      a release tag, and confirm all three jobs go green and the runtime lands inside each bundle.
- [ ] **A genuinely Python-free machine.** Install the resulting build and create a free world.
      That sentence is P0-11's literal gate.
- [ ] **The real keychain prompt** from a signed build, with a sensible app label.
- [ ] **Signing and notarization**, which run in CI with credentials I never touch.

---



## Decisions waiting on you

Every one of these is running as a default. They are listed where you will encounter them, and each
appears again in its row's checklist with more context.

**Things you will see in pass 1 or 3**

1. Monsters place through encounters rather than directly (your reversal of my default; confirm the
  model now that it exists).
2. A room save that touches several wires writes and journals once, with a combined kind.
3. A layout roll settles a walled-in door and warns rather than leaving the room unopenable.
4. On this dungeon world every dialogue gate reads amber, because the engine evaluates none of them.
5. The conversation history menu shows turn counts, not costs, until the cost lane grows a per-
  conversation total.
6. The model picker's choice takes effect on the next sidecar start, not mid-conversation.

**Things about money**

1. Two paid tools ship a zero estimate so the card renders, rather than degrading to a plain chip.
2. No budget caps anywhere, per your earlier decision. Warnings only, and none built yet.
3. Meshy is priced as credits times a configurable rate defaulting to $0.02. Three cells in the
  price table are still unverified and need a dashboard check from you.

**Things about data on disk**

1. `.canon/log.jsonl` differs under orchestration, and two identical creates differ by a
  `created_at` stamp in the registry. Emitted content is identical in both cases. My read is that
    both are fine because `.canon/` sits outside the byte-determinism contract.
2. Scenes live in `events.json` as a scene-typed event sharing the event id space.
3. A dungeon NPC row gains no status field from a dialogue write, so the engine's file shape is
  untouched and provenance lives in the journal.
4. A row restore on a collection kind restores the whole file and says so.
5. Quest states are the engine's four and `time:` uses period names, so the design package's
  `offered`, `turn-in` and hour windows render as unsupported rather than being faked.

**Things about the agent**

1. The write gate is held across a permission round-trip, so a chip awaiting your answer holds
  that artifact's lock.
2. `capture_frames` and `run_trajectory` are auto-tier, demotable to ask by a setting.
3. Real routing quality can only be measured on a real model, so the fake corpus proves the
  machinery and pass 4 measures the quality.

**Things about packaging**

1. The bundle carries the `agent` and `openai` extras; torch stays out.
2. Where no secret service exists, keys fall back to a 0600 file with a loud warning.
3. `db complete` on a dungeon kind answers "not yet" rather than inventing per-row prompts.

---



## What I could not prove, stated plainly

- The three-platform build and a Python-free machine (pass 5).
- The real macOS keychain prompt from a signed build.
- Any real provider call, including routing quality and estimate-versus-actual accuracy.
- Whether the app *feels* right. That is the entire reason this document exists.



## When you are done

Tell me which rows are approved and which decisions you want reversed. Approved rows flip to ✅ in
the master with a short built summary, and anything you reverse I will rebuild rather than patch
around. Stage 9's two release rows are then yours to sign off, which is where Phase 0 closes.