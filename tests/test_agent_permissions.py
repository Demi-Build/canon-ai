"""Tests for row A4 — the permission engine, the write tier and actor threading.

Hermetic and $0: the pack is a fake-backend platformer tree generated ONCE per
module with ``--orchestrate`` (so it carries ``bible.json`` for the pin
tools) and COPIED per test — the write tools mutate it, and every test starts
from the same bytes. Every conversation runs on ``FakeChatBackend``; the
round-trip tests run the service on a real uvicorn socket because the
decision endpoint must be served WHILE a turn streams (TestClient buffers a
stream to completion — it cannot answer a chip mid-turn).

The A4 gate, as the tests spell it: an ask round-trip end to end; a grant
persists in its project and provably NOT in a second; restore undoes.
"""

from __future__ import annotations

import hashlib
import json
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

import httpx
import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("PIL")
pytest.importorskip("fastapi")
uvicorn = pytest.importorskip("uvicorn")

from canon.agent import actors  # noqa: E402
from canon.agent.actors import (  # noqa: E402
    FOREMAN,
    USER_ACTOR,
    CallContext,
    agent_actor,
    bind_call,
    current_call,
    is_agent,
    parse_actor,
    user_actor,
)
from canon.agent.conversations import ConversationStore  # noqa: E402
from canon.agent.permissions import (  # noqa: E402
    ASK_MODE_NO_GRANTS,
    DECISIONS,
    GRANTS_FILE,
    GRANTS_SCHEMA,
    MODES,
    PAID_NEVER_ALWAYS,
    PLAN_MODE_NO_GRANTS,
    TIERS,
    AlwaysNotAllowed,
    Decision,
    GrantStore,
    PermissionEngine,
    PermissionRequest,
    always_allowance,
)
from canon.agent.registry import Tool, ToolRefused, ToolRegistry  # noqa: E402
from canon.agent.service import HOST, bind, create_app  # noqa: E402
from canon.agent.tools_code import CODE_TOOL_NAMES  # noqa: E402
from canon.agent.tools_paid import PAID_TOOL_NAMES  # noqa: E402
from canon.agent.tools_read import READ_TOOL_NAMES, ToolInputError, grid_ids, register_read_tools  # noqa: E402
from canon.agent.tools_vision import VISION_TOOL_NAMES  # noqa: E402
from canon.agent.tools_write import (  # noqa: E402
    TARGETS,
    WRITE_TIER,
    WRITE_TOOL_NAMES,
    register_write_tools,
    write_tool_specs,
)
from canon.backends.testing import FakeChatBackend  # noqa: E402
from canon.llm.chat import ChatRequest, ToolSpec  # noqa: E402
from canon.provenance import all_events  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
EMPTY_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}
CONV = "conv_t"


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def orchestrated_tree(tmp_path_factory) -> Path:
    """A real $0 platformer tree WITH a bible (``--orchestrate``): 1 stage ×
    2 levels, pinned seed — the write verbs' subject, copied per test."""
    out = tmp_path_factory.mktemp("a4_tree")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "canon.packs.platformer.run_slice",
            "--backend",
            "fake",
            "--engine",
            "json",
            "--image-backend",
            "fake",
            "--music-backend",
            "none",
            "--sfx-backend",
            "none",
            "--num-stages",
            "1",
            "--num-levels",
            "2",
            "--num-enemies",
            "2",
            "--num-items",
            "2",
            "--seed",
            "a4-perms",
            "--orchestrate",
            "--output-dir",
            str(out),
        ],
        check=True,
        capture_output=True,
        cwd=REPO,
    )
    return out


@pytest.fixture
def pack(orchestrated_tree: Path, tmp_path: Path) -> Path:
    dst = tmp_path / "pack"
    shutil.copytree(orchestrated_tree, dst)
    return dst


@pytest.fixture
def second_pack(orchestrated_tree: Path, tmp_path: Path) -> Path:
    dst = tmp_path / "other"
    shutil.copytree(orchestrated_tree, dst)
    return dst


def tree(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def file_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def enemy_ids(pack: Path) -> list[str]:
    return sorted(p.stem for p in (pack / "enemy").glob("*.json"))


def levels(pack: Path) -> list[dict[str, str]]:
    return grid_ids(pack, "level/{stage_id}/{level_id}/")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def dummy_tool(name: str, tier: str, run=lambda i: "ran") -> Tool:
    return Tool(
        spec=ToolSpec(name=name, description=f"a {tier} tool", input_schema=EMPTY_SCHEMA),
        tier=tier,
        run=run,
        touches="",
    )


class AutoAnswer:
    """A listener that decides every request from another thread — what the
    service's endpoint does, without the HTTP."""

    def __init__(self, engine: PermissionEngine, decision: str = "accept", reason: str | None = None) -> None:
        self.engine = engine
        self.decision = decision
        self.reason = reason
        self.requests: list[PermissionRequest] = []
        self.records: list[dict] = []
        self.errors: list[Exception] = []

    def on_request(self, request: PermissionRequest) -> None:
        self.requests.append(request)

        def answer() -> None:
            try:
                self.engine.decide(request.request_id, self.decision, self.reason)
            except Exception as exc:  # noqa: BLE001 — surfaced by the test
                self.errors.append(exc)

        threading.Thread(target=answer, daemon=True).start()

    def on_decision(self, request: PermissionRequest, record: dict) -> None:
        self.records.append(record)


def write_registry(pack: Path, engine: PermissionEngine | None = None) -> ToolRegistry:
    registry = ToolRegistry(engine if engine is not None else PermissionEngine(pack))
    register_read_tools(registry, pack)
    register_write_tools(registry, pack, actor_for=current_call)
    return registry


def run_tool(
    registry: ToolRegistry,
    name: str,
    tool_input: dict,
    *,
    conversation: str = CONV,
    decision: str = "accept",
    mode: str = "ask",
) -> tuple[dict, AutoAnswer]:
    """Execute a write tool the way a turn does: the call context bound, a
    listener answering ``decision`` from another thread."""
    engine = registry.permissions
    engine.set_mode(conversation, mode)
    answer = AutoAnswer(engine, decision)
    actor = agent_actor(conversation, FOREMAN)
    with (
        engine.listen(conversation, on_request=answer.on_request, on_decision=answer.on_decision),
        bind_call(actor, conversation),
    ):
        out = registry.execute(name, tool_input, actor=actor, conversation=conversation)
    assert answer.errors == []
    assert isinstance(out, str)
    assert out == json.dumps(json.loads(out), separators=(",", ":"), ensure_ascii=False)  # compact
    return json.loads(out), answer


# ---------------------------------------------------------------------------
# canon.agent.actors — the one place the string is built
# ---------------------------------------------------------------------------


class TestActors:
    def test_agent_actor_builds_the_string(self) -> None:
        assert agent_actor("conv_1", "foreman") == "agent:conv_1/foreman"
        assert agent_actor("conv_1") == f"agent:conv_1/{FOREMAN}"
        assert FOREMAN == "foreman"
        assert user_actor() == USER_ACTOR == "user"

    @pytest.mark.parametrize(("conversation", "specialist"), [("", "x"), ("c", ""), ("a/b", "x"), ("a b", "x")])
    def test_agent_actor_refuses_unparseable_parts(self, conversation: str, specialist: str) -> None:
        with pytest.raises(ValueError):
            agent_actor(conversation, specialist)

    def test_parse_actor_round_trips(self) -> None:
        ref = parse_actor("agent:conv_1/level_designer")
        assert (ref.kind, ref.conversation, ref.specialist) == ("agent", "conv_1", "level_designer")
        assert parse_actor("agent:conv_1").specialist is None  # row A2's pre-specialist shape
        assert parse_actor("cradle:user").kind == "user" and parse_actor("user").conversation is None
        assert is_agent("agent:c/f") and not is_agent("cradle:user")

    def test_call_context_is_bound_per_thread_and_never_invented(self) -> None:
        with pytest.raises(LookupError):
            current_call()
        seen: dict[str, str] = {}

        def worker(name: str, ready: threading.Event, go: threading.Event) -> None:
            with bind_call(agent_actor(name), name):
                ready.set()
                go.wait(5)
                seen[name] = current_call().actor

        ready_a, ready_b, go = threading.Event(), threading.Event(), threading.Event()
        a = threading.Thread(target=worker, args=("conv_a", ready_a, go))
        b = threading.Thread(target=worker, args=("conv_b", ready_b, go))
        a.start()
        b.start()
        assert ready_a.wait(5) and ready_b.wait(5)
        with pytest.raises(LookupError):
            current_call()  # the main thread never bound one
        go.set()
        a.join(5)
        b.join(5)
        assert seen == {"conv_a": "agent:conv_a/foreman", "conv_b": "agent:conv_b/foreman"}
        with bind_call("agent:x/y", "x") as context:
            assert context == CallContext(actor="agent:x/y", conversation="x") == current_call()
        with pytest.raises(LookupError):
            current_call()

    def test_the_actor_helper_is_the_only_place_the_string_is_built(self) -> None:
        """Grep test (I6): no module in ``canon.agent`` other than ``actors``
        spells ``"agent:`` in code — f-strings and concatenations included."""
        agent_dir = Path(actors.__file__).parent
        offenders = [
            path.name
            for path in sorted(agent_dir.glob("*.py"))
            if path.name != "actors.py" and re.search(r"""["']agent:""", path.read_text(encoding="utf-8"))
        ]
        assert offenders == []


# ---------------------------------------------------------------------------
# GrantStore — <pack>/.canon/agent/permissions.json
# ---------------------------------------------------------------------------


class TestGrantStore:
    def test_missing_file_reads_empty(self, tmp_path: Path) -> None:
        store = GrantStore(tmp_path)
        assert store.path == tmp_path / ".canon" / "agent" / "permissions.json" == tmp_path / GRANTS_FILE
        assert store.read() == {"schema": GRANTS_SCHEMA, "grants": []}
        assert store.grants() == [] and store.find("update_row") is None
        assert not store.path.exists()  # a read never creates it

    def test_add_writes_the_document_atomically(self, tmp_path: Path) -> None:
        store = GrantStore(tmp_path)
        grant = store.add("update_row", granted_by="agent:conv_1/foreman")
        assert grant == {
            "tool": "update_row",
            "granted_by": "agent:conv_1/foreman",
            "when": grant["when"],
            "scope": "project",
        }
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", grant["when"])
        document = json.loads(store.path.read_text(encoding="utf-8"))
        assert document == {"schema": "cradle-agent-permissions/v1", "grants": [grant]}
        assert not store.path.with_name("permissions.json.tmp").exists()
        assert store.find("update_row") == grant
        # idempotent per tool: a second "always" for the same action is the same grant
        assert store.add("update_row", granted_by="agent:conv_2/mason") == grant
        assert len(store.grants()) == 1

    def test_revoke_one_and_all(self, tmp_path: Path) -> None:
        store = GrantStore(tmp_path)
        store.add("update_row", granted_by="a")
        store.add("pin", granted_by="b")
        with pytest.raises(IndexError):
            store.revoke(2)
        assert store.revoke(0)["tool"] == "update_row"
        assert [g["tool"] for g in store.grants()] == ["pin"]
        assert store.revoke_all() == 1
        assert store.grants() == [] and store.revoke_all() == 0
        assert json.loads(store.path.read_text(encoding="utf-8")) == {"schema": GRANTS_SCHEMA, "grants": []}

    def test_foreign_schema_is_never_reinterpreted(self, tmp_path: Path) -> None:
        store = GrantStore(tmp_path)
        store.path.parent.mkdir(parents=True)
        store.path.write_text(
            json.dumps({"schema": "something-else/v9", "grants": [{"tool": "pin"}]}), encoding="utf-8"
        )
        with pytest.raises(ValueError, match="expected schema"):
            store.grants()


# ---------------------------------------------------------------------------
# PermissionEngine — classify (pure) + the round-trip (threads)
# ---------------------------------------------------------------------------


class TestClassify:
    def test_vocabularies_are_plain_strings(self) -> None:
        assert TIERS == ("auto", "ask", "paid")
        assert MODES == ("ask", "plan", "allow")
        assert DECISIONS == ("accept", "always", "reject")
        assert Decision("allow", "x").allowed is True and Decision("ask", "x").allowed is False

    @pytest.mark.parametrize("mode", MODES)
    def test_auto_allows_in_every_mode(self, mode: str) -> None:
        decision = PermissionEngine().classify(dummy_tool("probe", "auto"), {}, actor="a", conversation="c", mode=mode)
        assert decision == Decision("allow", "auto-tier: probe reads only, reads never ask")

    @pytest.mark.parametrize("mode", MODES)
    def test_ask_asks_without_a_grant_in_every_mode(self, mode: str, tmp_path: Path) -> None:
        engine = PermissionEngine(tmp_path)
        decision = engine.classify(dummy_tool("update_row", "ask"), {}, actor="a", conversation="c", mode=mode)
        assert decision.outcome == "ask" and f"in {mode} mode" in decision.reason

    @pytest.mark.parametrize("mode", MODES)
    def test_a_grant_allows_the_action_in_every_mode_for_any_actor(self, mode: str, tmp_path: Path) -> None:
        engine = PermissionEngine(tmp_path)
        engine.grants.add("update_row", granted_by="agent:conv_1/foreman")
        decision = engine.classify(
            dummy_tool("update_row", "ask"), {}, actor="agent:conv_9/level_designer", conversation="conv_9", mode=mode
        )
        assert decision.outcome == "allow" and decision.grant["tool"] == "update_row"
        assert "granted by agent:conv_1/foreman" in decision.reason

    @pytest.mark.parametrize("mode", MODES)
    def test_paid_asks_in_every_mode_even_with_a_grant_of_that_name(self, mode: str, tmp_path: Path) -> None:
        engine = PermissionEngine(tmp_path)
        engine.grants.add("generate_thing", granted_by="x")
        decision = engine.classify(dummy_tool("generate_thing", "paid"), {}, actor="a", conversation="c", mode=mode)
        assert decision.outcome == "ask" and "confirms every time" in decision.reason

    def test_unknown_tier_and_mode_fail_closed(self) -> None:
        engine = PermissionEngine()
        refused = engine.classify(dummy_tool("t", "mystery"), {}, actor="a", conversation="c", mode="ask")
        assert refused.outcome == "refuse" and "unknown tier 'mystery'" in refused.reason
        refused = engine.classify(dummy_tool("t", "ask"), {}, actor="a", conversation="c", mode="yolo")
        assert refused.outcome == "refuse" and "unknown permission mode 'yolo'" in refused.reason
        # …for EVERY tier, reads included: an unknown mode is not understood,
        # so nothing runs under it (the docstring's "any other tier or mode").
        refused = engine.classify(dummy_tool("t", "auto"), {}, actor="a", conversation="c", mode="yolo")
        assert refused.outcome == "refuse" and "unknown permission mode 'yolo'" in refused.reason

    def test_always_allowance_matrix(self) -> None:
        assert always_allowance("ask", "allow") == (True, None)
        assert always_allowance("ask", "ask") == (False, ASK_MODE_NO_GRANTS)
        assert always_allowance("ask", "plan") == (False, PLAN_MODE_NO_GRANTS)
        assert always_allowance("paid", "allow") == (False, PAID_NEVER_ALWAYS)
        assert "A4.5" in PLAN_MODE_NO_GRANTS and "Allow mode" in ASK_MODE_NO_GRANTS

    def test_mode_defaults_per_conversation(self, tmp_path: Path) -> None:
        engine = PermissionEngine(tmp_path, default_mode="ask")
        assert engine.mode_for("c") == "ask"
        engine.set_mode("c", "allow")
        assert engine.mode_for("c") == "allow" and engine.mode_for("d") == "ask"


class TestRoundTrip:
    def _engine(self, pack: Path, **kwargs) -> tuple[PermissionEngine, Tool]:
        engine = PermissionEngine(pack, **kwargs)
        engine.describe("update_row", TARGETS["update_row"])
        tool = dummy_tool("update_row", "ask")
        return engine, tool

    def test_no_listener_refuses_instead_of_hanging(self, pack: Path) -> None:
        engine, tool = self._engine(pack)
        decision = engine.check(tool, {}, actor="agent:c/foreman", conversation="c")
        assert decision.outcome == "refuse" and decision.reason.startswith("no one to ask: update_row is ask-tier")

    def test_accept_runs_once_and_the_request_is_shaped_for_the_chip(self, pack: Path) -> None:
        engine, tool = self._engine(pack)
        answer = AutoAnswer(engine, "accept")
        tool_input = {"type": "enemy", "id": "cinder_beetle", "fields": {"hp": 9}}
        with engine.listen("conv_1", on_request=answer.on_request, on_decision=answer.on_decision):
            decision = engine.check(tool, tool_input, actor="agent:conv_1/foreman", conversation="conv_1", mode="ask")
        assert decision == Decision("allow", "accepted by the user: update_row runs once")
        (request,) = answer.requests
        assert re.fullmatch(r"perm_[0-9a-f]{8}", request.request_id)
        payload = request.payload()
        assert payload == {
            "request_id": request.request_id,
            "conversation": "conv_1",
            "tool": "update_row",
            "input": tool_input,
            "tier": "ask",
            "actor": "agent:conv_1/foreman",
            "specialist": "foreman",
            "target": "update enemy cinder_beetle (hp)",
            "touches": "",
            "mode": "ask",
            "always_allowed": False,
            "always_reason": ASK_MODE_NO_GRANTS,
            "pack": str(pack),
            # Row A4.5's ⏹ keys on the run a chip belongs to; the foreman's
            # own calls belong to no delegated run.
            "run_id": None,
            # Row P1-A6: additive, and None for every free tier.
            "estimate": None,
            "created": payload["created"],
        }
        (record,) = answer.records
        assert record["decision"] == "accept" and record["by"] == "user" and record["grant"] is None
        assert record["request_id"] == request.request_id and record["tool"] == "update_row"
        assert engine.pending() == []
        assert not (pack / GRANTS_FILE).exists()  # accept never grants
        # a second call asks again
        with engine.listen("conv_1", on_request=answer.on_request, on_decision=answer.on_decision):
            engine.check(tool, tool_input, actor="agent:conv_1/foreman", conversation="conv_1", mode="ask")
        assert len(answer.requests) == 2

    def test_pending_lists_the_open_request_until_decided(self, pack: Path) -> None:
        engine, tool = self._engine(pack)
        opened = threading.Event()
        holder: list[PermissionRequest] = []

        def on_request(request: PermissionRequest) -> None:
            holder.append(request)
            opened.set()

        results: list[Decision] = []
        with engine.listen("conv_1", on_request=on_request, on_decision=lambda r, d: None):
            worker = threading.Thread(
                target=lambda: results.append(
                    engine.check(tool, {}, actor="agent:conv_1/foreman", conversation="conv_1")
                )
            )
            worker.start()
            assert opened.wait(5)
            assert [p["request_id"] for p in engine.pending("conv_1")] == [holder[0].request_id]
            assert engine.pending("conv_2") == []
            engine.decide(holder[0].request_id, "accept")
            worker.join(5)
        assert results[0].outcome == "allow" and engine.pending() == []

    def test_always_in_allow_mode_writes_the_grant_and_the_next_call_never_asks(self, pack: Path) -> None:
        engine, tool = self._engine(pack)
        answer = AutoAnswer(engine, "always")
        with engine.listen("conv_1", on_request=answer.on_request, on_decision=answer.on_decision):
            decision = engine.check(tool, {}, actor="agent:conv_1/foreman", conversation="conv_1", mode="allow")
        assert decision.outcome == "allow" and decision.grant["tool"] == "update_row"
        assert answer.requests[0].always_allowed is True and answer.requests[0].always_reason is None
        document = json.loads((pack / GRANTS_FILE).read_text(encoding="utf-8"))
        assert document["schema"] == "cradle-agent-permissions/v1"
        assert document["grants"] == [
            {
                "tool": "update_row",
                "granted_by": "agent:conv_1/foreman",
                "when": document["grants"][0]["when"],
                "scope": "project",
            }
        ]
        assert answer.records[0]["grant"] == document["grants"][0]
        # the grant governs the ACTION: another specialist, another conversation, any mode — no chip
        with engine.listen("conv_2", on_request=answer.on_request, on_decision=answer.on_decision):
            again = engine.check(tool, {}, actor="agent:conv_2/level_designer", conversation="conv_2", mode="ask")
        assert again.outcome == "allow" and again.grant == document["grants"][0]
        assert len(answer.requests) == 1

    @pytest.mark.parametrize(("mode", "reason"), [("ask", ASK_MODE_NO_GRANTS), ("plan", PLAN_MODE_NO_GRANTS)])
    def test_always_outside_allow_mode_is_refused_with_the_reason_and_stays_pending(
        self, pack: Path, mode: str, reason: str
    ) -> None:
        engine, tool = self._engine(pack)
        opened = threading.Event()
        holder: list[PermissionRequest] = []

        def on_request(request: PermissionRequest) -> None:
            holder.append(request)
            opened.set()

        results: list[Decision] = []
        with engine.listen("conv_1", on_request=on_request, on_decision=lambda r, d: None):
            worker = threading.Thread(
                target=lambda: results.append(
                    engine.check(tool, {}, actor="agent:conv_1/foreman", conversation="conv_1", mode=mode)
                )
            )
            worker.start()
            assert opened.wait(5)
            assert holder[0].always_allowed is False and holder[0].always_reason == reason
            with pytest.raises(AlwaysNotAllowed) as info:
                engine.decide(holder[0].request_id, "always")
            assert str(info.value) == reason
            assert len(engine.pending("conv_1")) == 1  # still open — the user can still Accept / Reject
            assert not (pack / GRANTS_FILE).exists()
            engine.decide(holder[0].request_id, "accept")
            worker.join(5)
        assert results[0].outcome == "allow"

    def test_paid_always_asks_and_always_is_refused(self, pack: Path) -> None:
        engine = PermissionEngine(pack)
        engine.grants.add("generate_thing", granted_by="x")  # a stray grant of that name never covers paid
        paid = dummy_tool("generate_thing", "paid")
        requests: list[PermissionRequest] = []
        errors: list[Exception] = []

        def on_request(request: PermissionRequest) -> None:
            requests.append(request)

            def answer() -> None:
                try:
                    engine.decide(request.request_id, "always")
                except AlwaysNotAllowed as exc:
                    errors.append(exc)
                engine.decide(request.request_id, "accept")

            threading.Thread(target=answer, daemon=True).start()

        with engine.listen("conv_1", on_request=on_request, on_decision=lambda r, d: None):
            decision = engine.check(paid, {}, actor="agent:conv_1/foreman", conversation="conv_1", mode="allow")
        assert decision.outcome == "allow"
        assert requests[0].tier == "paid" and requests[0].always_allowed is False
        assert requests[0].always_reason == PAID_NEVER_ALWAYS
        assert len(errors) == 1 and str(errors[0]) == PAID_NEVER_ALWAYS

    def test_reject_reaches_the_tool_as_the_users_reason(self, pack: Path) -> None:
        engine, tool = self._engine(pack)
        answer = AutoAnswer(engine, "reject", "wrong enemy — the beetle is fine")
        with engine.listen("conv_1", on_request=answer.on_request, on_decision=answer.on_decision):
            decision = engine.check(tool, {}, actor="agent:conv_1/foreman", conversation="conv_1")
        assert decision == Decision("refuse", "rejected by the user: wrong enemy — the beetle is fine")
        assert answer.records[0]["reason"] == "wrong enemy — the beetle is fine"
        silent = AutoAnswer(engine, "reject")
        with engine.listen("conv_1", on_request=silent.on_request, on_decision=silent.on_decision):
            decision = engine.check(tool, {}, actor="agent:conv_1/foreman", conversation="conv_1")
        assert decision.reason == "rejected by the user: no reason given"

    def test_decide_errors(self, pack: Path) -> None:
        engine, tool = self._engine(pack)
        with pytest.raises(KeyError):
            engine.decide("perm_00000000", "accept")
        with pytest.raises(ValueError, match="decision must be one of"):
            engine.decide("perm_00000000", "maybe")
        opened = threading.Event()
        holder: list[PermissionRequest] = []

        def on_request(request: PermissionRequest) -> None:
            holder.append(request)
            opened.set()

        with engine.listen("conv_1", on_request=on_request, on_decision=lambda r, d: None):
            worker = threading.Thread(
                target=lambda: engine.check(tool, {}, actor="agent:conv_1/foreman", conversation="conv_1")
            )
            worker.start()
            assert opened.wait(5)
            with pytest.raises(KeyError):  # another conversation cannot answer it
                engine.decide(holder[0].request_id, "accept", conversation="conv_2")
            engine.decide(holder[0].request_id, "accept", conversation="conv_1")
            worker.join(5)
            with pytest.raises(KeyError):  # decided once
                engine.decide(holder[0].request_id, "accept")

    def test_timeout_rejects_with_no_decision(self, pack: Path) -> None:
        engine, tool = self._engine(pack, timeout=0.05)
        records: list[dict] = []
        with engine.listen("conv_1", on_request=lambda r: None, on_decision=lambda r, d: records.append(d)):
            decision = engine.check(tool, {}, actor="agent:conv_1/foreman", conversation="conv_1")
        assert decision == Decision("refuse", "no decision within 0.05 s — update_row was not run")
        assert records[0]["decision"] == "timeout" and records[0]["by"] == "service"
        assert engine.pending() == []

    def test_grant_persists_in_its_pack_and_not_in_a_second(self, pack: Path, second_pack: Path) -> None:
        engine_a, tool = self._engine(pack)
        answer = AutoAnswer(engine_a, "always")
        with engine_a.listen("conv_1", on_request=answer.on_request, on_decision=answer.on_decision):
            assert engine_a.check(tool, {}, actor="agent:conv_1/foreman", conversation="conv_1", mode="allow").allowed
        assert (pack / GRANTS_FILE).is_file()
        assert not (second_pack / GRANTS_FILE).exists()
        engine_b = PermissionEngine(second_pack)
        assert (
            engine_b.classify(tool, {}, actor="agent:conv_1/foreman", conversation="conv_1", mode="allow").outcome
            == "ask"
        )
        # a fresh engine on pack A reads the grant from disk — it survived the process that wrote it
        assert PermissionEngine(pack).classify(tool, {}, actor="x", conversation="y", mode="ask").outcome == "allow"

    def test_target_falls_back_to_run_name(self, pack: Path) -> None:
        engine = PermissionEngine(pack)
        engine.describe("broken", lambda i: i["missing"])
        assert engine.target_for(dummy_tool("plain", "ask"), {}) == "run plain"
        assert engine.target_for(dummy_tool("broken", "ask"), {}) == "run broken"


# ---------------------------------------------------------------------------
# The write tools — real verbs on a copied pack, attributed to the agent
# ---------------------------------------------------------------------------


class TestWriteTools:
    def test_registration_order_tier_specs_and_touches(self, pack: Path) -> None:
        registry = write_registry(pack)
        assert registry.names() == [*READ_TOOL_NAMES, *WRITE_TOOL_NAMES]
        assert WRITE_TIER == "ask"
        for name in WRITE_TOOL_NAMES:
            tool = registry.get(name)
            assert tool.tier == "ask" and tool.touches
            assert tool.spec.input_schema["additionalProperties"] is False  # strict inputs
            assert name in TARGETS
        assert [spec.name for spec in write_tool_specs()] == list(WRITE_TOOL_NAMES)
        assert set(WRITE_TOOL_NAMES) == {
            "apply_level_edit",
            "import_level_grids",
            "create_level",
            "publish_level",
            "edit_world_map",
            "update_row",
            "update_schema",
            "pin",
            "unpin",
            "restore",
        }

    def test_update_row_journals_the_agent_actor_and_session(self, pack: Path) -> None:
        registry = write_registry(pack)
        enemy = enemy_ids(pack)[0]
        path = pack / "enemy" / f"{enemy}.json"
        before_hash = file_hash(path)
        before_tree = tree(pack)
        original_hp = read_json(path)["stats"]["hp"]
        assert original_hp != 9
        result, answer = run_tool(registry, "update_row", {"type": "enemy", "id": enemy, "fields": {"hp": 9}})
        assert answer.requests[0].target == f"update enemy {enemy} (hp)"
        assert result["changed"] == {"hp": {"from": original_hp, "to": 9}}
        assert read_json(path)["stats"]["hp"] == 9 and read_json(path)["status"] == "user_edited"
        (event,) = result["journal"]
        # Row P1-A6 widened the compact view with the lane fields the write
        # card / History render (identity here; costCents only when there IS a
        # cost). ``ts`` came with them so a spend row can point back.
        assert {k: v for k, v in event.items() if k != "ts"} == {
            "artifact_id": f"enemy:{enemy}",
            "op": "edit",
            "actor": "agent:conv_t/foreman",
            "identity": "agent:conv_t/foreman",
            "kind": "db_update",
            "session": "conv_t",
            "before_hash": before_hash,
            "after_hash": file_hash(path),
        }
        assert "costCents" not in event, "an uncosted edit is not a cost row"
        on_disk = all_events(pack)[-1]
        assert on_disk["actor"] == "agent:conv_t/foreman" and on_disk["session"] == "conv_t"
        changed = {rel for rel in set(before_tree) | set(tree(pack)) if before_tree.get(rel) != tree(pack).get(rel)}
        assert changed == {f"enemy/{enemy}.json", ".canon/journal.jsonl"} | {
            rel for rel in tree(pack) if rel.startswith(".canon/objects/")
        }
        assert not (pack / GRANTS_FILE).exists()  # accept grants nothing; tools never touch the grants file

    def test_restore_undoes_an_update_row_by_hash_and_journals_restore(self, pack: Path) -> None:
        registry = write_registry(pack)
        enemy = enemy_ids(pack)[0]
        path = pack / "enemy" / f"{enemy}.json"
        original = read_json(path)
        first, _ = run_tool(registry, "update_row", {"type": "enemy", "id": enemy, "fields": {"hp": 9}})
        second, _ = run_tool(registry, "update_row", {"type": "enemy", "id": enemy, "fields": {"hp": 11}})
        h0, h1 = first["journal"][0]["before_hash"], first["journal"][0]["after_hash"]
        assert second["journal"][0]["before_hash"] == h1
        # back to the first edit: the bytes are EXACTLY that version's
        result, answer = run_tool(registry, "restore", {"target": f"enemy:{enemy}", "version_hash": h1})
        assert answer.requests[0].target == f"restore enemy:{enemy} to {h1[:19]}…"
        assert file_hash(path) == h1 and read_json(path)["stats"]["hp"] == 9
        (event,) = result["journal"]
        assert event["op"] == "restore" and event["actor"] == "agent:conv_t/foreman" and event["session"] == "conv_t"
        assert event["before_hash"] == second["journal"][0]["after_hash"] and event["after_hash"] == h1
        assert result["kind"] == "row_restore" and result["artifact_id"] == f"enemy:{enemy}"
        # back to the original content (status re-stamped user_edited — nothing else differs)
        result, _ = run_tool(registry, "restore", {"target": f"enemy:{enemy}", "version_hash": h0})
        restored = read_json(path)
        assert restored["stats"]["hp"] == original["stats"]["hp"]
        assert {k: v for k, v in restored.items() if k != "status"} == {
            k: v for k, v in original.items() if k != "status"
        }
        assert [e["op"] for e in all_events(pack)[-4:]] == ["edit", "edit", "restore", "restore"]

    def test_apply_level_edit_and_level_step_restore(self, pack: Path) -> None:
        registry = write_registry(pack)
        stage, level_id = levels(pack)[0]["stage_id"], levels(pack)[0]["level_id"]
        level_dir = pack / "level" / stage / level_id
        level = read_json(level_dir / "level.json")
        entities = [
            {
                "enemy_id": e["ref"].split(":", 1)[1],
                "x": e["pos"][0],
                "y": e["pos"][1],
                **({"variant": e["overrides"]["variant"]} if e.get("overrides", {}).get("variant") else {}),
            }
            for e in level["entities"]
        ]
        assert entities, "the fixture places enemies"
        entities[0]["x"] = entities[0]["x"] - 1 if entities[0]["x"] > 0 else entities[0]["x"] + 1
        entities_before = file_hash(level_dir / "entities.json")
        result, answer = run_tool(
            registry, "apply_level_edit", {"level_id": level_id, "sparse_edits": {"entities": entities}}
        )
        assert answer.requests[0].target == f"edit level {level_id} (entities)"
        assert result["updated"] == ["entities"] and result["status"] == "user_edited"
        (event,) = result["journal"]
        assert event["artifact_id"] == f"level:{stage}/{level_id}/entities" and event["kind"] == "enemy_move"
        assert event["actor"] == "agent:conv_t/foreman" and event["before_hash"] == entities_before
        # restore the step by hash — bytes back exactly, journaled as restore
        target = f"level:{stage}/{level_id}/entities"
        result, _ = run_tool(registry, "restore", {"target": target, "version_hash": entities_before})
        assert file_hash(level_dir / "entities.json") == entities_before
        assert result["restored_step"] == "entities" and result["restored_to"] == entities_before
        assert result["journal"][0]["op"] == "restore" and result["journal"][0]["session"] == "conv_t"

    def test_import_level_grids_replaces_collision(self, pack: Path) -> None:
        registry = write_registry(pack)
        stage, level_id = levels(pack)[0]["stage_id"], levels(pack)[0]["level_id"]
        with np.load(pack / "level" / stage / level_id / "collision.npz") as data:
            rows = data["collision"].tolist()
        rows[0][0] = 0 if rows[0][0] else 1
        result, answer = run_tool(registry, "import_level_grids", {"level_id": level_id, "layers": {"collision": rows}})
        assert answer.requests[0].target == f"import grids into {level_id}"
        assert result["changed_cells"] == 1 and "collision" in result["updated"]
        assert (
            result["journal"][0]["kind"] == "terrain_paint" and result["journal"][0]["actor"] == "agent:conv_t/foreman"
        )
        with np.load(pack / "level" / stage / level_id / "collision.npz") as data:
            assert data["collision"].tolist() == rows

    def test_create_and_publish_level(self, pack: Path) -> None:
        registry = write_registry(pack)
        stage = levels(pack)[0]["stage_id"]
        created, answer = run_tool(registry, "create_level", {"params": {"stage_id": stage, "width": 24, "height": 12}})
        assert answer.requests[0].target == f"create a level in stage {stage}"
        new_id = created["level_id"]
        assert created["draft"] is True and (pack / "level" / stage / new_id / "level.json").is_file()
        assert {e["op"] for e in created["journal"]} == {"create"} and created["journal"][0]["session"] == "conv_t"
        assert new_id not in read_json(pack / "stage" / stage / "stage.json")["level_ids"]
        published, answer = run_tool(registry, "publish_level", {"level_id": new_id, "position": 1})
        assert answer.requests[0].target == f"publish {new_id}"
        assert published["published"] is True and published["stage_levels"][0] == new_id
        assert (
            published["journal"][0]["artifact_id"] == f"stage:{stage}" and published["journal"][0]["kind"] == "publish"
        )
        removed, answer = run_tool(registry, "publish_level", {"level_id": new_id, "remove": True})
        assert answer.requests[0].target == f"unpublish {new_id}"
        assert removed["published"] is False and new_id not in removed["stage_levels"]

    def test_edit_world_map(self, pack: Path) -> None:
        registry = write_registry(pack)
        level_id = levels(pack)[0]["level_id"]
        result, answer = run_tool(registry, "edit_world_map", {"edits": {"nodes": {level_id: {"pos": [0.25, 0.5]}}}})
        assert answer.requests[0].target == "edit the world map"
        assert result["world_map"] == "updated" and result["changed"] == [f"placed {level_id}"]
        assert result["journal"][0]["artifact_id"] == "world" and result["journal"][0]["kind"] == "world_map_edit"
        assert read_json(pack / "world.json")["map_nodes"][level_id] == {"pos": [0.25, 0.5]}

    def test_update_schema_lands_a_pack_local_override(self, pack: Path) -> None:
        registry = write_registry(pack)
        assert not (pack / "schemas" / "enemy.json").exists()
        result, answer = run_tool(
            registry, "update_schema", {"type": "enemy", "changes": {"fields": {"patrol_range": {"range": [3, 9]}}}}
        )
        assert answer.requests[0].target == "change the enemy schema"
        assert result["changed"] == {"patrol_range": {"from": {"range": [3, 8]}, "to": {"range": [3, 9]}}}
        assert read_json(pack / "schemas" / "enemy.json")["fields"]["patrol_range"] == {"range": [3, 9]}
        assert result["journal"][0]["artifact_id"] == "schema:enemy" and result["journal"][0]["session"] == "conv_t"

    def test_pin_and_unpin_mirror_the_cli(self, pack: Path) -> None:
        from canon.bible.models import Bible
        from canon.pipeline.orchestrator import pinnable_ids

        registry = write_registry(pack)
        pinnable = sorted(pinnable_ids(Bible.load(pack / "bible.json")))
        assert pinnable, "the orchestrated fixture has pinnable art"
        target = pinnable[0]
        result, answer = run_tool(registry, "pin", {"ids": [target]})
        assert answer.requests[0].target == f"pin {target}"
        assert result == {"result": "pinned", "pinned": [target], "already_pinned": [], "stale_cleared": []}
        assert read_json(pack / "bible.json")["metadata"]["pinned"] == [target]
        again, _ = run_tool(registry, "pin", {"ids": [target]})
        assert again["already_pinned"] == [target] and again["pinned"] == []
        with pytest.raises(ValueError, match="not pinnable"):
            run_tool(registry, "pin", {"ids": [target, "level:x/y/entities"]})
        assert read_json(pack / "bible.json")["metadata"]["pinned"] == [target]  # atomic: nothing changed
        result, answer = run_tool(registry, "unpin", {"ids": [target, "tileset:nope"]})
        assert answer.requests[0].target == f"unpin {target}, tileset:nope"
        assert result == {"result": "unpinned", "unpinned": [target], "not_pinned": ["tileset:nope"]}
        assert read_json(pack / "bible.json")["metadata"]["pinned"] == []

    def test_pin_without_a_bible_is_a_named_error(self, pack: Path) -> None:
        (pack / "bible.json").unlink()
        registry = write_registry(pack)
        with pytest.raises(FileNotFoundError, match="no bible.json"):
            run_tool(registry, "pin", {"ids": ["tileset:x"]})

    def test_input_validation_and_unknown_restore_target(self, pack: Path) -> None:
        registry = write_registry(pack)
        with pytest.raises(ToolInputError, match="update_row: input.fields is required"):
            run_tool(registry, "update_row", {"type": "enemy", "id": "x"})
        with pytest.raises(ToolInputError, match="not an accepted field"):
            run_tool(registry, "restore", {"target": "enemy:x", "version_hash": "sha256:0", "extra": 1})
        with pytest.raises(ValueError, match="cannot restore 'schema:enemy'"):
            run_tool(registry, "restore", {"target": "schema:enemy", "version_hash": "sha256:0"})
        with pytest.raises(ValueError, match="level targets are level:<stage>/<level>/<step>"):
            run_tool(registry, "restore", {"target": "level:s1", "version_hash": "sha256:0"})
        with pytest.raises(ValueError, match="fields must be a non-empty object"):
            run_tool(registry, "update_row", {"type": "enemy", "id": "x", "fields": {}})

    def test_a_write_outside_a_turn_has_no_actor_and_refuses_to_invent_one(self, pack: Path) -> None:
        registry = write_registry(pack)
        engine = registry.permissions
        answer = AutoAnswer(engine, "accept")
        with engine.listen(CONV, on_request=answer.on_request, on_decision=answer.on_decision):
            with pytest.raises(LookupError, match="no call context is bound"):
                registry.execute(
                    "edit_world_map", {"edits": {"locked": True}}, actor=agent_actor(CONV), conversation=CONV
                )
        assert read_json(pack / "world.json").get("map_locked") is not True

    def test_rejected_write_changes_nothing(self, pack: Path) -> None:
        registry = write_registry(pack)
        before = tree(pack)
        enemy = enemy_ids(pack)[0]
        with pytest.raises(ToolRefused, match="rejected by the user: no reason given"):
            run_tool(registry, "update_row", {"type": "enemy", "id": enemy, "fields": {"hp": 9}}, decision="reject")
        assert tree(pack) == before


# ---------------------------------------------------------------------------
# The service round-trip over a real socket
# ---------------------------------------------------------------------------


class Stream:
    """One ``POST …/messages`` read on a background thread, frame by frame."""

    def __init__(self, url: str, body: dict) -> None:
        self.events: list[tuple[str, dict]] = []
        self.status: int | None = None
        self._frames: queue.Queue[tuple[str, dict] | None] = queue.Queue()
        self._thread = threading.Thread(target=self._read, args=(url, body), daemon=True)
        self._thread.start()

    def _read(self, url: str, body: dict) -> None:
        try:
            with httpx.stream("POST", url, json=body, timeout=60) as response:
                self.status = response.status_code
                event = data = None
                for line in response.iter_lines():
                    if line.startswith("event: "):
                        event = line[len("event: ") :]
                    elif line.startswith("data: "):
                        data = json.loads(line[len("data: ") :])
                    elif line == "" and event is not None:
                        self._frames.put((event, data or {}))
                        event = data = None
        finally:
            self._frames.put(None)

    def wait_for(self, name: str, timeout: float = 15) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            assert remaining > 0, f"no {name!r} within {timeout}s; got {[e for e, _ in self.events]}"
            frame = self._frames.get(timeout=remaining)
            assert frame is not None, f"stream ended before {name!r}; got {[e for e, _ in self.events]}"
            self.events.append(frame)
            if frame[0] == name:
                return frame[1]

    def finish(self, timeout: float = 30) -> list[tuple[str, dict]]:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            assert remaining > 0, "stream did not end"
            frame = self._frames.get(timeout=remaining)
            if frame is None:
                break
            self.events.append(frame)
        self._thread.join(5)
        return self.events

    def names(self) -> list[str]:
        return [event for event, _ in self.events]


class LiveServer:
    """The app on a real uvicorn socket (the A2 overlapping-POST precedent)."""

    def __init__(self, app) -> None:
        self.sock = bind(HOST, 0)
        self.port = self.sock.getsockname()[1]
        self.base = f"http://{HOST}:{self.port}"
        self.server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=self.port, log_config=None, access_log=False))
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [self.sock]}, daemon=True)

    def __enter__(self) -> LiveServer:
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        assert self.server.started
        return self

    def __exit__(self, *exc) -> None:
        self.server.should_exit = True
        self.thread.join(10)
        self.sock.close()

    def create(self) -> str:
        return httpx.post(f"{self.base}/conversations", json={"system": "sys"}, timeout=5).json()["id"]

    def send(self, conversation_id: str, text: str, mode: str | None = None) -> Stream:
        body: dict = {"text": text}
        if mode is not None:
            body["mode"] = mode
        return Stream(f"{self.base}/conversations/{conversation_id}/messages", body)

    def decide(self, conversation_id: str, request_id: str, decision: str, reason: str | None = None) -> httpx.Response:
        body: dict = {"request_id": request_id, "decision": decision}
        if reason is not None:
            body["reason"] = reason
        return httpx.post(f"{self.base}/conversations/{conversation_id}/permissions", json=body, timeout=5)

    def pending(self, conversation_id: str) -> httpx.Response:
        return httpx.get(f"{self.base}/conversations/{conversation_id}/permissions", timeout=5)

    def transcript(self, conversation_id: str) -> list[dict]:
        return httpx.get(f"{self.base}/conversations/{conversation_id}", timeout=5).json()

    def grants(self, pack: Path | None = None) -> httpx.Response:
        params = {"pack": str(pack)} if pack is not None else None
        return httpx.get(f"{self.base}/packs/permissions", params=params, timeout=5)


class ScriptedForeman:
    """A ``FakeChatBackend`` script the test extends between turns — the
    fake foreman: ``push`` a turn (a block list) before each message."""

    def __init__(self) -> None:
        self.turns: deque[list | dict] = deque()
        self.calls: list[ChatRequest] = []

    def push(self, *turns: list | dict) -> None:
        self.turns.extend(turns)

    def __call__(self, request: ChatRequest) -> list | dict:
        self.calls.append(request)
        if not self.turns:
            return [{"type": "text", "text": "(script exhausted)"}]
        return self.turns.popleft()


def tool_use(name: str, **tool_input) -> dict:
    return {"type": "tool_use", "name": name, "input": tool_input}


def text(value: str) -> list:
    return [{"type": "text", "text": value}]


def live_app(pack: Path, registry: ToolRegistry | None = None, **kwargs) -> tuple[LiveServer, ScriptedForeman]:
    foreman = ScriptedForeman()
    tools = registry if registry is not None else write_registry(pack)
    app = create_app(pack, "fake", None, tools, ConversationStore(pack), backend=FakeChatBackend(foreman), **kwargs)
    return LiveServer(app), foreman


class TestServiceRoundTrip:
    def test_an_unknown_mode_is_a_422_not_a_degraded_turn(self, pack: Path) -> None:
        """A typo'd mode used to be accepted and then refuse every ask/paid
        tool mid-turn, where only the model saw it — the same shape the
        decision endpoint already rejects with a 422."""
        server, _ = live_app(pack)
        with server:
            conversation = server.create()
            bad = httpx.post(
                f"{server.base}/conversations/{conversation}/messages",
                json={"text": "hp 9", "mode": "Allow"},
                timeout=5,
            )
            assert bad.status_code == 422
            assert "mode must be one of ['ask', 'plan', 'allow']" in bad.text
            # …and the turn lock was not left held: a good mode still runs.
            assert server.send(conversation, "hello", mode="allow").finish()[-1][0] == "done"

    def test_accept_round_trip_end_to_end(self, pack: Path) -> None:
        """The gate: the fake foreman calls update_row → permission_request on
        the stream → POST accept → tool_result + done; the journal event
        carries actor agent:<conv>/foreman and session <conv>."""
        server, foreman = live_app(pack)
        enemy = enemy_ids(pack)[0]
        path = pack / "enemy" / f"{enemy}.json"
        before_hash = file_hash(path)
        with server:
            conversation = server.create()
            foreman.push([tool_use("update_row", type="enemy", id=enemy, fields={"hp": 9})], text("Bumped hp to 9."))
            stream = server.send(conversation, "give the beetle more hp")
            request = stream.wait_for("permission_request")
            assert request == {
                "request_id": request["request_id"],
                "conversation": conversation,
                "tool": "update_row",
                "input": {"type": "enemy", "id": enemy, "fields": {"hp": 9}},
                "tier": "ask",
                "actor": f"agent:{conversation}/foreman",
                "specialist": "foreman",
                "target": f"update enemy {enemy} (hp)",
                "touches": "writes <type>/<id>.json via canon db update; journals edit",
                "mode": "ask",
                "always_allowed": False,
                "always_reason": ASK_MODE_NO_GRANTS,
                "pack": str(pack),
                "run_id": None,
                "estimate": None,  # row P1-A6, additive
                "created": request["created"],
            }
            # the turn is BLOCKED: nothing after the request yet, the file is untouched, the request is pending
            time.sleep(0.2)
            assert stream.names()[-1] == "permission_request" and file_hash(path) == before_hash
            pending = server.pending(conversation).json()
            assert [p["request_id"] for p in pending] == [request["request_id"]] and pending[0] == request
            response = server.decide(conversation, request["request_id"], "accept")
            assert response.status_code == 200
            body = response.json()
            assert body["ok"] is True and body["decision"] == "accept" and body["tool"] == "update_row"
            assert body["request_id"] == request["request_id"] and body["grant"] is None and body["by"] == "user"
            decision = stream.wait_for("permission_decision")
            assert decision == {k: v for k, v in body.items() if k != "ok"}
            assert stream.wait_for("tool_result") == {"name": "update_row", "is_error": False}
            events = stream.finish()
            assert events[-1][0] == "done" and events[-1][1]["stop_reason"] == "end_turn"
            names = [e for e, _ in events]
            assert names.index("tool_call") < names.index("permission_request") < names.index("permission_decision")
            assert names.index("permission_decision") < names.index("tool_result") < names.index("done")
            assert server.pending(conversation).json() == []
            transcript = server.transcript(conversation)
        assert [line["type"] for line in transcript] == [
            "meta",
            "user",
            "assistant",
            "permission_request",
            "permission_decision",
            "tool_result",
            "assistant",
            "turn_end",
        ]
        assert transcript[3]["request_id"] == request["request_id"] and transcript[3]["target"] == request["target"]
        assert transcript[4]["decision"] == "accept"
        result = json.loads(transcript[5]["content"][0]["content"])
        assert "is_error" not in transcript[5]["content"][0]
        assert read_json(path)["stats"]["hp"] == 9
        (event,) = result["journal"]
        assert event["actor"] == f"agent:{conversation}/foreman" and event["session"] == conversation
        assert event["before_hash"] == before_hash and event["after_hash"] == file_hash(path)
        on_disk = all_events(pack)[-1]
        assert on_disk["actor"] == f"agent:{conversation}/foreman" and on_disk["session"] == conversation
        # the permission lines never reach the provider: the second request replays messages only
        assert [m["role"] for m in foreman.calls[1].messages] == ["user", "assistant", "user"]
        assert not (pack / GRANTS_FILE).exists()

    def test_always_in_allow_mode_grants_and_the_next_call_runs_without_asking(self, pack: Path) -> None:
        server, foreman = live_app(pack)
        enemy = enemy_ids(pack)[0]
        with server:
            conversation = server.create()
            foreman.push([tool_use("update_row", type="enemy", id=enemy, fields={"hp": 9})], text("done"))
            stream = server.send(conversation, "hp 9", mode="allow")
            request = stream.wait_for("permission_request")
            assert request["mode"] == "allow" and request["always_allowed"] is True and request["always_reason"] is None
            response = server.decide(conversation, request["request_id"], "always")
            assert response.status_code == 200
            grant = response.json()["grant"]
            assert grant == {
                "tool": "update_row",
                "granted_by": f"agent:{conversation}/foreman",
                "when": grant["when"],
                "scope": "project",
            }
            assert stream.wait_for("permission_decision")["grant"] == grant
            assert stream.wait_for("tool_result")["is_error"] is False
            assert stream.finish()[-1][0] == "done"
            document = read_json(pack / GRANTS_FILE)
            assert document == {"schema": "cradle-agent-permissions/v1", "grants": [grant]}
            assert server.grants().json() == {
                "pack": str(pack),
                "path": str(pack / GRANTS_FILE),
                "grants": [{"index": 0, **grant}],
            }
            # the second identical call — sent in ASK mode, from a new conversation — runs without a chip
            other = server.create()
            foreman.push([tool_use("update_row", type="enemy", id=enemy, fields={"hp": 10})], text("done again"))
            events = server.send(other, "hp 10", mode="ask").finish()
            names = [e for e, _ in events]
            assert "permission_request" not in names and "permission_decision" not in names
            assert dict(events)["tool_result"] == {"name": "update_row", "is_error": False}
            assert names[-1] == "done"
            assert [line["type"] for line in server.transcript(other)] == [
                "meta",
                "user",
                "assistant",
                "tool_result",
                "assistant",
                "turn_end",
            ]
        assert read_json(pack / "enemy" / f"{enemy}.json")["stats"]["hp"] == 10

    def test_always_in_ask_mode_is_409_with_the_reason_and_the_chip_stays(self, pack: Path) -> None:
        server, foreman = live_app(pack)
        enemy = enemy_ids(pack)[0]
        with server:
            conversation = server.create()
            foreman.push([tool_use("update_row", type="enemy", id=enemy, fields={"hp": 9})], text("done"))
            stream = server.send(conversation, "hp 9", mode="ask")
            request = stream.wait_for("permission_request")
            refused = server.decide(conversation, request["request_id"], "always")
            assert refused.status_code == 409 and refused.json()["detail"] == ASK_MODE_NO_GRANTS
            assert len(server.pending(conversation).json()) == 1
            assert not (pack / GRANTS_FILE).exists()
            assert server.decide(conversation, request["request_id"], "accept").status_code == 200
            assert stream.wait_for("tool_result")["is_error"] is False
            assert stream.finish()[-1][0] == "done"
        assert not (pack / GRANTS_FILE).exists()

    def test_reject_is_an_is_error_result_with_the_reason_and_changes_nothing(self, pack: Path) -> None:
        server, foreman = live_app(pack)
        enemy = enemy_ids(pack)[0]
        before = tree(pack)
        with server:
            conversation = server.create()
            foreman.push(
                [tool_use("update_row", type="enemy", id=enemy, fields={"hp": 9})],
                text("Understood — I left the beetle alone."),
            )
            stream = server.send(conversation, "hp 9")
            request = stream.wait_for("permission_request")
            response = server.decide(conversation, request["request_id"], "reject", reason="not that enemy")
            assert response.status_code == 200 and response.json()["reason"] == "not that enemy"
            assert stream.wait_for("permission_decision")["decision"] == "reject"
            assert stream.wait_for("tool_result") == {
                "name": "update_row",
                "is_error": True,
                "error": "ToolRefused: rejected by the user: not that enemy",
            }
            done = stream.finish()[-1]
            assert done[0] == "done" and done[1]["stop_reason"] == "end_turn"  # the turn continued
            transcript = server.transcript(conversation)
        block = transcript[5]["content"][0]
        assert transcript[5]["type"] == "tool_result" and block["is_error"] is True
        assert block["content"] == "ToolRefused: rejected by the user: not that enemy"
        assert foreman.calls[1].messages[-1]["content"][0]["is_error"] is True
        after = tree(pack)
        assert {rel for rel in set(before) | set(after) if before.get(rel) != after.get(rel)} == {
            f".canon/agent/{conversation}.jsonl"
        }

    def test_paid_tier_always_asks_and_always_is_refused_in_every_mode(self, pack: Path) -> None:
        registry = write_registry(pack)
        spent: list[dict] = []
        registry.register(
            Tool(
                spec=ToolSpec(name="generate_thing", description="a paid generation", input_schema=EMPTY_SCHEMA),
                tier="paid",
                run=lambda i: spent.append(i) or "spent $0 (fake)",
                touches="spends via a provider",
            )
        )
        server, foreman = live_app(pack, registry)
        with server:
            conversation = server.create()
            foreman.push([tool_use("generate_thing")], text("generated"))
            stream = server.send(conversation, "generate", mode="allow")
            request = stream.wait_for("permission_request")
            assert request["tier"] == "paid" and request["always_allowed"] is False
            assert request["always_reason"] == PAID_NEVER_ALWAYS and request["target"] == "run generate_thing"
            refused = server.decide(conversation, request["request_id"], "always")
            assert refused.status_code == 409 and refused.json()["detail"] == PAID_NEVER_ALWAYS
            assert server.decide(conversation, request["request_id"], "accept").status_code == 200
            assert stream.wait_for("tool_result")["is_error"] is False
            assert stream.finish()[-1][0] == "done" and spent == [{}]
            # and again in allow mode: paid never becomes granted
            foreman.push([tool_use("generate_thing")], text("generated again"))
            second = server.send(conversation, "generate again", mode="allow")
            again = second.wait_for("permission_request")
            assert again["tier"] == "paid"
            server.decide(conversation, again["request_id"], "reject", reason="not now")
            assert second.wait_for("tool_result")["is_error"] is True
            second.finish()
        assert not (pack / GRANTS_FILE).exists() and len(spent) == 1

    def test_grant_persists_in_pack_a_and_pack_b_still_asks(self, pack: Path, second_pack: Path) -> None:
        server_a, foreman_a = live_app(pack)
        server_b, foreman_b = live_app(second_pack)
        enemy = enemy_ids(pack)[0]
        with server_a, server_b:
            conv_a = server_a.create()
            foreman_a.push([tool_use("update_row", type="enemy", id=enemy, fields={"hp": 9})], text("done"))
            stream_a = server_a.send(conv_a, "hp 9", mode="allow")
            request_a = stream_a.wait_for("permission_request")
            assert server_a.decide(conv_a, request_a["request_id"], "always").status_code == 200
            assert stream_a.finish()[-1][0] == "done"
            assert read_json(pack / GRANTS_FILE)["grants"][0]["tool"] == "update_row"
            assert not (second_pack / GRANTS_FILE).exists()
            # pack B: same tool, same input, allow mode — a chip, not a pass
            conv_b = server_b.create()
            foreman_b.push([tool_use("update_row", type="enemy", id=enemy, fields={"hp": 9})], text("done"))
            stream_b = server_b.send(conv_b, "hp 9", mode="allow")
            request_b = stream_b.wait_for("permission_request")
            assert request_b["pack"] == str(second_pack) and request_b["tool"] == "update_row"
            assert server_b.grants().json()["grants"] == []
            # B's service can read A's grants by path (the Settings pane's per-project list, A6)
            assert [g["tool"] for g in server_b.grants(pack).json()["grants"]] == ["update_row"]
            assert server_b.grants(pack / "missing").status_code == 404
            assert server_b.decide(conv_b, request_b["request_id"], "accept").status_code == 200
            assert stream_b.finish()[-1][0] == "done"
        assert not (second_pack / GRANTS_FILE).exists()
        assert read_json(second_pack / "enemy" / f"{enemy}.json")["stats"]["hp"] == 9

    def test_restore_undoes_an_accepted_update_row_over_the_service(self, pack: Path) -> None:
        server, foreman = live_app(pack)
        enemy = enemy_ids(pack)[0]
        path = pack / "enemy" / f"{enemy}.json"
        with server:
            conversation = server.create()

            def accept_next(stream: Stream) -> list[tuple[str, dict]]:
                request = stream.wait_for("permission_request")
                assert server.decide(conversation, request["request_id"], "accept").status_code == 200
                events = stream.finish()
                assert events[-1][0] == "done" and dict(events)["tool_result"]["is_error"] is False
                return events

            foreman.push([tool_use("update_row", type="enemy", id=enemy, fields={"hp": 9})], text("hp 9"))
            accept_next(server.send(conversation, "hp 9"))
            first = json.loads(server.transcript(conversation)[5]["content"][0]["content"])
            h1 = first["journal"][0]["after_hash"]
            foreman.push([tool_use("update_row", type="enemy", id=enemy, fields={"hp": 11})], text("hp 11"))
            accept_next(server.send(conversation, "hp 11"))
            assert read_json(path)["stats"]["hp"] == 11
            # "undo this" — the foreman restores the write card's after-hash
            foreman.push([tool_use("restore", target=f"enemy:{enemy}", version_hash=h1)], text("restored"))
            stream = server.send(conversation, "undo that")
            assert stream.wait_for("permission_request")["target"] == f"restore enemy:{enemy} to {h1[:19]}…"
            request = stream.events[-1][1]
            assert server.decide(conversation, request["request_id"], "accept").status_code == 200
            assert stream.finish()[-1][0] == "done"
        assert file_hash(path) == h1 and read_json(path)["stats"]["hp"] == 9
        last = all_events(pack)[-1]
        assert last["op"] == "restore" and last["actor"] == f"agent:{conversation}/foreman"
        assert last["session"] == conversation and last["after_hash"] == h1

    def test_revoke_endpoints(self, pack: Path) -> None:
        server, _ = live_app(pack)
        store = GrantStore(pack)
        store.add("update_row", granted_by="agent:conv_1/foreman")
        store.add("pin", granted_by="agent:conv_1/foreman")
        with server:
            listed = server.grants().json()
            assert [(g["index"], g["tool"]) for g in listed["grants"]] == [(0, "update_row"), (1, "pin")]
            missing = httpx.delete(f"{server.base}/packs/permissions/5", timeout=5)
            assert missing.status_code == 404 and "no grant at index 5" in missing.json()["detail"]
            one = httpx.delete(f"{server.base}/packs/permissions/0", timeout=5)
            assert one.status_code == 200 and one.json()["revoked"]["tool"] == "update_row"
            assert [g["tool"] for g in one.json()["grants"]] == ["pin"]
            assert store.grants()[0]["tool"] == "pin"
            everything = httpx.delete(f"{server.base}/packs/permissions", timeout=5)
            assert everything.status_code == 200 and everything.json() == {
                "revoked": 1,
                "pack": str(pack),
                "path": str(pack / GRANTS_FILE),
                "grants": [],
            }
            assert store.grants() == []
            # revoke on another pack, by path
            other = httpx.delete(f"{server.base}/packs/permissions", params={"pack": str(pack / "nope")}, timeout=5)
            assert other.status_code == 404

    def test_decision_endpoint_errors(self, pack: Path) -> None:
        server, _ = live_app(pack)
        with server:
            conversation = server.create()
            assert server.pending(conversation).json() == []
            assert server.pending("conv_deadbeef").status_code == 404
            assert server.decide("conv_deadbeef", "perm_00000000", "accept").status_code == 404
            unknown = server.decide(conversation, "perm_00000000", "accept")
            assert unknown.status_code == 404 and "no pending permission request" in unknown.json()["detail"]
            bad = server.decide(conversation, "perm_00000000", "maybe")
            assert bad.status_code == 422
            assert (
                httpx.post(f"{server.base}/conversations/{conversation}/permissions", json={}, timeout=5).status_code
                == 422
            )


# ---------------------------------------------------------------------------
# The sidecar: write tools registered after the reads; the timeout flag
# ---------------------------------------------------------------------------


def test_sidecar_registers_write_tools_after_reads_and_validates_the_timeout(pack: Path) -> None:
    bad = subprocess.run(
        [sys.executable, "-m", "canon.agent.service", "--pack", str(pack), "--permission-timeout", "0"],
        capture_output=True,
        text=True,
    )
    assert bad.returncode == 2 and bad.stdout == ""
    assert "--permission-timeout" in json.loads(bad.stderr.strip().splitlines()[-1])["error"]
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
            "--permission-timeout",
            "0.5",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        info = json.loads(sidecar.stdout.readline())
        health = httpx.get(f"http://{HOST}:{info['port']}/health", timeout=5).json()
        # Row A4.5 registers the sandbox tool after the writes, then the run
        # manager's two foreman tools (the sidecar always loads the roster).
        # Row A6 appends the $-tier tools after the play tool.
        # Row A7 registers the vision tools between the play tool and the
        # $-tier ones; row A7.5 registers game_coder's engine-copy tools
        # (engine_status / engine_sync / edit_project_code) after those.
        assert health["tools"] == [
            *READ_TOOL_NAMES, *WRITE_TOOL_NAMES, "sandbox_level", *VISION_TOOL_NAMES, *CODE_TOOL_NAMES,
            *PAID_TOOL_NAMES, "delegate", "propose_plan",
        ]
        assert httpx.get(f"http://{HOST}:{info['port']}/packs/permissions", timeout=5).json()["grants"] == []
    finally:
        sidecar.terminate()
        sidecar.wait(timeout=5)
