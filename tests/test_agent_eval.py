"""Tests for the agent loop driver and the scripted-conversation eval (Phase 1 A1).

Everything runs on ``FakeChatBackend`` — hermetic, keyless, $0.
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace

import pytest

from canon.agent.eval import FAKE_COST_NOTE, EvalResult, main, run_scripted
from canon.agent.evals import CONVERSATIONS, ScriptedConversation, conversation
from canon.agent.loop import MAX_TOOL_ROUNDS_STOP, ConversationResult, run_conversation
from canon.backends import BackendRegistry, FakeChatBackend
from canon.llm.chat import (
    ChatError,
    ChatRequest,
    MessageStart,
    MessageStop,
    ToolSpec,
    Usage,
    collect,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PROBE = ToolSpec(name="probe", description="probe", input_schema={"type": "object", "properties": {}})


def run_builtin(name: str, **kwargs) -> tuple[ConversationResult, FakeChatBackend]:
    conv = conversation(name)
    fake = FakeChatBackend(conv.fake_turns)

    def executor(tool: str, tool_input: dict):
        spec = conv.tool_results[tool]
        return spec(tool_input) if callable(spec) else spec

    result = run_conversation(
        fake,
        system=conv.system,
        tools=conv.tools,
        tool_executor=executor,
        user_messages=conv.user_messages,
        **kwargs,
    )
    return result, fake


def tool_results_of(message: dict) -> list[dict]:
    return [b for b in message["content"] if b.get("type") == "tool_result"]


# ---------------------------------------------------------------------------
# Built-in corpus
# ---------------------------------------------------------------------------


class TestBuiltinConversations:
    def test_corpus_has_the_five_named_conversations(self) -> None:
        names = [c.name for c in CONVERSATIONS]
        required_names = [
            "unbeatable-level",
            "parallel-reads",
            "just-talking",
            "tool-error-recovers",
            "refusal-surfaces",
        ]
        for required in required_names:
            assert required in names
        assert len(names) == len(set(names))

    @pytest.mark.parametrize("conv", CONVERSATIONS, ids=[c.name for c in CONVERSATIONS])
    def test_every_conversation_passes_on_the_fake(self, conv: ScriptedConversation) -> None:
        result = run_scripted(conv, FakeChatBackend(conv.fake_turns))
        assert result.failures == []
        assert result.passed is True
        assert result.tool_calls == [call["name"] for call in conv.expected_tool_calls]
        assert result.usage == Usage()
        assert result.cost_note == FAKE_COST_NOTE

    def test_fake_turn_count_matches_assistant_turns(self) -> None:
        for conv in CONVERSATIONS:
            fake = FakeChatBackend(conv.fake_turns)
            run_scripted(conv, fake)
            assert len(fake.calls) == len(conv.fake_turns), conv.name

    def test_conversation_lookup_unknown_name(self) -> None:
        with pytest.raises(KeyError, match="bogus"):
            conversation("bogus")


# ---------------------------------------------------------------------------
# Loop behaviour through the scripted conversations
# ---------------------------------------------------------------------------


class TestLoopUnbeatableLevel:
    def test_tool_order_and_final_text(self) -> None:
        result, fake = run_builtin("unbeatable-level")
        assert [s["tool"] for s in result.steps] == ["validate_level", "describe_level"]
        assert result.steps[0]["input"] == {"level_id": "l6"}
        assert "unreachable" in result.texts[-1]
        assert result.stop_reasons == ["end_turn"]
        assert len(fake.calls) == 3

    def test_dict_tool_result_is_json_encoded(self) -> None:
        result, _ = run_builtin("unbeatable-level")
        report = json.loads(result.steps[0]["result"])
        assert report["ok"] is False
        assert report["findings"][1]["x_start"] == 41

    def test_history_is_well_formed(self) -> None:
        result, _ = run_builtin("unbeatable-level")
        roles = [m["role"] for m in result.messages]
        assert roles == ["user", "assistant", "user", "assistant", "user", "assistant"]
        assert result.messages[1]["content"][0]["type"] == "text"  # assistant turn keeps its text + tool_use


class TestLoopParallelReads:
    def test_two_tool_results_land_in_one_user_message(self) -> None:
        result, fake = run_builtin("parallel-reads")
        follow_up = fake.calls[1].messages
        assert follow_up[-1]["role"] == "user"
        results = tool_results_of(follow_up[-1])
        assert len(results) == 2
        assert len(follow_up[-1]["content"]) == 2
        uses = [b for b in follow_up[-2]["content"] if b["type"] == "tool_use"]
        assert [r["tool_use_id"] for r in results] == [u["id"] for u in uses]
        assert [s["tool"] for s in result.steps] == ["db_row", "view_asset"]
        assert "ember hopper" in result.texts[-1].lower()


class TestLoopJustTalking:
    def test_second_request_threads_first_assistant_turn(self) -> None:
        result, fake = run_builtin("just-talking")
        assert result.steps == []
        assert len(fake.calls) == 2
        second = fake.calls[1].messages
        assert [m["role"] for m in second] == ["user", "assistant", "user"]
        assert second[1] == result.messages[1]
        assert fake.calls[1].tools == []
        assert result.stop_reasons == ["end_turn", "end_turn"]
        assert len(result.texts) == 2


class TestLoopToolErrorRecovers:
    def test_executor_exception_becomes_is_error_result(self) -> None:
        result, fake = run_builtin("tool-error-recovers")
        assert result.steps[0]["is_error"] is True
        assert "FileNotFoundError" in result.steps[0]["result"]
        block = tool_results_of(fake.calls[1].messages[-1])[0]
        assert block["is_error"] is True
        assert block["content"] == result.steps[0]["result"]
        assert result.stop_reasons == ["end_turn"]
        assert "missing" in result.texts[-1]


class TestLoopRefusalSurfaces:
    def test_refusal_stops_loop_and_executes_nothing(self) -> None:
        result, fake = run_builtin("refusal-surfaces")
        assert result.stop_reasons == ["refusal"]
        assert result.steps == []
        assert len(fake.calls) == 1
        conv = conversation("refusal-surfaces")
        resp = collect(FakeChatBackend(conv.fake_turns).stream(ChatRequest()))
        assert resp.stop_reason == "refusal"
        assert resp.stop_details["type"] == "refusal"


class TestLoopGuardsAndPlumbing:
    def test_max_tool_rounds_guard_stops_a_looping_model(self) -> None:
        fake = FakeChatBackend(lambda req: [{"type": "tool_use", "name": "probe", "input": {}}])
        result = run_conversation(
            fake,
            system=None,
            tools=[PROBE],
            tool_executor=lambda name, tool_input: "again",
            user_messages=["go"],
            max_tool_rounds=3,
        )
        assert result.stop_reasons == [MAX_TOOL_ROUNDS_STOP]
        assert len(result.steps) == 3
        assert len(fake.calls) == 4

    def test_guard_trip_leaves_a_resendable_history(self) -> None:
        """After the guard trips, the unexecuted tool_use turn still gets its
        is_error tool_result, so a second user turn sends a history the API
        accepts (every tool_use answered) and the guard is reported once."""
        from canon.agent.eval import _pairing_failures

        fake = FakeChatBackend(lambda req: [{"type": "tool_use", "name": "probe", "input": {}}])
        result = run_conversation(
            fake,
            system=None,
            tools=[PROBE],
            tool_executor=lambda name, tool_input: "again",
            user_messages=["a", "b"],
            max_tool_rounds=1,
        )
        assert result.stop_reasons == [MAX_TOOL_ROUNDS_STOP, MAX_TOOL_ROUNDS_STOP]
        assert len(result.steps) == 2  # only executed calls are steps
        assert _pairing_failures(result.messages) == []
        second_turn = fake.calls[2].messages  # first request of user turn "b"
        assert [m["role"] for m in second_turn] == ["user", "assistant", "user", "assistant", "user", "user"]
        skipped = tool_results_of(second_turn[-2])
        assert len(skipped) == 1 and skipped[0]["is_error"] is True
        assert MAX_TOOL_ROUNDS_STOP in skipped[0]["content"]
        assert skipped[0]["tool_use_id"] == second_turn[-3]["content"][0]["id"]
        assert _pairing_failures(second_turn) == []

    def test_on_event_sees_every_event(self) -> None:
        seen: list = []
        _, _ = run_builtin("just-talking", on_event=seen.append)
        assert isinstance(seen[0], MessageStart)
        assert isinstance(seen[-1], MessageStop)
        assert sum(isinstance(e, MessageStop) for e in seen) == 2

    def test_request_knobs_forwarded(self) -> None:
        fake = FakeChatBackend([[{"type": "text", "text": "ok"}]])
        run_conversation(
            fake,
            system="sys",
            tools=[PROBE],
            tool_executor=lambda n, i: "",
            user_messages=["go"],
            model="claude-opus-5",
            max_tokens=123,
            thinking=False,
            effort="low",
        )
        req = fake.calls[0]
        assert req.system == "sys"
        assert req.tools == [PROBE]
        assert (req.model, req.max_tokens, req.thinking, req.effort) == ("claude-opus-5", 123, False, "low")
        assert req.metadata == {}

    def test_usage_sums_across_requests(self) -> None:
        class Counting:
            def stream(self, request: ChatRequest):
                yield MessageStart("m")
                yield MessageStop("end_turn", Usage(3, 4, 1, 2), [{"type": "text", "text": "x"}])

        result = run_conversation(
            Counting(), system=None, tools=[], tool_executor=lambda n, i: "", user_messages=["a", "b"]
        )
        assert result.usage == Usage(6, 8, 2, 4)

    def test_string_and_other_results_rendered(self) -> None:
        fake = FakeChatBackend(
            [
                [
                    {"type": "tool_use", "name": "s", "input": {}},
                    {"type": "tool_use", "name": "n", "input": {}},
                    {"type": "tool_use", "name": "l", "input": {}},
                ],
                [{"type": "text", "text": "done"}],
            ]
        )
        outputs = {"s": "plain", "n": 42, "l": [1, 2]}
        result = run_conversation(
            fake, system=None, tools=[], tool_executor=lambda n, i: outputs[n], user_messages=["go"]
        )
        assert [s["result"] for s in result.steps] == ["plain", "42", "[1, 2]"]


# ---------------------------------------------------------------------------
# run_scripted checks
# ---------------------------------------------------------------------------


class TestRunScripted:
    def test_wrong_tool_expectation_fails_with_named_failure(self) -> None:
        conv = replace(conversation("unbeatable-level"), expected_tool_calls=[{"name": "describe_level"}])
        result = run_scripted(conv, FakeChatBackend(conv.fake_turns))
        assert result.passed is False
        assert any(f.startswith("tool calls:") for f in result.failures)

    def test_wrong_input_subset_fails(self) -> None:
        base = conversation("unbeatable-level")
        calls = [dict(base.expected_tool_calls[0], input_subset={"level_id": "l7"}), base.expected_tool_calls[1]]
        result = run_scripted(replace(base, expected_tool_calls=calls), FakeChatBackend(base.fake_turns))
        assert any("lacks {'level_id': 'l7'}" in f for f in result.failures)

    def test_wrong_text_expectation_fails_and_is_freed_by_strict_text_false(self) -> None:
        conv = replace(conversation("just-talking"), expected_text_contains=["definitely not said"])
        strict = run_scripted(conv, FakeChatBackend(conv.fake_turns))
        assert strict.passed is False
        assert any(f.startswith("text:") for f in strict.failures)
        freed = run_scripted(conv, FakeChatBackend(conv.fake_turns), strict_text=False)
        assert freed.passed is True

    def test_text_check_is_case_insensitive(self) -> None:
        conv = replace(conversation("unbeatable-level"), expected_text_contains=["UNREACHABLE"])
        assert run_scripted(conv, FakeChatBackend(conv.fake_turns)).passed

    def test_stop_reason_expectation_fails_with_named_failure(self) -> None:
        conv = replace(conversation("refusal-surfaces"), expected_stop_reasons=["end_turn"])
        strict = run_scripted(conv, FakeChatBackend(conv.fake_turns))
        assert strict.passed is False
        assert any(f.startswith("stop reasons:") and "refusal" in f for f in strict.failures)
        freed = run_scripted(conv, FakeChatBackend(conv.fake_turns), strict_text=False)
        assert freed.passed is True

    def test_refusal_stop_reason_is_gated(self) -> None:
        conv = conversation("refusal-surfaces")
        assert conv.expected_stop_reasons == ["refusal"]
        relabelled = replace(conv, fake_turns=[[{"type": "text", "text": "I can't help with that."}]])
        result = run_scripted(relabelled, FakeChatBackend(relabelled.fake_turns))
        assert any(f.startswith("stop reasons:") for f in result.failures)

    def test_request_history_is_checked_not_just_loop_history(self) -> None:
        class Trimming(FakeChatBackend):
            """A fake that records a request whose history splits the pair —
            what a future history-trimming loop could send."""

            def stream(self, request: ChatRequest):
                if len(request.messages) > 2 and request.messages[-1]["role"] == "user":
                    self.calls.append(replace(request, messages=request.messages[:-1]))
                    return FakeChatBackend.stream(self, replace(request, messages=[]))
                return FakeChatBackend.stream(self, request)

        conv = conversation("parallel-reads")
        result = run_scripted(conv, Trimming(conv.fake_turns))
        assert result.passed is False
        assert any(f.startswith("request history (call 1):") for f in result.failures)

    def test_backend_crash_is_a_named_failure_not_a_traceback(self) -> None:
        class Dead:
            def stream(self, request: ChatRequest):
                raise TypeError("Could not resolve authentication method")
                yield  # pragma: no cover - makes this a generator

        result = run_scripted(conversation("just-talking"), Dead())
        assert result.passed is False
        assert result.failures == ["backend crashed (TypeError): Could not resolve authentication method"]

    def test_max_tool_rounds_is_a_named_failure(self) -> None:
        conv = ScriptedConversation(
            name="spinner",
            system="s",
            tools=[PROBE],
            tool_results={"probe": "again"},
            user_messages=["go"],
            expected_tool_calls=[{"name": "probe"}] * 8,
        )
        fake = FakeChatBackend(lambda req: [{"type": "tool_use", "name": "probe", "input": {}}])
        result = run_scripted(conv, fake)
        assert result.passed is False
        assert any(MAX_TOOL_ROUNDS_STOP in f for f in result.failures)
        # reported once — the guard leaves a paired history, so no pairing failure piles on
        assert [f for f in result.failures if "tool_use block(s) without" in f] == []

    def test_unscripted_tool_call_is_reported(self) -> None:
        conv = ScriptedConversation(
            name="unknown-tool",
            system="s",
            tools=[PROBE],
            tool_results={},
            user_messages=["go"],
            expected_tool_calls=[],
            fake_turns=[[{"type": "tool_use", "name": "probe", "input": {}}], [{"type": "text", "text": "ok"}]],
        )
        result = run_scripted(conv, FakeChatBackend(conv.fake_turns))
        assert result.passed is False
        assert result.tool_calls == ["probe"]

    def test_chat_error_is_a_named_failure_not_a_crash(self) -> None:
        class Broken:
            def stream(self, request: ChatRequest):
                raise ChatError("rate limited", retryable=True, status=429)
                yield  # pragma: no cover - makes this a generator

        conv = conversation("just-talking")
        result = run_scripted(conv, Broken())
        assert result.passed is False
        assert result.failures == ["backend error (retryable): rate limited"]
        assert result.cost_note.startswith("measured tokens")

    def test_cost_note_for_non_fake_backend_names_the_price_module(self) -> None:
        class Silent:
            def stream(self, request: ChatRequest):
                yield MessageStart("m")
                yield MessageStop("end_turn", Usage(10, 20), [{"type": "text", "text": "permission chip"}])

        result = run_scripted(conversation("just-talking"), Silent())
        assert result.passed is True
        assert result.cost_note == (
            "measured tokens in=20/out=40 (cache read=0, creation=0); priced by the §3.0-C module from P0-7"
        )

    def test_eval_result_has_no_cost_number(self) -> None:
        assert not any(name.endswith("_cost") or name == "cost" for name in EvalResult.__dataclass_fields__)


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


class TestMain:
    def setup_method(self) -> None:
        BackendRegistry.reset()

    def teardown_method(self) -> None:
        BackendRegistry.reset()

    def test_fake_backend_returns_zero_and_prints_summary(self, capsys) -> None:
        assert main(["--backend", "fake"]) == 0
        out = capsys.readouterr().out
        assert f"{len(CONVERSATIONS)}/{len(CONVERSATIONS)} passed" in out
        assert out.count("PASS") == len(CONVERSATIONS)
        assert "FAIL" not in out
        assert FAKE_COST_NOTE in out

    def test_default_backend_is_fake(self, capsys) -> None:
        assert main([]) == 0
        assert "backend=fake" in capsys.readouterr().out

    def test_unknown_backend_returns_two(self, capsys) -> None:
        assert main(["--backend", "nope"]) == 2
        err = capsys.readouterr().err
        assert "nope" in err and "known ids" in err

    def test_only_filters(self, capsys) -> None:
        assert main(["--only", "parallel-reads"]) == 0
        out = capsys.readouterr().out
        assert "parallel-reads" in out
        assert "unbeatable-level" not in out
        assert "1/1 passed" in out

    def test_only_unknown_returns_two(self, capsys) -> None:
        assert main(["--only", "bogus"]) == 2
        assert "bogus" in capsys.readouterr().err

    def test_json_output_is_parseable(self, capsys) -> None:
        assert main(["--backend", "fake", "--json"]) == 0
        document = json.loads(capsys.readouterr().out)
        assert document["backend"] == "fake"
        assert document["passed"] == document["total"] == len(CONVERSATIONS)
        assert [r["name"] for r in document["results"]] == [c.name for c in CONVERSATIONS]
        assert all(r["passed"] for r in document["results"])
        assert document["results"][0]["usage"] == {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }

    def test_registered_backend_id_resolves_through_registry(self, capsys) -> None:
        conv = conversation("unbeatable-level")
        turns = iter(conv.fake_turns)
        BackendRegistry.register_chat("scripted", lambda: FakeChatBackend(lambda req: next(turns)))
        assert main(["--backend", "scripted", "--only", "unbeatable-level"]) == 0
        out = capsys.readouterr().out
        assert "PASS" in out and "backend=scripted" in out

    def test_failure_returns_one(self, capsys, monkeypatch) -> None:
        import canon.agent.eval as eval_module

        broken = replace(conversation("just-talking"), expected_tool_calls=[{"name": "probe"}])
        monkeypatch.setattr(eval_module, "CONVERSATIONS", [broken])
        assert main(["--backend", "fake"]) == 1
        out = capsys.readouterr().out
        assert "FAIL" in out and "tool calls:" in out

    @pytest.mark.parametrize(
        ("backend_id", "key_env"),
        [("openai", "OPENAI_API_KEY"), ("kimi", "MOONSHOT_API_KEY")],
    )
    def test_real_provider_without_credential_is_a_named_failure(
        self, backend_id: str, key_env: str, capsys, monkeypatch
    ) -> None:
        """Row A8's providers resolve through the registrar map. With the
        extra installed and no credential the run is 1 with the named
        no-credential line (never a traceback, never a paid call)."""
        pytest.importorskip("openai")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
        code = main(["--backend", backend_id, "--only", "just-talking"])
        captured = capsys.readouterr()
        assert "Traceback" not in captured.out + captured.err
        assert code == 1
        assert f"backend error (not retryable): {backend_id}: no credential — set {key_env}" in captured.out
        assert f"backend={backend_id}" in captured.out
        assert "measured tokens in=0/out=0" in captured.out

    @pytest.mark.parametrize("backend_id", ["openai", "kimi"])
    def test_missing_openai_extra_is_a_usage_error(self, backend_id: str, capsys, monkeypatch) -> None:
        """Without the ``openai`` extra the registrar's construction raises
        ``ImportError``; ``_real_backend`` prints the install hint and
        ``main`` returns 2 — deterministic, whatever the venv has installed."""
        monkeypatch.setitem(sys.modules, "openai", None)  # ``import openai`` → ImportError
        monkeypatch.setenv("OPENAI_API_KEY", "sk-never-used")
        monkeypatch.setenv("MOONSHOT_API_KEY", "sk-never-used")
        assert main(["--backend", backend_id, "--only", "just-talking"]) == 2
        captured = capsys.readouterr()
        assert "Traceback" not in captured.out + captured.err
        assert f"chat backend {backend_id!r} is not installed" in captured.err
        assert "canon-ai[openai]" in captured.err

    def test_anthropic_does_not_depend_on_the_openai_extra(self, capsys, monkeypatch) -> None:
        """The registrar map imports ``chat_openai`` for its callables, but
        that module must not import ``openai`` at module level — the
        anthropic id still resolves (to its own named no-credential failure)
        with the openai extra absent."""
        pytest.importorskip("anthropic")
        monkeypatch.setitem(sys.modules, "openai", None)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        code = main(["--backend", "anthropic", "--only", "just-talking"])
        captured = capsys.readouterr()
        assert code == 1
        assert "not installed" not in captured.err
        assert "backend error (not retryable): anthropic: no credential — set ANTHROPIC_API_KEY" in captured.out

    def test_registrar_map_is_data(self) -> None:
        """Row A2 moved the map to ``canon.agent.providers`` so the service
        and this runner resolve ids through the same data."""
        import canon.agent.eval as eval_module
        from canon.agent.providers import registrars, resolve_chat_backend

        registrar_map = registrars()
        assert list(registrar_map) == ["anthropic", "openai", "kimi"]
        assert all(callable(r) for r in registrar_map.values())
        assert not hasattr(eval_module, "_registrars")  # moved, not copied
        assert eval_module.resolve_chat_backend is resolve_chat_backend

    def test_resolve_chat_backend_unknown_id_raises_key_error(self) -> None:
        from canon.agent.providers import resolve_chat_backend

        with pytest.raises(KeyError, match="nope"):
            resolve_chat_backend("nope", None)
