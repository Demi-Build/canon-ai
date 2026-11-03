# Human test plan — row P1-A6, the journal shape, paid tier, and cost dashboard

**Row:** master §3.1 stage 5, `P1-A6 [B]` · **Gate:** confirmed, metered, per-conversation and
per-specialist lanes render; the dashboard tables reconcile. **Met 2026-09-01**, after the reviews
caught three metering holes that are now fixed: images journalled zero, accuracy always read
`measured` so fal could never show as estimated, and specialist token burn never journalled.

**Design source:** `design_handoff_agent_panel/README.md` §12 and board 06 for the dashboard, §6
for the Settings → Permissions pane. The contract is the P0 paper §P.8, implemented once in
`src/canon/provenance.py`.

## A. Suites and the paper's own examples

```bash
uv run python -m pytest tests/test_journal_shape.py tests/test_agent_paid.py -q -p no:cacheprovider
```

- [x] **A1** green. `test_journal_shape.py` reproduces each of the paper's eight worked examples
      key for key from events the shipping code actually emits, allowing only timestamps and
      hashes to vary.
- [x] **A2 — the vocabularies stay open.** A novel `genKind` and a novel detail kind both round-trip
      and render. That is what lets `mesh` join at W2.2 without a schema change.



## B. Metering is honest

```bash
uv run canon journal list /tmp/a3_pack --summary | python3 -m json.tool | head -40
```

- [x] **B1 — one field, three tables.** Every total sums `costCents` over journal events, so the
      by-kind, by-identity and by-conversation tables reconcile by construction rather than by
      agreement. `spend.jsonl` is now a derived index carrying `journal_ref`; `jobs.jsonl` is run
      status only and its dollar column is never summed.
- [x] **B2 — measured versus estimated is visible.** A PixelLab or Retro image reports what the
      provider billed and reads `measured`; a fal image is priced from `canon.pricing` and reads
      `estimated`. Neither is ever a silent zero, and a backend with no price row still writes its
      event, carrying the error rather than losing the write.
- [x] **B3 — tokens are their own lane.** A conversation's thinking cost journals against
      `conversation:<id>` with kind `tokens`, and a delegated specialist's burn is attributed to
      that specialist rather than folded into the foreman.



## C. In the app

- [x] **C1 — the dashboard.** Four tiles (total, generation, conversation, today), the you-versus-
      agent split bar, then by-kind with backend and model named, by-identity with specialists
      nested and tokens in their own column (human rows have no token entry), and by-conversation
      with running ones marked. An unknown kind appears as its own row.
- [x] **C2 — the today tile is UTC**, matching the timestamps the journal writes, so it does not
      read zero for part of every day in your timezone.
- [x] **C3 — Settings → Permissions.** The per-project grant list with Revoke and Revoke all,
      reached from a chip's footnote. Revoking undoes nothing already done, and says so.
- [x] **C4 — the job tray reads the durable ledger** now that `jobs_list` and `jobs_record` are
      native rather than mock-only, so agent runs and button runs share one history.
- [x] **C5 — free never spend-confirms.** An all-fake action shows "$0" on an ordinary permission
      chip; only real money raises the accent spend card. A paid action always confirms and can
      never be granted "always".



## D. Decisions to confirm

- [ ] Two paid tools with no estimator scope yet (`generate_asset`, `complete_row`) ship a
      shape-complete zero estimate so the card renders rather than degrading to a plain chip. The
      real per-sprite scope belongs to the estimator's row. Confirm.
- [x] No budget caps anywhere, per master §8 A-2; warnings only, and none are built yet. Confirm.



## E. On approval

- [x] Say "A6 approved".