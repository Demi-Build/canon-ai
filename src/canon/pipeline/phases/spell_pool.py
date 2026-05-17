"""SpellPoolPhase — generates classes/spell_pools.json keyed by
<archetype>_<pool_type>. Driven by class archetypes already in the bible.

Per mazeworld's data/classes/spell_pools.json shape: a flat dict with keys
like 'mage_damage', 'healer_heal', 'buff', 'damage'. Each value is a list
of Spell entries.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from canon.bible.models import BibleMetadata
from canon.persistence import write_singleton

logger = logging.getLogger(__name__)


class SpellPoolPhase:
    """Reads spell_pool from each ClassArchetype and writes them out grouped
    by archetype name + pool type.

    For v0.2: the spell pool is taken from each archetype.spell_pool list.
    Pool type is derived from the spell's ``spell_type`` field
    (damage_single/damage_multi → 'damage'; heal → 'heal'; buff_* → 'buff').
    """

    name: str = "spell_pool"

    def run(self, ctx: Any) -> None:
        bible = ctx.bible
        if not bible.class_archetypes:
            logger.warning("SpellPoolPhase: no class_archetypes; nothing to do.")
            # Write an empty dict so the file always exists after the phase runs
            write_singleton(self._path(ctx), {})
            self._stamp_metadata(ctx)
            return

        pools: dict[str, list[dict]] = {}
        for arch_id, arch in bible.class_archetypes.items():
            archetype_label = arch.archetype or arch_id  # e.g. "mage", "healer"
            for spell in arch.spell_pool or []:
                pool_type = self._classify_spell(spell.spell_type)
                key = f"{archetype_label}_{pool_type}"
                pools.setdefault(key, []).append(spell.model_dump(mode="json"))

        path = self._path(ctx)
        write_singleton(path, pools)

        self._stamp_metadata(ctx)
        logger.info(
            "SpellPoolPhase wrote %d pool entries to %s",
            sum(len(v) for v in pools.values()),
            path,
        )

    @staticmethod
    def _classify_spell(spell_type: str) -> str:
        """Map a spell_type string to a pool bucket name."""
        if spell_type.startswith("damage"):
            return "damage"
        if spell_type == "heal":
            return "heal"
        if spell_type.startswith("buff"):
            return "buff"
        return "other"

    def _path(self, ctx: Any) -> Path:
        out = Path(getattr(ctx.config, "output_dir", "."))
        rel = getattr(ctx.config, "output_paths", {}).get(
            "spell_pools", "classes/spell_pools.json"
        )
        return out / rel

    def _stamp_metadata(self, ctx: Any) -> None:
        if not isinstance(getattr(ctx.bible, "metadata", None), BibleMetadata):
            ctx.bible.metadata = BibleMetadata()
        ctx.bible.metadata.phases_run.append(self.name)
