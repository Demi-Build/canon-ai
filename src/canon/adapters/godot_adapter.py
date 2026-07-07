"""GodotOutputAdapter — Godot-facing output (PRD §8.3, Phase 4).

Subclasses JsonOutputAdapter rather than reimplementing it: Godot parses
JSON natively, so the canonical output tree is already engine-readable.
The one gap is dense masks — Godot cannot read ``.npz`` — so ``write_numpy``
additionally emits a ``<name>.grid.json`` sibling with the arrays as nested
lists.

The canonical ``.npz`` is still written and its content hash is still the
return value, so the Bible's ``*_hash`` fields and Phase 2's future edit
detection are identical under either adapter. The sibling is derived
engine output, not a canonical artifact — nothing references or hashes it.

Engine-native resource formats (``.tres``/``.res``) are deliberately
deferred until real art assets exist; JSON + PNG is idiomatic Godot
consumption at placeholder scale.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from canon.adapters.json_adapter import JsonOutputAdapter


class GodotOutputAdapter(JsonOutputAdapter):
    """JsonOutputAdapter + Godot-readable siblings for binary masks."""

    def write_numpy(self, path: str | Path, **arrays: Any) -> str:
        content_hash = super().write_numpy(path, **arrays)

        rel = str(path)
        sibling = (rel[: -len(".npz")] if rel.endswith(".npz") else rel) + ".grid.json"
        self.write_json_singleton(
            sibling, {name: array.tolist() for name, array in arrays.items()}
        )
        return content_hash
