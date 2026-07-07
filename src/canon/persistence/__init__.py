"""Persistence helpers for canon's per-phase file output.

Mazeworld conventions:
- ``write_array_db``   — flat JSON array  (npcs.json, events.json, quests.json, classes.json)
- ``write_keyed_db``   — keyed object     (items.json, monsters.json)
- ``write_singleton``  — single JSON obj  (manifest.json, narrative.json, story.json, …)
- ``write_per_map_file`` — per-map singleton (rooms/{map_id}/maze.json)

All writers:
- Create parent directories as needed.
- Use 2-space indentation (matches mazeworld).
- Accept both Pydantic ``BaseModel`` instances (serialised via
  ``model_dump(mode="json")``) and plain dicts / JSON-serialisable objects.
- Return the content hash (``"sha256:<hex>"``) of the exact bytes written,
  so callers (the adapter layer, PRD §8.2) can stamp provenance without
  re-reading the file. Callers that don't need it ignore the return.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _to_jsonable(obj: Any) -> Any:
    """Convert *obj* to a JSON-serialisable value.

    Handles Pydantic BaseModel (calls model_dump), dicts, and anything else
    that is already JSON-serialisable.
    """
    # Duck-typed Pydantic check: avoids a hard import of pydantic at module
    # level while still supporting both v1 and v2 BaseModel.
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    # Pydantic v1 fallback
    if hasattr(obj, "dict"):
        return obj.dict()
    return obj


def _write_json(path: Path, data: Any) -> str:
    """Write *data* to *path* as JSON with 2-space indent.

    Creates parent directories.  Writes atomically via a temp file in the
    same directory so a crash mid-write does not leave a truncated file.
    Returns the content hash of the written bytes.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2)
    # Atomic write: write to a temp file in the same dir, then rename.
    dir_ = path.parent
    fd, tmp_path = tempfile.mkstemp(dir=dir_, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp_path, path)
    except Exception:
        # Clean up the temp file on any error before re-raising.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def write_array_db(path: str | Path, entities: list) -> str:
    """Write a flat JSON array of entity dicts.

    Used for npcs.json, events.json, quests.json, classes.json.

    Each entity may be a Pydantic BaseModel (serialised via
    ``model_dump(mode="json")``) or a plain dict.
    Returns the content hash of the written bytes.
    """
    data = [_to_jsonable(e) for e in entities]
    return _write_json(Path(path), data)


def write_keyed_db(
    path: str | Path,
    entities: list | dict,
    key_field: str = "id",
) -> str:
    """Write a keyed-object JSON file.

    Mazeworld convention: ``{"<id>": {entity_dict_minus_id}}``.
    Used for items.json, monsters.json.

    - If *entities* is a **list**, each element is serialised and keyed by
      ``str(entity[key_field])``.  The ``key_field`` is **not** removed from
      the value dict — mazeworld's files retain the id inside the value.
    - If *entities* is already a **dict** (key → entity), it is written
      directly (values are still normalised via ``_to_jsonable``).

    Handles both Pydantic models and dicts.
    """
    if isinstance(entities, dict):
        data = {k: _to_jsonable(v) for k, v in entities.items()}
    else:
        data = {}
        for entity in entities:
            serialised = _to_jsonable(entity)
            key = str(serialised[key_field])
            data[key] = serialised
    return _write_json(Path(path), data)


def write_singleton(path: str | Path, obj: Any) -> str:
    """Write a single JSON object to *path*.

    Used for manifest.json, narrative.json, generation_stats.json,
    world_bible.json, story.json, spell_pools.json.

    *obj* may be a Pydantic BaseModel, a dict, or any JSON-serialisable value.
    Returns the content hash of the written bytes.
    """
    return _write_json(Path(path), _to_jsonable(obj))


def write_per_map_file(
    path_template: str | Path,
    map_id: str,
    obj: Any,
) -> str:
    """Format *path_template* with *map_id* and call ``write_singleton``.

    Example::

        write_per_map_file("rooms/{map_id}/maze.json", "room_0", maze_obj)
        # writes to rooms/room_0/maze.json

    The template is formatted with a single ``{map_id}`` placeholder.
    """
    resolved = str(path_template).format(map_id=map_id)
    return write_singleton(Path(resolved), obj)


# Re-export IDAllocator for convenience.
from canon.persistence.id_allocator import IDAllocator  # noqa: E402

__all__ = [
    "write_array_db",
    "write_keyed_db",
    "write_singleton",
    "write_per_map_file",
    "IDAllocator",
]
