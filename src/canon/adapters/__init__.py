"""Output adapters — the engine-facing write layer, plus the GridKind read maps.

Phases call ``ctx.adapter.write_*`` instead of the persistence helpers
directly, so a pipeline can target a different engine (Godot, etc.) by
swapping the adapter on the PipelineContext. ``JsonOutputAdapter`` is the
default and preserves MazeWorld's exact on-disk output.

``GRID_READERS`` / ``GRID_DESCRIBERS`` are the read side's dispatch data:
GridKind id → the verb that serves it, as ``module:attr`` strings resolved
lazily by ``grid_verb`` so ``canon --help`` never pays for numpy. Row P0-5
kept the reader map inside ``cli/main.py``; row A3 moved it here because the
agent's in-process read tools (``canon.agent.tools_read``) dispatch off the
same map — one data entry per template, two consumers, never a branch on
``pack_type``. Row P0-6 added the write side beside them: ``GRID_EDITORS``
(``canon grid apply-edit`` — sparse placements / spawn / exit) and
``GRID_IMPORTERS`` (``canon grid import-grids`` — the painted dense grid).

Row P0-8 fills the ``room`` gaps: the describer, the two writers, the
restorer (``GRID_RESTORERS`` — History's write half) and the per-step roller
(``GRID_ROLLERS``). Every table is the same shape, so a third template
registers five data entries and never a branch on ``pack_type``. Where a
table has no entry for a kind the consumers still answer a structured
"not yet"/"use that surface" naming what serves it instead.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from canon.adapters.godot_adapter import GodotOutputAdapter
from canon.adapters.json_adapter import JsonOutputAdapter

#: GridKind id → its render-ready bundle reader (``canon grid export`` and the
#: agent's ``export_level``). A third template registers its reader here.
GRID_READERS: dict[str, str] = {
    "level": "canon.adapters.platformer_read:export_level_bundle",
    "room": "canon.adapters.dungeon_read:export_room_bundle",
}

#: GridKind id → its compact describer (``canon grid describe`` and the
#: agent's ``describe_level``).
GRID_DESCRIBERS: dict[str, str] = {
    "level": "canon.adapters.platformer_read:describe_level",
    "room": "canon.adapters.dungeon_read:describe_room",
}

#: GridKind id → the sparse-layer edit writer (``canon grid apply-edit``;
#: ``level apply-edit`` is the alias).
GRID_EDITORS: dict[str, str] = {
    "level": "canon.adapters.platformer_write:apply_level_edit",
    "room": "canon.adapters.dungeon_write:apply_room_edit",
}

#: GridKind id → the dense-grid import writer (``canon grid import-grids``;
#: ``level import-grids`` is the alias).
GRID_IMPORTERS: dict[str, str] = {
    "level": "canon.adapters.platformer_write:import_level_grids",
    "room": "canon.adapters.dungeon_write:import_room_grids",
}

#: GridKind id → the step restorer (``canon grid restore``): a stored version
#: written back through the SAME writer, ``op:"restore"`` (doctrine 6).
GRID_RESTORERS: dict[str, str] = {
    "level": "canon.adapters.platformer_write:restore_level_step",
    "room": "canon.adapters.dungeon_write:restore_room_step",
}

#: GridKind id → the per-step roller (``canon grid roll``): ONE verb
#: dispatching on ``--step``, code-only and $0 (P0 paper P.6.3). The
#: platformer has no such verb — its per-step generation is LLM-backed and
#: lives on its own commands (``level generate-terrain`` / ``place-enemies``
#: / ``place-items``), which the CLI names when this table has no entry.
GRID_ROLLERS: dict[str, str] = {
    "room": "canon.packs.dungeon.rolls:roll_room",
}

#: The row that filled the ``room`` gaps above (kept as the "which row brings
#: this?" pointer for any table a future template leaves empty).
GRID_ROOM_ROW = "P0-8"


def grid_verb(table: dict[str, str], kind: str) -> Callable[..., Any] | None:
    """The verb ``table`` registers for GridKind ``kind``, imported on demand;
    ``None`` when the table has no entry. ``ImportError`` propagates — the
    caller says which extra is missing in its own voice."""
    target = table.get(kind)
    if target is None:
        return None
    module_path, attr = target.split(":", 1)
    return getattr(importlib.import_module(module_path), attr)


@runtime_checkable
class OutputAdapter(Protocol):
    """Structural protocol for pipeline output backends.

    Any class implementing these methods satisfies the protocol; no
    inheritance required (mirrors the ``Phase`` protocol in the runner).

    Every ``write_*`` method returns the **content hash** of the exact
    bytes written, as ``"sha256:<hex>"`` (PRD §8.2). The hash is of file
    content only — recomputable from disk for edit detection (§6.3).
    Phases fold generation inputs into the separate *provenance* hash via
    ``canon.bible.artifacts.compute_provenance_hash``.
    """

    def write_json_array(self, path: Path, entities: list) -> str: ...

    def write_json_keyed(
        self, path: Path, entities: list | dict, key_field: str = "id"
    ) -> str: ...

    def write_json_singleton(self, path: Path, obj: Any) -> str: ...

    def write_binary(self, path: Path, data: bytes) -> str: ...

    def write_numpy(self, path: Path, **arrays: Any) -> str: ...  # → .npz

    def write_per_map(self, template: str, map_id: str, obj: Any) -> str: ...

    def resolve_path(self, relative: str | Path) -> Path: ...


__all__ = [
    "GRID_DESCRIBERS",
    "GRID_EDITORS",
    "GRID_IMPORTERS",
    "GRID_READERS",
    "GRID_RESTORERS",
    "GRID_ROLLERS",
    "GRID_ROOM_ROW",
    "GodotOutputAdapter",
    "JsonOutputAdapter",
    "OutputAdapter",
    "grid_verb",
]
