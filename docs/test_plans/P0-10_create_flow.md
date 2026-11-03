# Human test plan — row P0-10, W2 create flow

**Row:** master §3.1 stage 7, `P0-10 [A]` · **Gate:** a fresh dungeon created from the wizard opens
editable. **Met 2026-09-01.** **Design source:** `design_handoff_editor_worldmap_start` board 06;
the two additions it does not design, the key precheck and the seed/model disclosure, are pattern
builds.

What shipped: `world new --template` dispatching through the registry, `--orchestrate` as the
default, a StepLog in the dungeon runner (it had none, one of the two wiring blockers), phase
labels as template data so the 22 hardcoded platformer ids dissolved, `canon pack templates`
driving the wizard cards, the key precheck, the live estimate, the project store, auto-uniquify,
the recents tile fix, and the engines-block seed.

## A. Suites

```bash
uv run python -m pytest tests/test_create_flow.py -q -p no:cacheprovider
```



## B. The gate

```bash
uv run canon pack templates | python3 -c "import json,sys; d=json.load(sys.stdin); print([t['id'] for t in d['templates']])"
```

```bash
uv run canon world new /tmp/keep --template dungeon --name "Shadow Keep" --rooms 2 --seed dseed && uv run canon pack info /tmp/keep | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['pack_type'], d['source'], d['engines'])"
```

- [x] **B1** a dungeon is created and resolves, with exactly one engines entry.
- [x] **B2 — it opens editable**, which is the day-1-editing rule and the reason this row waited for
      rows 6, 8 and 9. Spot-check by editing a row, describing the room, and showing its dialogue.
- [x] **B3 — the orchestrate flip's only emitted delta is** `bible.json`, exactly as doctrine 7 was
      amended. The verifier diffed a tree at the new default against one with `--no-orchestrate`
      and every content file was identical.



## C. In the app

- [x] **C1 — the cards are data.** The wizard renders `pack templates`; the hardcoded array is gone.
      If canon cannot be reached, it says so honestly instead of falling back to a stale list.
- [x] **C2 — step 2 is honest to the generator.** The dungeon asks for Rooms plus NPC, monster and
      item counts, with events, quests and classes under Advanced. There is no "Floors" vocabulary
      anywhere, because the manifest has no floors.
- [x] **C3 — the live estimate** comes from the pack's own estimator and shows a low-to-high range.
      An all-fake selection reads $0 and raises no spend card.
- [x] **C4 — the key precheck** disables creation with a reason naming the missing key and links to
      Settings. That link is dead until Stage 8 lands the screen, which is the inversion the master
      called out; after Stage 8 it must resolve.
- [x] **C5 — progress is honest.** CreateProgress shows phase, item and counts from the StepLog,
      with labels resolved from template data rather than a hardcoded list. Stop cancels the run and
      keeps what landed.
- [x] **C6 — papercuts.** The recents "add" tile opens the modal rather than the folder picker; a
      name collision auto-uniquifies instead of erroring; the seed and model reach the runner.



## D. Decisions to confirm

- [x] `.canon/log.jsonl` legitimately differs under orchestration (116 lines versus 62), because
      the DAG scheduler logs more than the sequential runner. It is an observability record rather
      than pack content and is already outside the byte-determinism contract. Confirm you read the
      Q6 amendment as covering it.
- [x] Two identical `world new` invocations differ inside `.canon/` only, by a `created_at`
      wall-clock stamp in the registry. Emitted content is identical. Confirm that is acceptable,
      or say the stamp should be derived from the seed instead.



## E. On approval

- [x] Say "P0-10 approved".