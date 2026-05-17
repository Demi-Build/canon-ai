"""Validation phase — runs the 3-stage validation on the current bible state."""

from __future__ import annotations

import logging
from typing import Any

from canon.bible.models import BibleMetadata
from canon.validation.checker import BaseChecker
from canon.validation.validator import BaseValidator, ValidationReport, ValidationResult

logger = logging.getLogger(__name__)


class ValidationPhase:
    """Run the 3-stage validation on the current bible state.

    Stage 1: Per-entity checkers (structural correctness)
    Stage 2: Cross-entity validators (reference integrity)
    Stage 3: Results accumulated into a ValidationReport

    The report is stored in ``ctx.artifacts["validation_report"]``.

    Checkers and validators are pulled from ``ctx.checkers`` and
    ``ctx.validators``. If none are registered, the phase logs a
    warning and produces an empty report.
    """

    name: str = "validation"

    def run(self, ctx: Any) -> None:
        report = validate_bible(
            bible=ctx.bible,
            checkers=ctx.checkers,
            validators=ctx.validators,
        )
        report.bible_seed = getattr(ctx.bible, "seed", "")
        ctx.artifacts["validation_report"] = report

        if not isinstance(getattr(ctx.bible, "metadata", None), BibleMetadata):
            ctx.bible.metadata = BibleMetadata()
        ctx.bible.metadata.phases_run.append(self.name)

        if report.passed:
            logger.info(
                "Validation passed: %d checks, %d warnings.",
                len(report.results),
                report.minor_warnings,
            )
        else:
            logger.warning(
                "Validation failed: %d errors, %d warnings.",
                report.critical_failures,
                report.minor_warnings,
            )


def validate_bible(
    bible: Any,
    checkers: list[BaseChecker] | None = None,
    validators: list[BaseValidator] | None = None,
) -> ValidationReport:
    """Run all checkers and validators against a bible.

    Standalone function usable outside the pipeline (e.g., from cradle CLI).

    Stage 1: For each checker, find entities matching its ``entity_type``
    and run ``check()``. Failed checks are recorded as errors.

    Stage 2: For each validator, run ``validate()`` with the bible as
    context. Failed validations are recorded at their stated severity.

    Returns a ValidationReport accumulating all findings.
    """
    report = ValidationReport()
    checkers = checkers or []
    validators = validators or []

    if not checkers and not validators:
        logger.warning("No checkers or validators registered; validation is a no-op.")
        return report

    entities = _collect_entities(bible)

    for checker in checkers:
        matching = [e for e in entities if e.get("entity_type") == checker.entity_type]
        for entity in matching:
            result = checker.check(entity, context={"bible": bible})
            if not result.passed:
                report.add_error(
                    "; ".join(result.issues),
                    entity_id=entity.get("entity_id", entity.get("id", "")),
                    phase=f"checker:{checker.entity_type}",
                )
            else:
                report.add_result(
                    ValidationResult(
                        passed=True,
                        severity="info",
                        entity_id=entity.get("entity_id", entity.get("id", "")),
                    )
                )

    for validator in validators:
        result = validator.validate(entities, context={"bible": bible})
        report.add_result(result)

    return report


def _collect_entities(bible: Any) -> list[dict]:
    """Extract all entities from a bible into a flat list of dicts.

    Works with both canon's Bible model and plain dicts.
    """
    entities: list[dict] = []

    maps = {}
    if hasattr(bible, "maps"):
        maps = bible.maps
    elif hasattr(bible, "rooms"):
        maps = bible.rooms
    elif isinstance(bible, dict):
        maps = bible.get("maps", bible.get("rooms", {}))

    for map_id, map_data in maps.items():
        map_entities = []

        if hasattr(map_data, "entities"):
            map_entities = map_data.entities
        elif isinstance(map_data, dict):
            map_entities = map_data.get("entities", [])
        else:
            for attr in ("npcs", "items", "monsters"):
                items = getattr(map_data, attr, None)
                if items is None and isinstance(map_data, dict):
                    items = map_data.get(attr, [])
                if items:
                    for item in items:
                        if hasattr(item, "model_dump"):
                            d = item.model_dump()
                        elif isinstance(item, dict):
                            d = item
                        else:
                            continue
                        d.setdefault("map_id", map_id)
                        entities.append(d)

        for entity in map_entities:
            if hasattr(entity, "model_dump"):
                d = entity.model_dump()
            elif isinstance(entity, dict):
                d = entity
            else:
                continue
            d.setdefault("map_id", map_id)
            entities.append(d)

    return entities
