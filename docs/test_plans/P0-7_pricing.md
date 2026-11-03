# Human test plan — row P0-7, the estimator core and `canon.pricing`

**Row:** master §3.1 stage 4, `P0-7 [A2]` · **Gates:** platformer estimates output-identical;
dungeon estimate sane against the $30 / 3-map anchor. **Both met 2026-09-01.**

`canon.pricing` is now the product's only price source (master §3.0-C), seeded from
`docs/provider_price_table.md`. `canon.estimator` is the engine; each pack supplies a count
function and a calibrated cost model. Every backend carries `last_cost_accuracy`, so a fal image
is priced and flagged `estimated` instead of silently reporting $0.

## A. Identity, then the sanctioned changes

- [x] **A1** the replay test passes: the old prices injected into the new engine reproduce the
      pre-refactor JSON byte for byte.

```bash
uv run python -m pytest tests/test_estimate_identity.py tests/test_pricing.py -q -p no:cacheprovider
```

- [x] **A2 — what changed on purpose** (your decision of 2026-09-01, exact prices over buffers).
      On the default paid world estimate:


| Line               | Before            | After                             |
| ------------------ | ----------------- | --------------------------------- |
| images (109)       | $4.36 at $0.04    | $4.251 at $0.039, fal nano-banana |
| music (3)          | $0.30 at $0.10    | $0.24 at $0.08, Lyria 3 Pro       |
| sfx (4)            | $0.20 at $0.05    | $0.16 at $0.04, ElevenLabs        |
| mid-tier LLM tasks | claude-sonnet-4-6 | claude-sonnet-5 (cheaper per 1M)  |


```bash
uv run canon world estimate --llm-backend anthropic --image-backend fal --music-backend lyria --sfx-backend elevenlabs --vlm-backend anthropic | python3 -c "import json,sys; d=json.load(sys.stdin)['estimate']; print(d['total_usd'], d['assets']['images'], d['low'], d['high'], d['accuracy'])"
```

- [x] **A3 — free stays free.** With no backend flags (all fake/none) the total is $0 and the
      counts still render, so a $0 run never raises a spend dialog.



## B. The dungeon estimate and the anchor

```bash
uv run canon world estimate --template dungeon --llm-backend anthropic --image-backend fal --music-backend lyria --sfx-backend elevenlabs | python3 -c "import json,sys; d=json.load(sys.stdin)['estimate']; print('best', d['total_usd']['best'], 'worst', d['total_usd']['worst'], 'llm calls', d['llm']['calls'])"
```

- [x] **B1** a 3-room paid dungeon lands near $3.84 best / $6.25 worst, and the 3-map full-API
      figure sits in a plausible band around the $30 anchor. The anchor refines against real spend
      after the first paid runs, which is an open item in the master, not a gate.
- [x] **B2** counts respond to the wizard's knobs:

```bash
uv run canon world estimate --template dungeon --rooms 5 --npcs 3 --llm-backend anthropic --model claude-sonnet-5 | python3 -c "import json,sys; d=json.load(sys.stdin)['estimate']; print(d['llm']['by_task'].get('db:npc'), d['model'], d['unitCount'])"
```



## C. Loud, never silent

- [x] **C1 — an unpriced model warns rather than reporting $0:**

```bash
uv run canon world estimate --template dungeon --llm-backend anthropic --model not-a-model | python3 -c "import json,sys; d=json.load(sys.stdin)['estimate']; print(d['llm']['usd'], d['warnings'])"
```

- [x] **C2 — an unknown template is a named error, exit 1:**

```bash
uv run canon world estimate --template nope; echo "exit=$?"
```

- [x] **C3** every pricing row carries its source URL and the date it was verified:

```bash
uv run python -c "from canon import pricing; r=pricing.llm('claude-sonnet-5'); print(r)"
```



## D. Decisions to confirm

- [x] Meshy enters as credits times a configurable rate, default $0.02, overridable with
      `MESHY_USD_PER_CREDIT`. It has no backend until W2.2; only the price rows exist. Confirm.
- [x] Three cells in the price table are still UNVERIFIED (the Meshy wallet rate, Meshy
      Premium/Ultra credits, retired kimi-k2). They need a dashboard check from you before those
      specific rows are trusted. Everything else is confirmed against provider pages.



## E. On approval

- [x] Say "P0-7 approved". `cost_model.json` now holds counts and tokens only; every dollar comes
      from `canon.pricing`.

