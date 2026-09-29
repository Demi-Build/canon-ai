# Pass 4 — the paid runs

**Rewritten 2026-09-09** after four reviewers found 61 defects in the previous version, eight of them
capable of spending your money for nothing. Every command and figure below was executed on this
machine. Where something was *not* verified, it says so.

---

## Who runs this

**A human, not an agent.** Master §1 rule 3: paid legs are user-run, and an agent "may raise a paid
suggestion card, never accept one." This pass is partly a test *of that rule*.

An agent may do all of this: run every `$0` command here, read estimates, prepare the wizard up to
the spend card, and do all the post-run analysis. **It must stop at every spend card and hand the
click to you.** Each leg needs its own spoken go-ahead — approval for one leg is never approval for
the next.

**Cumulative ceiling for the whole pass: $12.** If the running total passes it, stop and report.

**One attempt per leg.** A retry is not a resume: `unique_pack_dir` writes a fresh `<name>_2` and
re-spends the leg from zero. If a leg fails, hand over the partial pack path and stop.

---

## Which cradle

Three exist on this machine and two of them are wrong.

```bash
cd ~/Documents/projects/cradle && export CANON_BIN=~/Documents/projects/canon-ai/.venv/bin/canon && npm run tauri dev
```

- **Not** `/Applications/Cradle.app` — dated April 2026, months older than the build under test.
- **Not** `npm run dev` — that is the browser mock. It fabricates every result and spends nothing.

Once up: Settings → **Environment** and confirm the effective-canon row points at your checkout.

---

## Where the keys live — read this before anything else

**`canon-ai/.env` is the one source of keys. Keep your shell clean of provider exports.**

cradle reads `<canon repo>/.env` on its own in the dev checkout and hands it to every canon child as
`CANON_ENV_FILE`. But the file is applied with *setdefault* semantics — **any provider variable
already exported in the shell you launch cradle from silently beats the file.** That is exactly what
went wrong on the first run: stale `.zshrc` exports shadowed the fresh `.env` keys for OpenAI and
Google, Settings Test reported good keys as bad, and every Lyria call went to a project that was not
yours.

So: **no provider `export` lines in `.zshrc`**, and do **not** `source .env` before launching cradle
— that just copies the values into the layer that overrides the file. Before you launch, in the
terminal you will launch from:

```bash
for v in ANTHROPIC_API_KEY OPENAI_API_KEY GOOGLE_API_KEY FAL_KEY ELEVENLABS_API_KEY MOONSHOT_API_KEY; do printf "%-20s %s\n" "$v" "${(P)v:+SHADOWING .env — run: unset $v}"; done
```

**Every line must be blank.** If one says SHADOWING, `unset` it in that terminal (or open a fresh
terminal) before launching. Leg D is the one terminal leg; it loads `.env` per command, below.

---

## Step 0 — prove your tree matches this plan ($0, 1 min)

```bash
cd ~/Documents/projects/canon-ai && uv run canon world estimate --template dungeon --rooms 3 --llm-backend anthropic --image-backend fal --music-backend lyria --sfx-backend elevenlabs --vlm-backend none
```

**Expect `low 3.9952`, `high 6.4107`, images 50.** Anything else means your tree differs from the one
these figures were measured on — stop and say so.

---

## Step 0a — prove the keys ($0, 2 min)

Settings → **API keys** → **Test** on anthropic, Google (Lyria), ElevenLabs. **Test checks whichever
source is winning** — a shadowing shell export, not your `.env` — so run the shadow check above
first or a red result tells you nothing about the key you meant. Each is a free
authenticated read. All three must come back green.

**fal is the exception and it matters.** fal publishes no free endpoint, so its Test button is
disabled with that reason and `FAL_KEY` cannot be pre-verified. fal is also **$1.95 of leg A's
$4.00** — the single biggest lane. A bad `FAL_KEY` is the most expensive way to fail here, so check
it against your fal dashboard by hand.

**A green Test does not guarantee a lane will produce.** Lyria is paid-tier only — a free-tier Google
key authenticates fine and then cannot generate. ElevenLabs credits are shared account-wide. If music
or sfx returns zero rows later, check tier and balance **before** reporting a metering defect. *(Read
from the provider registry; not independently verified.)*

---

## Step 0b — the free rehearsal ($0, ~1 min). Do not skip this.

Create the **identical** dungeon in the wizard — 3 rooms — with every generator left on `fake`/`none`.
Then:

```bash
uv run canon journal list <that-pack> --summary
```

```bash
uv run canon spend list <that-pack>
```

This answers, for free, the two questions leg A would otherwise answer expensively:

| Question | Expected answer |
|---|---|
| Does the 💰 dashboard populate for a create? | **No — and that is correct.** See below. |
| Does cradle write `spend.jsonl` + `jobs.jsonl` with the estimate/actual pair? | Yes |

**Report what you see before any money moves.**

### The thing that would otherwise look like a bug

**A create does not journal costs.** The pipeline drains spend into `generation_stats.json` →
`manifest` → `jobs.jsonl`. The 💰 dashboard sums journal `costCents`, which only the *editor/op*
surface writes. So after a real paid create the dashboard's tables will be empty and its tiles will
read `$0`.

**That is correct-by-design, not a metering defect.** Confirmed on your existing dc2: 46 images and
92 llm calls in `generation_stats.json`, while `journal list --summary` reports `costedEvents: 0`.

**The money of record for a create is `canon spend list <pack>`** — it reports `total_estimate_usd`
beside `total_actual_usd` — plus `generation_stats.json`.

---

## Before any spend — capture this

Nothing afterwards can otherwise be attributed to this run.

```bash
date -u && ls -t ~/CradleProjects | head -5 && cd ~/Documents/projects/canon-ai && git rev-parse --short HEAD
```

Then record today's spend on all four provider dashboards: `console.anthropic.com`, `fal.ai`,
`aistudio.google.com`, `elevenlabs.io`. **Those four numbers are the only independent audit of
canon's own arithmetic — and the only record at all for leg D.**

---

## Leg C(free) — rehearse improve at $0

**STOP. Ask before starting any leg below.**

Do this on the free backend first, so the paid run later is muscle memory rather than a first attempt.

1. Open the **dungeon project from step 0b**. The platformer has no NPCs and no dialogue.
2. Sidebar → **NPCs** → any NPC → **Dialogue**.
3. **⌘S first.** The improve gate reads disk, not your unsaved buffer, and will correctly refuse a
   tree that exists only in the editor.
4. **✨ Improve…** in the editor header, immediately left of the blue Save button.
5. Confirm the scope control sits on **`this tree`**, not the NPC pill.
6. Leave the backend on its default `fake — $0, canned text`. Button reads **Propose — $0**.
7. Propose. **Proposals must land in the unsaved buffer and write nothing.** If it writes directly,
   that is a real doctrine violation — report it.

---

## Leg D — the provider-swapped eval (~$0.10, terminal)

**STOP. Ask first.** This is the only shell leg. **It is also the only leg whose spend nothing
records** — the eval runs outside any pack and writes no journal, spend or jobs row. Your provider
dashboard is the sole audit.

The eval harness reads plain environment variables and does **not** read `CANON_ENV_FILE`, so load
`.env` **inside a subshell for that one command only** — the variables die when the parentheses
close and never reach your interactive shell:

```bash
cd ~/Documents/projects/canon-ai && (set -a; source .env; set +a; uv run python -m canon.agent.eval --backend openai --only just-talking)
```

```bash
cd ~/Documents/projects/canon-ai && (set -a; source .env; set +a; uv run python -m canon.agent.eval --backend kimi --only just-talking)
```

`--only` takes **exactly one** name. A second `--only` silently overrides the first — two
conversations is two commands. Then, only if those pass:

```bash
cd ~/Documents/projects/canon-ai && (set -a; source .env; set +a; uv run python -m canon.agent.eval --backend openai --only unbeatable-level)
```

```bash
cd ~/Documents/projects/canon-ai && (set -a; source .env; set +a; uv run python -m canon.agent.eval --backend kimi --only unbeatable-level)
```

**Dropping `--only` runs the whole 9-conversation corpus on that provider and needs separate
approval.** Running the full corpus on both providers is what actually closes row A8.

**Known, from the 2026-09-10 run:** `unbeatable-level` fails on both providers on ordering alone —
the eval demands `validate_level` before `describe_level`, but nothing in the task requires it and
both models flip the order between runs. That is the eval's strict-order check, not the providers.
Fix it before the full-corpus run or up to 4 of 9 conversations fail on ordering.

---

## Leg C(paid) — one real improve (single-digit cents)

**STOP. Ask first.** Same steps as leg C(free), with two changes that are the whole point:

5. **Change the backend select from `fake — $0, canned text` to `anthropic — paid`.** The header chip
   must flip to `paid · anthropic` and the button must read **Propose — paid run**. **If it still
   says `Propose — $0`, you are on the free backend and nothing will be billed.**
6. The spend card appears reading **`not estimated`** — there is no dialogue estimate verb, so it
   gates without a price. That is expected.

**Ceiling:** one NPC, one tree, one run. If the card names more than one tree, Cancel.

---

## Leg A — the dungeon run (~$4.00–6.41)

**STOP. Ask first. Ceiling: if the wizard estimate exceeds $7.00, something is set wrong — stop.**

1. New project → **dungeon** → **3 rooms**, everything else at its default.
2. Generator selects: **anthropic / fal / lyria / elevenlabs**, vlm on **none**.
3. **Read the live estimate before committing.** It must be a low–high range near **$4.00–$6.41**,
   never a single number and never `$0`.
4. Accept the spend card. **This click is yours.**

**Verifying it, objectively:**

```bash
uv run canon spend list <pack>
```

```bash
cat <pack>/generation_stats.json
```

| Check | Pass condition |
|---|---|
| Actual vs estimate | `total_actual_usd` within the $3.99–$6.41 band |
| Images produced | 50 (46 entity/environment + 4 fixed portraits) |
| Dashboard | **empty — expected, not a defect** |

**The number this pass exists to produce** is `total_estimate_usd` versus `total_actual_usd`. That
delta is the estimator's real error, measured for the first time.

### If it fails mid-run

A **failed** create records neither ledger. Note the pack path off the error banner before dismissing
it, then read `<pack>/generation_stats.json` — it is the only record of what the failed run spent.

If a run is going wrong, press ⏹ **Stop**. A stopped run is terminal but **not** a failure: it records
both ledgers and keeps what landed. Stopping is always cheaper than letting it finish.

---

## Leg B — the platformer run (~$2.56–3.19)

**STOP. Ask first. Ceiling: if the estimate exceeds $3.50, your counts are not the wizard's defaults
— stop.**

**Leave every count exactly as the wizard presents them** — 1 stage, 2 levels, 4 enemies, 4 items.
Raising them to 3 stages / 9 levels prices at $5.52–8.13 and is not what this leg is for.

| | Best | Worst | Images | Music | SFX | LLM |
|---|---|---|---|---|---|---|
| platformer, wizard defaults | **$2.56** | $3.19 | 54 · $2.11 | 1 · $0.08 | 4 · $0.16 | $0.21–0.85 |

Run it only if leg A's numbers reconciled. Its value is exercising a *second* estimator against the
same ledger — which tells you whether the metering is right in general or right by coincidence.

---

## Measured costs

| Leg | Best | Worst |
|---|---|---|
| A — dungeon, 3 rooms | $4.00 | $6.41 |
| B — platformer, wizard defaults | $2.56 | $3.19 |
| C — one improve | ~cents | ~cents |
| D — four eval runs | ~$0.10 | ~$0.20 |
| **Total** | **~$6.65** | **~$9.90** |

Ceiling **$12**.

---

## What this pass does *not* produce

**It does not flip `calibration` to `"actuals"`.** I claimed that previously and it was wrong. A
world-scope estimate passes no pack to calibrate from, and the dungeon hardcodes `"defaults"`
outright. Calibration only bites on a per-op estimate against an existing pack, and only for the
platformer. **The dungeon cannot calibrate at all yet** — its own `cost_model.json` says so.

What this pass produces is an **estimate-versus-actual delta**. That is the valuable number, and it
is what turns the anchor into something measured.

---

## Afterwards

Hand over the pack paths. I will read `spend.jsonl`, `jobs.jsonl` and `generation_stats.json` and
produce the estimate-versus-actual table.

**Practicalities:** leg A writes ~73 real assets and leg B ~59. Allow ~1 GB free on the volume holding
`~/CradleProjects`, expect many minutes per leg, run `caffeinate -i`, and stay on a stable connection
— a network drop mid-run is the failure path above and is **not resumable**.

---

## Bugs found while writing this, worth filing separately

- **`world estimate`'s platformer defaults (3/9/7/5) disagree with `pack templates`' (1/2/4/4)**, so
  the terminal estimator forecasts a world the create verb will not build.
- A **failed** create records neither `spend.jsonl` nor `jobs.jsonl` — `NewProjectModal` returns
  before both writers, so the most expensive outcome is the least recorded.
