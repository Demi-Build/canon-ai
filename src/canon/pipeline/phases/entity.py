"""EntityPhase — skeleton-driven entity generation.

This is canon's core "skeleton-driven generation" phase. The user defines
a SkeletonSpec for their entity type ("weapon", "tower", "spell", "route",
"ability", anything), and EntityPhase:

1. Rolls the skeleton's mechanical properties deterministically (skeleton wins).
2. Asks the LLM only for ``name`` + ``lore`` consistent with the world context.
3. Merges them into an EntityLore and adds it to the bible.

A pipeline can compose multiple EntityPhase instances — one per entity type:

    EntityPhase("weapon"), EntityPhase("tower"), EntityPhase("monster")

The phase has zero opinions about what entity types exist; that is user data.

# TODO(v0.2): global (non-map) entity store on Bible — currently per_map=False
# entities attach to the first map as a fallback.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from canon.bible.models import BibleMetadata, EntityLore, GenerationTrail
from canon.pipeline.retry import retry_with_feedback
from canon.skeleton.core import roll_skeleton

logger = logging.getLogger(__name__)


class EntityPhase:
    """Generates entities of a given type.

    Skeleton rolled deterministically from ``ctx.schemas[entity_type]``, LLM
    fills ``name`` + ``lore`` consistent with world context, merged. Skeleton
    values always win for mechanical fields.

    Compose multiple EntityPhase instances for multi-type pipelines::

        EntityPhase('weapon'), EntityPhase('tower'), EntityPhase('crop'), ...

    Args:
        entity_type: User-defined entity type. Must be a key in ``ctx.schemas``.
        per_map: If True, generate ``count`` entities per map. If False,
            generate ``count`` global entities (e.g. universal items).
        count: Per-map (or global) count. Can be an int or a
            ``Callable[[Map | None], int]``.
    """

    def __init__(
        self,
        entity_type: str,
        per_map: bool = True,
        count: int | Callable[[Any], int] = 3,
    ) -> None:
        self.entity_type = entity_type
        self.per_map = per_map
        self.count = count
        self.name = f"entity:{entity_type}"

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self, ctx: Any) -> None:
        """Execute the phase against the shared pipeline context.

        Skips silently (with a warning) when ``ctx.schemas`` has no spec
        registered for ``self.entity_type``.
        """
        spec = ctx.schemas.get(self.entity_type)
        if spec is None:
            logger.warning(
                "EntityPhase: no SkeletonSpec registered for entity_type=%r; skipping.",
                self.entity_type,
            )
            return

        max_retries = getattr(ctx.config, "max_retries", 3)

        if self.per_map:
            for map_id, map_obj in ctx.bible.maps.items():
                cnt = self._resolve_count(map_obj)
                for i in range(cnt):
                    self._generate_one(
                        ctx, spec, map_id=map_id, index=i, max_retries=max_retries
                    )
        else:
            cnt = self._resolve_count(None)
            for i in range(cnt):
                self._generate_one(
                    ctx, spec, map_id=None, index=i, max_retries=max_retries
                )

        if not isinstance(ctx.bible.metadata, BibleMetadata):
            ctx.bible.metadata = BibleMetadata()
        ctx.bible.metadata.phases_run.append(self.name)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_count(self, map_obj: Any) -> int:
        if callable(self.count):
            return self.count(map_obj)
        return int(self.count)

    def _generate_one(
        self,
        ctx: Any,
        spec: Any,
        *,
        map_id: str | None,
        index: int,
        max_retries: int,
    ) -> None:
        # 1. Roll the skeleton deterministically — skeleton wins.
        skeleton = roll_skeleton(spec, ctx.rng)

        # 2. Assemble world context for the LLM call.
        if map_id is not None:
            context_text = ctx.bible.get_cumulative_context(map_id)
        else:
            context_text = ctx.bible.get_context("")

        # Mutable capture dict so prompt + response survive across retry attempts.
        captured: dict[str, str] = {"prompt": "", "response": ""}

        def generate(feedback: list[str] | None = None) -> str:
            if feedback:
                request = ctx.prompts.entity_generation_with_feedback(
                    entity_type=self.entity_type,
                    context=context_text,
                    skeleton=skeleton,
                    feedback=feedback,
                )
            else:
                request = ctx.prompts.entity_generation(
                    entity_type=self.entity_type,
                    context=context_text,
                    skeleton=skeleton,
                )
            captured["prompt"] = request.user_message
            response = ctx.llm.generate(request, phase=self.name)
            captured["response"] = response
            return response

        def validate(content: str) -> tuple[bool, list[str]]:
            try:
                data = json.loads(content)
            except json.JSONDecodeError as exc:
                return False, [f"Not valid JSON: {exc}"]
            if not isinstance(data, dict):
                return False, ["Response must be JSON object."]
            issues: list[str] = []
            if not data.get("name"):
                issues.append("Missing 'name'.")
            return len(issues) == 0, issues

        fallback = json.dumps(
            {"name": f"{self.entity_type} {index}", "lore": ""}
        )

        result = retry_with_feedback(
            generate_fn=generate,
            validate_fn=validate,
            fallback=fallback,
            max_retries=max_retries,
            label=self.name,
        )

        try:
            data = json.loads(result)
        except json.JSONDecodeError:
            return

        # 3. Assign a stable entity_id.
        entity_id = data.get("entity_id") or (
            f"{self.entity_type}_{_count_existing(ctx.bible, self.entity_type):04d}"
        )

        trail = GenerationTrail(
            prompt=captured["prompt"],
            response=captured["response"],
            validation_history=[],
            retry_count=0,
            cost=getattr(ctx.llm.backend, "last_cost", None),
            model=getattr(ctx.llm.backend, "model", None),
        )

        # 4. Build EntityLore — skeleton is persisted verbatim (skeleton wins).
        entity = EntityLore(
            entity_id=entity_id,
            entity_type=self.entity_type,
            name=data.get("name", f"{self.entity_type} {index}"),
            map_id=map_id,
            lore=data.get("lore", ""),
            skeleton=skeleton,  # CRITICAL: persist the rolled skeleton — cradle uses this for re-rolls
            tags=data.get("tags") or [],
            generation_trail=trail,
        )

        # 5. Attach to bible.
        if map_id is not None:
            ctx.bible.add_entity(map_id, entity)
        else:
            # Global entities — per_map=False path.
            # TODO(v0.2): global (non-map) entity store on Bible — currently
            # per_map=False entities attach to the first map as a fallback.
            if ctx.bible.maps:
                first_map = next(iter(ctx.bible.maps))
                ctx.bible.add_entity(first_map, entity)
            else:
                logger.warning(
                    "EntityPhase: per_map=False with no maps; entity %s dropped.",
                    entity.entity_id,
                )


# ---------------------------------------------------------------------------
# Module-level helper
# ---------------------------------------------------------------------------


def _count_existing(bible: Any, entity_type: str) -> int:
    """Count entities of the given type across all maps in the bible."""
    return sum(
        1
        for m in bible.maps.values()
        for e in m.entities
        if e.entity_type == entity_type
    )
