"""Per-agent model assignment — PRD §9.1 realized as pack DATA.

The FORMAT (``ModelTable`` + the loader/validator) moved to the core at row
P0-7 (``canon.model_table``) so the shared estimator can resolve any pack's
``models_path`` without importing this pack; this module re-exports both
names unchanged — every existing import site (``dag.py``, ``ops.py``,
``run_slice.py``, ``estimate.py``, ``tests/test_model_table.py``) keeps
working — and keeps what is genuinely platformer data: the path of THIS
pack's ``models.json`` and the table loaded from it.

``models.json`` holds two maps. ``model_tiers`` names the tier→model-id
map (the single place a model bump lands). ``agent_tiers`` assigns a
tier per PHASE-LABEL PREFIX (``plat:layout`` matches
``plat:layout:l5:s2``), with ``_default`` covering everything unlisted.

Assignment principle (PRD §9.1): spend ``mid`` where the output defines
structure/playability with no deterministic gate behind it; drop to
``cheap`` where a validator backstops the agent or the task is
naming/flavor only. ``top`` is opt-in per node, never a blanket.

The resolver only takes effect on backends that declare
``supports_request_model`` (AnthropicBackend). The fake backend ignores
it, so $0 runs — and their provenance stamps — are untouched.
"""

from __future__ import annotations

from pathlib import Path

from canon.model_table import ModelTable
from canon.model_table import load_models as _load_models

__all__ = ["DEFAULT_MODELS_PATH", "DEFAULT_MODEL_TABLE", "ModelTable", "load_models"]

DEFAULT_MODELS_PATH = Path(__file__).parent / "models.json"


def load_models(path: str | Path = DEFAULT_MODELS_PATH) -> ModelTable:
    """Load and validate a models.json (every assigned tier must exist) —
    :func:`canon.model_table.load_models` with this pack's file as the
    default path."""
    return _load_models(path)


DEFAULT_MODEL_TABLE = load_models()
