"""Anthropic Claude chat backend for the agent's conversation loop (Phase 1 A1).

``AnthropicChatBackend`` implements ``canon.backends.base.ChatBackend``: it
streams one assistant turn for a ``ChatRequest`` through the Anthropic SDK's
``messages.stream`` and translates the SDK's events into the provider-neutral
``canon.llm.chat`` events. It is the chat sibling of ``AnthropicBackend``
(``canon.backends.anthropic``, the one-shot ``LLMBackend``) and follows the
same rules: the ``anthropic`` package stays optional, registration is
**explicit** via ``register()`` — never at import — and a pre-built
``client`` can be injected for tests.

What is deliberately NOT here:

- **No price table and no cost fields.** The one-shot backend carries a
  per-1M table; this module reports *measured* usage only (input, output,
  cache read, cache creation tokens on ``MessageStop``). The single
  price/constants module born at row P0-7 (master PRD §3.0-C) prices those
  counts; row A6 meters against it. Do not add a table here.
- No tools, permissions, journaling, sidecar or HTTP — rows A2–A4.5.
- No vision handling beyond passing ``image`` blocks through — row A7.

Refusal fallbacks (on by default, ``fallbacks=False`` to opt out)
-----------------------------------------------------------------

A server-side fallback means: when the model declines a request on policy
(``stop_reason: "refusal"``), the API re-runs the *same* request on a
fallback model inside the *same* call, so the caller sees one response.
``fallbacks: "default"`` (the scalar form) lets the API route by refusal
category — no model list to maintain here; ids stay data. The served model
is whatever ``message.model`` reports on ``message_start`` /
``message_stop``, which this backend already surfaces as
``MessageStart.model`` (the loop reads that: it is the requested-or-served
model exactly as the API reports it). A ``stop_reason: "refusal"`` on the
final response means the whole chain refused — the loop still stops on it.

The installed SDK (0.98.x) predates the feature: ``messages.stream`` has no
``fallbacks`` / ``betas`` parameters, so the flag rides the SDK's generic
``extra_headers`` (``anthropic-beta: server-side-fallback-2026-07-01``, the
header paired with the scalar form — the array form's ``-2026-06-01`` header
is NOT what we send) and ``extra_body`` (``{"fallbacks": "default"}``) until
the SDK is bumped, at which point ``_build_kwargs`` switches to the typed
parameters and nothing else changes. A fallback response may also carry a
content block of type ``"fallback"`` (``from`` / ``to`` model) that this SDK
version does not model; the stream translation dumps every block through
``_dump_block`` so an unknown block passes through as a canonical dict with
its ``"type"`` instead of crashing.

Example::

    from canon.backends.chat_anthropic import AnthropicChatBackend, register
    from canon.llm.chat import ChatRequest, collect

    register()  # now BackendRegistry.chat("anthropic") works
    backend = AnthropicChatBackend()
    response = collect(backend.stream(ChatRequest(messages=[{"role": "user", "content": "hi"}])))
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from canon.backends.registry import BackendRegistry
from canon.llm.chat import (
    ChatError,
    ChatEvent,
    ChatRequest,
    ContentBlockDone,
    MessageStart,
    MessageStop,
    TextDelta,
    ThinkingDelta,
    ToolInputDelta,
    ToolUseStart,
    Usage,
)

if TYPE_CHECKING:
    from anthropic import Anthropic  # type: ignore[import]

logger = logging.getLogger(__name__)

#: Code default for the conversation model (Sonnet, user decision 2026-09-01).
#: Ids are data — override per backend (``AnthropicChatBackend(model=...)``)
#: or per request (``ChatRequest.model``). This is only the LAST rung of the
#: effective default, which resolves project settings → cradle settings →
#: this constant once P0-12 / A5 land the settings layers.
DEFAULT_CHAT_MODEL = "claude-sonnet-5"

#: Thinking config sent when ``ChatRequest.thinking`` is on. Adaptive is the
#: recommended on-mode for 4.6+ models (``budget_tokens`` is deprecated on 4.6
#: and removed on 4.7+). When thinking is off the key is OMITTED — never an
#: explicit "disabled", which some models reject — and omission means the
#: provider default: no thinking on Opus 4.7/4.8, adaptive thinking on Sonnet 5
#: and Opus 5 (so ``thinking=False`` is a no-op on ``DEFAULT_CHAT_MODEL``;
#: lower ``effort`` is the knob that reduces thinking there).
_ADAPTIVE_THINKING: dict = {"type": "adaptive"}

#: Beta header paired with the scalar ``fallbacks: "default"`` form (see the
#: module docstring — the array form uses a different, older header).
_FALLBACK_BETA_HEADER = "server-side-fallback-2026-07-01"
#: Body fragment the SDK cannot type yet; rides ``extra_body``.
_FALLBACK_BODY: dict = {"fallbacks": "default"}


class AnthropicChatBackend:
    """Claude streaming chat backend.

    Implements ``canon.backends.base.ChatBackend``.

    Args:
        model: Claude model id used when ``ChatRequest.model`` is ``None``.
        api_key: Anthropic API key; falls back to ``ANTHROPIC_API_KEY``.
        client: Pre-built ``Anthropic`` client (tests inject a fake whose
            ``messages.stream(**kwargs)`` returns a context manager yielding
            SDK-shaped events).
        thinking: Overrides the adaptive thinking config sent when a request
            asks for thinking (e.g. ``{"type": "adaptive", "display":
            "summarized"}``). ``None`` = ``{"type": "adaptive"}``.
        fallbacks: Server-side refusal fallbacks (module docstring). ``True``
            (default) sends the beta header + ``fallbacks: "default"`` via
            ``extra_headers`` / ``extra_body``; ``False`` sends neither.
    """

    #: The registry key ``register()`` uses; the picker shows it.
    id = "anthropic"
    #: ``ChatRequest.thinking`` is honored (adaptive thinking config).
    supports_thinking = True

    def __init__(
        self,
        model: str = DEFAULT_CHAT_MODEL,
        api_key: str | None = None,
        client: Anthropic | None = None,
        thinking: dict | None = None,
        fallbacks: bool = True,
    ) -> None:
        try:
            from anthropic import Anthropic
        except ImportError as e:
            raise ImportError(
                "AnthropicChatBackend requires the `anthropic` package. "
                "Install with: pip install canon-ai[anthropic]"
            ) from e

        self.model = model
        self._client = client or Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        self._thinking: dict = dict(thinking) if thinking is not None else dict(_ADAPTIVE_THINKING)
        self._fallbacks = bool(fallbacks)

    # -- request mapping ------------------------------------------------------

    def _build_kwargs(self, request: ChatRequest) -> dict[str, Any]:
        """Translate a ``ChatRequest`` into ``messages.stream`` keyword args.

        Omission is the rule for every optional knob: no ``system`` key when
        ``None``, no ``tools`` when empty, no ``thinking`` when off, no
        ``output_config`` without an effort, no ``tool_choice`` for ``"auto"``,
        no ``extra_headers`` / ``extra_body`` when ``fallbacks`` is off.
        ``request.metadata`` is loop-side and never forwarded.
        """
        kwargs: dict[str, Any] = {
            "model": request.model or self.model,
            "max_tokens": request.max_tokens,
            # str content and canonical block lists both pass straight through —
            # the canonical shapes are the API's own.
            "messages": [dict(m) for m in request.messages],
        }
        if request.system is not None:
            kwargs["system"] = request.system
        if request.tools:
            kwargs["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in request.tools
            ]
        if request.thinking:
            kwargs["thinking"] = dict(self._thinking)
        if request.effort is not None:
            kwargs["output_config"] = {"effort": request.effort}
        if request.tool_choice == "none":
            kwargs["tool_choice"] = {"type": "none"}
        elif request.tool_choice != "auto":
            raise ValueError(
                f"ChatRequest.tool_choice must be 'auto' or 'none', got {request.tool_choice!r}: "
                "forced tool choice is not portable across providers and current models reject it"
            )
        if self._fallbacks:
            # SDK 0.98.x has no `fallbacks`/`betas` on messages.stream; ride the
            # generic escape hatches (module docstring, "Refusal fallbacks").
            kwargs["extra_headers"] = {"anthropic-beta": _FALLBACK_BETA_HEADER}
            kwargs["extra_body"] = dict(_FALLBACK_BODY)
        return kwargs

    # -- streaming ------------------------------------------------------------

    def stream(self, request: ChatRequest) -> Iterator[ChatEvent]:
        """Stream one assistant turn, translating SDK events per the
        ``ChatBackend`` order contract.

        Cancel contract: this is a generator inside a ``with
        messages.stream(...)`` block, so ``gen.close()`` raises
        ``GeneratorExit`` here and the SDK context's ``__exit__`` closes the
        HTTP stream — nothing further is generated or billed.

        The SDK fires both raw wire events and synthetic ones; we consume the
        synthetic ``text`` / ``thinking`` / ``input_json`` events for deltas
        (they carry no index, so the current block index is tracked from
        ``content_block_start``) and the raw ``content_block_start`` /
        ``content_block_stop`` / ``message_start`` / ``message_stop`` events
        for structure. ``signature`` / ``citation`` / ``message_delta`` and
        the raw ``content_block_delta`` are ignored — ``message_stop``'s
        final message already carries everything they add up to.

        ``MessageStart.model`` is ``message.model`` as the API reports it —
        the requested model, or the served one when a refusal fallback ran
        (module docstring). Blocks are dumped through ``_dump_block`` so a
        type this SDK does not model (``"fallback"``) passes through as a
        dict with its ``"type"`` rather than crashing the turn.
        """
        kwargs = self._build_kwargs(request)
        current_index = 0
        tool_ids: dict[int, str] = {}
        try:
            with self._client.messages.stream(**kwargs) as s:
                logger.debug(
                    "chat stream: model=%s request_id=%s",
                    kwargs["model"],
                    getattr(s, "request_id", None),
                )
                for ev in s:
                    kind = ev.type
                    if kind == "message_start":
                        yield MessageStart(model=str(ev.message.model), message_id=ev.message.id)
                    elif kind == "content_block_start":
                        current_index = ev.index
                        block = ev.content_block
                        if block.type == "tool_use":
                            tool_ids[ev.index] = block.id
                            yield ToolUseStart(index=ev.index, id=block.id, name=block.name)
                    elif kind == "text":
                        yield TextDelta(index=current_index, text=ev.text)
                    elif kind == "thinking":
                        yield ThinkingDelta(index=current_index, text=ev.thinking)
                    elif kind == "input_json":
                        yield ToolInputDelta(
                            index=current_index,
                            id=tool_ids.get(current_index, ""),
                            partial_json=ev.partial_json,
                        )
                    elif kind == "content_block_stop":
                        yield ContentBlockDone(index=ev.index, block=_dump_block(ev.content_block))
                    elif kind == "message_stop":
                        message = ev.message
                        stop_details = message.stop_details
                        yield MessageStop(
                            stop_reason=message.stop_reason or "end_turn",
                            usage=_usage_from(message.usage),
                            content=[_dump_block(b) for b in message.content],
                            stop_details=(
                                stop_details.model_dump(mode="json", exclude_none=True)
                                if stop_details is not None
                                else None
                            ),
                        )
                    # signature / citation / message_delta / raw content_block_delta: ignored.
        except ChatError:
            raise
        except Exception as exc:
            translated = _translate_error(exc)
            if translated is None:
                raise
            raise translated from exc


def _dump_block(block: Any) -> dict[str, Any]:
    """One SDK content block → canonical dict (``canon.llm.chat`` block shape).

    Extends the previous inline ``model_dump(mode="json", exclude_none=True)``
    with a fallback for objects the SDK does not model as pydantic (a
    ``"fallback"`` block on SDK 0.98.x arrives as a loosely constructed
    model; a hand-built stream may hand us a plain object or dict): dump via
    ``model_dump`` when available, else ``dict(vars(...))`` / ``dict(...)``,
    dropping ``None`` and private attributes. The ``"type"`` key is always
    present so the loop and ``ChatResponse.text`` can branch on it.
    """
    dump = getattr(block, "model_dump", None)
    if callable(dump):
        data = dump(mode="json", exclude_none=True)
    elif isinstance(block, dict):
        data = {k: v for k, v in block.items() if v is not None}
    else:
        try:
            attrs = dict(vars(block))
        except TypeError:
            attrs = {}
        data = {k: v for k, v in attrs.items() if not k.startswith("_") and v is not None}
    data = dict(data)
    data.setdefault("type", str(getattr(block, "type", None) or "unknown"))
    return data


def _usage_from(usage: Any) -> Usage:
    """Measured counts from an SDK ``Usage``; the cache fields are Optional
    on the wire and read as 0 when absent."""
    return Usage(
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        cache_read_input_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        cache_creation_input_tokens=int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
    )


def _translate_error(exc: Exception) -> ChatError | None:
    """Map an SDK exception onto ``ChatError`` (``None`` = not an SDK error).

    Retryable: rate limits, timeouts, connection drops, 5xx. Not retryable:
    auth, permission, bad request, not found, unprocessable, other 4xx, and
    the SDK's missing-credential ``TypeError``.
    ``status_code`` / ``request_id`` are copied when the SDK has them.
    """
    import anthropic

    if isinstance(exc, TypeError) and "Could not resolve authentication method" in str(exc):
        # The SDK raises a bare TypeError (not an APIError) when no credential
        # resolves — before any request is made. Name the fix, not the traceback.
        return ChatError(
            "anthropic: no credential — set ANTHROPIC_API_KEY (or run `ant auth login`)",
            retryable=False,
        )
    if not isinstance(exc, anthropic.APIError):
        return None
    status = getattr(exc, "status_code", None)
    request_id = getattr(exc, "request_id", None)
    if isinstance(exc, anthropic.RateLimitError | anthropic.APIConnectionError | anthropic.InternalServerError):
        # APITimeoutError subclasses APIConnectionError.
        retryable = True
    elif isinstance(
        exc,
        anthropic.AuthenticationError
        | anthropic.PermissionDeniedError
        | anthropic.BadRequestError
        | anthropic.NotFoundError
        | anthropic.UnprocessableEntityError,
    ):
        retryable = False
    elif isinstance(exc, anthropic.APIStatusError):
        retryable = bool(status is not None and status >= 500)
    else:
        retryable = False
    message = getattr(exc, "message", None) or str(exc)
    return ChatError(
        f"anthropic: {message}",
        retryable=retryable,
        status=int(status) if isinstance(status, int) else None,
        request_id=request_id,
    )


def register(model: str | None = None) -> None:
    """Register ``AnthropicChatBackend`` under the chat id ``"anthropic"``.

    Explicit, never at import — same rule as ``canon.backends.anthropic``:
    canon does not hard-depend on the ``anthropic`` package. Call once at
    startup (the eval runner does so for ``--backend anthropic``); the
    registry constructs lazily on first ``BackendRegistry.chat("anthropic")``.

    Args:
        model: Model id the registered instance uses; ``None`` =
            ``DEFAULT_CHAT_MODEL``.
    """
    BackendRegistry.register_chat("anthropic", lambda: AnthropicChatBackend(model=model or DEFAULT_CHAT_MODEL))
