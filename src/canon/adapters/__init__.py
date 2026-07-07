"""Output adapters — the engine-facing write layer.

Phases call ``ctx.adapter.write_*`` instead of the persistence helpers
directly, so a pipeline can target a different engine (Godot, etc.) by
swapping the adapter on the PipelineContext. ``JsonOutputAdapter`` is the
default and preserves MazeWorld's exact on-disk output.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from canon.adapters.godot_adapter import GodotOutputAdapter
from canon.adapters.json_adapter import JsonOutputAdapter


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


__all__ = ["OutputAdapter", "JsonOutputAdapter", "GodotOutputAdapter"]
