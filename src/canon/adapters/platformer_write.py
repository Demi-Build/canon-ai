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
from collections.abc import Callable
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


#: The ``gen`` keys that carry MONEY (row P1-A6 / P.8.3). A multi-event verb
#: stamps them on its first event only; every later event of the same op keeps
#: the descriptive half of the block so lineage still reads model + prompt.
_GEN_COST_KEYS = ("cost_usd", "cost_breakdown", "cost_accuracy")


def _uncosted_gen(gen: dict | None) -> dict | None:
    """``gen`` minus its cost keys — what the 2nd..Nth event of one op gets, so
    a single billable leg is counted once (P.8.7)."""
    if not gen:
        return None
    stripped = {k: v for k, v in gen.items() if k not in _GEN_COST_KEYS}
    return stripped or None


def apply_level_edit(
    pack_dir: str | Path,
    level_id: str,
    edit: dict,
    *,
    actor: str = "user",
    session: str | None = None,
    op: str = "edit",
    source: str = "user",
    gen: dict | None = None,
    gen_kind: str | None = None,
    accuracy: str | None = None,
    cost_error: str | None = None,
) -> dict:
    """Apply a sparse-layer edit, persist it, and journal the mutation.

    ``op``/``source`` default to a user edit; ``restore_level_step`` reuses this
    with ``op="restore"`` so reverting to an old version flows through the same
    write + journal path.

    Row P1-A6: ``gen`` / ``gen_kind`` / ``accuracy`` / ``cost_error`` thread a
    COSTED write through this same path (``generate_level_music`` repoints the
    level through here, so its dollars belong on the event it already writes).
    One op is ONE billable leg, so the cost rides the FIRST event emitted and
    the remaining step events carry the gen block without its cost keys — the
    dashboard therefore counts each op exactly once (P.8.7's one-number rule).
    """
    pack = Path(pack_dir)
    level_dir, stage_id = _find_level_dir(pack, level_id)
    level = _read(level_dir / "level.json")
    adapter = JsonOutputAdapter(pack)
    rel = f"level/{stage_id}/{level_id}"
    updated: list[str] = []
    events: list[dict] = []
    uncosted = _uncosted_gen(gen)

    def _emit(step: str, before_hash: str | None, after_path: Path, detail: Any) -> None:
        after_hash = provenance.snapshot_file(pack, after_path)
        if before_hash == after_hash:
            # No-op write (e.g. a placement grabbed and released in place) —
            # don't journal noise; it would pollute the training corpus.
            return
        first = not events
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
                gen=(gen if first else uncosted) or None,
                gen_kind=gen_kind,
                accuracy=accuracy,
                cost_error=cost_error if first else None,
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
    gen: dict | None = None,
    gen_kind: str | None = None,
    accuracy: str | None = None,
    cost_error: str | None = None,
) -> dict:
    """Record step-artifact events for a level's as-generated files.

    Called when cradle imports a fresh generation (``op="generate"``, the
    default) or after a context-aware improve (``op="regenerate"``). Idempotent
    — a step already in the journal at its current hash is skipped, so
    re-opening a level is safe. ``detail`` (e.g. ``{"kind": "improve"}``) rides
    each recorded event so consumers can tell WHICH generation op produced the
    change (generate / improve / regenerate / place_enemies / place_items).

    Row P1-A6: the level ops (generate / regenerate / improve / place_*) pass
    their ``gen`` block (with ``cost_usd``), ``gen_kind`` (``text`` — LLM-authored
    data, P.9 J4) and ``accuracy`` here. One op is ONE billable leg: the cost
    lands on the FIRST event this call records and every later step event keeps
    the block minus its cost keys, so a five-step level generate is one costed
    row in the dashboard, not five. A costed op whose steps were ALL already
    journalled (an identical re-roll) still records its money — a hash-less
    event on ``level:<stage>/<id>/level`` carrying the op's detail — because
    money spent must never vanish from the authoritative source (P.8.2).
    """
    pack = Path(pack_dir)
    level_dir, stage_id = _find_level_dir(pack, level_id)
    recorded: list[str] = []
    uncosted = _uncosted_gen(gen)
    # Only a NON-ZERO leg (or a loud pricing failure) is worth a hash-less
    # row: a $0 fake run that changed nothing lost nothing.
    costed = bool(cost_error) or float((gen or {}).get("cost_usd") or 0.0) > 0.0
    for step, fname in _STEP_FILES.items():
        path = level_dir / fname
        after_hash = provenance.snapshot_file(pack, path)
        if after_hash is None:
            continue
        aid = _artifact_id(stage_id, level_id, step)
        if provenance.already_recorded(pack, aid, after_hash):
            continue
        first = not recorded
        provenance.record(
            pack,
            artifact_id=aid,
            op=op,
            source="llm",
            actor=actor,
            session=session,
            detail=detail,
            after_hash=after_hash,
            gen=(gen if first else uncosted) or None,
            gen_kind=gen_kind,
            accuracy=accuracy,
            cost_error=cost_error if first else None,
        )
        recorded.append(step)
    if costed and not recorded:
        # Nothing changed on disk, but the provider still billed. A hash-less
        # event is invisible to artifact_versions / lineage / restore by
        # construction (P.8.5), so this reports the spend without inventing a
        # version nobody can restore to.
        provenance.record(
            pack,
            artifact_id=_artifact_id(stage_id, level_id, "level"),
            op=op,
            source="llm",
            actor=actor,
            session=session,
            detail={**(detail or {}), "no_change": True},
            gen=gen,
            gen_kind=gen_kind,
            accuracy=accuracy,
            cost_error=cost_error,
        )
    return {"level_id": level_id, "stage_id": stage_id, "baselined": recorded}


# ---------------------------------------------------------------------------
# Grid import (terrain painting / resize) — the dense-mask half of hand-edits.
#
# The user paints collision TILE TYPES; terrain (autotile / water-deep slot
# indices), background bands, and the hazards layer are re-DERIVED exactly the
# way canon's own phases derive them (mirrors src/canon/packs/platformer/
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
    """Godot-engine packs need the .grid.json siblings kept in sync — the
    one rule now lives in ``canon.write_core.pack_adapter`` (row P0-6);
    this name stays for every caller that imports it."""
    from canon.write_core import pack_adapter

    return pack_adapter(pack)


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


#: Reserved id for the game-feel sandbox room. A FIXED id is what makes
#: `ensure_sandbox_level` idempotent — opening the sandbox twice reuses the same
#: draft instead of scaffolding (and journaling) a fresh level every launch.
SANDBOX_LEVEL_ID = "sandbox"


def ensure_sandbox_level(
    pack_dir: str | Path,
    stage_id: str | None = None,
    *,
    width: int = 40,
    height: int = 16,
    actor: str = "user",
    session: str | None = None,
) -> dict:
    """Create-or-reuse the flat room the movement sandbox plays in.

    The sandbox needs somewhere obstacle-free to judge how the player FEELS,
    and ``create_level`` already scaffolds exactly that — flat floor, spawn
    left, exit right, and crucially a DRAFT, so it stays out of the manifest
    and world map and never leaks into the playable progression. This is
    therefore id selection + an existence check on top of it, not a second
    scaffolder.

    ``stage_id`` defaults to the pack's first stage (the sandbox only needs a
    tileset to draw with; which biome it borrows doesn't matter).
    """
    pack = Path(pack_dir)
    if stage_id is None:
        manifest = json.loads((pack / "manifest.json").read_text())
        stages = manifest.get("stages") or []
        if not stages:
            raise ValueError(f"no stages in {pack}/manifest.json")
        stage_id = str(stages[0].get("stage_id"))
    existing = pack / "level" / stage_id / SANDBOX_LEVEL_ID
    if existing.is_dir():
        return {
            "level_id": SANDBOX_LEVEL_ID,
            "stage_id": stage_id,
            "created": False,
            "draft": True,
        }
    out = create_level(
        pack, stage_id, width, height, SANDBOX_LEVEL_ID,
        actor=actor, session=session,
    )
    out["created"] = True
    return out


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


#: Bare document artifacts the registry-era verbs journal (P.7.3) → file.
#: ``world`` resolves per pack type inside ``_restore_document``.
_DOCUMENT_TARGETS: dict[str, str] = {
    "registry": ".canon/registry.json",
    "manifest": "manifest.json",
    "story": "story/story.json",
    "narrative": "narrative.json",
}


def _restore_document(
    pack: Path,
    target: str,
    kind: str,
    rest: str,
    data: bytes,
    to_hash: str,
    *,
    actor: str,
    session: str | None,
) -> dict | None:
    """Row P0-6: the registry-era restore families, resolved through the
    pack registry — ``<kind>:<id>`` of a COLLECTION kind (the CAS unit is
    the file: every row in it comes back; History labels it "restores
    <file> (N rows)", P.4.1), and the bare document artifacts ``registry`` /
    ``world`` / ``manifest`` / ``story`` / ``narrative`` (P.7.3). ``None``
    when the target is one of the platformer families below (per-file rows,
    sprites, sheets, bands), which keep their own branches."""
    from canon.packs import PackTypeError, resolve_pack

    try:
        spec = resolve_pack(pack).spec
    except PackTypeError:
        return None
    rel: str | None = None
    artifact_id = target
    lineage: Callable[[str], bool]
    entity = spec.entities.get(kind) if rest else None
    if entity is not None and (entity.layout or {}).get("mode") == "collection":
        rel = str(entity.layout.get("path"))
        artifact_id = f"{kind}:{rest}"

        def lineage(aid: str) -> bool:
            return aid.startswith(f"{kind}:")
    elif not rest and (kind in _DOCUMENT_TARGETS or kind == "world"):
        if kind == "world":
            rel = "world.json" if (pack / "world.json").is_file() else "world_bible.json"
        else:
            rel = _DOCUMENT_TARGETS[kind]

        def lineage(aid: str) -> bool:
            return aid == kind
    if rel is None:
        return None
    if not any(
        lineage(str(e.get("artifact_id", ""))) and to_hash in (e.get("before_hash"), e.get("after_hash"))
        for e in provenance.all_events(pack)
    ):
        raise ValueError(
            f"{to_hash} is not part of {artifact_id}'s history — restore only rewinds an artifact's own lineage"
        )
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError(f"version {to_hash} is not JSON — wrong hash?") from None
    path = pack / rel
    before = provenance.snapshot_file(pack, path)
    _pack_adapter(pack).write_json_singleton(rel, document)
    after = provenance.snapshot_file(pack, path)
    if entity is not None:
        rows = len(document) if isinstance(document, (list, dict)) else 0
        detail: dict = {"kind": "row_restore", "to": to_hash, "file": rel, "rows": rows,
                        "label": f"restores {rel} ({rows} rows)"}
    else:
        detail = {"kind": "document_restore", "to": to_hash, "file": rel}
    provenance.record(
        pack, artifact_id=artifact_id, op="restore", source="user", actor=actor, session=session,
        detail=detail, before_hash=before, after_hash=after,
    )
    return {"target": target, "artifact_id": artifact_id, "pinned": False, **detail}


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

    generic = _restore_document(pack, target, kind, rest, data, to_hash, actor=actor, session=session)
    if generic is not None:
        return generic

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


# ---------------------------------------------------------------------------
# World map authoring
# ---------------------------------------------------------------------------

_LOOP_MODES = {"loop", "once", "ping_pong"}
#: Playback fields a human may correct by hand. Deliberately NOT the geometry
#: (x/y/w/h/ox/oy) — that is generation's output; these are corrections layered
#: on top of it, which is why re-animating clears them.
_FRAME_EDITABLE = ("offsets", "durations_ms", "loop")


def apply_frames_edit(
    pack_dir: str | Path,
    target: str,
    state: str,
    edit: dict,
    *,
    actor: str = "user",
    session: str | None = None,
) -> dict:
    """Hand-correct one animation state's PLAYBACK: per-frame offsets, per-frame
    durations, loop mode.

    The point is fixing a badly-seated or badly-timed animation without paying
    to regenerate it. Offsets are `[[dx, dy], ...]` in FRAME pixel space and
    must match the frame count — the same contract ``durations_ms`` already
    follows, so a desynced list is refused here rather than silently shifting
    the wrong frames at play time.

    Written to BOTH ``atlas.json`` and ``frames.json``: the two loaders read
    different manifests (atlas wins where present, strips are the fallback),
    so writing one would leave the other rendering the un-corrected version.

    Mirrors ``db update``: validate → write → rehash → journal. A no-op edit
    does not journal.
    """
    from canon.adapters.platformer_read import _sprite_dir_for

    pack = Path(pack_dir)
    _label, sprite_dir = _sprite_dir_for(pack, target)
    frames_path = pack / sprite_dir / "frames.json"
    atlas_path = pack / sprite_dir / "atlas.json"
    if not frames_path.is_file():
        raise FileNotFoundError(f"{target} has no animation ({frames_path} missing)")

    frames_meta = json.loads(frames_path.read_text())
    entry = frames_meta.get(state)
    if not isinstance(entry, dict):
        raise ValueError(
            f"{target} has no animation state {state!r} "
            f"(has {', '.join(sorted(frames_meta)) or 'none'})"
        )
    count = int(entry.get("frames", 0) or 0)

    unknown = [k for k in edit if k not in _FRAME_EDITABLE]
    if unknown:
        raise ValueError(
            f"cannot edit {', '.join(sorted(unknown))} — "
            f"frames-edit changes playback ({', '.join(_FRAME_EDITABLE)}), "
            f"not the generated frame geometry"
        )

    patch: dict[str, Any] = {}
    if "offsets" in edit:
        raw = edit["offsets"]
        if raw is None:
            patch["offsets"] = None  # clear back to generation's seating
        else:
            if not isinstance(raw, list) or len(raw) != count:
                raise ValueError(
                    f"offsets needs one [dx, dy] per frame ({count} for {state!r}), "
                    f"got {len(raw) if isinstance(raw, list) else type(raw).__name__}"
                )
            pairs = []
            for pair in raw:
                if (
                    not isinstance(pair, (list, tuple))
                    or len(pair) != 2
                    or not all(isinstance(v, (int, float)) for v in pair)
                ):
                    raise ValueError(f"offsets entries must be [dx, dy]; got {pair!r}")
                pairs.append([int(pair[0]), int(pair[1])])
            patch["offsets"] = pairs
    if "durations_ms" in edit:
        raw = edit["durations_ms"]
        if not isinstance(raw, list) or len(raw) != count:
            raise ValueError(
                f"durations_ms needs one value per frame ({count} for {state!r})"
            )
        if not all(isinstance(v, (int, float)) and v > 0 for v in raw):
            raise ValueError("durations_ms entries must be positive numbers")
        patch["durations_ms"] = [int(v) for v in raw]
    if "loop" in edit:
        if edit["loop"] not in _LOOP_MODES:
            raise ValueError(
                f"loop must be one of {', '.join(sorted(_LOOP_MODES))}; got {edit['loop']!r}"
            )
        patch["loop"] = edit["loop"]

    def _apply(entry: dict) -> bool:
        changed = False
        for key, value in patch.items():
            if value is None:
                if entry.pop(key, None) is not None:
                    changed = True
                continue
            if entry.get(key) != value:
                entry[key] = value
                changed = True
        return changed

    before = provenance.snapshot_file(pack, frames_path)
    touched = _apply(entry)

    atlas_meta = None
    if atlas_path.is_file():
        atlas_meta = json.loads(atlas_path.read_text())
        astate = (atlas_meta.get("states") or {}).get(state)
        if isinstance(astate, dict) and _apply(astate):
            touched = True

    if not touched:
        return {"frames_edit": "no_change", "target": target, "state": state}

    frames_path.write_text(json.dumps(frames_meta, indent=2))
    if atlas_meta is not None:
        atlas_path.write_text(json.dumps(atlas_meta, indent=2))
    after = provenance.snapshot_file(pack, frames_path)

    provenance.record(
        pack,
        # The BARE target, matching what `asset animate` journals — animation is
        # a facet of the actor, not a separate artifact. A different id here
        # would fork the lineage tree the History tab reads.
        artifact_id=target,
        op="edit",
        source="user",
        actor=actor,
        session=session,
        detail={"kind": "frames_edit", "state": state, "fields": sorted(patch)},
        before_hash=before,
        after_hash=after,
    )
    return {
        "frames_edit": "updated",
        "target": target,
        "state": state,
        "fields": sorted(patch),
    }


_EDGE_KINDS = {"path", "one", "lock", "new"}


def apply_world_map_edit(
    pack_dir: str | Path,
    edit: dict,
    *,
    actor: str = "user",
    session: str | None = None,
) -> dict:
    """Persist hand-authoring of the world map onto ``world.json``.

    The map itself is RECOMPUTED from the seed on every resume, so the durable
    record is a set of overrides on the World bible — ``map_nodes`` (placed
    positions), ``map_edges`` (typed connections) and ``map_locked``. Writing
    them here and letting ``compose._world_map`` layer them on is what stops
    the next run from silently reverting a human's layout.

    Accepts any subset of::

        {"nodes": {"l1": {"pos": [0.2, 0.4]}, "l2": null},
         "edges": [{"a": "l1", "b": "l2", "kind": "lock", "condition": "..."}],
         "locked": true}

    A ``null`` node value REMOVES the override, handing that node back to the
    generator. Mirrors ``db update``: validate → write → journal.
    """
    pack = Path(pack_dir)
    wj = pack / "world.json"
    if not wj.is_file():
        raise FileNotFoundError(f"no world.json in {pack}")
    world = json.loads(wj.read_text())
    before = provenance.snapshot_file(pack, wj)

    changed: list[str] = []

    if "nodes" in edit:
        nodes = dict(world.get("map_nodes") or {})
        for level_id, value in (edit["nodes"] or {}).items():
            if value is None:
                if nodes.pop(level_id, None) is not None:
                    changed.append(f"unplaced {level_id}")
                continue
            pos = (value or {}).get("pos")
            if (
                not isinstance(pos, (list, tuple))
                or len(pos) != 2
                or not all(isinstance(v, (int, float)) for v in pos)
            ):
                raise ValueError(f"node {level_id!r} needs pos [x, y] in 0..1")
            # Clamp rather than reject: a drag that overshoots the canvas edge
            # is a normal gesture, not an error.
            clamped = [round(min(1.0, max(0.0, float(v))), 4) for v in pos]
            if nodes.get(level_id, {}).get("pos") != clamped:
                changed.append(f"placed {level_id}")
            nodes[level_id] = {"pos": clamped}
        world["map_nodes"] = nodes

    if "edges" in edit:
        specs: list[dict] = []
        for e in edit["edges"] or []:
            a, b = e.get("a"), e.get("b")
            if not a or not b:
                raise ValueError("every edge needs both 'a' and 'b'")
            kind = e.get("kind") or "path"
            if kind not in _EDGE_KINDS:
                raise ValueError(
                    f"edge kind {kind!r} not one of {sorted(_EDGE_KINDS)}"
                )
            spec: dict[str, Any] = {"a": a, "b": b, "kind": kind}
            if e.get("condition"):
                spec["condition"] = str(e["condition"])
            if e.get("stop"):
                spec["stop"] = str(e["stop"])
            specs.append(spec)
        if specs != (world.get("map_edges") or []):
            changed.append(f"{len(specs)} edge(s)")
        world["map_edges"] = specs

    if "locked" in edit:
        locked = bool(edit["locked"])
        if locked != bool(world.get("map_locked")):
            changed.append("locked" if locked else "unlocked")
        world["map_locked"] = locked

    if not changed:
        # No-op edits must not pollute the journal (the same hygiene rule
        # apply_level_edit follows for grab-and-release-in-place).
        return {"world_map": "no_change", "changed": []}

    wj.write_text(json.dumps(world, indent=2))
    after = provenance.snapshot_file(pack, wj)
    provenance.record(
        pack,
        artifact_id="world",
        op="edit",
        source="user",
        actor=actor,
        session=session,
        detail={"kind": "world_map_edit", "changes": changed},
        before_hash=before,
        after_hash=after,
    )
    return {"world_map": "updated", "changed": changed}


def _level_facts(lv: dict) -> dict[str, Any]:
    """The per-level detail the map shows without opening the editor: how big
    the level is, how much is placed in it, and which sub-rooms hang off it."""
    facts: dict[str, Any] = {}
    width, height = lv.get("grid_width"), lv.get("grid_height")
    if isinstance(width, int) and isinstance(height, int) and width and height:
        facts["size"] = f"{width}×{height}"
    facts["entities"] = len(lv.get("entities") or [])
    facts["items"] = len(lv.get("items") or [])
    rooms = [r for r in (lv.get("secret_rooms") or []) if isinstance(r, str)]
    if rooms:
        facts["rooms"] = rooms
    return facts


def _level_overrides(lv: dict, stage: dict) -> list[str]:
    """Which of its area's defaults a level actually departs from.

    REPORTED, not configured. A level inherits its area's theme, blocks and
    music bed today — there is no per-level field for any of them — so the only
    real divergences are an enemy placed outside the area's roster and the
    per-level physics overrides ``Level`` does carry.
    """
    out: list[str] = []
    pool = {str(ref) for ref in (stage.get("enemy_refs") or [])}
    if pool:
        placed = {
            str(e.get("ref"))
            for e in (lv.get("entities") or [])
            if isinstance(e, dict) and str(e.get("ref", "")).startswith("enemy:")
        }
        if placed - pool:
            out.append("enemies")
    if lv.get("rules_overrides") or lv.get("movement_overrides"):
        out.append("physics")
    return out


def read_world_map(pack_dir: str | Path) -> dict:
    """The render-ready world map: nodes with positions + display names, typed
    edges, and the AREAS (stages) they cluster under.

    Areas are stages — they already carry the theme/biome/level membership the
    design's area inspector wants, so this exposes what exists rather than
    inventing a parallel grouping.
    """
    pack = Path(pack_dir)
    manifest = json.loads((pack / "manifest.json").read_text())
    wmap = manifest.get("world_map") or {}
    world = {}
    if (pack / "world.json").is_file():
        world = json.loads((pack / "world.json").read_text())

    published = {n.get("level_id") for n in wmap.get("nodes") or []}
    placed = dict(world.get("map_nodes") or {})

    # ONE pass over the level files on disk. Draft discovery and the per-node
    # detail below both need them, and a pack can hold a lot of levels.
    on_disk: dict[str, dict[str, Any]] = {}
    for level_json in sorted(pack.glob("level/*/*/level.json")):
        try:
            lv = json.loads(level_json.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        lid = lv.get("level_id") or level_json.parent.name
        on_disk[lid] = {"stage_id": level_json.parent.parent.name, "data": lv}

    # Stage records carry the area defaults (enemy roster, tileset, boss) that
    # the manifest's stage summary leaves out.
    stage_recs: dict[str, dict[str, Any]] = {}
    for stage_json in sorted(pack.glob("stage/*/stage.json")):
        try:
            rec = json.loads(stage_json.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        stage_recs[rec.get("stage_id") or stage_json.parent.name] = rec

    def _detail(level_id: str) -> dict[str, Any]:
        found = on_disk.get(level_id)
        if not found:
            return {}
        lv = found["data"]
        facts = _level_facts(lv)
        overrides = _level_overrides(lv, stage_recs.get(found["stage_id"]) or {})
        if overrides:
            facts["overrides"] = overrides
        return facts

    # DRAFT levels — created by `level create` but not yet published into a
    # stage's level list, so `compose._world_map` (which walks stage.level_ids)
    # can't see them. They are the design's `planned` nodes: on the map, not
    # yet part of the progression.
    #
    # SECRET ROOMS are deliberately excluded: a room is a sub-room INSIDE a
    # level, not a stop on the world map, so it has no node here.
    drafts: list[dict[str, Any]] = []
    for lid, found in on_disk.items():
        lv = found["data"]
        if lid in published or lv.get("parent_level"):
            continue
        pos = (placed.get(lid) or {}).get("pos") or [0.5, 0.5]
        drafts.append(
            {
                "level_id": lid,
                "display_name": None,
                "stage_id": found["stage_id"],
                "pos": [round(float(pos[0]), 4), round(float(pos[1]), 4)],
                "status": "planned",
                **({"origin": "manual"} if lid in placed else {}),
                **_detail(lid),
            }
        )
    drafts.sort(key=lambda n: n["level_id"])

    # Layer the DURABLE overrides on the manifest here too, exactly as
    # `compose._world_map` does at compose time. Without this the editor writes
    # world.json and reads back the pre-edit manifest — the map would appear to
    # ignore every edit until the next full pipeline run.
    nodes: list[dict[str, Any]] = []
    for n in wmap.get("nodes") or []:
        node = dict(n)
        placed_pos = (placed.get(node.get("level_id")) or {}).get("pos")
        if isinstance(placed_pos, (list, tuple)) and len(placed_pos) == 2:
            node["pos"] = [round(float(placed_pos[0]), 4), round(float(placed_pos[1]), 4)]
            node["origin"] = "manual"
        node.update(_detail(str(node.get("level_id"))))
        nodes.append(node)

    authored = world.get("map_edges") or []
    specs = authored or wmap.get("edge_specs")
    if not specs:
        # Untyped derived chain — surface it in the same shape so the consumer
        # has exactly one edge model to draw.
        specs = [
            {"a": a, "b": b, "kind": "path"} for a, b in (wmap.get("edges") or [])
        ]

    validation = {}
    for lid in [n.get("level_id") for n in wmap.get("nodes") or []]:
        rel = None
        for stage in manifest.get("stages") or []:
            if lid in (stage.get("levels") or []):
                rel = stage.get("stage_id")
        if rel:
            validation[lid] = rel

    areas = []
    for i, stage in enumerate(manifest.get("stages") or []):
        sid = stage.get("stage_id")
        rec = stage_recs.get(sid) or {}
        # `blocks` is the area's tile set and `enemy_pool` its roster — the
        # defaults every level inside inherits. Both live on the stage record;
        # the manifest's stage summary carries only theme/biome/membership.
        areas.append(
            {
                "stage_id": sid,
                "index": i,
                "theme": stage.get("theme", ""),
                "biome": stage.get("biome", ""),
                "level_ids": list(stage.get("levels") or []),
                "music": (manifest.get("audio") or {}).get(sid, {}).get("music"),
                "blocks": str(rec.get("tileset_ref") or "").split(":", 1)[-1],
                "enemy_pool": [
                    str(ref).split(":", 1)[-1] for ref in (rec.get("enemy_refs") or [])
                ],
                "boss": str(rec.get("boss_ref") or "").split(":", 1)[-1],
            }
        )

    return {
        "world": manifest.get("world", ""),
        "nodes": [*nodes, *drafts],
        "edges": specs,
        "areas": areas,
        "locked": bool(world.get("map_locked")),
        "manual_count": len(world.get("map_nodes") or {}),
    }
