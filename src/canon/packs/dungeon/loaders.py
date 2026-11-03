"""Read-back loaders for the dungeon pack's ``EntityKind`` rows — the
"missing organ" of Phase 0 §8.2 (P0 paper P.3.1 ``loader``; row P0-5).

Two functions, both pure reads:

- ``load_rows(pack, entity)`` — the collection file of one kind as a dict
  keyed by the stringified ``id_field`` value, generic over ``layout.format``
  (``array`` | ``keyed_object`` | ``array_positional``). Rows come back as
  they sit ON DISK — engine truth, untouched: no rename, no coercion.
- ``skeleton_view(row, entity)`` — the row with the kind's ``renames`` map
  applied INVERTED (on-disk name → skeleton name, dotted targets such as
  ``item_stats.stat_modifier`` ← ``weapon_stat`` included), so a
  reroll-with-locks can rebuild the skeleton the writer never persisted
  (P.1: "the rolled skeleton is not persisted"). The one rename whose VALUES
  change too — npc ``behavior_type`` → engine class names — inverts through
  ``parsers._NPC_TYPE_MAP`` itself (imported, never copied); the class row's
  ``stat_template`` re-derives from ``stats``, the field the engine reads
  (P.1.6).

The seeds bind ``EntityKind.loader`` to ``load_rows`` per kind
(``spec.py``); ``loader`` is seed-only and never stamped (P.3.1). This
extends the registry seam of row P0-3 — the same ``layout`` data ``pack
info`` counts rows with now also reads them.

Row P0-6 moved ``load_rows`` itself to ``canon.packs.rows`` (it is generic
over any collection layout, and a ``db define``d kind of ANY pack now
reads through it); this module re-exports it unchanged and keeps
``skeleton_view`` — the rename/value inverses are this pack's. Every
write lives in ``canon.db_ops``.
"""

from __future__ import annotations

import copy
from typing import Any

from canon.packs.dungeon.parsers import _NPC_TYPE_MAP
from canon.packs.rows import load_rows
from canon.packs.rows import read_json as _read_json
from canon.packs.spec import EntityKind

#: On-disk value → skeleton value, per kind and ON-DISK field, for the one
#: rename whose values change too (P.1.1: the ``behavior_type`` roll
#: vocabulary is written as the engine's NPC class names). Built from the
#: writer's own table so the two directions cannot drift.
_VALUE_INVERSES: dict[str, dict[str, dict[Any, Any]]] = {
    "npc": {"type": {disk: skeleton for skeleton, disk in _NPC_TYPE_MAP.items()}},
}

#: duplicate field → the authoritative field it mirrors (P.1.6: the engine
#: reads ``stats``; ``stat_template`` is canon's copy). The view re-derives
#: the duplicate from the authority so a locked reroll starts from truth.
_DUPLICATES: dict[str, dict[str, str]] = {
    "class": {"stat_template": "stats"},
}


def _pop_path(container: dict, dotted: str) -> tuple[bool, Any]:
    """Remove and return the value at a dotted path (``item_stats.attribute``);
    ``(False, None)`` when any segment is absent, leaving the row untouched."""
    parts = dotted.split(".")
    node: Any = container
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    if not isinstance(node, dict) or parts[-1] not in node:
        return False, None
    return True, node.pop(parts[-1])


def skeleton_view(row: dict, entity: EntityKind) -> dict:
    """*row* re-keyed by the kind's skeleton names: every ``renames`` entry
    ``skeleton → disk`` is applied in reverse (the on-disk key is lifted out,
    dotted targets from their container), on-disk VALUES map back through
    ``_VALUE_INVERSES`` where the writer changed them, and duplicates
    (``_DUPLICATES``) re-derive from their authority. Fields the map does not
    name pass through unchanged. The input row is never mutated."""
    out = copy.deepcopy(row)
    value_inverse = _VALUE_INVERSES.get(entity.kind, {})
    for skeleton_name, disk_name in entity.renames.items():
        present, value = _pop_path(out, disk_name)
        if not present:
            continue
        table = value_inverse.get(disk_name)
        if table is not None:
            value = table.get(value, value)
        out[skeleton_name] = value
    for duplicate, authority in _DUPLICATES.get(entity.kind, {}).items():
        if authority in out:
            out[duplicate] = copy.deepcopy(out[authority])
    return out


__all__ = ["_read_json", "load_rows", "skeleton_view"]
