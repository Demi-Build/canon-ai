"""``canon.model_table`` — the per-agent model table format, in the CORE
(row P0-7's core/pack split, Phase 0 W2.1.2).

Moved verbatim out of ``canon.packs.platformer.models`` (which re-exports
these names, so ``dag.py`` / ``ops.py`` / ``run_slice.py`` and the tests keep
importing from where they always did, and the platformer's
``DEFAULT_MODELS_PATH`` / ``DEFAULT_MODEL_TABLE`` stay pack data): the shared
estimator (``canon.estimator.Estimator.resolver``) resolves ANY pack's
``models_path`` through this loader, so the format cannot live inside one
pack — a dungeon-only wheel would drag ``canon.packs.platformer`` in with it.

``models.json`` holds two maps. ``model_tiers`` names the tier→model-id map
(the single place a model bump lands). ``agent_tiers`` assigns a tier per
PHASE-LABEL PREFIX (``plat:layout`` matches ``plat:layout:l5:s2``), with
``_default`` covering everything unlisted.

Assignment principle (PRD §9.1): spend ``mid`` where the output defines
structure/playability with no deterministic gate behind it; drop to ``cheap``
where a validator backstops the agent or the task is naming/flavor only.
``top`` is opt-in per node, never a blanket.

The resolver only takes effect on backends that declare
``supports_request_model`` (AnthropicBackend). The fake backend ignores it, so
$0 runs — and their provenance stamps — are untouched.

Deliberately absent, by row ownership: the tier ids themselves (pack data in
each pack's ``models.json``), the wizard's model picker (P0-10), the ledger's
per-call model record (A6).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ModelTable:
    """tier→model-id map + phase-label-prefix→tier assignments."""

    model_tiers: dict[str, str] = field(default_factory=dict)
    agent_tiers: dict[str, str] = field(default_factory=dict)

    def resolve(self, label: str) -> str | None:
        """Model id for a phase label, via the longest matching agent
        prefix (colon-bounded), falling back to ``_default``. ``None``
        means "no opinion — use the backend's constructed model"."""
        best: str | None = None
        for key in self.agent_tiers:
            if key == "_default":
                continue
            if (label == key or label.startswith(key + ":")) and (
                best is None or len(key) > len(best)
            ):
                best = key
        tier = (
            self.agent_tiers[best]
            if best is not None
            else self.agent_tiers.get("_default")
        )
        if tier is None:
            return None
        return self.model_tiers[tier]


def load_models(path: str | Path) -> ModelTable:
    """Load and validate a models.json (every assigned tier must exist)."""
    data = json.loads(Path(path).read_text())
    tiers = dict(data.get("model_tiers", {}))
    agents = dict(data.get("agent_tiers", {}))
    unknown = sorted({t for t in agents.values() if t not in tiers})
    if unknown:
        raise ValueError(
            f"models.json: agent_tiers reference undefined tier(s) "
            f"{unknown}; model_tiers defines {sorted(tiers)}"
        )
    return ModelTable(model_tiers=tiers, agent_tiers=agents)
