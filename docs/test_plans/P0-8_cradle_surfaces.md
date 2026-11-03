# Human test plan — row P0-8, W1 P4 cradle surfaces

**Row:** master §3.1 stage 5, `P0-8 [A]` · **Gate:** edit every type end to end on a dungeon world.
**Design source:** the level-editor screen of `design_handoff_editor_worldmap_start` (board 04) as
the explicit pattern note (master §8 Q8), plus a maze cell palette and roll buttons mirroring the
platformer's. **Built 2026-09-01**; two defects the verifier found afterwards are fixed in Stage 6
and re-proved there (a step-scoped restore, and `db update --type room` on legacy trees).

What shipped: canon `adapters/dungeon_write.py` (the room writer on the P0-6 core),
`packs/dungeon/rolls.py`, `canon grid apply-edit` / `import-grids` / `roll` / `restore` serving
rooms; cradle RowEditor driven by `db schema` data instead of its old two-entry type map, the room
editor made writable on the same screen P0-5 built, and History working on dungeon artifacts.

## A. Suites

```bash
uv run python -m pytest tests/test_dungeon_write.py tests/test_dungeon_read.py tests/test_db_core.py -q -p no:cacheprovider
```

```bash
cd ~/Documents/projects/cradle && npx vitest run --reporter=dot && npx tsc --noEmit && npx eslint src
```



## B. The gate, in the app

Run cradle with `CANON_BIN` set, open `bibles/mazeworld_scifi`.

- [ ] **B1 — every one of the nine kinds edits.** NPCs, monsters, items, quests, events, classes,
      rooms, music, sfx: open a row, change a field, save, and see it land. The form is built from
      the pack's own schema, so hidden fields do not appear, protected fields are visible but not
      editable and say why, and a field the grid owns links you to the room canvas instead of
      letting you type a stale value.
- [ ] **B2 — list containers.** On an NPC with a shop, add and remove an inventory row; on a class,
      add an ability. These use the address grammar the write core accepts.
- [x] **B3 — the room canvas is writable now.** Paint walls and floor with the two-swatch palette,
      drag an NPC, place an item from the Dock, drag spawn and the door. Each save re-reads from
      disk, so what you see is what canon wrote.
- [ ] **B4 — monsters place through encounters** (your decision). Drop a monster on a cell: it
      creates or targets the combat event there and adds the monster to it. Two files change, both
      journaled, in one batch.
- [x] **B5 — the refusals are honest.** Painting a wall over a placement, dragging the door away
      from its gate, or placing on the spawn cell are each refused with a reason and change nothing.
      Resize stays disabled: 40×30 is an engine constant until the W2.0 pull-in.
- [x] **B6 — per-step rolls.** Whole room, layout, NPCs, events, items and monsters each re-roll
      and journal. Every one reads "$0, code only" and none raises a spend card, because none of
      them calls a model.
- [x] **B7 — History and restore.** The History tab shows both the grid and the placement steps.
      Restore an earlier version: the maze reverts, a new version is written, and nothing is
      deleted. After Stage 6's fix, restoring one step no longer discards the other step's edits.



## C. Decisions to confirm

- [ ] A room save that touches several wires writes `maze.json` once and journals once, with the
      wire's own kind when only one changed and a combined kind when several did. The platformer's
      per-key kinds come from per-key files. Confirm this collapse.
- [x] A layout roll settles a walled-in door to the nearest open cell and warns, rather than
      leaving the room unopenable. Confirm.



## D. On approval

- [ ] Say "P0-8 approved". Suite after Stage 5: canon 3212 passed, cradle 368 passed.