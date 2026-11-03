"""The minimal single-actor tool-use loop (Phase 1 A1).

``run_conversation`` drives one ``ChatBackend`` through a list of user
messages: for each, it streams an assistant turn, executes every tool call
the turn asked for (in order, results returned in ONE user message), and
repeats until the model stops asking for tools — or the per-turn
``max_tool_rounds`` guard trips, so a model that keeps calling tools can
never spin.

This is the loop later rows extend, not replace: A2 hooked the transcript
write and the SSE relay onto ``on_event`` and the message appends
(``history`` seeds a turn with a stored transcript, ``on_message`` sees
every append — see ``run_conversation``); A4 puts the permission check in
front of ``tool_executor``; A4.5 threads the cancel flag through the same
points (``cancel`` — checked per streamed event, before every tool call
and before every request; a set flag CLOSES the backend generator, A1's
cancel contract, and raises ``ConversationCancelled`` with the partial
result) and adds ``parallel`` — the predicate naming which tools of one
assistant turn may execute concurrently (the run manager's ``delegate``,
so a foreman fans out artist + writer). The run manager wraps this
function per delegation. Tools here are plain callables — the registry,
tiers and permissions are ``canon.agent.registry`` /
``canon.agent.permissions`` (A2 shell, A4 engine).

Usage rolls up as measured tokens only; pricing is the §3.0-C module's
(row P0-7).
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Protocol

from canon.backends.base import ChatBackend
from canon.llm.chat import (
    ChatEvent,
    ChatRequest,
    ToolSpec,
    Usage,
    assistant_message,
    collect,
    tool_result_message,
)

#: ``(tool_name, tool_input) -> str | dict``. A raised exception becomes an
#: ``is_error`` tool result carrying the exception text — the model sees the
#: failure and may recover; the loop never dies on a tool.
ToolExecutor = Callable[[str, dict], Any]

#: Stop reason the loop records when a user turn exceeds ``max_tool_rounds``.
MAX_TOOL_ROUNDS_STOP = "max_tool_rounds"

#: Stop reason the loop records on a turn ⏹ Stop ended (row A4.5).
CANCELLED_STOP = "cancelled"


class CancelFlag(Protocol):
    """What ``cancel`` needs: ``threading.Event`` or anything with ``is_set``."""

    def is_set(self) -> bool: ...


class ConversationCancelled(Exception):
    """The turn was stopped (row A4.5, master §3.0-D: start nothing new,
    keep what landed, say what it cost).

    Attributes:
        result: The partial ``ConversationResult`` — every message
            appended so far (a resendable transcript: a stopped tool round
            gets its ``is_error`` results before this is raised), the
            steps that ran, and the usage measured so far.
        where: ``"stream"`` (the provider stream was closed mid-reply),
            ``"tool"`` (a pending tool call was skipped) or ``"request"``
            (no further request was made).
    """

    def __init__(self, result: ConversationResult, where: str) -> None:
        self.result = result
        self.where = where
        super().__init__(f"conversation cancelled at {where}")


@dataclass
class ConversationResult:
    """What one ``run_conversation`` produced.

    Attributes:
        messages: The full conversation history in canonical message dicts
            (user turns, assistant turns with all their blocks, tool-result
            user messages) — exactly what the next request would resend.
        steps: One dict per executed tool call, in execution order:
            ``{"tool", "input", "result", "is_error"}``.
        texts: The final assistant text per user turn (the last response's
            text blocks joined).
        stop_reasons: The final stop reason per user turn — the provider's
            (``end_turn``, ``refusal``, ``max_tokens``, …) or
            ``"max_tool_rounds"`` when the guard tripped.
        usage: Measured tokens summed over every request made.
    """

    messages: list[dict] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    stop_reasons: list[str] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)


class _StreamCancelled(Exception):
    """Internal: the cancel flag was seen mid-stream (the generator is closed)."""


def _tap(
    events: Iterable[ChatEvent],
    on_event: Callable[[ChatEvent], None] | None,
    cancel: CancelFlag | None = None,
) -> Iterator[ChatEvent]:
    """Pass every event to ``on_event`` (when given) on its way to
    ``collect``. With ``cancel`` set between two events the backend
    generator is CLOSED (A1's cancel contract — the provider connection is
    released, no further tokens are billed) and ``_StreamCancelled`` ends
    the collect. The generator is closed on every exit, so a normal
    ``MessageStop`` also releases the provider connection promptly."""
    try:
        for event in events:
            if cancel is not None and cancel.is_set():
                raise _StreamCancelled()
            if on_event is not None:
                on_event(event)
            yield event
    finally:
        close = getattr(events, "close", None)
        if close is not None:
            close()


#: Canonical content-block types a tool result may carry directly (row A7's
#: vision tools attach images this way). ``canon.llm.chat`` documents both;
#: the anthropic backend passes them through untouched. A plain string or a
#: JSON document stays exactly what it was — this widens nothing else.
CONTENT_BLOCK_TYPES: frozenset[str] = frozenset({"text", "image"})


def _is_content_blocks(value: Any) -> bool:
    """``value`` is a non-empty list of canonical content blocks (row A7)."""
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(block, dict) and block.get("type") in CONTENT_BLOCK_TYPES for block in value)
    )


def _render_result(result: Any) -> str | list[dict]:
    """A tool's return value as tool_result content: str passes through, a
    list of canonical content blocks passes through as BLOCKS (row A7 —
    a vision tool returns its summary text plus image blocks), any other
    dict or list is JSON, anything else is stringified."""
    if isinstance(result, str):
        return result
    if _is_content_blocks(result):
        return result
    if isinstance(result, dict | list):
        return json.dumps(result)
    if result is None:
        return ""
    return str(result)


def _execute(tool_executor: ToolExecutor, name: str, tool_input: dict) -> tuple[str | list[dict], bool]:
    """Run one tool; ``(content, is_error)``. Exceptions become error results."""
    try:
        return _render_result(tool_executor(name, tool_input)), False
    except Exception as exc:  # noqa: BLE001 — every tool failure must reach the model as data
        return f"{type(exc).__name__}: {exc}", True


def run_conversation(
    backend: ChatBackend,
    *,
    system: str | None,
    tools: list[ToolSpec],
    tool_executor: ToolExecutor,
    user_messages: list[str],
    model: str | None = None,
    max_tokens: int = 8192,
    thinking: bool = True,
    effort: str | None = None,
    max_tool_rounds: int = 8,
    on_event: Callable[[ChatEvent], None] | None = None,
    history: list[dict] | None = None,
    on_message: Callable[[dict], None] | None = None,
    cancel: CancelFlag | None = None,
    parallel: Callable[[str], bool] | None = None,
) -> ConversationResult:
    """Run ``user_messages`` through ``backend`` as one conversation.

    For each user message: append it; then request → ``collect`` the stream
    (each event also handed to ``on_event``) → append the assistant turn →
    if it stopped for ``tool_use``, execute every tool_use block in order
    and append ONE ``tool_result_message`` with all results → repeat. Any
    other stop reason ends the turn.

    Row A2's extension (the service): ``history`` pre-populates
    ``messages`` with a stored transcript (deep-copied; the caller's list
    is never touched) so ONE new user message threads onto everything a
    conversation already said, and ``on_message`` is called with every
    message this call appends — the user turn, each assistant turn, each
    tool_result message — the moment it is appended, which is how the
    transcript file grows incrementally instead of after the turn. The
    seeded history is not replayed to ``on_message``.

    Guard: after ``max_tool_rounds`` executed tool rounds in one user turn,
    a further ``tool_use`` stop is not executed — the turn ends with
    ``"max_tool_rounds"`` recorded (never spin, never burn tokens
    unbounded). Each unexecuted tool_use block still gets ONE ``is_error``
    tool_result (``"not executed: max_tool_rounds reached"``) so
    ``messages`` stays a valid, resendable transcript — the API rejects a
    tool_use turn without its tool_result — while ``steps`` records only
    what actually ran.

    Args:
        backend: Any ``ChatBackend``.
        system: System prompt (``None`` = none).
        tools: Tools offered on every request.
        tool_executor: ``(name, input) -> str | dict``; exceptions become
            ``is_error`` results.
        user_messages: The user's turns, in order.
        model, max_tokens, thinking, effort: Forwarded on every ``ChatRequest``.
        max_tool_rounds: Executed tool rounds allowed per user turn.
        on_event: Optional observer for every streamed ``ChatEvent`` (the
            A2 transcript/SSE hook point).
        history: Prior messages (canonical dicts, oldest first) to seed
            the conversation with — ``ConversationStore.messages(id)``.
        on_message: Optional observer for every message appended by this
            call (the A2 transcript hook point).
        cancel: Row A4.5's ⏹ Stop flag (``threading.Event``-like). Checked
            per streamed event (the stream is closed — no further tokens),
            before each tool call (the pending call is skipped; every
            unexecuted tool_use gets an ``is_error`` result so the
            transcript stays resendable) and before each request.
        parallel: ``(tool_name) -> bool`` — tool_use blocks of ONE
            assistant turn whose names all pass run concurrently (a
            thread each; results keep block order). ``None`` = serial,
            exactly as before.

    Returns:
        ``ConversationResult`` — its ``messages`` include the seeded
        history followed by everything this call appended.

    Raises:
        ChatError: propagated from the backend — a provider failure is not
            the loop's to hide.
        ConversationCancelled: ``cancel`` was set; ``.result`` is the
            partial result (usage so far, what landed).
    """
    result = ConversationResult()
    messages = result.messages
    if history:
        messages.extend(copy.deepcopy(message) for message in history)

    def append(message: dict) -> None:
        messages.append(message)
        if on_message is not None:
            on_message(message)

    def cancelled() -> bool:
        return cancel is not None and cancel.is_set()

    for user_text in user_messages:
        append({"role": "user", "content": user_text})
        rounds = 0
        final_text = ""
        stop_reason = ""

        while True:
            if cancelled():
                result.stop_reasons.append(CANCELLED_STOP)
                raise ConversationCancelled(result, "request")
            request = ChatRequest(
                system=system,
                messages=list(messages),
                tools=list(tools),
                model=model,
                max_tokens=max_tokens,
                thinking=thinking,
                effort=effort,
            )
            try:
                response = collect(_tap(backend.stream(request), on_event, cancel))
            except _StreamCancelled:
                result.stop_reasons.append(CANCELLED_STOP)
                raise ConversationCancelled(result, "stream") from None
            result.usage = result.usage + response.usage
            append(assistant_message(response))
            final_text = response.text
            stop_reason = response.stop_reason

            if stop_reason != "tool_use":
                break
            if rounds >= max_tool_rounds:
                stop_reason = MAX_TOOL_ROUNDS_STOP
                skipped = f"not executed: {MAX_TOOL_ROUNDS_STOP} reached"
                append(tool_result_message([(b.get("id", ""), skipped, True) for b in response.tool_uses]))
                break
            if cancelled():
                skipped = f"not executed: {CANCELLED_STOP}"
                append(tool_result_message([(b.get("id", ""), skipped, True) for b in response.tool_uses]))
                result.stop_reasons.append(CANCELLED_STOP)
                raise ConversationCancelled(result, "tool")
            rounds += 1

            results = _run_tools(response.tool_uses, tool_executor, parallel, cancelled)
            for block, (content, is_error, ran) in zip(response.tool_uses, results, strict=True):
                if ran:
                    name = block.get("name", "")
                    tool_input = dict(block.get("input") or {})
                    result.steps.append({"tool": name, "input": tool_input, "result": content, "is_error": is_error})
            paired = zip(response.tool_uses, results, strict=True)
            append(tool_result_message([(b.get("id", ""), c, e) for b, (c, e, _) in paired]))
            if any(not ran for _, _, ran in results):
                result.stop_reasons.append(CANCELLED_STOP)
                raise ConversationCancelled(result, "tool")

        result.texts.append(final_text)
        result.stop_reasons.append(stop_reason)

    return result


def _run_tools(
    blocks: list[dict],
    tool_executor: ToolExecutor,
    parallel: Callable[[str], bool] | None,
    cancelled: Callable[[], bool],
) -> list[tuple[str | list[dict], bool, bool]]:
    """Execute one assistant turn's tool_use blocks; ``[(content, is_error,
    ran)]`` in block order. Serial by default; when ``parallel`` passes
    EVERY block of the turn (and there are at least two) they run
    concurrently, one thread each — the executor callback binds its own
    call context per thread. A block reached after the cancel flag is set
    is skipped (``ran=False``) with an ``is_error`` "not executed" result."""
    skipped: tuple[str, bool, bool] = (f"not executed: {CANCELLED_STOP}", True, False)

    def one(block: dict) -> tuple[str | list[dict], bool, bool]:
        if cancelled():
            return skipped
        content, is_error = _execute(tool_executor, block.get("name", ""), dict(block.get("input") or {}))
        return content, is_error, True

    if parallel is not None and len(blocks) > 1 and all(parallel(b.get("name", "")) for b in blocks):
        with ThreadPoolExecutor(max_workers=len(blocks), thread_name_prefix="tool") as pool:
            return list(pool.map(one, blocks))
    return [one(block) for block in blocks]


__all__ = [
    "CANCELLED_STOP",
    "CONTENT_BLOCK_TYPES",
    "CancelFlag",
    "ConversationCancelled",
    "ConversationResult",
    "MAX_TOOL_ROUNDS_STOP",
    "ToolExecutor",
    "run_conversation",
]
