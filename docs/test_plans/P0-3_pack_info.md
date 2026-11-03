# Human test plan — row P0-3, W1 P1: `pack_type`, `resolve_pack`, `canon pack info`, cradle `world_kind`

**Row:** master §3.1 stage 2, `P0-3 [A]` · **Gate:** suites green; platformer byte-identical
(only the additive manifest `pack_type` key); both demo worlds load by hand.
**Built 2026-09-01** to the P0 paper (P.3 shapes, P.4.1 four-tier resolution, P.4.6 output).

What shipped: `src/canon/packs/spec.py` (the four dataclasses from the paper),
`src/canon/packs/platformer/spec.py` + `src/canon/packs/dungeon/spec.py` (the two seeds, the
platformer's entities derived from `DB_TYPES`), `resolve_pack` in `src/canon/packs/__init__.py`,
`PipelineContext.pack_type` + `"pack_type"` as the first manifest key in both writers,
`canon pack info`, and in cradle `WorldSummary.world_kind` (from `canon pack info`), one
`pack_kind()` in `data.rs` replacing five `is_platformer_pack` sites, and the four TS detectors
now reading `world_kind` (store, LeftNav, EntityTable, RecentTile) with devMock parity.

## A. Canon

- [x] **A1 — Tests:**

```bash
uv run python -m pytest tests/test_packs.py tests/test_cli.py tests/test_phase_manifest.py -q -p no:cacheprovider
```

- [x] **A2 —** `pack info` **on the legacy dungeon fixture** resolves by shape (no stamp anywhere) and counts rows from disk:

```bash
uv run canon pack info tests/reference/fixtures/cradle_mazeworld_scifi | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['pack_type'], d['source'], {k: v['count'] for k, v in d['entities'].items()})"
```

      Expected `dungeon shape` and non-zero counts for every kind incl. `room` (1 on this fixture, counted from `rooms/room_*/maze.json` since legacy trees have no `rooms.json` — decided 2026-09-01; the bundled 5-room demo reads 5).

- [x] **A3 — A fresh platformer tree carries the stamp as its FIRST manifest key** and resolves by manifest:

```bash
uv run canon world new /tmp/p03_world --seed 7 && head -c 60 /tmp/p03_world/manifest.json && echo && uv run canon pack info /tmp/p03_world | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['pack_type'], d['source'], list(d['entities']), d['engines'])"
```

      Expected `{"pack_type": "platformer", ...` then `platformer manifest ['enemy', 'item'] [{'id': 'godot', 'primary': True}]`.

- [x] **A4 — Read-both shim:** delete the key and it still resolves (by shape), with no file written:

```bash
python3 - <<'EOF'
import json; p='/tmp/p03_world/manifest.json'; d=json.load(open(p)); d.pop('pack_type'); json.dump(d, open(p,'w'))
EOF
uv run canon pack info /tmp/p03_world | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['pack_type'], d['source'])"; ls /tmp/p03_world/.canon 2>/dev/null; echo "(no .canon expected)"
```

- [x] **A5 — Unknown dir is a named error:**

```bash
mkdir -p /tmp/not_a_pack && uv run canon pack info /tmp/not_a_pack; echo "exit=$?"
```

- [x] **A6 — Byte-identity.** Two runs with the same seed are identical (the stamp is in both):

```bash
uv run canon world new /tmp/p03_a --seed 11 && uv run canon world new /tmp/p03_b --seed 11 && diff -rq /tmp/p03_a /tmp/p03_b --exclude=generation_stats.json --exclude=bible.json --exclude=log.jsonl --exclude=.canon; echo "expect no differences"
```



## B. Cradle (the hand test in the gate)

`load_world` now asks `canon pack info` for the kind and fails with a named message if `canon` is
not reachable, so run the app with `CANON_BIN` set (or `canon` on PATH) exactly as for any verb.

- [x] **B1 — Suites:**

```bash
cd ~/Documents/projects/cradle && npx vitest run --reporter=dot && npx tsc --noEmit && (cd src-tauri && cargo check)
```

- [x] **B2 — Demo world 1, dungeon.** Open the bundled MazeWorld demo (start page → demo). It opens; LeftNav shows the MazeWorld groups (Rooms, NPCs, …) and none of the platformer-only entries (Tilesets / Play game); EntityTable for Items shows the MazeWorld columns; the start page's recent tile counts rooms and NPCs.
- [x] **B3 — Demo world 2, platformer.** Open your platformer project. LeftNav shows Tilesets and the ▶ Play game entry; Items shows the platformer columns and the `+ new row` control; the recent tile counts levels and enemies.
- [x] **B4 — Old recents.** A recent saved before this build (no `world_kind`) still renders sensible counts (the old sniff is the fallback only for those).
- [x] **B5 — Canon missing.** Optional: launch with `CANON_BIN` unset and PATH without `canon`, open a world, and confirm the error names `CANON_BIN` / PATH instead of opening a mis-detected world. Restore the env afterwards.
- [x] **B6 — devMock parity.** `npm run dev` (browser, no Tauri): the mock world opens as a platformer with the same LeftNav shape.



## C. Decisions (also listed in chat)

- [x] DECIDED 2026-09-01: room count falls back to `rooms/room_*/maze.json` on legacy trees (applied).
- [x] `load_world` requires canon (no silent shape guess) — confirm.
- [x] Dungeon `schemas/<kind>.json` files do not exist yet (P.1.11 authors them at P0-6), so `schema_source` is null for dungeon kinds until then — acknowledge.



## D. On approval

- [x] Say "P0-3 approved". I flip row 3 in the Phase 0 table and master row P0-3 with a Built summary.