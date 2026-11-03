"""The deterministic mid-stream ⏹ Stop gate (row P1-A4.5's Stop contract,
closed at row P1-A7).

Row A4.5's gate — "Stop halts token burn mid-stream" — could only be
approximated with the shipped fakes: ``FakeChatBackend`` streamed a whole
turn in microseconds, so the only reliable way to have something in flight
when ``POST /conversations/{id}/stop`` arrived was to block on an ask-tier
permission chip, which tests the PERMISSION round-trip rather than the
stream. Row A7 adds ``FakeChatBackend(delay_s=…)`` (off by default, so every
existing test keeps its behaviour and its timing) and this file uses it for
the assertion the gate actually wanted:

    a turn that is genuinely mid-stream when Stop arrives closes the
    provider generator, lets no further delta reach the transcript, and
    records the turn ``cancelled`` with what landed and what it cost.

$0 and keyless: the backend is the fake, the pack is a generated $0 tree.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from canon.agent.actors import current_call
from canon.agent.conversations import ConversationStore
from canon.agent.permissions import PermissionEngine
from canon.agent.registry import ToolRegistry
from canon.agent.roster import load_roster
from canon.agent.tools_read import register_read_tools
from canon.agent.tools_vision import register_vision_tools
from canon.agent.tools_write import register_write_tools
from canon.backends.testing import FakeChatBackend
from canon.llm.chat import ChatEvent, MessageStop, TextDelta, Usage

pytest.importorskip("fastapi")

from canon.agent.service import create_app  # noqa: E402
from tests.test_agent_runs import LiveServer  # noqa: E402 — the row A4.5 harness, reused not rebuilt

REPO = Path(__file__).resolve().parents[1]

#: The last word of the scripted reply. It must never appear anywhere —
#: not on the stream, not in the transcript — once Stop has landed.
TAIL = "ENDMARK"

#: Per-event pacing. Tens of milliseconds: enough that a turn is provably in
#: flight, small enough that CI never waits on it (the generator is closed on
#: Stop, so the remaining script is never slept through).
DELAY_S = 0.02

#: What the scripted turns report as measured tokens, so "what it cost" is a
#: number the assertions can read. The shipped fake honestly reports zeros —
#: it measures nothing — which would make the cost assertion vacuous.
TURN_USAGE = Usage(input_tokens=1200, output_tokens=64)


@pytest.fixture(scope="module")
def generated_tree(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("a7_stop_tree")
    subprocess.run(
        [
            sys.executable, "-m", "canon.packs.platformer.run_slice",
            "--backend", "fake", "--engine", "json", "--image-backend", "fake",
            "--music-backend", "none", "--sfx-backend", "none",
            "--num-stages", "1", "--num-levels", "2", "--num-enemies", "2", "--num-items", "2",
            "--seed", "a7-stop", "--orchestrate", "--output-dir", str(out),
        ],
        check=True,
        capture_output=True,
        cwd=REPO,
    )
    return out


@pytest.fixture
def pack(generated_tree: Path, tmp_path: Path) -> Path:
    dst = tmp_path / "pack"
    shutil.copytree(generated_tree, dst)
    return dst


class ObservedFake(FakeChatBackend):
    """``FakeChatBackend`` plus exactly what this gate must observe: whether
    the provider generator was CLOSED mid-stream (A1's cancel contract), how
    many deltas it managed to emit, and a scripted usage per turn."""

    def __init__(self, turns: list) -> None:
        super().__init__(turns, delay_s=DELAY_S)
        self.closed = False
        self.finished = False
        self.deltas = 0

    def _play(self, turn: list | dict) -> Iterator[ChatEvent]:
        try:
            for event in super()._play(turn):
                if isinstance(event, TextDelta):
                    self.deltas += 1
                if isinstance(event, MessageStop):
                    event = MessageStop(
                        stop_reason=event.stop_reason,
                        usage=TURN_USAGE,
                        content=event.content,
                        stop_details=event.stop_details,
                    )
                yield event
            self.finished = True
        except GeneratorExit:
            self.closed = True
            raise


def long_reply() -> list[dict]:
    """A reply in many blocks, so the turn streams for seconds under pacing —
    one long block would be two deltas and nothing to interrupt."""
    return [{"type": "text", "text": f"chunk{i} "} for i in range(60)] + [{"type": "text", "text": TAIL}]


def full_registry(pack: Path) -> ToolRegistry:
    registry = ToolRegistry(PermissionEngine(pack, default_mode="allow"))
    register_read_tools(registry, pack)
    register_write_tools(registry, pack, actor_for=current_call)
    register_vision_tools(registry, pack)
    return registry


class TestFakeBackendPacing:
    def test_pacing_is_off_by_default(self) -> None:
        """Every existing test must keep its behaviour AND its timing."""
        fake = FakeChatBackend([[{"type": "text", "text": "hi"}]])
        assert fake.delay_s == 0.0 and fake.pace is None
        started = time.monotonic()
        events = list(fake.stream(_request()))
        assert [event.type for event in events] == [
            "message_start", "text_delta", "text_delta", "content_block_done", "message_stop"
        ]
        assert time.monotonic() - started < 0.05, "an unpaced fake must stream as instantly as it always did"

    def test_the_pace_hook_sees_every_event_in_order(self) -> None:
        seen: list[str] = []
        fake = FakeChatBackend([[{"type": "text", "text": "hi"}]], pace=lambda event: seen.append(event.type))
        streamed = [e.type for e in fake.stream(_request())]
        assert seen == streamed, "pace observes the stream; it never changes it"


def _request():
    from canon.llm.chat import ChatRequest

    return ChatRequest(messages=[{"role": "user", "content": "hi"}])


class TestStopMidStream:
    def test_stop_mid_stream_closes_the_generator_and_records_what_landed_and_cost(self, pack: Path) -> None:
        backend = ObservedFake([
            # Turn 1 lands a real auto-tier read — this is the "what landed".
            [{"type": "tool_use", "name": "validate_level", "input": {"level_id": "l1"}}],
            # Turn 2 is the long reply Stop interrupts.
            long_reply(),
        ])
        app = create_app(
            pack, "fake", None, full_registry(pack), ConversationStore(pack), backend=backend, roster=load_roster()
        )
        with LiveServer(app) as server:
            conversation = server.create()
            stream = server.send(conversation, "explain l1 at length")
            # The first text delta can only come from turn 2 — turn 1 is a
            # tool_use block and carries no text — so the reply is provably
            # in flight when the stop lands.
            stream.wait_for("tool_result")
            stream.wait_for("text_delta")
            stopped = server.post(f"/conversations/{conversation}/stop", {"reason": "esc"})
            assert stopped.status_code == 200 and stopped.json()["stopped"] is True

            events = stream.finish(timeout=30)

        names = [name for name, _ in events]
        assert names[-1] == "cancelled", names
        record = events[-1][1]
        assert record["where"] == "stream" and record["reason"] == "esc"
        # …what landed…
        assert record["landed"] == [{"tool": "validate_level", "is_error": False}]
        assert record["runs"] == []
        # …and what it cost: the completed turn's measured tokens, kept.
        assert record["usage"]["input_tokens"] == TURN_USAGE.input_tokens
        assert record["usage"]["output_tokens"] == TURN_USAGE.output_tokens

        # The provider stream was CLOSED, not drained (A1's cancel contract).
        assert backend.closed is True and backend.finished is False
        assert backend.deltas < 40, f"the stream kept burning tokens: {backend.deltas} deltas"

        # No further delta reached the stream or the transcript.
        assert not any(TAIL in str(data) for _, data in events), "text after the stop must not reach the client"
        transcript = ConversationStore(pack).load(conversation)
        assert not any(TAIL in str(line) for line in transcript)
        end = [line for line in transcript if line["type"] == "turn_end"][-1]
        assert end["stop_reason"] == "cancelled" and end["where"] == "stream"
        assert end["usage"] == record["usage"] and end["landed"] == record["landed"]

    def test_the_conversation_is_usable_again_after_a_mid_stream_stop(self, pack: Path) -> None:
        backend = ObservedFake([long_reply(), [{"type": "text", "text": "short one"}]])
        app = create_app(
            pack, "fake", None, full_registry(pack), ConversationStore(pack), backend=backend, roster=load_roster()
        )
        with LiveServer(app) as server:
            conversation = server.create()
            stream = server.send(conversation, "ramble")
            stream.wait_for("text_delta")
            assert server.post(f"/conversations/{conversation}/stop").json()["stopped"] is True
            assert stream.finish(timeout=30)[-1][0] == "cancelled"
            # A stop on an idle conversation is honest, and the next turn runs.
            assert server.post(f"/conversations/{conversation}/stop").json()["stopped"] is False
            backend.delay_s = 0.0
            again = server.send(conversation, "and now briefly").finish(timeout=30)
            assert again[-1][0] == "done"
