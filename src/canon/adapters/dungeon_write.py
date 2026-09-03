"""Write-side helpers for one dungeon ROOM — the sibling of
``dungeon_read`` and the mirror of ``platformer_write``'s level half
(P0 paper P.6.3 write table, P.6.4's P0-8 column; row P0-8).

What this module extends, rather than re-implements:

- **the P0-6 write core** (``canon.write_core``): every write here runs
  ``write_document`` / ``commit_document``, so the doctrine-1 pipeline
  (resolve → wall → apply → fail-closed validate → warnings → journal
  per-field diff → CAS snapshot) is the one at P0-6, not a second copy.
  Nothing in this file touches ``canon.provenance`` except to bind a batch
  and read the journal for the restore-lineage check.
- **the platformer's wire shape**: ``apply_room_edit`` takes the SAME sparse
  keys ``apply_level_edit`` takes (``entities`` / ``items`` / ``triggers`` /
  ``spawn`` / ``exit``) — "one ``apply-edit``, not a ``dungeon_*`` pair"
  (Phase 0 §6). The ``GridKind`` maps them onto ``maze.json``'s own keys, so
  cradle's canvas sends one payload for both grids and ``_placement_diff``
  (imported from ``platformer_write``) shapes the journal diff identically.
- **the registry as data**: which wire key feeds which ``maze.json`` key,
  which EntityKind it places, its id key and its grid stamp all come from
  ``GridKind.placements`` (P.3.2) — never a literal in this file. A third
  template with a single-file grid writes through the same body.

The one ADDITIVE key beyond the platformer's shape is ``encounters``
(P.9 G4, the user's decision of 2026-09-01): a dungeon places a monster by
building or targeting the combat EVENT on that cell, so the wire carries
``[{x, y, event_id?, monster_ids: [...]}]`` and the writer does the
cross-file write — ``events/events.json`` (the row, through the db core)
plus ``maze.json`` (the placement), each its own journal event under one
``batchId`` (the P.7.3 mirror pattern).

Fail-closed BEFORE any byte is written (P.6.3, P.9 G5/G7): every id exists in
its row file, every cell is inside the grid, every placement sits on an open
cell and never on the player start or the door, a wall never lands on a
placement, and the door only moves to a cell 4-adjacent to the gate encounter.
Reachability start→door is a WARNING and never a block (doctrine 10).

Deliberately absent, by row ownership: resize (M9 — the dims are engine
constants until the W2.0 pull-in), derived layers and the int8 cast (the
platformer's, P.6.3), the per-step rolls (``canon.packs.dungeon.rolls`` — the
same pack's generation phases drive those), dialogue and scene writes (P0-9).
"""

from __future__ import annotations

import contextlib
import copy
import json
import secrets
from collections import deque
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from canon import provenance
from canon.adapters.platformer_write import _placement_diff
from canon.packs import ResolvedPack, resolve_pack
from canon.packs.rows import load_rows, read_json
from canon.packs.spec import EntityKind, GridKind, PackSpec
from canon.write_core import commit_document, write_document

__all__ = [
    "STEP_GRID",
    "STEP_PLACEMENTS",
    "RoomContext",
    "apply_room_edit",
    "import_room_grids",
    "pack_seed",
    "read_room_json",
    "restore_room_step",
    "room_artifact_id",
    "room_context",
    "room_rows",
    "set_encounter_monsters",
    "write_room",
]

#: The two artifact STEPS of a room (P.9 R1: ``room:<map_id>/<step>``).
#: One file (``maze.json``) carries both, so a write journals one event on
#: the step that names WHAT changed: the painted cells, or the placements.
STEP_GRID = "grid"
STEP_PLACEMENTS = "placements"

#: Sparse keys that are not placements: the two point markers (P.3.2
#: ``points``, in order) and the additive encounter key (P.9 G4).
ENCOUNTERS_KEY = "encounters"


class RoomContext:
    """Everything a room write resolves once: the pack, its ``GridKind``, the
    room id, the loaded ``maze.json`` and the wire→placement lookup the
    registry stamps. Built by :func:`room_context`; every verb below takes
    one so resolution happens exactly once per call."""

    __slots__ = ("pack", "resolved", "spec", "grid", "room_id", "rel", "path", "maze",
                 "width", "height", "by_wire")

    def __init__(self, pack: Path, resolved: ResolvedPack, grid: GridKind, room_id: str) -> None:
        self.pack = pack
        self.resolved = resolved
        self.spec: PackSpec = resolved.spec
        self.grid = grid
        self.room_id = room_id
        self.rel = _fill(grid.path_template, map_id=room_id, level_id=room_id)
        self.path = pack / self.rel
        if not self.path.is_file():
            raise FileNotFoundError(f"room {room_id!r} not found: {self.path} is missing")
        maze = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(maze, dict) or not isinstance(maze.get("grid"), list):
            raise ValueError(f"{self.path} is not a maze layout (no grid)")
        self.maze: dict = maze
        rows = maze["grid"]
        self.height = len(rows)
        self.width = max((len(r) for r in rows if isinstance(r, list)), default=0)
        #: wire key ("entities") → (maze key, the stamped placement block).
        self.by_wire: dict[str, tuple[str, dict]] = {
            str(block.get("wire")): (key, dict(block))
            for key, block in grid.placements.items()
            if block.get("wire")
        }

    # -- convenience reads ------------------------------------------------
    def entity(self, kind: str) -> EntityKind | None:
        return self.spec.entities.get(kind)

    def rows(self, kind: str) -> dict[str, dict]:
        entity = self.entity(kind)
        if entity is None:
            return {}
        loader = entity.loader or (lambda p: load_rows(p, entity))
        return loader(self.pack)

    def point(self, index: int) -> str | None:
        points = self.grid.points or ["player_start", "door_position"]
        return points[index] if index < len(points) else None

    def cell(self, x: int, y: int) -> Any:
        row = self.maze["grid"][y]
        return row[x] if x < len(row) else None

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height


class _Blank(dict):
    """``str.format_map`` helper — an unknown placeholder renders empty, so
    ``room:{map_id}/{step}`` with only ``map_id`` yields the family PREFIX
    (the same helper ``dungeon_read`` uses for the ``last_change`` filter)."""

    def __missing__(self, key: str) -> str:
        return ""


def _fill(template: str, **values: str) -> str:
    return template.format_map(_Blank(values))


def _single_file_grid(spec: PackSpec) -> GridKind | None:
    """The GridKind this writer knows how to write: one JSON file per grid
    (``file`` set) — the dungeon room's shape. Data, not an id."""
    for grid in spec.grids.values():
        if grid.file:
            return grid
    return None


def room_context(pack_dir: str | Path, room_id: str) -> RoomContext:
    """Resolve the pack (P.4.1's read-both shim), pick its single-file grid
    and load the room. Raises ``FileNotFoundError`` for an unknown room —
    the class every read/write verb turns into the structured error."""
    pack = Path(pack_dir)
    resolved = resolve_pack(pack)
    grid = _single_file_grid(resolved.spec)
    if grid is None:
        raise FileNotFoundError(
            f"pack type {resolved.pack_type!r} declares no single-file grid to edit"
        )
    # A room id is a directory name under the grid's path template, never a
    # path of its own (the template supplies every separator).
    if not room_id or "/" in room_id or "\\" in room_id or room_id in (".", ".."):
        raise FileNotFoundError(f"room {room_id!r} is not a room id")
    return RoomContext(pack, resolved, grid, room_id)


def room_artifact_id(ctx: RoomContext, step: str) -> str:
    """``room:<map_id>/<step>`` from the GridKind's own template (P.9 R1)."""
    return _fill(ctx.grid.artifact_id, map_id=ctx.room_id, level_id=ctx.room_id, step=step)


# ---------------------------------------------------------------------------
# Shared geometry / validation
# ---------------------------------------------------------------------------


def _int(value: Any, what: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be an integer, got {value!r}") from None


def _point(ctx: RoomContext, key: str) -> tuple[int, int] | None:
    value = ctx.maze.get(key)
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            return int(value[0]), int(value[1])
        except (TypeError, ValueError):
            return None
    return None


def _reserved_points(ctx: RoomContext, maze: dict) -> dict[tuple[int, int], str]:
    """The cells a placement may never occupy: the player start and the door
    (P.6.3 "placements on open cells, never start/door")."""
    out: dict[tuple[int, int], str] = {}
    for index in (0, 1):
        key = ctx.point(index)
        if key is None:
            continue
        value = maze.get(key)
        if isinstance(value, (list, tuple)) and len(value) == 2:
            try:
                out[(int(value[0]), int(value[1]))] = key
            except (TypeError, ValueError):
                continue
    return out


def _placed_cells(ctx: RoomContext, maze: dict, *, skip_wire: str | None = None) -> dict[tuple[int, int], str]:
    """Every cell some placement occupies right now, cell → a human label.
    *skip_wire* drops one wire's own placements (the caller is rewriting
    them), so a move within a wire never collides with its old self."""
    out: dict[tuple[int, int], str] = {}
    for wire, (key, block) in ctx.by_wire.items():
        if wire == skip_wire:
            continue
        kind = str(block.get("kind", key))
        for entry in _entries(maze.get(key), block):
            out[(entry["x"], entry["y"])] = f"{kind} {entry['id']}"
    return out


def _entries(raw: Any, block: dict) -> list[dict]:
    """A maze placement value as ``[{id, x, y, meta}]`` — the two stamped
    shapes (``dict`` = ``{id: [x, y]}``, ``list`` = ``[{x, y, <id key>}]``).
    Malformed entries are skipped: the reader already names them."""
    out: list[dict] = []
    if block.get("shape") == "dict":
        if isinstance(raw, dict):
            for rid, xy in raw.items():
                if isinstance(xy, (list, tuple)) and len(xy) == 2:
                    try:
                        out.append({"id": str(rid), "x": int(xy[0]), "y": int(xy[1]), "meta": {}})
                    except (TypeError, ValueError):
                        continue
        return out
    id_key = str(block.get("id", "id"))
    if isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict) or id_key not in entry:
                continue
            try:
                out.append(
                    {"id": str(entry[id_key]), "x": int(entry["x"]), "y": int(entry["y"]), "meta": entry}
                )
            except (KeyError, TypeError, ValueError):
                continue
    return out


def _clear_stamps(maze: dict, block: dict, entries: Iterable[dict]) -> None:
    """Zero the grid cells the given placements stamped (P.6.3: "clear old /
    stamp new"). A kind with ``grid_stamp: null`` (NPCs) stamps nothing."""
    stamp = block.get("grid_stamp")
    if stamp is None:
        return
    grid = maze["grid"]
    for entry in entries:
        x, y = entry["x"], entry["y"]
        if 0 <= y < len(grid) and 0 <= x < len(grid[y]):
            grid[y][x] = 0


def _stamp(maze: dict, block: dict, x: int, y: int, row_id: str) -> None:
    """Write one placement's grid encoding: ``"id"`` puts the row id in the
    cell (items), an int puts that marker (events, ``-1``)."""
    stamp = block.get("grid_stamp")
    if stamp is None:
        return
    maze["grid"][y][x] = int(row_id) if stamp == "id" else int(stamp)


def _reachable(maze: dict, start: tuple[int, int], goal: tuple[int, int]) -> bool:
    """4-connected walk over every non-wall cell (an item / event cell is
    walkable — the engine steps onto it). Only ever a WARNING (doctrine 10:
    the editor warns loudly, never blocks)."""
    grid = maze["grid"]
    height = len(grid)
    seen = {start}
    queue = deque([start])
    while queue:
        x, y = queue.popleft()
        if (x, y) == goal:
            return True
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if not (0 <= ny < height and 0 <= nx < len(grid[ny])):
                continue
            if (nx, ny) in seen or grid[ny][nx] == 1:
                continue
            seen.add((nx, ny))
            queue.append((nx, ny))
    return False


def _reachability_warning(ctx: RoomContext, maze: dict) -> list[str]:
    start = _point_of(maze, ctx.point(0))
    door = _point_of(maze, ctx.point(1))
    if start is None or door is None or _reachable(maze, start, door):
        return []
    return [
        f"the door at {list(door)} is not reachable from the player start at {list(start)} "
        "— the room is authorable but not completable as it stands"
    ]


def _point_of(maze: dict, key: str | None) -> tuple[int, int] | None:
    if key is None:
        return None
    value = maze.get(key)
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            return int(value[0]), int(value[1])
        except (TypeError, ValueError):
            return None
    return None


def _gate_cell(ctx: RoomContext, maze: dict) -> tuple[int, int] | None:
    """The cell of the gate encounter — the combat event guarding the door
    (P.6.1). ``None`` when the room has no gate: the door then moves freely."""
    gate_id = maze.get("gate_encounter_id")
    if gate_id is None:
        return None
    for _wire, (key, block) in ctx.by_wire.items():
        if block.get("kind") != "event":
            continue
        for entry in _entries(maze.get(key), block):
            if entry["id"] == str(gate_id):
                return entry["x"], entry["y"]
    return None


# ---------------------------------------------------------------------------
# `canon grid apply-edit` — the sparse half
# ---------------------------------------------------------------------------


def apply_room_edit(
    pack_dir: str | Path,
    room_id: str,
    edit: dict,
    *,
    actor: str = "user",
    session: str | None = None,
    batch_id: str | None = None,
    op: str = "edit",
    source: str = "user",
) -> dict:
    """Apply a sparse placement edit to one room and journal it.

    *edit* carries the platformer's own keys — ``entities`` (→
    ``npc_positions``), ``items`` (→ ``item_placements`` + the grid's item-id
    stamps), ``triggers`` (→ ``event_positions`` + the grid's ``-1`` markers),
    ``spawn`` / ``exit`` (→ ``player_start`` / ``door_position``) — plus the
    additive ``encounters`` (P.9 G4). Which wire feeds which maze key comes
    from ``GridKind.placements``; the id key inside each entry comes from the
    placement block, so the WIRE never learns a pack's field names (P.9 G9).

    One file, one journal event: ``maze.json`` is the CAS unit, so a save that
    touches several wires still writes once and journals once. ``detail.kind``
    is that wire's own kind (``npc_move`` / ``item_move`` / ``event_move``,
    ``level_edit`` for the markers) when exactly one wire changed, and
    ``room_edit`` with a ``kinds`` list when several did — the platformer's
    per-key kinds come from per-key FILES, and a room has one. An
    ``encounters`` entry adds a SECOND event on ``events/events.json``; both
    ride one ``batchId`` (P.7.3).
    """
    ctx = room_context(pack_dir, room_id)
    if not isinstance(edit, dict) or not edit:
        raise ValueError("edit contained no recognized layers")

    batch = batch_id or f"room-edit:{secrets.token_hex(6)}"
    known_points = {"spawn": ctx.point(0), "exit": ctx.point(1)}
    recognized = set(ctx.by_wire) | {k for k, v in known_points.items() if v} | {ENCOUNTERS_KEY}
    unknown = [k for k in edit if k not in recognized]
    if unknown:
        raise ValueError(
            f"edit contained no recognized layers: {sorted(unknown)} — this grid takes "
            f"{sorted(recognized)}"
        )

    cross_file = ENCOUNTERS_KEY in edit
    warnings: list[str] = []
    changed_wires: list[str] = []
    kinds: list[str] = []
    # ``write_document`` expands this dict at COMMIT time (after ``apply``),
    # so the kind the diff actually earned lands on the event.
    detail: dict[str, Any] = {"kind": "room_edit", "room": ctx.room_id}

    def apply(maze: dict, changes: dict[str, Any]) -> dict[str, dict]:
        diff: dict[str, dict] = {}
        # Markers first: a placement may not sit on the new start / door.
        for name in ("spawn", "exit"):
            if name not in changes:
                continue
            key = known_points[name]
            assert key is not None
            old = maze.get(key)
            new = _validate_point(ctx, maze, name, key, changes[name])
            if list(old or []) != list(new):
                diff[name] = {"from": old, "to": new}
                kinds.append("level_edit")
            maze[key] = new
        for wire, (key, block) in ctx.by_wire.items():
            if wire not in changes:
                continue
            old_entries = _entries(maze.get(key), block)
            new_entries = _validate_wire(ctx, maze, wire, key, block, changes[wire])
            _clear_stamps(maze, block, old_entries)
            for entry in new_entries:
                _stamp(maze, block, entry["x"], entry["y"], entry["id"])
            maze[key] = _store(block, new_entries)
            id_key = str(block.get("id", "id"))
            flat_old = [{id_key: e["id"], "x": e["x"], "y": e["y"]} for e in old_entries]
            flat_new = [{id_key: e["id"], "x": e["x"], "y": e["y"]} for e in new_entries]
            if flat_old != flat_new:
                diff[wire] = _placement_diff(flat_old, flat_new, id_key)
                changed_wires.append(wire)
                kinds.append(str(block.get("journal_kind", f"{block.get('kind')}_move")))
        unique = list(dict.fromkeys(kinds))
        if unique:
            detail["kind"] = unique[0] if len(unique) == 1 else "room_edit"
            if len(unique) > 1:
                detail["kinds"] = unique
        return diff

    def warn(maze: dict, _diff: dict[str, dict]) -> list[str]:
        return _reachability_warning(ctx, maze)

    def validate(maze: dict, _diff: dict[str, dict]) -> Any:
        _validate_room(ctx, maze)
        return None

    # Both files of a cross-file encounter write ride ONE batchId (P.7.3), so
    # a reader walks the pair in reverse as one act. A single-file edit binds
    # nothing — a batch of one is noise.
    event_wire = _wire_of_kind(ctx, "event") if cross_file else None
    if cross_file:
        if event_wire is None:
            raise ValueError("this grid places no events — encounters need an event placement")
        # Judge the WHOLE payload before the first row is created: the row
        # write is a separate file and cannot be rolled back once journaled.
        _check_encounters(ctx, edit[ENCOUNTERS_KEY], event_wire)
    with provenance.bind_batch(batch) if cross_file else contextlib.nullcontext():
        encounter_events: list[dict] = []
        if cross_file:
            assert event_wire is not None
            resolved, encounter_events = _apply_encounters(
                ctx,
                edit[ENCOUNTERS_KEY],
                actor=actor,
                session=session,
                base=edit.get(event_wire),
            )
            edit = {k: v for k, v in edit.items() if k != ENCOUNTERS_KEY}
            edit[event_wire] = resolved
        result = write_document(
            ctx.pack,
            artifact_id=room_artifact_id(ctx, STEP_PLACEMENTS),
            rel_path=ctx.rel,
            document=ctx.maze,
            changes=edit,
            apply=apply,
            warn=warn,
            validate=validate,
            # maze.json carries no `status` key — the engine's file shape is
            # untouched (write_core step 5: "a dungeon row without one is not
            # given one"). The user_edited signal for a room is the journal.
            user_edited=False,
            actor=actor,
            session=session,
            detail=detail,
            op=op,
            source=source,
            warnings=warnings,
        )
    events = ([] if result.get("no_change") else [result["event"]]) + encounter_events
    return {
        "level_id": ctx.room_id,
        "room_id": ctx.room_id,
        "stage_id": "",
        "updated": sorted({*changed_wires, *(k for k in ("spawn", "exit") if k in result["changed"])}),
        "changed": result["changed"],
        "warnings": result["warnings"],
        "events": len([e for e in events if e]),
        "batch": batch if encounter_events else None,
        "encounters": [e.get("artifact_id") for e in encounter_events],
        "no_change": bool(result.get("no_change")) and not encounter_events,
    }


def _wire_of_kind(ctx: RoomContext, kind: str) -> str | None:
    for wire, (_key, block) in ctx.by_wire.items():
        if block.get("kind") == kind:
            return wire
    return None


def _store(block: dict, entries: list[dict]) -> Any:
    """Placements back in their stamped on-disk shape. A ``list`` shape keeps
    every metadata key the entry carried (the item sidecar's ``name`` /
    ``portrait_prompt`` / ``profile_image``) so a move never drops fields."""
    if block.get("shape") == "dict":
        return {entry["id"]: [entry["x"], entry["y"]] for entry in entries}
    id_key = str(block.get("id", "id"))
    out: list[dict] = []
    for entry in entries:
        meta = dict(entry.get("meta") or {})
        meta.update({"x": entry["x"], "y": entry["y"], id_key: entry["raw_id"]})
        out.append(meta)
    return out


def _validate_point(ctx: RoomContext, maze: dict, name: str, key: str, value: Any) -> list[int]:
    """A marker move: in bounds, on an open cell, never onto a placement, and
    — for the door — only to a cell 4-adjacent to the gate encounter (P.9 G5:
    "snap-to-gate-adjacent; free drag refused with the reason").

    A marker the caller RE-SENDS unchanged is not a move, so it skips the
    invariants: cradle sends ``spawn`` and ``exit`` together whenever the
    markers layer is dirty, and a pre-existing state (a gate that drifted, a
    placement that landed on the door) must not hold the OTHER marker's drag
    hostage. Only the value that actually changes is judged.
    """
    if not (isinstance(value, (list, tuple)) and len(value) == 2):
        raise ValueError(f"{name} must be [x, y], got {value!r}")
    x, y = _int(value[0], f"{name}.x"), _int(value[1], f"{name}.y")
    current = maze.get(key)
    if isinstance(current, (list, tuple)) and len(current) == 2:
        with contextlib.suppress(TypeError, ValueError):
            if (int(current[0]), int(current[1])) == (x, y):
                return [x, y]
    if not ctx.in_bounds(x, y):
        raise ValueError(f"{name} ({x}, {y}) is outside the {ctx.width}×{ctx.height} grid")
    if maze["grid"][y][x] == 1:
        raise ValueError(f"{name} ({x}, {y}) is a wall — markers sit on open cells")
    occupied = _placed_cells(ctx, maze)
    if (x, y) in occupied:
        raise ValueError(f"{name} ({x}, {y}) is occupied by {occupied[(x, y)]} — move that first")
    if key == ctx.point(1):
        gate = _gate_cell(ctx, maze)
        if gate is not None and abs(gate[0] - x) + abs(gate[1] - y) != 1:
            raise ValueError(
                f"the door must stay next to the gate encounter at {list(gate)} — the player has "
                f"to pass the boss to leave; ({x}, {y}) is not adjacent to it"
            )
    return [x, y]


def _validate_wire(
    ctx: RoomContext, maze: dict, wire: str, key: str, block: dict, value: Any
) -> list[dict]:
    """One wire's incoming placements, fully checked before anything moves:
    the payload shape, the row id exists, the cell is inside the grid, open,
    not the start or the door, and not already taken by another placement.

    One CELL holds one placement; one ROW may hold many cells. A dungeon
    legitimately scatters the same item template over a dozen squares
    (``item_placements`` on the reference world places 18 distinct items on 77
    cells), so a "one cell per row" rule would refuse the file's own contents
    on the first drag. The dict-shaped block (``npc_positions``) is the one
    exception — its id IS the key, so a repeat is an ambiguous payload rather
    than a second placement, and it stays refused.

    The gate encounter is checked in BOTH directions here (P.9 G5): the door
    drag is refused when it leaves the gate, and so is a gate drag that leaves
    the door — otherwise the room reaches a state neither marker can repair.
    """
    if not isinstance(value, list):
        raise ValueError(f"{wire} must be a list of placements, got {type(value).__name__}")
    kind = str(block.get("kind", key))
    entity = ctx.entity(kind)
    rows = ctx.rows(kind)
    row_file = str((entity.layout if entity else {}).get("path", f"{kind} rows"))
    id_key = str(block.get("id", "id"))
    # The wire's own id key is the shared bundle's literal (`enemy_id` /
    # `item_id`, P.9 G9); the placement block names the STORED one. Accept
    # either, plus a bare `id`, so cradle keeps sending one payload shape.
    wire_id_keys = (id_key, "enemy_id", "item_id", "event_id", "id")
    reserved = _reserved_points(ctx, maze)
    taken = _placed_cells(ctx, maze, skip_wire=wire)
    on_disk = _entries(maze.get(key), block)
    # The sidecar shape the file itself uses, learned from its own entries.
    sidecar_keys = [
        k for k in (on_disk[0].get("meta") or {}) if k not in {"x", "y", id_key}
    ] if on_disk and block.get("shape") != "dict" else []
    # A dict-shaped block keys BY the row id, so the same id twice is one
    # ambiguous payload; a list-shaped block is a list of cells and repeats
    # are the normal case.
    one_cell_per_row = block.get("shape") == "dict"
    gate_raw = maze.get("gate_encounter_id")
    gate_watch = str(gate_raw) if gate_raw is not None and kind == "event" else None
    on_disk_by_id = {e["id"]: (e["x"], e["y"]) for e in on_disk}
    door = _point_of(maze, ctx.point(1))
    out: list[dict] = []
    seen_cells: dict[tuple[int, int], str] = {}
    seen_ids: set[str] = set()
    for entry in value:
        if not isinstance(entry, dict):
            raise ValueError(f"{wire}: every placement is an object, got {entry!r}")
        raw_id = next((entry[k] for k in wire_id_keys if k in entry), None)
        if raw_id is None:
            # The `triggers` wire is the platformer's `{x, y, type, params}`
            # shape (P.9 G3), so the row id rides in `params.event_id`.
            params = entry.get("params")
            if isinstance(params, dict):
                raw_id = next((params[k] for k in wire_id_keys if k in params), None)
        if raw_id is None:
            raise ValueError(f"{wire}: placement {entry!r} carries no {id_key}")
        row_id = str(raw_id)
        if row_id not in rows:
            raise ValueError(f"{wire}: {kind} {row_id} has no row in {row_file}")
        x, y = _int(entry.get("x"), f"{wire}.x"), _int(entry.get("y"), f"{wire}.y")
        if not ctx.in_bounds(x, y):
            raise ValueError(
                f"{wire}: {kind} {row_id} at ({x}, {y}) is outside the "
                f"{ctx.width}×{ctx.height} grid"
            )
        if maze["grid"][y][x] == 1:
            raise ValueError(f"{wire}: {kind} {row_id} at ({x}, {y}) is a wall — placements sit on open cells")
        if (x, y) in reserved:
            raise ValueError(
                f"{wire}: {kind} {row_id} at ({x}, {y}) is the {reserved[(x, y)]} cell — "
                "placements never sit on the player start or the door"
            )
        if (x, y) in taken:
            raise ValueError(f"{wire}: {kind} {row_id} at ({x}, {y}) is occupied by {taken[(x, y)]}")
        if (x, y) in seen_cells:
            raise ValueError(f"{wire}: two placements share ({x}, {y}) — {seen_cells[(x, y)]} and {row_id}")
        if one_cell_per_row and row_id in seen_ids:
            raise ValueError(f"{wire}: {kind} {row_id} is placed twice — one cell per row")
        if (
            gate_watch is not None
            and row_id == gate_watch
            and door is not None
            and on_disk_by_id.get(row_id) != (x, y)
            and abs(door[0] - x) + abs(door[1] - y) != 1
        ):
            raise ValueError(
                f"{wire}: the gate encounter {row_id} must stay next to the door at "
                f"{list(door)} — the player has to pass the boss to leave; ({x}, {y}) is "
                "not adjacent to it. Move the door first, or drag the gate to a cell beside it"
            )
        seen_cells[(x, y)] = row_id
        seen_ids.add(row_id)
        # The sidecar's metadata comes from the FILE and the ROW, never from
        # the wire: the canvas sends the platformer's chrome (`type`,
        # `params`, `source`, `variant`) and none of it belongs in
        # `maze.json`. An existing entry keeps its own keys; a brand-new
        # placement gets the shape the file already uses (item_placements'
        # name / portrait_prompt / profile_image), filled from the row.
        meta = next(
            ({k: v for k, v in (old.get("meta") or {}).items() if k not in {"x", "y", id_key}}
             for old in on_disk if old["id"] == row_id),
            None,
        )
        if meta is None:
            meta = {k: rows[row_id].get(k, "") for k in sidecar_keys}
        # `raw_id` keeps the STORED type (maze ids are ints — P.6.5 M11).
        stored = int(row_id) if str(row_id).lstrip("-").isdigit() else row_id
        out.append({"id": row_id, "raw_id": stored, "x": x, "y": y, "meta": meta})
    return out


def _validate_room(ctx: RoomContext, maze: dict) -> None:
    """The fail-closed post-condition every write shares: the grid is still a
    rectangle of ints and the dims fields still describe it (M9 — nothing here
    resizes).

    It deliberately checks NO placement here. Row/open-cell/reservation checks
    live on the way IN, per wire — ``_validate_wire`` for a placement payload,
    ``_check_encounters`` for the cross-file encounter path, and
    ``import_room_grids``' own paint check for a wall painted onto an occupied
    cell. ``restore_room_step`` depends on that split: a restore that walls a
    placement WARNS (``_disturbance_warnings``) and keeps it — doctrine 10 —
    so a refusal added here would turn every such restore into a hard error.
    """
    grid = maze.get("grid")
    if not isinstance(grid, list) or not grid or not all(isinstance(row, list) for row in grid):
        raise ValueError("the write would leave the room without a rectangular grid")
    widths = {len(row) for row in grid}
    if len(widths) != 1:
        raise ValueError(f"the write would leave a ragged grid (row widths {sorted(widths)})")
    if len(grid) != ctx.height or next(iter(widths)) != ctx.width:
        raise ValueError(
            f"a room is {ctx.width}×{ctx.height} — resizing is an engine constant until the "
            "runtime pull-in (P0 paper P.6.5 M9)"
        )
    dims = ctx.grid.dims or {}
    for field, expected in (
        (dims.get("width_field", "width"), ctx.width),
        (dims.get("height_field", "height"), ctx.height),
    ):
        if field in maze and maze[field] != expected:
            raise ValueError(f"{field} says {maze[field]} but the grid is {expected} wide/tall")


# ---------------------------------------------------------------------------
# Encounters — the cross-file monster path (P.9 G4)
# ---------------------------------------------------------------------------


def _check_encounters(ctx: RoomContext, value: Any, wire: str) -> None:
    """Judge the WHOLE ``encounters`` payload before a single row is written.

    ``_apply_encounters`` writes ``events/events.json`` through the db core
    and only then hands the placement to ``maze.json``'s own validation — so
    without this pass a payload that the maze half refuses has already
    created an orphan combat row and journalled it (doctrine 1: a fail-closed
    refusal writes NOTHING). Every geometry and reference check the maze half
    would make is made here first, for every entry.
    """
    if not isinstance(value, list):
        raise ValueError(f"{ENCOUNTERS_KEY} must be a list, got {type(value).__name__}")
    events = ctx.rows("event")
    monsters = ctx.rows("monster")
    monster_entity = ctx.entity("monster")
    monster_file = str((monster_entity.layout if monster_entity else {}).get("path", "monsters"))
    reserved = _reserved_points(ctx, ctx.maze)
    taken = _placed_cells(ctx, ctx.maze, skip_wire=wire)
    seen: dict[tuple[int, int], str] = {}
    for raw in value:
        if not isinstance(raw, dict):
            raise ValueError(f"{ENCOUNTERS_KEY}: every entry is an object, got {raw!r}")
        x = _int(raw.get("x"), "encounters.x")
        y = _int(raw.get("y"), "encounters.y")
        if not ctx.in_bounds(x, y):
            raise ValueError(
                f"{ENCOUNTERS_KEY}: ({x}, {y}) is outside the {ctx.width}×{ctx.height} grid"
            )
        if ctx.maze["grid"][y][x] == 1:
            raise ValueError(
                f"{ENCOUNTERS_KEY}: ({x}, {y}) is a wall — an encounter sits on an open cell"
            )
        if (x, y) in reserved:
            raise ValueError(
                f"{ENCOUNTERS_KEY}: ({x}, {y}) is the {reserved[(x, y)]} cell — an encounter "
                "never sits on the player start or the door"
            )
        if (x, y) in taken:
            raise ValueError(f"{ENCOUNTERS_KEY}: ({x}, {y}) is occupied by {taken[(x, y)]}")
        if (x, y) in seen:
            raise ValueError(f"{ENCOUNTERS_KEY}: two encounters share ({x}, {y})")
        seen[(x, y)] = str(raw.get("event_id", "new"))
        monster_ids = raw.get("monster_ids") or []
        if not isinstance(monster_ids, list):
            raise ValueError("encounters[].monster_ids must be a list of monster ids")
        for mid in monster_ids:
            if str(mid) not in monsters:
                raise ValueError(f"monster {mid} has no row in {monster_file}")
        event_id = raw.get("event_id")
        if event_id is not None and str(event_id) not in events:
            raise ValueError(f"event {event_id} has no row to add monsters to")


def _apply_encounters(
    ctx: RoomContext,
    value: Any,
    *,
    actor: str,
    session: str | None,
    base: Any = None,
) -> tuple[list[dict], list[dict]]:
    """Create or target the combat EVENT each encounter names and write its
    ``monster_ids`` — the user's G4 decision: "placing a monster means
    building or placing an encounter on a square".

    Returns ``(trigger placements, journal events)``. The row write happens
    FIRST (through the db core, so it is walled, validated and journaled like
    any other row edit) and the caller then writes the placement into
    ``maze.json`` — two files, two events, one ``batchId``. ``_check_encounters``
    has already judged the whole payload, so the first row write only happens
    once the maze half is known to accept it.

    *base* is the caller's OWN event placements when the same edit also
    carried the ``triggers`` wire: the encounters merge INTO it (they win only
    on the ids they name) instead of silently replacing it with what is on
    disk. Absent, the on-disk placements are the base.

    The gate flags (``is_gate`` / ``is_climax_boss``) are NOT writable here:
    they are recomputed by the placement phase from the room's own shape
    (P.6.2 row 9, "code-owned"), so this path never sets them.
    """
    from canon.db_ops import new_db_row

    if not isinstance(value, list):
        raise ValueError(f"{ENCOUNTERS_KEY} must be a list, got {type(value).__name__}")
    event_kind = "event"
    wire = _wire_of_kind(ctx, event_kind)
    if wire is None or ctx.entity(event_kind) is None:
        raise ValueError(
            f"this grid places no {event_kind!r} rows — an encounter IS a combat event "
            "(P0 paper P.9 G4), so a pack without one cannot place monsters"
        )
    events = ctx.rows(event_kind)
    key, block = ctx.by_wire[wire]

    # Start from the caller's OWN event placements when the same edit carried
    # the wire (F: they used to be thrown away), else from what is on disk. An
    # encounter then either moves one of those or appends a new one.
    if isinstance(base, list):
        placements = [p for p in (_encounter_entry(raw, block) for raw in base) if p is not None]
    else:
        placements = [
            {"event_id": e["id"], "x": e["x"], "y": e["y"],
             **{k: v for k, v in (e["meta"] or {}).items() if k not in ("x", "y", "event_id")}}
            for e in _entries(ctx.maze.get(key), block)
        ]
    by_id = {str(p["event_id"]): p for p in placements}

    journal: list[dict] = []
    for raw in value:
        if not isinstance(raw, dict):
            raise ValueError(f"{ENCOUNTERS_KEY}: every entry is an object, got {raw!r}")
        x, y = _int(raw.get("x"), "encounters.x"), _int(raw.get("y"), "encounters.y")
        monster_ids = raw.get("monster_ids") or []
        wanted = [int(m) if str(m).lstrip("-").isdigit() else m for m in monster_ids]
        event_id = raw.get("event_id")
        if event_id is None:
            # New combat event: the id comes from the kind's own id_alloc
            # (P.3.1) — this file never allocates one itself. `db new` walls,
            # validates and journals it exactly like any other created row.
            fields: dict[str, Any] = {"type": "combat", "monster_ids": wanted}
            for optional in ("name", "description", "difficulty", "room_level", "time_gate"):
                if raw.get(optional) is not None:
                    fields[optional] = raw[optional]
            fields.setdefault("name", f"Encounter at {x},{y}")
            created = new_db_row(ctx.pack, event_kind, fields, actor=actor, session=session)
            event_id = created["id"]
            journal.append({"artifact_id": f"{event_kind}:{event_id}", "op": "create"})
        else:
            if str(event_id) not in events:
                raise ValueError(f"{event_kind} {event_id} has no row to add monsters to")
            written = set_encounter_monsters(
                ctx, event_kind, str(event_id), wanted, actor=actor, session=session
            )
            if written is not None:
                journal.append(written)
        entry = by_id.get(str(event_id)) or {"event_id": event_id}
        entry.update({"x": x, "y": y, "event_id": event_id})
        if str(event_id) not in by_id:
            by_id[str(event_id)] = entry
            placements.append(entry)
    return placements, journal


def _encounter_entry(raw: Any, block: dict) -> dict | None:
    """One caller-sent event placement as ``{event_id, x, y, …}``.

    The ``triggers`` wire is the platformer's ``{x, y, type, params}`` shape
    (P.9 G3), so the row id may ride in ``params.event_id``; ``_validate_wire``
    re-reads both shapes, and this only has to name the id so an ``encounters``
    entry can find the placement it is moving."""
    if not isinstance(raw, dict):
        return None
    id_key = str(block.get("id", "id"))
    keys = (id_key, "event_id", "id")
    rid = next((raw[k] for k in keys if k in raw), None)
    if rid is None and isinstance(raw.get("params"), dict):
        rid = next((raw["params"][k] for k in keys if k in raw["params"]), None)
    if rid is None:
        return None
    try:
        return {"event_id": rid, "x": int(raw["x"]), "y": int(raw["y"])}
    except (KeyError, TypeError, ValueError):
        return None


def set_encounter_monsters(
    ctx: RoomContext,
    kind: str,
    event_id: str,
    monster_ids: list,
    *,
    actor: str,
    session: str | None,
    detail_kind: str = "encounter_monsters",
) -> dict | None:
    """Rewrite one combat event's ``monster_ids`` through the P0-6 core.

    ``db update`` refuses a WHOLE-container write (P.1's list grammar is
    ``monster_ids[<i>]``) because a row surface must not let a typo blank a
    list. The encounter surface OWNS the roster the way the grid owns
    ``x``/``y``, so this path replaces it wholesale — with the kind's real
    protected wall still in force and the same journal shape (``changed``
    per field, one CAS pair on ``events/events.json``).
    """
    from canon.db_ops import _entity, _read_collection, _wall

    entity = _entity(ctx.spec, kind)
    document = _read_collection(ctx.pack, entity)

    def locate(doc: Any) -> dict:
        if isinstance(doc, dict):
            return doc[str(event_id)]
        for row in doc:
            if isinstance(row, dict) and str(row.get(entity.id_field)) == str(event_id):
                return row
        raise FileNotFoundError(f"{kind} {event_id!r} not found")

    def apply(doc: Any, changes: dict[str, Any]) -> dict[str, dict]:
        row = locate(doc)
        diff: dict[str, dict] = {}
        for name, value in changes.items():
            old = row.get(name)
            if old != value:
                diff[name] = {"from": old, "to": value}
            row[name] = value
        return diff

    result = write_document(
        ctx.pack,
        artifact_id=f"{kind}:{event_id}",
        rel_path=str(entity.layout.get("path")),
        document=document,
        changes={"monster_ids": monster_ids},
        wall=_wall(entity),
        apply=apply,
        user_edited=False,
        actor=actor,
        session=session,
        detail={"kind": detail_kind, "type": kind, "room": ctx.room_id,
                "mirror_of": room_artifact_id(ctx, STEP_PLACEMENTS)},
    )
    if result.get("no_change"):
        return None
    return {"artifact_id": f"{kind}:{event_id}", "op": "edit"}


# ---------------------------------------------------------------------------
# `canon grid import-grids` — the dense half
# ---------------------------------------------------------------------------


def import_room_grids(
    pack_dir: str | Path,
    room_id: str,
    collision_rows: Any,
    *,
    actor: str = "user",
    session: str | None = None,
    op: str = "edit",
    source: str = "user",
) -> dict:
    """Apply a painted maze grid — the collision layer ONLY (P.6.3): cells
    ``0`` (open) / ``1`` (wall), no int8 cast, no derived layers, no resize.

    The placements are re-stamped after the paint, so the file stays exactly
    what the engine reads. Painting a wall over a placement is REFUSED with
    the reason (P.9 G7: "the user moves the placement first"), as is painting
    over the player start or the door. Journals ``room:<map_id>/grid``.
    """
    ctx = room_context(pack_dir, room_id)
    rows = collision_rows.get("collision") if isinstance(collision_rows, dict) else collision_rows
    if not isinstance(rows, list) or not rows or not all(isinstance(r, list) for r in rows):
        raise ValueError("collision must be a rectangular list of rows of 0/1 cells")
    height = len(rows)
    width = len(rows[0])
    if any(len(r) != width for r in rows):
        raise ValueError("collision rows must all be the same width")
    if (width, height) != (ctx.width, ctx.height):
        raise ValueError(
            f"a room is {ctx.width}×{ctx.height} and cannot be resized — the dungeon engine sizes "
            f"from its own constants (P0 paper P.6.5 M9); got {width}×{height}"
        )
    tiles = {0, 1}
    for y, row in enumerate(rows):
        for x, value in enumerate(row):
            if value not in tiles:
                raise ValueError(
                    f"cell ({x}, {y}) is {value!r} — a maze cell is 0 (open) or 1 (wall); "
                    "items and events are placements, not tile types"
                )

    reserved = _reserved_points(ctx, ctx.maze)
    occupied = _placed_cells(ctx, ctx.maze)
    for (x, y), label in list(reserved.items()) + list(occupied.items()):
        if rows[y][x] == 1:
            raise ValueError(
                f"cell ({x}, {y}) holds {label} — move it before painting a wall there"
            )

    warnings: list[str] = []

    def apply(maze: dict, _changes: dict[str, Any]) -> dict[str, dict]:
        old = [list(r) for r in maze["grid"]]
        painted = [list(r) for r in rows]
        # Re-stamp every placement onto the fresh canvas (P.6.3: "placements
        # re-stamped after the paint") — the paint speaks for tiles only.
        for _wire, (key, block) in ctx.by_wire.items():
            for entry in _entries(maze.get(key), block):
                stamp = block.get("grid_stamp")
                if stamp is None:
                    continue
                painted[entry["y"]][entry["x"]] = (
                    int(entry["id"]) if stamp == "id" else int(stamp)
                )
        changed = sum(
            1
            for y in range(ctx.height)
            for x in range(ctx.width)
            if old[y][x] != painted[y][x]
        )
        if not changed:
            return {}
        maze["grid"] = painted
        return {"grid": {"from": f"{changed} cells", "to": "painted"}}

    def warn(maze: dict, _diff: dict[str, dict]) -> list[str]:
        return _reachability_warning(ctx, maze)

    def validate(maze: dict, _diff: dict[str, dict]) -> Any:
        _validate_room(ctx, maze)
        return None

    before_cells = [list(r) for r in ctx.maze["grid"]]
    result = write_document(
        ctx.pack,
        artifact_id=room_artifact_id(ctx, STEP_GRID),
        rel_path=ctx.rel,
        document=ctx.maze,
        changes={"collision": rows},
        apply=apply,
        warn=warn,
        validate=validate,
        user_edited=False,
        actor=actor,
        session=session,
        detail={"kind": "terrain_paint", "room": ctx.room_id,
                "dims": [ctx.width, ctx.height]},
        op=op,
        source=source,
        warnings=warnings,
    )
    if result.get("no_change"):
        return {
            "level_id": ctx.room_id, "room_id": ctx.room_id, "stage_id": "",
            "updated": [], "no_op": True, "warnings": result["warnings"],
        }
    after_cells = result["document"]["grid"]
    changed_cells = sum(
        1
        for y in range(ctx.height)
        for x in range(ctx.width)
        if before_cells[y][x] != after_cells[y][x]
    )
    return {
        "level_id": ctx.room_id,
        "room_id": ctx.room_id,
        "stage_id": "",
        "updated": ["grid"],
        "dims": [ctx.width, ctx.height],
        "changed_cells": changed_cells,
        "warnings": result["warnings"],
    }


# ---------------------------------------------------------------------------
# Restore — History's write half (P.6.4: "restore writes a new version
# through the same room writer")
# ---------------------------------------------------------------------------


#: Maze keys the PLACEMENTS step owns that neither ``GridKind.placements`` nor
#: ``GridKind.points`` names: the door's own state, the gate reference the door
#: snaps to (P.9 G5) and the room's quest list. Everything else in the step
#: partition is asked of the registry (``_step_keys``); when the ``GridKind``
#: grows a per-step key list (a stamped field, owned by whichever row widens
#: the grid registry), this residue dissolves into it.
_PLACEMENT_EXTRAS = ("door_revealed", "gate_encounter_id", "quest_ids")


def _step_keys(ctx: RoomContext) -> dict[str, tuple[str, ...]]:
    """Which ``maze.json`` keys each STEP owns — the partition a scoped
    restore rewinds (the P0-8 carry-over fix).

    The platformer's steps are FILES, so ``restore_level_step`` is scoped by
    construction; a room's two steps share ONE file, so the scope has to be
    named. Registry first: the grid step owns the dense layer(s) plus the dims
    fields, the placements step owns every placement key and both point
    markers. Keys in NEITHER list (``layout_type``, ``extra``, and the
    ``environment`` / ``environment_name`` mirrors of the room row) belong to
    no step and are never touched by a restore.
    """
    dims = ctx.grid.dims or {}
    grid_keys = [
        *ctx.grid.dense,
        str(dims.get("width_field", "width")),
        str(dims.get("height_field", "height")),
    ]
    placement_keys = [*ctx.grid.placements, *(ctx.grid.points or []), *_PLACEMENT_EXTRAS]
    return {
        STEP_GRID: tuple(dict.fromkeys(grid_keys)),
        STEP_PLACEMENTS: tuple(dict.fromkeys(placement_keys)),
    }


def _restamp(ctx: RoomContext, maze: dict, stamped_by: dict) -> None:
    """Re-derive the grid's placement encodings after a scoped restore.

    The same rule ``import_room_grids`` follows ("the placements are
    re-stamped after the paint, so the file stays exactly what the engine
    reads"): items live in the grid as cell values, events as ``-1``, so a
    grid and a placement list that disagree is a file the engine mis-reads.
    *stamped_by* is the document whose placements produced the stamps the grid
    carries right now — the snapshot when the GRID was restored, the
    pre-restore document when the PLACEMENTS were. Cleared first, stamped
    second, so one kind never wipes another's fresh stamp.
    """
    for _wire, (key, block) in ctx.by_wire.items():
        _clear_stamps(maze, block, _entries(stamped_by.get(key), block))
    for _wire, (key, block) in ctx.by_wire.items():
        for entry in _entries(maze.get(key), block):
            if ctx.in_bounds(entry["x"], entry["y"]):
                _stamp(maze, block, entry["x"], entry["y"], entry["id"])


def _disturbance_warnings(ctx: RoomContext, maze: dict, step: str) -> list[str]:
    """Name every placement — and every reserved point — now standing in a
    wall after a scoped restore (doctrine 10: warn loudly, never refuse and
    never silently repair). Labels come from ``_placed_cells`` and
    ``_reserved_points``, the SAME two tables ``import_room_grids`` refuses on
    (``_validate_wire`` / the paint check), so "cell (8, 5) holds event 3000"
    and "cell (3, 5) holds player_start" read identically in both surfaces.

    *step* decides the sentence, because only a ``grid`` restore brings a wall
    back: restoring ``placements`` puts the placement into a wall that was
    already there. Either way the warning names the CONSEQUENCE — every later
    paint / drag / marker save refuses this cell until the placement moves —
    because "nothing was repaired for you" alone does not tell the author that
    the room is stuck (the carry-over reviewers' CASE A/CASE B).
    """
    out: list[str] = []
    occupied = dict(_placed_cells(ctx, maze))
    occupied.update(_reserved_points(ctx, maze))  # the other half of the paint check
    for (x, y), label in sorted(occupied.items()):
        if not ctx.in_bounds(x, y):
            continue
        if maze["grid"][y][x] != 1:
            continue
        kind = label.split(" ", 1)[0]
        stamps = any(
            block.get("kind") == kind and block.get("grid_stamp") is not None
            for _wire, (_key, block) in ctx.by_wire.items()
        )
        cause = (
            "the restored grid walls it"
            if step == STEP_GRID
            else "the restored placement lands in a wall that was already there"
        )
        out.append(
            f"cell ({x}, {y}) holds {label} but {cause} — it is "
            + (
                "kept and its cell re-opened by the stamp"
                if stamps
                else "kept and now stands in a wall"
            )
            + "; nothing was repaired for you, and every later paint / drag / marker save "
            f"refuses ({x}, {y}) until you move it onto an open cell"
        )
    return out


def restore_room_step(
    pack_dir: str | Path,
    room_id: str,
    step: str,
    to_hash: str,
    *,
    actor: str = "user",
    session: str | None = None,
) -> dict:
    """Make a stored version of ONE room step current again (``op:"restore"``).

    Scoped to the step's own keys (``_step_keys``) — the P0-8 carry-over fix.
    Before it, this wrote the whole stored ``maze.json`` back whichever step
    was named, so restoring ``grid`` silently threw away every ``placements``
    edit made since that version (doctrine 10: an edit may not disappear
    unannounced). Now the CURRENT document is read, the named step's keys are
    overlaid from the snapshot (a key absent there is removed — the stored
    state of that step is what becomes current), the grid's placement stamps
    are re-derived (``_restamp``) so the file stays what the engine reads, and
    the result goes through the same writer as a hand edit: a NEW version,
    journaled, nothing deleted (doctrine 6). A restore whose keys are already
    current is a ``no_change`` — nothing written, nothing journaled.

    Where the restore disturbs the OTHER step — a placement (or the player
    start / door) standing where the restored grid brings a wall back, or a
    restored placement landing in a wall that was already there — it warns
    naming each one AND the consequence (``_disturbance_warnings``) rather
    than refusing or repairing silently. ``packs.dungeon.rolls._restamp``
    makes the other call for the same file and relocates instead; a restore
    must not, because the author chose these bytes and nothing here may move
    what they asked for.

    The hash must belong to THIS room's family (``room:<map_id>/…``) — restore
    only ever rewinds an artifact's own lineage, the rule
    ``platformer_write.restore_asset`` already enforces. ``restore_level_step``
    is untouched: the platformer's steps are per-step FILES.
    """
    ctx = room_context(pack_dir, room_id)
    steps = set(ctx.grid.steps or {}) | {STEP_GRID, STEP_PLACEMENTS}
    if step not in steps:
        raise ValueError(f"step {step!r} is not a room step; one of {sorted(steps)}")
    keys = _step_keys(ctx).get(step)
    if not keys:
        raise ValueError(
            f"step {step!r} names no {ctx.grid.file} keys — this writer restores the steps it "
            f"can scope ({sorted(_step_keys(ctx))})"
        )
    prefix = _fill(ctx.grid.artifact_id, map_id=ctx.room_id, level_id=ctx.room_id)
    if not any(
        str(event.get("artifact_id", "")).startswith(prefix)
        and to_hash in (event.get("before_hash"), event.get("after_hash"))
        for event in provenance.all_events(ctx.pack)
    ):
        raise ValueError(
            f"{to_hash} is not part of {prefix}'s history — restore only rewinds an "
            "artifact's own lineage"
        )
    data = provenance.read_object(ctx.pack, to_hash)
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError(f"version {to_hash} is not JSON — wrong hash?") from None
    if not isinstance(document, dict) or not isinstance(document.get("grid"), list):
        raise ValueError(f"version {to_hash} is not a maze layout — wrong hash?")

    # The stored value of each key this step owns; a key the snapshot lacks is
    # carried as None and REMOVED below (that step had no such key then).
    changes: dict[str, Any] = {
        key: copy.deepcopy(document.get(key))
        for key in keys
        if key in document or key in ctx.maze
    }
    if not changes:
        return {
            "level_id": ctx.room_id, "room_id": ctx.room_id, "restored_step": step,
            "restored_to": to_hash, "before_hash": None, "after_hash": None,
            "changed": {}, "no_change": True, "warnings": [],
        }
    restores_grid = any(key in ctx.grid.dense for key in keys)
    warnings: list[str] = []

    def apply(maze: dict, values: dict[str, Any]) -> dict[str, dict]:
        before = copy.deepcopy(maze)
        diff: dict[str, dict] = {}
        # A dense layer carries the placement STAMPS as well as the true
        # tiles, and `_restamp` puts the current stamps straight back — so its
        # cells are counted AFTER the restamp, never before. Counting first
        # made a rewind past a placement-only edit report `changed: 2 cells`
        # and `no_change: false` for a document it had left byte-identical:
        # a no-op version in the chain and a count that was a lie.
        dense: list[str] = []
        for key, value in values.items():
            old = maze.get(key)
            if key in document:
                maze[key] = value
            elif key in maze:
                del maze[key]
            if old == maze.get(key) and (key in maze) == (key in before):
                continue
            if key in ctx.grid.dense:
                dense.append(key)
            elif key in ctx.grid.placements:
                block = dict(ctx.grid.placements[key])
                id_key = str(block.get("id", "id"))
                flat = [
                    [{id_key: e["id"], "x": e["x"], "y": e["y"]} for e in _entries(doc.get(key), block)]
                    for doc in (before, maze)
                ]
                wire = str(block.get("wire") or key)
                diff[wire] = _placement_diff(flat[0], flat[1], id_key)
            else:
                diff[key] = {"from": old, "to": maze.get(key)}
        if diff or dense:
            _restamp(ctx, maze, document if restores_grid else before)
            for key in dense:
                changed_cells = sum(
                    1
                    for y in range(ctx.height)
                    for x in range(ctx.width)
                    if (before[key][y][x] if y < len(before[key]) else None)
                    != (maze[key][y][x] if y < len(maze[key]) else None)
                )
                if changed_cells:  # 0 = a stamp-only delta `_restamp` put back
                    diff[key] = {"from": f"{changed_cells} cells", "to": "restored"}
        if diff:
            warnings.extend(_disturbance_warnings(ctx, maze, step))
        return diff

    def warn(maze: dict, _diff: dict[str, dict]) -> list[str]:
        return _reachability_warning(ctx, maze)

    def validate(maze: dict, _diff: dict[str, dict]) -> Any:
        _validate_room(ctx, maze)
        return None

    result = write_document(
        ctx.pack,
        artifact_id=room_artifact_id(ctx, step),
        rel_path=ctx.rel,
        document=ctx.maze,
        changes=changes,
        apply=apply,
        warn=warn,
        validate=validate,
        user_edited=False,
        actor=actor,
        session=session,
        detail={"kind": "room_restore", "to": to_hash, "file": ctx.rel, "room": ctx.room_id,
                "step": step, "keys": list(keys), "label": f"restores {ctx.rel} ({step})"},
        op="restore",
        source="user",
        warnings=warnings,
    )
    return {
        "level_id": ctx.room_id,
        "room_id": ctx.room_id,
        "restored_step": step,
        "restored_to": to_hash,
        "before_hash": result["before_hash"],
        "after_hash": result["after_hash"],
        "changed": result["changed"],
        "no_change": bool(result.get("no_change")),
        "warnings": result["warnings"],
    }


# ---------------------------------------------------------------------------
# Shared with the roll entry points (canon.packs.dungeon.rolls)
# ---------------------------------------------------------------------------


def write_room(
    ctx: RoomContext,
    maze: dict,
    *,
    step: str,
    detail: dict,
    actor: str,
    session: str | None,
    op: str = "edit",
    source: str = "user",
    extra_warnings: Iterable[str] = (),
) -> dict:
    """Commit a whole rewritten ``maze.json`` (the rolls' write half) through
    the same fail-closed validation + journal + CAS the hand edits use.
    Returns ``{changed, warnings, before_hash, after_hash, event}``."""
    _validate_room(ctx, maze)
    warnings = [*extra_warnings, *_reachability_warning(ctx, maze)]
    before = json.dumps(ctx.maze, sort_keys=True)
    if json.dumps(maze, sort_keys=True) == before:
        return {"no_change": True, "warnings": warnings, "event": None}
    committed = commit_document(
        ctx.pack,
        artifact_id=room_artifact_id(ctx, step),
        rel_path=ctx.rel,
        data=maze,
        actor=actor,
        session=session,
        detail=detail,
        op=op,
        source=source,
    )
    return {"no_change": False, "warnings": warnings, **committed}


def read_room_json(ctx: RoomContext) -> dict:
    """A deep copy of the loaded ``maze.json`` for a caller to rewrite."""
    return copy.deepcopy(ctx.maze)


def room_rows(pack_dir: str | Path, kind: str) -> dict[str, dict]:
    """One kind's rows straight off disk — the rolls' roster source."""
    pack = Path(pack_dir)
    spec = resolve_pack(pack).spec
    entity = spec.entities.get(kind)
    if entity is None:
        return {}
    loader = entity.loader or (lambda p: load_rows(p, entity))
    return loader(pack)


def pack_seed(pack_dir: str | Path) -> str:
    """The pack's generation seed (``manifest.json.seed``) — the base every
    roll derives from (P.9 G8)."""
    manifest = read_json(Path(pack_dir) / "manifest.json") or {}
    return str(manifest.get("seed", ""))
