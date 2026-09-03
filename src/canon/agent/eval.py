"""The scripted-conversation eval runner (Phase 1 A1's gate).

``run_scripted`` plays one ``ScriptedConversation`` through
``run_conversation`` on a backend and reports pass/fail with named failures.
``main`` runs the built-in corpus::

    uv run python -m canon.agent.eval                      # fake backend, $0
    uv run python -m canon.agent.eval --only parallel-reads --json
    uv run python -m canon.agent.eval --backend anthropic  # USER-RUN: paid

The default — and the only backend this runner ever picks on its own — is
``fake``: ``FakeChatBackend`` plays each conversation's ``fake_turns``, so
the gate is hermetic, keyless and $0. Any other id is a real provider and a
paid leg (doctrine: paid legs are user-run); it resolves through
``canon.agent.providers.resolve_chat_backend`` — the registrar map
(anthropic, openai, kimi — data) shared with the A2 service, then
``BackendRegistry.chat(id)``. On a real backend the tool order stays
strict and the wording check is freed (row A8's provider-swap rule).

Cost is reported honestly and never computed here: the fake's note is
"$0 — nothing measured"; a real backend's note carries the measured token
counts and names the §3.0-C module (row P0-7) as the thing that prices them.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field

from canon.agent.evals import CONVERSATIONS, ScriptedConversation
from canon.agent.loop import MAX_TOOL_ROUNDS_STOP, ConversationResult, run_conversation
from canon.agent.providers import resolve_chat_backend
from canon.agent.runs import DELEGATE_TOOL
from canon.backends.base import ChatBackend
from canon.backends.registry import BackendRegistry
from canon.backends.testing import FakeChatBackend
from canon.llm.chat import ChatError, Usage

FAKE_COST_NOTE = "$0 — fake backend, nothing measured"


@dataclass
class EvalResult:
    """One conversation's verdict.

    Attributes:
        name: The conversation's name.
        passed: ``True`` iff ``failures`` is empty.
        failures: Named, human-readable failures (empty on pass).
        tool_calls: Tool names actually called, in order.
        usage: Measured tokens summed over the conversation.
        cost_note: What the run cost, stated honestly — never a number this
            module computed.
    """

    name: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    tool_calls: list[str] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    cost_note: str = FAKE_COST_NOTE


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _executor_from(tool_results: dict):
    """The executor for a script's canned results; unknown tools raise so the
    loop reports them as ``is_error`` results (and the tool-order check names
    the mismatch)."""

    def execute(name: str, tool_input: dict):
        if name not in tool_results:
            raise KeyError(f"no scripted result for tool {name!r}")
        spec = tool_results[name]
        if callable(spec):
            return spec(tool_input)
        return spec

    return execute


def _is_subset(subset: dict, actual: dict) -> bool:
    return all(key in actual and actual[key] == value for key, value in subset.items())


def _tool_call_failures(conv: ScriptedConversation, result: ConversationResult) -> list[str]:
    failures: list[str] = []
    actual_names = [step["tool"] for step in result.steps]
    expected_names = [call["name"] for call in conv.expected_tool_calls]
    if actual_names != expected_names:
        failures.append(f"tool calls: expected {expected_names} got {actual_names}")
    for position, (expected, step) in enumerate(zip(conv.expected_tool_calls, result.steps, strict=False)):
        subset = expected.get("input_subset")
        if subset and expected["name"] == step["tool"] and not _is_subset(subset, step["input"]):
            failures.append(f"tool call {position} ({step['tool']}): input {step['input']} lacks {subset}")
    return failures


def _text_failures(conv: ScriptedConversation, result: ConversationResult) -> list[str]:
    final_text = result.texts[-1] if result.texts else ""
    lowered = final_text.lower()
    return [
        f"text: final assistant text lacks {needle!r}"
        for needle in conv.expected_text_contains
        if needle.lower() not in lowered
    ]


def _delegations(result: ConversationResult) -> list[str]:
    """The ``specialist`` argument of every ``delegate`` call, in order."""
    return [
        str((step.get("input") or {}).get("specialist", ""))
        for step in result.steps
        if step["tool"] == DELEGATE_TOOL
    ]


def _delegation_failures(conv: ScriptedConversation, result: ConversationResult) -> list[str]:
    """Row A7's routing contract: the delegations a conversation makes must
    be exactly the ones the corpus expects, in order — the same strict rule
    ``_tool_call_failures`` applies to tool names, read at the level the
    routing question is actually asked at (WHICH specialist, not "a delegate
    call happened"). ``[]`` is a real expectation: a pure question must
    delegate to nobody. Checked on every backend — routing is precisely what
    the provider-swap leg measures, so it is never freed with the wording."""
    if conv.expected_delegations is None:
        return []
    actual = _delegations(result)
    if actual == conv.expected_delegations:
        return []
    return [f"delegations: expected {conv.expected_delegations} got {actual}"]


def _stop_reason_failures(conv: ScriptedConversation, result: ConversationResult) -> list[str]:
    if conv.expected_stop_reasons is None or result.stop_reasons == conv.expected_stop_reasons:
        return []
    return [f"stop reasons: expected {conv.expected_stop_reasons} got {result.stop_reasons}"]


def _pairing_failures(messages: list[dict]) -> list[str]:
    """Every assistant turn with tool_use blocks must be followed by exactly
    ONE user message holding exactly its tool_result blocks, in order —
    parallel tool use never splits its results (provider-neutral rule)."""
    failures: list[str] = []
    for i, message in enumerate(messages):
        content = message.get("content")
        if message.get("role") != "assistant" or not isinstance(content, list):
            continue
        uses = [b for b in content if b.get("type") == "tool_use"]
        if not uses:
            continue
        following = messages[i + 1] if i + 1 < len(messages) else None
        if following is None or following.get("role") != "user" or not isinstance(following.get("content"), list):
            failures.append(f"turn {i}: {len(uses)} tool_use block(s) without a following tool_result message")
            continue
        blocks = following["content"]
        results = [b for b in blocks if b.get("type") == "tool_result"]
        if len(results) != len(blocks) or [b.get("tool_use_id") for b in results] != [u.get("id") for u in uses]:
            failures.append(
                f"turn {i}: expected ONE user message with exactly {len(uses)} tool_result block(s) "
                f"for {[u.get('id') for u in uses]}, got {[b.get('tool_use_id', b.get('type')) for b in blocks]}"
            )
    return failures


def _request_history_failures(backend: ChatBackend) -> list[str]:
    """What the backend actually RECEIVED must pair up too — not just the
    loop's own history. Backends that record ``calls`` (the fake) are
    checked on every request; a real backend has no ``calls`` and is
    skipped."""
    calls = getattr(backend, "calls", None)
    if not isinstance(calls, list):
        return []
    failures: list[str] = []
    for n, request in enumerate(calls):
        messages = getattr(request, "messages", None)
        if isinstance(messages, list):
            failures += [f"request history (call {n}): {f}" for f in _pairing_failures(messages)]
    return failures


def _cost_note(backend: ChatBackend, usage: Usage) -> str:
    if isinstance(backend, FakeChatBackend):
        return FAKE_COST_NOTE
    return (
        f"measured tokens in={usage.input_tokens}/out={usage.output_tokens} "
        f"(cache read={usage.cache_read_input_tokens}, creation={usage.cache_creation_input_tokens}); "
        "priced by the §3.0-C module from P0-7"
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_scripted(conv: ScriptedConversation, backend: ChatBackend, *, strict_text: bool = True) -> EvalResult:
    """Run one scripted conversation on ``backend`` and judge it.

    Checks, each a named failure: tool names in the expected order; each
    expected ``input_subset`` ⊆ the actual input; the DELEGATIONS in the
    expected order with the expected specialists (row A7's routing contract,
    checked on every backend); the final assistant text
    contains every expected substring (skipped when ``strict_text`` is off —
    the provider-swap gate keeps tool order strict and frees the wording);
    the recorded stop reasons match ``expected_stop_reasons`` (also freed
    with ``strict_text``); tool results paired one-message-per-turn, both
    in the loop's history and in every request the backend recorded; and
    the loop never hit the ``max_tool_rounds`` guard. A ``ChatError`` from
    the backend is a named failure, not a crash — one flaky provider call
    must not take the whole corpus down — and so is any other exception a
    backend lets escape (an SDK that dies before its first request, say).
    """
    try:
        result = run_conversation(
            backend,
            system=conv.system,
            tools=conv.tools,
            tool_executor=_executor_from(conv.tool_results),
            user_messages=conv.user_messages,
        )
    except ChatError as exc:
        return EvalResult(
            name=conv.name,
            passed=False,
            failures=[f"backend error ({'retryable' if exc.retryable else 'not retryable'}): {exc}"],
            cost_note=_cost_note(backend, Usage()),
        )
    except Exception as exc:  # noqa: BLE001 — a crashing backend is a named failure, never a dead corpus
        return EvalResult(
            name=conv.name,
            passed=False,
            failures=[f"backend crashed ({type(exc).__name__}): {exc}"],
            cost_note=_cost_note(backend, Usage()),
        )

    failures = _tool_call_failures(conv, result)
    failures += _delegation_failures(conv, result)
    if strict_text:
        failures += _text_failures(conv, result)
        failures += _stop_reason_failures(conv, result)
    failures += _pairing_failures(result.messages)
    failures += _request_history_failures(backend)
    if MAX_TOOL_ROUNDS_STOP in result.stop_reasons:
        failures.append(f"loop: hit the {MAX_TOOL_ROUNDS_STOP} guard (stop reasons {result.stop_reasons})")

    return EvalResult(
        name=conv.name,
        passed=not failures,
        failures=failures,
        tool_calls=[step["tool"] for step in result.steps],
        usage=result.usage,
        cost_note=_cost_note(backend, result.usage),
    )


def _real_backend(backend_id: str, model: str | None) -> ChatBackend | None:
    """Resolve a non-fake backend through the shared registrar map +
    registry (``canon.agent.providers``, moved there at row A2 so the
    service resolves ids identically); ``None`` (after printing why) when
    it cannot be built. Every real backend runs with ``strict_text=False``
    (row A8's provider-swap rule) — see ``main``."""
    try:
        return resolve_chat_backend(backend_id, model)
    except KeyError:
        print(
            f"unknown chat backend {backend_id!r}; known ids: {BackendRegistry.chat_ids()} (plus 'fake')",
            file=sys.stderr,
        )
    except ImportError as exc:
        print(f"chat backend {backend_id!r} is not installed: {exc}", file=sys.stderr)
    return None


def main(argv: list[str] | None = None) -> int:
    """CLI entry: run the corpus, print one line per conversation + a summary.

    Returns 0 when every selected conversation passes, 1 when any fails, 2
    for a usage error (unknown backend id or conversation name).
    """
    parser = argparse.ArgumentParser(
        prog="python -m canon.agent.eval",
        description="Run the agent's scripted tool-use conversations. Default backend 'fake' is $0 and keyless; "
        "any other backend id is a real, paid, user-run leg.",
    )
    parser.add_argument("--backend", default="fake", help="chat backend id (default: fake)")
    parser.add_argument("--model", default=None, help="model id for the backend (a plain string; ids are data)")
    parser.add_argument("--only", default=None, metavar="NAME", help="run one conversation by name")
    parser.add_argument("--json", action="store_true", help="print a single JSON document instead of lines")
    args = parser.parse_args(argv)

    selected = [c for c in CONVERSATIONS if args.only is None or c.name == args.only]
    if not selected:
        print(
            f"no scripted conversation named {args.only!r}; known: {[c.name for c in CONVERSATIONS]}",
            file=sys.stderr,
        )
        return 2

    real_backend: ChatBackend | None = None
    if args.backend != "fake":
        real_backend = _real_backend(args.backend, args.model)
        if real_backend is None:
            return 2

    results: list[EvalResult] = []
    for conv in selected:
        if real_backend is None:
            backend: ChatBackend = FakeChatBackend(conv.fake_turns, model=args.model or "fake-chat")
            results.append(run_scripted(conv, backend, strict_text=True))
        else:
            results.append(run_scripted(conv, real_backend, strict_text=False))

    passed = sum(1 for r in results if r.passed)
    total_usage = Usage()
    for r in results:
        total_usage = total_usage + r.usage
    summary_cost = FAKE_COST_NOTE if real_backend is None else _cost_note(real_backend, total_usage)

    if args.json:
        document = {
            "backend": args.backend,
            "model": args.model,
            "passed": passed,
            "total": len(results),
            "cost_note": summary_cost,
            "results": [asdict(r) for r in results],
        }
        print(json.dumps(document, indent=2))
    else:
        width = max(len(r.name) for r in results)
        for r in results:
            verdict = "PASS" if r.passed else "FAIL"
            tools = ",".join(r.tool_calls) or "-"
            print(f"{verdict}  {r.name:<{width}}  tools={tools}  {r.cost_note}")
            for failure in r.failures:
                print(f"      - {failure}")
        print(f"{passed}/{len(results)} passed · backend={args.backend} · {summary_cost}")

    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
