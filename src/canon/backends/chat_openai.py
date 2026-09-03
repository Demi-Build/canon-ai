"""OpenAI-compatible chat backends for the agent's conversation loop (Phase 1 A8).

``OpenAIChatBackend`` implements ``canon.backends.base.ChatBackend`` against
the Chat Completions streaming API and translates its chunks into the
provider-neutral ``canon.llm.chat`` events. It is the sibling of
``AnthropicChatBackend`` (``canon.backends.chat_anthropic``) and follows the
same rules: the ``openai`` package stays optional (the ``openai`` extra),
registration is **explicit** — ``register()`` / ``register_kimi()``, never at
import — and a pre-built ``client`` can be injected for tests.

One class, two registrations (Phase 1 §3.3 + Appendix I.1): Kimi is
Moonshot's OpenAI-compatible API, so ``"kimi"`` is this same class with a
different base URL, key variable and default model — no second impl. Ids
and model names are data (``DEFAULT_*_CHAT_MODEL``, ``KIMI_*``), never a
Literal union; the price table's current ids (gpt-5.1, gpt-5.4-mini/nano;
kimi-k2.6 / kimi-k3) are defaults overridden per backend or per request.

What the request mapping does that the protocol anticipates:

- The neutral history carries every tool result of one assistant turn in
  ONE user message; Chat Completions wants one ``role: "tool"`` message per
  call, so ``tool_result`` blocks are exploded into consecutive tool
  messages, in order. ``is_error`` has no wire field — the error text is
  the content, which is what the loop already renders.
- ``thinking`` / ``redacted_thinking`` blocks are DROPPED on replay — a
  provider drops what it cannot use — and ``ChatRequest.thinking`` is
  ignored (``supports_thinking = False``). ``effort`` maps to
  ``reasoning_effort`` only when the backend was built with
  ``reasoning=True``: a documented knob, because ids are data and there is
  no model-name sniffing to decide it.
- ``finish_reason`` maps onto the protocol's vocabulary (``tool_calls`` →
  ``tool_use``, ``stop`` → ``end_turn``, ``length`` → ``max_tokens``,
  ``content_filter`` → ``refusal``); content holding any tool_use block
  reports ``"tool_use"`` regardless — the loop's contract.
- Usage follows the protocol's Anthropic convention: ``input_tokens`` is
  ``prompt_tokens`` MINUS ``cached_tokens`` (OpenAI's prompt count includes
  cache hits), ``cache_read_input_tokens`` is the cached count, and cache
  creation is reported as 0 — OpenAI bills no write premium, and the SDK's
  ``cache_write_tokens`` is already inside ``prompt_tokens``, so mapping it
  would undercount input. Two wire shapes carry the cached count: OpenAI
  nests it (``prompt_tokens_details.cached_tokens``), Moonshot reports it
  top-level (``usage.cached_tokens``); the nested field wins when present,
  the top-level one is the compatible-provider fallback.
- Thinking on Kimi is OFF by construction: ``DEFAULT_KIMI_CHAT_MODEL``
  (kimi-k2.6) enables thinking by default, and Moonshot's rule for a
  thinking model is that every assistant ``reasoning_content`` must be
  replayed across the tool-call loop — a replay this impl does not do
  (``supports_thinking = False``). So ``kimi_backend`` sends the documented
  request knob ``thinking: {type: "disabled"}`` through the SDK's
  ``extra_body`` escape hatch unless built with ``reasoning=True`` (the
  caller's statement that the id is a reasoning model whose knob is
  ``reasoning_effort`` — kimi-k3's shape; ids are data, so no sniffing).

What is deliberately NOT here:

- **No price table and no cost fields** — measured usage only; the single
  price/constants module born at row P0-7 (master PRD §3.0-C) prices it.
- No model picker (row A5); no vision beyond the minimal ``image`` block →
  ``image_url`` part (row A7); no provider-swapped eval run (the stage-6
  gate — a paid, user-run leg).
- No reasoning stream: the thinking-on path — capture ``delta.reasoning_content``
  into the canonical ``thinking`` block ``canon.llm.chat`` already defines and
  replay it as ``reasoning_content`` on the kimi flavour — belongs to the row
  that adds reasoning-stream support to this backend. Until then a Kimi
  backend built with ``reasoning=True`` leaves thinking on and knowingly
  does not replay it.

Example::

    from canon.backends.chat_openai import register, register_kimi

    register()       # BackendRegistry.chat("openai") — needs OPENAI_API_KEY
    register_kimi()  # BackendRegistry.chat("kimi")   — needs MOONSHOT_API_KEY
"""

from __future__ import annotations

import json
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
    ToolInputDelta,
    ToolUseStart,
    Usage,
)

if TYPE_CHECKING:
    from openai import OpenAI  # type: ignore[import]

logger = logging.getLogger(__name__)

#: Default OpenAI conversation model (named in the Phase 1 copy; the price
#: table lists gpt-5.4-mini/nano as the current cheaper tiers). Ids are data —
#: override per backend (``OpenAIChatBackend(model=...)``) or per request.
DEFAULT_OPENAI_CHAT_MODEL = "gpt-5.1"
#: Default Kimi model — kimi-k2 is retired; k2.6 / k3 are current.
DEFAULT_KIMI_CHAT_MODEL = "kimi-k2.6"
#: Credential variables. The OpenAI SDK also reads ``OPENAI_API_KEY`` itself,
#: but this module resolves the key up front so a missing credential is a
#: named ``ChatError`` at stream time rather than a provider 401.
OPENAI_KEY_ENV = "OPENAI_API_KEY"
KIMI_KEY_ENV = "MOONSHOT_API_KEY"
#: Moonshot's OpenAI-compatible endpoint; ``MOONSHOT_BASE_URL`` overrides it.
KIMI_BASE_URL = "https://api.moonshot.ai/v1"
KIMI_BASE_URL_ENV = "MOONSHOT_BASE_URL"
#: Request body the kimi flavour sends unless built with ``reasoning=True``:
#: Moonshot's documented knob that turns thinking off on kimi-k2.6, so no
#: ``reasoning_content`` exists for the loop to (fail to) replay. Data, not
#: sniffed from the model id — see the module docstring.
KIMI_THINKING_OFF: dict[str, Any] = {"thinking": {"type": "disabled"}}

#: ``finish_reason`` → the ``ChatBackend`` stop-reason vocabulary. Anything
#: not listed passes through as-is (the protocol's rule for provider-specific
#: reasons); ``None`` reads as ``end_turn``.
_FINISH_REASONS: dict[str, str] = {
    "tool_calls": "tool_use",
    "stop": "end_turn",
    "length": "max_tokens",
    "content_filter": "refusal",
}


class OpenAIChatBackend:
    """Chat Completions streaming backend — OpenAI, and Kimi via Moonshot.

    Implements ``canon.backends.base.ChatBackend``.

    Args:
        model: Model id used when ``ChatRequest.model`` is ``None``.
        api_key: API key; falls back to the ``key_env`` variable. With neither
            and no ``client``, the SDK client is NOT built — ``stream()``
            raises a named, non-retryable ``ChatError`` instead (mirrors the
            anthropic impl: the fix is named, never a traceback).
        base_url: Endpoint override (Kimi's Moonshot URL; any compatible
            server). ``None`` = the SDK's default (api.openai.com).
        client: Pre-built ``OpenAI`` client (tests inject a fake whose
            ``chat.completions.create(**kwargs)`` returns a closeable
            iterable of ``ChatCompletionChunk`` objects).
        key_env: Environment variable the key is read from.
        id: The registry key this instance expects (``"openai"`` / ``"kimi"``)
            — reported in every ``ChatError`` message and read by the picker.
        reasoning: Forward ``ChatRequest.effort`` as ``reasoning_effort``.
            Off by default: only reasoning-capable ids accept the parameter,
            and ids are data, so the caller says so rather than the backend
            sniffing model names.
        extra_body: Extra JSON properties merged into every request through
            the SDK's documented ``create(extra_body=...)`` escape hatch —
            how compatible providers' own knobs (Moonshot's ``thinking``)
            ride along without a second impl. ``None`` sends nothing.
    """

    #: ``ChatRequest.thinking`` does nothing here; thinking blocks are dropped
    #: on replay and no reasoning stream is surfaced.
    supports_thinking = False

    def __init__(
        self,
        model: str = DEFAULT_OPENAI_CHAT_MODEL,
        api_key: str | None = None,
        base_url: str | None = None,
        client: OpenAI | None = None,
        key_env: str = OPENAI_KEY_ENV,
        id: str = "openai",
        reasoning: bool = False,
        extra_body: dict[str, Any] | None = None,
    ) -> None:
        try:
            import openai  # noqa: F401
        except ImportError as e:
            raise ImportError(
                "OpenAIChatBackend requires the `openai` package. Install with: pip install canon-ai[openai]"
            ) from e

        self.id = id
        self.model = model
        self.key_env = key_env
        self.base_url = base_url
        self.reasoning = reasoning
        self.extra_body = extra_body
        self._client: OpenAI | None = client
        if self._client is None:
            key = api_key or os.environ.get(key_env)
            if key:
                from openai import OpenAI

                client_kwargs: dict[str, Any] = {"api_key": key}
                if base_url:
                    client_kwargs["base_url"] = base_url
                self._client = OpenAI(**client_kwargs)

    # -- request mapping ------------------------------------------------------

    def _build_kwargs(self, request: ChatRequest) -> dict[str, Any]:
        """Translate a ``ChatRequest`` into ``chat.completions.create`` kwargs.

        Always streamed, with ``stream_options.include_usage`` so the final
        chunk carries measured usage. Omission is the rule for optional
        knobs: no ``tools`` when empty, no ``tool_choice`` for ``"auto"``,
        ``reasoning_effort`` only with ``reasoning=True`` and an effort.
        ``max_tokens`` becomes ``max_completion_tokens`` (the current name;
        ``max_tokens`` is the deprecated alias). ``request.metadata`` is
        loop-side and never forwarded; ``request.thinking`` is ignored.
        ``self.extra_body`` (a compatible provider's own knobs) is forwarded
        as the SDK's ``extra_body`` when set.
        """
        messages: list[dict] = []
        if request.system is not None:
            messages.append({"role": "system", "content": request.system})
        for message in request.messages:
            messages.extend(_to_provider_messages(message))

        kwargs: dict[str, Any] = {
            "model": request.model or self.model,
            "messages": messages,
            "max_completion_tokens": request.max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if request.tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.input_schema},
                }
                for t in request.tools
            ]
        if request.tool_choice == "none":
            # The API rejects tool_choice without tools; with none sent,
            # "no tool call" is already the only possible outcome.
            if request.tools:
                kwargs["tool_choice"] = "none"
        elif request.tool_choice != "auto":
            raise ValueError(
                f"ChatRequest.tool_choice must be 'auto' or 'none', got {request.tool_choice!r}: "
                "forced tool choice is not portable across providers and current models reject it"
            )
        if request.effort is not None and self.reasoning:
            kwargs["reasoning_effort"] = request.effort
        if self.extra_body:
            kwargs["extra_body"] = dict(self.extra_body)
        return kwargs

    # -- streaming ------------------------------------------------------------

    def stream(self, request: ChatRequest) -> Iterator[ChatEvent]:
        """Stream one assistant turn, translating chunks per the
        ``ChatBackend`` order contract.

        Cancel contract: the SDK ``Stream`` is closed in a ``finally``, so
        ``gen.close()`` (``GeneratorExit`` at the suspended yield) releases
        the HTTP response — nothing further is generated or billed. A
        missing credential surfaces here, on the first ``next()``, as a
        named ``ChatError`` — never at construction.
        """
        if self._client is None:
            raise ChatError(f"{self.id}: no credential — set {self.key_env}", retryable=False)
        kwargs = self._build_kwargs(request)
        sdk_stream = None
        try:
            sdk_stream = self._client.chat.completions.create(**kwargs)
            logger.debug("chat stream: backend=%s model=%s", self.id, kwargs["model"])
            yield from self._translate(sdk_stream)
        except ChatError:
            raise
        except Exception as exc:
            translated = _translate_error(exc, self.id)
            if translated is None:
                raise
            raise translated from exc
        finally:
            if sdk_stream is not None:
                sdk_stream.close()

    def _translate(self, chunks: Any) -> Iterator[ChatEvent]:
        """Chunks → events, keeping the canonical content list as it grows.

        Block indices are positional in the final content: the text block
        (opened on the first ``delta.content``) takes index 0; tool call
        ``n`` takes ``1 + n`` when a text block exists, else ``n``. The text
        block is closed when the first tool call starts (or at the end);
        every tool block closes at the end with its arguments parsed. Text
        arriving after that close (a compatible-provider quirk) opens a new
        text block rather than reopening a closed one. ``ToolUseStart`` is
        emitted on the first fragment for a tool_call index and carries the
        name that fragment had — OpenAI always sends it there; a compatible
        provider that sends the name late yields an empty start name, and the
        block (and ``ContentBlockDone``) is patched with the name once seen.
        """
        started = False
        blocks: list[dict] = []  # canonical blocks, index order == list order
        open_text: int | None = None  # index of the text block receiving deltas
        tool_blocks: dict[int, int] = {}  # provider tool_call index → block index
        arguments: dict[int, str] = {}  # block index → accumulated arguments JSON
        finish_reason: str | None = None
        usage: Any = None

        for chunk in chunks:
            if not started:
                started = True
                yield MessageStart(model=str(chunk.model), message_id=chunk.id)
            if chunk.usage is not None:
                usage = chunk.usage
            for choice in chunk.choices:
                delta = choice.delta
                for piece in (delta.content, delta.refusal):
                    if not piece:
                        continue
                    if open_text is None:
                        open_text = len(blocks)
                        blocks.append({"type": "text", "text": ""})
                    blocks[open_text]["text"] += piece
                    yield TextDelta(index=open_text, text=piece)
                for call in delta.tool_calls or []:
                    function = call.function
                    if call.index not in tool_blocks:
                        if open_text is not None:
                            yield ContentBlockDone(index=open_text, block=dict(blocks[open_text]))
                            open_text = None
                        index = len(blocks)
                        tool_blocks[call.index] = index
                        block = {
                            "type": "tool_use",
                            "id": call.id or f"call_{index}",
                            "name": (function.name if function is not None else None) or "",
                            "input": {},
                        }
                        blocks.append(block)
                        arguments[index] = ""
                        yield ToolUseStart(index=index, id=block["id"], name=block["name"])
                    index = tool_blocks[call.index]
                    if function is not None and function.name and not blocks[index]["name"]:
                        blocks[index]["name"] = function.name
                    fragment = function.arguments if function is not None else None
                    if fragment:
                        arguments[index] += fragment
                        yield ToolInputDelta(index=index, id=blocks[index]["id"], partial_json=fragment)
                if choice.finish_reason is not None:
                    finish_reason = choice.finish_reason

        if not started:
            return  # no chunks at all: ``collect`` reports the missing MessageStop

        if open_text is not None:
            yield ContentBlockDone(index=open_text, block=dict(blocks[open_text]))
        for index in sorted(arguments):
            blocks[index]["input"] = _parse_arguments(arguments[index], blocks[index]["id"])
            yield ContentBlockDone(index=index, block=dict(blocks[index]))

        if tool_blocks:
            stop_reason = "tool_use"  # the loop's contract, whatever finish_reason said
        elif finish_reason is None:
            stop_reason = "end_turn"
        else:
            stop_reason = _FINISH_REASONS.get(finish_reason, finish_reason)
        stop_details = (
            {"type": "refusal", "category": None, "explanation": "content_filter"} if stop_reason == "refusal" else None
        )
        if usage is None:
            logger.debug("chat stream: backend=%s reported no usage chunk; usage recorded as zeros", self.id)
        yield MessageStop(
            stop_reason=stop_reason,
            usage=_usage_from(usage),
            content=[dict(b) for b in blocks],
            stop_details=stop_details,
        )


# ---------------------------------------------------------------------------
# Canonical blocks → Chat Completions messages
# ---------------------------------------------------------------------------


def _to_provider_messages(message: dict) -> list[dict]:
    """One canonical message → one or more provider messages.

    String content passes through under its role. An assistant block list
    becomes one assistant message (text joined; tool_use → ``tool_calls``;
    thinking dropped). A user block list yields ``role: "tool"`` messages
    for its ``tool_result`` blocks — one per call, in order — and a user
    message for the text/image parts around them (text-only parts collapse
    to a string; an image part keeps the parts list).
    """
    role = message.get("role")
    content = message.get("content")
    if not isinstance(content, list):
        return [{"role": role, "content": content}]
    if role == "assistant":
        return [_assistant_message(content)]

    out: list[dict] = []
    parts: list[dict] = []
    for block in content:
        kind = block.get("type")
        if kind == "tool_result":
            _flush_user_parts(parts, out)
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": block["tool_use_id"],
                    "content": _tool_result_content(block.get("content", "")),
                }
            )
        elif kind == "text":
            parts.append({"type": "text", "text": block.get("text", "")})
        elif kind == "image":
            parts.append(_image_part(block["source"]))
        else:
            logger.debug("chat_openai: dropping unsupported %r block from a %s message", kind, role)
    _flush_user_parts(parts, out)
    return out


def _flush_user_parts(parts: list[dict], out: list[dict]) -> None:
    if not parts:
        return
    if all(p["type"] == "text" for p in parts):
        out.append({"role": "user", "content": "\n".join(p["text"] for p in parts)})
    else:
        out.append({"role": "user", "content": list(parts)})
    parts.clear()


def _assistant_message(content: list[dict]) -> dict:
    # Only text and tool_use are read: thinking / redacted_thinking blocks
    # have no replay shape here and are dropped (the module docstring's rule).
    texts = [b.get("text", "") for b in content if b.get("type") == "text"]
    tool_calls = [
        {
            "id": b["id"],
            "type": "function",
            "function": {"name": b["name"], "arguments": json.dumps(b.get("input", {}))},
        }
        for b in content
        if b.get("type") == "tool_use"
    ]
    message: dict = {"role": "assistant", "content": "\n".join(texts) if texts else None}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message


def _tool_result_content(content: str | list) -> str:
    return content if isinstance(content, str) else json.dumps(content)


def _image_part(source: dict) -> dict:
    kind = source.get("type")
    if kind == "base64":
        url = f"data:{source.get('media_type', 'image/png')};base64,{source['data']}"
    elif kind == "url":
        url = source["url"]
    else:
        raise ValueError(f"chat_openai: unsupported image source type {kind!r} (expected 'base64' or 'url')")
    return {"type": "image_url", "image_url": {"url": url}}


# ---------------------------------------------------------------------------
# Stream-side helpers
# ---------------------------------------------------------------------------


def _parse_arguments(raw: str, tool_id: str) -> dict:
    """Tool arguments JSON → input dict. Unparseable or non-object arguments
    never crash the stream: they ride through as ``{"_raw": ...}`` with a
    warning, so the loop's tool executor reports the bad input as a tool
    error the model can see and repair."""
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        logger.warning("chat_openai: tool call %s carried unparseable JSON arguments (%d chars)", tool_id, len(raw))
        return {"_raw": raw}
    if not isinstance(parsed, dict):
        logger.warning("chat_openai: tool call %s arguments are not a JSON object (%s)", tool_id, type(parsed).__name__)
        return {"_raw": raw}
    return parsed


def _usage_from(usage: Any) -> Usage:
    """Measured counts from a ``CompletionUsage`` (``None`` → zeros).
    ``prompt_tokens`` includes cache hits, so they are subtracted out —
    the protocol's disjoint-input-counts rule. The cached count has two
    wire shapes: OpenAI's nested ``prompt_tokens_details.cached_tokens``
    (preferred when the details object is present) and Moonshot's top-level
    ``usage.cached_tokens`` (the SDK model is ``extra="allow"``, so the
    field survives parsing as an attribute) — the compatible fallback."""
    if usage is None:
        return Usage()
    prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        cached = int(getattr(details, "cached_tokens", 0) or 0)
    else:
        cached = int(getattr(usage, "cached_tokens", 0) or 0)
    return Usage(
        input_tokens=max(prompt - cached, 0),
        output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        cache_read_input_tokens=cached,
        cache_creation_input_tokens=0,
    )


def _translate_error(exc: Exception, backend_id: str) -> ChatError | None:
    """Map an SDK exception onto ``ChatError`` (``None`` = not an SDK error).

    Retryable: rate limits, timeouts, connection drops, 5xx. Not retryable:
    auth, permission, bad request, not found, unprocessable, other 4xx.
    ``status_code`` / ``request_id`` are copied when the SDK has them.
    """
    import openai

    if not isinstance(exc, openai.APIError):
        return None
    status = getattr(exc, "status_code", None)
    request_id = getattr(exc, "request_id", None)
    if isinstance(exc, openai.RateLimitError | openai.APIConnectionError | openai.InternalServerError):
        # APITimeoutError subclasses APIConnectionError.
        retryable = True
    elif isinstance(
        exc,
        openai.AuthenticationError
        | openai.PermissionDeniedError
        | openai.BadRequestError
        | openai.NotFoundError
        | openai.UnprocessableEntityError,
    ):
        retryable = False
    elif isinstance(exc, openai.APIStatusError):
        retryable = bool(status is not None and status >= 500)
    else:
        retryable = False
    message = getattr(exc, "message", None) or str(exc)
    return ChatError(
        f"{backend_id}: {message}",
        retryable=retryable,
        status=int(status) if isinstance(status, int) else None,
        request_id=request_id,
    )


# ---------------------------------------------------------------------------
# Registration — explicit, never at import
# ---------------------------------------------------------------------------


def kimi_backend(
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    client: OpenAI | None = None,
    reasoning: bool = False,
) -> OpenAIChatBackend:
    """Construct the Kimi flavour: id ``"kimi"``, Moonshot's base URL
    (``base_url`` arg → ``MOONSHOT_BASE_URL`` → ``KIMI_BASE_URL``), key from
    ``MOONSHOT_API_KEY``, default model ``DEFAULT_KIMI_CHAT_MODEL``.

    ``reasoning=False`` (the default) sends ``KIMI_THINKING_OFF`` as
    ``extra_body`` — thinking off by construction, because this impl does
    not replay ``reasoning_content`` (module docstring). ``reasoning=True``
    is the caller saying the id is a reasoning model whose knob is
    ``reasoning_effort`` (kimi-k3's shape): no ``thinking`` body is sent and
    ``ChatRequest.effort`` is forwarded."""
    return OpenAIChatBackend(
        model=model or DEFAULT_KIMI_CHAT_MODEL,
        api_key=api_key,
        base_url=base_url or os.environ.get(KIMI_BASE_URL_ENV) or KIMI_BASE_URL,
        client=client,
        key_env=KIMI_KEY_ENV,
        id="kimi",
        reasoning=reasoning,
        extra_body=None if reasoning else dict(KIMI_THINKING_OFF),
    )


def register(model: str | None = None) -> None:
    """Register ``OpenAIChatBackend`` under the chat id ``"openai"``.

    Explicit, never at import — the same rule as ``chat_anthropic.register``.
    The registry constructs lazily on first ``BackendRegistry.chat("openai")``.

    Args:
        model: Model id the registered instance uses; ``None`` =
            ``DEFAULT_OPENAI_CHAT_MODEL``.
    """
    BackendRegistry.register_chat("openai", lambda: OpenAIChatBackend(model=model or DEFAULT_OPENAI_CHAT_MODEL))


def register_kimi(model: str | None = None, base_url: str | None = None, reasoning: bool = False) -> None:
    """Register the Kimi flavour under the chat id ``"kimi"`` — the second
    registration of the one class (Phase 1 Appendix I.1).

    Args:
        model: Model id; ``None`` = ``DEFAULT_KIMI_CHAT_MODEL``.
        base_url: Endpoint override; ``None`` = ``MOONSHOT_BASE_URL`` env,
            then ``KIMI_BASE_URL``.
        reasoning: ``kimi_backend``'s knob — ``False`` turns thinking off on
            the wire; ``True`` for a reasoning id (kimi-k3) whose knob is
            ``reasoning_effort``.
    """
    BackendRegistry.register_chat(
        "kimi", lambda: kimi_backend(model=model, base_url=base_url, reasoning=reasoning)
    )
