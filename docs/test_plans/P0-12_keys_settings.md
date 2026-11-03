# Human test plan — row P0-12, keys and Settings

**Row:** master §3.1 stage 8, `P0-12 [A]` · **Gate:** on a fresh machine, add a key in Settings and
a paid generation succeeds; and the create wizard's precheck deep-link resolves. **The local legs
are met 2026-09-01**; the two that need a real app and a real provider are in section D.
**Design source:** an explicit pattern build (master §8 Q7), following Phase 0 W3.5's spec text and
cradle's existing pane conventions.

What shipped: `canon providers` as the one source of provider rows, keychain storage through the
`keyring` crate with a loud unencrypted file fallback where no secret service exists, key injection
into every child process, the two-pane Settings screen with A6's Permissions pane mounted, the
`PIXELLAB_SECRET` fix, `BLENDER_BIN` detection as the one detector W2.2 will consume, and deep
links from all four missing-key refusals.

## A. Suites and the secrets discipline

```bash
uv run python -m pytest tests/test_providers.py -q -p no:cacheprovider && cd ~/Documents/projects/cradle/src-tauri && cargo test --lib
```

- [x] **A1** green: 21 provider tests, 57 Rust tests including a real keychain round trip under a
      randomized test service name.
- [x] **A2 — no key value escapes.** Prove it yourself with a canary:

```bash
ANTHROPIC_API_KEY=sk-CANARY-abcdefghijklmnop uv run canon providers list | grep -c CANARY; echo "0 is the expected count"
```

      Status reports names and sources only. Not the value, not a mask, not even a length.



## B. Provider rows are data

```bash
uv run canon providers list | python3 -c "import json,sys; d=json.load(sys.stdin); print([(r['id'], r['env_var'], r['set']) for r in d['providers']])"
```

- [x] **B1** all nine rows appear: the six September providers plus `MESHY_API_KEY` and the two
      chat keys. Adding a provider is adding a row, not editing a union.
- [x] **B2 — the PixelLab pair.** `PIXELLAB_SECRET` is canonical and `PIXELLAB_API_KEY` is accepted
      as the dashboard's alias, matching what the backend actually reads.
- [x] **B3 — the Meshy note** uses the corrected wording: the free tier is CC BY 4.0, so the paid
      tier is what gives you full ownership and commercial use without attribution.



## C. In the app

- [x] **C1 — the gear opens Settings** with no project open, and the theme toggle stays where it was.
- [x] **C2 — the Keys pane.** Each row shows set or unset with its source. The paste field is
      write-only: after saving, the value is gone from the field and never comes back from any read.
- [x] **C3 — the Test button never fires on its own.** It is a click, it says it contacts that
      provider, and it is the cheapest possible authenticated ping rather than a generation. Rows
      with no free endpoint are disabled with that reason.
- [x] **C4 — the Environment pane** shows the effective canon (bundled runtime or your `CANON_BIN`
      override), Godot detection, Blender detection, and the project store with a relocate control.
- [x] **C5 — Permissions is the third pane**, the one A6 built and this row mounted.
- [x] **C6 — the four deep links land on the right row:** the create wizard's precheck, the entity
      gate, the model picker's unavailable entries, and the agent's paid card. The wizard's link is
      the one the master called an inversion, because it pointed at a screen that did not exist.



## D. What needs you

- [x] **D1 — the real keychain prompt.** Launch the actual app, save a key, and confirm macOS
      prompts with a sensible app label, that Always Allow stops it recurring, and that the pane's
      copy about it matches what you see. Nothing headless can raise that dialog.
- [x] **D2 — one real provider ping** via the Test button, which is user-initiated by design.
- [x] **D3 — the gate's own sentence:** a fresh machine, a key added in Settings, a paid generation
      succeeding. That needs D1 plus P0-11's bundled build.



## E. Decisions to confirm

- [x] Where no secret service exists (some Linux desktops), keys fall back to a 0600 file with a
      loud "stored unencrypted" warning rather than failing. Confirm that trade.



## F. On approval

- [x] Say "P0-12 approved for the local legs". Suite after Stage 8: canon 3403 passed, cradle 703.