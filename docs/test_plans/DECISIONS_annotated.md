# The open decisions, annotated

Each was checked against the code by its own agent. **Reply with the numbers you want changed** —
everything else stays as described.

`keep` = my recommendation is the current behaviour. `change` = I think the default is wrong.


## I recommend changing 4: D5, D6, D13, D20


## D2 — ✅ keep · cost to change: medium

**Today:** A room save that moves an NPC, an item and a wall writes maze.json once and appends one journal
event whose `detail.kind` is that wire's own kind when exactly one wire changed
(`npc_move`/`item_move`/`event_move`/`level_edit`) and `room_edit` plus a `kinds` list when
several did — but the per-wire breakdown is still fully present inside that one event as
`detail.changed`, keyed by wire with from/to placement diffs.

**Why it was built that way:** The journal's unit is the FILE, not the wire: every event carries a `before_hash`/`after_hash` CAS
pair that is the restore/replay handle, and a room's walls, NPCs and items all live in one
maze.json, so one write can only honestly produce one hash pair — the platformer emits several
events for the same shaped edit only because its layers are separate files (entities.json,
items.json, level.json).

**If you change it:** Per-wire journalling would mean three events for one save that all carry the identical
before/after hash, so the restore picker would list one save as three indistinguishable versions
and `already_recorded` would treat them as the same write — you'd be buying nothing except a
duplicated history, since nothing can actually restore "just the NPC move" (restore is scoped to a
whole step, and all placement wires are one step).

**My call:** Everything per-wire journalling would give you — "which wires changed", "when did this NPC last
move", per-wire diffs — is already answerable off `detail.changed` in the single event, and the
only thing it adds is a version history that lies about how many saves happened.

<details><summary>evidence</summary>

Writer + combined kind: /Users/wolfgangblack/Documents/projects/canon-
ai/src/canon/adapters/dungeon_write.py:379-386 (docstring states the rule), :439
(`kinds.append(block['journal_kind'])`), :440-445 (`detail["kind"] = unique[0] if len(unique) == 1
else "room_edit"`, plus `detail["kinds"]`), :477-491 (one `write_document` call for the whole
edit). Per-wire diff survives in the single event: /Users/wolfgangblack/Documents/projects/canon-
ai/src/canon/write_core.py:403 (`detail={**(detail or {}), "changed": diff}`) with the diff keyed
by wire at dungeon_write.py:434-441. Contrast — the platformer journals per FILE, not per wire:
/Users/wolfgangblack/Documents/projects/canon-ai/src/canon/adapters/platformer_write.py:194, :214,
:225, :270-274 (four `_emit` calls, four files). Cost of splitting:
/Users/wolfgangblack/Documents/projects/canon-ai/src/canon/provenance.py:275-305
(`artifact_versions` appends every event with an `after_hash`, no dedupe → N rows per save in the
restore picker) and :229-246 (`already_recorded` keys on `(artifact_id, after_hash)`). Gain is nil
for undo: restore is per-step and all placement wires are one step —
/Users/wolfgangblack/Documents/projects/canon-ai/src/canon/adapters/dungeon_write.py:1093-1116
(`_step_keys`) and :1184-1206 (`restore_room_step`). Locked by tests:
/Users/wolfgangblack/Documents/projects/canon-ai/tests/test_dungeon_write.py:272-294 ("several
wires in one save stay one event"). Two loose ends worth a minute, neither a reason to change the
decision: (1) `"room_edit"` is missing from `_CHANGE_LABELS` at
/Users/wolfgangblack/Documents/projects/canon-ai/src/canon/adapters/platformer_read.py:44-62, so a
multi-wire save falls through to the op fallback at :114-118 and shows the generic "Saved edit"
while single-wire saves show "Moved an NPC"; (2) nothing in either repo currently reads
`detail.kinds` — grep over canon `src/` and cradle `src/` finds no consumer — so it is written for
a reader that does not exist yet. The one genuinely multi-event path is cross-file and already
handled: an `encounters` entry writes events/events.json and maze.json as two events under one
`batchId` (dungeon_write.py:454-476, :786-864), which is the codebase confirming its rule is one
event per file rather than one event per save.

</details>


---

## D3 — ✅ keep · cost to change: small

**Today:** When a layout re-carve buries the room's door in a wall (or on a placement), the roll still
commits and moves the door to the nearest open cell — preferring one still 4-adjacent to the gate
encounter — and returns a warning naming both the old and new cell.

**Why it was built that way:** `generate_maze` carves from `player_start` and never carves toward `door_position` (it only copies
the value through), so a buried door is a normal outcome of a re-carve, not an error — and
doctrine 10 ("warn, never block") plus doctrine 1 (a code-only roll must leave a document its own
writer accepts) says settle it rather than refuse, since a door left in a wall or on a placement
makes every later drag/paint/marker save refuse.

**If you change it:** Refuse the roll: raise, write nothing, keep the old maze, and tell the user to press 🎲 again — the
door then never moves on its own, at the price of the button failing on a fair share of presses.

**My call:** I measured the refusal rate on the real carver (40×30, 1200 re-carves): ~21% of layout rolls bury
a mid-maze door and 56% bury the default corner door (38, 28), so refusing would fail roughly one
press in five — and a `--seed`-pinned roll is deterministic, so that same pin would refuse forever
with no way to apply it, whereas the current path is journaled and revertible in one click from
the History panel.

**Needs you:** Only one judgement call: cradle's roll note prints `result.warnings?.[0]` — just the first warning
— so on a whole-room roll a "dropped N monsters" warning can hide the door-moved line; say whether
you want all warnings listed there (that is the real fix, not refusing the roll).

<details><summary>evidence</summary>

/Users/wolfgangblack/Documents/projects/canon-ai/src/canon/packs/dungeon/rolls.py:207-262
(`_settle_door`: gate-adjacent preference, nearest-open fallback, the warning text) and :416, :540
(called from `_roll_layout` / `_roll_whole`); /Users/wolfgangblack/Documents/projects/canon-
ai/src/canon/layout/maze.py:79-89 (door_position is stored, never carved to);
/Users/wolfgangblack/Documents/projects/canon-ai/src/canon/adapters/dungeon_write.py:1370-1371
(roll commits after `_validate_room`, with reachability as another warning) and :556-565
(`_validate_point`: a door in a wall / off the gate would refuse every later drag — the state the
settle avoids); /Users/wolfgangblack/Documents/projects/canon-
ai/tests/test_dungeon_write.py:955-968 (asserts the rolled door is never on a placement or a wall,
5 seeds); /Users/wolfgangblack/Documents/projects/cradle/src/components/level/LevelDetail.tsx:625
(only warnings[0] is shown);
/Users/wolfgangblack/Documents/projects/cradle/src/components/db/LineagePanel.tsx:199
(`restoreGridStep` — a roll is one-click revertible). Measured with the repo's own `generate_maze`
via ./.venv/bin/python: 251/1200 re-carves wall in a random open door cell, 168/300 wall in the
(38, 28) default.

</details>


---

## D4 — ✅ keep · cost to change: small

**Today:** On a dungeon world every tree-scope dialogue condition paints amber in four places at once (ribbon
dot, dashed choice row, tree banner, save sheet) saying "authored and validated, but pygame
ignores it and shows the choice unconditionally — the tester does evaluate it," because the
dungeon pack ships an explicitly empty tree block.

**Why it was built that way:** Doctrine 10 ("data may outrun the engine"): a namespace the pack registers is legal to author, so
the editor's job is to say loudly what the runtime will do instead rather than refuse a legal
token — and an explicit empty block is deliberately distinguished from a missing one, which skips
the whole layer instead of warning falsely.

**If you change it:** Hiding the layer means the author writes gates that silently do nothing in game and only discovers
it at playtest; blocking means the editor refuses conditions the pack itself declares legal, so no
dungeon dialogue can be gated at all until the engine catches up.

**My call:** Nothing is blocked and the amber clears itself the day pygame's block gains a namespace — no edit,
no migration — so the only cost today is visual noise, which the per-tree mute already answers.

**Needs you:** Nothing to verify in code; the only judgment call is whether four simultaneous treatments is too
loud when 100% of gates are amber — if so, the cheap trim is to default the banner to muted on an
all-amber tree and keep the dots, rows and save-sheet block.

<details><summary>evidence</summary>

Empty block is real and deliberate: /Users/wolfgangblack/Documents/projects/canon-
ai/src/canon/packs/dungeon/spec.py:385-391 — `"tree": {}`, `"scene": {}`, `"effects": {}`,
`"music": {}`; the one exception is `"selector": {"quest": {"states": ["completed","failed"]}}`,
so "evaluates no gates at all" is exactly true at tree/scene/effects scope and near-true at
selector. Empty vs missing is the whole hinge:
/Users/wolfgangblack/Documents/projects/cradle/src/components/dialogue/grammar.ts:485-495 (`if
(!blocks) return true` — missing block skips the layer) and :553-577 (`engineVerdict`). Authoring
is never blocked, three independent confirmations: (1) SaveSheet.tsx:56 `const blocked =
errors.length > 0` where `errors = report.errors` — the buffer pre-flight (model.ts:486-532, which
only ever pushes missing-entry-node and dangling-selector-target errors); `engineLag` is a
separate prop rendered as its own block at SaveSheet.tsx:86-97 reading "these save fine and are
journaled as warnings." (2) The condition picker never disables on lag — EntityPicker.tsx:163-177,
`disabled={!!why}` where `why` is only `excludeReason` ("already used"); lag shows only as a
footer dot at :211-214. (3) Test lock:
/Users/wolfgangblack/Documents/projects/cradle/src/components/dialogue/engineLag.test.tsx:288-306
"the save sheet lists each lagging gate and STILL enables the primary" asserts `primary.disabled
=== false`, and :258 "on the reference world EVERY dot is amber — deliberate, not broken". Canon
agrees and also refuses to enforce: /Users/wolfgangblack/Documents/projects/canon-
ai/src/canon/dialogue/grammar.py:309-340 `engine_evaluable` returns `(bool, reason)` with the
docstring "Doctrine 10: this NEVER blocks", and verbs.py:244-250 reports it as
`engine_evaluable`/`engine_reason` fields alongside `legal: True`. Mute is banner-only, per tree,
per session — EngineLag.tsx:72-88 and DialogueSurface.tsx:209,837. Scope of a change: the whole
layer is ~238 lines in EngineLag.tsx plus one predicate in grammar.ts and 383 lines of tests; the
dots/rows read the same `engineVerdict`, so there is one predicate to flip, not four.

</details>


---

## D5 — 🔧 **CHANGE** · cost to change: small

**Today:** The ⏱ history menu labels each past conversation with its turn count ("6 turns"), except rows that
are also currently-open tabs, which instead show the panel's own client-side token-only cost
estimate.

**Why it was built that way:** It is a deviation declared in the file's own docstring, written while row P1-A6 was still unbuilt:
`GET /conversations` answers only `{id, created, turns, title}`, and at that moment no endpoint
carried per-conversation cost — the comment explicitly says "`costCents` replaces `turns` here"
once A6 lands.

**If you change it:** Every row reads a real ledger figure ("$0.14") instead of a turn count, with the one new judgment
call being what a conversation that never spent (fake/free backend, or an unpriced run) shows —
"—", "$0", or a fallback to turns.

**My call:** A6 has shipped, so the deviation's own stated trigger has already fired — the data exists in the
journal, the roll-up is already computed, and cradle already renders it on another screen.

<details><summary>evidence</summary>

DEFAULT:
/Users/wolfgangblack/Documents/projects/cradle/src/components/agent/SessionHistoryMenu.tsx:9-13
(declared deviation), :43-50 (renders `turns` for closed rows, live `costCents` for open ones).
SOURCE OF THE LIMIT: /Users/wolfgangblack/Documents/projects/canon-
ai/src/canon/agent/conversations.py:196-214 — `list()` counts `turn_end` lines and returns `{id,
created, turns}`, nothing priced; served verbatim at
/Users/wolfgangblack/Documents/projects/canon-ai/src/canon/agent/service.py:692-694. YES, THE DATA
EXISTS: every turn's token cost is journalled with `session=<conversation_id>` at
service.py:369-380, and every agent-driven paid tool journals the same key (tools_paid.py:488,
:676); /Users/wolfgangblack/Documents/projects/canon-ai/src/canon/provenance.py:658-668,686
already rolls that into `byConversation: [{session, tokensCents, generationCents, totalCents,
runs}]`. IT IS ALREADY WIRED INTO CRADLE:
/Users/wolfgangblack/Documents/projects/cradle/src/lib/invoke.ts:535-541
(`JournalConversationRow`) and :1276-1286 (`journalList(path, {summary:true})`);
/Users/wolfgangblack/Documents/projects/cradle/src/components/CostDashboard.tsx:54-60 fetches it
and :299-320 renders an "agent · by conversation" table keyed on the same conversation id the
history menu already holds. SO THE CHANGE IS: one `api.journalList(worldPath, {summary:true})` in
the menu's existing `useEffect` (beside `refreshHistory()`, SessionHistoryMenu.tsx:20), a Map from
`session`→`totalCents`, and swapping the ternary at :45-49 — no canon change, no new endpoint, no
schema change. WORTH KNOWING: the open-tab figure it shows today is NOT the ledger —
/Users/wolfgangblack/Documents/projects/cradle/src/lib/agentState.ts:311-316 documents
`Conversation.costCents` as a client-side estimate priced from token usage alone ("the ledger (A6)
is the measured truth"), so it excludes the image/animation spend the conversation triggered;
switching all rows to the journal also removes that second arithmetic path, at the cost of a mid-
turn conversation lagging until `turn_end` journals. A6 is marked shipped at
/Users/wolfgangblack/Documents/projects/canon-ai/docs/September_master_prd.md:120. Only one test
touches this menu and it asserts nothing about turns
(/Users/wolfgangblack/Documents/projects/cradle/src/components/agent/TabStrip.test.tsx:145-154),
and devMock already handles `journal_list`
(/Users/wolfgangblack/Documents/projects/cradle/src/lib/devMock.ts:2413), so the browser path
keeps working.

</details>


---

## D6 — 🔧 **CHANGE** · cost to change: small

**Today:** Picking a model restarts the whole agent sidecar on the next send — and the conversation you
picked it in keeps running on the model it was created with, because a conversation's model is
written once into its transcript's meta line and never changed.

**Why it was built that way:** The sidecar's `--backend` / `--model` flags were the only seam that carried a model to the
service, since `POST /conversations` and the user-message body both have no `model` field; the
code comment at agentActions.ts:255-264 declares this as a known deviation "until A4.5 carries a
model on CreateConversation / UserMessage" — that follow-up never landed.

**If you change it:** The picker applies on the very next message with no restart and no toast: same-provider switches
(Sonnet to Haiku to Opus) go live immediately, and cross-provider switches go live too once the
service holds one backend per provider instead of one per process.

**My call:** This is not just a delayed switch — the picker silently does nothing for the tab you used it in,
the header quotes a price for a model that is not running, and "Retry on X" after a provider error
retries on the model that just failed; the fix that makes it honest is ~10 lines (add `model` to
UserMessage, pass it to run_turn) because ChatRequest already carries a per-request model and both
provider clients honor it, and no test asserts the current behavior.

**Needs you:** Whether live CROSS-provider switching (Claude to GPT mid-conversation) is worth the extra work of
a per-provider backend map threaded through RunManager, or whether same-provider live switching
plus a restart only when the provider actually changes is enough.

<details><summary>evidence</summary>

canon-ai/src/canon/agent/service.py:485 (`model=meta.get("model")` — the turn's model comes from
the transcript, not the process); service.py:580-581,625-627 (one `app.state.backend` per
process); service.py:687 (`store.create(backend=backend_id, model=shown_model)` pins it at
creation); service.py:1087-1088 (`--backend`/`--model` CLI); canon-
ai/src/canon/agent/conversations.py:136-155,179-184 (meta is line 1 of an append-only JSONL, no
mutator exists); canon-ai/src/canon/agent/loop.py:282-289 (`ChatRequest(model=model)` per
request); canon-ai/src/canon/backends/chat_anthropic.py:165 and chat_openai.py:224 (`"model":
request.model or self.model`); canon-ai/src/canon/agent/runs.py:988,1073 (the only two
`self.backend` uses); cradle/src/lib/agentActions.ts:266-290 (setModel holds `pendingModel`;
applyModel does stopService+ensureService), :391 (applied at the top of sendMessage), :45-56
(ensureService passes --backend/--model), :346-352 (ensureRemote does NOT recreate an existing
conversation after the restart), :674 (retryLast routes "Retry on X" through setModel);
cradle/src/components/agent/ModelPicker.tsx (picker is in the header on every conversation at any
point). Runtime check with FakeChatBackend on a temp pack: conversation created with model "OLD-
MODEL-ID", turn run on a backend rebuilt as "NEW-MODEL-ID" -> the model that reached the provider
was "OLD-MODEL-ID". Test coverage: only cradle/src/components/agent/TabStrip.test.tsx:115 (renders
the menu); nothing asserts switch behavior.

</details>


---

## D7 — ✅ keep · cost to change: medium

**Today:** generate_asset and complete_row have no estimator scope, so they always get a zero-range but
shape-complete estimate: the accent paid card renders "estimate — not estimated", "backend fal ·
?", "work one asset", and an Accept button reading "Accept · spend on fal" instead of a price.

**Why it was built that way:** canon's pack estimators only know how to count nodes for whole runs, level ops, animate and music
(platformer/estimate.py:318-346) — there is no per-sprite or per-row scope — and returning None
would drop the `paid` block from the permission payload entirely, so the two most-used paid tools
would lose the card built to price them.

**If you change it:** Return None and the user instead gets a smaller card: still the "paid" badge, the tool label,
plain Accept/Reject chips (no "Always allow") and the "paid work is never always-allowed" footnote
— but no backend named, no model, no unit of work, and no "not estimated" line at all, so nothing
tells him which service is about to be billed.

**My call:** The zero payload strictly adds information over the fallback (backend, model, unit, an explicit
"not estimated") and never prints a confident $0, so the only thing the alternative buys is less
UI.

<details><summary>evidence</summary>

canon-ai/src/canon/agent/tools_paid.py:407-439 (_unpriced_payload: low/high 0.0, backend = first
category in _BACKEND_FIELDS, unitLabel "one asset"/"one row" from :295-298); :282-291
(_ESTIMATE_SCOPE has no generate_asset/complete_row entry, so :367-369 always takes the unpriced
path); permissions.py:220-222 (no estimate → no `paid` key);
cradle/src/components/agent/ToolCall/PaidCard.tsx:49-70 (unknown = low<=0 && high<=0 → "— not
estimated" + "Accept · spend on {backend}");
cradle/src/components/agent/ToolCall/WriteCard.tsx:130-145 (the !paid fallback card); tests:
canon-ai/tests/test_agent_paid.py:307-329 and
cradle/src/components/agent/PaidCard.test.tsx:157-171. TWO REAL GAPS, both small and separate from
the keep/change call: (1) tools_paid.py:389-393 — a SCOPED tool whose estimator raises returns
None, not _unpriced_payload, so the degraded pending card D7 exists to prevent is still reachable
(e.g. generate_layout with a level_id not on disk); routing that except-branch through
_unpriced_payload closes it in one line. (2) If the model omits image_backend, selected_backends
returns {} (tools_paid.py:230-237), primary is "" and agentState.ts:687-688 turns empty strings
into "?", so the card reads "backend ? · ?" and "Accept · spend on ?" — fail-closed-paid but
naming nothing. Also note pricing data already exists (canon/pricing.py IMAGE: fal-ai/nano-banana
$0.039/image), so actually pricing these is a new count scope per pack (asset kind varies: sprite
= 1 image + optional edit pass, or music/sfx), not a missing price table.

</details>


---

## D8 — ✅ keep · cost to change: small

**Today:** Nothing anywhere caps or warns on cumulative spend — the only thing between you and money is a
per-action confirm card that shows a price estimate and today's running total, and that card is
literally labeled "no cap set".

**Why it was built that way:** It was decided deliberately, not defaulted: the master PRD's §8 A-2 says "No hard budget caps…
warnings only, never caps — do not build caps", explicitly superseding Phase 1 §7.3's hard-cap
proposal, and tools_paid.py names caps in its "deliberately absent" list — but the warnings half
of that decision was never built.

**If you change it:** A cumulative ceiling (per day / per project) that refuses or pauses a paid call once crossed —
which means a run can now die mid-chain on a dollar figure rather than on a real failure, and
someone has to own unwinding a half-generated project.

**My call:** The runaway path caps are meant to close is already closed by something stronger — `paid` is never
Always-allowable in any mode, so no agent, plan approval, or autonomy setting can spend without a
human click, and tools default to the `fake` backend when a backend is unnamed, so an agent that
forgets to name a provider bills $0.

**Needs you:** Nothing to keep it. If you want the missing warnings half of A-2, you'd need to supply one number
(the daily-dollar figure the card should turn amber at) — the today's-spend value it would compare
against is already computed and on screen.

<details><summary>evidence</summary>

Decision recorded: /Users/wolfgangblack/Documents/projects/canon-
ai/docs/September_master_prd.md:279 (A-2 "No hard budget caps… Do not build caps") and :247 (S19
supersedes Phase 1 §7.3). Absence noted in code: /Users/wolfgangblack/Documents/projects/canon-
ai/src/canon/agent/tools_paid.py:56 ("budget caps (master §8 A-2 forbids them — warnings only, and
none are built here)"). The control that does exist:
/Users/wolfgangblack/Documents/projects/canon-ai/src/canon/agent/permissions.py:108
(PAID_NEVER_ALWAYS) and :516-517 (paid always returns "ask", every mode). Free-by-default
fallback: tools_paid.py:_BACKEND_FIELDS ~line 128+ (llm_backend defaults to "fake"; `None` means
fail-closed to paid). UI:
/Users/wolfgangblack/Documents/projects/cradle/src/components/agent/ToolCall/PaidCard.tsx:61
renders "$X spent today · no cap set", fed real ledger data by
/Users/wolfgangblack/Documents/projects/cradle/src/components/agent/ConfirmGate.tsx:67-88.
`budgetCents` (PaidCard.tsx:205, agentState.ts:902) is only the approved estimate high used as a
progress denominator — grep for exceed/abort on it returns nothing. Ledger + dashboard are read-
only observability: /Users/wolfgangblack/Documents/projects/canon-ai/src/canon/spend.py:161
summarize(), consumed at cli/main.py:2730, agent/prompt.py:118, and
cradle/src/components/CostDashboard.tsx. No env-var cap exists (grep for
CANON_*SPEND/BUDGET/MAX_SPEND: zero hits), and the CLI has no confirmation prompt at all (grep for
confirm/input( in cli/main.py: zero hits). Two residual gaps if you keep it: one approval can
authorize a long chain (`generate_level` = terrain + placements; `create_project` = a full world
run, tools_paid.py:22-28), and an unestimated paid call still opens the gate with "— not
estimated" and an Accept button reading "Accept · spend on {backend}" (PaidCard.tsx:52,66-68).
Blast radius inside one approval is bounded only indirectly: MAX_RETRIES=3 (pipeline/retry.py:9)
and PARALLEL_CAP=3 (agent/runs.py:112).

</details>


---

## D9 — ✅ keep · cost to change: trivial

**Today:** Every Meshy 3D op prices as a fixed credit count (20/30/35 image-to-3D, 10/15 texture, 5 rig, 3
animation) multiplied by $0.02/credit, overridable at runtime by the MESHY_USD_PER_CREDIT env var
— so a textured image-to-3D estimates at $0.60.

**Why it was built that way:** Meshy publishes credits-per-op but not the API wallet's dollars-per-credit (that page is login-
only), so the build seeded the Pro-tier proxy ($20/mo ÷ 1,000 credits = $0.02) and put the real
number behind an env knob instead of blocking on a dashboard read.

**If you change it:** Enter the real API-wallet rate as the default, which rescales every Meshy dollar figure
proportionally (at $0.04/cr a textured mesh reads $1.20 instead of $0.60) and changes nothing else
in the module.

**My call:** The mechanism is right and the rate is inert today — there is no Meshy backend until W2.2 and
neither pack's cost_model.json carries mesh counts, so nothing in the product currently prices a
mesh.

**Needs you:** THREE lookups, only the first load-bearing. (1) MESHY API-WALLET $/CREDIT — the one that moves
shipped numbers. Log in at meshy.ai and open the API/developer credit-pack purchase page (the API
wallet is a SEPARATE prepaid balance from the monthly subscription); read the pack price and the
credits in that pack and divide. Example: $20 for 1,000 cr = $0.02. That figure either replaces
0.02 at src/canon/pricing.py:282 (plus the 0.02 assertion at tests/test_pricing.py:122 and the
seven dollar figures at :124-130) or is just exported as MESHY_USD_PER_CREDIT with no code edit at
all. (2) MESHY PREMIUM / ULTRA MONTHLY CREDIT COUNTS — meshy.ai/pricing renders credit counts
client-side, so the research pass captured only the dollar prices ($40 Premium / $100 Ultra /
$70+$10-per-seat Studio) and not the credits each includes. Open meshy.ai/pricing in a real
browser and read credits-per-month for Premium and Ultra. This matters ONLY as a sanity check on
the proxy: $0.02 is Pro's $20÷1,000, so if he subscribes above Pro the right proxy changes. These
tier rows exist nowhere in the code — MESH holds per-op credits only. (3) KIMI-K2 (ORIGINAL) —
whether it is still served, at platform.kimi.ai/docs/pricing/chat. Nothing to do unless it is
alive AND he wants to target it: the code already treats it as retired (no LLM row;
tests/test_pricing.py:46 pins pricing.llm("kimi-k2") is None; the kimi default is kimi-k2.6 at
pricing.py:328). A fourth doc row marked UNVERIFIED, Lyria RealTime, is unmetered by design and
needs nothing.

<details><summary>evidence</summary>

/Users/wolfgangblack/Documents/projects/canon-ai/src/canon/pricing.py:279-283 (the "UNVERIFIED
(login-only)" comment + MESHY_USD_PER_CREDIT_DEFAULT = 0.02 and the env var name);
pricing.py:285-298 (MESH — per-op credits only, no subscription-tier rows); pricing.py:301-310
(meshy_usd_per_credit: env override, bad value falls back to 0.02); pricing.py:399-409 (mesh():
usd = credits x rate at call time); pricing.py:97-127 (LLM — kimi-k3 / k2.6 / k2.7-code, no
kimi-k2); pricing.py:328 (kimi default = kimi-k2.6);
/Users/wolfgangblack/Documents/projects/canon-ai/tests/test_pricing.py:119-138 (credit math at
0.02 and the env-override "dashboard-confirm knob" test) and :46 (kimi-k2 pinned absent);
/Users/wolfgangblack/Documents/projects/canon-ai/docs/provider_price_table.md:3 (names the three
UNVERIFIED cells), :72 (Premium/Ultra/Studio — "credit counts render client-side, confirm in
browser"), :79 (API credit-pack rate, login-only), :38 (retired kimi-k2), :66 (Lyria RealTime, the
fourth row, unmetered by design); /Users/wolfgangblack/Documents/projects/canon-
ai/docs/September_master_prd.md:214 (same three, Lyria excluded); no mesh consumer exists — grep
over canon-ai/src finds mesh only inside pricing.py and providers.py:181-187, and neither
src/canon/packs/platformer/cost_model.json nor .../dungeon/cost_model.json contains "mesh".

</details>


---

## D10 — ✅ keep · cost to change: small

**Today:** Two identical runs leave `.canon/` non-reproducible in two ways: the DAG scheduler writes a
different, finer-grained event stream to `log.jsonl` than the sequential one (measured: 136 lines
vs 62 on the same platformer create), and `registry.json` carries a `created_at` wall-clock stamp
that differs between any two creates.

**Why it was built that way:** `.canon/` is declared observability + provenance, not state — excluded wholesale from the byte-
determinism contract (tests/treediff.py:36) — so the log records what each scheduler actually did
rather than a lowest-common-denominator shape, and `created_at` records when the project was
really made.

**If you change it:** Pass a seed-derived string into the `created_at` kwarg that `synthesize_registry` already accepts,
making registry.json byte-identical across same-seed creates — at the price of a stamp that is a
fabricated date rather than the project's real birthday.

**My call:** Deriving `created_at` would not make `.canon/` reproducible anyway — `journal.jsonl` stamps a
wall-clock `ts` on every line (all 3 lines differ between two identical platformer creates for
that reason alone), and `--seed` defaults to `secrets.token_hex(4)` so an unseeded create stays
random regardless — so the change buys one byte-identical file, loses the field's only meaning,
and nothing anywhere reads `created_at` back (`packs/__init__.py:399-402` surfaces only
`template.id` and `template.version`).

**Needs you:** Nothing for D10 itself, but a separate ruling is needed on `--template dungeon`: it writes
`generated_at` and a `validation_report.timestamp` into `manifest.json`, which is emitted pack
content OUTSIDE `.canon/` and not in treediff's exclude list, so the "emitted pack content is
identical" premise is false for the dungeon and no test catches it (`test_create_flow.py:243`
compares file lists only).

<details><summary>evidence</summary>

canon src/canon/registry_ops.py:109,126 (`created_at or datetime.now(UTC)`; the kwarg exists and
NO caller passes it); registry_ops.py:104 + :160 (`template_version` excludes the `template`
block, so `created_at` does not affect the version hash — verified: two creates share
`sha256:c13e11ac…` while created_at differs); src/canon/packs/__init__.py:399-402 (only id+version
surfaced; created_at dropped); tests/test_db_core.py:587 (the only assertion on it is truthiness);
src/canon/pipeline/runner.py:103-134 vs src/canon/pipeline/orchestrator.py:270-283,300-345,380-393
(per-phase vs per-node + node_skipped vocabularies); tests/treediff.py:34-40 (.canon excluded
wholesale, log.jsonl by basename); cradle src/lib/invoke.ts:441-446 (reader already accepts
`phases` OR `nodes`); src/canon/cli/main.py:1035 (`eff_seed = seed or
f"cradle-{secrets.token_hex(4)}"`); src/canon/pipeline/phases/manifest.py:206 (dungeon
manifest.json `generated_at`). Measured myself: `world new --template platformer --seed p10` twice
orchestrated = 136 log lines each, once `--no-orchestrate` = 62; full-tree diff of the two
orchestrated runs shows divergence ONLY in .canon/journal.jsonl, .canon/log.jsonl, one
.canon/objects blob (the registry doc) and .canon/registry.json plus bible.json — emitted pack
content identical; the same diff on `--template dungeon` additionally shows manifest.json
differing at `generated_at` and `validation_report.timestamp`.

</details>


---

## D11 — ✅ keep · cost to change: small

**Today:** A group scene is a row in `events/events.json` with `type: "scene"`, allocated off the same 3000
event id counter as combat/puzzle events, carrying two unused fields (`name`, `description`) so
the engine's registry can still load the file.

**Why it was built that way:** One store, three readers: the NPC rail, the quest rail and the scene surface all deep-link scene
ids, and reusing the event kind gave scenes the write core, the CAS unit, the journal and cradle's
entity browser for free.

**If you change it:** A sibling `events/scenes.json` the engine never opens: scene rows stop carrying two dummy fields,
stop inflating the Events count in cradle, and stop being reachable through the `event:` operand —
at the price of a new EntityKind, a second id space, and split deep-links.

**My call:** The split's main prize — not being coupled to the engine's Event model — is worth less than it
looks because placement structurally cannot place a scene and Pydantic ignores the extra keys,
while the one genuine defect (a scene id passing the unfiltered `event:` operand) is a one-line
filter fix that does not need the file split.

**Needs you:** nothing — but if he wants the leak closed, the fix is to give the `event` operand descriptor an
exclusion for `type: "scene"`, which needs a small change to `operand_tables`' equality-only
matcher

<details><summary>evidence</summary>

canon-ai/src/canon/dialogue/verbs.py:826-847 (scene resolves on the `event` EntityKind), :869-874
(`_allocate` off the shared event id base), :925-929 (event_positions error); canon-
ai/src/canon/dialogue/scenes.py:47-72 (name/description emitted only to keep the engine's registry
load alive); canon-ai/src/canon/packs/dungeon/spec.py:184-212 (event kind, `routed` scene fields);
canon-ai/src/canon/packs/spec.py:221-224 vs :225 (scene operand is event+filter; event operand has
NO filter — the leak) with the equality-only matcher at verbs.py:190-203; canon-
ai/src/canon/packs/dungeon/validators.py:114 (quest target_event_id accepts a scene id); canon-
ai/src/canon/packs/dungeon/rolls.py:117-139 (placement stubs come from the room's `encounters`
list, so scenes are never placement candidates); canon-ai/tests/test_dialogue.py:506-522
(invariant codified); /Users/wolfgangblack/gt/mazeworld/src/registry.py:109-118 (every row through
create_event_from_data, no per-row guard) and
/Users/wolfgangblack/gt/mazeworld/src/models/encounter.py:20-23, :431-434 (Event requires
name/description; unknown type falls back to CombatEvent);
cradle/src/components/DetailPane.tsx:151-159 (Scene tab mounts on typeId "events"); 0 rows with
type "scene" across every events.json on disk, so no data migration today.

</details>


---

## D13 — 🔧 **CHANGE** · cost to change: small

**Today:** Clicking Restore on any version in one collection-row's History (npc:1000, quest:4000,
class:warrior — every dungeon-pack kind) writes that version's WHOLE file back, so all sibling
rows in npcs.json revert too; the confirm dialog says "restores the whole row file — every row in
it comes back."

**Why it was built that way:** The content-addressed unit is the file, not the row — a row edit snapshots the entire npcs.json
bytes, so the object store holds no row-level blob a per-row restore could read (db_ops.py:178-206
_RowFile: "the CAS UNIT... is the FILE, not the row").

**If you change it:** Per-row restore: read the old file bytes, lift only that row out of them, drop it into the CURRENT
file, and leave every sibling row exactly as it is — i.e. restore_room_step's key-scoping applied
one level down, to a row slot instead of a step's keys.

**My call:** This is the same bug you already paid to fix once for room steps (P0-8: restoring `grid` used to
silently throw away `placements` edits), the fix machinery is already sitting in db_ops
(_row_in/_set_row_in) and write_core.write_document, and today's path additionally journals only
ONE event under the clicked row — so the sibling row's own History keeps ringing its newer version
as "current" while disk holds the older bytes, and the raw write_json_singleton skips the
model/shape validation every other row write goes through.

**Needs you:** Two calls only you can make: (1) when the chosen version predates the row's creation (or postdates
its deletion), should a per-row restore delete the row, refuse, or fall back to whole-file? (2) do
you want to KEEP a whole-file "restore this file as it was" as a separate, explicitly-labelled
action, or drop it entirely?

<details><summary>evidence</summary>

/Users/wolfgangblack/Documents/projects/canon-ai/src/canon/adapters/platformer_write.py:1110-1177
(_restore_document: collection kind -> rel = layout path, write_json_singleton of the whole
document, one provenance.record under artifact_id=f"{kind}:{rest}", label "restores {rel} ({rows}
rows)"); reached from restore_asset at :1209. /Users/wolfgangblack/Documents/projects/canon-
ai/src/canon/db_ops.py:178-206 and :215-220 (_RowFile / _read_row_file — the CAS unit is the file;
a row edit snapshots the whole document). /Users/wolfgangblack/Documents/projects/canon-
ai/src/canon/packs/dungeon/spec.py:77,110,128,153,187,217,252,279,295
(npc/monster/item/quest/event/class/room/music/sfx are all mode:"collection"); platformer
enemy/item are per_file, so their restore is ALREADY per-row (platformer_write.py:1234-1252).
Precedent for the fix: /Users/wolfgangblack/Documents/projects/canon-
ai/src/canon/adapters/dungeon_write.py:1184-1290 (restore_room_step overlays only the named step's
keys onto the CURRENT document, routed through write_document for diff + validation + warnings),
with its regression test at /Users/wolfgangblack/Documents/projects/canon-
ai/tests/test_dungeon_write.py:607-630 ("the npc move survived the grid restore"). Current whole-
file behaviour is pinned by /Users/wolfgangblack/Documents/projects/canon-
ai/tests/test_db_core.py:752-756. UI copy:
/Users/wolfgangblack/Documents/projects/cradle/src/components/db/LineagePanel.tsx:96-104
(restoreScope) and :181-196 (confirm) — generic, never names which sibling rows change. Stale-
current inference: /Users/wolfgangblack/Documents/projects/canon-
ai/src/canon/adapters/platformer_read.py:983-988 computes current per (artifact_id, facet) from
journal events only, and the restore writes no event for the sibling row.

</details>


---

## D14 — ✅ keep · cost to change: medium

**Today:** The dialogue tester accepts only the four quest states the MazeWorld engine actually stores
(not_started/active/completed/failed) and only the four period names (dawn/day/dusk/night), so a
designer typing `quest:4000:offered` or `time:18:00-22:00` gets a red "not in this pack's
vocabulary" refusal naming the legal set, instead of a working gate.

**Why it was built that way:** Vocabulary is pack-registry data mirrored from what the engine really has — `Quest.status` is
those four strings and `DayNightCycle` has no hour clock at all, only period percentages — so
seeding richer values would have meant shipping a vocabulary that can never be true in a running
game.

**If you change it:** Designers could author `offered` / `turn-in` gates that parse, evaluate correctly in the tester,
and render amber "the engine ignores this" — which is what doctrine 10 says authoring should do
everywhere else — while hour windows would additionally need real range-comparison semantics that
nothing in either repo has today.

**My call:** Neither addition changes one thing a player sees — the dungeon engine's evaluable block is empty
for every scope except selector-quest-completed/failed, so richer tokens would only move from
"refused at authoring" to "accepted and amber" — and because the vocabulary is data, the quest-
state half stays a two-line edit for the day the engine grows those states.

**Needs you:** One judgment call only: whether authors are actually hitting the `offered`/`turn-in` rejection
often enough that a red refusal is worse than an amber never-fires gate — that is a doctrine-10
consistency question, not a code question, and if he says yes the quest-state half can ship as
data (the hour-window half should still stay out).

<details><summary>evidence</summary>

Vocabulary is pure data: canon-ai/src/canon/packs/spec.py:208 (quest states) and :210 (time
windows), mirrored as a fallback in cradle/src/components/dialogue/grammar.ts:88-92. Refusal comes
from the generic choices check at canon-ai/src/canon/dialogue/grammar.py:260, not from any
quest/time-specific code. Verified by probe: `quest:4000:offered` -> "state 'offered' is not in
this pack's vocabulary"; `time:18-22` -> same for window; `time:18:00-22:00` -> "time takes 1
operand(s)" because grammar.py:237 splits on ':'. Quest states are FREE to add:
evaluator.py:141-146 is plain string equality, and with states extended to six the probe parsed,
evaluated ("quest is turn_in", verdict unevaluable) and picked with zero code change. Two derived
behaviours DO shift and would need guarding: storage.py:181-189 `state_for_slot` takes the LAST
state mapping to a slot, so the legacy-import selector flips from `quest:<id>:active` to
`quest:<id>:turn_in` (probe confirmed; same bug mirrored at cradle grammar.ts:201-207), and
evaluator.py:268 `_next_quest_state` makes a bare `advance_quest` on an active quest land on
turn_in instead of completed. Hour windows are genuinely canon-side code, not editor work:
evaluator.py:147-153 compares `state["clock"]["period"]` by string equality, and grammar.py's
`namespace_shape` has no range/free-value descriptor shape — a `windows` descriptor can only
enumerate. Engine reality: canon-ai/src/canon/packs/dungeon/spec.py:385-391 declares `{"tree": {},
"selector": {"quest": {"states": ["completed","failed"]}}, "scene": {}, "effects": {}, "music":
{}}`; MazeWorld/src/models/quest.py:34 is `status: str = "not_started"` with the four in a
comment; MazeWorld/src/models/time.py:18-33 has TimePeriod (4 values) and FULL_CYCLE_MS with no
hour field. Decision text: canon-ai/docs/September_Phase_0_prd.md:2325 (C2) and :2328-2331 (C3),
both marked DECIDED 2026-09-01.

</details>


---

## D15 — ✅ keep · cost to change: small

**Today:** `WriteGate.hold(target)` wraps one whole `registry.execute` — and the permission chip blocks
*inside* that call — so while a chip sits unanswered, every other run wanting that same
level/artifact is parked on the mutex.

**Why it was built that way:** The §5.5 promise ("two runs never interleave a target") was taken literally: a second write must
not slip in between "the user was shown this" and "the write happened", per the deliberate-
ordering note at runs.py:812-826.

**If you change it:** Ask first, then take the lock and write — which, since canon has no compare-and-swap on writes
(`before_hash` is journaled after the fact, never checked as a precondition), means an approved
write silently clobbers whatever landed during the wait, with nothing detecting the conflict.

**My call:** Keep the hold — releasing it buys last-writer-wins with no conflict detection — but bound the
waiter, because today it is unbounded, unstoppable and invisible: no true deadlock cycle exists
(one lock at a time everywhere; only two `hold` sites, runs.py:850 and 1703), yet `lock.acquire()`
has no timeout (runs.py:370), the cancel flag is checked *before* the hold and never rechecked
after (runs.py:833 vs 850; registry.py:112-124 doesn't recheck either), so ⏹ Stop returns
`stopped: true` while the parked thread stays parked — and when the holder finally resolves, that
thread wakes and raises a *brand-new* permission chip after the user pressed Stop; the wait is
literally forever because `--permission-timeout` defaults to None (service.py:1092-97) and
cradle's sidecar argv never passes it (lib.rs:236-252); and nothing surfaces it — `RunManager`
builds a bare `WriteGate()` with no `on_acquire`/`on_release` hooks, so no SSE event says "waiting
on level:l3", which cross-conversation means tab 2 spins forever while the chip that blocks it
renders only in tab 1. Two cheap fixes, neither touching the invariant: give `hold` an acquire
timeout that refuses with "another run is editing level:l3 — its chip is open in <conversation>"
and re-check cancel after acquiring, and pass `--permission-timeout` from cradle's argv. No test
pins the hold-across-permission behavior (the gate test at tests/test_agent_runs.py:733 uses a
slow tool, not a chip), so this is a small change. Two things also shrink what the invariant is
actually worth: the chip carries `input` verbatim plus a "‹verb› ‹target›" string
(permissions.py:163-193) — there is no diff and no before-state snapshot, so "the user was shown
this diff" overstates it; and the gate is an in-process dict of RLocks, so a `canon` CLI writing
the same pack is not serialized at all.

**Needs you:** Two calls: (a) is a second conversation writing the same level while the first waits a scenario
you care about shipping, or is single-conversation-at-a-time the real usage — that decides whether
the acquire timeout is worth it at all; (b) what timeout to pass for `--permission-timeout`
(minutes) if you want the chip itself bounded.

<details><summary>evidence</summary>

/Users/wolfgangblack/Documents/projects/canon-ai/src/canon/agent/runs.py:347-380 (WriteGate; bare
`lock.acquire()` at :370, no timeout); runs.py:812-826 (the docstring stating the hold-across-
permission ordering is deliberate); runs.py:833 vs :850 (cancel checked before the hold, never
after); runs.py:850 and :1703 (the only two `hold` sites — one lock at a time, so no cycle);
runs.py:1380-1396 (`stop_run` cancels the run's pending *chips*, not gate waiters);
/Users/wolfgangblack/Documents/projects/canon-ai/src/canon/agent/permissions.py:579
(`entry.event.wait(self.timeout)`), :373 + :369 (timeout defaults None = wait forever), :163-193
(chip payload: input + target string, no diff), :644 (`cancel_pending` is per conversation);
/Users/wolfgangblack/Documents/projects/canon-ai/src/canon/agent/registry.py:112-124 (check-then-
run in one method, no cancel recheck); /Users/wolfgangblack/Documents/projects/canon-
ai/src/canon/agent/service.py:1092-1097 (`--permission-timeout` default None), :493
(`parallel=lambda name: name == DELEGATE_TOOL` — the concurrency that reaches the gate), :767
(thread per turn), no `gate=`/`WriteGate` anywhere in service.py (no acquire/release hooks wired);
/Users/wolfgangblack/Documents/projects/cradle/src-tauri/src/lib.rs:236-252 (sidecar argv omits
`--permission-timeout`); /Users/wolfgangblack/Documents/projects/canon-
ai/src/canon/agent/tools_write.py (no `expected_hash`/CAS on any write path);
/Users/wolfgangblack/Documents/projects/canon-ai/tests/test_agent_runs.py:733-777 (the gate test
serializes via a slow tool, not a permission wait — nothing pins this decision).

</details>


---

## D16 — ✅ keep · cost to change: trivial

**Today:** capture_frames and run_trajectory run without a confirmation prompt: each spawns the pygame
harness headless (SDL_VIDEODRIVER=dummy) against a temp dir outside the pack, returns up to 8 PNGs
or a position summary, then deletes the temp dir — the pack's files and hashes are byte-identical
before and after, and no paid backend is touched.

**Why it was built that way:** ASSUMPTION-6a: windowless, hard-capped (MAX_TICKS 3000, MAX_FRAMES 8, 180s timeout), writes
nothing, and it executes the same module the user's own Play button already runs — so an ask
prompt would be friction with nothing behind it, and the escape hatch was made data rather than a
code change.

**If you change it:** Every time the agent wants to look at the game it stops and asks, which turns "check whether the
jump clears the gap" from one turn into a prompt-answer-prompt round trip, in exchange for the
only real exposure: unmetered LLM token spend from up to 8 base64 PNGs per call, bounded at 8 tool
rounds per user turn.

**My call:** The claim checks out in code and is pinned by tests — no spend, no pack writes, hard budgets — and
the demote already exists and is tested, so keeping AUTO costs nothing that cannot be reversed in
one JSON line.

**Needs you:** Nothing to verify; one judgment call only he can make — the demote is hand-edited JSON with no CLI
or cradle UI writing it, so if he wants the ask-tier fallback to be reachable by a non-engineer,
that surface has to be built.

<details><summary>evidence</summary>

THE SETTING (exact): `<pack>/.canon/agent/settings.json` → top-level `tool_tiers` →
`{"capture_frames": "ask", "run_trajectory": "ask"}`. Re-read on every call, no restart.
Secondary/legacy source `.canon/registry.json` → `agent.tool_tiers` still honored; settings.json
wins.
TIER DECLARATION — /Users/wolfgangblack/Documents/projects/canon-
ai/src/canon/agent/tools_vision.py:89 `VISION_TIER = "auto"`; :95 `HEADLESS_TOOL_NAMES =
("capture_frames", "run_trajectory")`; :992-994 registers tier=VISION_TIER and attaches
`tier_resolver` only for the two headless tools (view_asset is NOT demotable by this flag).
SETTING PATHS — tools_vision.py:106 `AGENT_SETTINGS_FILE = .canon/agent/settings.json`; :107
`AGENT_TIER_PATH = ("tool_tiers",)`; :113 `REGISTRY_TIER_PATH = ("agent","tool_tiers")`; :277-288
`registry_tier()` reads settings first, registry second; :291-299 `tier_resolver()` falls back to
"auto". ENFORCEMENT — src/canon/agent/permissions.py:435-443 `tier_with`; :445-457
`effective_tier` (fails closed to the registered tier on a raise or an unknown string); :508 `tier
== "auto"` → `Decision("allow", ...)`. COSTS NOTHING — capture_frames/run_trajectory appear
nowhere in src/canon/agent/tools_paid.py (grep: zero hits); no estimator, no spend confirm. The
only spend is LLM tokens for the attached images. WRITES NOTHING — tools_vision.py:486 + :537
(`tempfile.mkdtemp("canon-capture-")` … `shutil.rmtree` in `finally`); :576 + :609 same for traj;
summaries carry `"wrote_to_pack": False` (:521, :594). BUDGETS — tools_vision.py:127-129
`MAX_TICKS = 3000`, `MAX_FRAMES = 8`; :133 `HARNESS_TIMEOUT_S = 180.0`; :319-320
`SDL_VIDEODRIVER=dummy` + PLAT_* prefix scrub. Per-turn blast radius capped at
src/canon/agent/loop.py:191 `max_tool_rounds: int = 8`. TESTS PIN IT —
tests/test_agent_vision.py:255-277 and :279-294 assert `fingerprint(pack) == before` around real
harness runs; :186-196 asserts the settings.json demote flips both tools to `ask`; :210-225
asserts the demote survives `ensure_registry` (the reason it is not in registry.json); :227-238
asserts malformed JSON keeps the registered tier. NO UI — grep for `agent/settings.json` /
`tool_tiers` across canon src, cradle/src and cradle/src-tauri/src returns only tools_vision.py,
tests, docs, and the vendored copy at /Users/wolfgangblack/Documents/projects/cradle/src-
tauri/resources/runtime/aarch64-apple-darwin/python/lib/python3.12/site-
packages/canon/agent/tools_vision.py (identical). Nothing writes the file. NOT MOOT — the vendored
cradle runtime copy matches the canon source, so the shipped behavior is the described behavior.
ONE NUANCE — tools_vision.py:325-348 `harness_launch` will execute a `launch.headless.cmd` from
the pack's `engines[]` block if one exists; no Phase 0/1 pack has one, and adding it requires an
ask-tier registry write, so it is not an auto-tier hole today.

</details>


---

## D17 — ✅ keep · cost to change: small

**Today:** Three routing conversations run against `FakeChatBackend`, which ignores the system prompt
entirely and replays hand-written assistant turns, so the fake pass proves the checker (a mis-
routed script produces the named failure `delegations: expected [...] got [...]`), proves the
corpus uses the real shipped `roster/core.md` + `foreman.md` and only tools on
`roster/foreman.json`, and proves nothing about which specialist a model would pick; the real
measurement is one already-wired command (`python -m canon.agent.eval --backend openai|kimi`),
user-run and paid, held as row A8's stage-6 gate.

**Why it was built that way:** Paid legs are user-run by doctrine, so the machinery was built to be exercised at $0 and swapped
onto a real provider unchanged — `expected_delegations` is deliberately the one check never freed
with the wording on a real backend.

**If you change it:** There is no alternative to the split itself, but there is a live choice about the paid leg: today
it would score routing and probe-order together, and it is three easy single-shot cases.

**My call:** Keep the split — it is a fact, not a choice — but fix one thing before spending money on it:
`run_scripted` applies `_tool_call_failures` on every backend (eval.py:242, outside the
`strict_text` guard), so a real model that answers correctly but skips the scripted probe read
(`describe_level` / `db_row`) fails the conversation even with perfect routing, and the paid run
would report a routing failure that is not one.

**Needs you:** Two rulings only he can give: (1) does A7's second gate — "a mixed request routes to the right
specialists unaided" — close on machinery-only evidence, since the doc already marks it met
2026-09-01 and "unaided" is exactly the part the fake cannot show; (2) does he want the paid leg
to isolate routing (a `strict_tools=False` flag mirroring `strict_text`, plus harder cases —
`routing-art-only`'s prompt literally says "don't touch the levels", which hands the model the
answer) before he runs it, or is a 3/3 pass on three easy cases the number he wants.

<details><summary>evidence</summary>

/Users/wolfgangblack/Documents/projects/canon-ai/src/canon/backends/testing.py:500-531
(FakeChatBackend: canned turns in order, prompt never read, "No randomness anywhere");
/Users/wolfgangblack/Documents/projects/canon-ai/src/canon/agent/eval.py:202 and 242-247
(run_scripted: tool-call check unconditional, delegation check unconditional, only text + stop
reasons freed by strict_text); /Users/wolfgangblack/Documents/projects/canon-
ai/src/canon/agent/eval.py:118-133 (_delegation_failures, "never freed with the wording");
/Users/wolfgangblack/Documents/projects/canon-ai/tests/test_routing_eval.py:179 (each routing conv
passes on the fake by replaying its own fake_turns) and 218-233 (misroute → named failure — the
real fake-side proof); /Users/wolfgangblack/Documents/projects/canon-
ai/tests/test_routing_eval.py:153-166 (corpus must use the shipped foreman prompt and roster
tools); /Users/wolfgangblack/Documents/projects/canon-ai/src/canon/agent/evals.py:193-214
(_foreman_system = real core.md + foreman.md), 455-596 (the three routing conversations; art-only
user message "don't touch the levels"), 792-796 (ROUTING_CONVERSATIONS = 3 of 8); no
`strict_tools` flag exists anywhere in src/ or tests/;
/Users/wolfgangblack/Documents/projects/canon-ai/docs/test_plans/P1-A7_vision_verify.md:3-5 ("Both
met 2026-09-01") and :74-77 (decision E as written);
/Users/wolfgangblack/Documents/projects/canon-
ai/docs/test_plans/README_consolidated_phase0.md:150-161 (pass 4, the paid leg, "Running the whole
corpus on both providers is what actually closes row A8").

</details>


---

## D18 — ✅ keep · cost to change: large

**Today:** The bundled Python runtime installs canon with
`cli,platformer,play,anthropic,images,audio,agent,openai` and no torch, which disables exactly one
feature — the `local` image backend (on-box diffusion sprite/tile art) — while leaving every other
verb, the agent sidecar, and all hosted art/audio backends working.

**Why it was built that way:** torch/diffusers would add gigabytes per platform to a ~300 MB payload for a path the desktop app
never takes by default, and it still would not work offline — the pipeline downloads multi-GB
model weights from the HF hub on first `generate` anyway.

**If you change it:** Bundling `images-local` would let a user generate sprites on their own GPU with no API key and no
per-image cost — but only after a multi-GB weights download on first use, and the installer would
grow from ~300 MB to several GB on every platform including the ones with no usable GPU.

**My call:** torch gates one optional backend that cannot animate, cannot run without a further multi-GB
download, and already has three keyed alternatives plus a CANON_BIN escape hatch — but the UI
still offers it unlabeled, which is a trivial bug worth fixing.

**Needs you:** Nothing for the packaging call. One decision he may want to make now: cradle's New Project art
picker lists `["local", "local"]` as a bare option next to labeled ones ("placeholder ($0)", "fal
(paid)"). In a bundled build picking it fails fast, before any paid spend, with "LocalImageBackend
requires `diffusers` and `torch`. Install with: pip install canon-ai[images-local]" — pip advice
the user cannot act on, since the interpreter is inside the .app. Either relabel it ("local —
needs your own canon install") or drop it from the bundled build's list.

<details><summary>evidence</summary>

canon-ai/pyproject.toml:44 (`images-local = ["diffusers>=0.30", "torch>=2.0", "Pillow"]`), :37
(`huggingface` = torch/transformers/accelerate/sentencepiece), :36 (`openai`), :50 (`agent` =
fastapi/uvicorn). cradle/scripts/runtime-manifest.txt:55 `canon_extras
cli,platformer,play,anthropic,images,audio,agent,openai`, with the rationale in the header comment
at lines 32-38. Built payload confirms it: cradle/src-tauri/resources/runtime/aarch64-apple-
darwin/.runtime-stamp matches that extras list, site-packages is 240 MB (306 MB whole tree) and
contains openai/uvicorn/fastapi/pygame/numpy/typer but zero torch, diffusers, transformers or
accelerate. torch's ONLY importer in canon is canon-ai/src/canon/backends/image_local.py:53-58;
the `huggingface` extra has no importer at all in src (transformers/accelerate are dead weight in
pyproject — the `canon/backends/huggingface.py` in docs/canon_extraction_discovery.md:356 does not
exist). Consumers of the local backend: canon-ai/src/canon/packs/platformer/tileset_art.py:1473
and canon-ai/src/canon/packs/dungeon/run_world.py:91. It is constructed at pipeline-composition
time (canon-ai/src/canon/packs/platformer/dag.py:705) so the ImportError lands before any
generation spend. Substitutes remain bundled: fal, Retro Diffusion, PixelLab (paid, keyed) and
`fake` ($0). local was never the animation path —
cradle/src/components/anim/AnimateModal.tsx:24-27 and :329-331 restrict animation to fal and fake,
the only backends with an img2img `edit()`. Escape hatch for power users: cradle/src-
tauri/src/lib.rs:3587 — `CANON_BIN` wins over the bundled runtime. The unlabeled UI option is
cradle/src/components/start/NewProjectModal.tsx:562.

</details>


---

## D19 — ✅ keep · cost to change: small

**Today:** When no OS credential store answers (headless/minimal Linux with no Secret Service), cradle writes
provider keys as plaintext `KEY=VALUE` lines to `~/.config/cradle/provider-keys.env`, created mode
0600 from the open call (not chmodded after), and every Keys-pane read returns a red banner saying
"stored UNENCRYPTED … anyone who can read your home directory can read them" plus a per-row source
label "cradle's unencrypted fallback file".

**Why it was built that way:** Refusing would leave a headless-Linux user with no way to add a key from the UI at all, and the
module treats a *silent* fallback — not the fallback itself — as the actual bug.

**If you change it:** Refusing means the Keys pane becomes a dead end on those machines and the user's only remaining
path is hand-writing a plaintext `.env` in the canon repo checkout via `CANON_ENV_FILE` — which is
also plaintext, gets created with the default umask (0644, not 0600), and lives inside a project
directory that travels when the project is copied.

**My call:** Refusing does not make anyone safer — it pushes the same secret into a worse-permissioned,
project-local file — and the exposure delta over gnome-keyring is narrow anyway, since an unlocked
Secret Service hands the same key to any process running as that user.

**Needs you:** Nothing — unless you want to know whether any real target user runs cradle on a Linux box with no
Secret Service at all; if that number is zero, this whole branch is dead code you could delete
instead of debating.

<details><summary>evidence</summary>

cradle/src-tauri/src/keys.rs:56-61 (FALLBACK_WARNING text), :163-172 (backend() selects File when
keychain_available() is false), :253-257 (set() writes plaintext pairs), :434-451
(write_owner_only opens with .mode(0o600), no umask window), :376-379 (keychain_available =
keyring::Entry::store_status().is_ok()), :482-530 (test asserts 0600 on first write and on
rewrite). Native backends for all three OSes are compiled in — cradle/src-
tauri/Cargo.lock:2070-2075 lists apple-native / windows-native / zbus-secret-service stores — so
this is genuinely the "no Secret Service reachable" case, not a missing-feature case. Refusal path
already exists as Backend::None at keys.rs:167-171 and its message points at CANON_ENV_FILE; that
env file resolves to `<canon repo>/.env` per cradle/src-tauri/src/lib.rs:1500-1511. Warning is
surfaced at lib.rs:1640-1641 (provider_keys), :1653-1655 (set), :1666-1668 (delete) and rendered
at cradle/src/components/settings/KeysPane.tsx:101-105 plus the per-row label at :376. Two notes:
the fallback also fires on macOS/Windows if store_status() errors and on any platform via
CRADLE_KEYSTORE=file (keys.rs:143-147), so it is not strictly Linux; and the module doc at
keys.rs:26-27 claims the warning renders "beside every row" while KeysPane.tsx renders one banner
at the top — doc drift, not a behavior gap.

</details>


---

## D20 — 🔧 **CHANGE** · cost to change: small

**Today:** On a dungeon kind (sfx included), `canon db complete` — and `db new --complete` — refuses with a
structured `not_yet` JSON error instead of inventing a per-row LLM prompt, because only the
platformer binds an `EntityKind.builder`.

**Why it was built that way:** Per-row completion genuinely does not exist for those kinds — the dungeon's five prompted kinds
(npc/monster/item/quest/event) have a prompt+parser but no builder wired to them, and
sfx/music/class/room have no prompt at all — so refusing honestly was the only truthful answer
available.

**If you change it:** Building it means writing a registry-driven builder that calls the existing per-row generation
loop (feasible for the five prompted dungeon kinds, since `npc_generation(context, skeleton,
role)` is already per-row) plus authoring brand-new prompts for sfx/music — while hiding the
button would delete the only place the user is told the capability exists at all.

**My call:** Keep the refusal — it is capability-blocked, which the amended doctrine says to show greyed with a
reason, not hide — but change two things: cradle currently makes the user approve a $0.01 spend
BEFORE the refusal and only greys the button after the failure, and the refusal's user-visible
copy still cites "Phase 0 §6 … unassigned in the master", which your own rule 11 (amended
2026-09-05) already names as the worked example of a violation.

**Needs you:** Whether per-row LLM completion is ever coming for the five prompted dungeon kinds — that decides
the greyed tooltip's wording ("not available for sound effects, use Create + Edit" for a permanent
no, vs "coming later" for a deferral) and whether the flag is per-kind or per-pack.

<details><summary>evidence</summary>

canon-ai/src/canon/db_ops.py:447 `_complete_not_yet`; raised at db_ops.py:822 (`new --complete`)
and db_ops.py:845 (`complete`); db_ops.py:91 `COMPLETE_NOT_YET_ROW = "Phase 0 §6 ... (unassigned
in the master)"` — the PRD citation rule 11 forbids, still in the shipped message. Verified at
runtime: platformer enemy/item have builders (packs/platformer/spec.py:113), all 9 dungeon kinds
have none; dungeon npc/monster/item/quest/event carry prompt_method+parser, sfx/music/class/room
carry neither (packs/dungeon/spec.py:293-307 for sfx, :312-325 `_generation_seed`). No capability
flag is published: db_ops.py:487-499 (`db_types`) and packs/__init__.py:340-350 (`pack_info`
entities block, where `placeable`/`schema_source` already live) both omit it.
cradle/src/components/db/RowEditor.tsx:249-281 — `confirmSpend` runs before `api.dbNew`,
`setCompleteOff` only in the catch; RowEditor.tsx:726-740 — button greys only after the failed
call. Doctrine: canon-ai/docs/September_master_prd.md:34 (capability-blocked = greyed + reason)
and rule 11 in the same section, which cites this sfx error by name. Tests pinning current
behaviour: canon-ai/tests/test_db_core.py:379-401.

</details>


---