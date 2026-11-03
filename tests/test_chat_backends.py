"""Tests for the ChatBackend protocol, the registry's chat namespace,
FakeChatBackend, and AnthropicChatBackend (Phase 1 A1).

Hermetic: no network, no keys, no sleeps. The anthropic-specific tests run
under ``pytest.importorskip("anthropic")`` (via the ``sdk`` fixture) with an
injected fake SDK client that yields real ``anthropic.types`` event models.
"""

from __future__ import annotations

import dataclasses
import importlib
import inspect
import json

import pytest

from canon.backends import BackendRegistry, ChatBackend, FakeChatBackend
from canon.llm.chat import (
    ChatError,
    ChatRequest,
    ChatResponse,
    ContentBlockDone,
    MessageStart,
    MessageStop,
    TextDelta,
    ThinkingDelta,
    ToolInputDelta,
    ToolSpec,
    ToolUseStart,
    Usage,
    assistant_message,
    collect,
    tool_result_message,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALIDATE = ToolSpec(
    name="validate_level",
    description="validate",
    input_schema={"type": "object", "properties": {"level_id": {"type": "string"}}},
)


def make_request(text: str = "hi", **kwargs) -> ChatRequest:
    return ChatRequest(system="sys", messages=[{"role": "user", "content": text}], **kwargs)


def text_turn(text: str) -> list[dict]:
    return [{"type": "text", "text": text}]


# ---------------------------------------------------------------------------
# Value types + helpers (canon.llm.chat)
# ---------------------------------------------------------------------------


class TestChatValueTypes:
    def test_usage_adds_fieldwise(self) -> None:
        total = Usage(1, 2, 3, 4) + Usage(10, 20, 30, 40)
        assert total == Usage(11, 22, 33, 44)

    def test_usage_has_no_cost_field(self) -> None:
        assert {f.name for f in dataclasses.fields(Usage)} == {
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        }

    def test_events_carry_string_type_for_sse(self) -> None:
        assert dataclasses.asdict(TextDelta(0, "x"))["type"] == "text_delta"
        assert dataclasses.asdict(MessageStart("m"))["type"] == "message_start"
        assert dataclasses.asdict(MessageStop("end_turn", Usage(), []))["type"] == "message_stop"
        assert ToolUseStart(0, "id", "n").type == "tool_use_start"
        assert ToolInputDelta(0, "id", "{").type == "tool_input_delta"
        assert ContentBlockDone(0, {}).type == "content_block_done"
        assert ThinkingDelta(0, "").type == "thinking_delta"

    def test_request_defaults(self) -> None:
        req = ChatRequest()
        assert req.system is None
        assert req.messages == [] and req.tools == [] and req.metadata == {}
        assert req.model is None and req.max_tokens == 8192
        assert req.thinking is True and req.effort is None and req.tool_choice == "auto"

    def test_response_text_and_tool_uses(self) -> None:
        resp = ChatResponse(
            content=[
                {"type": "thinking", "thinking": "t", "signature": "s"},
                {"type": "text", "text": "a"},
                {"type": "tool_use", "id": "1", "name": "x", "input": {}},
                {"type": "text", "text": "b"},
            ],
            stop_reason="tool_use",
            usage=Usage(),
            model="m",
        )
        assert resp.text == "ab"
        assert [t["id"] for t in resp.tool_uses] == ["1"]

    def test_collect_raises_without_message_stop(self) -> None:
        with pytest.raises(ChatError) as exc_info:
            collect([MessageStart("m"), TextDelta(0, "x")])
        assert exc_info.value.retryable is False

    def test_assistant_message_carries_full_content(self) -> None:
        resp = ChatResponse(content=text_turn("hi"), stop_reason="end_turn", usage=Usage(), model="m")
        assert assistant_message(resp) == {"role": "assistant", "content": [{"type": "text", "text": "hi"}]}

    def test_tool_result_message_is_one_user_message(self) -> None:
        msg = tool_result_message([("a", "ok", False), ("b", "boom", True)])
        assert msg["role"] == "user"
        assert msg["content"] == [
            {"type": "tool_result", "tool_use_id": "a", "content": "ok"},
            {"type": "tool_result", "tool_use_id": "b", "content": "boom", "is_error": True},
        ]

    def test_chat_error_attributes(self) -> None:
        err = ChatError("x", retryable=True, status=429, request_id="req_1")
        assert isinstance(err, RuntimeError)
        assert (err.retryable, err.status, err.request_id) == (True, 429, "req_1")


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestChatBackendProtocol:
    def test_fake_satisfies_protocol(self) -> None:
        assert isinstance(FakeChatBackend([]), ChatBackend)

    def test_custom_class_satisfies_protocol(self) -> None:
        class MyChat:
            def stream(self, request: ChatRequest):
                yield MessageStart("m")
                yield MessageStop("end_turn", Usage(), [])

        assert isinstance(MyChat(), ChatBackend)

    def test_object_without_stream_does_not_satisfy_protocol(self) -> None:
        class NotAChat:
            def generate(self, request):
                return ""

        assert not isinstance(NotAChat(), ChatBackend)

    def test_optional_attributes_are_not_required(self) -> None:
        """id / model / supports_thinking are read via getattr, never required."""

        class Bare:
            def stream(self, request):
                yield from ()

        assert isinstance(Bare(), ChatBackend)
        assert getattr(Bare(), "supports_thinking", False) is False


# ---------------------------------------------------------------------------
# BackendRegistry — chat namespace
# ---------------------------------------------------------------------------


class TestBackendRegistryChat:
    def setup_method(self) -> None:
        BackendRegistry.reset()

    def teardown_method(self) -> None:
        BackendRegistry.reset()

    def test_register_and_retrieve(self) -> None:
        BackendRegistry.register_chat("fake", lambda: FakeChatBackend([text_turn("hi")]))
        assert isinstance(BackendRegistry.chat("fake"), FakeChatBackend)

    def test_retrieval_caches_instance(self) -> None:
        BackendRegistry.register_chat("fake", lambda: FakeChatBackend([]))
        assert BackendRegistry.chat("fake") is BackendRegistry.chat("fake")

    def test_unknown_name_raises_key_error_listing_known(self) -> None:
        BackendRegistry.register_chat("known", lambda: FakeChatBackend([]))
        with pytest.raises(KeyError, match="nonexistent.*known"):
            BackendRegistry.chat("nonexistent")

    def test_re_register_clears_cached_instance(self) -> None:
        BackendRegistry.register_chat("fake", lambda: FakeChatBackend([text_turn("first")]))
        first = BackendRegistry.chat("fake")
        BackendRegistry.register_chat("fake", lambda: FakeChatBackend([text_turn("second")]))
        second = BackendRegistry.chat("fake")
        assert first is not second
        assert collect(second.stream(make_request())).text == "second"

    def test_reset_clears_registrations_and_instances(self) -> None:
        BackendRegistry.register_chat("fake", lambda: FakeChatBackend([]))
        _ = BackendRegistry.chat("fake")
        BackendRegistry.reset()
        assert BackendRegistry.chat_ids() == []
        with pytest.raises(KeyError):
            BackendRegistry.chat("fake")

    def test_chat_ids_lists_in_registration_order_without_instantiating(self) -> None:
        constructed: list[str] = []

        def factory() -> FakeChatBackend:
            constructed.append("built")
            return FakeChatBackend([])

        BackendRegistry.register_chat("b", factory)
        BackendRegistry.register_chat("a", factory)
        assert BackendRegistry.chat_ids() == ["b", "a"]
        assert constructed == []

    def test_chat_namespace_is_separate_from_llm(self) -> None:
        BackendRegistry.register_chat("shared", lambda: FakeChatBackend([]))
        with pytest.raises(KeyError):
            BackendRegistry.llm("shared")


# ---------------------------------------------------------------------------
# FakeChatBackend
# ---------------------------------------------------------------------------


class TestFakeChatBackend:
    def test_streaming_assembly_equals_scripted_blocks(self) -> None:
        turn = [
            {"type": "thinking", "thinking": "plan", "signature": "sig"},
            {"type": "text", "text": "Let me check."},
            {"type": "tool_use", "id": "toolu_given", "name": "validate_level", "input": {"level_id": "l6"}},
        ]
        fake = FakeChatBackend([turn])
        resp = collect(fake.stream(make_request()))
        assert resp.content == turn
        assert resp.stop_reason == "tool_use"
        assert resp.usage == Usage()
        assert resp.model == "fake-chat"
        assert resp.stop_details is None

    def test_event_order_contract(self) -> None:
        fake = FakeChatBackend(
            [
                [
                    {"type": "thinking", "thinking": "plan", "signature": "sig"},
                    {"type": "text", "text": "hello"},
                    {"type": "tool_use", "name": "t", "input": {}},
                ]
            ]
        )
        events = list(fake.stream(make_request()))
        assert isinstance(events[0], MessageStart)
        assert isinstance(events[-1], MessageStop)
        kinds = [type(e).__name__ for e in events[1:-1]]
        assert kinds == [
            "ThinkingDelta",
            "ContentBlockDone",
            "TextDelta",
            "TextDelta",
            "ContentBlockDone",
            "ToolUseStart",
            "ToolInputDelta",
            "ContentBlockDone",
        ]
        assert [e.index for e in events[1:-1]] == [0, 0, 1, 1, 1, 2, 2, 2]
        assert events[1].text == "plan"

    def test_text_arrives_in_at_least_two_deltas_that_reassemble(self) -> None:
        fake = FakeChatBackend([text_turn("The exit is unreachable.")])
        deltas = [e for e in fake.stream(make_request()) if isinstance(e, TextDelta)]
        assert len(deltas) >= 2
        assert "".join(d.text for d in deltas) == "The exit is unreachable."

    def test_tool_ids_auto_assigned_globally_per_backend(self) -> None:
        fake = FakeChatBackend(
            [
                [{"type": "tool_use", "name": "a", "input": {"x": 1}}],
                [{"type": "tool_use", "name": "b", "input": {}}, {"type": "tool_use", "name": "c", "input": {}}],
            ]
        )
        first = collect(fake.stream(make_request()))
        second = collect(fake.stream(make_request()))
        assert [t["id"] for t in first.tool_uses] == ["toolu_fake_1"]
        assert [t["id"] for t in second.tool_uses] == ["toolu_fake_2", "toolu_fake_3"]
        starts = [e for e in FakeChatBackend([[{"type": "tool_use", "name": "a", "input": {"x": 1}}]]).stream(
            make_request()
        )]
        input_delta = next(e for e in starts if isinstance(e, ToolInputDelta))
        assert json.loads(input_delta.partial_json) == {"x": 1}
        assert input_delta.id == "toolu_fake_1"

    def test_script_is_not_mutated(self) -> None:
        block = {"type": "tool_use", "name": "a", "input": {}}
        fake = FakeChatBackend([[block]])
        collect(fake.stream(make_request()))
        assert "id" not in block

    def test_list_exhaustion_raises_index_error(self) -> None:
        fake = FakeChatBackend([text_turn("only")])
        collect(fake.stream(make_request()))
        with pytest.raises(IndexError):
            fake.stream(make_request())

    def test_dict_turn_scripts_refusal_with_stop_details(self) -> None:
        details = {"type": "refusal", "category": "cyber", "explanation": "nope"}
        fake = FakeChatBackend([{"content": text_turn("I can't."), "stop_reason": "refusal", "stop_details": details}])
        resp = collect(fake.stream(make_request()))
        assert resp.stop_reason == "refusal"
        assert resp.stop_details == details
        assert resp.text == "I can't."

    def test_dict_turn_defaults_stop_reason(self) -> None:
        fake = FakeChatBackend([{"content": text_turn("x")}])
        assert collect(fake.stream(make_request())).stop_reason == "end_turn"

    def test_callable_mode_receives_request(self) -> None:
        seen: list[ChatRequest] = []

        def script(req: ChatRequest) -> list[dict]:
            seen.append(req)
            return text_turn(f"echo:{req.messages[-1]['content']}")

        fake = FakeChatBackend(script)
        assert collect(fake.stream(make_request("ping"))).text == "echo:ping"
        assert collect(fake.stream(make_request("pong"))).text == "echo:pong"
        assert len(seen) == 2

    def test_calls_recorded_in_order(self) -> None:
        fake = FakeChatBackend([text_turn("a"), text_turn("b")])
        r1, r2 = make_request("one"), make_request("two")
        collect(fake.stream(r1))
        collect(fake.stream(r2))
        assert fake.calls == [r1, r2]

    def test_unsupported_turns_type_raises(self) -> None:
        with pytest.raises(TypeError):
            FakeChatBackend("nope").stream(make_request())  # type: ignore[arg-type]

    def test_close_mid_stream_is_clean(self) -> None:
        fake = FakeChatBackend([text_turn("hello world")])
        gen = fake.stream(make_request())
        assert isinstance(next(gen), MessageStart)
        gen.close()
        with pytest.raises(StopIteration):
            next(gen)

    def test_identity_attributes(self) -> None:
        fake = FakeChatBackend([], model="scripted")
        assert fake.id == "fake"
        assert fake.model == "scripted"
        assert fake.supports_thinking is True

    def test_deterministic_message_ids(self) -> None:
        fake = FakeChatBackend([text_turn("a"), text_turn("b")])
        starts = [next(fake.stream(make_request())).message_id for _ in range(2)]
        assert starts == ["msg_fake_1", "msg_fake_2"]


# ---------------------------------------------------------------------------
# AnthropicChatBackend — under importorskip, injected fake SDK client
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sdk():
    return pytest.importorskip("anthropic")


class _FakeStream:
    """What the SDK's ``MessageStreamManager.__enter__`` returns: iterable of
    events. ``error_after`` raises mid-iteration after that many events —
    where a real stream surfaces a dropped connection."""

    request_id = "req_fake_001"

    def __init__(self, events: list, error: Exception | None = None, error_after: int | None = None) -> None:
        self._events = events
        self._error = error
        self._error_after = error_after

    def __iter__(self):
        for n, event in enumerate(self._events):
            if self._error is not None and self._error_after is not None and n == self._error_after:
                raise self._error
            yield event


class _FakeStreamManager:
    """Mirrors ``anthropic.lib.streaming.MessageStreamManager``: lazy — the
    request is made in ``__enter__`` (``raw_stream = self.__api_request()``),
    so that is where a status/connection error is raised, never in
    ``messages.stream(...)`` itself."""

    def __init__(self, events: list, log: list[str], error: Exception | None, error_after: int | None) -> None:
        self._events = events
        self._log = log
        self._error = error
        self._error_after = error_after

    def __enter__(self) -> _FakeStream:
        self._log.append("enter")
        if self._error is not None and self._error_after is None:
            raise self._error
        return _FakeStream(self._events, self._error, self._error_after)

    def __exit__(self, exc_type, exc, tb) -> None:
        self._log.append("exit")


class _FakeMessages:
    def __init__(self, events: list, log: list[str], error: Exception | None, error_after: int | None) -> None:
        self._events = events
        self._log = log
        self._error = error
        self._error_after = error_after
        self.calls: list[dict] = []

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeStreamManager(self._events, self._log, self._error, self._error_after)


class FakeSDKClient:
    """Stand-in for ``anthropic.Anthropic`` exposing ``messages.stream(**kwargs)``.

    ``error`` is raised on ``__enter__`` (the real request point); with
    ``error_after=N`` it is raised instead while iterating, after N events.
    """

    def __init__(
        self, events: list | None = None, error: Exception | None = None, error_after: int | None = None
    ) -> None:
        self.log: list[str] = []
        self.messages = _FakeMessages(list(events or []), self.log, error, error_after)


def _sdk_message(
    *,
    content: list,
    stop_reason: str | None = None,
    stop_details=None,
    usage=None,
    model: str = "claude-opus-5",
    message_id: str = "msg_01",
):
    from anthropic.types import Message
    from anthropic.types import Usage as SdkUsage

    return Message(
        id=message_id,
        type="message",
        role="assistant",
        model=model,
        content=content,
        stop_reason=stop_reason,
        stop_details=stop_details,
        usage=usage or SdkUsage(input_tokens=1, output_tokens=1),
    )


def _mixed_turn_events() -> list:
    """thinking (0) → text (1, two deltas) → tool_use (2, two json deltas) → stop tool_use.

    Includes the raw wire events the SDK also fires (content_block_delta,
    message_delta, signature) to prove they are ignored rather than doubled.
    """
    from anthropic.lib.streaming._types import (
        ContentBlockStopEvent,
        InputJsonEvent,
        MessageStopEvent,
        SignatureEvent,
        TextEvent,
        ThinkingEvent,
    )
    from anthropic.types import (
        MessageDeltaUsage,
        RawContentBlockDeltaEvent,
        RawContentBlockStartEvent,
        RawMessageDeltaEvent,
        RawMessageStartEvent,
        TextBlock,
        ThinkingBlock,
        ToolUseBlock,
    )
    from anthropic.types import TextDelta as SdkTextDelta
    from anthropic.types import Usage as SdkUsage
    from anthropic.types.raw_message_delta_event import Delta

    thinking_final = ThinkingBlock(type="thinking", thinking="plan: validate", signature="sig_abc")
    text_final = TextBlock(type="text", text="Let me check.")
    tool_final = ToolUseBlock(type="tool_use", id="toolu_01", name="validate_level", input={"level_id": "l6"})
    return [
        RawMessageStartEvent(
            type="message_start",
            message=_sdk_message(content=[], usage=SdkUsage(input_tokens=12, output_tokens=1)),
        ),
        RawContentBlockStartEvent(
            type="content_block_start",
            index=0,
            content_block=ThinkingBlock(type="thinking", thinking="", signature=""),
        ),
        ThinkingEvent(type="thinking", thinking="plan: validate", snapshot="plan: validate"),
        SignatureEvent(type="signature", signature="sig_abc"),
        ContentBlockStopEvent(type="content_block_stop", index=0, content_block=thinking_final),
        RawContentBlockStartEvent(type="content_block_start", index=1, content_block=TextBlock(type="text", text="")),
        RawContentBlockDeltaEvent(
            type="content_block_delta", index=1, delta=SdkTextDelta(type="text_delta", text="Let me ")
        ),
        TextEvent(type="text", text="Let me ", snapshot="Let me "),
        TextEvent(type="text", text="check.", snapshot="Let me check."),
        ContentBlockStopEvent(type="content_block_stop", index=1, content_block=text_final),
        RawContentBlockStartEvent(
            type="content_block_start",
            index=2,
            content_block=ToolUseBlock(type="tool_use", id="toolu_01", name="validate_level", input={}),
        ),
        InputJsonEvent(type="input_json", partial_json='{"level_id"', snapshot={}),
        InputJsonEvent(type="input_json", partial_json=': "l6"}', snapshot={"level_id": "l6"}),
        ContentBlockStopEvent(type="content_block_stop", index=2, content_block=tool_final),
        RawMessageDeltaEvent(
            type="message_delta",
            delta=Delta(stop_reason="tool_use"),
            usage=MessageDeltaUsage(output_tokens=30),
        ),
        MessageStopEvent(
            type="message_stop",
            message=_sdk_message(
                content=[thinking_final, text_final, tool_final],
                stop_reason="tool_use",
                usage=SdkUsage(
                    input_tokens=12,
                    output_tokens=30,
                    cache_read_input_tokens=None,
                    cache_creation_input_tokens=7,
                ),
            ),
        ),
    ]


def _text_turn_events(text: str = "Hello", *, stop_reason: str = "end_turn", stop_details=None) -> list:
    from anthropic.lib.streaming._types import ContentBlockStopEvent, MessageStopEvent, TextEvent
    from anthropic.types import RawContentBlockStartEvent, RawMessageStartEvent, TextBlock
    from anthropic.types import Usage as SdkUsage

    final = TextBlock(type="text", text=text)
    return [
        RawMessageStartEvent(type="message_start", message=_sdk_message(content=[])),
        RawContentBlockStartEvent(type="content_block_start", index=0, content_block=TextBlock(type="text", text="")),
        TextEvent(type="text", text=text[:2], snapshot=text[:2]),
        TextEvent(type="text", text=text[2:], snapshot=text),
        ContentBlockStopEvent(type="content_block_stop", index=0, content_block=final),
        MessageStopEvent(
            type="message_stop",
            message=_sdk_message(
                content=[final],
                stop_reason=stop_reason,
                stop_details=stop_details,
                usage=SdkUsage(input_tokens=5, output_tokens=3, cache_read_input_tokens=4),
            ),
        ),
    ]


def _backend(sdk, client: FakeSDKClient, **kwargs):
    from canon.backends.chat_anthropic import AnthropicChatBackend

    return AnthropicChatBackend(client=client, **kwargs)


class TestAnthropicChatBackendKwargs:
    def test_minimal_request_omits_optional_keys(self, sdk) -> None:
        client = FakeSDKClient(_text_turn_events())
        backend = _backend(sdk, client, model="claude-opus-5", fallbacks=False)
        collect(backend.stream(ChatRequest(messages=[{"role": "user", "content": "hi"}], thinking=False)))
        (kwargs,) = client.messages.calls
        assert kwargs == {
            "model": "claude-opus-5",
            "max_tokens": 8192,
            "messages": [{"role": "user", "content": "hi"}],
        }

    def test_fallbacks_on_by_default_ride_extra_headers_and_body(self, sdk) -> None:
        # SDK 0.98.x has no `fallbacks`/`betas` on messages.stream — the scalar
        # "default" form pairs with the -2026-07-01 header (never -2026-06-01).
        client = FakeSDKClient(_text_turn_events())
        backend = _backend(sdk, client)
        collect(backend.stream(make_request()))
        (kwargs,) = client.messages.calls
        assert kwargs["extra_headers"] == {"anthropic-beta": "server-side-fallback-2026-07-01"}
        assert kwargs["extra_body"] == {"fallbacks": "default"}
        assert "fallbacks" not in kwargs and "betas" not in kwargs

    def test_fallbacks_off_omits_both_extra_keys(self, sdk) -> None:
        client = FakeSDKClient(_text_turn_events())
        backend = _backend(sdk, client, fallbacks=False)
        collect(backend.stream(make_request()))
        (kwargs,) = client.messages.calls
        assert "extra_headers" not in kwargs and "extra_body" not in kwargs

    def test_full_request_mapping(self, sdk) -> None:
        client = FakeSDKClient(_text_turn_events())
        backend = _backend(sdk, client, model="claude-opus-5")
        req = ChatRequest(
            system="be brief",
            messages=[
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "ok"}]},
            ],
            tools=[VALIDATE],
            model="claude-sonnet-5",
            max_tokens=999,
            thinking=True,
            effort="high",
            tool_choice="none",
            metadata={"conversation": "c1", "actor": "agent:wick"},
        )
        collect(backend.stream(req))
        (kwargs,) = client.messages.calls
        assert kwargs["model"] == "claude-sonnet-5"  # request.model overrides the constructed model
        assert kwargs["max_tokens"] == 999
        assert kwargs["system"] == "be brief"
        assert kwargs["messages"] == req.messages
        assert kwargs["tools"] == [
            {"name": "validate_level", "description": "validate", "input_schema": VALIDATE.input_schema}
        ]
        assert kwargs["thinking"] == {"type": "adaptive"}
        assert kwargs["output_config"] == {"effort": "high"}
        assert kwargs["tool_choice"] == {"type": "none"}
        assert "metadata" not in kwargs
        assert "conversation" not in json.dumps(kwargs)

    def test_thinking_absent_when_disabled_never_disabled_literal(self, sdk) -> None:
        client = FakeSDKClient(_text_turn_events())
        backend = _backend(sdk, client)
        collect(backend.stream(make_request(thinking=False)))
        assert "thinking" not in client.messages.calls[0]

    def test_thinking_override_config(self, sdk) -> None:
        client = FakeSDKClient(_text_turn_events())
        backend = _backend(sdk, client, thinking={"type": "adaptive", "display": "summarized"})
        collect(backend.stream(make_request(thinking=True)))
        assert client.messages.calls[0]["thinking"] == {"type": "adaptive", "display": "summarized"}

    def test_output_config_only_with_effort(self, sdk) -> None:
        client = FakeSDKClient(_text_turn_events())
        backend = _backend(sdk, client)
        collect(backend.stream(make_request(effort=None)))
        assert "output_config" not in client.messages.calls[0]

    def test_tool_choice_auto_omitted(self, sdk) -> None:
        client = FakeSDKClient(_text_turn_events())
        backend = _backend(sdk, client)
        collect(backend.stream(make_request(tool_choice="auto")))
        assert "tool_choice" not in client.messages.calls[0]

    @pytest.mark.parametrize("choice", ["any", "tool", "validate_level"])
    def test_forced_tool_choice_raises_value_error(self, sdk, choice: str) -> None:
        client = FakeSDKClient(_text_turn_events())
        backend = _backend(sdk, client)
        with pytest.raises(ValueError, match="tool_choice"):
            next(backend.stream(make_request(tool_choice=choice)))
        assert client.messages.calls == []

    def test_default_model_constant(self, sdk) -> None:
        from canon.backends.chat_anthropic import DEFAULT_CHAT_MODEL

        client = FakeSDKClient(_text_turn_events())
        backend = _backend(sdk, client)
        # Code default is Sonnet (user decision 2026-09-01); the effective
        # default resolves project → cradle settings → this constant at P0-12/A5.
        assert DEFAULT_CHAT_MODEL == "claude-sonnet-5"
        assert backend.model == DEFAULT_CHAT_MODEL
        collect(backend.stream(make_request()))
        assert client.messages.calls[0]["model"] == "claude-sonnet-5"


class TestAnthropicChatBackendEvents:
    def test_translation_of_mixed_turn(self, sdk) -> None:
        client = FakeSDKClient(_mixed_turn_events())
        backend = _backend(sdk, client)
        events = list(backend.stream(make_request(tools=[VALIDATE])))

        assert isinstance(events[0], MessageStart)
        assert events[0].model == "claude-opus-5" and events[0].message_id == "msg_01"

        thinking = [e for e in events if isinstance(e, ThinkingDelta)]
        assert [(e.index, e.text) for e in thinking] == [(0, "plan: validate")]

        text = [e for e in events if isinstance(e, TextDelta)]
        assert [(e.index, e.text) for e in text] == [(1, "Let me "), (1, "check.")]  # raw delta not doubled

        starts = [e for e in events if isinstance(e, ToolUseStart)]
        assert [(e.index, e.id, e.name) for e in starts] == [(2, "toolu_01", "validate_level")]
        inputs = [e for e in events if isinstance(e, ToolInputDelta)]
        assert [(e.index, e.id, e.partial_json) for e in inputs] == [
            (2, "toolu_01", '{"level_id"'),
            (2, "toolu_01", ': "l6"}'),
        ]

        done = [e for e in events if isinstance(e, ContentBlockDone)]
        assert [d.index for d in done] == [0, 1, 2]
        assert done[0].block == {"type": "thinking", "thinking": "plan: validate", "signature": "sig_abc"}
        assert done[1].block == {"type": "text", "text": "Let me check."}
        assert done[2].block == {
            "type": "tool_use",
            "id": "toolu_01",
            "name": "validate_level",
            "input": {"level_id": "l6"},
        }

        stop = events[-1]
        assert isinstance(stop, MessageStop)
        assert stop.stop_reason == "tool_use"
        assert stop.stop_details is None
        assert stop.usage == Usage(
            input_tokens=12, output_tokens=30, cache_read_input_tokens=0, cache_creation_input_tokens=7
        )
        assert stop.content == [done[0].block, done[1].block, done[2].block]
        assert sum(isinstance(e, MessageStop) for e in events) == 1

    def test_collect_assembles_response(self, sdk) -> None:
        backend = _backend(sdk, FakeSDKClient(_mixed_turn_events()))
        resp = collect(backend.stream(make_request()))
        assert resp.text == "Let me check."
        assert resp.tool_uses[0]["input"] == {"level_id": "l6"}
        assert resp.model == "claude-opus-5"
        assert resp.content[0]["type"] == "thinking" and resp.content[0]["signature"] == "sig_abc"

    def test_end_turn_with_cache_read(self, sdk) -> None:
        backend = _backend(sdk, FakeSDKClient(_text_turn_events("Hello")))
        resp = collect(backend.stream(make_request()))
        assert resp.stop_reason == "end_turn"
        assert resp.text == "Hello"
        assert resp.usage == Usage(input_tokens=5, output_tokens=3, cache_read_input_tokens=4)

    def test_refusal_with_stop_details(self, sdk) -> None:
        from anthropic.types import RefusalStopDetails

        details = RefusalStopDetails(type="refusal", category="cyber", explanation="policy")
        backend = _backend(sdk, FakeSDKClient(_text_turn_events("No.", stop_reason="refusal", stop_details=details)))
        resp = collect(backend.stream(make_request()))
        assert resp.stop_reason == "refusal"
        assert resp.stop_details == {"type": "refusal", "category": "cyber", "explanation": "policy"}

    def test_refusal_stop_details_drop_null_fields(self, sdk) -> None:
        from anthropic.types import RefusalStopDetails

        details = RefusalStopDetails(type="refusal", category=None, explanation=None)
        backend = _backend(sdk, FakeSDKClient(_text_turn_events("No.", stop_reason="refusal", stop_details=details)))
        resp = collect(backend.stream(make_request()))
        assert resp.stop_details == {"type": "refusal"}  # same exclude_none rule as the block dumps

    @pytest.mark.parametrize("shape", ["sdk_construct", "namespace"])
    def test_unknown_fallback_block_passes_through(self, sdk, shape: str) -> None:
        """A refusal-fallback response can carry a ``fallback`` block this SDK
        does not model. Two shapes: what the SDK's loose ``construct`` yields
        for an unknown type (a pydantic object whose ``type`` is "fallback"),
        and a plain object with only ``.type`` (the getattr/vars path)."""
        from types import SimpleNamespace

        from anthropic.lib.streaming._types import ContentBlockStopEvent, MessageStopEvent, TextEvent
        from anthropic.types import Message, RawContentBlockStartEvent, RawMessageStartEvent, TextBlock
        from anthropic.types import Usage as SdkUsage

        raw = {"type": "fallback", "from": {"model": "claude-sonnet-5"}, "to": {"model": "claude-opus-4-8"}}
        served = "claude-opus-4-8"
        text_final = TextBlock(type="text", text="Hello")
        if shape == "sdk_construct":
            final_message = Message.construct(
                id="msg_fb",
                type="message",
                role="assistant",
                model=served,
                content=[raw, text_final],
                stop_reason="end_turn",
                stop_details=None,
                usage=SdkUsage(input_tokens=5, output_tokens=3),
            )
            fallback_block = final_message.content[0]
            assert not isinstance(fallback_block, dict)  # the SDK built *something* for it
            start = RawContentBlockStartEvent.construct(
                type="content_block_start", index=0, content_block=fallback_block
            )
            stop = ContentBlockStopEvent.construct(type="content_block_stop", index=0, content_block=fallback_block)
        else:
            fallback_block = SimpleNamespace(**raw)
            final_message = _sdk_message(
                content=[text_final],
                stop_reason="end_turn",
                model=served,
                usage=SdkUsage(input_tokens=5, output_tokens=3),
            )
            final_message.content = [fallback_block, text_final]  # bypass validation, like a newer wire shape
            start = SimpleNamespace(type="content_block_start", index=0, content_block=fallback_block)
            stop = SimpleNamespace(type="content_block_stop", index=0, content_block=fallback_block)
        events = [
            RawMessageStartEvent(type="message_start", message=_sdk_message(content=[], model=served)),
            start,
            stop,
            RawContentBlockStartEvent(
                type="content_block_start", index=1, content_block=TextBlock(type="text", text="")
            ),
            TextEvent(type="text", text="Hel", snapshot="Hel"),
            TextEvent(type="text", text="lo", snapshot="Hello"),
            ContentBlockStopEvent(type="content_block_stop", index=1, content_block=text_final),
            MessageStopEvent.construct(type="message_stop", message=final_message),
        ]
        backend = _backend(sdk, FakeSDKClient(events))
        seen = list(backend.stream(make_request()))
        resp = collect(seen)

        done = [e for e in seen if isinstance(e, ContentBlockDone)]
        assert done[0].block["type"] == "fallback"
        assert done[0].block["from"] == {"model": "claude-sonnet-5"} and done[0].block["to"] == {"model": served}
        assert resp.content[0] == done[0].block
        assert resp.content[1] == {"type": "text", "text": "Hello"}
        assert resp.text == "Hello"  # the unknown block never pollutes text assembly
        assert resp.stop_reason == "end_turn"
        # The served model is what message.model reports; MessageStart carries it.
        assert resp.model == served and seen[0].model == served

    def test_missing_stop_reason_defaults_to_end_turn(self, sdk) -> None:
        events = _text_turn_events("x")
        events[-1].message.stop_reason = None
        resp = collect(_backend(sdk, FakeSDKClient(events)).stream(make_request()))
        assert resp.stop_reason == "end_turn"

    def test_cancel_closes_sdk_context(self, sdk) -> None:
        client = FakeSDKClient(_text_turn_events("Hello world"))
        backend = _backend(sdk, client)
        gen = backend.stream(make_request())
        first_text = None
        for event in gen:
            if isinstance(event, TextDelta):
                first_text = event
                break
        assert first_text is not None
        assert client.log == ["enter"]
        gen.close()
        assert client.log == ["enter", "exit"]

    def test_stream_context_exits_after_normal_completion(self, sdk) -> None:
        client = FakeSDKClient(_text_turn_events())
        collect(_backend(sdk, client).stream(make_request()))
        assert client.log == ["enter", "exit"]


class TestAnthropicChatBackendErrors:
    @staticmethod
    def _request(sdk):
        import httpx

        return httpx.Request("POST", "https://api.anthropic.com/v1/messages")

    def _status_error(self, sdk, cls, status: int, request_id: str = "req_err"):
        import httpx

        request = self._request(sdk)
        response = httpx.Response(status, request=request, headers={"request-id": request_id})
        return cls("boom", response=response, body=None)

    def _raise(self, sdk, error: Exception) -> ChatError:
        backend = _backend(sdk, FakeSDKClient(error=error))
        with pytest.raises(ChatError) as exc_info:
            collect(backend.stream(make_request()))
        assert exc_info.value.__cause__ is error
        return exc_info.value

    def test_rate_limit_is_retryable(self, sdk) -> None:
        err = self._raise(sdk, self._status_error(sdk, sdk.RateLimitError, 429, "req_429"))
        assert err.retryable is True
        assert err.status == 429
        assert err.request_id == "req_429"

    def test_bad_request_not_retryable(self, sdk) -> None:
        err = self._raise(sdk, self._status_error(sdk, sdk.BadRequestError, 400))
        assert err.retryable is False
        assert err.status == 400

    def test_auth_and_not_found_not_retryable(self, sdk) -> None:
        assert self._raise(sdk, self._status_error(sdk, sdk.AuthenticationError, 401)).retryable is False
        assert self._raise(sdk, self._status_error(sdk, sdk.NotFoundError, 404)).retryable is False

    def test_connection_error_retryable_without_status(self, sdk) -> None:
        err = self._raise(sdk, sdk.APIConnectionError(request=self._request(sdk)))
        assert err.retryable is True
        assert err.status is None

    def test_timeout_retryable(self, sdk) -> None:
        assert self._raise(sdk, sdk.APITimeoutError(request=self._request(sdk))).retryable is True

    def test_internal_server_error_retryable(self, sdk) -> None:
        assert self._raise(sdk, self._status_error(sdk, sdk.InternalServerError, 500)).retryable is True

    def test_generic_status_error_retryable_iff_5xx(self, sdk) -> None:
        assert self._raise(sdk, self._status_error(sdk, sdk.APIStatusError, 503)).retryable is True
        assert self._raise(sdk, self._status_error(sdk, sdk.APIStatusError, 409)).retryable is False

    def test_error_on_enter_is_translated_with_nothing_to_close(self, sdk) -> None:
        # The real manager makes the HTTP request inside __enter__; when that
        # raises, no stream was opened and Python never calls __exit__.
        client = FakeSDKClient(error=self._status_error(sdk, sdk.RateLimitError, 429, "req_enter"))
        with pytest.raises(ChatError) as exc_info:
            collect(_backend(sdk, client).stream(make_request()))
        assert exc_info.value.request_id == "req_enter"
        assert client.log == ["enter"]

    def test_mid_stream_connection_drop_closes_once_and_chains(self, sdk) -> None:
        error = sdk.APIConnectionError(request=self._request(sdk))
        client = FakeSDKClient(_text_turn_events("Hello world"), error=error, error_after=3)
        backend = _backend(sdk, client)
        seen: list = []
        with pytest.raises(ChatError) as exc_info:
            for event in backend.stream(make_request()):
                seen.append(event)
        assert any(isinstance(e, TextDelta) for e in seen)  # the drop came after text had landed
        assert exc_info.value.retryable is True
        assert exc_info.value.__cause__ is error
        assert client.log == ["enter", "exit"]

    def test_missing_credential_type_error_is_named_not_retryable(self, sdk) -> None:
        error = TypeError('"Could not resolve authentication method. Expected one of api_key, auth_token, ..."')
        backend = _backend(sdk, FakeSDKClient(error=error))
        with pytest.raises(ChatError) as exc_info:
            collect(backend.stream(make_request()))
        assert exc_info.value.retryable is False
        assert "ANTHROPIC_API_KEY" in str(exc_info.value)
        assert exc_info.value.__cause__ is error

    def test_non_sdk_exception_propagates_unchanged(self, sdk) -> None:
        backend = _backend(sdk, FakeSDKClient(error=RuntimeError("not the sdk")))
        with pytest.raises(RuntimeError, match="not the sdk"):
            collect(backend.stream(make_request()))


class TestAnthropicChatBackendRegistration:
    def setup_method(self) -> None:
        BackendRegistry.reset()

    def teardown_method(self) -> None:
        BackendRegistry.reset()

    def test_not_registered_at_import_time(self, sdk) -> None:
        import canon.backends.chat_anthropic  # noqa: F401

        assert "anthropic" not in BackendRegistry.chat_ids()

    def test_register_is_explicit_and_idempotent(self, sdk) -> None:
        from canon.backends.chat_anthropic import register

        register()
        register("claude-sonnet-5")
        assert BackendRegistry.chat_ids() == ["anthropic"]

    def test_implements_protocol_and_identity(self, sdk) -> None:
        backend = _backend(sdk, FakeSDKClient())
        assert isinstance(backend, ChatBackend)
        assert backend.id == "anthropic"
        assert backend.supports_thinking is True

    def test_lazy_export_from_package(self, sdk) -> None:
        import canon.backends

        assert canon.backends.AnthropicChatBackend is not None
        assert "AnthropicChatBackend" in canon.backends.__all__


# ---------------------------------------------------------------------------
# The no-pricing gate — A1 ships NO pricing data
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module_name",
    [
        "canon.llm.chat",
        "canon.backends.chat_anthropic",
        "canon.backends.chat_openai",
        "canon.agent",
        "canon.agent.loop",
        "canon.agent.evals",
        "canon.agent.eval",
    ],
)
def test_no_pricing_symbol_in_chat_modules(module_name: str) -> None:
    module = importlib.import_module(module_name)
    assert not hasattr(module, "PRICING"), f"{module_name} must not carry a price table (that is the §3.0-C module's)"
    source = inspect.getsource(module)
    assert "PRICING" not in source
    assert "last_cost" not in source
    assert not any(name.lower().endswith("_cost") for name in dir(module)), module_name


def test_fake_chat_backend_has_no_cost_attributes() -> None:
    fake = FakeChatBackend([])
    assert not any("cost" in name for name in vars(fake))
