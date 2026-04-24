"""Generate-validate-retry loop with feedback.

The core pattern: generate content, validate it, and on failure retry
with the failure reasons fed back to the generator.
"""

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

MAX_RETRIES = 3


def retry_with_feedback(
    generate_fn: Callable[..., Any],
    validate_fn: Callable[[Any], tuple[bool, list[str]]],
    fallback: Any,
    max_retries: int = MAX_RETRIES,
    label: str = "content",
) -> Any:
    """Generate content with retry-on-validation-failure.

    1. Call *generate_fn()* to produce content.
    2. Call *validate_fn(content)* -> (passed, reasons).
    3. On failure, retry up to *max_retries* times, passing failure
       reasons back to the generator via *generate_fn(feedback=reasons)*.
    4. On exhaustion, return *fallback*.
    """
    feedback: list[str] | None = None

    for attempt in range(1, max_retries + 1):
        try:
            content = generate_fn(feedback=feedback) if feedback else generate_fn()
        except Exception as e:
            logger.warning("[%s] attempt %d generation error: %s", label, attempt, e)
            feedback = [str(e)]
            continue

        passed, reasons = validate_fn(content)
        if passed:
            logger.info("[%s] passed validation on attempt %d.", label, attempt)
            return content

        logger.warning("[%s] attempt %d failed validation: %s", label, attempt, reasons)
        feedback = reasons

    logger.warning("[%s] exhausted %d retries, using fallback.", label, max_retries)
    return fallback
