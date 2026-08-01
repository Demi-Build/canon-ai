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

Nothing here mutates the pack; it is a pure projection of on-disk state.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
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
    level has no journal history). Reads the provenance journal — the single
    source of truth across generation, improvement, hand-paint and save. The
    journal is append-ordered, so the last event touching this level is the last
    write (for a multi-step action like improve+reroll that's its final sub-step)."""
    from canon import provenance

    prefix = f"level:{stage_id}/{level_id}/"
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


def export_level_bundle(pack_dir: str | Path, level_id: str) -> dict:
    """Assemble a render-ready bundle for one level of a platformer pack.

    ``pack_dir`` is the output root (the dir holding ``manifest.json``).
    Raises ``FileNotFoundError`` if the pack or level cannot be located.
    """
    pack = Path(pack_dir)
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
