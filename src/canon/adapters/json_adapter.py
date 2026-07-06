"""JsonOutputAdapter — the default OutputAdapter.

Wraps the writers in ``canon.persistence`` so output is byte-identical to
the pre-adapter direct calls. This is the MazeWorld-compatible backend;
engine-specific adapters (Godot, etc.) are sibling implementations of the
same protocol.

All methods are safe to call concurrently as long as target paths differ:
every write is atomic (temp file in the target directory + ``os.replace``)
and the adapter holds no mutable shared state beyond the immutable
``output_dir``.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from canon.persistence import (
    write_array_db,
    write_keyed_db,
    write_per_map_file,
    write_singleton,
)


class JsonOutputAdapter:
    """Default OutputAdapter: JSON files with MazeWorld conventions.

    Wraps ``canon.persistence`` writers; does not replace them.
    """

    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = Path(output_dir)

    # ------------------------------------------------------------------
    # Path resolution
    # ------------------------------------------------------------------

    def resolve_path(self, relative: str | Path) -> Path:
        """Resolve *relative* against ``output_dir``.

        Resolution rule: relative paths are joined onto ``output_dir``;
        absolute paths pass through unchanged (some callers and tests hand
        phases pre-resolved absolute paths — both must keep working).
        """
        path = Path(relative)
        return path if path.is_absolute() else self.output_dir / path

    # ------------------------------------------------------------------
    # JSON writers (delegating to canon.persistence)
    # ------------------------------------------------------------------

    def write_json_array(self, path: str | Path, entities: list) -> None:
        write_array_db(self.resolve_path(path), entities)

    def write_json_keyed(
        self,
        path: str | Path,
        entities: list | dict,
        key_field: str = "id",
    ) -> None:
        write_keyed_db(self.resolve_path(path), entities, key_field=key_field)

    def write_json_singleton(self, path: str | Path, obj: Any) -> None:
        write_singleton(self.resolve_path(path), obj)

    def write_per_map(self, template: str, map_id: str, obj: Any) -> None:
        # The {map_id} placeholder survives the join; write_per_map_file
        # formats it exactly as the phases did pre-adapter.
        write_per_map_file(str(self.resolve_path(template)), map_id, obj)

    # ------------------------------------------------------------------
    # Binary writers (atomic, matching the persistence temp+rename pattern)
    # ------------------------------------------------------------------

    def write_binary(self, path: str | Path, data: bytes) -> None:
        target = self.resolve_path(path)
        self._atomic_write_bytes(target, lambda fh: fh.write(data))

    def write_numpy(self, path: str | Path, **arrays: Any) -> None:
        # Lazy import: numpy is not a core canon dependency; only adapters
        # that actually write .npz payloads need it installed.
        import numpy

        target = self.resolve_path(path)
        self._atomic_write_bytes(
            target, lambda fh: numpy.savez_compressed(fh, **arrays)
        )

    @staticmethod
    def _atomic_write_bytes(target: Path, write_fn: Any) -> None:
        """Write binary content atomically: temp file in the target dir,
        then ``os.replace``. No partial file is left on crash."""
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                write_fn(fh)
            os.replace(tmp_path, target)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
