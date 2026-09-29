# Pass 4 findings — diagnosed against the real packs

**2026-09-11.** Each finding got an investigator, then a second agent whose only job was to refute it.
The challenger's reading is what is recorded. Evidence came from the two paid packs on disk
(`~/CradleProjects/phase4_test2_paid`, `~/CradleProjects/pass4_test3_plat_paid`), read-only, never mutated.

**What the money proved:** the estimator is accurate per unit on both templates. The dungeon's
$0.99 shortfall against best-case equals the 26 failed units at list price ($1.03). The platformer
landed within 2.5% of best-case. **The estimator passed. Reliability and bookkeeping did not.**


---

## Your shell is shadowing your fresh keys (and the Test button)

**partly-confirmed** · effort **small** · *investigator overturned*

**Money:** Direct: $0. Test never bills (all probes read-only; billable rows disabled). The diagnosis's worry
that the dungeon's 8 unmetered Lyria attempts might have been billed is moot: the recorded failure
mode is a 403 PERMISSION_DENIED billing-dunning refusal at Google's gateway, which generates
nothing and bills nothing; the dungeon attempts 25 minutes earlier under the same project met the
same gate. Indirect: $5.60 of paid runs (3.00 + 2.60) were made believing fresh keys were in play
for three providers when the May-30 shell values were; Lyria produced 0 tracks in both runs, so
music is silent in both packs; the dungeon's 11 of 15 failed SFX are unbilled (ElevenLabs charges
per delivered effect; 4 × $0.04 = $0.16 matches audio_cost_usd) and, with sfx_concurrency=10
(asset.py:165), plausibly a concurrency-limit rejection rather than a key issue — unverified
because the reason is swallowed. The user's Google Cloud project 935155598203 has a billing
problem that will block every Lyria call until resolved regardless of which key is used.

**Cause**

Two causes stack, neither of which is "Test is broken for those providers". PRIMARY (the user's
own machine): three of the nine variables are exported by ~/.zshrc with stale May-30 values, and
cradle's precedence is keychain > inherited process env > env file (lib.rs:111-121, :141).
Launched from an interactive shell, cradle hands canon the shell's OPENAI/GOOGLE/ANTHROPIC values
for every child — Test and paid runs alike — so pasting fresh keys into .env changed nothing for
those rows. Anthropic stayed green only because the old shell key is still valid; the Google key
that reached Google belongs to a project whose Cloud Billing is in a dunning "deny" state (403
PERMISSION_DENIED, recorded in the platformer manifest), and the old OpenAI key plausibly no
longer exists (401 → "rejected"). The chip does expose this ("set · this machine's environment /
Also present in: the env file"), but the Test result line does not say which source it tested.
SECONDARY (the probe): providers.py:366-367 folds every 401/403 into "the provider rejected the
key" and :311-314 discards the body, so a 403 whose body says PERMISSION_DENIED-for-billing reads
as "bad key" — the opposite of what the user needed to hear. Whether GET /v1beta/models is itself
gated by that billing decision could not be checked offline: if it is, Google's Test was red with
the wrong reason; if not, it was green while the lane could not produce. Either way the button
could not name the real problem. ElevenLabs and Kimi use .env values (not in .zshrc): the run
proved the ElevenLabs value on POST /v1/sound-generation (platformer 4/4), so a red on GET
/v1/user is most plausibly a permission-restricted key lacking user_read (permission_type.py) —
unverified; Kimi's api.moonshot.ai vs .cn region split remains plausible and unverified.
fal/pixellab/retro/meshy are correctly DISABLED, not broken; fal's stated reason is stale (the SDK
makes non-billing authenticated POSTs), pixellab/meshy unverified, retro's reason may be right.

**Fix**

1. Precedence/visibility (cradle, extends provider_keys + the Test result): include the winning
source in canon's test result copy or have KeysPane append it from the status it already holds —
"tested the value from this machine's environment; the env file also has one" — so a fresh .env
key that is being shadowed is named at the moment the user clicks. Decision for the user (default
marked): keep keychain > shell > env file as is and make the shadowing loud [default], or flip
.env above the inherited shell env for dev launches. No pack file is touched either way. 2.
providers.py _urlopen/test_provider: read the error body only to extract a small enum (Google
error.status, ElevenLabs detail.status, OpenAI error.code), never echo it; map 403
PERMISSION_DENIED → "the key authenticated, but Google refused this project (billing or
permission)"; Google 400 API_KEY_INVALID → "rejected"; ElevenLabs missing_permissions → "key
valid; it lacks user_read (SFX needs only sound_generation)". Add a method field to _test so a
POST probe is representable. 3. Stop swallowing lane failures in the dungeon path:
music_lyria.py:164-165 and sfx_elevenlabs.py:114-127 return False with no record; record status
code + provider status enum (never the key) through the existing step/log machinery, matching what
audio_phases.py:207-212 already does for the platformer. 4. Row reasons: fal → POST
rest.fal.ai/storage/auth/token with Authorization: Key; pixellab/meshy only after their balance
endpoints are verified against live docs; leave retro disabled unless a free credits read is
confirmed. 5. Keep the button only with 1+2 landed; without them it produces false reds (shadowed
keys, restricted keys, billing-denied projects) and unavoidable false greens (tier), and PASS4
Step 0a's "$0 rehearsal" already covers the rest. Also outside this finding: ~/.zshrc line 18
holds an Apple app-specific password in plaintext beside the API keys — worth moving.

**Needs you**

1. In the terminal you run `npm run tauri dev` from, `echo ${GOOGLE_API_KEY:+set}` — it will print
"set" because ~/.zshrc line 8 exports it; is the FRESH Google key you created the one in .env, and
does it belong to the same Google Cloud project as 935155598203 (the project Google refused on a
billing "dunning deny" decision)? Check that project's billing account status. 2. For each row you
clicked Test on, which exact text did you see: "the provider rejected the key", "answered N — the
key reached it…", or "could not reach it"? That splits shadowed/revoked keys (401) from
billing/permission (403) from region/network (0). 3. Did the ElevenLabs key get created with
restricted permissions (sound generation only)? 4. Decision: keep shell env winning over .env and
make the shadowing loud in the Test result [default], or make .env win for dev launches?

<details><summary>evidence</summary>

~/.zshrc:4,8,21 unconditional exports of OPENAI_API_KEY, GOOGLE_API_KEY, ANTHROPIC_API_KEY (file
mtime May 30); sha256 of each differs from canon-ai/.env's value (.env mtime Sep 10 21:28).
cradle/src-tauri/src/lib.rs:141 `std::env::var_os(key).is_none()` drops shadowed .env names;
lib.rs:115-120 keychain overrides; canon-ai/src/canon/cli/main.py:2547 `os.environ.setdefault`
never overrides. ~/.zsh_history: repeated `cd ~/Documents/projects/cradle && npm run tauri dev`;
cradle/src-tauri/target/debug/cradle.d mtime Sep 10 21:04; ~/Library/Application
Support/cradle/provider-keys.json `"vars": []` mtime 21:17.
/Users/wolfgangblack/CradleProjects/pass4_test3_plat_paid/manifest.json slice warning: "music:
theme generation failed for stage 'hollow_roots' (ClientError: 403 PERMISSION_DENIED … 'Lightning
dunning decision is deny for project: projects/935155598203')" written by canon-
ai/src/canon/packs/platformer/audio_phases.py:207-212 via phases.py:63-70; log.jsonl shows the
music step lasting 128 ms (06:22:00.966→01.094), i.e. an immediate refusal.
phase4_test2_paid/generation_stats.json music_attempted 8 / succeeded 0, sfx 4/15, audio_cost_usd
0.16; music_lyria.py:164-165 and sfx_elevenlabs.py:114-127 swallow reasons. providers.py:306-316
GET-only, body discarded; :366-367 401/403 → "rejected the key"; :370-376 other → "answered N".
KeysPane.tsx runTest/testWhy and lib.rs:1691-1695 run `providers test <id>` with no --env-file
(key via apply_provider_env only). lib.rs:1569-1635 provider_keys reports source order
keychain/env/env_file and also_in. .venv: fal_client/client.py:82 REST_URL="https://rest.fal.ai",
:161-172 POST /storage/auth/token, :1676-1685 POST /tokens/; elevenlabs/user/raw_client.py GET
v1/user, core/client_wrapper.py:32 xi-api-key, types/permission_type.py lists user_read and
sound_generation separately; google/genai/_api_client.py:784 x-goog-api-key.
image_retro_diffusion.py:185 reads remaining_balance from the generate response only.

</details>


---

## Music: Google billing is in a "dunning deny" state

**partly-confirmed** · effort **medium**

**Money:** Failed music cost $0 — confirmed. audio_cost_usd 0.16 on both packs = 4 succeeded ElevenLabs
effects × COST_PER_EFFECT ($0.04, sfx_elevenlabs.py:28 ← pricing.py SFX.elevenlabs). Dungeon:
`_add_cost` for music sits inside `if ok` (asset.py:358-360) and Lyria's `_note_cost` runs only
after a returned response (music_lyria.py:147); Platformer: `_add_audio_cost` only after
write_binary (audio_phases.py:206). A 403 at Google's billing gate generates nothing and bills
nothing. But each run spent $2-3 on LLM+images before music was found dead (dungeon $3.00,
platformer $2.60) with no preflight, and any retry repeats that unless the lane is probed first.
Separate misrecord confirmed: pass4_test3_plat_paid/.canon/spend.jsonl and jobs.jsonl say
actual_usd 0 while generation_stats.json says 2.59948; the dungeon's ledger is right (3.001468)
because its manifest embeds generation_stats (manifest.json:87). The platformer manifest has no
generation_stats key, so five cradle readers (ledger writers, recent-tile cost, CostDashboard
genActual) see 0; only WorldBibleView reads generation_stats.json and shows the true $2.60.

**Cause**

One provider failure, two bookkeeping failures, one visibility dead-end. PROVIDER: Google rejected
Lyria generation for the project with 403 PERMISSION_DENIED "Lightning dunning decision is deny
for project: projects/935155598203" (on disk at pass4_test3_plat_paid/manifest.json:336). Forum
threads (Google AI Developers Forum) identify this as Google's billing/dunning gate — declined
charges or an unbilled/denied project — cleared only by fixing billing or a manual review; it is
not the "free-tier" case providers.py:130 describes, and providers.py:131's free GET on
/v1beta/models cannot detect it. DUNGEON (a): 8 real calls, 8 failures, reason discarded.
LyriaMusicBackend() is constructed at run_world.py:104 with no try/except, and google-genai 1.75.0
Client raises ValueError without a key (verified in .venv) — the run finished with
stats.music_backend="lyria" (set only when music is not None, run_world.py:206-208), so a key was
present and `client.aio.models.generate_content` (music_lyria.py:141-148) went out 8 times (5
fixed + 3 env, asset.py:363-381; music/ dir mkdir'd at 22:56 by asset.py:353).
generate_and_save_async swallows every exception into False with no logging
(music_lyria.py:171-177); AssetPhase consumes only the boolean (asset.py:355-360); the dungeon has
no warnings channel (no slice_warnings consumer outside the platformer;
pipeline/phases/manifest.py has none). Same swallow in sfx_elevenlabs.py:117-127 lost 11/15 SFX
equally silently. The 4 ms spread of the 8 node_item lines is announce-before-await under
Semaphore(8) (asset.py:347-354), not evidence of no call. PLATFORMER (b): one real attempt, real
403, counter never incremented. step() announces at audio_phases.py:191 BEFORE generate (192), so
the 128 ms to the next item (log.jsonl:150-151) is the call's round trip. `_add_audio_cost`
(audio_phases.py:57-75) is the ONLY writer of music_attempted and increments attempted and
succeeded together, only after write_binary (206); the except at 207 warns but never touches
stats, so attempted==succeeded by construction. Same coupling in ops.generate_level_music (~2030).
VISIBILITY: warn() lands in manifest.warnings (phases.py:63-69 → compose.py:130-136,327) and
runner stderr; `world new` runs the runner with capture_output=True and discards output on exit 0
(cli/main.py:1073-1078), result["warnings"] carries only argv warnings (1120); cradle reads
job.result only for pack_dir/kept and no non-test cradle code reads manifest.warnings. The user's
only copy was manifest.json.

**Fix**

1. Stop swallowing (both backends, both packs): in AssetPhase._bounded (asset.py:350-360) call
`backend.generate_async` (protocol method, base.py:269) + write the file inside the task and catch
there — per-task, race-free — appending "music: <stem> failed (<Type>: <msg>)" to
ctx.artifacts["slice_warnings"], then give the dungeon ManifestPhase a "warnings" key sourced from
that list (the platformer's compose.py:130-136,327 channel widened to the dungeon). Same for SFX.
Keep generate_and_save_async's bool contract but add logger.warning inside its except
(music_lyria.py:176, sfx_elevenlabs.py:126) for the sync/other callers. 2. Split _add_audio_cost
(audio_phases.py:57-75): `<lane>_attempted` +1 before generate; `<lane>_succeeded` + audio_cost
only after write_binary. Same split in ops.generate_level_music. Safe for the forecast:
estimator._UNIT_ACTUAL_FIELDS (estimator.py:217-221) calibrates on `*_succeeded`, not attempted.
Test: producer that raises → attempted 1, succeeded 0, audio_cost 0, one warning. 3. Surface the
lane: (a) `world new` reads the finished pack's manifest.warnings into result["warnings"]
(cli/main.py:1120) — CLI users see it; (b) cradle must ALSO read it: create path
(startCreate.ts/NewProjectModal.tsx) shows result.warnings on the done card, and WorldBibleView
(already fetches manifest, :91) renders a Warnings section; RecentTile status can OR
manifest.warnings.length into "has warnings". Cradle reads only — no pack writes. (c) optional
steplog `node_item_failed` event with reason for the live tail. 4. Preflight (design fork): there
is no $0 entitlement probe (a free /models GET passes a dunning-denied key; a successful generate
costs $0.04). Run the music lane FIRST — dungeon fixed tracks need no bible; platformer theme
needs only the ~$0.01 stage LLM call — and on 403 either abort before image spend or drop the lane
with a loud warning. Reword providers.py:130 note: "authenticates but a billing-denied project
cannot generate (403 'dunning decision')". Do not embed a planning-row cite in the string. 5.
Ledger: cradle-side only. One helper `readActualCost(dir)` =
manifest.generation_stats.total_cost_usd ?? generation_stats.json.total_cost_usd (the pattern
WorldBibleView.tsx:92 already uses), used by startCreate.ts:271, NewProjectModal.tsx:350,
store.ts:738/867, CostDashboard.tsx:93. Do NOT embed stats in the platformer manifest —
treediff.py:32-39 keeps manifest.json inside the byte-determinism contract.

**Needs you**

Google project 935155598203 (the one behind GOOGLE_API_KEY) is billing-denied, not merely free-
tier: is a billing account linked and in good standing, or has a charge been declined? Per the
forum threads the fix is relinking/fixing billing or asking Google for a manual review of the
dunning decision — until that clears, every Lyria call (create or MusicPanel retry) will 403 and
no code change here can produce music. Separately: the dungeon lost 11/15 ElevenLabs effects in
the same silent way — 5 of them returned within ~160 ms of a 10-wide burst (log.jsonl:102-116),
which smells like the account's concurrency limit; worth a ticket once the swallow is fixed and
the reason is on disk. Also decide fork 4: abort the create on a music 403 or drop the lane and
continue?

<details><summary>evidence</summary>

pass4_test3_plat_paid/manifest.json:336 = "music: theme generation failed for stage 'hollow_roots'
(ClientError: 403 PERMISSION_DENIED. {'error': {'code': 403, 'message': 'Lightning dunning
decision is deny for project: projects/935155598203', 'status': 'PERMISSION_DENIED'}}); the game
stays silent." alongside generation_stats.json music_attempted 0 / music_succeeded 0 — the attempt
happened (log.jsonl:150 announce at 06:22:00.966, next item 128 ms later) but
audio_phases.py:74-75 increments attempted only together with succeeded, on success. Dungeon:
generation_stats.json music_attempted 8 / succeeded 0, music/ dir empty (mkdir'd at 22:56 by
asset.py:353), manifest.json:225 "music": {}, log.jsonl has no error event of any kind, and
music_lyria.py:171-177 `except Exception: return False` is where the 8 reasons died; google-genai
1.75.0 Client raises without a key, so the completed run proves the 8 calls carried one. Both
spend ledgers: dungeon actual_usd 3.001468 (correct), platformer actual_usd 0 vs
generation_stats.json 2.59948 (platformer manifest has no generation_stats key; cradle readers at
startCreate.ts:271, NewProjectModal.tsx:350, store.ts:738/867, CostDashboard.tsx:93 all read the
manifest). Forum sources on the 403: https://discuss.ai.google.dev/t/http-403permission-denied-
lightning-dunning-decision-is-deny-for-project-projects-1104186197/170077 and
https://discuss.ai.google.dev/t/403-permission-denied-lightning-dunning-decision-is-deny/182148.

</details>


---

## Improve DID call Claude — under the other key, with every failure collapsed to one message

**partly-confirmed** · effort **medium**

**Money:** Silently unrecorded and mis-attributed spend, not an overcharge. The improve completed one real
claude-sonnet-5 call (the only way the copy the user saw can be produced): 428-char system +
4,339-char user ≈ 1.3k input tokens at $2/1M plus ≤4,096 output tokens (thinking included) at
$10/1M → ≤ ~$0.05. The SDK does not retry once a stream has started, so at most one generation
billed. canon returns cost.usd None and cradle never calls recordSpend from ImproveDialogue, so
phase4_test2_paid/.canon/spend.jsonl still holds only the $3.00 world row, no journal/jobs/log row
mentions the improve, and no pack file was modified after 22:58 — the daily figure and any cap
cannot see it. Attribution: because cradle's process env (from ~/.zshrc) outranks canon-ai/.env,
the improve AND the world run's $1.16 of LLM calls billed to the .zshrc key (…0wAA), not the .env
key (…DAAA); the user should look for both under that key. "not estimated" on the card is a gap
(no estimator row), not an error.

**Cause**

Three independent defects, none a lock or a network hang. (1) canon improve.py:99-163
`_provider_rows`: the paid path makes ONE real `messages.stream` round trip (no pre-check exists;
improve.py:203-216 sends every non-{none,fake,""} id to resolve_chat_backend →
AnthropicChatBackend.stream, chat_anthropic.py:226), then discards
`response.stop_reason`/`stop_details`, swallows JSON-parse failure into `[]` (127-130), silently
drops every row whose tree/node_id/choice does not match the payload or whose after==before
(133-152), sends ChatRequest default thinking=True (chat.py:95 → `thinking:{"type":"adaptive"}`)
sharing a 4096 max_tokens budget, and prices nothing (cost.usd None). So a genuine `[]`, a
max_tokens truncation, a thinking-only reply, a refusal, a refusal-fallback, a mis-keyed but well-
formed list, an object wrapper, or a trailing-comma reply ALL come back as rows=0 with the
identical note "LLM re-author on anthropic — a paid run, and still only a proposal"
(improve.py:216); the only proof of the call (gen.input_tokens/output_tokens) is never rendered by
the modal. Cradle's static empty-state copy (ImproveDialogue.tsx:404-408, "already clean by this
backend's rules") then asserts a reason that is only true for the deterministic pass. (2) cradle
runs the paid improve as a synchronous #[tauri::command] (lib.rs:3062-3104 → run_canon_owned 1522
→ run_canon 733-747 `Command::output()`), which tauri-macros 2.5.5 compiles as body_blocking
(wrapper.rs:228-232, 359-392) and which Tauri dispatches inline from the WKWebView scheme handler
on the macOS main thread (wry-0.54.4 url_scheme_handler.rs:57-95 → tauri-2.10.3
ipc/protocol.rs:38-77 → webview/mod.rs:1888 run_invoke_handler). For the whole provider round trip
the app's main thread is blocked: no click reaches the webview (Cancel at :501 is never disabled,
it simply cannot be delivered), no other invoke runs, the only affordance is the label "Asking…"
(:515), no spinner/elapsed/tray/Stop/timeout (cradle sets none; SDK 0.98.1 _constants.py:9-10
gives 600s read timeout × up to 3 attempts). The create wizard instead goes through
enqueue/enqueue_watching (lib.rs:1703-1740) → run_job_worker on its own thread (1775-1782) → job-
updated/job-progress → start/CreateProgress.tsx + JobTray.tsx. db_new --complete / db_complete
(2207-2266) share the blocking shape. (3) Key provenance: cradle's effective key precedence is
keychain > cradle's own process env > env file (lib.rs:113-121, 141-143), and this machine's
~/.zshrc exports a different ANTHROPIC_API_KEY than canon-ai/.env, so the call billed to the
.zshrc key while the UI copy names CANON_ENV_FILE as the source — the most plausible reason "no
request landed" in the console the user checked. The ~5 minutes is unrecoverable: the $0 child
runs in 0.16s wall (measured), so essentially all of it was the provider round trip plus any SDK
retries; nothing in either repo records the duration.

**Fix**

canon improve.py `_provider_rows`: read response.stop_reason/stop_details and return `outcome` ∈
{proposed, no_changes, truncated, refused, unparseable, clamped} plus gen.stop_reason, gen.parsed,
gen.dropped (count of rows clamped away) beside the token counts; treat anything but a parsed list
on end_turn as a named non-answer, never `[]`; raise max_tokens (e.g. 16384 — the call already
streams) and pass effort="low" (NOT thinking=False, a no-op on Sonnet 5); optionally use
output_config.format for the array; price cost.usd in canon from Usage via
pricing.llm(response.model) (pricing.py:356; input/output/cache fields) so cost stops being None.
Do NOT write any ledger from improve.py (its contract is a pure read). Replace :216 with f"LLM re-
author with {backend_id}" and put the outcome in its own field. Add a `dialogue improve` estimator
row (price_llm, estimator.py:295) so the card shows a range instead of "not estimated". cradle
lib.rs: route dialogue_improve (and db_new --complete / db_complete) through the existing queue —
enqueue_watching with a job id, worker thread, job-updated queued/running/done carrying the
result, tray row, Stop via cancel_job (lib.rs:1939-1975 writes the cancel file then watcher.kill
after CANCEL_GRACE, so Stop works by kill even though `dialogue improve` never polls
CANON_CANCEL_FILE); note the queue is serial. Minimal stopgap if the queue routing waits:
`#[tauri::command(async)]` (wrapper.rs:241 sync_threadpool) to unblock the main thread — no
progress or cancel, so not the fix. ImproveDialogue.tsx: while running show the CreateProgress-
style live state (provider, elapsed clock, Stop); after the result render gen (model, in/out
tokens, outcome) and call recordSpend(worldPath, {op:"dialogue_improve", scope,
backends:{llm:backend}, actual_usd: r.cost.usd, tokens, actor, category:"generation"}) — the same
path the create run uses; replace :404-408 with outcome copy ("{provider} returned no changes · N
in / M out tokens", "reply cut off at the token limit — nothing usable came back", "{provider}
refused", "reply was not a proposal list", "{provider} proposed N rows that did not match this
tree") and keep "already clean" for none/fake only; fix :354 to state the EFFECTIVE key source.
Key precedence (decision for the user): either make the env file win for names it carries (drop
the `var_os(key).is_none()` skip in env_file_pairs — a behaviour change) or keep shell-wins and
have Settings → Environment show which source supplies each name. SpendGate (ConfirmGate.tsx):
render "—" until spendList resolves and surface a failed read instead of $0. Tests: canon
scripted-backend case per outcome including the clamped-away list, asserting a priced cost.usd;
cradle test past the card asserting the job-queue invoke, recordSpend, and rendered usage; Rust
unit test on apply_provider_env_from documenting shell-vs-file precedence.

**Needs you**

Which key/workspace did you check in the Anthropic console — the one in canon-ai/.env (suffix
…DAAA) or the one ~/.zshrc line 21 exports (suffix …0wAA)? Cradle launched from your terminal ran
every Anthropic call under the .zshrc key; please check that key's usage for a ~1.3k-in/≤4k-out
claude-sonnet-5 request at the improve time and for the 83 world-run calls. Two smaller ones:
which tree was open when you clicked Propose (incomplete/complete/failed/default), and roughly
what time you clicked Accept — nothing recorded the duration, so those bound it. And a decision:
should the env file win over your shell for names it carries (behaviour change in env_file_pairs),
or should cradle keep shell-wins and just show the effective source?

<details><summary>evidence</summary>

canon: improve.py:203-216 (FREE_BACKENDS branch vs real provider; note built at :216 only after
_provider_rows returns), :99-163 (stop_reason never read; :127-130 parse failure → []; :133-152
silent clamp; :124 max_tokens=4096; thinking not overridden → chat.py:95 default True), :6-7 (no-
write contract); backends/chat_anthropic.py:178-179,99 (adaptive thinking sent), :226
(messages.stream), :254-266 (MessageStop only on SDK message_stop), :325-331 (credential TypeError
→ ChatError); llm/chat.py:281-302 (collect raises without MessageStop); cli/main.py:57-60,
3972-4004 (_emit_error exit 1; _load_env_file setdefault); .venv anthropic 0.98.1
_constants.py:9-10 (600s/connect 5s/2 retries); pricing.py:103, 356; estimator.py:295 (no improve
row); tests/test_dialogue.py:877-910 (only scripted clamp test). Scratchpad repro on a COPY
(adv_pack, $0 scripted backend): cases A–L all rows=0 with identical note, stop_reason absent from
payload, npcs.json hash unchanged; fenced/prose-wrapped good JSON parses (rows=1); `canon dialogue
improve --backend none` wall 0.16s; `canon spend list` on the copy returns the single $3.00 world
entry ts 2026-09-11T05:58:32+00:00. cradle: src-tauri/src/lib.rs:3062-3104 (sync #[tauri::command]
dialogue_improve → run_canon_owned :1522 → run_canon :733-747 Command::output()), :1692/4075/4113
(the only async fns), :1703-1740 enqueue/enqueue_watching, :1775-1782 run_job_worker "off the UI
thread", :1939-1975 cancel_job (cancel file then kill), :104-121 apply_provider_env_from, :125-147
env_file_pairs skips names already in cradle's env, :1500-1520 env_file_path/with_env_file,
:2207-2266 db_new/db_complete same shape; keys.rs read_indexed (empty index → keychain untouched);
~/Library/Application Support/cradle/provider-keys.json "vars": []; ~/.zshrc:21 exports
ANTHROPIC_API_KEY (sha256 30942c23de21, …0wAA) ≠ canon-ai/.env ANTHROPIC_API_KEY (sha256
81f4bf5e41dc, …DAAA); ImproveDialogue.tsx:92-93,161-214 (busy flag only), :348 "not estimated",
:354 "Key read from CANON_ENV_FILE", :404-408 static empty-state copy, :501 Cancel never disabled,
:515 "Asking…", no `gen`/recordSpend reference; invoke.ts:1073-1090 (gen typed, never rendered),
:1713-1722; cost.ts:33 recordSpend, callers NewProjectModal.tsx:357 / startCreate.ts:275 /
jobs.ts:206 only; ConfirmGate.tsx:66-93 (today ?? 0 → "$0 spent today" before/without spendList);
PaidCard.tsx:61; improve.test.tsx:266-284 (stops at the card). Tauri: tauri-macros-2.5.5
wrapper.rs:228-232 (Blocking → body_blocking), :241 (command(async) → sync_threadpool), :359-392
(inline call + kind.block); tauri-2.10.3 ipc/protocol.rs:38-77 (custom-protocol handler →
webview.on_message inline), :185 (postMessage path), manager/webview.rs:501, webview/mod.rs:1888
(run_invoke_handler synchronous); wry-0.54.4 wkwebview/class/url_scheme_handler.rs:57-95
(start_task invokes the handler inline on the WebKit main thread). Pack (read-only):
phase4_test2_paid/.canon/{spend,jobs,journal,log}.jsonl all stamp the 05:33–05:58Z world run; no
improve row anywhere; no file newer than 22:58 local; npcs.json carries only legacy dialogue_tree*
fields (4/4/10/8 nodes). docs/test_plans/PASS4_paid_runs.md:31 (CANON_BIN launch), :180-190 (Leg
C(paid) steps; "not estimated" expected).

</details>


---

## Platformer spend recorded $0 for a $2.60 run

**confirmed** · effort **small**

**Money:** Silent under-report; no overcharge. Verified on disk: pass4_test3_plat_paid/generation_stats.json
total_cost_usd 2.5994800000000002 (llm 0.41148 over 52 calls, image 2.028 over 52 fal images,
audio 0.16 over 4 elevenlabs sfx) versus .canon/spend.jsonl and .canon/jobs.jsonl actual_usd 0
(estimate best 2.6656). `canon spend list` on a fresh scratch copy: count 1, total_actual_usd 0.0
(dungeon copy: 3.001468, matching its stats). Journals carry no costed events in either pack, so
the spend row is the only ledger — and it says $0. Downstream: the agent system prompt's "Spend to
date" (prompt.py L183) reads $0.00; the cost dashboard's legacy total (CostDashboard.tsx L105-110,
Σ actual_usd of rows without journal_ref) is $0 and its generation tile (L93) never populates; the
start-page world card and recents show no cost (store.ts L738/L867). Affects every platformer
create via the wizard, the start-page agent create, and the canon agent's create_project tool —
the default template. The dungeon is correct only because its manifest embeds the block.
Additionally the 2.59948 itself excludes the 4 anthropic VLM QA calls (log.jsonl plat:vlm_qa items
l1, l1r1, l2, l2r1, 06:22:09→06:23:16); no token or cost trace of those calls exists anywhere in
the pack (grep of the tree found only generation_stats.json), so the real spend is somewhat above
$2.60 and cannot be reconstructed from disk. Same $0 outcome would also occur for any cancelled
create of either template.

**Cause**

The platformer create's actual cost is computed and written — to `<pack>/generation_stats.json`
(compose.py L391-394; on disk: total_cost_usd 2.5994800000000002) — but every cradle/canon reader
of a create's actual looks for an EMBEDDED `manifest.generation_stats.total_cost_usd` block:
NewProjectModal.tsx L344-350, startCreate.ts L266-273, tools_paid.py L716-717, plus store.ts
L738/L867 and CostDashboard.tsx L93. That embedded block is a dungeon/mazeworld manifest
convention (pipeline/phases/manifest.py L271, since canon 83f428f 2026-06-02); the reader was
written against it in cradle 9d2300e (2026-07-31) when the dungeon was the only create template;
the platformer's manifest writer (compose.py L329) never emits it and its standalone stats file
(fce0749, 2026-09-03) was never wired to the settle. `canon world new`'s result document
(cli/main.py L1105-1114) carries no cost, and the Rust new_project (lib.rs L1054-1145, result =
whole stdout JSON at L1922-1925) is a pass-through, so nothing else could have supplied the
number. The `?? 0` / try-catch / `or 0.0` at all sites turn the miss into a measured-looking $0
rather than "unknown". The dungeon records correctly only because its manifest carries the block.
The stats file both templates DO write is the location canon itself declares canonical (config.py
L45) and already reads (estimator.py L174-183).

**Fix**

Fork (default = 1), amended from the diagnosis:
1. [default] Two parts, both extending existing readers.    (a) canon cli/main.py world_new
(~L1105): after the runner exits, set `result["actual_usd"]` (and llm/image/audio split) from
`estimator._generation_stats(output_dir)["total_cost_usd"]` — reuse that helper (promote it or
import it), do not add another json.loads. When the file is absent (cancelled create, crashed last
phase), OMIT the key and append a warning — never emit 0. The three settle readers
(NewProjectModal.tsx ~L344, startCreate.ts ~L266, tools_paid.py _create_spend ~L716) prefer
`job.result.actual_usd`; when absent they omit `actual_usd` from the spend/jobs row (spend.py
summarize L176-177 already tolerates an absent key) and surface the warning, so an unmeasured row
is distinguishable from a measured $0. Also fix the tools_paid.py L707-709 docstring ("already
journalled its per-step money") — it is false for creates.    (b) The two on-disk readers the
diagnosis missed — store.ts L728-739 and L857-868, CostDashboard.tsx L91-96 — must read
`readWorldJson(path, "generation_stats")` (the call WorldBibleView.tsx L92 already makes; data.rs
L552-561 resolves it for both layouts) with `manifest.generation_stats` kept only as fallback.
Without (b) the start-page card and dashboard tile stay blank for every platformer pack even after
(a).    Tests: canon test_create_flow — for BOTH templates assert `result.actual_usd ==
json.load(generation_stats.json)["total_cost_usd"]` (fake backends give 0, so assert key presence
+ equality) and one cancelled-create case asserting the key is absent and a warning present;
cradle — a platformer-shaped fixture (manifest `{}`, `generation_stats.json` with a nonzero
figure, result.actual_usd) asserting the spend row and the card cost are nonzero; and a fixture
with neither asserting no `actual_usd` key.
2. [smallest] Fallback-only: at all SIX reader sites, when `manifest.generation_stats` is absent
read `generation_stats.json` (cradle via the existing readWorldJson name form; canon via
estimator._generation_stats). Fewer moving parts, but leaves the read duplicated six ways and the
drift that caused this uncaught.
DO NOT embed the stats block in the platformer manifest — but note the real reason: the platformer
determinism tests run in-process without --orchestrate and would likely pass; real creates run the
ThreadPoolExecutor orchestrator (orchestrator.py L307) where by_phase insertion order is
completion order, so manifest.json would become non-reproducible in the field (the wall-clock
class test_dungeon_portraits.py fixed). The standalone, treediff-exempt file is the right carrier
for both templates.
Rules check: cradle still writes no pack files (recordSpend → api.spendRecord → canon, cost.ts
L33-37); ids untouched; nothing regenerates; no doc citations in strings.
Separately (adjacent, same class): pass `stats=` to the VLM judge in run_slice.py L1298 (and
dag.py L713) and fold `last_cost` after each call — GenerationStats has no vlm field and vlm_qa.py
never touches ctx.stats, so VLM spend never enters generation_stats at all.
Backfill for the paid pack: the true figure is on disk in its generation_stats.json; append
corrected rows via `canon spend record` / `canon jobs record` only on the user's say-so. I did not
touch either pack.

**Needs you**

1. Backfill the platformer pack's ledgers with the on-disk 2.59948 (append-only via `canon spend
record` / `canon jobs record`, marked accuracy "measured" but noting the VLM calls are not
included)? — default: yes, on your word only. 2. Fix fork: (1) result-document actual + fix the
two on-disk readers (default) or (2) fallback reads at all six sites? 3. Should the cancel-path
gap (stopped creates record $0 for both templates because stats are written by the last phase)
ride this ticket, or be its own? 4. Should the VLM stats fold (run_slice.py L1298 / dag.py L713)
be in scope now, given VLM spend on this run is unrecoverable either way?

<details><summary>evidence</summary>

ON DISK (user's packs, read only; CLI runs on fresh scratch copies): pass4_test3_plat_paid —
spend.jsonl actual_usd 0, jobs.jsonl actual_usd 0 (status no_change, 919303 ms),
generation_stats.json total_cost_usd 2.5994800000000002, manifest.json keys have no
generation_stats and no "cost" substring, journal 12 events 0 costed (9 `generate` events carry
only actor/artifact_id/identity/op/schema/source/ts), log.jsonl run_end scheduler "orchestrated",
plat:vlm_qa 4 items. phase4_test2_paid — spend/jobs actual_usd 3.001467999999999 =
generation_stats.json = manifest.json.generation_stats.total_cost_usd, log.jsonl scheduler
"sequential", journal 4 events 0 costed. `canon spend list`: plat count 1 / total_actual_usd 0.0;
dungeon 3.001468.
CODE — readers of manifest.generation_stats: cradle NewProjectModal.tsx L344-350 (+ recordSpend
L357-363, recordJob L364-375); startCreate.ts L266-273; store.ts L728-739, L857-868;
CostDashboard.tsx L91-96; canon tools_paid.py L716-719 (docstring L707-709 wrongly claims per-step
journaling). Existing standalone-file readers: canon estimator.py L174-183 `_generation_stats`;
cradle WorldBibleView.tsx L92 `readWorldJson(path,"generation_stats")`; data.rs L552-561
name→".json" + data_root. Writers: manifest.py L271 embed + L277-279 standalone (dungeon);
compose.py L329 manifest without stats, L391-394 standalone only (platformer); config.py L45
canonical path. No cost on the create result: cli/main.py L1105-1114; lib.rs new_project
L1054-1145, result = parsed stdout L1922-1925. Determinism: treediff.py L31-35 exempts
generation_stats.json; test_dungeon_portraits.py compares dungeon manifest.json byte-identical;
test_multistage.py L30-46 / test_platformer_slice.py L1591 paired runs carry no --orchestrate;
spec.py RUNNER "orchestrate": "--orchestrate"; orchestrator.py L307 ThreadPoolExecutor; stats.py
L98 by_phase insertion. VLM: run_slice.py L1204-1207 LLMClient(stats=stats) vs L1298
build_vlm_judge(no stats); dag.py L713 same; vlm_anthropic.py L90-96 computes last_cost; stats.py
has no vlm field; vlm_qa.py never touches ctx.stats. Tests: NewProjectModal.test.tsx L82
readWorldJson→{}; CreateDetach.test.tsx L90 dungeon-shaped fixture, L218 asserts 0.42;
StartAgentPanel.test.tsx L101 total_cost_usd 0. Git: canon 83f428f 2026-06-02 (embed), fce0749
2026-09-03 (platformer standalone stats); cradle 9d2300e 2026-07-31 (reader). Rule check: cost.ts
L33-37 recordSpend → api.spendRecord (canon writes the pack, cradle never does).

</details>


---

## 26 asset failures, three different causes, all silenced

**confirmed** · effort **medium**

**Money:** Dungeon ledger is honest: $3.0015 = LLM $1.1645 + 43 x $0.039 + 4 x $0.04, and the estimate's best
$3.9952 minus actual = $0.99 ~= the 26 failed units at list ($1.03) — the estimator was right and
the shortfall IS the failures. Repair of what is repairable costs $0.71 (7 images $0.273 + 11 sfx
$0.44, two of which need the 0.4->0.5 s fix first); music's $0.32 is blocked by the Google billing
hold, not by canon. The only route today is a fresh create (~$3-4) that re-rolls the world. Leaks
in the same pack: $0.117 double-paid on three name-collided portraits; ~$0.70 of art bought with
prompts that discarded the LLM's portrait_prompt ("a quest: quest_N", "a event: <name>"). Whether
fal/ElevenLabs billed the failed calls is unknowable (responses destroyed); Google bills nothing
on a 403. NEW and not in the diagnosis: the platformer control run the same night is recorded as
$0 in spend.jsonl, jobs.jsonl and every cradle cost surface against a real $2.60, because cradle
reads manifest.generation_stats and the platformer manifest has none.

**Cause**

Three stacked layers of by-design silence, confirmed: (1) every paid asset backend's
`generate_and_save_async` is `except Exception: return False` with no log and no stored error
(image_fal.py:225-234, sfx_elevenlabs.py:117-127, music_lyria.py:167-177; tests pin the boolean at
test_backend_fal.py:356-364/420-428, test_backend_elevenlabs.py:175-184,
test_backend_lyria.py:268-276/350-357); (2) AssetPhase turns False into a counter only
(asset.py:285-297, 355-360, 404-409), emits no StepLog event, and the dungeon has no
warn()->manifest conduit (grep slice_warnings in packs/dungeon: none), so runner.py:134 `run_end
ok:true` ("no phase raised") is literally true; (3) cradle reads generation_stats only for
total_cost_usd and auto-opens on ok/no_change (NewProjectModal.tsx:331-372), the fold drops
unknown events (jobs.ts:367), and no surface renders counts (WorldBibleView.tsx:61 types
images_succeeded, 287-307 never renders it) or manifest warnings.
What the corrected evidence adds: the three lanes failed for three DIFFERENT, partly deterministic
reasons that one "transient, add backoff" story hides. Music: Google billing hold (403
PERMISSION_DENIED "dunning decision is deny", recorded verbatim by the platformer's conduit) —
user-side, unfixable in code. SFX: at least two catalog entries violate ElevenLabs' 0.5 s floor
(asset.py:414, 421) and can never succeed; the remaining nine most plausibly hit a plan
concurrency cap under a 10-wide burst (asset.py:165) with the SDK's retries left at 0. Images:
seven failures that survived fal's own 10-attempt retry, so non-retryable (content policy /
validation / app 500 / empty result), on prompts that ignore the row's LLM-authored
`portrait_prompt`. The proximate root cause is still layer (1): because the exception is destroyed
at that line, none of these three classes can be told apart on disk, the user cannot know that
music is a billing problem rather than a canon problem, and no repair verb can target what is
missing.

**Fix**

Extend existing machinery; per lane, not one generic retry.
CANON (a) Backends keep the boolean contract but `logger.warning(...)` and stash `self.last_error
= f"{type(e).__name__}: {e}"` beside `last_cost` (3 x ~4 lines; existing `is False` tests pass;
add one asserting last_error). (b) Lift `warn(ctx, ...)` from packs/platformer/phases.py:63 to
pipeline/steplog.py the way P0-10 lifted `step`. In the three `_bounded` closures on `not ok`:
`warn(ctx, ...)` + `steplog.emit("asset_failed", node, item, kind, target, path,
error=backend.last_error)` through the one emitter. Base ManifestPhase (manifest.py:236-274)
writes `warnings` from ctx.artifacts the way compose.py:130-146 does; run_world.py prints the `!!
N generation warning(s)` block like run_slice.py:1324-1328; `world new`'s result doc
(cli/main.py:1111-1120) gains `changed: true` and `assets: {images|sfx|music: {attempted,
succeeded, failed: [{target, error}]}}` from ctx.stats. (c) Retry/limits per lane using the SDKs'
own knobs: ElevenLabs — pass `request_options={"max_retries": 2}` (the SDK already backs off on
429/5xx and honors Retry-After, http_client.py:99-123), lower `sfx_concurrency` to the plan's
concurrency, and fix the catalog floor 0.4 -> 0.5 at asset.py:414/421 (or clamp with
SFX_MIN_SECONDS like audio_phases.py:50); fal — the SDK already retries 429/ingress x10, so what
is missing is the shared content-policy sanitized re-prompt that tileset_art.py:1072-1085 already
implements (lift it into the fal backend or AssetPhase) plus honoring the row's `portrait_prompt`:
add `portrait_prompt=entity_dict_for_stub.get("portrait_prompt")` to the stub
(database.py:401-416; EntityLore already has `extra`) and `prompt = portrait_prompt or entity.lore
or f"a {type}: {name}"` at asset.py:308; Lyria — nothing to retry, just surface the 403 text. Drop
the estimator worst_retries item. (d) Repair verb, widening `asset generate` rather than a new
verb: cli/main.py:3354 `_pack_ops()` -> `_pack_ops(pack_dir)` (already dispatches non-platformer
packs to db_ops); add `db_ops.generate_asset(pack, target, ...)` that resolves `target` through
the spec's declared `asset.targets` (spec.py:104/124/150/185/216/239/293/309:
npc|monster|item|quest|event:<id>, class:<archetype>, music:<track_id>, sfx:<sfx_id>), runs one
AssetPhase `_bounded`, stamps via `stamp_collection` and commits with `commit_document(op="edit",
source="repair")` exactly as backfill_portraits does (portraits.py:224-240). Add `--target
missing`: the backfill walk already computes `no_file_on_disk` (portraits.py:262-268) for images;
sfx/music come from catalog (asset.py:412-443, 369-380) minus the manifest's disk-scanned
`music`/`sfx` index (manifest.py:208-211). One estimate, one money card, N targets. Here: 7 images
+ 11 sfx = $0.71; music excluded until billing clears. (e) Name-collided rows: give monster/item
`"dedup": ["name"]` like npc (spec.py:103) or key the file by id, so two rows never buy one file.
CRADLE (writes no pack files; nothing regenerates on its own) (f) jobs.ts fold: `case
"asset_failed"` -> progress.failedAssets[]. CreateProgress after "Finished": "N of M assets did
not generate" with the per-lane reasons (the Lyria text tells the user it is billing).
NewProjectModal: when failedAssets > 0 or result.warnings, stay on the summary with the existing
"Open anyway" button (571-586) instead of auto-opening. JobTray StatusBadge gains "partial"; read
`changed` from the result doc. WorldBibleView renders the counts it already types. RecentTile "has
warnings" also from `manifest.warnings` (today the platformer's 24 warnings are invisible). "Retry
missing ($x.xx)" through the existing confirmSpend (confirmGateState.ts:101) -> `canon asset
generate <pack> --target missing`; cradle has no asset-generate invoke today (invoke.ts/lib.rs
grep: none), so this is one new command wire. (g) Separate money bug: read actual cost from
`generation_stats.json` (WorldBibleView.tsx:92 already does) instead of
`manifest.generation_stats` in NewProjectModal.tsx:347-350, startCreate.ts:268-271,
store.ts:738/867, CostDashboard.tsx:93 — or make the platformer manifest carry generation_stats.
Today a platformer create is ledgered at $0.

**Needs you**

1) Google Cloud project 935155598203 returned "Lightning dunning decision is deny" (403) for Lyria
on both runs — is billing on that project delinquent or suspended? Until it is cleared no music
will generate on either template and no code change helps. 2) Which ElevenLabs plan is the key on
(concurrency 2/3/5/10)? That sets the right sfx_concurrency and confirms or refutes the
concurrency-cap reading of the 11 SFX losses. 3) Repair route: widen `asset generate` with dungeon
targets + `--target missing` (my default, one-line dispatch fix + db_ops.generate_asset) or the
diagnosis's new `world assets --missing` verb?

<details><summary>evidence</summary>

- /Users/wolfgangblack/CradleProjects/phase4_test2_paid: generation_stats.json image 50/43, sfx
15/4, music 8/0, total_cost_usd 3.0015; null profile_image on npcs 1000/1002, monster 5004, quests
4000/4002, events 3000/3010; all 47 referenced asset paths exist; sfx/ 4 mp3; music/ empty;
manifest.json `music: {}` and `sfx` 4 entries (disk scan, manifest.py:208-211); validation_report
passed with 0 warnings; .canon/log.jsonl 169 rows, only
run_start/node_start/node_item/node_done/run_end (the single grep hit is "room_2 · event 4");
jobs.jsonl `changed: true, status: "no_change"`. -
/Users/wolfgangblack/CradleProjects/pass4_test3_plat_paid/manifest.json warnings[3]: "music: theme
generation failed ... 403 PERMISSION_DENIED ... Lightning dunning decision is deny for project:
projects/935155598203"; its spend.jsonl/jobs.jsonl `actual_usd: 0` vs generation_stats.json
2.59948; manifest has no generation_stats key. - Backends: image_fal.py:225-234,
sfx_elevenlabs.py:117-127, music_lyria.py:167-177 `except Exception: return False`;
image_fal.py:206 sync urllib download in the async path; asset.py:165 sfx_concurrency=10,
:275-297/:350-360/:395-409 counters only, :308 entity prompt ignores portrait_prompt (only
:301/:316 use it), :414/:421 duration 0.4 s. - SDKs (.venv): elevenlabs 2.45.0 http_client.py:300
max_retries default 0, :121-123 retryable 429/408/409/5xx, text_to_sound_effects/client.py:55 "at
least 0.5 and at most 30", loop v2-only; fal_client 1.0.0 client.py:1033-1037 MAX_ATTEMPTS=10
RETRY_CODES [408,409,429] + ingress, :1148-1170/:1613/:1782 retry wrappers on get/submit; google-
genai _api_client.py:514-515 never-retry default. - database.py:63 entity_index per map, :401-416
stub name `or f"{type}_{index}"` and lore fallback; parsers.py:197/290/434/542/746 profile_image
None; spec.py:103 npc-only dedup, :104-309 asset.targets declared for every kind. -
estimator.py:306 (LLM) vs :473-476 (image, measured path only); cost_model.json note: actuals not
wired for the dungeon; retry_with_feedback used in database.py:355 and 7 other dungeon phases. -
runner.py:134; run_world.py:114-133 backfill $0; portraits.py:151-173 stamp recorded only,
:206-285 backfill with `no_file_on_disk`; cli/main.py:1076 capture_output, :1111-1120 result doc
without `changed`, :3354 `_pack_ops()` without pack_dir; platformer ops.py:2083-2092 rejects non-
platformer targets; db_ops has no generate_asset. - Cradle: jobs.ts:60 terminalStatus, :333-368
fold `default: return`; CreateProgress.tsx:78-99 "Finished"; NewProjectModal.tsx:331-372 auto-
open, :372 changed:true, :571-586 Open anyway; CreateRunCard.tsx:96-108; JobTray.tsx:323-337;
WorldBibleView.tsx:61/:92/:287-307; RecentTile.tsx:53-60 + store.ts:895-909 validation-only;
Portrait.tsx:44-49; EntityTable.tsx:229-236; lib.rs:1894/:1911-1918 stderr only on non-zero exit;
no reader of manifest.warnings; no assetGenerate invoke; result.warnings read only in
EntityOverview.tsx:1123, RowEditor.tsx:352, LevelDetail.tsx:646.

</details>


---

## "Animation = Claude" is right, and ≥14 vision calls are unmetered

**partly-confirmed** · effort **medium**

**Money:** Yes, and larger than the diagnosis states. Two independent leaks: (1) Every Claude vision call —
≥14 in this run (5 motion specs + 5 sheet verdicts + 4 level judgments, plus up to 3 feedback
retries each) — is unrecorded everywhere: generation_stats.json, the animate op's journal cost
(`_cost_block` never sees the judge), cradle's ledger, and any calibration (no vlm row). ≥$0.13 at
cost_model token rates for this pack, scaling with actors × levels; real image-token usage is
unmeasured. (2) Cradle's per-project ledger records actual_usd 0 for EVERY platformer world run
(manifest.generation_stats is dungeon-only), so CostDashboard totals and ConfirmGate's today-spend
miss the entire $2.60 here, not just the VLM slice. Estimate quality: 54 quoted vs 50 nominal (+2
retries) is a coincidental near-miss; the vlm=none case quotes 54 images for a run that makes 26
(over-quotes $1.09); level judgments quoted 2 vs 4 made. The "anim" chip understates true
animation cost ~10× ($0.108 shown vs $0.936 of fal sheets + vision calls). Aside: music quoted
$0.080 but Lyria 403'd (manifest warning 3, music_attempted 0), so the user paid less than quoted
there.

**Cause**

(1) Label: the "Animation" select IS the vlm lane (NewProjectModal.tsx:643-647), whose
options/labels are TypeScript literals because canon publishes no per-lane option table (`pack
templates` → generators lane names only; `providers list` → paid id + key var + provider label
"Anthropic"). Canon itself names the lane two ways: `world new --vlm-backend` help says "Animation
authoring" (cli/main.py:984) while run_slice.py:1122-1128 says "Vision judge for the end-of-
pipeline QA loop"; cradle copied the first in the wizard and the second ("Vision backend") in
AnimateModal.tsx:352. "Claude" is the right model (build_vlm_judge vlm_qa.py:1715-1737 →
AnthropicVLMBackend, model claude-sonnet-4-6, pricing.py:329) and it genuinely runs three tasks
here: motion-spec authoring per actor (art_phases.py:986 → vlm_qa.py:1033, one judge call + up to
3 feedback retries), animation-sheet verdicts per actor (vlm_qa.py:1526-1533;
review/animation_qa.json vlm_model claude-sonnet-4-6, 5 actors) and level-render judgments
(VlmQaPhase vlm_qa.py:1745; qa_report.json l1, l1r1, l2, l2r1; log node_item vlm_qa 4/4). But
Claude never draws a frame: each state sheet is ONE fal img2img call metered to the image lane
(art_phases.py:1038-1070), so 24 of the 52 billed images (18 enemy + 6 player sheets, $0.936 of
$2.028) are animation hidden under "images 54×", while the "anim $0.108" chip
(NewProjectModal.tsx:732-734 = est.assets.vlm.usd.best) shows only the vision calls — and 25% of
that (2 × $0.0135 level judgments) is art QA, not animation. Corollary: the "placeholder ($0)" vlm
option only zeroes the vision lane; with image=fal the phase still runs (art_phases.py:895-909
gates on ImageEditBackend + judge is not None, and FakeVLMBackend passes) and bills ~24 fal edits.
(2) 52 vs 54: not a failure and not the animation setting. cost_model.json `assets` knobs restate
runner facts that changed: images_per_stage 18 assumes an "exit" prop (removed,
art_phases.py:89-92; PROP_SPECS has 4) and 3 backdrop bands (Graphics.backdrop_bands default 2,
graphics.py:98; foreground_band off; backdrop/hollow_roots has band_0/band_1); images_per_enemy 6
assumes 5 states for all (DEFAULT_SETS enemy=4, hopper=5, animation_spec.py:124-128; on disk
hopper 5, wanderer 4, strider 4, lurker 5 = 18). Runner nominal = 10 tiles (palette_drift.json 10
keys; one generation per tile NAME, art_phases.py:247-263) + 2 bands + 4 props + 9 bases (log
sprite_art 13 = 4+4+1+4 props) + 18 + 6 + 1 world = 50; generation_stats image_successes =
image_attempts = 52 = 50 + 2 metered retries (drift retry art_phases.py:1108-1112 and alpha-gate
retries tileset_art.py:1118-1121 both go through `meter`; attempts floors to successes,
art_phases.py:145-149); 2.028/52 = $0.039 = nano-banana per call. Reproduced read-only: `canon
world estimate --template platformer --stages 1 --levels 2 --enemies 4 --items 4 …` = {2.6656,
3.3004}, byte-equal to spend.jsonl; images 54 = 18 + 4×6 + 4 + 7 + 1. With --vlm-backend none the
estimate STILL says 54 images ($2.106) though the runner would make 26 — the estimator
(estimate.py:153-159) counts sheets whenever the sprite nodes are planned. Level judgments quoted
2 (capped at num_levels, estimate.py:162-175) vs 4 made. (3) Money: GenerationStats
(pipeline/stats.py:25-47) has no vlm field; no code drains judge.last_cost (vlm_anthropic.py:88-98
computes it per call); `_cost_block` sums only llm+image+audio; total_cost_usd 2.59948 = 0.41148 +
2.028 + 0.16 exactly. And cradle's spend/jobs ledger records actual_usd 0 for every platformer run
because it reads manifest.generation_stats, which only the dungeon writes.

**Fix**

(a) One lane name, one data source. Extend `canon pack templates` with per-lane
`generator_options: [{id, label, paid}]` sourced from the same registry
`build_vlm_judge`/run_slice choices read (paid rows can be derived from the existing `providers
list` backends+label data so "Anthropic" comes from PROVIDER_ROWS, not a literal); render
NewProjectModal's five `sel` tables, `anyPaid`, and AnimateModal's VLM_BACKENDS from it. Call the
lane what it does in both modals ("Vision: motion specs + art QA"), give the fake option a label
that does not promise $0 when the image lane is paid (e.g. "canned verdicts — sheets still bill
the art lane"), and align cli/main.py:984's help with run_slice's. (b) Chip: split
`est.assets.vlm` into its families (block already carries them) and add a family breakdown to
`est.assets.images` (tiles/bands/props/bases/animation sheets) so "images 54×" can show "(24
animation)". (c) Estimator from data: images_per_stage = len(tile names) +
Graphics().backdrop_bands (+1 if foreground_band) + len(PROP_SPECS); per-enemy = 1 +
len(DEFAULT_SETS["enemy"]) (+1 hopper, or price off the bible's archetypes); zero sheet images
when vlm backend is none; count rooms in level_judgments the way `_fresh_nodes` already lists them
and use the same secret_rooms_avg the runner does. (d) Spend: add `vlm_calls`/`vlm_cost_usd` to
GenerationStats, drain `judge.last_cost` right after each `judge.judge()` in
author_animation_spec, the animation-QA loop and VlmQaPhase (mirror `_add_image_cost`), fold it
into `total_cost_usd`, `by_phase` under plat:sprite_animation / plat:vlm_qa, AND into
`_cost_block` (add `vlm_usd`) so the animate op's journal `gen.cost_usd` and cradle's ledger see
it; add a vlm row to estimator.py:218-220's calibration table if calibration is wanted. (e)
Parity: cradle's actual_usd reader (NewProjectModal.tsx:347-350, startCreate.ts:280-290) must fall
back to reading `generation_stats.json` when `manifest.generation_stats` is absent — or the
platformer's compose step embeds stats in manifest.json like the dungeon's manifest phase does.
Either respects cradle-never-writes-pack-files; nothing regenerates.

**Needs you**

Which is the intended user-facing model: one "Vision" lane that both authors motion and QA's art
(then name it that in both modals and split the chip), or separate Animation and Art-QA toggles
(then the runner must let VlmQaPhase and SpriteAnimationPhase be enabled independently)? Also:
should the platformer embed generation_stats in manifest.json for dungeon parity, or should cradle
read generation_stats.json — pick one so the ledger stops recording $0.

<details><summary>evidence</summary>

cradle/src/components/start/NewProjectModal.tsx:643-647 (vlm lane literals, label "Animation"),
:732-734 (anim chip = est.assets.vlm.usd.best), :347-350 (actual_usd from
manifest.generation_stats); cradle/src/components/anim/AnimateModal.tsx:28-32, :352 ("Vision
backend"). canon: cli/main.py:984 vs run_slice.py:1122-1128 (two names for one lane);
vlm_qa.py:1715-1737 build_vlm_judge; art_phases.py:895-909 (gate: edit backend + judge not None),
:986 authoring call, :1038-1070 (one metered fal img2img per state), :1108-1112 + :1140-1146
(drift retry, warn only on residual drift), :145-149 (attempts floor); tileset_art.py:1026-1035
meter, :1118-1145 alpha-gate retries; ops.py:1239-1269 _gen_cost (components → accuracy only),
:1308-1333 _cost_block (llm+image+audio only); pipeline/stats.py:25-47 (no vlm field);
estimate.py:153-175 + cost_model.json assets (18/6/1/7/1; note names exit prop + 3 bands);
estimator.py:405-441, :218-220; compose.py:392-395 vs pipeline/phases/manifest.py:271. Pack
/Users/wolfgangblack/CradleProjects/pass4_test3_plat_paid: generation_stats.json
image_attempts=image_successes=52, image_cost_usd 2.028 (=52×0.039), total 2.59948 =
0.41148+2.028+0.16; .canon/spend.jsonl estimate {2.6656, 3.3004} actual_usd 0 (dungeon pack's
shows 3.0015); log.jsonl node_item sprite_animation 29 (5 actors + 24 sheets), sprite_art 13,
vlm_qa 4 (l1,l1r1,l2,l2r1); palette_drift.json 10 keys; backdrop band_0/band_1; sprite/prop 4
pngs; sheets on disk hopper 5/lurker 5/wanderer 4/strider 4/player 6; manifest
graphics.backdrop_bands 2, foreground_band false, 23 warnings with no "animation: … drifted";
review/animation_qa.json + qa_report.json vlm_model claude-sonnet-4-6. Read-only reproduction in
scratchpad: `canon world estimate … --vlm-backend anthropic` = byte-equal {2.6656, 3.3004}, images
54, vlm {2 level, 5 qa, 5 authoring, $0.108}; `--vlm-backend none` still images 54 / $2.106.

</details>


---

## The improve card: unpriced, unimplemented control, "no cap" copy

**partly-confirmed** · effort **medium**

**Money:** No overcharge anywhere. Silent under-recording, two ways: (a) a paid `dialogue improve` is never
priced, journaled, or recorded (improve.py:215 `usd: None`; no journal event;
CLI/Rust/ImproveDialogue write nothing) -- pennies per run (about $0.005-$0.06 on claude-sonnet-5,
the actual default; the diagnosis's sonnet-4-6 figures were ~1/3 high) but structurally invisible
to "spent today" and the journal-fed cost dashboard. (b) Adjacent, on the same number: the paid
platformer create recorded `actual_usd: 0` in pass4_test3_plat_paid/.canon/spend.jsonl and
jobs.jsonl because cradle copies `manifest.generation_stats.total_cost_usd`
(NewProjectModal.tsx:344-350, startCreate.ts:266-274) and the platformer manifest.json has no
`generation_stats` key (the dungeon's does), while generation_stats.json shows $2.60 actually
spent (llm 0.4115 + image 2.028 + audio 0.16). That pack's card will always say "$0 spent today".
The dungeon create recorded correctly ($3.00). Leg D evals record nothing by design
(PASS4:139-141).

**Cause**

(1) No dialogue estimate scope exists in either pack estimator (dungeon/estimate.py:135-136 raises
for anything but `world`; platformer/estimate.py:595-599 lists
world|music|animate|asset|row|generate|layout|enemies|items) and no `dialogue estimate` verb
(main.py estimate verbs only at :537, :1229, :2179, :3431), so ImproveDialogue.tsx:183 hands the
gate `estimate: null` and both :348 and PaidCard.tsx:52 print the honest-unknown state, even
though the request is fully determined before the call (improve.py:114-125: 428-char system +
instruction + json.dumps(scope trees)[:120000], max_tokens=4096, on claude-sonnet-5 by default).
The same gap leaves the paid run's actual unpriced (improve.py:215 `usd: None`), unjournaled, and
unrecorded (CLI :3989-4005 just emits; Rust lib.rs:3062-3106 passes through;
ImproveDialogue.tsx:195-208 never calls spendRecord/jobRecord). The user's premise that there is
no number to suggest is wrong. (2) ImproveDialogue.tsx:369-377 renders PromptOverride with native
`disabled` and the reason jammed into the label; PromptOverride.tsx:74-92 has no class (so
App.css:762 `.btn:disabled` never applies), inline `cursor: pointer` / `opacity: .85` / `color:
inherit` in both states, no title, no Tooltip, and native disabled drops it from the tab order and
swallows the click. It is unimplemented on the canon side twice: `dialogue improve` takes no
`--system-prompt` (main.py:3972-3985) and hard-codes `_SYSTEM` (improve.py:115); and
`kind="improve"` is the platformer level-layout prompt (ops.py:1359 `plat:layout`) with `prompt
show` dispatching `_pack_ops()` with no pack (main.py:2626) -- VERIFIED by running `canon prompt
show <dungeon copy> --kind improve`: it returns the platformer terrain prompt without error.
Nothing the user can do in any mode makes it live: the doctrine's mode-inapplicable/noise case
(master_prd.md:33-37), not capability-blocked. improve.test.tsx:284-292 pins the current state.
(3) PaidCard.tsx:60-61 is a hard-coded "budget" / "· no cap set" literal written when A-2
(master_prd.md:279) still contemplated warnings; the product now tracks only. The figure is
ConfirmGate.tsx:74-84 summing this pack's `canon spend list` rows whose UTC ts starts with the UTC
day (spend.py:69-70 stamps UTC), so "today" flips at 17:00 PDT and is per-pack. On the paid
dungeon pack it reads "$3.00 spent today" right now (row actual_usd 3.001468, ts
2026-09-11T05:58:32+00:00; verified via `canon spend list` on a scratch copy); "$0" is correct on
the step-0b rehearsal pack phase4_test1 (fake/none, actual 0), which is where PASS4 Leg C(paid)
(:172-180, "same steps as C(free)" -> :126 "the dungeon project from step 0b") runs.

**Fix**

Forks, defaults marked (*):
(1) * PRICE IT, do not hide the row (the dialog's design makes the estimate the largest figure on
screen: ImproveDialogue.tsx:18-22, App.css:6065-6069, improve.test.tsx:275-276; DECISIONS D7 chose
an explicit "not estimated" over silence). Extend, do not add:   (a) canon: a `dialogue` scope in
dungeon `estimate_cradle` (dungeon/estimate.py:117-137) that resolves the NPC/tree via
`dialogue.storage.npc_trees`, computes tokens from the REAL payload (input = len(system +
instruction + json.dumps(scope trees)[:120000])/4; output low = a few hundred, high = the 4096
ceiling from improve.py:124, NO retry multiplier since improve makes one call) and prices at the
model improve will actually run -- `chat_anthropic.DEFAULT_CHAT_MODEL` (sonnet-5) via
`estimate(..., models={"llm": ...})`, never the estimator's sonnet-4-6 default. This needs a small
extension to `estimate()`/`price_llm`: a per-request token override kwarg merged ahead of
`actuals` (estimator.py:310 already prefers actuals over the table) and a way to suppress
`worst_retries` for that task (estimator.py:306/:333). `unitLabel` = the dialog's "N nodes · M
choices". A `canon dialogue estimate <pack> --npc --tree-id --scope --backend [--model]` verb
emitting `{result:"estimate", estimate}` like `level estimate` (main.py:2207-2215), dispatched by
pack type.   (b) cradle: `estimate_dialogue` Tauri command beside `estimate_level` (lib.rs:2351),
`api.estimateDialogue` beside invoke.ts:1288, ImproveDialogue fetches on backend/scope change as
ImproveLayoutModal.tsx:52-62 does, passes `estimate: est` at :183 and prints `fmtRange` at :348;
keep "not estimated" only for a failed fetch.   (c) close the actual: in `dialogue_improve` price
`gen.input_tokens/output_tokens` through `pricing.price_for("llm", gen.model, warnings)` +
`per_token` so `cost.usd` is real (improve.py:215); then journal a costed TOKENS_GEN_KIND event
via `provenance.append_event` and derive the compat spend row with `spend_row_from_journal`
(spend.py:86) -- or, minimally, have ImproveDialogue call `api.spendRecord` (invoke.ts:1296,
through `canon spend record`, so cradle still writes no pack file) with the priced actual. Code
computes; no LLM. Also decide whether the two anthropic defaults (pipeline sonnet-4-6 vs chat
sonnet-5) should converge -- the estimator cannot be trusted for chat verbs until they do or the
caller names the model.   Alternative (small): hide the cost-box figure when paid and leave the
card's "— not estimated" -- rejected because the spend stays unrecorded.
(2) * HIDE IT. Remove the `<PromptOverride ... disabled>` block at ImproveDialogue.tsx:363-377,
drop `systemOverride` state (:91), flip improve.test.tsx:284-292 to assert absence. It returns
live with `disabled={busy}` (EntityOverview.tsx:1421-1441 pattern) only when canon grows
`--system-prompt` on `dialogue improve` AND `prompt show` gets a pack-aware `dialogue` kind (today
`kind="improve"` on a dungeon pack returns the platformer terrain prompt). If kept visible as a
capability announcement, it must go through GatedButton's treatment (aria-disabled + Tooltip on
hover/focus + opacity .45, GatedButton.tsx:75-99; Tooltip.tsx:99 opens on focus) and the label
must lose the appended reason.
(3) * Drop cap vocabulary and say only what is tracked: PaidCard.tsx:60 key "budget" -> "spend";
:61 -> `{fmtCents(todaySpendCents)} spent today · {fmtCents(projectSpendCents)} in this project`
(ConfirmGate.tsx:74-84 already has `r.spend.total_actual_usd`; add optional `projectSpendCents` to
the estimate PaidState at agentState.ts:85-96 and the parser at :693, guarded so the agent-service
cards that do not supply it still render). Make "today" the LOCAL day (ConfirmGate.tsx:78: compare
the local date of `new Date(e.ts)`, not the UTC string). PaidCard.test.tsx:54 only pins "spent
today"; add one assertion for the project figure. Optional: the running card's "spent so far $A of
$B" (PaidCard.tsx:204-205, B = estimate high) reads as a ceiling too -- "of the $B estimate" if
the user wants zero cap connotation.

**Needs you**

Two forks need you: (1) price the improve estimate (default) or hide the figure when paid? (2) The
chat backend runs improve on claude-sonnet-5 ($2/$10, chat_anthropic.py:90) while the estimator
and the create pipeline default anthropic to claude-sonnet-4-6 ($3/$15) -- should improve keep
sonnet-5 and the new estimate quote that model explicitly, or should the two anthropic defaults
converge? Also: "spent today" as local day and "in this project" wording -- fine as defaults?

<details><summary>evidence</summary>

cradle: ImproveDialogue.tsx:91 (systemOverride), :170-171 (no estimate verb comment), :183
(`estimate: null`), :348 (`{paid ? "not estimated" : "$0"}`), :369-377 (PromptOverride disabled,
reason in label); PromptOverride.tsx:74-92 (native disabled, inline cursor:pointer/opacity
.85/color inherit, no title; no class so App.css:762 `.btn:disabled` does not apply; no global
`button:disabled` rule); GatedButton.tsx:75-99 + Tooltip.tsx:99 (onFocus); PaidCard.tsx:42, :52,
:60-61, :65-66, :204-205; ConfirmGate.tsx:74-84 (UTC day filter); confirmGateState.ts:76-98 (null
estimate -> 0/0 cents, model "default model"); agentState.ts:85-96, :693;
improve.test.tsx:275-276, :284-292; PaidCard.test.tsx:54; EntityOverview.tsx:1421-1441;
ImproveLayoutModal.tsx:52-62; invoke.ts:1270/:1288/:1296 (spendRecord)/:1435/:1534/:1701, :211
(PromptKind); NewProjectModal.tsx:344-350; startCreate.ts:266-274; lib.rs:2351, :2786-2795
(preview_prompt passes only kind), :3062-3106; CostDashboard.tsx:21-45 (journal is the one
source). canon: improve.py:43, :45-52 (_SYSTEM = 428 chars), :113 (resolve_chat_backend), :114-125
(request, max_tokens=4096), :158-163 (usage), :186, :215 (`usd: None`);
backends/chat_anthropic.py:90 (`DEFAULT_CHAT_MODEL = "claude-sonnet-5"`), :373;
backends/anthropic.py:48 (`DEFAULT_MODEL = "claude-sonnet-4-6"`); tests/test_chat_backends.py:674;
pricing.py:103 (sonnet-5 $2/$10), :104 (sonnet-4-6 $3/$15), :328 (BACKEND_DEFAULT_MODEL), :429
(price_for), :446 (default_model); estimator.py:306 (worst_mult), :310 (actuals precedence), :333
(worst = best x mult), :554 (models kwarg), :601 (actuals from actuals_dir only);
cli/main.py:537/:1229/:2179/:3431 (only estimate verbs), :2207-2215, :2550-2574 (_pack_ops), :2626
(`_pack_ops()` no pack), :3972-4005 (no --system-prompt, no journal);
packs/dungeon/estimate.py:135-136; packs/platformer/estimate.py:595-599;
packs/platformer/ops.py:1357-1367 (PROMPT_KINDS improve = plat:layout), :1413-1435;
packs/dungeon/cost_model.json:32 (dialogue 1195/1145), :36 (worst_retries 3); spend.py:69-70
(UTC), :86-108 (spend_row_from_journal), :97/:105 (TOKENS_GEN_KIND); agent/tools_paid.py:507-557;
agent/tools_write.py:172 (journal_window); provenance.py:249 (append_event);
docs/September_master_prd.md:33-37 (doctrine 4 amended), :279 (A-2);
docs/test_plans/DECISIONS_annotated.md:280-297 (D7), :326-365 (D8);
docs/test_plans/PASS4_paid_runs.md:68-75 (step 0b), :126, :172-180 (Leg C(paid) same steps as
C(free)). Packs (read-only): phase4_test2_paid/.canon/registry.json capabilities [grid, dialogue,
per_step_roll]; spend.jsonl + jobs.jsonl actual_usd 3.001468, ts 2026-09-11T05:58:32+00:00;
manifest.json HAS generation_stats (total_cost_usd 3.001468). pass4_test3_plat_paid registry
capabilities [grid]; spend.jsonl + jobs.jsonl actual_usd 0 (status no_change, 919303 ms);
manifest.json has NO generation_stats; generation_stats.json total_cost_usd 2.5995 (llm 0.4115,
image 2.028, audio 0.16). Rehearsal pack phase4_test1: fake/none, actual_usd 0, ts
2026-09-11T04:44:35+00:00. Local clock at check: Thu Sep 10 23:45 PDT 2026 -> UTC day 2026-09-11.
Ran on scratch copy: `canon spend list` returns the $3.001468 row; `canon prompt show <copy>
--kind improve` returns label plat:layout with the platformer terrain system prompt (no error);
payload sizes via canon.dialogue.storage: per-tree 3685-8887 chars (4-12 nodes, 7-26 choices), NPC
scope up to 30574 chars (Ovra Ket, 4 trees).

</details>


---

## generation_time is a write-only placeholder

**confirmed** · effort **small**

**Money:** None for this finding: generation_time is reporting only; every cost figure in both
generation_stats.json files is populated, and cradle's jobs.jsonl duration_ms (1520555 / 919303)
was already correct.
Adjacent observation CONFIRMED on disk, separate ticket: pass4_test3_plat_paid/.canon/spend.jsonl
and jobs.jsonl carry actual_usd 0 against generation_stats.json total_cost_usd 2.5995 (the dungeon
journaled 3.0015 correctly). Cause verified: the platformer manifest.json has no generation_stats
key (SliceManifestPhase writes only the standalone file, compose.py:392-395), and all six actual-
cost readers source manifest.generation_stats.total_cost_usd (cradle NewProjectModal.tsx:347-350,
startCreate.ts:268-271, store.ts:738-739/:867-868, CostDashboard.tsx:93; canon
tools_paid.py:716-717). Every wizard- or agent-created platformer world is therefore journaled at
$0 and shows no cost in cradle's dashboard/recent list: under-recording of spend, not an
overcharge. One more inaccuracy there: tools_paid.py:708's claim that "the runner already
journalled its per-step money inside that pack" is false; record_spend is called only by
tools_paid.py:538/:727 and the `canon spend record` CLI cradle invokes (cli/main.py:2749), and the
platformer pack's spend.jsonl holds exactly one row, cradle's.

**Cause**

GenerationStats.generation_time_seconds (src/canon/pipeline/stats.py:47) is a write-only
placeholder from the mazeworld-parity port (docstring :12-19): no code in canon ever assigns it,
so to_dict() (:155-156) serializes the dataclass default 0.0 and generation_time_human (:64-67)
formats it as "0m 00s". There is no run timer anywhere in the generation path: PipelineContext
(runner.py:32-76) has no timing field; run_pipeline (runner.py:103-134) and orchestrate
(orchestrator.py:271-276, :380-388) only emit run_start/run_end through StepLog.emit
(steplog.py:153-167), which stamps datetime.now to log.jsonl and retains nothing in memory (its
attrs are path/_lock/_cancel_file/_done/_items/cancelled, steplog.py:96-102); the only
time.monotonic() in src/canon is the agent's per-tool-step timer (agent/runs.py:845-878),
unrelated. All three GenerationStats birth sites pass backend names only (dungeon
compose.py:282/:286, platformer run_slice.py:1180-1185/:1200, dag.py:657/:670), and both writers
copy ctx.stats.to_dict() verbatim (manifest.py:227 into the nested manifest block :271, :277-279
standalone; platformer compose.py:392-395 standalone only). git log -S finds the field introduced
in 83f428f only; git log -G finds no assignment ever. Scheduler-independent (dungeon ran
sequential, platformer orchestrated, both 0.0) and wizard-independent (canon create only builds
argv, cli/main.py:906-947, and subprocesses canon.packs.dungeon.run_world /
canon.packs.platformer.run_slice per spec.py:548-549 / :259-260).

**Fix**

Same shape as the diagnosis (extend existing machinery, ~10 lines + tests), with the stamp moved
so no clock reaches emitted content:
1. src/canon/pipeline/runner.py PipelineContext (:46-56): add `started_at: float =
field(default_factory=time.monotonic)`. Every runner builds its ctx moments before run_start
(dungeon compose.py:282 then run_world.py:232; platformer run_slice.py:1200 then _run_schedulers
-> dag.py run_orchestrated, whose two passes share the ctx). Alternative with the same effect and
no PipelineContext change: put the clock on GenerationStats itself (`started_at` with
compare=False/repr=False, never serialized) plus a `mark_elapsed()` helper; no test compares
GenerationStats instances.
2. Stamp elapsed ONLY where the standalone, determinism-exempt file is written:    -
src/canon/pipeline/phases/manifest.py ManifestPhase._write_stats (:277-279): set
ctx.stats.generation_time_seconds = time.monotonic() - ctx.started_at immediately before
write_json_singleton. This runs AFTER _write_manifest (:66 vs :69), so manifest.json's nested copy
(:227/:271) never carries the clock. Guard with getattr(ctx, "started_at", None) for duck-typed
test contexts.    - src/canon/packs/platformer/compose.py SliceManifestPhase.run: the same line
before the generation_stats.json write at :392-395 (the platformer manifest.json nests no stats,
so any placement before :392 is safe).    Do NOT stamp before _write_manifest as the diagnosis
proposed; that fails tests/test_dungeon_portraits.py::test_two_same_seed_runs_are_byte_identical
and tests/test_create_flow.py::test_the_seed_reaches_the_runner (verified by probe).
3. The dungeon's nested manifest.json.generation_stats then still reads generation_time_seconds
0.0 / "0m 00s" (deterministic, but a residual zero in a file the user can open). Nothing reads
timing from the manifest: cradle takes the "time" cell from the standalone generation_stats.json
(WorldBibleView.tsx:92, :307) and every manifest.generation_stats reader wants only total_cost_usd
(store.ts:738/:867, CostDashboard.tsx:93, NewProjectModal.tsx:350, startCreate.ts:271,
tools_paid.py:717). Default: drop generation_time_seconds/generation_time_human from the nested
copy in _write_manifest, mirroring the existing validation_report.timestamp strip at
manifest.py:213-222 (same doctrine, same file). See open question.
4. Tests: extend tests/test_pipeline.py (ManifestPhase.run on a ctx with started_at, monkeypatched
time.monotonic or ~10 ms sleep; assert generation_stats.json generation_time_seconds > 0 and
manifest.json.generation_stats carries no clock) and the platformer slice fixture in
tests/test_steplog.py::TestSliceObservability. Verify with tests/test_dungeon_portraits.py
(omitted from the diagnosis's list; it is the one that catches the manifest regression),
tests/test_create_flow.py, tests/test_steplog.py, tests/test_phase_manifest.py,
tests/test_pipeline.py, tests/test_platformer_slice.py, tests/test_platformer_dag.py.
5. Paid packs stay untouched. True values from their own .canon/log.jsonl if the user wants to
hand-correct: phase4_test2_paid 1519.087 s ("25m 19s"), pass4_test3_plat_paid 918.274 s ("15m
18s"). A --resume measures only the resumed run, consistent with the cost counters, which already
restart per invocation (fresh GenerationStats at run_slice.py:1180 / dag.py:657).
6. Cradle: no change; the bundled runtime's stats.py is byte-identical to source and picks the fix
up at the next repackage.

**Needs you**

The dungeon manifest.json nests a copy of generation_stats and that file is inside the byte-
determinism bar, so the real elapsed can only live in the exempt generation_stats.json. For the
nested copy, pick one: 1. (default) Drop generation_time_seconds and generation_time_human from
the manifest.json copy only, the same way validation_report.timestamp is already stripped there
(manifest.py:213-222): no clock in emitted content, no reader loses anything (nothing in cradle or
canon reads timing from the manifest). Small shape divergence from mazeworld's manifest, which
does nest those two keys (tests/reference/fixtures/cradle_mazeworld_scifi/manifest.json:102-103);
does mazeworld's own loader read them? 2. Leave the two keys in the manifest copy at 0.0 / "0m
00s": byte-stable and shape-identical to mazeworld, but a residual zero remains in a file the user
opens. Also: do you want the two paid packs' generation_stats.json hand-corrected to 1519.087 s /
918.274 s? I have not touched them.

<details><summary>evidence</summary>

On disk (unmodified): both packs' generation_stats.json read "generation_time_seconds": 0.0,
"generation_time_human": "0m 00s"; phase4_test2_paid/.canon/log.jsonl run_start 05:33:12.514
(sequential, 16 phases) -> phase:manifest node_done == run_end 05:58:31.601 (1519.087 s);
pass4_test3_plat_paid/.canon/log.jsonl two orchestrated passes on one ctx, 06:07:58.536 (6 nodes)
-> 06:08:20.134, then 06:08:20.135 (55 nodes, 6 skipped) -> plat:manifest node_done 06:23:16.807 /
run_end .810 (918.274 s total); jobs.jsonl duration_ms 1520555 / 919303. Dungeon manifest.json
nests generation_stats (time 0.0); platformer manifest.json has no generation_stats key.
Code: stats.py:47 default, :64-67 formatter, :155-156 serializer; no assignment anywhere in src/
(only tests/test_pipeline.py:275, :281 manual sets and :338 default assertion); git log -S ->
83f428f only, git log -G '=' -> none; no monotonic/time.time/perf_counter in runner.py,
orchestrator.py, steplog.py, manifest.py, dungeon compose.py/run_world.py, platformer
dag.py/run_slice.py/compose.py; StepLog holds no start instant (steplog.py:96-102). Byte bar:
treediff.py:32-36; tests pinning dungeon manifest.json bytes: test_dungeon_portraits.py:272-275,
test_create_flow.py:243-266, with the no-clock doctrine at test_dungeon_portraits.py:12-18 and
manifest.py:13-20.
Probe (scratchpad/stamp_placement_probe_v2.py, fake backends, in-memory monkeypatches only):
baseline -> manifest.json identical across two same-seed dungeon creates; stamp before
_write_manifest (diagnosis placement) -> manifest.json is the one file inside the bar that differs
(0.0233 vs 0.0736 s); stamp inside _write_stats -> manifest.json identical, generation_stats.json
0.0219 / 0.0752 s. Existing tests test_dungeon_portraits.py -k "byte_identical or no_clock" pass
unmodified today (2 passed).

</details>
