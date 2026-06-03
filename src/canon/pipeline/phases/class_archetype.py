"""ClassPhase — generates ClassArchetype instances for games with class/job systems.

Named ``class_archetype.py`` to avoid shadowing the Python built-in ``class``
keyword if this file were named ``class.py``.

# TODO(v0.2): per-character archetype assignment strategy (LLM-driven affinity,
#             weighted by character lore) instead of random pick.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from canon.bible.models import Ability, BibleMetadata, ClassArchetype, Spell
from canon.llm.parsing import extract_json_object
from canon.persistence import write_array_db
from canon.pipeline.phases.spell_pool import generate_named_entries
from canon.pipeline.retry import default_token_escalation, retry_with_feedback
from canon.skeleton.core import SkeletonSpec, roll_skeleton
from canon.stats import fix_stats

logger = logging.getLogger(__name__)


@dataclass
class LoadoutGroup:
    """Spell- or ability-loadout definition for one archetype.

    ``skeleton_spec`` rolls the mechanical identity of each entry; ``starting``
    and ``pool`` are per-slot ``field_overrides`` dicts (one per spell/ability)
    that pin the mechanical fields for that slot (e.g. ``{"spell_type":
    "heal"}``). The LLM only supplies name + description. ``prompt_kwargs``
    themes the LLM call (e.g. archetype/element). All pack data — core holds no
    spell/ability vocabulary.
    """

    skeleton_spec: SkeletonSpec
    starting: list[dict] = field(default_factory=list)
    pool: list[dict] = field(default_factory=list)
    prompt_kwargs: dict = field(default_factory=dict)


@dataclass
class ClassLoadoutSpec:
    """Full per-archetype generation spec for ``ClassPhase`` loadout mode.

    Pure pack data: the archetype label, its stat template + role assignment +
    budget + role ranges (consumed by :func:`canon.stats.fix_stats`), and
    optional spell/ability :class:`LoadoutGroup`s. Core hardcodes none of the
    numbers, names, or vocab — this is the canonical "RPG class" shape a pack
    (or future user-edited template) fills in.
    """

    archetype: str
    stat_template: dict[str, int]
    stat_roles: dict[str, list[str]]
    stat_budget: int
    role_ranges: dict[str, tuple[int, int]]
    stat_names: list[str]
    name: str | None = None
    starting_weapon: str = ""
    spells: LoadoutGroup | None = None
    abilities: LoadoutGroup | None = None
    prompt_kwargs: dict = field(default_factory=dict)

DEFAULT_INSTANTIATE_ROLES = ("pc",)


def _coerce_abilities(raw: list) -> list[Ability]:
    """Coerce a list of raw LLM ability entries to typed Ability objects.

    Handles both dict entries (from well-formed LLM JSON) and string entries
    (legacy references like "shield_bash") by wrapping the latter as
    Ability(name=..., stat="STR") stubs.
    """
    result = []
    for item in raw or []:
        if isinstance(item, Ability):
            result.append(item)
        elif isinstance(item, dict):
            result.append(Ability(
                name=item.get("name", ""),
                description=item.get("description", ""),
                stat=item.get("stat", "STR"),
                stamina_cost=item.get("stamina_cost", 0),
            ))
        elif isinstance(item, str) and item.strip():
            result.append(Ability(name=item, stat="STR"))
    return result


def _coerce_spells(raw: list) -> list[Spell]:
    """Coerce a list of raw LLM spell entries to typed Spell objects.

    Handles both dict entries (from well-formed LLM JSON) and string entries
    (legacy references like "fireball") by wrapping the latter as
    Spell(name=..., spell_type="damage_single", element="fire", stat="INT") stubs.
    """
    result = []
    for item in raw or []:
        if isinstance(item, Spell):
            result.append(item)
        elif isinstance(item, dict):
            result.append(Spell(
                name=item.get("name", ""),
                spell_type=item.get("spell_type", "damage_single"),
                element=item.get("element", "fire"),
                stat=item.get("stat", "INT"),
                targets=item.get("targets", "single"),
                num_dice=item.get("num_dice", 1),
                die_sides=item.get("die_sides", 6),
                heal_amount=item.get("heal_amount", 0),
                buff_stat=item.get("buff_stat"),
                buff_value=item.get("buff_value", 0),
                buff_duration=item.get("buff_duration", 0),
                stamina_cost=item.get("stamina_cost", 0),
                description=item.get("description", ""),
            ))
        elif isinstance(item, str) and item.strip():
            result.append(Spell(name=item, spell_type="damage_single", element="fire", stat="INT"))
    return result


class ClassPhase:
    """Generates ClassArchetypes and (optionally) instantiates CharacterClass
    data for matching characters.

    This phase is for games with class/job/archetype systems. Skip for
    classless games. The phase is generic — it has no opinion about what
    archetypes mean.

    Behavior:
    - Calls ctx.prompts.class_archetype_generation() N times (N = archetype_count)
    - Each LLM response is parsed into a ClassArchetype and added to
      ctx.bible.class_archetypes (and ctx.archetypes for in-pipeline use)
    - If instantiate_for_roles is non-empty, walks ctx.bible.characters and
      for each character whose role matches, picks an archetype and rolls a
      CharacterClass from the matching SkeletonSpec in ctx.schemas (if any)
    """

    name: str = "classes"

    def __init__(
        self,
        archetype_count: int = 4,
        instantiate_for_roles: tuple[str, ...] | list[str] = DEFAULT_INSTANTIATE_ROLES,
        category: str | None = None,
        loadout_specs: list[ClassLoadoutSpec] | None = None,
    ) -> None:
        self.archetype_count = archetype_count
        self.instantiate_for_roles = tuple(instantiate_for_roles)
        self.category = category
        # When provided, ClassPhase generates a full mechanical loadout (stats
        # fixed to budget + spells + abilities) per spec instead of lore-only
        # archetypes. archetype_count is ignored in this mode (one class per spec).
        self.loadout_specs = loadout_specs

    def run(self, ctx: Any) -> None:
        max_retries = getattr(ctx.config, "max_retries", 3)
        context_text = ctx.bible.get_context("")  # global story context, no map binding

        if self.loadout_specs:
            for spec in self.loadout_specs:
                archetype = self._generate_loadout(ctx, spec, context_text, max_retries)
                ctx.bible.class_archetypes[archetype.archetype_id] = archetype
                ctx.archetypes[archetype.archetype_id] = archetype
        else:
            for i in range(self.archetype_count):
                archetype = self._generate_one(ctx, context_text, max_retries, index=i)
                if archetype is None:
                    logger.warning("ClassPhase: archetype %d generation failed entirely", i)
                    continue
                ctx.bible.class_archetypes[archetype.archetype_id] = archetype
                ctx.archetypes[archetype.archetype_id] = archetype

        # Instantiate CharacterClass for matching characters, if requested
        if self.instantiate_for_roles and ctx.bible.class_archetypes:
            self._instantiate_for_characters(ctx)

        if not isinstance(ctx.bible.metadata, BibleMetadata):
            ctx.bible.metadata = BibleMetadata()
        ctx.bible.metadata.phases_run.append(self.name)
        logger.info(
            "ClassPhase produced %d archetypes; instantiated for roles=%s",
            len(ctx.bible.class_archetypes),
            self.instantiate_for_roles,
        )

        # --- v0.2 persistence: write data/classes/classes.json ---
        self._persist(ctx)

    def _persist(self, ctx: Any) -> None:
        """Write classes.json as a flat array (no archetype_id field — mazeworld positional)."""
        output_dir = Path(getattr(ctx.config, "output_dir", "."))
        output_paths = getattr(ctx.config, "output_paths", {})
        classes_path = output_dir / output_paths.get("classes", "classes/classes.json")

        classes_list = []
        for arch in ctx.bible.class_archetypes.values():
            entry = arch.model_dump(mode="json")
            # mazeworld's classes.json is positional — no id field.
            entry.pop("archetype_id", None)
            classes_list.append(entry)

        write_array_db(classes_path, classes_list)

    def _generate_one(
        self,
        ctx: Any,
        context_text: str,
        max_retries: int,
        index: int,
    ) -> ClassArchetype | None:
        captured: dict[str, str] = {"prompt": "", "response": ""}

        def generate(
            feedback: list[str] | None = None,
            max_tokens: int | None = None,
        ) -> str:
            if feedback:
                request = ctx.prompts.class_archetype_generation_with_feedback(
                    context=context_text,
                    category=self.category,
                    feedback=feedback,
                )
            else:
                request = ctx.prompts.class_archetype_generation(
                    context=context_text,
                    category=self.category,
                )
            if max_tokens is not None:
                request.max_tokens = max_tokens
            captured["prompt"] = request.user_message
            response = ctx.llm.generate(request, phase=self.name)
            captured["response"] = response
            return response

        def validate(content: str) -> tuple[bool, list[str]]:
            data = extract_json_object(content)
            if data is None:
                return False, [
                    "Response did not contain a JSON object. Return ONLY a JSON "
                    "object, with no prose or markdown code fences."
                ]
            issues: list[str] = []
            if not data.get("name"):
                issues.append("Missing 'name' field.")
            return len(issues) == 0, issues

        fallback = json.dumps({"name": f"archetype_{index}", "description": ""})

        result = retry_with_feedback(
            generate_fn=generate,
            validate_fn=validate,
            fallback=fallback,
            max_retries=max_retries,
            label=f"{self.name}:archetype_{index}",
            token_escalation=default_token_escalation,
        )

        data = extract_json_object(result)
        if data is None:
            return None

        archetype_id = data.get("archetype_id") or _slugify(
            data.get("name", f"archetype_{index}")
        )
        return ClassArchetype(
            archetype_id=archetype_id,
            archetype=data.get("archetype", ""),
            name=data.get("name", f"Archetype {index}"),
            description=data.get("description", ""),
            flavor_text=data.get("flavor_text"),
            starting_weapon=data.get("starting_weapon"),
            portrait_prompt=data.get("portrait_prompt"),
            portrait_path=data.get("portrait_path"),
            category=data.get("category", self.category),
            stat_template=data.get("stat_template") or {},
            stat_roles=data.get("stat_roles") or {},
            stat_budget=data.get("stat_budget"),
            role_tags=data.get("role_tags") or [],
            abilities=_coerce_abilities(data.get("abilities") or []),
            spells=_coerce_spells(data.get("spells") or []),
            ability_pool=_coerce_abilities(data.get("ability_pool") or []),
            spell_pool=_coerce_spells(data.get("spell_pool") or []),
            starting_equipment=data.get("starting_equipment") or [],
            lore=data.get("lore"),
        )

    def _generate_loadout(
        self,
        ctx: Any,
        spec: ClassLoadoutSpec,
        context_text: str,
        max_retries: int,
    ) -> ClassArchetype:
        """Generate a complete class from a ClassLoadoutSpec.

        Per archetype: (1) an LLM flavor call for name/description/lore themed
        to the archetype; (2) skeleton-rolled spells and abilities whose
        name/description come from a batched LLM call (mechanics from the
        skeleton); (3) stats fixed to the spec's budget within its role ranges,
        preserving the archetype's emphasis. Returns a fully-populated
        ClassArchetype — no pack-side backfill needed.
        """
        # 1. Flavor (name / description / lore), themed by archetype.
        flavor = self._generate_flavor(ctx, spec, context_text, max_retries)

        # 2. Spells + abilities: roll per-slot skeletons, batch-LLM the names.
        starting_spells, spell_pool = self._gen_group(ctx, spec.spells, f"{spec.archetype}:spell")
        starting_abil, abil_pool = self._gen_group(ctx, spec.abilities, f"{spec.archetype}:ability")

        # 3. Stats fixed to budget within role ranges (archetype shape kept).
        stats = fix_stats(
            spec.stat_template,
            spec.stat_roles,
            spec.role_ranges,
            spec.stat_budget,
            spec.stat_names,
        )

        name = flavor.get("name") or spec.name or spec.archetype.title()
        return ClassArchetype(
            archetype_id=spec.archetype,
            archetype=spec.archetype,
            name=name,
            description=flavor.get("description", ""),
            flavor_text=flavor.get("flavor_text") or flavor.get("description", ""),
            starting_weapon=spec.starting_weapon,
            portrait_prompt=flavor.get("portrait_prompt"),
            category=flavor.get("category", self.category),
            stat_template=stats,
            stat_roles=spec.stat_roles,
            stat_budget=spec.stat_budget,
            role_tags=flavor.get("role_tags") or [],
            abilities=_coerce_abilities(starting_abil),
            spells=_coerce_spells(starting_spells),
            ability_pool=_coerce_abilities(abil_pool),
            spell_pool=_coerce_spells(spell_pool),
            lore=flavor.get("lore"),
        )

    def _generate_flavor(
        self, ctx: Any, spec: ClassLoadoutSpec, context_text: str, max_retries: int
    ) -> dict:
        """One LLM call for the class's name/description/lore, themed by archetype."""
        # Pass the archetype as the category hint so the prompt is class-aware.
        category = spec.archetype

        def generate(feedback: list[str] | None = None, max_tokens: int | None = None) -> str:
            if feedback:
                request = ctx.prompts.class_archetype_generation_with_feedback(
                    context=context_text, category=category, feedback=feedback,
                )
            else:
                request = ctx.prompts.class_archetype_generation(
                    context=context_text, category=category,
                )
            if max_tokens is not None:
                request.max_tokens = max_tokens
            return ctx.llm.generate(request, phase=self.name)

        def validate(content: str) -> tuple[bool, list[str]]:
            data = extract_json_object(content)
            if data is None:
                return False, [
                    "Response did not contain a JSON object. Return ONLY a JSON "
                    "object, with no prose or markdown code fences."
                ]
            return (bool(data.get("name")), [] if data.get("name") else ["Missing 'name'."])

        result = retry_with_feedback(
            generate_fn=generate,
            validate_fn=validate,
            fallback=json.dumps({"name": spec.name or spec.archetype.title()}),
            max_retries=max_retries,
            label=f"{self.name}:{spec.archetype}",
            token_escalation=default_token_escalation,
        )
        return extract_json_object(result) or {}

    def _gen_group(
        self, ctx: Any, group: LoadoutGroup | None, label: str
    ) -> tuple[list[dict], list[dict]]:
        """Roll per-slot skeletons for a LoadoutGroup's starting + pool slots,
        batch-generate their names/descriptions, and return (starting, pool)
        dict lists. Empty when the archetype has no such group (e.g. a warrior
        has no spells)."""
        if group is None or (not group.starting and not group.pool):
            return [], []
        slots = list(group.starting) + list(group.pool)
        skeletons: list[dict] = []
        for slot in slots:
            skel = roll_skeleton(group.skeleton_spec, ctx.rng)
            skel.update(slot)  # per-slot overrides pin this entry's mechanics
            skeletons.append(skel)
        named = generate_named_entries(
            ctx, label=label, skeletons=skeletons, prompt_kwargs=group.prompt_kwargs,
        )
        n = len(group.starting)
        return named[:n], named[n:]

    def _instantiate_for_characters(self, ctx: Any) -> None:
        """Roll CharacterClass for each character whose role matches.

        Uses ctx.schemas[archetype_id] if a SkeletonSpec is registered for
        the archetype; otherwise builds a minimal CharacterClass with empty
        stats/abilities/spells. Users with class systems should register a
        SkeletonSpec keyed by archetype_id (or a generic 'character_class' key).
        """
        from canon.bible.models import CharacterClass

        if not ctx.bible.class_archetypes:
            return
        archetypes = list(ctx.bible.class_archetypes.values())
        for character in ctx.bible.characters:
            if character.role not in self.instantiate_for_roles:
                continue
            archetype = ctx.rng.choice(archetypes)
            spec = ctx.schemas.get(archetype.archetype_id) or ctx.schemas.get(
                "character_class"
            )
            if spec is not None:
                skeleton = roll_skeleton(spec, ctx.rng)
            else:
                skeleton = {}
            character.class_data = CharacterClass(
                archetype_id=archetype.archetype_id,
                stats=archetype.stat_template.copy() if archetype.stat_template else {},
                abilities=list(archetype.ability_pool),
                spells=list(archetype.spell_pool),
                equipment=list(archetype.starting_equipment),
                skeleton=skeleton,
            )


def _slugify(name: str) -> str:
    """Convert a name to a safe identifier string (ASCII alphanumeric + underscores)."""
    return (
        "".join(c if c.isalnum() else "_" for c in name).strip("_").lower() or "archetype"
    )
