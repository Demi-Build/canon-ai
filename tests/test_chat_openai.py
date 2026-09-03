"""Tests for ``OpenAIChatBackend`` — the openai + kimi provider impls (Phase 1 A8).

Hermetic: no network, no keys, no sleeps. Every test runs under
``pytest.importorskip("openai")`` (the autouse ``sdk`` fixture) with an
injected fake SDK client whose ``chat.completions.create`` returns a
closeable iterable of real ``openai.types.chat.ChatCompletionChunk`` models
— the same house style as the anthropic tests in ``test_chat_backends.py``.
"""

from __future__ import annotations

import inspect
import json
import logging

import pytest

from canon.agent.eval import run_scripted
from canon.agent.evals import CONVERSATIONS
from canon.backends import BackendRegistry, ChatBackend
from canon.backends.chat_openai import (
    DEFAULT_KIMI_CHAT_MODEL,
    DEFAULT_OPENAI_CHAT_MODEL,
    KIMI_BASE_URL,
    KIMI_BASE_URL_ENV,
    KIMI_KEY_ENV,
    KIMI_THINKING_OFF,
    OPENAI_KEY_ENV,
    OpenAIChatBackend,
    kimi_backend,
    register,
    register_kimi,
)
from canon.llm.chat import (
    ChatError,
    ChatRequest,
    ContentBlockDone,
    MessageStart,
    MessageStop,
    TextDelta,
    ToolInputDelta,
    ToolSpec,
    ToolUseStart,
    Usage,
    collect,
)


@pytest.fixture(scope="module", autouse=True)
def sdk():
    return pytest.importorskip("openai")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALIDATE = ToolSpec(
    name="validate_level",
    description="validate",
    input_schema={"type": "object", "properties": {"level_id": {"type": "string"}}},
)
DESCRIBE = ToolSpec(name="describe_level", description="describe", input_schema={"type": "object"})


def make_request(text: str = "hi", **kwargs) -> ChatRequest:
    return ChatRequest(system="sys", messages=[{"role": "user", "content": text}], **kwargs)


class _FakeStream:
    """What ``chat.completions.create(stream=True)`` returns: an iterable of
    chunks with ``close()``. ``error_after`` raises mid-iteration after that
    many chunks — where a real stream surfaces a dropped connection."""

    def __init__(self, chunks: list, log: list[str], error: Exception | None, error_after: int | None) -> None:
        self._chunks = chunks
        self._log = log
        self._error = error
        self._error_after = error_after

    def __iter__(self):
        for n, chunk in enumerate(self._chunks):
            if self._error is not None and self._error_after is not None and n == self._error_after:
                raise self._error
            yield chunk

    def close(self) -> None:
        self._log.append("close")


class _FakeCompletions:
    def __init__(self, chunks: list, log: list[str], error: Exception | None, error_after: int | None) -> None:
        self._chunks = chunks
        self._log = log
        self._error = error
        self._error_after = error_after
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        self._log.append("create")
        if self._error is not None and self._error_after is None:
            raise self._error
        return _FakeStream(self._chunks, self._log, self._error, self._error_after)


class _FakeChat:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.completions = completions


class FakeSDKClient:
    """Stand-in for ``openai.OpenAI`` exposing ``chat.completions.create(**kwargs)``.

    ``error`` is raised by ``create`` (the real request point); with
    ``error_after=N`` it is raised instead while iterating, after N chunks.
    """

    def __init__(
        self, chunks: list | None = None, error: Exception | None = None, error_after: int | None = None
    ) -> None:
        self.log: list[str] = []
        self.chat = _FakeChat(_FakeCompletions(list(chunks or []), self.log, error, error_after))

    @property
    def calls(self) -> list[dict]:
        return self.chat.completions.calls


def _usage(prompt: int, completion: int, cached: int | None = None):
    from openai.types.completion_usage import CompletionUsage, PromptTokensDetails

    return CompletionUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
        prompt_tokens_details=PromptTokensDetails(cached_tokens=cached) if cached is not None else None,
    )


def _chunk(
    *,
    content: str | None = None,
    refusal: str | None = None,
    role: str | None = None,
    tool_calls: list[tuple] | None = None,
    finish_reason: str | None = None,
    usage=None,
    choices: bool = True,
    model: str = "gpt-5.1",
    chunk_id: str = "chatcmpl_01",
):
    """One real ``ChatCompletionChunk``. ``tool_calls`` are
    ``(index, id, name, arguments)`` tuples; ``choices=False`` builds the
    trailing usage-only chunk (``choices: []``)."""
    from openai.types.chat.chat_completion_chunk import (
        ChatCompletionChunk,
        Choice,
        ChoiceDelta,
        ChoiceDeltaToolCall,
        ChoiceDeltaToolCallFunction,
    )

    choice_list = []
    if choices:
        calls = None
        if tool_calls is not None:
            calls = [
                ChoiceDeltaToolCall(
                    index=index,
                    id=call_id,
                    type="function" if call_id else None,
                    function=ChoiceDeltaToolCallFunction(name=name, arguments=arguments),
                )
                for index, call_id, name, arguments in tool_calls
            ]
        delta = ChoiceDelta(content=content, refusal=refusal, role=role, tool_calls=calls)
        choice_list.append(Choice(index=0, delta=delta, finish_reason=finish_reason))
    return ChatCompletionChunk(
        id=chunk_id, choices=choice_list, created=1, model=model, object="chat.completion.chunk", usage=usage
    )


#: Sentinel: the default usage chunk is built lazily inside ``_text_chunks`` so
#: importing this module never touches ``openai.types`` (the autouse
#: ``importorskip`` fixture must be what skips when the extra is missing).
_DEFAULT_USAGE = object()


def _text_chunks(text: str = "Hello", *, finish_reason: str = "stop", usage=_DEFAULT_USAGE) -> list:
    """role → two content fragments → finish → usage-only chunk (what the
    API sends with ``include_usage``); ``usage=None`` drops the last."""
    if usage is _DEFAULT_USAGE:
        usage = _usage(5, 3, cached=4)
    chunks = [
        _chunk(role="assistant", content=""),
        _chunk(content=text[:2]),
        _chunk(content=text[2:]),
        _chunk(finish_reason=finish_reason),
    ]
    if usage is not None:
        chunks.append(_chunk(choices=False, usage=usage))
    return chunks


def _tool_chunks(
    *, text: bool = True, finish_reason: str = "tool_calls", arguments_1: tuple = ('{"level', '_id": "l6"}')
):
    """text (two fragments) → two parallel tool calls with fragmented
    arguments, interleaved → finish → usage."""
    chunks = [_chunk(role="assistant", content="")]
    if text:
        chunks += [_chunk(content="Let me "), _chunk(content="check.")]
    chunks += [
        _chunk(tool_calls=[(0, "call_1", "validate_level", '{"level_id"')]),
        _chunk(tool_calls=[(1, "call_2", "describe_level", arguments_1[0])]),
        _chunk(tool_calls=[(0, None, None, ': "l6"}')]),
        _chunk(tool_calls=[(1, None, None, arguments_1[1])]),
        _chunk(finish_reason=finish_reason),
        _chunk(choices=False, usage=_usage(100, 30, cached=40)),
    ]
    return chunks


def _backend(client: FakeSDKClient, **kwargs) -> OpenAIChatBackend:
    return OpenAIChatBackend(client=client, **kwargs)


# ---------------------------------------------------------------------------
# Request mapping
# ---------------------------------------------------------------------------


class TestOpenAIChatBackendKwargs:
    def test_minimal_request_shape(self) -> None:
        client = FakeSDKClient(_text_chunks())
        backend = _backend(client, model="gpt-5.4-mini")
        collect(backend.stream(ChatRequest(messages=[{"role": "user", "content": "hi"}])))
        (kwargs,) = client.calls
        assert kwargs == {
            "model": "gpt-5.4-mini",
            "messages": [{"role": "user", "content": "hi"}],
            "max_completion_tokens": 8192,
            "stream": True,
            "stream_options": {"include_usage": True},
        }

    def test_system_becomes_leading_system_message(self) -> None:
        client = FakeSDKClient(_text_chunks())
        req = ChatRequest(system="be brief", messages=[{"role": "user", "content": "hello"}])
        collect(_backend(client).stream(req))
        messages = client.calls[0]["messages"]
        assert messages[0] == {"role": "system", "content": "be brief"}
        assert messages[1] == {"role": "user", "content": "hello"}

    def test_request_model_overrides_constructed_model(self) -> None:
        client = FakeSDKClient(_text_chunks())
        collect(_backend(client, model="gpt-5.1").stream(make_request(model="gpt-5.4-nano")))
        assert client.calls[0]["model"] == "gpt-5.4-nano"

    def test_default_model_constants_are_current_ids(self) -> None:
        assert DEFAULT_OPENAI_CHAT_MODEL == "gpt-5.1"
        assert DEFAULT_KIMI_CHAT_MODEL == "kimi-k2.6"
        assert _backend(FakeSDKClient()).model == DEFAULT_OPENAI_CHAT_MODEL

    def test_tool_results_explode_into_tool_messages_in_order(self) -> None:
        client = FakeSDKClient(_text_chunks())
        req = ChatRequest(
            messages=[
                {"role": "user", "content": "go"},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "call_a", "name": "validate_level", "input": {"level_id": "l6"}},
                        {"type": "tool_use", "id": "call_b", "name": "describe_level", "input": {}},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "call_a", "content": "ok", "is_error": False},
                        {"type": "tool_result", "tool_use_id": "call_b", "content": [{"type": "text", "text": "x"}]},
                    ],
                },
            ]
        )
        collect(_backend(client).stream(req))
        messages = client.calls[0]["messages"]
        assert messages[2] == {"role": "tool", "tool_call_id": "call_a", "content": "ok"}
        assert messages[3] == {"role": "tool", "tool_call_id": "call_b", "content": '[{"type": "text", "text": "x"}]'}
        assert len(messages) == 4  # no empty user message trails the exploded results
        assert "is_error" not in json.dumps(messages)

    def test_mixed_user_message_keeps_order_around_tool_results(self) -> None:
        client = FakeSDKClient(_text_chunks())
        req = ChatRequest(
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "before"},
                        {"type": "tool_result", "tool_use_id": "call_a", "content": "ok"},
                        {"type": "text", "text": "after"},
                        {"type": "text", "text": "more"},
                    ],
                }
            ]
        )
        collect(_backend(client).stream(req))
        assert client.calls[0]["messages"] == [
            {"role": "user", "content": "before"},
            {"role": "tool", "tool_call_id": "call_a", "content": "ok"},
            {"role": "user", "content": "after\nmore"},
        ]

    def test_assistant_text_and_tool_calls(self) -> None:
        client = FakeSDKClient(_text_chunks())
        req = ChatRequest(
            messages=[
                {"role": "user", "content": "go"},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Let me check."},
                        {"type": "tool_use", "id": "call_a", "name": "validate_level", "input": {"level_id": "l6"}},
                    ],
                },
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_a", "content": "ok"}]},
            ]
        )
        collect(_backend(client).stream(req))
        assert client.calls[0]["messages"][1] == {
            "role": "assistant",
            "content": "Let me check.",
            "tool_calls": [
                {
                    "id": "call_a",
                    "type": "function",
                    "function": {"name": "validate_level", "arguments": '{"level_id": "l6"}'},
                }
            ],
        }

    def test_assistant_tool_calls_without_text_have_null_content(self) -> None:
        client = FakeSDKClient(_text_chunks())
        req = ChatRequest(
            messages=[
                {"role": "user", "content": "go"},
                {"role": "assistant", "content": [{"type": "tool_use", "id": "c", "name": "probe", "input": {}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c", "content": ""}]},
            ]
        )
        collect(_backend(client).stream(req))
        assistant = client.calls[0]["messages"][1]
        assert assistant["content"] is None
        assert assistant["tool_calls"][0]["function"]["arguments"] == "{}"

    def test_assistant_string_content_passes_through(self) -> None:
        client = FakeSDKClient(_text_chunks())
        req = ChatRequest(messages=[{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}])
        collect(_backend(client).stream(req))
        assert client.calls[0]["messages"][1] == {"role": "assistant", "content": "b"}

    def test_thinking_blocks_are_dropped_on_replay(self) -> None:
        client = FakeSDKClient(_text_chunks())
        req = ChatRequest(
            messages=[
                {"role": "user", "content": "go"},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "secret plan", "signature": "sig"},
                        {"type": "redacted_thinking", "data": "opaque"},
                        {"type": "text", "text": "done"},
                    ],
                },
                {"role": "user", "content": "thanks"},
            ]
        )
        collect(_backend(client).stream(req))
        assistant = client.calls[0]["messages"][1]
        assert assistant == {"role": "assistant", "content": "done"}
        assert "secret plan" not in json.dumps(client.calls[0])
        assert "thinking" not in json.dumps(client.calls[0])

    def test_thinking_flag_is_ignored(self) -> None:
        client = FakeSDKClient(_text_chunks())
        backend = _backend(client)
        assert backend.supports_thinking is False
        collect(backend.stream(make_request(thinking=True)))
        assert "thinking" not in client.calls[0]

    def test_tools_map_to_function_tools(self) -> None:
        client = FakeSDKClient(_text_chunks())
        collect(_backend(client).stream(make_request(tools=[VALIDATE])))
        assert client.calls[0]["tools"] == [
            {
                "type": "function",
                "function": {
                    "name": "validate_level",
                    "description": "validate",
                    "parameters": VALIDATE.input_schema,
                },
            }
        ]

    def test_no_tools_key_when_empty(self) -> None:
        client = FakeSDKClient(_text_chunks())
        collect(_backend(client).stream(make_request(tools=[])))
        assert "tools" not in client.calls[0]

    def test_tool_choice_none_with_tools(self) -> None:
        client = FakeSDKClient(_text_chunks())
        collect(_backend(client).stream(make_request(tools=[VALIDATE], tool_choice="none")))
        assert client.calls[0]["tool_choice"] == "none"

    def test_tool_choice_none_without_tools_is_omitted(self) -> None:
        client = FakeSDKClient(_text_chunks())
        collect(_backend(client).stream(make_request(tool_choice="none")))
        assert "tool_choice" not in client.calls[0]

    def test_tool_choice_auto_omitted(self) -> None:
        client = FakeSDKClient(_text_chunks())
        collect(_backend(client).stream(make_request(tools=[VALIDATE], tool_choice="auto")))
        assert "tool_choice" not in client.calls[0]

    @pytest.mark.parametrize("choice", ["any", "required", "validate_level"])
    def test_forced_tool_choice_raises_value_error(self, choice: str) -> None:
        client = FakeSDKClient(_text_chunks())
        with pytest.raises(ValueError, match="tool_choice"):
            next(_backend(client).stream(make_request(tools=[VALIDATE], tool_choice=choice)))
        assert client.calls == []

    def test_max_tokens_becomes_max_completion_tokens(self) -> None:
        client = FakeSDKClient(_text_chunks())
        collect(_backend(client).stream(make_request(max_tokens=321)))
        assert client.calls[0]["max_completion_tokens"] == 321
        assert "max_tokens" not in client.calls[0]

    def test_reasoning_effort_only_with_reasoning_flag(self) -> None:
        plain = FakeSDKClient(_text_chunks())
        collect(_backend(plain).stream(make_request(effort="high")))
        assert "reasoning_effort" not in plain.calls[0]

        reasoning = FakeSDKClient(_text_chunks())
        collect(_backend(reasoning, reasoning=True).stream(make_request(effort="high")))
        assert reasoning.calls[0]["reasoning_effort"] == "high"

        no_effort = FakeSDKClient(_text_chunks())
        collect(_backend(no_effort, reasoning=True).stream(make_request(effort=None)))
        assert "reasoning_effort" not in no_effort.calls[0]

    def test_metadata_never_forwarded(self) -> None:
        client = FakeSDKClient(_text_chunks())
        collect(_backend(client).stream(make_request(metadata={"conversation": "c1", "actor": "agent:wick"})))
        assert "metadata" not in client.calls[0]
        assert "conversation" not in json.dumps(client.calls[0])

    def test_image_block_becomes_data_url_part(self) -> None:
        client = FakeSDKClient(_text_chunks())
        req = ChatRequest(
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "what is this?"},
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}},
                    ],
                }
            ]
        )
        collect(_backend(client).stream(req))
        assert client.calls[0]["messages"] == [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "what is this?"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                ],
            }
        ]

    def test_unsupported_image_source_raises(self) -> None:
        client = FakeSDKClient(_text_chunks())
        req = ChatRequest(messages=[{"role": "user", "content": [{"type": "image", "source": {"type": "file"}}]}])
        with pytest.raises(ValueError, match="image source"):
            next(_backend(client).stream(req))


class TestKimiFlavour:
    def test_kimi_backend_identity(self, monkeypatch) -> None:
        monkeypatch.delenv(KIMI_BASE_URL_ENV, raising=False)
        backend = kimi_backend(client=FakeSDKClient())
        assert backend.id == "kimi"
        assert backend.key_env == KIMI_KEY_ENV == "MOONSHOT_API_KEY"
        assert backend.base_url == KIMI_BASE_URL == "https://api.moonshot.ai/v1"
        assert backend.model == DEFAULT_KIMI_CHAT_MODEL
        assert isinstance(backend, OpenAIChatBackend)

    def test_kimi_model_override(self) -> None:
        assert kimi_backend(model="kimi-k3", client=FakeSDKClient()).model == "kimi-k3"

    def test_kimi_base_url_env_override_and_arg_precedence(self, monkeypatch) -> None:
        monkeypatch.setenv(KIMI_BASE_URL_ENV, "https://proxy.example/v1")
        assert kimi_backend(client=FakeSDKClient()).base_url == "https://proxy.example/v1"
        explicit = kimi_backend(base_url="https://arg.example/v1", client=FakeSDKClient())
        assert explicit.base_url == "https://arg.example/v1"

    def test_kimi_reads_moonshot_key_and_builds_real_client(self, sdk, monkeypatch) -> None:
        monkeypatch.delenv(OPENAI_KEY_ENV, raising=False)
        monkeypatch.setenv(KIMI_KEY_ENV, "sk-moonshot-test")
        monkeypatch.delenv(KIMI_BASE_URL_ENV, raising=False)
        backend = kimi_backend()
        assert isinstance(backend._client, sdk.OpenAI)
        assert str(backend._client.base_url).rstrip("/") == KIMI_BASE_URL
        assert backend._client.api_key == "sk-moonshot-test"

    def test_openai_flavour_defaults(self) -> None:
        backend = _backend(FakeSDKClient())
        assert (backend.id, backend.key_env, backend.base_url) == ("openai", "OPENAI_API_KEY", None)
        assert backend.extra_body is None

    def test_kimi_flavour_turns_thinking_off_on_the_wire(self) -> None:
        """kimi-k2.6 thinks by default and Moonshot's loop rule wants every
        ``reasoning_content`` replayed — a replay this impl does not do — so
        the kimi flavour sends the documented off switch via ``extra_body``;
        the openai flavour sends no ``extra_body`` at all."""
        kimi = kimi_backend(client=FakeSDKClient())
        assert kimi.extra_body == KIMI_THINKING_OFF == {"thinking": {"type": "disabled"}}
        kwargs = kimi._build_kwargs(make_request(effort="high"))
        assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
        assert "reasoning_effort" not in kwargs
        assert kwargs["extra_body"] is not kimi.extra_body  # a copy per request
        assert "extra_body" not in _backend(FakeSDKClient())._build_kwargs(make_request(effort="high"))

    def test_kimi_flavour_reasoning_true_leaves_thinking_on_and_forwards_effort(self) -> None:
        """``reasoning=True`` is the caller naming a reasoning id (kimi-k3's
        ``reasoning_effort`` shape): no ``thinking`` body, effort forwarded."""
        kimi = kimi_backend(model="kimi-k3", client=FakeSDKClient(), reasoning=True)
        assert kimi.extra_body is None and kimi.reasoning is True
        kwargs = kimi._build_kwargs(make_request(effort="high"))
        assert "extra_body" not in kwargs
        assert kwargs["reasoning_effort"] == "high"

    def test_extra_body_is_forwarded_to_create(self) -> None:
        client = FakeSDKClient(_text_chunks())
        backend = _backend(client, extra_body={"custom": 1})
        collect(backend.stream(make_request()))
        assert client.calls[0]["extra_body"] == {"custom": 1}


# ---------------------------------------------------------------------------
# Streaming translation
# ---------------------------------------------------------------------------


class TestOpenAIChatBackendEvents:
    def test_text_only_turn(self) -> None:
        client = FakeSDKClient(_text_chunks("Hello"))
        events = list(_backend(client).stream(make_request()))

        assert isinstance(events[0], MessageStart)
        assert events[0].model == "gpt-5.1" and events[0].message_id == "chatcmpl_01"
        text = [e for e in events if isinstance(e, TextDelta)]
        assert [(e.index, e.text) for e in text] == [(0, "He"), (0, "llo")]  # the empty role chunk opens nothing
        done = [e for e in events if isinstance(e, ContentBlockDone)]
        assert [(d.index, d.block) for d in done] == [(0, {"type": "text", "text": "Hello"})]
        stop = events[-1]
        assert isinstance(stop, MessageStop)
        assert stop.stop_reason == "end_turn"
        assert stop.stop_details is None
        assert stop.content == [{"type": "text", "text": "Hello"}]
        assert sum(isinstance(e, MessageStop) for e in events) == 1
        assert sum(isinstance(e, MessageStart) for e in events) == 1

    def test_text_then_two_parallel_tool_calls(self) -> None:
        client = FakeSDKClient(_tool_chunks())
        events = list(_backend(client).stream(make_request(tools=[VALIDATE, DESCRIBE])))

        text = [e for e in events if isinstance(e, TextDelta)]
        assert [(e.index, e.text) for e in text] == [(0, "Let me "), (0, "check.")]

        starts = [e for e in events if isinstance(e, ToolUseStart)]
        assert [(e.index, e.id, e.name) for e in starts] == [
            (1, "call_1", "validate_level"),
            (2, "call_2", "describe_level"),
        ]
        inputs = [e for e in events if isinstance(e, ToolInputDelta)]
        assert [(e.index, e.id, e.partial_json) for e in inputs] == [
            (1, "call_1", '{"level_id"'),
            (2, "call_2", '{"level'),
            (1, "call_1", ': "l6"}'),
            (2, "call_2", '_id": "l6"}'),
        ]

        # the text block closes when the first tool call starts, before its ToolUseStart
        first_tool = events.index(starts[0])
        text_done = [e for e in events[:first_tool] if isinstance(e, ContentBlockDone)]
        assert [(d.index, d.block) for d in text_done] == [(0, {"type": "text", "text": "Let me check."})]

        done = [e for e in events if isinstance(e, ContentBlockDone)]
        assert [d.index for d in done] == [0, 1, 2]
        assert done[1].block == {
            "type": "tool_use",
            "id": "call_1",
            "name": "validate_level",
            "input": {"level_id": "l6"},
        }
        assert done[2].block == {
            "type": "tool_use",
            "id": "call_2",
            "name": "describe_level",
            "input": {"level_id": "l6"},
        }

        stop = events[-1]
        assert isinstance(stop, MessageStop)
        assert stop.stop_reason == "tool_use"
        assert stop.content == [done[0].block, done[1].block, done[2].block]
        assert stop.usage == Usage(input_tokens=60, output_tokens=30, cache_read_input_tokens=40)

    def test_collect_assembles_response(self) -> None:
        resp = collect(_backend(FakeSDKClient(_tool_chunks())).stream(make_request()))
        assert resp.text == "Let me check."
        assert [t["name"] for t in resp.tool_uses] == ["validate_level", "describe_level"]
        assert resp.tool_uses[0]["input"] == {"level_id": "l6"}
        assert resp.model == "gpt-5.1"
        assert resp.stop_reason == "tool_use"

    def test_tool_calls_without_text_take_indices_from_zero(self) -> None:
        events = list(_backend(FakeSDKClient(_tool_chunks(text=False))).stream(make_request()))
        starts = [e for e in events if isinstance(e, ToolUseStart)]
        assert [(e.index, e.name) for e in starts] == [(0, "validate_level"), (1, "describe_level")]
        assert [d.index for d in events if isinstance(d, ContentBlockDone)] == [0, 1]
        assert [b["type"] for b in events[-1].content] == ["tool_use", "tool_use"]

    @pytest.mark.parametrize("finish_reason", ["stop", "length", "content_filter"])
    def test_tool_calls_present_are_still_tool_use(self, finish_reason: str) -> None:
        """The loop's contract: content holding a tool_use block reports
        ``tool_use`` whatever finish_reason said — including the refusal
        mapping, whose stop_details must then NOT be attached."""
        resp = collect(_backend(FakeSDKClient(_tool_chunks(finish_reason=finish_reason))).stream(make_request()))
        assert resp.stop_reason == "tool_use"
        assert resp.stop_details is None

    def test_finish_length_is_max_tokens(self) -> None:
        resp = collect(_backend(FakeSDKClient(_text_chunks(finish_reason="length"))).stream(make_request()))
        assert resp.stop_reason == "max_tokens"
        assert resp.stop_details is None

    def test_finish_content_filter_is_refusal_with_details(self) -> None:
        resp = collect(_backend(FakeSDKClient(_text_chunks(finish_reason="content_filter"))).stream(make_request()))
        assert resp.stop_reason == "refusal"
        assert resp.stop_details == {"type": "refusal", "category": None, "explanation": "content_filter"}

    def test_unknown_finish_reason_passes_through(self) -> None:
        chunks = _text_chunks()
        chunks[3].choices[0].finish_reason = "function_call"  # the deprecated legacy reason
        resp = collect(_backend(FakeSDKClient(chunks)).stream(make_request()))
        assert resp.stop_reason == "function_call"

    def test_missing_finish_reason_defaults_to_end_turn(self) -> None:
        chunks = [_chunk(content="x"), _chunk(choices=False, usage=_usage(1, 1))]
        resp = collect(_backend(FakeSDKClient(chunks)).stream(make_request()))
        assert resp.stop_reason == "end_turn"

    def test_usage_subtracts_cached_tokens(self) -> None:
        resp = collect(_backend(FakeSDKClient(_text_chunks(usage=_usage(100, 7, cached=40)))).stream(make_request()))
        assert resp.usage == Usage(
            input_tokens=60, output_tokens=7, cache_read_input_tokens=40, cache_creation_input_tokens=0
        )

    def test_usage_without_details_has_no_cache_read(self) -> None:
        resp = collect(_backend(FakeSDKClient(_text_chunks(usage=_usage(9, 2)))).stream(make_request()))
        assert resp.usage == Usage(input_tokens=9, output_tokens=2)

    def test_usage_reads_kimi_top_level_cached_tokens(self) -> None:
        """Moonshot reports cache reads as a top-level ``usage.cached_tokens``
        (no ``prompt_tokens_details``); the SDK model keeps the extra field,
        and the backend must still subtract it (the protocol's usage rule)."""
        from openai.types.completion_usage import CompletionUsage

        usage = CompletionUsage.model_validate(
            {"prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105, "cached_tokens": 40}
        )
        resp = collect(_backend(FakeSDKClient(_text_chunks(usage=usage))).stream(make_request()))
        assert resp.usage == Usage(input_tokens=60, output_tokens=5, cache_read_input_tokens=40)

    def test_usage_prefers_nested_cached_tokens_over_top_level(self) -> None:
        from openai.types.completion_usage import CompletionUsage

        usage = CompletionUsage.model_validate(
            {
                "prompt_tokens": 100,
                "completion_tokens": 5,
                "total_tokens": 105,
                "cached_tokens": 99,
                "prompt_tokens_details": {"cached_tokens": 40},
            }
        )
        resp = collect(_backend(FakeSDKClient(_text_chunks(usage=usage))).stream(make_request()))
        assert resp.usage == Usage(input_tokens=60, output_tokens=5, cache_read_input_tokens=40)

    def test_no_usage_chunk_reports_zeros(self, caplog) -> None:
        with caplog.at_level(logging.DEBUG, logger="canon.backends.chat_openai"):
            resp = collect(_backend(FakeSDKClient(_text_chunks(usage=None))).stream(make_request()))
        assert resp.usage == Usage()
        assert any("no usage chunk" in r.message for r in caplog.records)

    def test_unparseable_arguments_never_crash(self, caplog) -> None:
        chunks = _tool_chunks(arguments_1=('{"level', "_id: oops"))
        with caplog.at_level(logging.WARNING, logger="canon.backends.chat_openai"):
            resp = collect(_backend(FakeSDKClient(chunks)).stream(make_request()))
        assert resp.stop_reason == "tool_use"
        assert resp.tool_uses[0]["input"] == {"level_id": "l6"}
        assert resp.tool_uses[1]["input"] == {"_raw": '{"level_id: oops'}
        assert any("call_2" in r.message and "unparseable" in r.message for r in caplog.records)

    def test_empty_arguments_parse_to_empty_input(self) -> None:
        chunks = [
            _chunk(tool_calls=[(0, "call_1", "probe", "")]),
            _chunk(finish_reason="tool_calls"),
        ]
        resp = collect(_backend(FakeSDKClient(chunks)).stream(make_request()))
        assert resp.tool_uses == [{"type": "tool_use", "id": "call_1", "name": "probe", "input": {}}]

    def test_refusal_delta_text_is_surfaced_as_text(self) -> None:
        chunks = [_chunk(refusal="I can't help with that."), _chunk(finish_reason="stop")]
        resp = collect(_backend(FakeSDKClient(chunks)).stream(make_request()))
        assert resp.text == "I can't help with that."
        assert resp.stop_reason == "end_turn"

    def test_text_after_tool_calls_opens_a_new_block(self) -> None:
        chunks = [
            _chunk(content="a"),
            _chunk(tool_calls=[(0, "call_1", "probe", "{}")]),
            _chunk(content="b"),
            _chunk(finish_reason="tool_calls"),
        ]
        events = list(_backend(FakeSDKClient(chunks)).stream(make_request()))
        assert [(e.index, e.text) for e in events if isinstance(e, TextDelta)] == [(0, "a"), (2, "b")]
        assert [d.index for d in events if isinstance(d, ContentBlockDone)] == [0, 2, 1]
        assert [b["type"] for b in events[-1].content] == ["text", "tool_use", "text"]

    def test_empty_stream_yields_nothing_so_collect_raises(self) -> None:
        backend = _backend(FakeSDKClient([]))
        assert list(backend.stream(make_request())) == []
        with pytest.raises(ChatError, match="without a MessageStop"):
            collect(backend.stream(make_request()))

    def test_cancel_closes_sdk_stream(self) -> None:
        client = FakeSDKClient(_text_chunks("Hello world"))
        gen = _backend(client).stream(make_request())
        first_text = None
        for event in gen:
            if isinstance(event, TextDelta):
                first_text = event
                break
        assert first_text is not None
        assert client.log == ["create"]
        gen.close()
        assert client.log == ["create", "close"]

    def test_stream_closes_after_normal_completion(self) -> None:
        client = FakeSDKClient(_text_chunks())
        collect(_backend(client).stream(make_request()))
        assert client.log == ["create", "close"]


# ---------------------------------------------------------------------------
# Error translation + credentials
# ---------------------------------------------------------------------------


class TestOpenAIChatBackendErrors:
    @staticmethod
    def _request():
        import httpx

        return httpx.Request("POST", "https://api.openai.com/v1/chat/completions")

    def _status_error(self, cls, status: int, request_id: str = "req_err"):
        import httpx

        response = httpx.Response(status, request=self._request(), headers={"x-request-id": request_id})
        return cls("boom", response=response, body=None)

    def _raise(self, error: Exception, **kwargs) -> ChatError:
        backend = _backend(FakeSDKClient(error=error), **kwargs)
        with pytest.raises(ChatError) as exc_info:
            collect(backend.stream(make_request()))
        assert exc_info.value.__cause__ is error
        return exc_info.value

    def test_rate_limit_is_retryable(self, sdk) -> None:
        err = self._raise(self._status_error(sdk.RateLimitError, 429, "req_429"))
        assert err.retryable is True
        assert err.status == 429
        assert err.request_id == "req_429"
        assert str(err).startswith("openai: ")

    def test_error_message_names_the_backend_id(self, sdk) -> None:
        err = self._raise(self._status_error(sdk.RateLimitError, 429), id="kimi")
        assert str(err).startswith("kimi: ")

    def test_bad_request_not_retryable(self, sdk) -> None:
        err = self._raise(self._status_error(sdk.BadRequestError, 400))
        assert err.retryable is False
        assert err.status == 400

    def test_auth_permission_not_found_unprocessable_not_retryable(self, sdk) -> None:
        assert self._raise(self._status_error(sdk.AuthenticationError, 401)).retryable is False
        assert self._raise(self._status_error(sdk.PermissionDeniedError, 403)).retryable is False
        assert self._raise(self._status_error(sdk.NotFoundError, 404)).retryable is False
        assert self._raise(self._status_error(sdk.UnprocessableEntityError, 422)).retryable is False

    def test_connection_error_retryable_without_status(self, sdk) -> None:
        err = self._raise(sdk.APIConnectionError(request=self._request()))
        assert err.retryable is True
        assert err.status is None

    def test_timeout_retryable(self, sdk) -> None:
        assert self._raise(sdk.APITimeoutError(request=self._request())).retryable is True

    def test_internal_server_error_retryable(self, sdk) -> None:
        assert self._raise(self._status_error(sdk.InternalServerError, 500)).retryable is True

    def test_generic_status_error_retryable_iff_5xx(self, sdk) -> None:
        assert self._raise(self._status_error(sdk.APIStatusError, 503)).retryable is True
        assert self._raise(self._status_error(sdk.APIStatusError, 409)).retryable is False

    def test_error_on_create_has_nothing_to_close(self, sdk) -> None:
        client = FakeSDKClient(error=self._status_error(sdk.RateLimitError, 429, "req_create"))
        with pytest.raises(ChatError) as exc_info:
            collect(_backend(client).stream(make_request()))
        assert exc_info.value.request_id == "req_create"
        assert client.log == ["create"]

    def test_mid_stream_connection_drop_closes_once_and_chains(self, sdk) -> None:
        error = sdk.APIConnectionError(request=self._request())
        client = FakeSDKClient(_text_chunks("Hello world"), error=error, error_after=3)
        seen: list = []
        with pytest.raises(ChatError) as exc_info:
            for event in _backend(client).stream(make_request()):
                seen.append(event)
        assert any(isinstance(e, TextDelta) for e in seen)
        assert exc_info.value.retryable is True
        assert exc_info.value.__cause__ is error
        assert client.log == ["create", "close"]

    def test_non_sdk_exception_propagates_unchanged(self) -> None:
        backend = _backend(FakeSDKClient(error=RuntimeError("not the sdk")))
        with pytest.raises(RuntimeError, match="not the sdk"):
            collect(backend.stream(make_request()))

    def test_missing_credential_is_named_at_stream_time_not_construction(self, monkeypatch) -> None:
        monkeypatch.delenv(OPENAI_KEY_ENV, raising=False)
        backend = OpenAIChatBackend()  # no key, no client: constructs fine
        assert backend._client is None
        with pytest.raises(ChatError) as exc_info:
            next(backend.stream(make_request()))
        assert exc_info.value.retryable is False
        assert str(exc_info.value) == "openai: no credential — set OPENAI_API_KEY"

    def test_missing_kimi_credential_names_moonshot_var(self, monkeypatch) -> None:
        monkeypatch.delenv(KIMI_KEY_ENV, raising=False)
        monkeypatch.setenv(OPENAI_KEY_ENV, "sk-openai-must-not-leak-into-kimi")
        backend = kimi_backend()
        with pytest.raises(ChatError) as exc_info:
            collect(backend.stream(make_request()))
        assert str(exc_info.value) == "kimi: no credential — set MOONSHOT_API_KEY"

    def test_explicit_api_key_builds_a_real_client(self, sdk, monkeypatch) -> None:
        monkeypatch.delenv(OPENAI_KEY_ENV, raising=False)
        backend = OpenAIChatBackend(api_key="sk-test")
        assert isinstance(backend._client, sdk.OpenAI)
        assert backend._client.api_key == "sk-test"


# ---------------------------------------------------------------------------
# Registration + protocol + the no-pricing gate
# ---------------------------------------------------------------------------


class TestOpenAIChatBackendRegistration:
    def setup_method(self) -> None:
        BackendRegistry.reset()

    def teardown_method(self) -> None:
        BackendRegistry.reset()

    def test_not_registered_at_import_time(self) -> None:
        import canon.backends.chat_openai  # noqa: F401

        assert BackendRegistry.chat_ids() == []

    def test_register_is_explicit_and_idempotent(self, monkeypatch) -> None:
        monkeypatch.delenv(OPENAI_KEY_ENV, raising=False)
        register()
        register("gpt-5.4-mini")
        assert BackendRegistry.chat_ids() == ["openai"]
        backend = BackendRegistry.chat("openai")
        assert backend.id == "openai" and backend.model == "gpt-5.4-mini"

    def test_register_kimi_is_the_second_registration_of_one_class(self, monkeypatch) -> None:
        monkeypatch.delenv(KIMI_KEY_ENV, raising=False)
        monkeypatch.delenv(KIMI_BASE_URL_ENV, raising=False)
        register()
        register_kimi()
        assert BackendRegistry.chat_ids() == ["openai", "kimi"]
        kimi = BackendRegistry.chat("kimi")
        assert type(kimi) is OpenAIChatBackend
        assert (kimi.id, kimi.model, kimi.base_url, kimi.key_env) == (
            "kimi",
            DEFAULT_KIMI_CHAT_MODEL,
            KIMI_BASE_URL,
            KIMI_KEY_ENV,
        )

    def test_register_kimi_overrides(self, monkeypatch) -> None:
        monkeypatch.delenv(KIMI_KEY_ENV, raising=False)
        register_kimi("kimi-k3", base_url="https://proxy.example/v1", reasoning=True)
        kimi = BackendRegistry.chat("kimi")
        assert (kimi.model, kimi.base_url) == ("kimi-k3", "https://proxy.example/v1")
        assert kimi.reasoning is True and kimi.extra_body is None

    def test_register_kimi_default_turns_thinking_off(self, monkeypatch) -> None:
        monkeypatch.delenv(KIMI_KEY_ENV, raising=False)
        register_kimi()
        assert BackendRegistry.chat("kimi").extra_body == KIMI_THINKING_OFF

    def test_implements_protocol_and_identity(self) -> None:
        backend = _backend(FakeSDKClient())
        assert isinstance(backend, ChatBackend)
        assert backend.id == "openai"
        assert backend.supports_thinking is False
        assert isinstance(kimi_backend(client=FakeSDKClient()), ChatBackend)

    def test_lazy_export_from_package(self) -> None:
        import canon.backends

        assert canon.backends.OpenAIChatBackend is OpenAIChatBackend
        assert "OpenAIChatBackend" in canon.backends.__all__


# ---------------------------------------------------------------------------
# Provider-swap dry run — the $0 twin of the stage-6 paid leg
# ---------------------------------------------------------------------------


class _ScriptedSDKClient(FakeSDKClient):
    """``FakeSDKClient`` that plays a conversation's ``fake_turns`` as
    Chat Completions chunk sequences, one turn per ``create()`` call: text
    → two content fragments; tool_use → a tool_call chunk with an id plus a
    fragmented-arguments chunk; a scripted refusal → ``content_filter``;
    then the finish chunk and a usage-only chunk."""

    def __init__(self, turns: list) -> None:
        super().__init__()
        self._turns = list(turns)
        self._tool_counter = 0
        self.chat.completions.create = self._create  # type: ignore[method-assign]

    def _create(self, **kwargs):
        self.chat.completions.calls.append(kwargs)
        return _FakeStream(self._chunks_for(self._turns.pop(0)), self.log, None, None)

    def _chunks_for(self, turn: list | dict) -> list:
        blocks = list(turn.get("content", [])) if isinstance(turn, dict) else list(turn)
        scripted_stop = turn.get("stop_reason") if isinstance(turn, dict) else None
        chunks = [_chunk(role="assistant", content="")]
        call_index = 0
        for block in blocks:
            if block["type"] == "text":
                text = block["text"]
                chunks += [_chunk(content=text[: len(text) // 2]), _chunk(content=text[len(text) // 2 :])]
            elif block["type"] == "tool_use":
                self._tool_counter += 1
                raw = json.dumps(block.get("input", {}))
                chunks.append(_chunk(tool_calls=[(call_index, f"call_{self._tool_counter}", block["name"], raw[:3])]))
                chunks.append(_chunk(tool_calls=[(call_index, None, None, raw[3:])]))
                call_index += 1
        if call_index:
            finish = "tool_calls"
        elif scripted_stop == "refusal":
            finish = "content_filter"
        else:
            finish = "stop"
        chunks.append(_chunk(finish_reason=finish))
        chunks.append(_chunk(choices=False, usage=_usage(10, 4, cached=2)))
        return chunks


class TestProviderSwapDryRun:
    """``run_scripted`` over ``OpenAIChatBackend`` with the corpus's own
    fake turns played as chunks: proves the loop's neutral history survives
    the tool_result explosion across several requests (assistant tool_calls
    → consecutive ``role: tool`` messages in id order, ``content`` None on
    tool-only turns, error text as content) — the composition the stage-6
    provider-swapped gate depends on, at $0."""

    @pytest.mark.parametrize("conv", CONVERSATIONS, ids=[c.name for c in CONVERSATIONS])
    def test_corpus_passes_over_openai_backend(self, conv) -> None:
        client = _ScriptedSDKClient(conv.fake_turns)
        result = run_scripted(conv, _backend(client), strict_text=False)
        assert result.failures == [] and result.passed
        assert len(client.calls) == len(conv.fake_turns)
        assert result.usage.input_tokens == 8 * len(conv.fake_turns)
        assert result.usage.cache_read_input_tokens == 2 * len(conv.fake_turns)

        for kwargs in client.calls:
            messages = kwargs["messages"]
            assert messages[0] == {"role": "system", "content": conv.system}
            assert "extra_body" not in kwargs
            for n, message in enumerate(messages):
                if message["role"] != "assistant" or not message.get("tool_calls"):
                    continue
                ids = [c["id"] for c in message["tool_calls"]]
                following = messages[n + 1 : n + 1 + len(ids)]
                assert [m["role"] for m in following] == ["tool"] * len(ids)
                assert [m["tool_call_id"] for m in following] == ids
                assert all(isinstance(m["content"], str) for m in following)
                if n + 1 + len(ids) < len(messages):
                    assert messages[n + 1 + len(ids)]["role"] != "tool"

    def test_parallel_reads_wire_shape(self) -> None:
        conv = next(c for c in CONVERSATIONS if c.name == "parallel-reads")
        client = _ScriptedSDKClient(conv.fake_turns)
        assert run_scripted(conv, _backend(client), strict_text=False).passed
        second = client.calls[1]["messages"]
        assert [m["role"] for m in second] == ["system", "user", "assistant", "tool", "tool"]
        assert second[2]["content"] is None
        assert [c["function"]["name"] for c in second[2]["tool_calls"]] == ["db_row", "view_asset"]

    def test_tool_error_text_rides_as_tool_content(self) -> None:
        conv = next(c for c in CONVERSATIONS if c.name == "tool-error-recovers")
        client = _ScriptedSDKClient(conv.fake_turns)
        assert run_scripted(conv, _backend(client), strict_text=False).passed
        tool_messages = [m for m in client.calls[1]["messages"] if m["role"] == "tool"]
        assert len(tool_messages) == 1 and "godot/main.gd" in tool_messages[0]["content"]

    def test_refusal_reaches_the_loop_as_a_stop_reason(self) -> None:
        conv = next(c for c in CONVERSATIONS if c.name == "refusal-surfaces")
        client = _ScriptedSDKClient(conv.fake_turns)
        result = run_scripted(conv, _backend(client), strict_text=True)
        assert result.failures == [] and result.passed


def test_no_pricing_symbol_in_chat_openai() -> None:
    import canon.backends.chat_openai as module

    assert not hasattr(module, "PRICING"), "chat_openai must not carry a price table (that is the §3.0-C module's)"
    source = inspect.getsource(module)
    assert "PRICING" not in source
    assert "last_cost" not in source
    assert not any(name.lower().endswith("_cost") for name in dir(module))
    assert not any("cost" in name for name in vars(_backend(FakeSDKClient())))
