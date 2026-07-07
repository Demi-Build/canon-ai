"""Generate-validate-retry loop with feedback."""

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
INITIAL_MAX_TOKENS = 1024
TOKEN_CAP = 8192


def _is_non_retryable(exc: Exception) -> bool:
    """Auth/permission failures never succeed on retry.

    Without this, a bad API key burns the full retry budget on every
    pipeline step (3 doomed calls x N steps) and produces an all-fallback
    output tree. Duck-typed so core never imports backend SDKs: provider
    errors carry ``status_code`` (401/403) and conventional class names.
    """
    if getattr(exc, "status_code", None) in (401, 403):
        return True
    name = type(exc).__name__
    return "Authentication" in name or "PermissionDenied" in name


def default_token_escalation(attempt: int, prev_max_tokens: int) -> int:
    """Bump max_tokens by 1.5x per attempt, capped at TOKEN_CAP.

    Used when retry_with_feedback's ``token_escalation`` arg is left as None
    and the caller wants the built-in escalation strategy.

    Args:
        attempt: Current attempt number (1-based). Not used by this strategy
            but available for custom escalation functions.
        prev_max_tokens: The max_tokens value used on the previous attempt.

    Returns:
        The next attempt's max_tokens, capped at ``TOKEN_CAP`` (8192).
    """
    return min(int(prev_max_tokens * 1.5), TOKEN_CAP)


def retry_with_feedback(
    generate_fn: Callable[..., Any],
    validate_fn: Callable[[Any], tuple[bool, list[str]]],
    fallback: Any,
    max_retries: int = MAX_RETRIES,
    label: str = "content",
    token_escalation: Callable[[int, int], int] | None = None,
    initial_max_tokens: int = INITIAL_MAX_TOKENS,
) -> Any:
    """Generate-validate-retry loop with optional token escalation.

    1. Call *generate_fn()* to produce content.
    2. Call *validate_fn(content)* -> ``(passed, reasons)``.
    3. On failure, retry up to *max_retries* times, passing failure
       reasons back to the generator via ``generate_fn(feedback=reasons)``.
    4. On exhaustion, return *fallback*.

    Token escalation (opt-in):
        If ``token_escalation`` is not ``None``, it is called as
        ``token_escalation(attempt, prev_max_tokens)`` on each retry.
        The result is passed to the generator as ``max_tokens=...`` kwarg.
        Generators that don't accept ``max_tokens`` are unaffected — the
        ``_call_generate`` helper strips kwargs the function doesn't declare.

    Backward compatibility:
        ``token_escalation=None`` (the default) produces identical behavior
        to the v0.1 implementation. Existing call sites that don't pass
        ``token_escalation`` see no change.

    Args:
        generate_fn: Zero-or-more-kwarg callable that produces content.
            May accept ``feedback: list[str] | None`` and/or
            ``max_tokens: int``. Unknown kwargs are silently dropped.
        validate_fn: ``(content) -> (passed, reasons)`` callable.
        fallback: Value returned after ``max_retries`` failures.
        max_retries: Maximum number of generation attempts.
        label: Name used in log messages (e.g. ``"npc"``).
        token_escalation: Optional ``(attempt, prev_max_tokens) -> int``
            callable. If provided, the result is forwarded to
            ``generate_fn`` as ``max_tokens``. ``default_token_escalation``
            provides a 1.5x-per-attempt strategy.
        initial_max_tokens: Starting value for ``max_tokens`` escalation.
            Only meaningful when ``token_escalation`` is not ``None``.

    Returns:
        The first validated content, or ``fallback`` after all retries fail.
    """
    feedback: list[str] | None = None
    current_tokens = initial_max_tokens

    for attempt in range(1, max_retries + 1):
        kwargs: dict = {}
        if feedback is not None:
            kwargs["feedback"] = feedback
        if token_escalation is not None:
            kwargs["max_tokens"] = current_tokens

        try:
            content = _call_generate(generate_fn, kwargs)
        except Exception as e:
            if _is_non_retryable(e):
                logger.error(
                    "[%s] non-retryable error (auth/permission) — aborting "
                    "instead of retrying: %s", label, e,
                )
                raise
            logger.warning("[%s] attempt %d generation error: %s", label, attempt, e)
            feedback = [str(e)]
            if token_escalation is not None:
                current_tokens = token_escalation(attempt, current_tokens)
            continue

        passed, reasons = validate_fn(content)
        if passed:
            logger.info("[%s] passed validation on attempt %d.", label, attempt)
            return content

        logger.warning("[%s] attempt %d failed validation: %s", label, attempt, reasons)
        feedback = reasons
        if token_escalation is not None:
            current_tokens = token_escalation(attempt, current_tokens)

    logger.warning("[%s] exhausted %d retries, using fallback.", label, max_retries)
    return fallback


def _call_generate(generate_fn: Callable, kwargs: dict) -> Any:
    """Call generate_fn, dropping kwargs the function doesn't accept.

    Handles four generator signatures:
    - Neither ``feedback`` nor ``max_tokens``
    - Only ``feedback``
    - Only ``max_tokens``
    - Both ``feedback`` and ``max_tokens`` (or ``**kwargs``)

    C-implemented callables that don't expose a signature are called with
    all kwargs and any resulting ``TypeError`` propagates naturally.
    """
    import inspect

    try:
        sig = inspect.signature(generate_fn)
        params = sig.parameters
        has_var_keyword = any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
        )
        if has_var_keyword:
            # **kwargs: forward everything
            accepted = kwargs
        else:
            accepted = {k: v for k, v in kwargs.items() if k in params}
    except (TypeError, ValueError):
        # Builtin or C-implemented callable — forward all kwargs and let
        # the caller decide; TypeError propagates if truly incompatible.
        accepted = kwargs

    return generate_fn(**accepted)
