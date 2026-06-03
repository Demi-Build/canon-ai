# TODO(v0.2): align _generate_name_and_lore with EntityPhase's helper through a shared
# module if drift becomes a concern.
"""Cradle-facing targeted bible-mutation operations.

These are the functions cradle invokes when the user wants to re-roll one
field, regenerate a whole entity, or add a new entity to a map. They are
equivalent to running EntityPhase against a single entity rather than an
entire pipeline.

Three public entry points:

* ``reroll_entity_flavor`` — re-generate name + lore for an existing entity,
  preserving its skeleton (mechanical roll). Used when the user wants a
  different name/lore for the same stats.

* ``regenerate_entity`` — full respin: new skeleton from spec, then new
  name + lore from the LLM. Preserves entity_id and entity_type.

* ``generate_entity`` — brand-new entity appended to the given map.

All three functions mutate the bible in place *and* return the resulting
``EntityLore`` so callers can inspect or persist immediately.
"""

from __future__ import annotations

import json
import logging
import random

from canon.bible.models import Bible, EntityLore, GenerationTrail
from canon.llm.client import LLMClient
from canon.llm.prompts import PromptSet
from canon.pipeline.retry import retry_with_feedback
from canon.skeleton.core import SkeletonSpec, roll_skeleton

logger = logging.getLogger(__name__)


def reroll_entity_flavor(
    bible: Bible,
    map_id: str,
    entity_id: str,
    llm: LLMClient,
    prompts: PromptSet,
    *,
    max_retries: int = 3,
) -> EntityLore:
    """Re-generate name + lore for an existing entity.

    Preserves the entity's skeleton — only flavor text changes. Used when
    the user wants a different name/lore for the same mechanical roll.

    Returns the updated EntityLore (also mutated in-place on the bible).
    Raises KeyError if the entity doesn't exist.
    """
    existing = bible.get_entity(map_id, entity_id)
    skeleton = existing.skeleton or {}
    context = bible.get_cumulative_context(map_id)

    name, lore, trail = _generate_name_and_lore(
        llm, prompts, entity_type=existing.entity_type,
        context=context, skeleton=skeleton, max_retries=max_retries,
        label=f"reroll:{entity_id}",
    )

    bible.update_entity(map_id, entity_id, name=name, lore=lore, generation_trail=trail)
    return bible.get_entity(map_id, entity_id)


def regenerate_entity(
    bible: Bible,
    map_id: str,
    entity_id: str,
    llm: LLMClient,
    prompts: PromptSet,
    spec: SkeletonSpec,
    rng: random.Random | None = None,
    *,
    max_retries: int = 3,
) -> EntityLore:
    """Regenerate an entity from scratch — new skeleton + new flavor.

    Uses *spec* to roll a new skeleton, then runs LLM for name + lore.
    The entity's id and entity_type are preserved; everything else changes.

    Raises KeyError if the entity doesn't exist.
    """
    existing = bible.get_entity(map_id, entity_id)
    if rng is None:
        rng = random.Random()
    skeleton = roll_skeleton(spec, rng)
    context = bible.get_cumulative_context(map_id)

    name, lore, trail = _generate_name_and_lore(
        llm, prompts, entity_type=existing.entity_type,
        context=context, skeleton=skeleton, max_retries=max_retries,
        label=f"regen:{entity_id}",
    )

    bible.update_entity(
        map_id, entity_id,
        name=name, lore=lore, skeleton=skeleton, generation_trail=trail,
    )
    return bible.get_entity(map_id, entity_id)


def generate_entity(
    bible: Bible,
    map_id: str,
    entity_type: str,
    llm: LLMClient,
    prompts: PromptSet,
    spec: SkeletonSpec,
    rng: random.Random | None = None,
    *,
    max_retries: int = 3,
    entity_id: str | None = None,
) -> EntityLore:
    """Generate a brand-new entity and add it to the given map.

    Uses *spec* to roll a skeleton, then LLM for name + lore. The new entity
    is appended to map.entities and returned.

    If entity_id is omitted, one is auto-allocated using the same scheme as
    EntityPhase: f"{entity_type}_{count_so_far:04d}".

    Raises KeyError if the map doesn't exist.
    """
    if rng is None:
        rng = random.Random()
    if map_id not in bible.maps:
        raise KeyError(f"Map {map_id!r} not in bible")

    skeleton = roll_skeleton(spec, rng)
    context = bible.get_cumulative_context(map_id)

    if entity_id is None:
        existing_count = sum(
            1 for m in bible.maps.values()
            for e in m.entities
            if e.entity_type == entity_type
        )
        entity_id = f"{entity_type}_{existing_count:04d}"

    name, lore, trail = _generate_name_and_lore(
        llm, prompts, entity_type=entity_type,
        context=context, skeleton=skeleton, max_retries=max_retries,
        label=f"gen:{entity_id}",
    )

    entity = EntityLore(
        entity_id=entity_id,
        entity_type=entity_type,
        name=name,
        map_id=map_id,
        lore=lore,
        skeleton=skeleton,
        generation_trail=trail,
    )
    bible.add_entity(map_id, entity)
    return entity


def _generate_name_and_lore(
    llm: LLMClient,
    prompts: PromptSet,
    *,
    entity_type: str,
    context: str,
    skeleton: dict,
    max_retries: int,
    label: str,
) -> tuple[str, str, GenerationTrail]:
    """Internal helper: run the entity_generation prompt with retry, parse,
    return (name, lore, trail).

    Mirrors EntityPhase's per-entity logic so behavior is consistent.

    On LLM JSON-parse failures, ``retry_with_feedback`` exhausts retries and
    returns the fallback value — this function never raises for generation or
    parse errors. Only ``KeyError`` from the caller (entity/map not found)
    propagates out of the public functions above.
    """
    captured: dict[str, str] = {"prompt": "", "response": ""}

    def generate(feedback: list[str] | None = None) -> str:
        if feedback:
            request = prompts.entity_generation_with_feedback(
                entity_type=entity_type, context=context,
                skeleton=skeleton, feedback=feedback,
            )
        else:
            request = prompts.entity_generation(
                entity_type=entity_type, context=context, skeleton=skeleton,
            )
        captured["prompt"] = request.user_message
        response = llm.generate(request)
        captured["response"] = response
        return response

    def validate(content: str) -> tuple[bool, list[str]]:
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            return False, [f"Not valid JSON: {e}"]
        if not isinstance(data, dict):
            return False, ["Response must be JSON object."]
        if not data.get("name"):
            return False, ["Missing 'name'."]
        return True, []

    fallback = json.dumps({"name": entity_type.replace("_", " ").title(), "lore": ""})
    result = retry_with_feedback(
        generate_fn=generate, validate_fn=validate,
        fallback=fallback, max_retries=max_retries, label=label,
    )

    try:
        data = json.loads(result)
    except json.JSONDecodeError:
        data = {"name": "Untitled", "lore": ""}

    trail = GenerationTrail(
        prompt=captured["prompt"],
        response=captured["response"],
        validation_history=[],  # TODO(v0.2): record per-attempt validation history
        retry_count=0,
        cost=getattr(getattr(llm, "backend", None), "last_cost", None),
        model=getattr(getattr(llm, "backend", None), "model", None),
    )
    return data.get("name", "Untitled"), data.get("lore", ""), trail
