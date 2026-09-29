# Triage — your hand-test findings, diagnosed and adversarially checked

**14 findings.** Each got an independent investigator, then a second agent whose only job was to
refute the first. Where the challenger overturned the investigator, the challenger's reading is what is
recorded here. 28 agents, read-only — nothing was changed while diagnosing.

`confirmed` = your report is right as stated. `partly-confirmed` = the symptom is real but the cause is
not what it looked like. **Read the partly-confirmed ones** — they change what gets fixed.


## Blocks the paid pass (4)

Spending money before these are fixed either wastes it or produces numbers you cannot read.

- **Pass 1b — the cost dashboard read all zeros** — partly-confirmed, effort small
- **A9 — "Edit steps" inert, and the create hang** — confirmed, effort medium
- **Portraits, agent name, undo, roll feedback, layout, back-nav** — partly-confirmed, effort large
- **P0-9 C10 — `dialogue improve` failed on a missing tree** — partly-confirmed, effort small

Everything else can wait until after pass 4.

---

## Pass 1b — the cost dashboard read all zeros

**partly-confirmed** · effort **small** · ⚠️ **blocks the paid pass**

**What is actually happening**

Three layers, only the first two of which the investigator reached.
SURFACE (confirmed). `/tmp/a3_pack` is a Sep 1 throwaway from
`docs/test_plans/P1-A3_read_tools.md:42`, built before A6 landed. Its `.canon/` holds `log.jsonl`
and nothing else — no `journal.jsonl`, no `objects/`, no `registry.json`. Today's `world new`
always writes all three (I verified on a fresh pack), so this is a pre-A6 tree, not an OS purge.
`all_events` returns `[]` for a missing journal (`src/canon/provenance.py:263-264`) and
`summarize_events` over `[]` yields exactly the zeros the user saw. `src/canon/cli/main.py:2673`
errors only when the PACK DIR is missing, never when the journal is, so a journal-less pack reads
as "the dashboard is empty" rather than "there is no journal here".
STRUCTURAL (confirmed, and it is the design). A pack straight out of `world new` cannot populate
the dashboard. Only `ops.py` and `godot_export.py` import the journal module inside
`src/canon/packs/platformer/`; `src/canon/pipeline/*` imports it nowhere. `world new`
(`cli/main.py:1095`, `:1105`) adds exactly three post-create events — world edit, manifest mirror,
registry create — and none carries a `gen` block, so none is costed.
`docs/September_Phase_0_prd.md:2216` states it plainly: "Not journaled today: the `world new`
create run (spend/jobs rows only)." And at $0 the metering is structurally floor-bound: no fake
backend defines `last_cost_accuracy`, so `backend_accuracy` (`provenance.py:413`) defaults to
MEASURED; `pricing.is_paid` (`pricing.py:452-455`) prices "fake / none / local" at $0 everywhere;
`FakeChatBackend` yields `Usage()` zeros (`backends/testing.py:636`, documented at `:529`), so
`token_gen_block` returns None (`provenance.py:496-497`) and no token row is ever written.
`estimated` and the tokens lane are unreachable for free.
THE LAYER UNDERNEATH (the investigator missed this). The test plan mis-files a paid-tier row into
a free pass. `P1-A6_journal_dashboard.md` is titled "the journal shape, **paid tier**, and cost
dashboard", and its section B names fal and PixelLab by name — yet
`README_consolidated_phase0.md:78-88` puts that section in Pass 1, which line 13 promises "need no
money". Section B was never runnable as scheduled, on any pack, at any time. Separately, `J8`
(`September_Phase_0_prd.md:2452`) was DECIDED on 2026-09-01 and is not built: the orchestrated
create journals no per-artifact `generate` events and no test asserts that it should.

**Fix**

Docs only. Keep the investigator's part 1, drop its part 2, add two corrections it missed.
1) BUILD THE PACK, AT A DURABLE PATH. In `docs/test_plans/P1-A6_journal_dashboard.md:29` and
`docs/test_plans/README_consolidated_phase0.md:81`, replace the bare `journal list /tmp/a3_pack
--summary` with a recipe that first creates the pack, and say plainly that a pack straight out of
`world new` has an empty dashboard by design. I ran this myself, ~15s, $0:
cd ~/Documents/projects/canon-ai     P=~/canon_demo_pack && rm -rf $P     uv run canon world new
$P --template platformer --seed 7 --name "Ember Demo"     uv run canon asset generate $P --target
enemy:ash_wraith --image-backend fake --actor cradle:user     uv run canon asset generate $P
--target enemy:cinder_beetle --image-backend fake --actor agent:mason/artist --session mason
uv run canon level place-enemies $P --level l1 --llm-backend fake --actor
agent:mason/level_designer --session mason     uv run canon asset generate $P --target
audio:ashen_depths --music-backend fake --actor cradle:user     uv run canon journal list $P
--summary | python3 -m json.tool
Seed 7 gives ash_wraith / cinder_beetle / slag_sentry / vent_skimmer and stage `ashen_depths` with
levels l1, l2. Output I reproduced exactly: `eventCount 15, costedEvents 4`, byKind `[audio,
image, text]`, byIdentity `[agent:mason/artist, agent:mason/level_designer, user]`, byConversation
`[mason]` — every table renders and B1's reconciliation is checkable, but every figure is $0 and
every row `measured`.
2) MOVE B2 AND B3 TO PASS 4, priced. Do NOT seed them. Add to Pass 4, roughly a dime total: `asset
generate --target enemy:<id> --image-backend fal` (~$0.039 → a 4c `estimated` image row), the same
against `--image-backend pixellab` (~$0.017 → a 2c `measured` row), and one real `--llm-backend
anthropic` agent turn for the `tokens` lane. Real verbs, real providers, no new code, no doctrine
bypass, and it actually tests what B2 claims to test.
3) CORRECT PASS 4'S OWN INSTRUCTION — this is the one that costs money if left.
`README_consolidated_phase0.md:170-174` tells the user to do a real paid generation run through
the wizard and "compare against `canon journal list --summary`". That command will report
`totalCents 0` no matter what was spent, because the create journals nothing costed. Point it
instead at `manifest.generation_stats.total_cost_usd` and `canon spend list` (the rows without
`journal_ref`) — which is exactly what cradle's own dashboard reads (`CostDashboard.tsx:64-88`),
and what `spend.py:45-48` defines as the total.
4) TWO ONE-LINERS, optional. Make `canon journal list` say "no journal at <path>" when
`journal_path(pack).is_file()` is False, beside the existing check at `cli/main.py:2673`. And fix
the false comment at `agent/tools_paid.py:665` ("the runner already journalled its per-step
money") — it contradicts `spend.py:33`.
5) SURFACE J8 TO THE USER as an unbuilt decided row rather than fixing it here. Building it is
separate work; note that `tests/treediff.py:39` excludes the whole `.canon/` directory, so making
the create journal would NOT break the platformer's byte-identity contract.

**Doctrine risk**

LOW as corrected — because dropping the seeder removes the only real risk the investigator
identified, and it was right to flag it.
The investigator's part 2 would have had a script call `provenance.record` directly to fabricate
dollars. That writes journal events outside a verb, which is precisely the bypass
`src/canon/provenance.py:11` forbids: "Everything cradle does to a pack flows through the canon
write verbs, and each verb records here, so coverage is complete from one choke point." The
investigator saw this and fenced it (scripts/ only, refuse on an existing journal, throwaway packs
only). Those fences are sound, but they are unnecessary once you notice the checks in question are
paid checks with a paid pass already scheduled. Fabricated money in an authoritative ledger is not
worth a fence when a dime of real money answers the same question better.
The corrected fix is docs plus at most two one-line code changes, so: - DATA-not-Literal:
untouched. Ids in the recipe (`ash_wraith`, `l1`, `ashen_depths`) are values read off disk from a
seeded create, not a hardcoded vocabulary, and `genKind` stays an open string throughout
(`cli/main.py:2649` documents it as "open"; `provenance.py:37` as "an OPEN string vocabulary,
never a Literal"). - Cradle never writes pack files: untouched. Every step is a `canon` verb with
`--actor`. - Byte-identity: untouched. Nothing goes near the platformer pack, and
`tests/treediff.py:39` excludes `.canon/` wholesale anyway. - Nothing regenerates or deletes on
its own: the recipe's `rm -rf $P` targets a demo path the user names, and it must stay in the doc
as an explicit user step, never wired into a verb.
One residual: if J8 is later built, it must journal into `.canon/` only (which treediff already
excludes) and must carry no cost, per its decided shape — journalling create cost would double-
count against the unreffed spend rows that `spend.py:45-48` says are added to the journal total.

**Needs your decision**

Two, and the first is the one I would not let slide.
1. J8 was decided on 2026-09-01 — "P0-10's orchestrated create journals per-artifact `generate`
events in the pre-A6 shape (no cost)" (`docs/September_Phase_0_prd.md:2452`) — and it is not
built. I created a pack and got three journal events, not one per artifact, and no test in
`tests/` asserts otherwise. Do you want it built before you sign off on stage 5, or recorded as a
known deferral and moved to Phase 1? It does not change the empty-dashboard symptom either way
(J8's events carry no cost), so this is a completeness call about the row, not a fix for the
finding.
2. Do you accept that B2 and B3 are paid checks and move them to Pass 4 at roughly a dime, or do
you want them tickable at $0? If the latter, the only route is fabricating rows, and I would
rather you know that a tick obtained that way tests `record()` and not fal.


---

## A9 — "Edit steps" inert, and the create hang

**confirmed** · effort **medium** · ⚠️ **blocks the paid pass**

**What is actually happening**

Two independent causes behind one report; both confirmed by reading the code.
(1) EDIT STEPS IS A DELIBERATE, INERT STUB ON THE START PAGE — not broken, not read-only.
PlanCard.tsx:136-144 renders the button `disabled={!!surface.planEditDisabledReason}` with the
reason carried only as `title=`; StartAgentPanel.tsx:90 always supplies PLAN_EDIT_DISABLED_REASON
(startConversation.ts:45-52). A green test locks this in (StartAgentPanel.test.tsx:523-540:
`edit.disabled === true`, card stays `proposed`). In the EDITOR it is real — PlanCard.tsx:168-199
opens the textarea, Re-propose POSTs `edit` (agentActions.ts:699-714) and canon replaces the steps
and approves in one go (canon runs.py:1615-1616, `_normalize_steps` at runs.py:510-533). The start
page has no service to POST to (startConversation.ts:15-36: a canon conversation requires an open
pack). Underneath that, the deeper fact: the plan's two steps (startConversation.ts:441-473) are
LABELS, not a script — the run reads `draft.params` (name/template/counts/backends, built at
startConversation.ts:379-385, consumed by beginCreate at startCreate.ts:122-168). Editing step
text would change nothing that runs. And the conversation has no surface for what does run: the
two clarifying questions (startConversation.ts:174-183) steer only `pickTemplate`, and
startConversation.ts:354 hard-sets `draft.counts = {...template.defaults}` on every proposal. So
the button is honest about being useless — but it is useless about the right thing.
(2) THE HANG IS REAL AND PERMANENT, AND CLOSEWORLD CAUSES IT. The create's UI state is module-
level and never reset by the app (startCreate.ts:63, and resetStartCreate/resetDrafts appear only
in StartAgentPanel.test.tsx:169-170). Exactly one thing settles it: CreateRunCard.tsx:58-60's
effect, and CreateRunCard mounts only on the start surface (AgentPanel.tsx:105, gated by
App.tsx:494 `world === null`). Opening any project unmounts it. Going Home is closeWorld
(TopBar.tsx:42-44), which sets `jobs: []` (store.ts:576) and `agent: INITIAL_AGENT` (store.ts:595)
— deleting the job record the settle needs and the conversation the plan card lived in — while
`startCreate.state.status` stays "creating" pointing at a job id that can never resolve. On
remount `if (job)` is false forever: CreateProgress.tsx:57,73-74,117,140-141 renders "Starting
canon…", "no output yet", "counting steps…", an indeterminate bar and a clock ticking from the
original startedAt; StartStatusBar.tsx:21-24 reads "creating <name> — starting" with the running
dot; LiveProjectCard.tsx:34-41 says "being created…". The ⏹ is a dead button —
agentActions.ts:846-849 `cancelJob` early-returns when the job is not in the store, silently. Only
an app restart clears it. The canon process itself was NOT killed: the job-updated listener is
app-level and mounted once (App.tsx:100-130) and the run lives on the Rust JobQueue worker (src-
tauri/src/lib.rs:1054-1128 `new_project` → `enqueue_watching`); the tree finishes on disk.
The lifecycle error is structural: handleJobEvent is documented as "the single place that
processes a background-job lifecycle event" (jobs.ts:39-44) but explicitly defers the create to
"the caller holding that path" (jobs.ts:86-91), and that caller is a view that can be unmounted.
COLLATERAL, and this is the expensive part: settleCreate (startCreate.ts:230-248) is the ONLY
writer of the created pack's spend and job ledgers, because handleJobEvent skips a job with no
targetType (jobs.ts:91) and the create is enqueued with `targetType: ""` (startCreate.ts:139-143).
canon does not journal the create's cost itself — jobs.ts:96-102 says so explicitly, and
lighthouse_keeper's .canon/journal.jsonl (12 rows) contains only world/manifest/registry
provenance edits, no cost row for the create. So leaving the start page mid-create means the
single most expensive operation in the app leaves NO money record anywhere.
No test covers unmount or closeWorld during a create (only unmount( in the repo is
DetailPane.test.tsx:85; closeWorld appears in store.test.ts:73 only).

**Fix**

HANG (do first): 1. Move the terminal fold to the app-level lifecycle that already owns it: call
`settleCreate` from handleJobEvent at the `if (!j.targetType) return;` branch
(src/lib/jobs.ts:91), whose comment already names the create as the one job it skips. Widening the
existing single place, not a second one. CreateRunCard.tsx:58-60 then becomes a pure view. 2.
REQUIRED, not optional: make closeWorld keep the in-flight create's job. Without it step 1 is dead
code for the reported sequence — handleJobEvent returns at jobs.ts:48-49 and handleJobProgress at
jobs.ts:186-187 because store.ts:576 already emptied `jobs`. store.ts cannot import startCreate.ts
(import cycle: startCreate.ts:2 imports useStore), so either keep the protected job id in the
store slice (startCreate sets it when it sets jobId) or filter on data (`!j.targetType &&
!terminal`). This is the piece that makes it small-to-medium rather than trivial. 3. Drive the
plan-step mirroring (CreateRunCard.tsx:64-77) from settleCreate too. Safe after closeWorld:
agentDispatch no-ops on an unknown conversation (store.ts:435-436), so a wiped conversation
swallows it rather than resurrecting a ghost card. 4. Residual guard: if `create.jobId` resolves
to nothing while status is "creating", CreateRunCard must say so instead of "Starting canon…"
forever, and its ⏹ must not be a silent no-op (agentActions.ts:846-849 returns without a word). 5.
Do NOT let closeWorld cancel or delete the run — nothing dies without asking. 6. Regression test
(none exists): render App on the start surface, run a create, load a world, closeWorld, re-render
— the run card must not read "creating", and the ledgers must be written.
EDIT STEPS: 7. Keep the shipped plan machinery. Add an `onEditPlan` / start-surface editor body
beside the existing `planEditDisabledReason` slot (panelSurface.ts:49-53) so PlanCard.tsx:136-144
stops rendering a dead button and flips the card to `editing` through the SAME `plan_editing`
event already reduced at agentState.ts:1095-1101. 8. The start page's editor edits what the run
actually reads — name, template, counts, backends on `draftFor(conversationId)`
(startConversation.ts:100-117) — never `tier · specialist · text`. Reuse the registry-driven
widgets that already exist: `splitCounts` / `rangeFor` / `countLabel` (packTemplates.ts:61-87) and
the `countRow` closure currently inlined at NewProjectModal.tsx:333-356 (extracting it means
lifting its `counts`/`setCounts`/`template`/`busy` closure — real work, not a move). 9. Re-propose
calls the existing `proposePlan` (startConversation.ts:331-402): it re-prices through `canon
estimate --template` and re-dispatches `plan_proposed`, so the button returns as `Create · up to
$X` and approving runs the real beginCreate → JobQueue → `canon world new`. No new run path, no
cradle-side writes. 10. Before that ships, fix startConversation.ts:354 `draft.counts = {
...template.defaults }` to fill-if-missing, exactly as :359-360 already does for backends —
otherwise every re-propose silently discards the user's edit. (Not a bug today; a certain one the
moment step 8 lands.) 11. Render whatever stays disabled with a VISIBLE reason, not only `title=`
(PlanCard.tsx:140) — a title on a disabled button is unreliable in browsers and invisible to AT,
so doctrine 4 is not currently satisfied.

**Doctrine risk**

Real, and the diagnosis named most of it correctly.
EDIT-STEPS HALF: (a) Porting the editor's `tier · specialist · text` textarea
(PlanCard.tsx:168-199) to the start page would be a parallel, fake surface — those steps are
labels and the run reads `draft.params`, so the edit must land on template/counts/backends or the
button lies a second time. (b) Re-propose and the create must stay canon verbs —
`api.estimateWorld` and beginCreate → `canon world new` on the JobQueue; cradle must not shortcut
a tree itself. (c) Ids/counts stay DATA: `loadPackTemplates()` / `splitCounts` / `rangeFor` /
`countLabel` (packTemplates.ts:61-87), never a literal list. This is currently clean on both sides
— canon's `_normalize_steps` (runs.py:517-519) takes `tier` as any non-empty string with no
Literal union, and packTemplates derives every field name from `t.defaults`. (d)
startConversation.ts:354 stomping draft.counts.
HANG HALF: the risk runs the other way — the fix must not make closing a project cancel, delete,
or silently discard a running create.
NOT A DOCTRINE RISK, contrary to how it looks: settleCreate's ledger writes are not cradle writing
pack files. recordSpend/recordJob (lib/cost.ts:31-48) go through Tauri commands that shell canon
verbs — `canon spend record` (src-tauri/src/lib.rs:2439-2443) and `canon jobs record`
(lib.rs:2458-2463). Moving the caller from CreateRunCard to handleJobEvent changes who calls, not
who writes. Byte-identity of the platformer pack is untouched by any of this.

**Needs your decision**

Two, and the first one matters for reproducing it.
1. How did you open the project while it was still creating? You could not have done it from the
panel — the live recents card is `disabled` until the create is done (LiveProjectCard.tsx:48) and
"Open it now" only appears on done (CreateRunCard.tsx:122-135). So it was the Open… dialog, the
recents rail, or the command palette — which one? And were you in the packaged app or `npm run
dev` in a browser (the dev-mock's create is a ~6s simulation, devMock.ts:2752-2755, and writes no
folder)?
2. Your ~/CradleProjects holds exactly two projects, dc2 and lighthouse_keeper, and BOTH have
complete .canon/jobs.jsonl + spend.jsonl — meaning both creates settled normally. If the create
you watched hang wrote a folder, it is gone. Did you delete it, or was that run in the browser
mock? (lighthouse_keeper's own ledger says the platformer create took 3.7 seconds end to end — so
the answer to "how long does the canned create take" is ~4 seconds, and anything past ~10 is a
stuck view, never a slow run.)
3. On "Real run, not a fake backend" — do you mean (a) the edited plan must drive the actual canon
create rather than a stub, or (b) the start page needs a way to pick PAID backends before
creating? Today the start page hard-seeds llm/image fake, music/sfx/vlm none
(startConversation.ts:74-80) with no control at all; "Start blank instead" is the only path to a
paid create.


---

## Portraits, agent name, undo, roll feedback, layout, back-nav

**partly-confirmed** · effort **large** · ⚠️ **blocks the paid pass** · *first diagnosis overturned*

**What is actually happening**

Six separate causes; the investigator's framing is right for four and wrong for the one that costs
money.
1. AGENT NAME — the "scattered" premise is REFUTED for the UI:
src/components/agent/agentLabel.ts:12 is the single definition, deriving the label from the
project title's last word (ASSUMPTION-14, comment at :10-11). world_bible.json story.title is "The
Silent Gospel" (fed via store.ts:837; platformer path store.ts:792) → "GOSPEL". Three consumers,
all through that function: Transcript.tsx:56, FirstRun.tsx:21-22, ValidationBar.tsx:29. But the
agent has no name in its own PROMPT (roster/core.md:3), so a rename is a two-place change.
Stragglers: canon evals.py:176/184 ("Wick"), App.tsx:283 palette keyword, Transcript.test.tsx:111
expects "WICK". Side note: ValidationBar.tsx:27-28 claims its `agent:gospel` segment is "the same
identity the job tray and History use" — it is not; real actors are agent:<conversation-
id>/<specialist> (lib/actor.ts:36) and conversation ids are local-N (agentActions.ts:232), so that
segment matches nothing in the journal.
2. UNDO — three unrelated undo machines, one key, two surfaces. (a) Buffer undo:
useDialogueEditor.ts:93-98 (cursor movement over an op list), bound to mod+Z at
DialogueSurface.tsx:452-456 AND SceneTab.tsx:149-152. (b) Version restore: DetailPane.tsx:219-233
History tab → LineagePanel.tsx:181-208 → api.restoreGridStep (invoke.ts:1096) → `canon grid
restore` (canon cli/main.py:1781) — click + confirm only. (c) Agent undo: PlanCard.tsx:268,
ChangeFeed.tsx:53, WriteCard.tsx:101 — click only. The global handler App.tsx:136-209 binds
K/⇧A/⇧N/Esc/O/N/./B/I, no Z; LevelDetail.tsx:958-986 binds mod+S, Esc, Delete and tool letters, no
Z; no palette command anywhere contains "undo" (registrations at App.tsx:254, LevelDetail.tsx:891,
DialogueSurface.tsx:613, WorldMapView.tsx:585). The honest answer to the user's question: ⌘Z works
only while editing dialogue or a scene and only before you save; everywhere else undo is a click
in the History tab — their own journal proves they found it
(bibles/mazeworld_scifi/.canon/journal.jsonl ends with op "restore", detail.kind "room_restore").
The deeper cause is that the room/grid editor has no undoable op-stack at all — its edits are a
Set of dirty layers (LevelDetail.tsx:273-274), so there is nothing for ⌘Z to step through.
3. ROLL FEEDBACK — confirmed at LevelDetail.tsx:626-629: `${step} rolled — ${result.changed ?
"updated ✓" : "no change"}` plus warnings[0]. RollResult (invoke.ts:47-60) already carries seed,
changed_artifacts, updated, encounter_id, monster_ids; canon fills them (rolls.py:390-400, :427,
:472-475). The placed COUNT is computed one line earlier and dropped into the journal instead of
the return dict (rolls.py:452 `before`, :461 `after`, :466-467 detail placed). The user's journal
shows exactly the numbers the message could have said: item_roll placed 19, npc_roll placed 16.
Same generic shape at EntityOverview.tsx:1042.
4. LAYOUT INSTABILITY — confirmed. LevelDetail.tsx:1061-1069 opens ONE flex/flexWrap row holding
chips AND buttons, split only by `<span style={{flex:1}}/>` at :1138. Conditional, variable-width
chips: revision+last-change (:1071-1101), draft (:1103), parent room (:1104), the three counts
that change after every roll (:1105-1107), warnings (:1108-1110), `unsaved: …` (:1111), "saved ✓"
(:1112), save error truncated to 90 chars (:1113-1122), validation (:1123-1136),
playNote.slice(0,70) (:1137). One roll flips at least four of those, moving the wrap point under
the 🎲 buttons at :1179-1204. Same pattern in EntityTable.tsx:487-548 with App.css:1614-1622.
5. MISSING PORTRAITS — TWO distinct causes, and the important one is in CANON, not cradle.  (i)
Canon-generated dungeon packs never record the portrait path. AssetPhase writes the PNGs
(asset.py:180-186) but passes on_success_attr=None for every entity portrait (asset.py:217) and,
for characters/classes, stamps an in-memory attribute (asset.py:203/223) after compose.py:332 has
already written the JSON collections; compose.py:336 places AssetPhase after them and nothing re-
writes them. Result in dc2: 6/6 npcs, 9/9 items, 6/6 monsters, 4/4 classes have profile_image /
portrait_path null with the matching PNG on disk. EntityTable.tsx:229-238 portraitHintFor returns
null → Portrait.tsx:44 renders "no image" without ever calling resolveAsset. Backend-independent:
it fails identically with a paid image backend. Asymmetric with the platformer, which DOES stamp
(art_phases.py:503/556/588, platformer_write.py:1012/1270) — a gap in the dungeon path, not a
design.  (ii) On the legacy mazeworld_scifi pack the hints resolve (I re-implemented
data.rs:681-702 against the real files: 79/79 npcs, 30/30 monsters, 91/95 items, 4/4 classes) and
the asset scope is fine. There, the reason the user saw nothing is that the surfaces they were on
mount no image at all: RoomContents.tsx:96/110/126/141 and EntityOverview.tsx:604-638
ShopInventoryTable (the merchant inventory — and only mazeworld_scifi has merchants: 9 npcs with
shop_inventory, e.g. "Vex the Quartermaster") plus the LootTable beside it. Portrait is mounted
only at EntityTable.tsx:387, EntityTable.tsx:650+ (card grid), EntityOverview.tsx:335 and :581.  A
scope refusal would be silent here (tauri protocol/asset.rs:46-49 logs via log::error! with no log
plugin installed), so a 403 and a null hint look identical on screen.
6. BACK NAVIGATION — confirmed, no history stack. store.ts:35-41 Selection is one tagged value;
store.ts:491 `select: (s) => set({ selection: s })` overwrites with no record.
EntityLink.tsx:57-60 is the drill (used by ShopInventoryTable at EntityOverview.tsx:621,
LootTable, RoomContents), each hop destroying the previous selection. The nearest thing is
App.tsx:459-464: ArrowLeft on an entity goes UP to that entity's type list, so from the merchant's
item it lands on "items", not the merchant. The "<" is TopBar.tsx:29-40, the leftmost control,
rendering g-chev-l (start/Icons.tsx:30-32, `M10 3l-5 5 5 5`), bound to mod+B (App.tsx:195-200) —
the only sidebar-collapse affordance.

**Fix**

Ordered by what actually blocks the paid pass.
5 FIRST (canon, then cradle). In canon, make AssetPhase record what it produced: pass a real (obj,
attr) for entity portraits at asset.py:217 instead of None, and add a small post-asset write-back
in the dungeon compose list (compose.py:336-337) that re-writes npcs/items/monsters/classes with
the produced path — the parsers' own comments ("# AssetPhase fills later") say this was the
intended contract, so this finishes existing machinery rather than adding one. AssetPhase is
imported ONLY by the dungeon pack (compose.py:21, run_world.py:48), so the platformer pack cannot
move a byte. Stamp the path the phase actually wrote (asset.py:202/214/216/222) so the file-naming
convention stays in one place and cradle never guesses it. Also fill manifest
start/player/gameover/victory portraits, which dc2 leaves "" while claiming portraits_generated:
true. For packs already on disk, add a repair/backfill verb (canon has repair verbs already)
rather than regenerating — regenerating spends money and "nothing regenerates on its own". Only
AFTER that is it worth mounting <Portrait size={28}> in ShopInventoryTable / LootTable /
RoomContents rows.
3 (small). Canon: have _roll_kind/_roll_layout/_roll_whole return the counts they already compute
(rolls.py:452/461, :494/506) alongside `updated` — additive keys on a returned dict, dungeon-only
(adapters/__init__.py:76 registers a roller for "room" and deliberately none for "platformer").
Cradle: rewrite LevelDetail.tsx:626-629 to build from whatever keys are present ("npcs rolled — 16
placed (was 19) · seed 5655da3a"), falling back to today's sentence when they are absent, so an
older canon still works and nothing switches on a fixed list of steps.
4 (small). Split LevelDetail.tsx:1061-1069 into two rows inside the same container — a fixed-order
chip row and a button row — and give the transient slot (playNote / save status / validation) one
fixed-min-width cell instead of appending siblings. Same two-row split for EntityTable.tsx:487
(App.css:1614-1622). CSS + JSX only.
6 (small). Add a bounded selection history beside `selection` (store.ts:117/491) — push the
previous Selection inside `select`, cap it, expose back(). Note store.ts:475/571/794/817 also set
selection directly (open/close world); those should RESET the stack, not push. Bind it to the
existing global handler (App.tsx:136-209) and register a palette command so ⌘K finds it.
Separately swap TopBar.tsx:38's chevron — but g-panel (start/Icons.tsx:23-26) draws its divider at
x=10, i.e. a RIGHT-hand panel, which is why the notes button at TopBar.tsx:114 uses it; a left-nav
toggle needs the mirrored glyph, so add a `g-panel-l` to the same sheet rather than reusing
g-panel as-is.
1 (trivial; 2 files + 1 test). Replace agentLabel.ts:12's body with a name that comes from data —
read an optional `agent_name` off the manifest store.ts already loads at 840-855, falling back to
a constant in that same module — keeping the exported signature so
Transcript/FirstRun/ValidationBar are untouched. Then put the SAME name in the agent's own prompt:
roster/core.md:3 ("You are the cradle agent") is where the model learns who it is; leaving it
means the UI says Demi and the agent says "the cradle agent". Sweep App.tsx:283's "wick" keyword,
Transcript.test.tsx:111, and canon evals.py:176/184.
2 (medium, and scope it honestly). Do NOT add a fourth undo, and do NOT wire ⌘Z to
restoreGridStep. Extend the existing command registry (store.ts:179-181/499-505; Command carries a
`run` at store.ts:64-75) with a per-surface `*.undo` and dispatch mod+Z to it from App.tsx's
existing handler: dialogue/scene → the editor.undo() that already exists; agent → the existing
undoPlan/undoWrite. For the room/level surface there is nothing to dispatch to today, so register
a "Restore a previous version…" command that opens the History tab (keeping LineagePanel's
confirm) rather than pretending ⌘Z means undo there. The palette entries alone answer the user's
literal question today. A true per-edit undo in the grid editor is its own ticket: it needs
LevelDetail's paints to become an op list like useDialogueEditor's, which is real work and should
not be smuggled into this fix.

**Doctrine risk**

(a) Portraits — the fix MUST land in canon, not cradle. Deriving "npc_<id>.png" / "mon_<slug>.png"
in portraitHintFor would put canon's file-naming convention into the editor (ids/paths as guessed
code, not data); worse, any hint cradle wrote back would violate "cradle never writes pack files
directly". Stamp the path canon actually produced, from inside canon. AssetPhase is dungeon-only
(compose.py:21, run_world.py:48), so the platformer pack stays byte-identical. Backfill existing
packs with a repair verb; do not regenerate (money, and "nothing regenerates on its own"). (b)
Agent name — a hardcoded string is the fast fix but names are DATA; prefer an optional manifest
field with the module constant as fallback, keep agentLabel.ts the single module, and never inline
the name at a call site. Whatever you choose, roster/core.md must agree or the product carries two
names. (c) Undo — the tempting fix (a global undo stack) is a parallel system beside three
existing ones; dispatch through the existing command registry instead. Binding ⌘Z to
restoreGridStep would turn a keystroke into a canon write with no confirm, breaking "nothing is
deleted or changed without asking" (LineagePanel.tsx:185-194 confirms today) and corrupting the
unsaved paint buffer. (d) Roll message — keep the cradle-side formatting tolerant of missing keys
rather than switching on a fixed list of steps; the roll buttons are already data-driven off
`tabs` (LevelDetail.tsx:1179-1187) and must stay that way. No fix here touches a pack file outside
a canon verb.

**Needs your decision**

Which project were you in when the portraits were blank — CradleProjects/dc2, or
bibles/mazeworld_scifi? It decides the fix. dc2 is provably broken at the source (canon wrote the
PNGs but left profile_image null on every row, so the editor has nothing to load, and a PAID art
run would fail the same way). mazeworld_scifi's paths all resolve, so if it was blank THERE,
something is failing at runtime and I need one `npm run tauri dev` against it — Portrait.tsx:30-31
already logs [cradle:asset:Portrait] in DEV and will say in one line whether resolve returned a
path. Second, smaller: are you launching the dev build, or /Applications/Cradle.app? That bundle
is from Apr 27 and predates most of this work.


---

## P0-8 B1 — dungeon rows cannot be edited

**confirmed** · effort **large**

**What is actually happening**

The edit affordance was never widened with the editor. P0-8 made RowEditor schema-driven and made
EntityTable's CREATE gate registry-driven, but the third gate — the one deciding whether an edit
control renders at all — is not a "kind list" by name. It is a presentation condition on a paid
asset toolbar: src/components/EntityOverview.tsx:351 `{(typeId === "enemies" || typeId ===
"player" || (isItem && "item_id" in data)) && <GenActions …/>}`, unchanged since the platformer
era. The app's only "✎ Edit row" button lives inside GenActions (EntityOverview.tsx:1162), so "who
may edit" was accidentally bolted to "who sees the sprite/animate/publish buttons".
Dungeon items miss the gate because `isItem` is `typeId === "items"` (:126) and their id_field is
`id`, not `item_id` (dungeon spec.py:129) — I confirmed items/items.json rows carry only
category/name/desc/room_level/item_stats/profile_image. And four of the nine kinds never reach
that header at all: rooms render RoomHero instead of the header (:328) and route to LevelDetail
(DetailPane.tsx:78); quests route to QuestDetail (:80), which never mounts RowEditor; music/sfx
early-return at EntityOverview.tsx:131. So "put the button back in that header" is not a fix.
Underneath sits a placement error: a free, code-only, registry-driven row action lives inside a
component whose stated purpose (EntityOverview.tsx:994-995) is paid generation for one pack's
actors. The unit suite cannot see it because RowEditor.test.tsx renders the editor directly — the
component is proven, the path to it is not.
Two further defects sit on the same B1 row and are independent of the button: • classes —
data.rs:481-484 and :755-756 key array rows on the literal "id" with an ARRAY-INDEX fallback;
classes.json is array_positional with no "id" (I checked: keys are archetype/name/…, archetypes
warrior/mage/healer/jester), so cradle's id is "0" while canon matches
`str(row.get(entity.id_field))` (db_ops.py:270-272). • music/sfx — data.rs:585-598 and :737-744
short-circuit on `is_audio_type` BEFORE the collection read, synthesizing rows from mp3 stems
(:84-90). `canon pack info` reports music count 0 and sfx count 0 on this world while cradle lists
10 and 23. There is no music/music.json or sfx/sfx.json anywhere in the tree (canon's own
rows.py:37-38 says "no tree carries music/music.json yet"), so `db update --type music --id
combat` has nothing to resolve. B1's music/sfx leg is currently untestable, not merely
unclickable.

**Fix**

PIECE 1 — the entry point (small, and the only piece needed to stop the finding).
a) /Users/wolfgangblack/Documents/projects/cradle/src/lib/placements.ts — add
`editableKinds(info)` beside `creatableKinds` (:56-59): every key of `info.entities`, with NO grid
exclusion (a grid owns room CREATION; the room's row still edits through `db update --type room`,
which the P0-8 doc itself records as a Stage-6 fix). Mirror EntityTable's no-canon fallback
(EntityTable.tsx:320-322) so a null pack_info does not silently remove the button.
b) /Users/wolfgangblack/Documents/projects/cradle/src/components/DetailPane.tsx — mount the ✎
control in the `selection.kind === "entity"` branch (:312-318), above the Tabs. Every entity
selection passes through here whichever detail component renders (LevelDetail :78, QuestDetail
:80, EntityOverview :85/:100), and it already holds the get_entity payload (:53) and imports
typeIdForKind. Gate on `editableKinds(world?.pack_info).has(kindForTypeId(world?.pack_info,
selection.typeId))` — data, never a typeId list. Pass `editId={String(payload[idField] ??
selection.id)}` rather than raw selection.id, so classes save correctly today (idField from the
same pack_info).
c) /Users/wolfgangblack/Documents/projects/cradle/src/components/EntityOverview.tsx — delete the
now-duplicate edit bits from GenActions: button :1161-1164, `editing` state :1017, edit-mode mount
:1382-1390. Leave GenActions and its :351 gate alone; sprite/animate/publish are genuinely
platformer asset plumbing. Verify the platformer is not regressed: platformer_file_db_refs
(data.rs:315-344) uses the file stem as the id, which equals enemy_id/item_id, so selection.id
matches what GenActions was passing.
PIECE 2 — making B1 actually pass all nine (large, src-tauri). Cache the `canon pack info`
document lib.rs:553 already fetches into AppState, hand entities[kind].id_field down to
LocalFsDataSource, and then:   • key array rows on that id_field instead of literal "id"
(data.rs:481-484, :755-756) — fixes classes AND the `class:0` History target at
DetailPane.tsx:214;   • fall back to mp3 stems only when the collection file is ABSENT
(data.rs:585, :737), so a created music row is listed and editable;   • read the room's
`row_source` mirror (world_bible.json → rooms[id]) when rooms/rooms.json is missing, so a legacy-
tree room presents its real row rather than maze.json (data.rs:775-789);   • drive ENTITY_TYPES
(data.rs:56-58) from the cached document instead of the hardcoded nine.
Do PIECE 1 first and re-run B1: it should turn 0/9 into 5/9 clean (npcs, monsters, items, quests,
events), rooms partial, classes fixed by (b)'s idField derivation, music/sfx still blocked until
PIECE 2.

**Doctrine risk**

The recommended fix is doctrine-clean: every mutation still goes through `canon db update --actor
USER_ACTOR` (lib.rs:2940-2956), cradle writes no pack file, no parallel system appears, nothing
regenerates, nothing is deleted. `editableKinds` reads `pack_info.entities` — data.
The trap to refuse: extending EntityOverview.tsx:351 to `typeId === "npcs" || typeId ===
"monsters" || …`. That plants a SECOND hardcoded kind list in the exact surface P0-8 existed to
free of them, and it still misses rooms, quests, music and sfx because those four never reach that
header.
One live risk in my own recommendation: `editId = String(payload[idField] ?? selection.id)` reads
correctly today only because dungeon item rows carry no `id` key (their id is the object key) and
class rows carry `archetype`. It papers over data.rs's index ids rather than fixing them, and the
`class:0` History target stays wrong until PIECE 2 lands. Do not let PIECE 1 shipping close the
row.
Pre-existing violations found while verifying, not introduced by this fix: src-
tauri/src/data.rs:56-58 ENTITY_TYPES is a hardcoded nine-kind list; src/lib/dbNesting.ts is a
hardcoded per-kind nesting map whose "item" key collides between packs — harmless today, and I
verified why: splitRow (RowEditor.tsx:128) only uses a bare name when `nesting[nk] === k`, and the
platformer's values ("stats"/"params") never equal the dungeon's container "item_stats", so
dungeon items always travel as `item_stats.<key>`, which canon accepts because the kind declares
`containers: ["item_stats"]` (dungeon spec.py:146, checked against db_ops.py:982-988).

**Needs your decision**

B1 asks you to edit nine kinds on `bibles/mazeworld_scifi`, but two of them have zero rows to edit
there: `canon pack info` reports music count 0 and sfx count 0, and there is no music/music.json
or sfx/sfx.json anywhere in that tree — cradle's table is showing 10 and 23 synthesized mp3 stems,
which canon has never heard of. Which do you want?   (1) Re-scope B1's audio leg to "create a
music row with ＋ new row, then edit it" — free, but it needs the data.rs audio short-circuit
removed first or the new row will not appear in the list.   (2) Treat the mp3-stem table as the
defect and make cradle's audio surface read music/music.json when it exists, accepting that on
this legacy world both kinds show empty until the audio verbs have run.   (3) Drop music/sfx from
B1 and gate them behind the paid audio pass instead. Related, smaller: do you want PIECE 1 shipped
on its own first so you can re-run B1 and get 7 of 9, or held until the Rust readers are fixed so
the row closes in one pass?


---

## P0-8 B4 — monsters / encounters cannot be placed

**partly-confirmed** · effort **medium** · *first diagnosis overturned*

**What is actually happening**

Category (c)+(d): the create half is built and works; the JOIN half is built but under-validated,
and neither half produces a visible result.
The encounter write was designed as a palette gesture (arm a monster, click a cell) whose entire
outcome is invisible on the surface the user is looking at. Three layers compound:
(1) NO RESULT FEEDBACK — the real defect. drawLevel.ts:696-715 draws combat, puzzle and plain
events as one identical purple diamond (only `is_gate` varies). Joining an encounter changes
nothing on screen. Creating one adds a diamond indistinguishable from the 96 already there.
Nothing renders a monster count, and drawLevel has no monster vocabulary at all. Success and
failure look the same.
(2) NO TYPE GUARD ON THE JOIN — a correctness bug. canon hardcodes `type: "combat"` when it
CREATES (dungeon_write.py:843) but never checks type when it JOINS (:771-774 checks existence
only; :851-857 writes regardless). 66% of room_0's diamonds are non-combat. Drop a monster on one
and canon writes `monster_ids` onto a puzzle row, journals it as a clean edit, and cradle reports
success — for a monster that will never spawn. The fail-closed pre-pass exhaustively validates
geometry, walls, reserved cells, occupancy and monster refs, then omits the one check that makes
the row an encounter.
(3) NAMING — three surfaces on the room screen are called "monsters", and the one the user meets
first refuses. The header roll button (LevelDetail.tsx:1188-1197) says "🎲 Monsters" and, with
nothing selected, "select an encounter on the canvas first". RoomFacts (:1520-1538) fills the
default tray with a read-only "monsters" row. The Dock tab that actually works is the third. The
codebase's own test had to disambiguate by CSS class (LevelDetail.test.tsx:207-210: "The dock's
Monsters TAB (the header's 🎲 Monsters is the roll button)"). Separately, the one sentence that
teaches the whole model (Dock.tsx:571) sits in a `tray ?? fallback` that LevelDetail.tsx:1423-1431
always fills for a room, so it can never render — real, verified, but a secondary contributor.
The user's first model ("place a combat zone, then add monsters") is genuinely not built: every
one of the pack's 481 event rows is already placed (verified: 0 unplaced, 0 combat with an empty
roster), so no Events swatch ever creates a new zone.

**Fix**

Mostly cradle-side, but canon needs one guard — the diagnosis's "no canon work is needed" is
wrong.
0. CANON, FIRST (fixes the silent corruption). In `_check_encounters` (dungeon_write.py:771-774),
when `event_id` is not None, refuse a target whose row type is not the combat type — same fail-
closed pre-pass, same style as the neighbouring refusals. Do NOT add a second `"combat"` literal:
read the type the create branch uses from one shared constant, or better, lift it onto the dungeon
spec's event block (packs/dungeon/spec.py:185-212) as data and have :843 read it too, so there is
exactly one source. Add the mirror test beside the existing 11 in `-k encounter`.
1. MAKE THE RESULT VISIBLE (the actual defect). In drawLevel.ts:696-715, distinguish a combat
encounter from a puzzle/plain event, and badge a combat row whose `monster_ids` is empty. Both
facts are already on the wire — dungeon_read.py:435-441 exports `monster_ids` in every trigger's
params, and the row type is `t.type`. Give a populated encounter a monster count. This is what
turns the gesture from invisible into obvious, and it also answers the user's "only viable when
monsters are added" without a refusal.
2. FIX THE NAMES. Rename the header roll button (LevelDetail.tsx:1188-1197) to something that
cannot collide — "🎲 Reroll roster". Label the Dock tab for the gesture, deriving the word from
`pack info`'s entity label (already available as `entities.monster.label` = "Monsters"), never a
literal.
3. MOVE THE DEAD COPY. Take the room sentence out of Dock.tsx:566-576's unreachable `tray ??
(...)` fallback into RoomFacts (LevelDetail.tsx:1492-1538) — the component that actually occupies
a room's tray — or into the Monsters strip header.
4. ADD THE MISSING FIRST HALF, SAFELY. One extra swatch on the Monsters strip (Dock.tsx:437-455)
arming `{kind:"encounter"}`, routed through `onPlace` (LevelDetail.tsx:500) to a write of
`{encounters:[{x, y, event_id: null, monster_ids: []}]}`. Canon already accepts this
(`_check_encounters` :765 coerces a missing roster to `[]`; `_apply_encounters` :843 creates the
row). CRITICAL, and missing from the original sketch: it must NOT reuse dropMonster's "find the
trigger at this cell and pass its event_id" logic — that would send `event_id: <existing>,
monster_ids: []` and blank a live roster. Force `event_id: null`, and refuse (or confirm) when a
trigger already occupies the cell.
5. ROSTER EDITOR ON THE SELECTED ENCOUNTER. Extend the Inspector's trigger branch
(LevelDetail.tsx:1723-1732) from read-only links to add/remove, fed by the `roomDb[monsterTypeId]`
LevelDetail already fetches (:173-208). Both directions call the same encounters write —
`set_encounter_monsters` replaces wholesale, so a shorter list is already a legal remove. While in
there, replace the hardcoded `typeId="monsters"` (:1727) and `"events"` (:1735) with
`monsterTypeId` and the event tab's `typeId`; same for RoomFacts:1501 and :1529. Copy must say
"remove from this encounter", not "delete".
Ticket separately, not in scope: src-tauri/src/lib.rs:709/750/779/807 pass the world root where
:685 passes `data_root` (data.rs:416-423) — inconsistent either way, latent for mazeworld_scifi.
And PaletteRail.tsx is 267 lines of never-rendered dead code.

**Doctrine risk**

Three tensions, one newly raised by my findings.
1. "Ids/kinds are DATA" — the fix must not add literals, and should retire some. `"combat"` is
already hardcoded at dungeon_write.py:843; my new canon guard must read the SAME value, not add a
second copy — best lifted onto packs/dungeon/spec.py's event block as data. Cradle-side,
`typeId="monsters"` (LevelDetail.tsx:1727, :1529) and `"events"` (:1501, :1735) are existing
violations sitting in exactly the code the fix extends; extending them propagates the violation.
The new swatch's event kind must keep coming from the `tabs.find(t => t.kind === "event")` gate at
:243-246. Note the semantic kind strings "monster" and "event" are already hardcoded on BOTH sides
(dungeon_write.py:457, :737-738; LevelDetail.tsx:243) — a pre-existing decision the fix inherits
rather than creates.
2. "Nothing is deleted without asking" — sharper than the original diagnosis allowed. This is not
just a copy problem on the remove affordance; the original fix sketch's combat-zone swatch would
have SILENTLY BLANKED a live encounter's roster by reusing dropMonster's event_id lookup with an
empty list. Any path that sends `monster_ids: []` to an existing `event_id` is a destructive
wholesale replace and must be structurally prevented, not just labelled.
3. "cradle never writes pack files directly" — intact and verified. Every path goes through
lib.rs:707-728 shelling `canon level apply-edit ... --actor cradle:user`; there is no parallel
writer. The fix adds no new write path.
Platformer byte-identity is safe: every change sits behind `room` / `fromPack` branches a
platformer level never enters, and the canon guard fires only inside the `encounters` cross-file
branch (dungeon_write.py:402, :458), which the platformer never sets.

**Needs your decision**

Two, and the first changes what gets built.
1. When you drop a monster onto a cell that already holds a NON-combat event (a puzzle or a plain
event — 63 of room_0's 96 diamonds), what should happen? Today canon silently writes monster_ids
onto that puzzle row and reports success, and the engine will ignore it. Options: (a) refuse with
a reason ("event 3035 is a puzzle, not an encounter"); (b) create a new combat event on that cell
alongside it; (c) convert the row to combat, asking first. I recommend (a) — it matches every
other refusal in the pre-pass — but it is your call, and it is the difference between a one-line
guard and a new gesture.
2. Should an empty encounter be a legal saved state? Your words were "it only becomes viable when
monsters are added to it", which reads two ways: a badge that says "empty — not viable yet"
(cheap, honest, keeps the combat-zone-first workflow you asked for), or a hard refusal in canon
that rejects `monster_ids: []`. The refusal would kill the "place a zone, then fill it" half of
your own model, so I have sketched the badge — confirm that is what you meant.


---

## History and restore — hash does not revert, no branching

**partly-confirmed** · effort **large**

**What is actually happening**

Three causes, none in the restore writer. The write path is provably correct:
rooms/room_0/maze.json hashes to 599206b8 with 7 grid cells still differing from the 37317e93
baseline (both terrain paints kept) and all 8 placement keys rewound. NOT a regression of the P0-8
fix.
(1) PRESENTATION DROPS WHAT CANON ALREADY RETURNS. `canon grid restore` answers {restored_to,
before_hash, after_hash, changed, no_change, warnings} (dungeon_write.py:1337-1347), emitted at
cli/main.py:1797 and forwarded verbatim as a Value by lib.rs:810-830. cradle types it
`invoke<unknown>` (invoke.ts:1096) and bare-awaits it (LineagePanel.tsx:198-199). The disturbance
warnings go with it. So the confirm promises "Restore this data version (37317e93) as current?"
(:187) and every surface afterwards reads 599206b8 — because this pack has no rooms.json index
row, so dungeon_read.py:178-187 makes the room's revision chip literally the CAS hash
(export_room_bundle returns revision_short "599206b81f", last_change label "Restored"). That is
S1. It is NOT staleness: the panel genuinely refetches (verified: DetailPane unmounts Tabs while
loading; LevelDetail holds no store cache, it refetches via api.exportLevel at
LevelDetail.tsx:348).
(2) THE LINEAGE GRAPH IS KEYED ON CONTENT AND LAID OUT BY LONGEST PATH. platformer_read.py builds
one node per distinct hash, stamps op/actor/ts from the FIRST producer (:953, first-producer-
wins), and computes depth as longest path over structural edges only (:1029-1044), with
LineagePanel.tsx:176 placing cards at x = depth * COL_W. A restore that reproduces bytes already
in the CAS adds no node, relabels nothing, and moves "current" backwards into the middle of a
graph whose right-hand columns are an abandoned line nothing marks. Reproduced exactly against the
real pack: 7 nodes, 8 edges, requested 599206b8 at depth 2 of max_depth 6, root 37317e93, edges
37317e93->599206b8 op=restore and 9362ff90->599206b8 kind=room_restore:replaced. That is S2 and
most of S3. Branching is NOT missing from canon's model — it just cannot exist for a restore whose
result already lives in the CAS, which is the NORMAL case for every whole-file asset restore
(after == detail.to, so :1017 suppresses even the restore edge).
(3) FOR A ROOM, THE UNIT OF VERSIONING IS THE FILE WHILE THE UNIT OF IDENTITY AND RESTORE IS THE
STEP. One maze.json, one CAS chain, two artifact ids. This is the deeper cause under the "tree
doesn't work" complaint: both step tabs return byte-identical trees (verified — same 7 nodes, 8
edges, requested, root, current_of), the current card reads "current · data, data"
(LineagePanel.tsx:373-377, because _facet_for at :826-848 has no room branch), and — the live
hazard — the restore button behind an identical-looking card does a DIFFERENT thing per tab, since
gridStepOf (LineagePanel.tsx:56-67) resolves the step from `artifactId` first. A user on the grid
tab clicking the card they were just looking at on the placements tab rewinds terrain instead of
placements. Only the confirm body discloses it (restoreScope, :96-105).
S4 is a plain missing affordance: ActionGateOpts = {title, body?, confirmLabel?}
(confirmGateState.ts:38) and the action card renders title + body + two buttons
(ConfirmGate.tsx:36-51). Independently, a room version has no renderable facet — PNG_FACETS =
{sprite,tilesheet,band} (:25) excludes "data", so no bytes are ever fetched and the card shows a 📄
glyph (:364).

**Fix**

Doctrine-safe; no new write verb (cradle still mutates only through `canon grid restore --actor`,
lib.rs:810-830). Corrected from the original sketch.
MEDIUM CORE (do these): 1. Stop discarding the verb's answer. Type restoreGridStep
(invoke.ts:1096) and assetRestore (:1572) with the dict dungeon_write.py:1337-1347 already
returns. Then surface it — but NOT in LineagePanel's `note`, which the post-restore remount
destroys. Put the message where it survives: a new editor-level notice field in the store
(extending the store, not a parallel system), or drop the blanket select() in favour of refreshing
the tree in place plus a targeted bundle refresh. Copy: "restored placements to 37317e93 — the
document is now 599206b8 because the grid edits you kept are still in the file", plus the
swallowed `warnings` and the `no_change` case. Fixes S1. 2. Correct the confirm copy at
LineagePanel.tsx:189-190: it promises "this becomes a new branch", which is false whenever the
restored bytes already exist. Say what will actually happen. (restoreScope at :96-105 already
states the step scope correctly — keep it.) 3. Mark the abandoned line, with the right rule. NOT
"unreachable from current" — that dims the trunk. Mark the nodes reachable FORWARD from current
along structural edges that feed a ":replaced" edge back into current: those are the superseded
ones. Purely in LineagePanel.tsx:169-179 + the card style; no canon edit. Fixes S2. (Optionally
have asset_lineage emit an ordering hint beside `depth` at platformer_read.py:1044 so current is
not stranded mid-row — additive only.) 4. Give a re-produced node the restore's identity.
platformer_read.py:953's first-producer-wins guard is what erases the restore. Extend the node
with an additive `produced_by` list (op/actor/ts per event) rather than overwriting `op`/`ts` —
the platformer's own History tab reads this payload, so redefinition would shift that surface.
Card shows the LATEST producer when a node was re-produced. Fixes the "nothing says a restore
happened" half of S3. 5. Preview slot, PNG facets only, in the existing gate: widen ActionGateOpts
(confirmGateState.ts:38) with an optional preview node and render it in the existing action card
(ConfirmGate.tsx:36-51) — the SpendGate's chassis, not a second dialog. Feed it from the already-
fetched `thumbs`. Fixes S4 for sprites/tilesheets/bands.
LARGER, CARVE OUT SEPARATELY: 6. Room/data preview. Needs a canon read path that projects an
arbitrary CAS snapshot through the room export (export_room_bundle, dungeon_read.py:236, takes
only room_id today). Doing the projection in cradle instead would re-implement canon's room
projection in the UI — forbidden. Until that exists, say "no preview for this version type" rather
than shipping a blank card. 7. Distinguish the two step chains. On canon: derive the facet from
the `<kind>:<id>/<step>` segment generically in _facet_for (platformer_read.py:826-848) — do NOT
add "room" to _PRIMARY_FACETS (:815). On cradle: add `steps` (and `restorable`) to the pack-info
grids block (packs/__init__.py:353-361) and to PackGrid (invoke.ts:14-18), then have
DetailPane.tsx:225 pass real steps so LineagePanel.tsx:608's hardcoded ["placements","grid"] can
go. Optionally scope the tree to the requested artifact's own chain so the step toggle does
something. Until then, at minimum make the step visible ON the cards, because the same-looking
card restores different things per tab.

**Doctrine risk**

Three live tripwires, one already tripped.
(a) ALREADY TRIPPED — ids/kinds/steps are DATA. RoomHistory's `steps = ["placements","grid"]`
(LineagePanel.tsx:608) is a hardcoded step vocabulary, and DetailPane.tsx:225 renders RoomHistory
without passing `steps`, so the hardcoded default is what ships. canon has the data
(GridKind.steps / .restorable, packs/spec.py:145,153) but pack info does not export it
(packs/__init__.py:353-361) and cradle's PackGrid does not type it (invoke.ts:14-18). Fixing the
"data, data" facet is tempting to do by adding "room" to _PRIMARY_FACETS (platformer_read.py:815)
or hardcoding step names in cradle — both violate the rule. Derive from the artifact-id step
segment, and export `steps` so the UI stops guessing. PNG-vs-JSON routing (PNG_FACETS,
LineagePanel.tsx:25) is another hardcoded set a preview fix would be tempted to grow.
(b) EXTEND, DON'T FORK. The room-preview half of S4 must not be built by re-implementing canon's
maze.json → LevelBundle projection inside cradle (drawLevel.ts:60-112 needs palette, slots,
tiles_by_type, resolved entities/items, absolute paths — all of it canon's export job). That is a
parallel system. It belongs as a hash-addressed variant of the existing room export.
(c) ADDITIVE PAYLOAD ONLY. The `produced_by` item changes the lineage payload the platformer's own
History tab also reads. It must be a new field, never a redefinition of `op`/`ts`, or that surface
shifts.
Nothing here touches generation, so the platformer pack stays byte-identical; nothing deletes or
regenerates; cradle keeps writing only through `canon grid restore --actor` (verified at
lib.rs:810-830). One adjacent hazard, outside the four symptoms but in the same flow: Tabs renders
only the active tab's content, so switching from the room editor to History unmounts LevelDetail
and silently discards its unsaved BATCH edits — an edit disappearing unannounced.

**Needs your decision**

Two, and they are design decisions, not code questions.
1. What should a restore whose result already exists in the CAS look like? The content-keyed graph
is correct and cheap — the same bytes really are one version. But "restore" is an EVENT, and the
tree renders only content, so an exact rewind is invisible by construction. Either the cards learn
to show a node's producing events (additive `produced_by`), or history stops being purely content-
keyed. The first is small and honest; the second is a real design change. Which?
2. Should a room's History be one tree or two? Today `room:<id>/grid` and `room:<id>/placements`
are two names for one byte-stream, so both tabs render the identical 7 nodes while the restore
behind each card rewinds a different step. Options: (i) keep one tree and label each edge with the
step that produced it, dropping the toggle; (ii) scope each tab to its own step's events. (i) is
truer to the CAS; (ii) matches what the tester expected. This is the question the "tree doesn't
work" complaint is really asking.
Also worth confirming: was the tester in the room editor's History tab (DetailPane → History →
RoomHistory), and which step tab was selected when they clicked restore? The journal says step
"placements", which matches the default tab — but if they believed they were restoring the room
wholesale, that reframes what "no new branch" meant to them.


---

## P0-9 C — the dialogue editor, broadly

**partly-confirmed** · effort **large**

**What is actually happening**

Five independent causes, not one bug — which is why the whole section reads as failing.
1. A DOCTRINE THAT MOVED UNDER THE CODE (complaint 2, confirmed, no tension).
September_master_prd.md:33-37 — committed at HEAD, amended 2026-09-05 "hand-test pass, user-
confirmed" — splits capability-blocked (render greyed with a reason) from mode-inapplicable (do
not render), with `+ new row` in view/test as its literal worked example. Every dialogue/scene
component was written against the un-split rule and greys both. The user's complaint IS the
current doctrine. Confirmed offenders: SceneScript.tsx:71-77 (the exact "add a line" the user saw,
"enter Edit mode to write lines"); TreeRail.tsx:246-270 (＋ New tree has no gate at all and opens
in every mode — DialogueSurface.tsx:786 passes `onNewTree={mode === "edit" ? newTree :
undefined}`, so each axis row inside is disabled with "structural editing lands with Edit mode");
SceneActors.tsx:45-52, :82-88; SceneSettings.tsx:69-75, :124-129; SceneTab.tsx:333-335 inside a
tray rendered whenever `mode !== "view"` (:280); DialogueSurface.tsx:879-892 and :527-535. BUT the
disabled data-bearing inputs in the same files are NOT offenders (correction A).
2. ONE DEAD WIRE (complaint 4, confirmed exactly). Inspector.tsx:89-95 renders `<button
className="btn dang" onClick={() => onSelectNode(node.node_id)} title="Delete this node — every
consequence is previewed first">Delete…</button>`. `onSelectNode` is `setSelectedNode`
(DialogueSurface.tsx:985), so the button re-selects the already-selected node — a true no-op.
`onSelectNode` is used at exactly ONE site in Inspector (:91), i.e. the prop exists solely for
this button and is misnamed; Inspector has no `onDeleteNode` in its props (:23-47). The key path
works (DialogueSurface.tsx:482-487 → `previewDelete` at :323-350). Tree delete works
(Inspector.tsx:494 → DialogueSurface.tsx:975-984 → the same DeletePreview sheet, labelled `Delete
${node|tree}` at DeletePreview.tsx:39-41), which is why the user saw that half behave. Inspector
is only mounted when `mode === "edit" && tree` (DialogueSurface.tsx:171), so this is edit-mode-
only, exactly as reported. It escaped because structural.test.tsx:316-320 drives only `keyDown
Backspace`, commented "drive the same path the ⌫ key takes"; nothing tests the tray button or the
correctly-wired tool-rail ✕ (DialogueSurface.tsx:829, DialogueToolRail.tsx:87-101).
3. A CHOICE CARD THAT IS 20% CLICKABLE, WITH NO HOVER, PLUS TWO INPUT PATHS ON DIFFERENT RULES
(complaint 1, mouse). Dominant cause is the hit target and the missing affordance (correction D).
Underneath it there is a real asymmetry: TesterDock.tsx:72-74 fires the key path with no gate;
:213 refuses locally via `disabled={verdict === "fail"}` (verdict from canon's `choice.pass` at
:204); canon returns `refused` at verbs.py:707-713, surfaced at useDialogueTest.ts:198-201. Plus
the unbounded key index (correction E).
4. A FIXED-HEIGHT DOCK WITH UNBOUNDED CHROME (complaint 1, scroll). NOT a missing scroll
container: `.dlg-dock-transcript,.dlg-dock-choices{flex:1;min-width:0;overflow-y:auto}`
(App.css:5387-5393) with `min-height:0` on every ancestor (`.dlg-dock` 5351-5358, `.dlg-dock-body`
5382-5386, `.dialogue-tab` 2338-2343, `.tabs-body` 450-455). The defect is that `.dlg-dock` is
`flex:0 0 186px` with `overflow:hidden` while its two non-scrolling siblings grow without bound —
`.dlg-dock-head` (5372-5377) is `display:flex` with no wrap and no `flex:none`, so at narrow
widths its title/gates spans shrink to min-content and wrap taller; `.dlg-statechips` (5484-5490)
is `flex-wrap:wrap` and also unbounded, and TesterDock.tsx:117 renders it ONLY when collapsed. In
a column flex container `min-height:auto` stops both shrinking below content, so the only child
that gives is the scrollable body — it collapses toward ~40px while the scroll container itself
stays correct. `.tabs-body` being `overflow-y:auto` then scroll-chains the wheel to the whole tab.
I could not run the app, so this is CSS reading, not observation. Separately confirmed:
SceneTab.tsx:359-372's dock has no expand state and no Expand button at all — permanently 186px.
5. TWO THINGS WORKING AS DESIGNED THAT LOOK LIKE FAILURES. Rename (complaint 5) is forbidden by an
explicit v1 decision in three places — design_handoff_dialogue/PLAN.md:254 ("Renaming is a rewire
of every inbound choice — forbid renames in v1 … or implement as a compound op"), canon ops.py:36,
cradle ops.ts:27-28 — and TREE_OPS (ops.py:49-54) has no rename verb and no `tree.label` verb
either, so trees are equally un-renameable after `tree.add` (label is set only at ops.py:146 and
:167). The scene canvas (complaint 6) is a numbered script by design (SceneScript.tsx:1-7 citing
README screen 08).
AND THE ENTRY ANSWER, DEFINITIVE (complaint 6): NO. If a start node has three options leading to
A, B and C, the entry field of A, B and C is NOTHING AT ALL — nodes have no entry field.
`entry_node_id` is a TREE-level field (models.py:109, default "start"); `DialogueNode` carries
only node_id, speaker, prompt, choices, tags (models.py:74-78); entry-ness is derived by
comparison (`is_entry`, models.py:119-121). A, B and C are reached purely through each choice's
`next_node_id`. Setting entry to A would make the conversation BEGIN at A and orphan start. It
reads as per-node only because the "entry node" `<select>` (Inspector.tsx:435-450) sits directly
below the per-node choices/conditions sections in the same 300px tray, separated by a small "this
tree" header (:430-434). Scenes have no trees and no entry at all: a SceneDoc is `{actors,
settings, trigger, once, on_finish, lines}` with lines numbered 1..N and choice options branching
by LINE NUMBER (`to: number|null`, scene.ts:33-38). The rail section that reads as "trees in a
column" is SceneTab.tsx:219-234, headed "npc tree lists" but actually listing ACTOR IDS that deep-
link to each NPC's own Dialogue tab; the rail one line above already says "appears in" (:217).

**Fix**

Split into what is buildable now and what is blocked on the user. Every item extends existing
machinery; no cradle-side pack writes anywhere; no fix introduces a hardcoded id list.
BUILDABLE NOW, NO DECISION: (4) DEAD DELETE BUTTON — trivial, do first. Add `onDeleteNode:
(nodeId: string) => void` to Inspector's props (Inspector.tsx:23-47), point Inspector.tsx:91 at
it, pass `onDeleteNode={previewDelete}` at DialogueSurface.tsx:985. `previewDelete`
(DialogueSurface.tsx:323-350) already computes the whole Consequences object the sheet and canvas
share. Drop the now-unused `onSelectNode` prop rather than leaving a second dead wire. Extend
structural.test.tsx's existing delete block (:316) with a button-path case, and cover the tool-
rail ✕ (DialogueSurface.tsx:829, DialogueToolRail.tsx:87-101), which is correctly wired but
untested. (1a) HIT TARGET + AFFORDANCE — small, and this is the fix that actually answers the
user. Make the whole `.dlg-verdict` card the click target for pass/unevaluable choices instead of
the one-line `.dlg-verdict-take` (App.css:5425-5448 / TesterDock.tsx:208-224), with the repair
buttons nested as stop-propagation controls, and add the `:hover` rule that does not exist today
so a passing choice reads as clickable. (1b) KEY BOUNDS — trivial. Guard TesterDock.tsx:72-74 (or
useDialogueTest.ts:193) on `index < result.choices.length` so an out-of-range key is a no-op
instead of surfacing `--choose 5 is outside 0..1` (verbs.py:706 / dialogueMock.ts:571) in the
dock. (1c) DOCK GEOMETRY — small. Bound the chrome so the body keeps a floor inside 186px: `max-
height` + own `overflow-y:auto` on `.dlg-statechips` (App.css:5484-5490), `white-space:nowrap` +
`overflow-x:auto` on `.dlg-dock-head` (5372-5377), and a `min-height` on `.dlg-dock-body`
(5382-5386); or let the dock grow with `.dlg-dock{flex:0 0 auto;min-height:186px}` (5351-5358) —
NOT `flex:none` on the head and NOT `flex:0 1 186px`, both of which make it worse. Same edit gives
SceneTab.tsx:359-372's dock the Expand affordance it lacks. Also guard against `.tabs-body`
(450-455) scroll-chaining with `overscroll-behavior:contain` on the two dock columns. (3) EMPTY-
STATE PROSE — small. TreeRail.tsx:167-171 ALREADY renders "scenes · 0", so the user's "Scenes 0"
half exists and this is only a disclosure. Add one shared `EmptyState({label, count, children})`
beside ExpandableText.tsx using the app's existing `<details>/<summary>` convention
(EntityOverview.tsx:531 `.complex-field`); no such primitive exists today — 12 one-off `-empty`
classes in App.css (229, 351, 2045, 2392, 4165, 4380, 4845, 5076, 5288, 5313, 5604, 6097) and zero
components. First callers TreeRail.tsx:172-175, :194 (make the note conditional — it prints even
when scenes exist) and :202-203. (6-copy) — trivial. Rename SceneTab.tsx:221's "npc tree lists"
(it lists actor ids deep-linking to each NPC's Dialogue tab, and :217 already says "appears in").
Retitle Inspector.tsx:430-434's TreeSection so "entry node" reads as per-tree, with a one-line
hint that a tree has exactly one entry node and nodes have none. Fix SceneScript.tsx:9-11's
factually inverted renumbering comment. Fix the two doctrine-11 strings: DeletePreview.tsx:56 and
DialogueSurface.tsx:903.
BLOCKED ON THE USER: (2) MODE-INERT SWEEP — medium, NOT mechanical. Hide only inert ACTION
controls (sites in cause 1 above), copying the `{editable ? … : null}` pattern already in
SceneScript.tsx:198-209 and SelectorNode.tsx:252-272. LEAVE every capability-blocked reason
greyed: testDisabledReason (DialogueSurface.tsx:205), improveDisabledReason (:212),
saveDisabledReason (:718), the entity picker's already-added rows that C5 explicitly tests. Re-
check DialogueSurface.tsx:879-892 against the carve-out at master_prd:37 before touching it. Gated
on decision 1 below. (1d) FAILING-CHOICE PARITY — gated on decision 2. (5) RENAME — large, and it
starts in canon. Doctrine 1 means cradle cannot do this alone: a `node.rename` compound op added
to TREE_OPS (ops.py:49-54) that rewrites the node key, retargets every inbound
`choice.next_node_id`, and moves `entry_node_id` when the renamed node was the entry — one journal
entry, one CAS pair, mirrored in cradle's ops.ts, then Inspector.tsx:88 becomes an input.
PLAN.md:254 names this as the sanctioned alternative. Cheaper first step: a `tree.label` op alone,
since trees are equally un-renameable today. (6-graph) TREE DIAGRAMS IN SCENE VIEW — a design
reversal against SceneScript.tsx:1-7 and README screen 08 under doctrine 9. The machinery exists
twice (DialogueGraphMode with @xyflow/react + dagre, and event/PuzzleGraphMode), and scene choice
options already carry `to: line-number` edges, so a Script/Graph segmented reader — the pattern
ModeBar already hosts for Card/Graph in View mode (DialogueSurface.tsx:724-739) — is the
extension. Needs the user's call.

**Doctrine risk**

Complaint 2 has NO doctrine tension in the direction the user feared — the diagnosis is right that
September_master_prd.md:33-37 (committed at HEAD, amended 2026-09-05 "hand-test pass, user-
confirmed") already splits the two cases and its worked example is verbatim "`+ new row` while in
view or test mode → Do not render it." The dialogue components predate it. But four risks the
diagnosis missed or got backwards:
1. THE INERT-CONTROL / READ-ONLY-FIELD GAP — the biggest risk in the sketch. Doctrine 4's test as
written ("if the honest answer is 'because you are not in that mode', it should not be on screen")
would hide SceneScript.tsx:211-216's textarea, :163-166's speaker select,
SceneSettings.tsx:79-85's once checkbox and :91-96's trigger select, SceneActors.tsx:66-71's
required select — the scene's actual content — leaving View mode blank. The doctrine does not
distinguish a control from a read-only field, and the sketch's "mechanical, convert every
`disabled={!editable}`" instruction would ship that regression. Needs a ruling before the sweep.
2. DOCTRINE 9 vs THE TESTER FIX. Dropping `disabled` from `.dlg-verdict-take` is a design
reversal, not "more compliant": README.md:153 pins "err-washed, not clickable", :154 pins
unevaluable "still clickable", master_prd:42 says design READMEs win on interaction, and
tester.test.tsx:234-241 asserts it. Its stated reason for refusing the other direction is also
wrong — reading canon's returned `choice.pass` in the key handler is not a second evaluator;
useDialogueTest.ts:5-8 bans EVALUATING, and TesterDock.tsx:213's `disabled` already reads that
same field. 3. DOCTRINE 4'S OWN CARVE-OUT, OMITTED. master_prd:37 adds a fifth bullet: where the
blocked control IS the screen's primary action, the reason stays visible inline with its deep
link. DialogueSurface.tsx:879-892 is exactly that case for a treeless NPC; hiding it wholesale is
not obviously right. 4. DOCTRINE 11, NEWLY RELEVANT AND UNMENTIONED. "Code never cites a planning
document" (master_prd:44-47, added 2026-09-05, and the ONLY uncommitted part of the doctrine —
`git diff` shows rule 11 alone). Three live violations in these components: the raw `--choose 5 is
outside 0..1` reaching the dock (verbs.py:706 / dialogueMock.ts:571), DeletePreview.tsx:56's
visible "README §8", and DialogueSurface.tsx:903's tooltip "this row does not own". The many
`README Q5` / `README screen 08` / `PLAN.md:254` file-header citations fall under its third bullet
(internal docstrings may keep rationale, but must not be the only sentence) — lower severity.
UNCHANGED AND SAFE. Doctrine 1 is respected everywhere: the only backend change proposed is an
additive canon op for rename, with journal + CAS. Doctrine 7 (platformer byte-identical) is
untouched. Doctrine 8's "ids are data, never a hardcoded union" holds throughout the code I read
and in every fix — `AxisId` is explicitly `type AxisId = string` with the comment "not a union:
the axis list is DATA" (axes.ts:20-22), axes come from `axesOf(vocab)` (TreeRail.tsx:252), scene
triggers from `vocab.scene.triggers` with an escape option for an undeclared value
(SceneSettings.tsx:40, :97-104), and TREE_OPS/SCENE_OPS are op-KIND tuples, not id lists. No fix
above introduces a literal id list.

**Needs your decision**

Four decisions, in the order they block work:
1. MODE-HIDING SCOPE — gates the whole sweep. Doctrine 4 says hide mode-inapplicable controls.
Does that cover only inert ACTION controls (＋ line, ＋ actor, ＋ setting, remove ✕), or also the
disabled data-bearing inputs beside them (the line's textarea, the speaker select, the trigger
select, the "Plays once" checkbox)? Hiding those would blank the scene's content in View mode.
Recommended: hide actions, keep disabled fields as the read-only rendering of a value, and add
that sentence to doctrine 4 as a third bullet.
2. FAILING CHOICES IN THE TESTER — you asked for both mouse and keys. Widening the hit target and
adding a hover state fixes the passing-choice case with no decision, and that is probably the
whole of what you actually hit. For BLOCKED choices the two paths disagree today: keys fire and
show canon's refusal, the mouse cannot, because README.md:153 pins failing choices as "not
clickable". (a) gate the key handler on the same `choice.pass` canon already returned, so keys
match the design; or (b) reverse README:153 so both paths click through to canon's refusal. (b) is
likely what you want but it overrides a design README under doctrine 9.
3. RENAME — reverse the v1 forbid? PLAN.md:254 forbids `node.rename` and sanctions "or implement
as a compound op". Yes = a new canon op with journal + CAS, then cradle mirrors it. Cheaper first
step: TREE_OPS has no `tree.label` either, so trees cannot be renamed after creation; that one is
a simple additive op and may cover most of the pain.
4. TREE DIAGRAMS IN SCENE VIEW — reverse the design? SceneScript.tsx:1-7 cites README screen 08
for "a numbered vertical sequence, not a graph", and doctrine 9 says the README wins. The
machinery exists twice and scene choice options already carry line-number edges, so a Script/Graph
toggle is cheap — but only you can reverse it.
And one answer you can have now, no decision needed: if a start node has three options leading to
A, B and C, the entry field of A, B and C is nothing at all. Nodes have no entry field;
`entry_node_id` is a per-TREE field defaulting to "start". Setting entry to A would make the
conversation BEGIN at A and orphan start.


---

## P0-9 D1 — dungeon NPC `status` (your reversal)

**confirmed** · effort **medium**

**What is actually happening**

Not a bug — a deliberate design rule, correctly implemented, that the user has now reversed. The
stamp step asks the ROW ("does this dict already carry a status key?") instead of asking the KIND
("does this entity type carry a status?"). Dungeon EntityKinds bind no Pydantic model
(src/canon/packs/dungeon/spec.py:75-105 declares no `model`), unlike the platformer's
EnemyDefinition/ItemDefinition which descend from ArtifactMeta whose `status: ArtifactStatus =
PENDING` (src/canon/bible/artifacts.py:61) puts the key on disk at birth. So no dungeon row is
ever born with a status, the row-sniffing guard is permanently false, and the provenance signal
lives only in .canon/journal.jsonl — invisible to anything reading the pack files, including
cradle's row table and any future staleness cascade.
The deeper layer the original diagnosis stopped short of: because pack specs resolve from the
pack's OWN .canon/registry.json once `world new` has stamped it, any per-kind datum added to fix
this is stale-by-construction on every existing world. The guard's real dependency is not "what
does the kind declare" but "what did this pack's registry declare when it was created."

**Fix**

First put the scoping question to the user, because it picks the fix (see open_question).
VARIANT A — npc only (or any subset). Needs the datum AND a migration answer. 1.
src/canon/packs/spec.py:101-107 — add one stamped field to EntityKind's write-discipline block,
e.g. `status_field: str | None = None` (the field NAME as data, so no literal union, no hardcoded
kind list). It rides `stamped()` (spec.py:129-133) into registry.json and shifts template_version
(src/canon/registry_ops.py:101-107) — safe, `.canon/` is outside the byte-determinism contract
(tests/test_create_flow.py:547-552 docstring, and the EXCLUDED_DIRS assertion), and
tests/test_db_core.py:591-595 recomputes the hash from the seed rather than pinning a literal. 2.
src/canon/packs/dungeon/spec.py:75-105 — set it on the `npc` entry. 3. **NEW, and the item the
original sketch omitted: make it reach existing packs.** src/canon/packs/__init__.py:165-184
`_entity_from_registry` copies only SEED_ONLY from the seed, so a registry stamped before this
change yields `status_field=None` forever. Pick one: (a) merge the seed's value for stamped keys
the entry does not carry — but that cuts against the stated doctrine at packs/__init__.py:186-190
("a present registry is the source of truth, and guessing past it would hide the corruption"); (b)
ship a re-stamp path and tell the user to run it on existing worlds; or (c) accept that only newly
created worlds get it. Whichever, it must be a conscious call, not a silent no-op. Add a test that
resolves a pack THROUGH a stamped registry, not just the registry-less fixture — otherwise CI will
not catch this. 4. src/canon/db_ops.py:1085 — replace `if diff and not per_file and "status" in
row:` with a shared helper true when the kind declares the datum OR the row already carries the
key, so every existing kind stays bit-for-bit unchanged. 5. src/canon/dialogue/verbs.py:579-581 —
same helper (`entity` is already in scope at :569). 6. src/canon/dialogue/verbs.py:1073-1074 —
same, for the scene/event row, only if `event` also flips.
VARIANT B — every dungeon collection kind. No datum, no registry change, no migration, works on
existing packs today. 1. src/canon/db_ops.py:1085 — drop `and "status" in row`. 2.
src/canon/dialogue/verbs.py:580 and :1073 — drop the same guard. That is it. Platformer is
untouched because both its kinds are per_file and never reach line 1085 (db_ops.py:1157
`user_edited=None if per_file else False`). Side effect to name out loud: dungeon room, monster,
item, quest, class, music, sfx and any `db define`d kind start stamping too.
BOTH VARIANTS also touch: 7. src/canon/write_core.py:353-357 — update the step-5 docstring. 8.
tests/test_db_core.py:273-274 — flip the assertion and its comment (this is the line that pins
D1). 9. Docs carrying D1: docs/test_plans/P0-9_dialogue.md:85;
docs/test_plans/README_consolidated_phase0.md:225; the comments at src/canon/db_ops.py:1040-1041,
src/canon/dialogue/verbs.py:576-578, src/canon/adapters/dungeon_write.py:486-489. 10. Decide
whether `status` joins the npc's `code_fields`, or `db complete` will silently drop it
(src/canon/db_ops.py:862-880 + :902).
NO CRADLE CHANGE, confirmed: `status` is already in CORE_PROTECTED
(src/canon/packs/spec.py:56-58), unioned by `_wall` (db_ops.py:138-139) and published via
`_registry_lists` (db_ops.py:470-477); cradle/src/components/db/RowEditor.tsx:109-135 builds its
fields from THE ROW and :209-217 classifies anything protected as disabled, so a new key renders
correctly with zero work. Doctrine 2 (cradle never writes packs) is untouched — every path here is
a canon verb.
TRAP the original sketch is right about, and I confirmed: do NOT pass `user_edited=True` to
write_document from a collection caller. src/canon/write_core.py:393 short-circuits `if
user_edited or (...isinstance(updated, dict)...)`, so True skips the dict guard; npcs.json is a
JSON list and `updated["status"] = ...` raises TypeError, and for a keyed_object collection
(monsters/items) it would silently create a bogus row keyed "status". The stamp must stay at row
level inside each caller's apply().

**Doctrine risk**

Three, all avoidable, one newly identified.
(a) "Ids/kinds are DATA, never hardcoded lists" — a fix that writes `if entity.kind == "npc"` or a
module-level STAMPS_STATUS set violates it. Variant A's datum belongs on EntityKind in the pack
registry seed, beside protected/routed/renames/refs. Variant B introduces no kind test at all and
so cannot violate this rule.
(b) "The platformer pack must stay byte-identical" — safe under both variants. Platformer
enemy/item are per_file and never reach db_ops.py:1085; their rows already carry a status from
ArtifactMeta. The only delta under Variant A is registry.json's template_version, and .canon/ is
exempt (tests/test_create_flow.py:547-552). This only becomes a real risk under a GENERATION-
SEEDED variant — emitting `"status": "pending"` from src/canon/packs/dungeon/parsers.py:183-220 —
which would be an emitted-tree delta for `world new --template dungeon`.
src/canon/world_ops.py:306-317 refuses to do exactly that for world.json, on the reasoning that
stamping at create "would be an emitted-tree delta R14 never sanctioned AND would label every
freshly generated world as human-corrected, inverting the (generated → human-corrected) signal the
journal exists to collect." Strong precedent for stamp-on-write only.
(c) NEW — "extend existing machinery, never build a parallel system." Variant A's step 3 pushes on
packs/__init__.py:186-190's "a present registry is the source of truth" rule. Backfilling seed
defaults into a stamped registry entry is a real doctrine amendment, not a detail; it must be
decided deliberately rather than slipped in as a shim. Variant B sidesteps it entirely, which is
its main argument.
Doctrine 6 ("nothing regenerates on its own; nothing is deleted without asking") is satisfied
either way: the stamp lands on the row being edited, when it is edited — the same upgrade-on-write
shape as `dialogue_trees`. No migration verb, no background rewrite, no existing pack touched
until a human edits a row.

**Needs your decision**

Which rows must carry status — npc only, or every dungeon row? This single answer picks the fix
and its cost.
- "Every dungeon row" → Variant B: three one-line guard deletions, no registry change, no
template_version shift, works on your EXISTING test world immediately. Effort: trivial. - "npc
only" (or npc + event) → Variant A: a new stamped EntityKind datum, plus a decision I need from
you on how it reaches worlds already on disk. Your current dungeon world's .canon/registry.json
was stamped by `world new` before this field existed, so the seed change alone will not touch it —
you would either need to re-stamp that registry, or accept that only newly created worlds get the
stamp. Effort: medium.
Second, smaller question: should `status` survive `db complete`? Today it would not —
complete_db_row rebuilds the row and only carries over locked/skeleton/code_fields values. Adding
"status" to the npc's code_fields makes it durable; leaving it out means a re-completion clears
the human-edited mark, which may be what you want.


---

## P0-9 — conditional trees need a pre-set selector

**partly-confirmed** · effort **medium**

**What is actually happening**

Two layers, and the investigator named only the upper one.
UPPER (confirmed): cradle built "axis first, selector later" per
design_handoff_dialogue/README.md:130 ("＋ New tree asks for the axis first" — it never says the
selector is seeded). `newTree` (DialogueSurface.tsx:292-310) emits `tree.add` with `axis` and NO
`selector` key; the comment at :283-291 states this is deliberate. The selector then becomes a
modal gesture (Inspector.tsx:482-484 button → SelectorPicker at :505, `dlg-sheet-scrim`
role="dialog" at :534). The bridge that would connect them — `AxisMeta.namespace` (axes.ts:35-38,
table 43-100) and `axisForNamespace` (axes.ts:137-142) — is DEAD: I grepped src/, the only hit is
its own definition. So every blank row falls back to `vocab.condition_namespaces[0]` = `has_item`
(Inspector.tsx:560, :273; and `vocab.effects[0]` at :366), which is context-free by construction.
LOWER (the investigator missed it): `axis` is a persisted presentation field with NO invariant
tying it to `selector` anywhere on either side. canon's `stored_tree` (models.py:169-183) writes
`axis` straight through; `validate_trees` (verbs.py:379-490) reads `selector` and never once reads
`axis`; `tree.selector` (ops.py:203-215) sets the two independently. That is why `axis: Quest
state / selector: none — the fallback` is reachable and why the rail groups it under "Quest state"
(axes.ts:153) while it matches every state. A cradle-side fix can make the pair coherent AT
CREATION; nothing on either side will keep it coherent afterwards. Worth stating so nobody later
"fixes" it by adding a canon-side coherence rule — the `custom` axis exists precisely to allow
arbitrary tokens, so such a rule cannot be written.
The unreachable-default consequence is confirmed and reproduced verbatim: `rankBeforeFallback`
(ops.ts:358-368) correctly lands the new tree at rank 0, ahead of the rank-999 fallback, but
because it arrives `selector: null` it BECOMES the first fallback. Both warnings fire for every
NPC, not only legacy ones — `write_back`/`legacy_projection` (storage.py:337-360, 412-427) take
`legacy_fields` from the SPEC, not from the row.

**Fix**

Cradle only; canon needs zero changes. SIX touch points (the investigator listed five).
1. axes.ts — derive the namespace from pack data instead of the AXIS_META column:
`vocab.condition_namespaces.includes(axis) ? axis : null`. I verified it reproduces the table
exactly: condition_namespaces = has_item/quest/time/player/flag/segment/room/scene/event (canon
spec.py:226-228) contains all seven non-custom selector_axes =
quest/segment/time/flag/room/scene/player/custom (spec.py:231), and `custom` is absent. It is also
strictly BETTER than the table: `axisMeta`'s unknown-axis fallback currently hands back
`namespace: axis` (axes.ts:123) for a pack axis that is not a declared condition namespace — a
token `legal_in` (grammar.py:129-151) would refuse. Thread `vocab` in the way
`axesOf`/`groupTrees` already do. AXIS_META keeps label/hint/tone only. Delete `axisForNamespace`
or wire it.
2. DialogueSurface.tsx:292-310 `newTree(axis)` — emit `selector: ns ? { rows: [seedRow(ns, vocab)]
} : null` beside the existing `axis` + `rank: rankBeforeFallback(doc)`. Same `tree.add` op both
sides already accept (canon ops.py:136-155; cradle ops.ts:407-424 accepts `op.selector ?? null`).
Removes both shadowed-fallback warnings. Update the pin at structural.test.tsx:363-383.
2b. `seedRow(ns, vocab)` — prefer a COMPLETE token where the pack's own descriptor closes the
slot: walk `namespaceShape(ns, vocab)` (grammar.ts:283-321) and if every required slot carries a
non-empty `choices`, seed `formatToken(ns, ...choices[0])` — `time` → `time:dawn`, saveable
immediately. Otherwise seed the bare namespace and rely on 3+6. Still 100% pack-data-derived. Note
only `time` qualifies on the seed pack, so this is mitigation, not a substitute for 6.
3. Inspector.tsx — move the SelectorPicker body (ConditionRow stack + EngineLagTrayNote, :543-556)
inline into TreeSection, replacing the read-only span at :475-479. Modal chrome (:534-542,
:557-570) comes off; `liftAboveFallbackOps` in `set` (:524-532) stays. TreeSection's signature
(:416-428) gains `doc` and `packInfo`; BOTH call sites (:63, :389) must pass them. Non-negotiable
pairing with 2 — this is what puts the parse error where the author is looking.
4. Inspector.tsx:558-561 ＋ row — seed the tree's own axis namespace, not
`vocab.condition_namespaces[0]`.
5. Optional: Inspector.tsx:452-473 axis select — when the tree has no selector, seed the blank row
too, so `axis: Quest state / selector: none` stops being reachable.
6. NEW, and required: model.ts `localReport` (:485-525) must run `parseToken` over selector rows
and choice conditions/effects and report unparseable tokens as ERRORS. Today it does not, so the
save sheet goes green on a doc canon will refuse (SaveSheet.tsx:56-58 blocks only on
`report.errors`) and the author eats "dialogue update refused (fail-closed)" after the fact,
losing nothing but discovering it late. `parseToken` needs only `vocab`, which cradle holds, so
this respects the function's own "no pack lookup" contract at model.ts:477.
Verbs unchanged: `tree.add` and `tree.selector` through `canon dialogue update --ops --actor`
(cli/main.py:3805-3831 → verbs.py:530). Mock follows free — dialogueMock.ts:28,500 reuses cradle's
applyOps and mirrors the same refusal at :503.

**Doctrine risk**

Three, all avoidable; the investigator named two.
(1) "Ids/kinds are DATA" — the obvious fix reaches for `AxisMeta.namespace` out of AXIS_META
(axes.ts:43-100), a cradle-side hardcoded axis→namespace map. Derive from
`vocab.condition_namespaces` instead. I verified the derivation reproduces the table exactly for
all eight seed axes and additionally repairs the unknown-axis fallback at axes.ts:123.
(2) "Extend, never build beside" — do NOT re-shape `DialogueSpec.selector_axes` (canon
spec.py:173) into axis descriptors. It is stamped into `.canon/registry.json` as a plain list of
strings (packs/__init__.py:371-373 via `stamped()`) and re-shaping it breaks already-stamped
registries.
(3) NEW — do NOT add `conditions` to `DialogueNode` (canon models.py:63-80) to satisfy "a
conditional on a node." `DialogueChoice.conditions` (models.py:55-58) already carries exactly that
meaning and is what the engine, the grammar, the tester and the engine-lag layer all read. A node-
level field would be a second gating system beside the existing one — the parallel-system trap in
this finding.
Platformer byte-identity is NOT at risk: platformer/spec.py:288-289 sets `dialogue=None,
capabilities=["grid"]`, and `pack info` only emits the dialogue block when "dialogue" is in
capabilities (packs/__init__.py:371). Nothing regenerates or deletes; every mutation stays a
buffered EditOp flushed by ⌘S through `canon dialogue update --actor`. Cradle still writes no pack
file.
One live-defect note for the fix's own safety: item 6 is doctrine-adjacent in the other direction
— doctrine 10 says authoring is never blocked by what the runtime cannot evaluate. Item 6 promotes
only PARSE failures (which canon already treats as errors, verbs.py:435-437) to cradle-side
errors; engine-lag stays a warning. Do not let it creep past that line.

**Needs your decision**

When ＋ New tree seeds a selector row that is incomplete (e.g. `time` with no window — which canon
refuses fail-closed), what should the save sheet do?
1. Block Save with the parse error named, from the moment the tree is created (needs fix item 6;
honest, but the first thing a new tree does is disable ⌘S).   2. Seed a fully-valid default
wherever the pack's descriptor closes the slot (`time:dawn`) and only fall back to the incomplete
row otherwise (fix item 2b). Saveable immediately for `time`; `quest`/`room`/`scene` still need an
entity pick, so they land incomplete.  ← DEFAULT   3. Leave the tree ungated on disk and render a
"pending" selector row in local component state, only emitting `tree.selector` once the author
completes it. Closest to "pre-set but not yet committed", but it is the only option that adds UI
state the op log does not see.
Related, smaller: today ＋ New tree makes the new UNGATED tree the first fallback and shadows the
NPC's real default (two warnings, reproduced). Fixing that falls out of any of the three —
confirming you want it fixed rather than kept as the deliberate "the selector is the author's next
decision" behaviour the code comment at DialogueSurface.tsx:283-291 argues for.


---

## P0-9 C10 — `dialogue improve` failed on a missing tree

**partly-confirmed** · effort **small** · ⚠️ **blocks the paid pass**

**What is actually happening**

A buffer-vs-disk boundary error in the improve gate. NOT an id-synthesis bug.
`canon dialogue improve` is a pure DISK read: improve.py:190-191 `resolve_npc(pack_dir, npc_id)`
then `npc_trees(res.row, ...)`, and :198-201 raises `f"npc {npc_id} has no tree {target!r} (have
{[t.get('tree_id') for t in trees]})"` when the requested id is not on disk. It is handed no
buffer, by design.
cradle's gate is computed from the UNSAVED BUFFER and asks the wrong question.
DialogueSurface.tsx:212 `const improveDisabledReason = doc.trees.length === 0 ? "no dialogue to
improve yet" : "";` where `doc` is the buffer (:127 `const doc = editor.doc ?? base`). "Does the
buffer hold any tree at all" is not "does THIS tree exist on disk". That one string drives both
consumers — the dlg.improve command (:553-554) and the ModeBar button (:719, disabled at
ModeBar.tsx:124) — so both stayed live.
The unsaved id then rides straight through: :160 `const treeId = activeTreeByKey[key] ??
defaultTreeId(doc)` → :1080 `treeId={treeId}` → ImproveDialogue.tsx:100 `treeId: scope === "tree"
? treeId : null`.
ARITHMETIC CONFIRMED AGAINST THE REAL PACK. /Users/wolfgangblack/Documents/canon-
dlg/npcs/npcs.json, npc 1001 carries `dialogue_trees` with exactly
['1001:incomplete','1001:complete','1001:failed','1001:default'] — byte-for-byte the error's
"have" list. 4 trees in the buffer → DialogueSurface.tsx:294 `let n = doc.trees.length + 1` → 5 →
'1001:tree_5'. The pack journal (.canon/journal.jsonl) shows dialogue_update on 1001 at 17:04
(actor user) and 17:47 (actor cradle:user) carrying only choice.add / node.add — no tree.add ever
landed, so tree_5 never reached disk.
A SECOND PATH, same cause: :160 never reconciles `activeTreeByKey[key]` against `doc.trees`.
`confirmDelete` resets it on tree.remove (:359), but ⌘Z (:455 `editor.undo()`) is cursor movement
(useDialogueEditor.ts:13-16, "undo/redo is CURSOR MOVEMENT, not inverse ops"), so undoing a
tree.add drops the tree while activeTree still names it — `tree` goes null, improve stays enabled,
same failure.
THE ID IS NOT THE BUG — I confirmed the investigator's refutation. The ids-are-DATA rule forbids
Literal unions and hardcoded lists over ids that already exist in pack/registry data.
'1001:tree_5' names an object the author is creating; there is nothing to read it from. canon
takes it verbatim: ops.py:136-152 `tree.add` accepts any non-empty, non-colliding id and appends
it. It becomes real the moment ⌘S runs. cradle is the correct namer. (The legacy ids in the error
list are themselves derived, not hardcoded — model.ts:245-268 `importLegacy` builds
`${characterId}:${suffix}` from vocab field names, mirroring canon's storage.py:278-287.)

**Fix**

All in /Users/wolfgangblack/Documents/projects/cradle. Extends the existing disabled-reason
machinery; no canon change; no pack write.
1. Gate on disk truth from the BUFFER'S BASE, not from `show`. Replace DialogueSurface.tsx:212:
const savedTreeIds = useMemo(        () => new Set(((editor.buffer?.base ?? base) as
AuthorDoc).trees.map((t) => t.tree_id)),        [editor.buffer, base],      );    `base` is
canon's own doc (toAuthorDoc of the row) and `editor.commit(saved)` (:230-235) replaces it with
the trees canon returned from the last save — synchronous, never null, no extra IPC, and the
existing 14 tests stay green. Do NOT use `show`: improve.test.tsx:104-117 returns `trees: []` and
would disable the button in nearly every test, and `show` is null on mount and on failure.
2. Do not blanket-disable the entry. Keep improveDisabledReason for the honest case only:
const improveDisabledReason =          doc.trees.length === 0 ? "no dialogue to improve yet"
: savedTreeIds.size === 0 ? "improve reads the SAVED pack — ⌘S this dialogue first"        : "";
Then pass a separate `unsavedTreeReason` (non-empty when `treeId && !savedTreeIds.has(treeId)`)
into ImproveDialogue as a prop beside the existing `treeId`. Use it to disable the "this tree"
pill (:201-206) with the reason, force scope to "npc" when the active tree is unsaved, and disable
Propose (:372) while scope==="tree". scope="npc" keeps working — it sends `treeId: null` (:100)
and hits disk. Doctrine 4 shape throughout: disabled WITH the reason, never hidden, same as
testDisabledReason (:205) and dlg.selector (:562).
3. Fix the stale active tree, DialogueSurface.tsx:160:      const active = activeTreeByKey[key];
const treeId = active && doc.trees.some((t) => t.tree_id === active) ? active :
defaultTreeId(doc);    Closes the undo-a-tree.add path and the cosmetic tree label "—" (:711).
4. NEW, and the money one. Make scope="npc" honest about what it will actually improve.
ImproveDialogue.tsx:67-75 must compute `units` from the SAVED trees, not `doc.trees`, so the spend
card (:84, :90) and the modal line (:246) quote what canon will really process; and render
`result.trees` in the result header (:283-290) so the user can see which trees came back. Add a
pre-spend line when the buffer holds unsaved trees: "N of M trees are unsaved and will not be
improved — ⌘S first."
5. Do NOT change the id scheme at :294-296. Optionally widen the collision check to dedupe against
savedTreeIds too.
6. Tests in improve.test.tsx, beside the existing reconciliation cases (:200-236): (a) push a
tree.add into the buffer, set it active, assert dlg.improve entry stays enabled but "this tree"
carries the reason and no dialogue_improve fires at scope=tree; (b) same buffer, scope=npc, assert
the quoted unit count excludes the unsaved tree; (c) undo a tree.add and assert treeId falls back
to defaultTreeId; (d) treeless NPC + newTreeFromGreeting (:266-281) then improve.

**Doctrine risk**

The corrected fix carries none: it reads tree ids out of the buffer's canon-supplied base (data,
not a hardcoded list or Literal union), extends the one existing disabled-reason path rather than
adding a parallel gate, touches no pack file, and leaves ⌘S as the only writer.
The traps are all in the WRONG fixes: - Auto-saving before improve, or shipping the unsaved buffer
into improve. Either breaks the invariant ImproveDialogue.tsx:1-8 exists to keep ("AN LLM RE-
AUTHOR IS NEVER A WRITE ... ⌘S remains the only write"), and auto-save also violates "nothing
regenerates on its own". Improve must stay a disk read. - Making canon tolerate an unknown tree
id. improve.py:198-201 refusing loudly is correct behaviour and must not be softened. - Gating on
`show` (the investigator's own step 1). Not a doctrine violation but a verified regression: it
breaks the green suite and false-disables on load and on fetch failure. - One thing to watch in
step 4: compute the saved-tree set from `editor.buffer.base` / `show.trees`, never from a
hardcoded notion of which tree names are "real".

**Needs your decision**

When the active tree is unsaved, should "every tree for this NPC" (scope=npc) stay live —
improving only the saved trees, with the modal saying plainly "3 of 5 trees are unsaved and will
not be improved — ⌘S first" before the spend card — or should the whole Improve entry be disabled
until the buffer is clean?
Option 1 (default, my recommendation): keep scope=npc live with the explicit partial-scope warning
and a corrected unit count. It preserves a working path and matches doctrine 4 (disabled WITH the
reason, never hidden), but the user does have to read one more line before a paid run.
Option 2: disable Improve entirely whenever the buffer is dirty. Simplest to reason about and
closest to the literal "don't let me in if it's not going to work", but it removes a capability
that works today and makes improve unavailable during normal authoring.


---

## P0-8 — music/sfx buttons unreadable, new roll fails

**confirmed** · effort **small** · *first diagnosis overturned*

**What is actually happening**

Three independent causes, all verified against the code.
1. CONTRAST — a container class reused as a button class, on top of a missing systemic guard.
`.view-toggle` (App.css:1651-1658) sets `background: var(--bg-sunken)` and deliberately no
`color`/`font-family`, because those live on the descendant rule `.view-toggle button { color:
var(--fg-muted) }` (App.css:1659-1668). EntityTable.tsx:501 and :511 put that container class on
bare `<button>`s. I confirmed there is no base `button {}` rule reaching them — the only one,
`.start-app button { font: inherit; color: inherit }` (start.css:18-25), is scoped to `.start-
app`, which is applied ONLY on StartScreen.tsx:88 and RecentProjectsPage.tsx:100, never on the
editor shell (`.app`, App.css:32) — and no `color-scheme` anywhere (zero hits across App.css,
tokens.css, start.css, index.html). So the buttons take `--bg-sunken` #0a0b0e (tokens.css:29,
dark) with UA `color: buttontext` resolving light-black, plus the UA font (buttons do not inherit
`font-family`). Default theme is dark (store.ts:381). NOT music/sfx-specific: `canCreateRow` is
registry-driven (EntityTable.tsx:319-323 → placements.ts:56-58) and true for
npc/monster/item/quest/event/class/music/sfx — equally black on all eight; music/sfx merely
default to list view (EntityTable.tsx:325-327) so the toolbar is the only chrome on screen. The
same missing guard makes RowEditor's unclassed footer buttons (RowEditor.tsx:700-741) off-theme,
including the disabled Create+LLM button.
2. FAILING ACTION — the dungeon PackSpec binds zero `EntityKind.builder` callables (`grep -rn
builder src/canon/packs/dungeon/` → zero hits; only platformer/spec.py:113 binds one), so
`new_db_row` hits `if complete: raise _complete_not_yet(entity)` at db_ops.py:822-823, message at
:447-453, constant at :91. The error text is accurate and the gap is real:
September_Phase_0_prd.md:344 lists "`canon generate / regenerate / reroll` (per-entity LLM) |
exists, unwired", and `grep -c reroll September_master_prd.md` → 0, genuinely unassigned. The UX
defect layered on top: the capability is projected nowhere cradle can read. I ran `.venv/bin/canon
pack info` on bibles/mazeworld_scifi — entity keys are exactly
label/id_field/layout/count/placeable/schema_source (packs/__init__.py:338-347); `db_types` emits
dir/id_field/skeleton_fields/llm_fields/code_fields/schema_source/label/layout + the P.1 lists
(db_ops.py:480-500). Neither carries a completion flag, so `completeOff` (RowEditor.tsx:163) can
only be set reactively from the caught error at :279 — after the user has already passed a $-tier
spend-confirm for an op that cannot spend.
3. REVIEWER'S CLAIM — correct, and the most serious. data.rs:56-58 hardcodes ENTITY_TYPES
including "music","sfx"; :60 AUDIO_TYPES; :67-83 scans .mp3/.wav/.ogg stems; :85-91 fabricates
`{name, filename, kind}`. `is_audio_type` short-circuits all three read paths before any JSON is
consulted — collection_entries :432-442 (feeding load_world's sidebar counts at :525-530),
list_entity_rows :584-597, get_entity :737-743. On bibles/mazeworld_scifi I confirmed: no
music/music.json or sfx/sfx.json; music/ has 10 .mp3, sfx/ has 28; manifest.json.music/.sfx are
`{stem: absolute path}` maps pointing at /Users/.../MazeWorld/data/. Canon disagrees provably — I
ran `pack info`: music count 0 layout music/music.json, sfx count 0 layout sfx/sfx.json — and
canon's own test enshrines it at tests/test_packs.py:474. No writer exists (`grep -rn
"track_id\|sfx_id" src/canon` matches only the registry declaration at
packs/dungeon/spec.py:277-308); no schemas either (`find src/canon -name music.json -o -name
sfx.json` → nothing; only platformer has a schemas/ dir), which is why schema_source is null.
Canon says so in prose at packs/rows.py:38-39 ("no tree carries `music/music.json` yet (P.1.8-9)")
and September_Phase_0_prd.md:959-963. Underneath: PRD decision A7
(September_Phase_0_prd.md:2431-2432) is unbuilt — AssetPhase still emits a fixed catalog of .mp3
names (pipeline/phases/asset.py:237-242) and no rows.
CONSEQUENCE CHAIN, verified in code (I did not execute it — read-only): a plain "Create" on music
WOULD succeed and write music/music.json, given a track_id typed into the id field
(RowEditor.tsx:283). `_new_collection_row` (db_ops.py:718-795) skips the protected wall for the
id_field (:734-736, which matters because `track_id` is itself in music's protected set),
`_allocate_id` accepts a caller-supplied id for an `id_alloc: null` kind (:707-715 — music/sfx
declare no id_alloc, so an id is REQUIRED; without one the click errors with the id_alloc message
instead), skeleton is None so `dynamic_model` types nothing and rides `extra="allow"`
(db_models.py:139-153), music declares no `refs`, `_read_collection` returns [] for an absent file
(:167-175), then commit_document writes it. That row is then PERMANENTLY INVISIBLE in cradle
because every audio read path bypasses the file. And "✎ Edit row" on any stem-derived pseudo-row
issues `db update --type music --id combat`, reaching `_locate` (db_ops.py:424-434) against an
empty collection → `FileNotFoundError: music 'combat' not found`.
docs/test_plans/P0-8_cradle_surfaces.md:25-31 gate B1 ("every one of the nine kinds edits … music,
sfx") cannot be closed on any world today.

**Fix**

Three changes; 1 and 2 in scope now, 3 filed separately.
1. CONTRAST — trivial, plus a one-line systemic guard.    a.
cradle/src/components/EntityTable.tsx:501 and :511 — `className="view-toggle"` →
`className="btn"`, and drop the inline `style={{cursor:"pointer"}}` (App.css:750 already sets it).
`.btn` (App.css:738-757, hover :758-761, disabled :762-765) is the codebase's existing toolbar-
button primitive with `color: var(--fg-muted)` and `font-family: inherit`; it is already used for
the peer control "✎ Edit row" (EntityOverview.tsx:1161). Tests query by `getByTitle`
(EntityTable.test.tsx:71, 86, 95) and no test references the class, so nothing breaks.    b. ALSO
give RowEditor's footer buttons (RowEditor.tsx:700-741) `className="btn"` / `"btn pri"`, and add
`color-scheme: dark light` to `:root` in src/styles/tokens.css. Without (b) the DISABLED "Create +
LLM complete" — the exact control fix 2 relies on — still renders UA buttonface with black text on
the dark panel.
2. THE COMPLETE BUTTON — small; fixes all eight creatable dungeon kinds at once. Publish the
capability so the control is disabled BEFORE the click and before the spend dialog.    a. Canon:
add ONE derived key to `db_types` ONLY, src/canon/db_ops.py:487-497 — `"completable":
entity.builder is not None`, plus a reason string from `COMPLETE_NOT_YET_ROW` (db_ops.py:91). NOT
`pack_info`: its entity block is asserted by exact dict equality at tests/test_packs.py:475-479
and tests/test_db_core.py:1013-1017 and both would need editing; db_types is asserted as a
superset at tests/test_db_core.py:232-235, so this is additive-safe. And NOT
`EntityKind.stamped()` — `builder` is SEED_ONLY (packs/spec.py:127), tests/test_packs.py:139-140
guards it, and P0-10's `template.version` hashes that shape.    b. Cradle: add `completable?:
boolean` to the `DbType` type (RowEditor.tsx:56-68) and seed `completeOff` from `spec` inside the
existing db_types effect (RowEditor.tsx:196-207) instead of only from the caught error at :279.
The `disabled + title` rendering at :727-728 already exists.    c. Amended-doctrine-4 compliance
(master_prd.md:33-37): the reason must reach KEYBOARD FOCUS, not only hover. `title=` alone is
hover-only — add `aria-describedby` or a focus-revealed tooltip, and do not leave it as inline red
`err` text.    d. Side effect: `confirmSpend` (RowEditor.tsx:254) is never reached on a builder-
less kind, closing the doctrine-3 wart of a $-tier dialog in front of an op that cannot spend.
This extends the existing capability-projection machinery (`placeable`, `schema_source`) rather
than adding a parallel one, and adds no kind list anywhere — the flag is derived from registry
data. `completable` is simply true for the platformer's enemy/item.
3. AUDIO ROWS — file separately; medium-to-large.    a. In cradle/src-tauri/src/data.rs, make the
three `is_audio_type` branches (:432-442, :584-597, :737-743) try the registry layout path first
(music/music.json, sfx/sfx.json) and fall back to the stem scan only when it is absent.    b.
CRITICAL: the interim "disable create/edit on the audio surfaces" needs a doctrine-8-safe
predicate, and the only one available today is `AUDIO_TYPES` (data.rs:60) — the literal that is
forbidden. Instead, STAMP THE ROW SET when the synthesis branch is taken (`source: "directory-
scan"` / `synthesized: true`) and have the UI grey create/edit off that flag, never off the kind
id. That keeps ids/kinds as data and generalises to any future kind whose rows are not on disk.
c. Replacing ENTITY_TYPES (data.rs:56-58) with pack_info data means plumbing the pack-info
document through the `DataSource` trait (data.rs:44-51 — its methods receive only `&Path`;
pack_info is shelled in lib.rs:564) and its test fixtures. Not a local edit.    d. The real gap
stays canon-side: nothing writes those rows until PRD decision A7
(September_Phase_0_prd.md:2431-2432) is built. Interim reason string: "canon declares this kind
but no generator writes its rows yet."
Nothing in 1, 2 or 3 writes a pack file; every mutation still goes through a canon verb with
`--actor` (lib.rs:2223-2224 always appends it; `--complete` + `--llm-backend` only when complete).
Platformer byte-identity untouched, and data.rs's `PLATFORMER_TYPES` branches (:374, :518-523)
must be left alone.

**Doctrine risk**

RISK 1 (as diagnosed, sound). Special-casing `if (typeId === "music" || typeId === "sfx")` in
EntityTable.tsx to hide the buttons breaks doctrine 8's "ids/kinds are DATA" and re-introduces
exactly the hardcoded capability gate the standing comment at EntityTable.tsx:313-317 says row
P0-8 removed. Nuance: the file DOES still carry presentational music/sfx literals (list-view
default :325-327, sfx partition regex :333) — the rule is that CAPABILITY gates are registry-
derived, not that no kind string may appear.
RISK 2 (as diagnosed, sound). "Just bind a builder for music/sfx" is §6 generate/regenerate/reroll
wiring, which the master leaves unassigned (`grep -c reroll September_master_prd.md` → 0) —
unsanctioned scope inside a bug fix.
RISK 3 — NEW, and the diagnosis's own fix 3 trips it. Its prescribed audio-surface disable has no
data-driven predicate; the only one in the code is `AUDIO_TYPES` (data.rs:60), the same species of
literal RISK 1 forbids. Derive it from "these rows were synthesized, not read from the registry's
layout path" and stamp that on the row set.
RISK 4 — NEW. Doctrine 4 was AMENDED 2026-09-05 (September_master_prd.md:33-37), during the very
hand-test pass this finding came from, into two cases: capability-blocked → render greyed with the
reason (tooltip on hover OR KEYBOARD FOCUS, never inline red text); mode-inapplicable → do not
render, worked example "`+ new row` while in view or test mode". The diagnosis reasons from the
old one-case rule and mis-cites it (:60 is about world migration; the sentence it quotes is at :68
and concerns 3D mesh surgery). Both fixes here are capability-blocked, so greying is correct — but
the reason must be focus-reachable, which `title=` alone is not.
RISK 5 — NEW. Do not put the completion flag in `EntityKind.stamped()`. `builder` is SEED_ONLY
(packs/spec.py:127), tests/test_packs.py:139-140 asserts no stamped entry intersects it, and
spec.py:130-133 says P0-10's `template.version` hashes that shape — stamping it moves a version
hash. `db_types` only.
RISK 6 — minor, doctrine 3. `confirmSpend` (RowEditor.tsx:254-268) currently raises a real $-tier
dialog quoting $0.01 in front of an op that structurally cannot spend. Fix 2 closes it; do not
"fix" it by suppressing the dialog generally.
Doctrine 1 is already satisfied everywhere: every path goes through a canon verb with `--actor`
(lib.rs:2223-2224). Doctrine 7 is untouched — none of this writes pack files.

**Needs your decision**

Two, and the second is the one that decides scope. (1) Under the amended doctrine 4, is "Create +
LLM complete" on a builder-less kind a secondary control (greyed, reason on hover/focus) or does
the "screen's primary action" carve-out apply, requiring the reason inline with a deep link? It
sits in the row-create panel next to plain "Create", so I read it as secondary — confirm. (2) Gate
B1 of P0-8 ("every one of the nine kinds edits … music, sfx") cannot be closed on any world today,
because canon writes no music/sfx rows at all. Do you want B1 amended to seven kinds with
music/sfx carried as a known gap behind PRD decision A7, or does the Phase 0 exit block on
building the audio row writer?


---

## Room editor — no horizontal pan, no coordinate rulers

**partly-confirmed** · effort **small** · *first diagnosis overturned*

**What is actually happening**

The room editor and the platformer level editor are the SAME component — LevelDetail.tsx:1297 is
the only `<LevelCanvas` mount in the entire repo (verified by grep), reached for rooms via
DetailPane.tsx:78 `<LevelDetail levelId={selection.id} room={gridKind !== "level"} />`. The world
map (WorldMapView.tsx) is a second, fully duplicated camera (its own `cam =
useRef({ox:0,oy:0,zoom:1})` at :81 vs LevelCanvas.tsx:125) that shares no code and has all the
gestures LevelCanvas lacks.
(A) HORIZONTAL PAN — two independent sufficient causes, both real:   A1. NO GESTURE PRODUCES A
HORIZONTAL DELTA ON A MOUSE. The only wheel pan is LevelCanvas.tsx:248 `c.ox += e.deltaX /
c.zoom`, with no shift/alt branch anywhere in 234-252. A physical wheel emits deltaY only. `grep
-rn "ArrowLeft|ArrowRight|shiftKey|button === 1|code === \"Space\"" src/components/level/` returns
nothing (the single shiftKey hit in the tree is DetailPane.tsx:258, unrelated tab cycling). No
arrow-pan, no space-pan, no middle-drag force-pan, no scrollbars.   A2. THE ONLY REMAINING GESTURE
IS SWALLOWED OR CLAMPED. Left-drag-empty-space pans both axes symmetrically
(LevelCanvas.tsx:344-350) but onPointerDown returns early on right-click (:291-295), on
tool==="erase" (:297-301), on fill (:304-307) and on `if (brush)` (:309-320) — so while a brush is
armed to place the gate, every drag paints. And when it does reach the pan branch, clampCam
LevelCanvas.tsx:138 `c.ox = worldW <= vw ? -(vw - worldW) / 2 : Math.min(Math.max(c.ox, 0), worldW
- vw);` OVERWRITES ox with a centering value on every redraw whenever the world fits the view.
Room geometry verified: room_0/maze.json is 30 rows x 40 cols, scale={26} hardcoded at
LevelDetail.tsx:1300, so worldW=1040 / worldH=780; on a 1512px window (App.css:116 `208px 1fr`,
LevelDetail.tsx:1053 padding 16) the pane is ~1272px wide and ~500px tall, so X is pinned and Y
pans — the exact asymmetry reported. Same clamp kills the minimap's horizontal jump:
Minimap.tsx:57-62 setOrigin sets both axes, but LevelCanvas.tsx:180-184 setOrigin -> redraw ->
clampCam.   Which of A1/A2 actually bit the user depends on their input device and window width,
and cannot be determined from the code. The Select tooltip advertises the gesture regardless
(ToolRail.tsx:34 "Drag empty space to pan.").
(B) RULERS ARE BUILT AND SWITCHED OFF FOR ROOMS. drawLevel.ts:360-416 drawRulers() draws cell
rulers in screen space (RULER_T=17, RULER_L=26 at :351-352), camera-correct, called at :321-326.
LevelCanvas.tsx:31/103/157 pipes showRulers straight through. LevelDetail.tsx:1304
`showRulers={!room && showBounds}` welds it to the platformer gravity-bounds toggle, which rooms
are forbidden to use (readOnlyReasons.ts:24 "no gravity here — bounds are platformer chrome",
RAIL_ROOM at :35-38, applied LevelDetail.tsx:1330). Rooms lost a neutral coordinate aid as
collateral damage of a physics rule. Locked in by LevelDetail.test.tsx:75
`expect(props.showRulers).toBe(false)` — 13/13 pass, I ran them.
(C) "RESET TO ORIGINAL" EXISTS BUT IS BURIED, AND THERE IS NO UNDO AT ALL. invoke.ts:1096
restoreGridStep -> lib.rs:810-830 `canon grid restore --actor USER_ACTOR`, surfaced only in the
History tab (DetailPane.tsx:225 RoomHistory, LineagePanel.tsx:181-205, which already confirms
"Nothing is deleted — newer versions stay in the history and this becomes a new branch"). `grep
-rn "undo" src/components/level/` returns NOTHING — a mis-paint has no in-editor recovery, only a
tab away.
The framing "a component tuned for wide-and-short platformer levels" is only half right:
scale={26} is unconditional, so a default 48x16 platformer level (canon spec.py:151 `default: [48,
16]`) is 1248x416 world px and is ALSO fully pinned on both axes at that window size — it just
fits, so nobody noticed. The clamp is a latent bug in both surfaces, exposed by the 40x30 room
(dungeon spec.py:368 `default: [40, 30]`).

**Fix**

A. PANNING — fixes both surfaces, since LevelCanvas is the single shared canvas. Do NOT paste
WorldMapView's blocks in; that would make a third near-copy of the same camera and is the opposite
of "extend existing machinery". Lift the shared pieces into one small camera module (clamp policy
+ space-pan hook + wheel-pan branch) that both LevelCanvas and WorldMapView consume, then:   -
clampCam LevelCanvas.tsx:138-139 — stop pinning. Clamp ox into [-(vw - worldW), 0] when the world
fits, instead of the single centered value. THREE edits, not one: the centered value must be
extracted and used as the reset in LevelCanvas.tsx:216 (level switch) and :387-388 (fit()), both
of which currently set ox=0/oy=0 and lean on clampCam to re-center. Miss those and every level and
room loads flush top-left.   - wheel LevelCanvas.tsx:234-252 — add the shift/alt branch that
WorldMapView.tsx:267-271 already has (`cam.current.ox += (e.deltaX || e.deltaY) /
cam.current.zoom`), so a plain mouse can pan horizontally. This is the half of the fix that works
regardless of window width.   - onPointerDown LevelCanvas.tsx:285-330 — compute `const forcePan =
e.button === 1 || space.current` and test it BEFORE the erase/fill/brush branches (mirroring
WorldMapView.tsx:327-331), plus the `space` ref and keydown/keyup pair (WorldMapView.tsx:92,
283-305). `inTextField` is already shared at src/lib/keys.ts (imported by App.tsx:25 and
WorldMapView.tsx:8) so the guard is reusable — but note LevelDetail registers NO keydown listener
today, while App.tsx:207 and DetailPane.tsx:263 already own window keydown; a third listener that
preventDefaults Space must not swallow the agent composer's spacebar.   - Update ToolRail.tsx:34
copy and the room help line to advertise the new gestures.
B. RULERS — split the boolean. Add `showRulers` state beside `showBounds` (LevelDetail.tsx:288),
add a Rulers toggle to ToolRail NOT in RAIL_ROOM so it stays live for rooms, pass
`showRulers={showRulers}` at LevelDetail.tsx:1304, default ON for rooms, update
LevelDetail.test.tsx:75. Recommend NUMERIC labels on both axes, not A-AAA: every coordinate in the
data and in `canon level apply-edit --json {"exit":[x,y]}` is numeric, and drawLevel.ts:399 places
labels on cell boundaries, so letters would both disagree with canon and sit on gridlines. Two
real cautions in drawRulers: the step adapts to keep labels ~56px apart (drawLevel.ts:370-374), so
a 40-wide room labels every 5th column at fit zoom — add a minor tick per cell; and the gutters
are fillRect'd over the world in screen space (drawLevel.ts:381-383) rather than reserving margin,
so they occlude cells 0-1 unless the camera is inset by RULER_L/RULER_T. The cheapest thing that
actually solves "I had to count squares" is a live cursor-cell chip beside the zoom pill
(LevelCanvas.tsx:419-446), fed by the cellAt() already computed on every pointer move (:258-265,
:352-357).
C. QUICK ACTIONS — smaller than the investigator thought. "Reset to original": pure surfacing. Put
a button in the room's roll row (LevelDetail.tsx:1178-1210) calling the existing
api.restoreGridStep through LineagePanel's existing confirm card (:182-199). It must restore a
journaled version, never re-roll. "Move gate to boss area": the write path is ALREADY DONE — the
gate is the draggable `exit` handle and saves through `canon level apply-edit --actor`. Tell the
user that first; they may not need a button at all once rulers and a cursor readout exist. If they
still want one, it is a computed target cell fed into the existing onMove/onCommit path
(LevelDetail.tsx:683-688, 751-753). The only open work is defining "boss area", which nothing in
maze.json expresses (keys are grid, environment, door_position, door_revealed, gate_encounter_id,
npc_positions, player_start, item_placements, event_positions, quest_ids) — a canon-side product
decision, not an engineering one.
Testing note the investigator missed: there is no LevelCanvas.test.tsx at all (only Dock,
LevelDetail, gridOps), and jsdom has no getContext (the LevelDetail run prints "Not implemented:
HTMLCanvasElement's getContext()"), so a camera regression test needs a context stub before it can
exist.

**Doctrine risk**

A and B are clean: pure view-layer changes inside cradle, no pack bytes touched, no writes, and
the rulers read bundle.grid_width/grid_height — data, not a hardcoded list. Live risks:
1. "Extend existing machinery" — the investigator's plan literally copies three blocks out of
WorldMapView into LevelCanvas. Two duplicate camera implementations already exist
(LevelCanvas.tsx:125 and WorldMapView.tsx:81, no shared code); copying makes them three-quarters
identical and doubly divergent. Lift the shared clamp/space/wheel logic into one module both
consume.
2. "cradle never writes pack files directly" — SAFE on the paths involved, verified:
save_level_edit shells `canon level apply-edit --actor USER_ACTOR` (lib.rs:710-729) and
restore_grid_step shells `canon grid restore --actor USER_ACTOR` (lib.rs:810-830). The risk is
only if someone builds a gate-mover that reaches maze.json directly instead of reusing the shipped
`exit` path.
3. "Nothing regenerates on its own" — "reset to original" MUST be `grid restore` to a chosen
journaled version, never the "⟳ Whole room" roll, which LevelDetail.tsx:1203 describes as "Re-
carve and re-place everything, then re-designate the gate". Those two must not sit adjacent
without distinct labels.
4. Ids/kinds as DATA — the A-AAA letter scheme is the one place this could go wrong in spirit: it
invents a coordinate vocabulary the pack data does not use. Not a Literal-union violation, but it
makes the UI unable to speak the numbers canon reads and writes. Numeric rulers avoid it entirely.
5. The Rulers toggle must be added to ToolRail as its own enabled control, never by re-enabling
`bounds` for rooms — that would contradict readOnlyReasons.ts:24 and RAIL_ROOM.
6. The platformer pack must stay byte-identical: nothing in A or B touches a pack, and the
existing verbs are unchanged, so this holds.

**Needs your decision**

Four, default marked:
1. Did you know the boss gate is already draggable? It renders as the `exit` marker and dragging
it saves through `canon level apply-edit --actor` today (drawLevel.ts:153,
LevelDetail.tsx:683/688/751-753). If yes and you were dragging it, the fix is rulers + a cursor
readout, not a "move gate" button. [DEFAULT: assume yes — it explains "I had to count squares"
exactly]
2. Mouse or trackpad, and was the agent panel open? This decides which of the two causes actually
bit you. Mouse wheel emits no deltaX at all, so X pan is dead regardless of window size; trackpad
emits deltaX, in which case the clamp is what ate it — and with the agent panel open the clamp
would NOT have fired. [DEFAULT: MacBook trackpad, agent panel closed]
3. Numeric rulers on both axes, or letters across the top? Letters look nice but every coordinate
in maze.json and in the canon verb args is numeric, so "column M" is a number you cannot type into
anything else. [DEFAULT: numeric both axes + a live cursor-cell chip]
4. What is a "boss area"? Nothing in maze.json names one — the keys are grid, environment,
door_position, door_revealed, gate_encounter_id, npc_positions, player_start, item_placements,
event_positions, quest_ids. Do you mean "the cell farthest from player_start", "the cell holding
gate_encounter_id", or something you'd point at? [DEFAULT: park it until 1-3 land; you may not
want the button once you can see coordinates]


---

## P0-8 C1 — the journal_kind asymmetry (your recommendation)

**partly-confirmed** · effort **small**

**What is actually happening**

Two causes, as the diagnosis said, both re-verified.
(1) CLAIMS 1+2 — `apply_level_edit` never learned to resolve its pack.
`src/canon/adapters/platformer_write.py` imports nothing from `canon.packs` (module body imports
at :20-27 are json/Callable/Path/Any/provenance/JsonOutputAdapter). Proven at runtime: importing
`canon.adapters.platformer_write` alone leaves `canon.packs` absent from sys.modules. It is
registry-blind by construction and carries THREE private copies of LEVEL_GRID data (`_SPARSE` :29,
`_STEP_FILES` :31-41, `_RESTORABLE` :652), so the journal tokens are hardcoded at :195 / :214 too.
When P0-8 wrote `apply_room_edit`, it was registry-first (`room_context` :166-182 → `resolve_pack`
→ `ctx.by_wire` → `block["journal_kind"]` at :439) and `journal_kind` was back-filled into the
platformer seed (packs/platformer/spec.py:143, :147) for symmetry — but nothing repointed the
platformer writer at it. Confirmed dead data: `docs/September_Phase_0_prd.md:1236-1238` still
describes the platformer placement blocks WITHOUT `journal_kind`, so the seed is ahead of the
paper.
(2) CLAIM 3 — a data-model gap, not a coding slip. `GridKind` (packs/spec.py:137-154) has
`placements: dict[str, dict]` (:148) whose entries carry `journal_kind`, but `points: list[str]`
(:149) is a bare name list with no slot for one. `apply_room_edit`'s marker loop therefore had
nowhere in data to read from and hardcoded `kinds.append("level_edit")` at dungeon_write.py:422 —
borrowing the platformer's token. It is the ONLY hardcoded kind inside `apply_room_edit`; every
other token there is data-derived.
Claims 4 and 5 are not defects. The two writers have different CAS units (per-step FILE vs one
maze.json — dungeon_write.py:379-386 says exactly this), and a sparse MASK has no ids to diff, so
`_change` carrying only `{"count": N}` (platformer_write.py:229) is correctly a different act from
`_move` carrying `_placement_diff` output (:73-99).

**Fix**

(a) PLATFORMER READS ITS OWN journal_kind — SAFE, TAKE IT. Output-identical.
Add the accessor as a METHOD on GridKind in `src/canon/packs/spec.py` (beside `stamped()` at :156)
rather than in platformer_write — a method adds no dataclass field, so `_GRID_STAMPED`
(packs/__init__.py:157), `stamped()`, the registry shape and `tests/test_packs.py:130-137` are all
untouched. Point dungeon_write.py:439 at it, and state explicitly whether you keep `.get(k,
default)` semantics (today's) or switch to `or` (the sketch's) — they differ on a stamped empty
string.
In `apply_level_edit`, resolve once with a FUNCTION-SCOPE import. The cycle is real and I proved
it, not just argued it: a meta_path probe fired at the moment `canon.adapters.platformer_write` is
first executed gives `ImportError: cannot import name 'resolve_pack' from partially initialized
module 'canon.packs' (most likely due to a circular import)`. Chain: packs/__init__.py:71 →
packs/platformer/spec.py:36 → packs/platformer/ops.py:46 → platformer_write. At that instant
`canon.packs` is in sys.modules with `resolve_pack` UNBOUND (verified:
`hasattr(...,'resolve_pack')` is False), while `canon.packs.spec` IS already fully loaded — so a
top-level `from canon.packs.spec import GridKind` happens to work today, but only by incidental
ordering. Use the house pattern either way (platformer_read.py:105;
ops.py:874/1142/1166/1380/1530/1582/1654/1692/1789/1871).
Then :195 / :214 read the block's journal_kind with `blocks.get("entities", {"kind": "enemy"})` /
`blocks.get("items", {"kind": "item"})`. Byte-identical because the `{kind}_move` fallback on the
platformer's own blocks yields exactly "enemy_move" / "item_move" — the literals hardcoded today —
and that holds for the seed, for a registry that stamped the datum, and for a pre-datum registry
(`effective_spec` at packs/__init__.py:199-206 does `GridKind(kind=kind, **entry)`, i.e. wholesale
replacement with dataclass defaults, so the `.get` fallbacks are load-bearing, not decorative).
Guard the new failure mode the diagnosis correctly flagged: `apply_level_edit` cannot raise
`PackTypeError` today. Wrap the resolve in try/except PackTypeError → `blocks = {}`. I checked the
other resolve_pack exits (packs/__init__.py:249-269): tier 3 shape detection answers `platformer`
for any dir with `level/`, which `_find_level_dir` (:52-60) already requires, so the only new
raise is PackTypeError from a malformed registry or an unregistered pack_type. Guarding it is
sufficient.
Add a regression test — the diagnosis's "no test changes" is what lets this rot again. Assert that
a registry stamping a non-default `journal_kind` on the level grid actually reaches `detail.kind`.
Without it the change is unobservable and indistinguishable from the current hardcoding.
Keep the diagnosis's scope caution: do NOT also swap `_SPARSE` (:29,
`("triggers","hazards","foreground")`) for `LEVEL_GRID.sparse` (spec.py:139,
`["hazards","triggers","foreground"]`) — the orders differ and that would silently reorder journal
events. Same for `_RESTORABLE` (:652). Separate tickets.
(b) UNCONDITIONAL `kinds` — DEFER. Mechanically trivial (drop the `if len(unique) > 1` guard at
dungeon_write.py:443; add `"kinds": [k]` at platformer_write.py:195/214/229/273) and genuinely
additive. But it writes a field with no consumer: the only `detail["kinds"]` references in either
repo are the write at dungeon_write.py:444 and the assertion at tests/test_dungeon_write.py:294.
Nothing in cradle reads it. It would additionally require widening `_compact_event` in BOTH
`src/canon/agent/tools_read.py:463` and `src/canon/agent/tools_write.py:134` (each copies only
`kind` out of detail) or the agent transcript still never sees it. The reviewer's stated motive —
that the platformer discards what changed — is false. Take it only alongside a reader that
consumes it.
(c) `grid_edit` — DON'T. Blast radius is genuinely small (dungeon_write.py:408/422/442,
platformer_write.py:273, `_CHANGE_LABELS` at platformer_read.py:44-62,
tests/test_platformer_ops.py:1228, tests/test_dungeon_write.py:293; zero cradle files carry these
detail strings — the only cradle hits for `level_edit` are the VERB names
`apply_level_edit`/`save_level_edit`), and per correction (1) it does NOT trip byte-identity. But
it is the wrong shape: it replaces two hardcoded literals with one hardcoded literal in a codebase
whose rule is that these tokens are data, and it splits the append-only corpus vocabulary three
ways for zero reader benefit (`_CHANGE_LABELS` maps all of them to "Saved edit" anyway).
TAKE THE COUNTER-PROPOSAL INSTEAD: add a marker journal-kind datum to GridKind — the missing slot
from root cause (2) — e.g. `point_journal_kind: str = ""` beside `points` (packs/spec.py:149),
"level_edit" in the platformer seed, a room-native token in the dungeon seed
(packs/dungeon/spec.py:353-366), `f"{kind}_edit"` as fallback. dungeon_write.py:422 then reads
`ctx.grid.point_journal_kind`; the platformer's history is untouched; only the DUNGEON's token
changes, and that path shipped at P0-8 with essentially no field history. Note the one migration
edge the diagnosis got right: a NEW field flows into `_GRID_STAMPED` automatically, so an old
registry read by new code is fine (missing key → dataclass default) but a new registry read by OLD
code is a hard `PackTypeError` at packs/__init__.py:202-203 (`set(entry) - set(_GRID_STAMPED)`).
SEPARATE TICKET I FOUND WHILE VERIFYING (not part of C1, same family): `restore_level_step`
(platformer_write.py:655) reuses `apply_level_edit` at :674 with `op="restore"`, and
`apply_level_edit` (:117-130) takes no detail override — so a platformer step restore journals
`op="restore"` with `detail.kind="enemy_move"`. `journal_last_change` prefers kind over op
(platformer_read.py:116), so the revision chip after a platformer restore reads "Moved an enemy",
not "Restored". The dungeon does this correctly: `restore_room` emits `{"kind": "room_restore"}`
(dungeon_write.py:1331), which is absent from `_CHANGE_LABELS` and falls through to op →
"Restored". Nothing catches the platformer case: tests/test_agent_permissions.py:770 asserts only
`op == "restore"`. Same cause (hardcoded kinds inside apply_level_edit), separate defect, real
user-visible wrong label. Also: platformer_read.py:1010-1012 finds restore parents via
`kind.endswith("_restore")`, so platformer level-step restores never draw the restored-from
lineage edge either.

**Doctrine risk**

1. "Ids/kinds/tiers are DATA, never hardcoded" — the CURRENT state violates it at
platformer_write.py:195/214 and dungeon_write.py:422. Fix (a) resolves half. Fix (c) as the
reviewer proposed does NOT resolve it — it substitutes one global literal for two. The
`point_journal_kind` counter-proposal does.
2. "Extend existing machinery, never a parallel system" — the shared accessor must be ONE lookup
used by both writers. Correcting the diagnosis: the right home is a method on `GridKind`
(packs/spec.py:137-159), where the datum lives, not inside the platformer's adapter. Putting a
pack-data accessor in platformer_write and having dungeon_write import it (as it already does for
the private `_placement_diff` at dungeon_write.py:56) deepens a coupling that should be shrinking.
3. "Platformer pack byte-identical" — CORRECTED, and it is now a non-risk for every option on the
table. `tests/treediff.py:39` excludes the entire `.canon/` directory (journal, CAS objects,
registry.json) from the byte-determinism contract, documented at treediff.py:14-25. Fix (a) is
byte-identical anyway; (c) and the counter-proposal cannot trip the harness either. The diagnosis
flagged this as unresolved; it is resolved.
4. "cradle NEVER writes pack files directly" — untouched by all three options. Every mutation
still routes through `apply_level_edit` / `apply_room_edit` via the `GRID_EDITORS` table
(adapters/__init__.py:52-55). Nothing in cradle contains these detail-kind strings.
5. Append-only corpus — the journal IS the training corpus (platformer_write.py:9-13, :157: "don't
journal noise; it would pollute the training corpus"). No fix may rewrite old events, so
`_CHANGE_LABELS` (platformer_read.py:44-62) must keep "level_edit" forever regardless of what is
chosen. Note that map is shared: dungeon_read.py:60 imports `journal_last_change` from
platformer_read, so both packs' labels come from the one dict — a second cross-pack leak of the
same flavour as the token itself.

**Needs your decision**

Two, and they are the only real decisions here.
Q1 — where does the shared `journal_kind` accessor live? (a) a method on `GridKind` in
`src/canon/packs/spec.py` — the datum's own home, no dataclass field, no registry-shape change, my
recommendation; or (b) a module function in `canon/adapters/platformer_write.py`, which the
investigator preferred because dungeon_write already imports `_placement_diff` from there.
Default: (a).
Q2 — does the DUNGEON's marker token get renamed off "level_edit" now, or later? Taking it now
means adding `point_journal_kind` to `GridKind`, which makes a new-registry/old-code combination a
hard PackTypeError (packs/__init__.py:202-203) and changes the token every future dungeon
spawn/exit edit writes. The only journals carrying the leaked token today are the /private/tmp
scratch packs from the P0-8 build (verified: /private/tmp/p08h/.canon/journal.jsonl line 1 is a
dungeon room spawn move journaled as `"kind": "level_edit"`), so the migration cost is near zero
right now and grows with every dungeon pack shipped. Default: fix (a) only in this ticket;
`point_journal_kind` as its own ticket, taken before dungeon packs go out.
Also flagging for a yes/no: should I file the `restore_level_step` mislabel ("Moved an enemy"
instead of "Restored") as a separate ticket? It is a real user-visible defect from the same
hardcoded-kinds cause, but it is not P0-8 C1.


---

## P0-4 A4 — the shasum mismatch

**partly-confirmed** · effort **trivial**

**What is actually happening**

Not an invariant breach and not an accidental sed sweep. The P0-4 play-harness move
(examples/platformer_play.py → canon.packs.platformer.play) deliberately updated the one template
comment that named it — main.gd:82 — because run_anim_preview moved into the wheel
(src/canon/packs/platformer/play.py:664; 0 occurrences in the 549-byte shim). The rewrite was
correct and made the file more accurate, not less.
The defect is purely documentary and has two parts: 1. A4's expected hash was recorded from the
pre-move content at a380ec3 (cb35293b0a6f6864e08c9dc9bd1933b76894a402) and never re-measured after
the deliberate re-baseline. The checkbox was marked [x] against a hash nobody re-ran. 2. A4's
parenthetical asserted "the sed pass touched a comment in it and was reverted." There is no
evidence anywhere in the history that such a revert existed or was intended — no revert commit, no
reverted line, and the surviving stale reference at main.gd:9 plus the never-matching A2 pattern
both argue against the sweep-and-revert story the parenthetical implies.
The byte-determinism contract that actually matters is about the EMITTED PACK TREE
(tests/treediff.py:21-26, "The fixtures compare the EMITTED PACK TREE… Everything else in an
output tree must be byte-identical across same-command runs and across schedulers"). It is intact
— all determinism/byte-identity tests pass. Separately, src/canon/engine_ops.py:165 states
"template changes route through the dev," so a developer editing godot_template/ in the repo is
the sanctioned path; that wall constrains the edit_project_code agent verb, not the repo. The main
fix already landed in abe60fc; two doc lines remain.

**Fix**

Doc-only, two lines, against the CURRENT line numbers at HEAD (not the diagnosis's). No code, no
test, no template mutation — so no pack goes stale and nothing regenerates. Do NOT take the
diagnosis's Option 2.
1. docs/test_plans/P0-4_packaging.md:44 — delete the false clause. It currently reads:    `- [x]
**A4 — The Godot template is byte-identical** (the sed pass touched a comment in it and was
reverted; engine stamps depend on this):`    That flatly contradicts the re-baseline note at
:16-27 in the same file. Replace the parenthetical with something true, e.g. "(one comment line
was re-baselined by the P0-4 module rename — see the note above; engine stamps depend on this
hash)", and retitle A4 from "is byte-identical" to "matches its recorded baseline" to remove the
last contradiction. The note at :16-27 and the hash at :50 are already correct.
2. docs/test_plans/P0-4_packaging.md:35-36 — make A2 pass as written. It says "expect no output
above" but emits one line:    `src/canon/packs/platformer/play.py:292:# inside the installed
package since row P0-4 — no sys.path fix-up needed).`    The grep matches its own explanatory
comment. Either name that one known hit instead of claiming no output, or tighten the pattern to
skip comments (e.g. append `| grep -v "^[^:]*:[0-9]*:#"`). Also drop the `grep -v
"^src/canon/packs/platformer/godot_template"` exclusion: I ran A2 with and without it and the
output is byte-identical, and I confirmed the `examples\.` pattern matched nothing in the pre-move
template either — it is dead code in both states.
3. Optional accuracy touch-up while in the note: :20-21 says the old comment "pointed at a deleted
file." examples/platformer_play.py still exists as a shim (the doc's own line 10 says so). The
true statement is that the path survives but no longer holds run_anim_preview.
SEPARATELY, ASK BEFORE ACTING — do not fix opportunistically: main.gd:9 still reads
`examples/platformer_pack/combat.py`, which no longer exists (now
src/canon/packs/platformer/combat.py). Correcting it is right, but it is a third template mutation
and another hash flip, so it belongs in a deliberate, announced re-baseline batched with any other
template edit.
MACHINERY EXTENDED: none new, and none needed. The rollup already lives in
src/canon/packs/platformer/godot_export.py:68-84 (template_manifest), and staleness is already
classified at :210-229, where a file matching its own stamp but not the current template is
`stale` (syncable) rather than `modified` (refused without --force).

**Doctrine risk**

Low — lower than the diagnosis implied, and the diagnosis's own fix menu carried the only real
risk.
"The platformer pack must stay byte-identical through all Phase 0 extraction work" is the rule at
issue, and it is intact in the sense that matters. The emitted-pack contract
(tests/treediff.py:21-26) is untouched and every determinism/byte-identity test passes: 42 passed
in 5.06s (test_packaging.py + test_engine_sync.py + test_estimate_identity.py), 1 passed for
test_derivations_roundtrip_byte_identical, 5 passed in 22.74s for the determinism selection over
test_platformer_slice.py. What changed is one comment in a dev-owned template, and
src/canon/engine_ops.py:165 names "template changes route through the dev" as the sanctioned path;
the wall at engine_ops.py:160-168 constrains the edit_project_code agent verb, not the repo.
THE REAL TRAP is the diagnosis's Option 2, which it presents as an equal choice. Reverting to
cb35293 does not restore a clean state: it is a second mutation that makes the file factually
wrong (naming a 549-byte shim that has 0 occurrences of the run_anim_preview it cites) and merely
moves which set of packs reports `stale`. Under "nothing regenerates on its own; nothing is
deleted without asking," neither that revert nor the main.gd:9 cleanup may be applied silently,
and neither should trigger an engine sync across existing packs unasked. No .engine.json exists
anywhere in either repo (0 in canon, 0 in cradle), so no pack on this machine is affected at all
today.
"Ids/kinds/tiers are DATA" is NOT implicated, checked in both repos: `grep -rE
"sha256:[0-9a-f]{16,}" src/ tests/` in canon returns nothing, and cradle types template_hash as a
plain `string` at src/lib/invoke.ts:707 and :718. The only hardcoded hash is a dev fixture at
cradle src/lib/devMock.ts:2250,2282 ("sha256:c65aea07f2de2865") — mock data, not a Literal union
or a validated list.
"cradle never writes pack files directly" is not implicated either: `grep -rn "godot_template"
src/ src-tauri/src` in cradle returns nothing, and this is a repo source file, not a pack file.

**Needs your decision**

Two, both cheap:
1. The main fix already landed in commit abe60fc (today, 08:20). Two doc lines remain —
P0-4_packaging.md:44's false "and was reverted" clause and :35-36's "expect no output" that emits
one line. Want me to draft those two edits, or is the note at :16-27 enough for you?
2. main.gd:9 still names `examples/platformer_pack/combat.py`, a directory that no longer exists.
Fixing it is correct but is a third template mutation and another engine-stamp hash flip. Do you
want it batched into a deliberate, announced re-baseline, or left alone for now? I did not touch
it.


---