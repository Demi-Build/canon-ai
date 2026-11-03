# Human test plan — row P0-5, W1 P2 read: loaders, one grid export, the read-only room view

**Row:** master §3.1 stage 3, `P0-5 [A]` · **Gate:** maze renders in Blocks view; suites green ·
**Design source:** W1 §8.2; the room view reuses the level-editor screen of
`cradle/design_handoff_editor_worldmap_start` (board `04 Level editor.html`, README "Screen 3")
with no new chrome. **Built 2026-09-01** to the P0 paper (P.6.3 read path, P.6.3a bundle, P.6.4).

What shipped: `src/canon/packs/dungeon/loaders.py` (`load_rows` per layout + `skeleton_view`
inverting every P.1 rename; each dungeon `EntityKind.loader` seeded), `src/canon/adapters/dungeon_read.py`
(`export_room_bundle`, the P.6.3a `LevelBundle` shape, warnings never block), `canon grid export`
with `level export` as its alias (one dispatch table in `canon.adapters`), the dungeon tile registry
`src/canon/packs/dungeon/tiles.json`; cradle: rooms route to `LevelDetail readOnly` (blocks mode,
no edit callbacks, Dock tabs from `pack info` placements, every disabled control carries a reason),
`WorldSummary.pack_info`, `export_level` hands canon the pack dir. Zero writes anywhere.

Note: `cradle/bibles/mazeworld_5_room_demo` on this machine holds only portraits and is not a
pack; the demo world that resolves is `cradle/bibles/mazeworld_scifi` (the app's Start-page demo
is whatever `resource_dir/demo` holds in a packaged build). Use `mazeworld_scifi` below.

## A. Canon

- [x] **A1 — Tests:**

```bash
uv run python -m pytest tests/test_dungeon_read.py tests/test_packs.py tests/test_cli.py -q -p no:cacheprovider
```

- [x] **A2 — One export, both shapes.** The two verbs are byte-identical on a room:

```bash
uv run canon grid export tests/reference/fixtures/cradle_mazeworld_scifi --level room_0 | md5 && uv run canon level export tests/reference/fixtures/cradle_mazeworld_scifi --level room_0 | md5
```

- [x] **A3 — The room bundle has the platformer bundle's shape** (34 keys, plus `room` and `warnings`), engine truth rendered:

```bash
uv run canon grid export tests/reference/fixtures/cradle_mazeworld_scifi --level room_0 | python3 -c "import json,sys; d=json.load(sys.stdin)['level']; print(len(d),'keys; dims',d['grid_width'],'x',d['grid_height'],'; spawn',d['spawn'],'exit',d['exit']); print('entities',len(d['entities']),'items',len(d['items']),'triggers',len(d['triggers']),'warnings',d['warnings']); print('room',d['room']['environment'],d['room']['gate_encounter_id'])"
```

      Expected: 40 × 30, 16 entities, 77 items, 96 triggers, no warnings, environment `ruins`.

- [x] **A4 — Unknown room is a structured error** (exit 1, JSON body naming the missing file):

```bash
uv run canon grid export tests/reference/fixtures/cradle_mazeworld_scifi --level room_9; echo "exit=$?"
```

- [x] **A5 — Reads write nothing.** Hash the fixture before and after an export:

```bash
find tests/reference/fixtures/cradle_mazeworld_scifi -type f -exec shasum {} + | shasum && uv run canon grid export tests/reference/fixtures/cradle_mazeworld_scifi --level room_0 > /dev/null && find tests/reference/fixtures/cradle_mazeworld_scifi -type f -exec shasum {} + | shasum
```

- [x] **A6 — Loaders invert the renames** (spot-check): an npc row's `type` reads back as `behavior_type` in the skeleton view:

```bash
uv run python -c "from canon.packs import PACKS; from canon.packs.dungeon.loaders import load_rows, skeleton_view; e=PACKS['dungeon'].entities['npc']; rows=load_rows('tests/reference/fixtures/cradle_mazeworld_scifi', e); r=next(iter(rows.values())); print(r['type'], '->', skeleton_view(r, e).get('behavior_type'))"
```



## B. Cradle — the gate's hand test

Run the app with `CANON_BIN` pointing at this checkout's `.venv/bin/canon`.

- [x] **B1 — Suites:**

```bash
cd ~/Documents/projects/cradle && npx vitest run --reporter=dot && npx tsc --noEmit && (cd src-tauri && cargo check)
```

- [x] **B2 — The maze renders.** Open `bibles/mazeworld_scifi` → Rooms → `room_0`. The Overview tab shows the 40×30 maze in Blocks view: walls in the ruins wall colour on the sunken surface, grid on, NPC squares, item markers, event diamonds, spawn and door markers. The Minimap works; pan and zoom work.
- [x] **B3 — Read-only is honest, not hidden.** Paint/fill/erase/place tools, the mode switch (art/overlay), variants, bounds and the audio lane are visible but disabled, and hovering each shows its reason in product words (no roadmap ids). ⌘K edit commands are greyed with reasons.
- [x] **B4 — Dock tabs come from the pack.** The Dock shows NPCs / Events / Items in that order with counts from the bundle; selecting an entry highlights the placement on the canvas; the tray shows the room facts (environment, gate, quests, monsters) and, for a selection, the row with an EntityLink to it (events link to their monsters). No drag, no delete.
- [x] **B5 — The old room details survive.** The "Details" tab still shows the room's layout/story/contents overview exactly as before.
- [x] **B6 — Platformer unchanged.** Open your platformer project → a level: editing tools, palette, audio lane and Dock tabs behave exactly as before this build.
- [x] **B7 — devMock parity.** `npm run dev` in the browser: the mock world's rooms path renders a synthetic room in blocks mode.



## C. Decisions (also listed in chat)

- [ ] Room `describe` and `--window` (A3's verbs) return a structured "not yet" naming P0-8 for rooms; P0-8 builds them. Confirm.
- [ ] `entities[].size` is the number `1` (the shared bundle's type), not `[1, 1]` as the paper's example wrote; the paper is the one to correct. Confirm.



## D. On approval

- [ ] Say "P0-5 approved". I flip row 5 in the Phase 0 table and master row P0-5 with a Built summary. Suite numbers after Stage 3: canon 2800 passed, 4 skipped, 1 deselected; cradle 227 passed; ruff, tsc, cargo check clean.