"""Tests for row A2 — the agent service skeleton.

Hermetic: every conversation runs on ``FakeChatBackend`` (keyless, $0); the
pack is a tmp dir carrying only ``manifest.json`` ``{"pack_type":
"platformer"}`` (``resolve_pack``'s manifest tier); the HTTP side runs on
FastAPI's ``TestClient``. One test spawns the real sidecar as a subprocess
to prove the port line + dies-with-parent contract.

Read verbs write NOTHING into a pack: every HTTP test snapshots the pack
tree before and after and asserts the only delta is under
``.canon/agent/`` — the transcript, which is the one thing A2 writes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

from canon.agent.conversations import AGENT_DIR, ConversationStore, record_for
from canon.agent.loop import run_conversation
from canon.agent.permissions import TIERS, Decision, PermissionEngine
from canon.agent.registry import Tool, ToolRefused, ToolRegistry, UnknownTool
from canon.agent.tools_code import CODE_TOOL_NAMES
from canon.agent.tools_paid import PAID_TOOL_NAMES
from canon.agent.tools_read import READ_TOOL_NAMES, register_read_tools
from canon.agent.tools_vision import VISION_TOOL_NAMES
from canon.agent.tools_write import WRITE_TOOL_NAMES
from canon.backends.testing import FakeChatBackend
from canon.llm.chat import ChatRequest, ToolSpec

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from canon.agent import service  # noqa: E402
from canon.agent.service import (  # noqa: E402
    FAKE_BACKEND_ID,
    HOST,
    TurnLocks,
    bind,
    build_backend,
    create_app,
    fake_backend,
    pack_problem,
    parent_alive,
    pid_alive,
    sse,
    watch_parent,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EMPTY_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}
PROBE = ToolSpec(name="probe", description="an auto read", input_schema=EMPTY_SCHEMA)
WRITE = ToolSpec(name="write_thing", description="an ask write", input_schema=EMPTY_SCHEMA)
SPEND = ToolSpec(name="generate_thing", description="a paid generation", input_schema=EMPTY_SCHEMA)


def make_pack(root: Path) -> Path:
    pack = root / "pack"
    pack.mkdir()
    (pack / "manifest.json").write_text(json.dumps({"pack_type": "platformer"}), encoding="utf-8")
    (pack / "level").mkdir()
    (pack / "level" / "l1.json").write_text('{"id": "l1"}', encoding="utf-8")
    return pack


def snapshot(pack: Path) -> dict[str, str]:
    """``{relative path: sha256}`` of every file in the pack."""
    out: dict[str, str] = {}
    for path in sorted(p for p in pack.rglob("*") if p.is_file()):
        out[str(path.relative_to(pack))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def assert_only_transcripts_changed(before: dict[str, str], after: dict[str, str]) -> None:
    agent_prefix = str(AGENT_DIR) + os.sep
    for rel, digest in before.items():
        assert after.get(rel) == digest, f"{rel} changed"
    for rel in set(after) - set(before):
        assert rel.startswith(agent_prefix), f"unexpected new file outside .canon/agent: {rel}"


def parse_sse(body: str) -> list[tuple[str, dict]]:
    """``[(event, data)]`` from a ``text/event-stream`` body."""
    events: list[tuple[str, dict]] = []
    for frame in body.split("\n\n"):
        if not frame.strip():
            continue
        event = data = None
        for line in frame.split("\n"):
            if line.startswith("event: "):
                event = line[len("event: ") :]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: ") :])
        assert event is not None and data is not None, frame
        events.append((event, data))
    return events


def registry_with_tools() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(Tool(spec=PROBE, tier="auto", run=lambda i: {"probed": True}, touches="reads nothing"))
    registry.register(Tool(spec=WRITE, tier="ask", run=lambda i: "wrote", touches="writes level/l1.json"))
    registry.register(Tool(spec=SPEND, tier="paid", run=lambda i: "spent", touches="spends via fal"))
    return registry


@pytest.fixture
def pack(tmp_path: Path) -> Path:
    return make_pack(tmp_path)


def app_for(pack: Path, turns, registry: ToolRegistry | None = None, **kwargs) -> tuple[TestClient, FakeChatBackend]:
    fake = FakeChatBackend(turns)
    tools = registry or registry_with_tools()
    app = create_app(pack, FAKE_BACKEND_ID, None, tools, ConversationStore(pack), backend=fake, **kwargs)
    return TestClient(app), fake


def post_message(client: TestClient, conversation_id: str, text: str) -> tuple[int, list[tuple[str, dict]]]:
    with client.stream("POST", f"/conversations/{conversation_id}/messages", json={"text": text}) as response:
        status = response.status_code
        body = "".join(response.iter_text())
    if status != 200:
        return status, [("http", json.loads(body))]
    assert response.headers["content-type"].startswith("text/event-stream")
    return status, parse_sse(body)


# ---------------------------------------------------------------------------
# ToolRegistry + PermissionEngine shell
# ---------------------------------------------------------------------------


class TestToolRegistry:
    def test_register_get_specs_names_in_order(self) -> None:
        registry = registry_with_tools()
        assert registry.names() == ["probe", "write_thing", "generate_thing"]
        assert registry.specs() == [PROBE, WRITE, SPEND]
        assert registry.get("probe").tier == "auto"
        assert registry.get("write_thing").touches == "writes level/l1.json"

    def test_duplicate_name_is_an_error(self) -> None:
        registry = registry_with_tools()
        with pytest.raises(ValueError, match="already registered"):
            registry.register(Tool(spec=PROBE, tier="auto", run=lambda i: "", touches=""))

    def test_execute_auto_runs_the_tool(self) -> None:
        registry = registry_with_tools()
        assert registry.execute("probe", {}, actor="agent:conv_x", conversation="conv_x") == {"probed": True}

    @pytest.mark.parametrize(("name", "tier"), [("write_thing", "ask"), ("generate_thing", "paid")])
    def test_execute_ask_and_paid_are_refused_when_nobody_listens(self, name: str, tier: str) -> None:
        """Row A4: outside a turn (no listener bound for the conversation) an
        ask/paid tool has no chip to wait on — refused with the reason,
        never allowed by default and never a silent hang."""
        registry = registry_with_tools()
        with pytest.raises(ToolRefused) as info:
            registry.execute(name, {}, actor="agent:conv_x/foreman", conversation="conv_x")
        reason = str(info.value)
        assert reason.startswith(f"no one to ask: {name} is {tier}-tier")
        assert "conv_x" in reason and "listener" in reason

    def test_unknown_tool_is_a_structured_error(self) -> None:
        registry = registry_with_tools()
        with pytest.raises(UnknownTool) as info:
            registry.execute("frobnicate", {}, actor="a", conversation="c")
        document = json.loads(str(info.value))
        assert document == {"error": "unknown_tool", "tool": "frobnicate", "known": registry.names()}

    def test_unknown_tier_fails_closed(self) -> None:
        registry = ToolRegistry()
        registry.register(Tool(spec=PROBE, tier="mystery", run=lambda i: "ran", touches=""))
        with pytest.raises(ToolRefused, match="unknown tier 'mystery'"):
            registry.execute("probe", {}, actor="a", conversation="c")

    def test_tiers_are_plain_strings(self) -> None:
        assert TIERS == ("auto", "ask", "paid")
        assert Tool.__dataclass_fields__["tier"].type == "str"
        decision = PermissionEngine().check(
            Tool(spec=PROBE, tier="auto", run=lambda i: "", touches=""), {}, actor="a", conversation="c"
        )
        assert decision == Decision("allow", "auto-tier: probe reads only, reads never ask")
        assert decision.allowed is True  # what the registry reads — unchanged from A2

    def test_loop_renders_refusal_and_unknown_as_is_error_results(self) -> None:
        registry = registry_with_tools()
        fake = FakeChatBackend(
            [
                [
                    {"type": "tool_use", "name": "write_thing", "input": {}},
                    {"type": "tool_use", "name": "nope", "input": {}},
                ],
                [{"type": "text", "text": "ok"}],
            ]
        )
        result = run_conversation(
            fake,
            system=None,
            tools=registry.specs(),
            tool_executor=lambda n, i: registry.execute(n, i, actor="a", conversation="c"),
            user_messages=["go"],
        )
        assert [s["is_error"] for s in result.steps] == [True, True]
        assert result.steps[0]["result"].startswith("ToolRefused: no one to ask: write_thing is ask-tier")
        assert result.steps[1]["result"].startswith("UnknownTool: ")
        assert json.loads(result.steps[1]["result"][len("UnknownTool: ") :])["tool"] == "nope"


# ---------------------------------------------------------------------------
# ConversationStore
# ---------------------------------------------------------------------------


class TestConversationStore:
    def test_create_writes_meta_under_canon_agent(self, pack: Path) -> None:
        store = ConversationStore(pack)
        conversation_id = store.create(backend="fake", model="fake-chat", system="sys")
        assert re.fullmatch(r"conv_[0-9a-f]{8}", conversation_id)
        path = pack / ".canon" / "agent" / f"{conversation_id}.jsonl"
        assert path.is_file()
        lines = store.load(conversation_id)
        assert len(lines) == 1
        meta = lines[0]
        assert {k: meta[k] for k in ("type", "id", "pack", "backend", "model", "system")} == {
            "type": "meta",
            "id": conversation_id,
            "pack": str(pack),
            "backend": "fake",
            "model": "fake-chat",
            "system": "sys",
        }
        assert meta["created"].endswith("Z")
        assert store.meta(conversation_id) == meta

    def test_append_load_messages_and_list(self, pack: Path) -> None:
        store = ConversationStore(pack)
        cid = store.create(backend="fake", model=None, system=None)
        store.append(cid, record_for({"role": "user", "content": "hi"}))
        use = {"type": "tool_use", "id": "t1", "name": "probe", "input": {}}
        result = {"type": "tool_result", "tool_use_id": "t1", "content": "x"}
        store.append(cid, record_for({"role": "assistant", "content": [use]}))
        store.append(cid, record_for({"role": "user", "content": [result]}))
        store.append(cid, record_for({"role": "assistant", "content": [{"type": "text", "text": "done"}]}))
        store.append(cid, {"type": "turn_end", "stop_reason": "end_turn", "usage": {}})
        types = [line["type"] for line in store.load(cid)]
        assert types == ["meta", "user", "assistant", "tool_result", "assistant", "turn_end"]
        assert all("ts" in line for line in store.load(cid)[1:])
        assert store.messages(cid) == [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "probe", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "x"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
        ]
        assert store.list() == [{"id": cid, "created": store.meta(cid)["created"], "turns": 1}]

    def test_append_never_creates_a_transcript(self, pack: Path) -> None:
        store = ConversationStore(pack)
        with pytest.raises(KeyError):
            store.append("conv_00000000", {"type": "user", "content": "x"})
        with pytest.raises(KeyError):
            store.load("conv_00000000")
        assert store.list() == []

    def test_ids_are_unique_and_conversation_named(self, pack: Path) -> None:
        store = ConversationStore(pack)
        ids = {store.create(backend="fake", model=None, system=None) for _ in range(20)}
        assert len(ids) == 20
        assert all(i.startswith("conv_") for i in ids)  # never "session" — master §3.0-D


# ---------------------------------------------------------------------------
# HTTP: health, create, list, get
# ---------------------------------------------------------------------------


class TestHttpBasics:
    def test_health(self, pack: Path) -> None:
        client, _ = app_for(pack, [])
        before = snapshot(pack)
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {
            "ok": True,
            "pack": str(pack),
            "backend": "fake",
            "model": "fake-chat",
            "tools": ["probe", "write_thing", "generate_thing"],
        }
        assert snapshot(pack) == before

    def test_create_and_get_conversation(self, pack: Path) -> None:
        client, _ = app_for(pack, [])
        before = snapshot(pack)
        response = client.post("/conversations", json={"system": "You are Wick."})
        assert response.status_code == 201
        conversation_id = response.json()["id"]
        assert re.fullmatch(r"conv_[0-9a-f]{8}", conversation_id)
        assert response.json() == {"id": conversation_id}
        transcript = client.get(f"/conversations/{conversation_id}").json()
        assert transcript[0]["type"] == "meta" and transcript[0]["system"] == "You are Wick."
        assert transcript[0]["backend"] == "fake" and transcript[0]["model"] == "fake-chat"
        assert (pack / ".canon" / "agent" / f"{conversation_id}.jsonl").is_file()
        listed = client.get("/conversations").json()
        assert listed == [{"id": conversation_id, "created": transcript[0]["created"], "turns": 0}]
        assert_only_transcripts_changed(before, snapshot(pack))

    def test_the_webview_origins_are_allowed_to_read_the_answer(self, pack: Path) -> None:
        """Cradle fetches this service from the webview, so every request is
        cross-origin and a missing header makes the browser drop a 200 —
        which the panel can only report as a service that never started.
        Node's ``fetch`` does not enforce CORS, so nothing else catches it."""
        client, _ = app_for(pack, [])
        for origin in ("http://localhost:1420", "http://127.0.0.1:5173", "tauri://localhost"):
            response = client.get("/health", headers={"Origin": origin})
            assert response.status_code == 200
            assert response.headers.get("access-control-allow-origin") == origin, origin

    def test_a_json_post_can_preflight(self, pack: Path) -> None:
        """A JSON body preflights, so ``OPTIONS`` has to answer for the
        streaming message route or the turn never leaves the panel."""
        client, _ = app_for(pack, [])
        response = client.options(
            "/conversations/conv_00000000/messages",
            headers={
                "Origin": "http://localhost:1420",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,accept",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:1420"
        assert "POST" in response.headers["access-control-allow-methods"]

    def test_a_page_on_the_open_internet_is_not_allowed_to_read_it(self, pack: Path) -> None:
        """The sidecar binds loopback, and the allowance is never wider than
        the socket: a page the user happens to have open cannot read a 200
        out of their own project."""
        client, _ = app_for(pack, [])
        for origin in ("http://evil.com", "http://localhost.evil.com", "tauri://localhost.evil"):
            response = client.get("/health", headers={"Origin": origin})
            assert "access-control-allow-origin" not in response.headers, origin

    def test_create_without_body_has_no_system(self, pack: Path) -> None:
        client, _ = app_for(pack, [])
        conversation_id = client.post("/conversations").json()["id"]
        assert client.get(f"/conversations/{conversation_id}").json()[0]["system"] is None

    def test_get_unknown_conversation_is_404(self, pack: Path) -> None:
        client, _ = app_for(pack, [])
        assert client.get("/conversations/conv_deadbeef").status_code == 404
        assert client.post("/conversations/conv_deadbeef/messages", json={"text": "x"}).status_code == 404

    def test_create_on_a_missing_pack_dir_is_404(self, tmp_path: Path) -> None:
        pack = make_pack(tmp_path)
        client, _ = app_for(pack, [])
        import shutil

        shutil.rmtree(pack)
        response = client.post("/conversations", json={})
        assert response.status_code == 404
        assert "no such pack directory" in response.json()["detail"]

    def test_create_when_the_dir_is_not_a_pack_is_400(self, tmp_path: Path) -> None:
        not_a_pack = tmp_path / "notes"
        not_a_pack.mkdir()
        client, _ = app_for(not_a_pack, [])
        response = client.post("/conversations", json={})
        assert response.status_code == 400
        assert "unknown pack type" in response.json()["detail"]
        assert not (not_a_pack / ".canon").exists()

    def test_pack_problem_helper(self, pack: Path, tmp_path: Path) -> None:
        assert pack_problem(pack) is None
        assert pack_problem(tmp_path / "missing")[0] == 404
        (tmp_path / "plain").mkdir()
        assert pack_problem(tmp_path / "plain")[0] == 400

    def test_shutdown_calls_the_hook(self, pack: Path) -> None:
        called: list[bool] = []
        client, _ = app_for(pack, [], on_shutdown=lambda: called.append(True))
        response = client.post("/shutdown")
        assert response.status_code == 200
        assert response.json() == {"ok": True, "shutting_down": True}
        assert called == [True]
        assert client.app.state.shutdown_requested is True


# ---------------------------------------------------------------------------
# HTTP: messages — SSE, transcript, threading, refusals, 409
# ---------------------------------------------------------------------------


class TestMessages:
    TURNS = [
        [
            {"type": "text", "text": "Let me probe."},
            {"type": "tool_use", "name": "probe", "input": {}},
        ],
        [{"type": "text", "text": "Probed: nothing to report."}],
    ]

    def test_stream_carries_chat_events_tool_events_and_done(self, pack: Path) -> None:
        client, fake = app_for(pack, self.TURNS)
        before = snapshot(pack)
        conversation_id = client.post("/conversations", json={"system": "sys"}).json()["id"]
        status, events = post_message(client, conversation_id, "What is in this pack?")
        assert status == 200
        names = [event for event, _ in events]
        assert names[0] == "message_start"
        assert names[-1] == "done"
        assert names.count("message_start") == names.count("message_stop") == 2
        assert names.index("tool_call") < names.index("tool_result") < names.index("message_start", 1)
        assert "text_delta" in names and "tool_use_start" in names and "tool_input_delta" in names
        by_name = dict(events)
        assert by_name["tool_call"] == {"name": "probe", "input": {}}
        assert by_name["tool_result"] == {"name": "probe", "is_error": False}
        assert by_name["done"] == {
            "stop_reason": "end_turn",
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
            "conversation": conversation_id,
        }
        stops = [data for event, data in events if event == "message_stop"]
        assert stops[0]["stop_reason"] == "tool_use" and stops[1]["stop_reason"] == "end_turn"
        assert stops[1]["content"] == [{"type": "text", "text": "Probed: nothing to report."}]
        # the request the fake saw carried the system prompt and the registry's specs
        assert fake.calls[0].system == "sys"
        assert [t.name for t in fake.calls[0].tools] == ["probe", "write_thing", "generate_thing"]
        assert_only_transcripts_changed(before, snapshot(pack))

    def test_transcript_has_meta_user_assistant_tool_result_turn_end(self, pack: Path) -> None:
        client, _ = app_for(pack, self.TURNS)
        conversation_id = client.post("/conversations", json={}).json()["id"]
        post_message(client, conversation_id, "probe it")
        lines = client.get(f"/conversations/{conversation_id}").json()
        assert [line["type"] for line in lines] == ["meta", "user", "assistant", "tool_result", "assistant", "turn_end"]
        assert lines[1]["content"] == "probe it"
        assert lines[2]["content"][1]["type"] == "tool_use"
        assert lines[3]["content"][0] == {
            "type": "tool_result",
            "tool_use_id": lines[2]["content"][1]["id"],
            "content": json.dumps({"probed": True}),
        }
        assert lines[5]["stop_reason"] == "end_turn" and lines[5]["usage"]["input_tokens"] == 0
        on_disk = (pack / ".canon" / "agent" / f"{conversation_id}.jsonl").read_text(encoding="utf-8")
        assert [json.loads(line) for line in on_disk.splitlines()] == lines
        assert client.get("/conversations").json()[0]["turns"] == 1

    def test_second_message_threads_the_first(self, pack: Path) -> None:
        turns = [[{"type": "text", "text": "first answer"}], [{"type": "text", "text": "second answer"}]]
        client, fake = app_for(pack, turns)
        conversation_id = client.post("/conversations", json={}).json()["id"]
        post_message(client, conversation_id, "one")
        _, events = post_message(client, conversation_id, "two")
        assert events[-1][0] == "done"
        assert len(fake.calls) == 2
        second = fake.calls[1].messages
        assert second == [
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": [{"type": "text", "text": "first answer"}]},
            {"role": "user", "content": "two"},
        ]
        types = [line["type"] for line in client.get(f"/conversations/{conversation_id}").json()]
        assert types == ["meta", "user", "assistant", "turn_end", "user", "assistant", "turn_end"]
        assert client.get("/conversations").json()[0]["turns"] == 2

    def test_ask_tier_tool_opens_a_chip_and_the_timeout_rejects_it(self, pack: Path) -> None:
        """Row A4 over the buffered TestClient (it cannot answer mid-stream —
        the live round-trip is in test_agent_permissions.py): the chip
        opens on the stream and in the transcript, nobody decides, and the
        configured timeout rejects it as an ``is_error`` result — the turn
        continues and the grants file is never touched."""
        turns = [
            [{"type": "tool_use", "name": "write_thing", "input": {}}],
            [{"type": "text", "text": "I could not write — the chip was not answered."}],
        ]
        client, fake = app_for(pack, turns, permission_timeout=0.2)
        before = snapshot(pack)
        conversation_id = client.post("/conversations", json={}).json()["id"]
        _, events = post_message(client, conversation_id, "change it")
        by_name = dict(events)
        names = [event for event, _ in events]
        assert names.index("tool_call") < names.index("permission_request") < names.index("permission_decision")
        assert names.index("permission_decision") < names.index("tool_result")
        request = by_name["permission_request"]
        assert request["tool"] == "write_thing" and request["tier"] == "ask" and request["mode"] == "ask"
        assert request["specialist"] == "foreman" and request["actor"] == f"agent:{conversation_id}/foreman"
        assert request["target"] == "run write_thing" and request["always_allowed"] is False
        assert request["conversation"] == conversation_id and request["pack"] == str(pack)
        assert by_name["permission_decision"]["decision"] == "timeout"
        assert by_name["permission_decision"]["request_id"] == request["request_id"]
        assert by_name["tool_result"] == {
            "name": "write_thing",
            "is_error": True,
            "error": "ToolRefused: no decision within 0.2 s — write_thing was not run",
        }
        block = fake.calls[1].messages[-1]["content"][0]
        assert block["type"] == "tool_result" and block["is_error"] is True
        transcript = client.get(f"/conversations/{conversation_id}").json()
        types = [line["type"] for line in transcript]
        assert types == [
            "meta",
            "user",
            "assistant",
            "permission_request",
            "permission_decision",
            "tool_result",
            "assistant",
            "turn_end",
        ]
        assert transcript[5]["content"][0]["is_error"] is True
        assert by_name["done"]["stop_reason"] == "end_turn"
        assert client.get(f"/conversations/{conversation_id}/permissions").json() == []
        assert not (pack / ".canon" / "agent" / "permissions.json").exists()
        assert_only_transcripts_changed(before, snapshot(pack))

    def test_unknown_tool_is_an_is_error_result(self, pack: Path) -> None:
        turns = [
            [{"type": "tool_use", "name": "describe_level", "input": {"level_id": "l1"}}],
            [{"type": "text", "text": "no such tool"}],
        ]
        client, fake = app_for(pack, turns)
        conversation_id = client.post("/conversations", json={}).json()["id"]
        _, events = post_message(client, conversation_id, "describe l1")
        by_name = dict(events)
        assert by_name["tool_call"] == {"name": "describe_level", "input": {"level_id": "l1"}}
        assert by_name["tool_result"]["is_error"] is True
        block = fake.calls[1].messages[-1]["content"][0]
        assert block["is_error"] is True
        document = json.loads(block["content"][len("UnknownTool: ") :])
        assert document["known"] == ["probe", "write_thing", "generate_thing"]

    def test_backend_failure_is_an_error_event_and_journaled(self, pack: Path) -> None:
        from canon.llm.chat import ChatError

        class Broken:
            def stream(self, request: ChatRequest):
                raise ChatError("rate limited", retryable=True, status=429)
                yield  # pragma: no cover

        registry = registry_with_tools()
        app = create_app(pack, "fake", None, registry, ConversationStore(pack), backend=Broken())
        client = TestClient(app)
        conversation_id = client.post("/conversations", json={}).json()["id"]
        _, events = post_message(client, conversation_id, "hi")
        assert events[-1] == ("error", {"message": "rate limited", "retryable": True, "conversation": conversation_id})
        types = [line["type"] for line in client.get(f"/conversations/{conversation_id}").json()]
        assert types == ["meta", "user", "error", "turn_end"]
        assert client.get(f"/conversations/{conversation_id}").json()[-1]["stop_reason"] == "error"

    def test_concurrent_post_on_the_same_conversation_is_409(self, pack: Path) -> None:
        client, _ = app_for(pack, [[{"type": "text", "text": "ok"}]])
        conversation_id = client.post("/conversations", json={}).json()["id"]
        locks: TurnLocks = client.app.state.locks
        assert locks.try_acquire(conversation_id)  # a turn "in flight"
        try:
            response = client.post(f"/conversations/{conversation_id}/messages", json={"text": "again"})
            assert response.status_code == 409
            assert "one turn at a time" in response.json()["detail"]
            assert "A4.5" in response.json()["detail"]
        finally:
            locks.release(conversation_id)
        status, events = post_message(client, conversation_id, "now")
        assert status == 200 and events[-1][0] == "done"
        assert not locks.busy(conversation_id)

    def test_two_overlapping_posts_over_real_http_are_200_and_409(self, pack: Path) -> None:
        """The lock-level test above proves the check; this one proves the
        handoff — request-thread acquire, worker-thread release — with two
        genuinely overlapping POSTs over a real uvicorn socket (TestClient
        buffers streams, so it cannot overlap requests). Hermetic and $0."""
        uvicorn = pytest.importorskip("uvicorn")

        def slow_turn(request: ChatRequest) -> list:
            time.sleep(1.0)
            return [{"type": "text", "text": "slow ok"}]

        app = create_app(
            pack, "fake", None, registry_with_tools(), ConversationStore(pack), backend=FakeChatBackend(slow_turn)
        )
        sock = bind(HOST, 0)
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=port, log_config=None, access_log=False))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, name="uvicorn-test", daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and time.monotonic() < deadline:
                time.sleep(0.02)
            assert server.started
            base = f"http://{HOST}:{port}"
            conversation_id = httpx.post(f"{base}/conversations", json={}, timeout=5).json()["id"]
            url = f"{base}/conversations/{conversation_id}/messages"
            results: list[tuple[int, str]] = []  # (status, body), appended once the body is read

            def post(text: str) -> None:
                with httpx.stream("POST", url, json={"text": text}, timeout=10) as r:
                    results.append((r.status_code, "".join(r.iter_text())))

            first = threading.Thread(target=post, args=("one",))
            second = threading.Thread(target=post, args=("two",))
            first.start()
            time.sleep(0.2)
            second.start()
            first.join(15)
            second.join(15)
            by_status = dict(results)
            assert sorted(by_status) == [200, 409], results
            rejected = json.loads(by_status[409])
            assert "one turn at a time" in rejected["detail"] and "A4.5" in rejected["detail"]
            assert parse_sse(by_status[200])[-1][0] == "done"
            # the worker released the lock: a third POST after both runs
            post("three")
            assert results[-1][0] == 200 and parse_sse(results[-1][1])[-1][0] == "done"
            types = [line["type"] for line in httpx.get(f"{base}/conversations/{conversation_id}", timeout=5).json()]
            assert types == ["meta", "user", "assistant", "turn_end", "user", "assistant", "turn_end"]
        finally:
            server.should_exit = True
            thread.join(10)
            sock.close()

    def test_another_conversation_is_not_blocked(self, pack: Path) -> None:
        client, _ = app_for(pack, [[{"type": "text", "text": "ok"}]])
        first = client.post("/conversations", json={}).json()["id"]
        second = client.post("/conversations", json={}).json()["id"]
        locks: TurnLocks = client.app.state.locks
        assert locks.try_acquire(first)
        try:
            status, events = post_message(client, second, "hello")
        finally:
            locks.release(first)
        assert status == 200 and events[-1][0] == "done"

    def test_missing_text_is_422(self, pack: Path) -> None:
        client, _ = app_for(pack, [])
        conversation_id = client.post("/conversations", json={}).json()["id"]
        assert client.post(f"/conversations/{conversation_id}/messages", json={}).status_code == 422


# ---------------------------------------------------------------------------
# Backend construction + SSE framing
# ---------------------------------------------------------------------------


class TestBackendsAndFraming:
    def test_sse_frame_shape(self) -> None:
        assert sse("done", {"a": 1}) == 'event: done\ndata: {"a": 1}\n\n'

    def test_fake_backend_plays_script_then_echoes(self, tmp_path: Path) -> None:
        script = tmp_path / "script.json"
        script.write_text(json.dumps({"turns": [[{"type": "text", "text": "scripted"}]]}), encoding="utf-8")
        backend = fake_backend(script)
        req = ChatRequest(messages=[{"role": "user", "content": "hello"}])
        from canon.llm.chat import collect

        assert collect(backend.stream(req)).text == "scripted"
        assert collect(backend.stream(req)).text == "(fake backend, $0) you said: hello"
        assert collect(backend.stream(req)).text == "(fake backend, $0) you said: hello"

    def test_fake_script_bare_list_and_bad_shape(self, tmp_path: Path) -> None:
        script = tmp_path / "s.json"
        script.write_text(json.dumps([[{"type": "text", "text": "one"}]]), encoding="utf-8")
        assert isinstance(build_backend("fake", None, script), FakeChatBackend)
        script.write_text(json.dumps({"nope": 1}), encoding="utf-8")
        with pytest.raises(ValueError, match="expected a JSON list of turns"):
            build_backend("fake", None, script)

    def test_unknown_backend_id_is_a_key_error(self) -> None:
        with pytest.raises(KeyError):
            build_backend("nope", None)

    def test_registered_id_resolves_through_the_registry(self) -> None:
        from canon.backends import BackendRegistry

        BackendRegistry.reset()
        try:
            BackendRegistry.register_chat("scripted", lambda: FakeChatBackend([[{"type": "text", "text": "hi"}]]))
            assert isinstance(build_backend("scripted", None), FakeChatBackend)
        finally:
            BackendRegistry.reset()


# ---------------------------------------------------------------------------
# The watchdog
# ---------------------------------------------------------------------------


class TestWatchdog:
    def test_poll_calls_on_dead_once_when_the_parent_flips(self) -> None:
        polls: list[int] = []
        dead: list[bool] = []

        def alive() -> bool:
            polls.append(1)
            return len(polls) < 3

        assert watch_parent(alive, lambda: dead.append(True), interval=0.01) is True
        assert dead == [True]
        assert len(polls) == 3

    def test_stop_event_ends_the_poll_without_on_dead(self) -> None:
        stop = threading.Event()
        dead: list[bool] = []
        thread = threading.Thread(
            target=lambda: watch_parent(lambda: True, lambda: dead.append(True), interval=0.01, stop=stop)
        )
        thread.start()
        stop.set()
        thread.join(timeout=2)
        assert not thread.is_alive() and dead == []

    def test_pid_alive_semantics(self) -> None:
        assert pid_alive(os.getpid()) is True
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        assert pid_alive(child.pid) is False

    def test_parent_alive_probe_uses_kill_zero_for_a_foreign_pid(self) -> None:
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
        try:
            probe = parent_alive(child.pid)
            assert probe() is True
        finally:
            child.kill()
            child.wait()
        assert probe() is False


# ---------------------------------------------------------------------------
# The sidecar as a subprocess: port line, health, SSE, dies with parent
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_sidecar_prints_port_line_serves_and_dies_with_parent(tmp_path: Path) -> None:
    pack = make_pack(tmp_path)
    script = tmp_path / "script.json"
    script.write_text(json.dumps([[{"type": "text", "text": "hello from the fake"}]]), encoding="utf-8")
    helper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    sidecar = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "canon.agent.service",
            "--pack",
            str(pack),
            "--backend",
            "fake",
            "--port",
            "0",
            "--parent-pid",
            str(helper.pid),
            "--fake-script",
            str(script),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        first_line = sidecar.stdout.readline()
        info = json.loads(first_line)
        assert set(info) == {"port", "pid"}
        assert info["pid"] == sidecar.pid
        assert 1024 <= info["port"] <= 65535
        base = f"http://127.0.0.1:{info['port']}"
        health = httpx.get(f"{base}/health", timeout=5).json()
        # row A3: main() registers the read tools on start, in registration order; row A4 the write tools after them
        assert health["ok"] is True and health["pack"] == str(pack)
        # Row A4.5: the sidecar also registers the sandbox tool and the run
        # manager's two foreman tools (it always loads the roster).
        # Row A6 appends the $-tier tools after the play tool, before the
        # run manager's two.
        # Row A7 registers the vision tools between the play tool and the
        # $-tier ones; row A7.5 registers game_coder's engine-copy tools after
        # those (engine_status / engine_sync / edit_project_code).
        assert health["tools"] == [
            *READ_TOOL_NAMES, *WRITE_TOOL_NAMES, "sandbox_level", *VISION_TOOL_NAMES, *CODE_TOOL_NAMES,
            *PAID_TOOL_NAMES, "delegate", "propose_plan",
        ]
        conversation_id = httpx.post(f"{base}/conversations", json={"system": "sys"}, timeout=5).json()["id"]
        messages_url = f"{base}/conversations/{conversation_id}/messages"
        with httpx.stream("POST", messages_url, json={"text": "hi"}, timeout=10) as r:
            assert r.headers["content-type"].startswith("text/event-stream")
            events = parse_sse("".join(r.iter_text()))
        assert [e for e, _ in events][:2] == ["message_start", "text_delta"]
        assert events[-1][0] == "done"
        assert "".join(d["text"] for e, d in events if e == "text_delta") == "hello from the fake"
        transcript = (pack / ".canon" / "agent" / f"{conversation_id}.jsonl").read_text(encoding="utf-8")
        types = [json.loads(line)["type"] for line in transcript.splitlines()]
        assert types == ["meta", "user", "assistant", "turn_end"]

        started = time.monotonic()
        helper.kill()
        helper.wait()
        assert sidecar.wait(timeout=5) == 0, sidecar.stderr.read()
        assert time.monotonic() - started < 5
        with pytest.raises(httpx.HTTPError):
            httpx.get(f"{base}/health", timeout=1)
        assert sidecar.stdout.read() == ""  # the port line was the ONLY stdout
    finally:
        for proc in (sidecar, helper):
            if proc.poll() is None:
                proc.kill()
                proc.wait()


def test_sidecar_main_usage_errors_are_json_on_stderr(tmp_path: Path) -> None:
    missing = subprocess.run(
        [sys.executable, "-m", "canon.agent.service", "--pack", str(tmp_path / "nope")],
        capture_output=True,
        text=True,
    )
    assert missing.returncode == 2
    assert missing.stdout == ""
    assert json.loads(missing.stderr.strip().splitlines()[-1])["status"] == 404
    not_a_pack = subprocess.run(
        [sys.executable, "-m", "canon.agent.service", "--pack", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert not_a_pack.returncode == 2
    assert json.loads(not_a_pack.stderr.strip().splitlines()[-1])["status"] == 400
    unknown = subprocess.run(
        [sys.executable, "-m", "canon.agent.service", "--pack", str(make_pack(tmp_path)), "--backend", "nope"],
        capture_output=True,
        text=True,
    )
    assert unknown.returncode == 2
    error = json.loads(unknown.stderr.strip().splitlines()[-1])
    assert "unknown chat backend" in error["error"] and "fake" in error["known"]


def _spawn_sidecar(pack: Path, *extra: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "canon.agent.service", "--pack", str(pack), "--backend", "fake", "--port", "0", *extra],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


@pytest.mark.slow
def test_sidecar_sigterm_is_a_graceful_exit_0(tmp_path: Path) -> None:
    """SIGTERM (cradle's terminate): uvicorn stops gracefully, then re-raises
    the signal onto the handler ``serve`` installed — so the status is 0,
    not 143, and the port is closed."""
    sidecar = _spawn_sidecar(make_pack(tmp_path))
    try:
        info = json.loads(sidecar.stdout.readline())
        base = f"http://127.0.0.1:{info['port']}"
        assert httpx.get(f"{base}/health", timeout=5).json()["ok"] is True
        started = time.monotonic()
        sidecar.terminate()
        assert sidecar.wait(timeout=5) == 0, sidecar.stderr.read()
        assert time.monotonic() - started < 5
        with pytest.raises(httpx.HTTPError):
            httpx.get(f"{base}/health", timeout=1)
        assert sidecar.stdout.read() == ""
    finally:
        if sidecar.poll() is None:
            sidecar.kill()
            sidecar.wait()


@pytest.mark.slow
def test_sidecar_busy_port_is_a_json_usage_error_before_the_port_line(tmp_path: Path) -> None:
    pack = make_pack(tmp_path)
    holder = _spawn_sidecar(pack)
    try:
        port = json.loads(holder.stdout.readline())["port"]
        second = subprocess.run(
            [sys.executable, "-m", "canon.agent.service", "--pack", str(pack), "--port", str(port)],
            capture_output=True,
            text=True,
        )
        assert second.returncode == 2
        assert second.stdout == ""  # no port line was ever printed
        assert "Traceback" not in second.stderr
        error = json.loads(second.stderr.strip().splitlines()[-1])
        assert error["port"] == port and f"cannot bind 127.0.0.1:{port}" in error["error"]
    finally:
        holder.terminate()
        holder.wait(timeout=5)


def test_canon_agent_serve_is_the_same_main(tmp_path: Path) -> None:
    """``canon agent serve`` is a typer face over ``service.main`` — same
    flags, same JSON usage error, and the group shows in ``--help``."""
    canon = [sys.executable, "-m", "canon.cli.main"]
    help_out = subprocess.run([*canon, "agent", "--help"], capture_output=True, text=True)
    assert help_out.returncode == 0 and "serve" in help_out.stdout
    serve_help = subprocess.run([*canon, "agent", "serve", "--help"], capture_output=True, text=True)
    assert serve_help.returncode == 0
    for flag in ("--pack", "--backend", "--model", "--port", "--parent-pid", "--fake-script"):
        assert flag in serve_help.stdout
    result = subprocess.run(
        [sys.executable, "-m", "canon.cli.main", "agent", "serve", "--pack", str(tmp_path / "missing")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert json.loads(result.stderr.strip().splitlines()[-1])["status"] == 404


def test_service_module_has_no_price_numbers() -> None:
    source = Path(service.__file__).read_text(encoding="utf-8")
    assert "per_1m" not in source.lower() and "usd" not in source.lower()


# ---------------------------------------------------------------------------
# Row A3: the real read tools behind the service
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def generated_pack(tmp_path_factory) -> Path:
    """A real $0 platformer tree (1 stage × 1 level — the ops suite's own
    fixture recipe) so a scripted turn can call the REAL ``describe_level``."""
    out = tmp_path_factory.mktemp("a3_service_pack")
    subprocess.run(
        [
            sys.executable, "-m", "canon.packs.platformer.run_slice",
            "--backend", "fake", "--engine", "json", "--image-backend", "fake",
            "--music-backend", "none", "--sfx-backend", "none",
            "--num-stages", "1", "--num-levels", "1", "--num-enemies", "2",
            "--seed", "a3-service", "--output-dir", str(out),
        ],
        check=True,
        capture_output=True,
        cwd=Path(__file__).resolve().parents[1],
    )
    return out


class TestReadToolsInService:
    TURNS = [
        [
            {"type": "text", "text": "Let me describe the level."},
            {"type": "tool_use", "name": "describe_level", "input": {"level_id": "l1"}},
        ],
        [{"type": "text", "text": "l1 is described."}],
    ]

    def test_scripted_describe_level_rides_the_sse_as_tool_call_and_result(self, generated_pack: Path) -> None:
        from canon.adapters.platformer_read import describe_level

        registry = ToolRegistry()
        register_read_tools(registry, generated_pack)
        client, fake = app_for(generated_pack, self.TURNS, registry=registry)
        assert client.get("/health").json()["tools"] == list(READ_TOOL_NAMES)
        before = snapshot(generated_pack)
        conversation_id = client.post("/conversations", json={"system": "sys"}).json()["id"]
        status, events = post_message(client, conversation_id, "Describe l1.")
        assert status == 200
        names = [event for event, _ in events]
        assert names.index("tool_call") < names.index("tool_result") < names.index("message_start", 1)
        by_name = dict(events)
        assert by_name["tool_call"] == {"name": "describe_level", "input": {"level_id": "l1"}}
        assert by_name["tool_result"] == {"name": "describe_level", "is_error": False}
        assert by_name["done"]["stop_reason"] == "end_turn"
        # the transcript carries the verb's real, compact JSON — not a canned string
        lines = client.get(f"/conversations/{conversation_id}").json()
        block = lines[3]["content"][0]
        assert lines[3]["type"] == "tool_result" and block["type"] == "tool_result" and "is_error" not in block
        summary = json.loads(block["content"])
        assert summary == json.loads(json.dumps(describe_level(generated_pack, "l1"), default=str))
        assert summary["level_id"] == "l1" and summary["validation"]["ok"] is True and "grids" not in summary
        assert block["content"] == json.dumps(summary, separators=(",", ":"), ensure_ascii=False)
        # the request the fake saw offered every read tool, in registration order
        assert [t.name for t in fake.calls[0].tools] == list(READ_TOOL_NAMES)
        assert_only_transcripts_changed(before, snapshot(generated_pack))

    def test_bad_input_and_unknown_level_reach_the_model_as_is_error(self, generated_pack: Path) -> None:
        turns = [
            [{"type": "tool_use", "name": "describe_level", "input": {}}],
            [{"type": "tool_use", "name": "describe_level", "input": {"level_id": "l99"}}],
            [{"type": "text", "text": "no such level"}],
        ]
        registry = ToolRegistry()
        register_read_tools(registry, generated_pack)
        client, fake = app_for(generated_pack, turns, registry=registry)
        before = snapshot(generated_pack)
        conversation_id = client.post("/conversations", json={}).json()["id"]
        _, events = post_message(client, conversation_id, "describe it")
        results = [data for event, data in events if event == "tool_result"]
        assert results[0]["is_error"] is True and "level_id is required" in results[0]["error"]
        assert results[1]["is_error"] is True and results[1]["error"].startswith("FileNotFoundError")
        assert dict(events)["done"]["stop_reason"] == "end_turn"
        assert fake.calls[2].messages[-1]["content"][0]["is_error"] is True
        assert_only_transcripts_changed(before, snapshot(generated_pack))
