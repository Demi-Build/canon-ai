"""Canon — coherence layer for AI-generated structured content."""

__version__ = "0.1.0"

from canon.pipeline.phases import ValidationPhase, validate_bible
from canon.pipeline.retry import retry_with_feedback
from canon.pipeline.runner import Phase, PipelineContext, run_phase, run_pipeline
from canon.pipeline.stats import GenerationStats
from canon.validation.checker import BaseChecker, CheckResult
from canon.validation.coherence import check_references, detect_cycles
from canon.validation.validator import (
    BaseValidator,
    ValidationReport,
    ValidationResult,
)

__all__ = [
    # Validation
    "BaseChecker",
    "CheckResult",
    "BaseValidator",
    "ValidationResult",
    "ValidationReport",
    "validate_bible",
    "check_references",
    "detect_cycles",
    # Pipeline
    "Phase",
    "PipelineContext",
    "run_pipeline",
    "run_phase",
    "retry_with_feedback",
    "GenerationStats",
    # Phases
    "ValidationPhase",
]
