"""Per-entity structural validation (stage 1 of the 3-stage pipeline).

Checkers answer: "Is this entity well-formed?" — required fields present,
values in valid ranges, formats correct. They do NOT check cross-entity
references; that's the validator's job.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CheckResult:
    """Outcome of a single check pass."""

    passed: bool
    issues: list[str] = field(default_factory=list)
    data: Any = None  # Optionally return corrected data


class BaseChecker(ABC):
    """Abstract checker for per-entity structural validation.

    Subclass and set ``entity_type`` to the type string this checker
    handles (e.g. "weapon", "character", "tower").
    """

    @property
    @abstractmethod
    def entity_type(self) -> str:
        """The entity_type string this checker applies to."""
        ...

    @abstractmethod
    def check(self, data: Any, context: dict | None = None) -> CheckResult:
        """Check *data* and return a CheckResult."""
        ...
