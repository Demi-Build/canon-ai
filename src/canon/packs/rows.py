"""Generic row reads over an ``EntityKind.layout`` — the collection-file
reader row P0-5 wrote for the dungeon (``canon.packs.dungeon.loaders.
load_rows``), moved to the registry home at row P0-6 so a ``db define``d
kind of ANY pack reads through it, plus its per-file sibling for a
model-less ``per_file`` kind. ``dungeon.loaders`` keeps ``load_rows`` as a
re-export (its ``skeleton_view`` stays there: the rename/value inverses are
that pack's). Pure reads — rows come back as they sit on disk.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from canon.packs.spec import EntityKind

__all__ = ["load_per_file_rows", "load_rows", "read_json"]


def read_json(path: Path) -> Any | None:
    """The parsed file, or ``None`` when it is absent. A present file that
    does not parse is an error the caller should see, not an empty kind."""
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_rows(pack: str | Path, entity: EntityKind) -> dict[str, dict]:
    """Every row of *entity* in *pack*, keyed by ``str(row[id_field])``.

    ``keyed_object`` files key by their own keys (the id lives there, not
    necessarily in the row — ``items/items.json``); ``array`` and
    ``array_positional`` files key by the ``id_field`` value of each row
    (``id`` for npc/quest/event, ``archetype`` for class). A row lacking its
    id field keys by its position — a hand-broken row never blocks a read
    (the ``_load_defs`` stance), and the key stays unique. An absent file is
    an empty kind: the legacy trees predate ``rooms/rooms.json`` and no tree
    carries ``music/music.json`` yet (P.1.8–9).
    """
    layout = entity.layout or {}
    if layout.get("mode") != "collection":
        raise ValueError(
            f"load_rows reads collection layouts; kind {entity.kind!r} is "
            f"{layout.get('mode')!r} (per-file kinds load through their own loader)"
        )
    fmt = layout.get("format")
    data = read_json(Path(pack) / str(layout.get("path", "")))
    if data is None:
        return {}
    if fmt == "keyed_object":
        if not isinstance(data, dict):
            raise ValueError(f"{layout.get('path')}: expected a keyed object for kind {entity.kind!r}")
        return {str(key): row for key, row in data.items()}
    if fmt in ("array", "array_positional"):
        if not isinstance(data, list):
            raise ValueError(f"{layout.get('path')}: expected an array for kind {entity.kind!r}")
        out: dict[str, dict] = {}
        for index, row in enumerate(data):
            key = row.get(entity.id_field) if isinstance(row, dict) else None
            out[str(key) if key is not None else str(index)] = row
        return out
    raise ValueError(f"unknown layout format {fmt!r} for kind {entity.kind!r}")


def load_per_file_rows(pack: str | Path, entity: EntityKind) -> dict[str, dict]:
    """Every ``<dir>/<id>.json`` of a ``per_file`` kind as plain dicts keyed
    by the file stem — the model-less counterpart of the platformer's
    ``_load_defs`` (which validates into the kind's Pydantic model). A file
    that does not parse is skipped, never fatal (the same stance)."""
    layout = entity.layout or {}
    if layout.get("mode") != "per_file":
        raise ValueError(f"load_per_file_rows reads per_file layouts; kind {entity.kind!r} is {layout.get('mode')!r}")
    directory = Path(pack) / str(layout.get("dir", ""))
    out: dict[str, dict] = {}
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.json")):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(row, dict):
            out[str(row.get(entity.id_field, path.stem))] = row
    return out
