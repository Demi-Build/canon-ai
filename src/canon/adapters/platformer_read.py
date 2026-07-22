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

import json
from pathlib import Path
from typing import Any


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
    return {
        "level_id": level_id,
        "stage_id": stage_id,
        "display_name": _display_name(manifest, level_id),
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
    }
