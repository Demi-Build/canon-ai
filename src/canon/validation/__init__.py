"""Validation framework — checkers, validators, and coherence utilities."""

from canon.validation.checker import BaseChecker, CheckResult
from canon.validation.coherence import check_references, detect_cycles
from canon.validation.validator import (
    BaseValidator,
    ValidationReport,
    ValidationResult,
)

__all__ = [
    "BaseChecker",
    "CheckResult",
    "BaseValidator",
    "ValidationResult",
    "ValidationReport",
    "check_references",
    "detect_cycles",
]
