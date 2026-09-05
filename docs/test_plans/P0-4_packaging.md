# Human test plan — row P0-4, W3.2 packaging surgery

**Row:** master §3.1 stage 2, `P0-4 [A2]` · **Gate:** wheel acceptance — fresh venv, no checkout,
all verbs run · **Met on 2026-09-01** by the build and re-run independently twice (reviewer +
final verifier). This plan lets you see it yourself in ~15 minutes.

What moved: `examples/platformer_pack` → `src/canon/packs/platformer`, `examples/mazeworld_pack`
→ `src/canon/packs/dungeon` (renamed 2026-09-01 per your decision; MazeWorld-named classes and test files rename with W2.0),
`examples/graphics_specs` → inside the platformer package, the slice runner → 
`canon.packs.platformer.run_slice` (the old `examples/run_platformer_slice.py` is a shim). Gone:
every `parents[2]` and `sys.path` hack under `src/`, `tqdm`, and cradle's `run_canon_module`.
The pygame play harness moved too (`canon.packs.platformer.play`, shim at `examples/platformer_play.py`,
cradle spawns it by module). Stayed in `examples/`: `lava_world/` (an acceptance fixture) and the
MazeWorld run scripts.

> **Hash re-baselined 2026-09-05 — not a drift.** This plan originally expected
> `cb35293b0a6f6864e08c9dc9bd1933b76894a402`, which is this file's content at commit `a380ec3`, the
> last commit *before* the P0-4 move. The current `3a8c7886…` differs from it by **exactly one line**:
> line 82's comment, which read `Mirrors examples/platformer_play.py's` and now reads
> `Mirrors canon.packs.platformer.play's`. That path stopped existing when this row moved the play
> harness into the wheel, so the old comment pointed at a deleted file. It was a recorded, deliberate
> re-baseline, not a regression — `diff` against the pre-move content shows that one comment line and
> nothing else, at identical line and byte counts. The byte-identity invariant held.
>
> **One real consequence:** engine stamps depend on this hash, so a pack created *before* the
> re-baseline reports its engine copy as drifted from the template, once. That is expected and
> self-clearing, not a bug to chase.

## A. Integrity

- [x] **A1 — The tree.** `git status --short` shows the `examples/{platformer_pack,mazeworld_pack,graphics_specs}` files as deletes and `src/canon/packs/` as untracked. `git add -A` will record them as renames (your call, I do not run git). Note the index still holds the other session's staged versions of `art_phases.py`, `ops.py`, `vlm_qa.py` at the OLD paths until you stage the move.
- [x] **A2 — Nothing in src/ reaches outside the package:**

```bash
grep -rn "parents\[2\]\|sys\.path\|examples\." src/canon | grep -v "^src/canon/packs/platformer/godot_template" ; echo "expect no output above"
```

- [x] **A3 — Ruff clean, packaging tests green:**

```bash
uv run ruff check src/ examples/ tests/ && uv run python -m pytest tests/test_packaging.py -q -p no:cacheprovider
```

- [x] **A4 — The Godot template is byte-identical** (the sed pass touched a comment in it and was reverted; engine stamps depend on this):

```bash
shasum src/canon/packs/platformer/godot_template/godot/main.gd
```

      Expected `3a8c7886ace31616589c0cc633af13f5e2f115b6`.



## B. The gate — wheel acceptance (fresh venv, no checkout)

- [x] **B1 — Build and install into a throwaway venv:**

```bash
uv build && uv venv /tmp/canon_wheel_venv --python 3.12 && uv pip install --python /tmp/canon_wheel_venv/bin/python "canon-ai[cli,platformer] @ file://$(pwd)/$(ls dist/canon_ai-*.whl | tail -1)"
```

      (One harmless warning: typer has no `all` extra any more; see decision list.)

- [x] **B2 — From a neutral directory, every verb runs at $0:**

```bash
cd /tmp && /tmp/canon_wheel_venv/bin/canon --version && /tmp/canon_wheel_venv/bin/canon world new /tmp/wheel_world --seed 7 && /tmp/canon_wheel_venv/bin/canon pack info /tmp/wheel_world | head -5 && /tmp/canon_wheel_venv/bin/canon level validate /tmp/wheel_world --level l1 | head -3 && /tmp/canon_wheel_venv/bin/canon db types /tmp/wheel_world | head -3 && /tmp/canon_wheel_venv/bin/canon world estimate --stages 1 | head -3
```

      `world new` defaults are fake/none backends, so nothing is spent.

- [x] **B3 — The moved runner works as a module and the shim still works:**

```bash
/tmp/canon_wheel_venv/bin/python -m canon.packs.platformer.run_slice --help | head -3 && uv run python examples/run_platformer_slice.py --help | head -3
```

- [x] **B3b — The play harness runs from the wheel** (install the `play` extra for pygame) and the shim still works:

```bash
uv pip install --python /tmp/canon_wheel_venv/bin/python "canon-ai[play] @ file://$(pwd)/$(ls dist/canon_ai-*.whl | tail -1)" && /tmp/canon_wheel_venv/bin/python -m canon.packs.platformer.play --help | head -2 && uv run python examples/platformer_play.py --help | head -2
```

- [x] **B4 — Byte-identity across the move.** A checkout-generated tree and a wheel-generated tree with the same seed are identical outside the observability files:

```bash
uv run canon world new /tmp/checkout_world --seed 7 && diff -rq /tmp/checkout_world /tmp/wheel_world --exclude=generation_stats.json --exclude=.canon --exclude=bible.json --exclude=log.jsonl ; echo "expect no differences above"
```



## C. Cradle

- [x] **C1 —** `run_canon_module` **is gone** and the three estimate commands use `run_canon`:

```bash
grep -n "run_canon_module" ~/Documents/projects/cradle/src-tauri/src/lib.rs ; echo "expect no output"
```

- [x] **C2 — Estimates still work in the app.** With `CANON_BIN` pointing at this checkout's `.venv/bin/canon`, open cradle, start a New Project, change a count: the live estimate updates. Open a level and trigger a level-scope estimate (any paid button's confirm shows a number; cancel it).
- [x] **C3 — ▶ Play a level still works** (cradle now spawns `python -m canon.packs.platformer.play`; the interpreter is still derived from `CANON_BIN`'s venv until P0-11 bundles one).



## D. Decisions (also listed in chat)

- [x] DECIDED 2026-09-01: module renamed to `canon.packs.dungeon` (applied).
- [x] DECIDED 2026-09-01: `world new` stays a subprocess (`python -m canon.packs.platformer.run_slice`).
- [x] DECIDED 2026-09-01: play harness moved into the wheel as `canon.packs.platformer.play` (applied).
- [x] `typer[all]` → `typer` in the cli extra to silence the install warning (open).



## E. On approval

- [x] Say "P0-4 approved". I flip row 4 in the Phase 0 table and master row P0-4 with a Built summary. Full-suite numbers after this row, P0-3/A8 and your decisions: canon 2670 passed, 4 skipped, 1 deselected; cradle 210 passed; ruff, tsc, cargo check clean.