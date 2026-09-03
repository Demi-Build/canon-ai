"""Per-step 🎲 rolls for one dungeon room — the code-only, $0 half of the
room editor (P0 paper P.6.3 "per-step rolls", P.6.4's P0-8 column; row P0-8).

Five steps, ONE entry point (``roll_room``), dispatching on ``step``:

======== =================================================================
step     what it re-runs
======== =================================================================
whole    ``MazeLayoutPhase`` + ``MazeworldPlacementPhase``: carve, place
         every kind, re-designate the gate and re-flag the gate event —
         two journaled artifacts (``maze.json`` and ``events/events.json``)
         under one ``batchId``
layout   ``generate_maze(width, height, rng)`` alone; the placements keep
         their cells and are re-stamped, and any that the new maze walled
         in are NAMED in the warnings (doctrine 10 — warn, never block;
         doctrine 6 — nothing is deleted)
npcs     the ``npc`` branch of ``_place_one``
events   the ``event`` branch
items    the ``item`` branch
monsters the placement phase's monster sampling, re-rolled for ONE
         encounter's ``monster_ids`` (P.9 G4)
======== =================================================================

Every roll is **pure code and $0** — no LLM, no provider, no spend dialog
(doctrine 3; the button says "$0 — code only"). Every roll **journals**
through the P0-6 write core, and **nothing regenerates on its own**
(doctrine 6): a roll happens because a human pressed the button.

What this extends, rather than re-implements: ``canon.layout.maze.
generate_maze`` and the placement branches ``placement.place_entities``
(lifted out of ``MazeworldPlacementPhase._place_one`` by this row so the
phase and the roll share one sampler), the phase's own ``_designate_gate`` /
``_move_door_adjacent``, and ``adapters.dungeon_write``'s validation + commit
path. Single-kind rolls take the per-kind sub-seed ``derive_seed(base,
"placement", map_id, kind)`` (P.9 G8); an unpinned roll salts the base the
way the platformer's per-level ops do, so pressing 🎲 twice gives two
answers and the seed used comes back in the result.
"""

from __future__ import annotations

import random
import secrets
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from canon import provenance
from canon.adapters.dungeon_write import (
    STEP_GRID,
    STEP_PLACEMENTS,
    RoomContext,
    _gate_cell,
    pack_seed,
    read_room_json,
    room_artifact_id,
    room_context,
    set_encounter_monsters,
    write_room,
)
from canon.layout.maze import generate_maze
from canon.packs.dungeon.placement import (
    PLACEMENT_KEYS,
    MazeworldPlacementPhase,
    place_entities,
)
from canon.packs.rows import read_json
from canon.pipeline.rng import derive_rng, derive_seed
from canon.write_core import write_document

__all__ = ["ROLL_STEPS", "roll_room"]

#: The step vocabulary — OPEN data the CLI lists in its help, never a
#: ``Literal`` (master §3.0-B's never-a-literal-union rule). ``whole`` first:
#: it is the one that rebuilds a room end to end.
ROLL_STEPS: tuple[str, ...] = ("whole", "layout", "npcs", "events", "items", "monsters")

#: roll step → the EntityKind whose placement branch it re-runs.
_STEP_KIND: dict[str, str] = {"npcs": "npc", "events": "event", "items": "item"}


class _Stub(SimpleNamespace):
    """The three attributes ``place_entities`` reads off a bible entity stub
    (``entity_type`` / ``entity_id`` / ``name``) — rebuilt from the room's own
    index row so a roll works on a pack read back from disk, with no bible in
    memory (Phase 1's Bible-completeness work is what removes this shim)."""


def _room_row(ctx: RoomContext) -> dict:
    """The room's index row: ``rooms/rooms.json[<id>]`` when the tree has the
    index, else the ``world_bible.json.rooms`` mirror the legacy trees carry
    (the same two sources ``dungeon_read`` reads)."""
    rows = ctx.rows(ctx.grid.kind)
    row = rows.get(ctx.room_id)
    if isinstance(row, dict):
        return row
    bible = read_json(ctx.pack / "world_bible.json")
    if isinstance(bible, dict) and isinstance(bible.get("rooms"), dict):
        mirror = bible["rooms"].get(ctx.room_id)
        if isinstance(mirror, dict):
            return mirror
    return {}


def _room_ids(ctx: RoomContext) -> list[str]:
    """Every room id in pack order — the placement phase's ``is_final`` (the
    last room's gate is the climax boss) needs to know which room is last."""
    rows = ctx.rows(ctx.grid.kind)
    if rows:
        return list(rows)
    bible = read_json(ctx.pack / "world_bible.json")
    if isinstance(bible, dict) and isinstance(bible.get("rooms"), dict):
        return list(bible["rooms"])
    return [ctx.room_id]


def _stubs(ctx: RoomContext, row: dict, maze: dict | None = None) -> list[_Stub]:
    """The room's entity roster as placement stubs, in the row's own order:
    npc / item / monster from their lore buckets, events from the
    ``encounters`` id list (the room row calls events encounters — P.1.7),
    UNIONED with whatever ``maze.json`` already places.

    The placements are engine truth (the stance ``dungeon_read`` takes when
    the grid and the sidecar disagree): a room row whose buckets are empty —
    every legacy tree, whose ``world_bible`` mirror lists no ``encounters`` —
    would otherwise make a roll silently DELETE the 96 encounters the room
    actually has (doctrine 6: nothing is deleted). A stub only needs its kind
    and id to be re-placed; the name is chrome."""
    out: list[_Stub] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, entity_id: Any, name: str = "") -> None:
        if entity_id is None:
            return
        marker = (kind, str(entity_id))
        if marker in seen:
            return
        seen.add(marker)
        out.append(_Stub(entity_type=kind, entity_id=entity_id, name=name))

    for bucket, kind in (("npcs", "npc"), ("items", "item"), ("monsters", "monster")):
        for entry in row.get(bucket) or []:
            if isinstance(entry, dict):
                add(kind, entry.get("entity_id"), entry.get("name") or "")
    for event_id in row.get("encounters") or []:
        add("event", event_id)
    if maze is not None:
        for key, raw_block in ctx.grid.placements.items():
            block = dict(raw_block)
            kind = str(block.get("kind", key))
            for _x, _y, row_id in _entries_of(maze, key, block):
                add(kind, row_id)
    return out


def _placed_counts(ctx: RoomContext, maze: dict) -> dict[str, int]:
    """How many cells each kind occupies right now — the "did the roll drop
    anything?" baseline (doctrine 10: name the loss, never write it silently)."""
    out: dict[str, int] = {}
    for key, raw_block in ctx.grid.placements.items():
        block = dict(raw_block)
        out[str(block.get("kind", key))] = len(_entries_of(maze, key, block))
    return out


def _drop_warning(kind: str, before: int, after: int) -> list[str]:
    """Name a roll that re-placed FEWER cells than the room had (doctrine 10:
    warn loudly, never write a loss silently). The roster is the room row's
    lore bucket unioned with what is on disk, so a shortfall means the sampler
    ran out of open cells — not that the row forgot the entities."""
    if after >= before:
        return []
    return [
        f"the {kind} roll placed {after} of the {before} {kind} cells the room had — "
        f"{before - after} could not be re-placed on the current maze; re-roll the layout or "
        "clear space, then re-roll this kind"
    ]


def _effective_seed(base: str, room_id: str, seed: str | None) -> str:
    """An explicit pin reproduces exactly; an unpinned roll salts the pack
    seed so two presses of 🎲 differ — the platformer's
    ``ops._effective_seed`` rule, and the value is reported for replay."""
    return str(seed) if seed else f"{base}:{room_id}:{secrets.token_hex(4)}"


def _points(ctx: RoomContext, maze: dict) -> tuple[tuple[int, int], tuple[int, int]]:
    start_key, door_key = ctx.point(0) or "player_start", ctx.point(1) or "door_position"
    start = maze.get(start_key) or [1, 1]
    door = maze.get(door_key) or [ctx.width - 2, ctx.height - 2]
    return (int(start[0]), int(start[1])), (int(door[0]), int(door[1]))


def _placement_block(ctx: RoomContext, kind: str) -> tuple[str, dict]:
    for key, block in ctx.grid.placements.items():
        if block.get("kind") == kind:
            return key, dict(block)
    raise ValueError(f"this grid places no {kind!r} rows")


def _entries_of(maze: dict, key: str, block: dict) -> list[tuple[int, int, str]]:
    from canon.adapters.dungeon_write import _entries

    return [(e["x"], e["y"], e["id"]) for e in _entries(maze.get(key), block)]


def _settle_door(ctx: RoomContext, maze: dict) -> list[str]:
    """Keep the door on an OPEN cell after a re-carve.

    The generation pair never leaves a walled-in door: ``MazeLayoutPhase``
    seeds it at the interior corner the carver reaches, and
    ``_designate_gate`` then nudges it next to the gate encounter. A roll
    carves around the room's EXISTING door, which the new maze may bury — so
    when it does, the door moves to the nearest open cell and says so. The
    gate-adjacency invariant (P.9 G5) is re-established by the gate
    designation on a whole-room roll; a layout-only roll keeps the door where
    it is whenever the carve left it walkable.

    The candidate cell must be free of PLACEMENTS too: parking the door on the
    gate encounter's own cell is a state the hand-edit writer refuses from
    both sides, so the roll would have left a room neither path could repair."""
    door_key = ctx.point(1) or "door_position"
    value = maze.get(door_key)
    if not (isinstance(value, (list, tuple)) and len(value) == 2):
        return []
    grid = maze["grid"]
    dx, dy = int(value[0]), int(value[1])
    occupied = _occupied_cells(ctx, maze)
    if (
        0 <= dy < len(grid)
        and 0 <= dx < len(grid[dy])
        and grid[dy][dx] != 1
        and (dx, dy) not in occupied
    ):
        return []
    start = _points(ctx, maze)[0]
    gate = _gate_cell(ctx, maze)
    candidates = [
        (x, y)
        for y, row in enumerate(grid)
        for x, cell in enumerate(row)
        if cell == 0 and (x, y) != start and (x, y) not in occupied
    ]
    # P.9 G5 — the door belongs beside the gate encounter, so prefer a cell
    # that keeps that invariant; the hand writer refuses a free drag away.
    adjacent = (
        [c for c in candidates if abs(c[0] - gate[0]) + abs(c[1] - gate[1]) == 1]
        if gate is not None
        else []
    )
    best = min(
        ((abs(x - dx) + abs(y - dy), y, x) for x, y in (adjacent or candidates)),
        default=None,
    )
    if best is None:
        return ["the new maze has no open cell for the door — re-roll the layout"]
    _dist, y, x = best
    maze[door_key] = [x, y]
    return [
        f"the re-carved maze walled in the door at ({dx}, {dy}) — it moved to the nearest "
        f"open cell ({x}, {y})"
    ]


def _occupied_cells(ctx: RoomContext, maze: dict) -> dict[tuple[int, int], str]:
    """Every cell a placement holds right now, cell → a human label — the same
    view ``dungeon_write._placed_cells`` validates against."""
    out: dict[tuple[int, int], str] = {}
    for key, raw_block in ctx.grid.placements.items():
        block = dict(raw_block)
        kind = str(block.get("kind", key))
        for x, y, row_id in _entries_of(maze, key, block):
            out[(x, y)] = f"{kind} {row_id}"
    return out


def _move_entry(maze: dict, key: str, block: dict, row_id: str, old: tuple[int, int],
                new: tuple[int, int]) -> None:
    """Rewrite one placement's cell in ``maze.json``'s own stamped shape."""
    if block.get("shape") == "dict":
        raw = maze.get(key)
        if isinstance(raw, dict):
            raw[str(row_id)] = [new[0], new[1]]
        return
    raw = maze.get(key)
    if not isinstance(raw, list):
        return
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        if str(entry.get(str(block.get("id", "id")))) == str(row_id) and (
            entry.get("x"), entry.get("y")
        ) == old:
            entry["x"], entry["y"] = new[0], new[1]
            return


def _restamp(ctx: RoomContext, maze: dict) -> list[str]:
    """Re-apply every placement's grid encoding after the grid was rebuilt,
    RELOCATING the ones the new maze buried and NAMING every move.

    A roll is a code-only verb, so it must leave a document its own writer
    accepts (doctrine 1's fail-closed contract): a placement left inside a
    wall — or sharing the player start, the door, or another placement's cell
    — makes ``_validate_wire`` and ``import_room_grids`` refuse EVERY later
    save, including the very drag the old warning told the user to perform.
    Nothing is deleted (doctrine 6): the placement moves to the nearest free
    open cell and the warning says where it went (doctrine 10)."""
    warnings: list[str] = []
    grid = maze["grid"]
    start, door = _points(ctx, maze)
    claimed: set[tuple[int, int]] = {start, door}
    open_cells = [
        (x, y)
        for y, row in enumerate(grid)
        for x, cell in enumerate(row)
        if cell != 1
    ]
    for key, raw_block in ctx.grid.placements.items():
        block = dict(raw_block)
        stamp = block.get("grid_stamp")
        kind = str(block.get("kind", key))
        for x, y, row_id in _entries_of(maze, key, block):
            inside = 0 <= y < len(grid) and 0 <= x < len(grid[y])
            blocked = (not inside) or grid[y][x] == 1 or (x, y) in claimed
            if blocked:
                spot = min(
                    ((abs(cx - x) + abs(cy - y), cy, cx) for cx, cy in open_cells
                     if (cx, cy) not in claimed),
                    default=None,
                )
                if spot is None:
                    warnings.append(
                        f"{kind} {row_id} at ({x}, {y}) has nowhere open to go after the roll — "
                        "re-roll the layout"
                    )
                    continue
                _d, ny, nx = spot
                _move_entry(maze, key, block, row_id, (x, y), (nx, ny))
                warnings.append(
                    f"the roll buried {kind} {row_id} at ({x}, {y}) — it moved to the nearest "
                    f"free open cell ({nx}, {ny})"
                )
                x, y = nx, ny
            claimed.add((x, y))
            if stamp is not None:
                grid[y][x] = int(row_id) if stamp == "id" else int(stamp)
    return warnings


# ---------------------------------------------------------------------------
# The one entry point
# ---------------------------------------------------------------------------


def roll_room(
    pack_dir: str | Path,
    room_id: str,
    step: str,
    *,
    encounter_id: str | int | None = None,
    seed: str | None = None,
    actor: str = "user",
    session: str | None = None,
) -> dict:
    """Re-roll one step of *room_id*. Code-only, $0, journaled.

    Returns ``{room_id, step, seed, changed, changed_artifacts, warnings,
    no_change}``. Raises ``ValueError`` for an unknown step or a monsters
    roll with no encounter selected (fail-closed, with the reason).
    """
    if step not in ROLL_STEPS:
        raise ValueError(f"unknown roll step {step!r} — one of {list(ROLL_STEPS)}")
    ctx = room_context(pack_dir, room_id)
    base = pack_seed(ctx.pack)
    effective = _effective_seed(base, room_id, seed)
    cursor = len(provenance.all_events(ctx.pack))

    if step == "monsters":
        result = _roll_monsters(ctx, effective, encounter_id, actor=actor, session=session)
    elif step == "layout":
        result = _roll_layout(ctx, effective, actor=actor, session=session)
    elif step == "whole":
        result = _roll_whole(ctx, effective, actor=actor, session=session)
    else:
        result = _roll_kind(ctx, _STEP_KIND[step], effective, actor=actor, session=session)

    delta = provenance.all_events(ctx.pack)[cursor:]
    artifacts = sorted({str(e.get("artifact_id", "")) for e in delta if e.get("artifact_id")})
    return {
        "room_id": room_id,
        "level_id": room_id,
        "step": step,
        "seed": effective,
        "changed": bool(artifacts),
        "changed_artifacts": artifacts,
        "no_change": not artifacts,
        "cost_usd": 0.0,
        **result,
    }


def _roll_layout(ctx: RoomContext, seed: str, *, actor: str, session: str | None) -> dict:
    """🪄 layout — re-carve the maze, keep everything else. The phase's own
    call, seed for seed: ``derive_rng(config_seed, "maze_layout", map_id)``."""
    maze = read_room_json(ctx)
    start, door = _points(ctx, maze)
    carved = generate_maze(
        width=ctx.width,
        height=ctx.height,
        rng=derive_rng(seed, "maze_layout", ctx.room_id),
        player_start=start,
        door_position=door,
    )
    maze["grid"] = carved.grid
    warnings = _settle_door(ctx, maze) + _restamp(ctx, maze)
    written = write_room(
        ctx,
        maze,
        step=STEP_GRID,
        detail={"kind": "layout_roll", "room": ctx.room_id, "seed": seed,
                "dims": [ctx.width, ctx.height]},
        actor=actor,
        session=session,
        extra_warnings=warnings,
    )
    return {"warnings": written["warnings"], "updated": [] if written.get("no_change") else ["grid"]}


def _roll_kind(ctx: RoomContext, kind: str, seed: str, *, actor: str, session: str | None) -> dict:
    """🎲 npcs / events / items — one placement branch, re-sampled against the
    room's CURRENT grid with the per-kind sub-seed (P.9 G8). The other kinds'
    cells are reserved, so a single-kind roll never lands on them."""
    key, block = _placement_block(ctx, kind)
    maze = read_room_json(ctx)
    grid = maze["grid"]
    start, door = _points(ctx, maze)

    # Clear this kind's own stamps, reserve everything else.
    reserved: set[tuple[int, int]] = {start, door}
    for other_key, other_block in ctx.grid.placements.items():
        entries = _entries_of(maze, other_key, dict(other_block))
        if other_key == key:
            if block.get("grid_stamp") is not None:
                for x, y, _rid in entries:
                    if 0 <= y < len(grid) and 0 <= x < len(grid[y]):
                        grid[y][x] = 0
            continue
        reserved.update((x, y) for x, y, _rid in entries)

    row = _room_row(ctx)
    before = _placed_counts(ctx, maze).get(kind, 0)
    placed = place_entities(
        grid,
        [s for s in _stubs(ctx, row, maze) if s.entity_type == kind],
        random.Random(derive_seed(seed, "placement", ctx.room_id, kind)),
        exclude=reserved,
        kinds=(kind,),
    )
    maze[PLACEMENT_KEYS[kind]] = _jsonable(placed[PLACEMENT_KEYS[kind]])
    after = len(placed[PLACEMENT_KEYS[kind]])
    written = write_room(
        ctx,
        maze,
        step=STEP_PLACEMENTS,
        detail={"kind": f"{kind}_roll", "room": ctx.room_id, "seed": seed,
                "placed": after},
        actor=actor,
        session=session,
        extra_warnings=_drop_warning(kind, before, after),
    )
    return {
        "warnings": written["warnings"],
        "updated": [] if written.get("no_change") else [key],
    }


def _roll_whole(ctx: RoomContext, seed: str, *, actor: str, session: str | None) -> dict:
    """The whole room: carve, place every kind, re-designate the gate and
    re-flag the gate event — ``MazeLayoutPhase`` then
    ``MazeworldPlacementPhase``, on one room, journaled per written file
    under one ``batchId`` (P.7.3)."""
    maze = read_room_json(ctx)
    start, door = _points(ctx, maze)
    carved = generate_maze(
        width=ctx.width,
        height=ctx.height,
        rng=derive_rng(seed, "maze_layout", ctx.room_id),
        player_start=start,
        door_position=door,
    )
    maze["grid"] = carved.grid
    row = _room_row(ctx)
    before = _placed_counts(ctx, maze)
    stubs = _stubs(ctx, row, maze)
    placed = place_entities(
        maze["grid"],
        stubs,
        random.Random(derive_seed(seed, "placement", ctx.room_id)),
        exclude=[start, door],
    )
    drop_warnings: list[str] = []
    for kind, key in PLACEMENT_KEYS.items():
        if key in placed:
            maze[key] = _jsonable(placed[key])
            drop_warnings += _drop_warning(kind, before.get(kind, 0), len(placed[key]))
    # The room row's `quests` bucket is a MIRROR, not the truth: a legacy tree
    # lists none while `maze.json` carries the ids the engine reads, so the
    # roll unions rather than replaces (doctrine 6 — nothing is deleted).
    quests = [int(q) for q in (row.get("quests") or []) if str(q).lstrip("-").isdigit()]
    for existing in maze.get("quest_ids") or []:
        if isinstance(existing, int) and existing not in quests:
            quests.append(existing)
    maze["quest_ids"] = quests

    # --- the gate: the phase's own designation, on a one-room shim ---------
    phase = MazeworldPlacementPhase()
    events_by_id = {
        int(k): v for k, v in ctx.rows("event").items() if str(k).lstrip("-").isdigit()
    }
    boss_ids = {
        int(k) for k, v in ctx.rows("monster").items()
        if v.get("is_boss") and str(k).lstrip("-").isdigit()
    }
    layout_shim = SimpleNamespace(
        grid=maze["grid"],
        event_positions=maze.get("event_positions") or [],
        door_position=tuple(door),
        gate_encounter_id=None,
    )
    map_shim = SimpleNamespace(entities=stubs)
    room_ids = _room_ids(ctx)
    gate = phase._designate_gate(
        map_shim, layout_shim, events_by_id, boss_ids,
        is_final=(bool(room_ids) and ctx.room_id == room_ids[-1]),
    )
    door_key = ctx.point(1) or "door_position"
    maze[door_key] = list(layout_shim.door_position)
    maze["gate_encounter_id"] = layout_shim.gate_encounter_id
    door_warnings = drop_warnings + _settle_door(ctx, maze) + _restamp(ctx, maze)

    batch = f"room-roll:{secrets.token_hex(6)}"
    with provenance.bind_batch(batch) if gate else _null():
        written = write_room(
            ctx,
            maze,
            step=STEP_GRID,
            detail={"kind": "room_roll", "room": ctx.room_id, "seed": seed,
                    "gate_encounter_id": maze.get("gate_encounter_id")},
            actor=actor,
            session=session,
            extra_warnings=door_warnings,
        )
        flagged = (
            _flag_gate(ctx, gate, actor=actor, session=session) if gate else None
        )
    updated = [] if written.get("no_change") else ["grid", "placements"]
    return {
        "warnings": written["warnings"],
        "updated": updated,
        "gate_encounter_id": maze.get("gate_encounter_id"),
        "batch": batch if flagged else None,
    }


def _flag_gate(ctx: RoomContext, gate: dict, *, actor: str, session: str | None) -> dict | None:
    """Re-stamp ``is_gate`` / ``is_climax_boss`` (and prepend the room boss to
    ``monster_ids``) on the designated gate event — the cross-file half of a
    whole-room roll, journaled on ``event:<id>`` with ``mirror_of`` naming the
    room artifact it came from (P.7.3). The flags stay CODE-OWNED: this is
    the only path that writes them (P.6.2 row 9)."""
    from canon.db_ops import _entity, _read_collection, _wall

    entity = _entity(ctx.spec, "event")
    document = _read_collection(ctx.pack, entity)
    gate_id = str(gate["event_id"])
    boss_id = gate.get("boss_id")

    def rows(doc: Any) -> list[dict]:
        return list(doc.values()) if isinstance(doc, dict) else [r for r in doc if isinstance(r, dict)]

    def apply(doc: Any, _changes: dict[str, Any]) -> dict[str, dict]:
        diff: dict[str, dict] = {}
        for row in rows(doc):
            rid = str(row.get(entity.id_field))
            want_gate = rid == gate_id
            for field, value in (
                ("is_gate", want_gate),
                ("is_climax_boss", want_gate and bool(gate["is_climax_boss"])),
            ):
                # The designated gate always carries both flags; every other
                # row only has a flag CLEARED (never added), so a re-roll can
                # never leave two gates behind.
                if not want_gate and not row.get(field):
                    continue
                if row.get(field) != value:
                    diff[f"{rid}.{field}"] = {"from": row.get(field), "to": value}
                row[field] = value
            if want_gate and boss_id is not None:
                mids = list(row.get("monster_ids") or [])
                if boss_id not in mids:
                    diff[f"{rid}.monster_ids"] = {"from": mids, "to": [boss_id, *mids]}
                    row["monster_ids"] = [boss_id, *mids]
        return diff

    result = write_document(
        ctx.pack,
        artifact_id=f"event:{gate_id}",
        rel_path=str(entity.layout.get("path")),
        document=document,
        changes={"is_gate": True},
        wall=_wall(entity),
        apply=apply,
        user_edited=False,
        actor=actor,
        session=session,
        detail={"kind": "gate_flags", "type": "event", "room": ctx.room_id,
                "mirror_of": room_artifact_id(ctx, STEP_GRID)},
        source="code",
    )
    return None if result.get("no_change") else result


def _roll_monsters(
    ctx: RoomContext,
    seed: str,
    encounter_id: str | int | None,
    *,
    actor: str,
    session: str | None,
) -> dict:
    """🎲 monsters (P.9 G4) — re-sample ONE encounter's roster from the room's
    own monsters, the way the event parser sampled it at generation
    (``rng.sample(room_monsters, min(monster_count, len(room_monsters)))``).
    The gate's boss stays first: the gate flags are code-owned and a gate
    without its boss is a broken room."""
    if encounter_id in (None, ""):
        raise ValueError(
            "select an encounter first — a monsters roll re-rolls ONE encounter's roster "
            "(P0 paper P.9 G4: monsters reach a room through a combat event)"
        )
    events = ctx.rows("event")
    event = events.get(str(encounter_id))
    if event is None:
        raise ValueError(f"event {encounter_id} has no row in this pack")
    if str(event.get("type")) != "combat":
        raise ValueError(
            f"event {encounter_id} is a {event.get('type')!r} event — only a combat encounter "
            "carries monsters"
        )
    row = _room_row(ctx)
    roster = [
        str(s.entity_id) for s in _stubs(ctx, row)
        if s.entity_type == "monster" and s.entity_id is not None
    ]
    monsters = ctx.rows("monster")
    roster = [m for m in roster if m in monsters]
    if not roster:
        raise ValueError(
            f"room {ctx.room_id} has no monsters to draw from — generate monsters for it first"
        )
    count = event.get("monster_count") or len(event.get("monster_ids") or []) or 1
    rng = derive_rng(seed, "placement", ctx.room_id, "monster", str(encounter_id))
    chosen = rng.sample(roster, min(int(count), len(roster)))
    ids = [int(m) if m.lstrip("-").isdigit() else m for m in chosen]
    if event.get("is_gate"):
        boss = next(
            (int(k) for k, v in monsters.items()
             if v.get("is_boss") and k in roster and str(k).lstrip("-").isdigit()),
            None,
        )
        if boss is not None:
            ids = [boss, *[m for m in ids if m != boss]]
    written = set_encounter_monsters(
        ctx, "event", str(encounter_id), ids, actor=actor, session=session,
        detail_kind="monsters_roll",
    )
    return {
        "warnings": [],
        "updated": [] if written is None else [f"event:{encounter_id}"],
        "encounter_id": encounter_id,
        "monster_ids": ids,
    }


def _jsonable(value: Any) -> Any:
    """Placement values as JSON types — ``place_entities`` returns NPC cells
    as tuples (the ``MazeLayout`` model's type); on disk they are arrays."""
    if isinstance(value, dict):
        return {k: list(v) if isinstance(v, tuple) else v for k, v in value.items()}
    return value


def _null():
    import contextlib

    return contextlib.nullcontext()
