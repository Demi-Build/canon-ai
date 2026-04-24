"""Cross-entity integrity validation (stage 2 of the 3-stage pipeline).

Validators answer: "Are the relationships between entities consistent?" —
quest references resolve, chains are acyclic, entity counts are reasonable.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


@dataclass
class ValidationResult:
    """Outcome of a single validation check."""

    passed: bool
    severity: Literal["info", "warning", "error"] = "info"
    issues: list[str] = field(default_factory=list)
    entity_id: str | None = None


class BaseValidator(ABC):
    """Abstract validator for cross-entity integrity checks."""

    @abstractmethod
    def validate(self, data: Any, context: dict | None = None) -> ValidationResult:
        """Validate *data* and return a ValidationResult."""
        ...


@dataclass
class ValidationReport:
    """Accumulates validation findings across all checkers and validators.

    Tracks counts by severity and stores individual results for inspection.
    """

    results: list[ValidationResult] = field(default_factory=list)
    bible_seed: str = ""
    canon_version: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def passed(self) -> bool:
        return not any(r.severity == "error" and not r.passed for r in self.results)

    @property
    def errors(self) -> list[ValidationResult]:
        return [r for r in self.results if r.severity == "error"]

    @property
    def warnings(self) -> list[ValidationResult]:
        return [r for r in self.results if r.severity == "warning"]

    @property
    def critical_failures(self) -> int:
        return len(self.errors)

    @property
    def minor_warnings(self) -> int:
        return len(self.warnings)

    def add_result(self, result: ValidationResult) -> None:
        self.results.append(result)

    def add_error(self, message: str, entity_id: str = "", phase: str = "") -> None:
        self.results.append(
            ValidationResult(
                passed=False,
                severity="error",
                issues=[f"[{phase}] {message}" if phase else message],
                entity_id=entity_id or None,
            )
        )

    def add_warning(self, message: str, entity_id: str = "", phase: str = "") -> None:
        self.results.append(
            ValidationResult(
                passed=True,
                severity="warning",
                issues=[f"[{phase}] {message}" if phase else message],
                entity_id=entity_id or None,
            )
        )

    def to_dict(self) -> dict:
        return {
            "status": "passed" if self.passed else "failed",
            "critical_failures": self.critical_failures,
            "minor_warnings": self.minor_warnings,
            "total_checks": len(self.results),
            "timestamp": self.timestamp,
            "bible_seed": self.bible_seed,
            "canon_version": self.canon_version,
            "details": [
                {
                    "passed": r.passed,
                    "severity": r.severity,
                    "issues": r.issues,
                    "entity_id": r.entity_id,
                }
                for r in self.results
            ],
        }
