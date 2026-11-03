"""Read-side helpers for the platformer output tree.

The adapters in this package are otherwise write-only (they emit the pack that
Godot / pygame consume). This module is the symmetric *read* half that external
tooling — notably Cradle — shells out to instead of re-implementing numpy `.npz`
decoding and the tileset slot registry.

``export_level_bundle`` assembles one self-contained, JSON-serializable dict with
everything needed to *render* a level: the three dense grids decoded to nested
int lists, the tileset slots + palette, sparse layers, and enemy placements
resolved against their global ``enemy/<id>.json`` definitions. Asset references
(tilesheet, sprites, backdrop bands) are resolved to absolute paths so a viewer
can load the bytes directly.

``describe_level`` (row A3) is the token-frugal sibling: a compact summary
(dims, tile histogram, platform bands, placements, overrides, the validation
verdict) the agent reads first; ``export_level_bundle(..., window=)`` slices
the full bundle to a region when it needs the cells themselves.

Nothing here mutates the pack; it is a pure projection of on-disk state.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

# The on-disk files whose bytes together define a level's current STATE. A
# change to any of them is a change to the level (terrain, placements, spawn/
# exit/music in level.json, the sparse layers). Ordered so the composite
# revision hash is stable.
_LEVEL_STATE_FILES = (
    "collision.npz", "terrain.npz", "background.npz",
    "hazards.json", "triggers.json", "foreground.json",
    "entities.json", "items.json", "level.json",
)

# How a level last changed → a human label, keyed by the journal event's
# detail.kind (falls back to the op). Covers every mutation path: generation
# ops (kinds stamped by baseline_level), hand edits (apply_level_edit /
# import_level_grids), and library/restore ops.
_CHANGE_LABELS = {
    "generate": "Generated",
    "improve": "Improved",
    "regenerate": "Regenerated layout",
    "place_enemies": "Placed enemies",
    "place_items": "Placed items",
    "terrain_paint": "Hand-painted terrain",
    "enemy_move": "Moved an enemy",
    "item_move": "Moved an item",
    "level_edit": "Saved edit",
    "hazards_change": "Edited hazards",
    "triggers_change": "Edited triggers",
    "foreground_change": "Edited foreground",
    # The room GridKind's journal kinds (P0 paper P.3.2 `placements[].journal_kind`),
    # read here so a room's revision chip labels them once P0-6/P0-8 write them.
    "npc_move": "Moved an NPC",
    "event_move": "Moved an event",
}


def level_revision(level_dir: str | Path) -> dict:
    """A content identifier for a level's CURRENT state — a composite SHA over
    its on-disk state files. Two byte-identical levels share it; any generation,
    improvement, or hand edit that changes bytes changes it (a no-op that writes
    identical bytes does not). Pure read — no CAS side effects (unlike
    ``provenance.snapshot_file``).

    Returns ``{"revision": "sha256:<hex>", "short": "<10 hex>"}``.
    """
    d = Path(level_dir)
    h = hashlib.sha256()
    for name in _LEVEL_STATE_FILES:
        p = d / name
        if not p.is_file():
            continue
        h.update(name.encode("utf-8"))
        h.update(hashlib.sha256(p.read_bytes()).hexdigest().encode("ascii"))
        h.update(b"\n")
    digest = h.hexdigest()
    return {"revision": f"sha256:{digest}", "short": digest[:10]}


def level_last_change(pack_dir: str | Path, stage_id: str, level_id: str) -> dict | None:
    """The most recent journaled change to this level, for the "how it last
    changed" chip: ``{op, source, kind, actor, ts, hash, label}`` (or None if the
    level has no journal history). The level's artifact-id family is
    ``level:<stage>/<level>/<step>``; the scan itself is ``journal_last_change``,
    shared with the room reader (row P0-5) whose family is ``room:<id>/<step>``."""
    return journal_last_change(pack_dir, f"level:{stage_id}/{level_id}/")


def journal_last_change(pack_dir: str | Path, artifact_prefix: str) -> dict | None:
    """The newest journal event whose ``artifact_id`` starts with
    *artifact_prefix* — one grid's artifact family (P0 paper P.9 R1:
    ``GridKind.artifact_id`` with the step left open) — labelled for the
    revision chip, or ``None`` when nothing in the family was ever journaled.
    Reads the provenance journal — the single source of truth across
    generation, improvement, hand-paint and save. The journal is
    append-ordered, so the last event touching the family is the last write
    (for a multi-step action like improve+reroll that's its final sub-step)."""
    from canon import provenance

    prefix = artifact_prefix
    best_event: dict | None = None
    for e in provenance.all_events(pack_dir):
        if str(e.get("artifact_id", "")).startswith(prefix):
            best_event = e  # keep the LAST match (append order = latest write)
    if best_event is None:
        return None
    op = str(best_event.get("op", ""))
    kind = str((best_event.get("detail") or {}).get("kind", "") or "")
    label = _CHANGE_LABELS.get(kind) or _CHANGE_LABELS.get(op) or (
        {"create": "Created", "restore": "Restored", "edit": "Saved edit"}.get(op)
        or (op.capitalize() if op else "Changed")
    )
    return {
        "op": op,
        "source": str(best_event.get("source", "")),
        "kind": kind,
        "actor": str(best_event.get("actor", "")),
        "ts": str(best_event.get("ts", "")),
        "hash": str(best_event.get("after_hash", "") or ""),
        "label": label,
    }


def load_grid(path: str | Path) -> list[list[int]]:
    """Decode a canon ``.npz`` dense mask into a row-major nested int list.

    The array is stored under a key equal to the leading filename token
    (``collision.npz`` -> key ``"collision"``); we read whichever single array
    the archive holds so callers need not know the key.
    """
    import numpy  # lazy: numpy is not a core canon dependency

    archive = numpy.load(path)
    keys = list(archive.keys())
    if len(keys) != 1:  # pragma: no cover - packs always write one array
        raise ValueError(f"expected exactly one array in {path}, found {keys}")
    return archive[keys[0]].astype(int).tolist()


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _stage_for_level(manifest: dict, level_id: str) -> str | None:
    for stage in manifest.get("stages", []):
        if level_id in stage.get("levels", []):
            return stage.get("stage_id")
    return None


def _display_name(manifest: dict, level_id: str) -> str | None:
    for node in manifest.get("world_map", {}).get("nodes", []):
        if node.get("level_id") == level_id:
            return node.get("display_name")
    return None


def _abs(pack_dir: Path, rel: str | None) -> str | None:
    if not rel:
        return None
    return str((pack_dir / rel).resolve())


#: Where an animation target's sprite directory lives, by target grammar.
def _sprite_dir_for(pack: Path, target: str) -> tuple[str, str]:
    """``(label, sprite dir relative to the pack)`` for an animation target.

    Grammar matches ``platformer_play._anim_preview_targets`` so the inspector
    and the viewers name the same things: ``enemy:<id>`` | ``item:<id>`` |
    ``player``.
    """
    t = (target or "").strip()
    if t == "player":
        return "player", "sprite/player"
    if ":" in t:
        kind, ident = t.split(":", 1)
        if kind in ("enemy", "item") and ident:
            return ident, f"sprite/{kind}/{ident}"
    raise ValueError(
        f"unknown animation target {target!r} — expected player, "
        f"enemy:<id> or item:<id>"
    )


def _pack_vlm_qa():
    """The platformer pack's ``vlm_qa`` module, or ``None``.

    The state vocabulary and the per-state motion briefs are PACK data (they
    describe this game's animation contract); since row P0-4 the pack ships
    inside the package, so this is a plain deferred import. The ``None``
    contract stays for the caller's sake: a pack whose optional deps are
    missing still reads animations fine, it just can't offer the briefs.
    """
    try:
        from canon.packs.platformer import vlm_qa
    except ImportError:  # pragma: no cover — env-specific
        return None
    return vlm_qa


def _animation_plan(pack: Path, target: str) -> tuple[list[str], dict]:
    """``(states this actor would animate, brief per state)``.

    The player has its own richer ladder (jump/fall/land/skid); an enemy takes
    the base vocabulary plus ``jump`` when it's a hopper — the same lookup
    generation itself uses, so the dialog promises exactly what a run delivers.
    """
    vq = _pack_vlm_qa()
    if vq is None:
        return [], {}
    if target == "player":
        planned = list(vq.PLAYER_ANIMATION_STATES)
    else:
        planned = list(vq.ANIMATION_STATES)
        kind, _, ident = target.partition(":")
        if kind == "enemy" and ident:
            rp = pack / "enemy" / f"{ident}.json"
            row = (_read_json(rp) if rp.is_file() else {}) or {}
            if str(row.get("archetype") or "") == "hopper":
                planned = list(vq.enemy_animation_states(SimpleNamespace(archetype="hopper")))
    briefs = {s: vq._STATE_BRIEF[s] for s in planned if s in vq._STATE_BRIEF}
    return planned, briefs


def _stored_motion_spec(pack: Path, target: str) -> dict:
    """The motion spec a previous animate run stored on THIS actor, if any.

    Actor-specific and therefore truer than the generic brief — it describes
    how this particular drawing moves. Enemies and items carry it on their row
    (`stats.animation.spec`); the player's lives in the Bible, which the
    sequential runner's packs don't ship, so it simply reads empty there.
    """
    kind, _, ident = (target or "").partition(":")
    if kind in ("enemy", "item") and ident:
        rp = pack / kind / f"{ident}.json"
        row = (_read_json(rp) if rp.is_file() else {}) or {}
        spec = ((row.get("stats") or {}).get("animation") or {}).get("spec")
        return spec if isinstance(spec, dict) else {}
    if target == "player":
        # The sequential runner's packs ship no bible.json — read empty.
        bp = pack / "bible.json"
        bible = (_read_json(bp) if bp.is_file() else {}) or {}
        player = bible.get("player") or {}
        spec = (player.get("animation") or {}).get("spec")
        return spec if isinstance(spec, dict) else {}
    return {}


def _frame_boxes(strip_path: Path, count: int, fw: int, fh: int) -> list[dict]:
    """The opaque content box of every frame in a strip, in FRAME pixel space.

    Measured from the shipped pixels rather than read back from the atlas
    rects: the rects are what generation *intended*, and the whole point of
    this inspector is catching art that disagrees with the intent. Frames with
    no opaque pixel at all report a null box instead of a zero-size one.
    """
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover — Pillow ships with the art extra
        return []
    try:
        sheet = Image.open(strip_path).convert("RGBA")
    except (OSError, ValueError):
        return []
    boxes: list[dict] = []
    for i in range(count):
        crop = sheet.crop((i * fw, 0, (i + 1) * fw, fh))
        bbox = crop.getbbox()
        if bbox is None:
            boxes.append({"index": i, "box": None})
            continue
        x0, y0, x1, y1 = bbox
        boxes.append(
            {
                "index": i,
                "box": {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0},
                # How far the content's feet sit above the cell's bottom edge.
                # A state whose feet wander between frames is the thing that
                # makes an actor look like it hops between poses.
                "foot_gap": fh - y1,
            }
        )
    return boxes


def read_animation(pack_dir: str | Path, target: str) -> dict:
    """Every measurable fact about one actor's animation, for the inspector.

    Pure read — no writes, no journal. Reports, per state: the shared frame
    square, playback timing, any authored offsets, and the measured content box
    of every frame. Also flags the two defects that are invisible frame by
    frame:

    ``flush``       the state's content touches the cell edge. With ONE shared
                    square across all states, only the single largest pose may
                    do this — so more than one flush state means the states
                    were sized independently, which is the bug that made the
                    player lose two thirds of its pixels mid-jump.
    ``foot_wander`` the feet move between frames of one state, which reads as
                    the actor bobbing while it should be planted.
    """
    pack = Path(pack_dir)
    label, sprite_dir = _sprite_dir_for(pack, target)
    meta = _read_json(pack / sprite_dir / "frames.json") or {}
    atlas = _read_json(pack / sprite_dir / "atlas.json") or {}

    states: list[dict] = []
    for state, entry in sorted(meta.items()):
        if not isinstance(entry, dict):
            continue
        count = int(entry.get("frames", 0) or 0)
        fw = int(entry.get("frame_width", 0) or 0)
        fh = int(entry.get("frame_height", 0) or 0)
        rel = str(entry.get("path", "") or "")
        frames = _frame_boxes(pack / rel, count, fw, fh) if (count and fw and fh) else []
        boxes = [f["box"] for f in frames if f["box"]]
        widest = max((b["w"] for b in boxes), default=0)
        tallest = max((b["h"] for b in boxes), default=0)
        foot_gaps = [f["foot_gap"] for f in frames if f["box"]]
        durations = entry.get("durations_ms")
        if not isinstance(durations, list) or len(durations) != count:
            durations = [int(entry.get("duration_ms", 120) or 120)] * count
        offsets = entry.get("offsets")
        if not isinstance(offsets, list) or len(offsets) != count:
            offsets = None
        states.append(
            {
                "state": state,
                "frames": count,
                "frame_width": fw,
                "frame_height": fh,
                "path": rel,
                "path_abs": _abs(pack, rel),
                "loop": entry.get("loop", "loop"),
                "durations_ms": durations,
                "offsets": offsets,
                "boxes": frames,
                "widest": widest,
                "tallest": tallest,
                # Flush = the content reaches the cell edge (1px tolerance for
                # the pad the writer leaves).
                "flush": bool(fw and fh and (widest >= fw - 1 or tallest >= fh - 1)),
                "foot_wander": (max(foot_gaps) - min(foot_gaps)) if foot_gaps else 0,
            }
        )

    flush = [s["state"] for s in states if s["flush"]]
    planned, briefs = _animation_plan(pack, target)
    spec = _stored_motion_spec(pack, target)
    return {
        "target": target,
        "label": label,
        "sprite_dir": sprite_dir,
        # What an animate run WORKS FROM, so the generate dialog can show its
        # real inputs instead of only backend dropdowns: the base sprite it
        # edits, the states it will author, the per-state motion brief, and any
        # spec a previous run already stored for THIS actor.
        "base_sprite": f"{sprite_dir}/base.png",
        "base_sprite_abs": _abs(pack, f"{sprite_dir}/base.png"),
        "planned_states": planned,
        "briefs": briefs,
        "spec": spec,
        "has_atlas": bool(atlas),
        "atlas_path_abs": _abs(pack, str(atlas.get("path", "") or "")) if atlas else None,
        "states": states,
        "flush_states": flush,
        # More than one state touching the edge means they were squared
        # independently — the signature `animation_scale` checks for.
        "independently_sized": len(flush) > 1,
    }


#: The bundle's per-cell placement layers — every record carries ``x``/``y``
#: in level cells, so a window filters them all the same way.
_WINDOWED_LAYERS = ("hazards", "triggers", "foreground", "entities", "items")


def normalize_window(window: Any, width: int, height: int) -> dict:
    """``(x0, y0, w, h)`` → the clamped ``{"x0", "y0", "w", "h"}`` block a
    windowed bundle carries (Phase 1 §3.4 "windowed grids"; row A3).

    The origin must sit inside the ``width`` × ``height`` grid and the size
    must be positive; a window running past the far edge is clamped to it
    (asking for 24 columns from x0 = 110 of a 123-wide level is a 13-wide
    window, not an error). Malformed shapes are a ``ValueError`` naming the
    expected form — every CLI/tool caller renders that as its structured
    error.
    """
    try:
        x0, y0, w, h = (int(v) for v in window)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"window must be four integers x0,y0,w,h (got {window!r})") from exc
    if w < 1 or h < 1:
        raise ValueError(f"window size must be positive (got w={w}, h={h})")
    if x0 < 0 or y0 < 0:
        raise ValueError(f"window origin must be non-negative (got x0={x0}, y0={y0})")
    if x0 >= width or y0 >= height:
        raise ValueError(f"window origin ({x0},{y0}) is outside the {width}x{height} grid")
    return {"x0": x0, "y0": y0, "w": min(w, width - x0), "h": min(h, height - y0)}


def window_bundle(bundle: dict, window: Any) -> dict:
    """Slice a full level bundle down to ``window`` IN PLACE and return it.

    The three dense grids become the window's rows × columns; every
    per-cell layer (``_WINDOWED_LAYERS``) keeps only the records inside it —
    with their ABSOLUTE level coordinates untouched, so a windowed bundle
    still names the same cells the full level does. ``grid_width`` /
    ``grid_height`` stay the full dims; the bundle gains ``window``
    ``{x0, y0, w, h}`` (clamped). Everything else (tileset, spawn/exit,
    revision, music) rides along unchanged.
    """
    collision = bundle["grids"]["collision"]
    height = len(collision)
    width = len(collision[0]) if height else 0
    win = normalize_window(window, width, height)
    x0, y0, w, h = win["x0"], win["y0"], win["w"], win["h"]
    bundle["grids"] = {
        name: [row[x0 : x0 + w] for row in grid[y0 : y0 + h]] for name, grid in bundle["grids"].items()
    }

    def inside(record: Any) -> bool:
        try:
            x, y = int(record.get("x")), int(record.get("y"))
        except (AttributeError, TypeError, ValueError):
            return False
        return x0 <= x < x0 + w and y0 <= y < y0 + h

    for layer in _WINDOWED_LAYERS:
        bundle[layer] = [record for record in bundle.get(layer, []) if inside(record)]
    bundle["window"] = win
    return bundle


def export_level_bundle(pack_dir: str | Path, level_id: str, window: Any = None) -> dict:
    """Assemble a render-ready bundle for one level of a platformer pack.

    ``pack_dir`` is the output root (the dir holding ``manifest.json``).
    Raises ``FileNotFoundError`` if the pack or level cannot be located.

    ``window`` (row A3, additive): ``(x0, y0, w, h)`` in level cells slices
    the grids and filters the per-cell layers to that region via
    ``window_bundle`` — the token-frugal read the agent uses instead of a
    full dump (Phase 1 §3.4). ``None`` (every pre-A3 caller, cradle's canvas
    included) is the full level, byte-for-byte the same document as before.
    """
    bundle = _export_full_level_bundle(Path(pack_dir), level_id)
    return window_bundle(bundle, window) if window is not None else bundle


def _export_full_level_bundle(pack: Path, level_id: str) -> dict:
    """The whole-level bundle ``export_level_bundle`` windows."""
    manifest_path = pack / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"not a platformer pack (no manifest.json): {pack}")
    manifest = _read_json(manifest_path)

    stage_id = _stage_for_level(manifest, level_id)
    if stage_id is None:
        # Secret rooms are deliberately absent from manifest stages/world_map
        # (they're not in stage.level_ids) — discover them on disk under their
        # parent's stage directory instead.
        level_root = pack / "level"
        if level_root.is_dir():
            for stage_dir in sorted(level_root.iterdir()):
                if (stage_dir / level_id / "level.json").is_file():
                    stage_id = stage_dir.name
                    break
    if stage_id is None:
        raise FileNotFoundError(
            f"level {level_id!r} not found in any stage of {manifest_path}"
        )

    level_dir = pack / "level" / stage_id / level_id
    level = _read_json(level_dir / "level.json")

    # Tileset — appearance (px_region) + physics (collision category) registry,
    # shared by every level in the stage.
    tileset = _read_json(pack / "tileset" / stage_id / "manifest.json")
    tileset["tilesheet_path_abs"] = _abs(pack, tileset.get("tilesheet_path"))
    # tile_type -> slot, the lookup a renderer needs to color a collision cell.
    tiles_by_type = {str(slot["tile_type"]): slot for slot in tileset.get("slots", [])}

    # Enemy placements resolved against global definitions.
    entities: list[dict] = []
    enemy_cache: dict[str, dict] = {}
    for placement in level.get("entities", []):
        ref = placement.get("ref", "")
        enemy_id = ref.split(":", 1)[1] if ":" in ref else ref
        if enemy_id not in enemy_cache:
            enemy_path = pack / "enemy" / f"{enemy_id}.json"
            enemy_cache[enemy_id] = (
                _read_json(enemy_path) if enemy_path.exists() else {}
            )
        enemy = enemy_cache[enemy_id]
        stats = enemy.get("stats", {})
        pos = placement.get("pos", [0, 0])
        entities.append(
            {
                "enemy_id": enemy_id,
                "x": pos[0],
                "y": pos[1],
                "variant": placement.get("overrides", {}).get("variant"),
                "name": enemy.get("name", enemy_id),
                "archetype": enemy.get("archetype"),
                "size": enemy.get("size", 1.0),
                "placeholder_color": stats.get("placeholder_color", "#ff00ff"),
                "sprite_path_abs": _abs(pack, enemy.get("sprite_path")),
            }
        )

    # Item placements resolved against global item definitions. The level's
    # `items` (Placement list) is the authoritative overlay; box-source items
    # also drop a solid tile that consumers overlay onto collision.
    items: list[dict] = []
    item_cache: dict[str, dict] = {}
    for placement in level.get("items", []):
        ref = placement.get("ref", "")
        item_id = ref.split(":", 1)[1] if ":" in ref else ref
        if item_id not in item_cache:
            item_path = pack / "item" / f"{item_id}.json"
            item_cache[item_id] = _read_json(item_path) if item_path.exists() else {}
        item = item_cache[item_id]
        pos = placement.get("pos", [0, 0])
        items.append(
            {
                "item_id": item_id,
                "x": pos[0],
                "y": pos[1],
                "source": placement.get("overrides", {}).get("source"),
                "name": item.get("name", item_id),
                "kind": item.get("kind"),
                "placeholder_color": item.get("stats", {}).get(
                    "placeholder_color", "#ffd700"
                ),
                "sprite_path_abs": _abs(pack, item.get("sprite_path")),
            }
        )

    # Backdrop parallax bands (optional).
    backdrop: dict | None = None
    backdrop_path = pack / "backdrop" / stage_id / "manifest.json"
    if backdrop_path.exists():
        bd = _read_json(backdrop_path)
        backdrop = {
            "depths": bd.get("depths", []),
            "band_paths_abs": [_abs(pack, p) for p in bd.get("band_paths", [])],
        }

    # Stage props (checkpoint/exit/vfx sprites) — abs-resolved for the art view.
    props = {
        name: _abs(pack, rel)
        for name, rel in (manifest.get("props", {}).get(stage_id, {}) or {}).items()
    }

    slots = tileset.get("slots", [])
    graphics = manifest.get("graphics", {})
    # Music: the level's own override + user sections, plus the stage default
    # it falls back to — all abs-resolved so cradle's <AudioPlayer> can play them.
    stage_music = ((manifest.get("audio", {}) or {}).get(stage_id, {}) or {}).get("music") or ""
    level_music = level.get("music_path") or ""
    music_sections = [
        {
            **s,
            "music_path_abs": _abs(pack, s.get("music_path")) if s.get("music_path") else None,
        }
        for s in (level.get("music_sections") or [])
    ]
    rev = level_revision(level_dir)
    return {
        "level_id": level_id,
        "stage_id": stage_id,
        "display_name": _display_name(manifest, level_id),
        # Content identity of the CURRENT state (changes on every real edit /
        # generation) + how it last changed (from the provenance journal).
        "revision": rev["revision"],
        "revision_short": rev["short"],
        "last_change": level_last_change(pack, stage_id, level_id),
        "grid_width": level.get("grid_width"),
        "grid_height": level.get("grid_height"),
        "spawn": level.get("spawn"),
        "exit": level.get("exit"),
        "layout_fallback": level.get("layout_fallback", False),
        "parent_level": level.get("parent_level"),
        "brief": level.get("brief"),
        # Render tuning the art view mirrors from canon's skinned renderer.
        "tile_px": slots[0]["px_region"][2] if slots else 32,
        "actor_scale": graphics.get("actor_scale", 1.0),
        "water_alpha": graphics.get("water_alpha", 1.0),
        # Enemy-variant vocabulary (elite/champion/…) for the editor palette.
        "variants": [v.get("name") for v in manifest.get("variants", []) if v.get("name")],
        "grids": {
            "collision": load_grid(level_dir / "collision.npz"),
            "terrain": load_grid(level_dir / "terrain.npz"),
            "background": load_grid(level_dir / "background.npz"),
        },
        "tileset": tileset,
        "tiles_by_type": tiles_by_type,
        "hazards": level.get("hazards", []),
        "triggers": level.get("triggers", []),
        "foreground": level.get("foreground", []),
        "entities": entities,
        "items": items,
        "props": props,
        "backdrop": backdrop,
        "music_path": level_music,
        "music_path_abs": _abs(pack, level_music) if level_music else None,
        "music_sections": music_sections,
        "stage_music": stage_music,
        "stage_music_abs": _abs(pack, stage_music) if stage_music else None,
    }


# ---------------------------------------------------------------------------
# describe_level — the compact, describe-first read (Phase 1 §3.4; row A3)
# ---------------------------------------------------------------------------

#: The collision categories a band reports, in the order they print. ``empty``
#: is the ground everything else sits in, so it never gets a span.
_BAND_CATEGORIES = ("solid", "one_way", "hazard", "volume")


def _tile_categories(manifest: dict) -> dict[int, str]:
    """tile id → collision category, from the manifest's tile registry — the
    game's ACTUAL registry (compose persists it); the template default only
    serves packs from before that landed. The same source ``validate_level``
    reads, so the histogram and the verdict agree on what a cell is."""
    from canon.packs.platformer.tiles import DEFAULT_TILES, TileRegistry

    tiles = (
        TileRegistry.model_validate({"tiles": manifest["tiles"]})
        if manifest.get("tiles") else DEFAULT_TILES
    )
    return {t.id: t.category for t in tiles.tiles}


def _row_spans(row: list[int], categories: dict[int, str]) -> dict[str, list[list[int]]]:
    """Run-length spans ``{category: [[x0, x1], ...]}`` (inclusive) of one
    row's non-empty cells, keyed only for categories the row has."""
    spans: dict[str, list[list[int]]] = {}
    current: str | None = None
    for x, value in enumerate(row):
        category = categories.get(int(value), "unknown")
        if category == "empty":
            current = None
            continue
        if category == current:
            spans[category][-1][1] = x
        else:
            spans.setdefault(category, []).append([x, x])
            current = category
    return spans


def platform_bands(grid: list[list[int]], categories: dict[int, str]) -> list[dict]:
    """The grid as a few lines of text-friendly data instead of a grid: one
    band per run of ADJACENT rows whose span sets are identical — a floor
    slab is one band, a staircase a band per step. Each band is
    ``{"rows": [y0, y1], "<category>": [[x0, x1], ...]}`` with inclusive
    cell ranges, categories per ``_BAND_CATEGORIES`` (plus ``unknown`` for
    ids the registry does not know). Empty rows are omitted."""
    bands: list[dict] = []
    for y, row in enumerate(grid):
        spans = _row_spans(row, categories)
        if not spans:
            continue
        ordered = {c: spans[c] for c in (*_BAND_CATEGORIES, "unknown") if c in spans}
        last = bands[-1] if bands else None
        if last is not None and last["rows"][1] == y - 1 and {k: v for k, v in last.items() if k != "rows"} == ordered:
            last["rows"][1] = y
            continue
        bands.append({"rows": [y, y], **ordered})
    return bands


def _counts(values: list[Any]) -> dict[str, int]:
    """Sorted ``value → count`` with ``None``/empty rendered as ``"unknown"``."""
    out: dict[str, int] = {}
    for value in values:
        key = str(value) if value not in (None, "") else "unknown"
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


def _validation_summary(report: dict) -> dict:
    """``validate_level``'s verdict without its prose: ``ok``, problem counts
    per check, the repair count, and each secret room's own verdict."""
    return {
        "ok": bool(report.get("ok")),
        "problems": {str(c.get("name")): len(c.get("problems") or []) for c in report.get("checks", [])},
        "repair_count": int(report.get("repair_count", 0) or 0),
        "rooms": [
            {"level_id": str(r.get("level_id", "")), "ok": bool(r.get("ok"))} for r in report.get("rooms", [])
        ],
    }


def describe_level(pack_dir: str | Path, level_id: str) -> dict:
    """A compact summary of one level — the describe-first read the agent
    calls before (and usually instead of) ``export_level_bundle`` (Phase 1
    §3.4, §4.B; row A3). A projection of the same bundle the canvas renders
    plus the level's ``validate_level`` verdict; pure read — nothing is
    written, journaled or snapshotted.

    Returns ``{level_id, stage_id, display_name, brief, dims {width, height,
    axis}, spawn, exit, rooms, parent_level, tiles {cells, by_category},
    platforms [bands], entities {count, by_archetype, placed}, items {count,
    by_kind, placed}, triggers {count, by_type}, hazards {count, by_type},
    overrides {rules, movement}, validation {ok, problems, repair_count,
    rooms}, revision, revision_short, last_change}``. ``platforms`` is
    ``platform_bands`` — run-length spans per row band, not the grid;
    positions are absolute level cells. Raises ``FileNotFoundError`` when
    the pack or level cannot be located (the export's contract).
    """
    from canon.packs.platformer.ops import validate_level

    pack = Path(pack_dir)
    bundle = _export_full_level_bundle(pack, level_id)
    stage_id = bundle["stage_id"]
    level = _read_json(pack / "level" / stage_id / level_id / "level.json")
    manifest = _read_json(pack / "manifest.json")
    categories = _tile_categories(manifest)

    grid = bundle["grids"]["collision"]
    height = len(grid)
    width = len(grid[0]) if height else 0
    histogram: dict[str, int] = {}
    for row in grid:
        for value in row:
            category = categories.get(int(value), "unknown")
            histogram[category] = histogram.get(category, 0) + 1

    entities = bundle["entities"]
    items = bundle["items"]
    return {
        "level_id": level_id,
        "stage_id": stage_id,
        "display_name": bundle["display_name"],
        "brief": bundle["brief"],
        "dims": {"width": width, "height": height, "axis": str(level.get("layout_axis") or "horizontal")},
        "spawn": bundle["spawn"],
        "exit": bundle["exit"],
        "rooms": list(level.get("secret_rooms") or []),
        "parent_level": bundle["parent_level"],
        "tiles": {"cells": width * height, "by_category": dict(sorted(histogram.items()))},
        "platforms": platform_bands(grid, categories),
        "entities": {
            "count": len(entities),
            "by_archetype": _counts([e.get("archetype") for e in entities]),
            "placed": [
                {
                    "id": e["enemy_id"], "archetype": e.get("archetype"), "x": e["x"], "y": e["y"],
                    **({"variant": e["variant"]} if e.get("variant") else {}),
                }
                for e in entities
            ],
        },
        "items": {
            "count": len(items),
            "by_kind": _counts([i.get("kind") for i in items]),
            "placed": [
                {"id": i["item_id"], "kind": i.get("kind"), "x": i["x"], "y": i["y"], "source": i.get("source")}
                for i in items
            ],
        },
        "triggers": {
            "count": len(bundle["triggers"]),
            "by_type": _counts([t.get("type") for t in bundle["triggers"]]),
        },
        "hazards": {
            "count": len(bundle["hazards"]),
            "by_type": _counts([h.get("type") for h in bundle["hazards"]]),
        },
        "overrides": {
            "rules": dict(level.get("rules_overrides") or {}),
            "movement": dict(level.get("movement_overrides") or {}),
        },
        "validation": _validation_summary(validate_level(pack, level_id)),
        "revision": bundle["revision"],
        "revision_short": bundle["revision_short"],
        "last_change": (bundle["last_change"] or {}).get("label"),
    }


# ---------------------------------------------------------------------------
# Asset lineage — the journal + CAS rendered as a family tree (Library A)
# ---------------------------------------------------------------------------

#: detail.kind → node FACET: what kind of bytes a version holds. Facets route
#: thumbnails (png vs json diff) and restore targets; unknown kinds fall back
#: by artifact-id family below.
_FACET_BY_KIND = {
    "db_new": "row", "db_update": "row", "db_complete": "row",
    "row_restore": "row",
    "sprite_replace": "sprite", "sprite_restore": "sprite",
    "asset_assign": "sprite",
    "asset_animate": "animation",
    "tile_reskin": "tilesheet", "tilesheet_restore": "tilesheet",
    "band_replace": "band", "band_restore": "band",
    "tile_params": "tileset_manifest",
    "db_schema": "schema",
}

#: Facet priority per artifact family: which facet's latest version is THE
#: current center of the tree (an animated enemy's identity is its row, not
#: its frames.json — alphabetical facet order picked the atlas).
_PRIMARY_FACETS = {
    "enemy": ("row", "sprite", "animation"),
    "item": ("row", "sprite"),
    "player": ("sprite", "animation"),
    "tileset": ("tilesheet", "tileset_manifest"),
    "backdrop": ("band", "backdrop_manifest"),
    "schema": ("schema",),
    "level": ("level_step",),
}


def _facet_for(event: dict) -> str:
    kind = str((event.get("detail") or {}).get("kind", ""))
    aid = str(event.get("artifact_id", ""))
    if kind == "asset_generate":
        # generate_asset journals ONE kind for every target family; the
        # snapshot bytes differ per family (backdrop/audio = their manifest).
        if aid.startswith("backdrop:"):
            return "backdrop_manifest"
        if aid.startswith("audio:"):
            return "audio_manifest"
        return "sprite"
    if kind in _FACET_BY_KIND:
        return _FACET_BY_KIND[kind]
    if aid.startswith("level:"):
        return "level_step"
    if aid.startswith("schema:"):
        return "schema"
    if aid.startswith(("tileset:", "backdrop:")):
        return "tileset_manifest" if aid.startswith("tileset:") else "band"
    return "data"


def _placement_usage(pack: Path) -> dict[str, list[str]]:
    """id → level ids that place it (usage badges). One pass over the tree."""
    usage: dict[str, list[str]] = {}
    level_root = pack / "level"
    if not level_root.is_dir():
        return usage
    for level_dir in sorted(level_root.glob("*/*")):
        lid = level_dir.name
        for fname, key, family in (
            ("entities.json", "enemy_id", "enemy"),
            ("items.json", "item_id", "item"),
        ):
            f = level_dir / fname
            if not f.is_file():
                continue
            try:
                records = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for rec in records if isinstance(records, list) else []:
                rid = str(rec.get(key, ""))
                # Typed keys ("enemy:x") — enemy/item/stage ids share one
                # slugify namespace and would collide on bare names.
                if rid and lid not in usage.setdefault(f"{family}:{rid}", []):
                    usage[f"{family}:{rid}"].append(lid)
    return usage


def asset_lineage(
    pack_dir: str | Path, artifact_id: str, max_nodes: int = 500
) -> dict:
    """The artifact's family tree, derived from the journal + object store.

    Content lives on NODES (one per distinct content hash — a version), and
    provenance lives on EDGES (the journal event that turned one version into
    the next: op, actor, time, model, prompt when recorded). Cross-asset
    connections come free from the CAS: the same bytes appearing in two
    artifacts' histories is ONE node wearing both badges — that's how a sheet
    repainted and reassigned to another enemy row stays visibly connected.
    Rooted at the earliest ancestor; centered (``requested_node_id``) on the
    artifact's CURRENT version, per the reference contract.
    """
    from canon import provenance

    pack = Path(pack_dir)
    events = [
        e for e in provenance.all_events(pack)
        if e.get("after_hash") or e.get("before_hash")
    ]

    # Transitive closure by shared hashes: start from the target artifact's
    # events, then pull in any event anywhere that touches a known hash.
    # Hash-indexed BFS (linear) — a rescan loop goes quadratic on long
    # cross-artifact chains. `included` then sorts by TIME so the
    # producer-metadata pass (first after_hash wins) and the `latest` pass
    # (last one wins) are journal-true regardless of discovery order.
    idx_by_hash: dict[str, list[int]] = {}
    for i, e in enumerate(events):
        for h in (e.get("before_hash"), e.get("after_hash")):
            if h:
                idx_by_hash.setdefault(h, []).append(i)
    queue: list[str] = []
    hashes: set[str] = set()
    for e in events:
        if e.get("artifact_id") == artifact_id:
            for h in (e.get("before_hash"), e.get("after_hash")):
                if h and h not in hashes:
                    hashes.add(h)
                    queue.append(h)
    included_ids: set[int] = set()
    while queue:
        h = queue.pop()
        for i in idx_by_hash.get(h, ()):
            if i in included_ids:
                continue
            included_ids.add(i)
            e = events[i]
            for hh in (e.get("before_hash"), e.get("after_hash")):
                if hh and hh not in hashes:
                    hashes.add(hh)
                    queue.append(hh)
    included = [events[i] for i in sorted(included_ids)]
    included.sort(key=lambda e: str(e.get("ts", "")))

    usage_map = _placement_usage(pack)

    # Nodes: one per content hash; the event that PRODUCED the hash (first
    # after_hash appearance) carries its op/actor/gen. A hash seen only as a
    # before_hash predates the journal (baseline bytes).
    nodes: dict[str, dict] = {}
    for e in included:
        for h in (e.get("before_hash"), e.get("after_hash")):
            if h and h not in nodes:
                nodes[h] = {
                    "id": h, "facet": "data", "op": "baseline",
                    "source": "", "actor": "", "ts": "",
                    "gen": None, "artifacts": [], "current_of": [],
                    "usage": {}, "detail": {},
                }
    for e in included:
        h = e.get("after_hash")
        aid = str(e.get("artifact_id", ""))
        for hh in (e.get("before_hash"), e.get("after_hash")):
            if hh and aid and aid not in nodes[hh]["artifacts"]:
                nodes[hh]["artifacts"].append(aid)
        if h and nodes[h]["op"] == "baseline":
            nodes[h].update(
                facet=_facet_for(e),
                op=str(e.get("op", "")),
                source=str(e.get("source", "")),
                actor=str(e.get("actor", "")),
                ts=str(e.get("ts", "")),
                gen=e.get("gen"),
                detail={
                    k: v for k, v in (e.get("detail") or {}).items()
                    if k in ("kind", "path", "band", "tile", "type")
                },
            )
    # A before-only node (bytes that predate the journal) inherits the facet
    # AND routing detail (band index, tile name) of the edit that consumed
    # it — otherwise the original band art has no restore target.
    for e in included:
        b = e.get("before_hash")
        if b and nodes[b]["op"] == "baseline":
            if nodes[b]["facet"] == "data":
                nodes[b]["facet"] = _facet_for(e)
            if not nodes[b]["detail"]:
                nodes[b]["detail"] = {
                    k: v for k, v in (e.get("detail") or {}).items()
                    if k in ("kind", "path", "band", "tile", "type")
                }

    # Current version + usage badges per artifact (latest after_hash wins —
    # facet-scoped so a row edit doesn't unseat the current sprite).
    latest: dict[tuple, str] = {}
    for e in included:
        h = e.get("after_hash")
        if h:
            latest[(str(e.get("artifact_id", "")), _facet_for(e))] = h
    for (aid, facet), h in latest.items():
        nodes[h]["current_of"].append(f"{aid}#{facet}")
    for node in nodes.values():
        for aid in node["artifacts"]:
            if aid in usage_map:
                node["usage"][aid] = usage_map[aid]

    edges: list[dict] = []
    for e in included:
        after = e.get("after_hash")
        if not after:
            continue
        detail = e.get("detail") or {}
        base = {
            "op": str(e.get("op", "")),
            "kind": str(detail.get("kind", "")),
            "actor": str(e.get("actor", "")),
            "ts": str(e.get("ts", "")),
            "gen": e.get("gen"),
        }
        # A restore's meaningful parent is the node it restored FROM
        # (detail.to) — that's where the new branch hangs. The state it
        # replaced stays as a secondary "replaced" edge.
        restored_from = (
            str(detail.get("to", ""))
            if str(detail.get("kind", "")).endswith("_restore")
            else ""
        )
        if restored_from and restored_from in nodes and restored_from != after:
            edges.append({"from": restored_from, "to": after, **base})
        before = e.get("before_hash")
        if before and before != after:
            edge = {"from": before, "to": after, **base}
            if restored_from:
                edge["kind"] = f"{base['kind']}:replaced"
            edges.append(edge)

    # Depths for the layered layout. Only STRUCTURAL edges participate — a
    # restore's secondary ":replaced" edge points backward and closes a
    # cycle (longest-path layering never terminates on cycles). The sweep
    # is bounded anyway, so even a hand-crafted cyclic journal converges
    # instead of hanging.
    structural = [e for e in edges if not e["kind"].endswith(":replaced")]
    incoming: dict[str, list[str]] = {h: [] for h in nodes}
    for edge in structural:
        incoming[edge["to"]].append(edge["from"])
    depth: dict[str, int] = {h: 0 for h in nodes}
    for _ in range(max(1, len(nodes))):
        settled = True
        for edge in structural:
            d = depth[edge["from"]] + 1
            if d > depth[edge["to"]] and d <= len(nodes):
                depth[edge["to"]] = d
                settled = False
        if settled:
            break
    for h, node in nodes.items():
        node["depth"] = depth[h]

    # Requested = the artifact's current PRIMARY-facet version (an animated
    # enemy centers on its row, not its frames.json).
    priority = _PRIMARY_FACETS.get(artifact_id.split(":", 1)[0], ())
    requested = next(
        (
            latest[(artifact_id, facet)]
            for facet in priority
            if (artifact_id, facet) in latest
        ),
        None,
    )
    if requested is None:
        candidates = sorted(
            (facet, h) for (aid, facet), h in latest.items() if aid == artifact_id
        )
        requested = candidates[0][1] if candidates else next(iter(nodes), None)
    # Root: walk up from the requested node; earliest-ts parent wins forks.
    root = requested
    seen_up: set[str] = set()
    while root and incoming.get(root) and root not in seen_up:
        seen_up.add(root)
        root = min(incoming[root], key=lambda h: nodes[h]["ts"])

    node_list = sorted(nodes.values(), key=lambda n: (n["depth"], n["ts"]))
    pruned = len(node_list) > max_nodes
    if pruned:
        keep = {n["id"] for n in node_list[:max_nodes]} | {requested, root}
        node_list = [n for n in node_list if n["id"] in keep]
        edges = [e for e in edges if e["from"] in keep and e["to"] in keep]

    return {
        "artifact_id": artifact_id,
        "root_id": root,
        "requested_node_id": requested,
        "nodes": node_list,
        "edges": edges,
        "metadata": {
            "total_nodes": len(nodes),
            "max_depth": max((n["depth"] for n in node_list), default=0),
            "pruned": pruned,
        },
    }
