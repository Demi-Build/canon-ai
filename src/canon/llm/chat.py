"""Provider-neutral value types for streaming, tool-using chat (Phase 1 row A1).

``LLMRequest`` (canon.llm.request) is the one-shot prompt→text contract the
generation pipeline runs on. The agent needs a second, wider contract beside
it: a multi-turn *conversation* with tool calls, thinking, and a live token
stream. This module holds that contract's value types; the backend Protocol
that consumes them (``ChatBackend``) lives beside ``LLMBackend`` in
``canon.backends.base``. Nothing here imports ``canon.backends`` — the
package's import order (``canon.llm`` before ``canon.backends``) depends on
that, see the note at the top of ``canon/__init__.py``.

Message content is plain JSON-serializable dicts, never SDK objects, so row
A2 can persist a conversation transcript as ``.jsonl`` without translation.
The canonical content blocks (every provider impl maps to/from these):

- text: ``{"type": "text", "text": str}``
- tool_use: ``{"type": "tool_use", "id": str, "name": str, "input": dict}``
- tool_result: ``{"type": "tool_result", "tool_use_id": str,
  "content": str | list[dict], "is_error": bool (optional)}``
- thinking: ``{"type": "thinking", "thinking": str, "signature": str}`` and
  ``redacted_thinking`` blocks pass through verbatim — the replay rule is to
  echo thinking blocks back unchanged on the same model; a provider drops
  what it cannot use.
- image: ``{"type": "image", "source": {...}}`` passes through untouched;
  vision (attaching renders and sprites) is row A7's, not this row's.

Messages are ``{"role": "user" | "assistant", "content": str | list[dict]}``.
Results for one assistant turn's tool calls go back in ONE user message
(``tool_result_message``) — splitting them teaches the model to stop
calling tools in parallel.

Usage is *measured* token counts only. There is no cost field anywhere in
this module on purpose: pricing belongs to the single price/constants module
row P0-7 births (master PRD §3.0-C); row A6 meters these counts against it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Request side
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolSpec:
    """A tool the model may call — name, description, JSON-schema input.

    Provider-neutral; ``AnthropicChatBackend`` maps it 1:1 onto the SDK's
    tool param. Row A2's tool registry produces these; A1 only scripts them.
    """

    name: str
    description: str
    input_schema: dict


@dataclass
class ChatRequest:
    """One provider call in a conversation: full history + generation knobs.

    Attributes:
        system: System prompt, or ``None`` to send none.
        messages: The conversation so far, oldest first, each
            ``{"role": ..., "content": str | list[dict]}`` in the canonical
            block shapes documented in the module docstring.
        tools: Tools the model may call this turn. Empty = no tools sent.
        model: Per-request model id (a plain string — ids are data, never a
            Literal union). ``None`` = the backend's constructed model.
        max_tokens: Output budget for this call.
        thinking: Ask the provider for adaptive thinking. ``False`` omits
            the thinking config entirely (a provider impl never sends an
            explicit "disabled"), which means *provider default* — off on
            Claude Opus 4.7/4.8, still ON (adaptive) on Claude Opus 5, so
            ``False`` is a no-op on the default model; ``effort`` is the
            knob that reduces thinking there.
        effort: Optional effort level string passed through to providers
            that support one (``"low"`` … ``"max"``). ``None`` = provider
            default.
        tool_choice: ``"auto"`` (model decides) or ``"none"`` (no tool call
            this turn). Forced choice is not portable across providers and
            current models reject it, so anything else is a ``ValueError``
            in the provider impl.
        metadata: Loop-side bookkeeping (conversation id, actor, …). NEVER
            forwarded to a provider — backends must not read it.
    """

    system: str | None = None
    messages: list[dict] = field(default_factory=list)
    tools: list[ToolSpec] = field(default_factory=list)
    model: str | None = None
    max_tokens: int = 8192
    thinking: bool = True
    effort: str | None = None
    tool_choice: str = "auto"
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Usage — measured tokens, no cost
# ---------------------------------------------------------------------------


@dataclass
class Usage:
    """Provider-reported token counts for one call (or a rollup of many).

    Cache reads/creations are kept separate from ``input_tokens`` because
    they are priced differently — the §3.0-C module needs all four to meter
    a call honestly. Adding two ``Usage`` values sums field-wise so a
    conversation can roll up its turns.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        if not isinstance(other, Usage):
            return NotImplemented
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_input_tokens=self.cache_read_input_tokens + other.cache_read_input_tokens,
            cache_creation_input_tokens=self.cache_creation_input_tokens + other.cache_creation_input_tokens,
        )


# ---------------------------------------------------------------------------
# Stream events
# ---------------------------------------------------------------------------
#
# Every event carries a string ``type`` (a non-init field, so ``dataclasses.
# asdict`` includes it) — that is the SSE event name row A2 will emit. The
# order contract a backend must honor is documented on ``ChatBackend``.


@dataclass
class MessageStart:
    """The provider accepted the request and began a reply."""

    model: str
    message_id: str | None = None
    type: str = field(default="message_start", init=False)


@dataclass
class TextDelta:
    """A chunk of a text block at content index ``index``."""

    index: int
    text: str
    type: str = field(default="text_delta", init=False)


@dataclass
class ThinkingDelta:
    """A chunk of a thinking block (may be empty when display is omitted)."""

    index: int
    text: str
    type: str = field(default="thinking_delta", init=False)


@dataclass
class ToolUseStart:
    """The model opened a tool call; its input JSON streams next."""

    index: int
    id: str
    name: str
    type: str = field(default="tool_use_start", init=False)


@dataclass
class ToolInputDelta:
    """A partial-JSON chunk of the tool input at ``index`` (tool call ``id``)."""

    index: int
    id: str
    partial_json: str
    type: str = field(default="tool_input_delta", init=False)


@dataclass
class ContentBlockDone:
    """A content block finished; ``block`` is its final canonical dict."""

    index: int
    block: dict
    type: str = field(default="content_block_done", init=False)


@dataclass
class MessageStop:
    """The reply ended. Carries everything a transcript needs: the final
    canonical ``content`` (thinking blocks included, for replay), why it
    stopped, the measured usage, and refusal details when present.

    ``stop_reason`` uses the provider-neutral vocabulary documented on
    ``canon.backends.base.ChatBackend``: ``"tool_use"`` whenever tool_use
    blocks are present (the loop's contract), ``"end_turn"``,
    ``"max_tokens"``, ``"refusal"``; anything else passes through."""

    stop_reason: str
    usage: Usage
    content: list[dict]
    stop_details: dict | None = None
    type: str = field(default="message_stop", init=False)


ChatEvent = MessageStart | TextDelta | ThinkingDelta | ToolUseStart | ToolInputDelta | ContentBlockDone | MessageStop


# ---------------------------------------------------------------------------
# Failure type
# ---------------------------------------------------------------------------


class ChatError(RuntimeError):
    """A provider call failed. Provider-neutral: every backend translates its
    SDK's exceptions into this one so the loop and the panel branch on
    ``retryable`` rather than on provider classes.

    Args:
        message: Human-readable failure text.
        retryable: ``True`` for rate limits, timeouts, connection drops and
            5xx — the kind a retry might clear. ``False`` for auth, bad
            requests, not-found and other 4xx.
        status: HTTP status when the provider gave one.
        request_id: Provider request id when present — log it, never content.
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        status: int | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status
        self.request_id = request_id


# ---------------------------------------------------------------------------
# Assembled response + helpers
# ---------------------------------------------------------------------------


@dataclass
class ChatResponse:
    """One assistant turn, assembled from a stream by ``collect``.

    ``stop_reason`` is ``MessageStop.stop_reason`` — the neutral vocabulary
    on ``canon.backends.base.ChatBackend`` (``"tool_use"`` / ``"end_turn"``
    / ``"max_tokens"`` / ``"refusal"`` + passthrough)."""

    content: list[dict]
    stop_reason: str
    usage: Usage
    model: str
    stop_details: dict | None = None

    @property
    def text(self) -> str:
        """Every text block's text, joined in order."""
        return "".join(b.get("text", "") for b in self.content if b.get("type") == "text")

    @property
    def tool_uses(self) -> list[dict]:
        """The ``tool_use`` blocks, in order."""
        return [b for b in self.content if b.get("type") == "tool_use"]


def collect(events: Iterable[ChatEvent]) -> ChatResponse:
    """Drain a ``ChatBackend.stream`` and assemble the final turn.

    The deltas are consumed and discarded — ``MessageStop`` already carries
    the final content, so assembly never depends on delta bookkeeping.
    Raises ``ChatError`` (not retryable) if the stream ends without a
    ``MessageStop``: a cancelled or broken stream must never be mistaken
    for an empty reply.
    """
    model = ""
    for event in events:
        if isinstance(event, MessageStart):
            model = event.model
        elif isinstance(event, MessageStop):
            return ChatResponse(
                content=list(event.content),
                stop_reason=event.stop_reason,
                usage=event.usage,
                model=model,
                stop_details=event.stop_details,
            )
    raise ChatError("chat stream ended without a MessageStop event", retryable=False)


def assistant_message(response: ChatResponse) -> dict:
    """The assistant turn to append to a conversation's ``messages`` — the
    full content list, thinking blocks included (the replay rule)."""
    return {"role": "assistant", "content": list(response.content)}


def tool_result_message(results: list[tuple[str, str | list[dict], bool]]) -> dict:
    """ONE user message carrying every tool result for one assistant turn.

    Args:
        results: ``(tool_use_id, content, is_error)`` triples in the order
            the tool calls appeared. Parallel tool use means several per
            turn; they all ride the same message — never split them.
    """
    blocks: list[dict] = []
    for tool_use_id, content, is_error in results:
        block: dict = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
        if is_error:
            block["is_error"] = True
        blocks.append(block)
    return {"role": "user", "content": blocks}


__all__ = [
    "ToolSpec",
    "ChatRequest",
    "Usage",
    "MessageStart",
    "TextDelta",
    "ThinkingDelta",
    "ToolUseStart",
    "ToolInputDelta",
    "ContentBlockDone",
    "MessageStop",
    "ChatEvent",
    "ChatError",
    "ChatResponse",
    "collect",
    "assistant_message",
    "tool_result_message",
]
