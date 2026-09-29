"""Structured asset-generation failures — the ONE retryable taxonomy, extended
from chat to the paid image / music / SFX backends.

The chat backends already translate every provider exception into
``canon.llm.chat.ChatError`` (``message`` + ``retryable`` + ``status`` +
``request_id``), and the agent loop retries on ``retryable`` alone, never on
provider classes. The asset backends used to do the opposite: every
``generate_and_save_async`` was ``except Exception: return False`` — no log,
no stored reason — so a paid dungeon run that lost 26 of 73 assets reported
``ok: true`` and the reasons (a wrong Google key, two impossible SFX
durations, a rate-limit burst) were only ever recoverable by guessing.

:class:`AssetError` IS a ``ChatError`` (a subclass, not a second type): the
same ``retryable`` flag, the same ``status``, the same ``request_id``, plus
the exception class that caused it and the provider it came from. Anything
that already branches on ``getattr(exc, "retryable", False)`` — the agent
service, the eval runner — reads an asset failure the same way.

:func:`classify_exception` is the duck-typed twin of the chat backends'
``_translate_error``: the SDKs differ (fal raises ``FalClientHTTPError`` with
``status_code``; ElevenLabs an ``ApiError`` with ``status_code``; google-genai
an ``APIError`` with ``code`` + a gRPC-style ``status`` string), so the rule
reads whatever status the exception carries and falls back to the class
name, never importing an SDK. The policy is the chat one, verbatim:

- retryable: 408 / 409 / 425 / 429 / 5xx, timeouts, connection drops,
  ``RESOURCE_EXHAUSTED`` / ``UNAVAILABLE`` / ``DEADLINE_EXCEEDED``;
- not retryable: 401 / 403 / 400 / 404 / 422 and every other 4xx,
  ``PERMISSION_DENIED`` / ``UNAUTHENTICATED`` / ``INVALID_ARGUMENT``,
  validation errors (``ValueError`` / ``TypeError`` / ``KeyError`` — a
  request the provider will refuse identically next time), and the
  auth/permission names ``canon.pipeline.retry`` already refuses to retry.

An exception the rule cannot place is RETRYABLE: the owner's rule is "at
least three retries for a failed asset", so the unknown case gets them and
only a failure known to be permanent is spared the wait.

:func:`retry_call` / :func:`retry_call_async` are the retry loop itself —
``ASSET_RETRIES`` retries after the first attempt, ``ASSET_BACKOFF_SECONDS``
between them — used by the pipeline's ``AssetPhase`` and by the platformer's
art / audio phases, so both templates retry the same way. Both take a
``should_stop`` probe (:func:`cancel_hook` reads it off a context's
``StepLog``): once a ⏹ Stop is requested no further attempt is made — a
retry after Stop is a paid call the user asked not to make.

The ``hint`` on every record names the repair. The default is the
pipeline's ``asset generate --target missing``; a template whose verbs
address assets differently (the platformer's ``enemy:<id>`` / ``audio:<stage>``)
passes its own ``repair`` clause through ``record_asset_failure`` — a hint
must never name a command the pack refuses.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, TypeVar

from canon.llm.chat import ChatError
from canon.pipeline.retry import _is_non_retryable
from canon.pipeline.steplog import RunCancelled

logger = logging.getLogger(__name__)

__all__ = [
    "ASSET_BACKOFF_SECONDS",
    "ASSET_RETRIES",
    "DEFAULT_REPAIR",
    "AssetError",
    "cancel_hook",
    "classify_exception",
    "retry_call",
    "retry_call_async",
]

#: Retries AFTER the first attempt of one asset (the owner's floor: "at least
#: 3 retries for failed assets"). A retryable failure therefore gets up to
#: ``ASSET_RETRIES + 1`` calls; a non-retryable one exactly one.
ASSET_RETRIES = 3

#: Seconds to wait before retry 1, 2, 3 … (the last value repeats past the
#: end). Exponential so a rate-limit burst thins out instead of re-forming.
ASSET_BACKOFF_SECONDS: tuple[float, ...] = (1.0, 2.0, 4.0)

#: The repair clause a failure record names when its template gave no
#: other: the pipeline's own verb, which regenerates only what is absent.
DEFAULT_REPAIR = (
    "repair with `asset generate --target missing` (it regenerates only what is absent)"
)

#: Statuses a retry may clear (the chat backends' list, plus the three
#: "try again" 4xx codes the SDKs above emit).
_RETRYABLE_STATUSES = frozenset({408, 409, 425, 429})

#: gRPC-style status names google-genai puts on ``APIError.status``.
_RETRYABLE_GRPC = ("RESOURCE_EXHAUSTED", "UNAVAILABLE", "DEADLINE_EXCEEDED", "ABORTED")
_PERMANENT_GRPC = (
    "PERMISSION_DENIED", "UNAUTHENTICATED", "INVALID_ARGUMENT", "NOT_FOUND",
    "FAILED_PRECONDITION", "OUT_OF_RANGE", "UNIMPLEMENTED",
)

#: Class-name fragments, checked when no status is carried.
_RETRYABLE_NAMES = (
    "RateLimit", "Timeout", "Connection", "ServiceUnavailable", "InternalServer",
    "Overloaded", "Throttl", "TooManyRequests", "GatewayTimeout", "Network",
    "RemoteDisconnected", "IncompleteRead", "Unavailable",
)
_PERMANENT_NAMES = (
    "Authentication", "PermissionDenied", "Forbidden", "Unauthorized", "BadRequest",
    "NotFound", "Unprocessable", "Validation", "InvalidArgument", "UnsupportedFunction",
)

T = TypeVar("T")


class AssetError(ChatError):
    """A provider asset call failed — ``ChatError`` plus what an asset
    failure list needs to name the cause.

    Args:
        message: Human-readable failure text (the provider's, when it gave one).
        retryable: Whether a retry might clear it (see the module doc).
        status: HTTP status when the provider gave one.
        request_id: Provider request id when present — log it, never content.
        kind: The originating exception's class name (``"FalClientHTTPError"``).
        provider: The backend id the call went through (``"fal"``).
        repair: The repair clause the hint ends on — what the person runs to
            get this asset back. ``None`` reads ``DEFAULT_REPAIR``; a template
            whose verbs address assets by their own grammar passes its own.
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = True,
        status: int | None = None,
        request_id: str | None = None,
        kind: str = "",
        provider: str = "",
        repair: str | None = None,
    ) -> None:
        super().__init__(message, retryable=retryable, status=status, request_id=request_id)
        self.kind = kind
        self.provider = provider
        self.repair = repair
        #: How many calls were made before this became final (the retry loop
        #: stamps it; a failure that was never retried reads 1).
        self.attempts: int = 1

    @property
    def hint(self) -> str:
        """What the person can DO about it — computed from the classification,
        never from a document row (rule 5). The provider is data off the
        record; the repair clause names the fix the PACK's own verbs offer
        (``repair``, else the pipeline's ``DEFAULT_REPAIR``)."""
        who = self.provider or "the provider"
        repair = self.repair or DEFAULT_REPAIR
        if self.status in (401, 403) or _looks_like_auth(self):
            return (
                f"{who} refused the credential or the billing account: check the "
                f"{who} key and plan, then {repair}."
            )
        if self.status in (400, 404, 422) or self.kind in ("ValueError", "TypeError", "KeyError"):
            return (
                f"{who} rejected the request itself; the same call will fail again — "
                f"fix the entry (prompt, size or duration), then {repair}."
            )
        if self.retryable:
            return f"transient {who} failure after {self.attempts} attempt(s); {repair}."
        return f"{repair} once the {who} error is resolved."

    def to_record(self) -> dict[str, Any]:
        """The durable shape (``generation_stats.failures[]`` / the step log):
        plain JSON, no exception objects. ``error`` is the exception class —
        the record's own ``kind`` is the asset family (image / music / sfx)."""
        return {
            "error": self.kind,
            "provider": self.provider,
            "message": str(self),
            "status": self.status,
            "request_id": self.request_id,
            "retryable": self.retryable,
            "attempts": self.attempts,
            "hint": self.hint,
        }


def _looks_like_auth(err: AssetError) -> bool:
    text = f"{err.kind} {err}"
    return any(name in text for name in ("Authentication", "PermissionDenied", "PERMISSION_DENIED", "UNAUTHENTICATED"))


def _status_of(exc: BaseException) -> int | None:
    """Whatever HTTP status the exception carries, on any of the attribute
    names the shipped SDKs use (``status_code`` — fal, ElevenLabs, httpx via
    ``response``; ``code`` — google-genai; a numeric ``status``)."""
    for attr in ("status_code", "code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and 100 <= value < 600:
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    if isinstance(value, int) and 100 <= value < 600:
        return value
    return None


def _grpc_status_of(exc: BaseException) -> str:
    value = getattr(exc, "status", None)
    return value.upper() if isinstance(value, str) else ""


def classify_exception(exc: BaseException, *, provider: str = "") -> AssetError:
    """Map ANY exception onto an :class:`AssetError` (an ``AssetError`` passes
    through with its provider filled in). See the module doc for the rule."""
    if isinstance(exc, AssetError):
        if provider and not exc.provider:
            exc.provider = provider
        return exc
    if isinstance(exc, ChatError):
        return AssetError(
            str(exc), retryable=exc.retryable, status=exc.status,
            request_id=exc.request_id, kind=type(exc).__name__, provider=provider,
        )

    name = type(exc).__name__
    status = _status_of(exc)
    grpc = _grpc_status_of(exc)
    message = str(getattr(exc, "message", None) or exc) or name
    request_id = getattr(exc, "request_id", None)

    if _is_non_retryable(exc):
        retryable = False
    elif status is not None:
        retryable = status >= 500 or status in _RETRYABLE_STATUSES
    elif grpc and any(g in grpc for g in _PERMANENT_GRPC):
        retryable = False
    elif grpc and any(g in grpc for g in _RETRYABLE_GRPC):
        retryable = True
    elif isinstance(exc, (TimeoutError, ConnectionError, asyncio.TimeoutError)):
        retryable = True
    elif any(fragment in name for fragment in _PERMANENT_NAMES):
        retryable = False
    elif any(fragment in name for fragment in _RETRYABLE_NAMES):
        retryable = True
    elif isinstance(exc, (ValueError, TypeError, KeyError)):
        # A malformed request or an unrecognised response shape: the same
        # call fails the same way next time.
        retryable = False
    elif any(g in message.upper() for g in _PERMANENT_GRPC):
        retryable = False
    else:
        retryable = True
    return AssetError(
        message, retryable=retryable, status=status,
        request_id=str(request_id) if request_id else None, kind=name, provider=provider,
    )


def _backoff(retry_index: int, backoff: Sequence[float]) -> float:
    if not backoff:
        return 0.0
    return float(backoff[min(retry_index, len(backoff) - 1)])


def cancel_hook(ctx: Any) -> Callable[[], bool] | None:
    """The ``should_stop`` probe for a pipeline context: its ``StepLog``'s
    ``cancel_requested`` (the ONE cancel signal — the per-job cancel file
    the item boundary already checks), or ``None`` for a context without a
    step log, which is never stopped."""
    steplog = getattr(ctx, "steplog", None)
    probe = getattr(steplog, "cancel_requested", None)
    return probe if callable(probe) else None


def _give_up(
    err: AssetError, *, attempts: int, retries: int, label: str,
    should_stop: Callable[[], bool] | None,
) -> bool:
    """Whether the loop raises ``err`` now instead of retrying: a permanent
    failure, the retries spent, or a ⏹ Stop requested since the attempt
    began. Spending after Stop is the one thing a retry must never do — the
    job is recorded as failed and the next item boundary raises
    ``RunCancelled``."""
    if not err.retryable or attempts > retries:
        return True
    if should_stop is not None and should_stop():
        logger.warning(
            "%s: attempt %d/%d failed (%s%s) — stop requested, not retrying",
            label, attempts, retries + 1, err.kind, f" {err.status}" if err.status else "",
        )
        return True
    return False


def retry_call(
    fn: Callable[[], T],
    *,
    provider: str = "",
    label: str = "asset",
    retries: int | None = None,
    backoff: Sequence[float] | None = None,
    sleep: Callable[[float], Any] = time.sleep,
    should_stop: Callable[[], bool] | None = None,
) -> T:
    """Call ``fn`` until it returns; retry a RETRYABLE failure up to
    ``retries`` more times with ``backoff`` between calls; raise the final
    :class:`AssetError` (``attempts`` stamped) otherwise. A non-retryable
    failure raises after exactly one call — a 403 is never waited on — and
    so does any failure once ``should_stop()`` reads true (``cancel_hook``).
    ``None`` for ``retries`` / ``backoff`` reads the module constants at
    call time (so a test can pin them without rebinding defaults)."""
    retries = ASSET_RETRIES if retries is None else retries
    backoff = ASSET_BACKOFF_SECONDS if backoff is None else backoff
    attempts = 0
    while True:
        attempts += 1
        try:
            return fn()
        except RunCancelled:
            raise  # a Stop is not a provider failure — never classified, never retried
        except Exception as exc:  # noqa: BLE001 — every provider type, classified below
            err = classify_exception(exc, provider=provider)
            err.attempts = attempts
            if _give_up(err, attempts=attempts, retries=retries, label=label, should_stop=should_stop):
                raise err from exc
            delay = _backoff(attempts - 1, backoff)
            logger.warning(
                "%s: attempt %d/%d failed (%s%s) — retrying in %.1fs",
                label, attempts, retries + 1, err.kind,
                f" {err.status}" if err.status else "", delay,
            )
            if delay:
                sleep(delay)


async def retry_call_async(
    fn: Callable[[], Awaitable[T]],
    *,
    provider: str = "",
    label: str = "asset",
    retries: int | None = None,
    backoff: Sequence[float] | None = None,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    should_stop: Callable[[], bool] | None = None,
) -> T:
    """The async twin of :func:`retry_call` (``AssetPhase``'s gather)."""
    retries = ASSET_RETRIES if retries is None else retries
    backoff = ASSET_BACKOFF_SECONDS if backoff is None else backoff
    attempts = 0
    while True:
        attempts += 1
        try:
            return await fn()
        except RunCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            err = classify_exception(exc, provider=provider)
            err.attempts = attempts
            if _give_up(err, attempts=attempts, retries=retries, label=label, should_stop=should_stop):
                raise err from exc
            delay = _backoff(attempts - 1, backoff)
            logger.warning(
                "%s: attempt %d/%d failed (%s%s) — retrying in %.1fs",
                label, attempts, retries + 1, err.kind,
                f" {err.status}" if err.status else "", delay,
            )
            if delay:
                await sleep(delay)
