"""Pipeline framework — phase protocol, context, runner, and built-in phases."""

from canon.pipeline.retry import retry_with_feedback
from canon.pipeline.runner import Phase, PipelineContext, run_pipeline
from canon.pipeline.stats import GenerationStats

__all__ = [
    "Phase",
    "PipelineContext",
    "run_pipeline",
    "retry_with_feedback",
    "GenerationStats",
]
