"""Write-side helpers for hand-edits to a platformer pack.

Cradle (or any editor) sends a partial level edit — moved enemy/item/door
placements, relocated spawn/exit — and this applies it: rewrites the affected
sparse layer files, recomputes their content hashes, updates the inline copies
+ hashes on ``level.json``, and stamps the level ``user_edited`` so a later
``canon regen`` cascade never clobbers the hand-edit (USER_EDITED protection).

Every mutation is also recorded to the provenance journal + content-addressed
object store (:mod:`canon.provenance`): the before/after bytes are snapshotted
and an ``edit`` event with the semantic diff is appended, so the (generated →
edited) trajectory is preserved as training data. ``baseline_level`` records the
matching ``generate`` events when cradle first imports a fresh generation.

Grids (collision/terrain/background ``.npz``) are NOT touched here — terrain
painting goes through a separate grid-import path. This is the sparse-layer half.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from canon import provenance
from canon.adapters.json_adapter import JsonOutputAdapter

_SPARSE = ("triggers", "hazards", "foreground")
# Level step artifacts that live in their own file (for baseline + addressing).
_STEP_FILES = {
    "collision": "collision.npz",
    "terrain": "terrain.npz",
    "background": "background.npz",
    "hazards": "hazards.json",
    "triggers": "triggers.json",
    "foreground": "foreground.json",
    "entities": "entities.json",
    "items": "items.json",
    "level": "level.json",
}


def _read(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _read_opt(path: Path) -> Any | None:
    return _read(path) if path.is_file() else None


def _find_level_dir(pack: Path, level_id: str) -> tuple[Path, str]:
    level_root = pack / "level"
    if level_root.is_dir():
        for stage_dir in level_root.iterdir():
            cand = stage_dir / level_id
            if (cand / "level.json").is_file():
                return cand, stage_dir.name
    raise FileNotFoundError(f"level {level_id!r} not found under {level_root}")


def _artifact_id(stage_id: str, level_id: str, step: str) -> str:
    return f"level:{stage_id}/{level_id}/{step}"


def level_artifact_id(pack_dir: str | Path, level_id: str, step: str) -> str:
    """Public: resolve a level step's artifact id (finds the stage on disk)."""
    _, stage_id = _find_level_dir(Path(pack_dir), level_id)
    return _artifact_id(stage_id, level_id, step)


def _placement_diff(old: list[dict] | None, new: list[dict], id_key: str) -> dict:
    """Index-aligned diff for placement lists: moves, id switches, count delta.

    Switches (same slot, different definition) are a distinct training signal —
    "generated X rejected in favor of Y" — so they get their own detail list.
    """
    old = old or []
    moves: list[dict] = []
    switched: list[dict] = []
    for i, n in enumerate(new):
        o = old[i] if i < len(old) else None
        if o is not None and o.get(id_key) != n.get(id_key):
            switched.append({"index": i, "from": o.get(id_key), "to": n.get(id_key)})
        if o is None or o.get("x") != n["x"] or o.get("y") != n["y"]:
            moves.append(
                {
                    "id": n[id_key],
                    "from": ([o["x"], o["y"]] if o else None),
                    "to": [n["x"], n["y"]],
                }
            )
    out: dict = {"moves": moves}
    if switched:
        out["switched"] = switched
    if len(new) != len(old):
        out["count"] = {"from": len(old), "to": len(new)}
    return out


def apply_level_edit(
    pack_dir: str | Path,
    level_id: str,
    edit: dict,
    *,
    actor: str = "user",
    session: str | None = None,
    op: str = "edit",
    source: str = "user",
) -> dict:
    """Apply a sparse-layer edit, persist it, and journal the mutation.

    ``op``/``source`` default to a user edit; ``restore_level_step`` reuses this
    with ``op="restore"`` so reverting to an old version flows through the same
    write + journal path.
    """
    pack = Path(pack_dir)
    level_dir, stage_id = _find_level_dir(pack, level_id)
    level = _read(level_dir / "level.json")
    adapter = JsonOutputAdapter(pack)
    rel = f"level/{stage_id}/{level_id}"
    updated: list[str] = []
    events: list[dict] = []

    def _emit(step: str, before_hash: str | None, after_path: Path, detail: Any) -> None:
        after_hash = provenance.snapshot_file(pack, after_path)
        if before_hash == after_hash:
            # No-op write (e.g. a placement grabbed and released in place) —
            # don't journal noise; it would pollute the training corpus.
            return
        events.append(
            provenance.record(
                pack,
                artifact_id=_artifact_id(stage_id, level_id, step),
                op=op,
                source=source,
                actor=actor,
                session=session,
                detail=detail,
                before_hash=before_hash,
                after_hash=after_hash,
            )
        )

    # Enemy placements: flat entities.json + rich level.json.entities.
    if "entities" in edit:
        before = provenance.snapshot_file(pack, level_dir / "entities.json")
        old = _read_opt(level_dir / "entities.json")
        flat = [
            {"enemy_id": e["enemy_id"], "x": int(e["x"]), "y": int(e["y"]), "variant": e.get("variant")}
            for e in edit["entities"]
        ]
        h = adapter.write_json_array(f"{rel}/entities.json", flat)
        level["entities"] = [
            {"ref": f"enemy:{e['enemy_id']}", "pos": [e["x"], e["y"]],
             "overrides": ({"variant": e["variant"]} if e.get("variant") else {})}
            for e in flat
        ]
        level["entities_hash"] = h
        updated.append("entities")
        _emit("entities", before, level_dir / "entities.json",
              {"kind": "enemy_move", **_placement_diff(old, flat, "enemy_id")})

    # Item placements: flat items.json + rich level.json.items.
    if "items" in edit:
        before = provenance.snapshot_file(pack, level_dir / "items.json")
        old = _read_opt(level_dir / "items.json")
        flat = [
            {"item_id": it["item_id"], "x": int(it["x"]), "y": int(it["y"]), "source": it.get("source")}
            for it in edit["items"]
        ]
        h = adapter.write_json_array(f"{rel}/items.json", flat)
        level["items"] = [
            {"ref": f"item:{it['item_id']}", "pos": [it["x"], it["y"]],
             "overrides": ({"source": it["source"]} if it.get("source") else {})}
            for it in flat
        ]
        level["items_hash"] = h
        updated.append("items")
        _emit("items", before, level_dir / "items.json",
              {"kind": "item_move", **_placement_diff(old, flat, "item_id")})

    # Sparse masks (same shape on disk and inline): triggers/hazards/foreground.
    for layer in _SPARSE:
        if layer not in edit:
            continue
        before = provenance.snapshot_file(pack, level_dir / f"{layer}.json")
        entries = [
            {"x": int(e["x"]), "y": int(e["y"]), "type": e["type"], "params": e.get("params", {})}
            for e in edit[layer]
        ]
        h = adapter.write_json_array(f"{rel}/{layer}.json", entries)
        level[layer] = entries
        level[f"{layer}_hash"] = h
        updated.append(layer)
        _emit(layer, before, level_dir / f"{layer}.json", {"kind": f"{layer}_change", "count": len(entries)})

    # Music override fields — level.json-only. Assigning just repoints the
    # level (or a section) at an existing track; the track BYTES are written by
    # the music-generate op. Both flow through here so cradle never writes
    # pack files itself and every change is journaled.
    music_detail: dict[str, Any] = {}
    if "music_path" in edit:
        music_detail["music_path"] = {
            "from": level.get("music_path", ""), "to": str(edit["music_path"] or "")
        }
        level["music_path"] = str(edit["music_path"] or "")
        level["music_hash"] = str(edit.get("music_hash", "") or "")
        updated.append("music")
    if "music_sections" in edit:
        level["music_sections"] = [
            {
                "start": int(s["start"]), "end": int(s["end"]),
                "music_path": str(s.get("music_path", "") or ""),
                "music_hash": str(s.get("music_hash", "") or ""),
                "name": str(s.get("name", "") or ""),
            }
            for s in edit["music_sections"]
        ]
        music_detail["music_sections"] = len(level["music_sections"])
        updated.append("music_sections")

    # Point fields live only on level.json.
    point_detail: dict[str, Any] = {}
    for point in ("spawn", "exit"):
        if point in edit and edit[point] is not None:
            point_detail[point] = {"from": level.get(point), "to": [int(edit[point][0]), int(edit[point][1])]}
            level[point] = [int(edit[point][0]), int(edit[point][1])]
            updated.append(point)

    if not updated:
        raise ValueError("edit contained no recognized layers")

    level["status"] = "user_edited"
    before_level = provenance.snapshot_file(pack, level_dir / "level.json")
    adapter.write_json_singleton(f"{rel}/level.json", level)
    if point_detail or music_detail:
        _emit(
            "level", before_level, level_dir / "level.json",
            {"kind": "level_edit", **point_detail, **music_detail},
        )

    return {
        "level_id": level_id,
        "stage_id": stage_id,
        "updated": updated,
        "status": "user_edited",
        "events": len(events),
    }


def baseline_level(
    pack_dir: str | Path,
    level_id: str,
    *,
    actor: str = "cradle",
    session: str | None = None,
    op: str = "generate",
    detail: dict | None = None,
) -> dict:
    """Record step-artifact events for a level's as-generated files.

    Called when cradle imports a fresh generation (``op="generate"``, the
    default) or after a context-aware improve (``op="regenerate"``). Idempotent
    — a step already in the journal at its current hash is skipped, so
    re-opening a level is safe. ``detail`` (e.g. ``{"kind": "improve"}``) rides
    each recorded event so consumers can tell WHICH generation op produced the
    change (generate / improve / regenerate / place_enemies / place_items).
    """
    pack = Path(pack_dir)
    level_dir, stage_id = _find_level_dir(pack, level_id)
    recorded: list[str] = []
    for step, fname in _STEP_FILES.items():
        path = level_dir / fname
        after_hash = provenance.snapshot_file(pack, path)
        if after_hash is None:
            continue
        aid = _artifact_id(stage_id, level_id, step)
        if provenance.already_recorded(pack, aid, after_hash):
            continue
        provenance.record(
            pack,
            artifact_id=aid,
            op=op,
            source="llm",
            actor=actor,
            session=session,
            detail=detail,
            after_hash=after_hash,
        )
        recorded.append(step)
    return {"level_id": level_id, "stage_id": stage_id, "baselined": recorded}


# ---------------------------------------------------------------------------
# Grid import (terrain painting / resize) — the dense-mask half of hand-edits.
#
# The user paints collision TILE TYPES; terrain (autotile / water-deep slot
# indices), background bands, and the hazards layer are re-DERIVED exactly the
# way canon's own phases derive them (mirrors examples/platformer_pack/
# layers.py::assign_level_terrain + paint_level_background). A round-trip on an
# unedited grid is byte-identical — tests enforce that.
# ---------------------------------------------------------------------------


def _load_tileset(pack: Path, stage_id: str) -> dict:
    return _read(pack / "tileset" / stage_id / "manifest.json")


def _derive_terrain(collision: Any, tileset: dict) -> Any:
    """Mirror of canon's assign_level_terrain, from the tileset manifest."""
    import numpy as np

    type_to_slot: dict[int, int] = {}
    variants: dict[int, dict[int, int]] = {}
    deep_slots: dict[int, int] = {}
    for slot in tileset.get("slots", []):
        params = slot.get("params") or {}
        if params.get("water_deep"):
            deep_slots[int(slot["tile_type"])] = slot["index"]
            continue
        mask_param = params.get("autotile_mask")
        if mask_param is None:
            type_to_slot[int(slot["tile_type"])] = slot["index"]
            continue
        variants.setdefault(int(slot["tile_type"]), {})[int(mask_param)] = slot["index"]
        if int(mask_param) == 0:
            type_to_slot[int(slot["tile_type"])] = slot["index"]

    known = set(type_to_slot) | set(deep_slots)
    painted = {int(v) for v in np.unique(collision)}
    unknown = painted - known
    if unknown:
        raise ValueError(f"unknown tile type(s) {sorted(unknown)} — not in the tileset registry")

    terrain = np.vectorize(type_to_slot.get, otypes=[np.int8])(collision)

    if variants:
        solid_ids = sorted(
            {int(s["tile_type"]) for s in tileset["slots"] if s.get("collision") == "solid"}
        )
        padded = np.pad(np.isin(collision, solid_ids), 1, constant_values=True)
        mask = (
            (~padded[:-2, 1:-1]).astype(np.int8)
            + (~padded[1:-1, 2:]).astype(np.int8) * 2
            + (~padded[2:, 1:-1]).astype(np.int8) * 4
            + (~padded[1:-1, :-2]).astype(np.int8) * 8
        )
        for tile_type in sorted(variants):
            lut = np.zeros(16, dtype=np.int8)
            for m, index in sorted(variants[tile_type].items()):
                lut[m] = index
            cells = collision == tile_type
            terrain[cells] = lut[mask[cells]]

    if deep_slots:
        empty_ids = sorted(
            {int(s["tile_type"]) for s in tileset["slots"] if s.get("collision") == "empty"}
        )
        air_above = np.pad(
            np.isin(collision, empty_ids), ((1, 0), (0, 0)), constant_values=False
        )[:-1, :]
        for tile_type, deep_index in sorted(deep_slots.items()):
            cells = (collision == tile_type) & ~air_above
            terrain[cells] = deep_index
    return terrain


def _derive_background(height: int, width: int) -> Any:
    """Mirror of canon's paint_level_background (3 horizon bands)."""
    import numpy as np

    bands = 3
    rows = (np.arange(height, dtype=np.int8) * bands) // max(height, 1)
    return np.repeat(rows[:, None], width, axis=1)


def _derive_hazards(
    old_collision: Any, new_collision: Any, tileset: dict, old_entries: list[dict]
) -> list[dict]:
    """Sync the sparse hazards layer with the painted collision grid.

    hazards.json is stamp SEMANTICS, not a pure projection of collision — the
    stamp records e.g. ``pit_spike`` on WALL cells (lethal pit floors) and
    names we cannot reconstruct (floor_spike vs pit_spike). Rule:

    - an entry whose cell the user did NOT repaint is preserved verbatim
      (unedited grids round-trip byte-identically);
    - an entry whose cell changed drops (re-added below if still a hazard);
    - newly painted hazard-band cells append with the registry tile name.
    """
    import numpy as np

    hazard_ids = {
        int(s["tile_type"]): s.get("name", "hazard")
        for s in tileset.get("slots", [])
        if s.get("collision") == "hazard"
    }
    height, width = new_collision.shape
    old_h, old_w = old_collision.shape

    def unchanged(x: int, y: int) -> bool:
        return (
            x < old_w and y < old_h and int(old_collision[y, x]) == int(new_collision[y, x])
        )

    kept: list[dict] = []
    seen: set[tuple[int, int]] = set()
    for entry in old_entries:
        x, y = int(entry.get("x", -1)), int(entry.get("y", -1))
        if 0 <= x < width and 0 <= y < height and unchanged(x, y):
            kept.append(entry)
            seen.add((x, y))
    live = {
        (int(x), int(y)): int(new_collision[y, x])
        for y, x in zip(*np.where(np.isin(new_collision, sorted(hazard_ids))))
    }
    for (x, y), tile_type in sorted(live.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        if (x, y) not in seen:
            kept.append({"x": x, "y": y, "type": hazard_ids[tile_type], "params": {}})
    return kept


def _pack_adapter(pack: Path) -> JsonOutputAdapter:
    """Godot-engine packs need the .grid.json siblings kept in sync."""
    if (pack / "project.godot").is_file():
        from canon.adapters.godot_adapter import GodotOutputAdapter

        return GodotOutputAdapter(pack)
    return JsonOutputAdapter(pack)


def _clamp_positions(level: dict, width: int, height: int) -> int:
    """Clamp placements/markers into the (possibly shrunk) grid. Returns count."""
    clamped = 0

    def _cl(v: int, hi: int) -> int:
        return max(0, min(hi - 1, int(v)))

    for key in ("spawn", "exit"):
        pt = level.get(key)
        if pt:
            nx, ny = _cl(pt[0], width), _cl(pt[1], height)
            if [nx, ny] != list(pt):
                clamped += 1
            level[key] = [nx, ny]
    for lst_key in ("entities", "items"):
        for p in level.get(lst_key, []) or []:
            pos = p.get("pos", [0, 0])
            nx, ny = _cl(pos[0], width), _cl(pos[1], height)
            if [nx, ny] != list(pos):
                clamped += 1
            p["pos"] = [nx, ny]
    for lst_key in ("triggers", "foreground"):
        for e in level.get(lst_key, []) or []:
            nx, ny = _cl(e.get("x", 0), width), _cl(e.get("y", 0), height)
            if nx != e.get("x") or ny != e.get("y"):
                clamped += 1
            e["x"], e["y"] = nx, ny
    return clamped


def import_level_grids(
    pack_dir: str | Path,
    level_id: str,
    collision_rows: list[list[int]],
    *,
    actor: str = "user",
    session: str | None = None,
) -> dict:
    """Apply a painted/resized collision grid: write collision, re-derive
    terrain/background/hazards, sync sparse layers, journal the edit."""
    import numpy as np

    pack = Path(pack_dir)
    level_dir, stage_id = _find_level_dir(pack, level_id)
    level = _read(level_dir / "level.json")
    tileset = _load_tileset(pack, stage_id)
    adapter = _pack_adapter(pack)
    rel = f"level/{stage_id}/{level_id}"

    collision = np.asarray(collision_rows, dtype=np.int8)
    if collision.ndim != 2 or collision.shape[0] < 4 or collision.shape[1] < 4:
        raise ValueError(f"collision grid must be 2-D and at least 4x4, got {collision.shape}")
    height, width = collision.shape
    old_dims = [level.get("grid_width"), level.get("grid_height")]

    before_hash = provenance.snapshot_file(pack, level_dir / "collision.npz")
    with np.load(level_dir / "collision.npz") as data:
        old_collision = data["collision"]
    changed_cells = (
        int(np.count_nonzero(old_collision != collision))
        if old_collision.shape == collision.shape
        else -1  # resized: cell diff not meaningful
    )
    if changed_cells == 0 and old_dims == [width, height]:
        return {"level_id": level_id, "stage_id": stage_id, "updated": [], "no_op": True}

    terrain = _derive_terrain(collision, tileset)
    background = _derive_background(height, width)
    hazard_entries = _derive_hazards(
        old_collision, collision, tileset, level.get("hazards", []) or []
    )

    level["collision_hash"] = adapter.write_numpy(f"{rel}/collision.npz", collision=collision)
    level["terrain_hash"] = adapter.write_numpy(f"{rel}/terrain.npz", terrain=terrain)
    level["background_hash"] = adapter.write_numpy(f"{rel}/background.npz", background=background)
    level["hazards"] = hazard_entries
    level["hazards_hash"] = adapter.write_json_array(f"{rel}/hazards.json", hazard_entries)
    level["grid_width"], level["grid_height"] = width, height
    clamped = _clamp_positions(level, width, height)
    if clamped:
        # Keep the flat sparse files in sync with the clamped inline copies.
        level["entities_hash"] = adapter.write_json_array(
            f"{rel}/entities.json",
            [
                {
                    "enemy_id": p["ref"].split(":", 1)[1],
                    "x": p["pos"][0],
                    "y": p["pos"][1],
                    "variant": (p.get("overrides") or {}).get("variant"),
                }
                for p in level.get("entities", []) or []
            ],
        )
        level["items_hash"] = adapter.write_json_array(
            f"{rel}/items.json",
            [
                {
                    "item_id": p["ref"].split(":", 1)[1],
                    "x": p["pos"][0],
                    "y": p["pos"][1],
                    "source": (p.get("overrides") or {}).get("source"),
                }
                for p in level.get("items", []) or []
            ],
        )
        level["triggers_hash"] = adapter.write_json_array(
            f"{rel}/triggers.json", level.get("triggers", []) or []
        )
        level["foreground_hash"] = adapter.write_json_array(
            f"{rel}/foreground.json", level.get("foreground", []) or []
        )

    level["status"] = "user_edited"
    adapter.write_json_singleton(f"{rel}/level.json", level)

    after_hash = provenance.snapshot_file(pack, level_dir / "collision.npz")
    provenance.record(
        pack,
        artifact_id=_artifact_id(stage_id, level_id, "collision"),
        op="edit",
        source="user",
        actor=actor,
        session=session,
        detail={
            "kind": "terrain_paint",
            "changed_cells": changed_cells,
            "dims_from": old_dims,
            "dims_to": [width, height],
            "clamped_placements": clamped,
        },
        before_hash=before_hash,
        after_hash=after_hash,
    )
    return {
        "level_id": level_id,
        "stage_id": stage_id,
        "updated": ["collision", "terrain", "background", "hazards"],
        "dims": [width, height],
        "changed_cells": changed_cells,
        "clamped_placements": clamped,
        "status": "user_edited",
    }


# Steps a user can revert (JSON placement/sparse layers; grids restore later).
_RESTORABLE = ("entities", "items", "triggers", "hazards", "foreground")


def restore_level_step(
    pack_dir: str | Path,
    level_id: str,
    step: str,
    to_hash: str,
    *,
    actor: str = "user",
    session: str | None = None,
) -> dict:
    """Revert one level step to a stored version (the original or any edit).

    Reads the chosen version's bytes from the content-addressed store and writes
    them back through the normal edit path, journalling ``op="restore"``. The
    version being left is not lost — every version stays in the object store.
    """
    if step not in _RESTORABLE:
        raise ValueError(f"step {step!r} is not restorable; one of {_RESTORABLE}")
    pack = Path(pack_dir)
    content = json.loads(provenance.read_object(pack, to_hash))
    result = apply_level_edit(
        pack, level_id, {step: content}, actor=actor, session=session, op="restore", source="user"
    )
    result["restored_step"] = step
    result["restored_to"] = to_hash
    return result


# ---------------------------------------------------------------------------
# Level lifecycle: create (draft) + publish/unpublish (world progression)
# ---------------------------------------------------------------------------


def _next_level_id(pack: Path) -> str:
    """Next mainline id lN across every stage dir + the manifest."""
    import re

    used = set()
    level_root = pack / "level"
    if level_root.is_dir():
        for stage_dir in level_root.iterdir():
            if stage_dir.is_dir():
                for lvl in stage_dir.iterdir():
                    m = re.fullmatch(r"l(\d+)", lvl.name)
                    if m:
                        used.add(int(m.group(1)))
    manifest = _read(pack / "manifest.json")
    for lid in manifest.get("levels", []):
        m = re.fullmatch(r"l(\d+)", str(lid))
        if m:
            used.add(int(m.group(1)))
    return f"l{max(used, default=0) + 1}"


def create_level(
    pack_dir: str | Path,
    stage_id: str,
    width: int = 60,
    height: int = 16,
    level_id: str | None = None,
    *,
    actor: str = "user",
    session: str | None = None,
) -> dict:
    """Scaffold a hand-built DRAFT level: flat floor, spawn left / exit right.

    The level exists on disk (and in cradle, which disk-discovers levels) but
    is NOT in the manifest/world-map until ``publish_level`` inserts it —
    half-built maps never leak into the playable progression.
    """
    import numpy as np

    pack = Path(pack_dir)
    if not (pack / "stage" / stage_id / "stage.json").is_file():
        raise FileNotFoundError(f"stage {stage_id!r} not found in {pack}")
    width, height = int(width), int(height)
    if width < 8 or height < 8:
        raise ValueError("level must be at least 8x8 cells")
    lid = level_id or _next_level_id(pack)
    level_dir = pack / "level" / stage_id / lid
    if level_dir.exists():
        raise ValueError(f"level {lid!r} already exists")

    tileset = _load_tileset(pack, stage_id)
    names = {s.get("name"): int(s["tile_type"]) for s in tileset.get("slots", [])}
    floor_t = names.get("floor", 1)
    wall_t = names.get("wall", 3)

    collision = np.zeros((height, width), dtype=np.int8)
    collision[height - 2, :] = floor_t
    collision[height - 1, :] = wall_t
    terrain = _derive_terrain(collision, tileset)
    background = _derive_background(height, width)

    adapter = _pack_adapter(pack)
    rel = f"level/{stage_id}/{lid}"
    level: dict[str, Any] = {
        "artifact_id": f"level:{stage_id}/{lid}/level",
        "status": "user_edited",
        "provenance_hash": "",
        "parents": [f"stage:{stage_id}", f"tileset:{stage_id}"],
        "review_status": "draft",
        "level_id": lid,
        "stage_id": stage_id,
        "grid_width": width,
        "grid_height": height,
        "pixels_per_cell": None,
        "view_cells": None,
        "layout_axis": "horizontal",
        "view_rows": None,
        "brief": "Hand-built in cradle.",
        "layout_fallback": False,
        "spawn": [2, height - 3],
        "exit": [width - 1, height - 3],
        "collision": f"{rel}/collision.npz",
        "collision_hash": adapter.write_numpy(f"{rel}/collision.npz", collision=collision),
        "terrain": f"{rel}/terrain.npz",
        "terrain_hash": adapter.write_numpy(f"{rel}/terrain.npz", terrain=terrain),
        "background": f"{rel}/background.npz",
        "background_hash": adapter.write_numpy(f"{rel}/background.npz", background=background),
        "hazards": [],
        "triggers": [],
        "foreground": [],
        "entities": [],
        "items": [],
        "secret_rooms": [],
        "parent_level": None,
        "rules_overrides": {},
        "movement_overrides": {},
        "step_parents": {},
    }
    for layer in ("hazards", "triggers", "foreground", "entities", "items"):
        level[f"{layer}_hash"] = adapter.write_json_array(f"{rel}/{layer}.json", [])
    adapter.write_json_singleton(f"{rel}/level.json", level)

    # Journal creation (op=create, user-authored from blank) for every step
    # artifact; baseline later no-ops because (artifact, hash) is recorded.
    for step, fname in _STEP_FILES.items():
        after_hash = provenance.snapshot_file(pack, level_dir / fname)
        if after_hash is None:
            continue
        provenance.record(
            pack,
            artifact_id=_artifact_id(stage_id, lid, step),
            op="create",
            source="user",
            actor=actor,
            session=session,
            after_hash=after_hash,
        )
    return {"level_id": lid, "stage_id": stage_id, "dims": [width, height], "draft": True}


def _rebuild_world_map(manifest: dict) -> None:
    """Recompute world_map nodes/edges + display names from the stage lists.

    Mirrors canon's layout scheme (per-stage runs of x with a stage gap,
    alternating y); exact jitter isn't reproduced — positions only drive the
    map render.
    """
    stages = manifest.get("stages", [])
    nodes = []
    x = 0.05
    for s_idx, stage in enumerate(stages):
        for l_idx, lid in enumerate(stage.get("levels", [])):
            nodes.append(
                {
                    "level_id": lid,
                    "display_name": f"{s_idx + 1}-{l_idx + 1}",
                    "stage_id": stage.get("stage_id"),
                    "pos": [round(min(x, 0.98), 4), 0.38 if len(nodes) % 2 == 0 else 0.64],
                }
            )
            x += 0.1
        x += 0.05  # stage gap
    order = [n["level_id"] for n in nodes]
    manifest["levels"] = order
    manifest["world_map"] = {
        "nodes": nodes,
        "edges": [[a, b] for a, b in zip(order, order[1:])],
    }


def publish_level(
    pack_dir: str | Path,
    level_id: str,
    position: int | None = None,
    remove: bool = False,
    *,
    actor: str = "user",
    session: str | None = None,
) -> dict:
    """Insert a level into (or remove it from) the playable progression.

    ``position`` is 1-based within the level's stage — publishing a new map at
    position 2 makes it "X-2" and renumbers the rest. Draft = simply absent.
    """
    pack = Path(pack_dir)
    _, stage_id = _find_level_dir(pack, level_id)
    stage_path = pack / "stage" / stage_id / "stage.json"
    stage = _read(stage_path)
    manifest = _read(pack / "manifest.json")
    adapter = _pack_adapter(pack)

    before_stage = provenance.snapshot_file(pack, stage_path)
    ids = [x for x in stage.get("level_ids", []) if x != level_id]
    if not remove:
        idx = len(ids) if position is None else max(0, min(len(ids), int(position) - 1))
        ids.insert(idx, level_id)
    stage["level_ids"] = ids
    adapter.write_json_singleton(f"stage/{stage_id}/stage.json", stage)

    for entry in manifest.get("stages", []):
        if entry.get("stage_id") == stage_id:
            entry["levels"] = ids
    _rebuild_world_map(manifest)
    adapter.write_json_singleton("manifest.json", manifest)

    after_stage = provenance.snapshot_file(pack, stage_path)
    provenance.record(
        pack,
        artifact_id=f"stage:{stage_id}",
        op="edit",
        source="user",
        actor=actor,
        session=session,
        detail={
            "kind": "unpublish" if remove else "publish",
            "level": level_id,
            "position": position,
        },
        before_hash=before_stage,
        after_hash=after_stage,
    )
    return {
        "level_id": level_id,
        "stage_id": stage_id,
        "published": not remove,
        "stage_levels": ids,
        "world_levels": manifest["levels"],
    }


# ---------------------------------------------------------------------------
# Asset replacement (Step 4): user art entering the pack.
#
# Replaces the BYTES behind a thing — an enemy/item/player sprite, one tile's
# art across its tilesheet slots (physics untouched: types-vs-skin), or a
# backdrop band — rehashes every reference, protects the artifact from regen
# (bible pin when a bible exists; user_edited status otherwise), and journals
# ``op:"import"`` with before/after snapshots. This is a distinct training
# signal: "generated art rejected in favor of external art".
# ---------------------------------------------------------------------------

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _maybe_pin(pack: Path, artifact_id: str) -> bool:
    """Pin the artifact in bible.json when one exists (regen protection)."""
    bible_path = pack / "bible.json"
    if not bible_path.is_file():
        return False
    try:
        bible = json.loads(bible_path.read_text(encoding="utf-8"))
        metadata = bible.setdefault("metadata", {})
        pinned = metadata.setdefault("pinned", [])
        if artifact_id not in pinned:
            pinned.append(artifact_id)
            bible_path.write_text(json.dumps(bible, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def replace_asset(
    pack_dir: str | Path,
    target: str,
    src_path: str | Path,
    *,
    actor: str = "user",
    session: str | None = None,
    extra_detail: dict | None = None,
) -> dict:
    """Replace an asset's bytes with an uploaded PNG.

    Targets:
      enemy:<id> | item:<id>       — the sprite (base.png), hash on the entity
      player                       — sprite/player/base.png
      tile:<stage>/<tile_name>     — that tile's art pasted into EVERY slot of
                                     the type (autotile variants flatten to the
                                     uploaded art until regenerated)
      backdrop:<stage>/<band_idx>  — one parallax band
    """
    pack = Path(pack_dir)
    data = Path(src_path).read_bytes()
    if not data.startswith(_PNG_MAGIC):
        raise ValueError("only PNG uploads are supported")
    adapter = _pack_adapter(pack)
    kind, _, rest = target.partition(":")

    if kind in ("enemy", "item") and rest:
        entity_path = pack / kind / f"{rest}.json"
        if not entity_path.is_file():
            raise FileNotFoundError(f"{kind} {rest!r} not found")
        entity = _read(entity_path)
        rel = entity.get("sprite_path") or f"sprite/{kind}/{rest}/base.png"
        before = provenance.snapshot_file(pack, pack / rel)
        sprite_hash = adapter.write_binary(rel, data)
        entity["sprite_path"] = rel
        entity["sprite_hash"] = sprite_hash
        entity["status"] = "user_edited"
        adapter.write_json_singleton(f"{kind}/{rest}.json", entity)
        artifact_id = f"{kind}:{rest}"
        detail: dict = {"kind": "sprite_replace", "path": rel}
        after = provenance.snapshot_file(pack, pack / rel)

    elif kind == "player":
        rel = "sprite/player/base.png"
        before = provenance.snapshot_file(pack, pack / rel)
        adapter.write_binary(rel, data)
        artifact_id = "player"
        detail = {"kind": "sprite_replace", "path": rel}
        after = provenance.snapshot_file(pack, pack / rel)

    elif kind == "tile" and "/" in rest:
        import io

        from PIL import Image

        stage_id, _, tile_name = rest.partition("/")
        ts_path = pack / "tileset" / stage_id / "manifest.json"
        if not ts_path.is_file():
            raise FileNotFoundError(f"tileset for stage {stage_id!r} not found")
        tileset = _read(ts_path)
        slots = [s for s in tileset.get("slots", []) if s.get("name") == tile_name]
        if not slots:
            raise ValueError(f"tile {tile_name!r} not in the {stage_id} tileset")
        sheet_rel = tileset["tilesheet_path"]
        before = provenance.snapshot_file(pack, pack / sheet_rel)
        sheet = Image.open(pack / sheet_rel).convert("RGBA")
        patch = Image.open(io.BytesIO(data)).convert("RGBA")
        for slot in slots:
            x, y, w, h = slot["px_region"]
            sheet.paste(patch.resize((w, h)), (x, y))
        buffer = io.BytesIO()
        sheet.save(buffer, "PNG")
        tileset["tilesheet_hash"] = adapter.write_binary(sheet_rel, buffer.getvalue())
        adapter.write_json_singleton(f"tileset/{stage_id}/manifest.json", tileset)
        artifact_id = f"tileset:{stage_id}"
        detail = {"kind": "tile_reskin", "tile": tile_name, "slots": len(slots)}
        after = provenance.snapshot_file(pack, pack / sheet_rel)

    elif kind == "backdrop" and "/" in rest:
        stage_id, _, idx_s = rest.partition("/")
        bd_path = pack / "backdrop" / stage_id / "manifest.json"
        if not bd_path.is_file():
            raise FileNotFoundError(f"backdrop for stage {stage_id!r} not found")
        backdrop = _read(bd_path)
        bands = backdrop.get("band_paths", [])
        idx = int(idx_s)
        if not (0 <= idx < len(bands)):
            raise ValueError(f"band index {idx} out of range (0..{len(bands) - 1})")
        rel = bands[idx]
        before = provenance.snapshot_file(pack, pack / rel)
        band_hash = adapter.write_binary(rel, data)
        backdrop.setdefault("band_hashes", {})[rel] = band_hash
        adapter.write_json_singleton(f"backdrop/{stage_id}/manifest.json", backdrop)
        artifact_id = f"backdrop:{stage_id}"
        detail = {"kind": "band_replace", "band": idx, "path": rel}
        after = provenance.snapshot_file(pack, pack / rel)

    else:
        raise ValueError(
            f"unknown target {target!r} — use enemy:<id>, item:<id>, player, "
            "tile:<stage>/<name>, or backdrop:<stage>/<index>"
        )

    pinned = _maybe_pin(pack, artifact_id)
    if extra_detail:
        # Callers wrapping this install (library import) fold their
        # provenance into the ONE event instead of journaling a second.
        detail = {**detail, **extra_detail}
    provenance.record(
        pack,
        artifact_id=artifact_id,
        op="import",
        source="import",
        actor=actor,
        session=session,
        detail=detail,
        before_hash=before,
        after_hash=after,
    )
    return {"target": target, "artifact_id": artifact_id, "pinned": pinned, **detail}


def restore_asset(
    pack_dir: str | Path,
    target: str,
    to_hash: str,
    *,
    actor: str = "user",
    session: str | None = None,
) -> dict:
    """Make a HISTORIC version current again (Library A: lineage restore).

    Nothing is deleted: newer versions keep their bytes and events; this
    writes the chosen version's bytes back into place and journals
    ``op:"restore"``, so the lineage grows a new branch from an old node.

    Routing is by target family + the bytes themselves:
      ``enemy:<id>`` / ``item:<id>`` — JSON bytes → the DB row; PNG bytes →
      the sprite (hash + refs updated, like ``asset replace``)
      ``player``                     — sprite/player/base.png
      ``tilesheet:<stage>``          — the WHOLE tilesheet
      ``backdrop:<stage>/<index>``   — one parallax band
    """
    pack = Path(pack_dir)
    data = provenance.read_object(pack, to_hash)
    adapter = _pack_adapter(pack)
    kind, _, rest = target.partition(":")
    is_png = data.startswith(_PNG_MAGIC)

    # Restore only rewinds an artifact's OWN lineage — without this, any PNG
    # in the store lands on any PNG target (fail-open). Moving bytes BETWEEN
    # artifacts is a deliberate future op (library import/assign), not this.
    expected_artifact = {
        "enemy": f"enemy:{rest}",
        "item": f"item:{rest}",
        "player": "player",
        "tilesheet": f"tileset:{rest}",
        "backdrop": f"backdrop:{rest.partition('/')[0]}",
    }.get(kind)
    if expected_artifact is not None and not any(
        e.get("artifact_id") == expected_artifact
        and to_hash in (e.get("before_hash"), e.get("after_hash"))
        for e in provenance.all_events(pack)
    ):
        raise ValueError(
            f"{to_hash} is not part of {expected_artifact}'s history — "
            "restore only rewinds an artifact's own lineage"
        )

    if kind in ("enemy", "item") and rest and not is_png:
        entity_path = pack / kind / f"{rest}.json"
        if not entity_path.is_file():
            raise FileNotFoundError(f"{kind} {rest!r} not found")
        try:
            row = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError(
                f"version {to_hash} is neither PNG nor JSON — wrong hash?"
            ) from None
        id_field = f"{kind}_id"
        if str(row.get(id_field, "")) != rest:
            raise ValueError(
                f"version {to_hash} belongs to {kind} "
                f"{row.get(id_field)!r}, not {rest!r}"
            )
        before = provenance.snapshot_file(pack, entity_path)
        row["status"] = "user_edited"
        adapter.write_json_singleton(f"{kind}/{rest}.json", row)
        after = provenance.snapshot_file(pack, entity_path)
        artifact_id = f"{kind}:{rest}"
        detail: dict = {"kind": "row_restore", "to": to_hash}

    elif kind in ("enemy", "item", "player") and is_png:
        if kind == "player":
            rel = "sprite/player/base.png"
            artifact_id = "player"
        else:
            entity_path = pack / kind / f"{rest}.json"
            if not entity_path.is_file():
                raise FileNotFoundError(f"{kind} {rest!r} not found")
            entity = _read(entity_path)
            rel = entity.get("sprite_path") or f"sprite/{kind}/{rest}/base.png"
            artifact_id = f"{kind}:{rest}"
        before = provenance.snapshot_file(pack, pack / rel)
        sprite_hash = adapter.write_binary(rel, data)
        if kind != "player":
            entity["sprite_path"] = rel
            entity["sprite_hash"] = sprite_hash
            entity["status"] = "user_edited"
            adapter.write_json_singleton(f"{kind}/{rest}.json", entity)
        after = provenance.snapshot_file(pack, pack / rel)
        detail = {"kind": "sprite_restore", "path": rel, "to": to_hash}

    elif kind == "tilesheet" and rest and is_png:
        ts_path = pack / "tileset" / rest / "manifest.json"
        if not ts_path.is_file():
            raise FileNotFoundError(f"tileset for stage {rest!r} not found")
        tileset = _read(ts_path)
        sheet_rel = tileset["tilesheet_path"]
        before = provenance.snapshot_file(pack, pack / sheet_rel)
        tileset["tilesheet_hash"] = adapter.write_binary(sheet_rel, data)
        tileset["status"] = "user_edited"
        adapter.write_json_singleton(f"tileset/{rest}/manifest.json", tileset)
        after = provenance.snapshot_file(pack, pack / sheet_rel)
        artifact_id = f"tileset:{rest}"
        detail = {"kind": "tilesheet_restore", "to": to_hash}

    elif kind == "backdrop" and "/" in rest and is_png:
        stage_id, _, idx_s = rest.partition("/")
        bd_path = pack / "backdrop" / stage_id / "manifest.json"
        if not bd_path.is_file():
            raise FileNotFoundError(f"backdrop for stage {stage_id!r} not found")
        backdrop = _read(bd_path)
        bands = backdrop.get("band_paths", [])
        idx = int(idx_s)
        if not (0 <= idx < len(bands)):
            raise ValueError(f"band index {idx} out of range (0..{len(bands) - 1})")
        rel = bands[idx]
        before = provenance.snapshot_file(pack, pack / rel)
        band_hash = adapter.write_binary(rel, data)
        backdrop.setdefault("band_hashes", {})[rel] = band_hash
        adapter.write_json_singleton(f"backdrop/{stage_id}/manifest.json", backdrop)
        after = provenance.snapshot_file(pack, pack / rel)
        artifact_id = f"backdrop:{stage_id}"
        detail = {"kind": "band_restore", "band": idx, "path": rel, "to": to_hash}

    else:
        raise ValueError(
            f"cannot restore {target!r} from these bytes — targets: "
            "enemy:<id> | item:<id> (row JSON or sprite PNG), player, "
            "tilesheet:<stage>, backdrop:<stage>/<index>"
        )

    pinned = _maybe_pin(pack, artifact_id)
    provenance.record(
        pack,
        artifact_id=artifact_id,
        op="restore",
        source="user",
        actor=actor,
        session=session,
        detail=detail,
        before_hash=before,
        after_hash=after,
    )
    return {"target": target, "artifact_id": artifact_id, "pinned": pinned, **detail}


def assign_asset(
    pack_dir: str | Path,
    source: str,
    to: str,
    *,
    actor: str = "user",
    session: str | None = None,
) -> dict:
    """The in-project "use this asset here" gesture (design §5a): copy one
    actor's WHOLE art bundle (base sprite + animation strips/atlas/frames)
    onto another row. Bytes are copied (rows stay independently editable),
    and because the copies hash identically, the lineage tree shows the two
    artifacts sharing nodes — the cross-asset connection the library exists
    to surface. Journals ``op:"import"``, ``detail.kind:"asset_assign"``.

    ``source`` / ``to``: ``enemy:<id>`` | ``item:<id>`` (distinct).
    """
    pack = Path(pack_dir)
    s_kind, _, s_id = source.partition(":")
    t_kind, _, t_id = to.partition(":")
    if s_kind not in ("enemy", "item") or t_kind not in ("enemy", "item"):
        raise ValueError("assign works between enemy:<id> / item:<id> rows")
    if (s_kind, s_id) == (t_kind, t_id):
        raise ValueError("source and destination are the same row")
    s_path = pack / s_kind / f"{s_id}.json"
    t_path = pack / t_kind / f"{t_id}.json"
    if not s_path.is_file():
        raise FileNotFoundError(f"{s_kind} {s_id!r} not found")
    if not t_path.is_file():
        raise FileNotFoundError(f"{t_kind} {t_id!r} not found")

    src_row = _read(s_path)
    sprite_rel = str(src_row.get("sprite_path") or "")
    src_base = pack / sprite_rel
    if not sprite_rel or not src_base.is_file():
        raise FileNotFoundError(f"{source} has no sprite to assign")
    src_dir = src_base.parent
    old_prefix = str(Path(sprite_rel).parent)
    new_prefix = f"sprite/{t_kind}/{t_id}"

    adapter = _pack_adapter(pack)
    dest_row = _read(t_path)
    # The event's hashes are the SPRITE bytes (facet sprite) — the after
    # hash equals the source's sprite hash, which is exactly what makes the
    # lineage tree show both artifacts sharing one node.
    old_sprite_rel = str(dest_row.get("sprite_path") or "")
    before = (
        provenance.snapshot_file(pack, pack / old_sprite_rel)
        if old_sprite_rel else None
    )
    # Clear stale art first: assigning a STATIC sprite must not leave the
    # dest's old animation playing from leftover manifests/strips.
    dest_dir = pack / new_prefix
    src_names = {
        f.name for f in src_dir.iterdir()
        if f.is_file() and not f.name.startswith(".") and not f.name.endswith(".tmp")
    }
    if dest_dir.is_dir():
        for f in dest_dir.iterdir():
            if f.is_file() and f.name not in src_names:
                f.unlink()
    sprite_hash = ""
    for f in sorted(src_dir.iterdir()):
        if (
            not f.is_file() or f.name.startswith(".") or f.name.endswith(".tmp")
        ):
            continue
        data = f.read_bytes()
        if f.name.endswith(".json"):
            # Playback manifests embed pack-relative paths — the play
            # surfaces read THESE, so a verbatim copy would render the dest
            # from the source's live files.
            data = (
                data.decode("utf-8").replace(old_prefix, new_prefix).encode("utf-8")
            )
        written = adapter.write_binary(f"{new_prefix}/{f.name}", data)
        if f.name == src_base.name:
            sprite_hash = written
    dest_row["sprite_path"] = f"{new_prefix}/{src_base.name}"
    dest_row["sprite_hash"] = sprite_hash
    dest_row["status"] = "user_edited"
    src_anim = (src_row.get("stats") or {}).get("animation")
    if isinstance(src_anim, dict):
        replaced = json.loads(
            json.dumps(src_anim).replace(old_prefix, new_prefix)
        )
        dest_row.setdefault("stats", {})["animation"] = replaced
    else:
        # Source is static — the dest's old animation block is now a lie.
        (dest_row.get("stats") or {}).pop("animation", None)
    adapter.write_json_singleton(f"{t_kind}/{t_id}.json", dest_row)
    after = provenance.snapshot_file(
        pack, pack / f"{new_prefix}/{src_base.name}"
    )

    pinned = _maybe_pin(pack, f"{t_kind}:{t_id}")
    provenance.record(
        pack,
        artifact_id=f"{t_kind}:{t_id}",
        op="import",
        source="user",
        actor=actor,
        session=session,
        detail={
            "kind": "asset_assign", "from": source, "from_hash": sprite_hash,
        },
        before_hash=before,
        after_hash=after,
    )
    return {
        "from": source, "to": to, "sprite_hash": sprite_hash, "pinned": pinned,
    }
