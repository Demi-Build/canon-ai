"""Read-side helpers for the dungeon output tree — the sibling of
``platformer_read`` (P0 paper P.6.3 / P.6.3a; row P0-5).

``export_room_bundle`` projects one room — ``rooms/<id>/maze.json`` plus the
row files its placements point at — into the SAME render-ready shape
``platformer_read.export_level_bundle`` emits, so cradle's ``LevelCanvas`` /
``drawLevel`` take it untouched. The shared thing is the OUTPUT SHAPE, never
the body (P.6.3 step 1: the platformer reader hard-requires ``manifest.stages``
and a tileset manifest); the dispatch between the two readers lives in
``canon grid export`` (``cli/main.py``) on the resolved pack's ``GridKind``.

What the projection does (P.6.3 steps 2–7):

- the grid normalises to ``collision[y][x] = 1 iff wall``; ``terrain`` is a
  copy and ``background`` zeros — blocks mode never reads them (P.6.2 row 15);
- every placement lifts out of ``maze.json`` by the ``GridKind.placements``
  data: a ``grid_stamp`` of ``"id"`` means the CELL carries the row id, so the
  grid is the engine's truth and the sidecar list only fills metadata (items);
  ``-1`` means the cell is a marker and the sidecar list is the truth (events);
  ``None`` means the sidecar list alone (npcs). Every grid ↔ sidecar
  disagreement is NAMED in ``warnings[]`` and never blocks — the next write
  repairs it;
- ``spawn`` ← ``player_start``, ``exit`` ← ``door_position``; the room-only
  facts (``environment*``, ``door_revealed``, ``gate_encounter_id``,
  ``quest_ids``, the ``monsters`` lore bucket) ride the additive ``room`` block;
- the tileset is synthesised from the template's tile registry
  (``GridKind.cell_vocab`` → ``tiles.json``), the palette from its
  per-environment wall table and a cradle theme token for the open cell
  (P.9 G1/G2), ``tilesheet_path_abs`` null;
- ``revision`` = sha256 over the ``maze.json`` bytes ‖ the canonical
  ``rooms.json`` row (or nothing when the legacy tree has no index);
  ``last_change`` = the newest journal event in the ``room:<id>/`` family
  (P.9 R1, read from ``GridKind.artifact_id``);
- the platformer-only keys (``layout_fallback``, ``parent_level``, ``brief``,
  music) ride along neutral so the key set is exactly the level bundle's plus
  ``room`` and ``warnings`` — one ``LevelBundle`` type on the cradle side.

Row P0-8 added the two projections the P0-5 column left as a structured
"not yet" — ``describe_room`` (the bundle's counts, dims and validation, the
dungeon sibling of ``describe_level``) and ``window=`` on the export (the
same ``window_bundle`` slice the platformer takes, since the shape is
shared). Both stay pure reads.

Pure projection: nothing here writes, journals or snapshots — the tests pin
the pack's file list and hashes before/after (doctrine 1; P.6.4 "engine
parity: byte-untouched"). Deliberately absent, by row ownership: every write
(the room writer behind ``apply-edit`` / ``import-grids`` lives in
``adapters.dungeon_write``; the per-step rolls in
``canon.packs.dungeon.rolls``) and dialogue (P0-9).
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from canon.adapters.platformer_read import journal_last_change, window_bundle
from canon.packs import ResolvedPack, resolve_pack
from canon.packs.dungeon.loaders import load_rows
from canon.packs.dungeon.parsers import ENV_TO_COLOR
from canon.packs.spec import EntityKind, GridKind, PackSpec

#: Neutral values for the level bundle's platformer-only keys (P.6.2 row 15:
#: "emit zeros/empties … blocks mode never reads them").
_PLATFORMER_ONLY_KEYS: dict[str, Any] = {
    "layout_fallback": False,
    "parent_level": None,
    "brief": None,
    "variants": [],
    "hazards": [],
    "foreground": [],
    "props": {},
    "backdrop": None,
    "music_path": "",
    "music_path_abs": None,
    "music_sections": [],
    "stage_music": "",
    "stage_music_abs": None,
}


class _Blank(dict):
    """``str.format_map`` helper: an unknown placeholder renders empty, so
    ``room:{map_id}/{step}`` with only ``map_id`` yields the family PREFIX."""

    def __missing__(self, key: str) -> str:
        return ""


def _fill(template: str, **values: str) -> str:
    return template.format_map(_Blank(values))


def _read_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _hex(rgb: Any) -> str | None:
    """``[r, g, b]`` (the engine's tuple form) → ``#rrggbb``; ``None`` when the
    value is not a colour triple."""
    if isinstance(rgb, (list, tuple)) and len(rgb) == 3:
        try:
            r, g, b = (max(0, min(255, int(v))) for v in rgb)
        except (TypeError, ValueError):
            return None
        return f"#{r:02x}{g:02x}{b:02x}"
    return None


def _abs(pack: Path, rel: Any) -> str | None:
    """A row's asset path resolved to an absolute path — the engine trees
    carry machine-absolute portrait paths already; a relative one joins the
    pack. Empty / non-string → ``None`` (no art), never an empty string."""
    if not isinstance(rel, str) or not rel:
        return None
    path = Path(rel)
    return str(path if path.is_absolute() else (pack / path).resolve())


def _int_or(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _single_file_grid(spec: PackSpec) -> GridKind | None:
    """The GridKind this reader knows how to read: one JSON file per grid
    (``file`` set), the dungeon room's shape. Data, not an id: a third
    template with the same shape reads through the same path."""
    for grid in spec.grids.values():
        if grid.file:
            return grid
    return None


def _tile_registry(spec: PackSpec, grid: GridKind) -> dict:
    """``GridKind.cell_vocab`` resolved against the template dir (P.6.3)."""
    if spec.template_dir is None or not grid.cell_vocab:
        raise FileNotFoundError(f"pack type {spec.pack_type!r} declares no tile registry for grid {grid.kind!r}")
    path = spec.template_dir / grid.cell_vocab
    registry = _read_json(path)
    if not isinstance(registry, dict) or not isinstance(registry.get("tiles"), list):
        raise FileNotFoundError(f"tile registry {path} is missing or malformed")
    return registry


def _rows(pack: Path, spec: PackSpec, kind: str) -> tuple[EntityKind | None, dict[str, dict]]:
    """A kind's rows through its seeded ``loader`` (§8.2), falling back to
    the generic ``load_rows`` on the stamped layout for a kind the seed left
    unbound (a ``db define``d type, P0-6)."""
    entity = spec.entities.get(kind)
    if entity is None:
        return None, {}
    loader = entity.loader or (lambda p: load_rows(p, entity))
    return entity, loader(pack)


def _room_row(pack: Path, spec: PackSpec, grid: GridKind, room_id: str) -> tuple[dict | None, dict | None]:
    """``(rooms.json[id], world_bible.json.rooms[id])`` — the index row and
    its mirror (P.1.7). The legacy trees predate the index, so the mirror
    alone answers there; only the INDEX row joins the revision (P.9 R1)."""
    _, index = _rows(pack, spec, grid.kind)
    row = index.get(room_id)
    bible = _read_json(pack / "world_bible.json")
    mirror = None
    if isinstance(bible, dict) and isinstance(bible.get("rooms"), dict):
        candidate = bible["rooms"].get(room_id)
        mirror = candidate if isinstance(candidate, dict) else None
    return (row if isinstance(row, dict) else None), mirror


def _revision(maze_bytes: bytes, index_row: dict | None) -> dict:
    """``sha256(maze.json bytes ‖ canonical rooms.json[id])`` (P.6.3a); the
    canonical form is sorted-key compact JSON so key order on disk is not
    identity. Absent index → the maze bytes alone."""
    h = hashlib.sha256()
    h.update(maze_bytes)
    if index_row is not None:
        h.update(json.dumps(index_row, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    digest = h.hexdigest()
    return {"revision": f"sha256:{digest}", "short": digest[:10]}


def _positions(maze: dict, key: str, placement: dict) -> tuple[list[dict], list[str]]:
    """The sidecar list for one placement key as ``[{id, x, y, meta}]`` in
    file order, by the stamped ``shape`` (``dict`` = ``{id: [x, y]}``,
    ``list`` = ``[{x, y, <id key>, …}]``). Malformed entries are named in
    the returned warnings and skipped, never fatal."""
    raw = maze.get(key)
    out: list[dict] = []
    warnings: list[str] = []
    if raw is None:
        return out, warnings
    if placement.get("shape") == "dict":
        if not isinstance(raw, dict):
            return out, [f"{key}: expected an object of id → [x, y], found {type(raw).__name__}"]
        for rid, xy in raw.items():
            if not (isinstance(xy, (list, tuple)) and len(xy) == 2):
                warnings.append(f"{key}: {rid} has no [x, y] position")
                continue
            try:
                x, y = int(xy[0]), int(xy[1])
            except (TypeError, ValueError):
                warnings.append(f"{key}: {rid} has a non-integer position {list(xy)!r}")
                continue
            out.append({"id": str(rid), "x": x, "y": y, "meta": {}})
        return out, warnings
    if not isinstance(raw, list):
        return out, [f"{key}: expected a list, found {type(raw).__name__}"]
    id_key = str(placement.get("id", "id"))
    for entry in raw:
        if not isinstance(entry, dict) or id_key not in entry or "x" not in entry or "y" not in entry:
            warnings.append(f"{key}: entry {entry!r} lacks {id_key}/x/y")
            continue
        try:
            x, y = int(entry["x"]), int(entry["y"])
        except (TypeError, ValueError):
            warnings.append(
                f"{key}: {entry[id_key]} has a non-integer position {[entry['x'], entry['y']]!r}"
            )
            continue
        out.append({"id": str(entry[id_key]), "x": x, "y": y, "meta": entry})
    return out, warnings


def _in_bounds(x: int, y: int, width: int, height: int) -> bool:
    return 0 <= x < width and 0 <= y < height


def export_room_bundle(pack_dir: str | Path, room_id: str, window: Any = None) -> dict:
    """Assemble the render-ready bundle for one room of a dungeon pack —
    the P.6.3a shape, key for key (see the module docstring for each
    field's source). Raises ``FileNotFoundError`` when the room is unknown
    (the CLI turns it into the structured error every read verb emits).

    ``window`` (row P0-8, additive — the platformer's row A3 flag): ``(x0,
    y0, w, h)`` in room cells slices the grids and filters the placements to
    that region, keeping ABSOLUTE coordinates and the full ``grid_width`` /
    ``grid_height``. One slicer serves both grids because the bundle SHAPE is
    shared (P.6.3 step 1).
    """
    pack = Path(pack_dir)
    resolved: ResolvedPack = resolve_pack(pack)
    spec = resolved.spec
    grid_kind = _single_file_grid(spec)
    if grid_kind is None:
        raise FileNotFoundError(f"pack type {resolved.pack_type!r} declares no single-file grid to export")
    # A room id is a directory name under the grid's path template — never a
    # path of its own (the template supplies every separator).
    if not room_id or "/" in room_id or "\\" in room_id or room_id in (".", ".."):
        raise FileNotFoundError(f"room {room_id!r} is not a room id")
    maze_path = pack / _fill(grid_kind.path_template, map_id=room_id, level_id=room_id)
    if not maze_path.is_file():
        raise FileNotFoundError(f"room {room_id!r} not found: {maze_path} is missing")

    maze_bytes = maze_path.read_bytes()
    maze = json.loads(maze_bytes)
    if not isinstance(maze, dict) or not isinstance(maze.get("grid"), list):
        raise ValueError(f"{maze_path} is not a maze layout (no grid)")
    warnings: list[str] = []

    # --- dims + the normalised grid (P.6.3 step 2) -------------------------
    grid = [list(row) if isinstance(row, list) else [] for row in maze["grid"]]
    actual_h = len(grid)
    actual_w = max((len(row) for row in grid), default=0)
    dims = grid_kind.dims or {}
    width = _int_or(maze.get(dims.get("width_field", "width")))
    height = _int_or(maze.get(dims.get("height_field", "height")))
    if width is None or height is None:
        width, height = actual_w, actual_h
    elif (width, height) != (actual_w, actual_h):
        warnings.append(
            f"{grid_kind.file}: width/height say {width}×{height} but the grid is "
            f"{actual_w}×{actual_h} — rendering the grid's own size"
        )
        width, height = actual_w, actual_h
    collision = [[1 if cell == 1 else 0 for cell in row] + [0] * (width - len(row)) for row in grid]

    # --- rows the placements point at ---------------------------------------
    index_row, mirror_row = _room_row(pack, spec, grid_kind, room_id)
    environment = str(maze.get("environment") or (index_row or mirror_row or {}).get("environment") or "")
    environment_name = str(
        maze.get("environment_name") or (index_row or mirror_row or {}).get("environment_name") or ""
    )

    registry = _tile_registry(spec, grid_kind)
    tile_px = int(registry.get("tile_px", 20))
    markers = registry.get("markers") or {}
    palette_data = registry.get("palette") or {}
    walls = palette_data.get("wall_by_environment") or {}
    wall = walls.get(environment) or walls.get(str(palette_data.get("wall_fallback", "")), "#32323c")

    # --- placements, generically from the GridKind's placements block -------
    lists: dict[str, list[dict]] = {}
    # What a cell may legally hold besides a tile id: the int stamps (-1) and
    # the id-carrying kinds' ``(base, rows)`` — for the "neither a tile nor a
    # placement" warning after the placements pass.
    stamp_values: set[int] = set()
    id_carriers: list[tuple[int | None, dict[str, dict]]] = []
    for key, placement in grid_kind.placements.items():
        kind = str(placement.get("kind", ""))
        wire = str(placement.get("wire", ""))
        stamp = placement.get("grid_stamp")
        entity, rows = _rows(pack, spec, kind)
        row_file = str((entity.layout if entity else {}).get("path", f"{kind} rows"))
        positions, pos_warnings = _positions(maze, key, placement)
        warnings.extend(pos_warnings)
        for pos in positions:
            if not _in_bounds(pos["x"], pos["y"], width, height):
                warnings.append(
                    f"{key}: {kind} {pos['id']} at ({pos['x']}, {pos['y']}) is outside the {width}×{height} grid"
                )
        placed: list[dict] = []
        if stamp == "id":
            # The CELL carries the row id: the grid is the engine's truth (P.6.3
            # step 2); the sidecar list only fills metadata and is cross-checked.
            # A cell is this kind's stamp when it reads at or above the kind's
            # id base (P.6.1: ``>= 2000`` item on the cell), or names a row.
            base = _int_or(((entity.id_alloc if entity else None) or {}).get("base"))
            id_carriers.append((base, rows))
            by_cell = {(p["x"], p["y"]): p for p in positions}
            seen: set[tuple[int, int]] = set()
            for y, row in enumerate(grid):
                for x, value in enumerate(row):
                    if not isinstance(value, int) or value < 2:
                        continue
                    if not ((base is not None and value >= base) or str(value) in rows):
                        continue
                    seen.add((x, y))
                    meta = by_cell.get((x, y))
                    if meta is None:
                        warnings.append(f"grid: cell ({x}, {y}) reads {kind} {value} with no {key} entry")
                    elif meta["id"] != str(value):
                        warnings.append(
                            f"{key}: {kind} {meta['id']} at ({x}, {y}) but the grid cell reads {value}"
                        )
                    placed.append({"id": str(value), "x": x, "y": y, "meta": (meta or {}).get("meta", {})})
            for pos in positions:
                if (pos["x"], pos["y"]) not in seen:
                    cell = grid[pos["y"]][pos["x"]] if _in_bounds(pos["x"], pos["y"], width, height) else None
                    warnings.append(
                        f"{key}: {kind} {pos['id']} at ({pos['x']}, {pos['y']}) but the grid cell reads {cell}"
                    )
        elif stamp is not None:
            # The cell is a bare marker (-1): the sidecar list is the truth;
            # the marker cells are cross-checked against it.
            placed = positions
            if isinstance(stamp, int):
                stamp_values.add(stamp)
            marked = {
                (x, y) for y, row in enumerate(grid) for x, value in enumerate(row) if value == stamp
            }
            listed = {(p["x"], p["y"]) for p in positions}
            for pos in positions:
                if (pos["x"], pos["y"]) not in marked and _in_bounds(pos["x"], pos["y"], width, height):
                    cell = grid[pos["y"]][pos["x"]]
                    warnings.append(
                        f"{key}: {kind} {pos['id']} at ({pos['x']}, {pos['y']}) but the grid cell reads "
                        f"{cell} (expected {stamp})"
                    )
            for x, y in sorted(marked - listed, key=lambda c: (c[1], c[0])):
                warnings.append(f"grid: cell ({x}, {y}) reads {stamp} ({kind} stamp) with no {key} entry")
        else:
            # No grid stamp: the sidecar list alone, in stable id order.
            placed = sorted(positions, key=lambda p: (_int_or(p["id"]) is None, _int_or(p["id"]) or 0, p["id"]))
        for pos in placed:
            if pos["id"] not in rows:
                warnings.append(f"{key}: {kind} {pos['id']} has no row in {row_file}")
        lists[wire] = [
            {"kind": kind, "row": rows.get(p["id"]) or {}, "id": p["id"], "x": p["x"], "y": p["y"], "meta": p["meta"]}
            for p in placed
        ]

    # Cells that are neither a registered tile nor any placement's stamp —
    # named, capped so a corrupt grid reads as one finding, not thousands.
    tile_ids = {int(tile["id"]) for tile in registry["tiles"]}
    unknown: list[str] = []
    for y, row in enumerate(grid):
        for x, value in enumerate(row):
            if not isinstance(value, int) or value in tile_ids or value in stamp_values:
                continue
            if any((base is not None and value >= base) or str(value) in rows for base, rows in id_carriers):
                continue
            unknown.append(f"grid: cell ({x}, {y}) reads {value} — not a tile type or a placement stamp")
    warnings.extend(unknown[:10])
    if len(unknown) > 10:
        warnings.append(f"grid: … and {len(unknown) - 10} more cells with unknown values")

    env_color = _hex(ENV_TO_COLOR.get(environment)) or str(markers.get("npc", "#808080"))
    entities: list[dict] = []
    for p in lists.get("entities", []):
        row = p["row"]
        entities.append(
            {
                "enemy_id": p["id"],
                "x": p["x"],
                "y": p["y"],
                "variant": None,
                "name": str(row.get("name") or p["id"]),
                "archetype": row.get("type"),
                "size": 1,
                "placeholder_color": _hex(row.get("color")) or env_color,
                "sprite_path_abs": _abs(pack, row.get("profile_image")),
            }
        )
    items: list[dict] = []
    for p in lists.get("items", []):
        row, meta = p["row"], p["meta"]
        items.append(
            {
                "item_id": p["id"],
                "x": p["x"],
                "y": p["y"],
                "source": None,
                "name": str(row.get("name") or meta.get("name") or p["id"]),
                "kind": row.get("category"),
                "placeholder_color": str(markers.get("item", "#ffd700")),
                "sprite_path_abs": _abs(pack, row.get("profile_image") or meta.get("profile_image")),
            }
        )
    triggers: list[dict] = []
    for p in lists.get("triggers", []):
        row = p["row"]
        event_id = _int_or(p["id"])
        triggers.append(
            {
                "x": p["x"],
                "y": p["y"],
                "type": str(row.get("type") or p["kind"]),
                "params": {
                    "event_id": event_id if event_id is not None else p["id"],
                    "is_gate": bool(row.get("is_gate", False)),
                    "is_climax_boss": bool(row.get("is_climax_boss", False)),
                    "monster_ids": list(row.get("monster_ids") or []),
                },
            }
        )

    gate_id = maze.get("gate_encounter_id")
    if gate_id is not None and not any(str(t["params"]["event_id"]) == str(gate_id) for t in triggers):
        warnings.append(f"gate_encounter_id {gate_id} is not placed in event_positions")

    # --- points, tileset, revision ------------------------------------------
    points = grid_kind.points or ["player_start", "door_position"]
    spawn = maze.get(points[0]) if len(points) > 0 else None
    door = maze.get(points[1]) if len(points) > 1 else None
    slots = [
        {
            "index": index,
            "tile_type": int(tile["id"]),
            "name": str(tile["name"]),
            "px_region": [0, 0, tile_px, tile_px],
            "collision": str(tile.get("category", "empty")),
            "params": {},
        }
        for index, tile in enumerate(registry["tiles"])
    ]
    tileset = {
        "slots": slots,
        "palette": {"background": str(palette_data.get("background", "--bg-sunken")), "wall": str(wall)},
        "render_filter": "nearest",
        "tilesheet_path_abs": None,
    }
    revision = _revision(maze_bytes, index_row)
    prefix = _fill(grid_kind.artifact_id, map_id=room_id, level_id=room_id)
    room_row = index_row or mirror_row or {}

    bundle: dict[str, Any] = {
        "level_id": room_id,
        "stage_id": "",
        "display_name": environment_name or None,
        "revision": revision["revision"],
        "revision_short": revision["short"],
        "last_change": journal_last_change(pack, prefix) if prefix else None,
        "grid_width": width,
        "grid_height": height,
        "spawn": [int(spawn[0]), int(spawn[1])] if isinstance(spawn, (list, tuple)) and len(spawn) == 2 else None,
        "exit": [int(door[0]), int(door[1])] if isinstance(door, (list, tuple)) and len(door) == 2 else None,
        "tile_px": tile_px,
        "actor_scale": 1,
        "water_alpha": 1,
        "grids": {
            "collision": collision,
            "terrain": copy.deepcopy(collision),
            "background": [[0] * width for _ in range(height)],
        },
        "tileset": tileset,
        "tiles_by_type": {str(slot["tile_type"]): slot for slot in slots},
        "entities": entities,
        "items": items,
        "triggers": triggers,
        "warnings": warnings,
        "room": {
            "environment": environment,
            "environment_name": environment_name,
            "door_revealed": bool(maze.get("door_revealed", False)),
            "gate_encounter_id": gate_id,
            "quest_ids": list(maze.get("quest_ids") or []),
            "monsters": list(room_row.get("monsters") or []),
        },
    }
    bundle.update(copy.deepcopy(_PLATFORMER_ONLY_KEYS))
    if window is not None:
        window_bundle(bundle, window)
    return bundle


def _counts(values: list[Any]) -> dict[str, int]:
    """Sorted ``value → count``, empty rendered ``"unknown"`` — the same
    histogram helper ``describe_level`` uses."""
    out: dict[str, int] = {}
    for value in values:
        key = str(value) if value not in (None, "") else "unknown"
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


def describe_room(pack_dir: str | Path, room_id: str) -> dict:
    """A compact summary of one room — the describe-first read (Phase 1 §3.4)
    for a dungeon grid, and the sibling of ``platformer_read.describe_level``.

    It is a thin PROJECTION of the room bundle: counts and dims from the same
    arrays, the tile histogram by the registry's own categories, the room
    passthrough block, and the export's ``warnings`` as the validation
    verdict — a room has no ``validate_level`` equivalent yet (the dungeon
    validators run at generation), so ``validation.ok`` means "the projection
    found no grid ↔ sidecar disagreement", and the warnings say what did.
    Pure read; nothing is written.
    """
    bundle = export_room_bundle(pack_dir, room_id)
    grid = bundle["grids"]["collision"]
    by_type = bundle["tiles_by_type"]
    histogram: dict[str, int] = {}
    for row in grid:
        for value in row:
            slot = by_type.get(str(int(value))) or {}
            category = str(slot.get("collision") or "unknown")
            histogram[category] = histogram.get(category, 0) + 1
    entities, items, triggers = bundle["entities"], bundle["items"], bundle["triggers"]
    warnings = list(bundle.get("warnings") or [])
    return {
        "level_id": room_id,
        "room_id": room_id,
        "stage_id": "",
        "display_name": bundle["display_name"],
        "brief": None,
        "dims": {"width": bundle["grid_width"], "height": bundle["grid_height"], "axis": None},
        "spawn": bundle["spawn"],
        "exit": bundle["exit"],
        "rooms": [],
        "parent_level": None,
        "tiles": {
            "cells": bundle["grid_width"] * bundle["grid_height"],
            "by_category": dict(sorted(histogram.items())),
        },
        "entities": {
            "count": len(entities),
            "by_archetype": _counts([e.get("archetype") for e in entities]),
            "placed": [
                {"id": e["enemy_id"], "archetype": e.get("archetype"), "x": e["x"], "y": e["y"]}
                for e in entities
            ],
        },
        "items": {
            "count": len(items),
            "by_kind": _counts([i.get("kind") for i in items]),
            "placed": [
                {"id": i["item_id"], "kind": i.get("kind"), "x": i["x"], "y": i["y"]}
                for i in items
            ],
        },
        "triggers": {
            "count": len(triggers),
            "by_type": _counts([t.get("type") for t in triggers]),
            "placed": [
                {
                    "id": str((t.get("params") or {}).get("event_id", "")),
                    "type": t.get("type"),
                    "x": t["x"],
                    "y": t["y"],
                    "is_gate": bool((t.get("params") or {}).get("is_gate")),
                    "monster_ids": list((t.get("params") or {}).get("monster_ids") or []),
                }
                for t in triggers
            ],
        },
        "hazards": {"count": 0, "by_type": {}},
        "overrides": {"rules": {}, "movement": {}},
        "room": bundle["room"],
        "validation": {
            "ok": not warnings,
            "problems": warnings,
            "repair_count": 0,
            "rooms": [],
        },
        "revision": bundle["revision"],
        "revision_short": bundle["revision_short"],
        "last_change": (bundle["last_change"] or {}).get("label"),
    }


__all__ = ["describe_room", "export_room_bundle"]
