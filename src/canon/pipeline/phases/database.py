"""DatabasePhase — generic, configurable database-style entity generator.

Replaces the v0.1 EntityPhase for typed, file-persisting entity generation.
A pipeline composes one DatabasePhase per entity type:

    DatabasePhase(npc_spec), DatabasePhase(item_spec), DatabasePhase(monster_spec)

Each DatabasePhase writes one file to ``ctx.config.output_dir``.

EntityPhase remains as a deprecated compatibility shim at the bottom of this
module; it will be removed in Wave 4.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from canon.llm.parsing import extract_json_object
from canon.pipeline.retry import default_token_escalation, retry_with_feedback
from canon.skeleton.core import SkeletonSpec, roll_skeleton

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Bible-population helper
# ---------------------------------------------------------------------------


def _extract_tags(entity_dict: dict) -> list[str]:
    """Best-effort tag extraction from a parsed entity dict."""
    tags = entity_dict.get("tags")
    if isinstance(tags, list):
        return [str(t) for t in tags]
    category = entity_dict.get("category") or entity_dict.get("type")
    return [str(category)] if category else []


# ---------------------------------------------------------------------------
# BuildContext — context bundle passed to each parser invocation
# ---------------------------------------------------------------------------


class BuildContext(BaseModel):
    """Context bundle passed to each parser invocation.

    Carries everything the parser needs to construct a typed entity from the
    rolled skeleton + LLM response without reaching into global state.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    map_id: str | None
    map_obj: Any  # Map | None
    skeleton: dict
    llm_response: dict  # parsed LLM JSON
    entity_index: int  # 0-based within this DatabasePhase run, this map
    allocated_id: int | str | None  # from IDAllocator; None if id_prefix not set
    bible: Any  # Bible


# Parser signature: receives a BuildContext, returns a typed entity or dict.
ParserFn = Callable[[BuildContext], "BaseModel | dict"]


# ---------------------------------------------------------------------------
# DatabaseSpec — configuration for one database-style entity generator
# ---------------------------------------------------------------------------


class DatabaseSpec(BaseModel):
    """Configuration for a database-style entity generator.

    Defines how to: roll a skeleton, prompt the LLM, parse the response,
    merge skeleton+LLM into a typed entity, allocate IDs, and persist to a
    file with the right format.

    Fields:
        entity_type:       Human-readable type tag, e.g. ``"npc"``, ``"item"``.
        skeleton_spec:     Optional skeleton to roll before every LLM call.
                           When ``None``, ``BuildContext.skeleton`` will be ``{}``.
        prompt_method:     Name of a method on the PromptSet, e.g.
                           ``"entity_generation"``.
        parser:            ``(build_context: BuildContext) -> BaseModel | dict``.
                           Constructs the final entity from skeleton + LLM JSON.
        output_path:       Relative to ``ctx.config.output_dir``.
                           E.g. ``"npcs/npcs.json"`` → ``<output_dir>/npcs/npcs.json``.
        output_format:     ``"array"`` (flat JSON array) or ``"keyed_object"``
                           (``{"<id>": entity}`` dict keyed by ``id_prefix`` ID).
        id_prefix:         IDAllocator key, e.g. ``"npc"``.  When ``None``,
                           ``BuildContext.allocated_id`` is ``None``.
        per_map:           If ``True``, generate ``count`` entities per map.
                           If ``False``, generate ``count`` entities total.
        count:             Number of entities per map (or globally).
        cross_room_dedup:  Field names to deduplicate across maps.  When the
                           LLM returns a value that was already seen for a
                           listed field, DatabasePhase retries once with
                           feedback; if still a duplicate, accepts it.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    entity_type: str
    skeleton_spec: SkeletonSpec | None = None
    prompt_method: str  # method name on PromptSet, e.g. "entity_generation"
    parser: ParserFn  # (BuildContext) -> BaseModel | dict
    output_path: str  # relative to ctx.config.output_dir
    output_format: Literal["array", "keyed_object"] = "array"
    id_prefix: str | None = None  # IDAllocator key
    per_map: bool = True
    # TODO(v0.3): Callable[[Map], int] for dynamic per-map counts
    count: int = 3
    cross_room_dedup: list[str] = Field(default_factory=list)
    # Extra kwargs passed to the prompt method beyond (entity_type, context,
    # skeleton). Use this when prompt_method requires typed args like
    # role="npc", item_category="weapon", level=2, event_type="combat",
    # quest_type="escort". _call_prompt drops kwargs the method doesn't accept,
    # so passing extra keys is harmless.
    prompt_kwargs: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# DatabasePhase — the engine
# ---------------------------------------------------------------------------


class DatabasePhase:
    """Generic database engine.

    Configured by DatabaseSpec; emits one typed file per run.  Replaces
    v0.1's EntityPhase.  Composable: a pipeline composes
    ``[DatabasePhase(npc_spec), DatabasePhase(item_spec), ...]``.

    Key behaviours:

    - Resolves ``output_path`` relative to ``ctx.config.output_dir``.
    - Rolls a skeleton from ``spec.skeleton_spec`` (if present) before each
      LLM call and passes it to the parser via ``BuildContext.skeleton``.
    - Allocates an integer ID from ``ctx.id_allocator`` when
      ``spec.id_prefix`` is set; otherwise ``BuildContext.allocated_id`` is
      ``None``.
    - Applies ``cross_room_dedup``: tracks seen field values across all maps;
      when the LLM returns a duplicate, retries once with feedback; if still a
      duplicate, accepts (no infinite loop).
    - Writes the file via ``canon.persistence`` writers at the end of the run.
    - Appends ``f"db:{entity_type}"`` to ``ctx.bible.metadata.phases_run``.
    """

    def __init__(self, spec: DatabaseSpec) -> None:
        self.spec = spec
        self.name = f"db:{spec.entity_type}"

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self, ctx: Any) -> None:
        """Execute the phase against the shared pipeline context."""
        spec = self.spec
        max_retries = getattr(ctx.config, "max_retries", 3)

        # dedup tracking across all maps
        seen: dict[str, set] = {field: set() for field in spec.cross_room_dedup}
        entities: list[Any] = []

        if spec.per_map:
            for map_id, map_obj in ctx.bible.maps.items():
                for i in range(spec.count):
                    entity = self._generate_one(
                        ctx,
                        map_id=map_id,
                        map_obj=map_obj,
                        index=i,
                        max_retries=max_retries,
                        seen=seen,
                    )
                    if entity is not None:
                        entities.append(entity)
        else:
            for i in range(spec.count):
                entity = self._generate_one(
                    ctx,
                    map_id=None,
                    map_obj=None,
                    index=i,
                    max_retries=max_retries,
                    seen=seen,
                )
                if entity is not None:
                    entities.append(entity)

        # Persist to disk
        self._write(ctx, entities)

        # Record in bible metadata
        from canon.bible.models import BibleMetadata  # avoid circular at module level

        if not isinstance(ctx.bible.metadata, BibleMetadata):
            ctx.bible.metadata = BibleMetadata()
        ctx.bible.metadata.phases_run.append(self.name)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _generate_one(
        self,
        ctx: Any,
        *,
        map_id: str | None,
        map_obj: Any,
        index: int,
        max_retries: int,
        seen: dict[str, set],
    ) -> Any | None:
        """Generate a single entity, applying cross-room dedup."""
        spec = self.spec

        # Roll skeleton (empty dict when no spec). Thread generation-position
        # context (room_level + map_id) so specs can scale deterministically by
        # where/when they're generated — canon's keystone for level scaling.
        # Core just forwards these; the pack's specs decide what to do with them.
        roll_context = {
            "room_level": (getattr(map_obj, "level", None) if map_obj is not None else None),
            "map_id": map_id,
        }
        skeleton = (
            roll_skeleton(spec.skeleton_spec, ctx.rng, context=roll_context)
            if spec.skeleton_spec is not None
            else {}
        )

        # Build cumulative context for the LLM
        if map_id is not None and hasattr(ctx.bible, "get_cumulative_context"):
            context_text = ctx.bible.get_cumulative_context(map_id)
        elif hasattr(ctx.bible, "get_context"):
            context_text = ctx.bible.get_context("")
        else:
            context_text = ""

        # Mutable capture for prompt/response surviving across retries
        captured: dict[str, str] = {"prompt": "", "response": ""}

        def generate(
            feedback: list[str] | None = None,
            max_tokens: int | None = None,
        ) -> str:
            prompt_fn = getattr(ctx.prompts, spec.prompt_method)
            # Build kwargs for the prompt method — entity_generation signature:
            #   entity_generation(entity_type, context, skeleton)
            # Other methods may have different signatures; parsers that need
            # something different should use a custom prompt_method.
            base_kwargs = {
                "entity_type": spec.entity_type,
                "context": context_text,
                "skeleton": skeleton,
                **spec.prompt_kwargs,
            }
            if feedback:
                feedback_method = spec.prompt_method + "_with_feedback"
                if hasattr(ctx.prompts, feedback_method):
                    prompt_fn_fb = getattr(ctx.prompts, feedback_method)
                    request = _call_prompt(prompt_fn_fb, **base_kwargs, feedback=feedback)
                else:
                    request = _call_prompt(prompt_fn, **base_kwargs)
            else:
                request = _call_prompt(prompt_fn, **base_kwargs)
            if max_tokens is not None:
                request.max_tokens = max_tokens
            captured["prompt"] = request.user_message
            response = ctx.llm.generate(request, phase=self.name)
            captured["response"] = response
            return response

        def validate(content: str) -> tuple[bool, list[str]]:
            # Real models wrap JSON in markdown fences / add a preamble;
            # extract_json_object tolerates that (see canon.llm.parsing).
            if extract_json_object(content) is None:
                return False, [
                    "Response did not contain a JSON object. Return ONLY a JSON "
                    "object, with no prose or markdown code fences."
                ]
            return True, []

        fallback = json.dumps({"name": f"{spec.entity_type} {index}"})

        result = retry_with_feedback(
            generate_fn=generate,
            validate_fn=validate,
            fallback=fallback,
            max_retries=max_retries,
            label=self.name,
            token_escalation=default_token_escalation,
        )

        llm_response = extract_json_object(result) or {
            "name": f"{spec.entity_type} {index}"
        }

        # Allocate ID from IDAllocator if id_prefix is configured
        allocated_id: int | str | None = None
        if spec.id_prefix is not None:
            id_alloc = getattr(ctx, "id_allocator", None)
            if id_alloc is not None:
                allocated_id = id_alloc.next(spec.id_prefix)

        # Build context bundle for the parser
        build_ctx = BuildContext(
            map_id=map_id,
            map_obj=map_obj,
            skeleton=skeleton,
            llm_response=llm_response,
            entity_index=index,
            allocated_id=allocated_id,
            bible=ctx.bible,
        )

        # Parse into typed entity
        try:
            entity = spec.parser(build_ctx)
        except Exception as exc:
            logger.warning(
                "[%s] parser raised %s for entity %d (map=%s); skipping.",
                self.name, exc, index, map_id,
            )
            return None

        # Cross-room dedup: check each tracked field
        if spec.cross_room_dedup:
            entity_dict = _to_dict(entity)
            dup_fields: list[str] = []
            for field_name in spec.cross_room_dedup:
                value = entity_dict.get(field_name)
                if value is not None and value in seen[field_name]:
                    dup_fields.append(field_name)

            if dup_fields:
                # Retry once with dedup feedback
                dup_feedback = [
                    f"Field '{f}' value {entity_dict.get(f)!r} already used; pick a different one."
                    for f in dup_fields
                ]
                retry_result = retry_with_feedback(
                    generate_fn=generate,
                    validate_fn=validate,
                    fallback=result,  # fall back to original if retry also fails
                    max_retries=1,
                    label=f"{self.name}:dedup",
                )
                retry_response = extract_json_object(retry_result) or llm_response

                retry_build_ctx = BuildContext(
                    map_id=map_id,
                    map_obj=map_obj,
                    skeleton=skeleton,
                    llm_response=retry_response,
                    entity_index=index,
                    allocated_id=allocated_id,
                    bible=ctx.bible,
                )
                try:
                    entity = spec.parser(retry_build_ctx)
                except Exception:
                    pass  # keep original entity on parser error
                entity_dict = _to_dict(entity)
                # Suppress unused-variable warning on dup_feedback; it was
                # built for the log context and future feedback plumbing.
                _ = dup_feedback

            # Update seen sets (whether dedup happened or not)
            for field_name in spec.cross_room_dedup:
                value = entity_dict.get(field_name)
                if value is not None:
                    seen[field_name].add(value)

        # ------------------------------------------------------------------
        # Bible population — mirrors MazeWorld's bible.add_npc/item/monster
        # pattern.  After each entity is parsed, append an EntityLore stub
        # to bible.maps[map_id].entities so downstream phases (parse_event,
        # parse_quest, ManifestPhase) can find entities by type via
        # ctx.map_obj.entities.
        # ------------------------------------------------------------------
        if map_id is not None and map_id in ctx.bible.maps:
            from canon.bible.models import EntityLore  # avoid circular at top
            entity_dict_for_stub = _to_dict(entity)
            stub = EntityLore(
                entity_id=(
                    str(allocated_id)
                    if allocated_id is not None
                    else f"{spec.entity_type}_{index}"
                ),
                entity_type=spec.entity_type,
                name=entity_dict_for_stub.get("name") or f"{spec.entity_type}_{index}",
                map_id=map_id,
                lore=(
                    entity_dict_for_stub.get("lore")
                    or entity_dict_for_stub.get("desc")
                    or entity_dict_for_stub.get("backstory")
                    or ""
                ),
                tags=_extract_tags(entity_dict_for_stub),
            )
            ctx.bible.maps[map_id].entities.append(stub)
        # Global entities (map_id is None) — skipped for v0.2; mazeworld is
        # per-map only.

        return entity

    def _write(self, ctx: Any, entities: list[Any]) -> None:
        """Persist the collected entities via the context's output adapter."""
        if self.spec.output_format == "keyed_object":
            ctx.adapter.write_json_keyed(self.spec.output_path, entities, key_field="id")
        else:
            ctx.adapter.write_json_array(self.spec.output_path, entities)


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def _to_dict(entity: Any) -> dict:
    """Convert an entity (BaseModel or dict) to a plain dict."""
    if hasattr(entity, "model_dump"):
        return entity.model_dump(mode="json")
    if hasattr(entity, "dict"):
        return entity.dict()
    if isinstance(entity, dict):
        return entity
    return {}


def _call_prompt(fn: Callable, **kwargs: Any) -> Any:
    """Call a prompt method with as many kwargs as it accepts.

    Gracefully drops kwargs the method doesn't declare so DatabasePhase can
    call any PromptSet method without knowing its exact signature at
    construction time.
    """
    import inspect

    sig = inspect.signature(fn)
    accepted = set(sig.parameters)
    filtered = {k: v for k, v in kwargs.items() if k in accepted}
    return fn(**filtered)


# ---------------------------------------------------------------------------
# Deprecated EntityPhase compatibility re-export
# ---------------------------------------------------------------------------
#
# EntityPhase is preserved in ``entity.py`` so that existing imports work
# without modification.  Wave 4 will remove it.  The re-export here lets
# users do ``from canon.pipeline.phases.database import EntityPhase`` as well
# as the existing ``from canon.pipeline.phases.entity import EntityPhase``.
#
# NOTE: entity.py is NOT deleted until Wave 4.

from canon.pipeline.phases.entity import EntityPhase  # noqa: F401, E402
