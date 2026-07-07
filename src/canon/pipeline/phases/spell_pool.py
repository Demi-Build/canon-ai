"""SpellPoolPhase — generates classes/spell_pools.json keyed by pool name.

Two modes:

* **Spec-driven (preferred)** — pass ``pool_specs: list[PoolSpec]``. Each pool
  is generated fresh: roll ``count`` mechanical skeletons, apply per-pool
  ``field_overrides``, make one batched LLM call for name + description, merge.
  The phase is game-agnostic: pool names, counts, skeletons, and theming all
  arrive via the specs, so any RPG (or a future user-edited JSON) defines its
  own pools. Mirrors mazeworld's spell-pool generator.

* **Archetype-regroup (back-compat)** — when ``pool_specs`` is ``None``, the
  legacy behavior is kept: read ``spell_pool`` off each ClassArchetype already
  in the bible and regroup by ``<archetype>_<pool_type>``.

Output shape (both modes): a flat dict keyed by pool name, each value a list of
Spell dicts — matches mazeworld's data/classes/spell_pools.json.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from canon.bible.models import BibleMetadata
from canon.llm.parsing import extract_json_array
from canon.pipeline.retry import default_token_escalation, retry_with_feedback
from canon.skeleton.core import SkeletonSpec, roll_skeleton

logger = logging.getLogger(__name__)


@dataclass
class PoolSpec:
    """Definition of one named spell pool.

    Pure data — packs (or a user-edited JSON later) supply a list of these so
    canon core never hardcodes pool names or game-specific mechanics.

    Attributes:
        name: Output key in spell_pools.json (e.g. ``"mage_damage"``).
        skeleton_spec: SkeletonSpec rolled once per spell to pre-roll mechanics.
        count: Number of spells in this pool.
        field_overrides: Values pinned onto every rolled skeleton (e.g.
            ``{"spell_type": "damage"}`` so a whole pool is damage spells).
        prompt_kwargs: Theming descriptors forwarded to the prompt (e.g.
            ``{"archetype": "mage", "element": "fire"}``).
    """

    name: str
    skeleton_spec: SkeletonSpec
    count: int = 10
    field_overrides: dict = field(default_factory=dict)
    prompt_kwargs: dict = field(default_factory=dict)


class SpellPoolPhase:
    """Generates classes/spell_pools.json.

    Spec-driven when constructed with ``pool_specs``; otherwise falls back to
    regrouping ``ClassArchetype.spell_pool`` (legacy v0.2 behavior).
    """

    name: str = "spell_pool"

    def __init__(self, pool_specs: list[PoolSpec] | None = None) -> None:
        self.pool_specs = pool_specs

    def run(self, ctx: Any) -> None:
        if self.pool_specs:
            pools = self._generate_pools(ctx)
        else:
            pools = self._regroup_archetype_pools(ctx)

        rel_path = self._rel_path(ctx)
        ctx.adapter.write_json_singleton(rel_path, pools)
        self._stamp_metadata(ctx)
        logger.info(
            "SpellPoolPhase wrote %d spells across %d pools to %s",
            sum(len(v) for v in pools.values()),
            len(pools),
            ctx.adapter.resolve_path(rel_path),
        )

    # ------------------------------------------------------------------
    # Spec-driven generation (preferred)
    # ------------------------------------------------------------------

    def _generate_pools(self, ctx: Any) -> dict[str, list[dict]]:
        pools: dict[str, list[dict]] = {}
        for spec in self.pool_specs:
            # Pre-roll one mechanical skeleton per spell; pin field_overrides.
            skeletons: list[dict] = []
            for _ in range(spec.count):
                skel = roll_skeleton(spec.skeleton_spec, ctx.rng)
                skel.update(spec.field_overrides)
                skeletons.append(skel)

            pools[spec.name] = generate_named_entries(
                ctx,
                label=f"{self.name}:{spec.name}",
                skeletons=skeletons,
                prompt_kwargs=spec.prompt_kwargs,
            )
            logger.info("Spell pool %r: %d spells", spec.name, len(pools[spec.name]))
        return pools

    @staticmethod
    def _merge(
        skeletons: list[dict], llm_spells: list, pool_name: str
    ) -> list[dict]:
        """Merge LLM name/description onto each pre-rolled skeleton.

        Mechanics always come from the skeleton; only name + description are
        taken from the LLM. Missing LLM entries fall back to a generic name.
        """
        return _merge_named(skeletons, llm_spells, pool_name)

    # ------------------------------------------------------------------
    # Legacy archetype-regroup (back-compat when no pool_specs)
    # ------------------------------------------------------------------

    def _regroup_archetype_pools(self, ctx: Any) -> dict[str, list[dict]]:
        bible = ctx.bible
        if not bible.class_archetypes:
            logger.warning("SpellPoolPhase: no class_archetypes; nothing to do.")
            return {}

        pools: dict[str, list[dict]] = {}
        for arch_id, arch in bible.class_archetypes.items():
            archetype_label = arch.archetype or arch_id  # e.g. "mage", "healer"
            for spell in arch.spell_pool or []:
                pool_type = self._classify_spell(spell.spell_type)
                key = f"{archetype_label}_{pool_type}"
                pools.setdefault(key, []).append(spell.model_dump(mode="json"))
        return pools

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

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _rel_path(self, ctx: Any) -> str:
        return getattr(ctx.config, "output_paths", {}).get(
            "spell_pools", "classes/spell_pools.json"
        )

    def _stamp_metadata(self, ctx: Any) -> None:
        if not isinstance(getattr(ctx.bible, "metadata", None), BibleMetadata):
            ctx.bible.metadata = BibleMetadata()
        ctx.bible.metadata.phases_run.append(self.name)


# ---------------------------------------------------------------------------
# Shared generation helper — reused by SpellPoolPhase and ClassPhase
# ---------------------------------------------------------------------------


def _merge_named(skeletons: list[dict], llm_entries: list, label: str) -> list[dict]:
    """Graft LLM name/description onto each pre-rolled skeleton (mechanics stay
    from the skeleton). Missing LLM entries fall back to a generic name."""
    merged: list[dict] = []
    for i, skel in enumerate(skeletons):
        llm = llm_entries[i] if i < len(llm_entries) and isinstance(llm_entries[i], dict) else {}
        entry = dict(skel)
        entry["name"] = llm.get("name") or f"{label.replace('_', ' ').title()} {i + 1}"
        entry["description"] = llm.get("description", "")
        merged.append(entry)
    return merged


def generate_named_entries(
    ctx: Any,
    *,
    label: str,
    skeletons: list[dict],
    prompt_kwargs: dict,
) -> list[dict]:
    """Batch-generate names+descriptions for pre-rolled skeletons, then merge.

    The canonical "mechanics from skeleton, flavor from LLM" primitive: takes a
    list of already-rolled mechanical skeletons, makes ONE batched
    ``spell_pool_generation`` call for ``len(skeletons)`` name/description pairs,
    and grafts them on. Reused for shared spell pools AND per-class
    spell/ability loadouts. Returns merged dicts (mechanics intact); on LLM
    failure, every entry still exists with a generic name.
    """
    if not skeletons:
        return []
    count = len(skeletons)
    max_retries = getattr(ctx.config, "max_retries", 3)
    context_text = ctx.bible.get_context("") if hasattr(ctx.bible, "get_context") else ""

    def generate(feedback: list[str] | None = None, max_tokens: int | None = None) -> str:
        if feedback:
            request = ctx.prompts.spell_pool_generation_with_feedback(
                context=context_text, count=count, prompt_kwargs=prompt_kwargs, feedback=feedback,
            )
        else:
            request = ctx.prompts.spell_pool_generation(
                context=context_text, count=count, prompt_kwargs=prompt_kwargs,
            )
        if max_tokens is not None:
            request.max_tokens = max_tokens
        return ctx.llm.generate(request, phase=label)

    def validate(content: str) -> tuple[bool, list[str]]:
        if extract_json_array(content) is None:
            return False, [
                "Response did not contain a JSON array. Return ONLY a JSON "
                "array of objects, no prose or markdown code fences."
            ]
        return True, []

    result = retry_with_feedback(
        generate_fn=generate,
        validate_fn=validate,
        fallback="[]",
        max_retries=max_retries,
        label=label,
        token_escalation=default_token_escalation,
        initial_max_tokens=max(400, count * 80),
    )
    return _merge_named(skeletons, extract_json_array(result) or [], label)
