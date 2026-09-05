# Human test plan — row P0-6, W1 P3 write: the write core, `db define`/`db evolve`, `world update`

**Row:** master §3.1 stage 4, `P0-6 [A]` · **Gates:** platformer byte-identical; success criterion 6
(add a field and define a type, zero code) demoed. **Both met 2026-09-01**, verified independently.

What shipped: `src/canon/write_core.py` (the one doctrine-1 pipeline: resolve → wall → fail-closed
validate → warnings → `user_edited` stamp → journal per-field diff → CAS), with `db_ops.py`,
`registry_ops.py`, `world_ops.py`, `db_models.py` and `packs/rows.py` on top of it. All nine dungeon
kinds now write through the registry; `canon.packs.platformer.ops` keeps every public name as a
thin wrapper. New verbs: `db define`, `db evolve`, `registry set`, `world update`.

## A. Suites and byte-identity

```bash
uv run python -m pytest tests/test_db_core.py tests/test_packs.py tests/test_platformer_ops.py -q -p no:cacheprovider
```

- [x] **A1** the above is green (35 + tests in `test_db_core.py` alone).
- [x] **A2 — the platformer did not move.** Every A/B tree comparison passes:

```bash
uv run python -m pytest tests/test_platformer_slice.py tests/test_multistage.py tests/test_platformer_dag.py tests/test_steplog.py -q -p no:cacheprovider
```



## B. Success criterion 6, zero code (the gate)

```bash
zsh /private/tmp/claude-501/-Users-wolfgangblack-Documents-projects-canon-ai/922ec9b0-5130-4fd3-b539-b0cda486e6fe/scratchpad/sc6_demo.sh /tmp/sc6
```

Or by hand, which is worth doing once because it is the whole thesis of the phase:

```bash
uv run canon world new /tmp/sc6 --name "SC6 Demo" --seed sc6 && uv run canon db schema /tmp/sc6 --type enemy --actor you --set '{"fields": {"temperament": {"choices": [["calm", 3], ["feral", 1]]}}}'
```

```bash
uv run canon db define /tmp/sc6 --type player_ability --actor you --set '{"label": "Abilities", "layout": {"mode": "collection", "path": "abilities/abilities.json", "format": "array"}, "id_field": "id", "id_alloc": {"base": 7000}, "llm_fields": ["name", "description"], "schema": {"fields": {"tier": {"choices": [["minor", 3], ["major", 1]]}}}}'
```

```bash
uv run canon db new /tmp/sc6 --type player_ability --actor you --fields '{"name": "Dash"}' && uv run canon pack info /tmp/sc6 | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['source'], d['entities']['player_ability'])"
```

- [x] **B1** the new type exists with `count 1` and `schema_source pack`, and `pack info` now
      answers from the registry (`source: registry`) because `db define` synthesized it.
- [x] **B2** `uv run canon level history /tmp/sc6` lists `db_schema`, `registry_synthesize`,
      `db_define`, `db_new`, `db_update`. No code was written to add a type.



## C. The dungeon comes online (adopt-on-write, collections, refusals)

```bash
cp -r tests/reference/fixtures/cradle_mazeworld_scifi /tmp/dun && ls /tmp/dun/.canon 2>/dev/null; echo "(absent is correct)"
```

```bash
uv run canon db update /tmp/dun --type npc --id 1000 --actor you --set '{"name": "Mira Renamed", "availability": "night"}' && ls /tmp/dun/.canon
```

- [x] **C1** the edit lands and `.canon/journal.jsonl` plus `objects/` appear on first mutation
      (adopt-on-write, no migration).
- [x] **C2 — the wall holds.** Each of these is refused with its reason and writes nothing:

```bash
uv run canon db update /tmp/dun --type npc --id 1000 --actor you --set '{"id": 5}'; uv run canon db update /tmp/dun --type npc --id 1000 --actor you --set '{"x": 3}'; uv run canon db update /tmp/dun --type npc --id 1000 --actor you --set '{"shop_inventory": []}'
```

      Expected in order: protected (identity / provenance / asset plumbing); routed (owned by the
      grid, use that surface); container (write a leaf, not the whole container).

- [ ] **C3 — restore.** `canon level history /tmp/dun`, then restore the npc collection to its
      previous version; the file reverts, a NEW version is written, nothing is deleted.



## D. World update

```bash
uv run canon world update /tmp/dun --actor you --set '{"story.title": "A Renamed Tale"}' && uv run canon level history /tmp/dun | tail -5
```

- [x] **D1** three files change in one batch (`world_bible.json`, the `story/story.json` mirror,
      the `manifest.json` mirror), each its own journal event carrying `mirror_of`.
- [x] **D2** `manifest.movement` and friends are refused: tuning is Phase 2's `tune set`.



## E. Decisions to confirm

- [x] `db complete` on a dungeon kind answers a structured "not yet" naming its owning row rather
      than inventing per-row completion prompts. Confirm.
- [x] A row restore on a collection kind restores the whole file and says so ("restores
      npcs/npcs.json (79 rows)"), the file-level CAS granularity accepted in §12. Confirm.



## F. On approval

- [x] Say "P0-6 approved". Suite after Stage 4: canon 3007 passed, 4 skipped, 1 deselected.