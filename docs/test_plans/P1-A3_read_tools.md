# Human test plan — row P1-A3, read-tier tools

**Row:** master §3.1 stage 3, `P1-A3 [B]` · **Gate:** answers pack questions, zero writes; token
budget measured on the widest level — **met on a generated fake tree** (your choice), 2026-09-01.
No design source (service internals).

What shipped: `src/canon/agent/tools_read.py` registering 12 `auto`-tier tools into A2's registry
(`describe_pack`, `describe_level`, `export_level` with `window`, `validate_level`, `db_types`,
`db_schema`, `db_row`, `get_history`, `get_versions`, `list_pack_files`, `read_pack_file`,
`search_pack`), each a thin wrapper over a canon function, path-guarded to the pack root; two
canon verbs, `canon level describe` and `canon level export … --window x0,y0,w,h` (the grid group
too); the eval corpus now uses the real tool specs (only `view_asset` remains a stand-in until A7);
`canon agent serve` registers the tools on start.

| Measurement (widest generated level, 123 × 26) | chars | ≈ tokens (chars ÷ 4) |
|---|---|---|
| `describe_level` | 1,825 | 456 |
| `export_level` 24 × 16 window | 9,350 | 2,337 |
| `export_level` full | 32,831 | 8,207 |

The test asserts describe ≤ 2,500 and the window ≤ 6,000 approximate tokens.

## A. Integrity

- [ ] **A1 — Tests** (34 tool tests incl. the budget gate; the fixture generation takes ~11 s):

```bash
uv run python -m pytest tests/test_agent_tools_read.py tests/test_agent_service.py -q -p no:cacheprovider
```

- [ ] **A2 — See the budget numbers yourself:**

```bash
uv run python -m pytest tests/test_agent_tools_read.py -q -p no:cacheprovider -k TokenBudget -s | grep "token budget"
```

## B. Answers pack questions, zero writes

- [ ] **B1 — Describe a level** on a fresh $0 pack (compact JSON: dims, spawn/exit, tile histogram by category, platform bands, entity/item/trigger summaries, overrides, validation verdict):

```bash
uv run canon world new /tmp/a3_pack --seed 7 > /dev/null && uv run canon level describe /tmp/a3_pack --level l2 | head -c 900; echo
```

- [ ] **B2 — A windowed export** carries a `window` field and only the cells inside it:

```bash
uv run canon level export /tmp/a3_pack --level l2 --window 10,0,24,16 | python3 -c "import json,sys; d=json.load(sys.stdin)['level']; print(d['window'], len(d['grids']['collision']), 'rows x', len(d['grids']['collision'][0]), 'cols; full dims', d['grid_width'], d['grid_height'])"
```

- [ ] **B3 — Zero writes.** Hash the pack, run every read through the service, hash again:

```bash
find /tmp/a3_pack -type f -not -path "*/.canon/agent/*" -exec shasum {} + | shasum
```

      Then run the A2 checklist's B2/B3 against `/tmp/a3_pack` with a script whose turn calls `describe_level` (`[[{"type":"tool_use","name":"describe_level","input":{"level_id":"l2"}}],[{"type":"text","text":"done"}]]`), and re-run the hash: only `.canon/agent/` changed.

- [ ] **B4 — The path guard refuses escapes** (each returns a named refusal, never a file): through the service or `uv run python -c`, call `read_pack_file` with `../../etc/hosts`, an absolute path, and `.canon/objects/…`; `list_pack_files` reports what the guard skipped instead of a silent zero.
- [ ] **B5 — Dungeon rooms answer "not yet"** as structured JSON naming P0-8 for `describe` and `--window`:

```bash
uv run canon level describe tests/reference/fixtures/cradle_mazeworld_scifi --level room_0; echo "exit=$?"
```

## C. Decisions (also listed in chat)

- [ ] `world_map` and `engine_status` reads are left to the rows that need them (A4's `edit_world_map`, A7.5's engine gate) rather than registered now. Confirm the split.
- [ ] `get_versions` takes a full artifact id; a `level_id`/`step` convenience form can be added later without renaming the tool. Confirm.

## D. On approval

- [ ] Say "A3 approved". I flip A3 in master §3.1 and Phase 1 §8 with a Built summary.
