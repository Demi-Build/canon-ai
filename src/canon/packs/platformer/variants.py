"""Enemy variant vocabulary — per-GAME variation, as data (Phase 3b).

Generalizes the 3a ``elite`` flag: named variants load from a per-game
JSON file (``variants.json`` next to the schemas; ``--variants`` on the
runner) and ship in ``manifest.json``. A placement opts in via the
existing ``Placement.overrides`` (``{"variant": "champion"}``) — the
EnemyDefinition stays the single canonical artifact; variation rides on
the placement (§6.1). A boss is just a champion variant at a chokepoint;
``BossDefinition`` stays available but the template never requires it.

Enforcement points (a variant is only real because code interprets it):
- placement validation checks names against this vocabulary and enforces
  the per-level ``GameRules.variant_caps``;
- the placement prompt offers the vocabulary + caps;
- every play surface resolves ``stat_mults``/``speed_mult``/``size``/
  ``visual`` from the manifest — never hardcoded per variant.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: The pack's default variant set — the template other games copy and edit.
DEFAULT_VARIANTS_PATH = Path(__file__).parent / "variants.json"


class EnemyVariant(BaseModel):
    """One named variant. ``stat_mults`` multiplies definition stats
    (hp, damage); ``behavior`` overrides behavior params (patrol_range,
    aggro_range). ``visual`` tells review surfaces how to mark it:
    "outline", "scale", or "outline_scale"."""

    name: str
    stat_mults: dict[str, float] = Field(default_factory=dict)
    speed_mult: float = 1.0
    size: float = 1.0
    visual: str = "outline"
    behavior: dict[str, Any] = Field(default_factory=dict)


class VariantSet(BaseModel):
    model_config = ConfigDict(extra="allow")  # _doc etc. ride through

    variants: list[EnemyVariant] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_names(self) -> VariantSet:
        names = [v.name for v in self.variants]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate variant names in {sorted(names)}")
        return self

    @property
    def by_name(self) -> dict[str, EnemyVariant]:
        return {v.name: v for v in self.variants}


def load_variants(path: str | Path = DEFAULT_VARIANTS_PATH) -> VariantSet:
    """Load a game's variant vocabulary; names validated unique at load."""
    return VariantSet.model_validate(json.loads(Path(path).read_text()))


DEFAULT_VARIANTS = load_variants()
