"""Tests for row P1-A7's second half — the routing eval and the verify loop
(Phase 1 §5.1 "routing is the foreman's tool choice", §5.5, goal 2).

Two gates live here:

1. **Routing.** The corpus conversations whose subject is the FOREMAN's tool
   choice pass on the fake with a strict delegation order, and a mis-routed
   script is a NAMED failure rather than a plausible pass. A fake backend
   cannot route, so what is asserted is the delegation calls a script makes;
   on a real backend (row A8's provider-swap leg, user-run) the same corpus
   measures real routing with the wording check freed.
2. **Break and repair.** A specialist writes something that makes a level
   INVALID, the mandatory post-mutation validation catches it, and the SAME
   run repairs it unprompted on its one corrected retry. This runs against a
   real generated $0 pack with the real write + read + validate tools, so the
   closing assertion is that the FILES ON DISK are valid again — never a
   scripted claim of done.

Everything is $0 and keyless: ``FakeChatBackend`` plays every turn.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from collections import deque
from pathlib import Path

import pytest

from canon.agent.actors import current_call
from canon.agent.conversations import ConversationStore
from canon.agent.eval import run_scripted
from canon.agent.evals import CONVERSATIONS, ROUTING_CONVERSATIONS, ScriptedConversation, conversation, routing_corpus
from canon.agent.permissions import GrantStore, PermissionEngine
from canon.agent.registry import ToolRegistry
from canon.agent.roster import core_law, load_roster
from canon.agent.runs import DELEGATE_TOOL, RUN_FAILURE_LIMIT, VLM_VERIFY_PROMPT, RunManager
from canon.agent.tools_read import register_read_tools
from canon.agent.tools_vision import AGENT_SETTINGS_FILE, register_vision_tools
from canon.agent.tools_write import register_write_tools
from canon.backends.testing import FakeChatBackend, FakeVLMBackend
from canon.llm.chat import ChatRequest

REPO = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def generated_tree(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("a7_routing_tree")
    subprocess.run(
        [
            sys.executable, "-m", "canon.packs.platformer.run_slice",
            "--backend", "fake", "--engine", "json", "--image-backend", "fake",
            "--music-backend", "none", "--sfx-backend", "none",
            "--num-stages", "1", "--num-levels", "2", "--num-enemies", "2", "--num-items", "2",
            "--seed", "a7-routing", "--orchestrate", "--output-dir", str(out),
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


def tool_use(name: str, **tool_input) -> dict:
    return {"type": "tool_use", "name": name, "input": tool_input}


def text(value: str) -> list:
    return [{"type": "text", "text": value}]


class Router:
    """A ``FakeChatBackend`` script keyed by the acting specialist (the role id
    inside the assembled prompt) — the row A4.5 test helper, reused verbatim so
    one backend serves the foreman and every specialist run."""

    _ROLE = re.compile(r"# Role: .* \(`([a-z_]+)`\)")

    def __init__(self) -> None:
        self.turns: dict[str, deque] = {}
        self.calls: list[tuple[str, ChatRequest]] = []

    def push(self, specialist: str, *turns: list | dict) -> None:
        self.turns.setdefault(specialist, deque()).extend(turns)

    def __call__(self, request: ChatRequest) -> list | dict:
        match = self._ROLE.search(request.system or "")
        role = match.group(1) if match else "foreman"
        self.calls.append((role, request))
        script = self.turns.get(role)
        if not script:
            return text(f"({role}: script exhausted)")
        return script.popleft()


def level_dir(pack: Path, level_id: str) -> Path:
    return next(p for p in (pack / "level").glob(f"*/{level_id}") if p.is_dir())


def level_json(pack: Path, level_id: str) -> dict:
    return json.loads((level_dir(pack, level_id) / "level.json").read_text(encoding="utf-8"))


def validate(pack: Path, level_id: str) -> dict:
    from canon.packs.platformer.ops import validate_level

    return validate_level(pack, level_id)


def manager_for(pack: Path, backend, *, grants: tuple[str, ...] = ("apply_level_edit",)) -> RunManager:
    engine = PermissionEngine(pack, default_mode="allow")
    for name in grants:
        GrantStore(pack).add(name, granted_by="agent:t/foreman")
    registry = ToolRegistry(engine)
    register_read_tools(registry, pack)
    register_write_tools(registry, pack, actor_for=current_call)
    register_vision_tools(registry, pack)
    manager = RunManager(
        pack_dir=pack, registry=registry, backend=backend, store=ConversationStore(pack), roster=load_roster()
    )
    manager.register_tools()
    return manager


def delegate_once(manager: RunManager, specialist: str, task: str) -> tuple[dict, list[tuple[str, dict]]]:
    """One delegation through the foreman's own tool, with the events it emitted."""
    conversation = manager.store.create("fake", None, None)
    events: list[tuple[str, dict]] = []
    with manager.turn(conversation, emit=lambda e, d: events.append((e, d))):
        result = manager.delegate(conversation=conversation, specialist=specialist, task=task)
    return result, events


# ---------------------------------------------------------------------------
# 1. Routing
# ---------------------------------------------------------------------------


class TestRoutingCorpus:
    def test_the_corpus_carries_the_three_routing_conversations(self) -> None:
        names = [c.name for c in CONVERSATIONS]
        for name in ROUTING_CONVERSATIONS:
            assert name in names
        assert [c.name for c in routing_corpus()] == list(ROUTING_CONVERSATIONS)

    def test_the_routing_foreman_is_the_shipped_one_prompt_and_tools(self) -> None:
        """Doctrine 2, both halves. The corpus may not carry a second
        definition of the foreman: its system prompt is ``roster/core.md`` +
        ``roster/foreman.md`` (so an edit to either moves this eval, which is
        what row A8's real-backend leg measures), and every tool it offers is
        one ``roster/foreman.json`` actually holds (so no script can make a
        turn the shipped foreman could not)."""
        foreman = load_roster()["foreman"]
        allowed = set(foreman.tools)
        for conv in routing_corpus():
            assert foreman.role_prompt.strip() in conv.system, conv.name
            assert core_law().strip() in conv.system, conv.name
            offered = {spec.name for spec in conv.tools}
            assert offered <= allowed, f"{conv.name} offers {sorted(offered - allowed)}, not on the foreman's roster"

    @pytest.mark.parametrize("name", ROUTING_CONVERSATIONS)
    def test_each_routing_conversation_passes_on_the_fake(self, name: str) -> None:
        conv = conversation(name)
        result = run_scripted(conv, FakeChatBackend(conv.fake_turns))
        assert result.failures == []
        assert result.passed is True

    def test_a_mixed_request_reaches_both_crafts_in_one_turn(self) -> None:
        conv = conversation("routing-design-and-art")
        assert conv.expected_delegations == ["level_designer", "artist"]
        # Both delegations ride ONE assistant turn — §5.5's parallel fan-out.
        fan_out = [turn for turn in conv.fake_turns if isinstance(turn, list)
                   and sum(1 for b in turn if b.get("type") == "tool_use") > 1]
        assert fan_out, "the mixed request must hand both tasks out in one turn"

    def test_a_pure_question_delegates_to_nobody(self) -> None:
        conv = conversation("routing-question-delegates-to-nobody")
        assert conv.expected_delegations == []
        result = run_scripted(conv, FakeChatBackend(conv.fake_turns))
        assert result.passed and DELEGATE_TOOL not in result.tool_calls

    def test_an_art_only_request_never_touches_the_level_designer(self) -> None:
        conv = conversation("routing-art-only")
        assert conv.expected_delegations == ["artist"]
        result = run_scripted(conv, FakeChatBackend(conv.fake_turns))
        assert result.passed and "level_designer" not in json.dumps(result.failures)


class TestRoutingFailuresAreNamed:
    def _misroute(self, conv: ScriptedConversation, specialist: str) -> ScriptedConversation:
        """The same conversation with its FIRST delegation sent elsewhere."""
        turns = json.loads(json.dumps(conv.fake_turns))
        for turn in turns:
            blocks = turn if isinstance(turn, list) else turn.get("content", [])
            for block in blocks:
                if block.get("type") == "tool_use" and block["name"] == DELEGATE_TOOL:
                    block["input"]["specialist"] = specialist
                    return ScriptedConversation(
                        **{**conv.__dict__, "name": f"{conv.name}-misrouted", "fake_turns": turns}
                    )
        raise AssertionError("no delegation to misroute")

    def test_a_wrong_delegation_is_a_named_failure(self) -> None:
        conv = conversation("routing-art-only")
        broken = self._misroute(conv, "writer")
        result = run_scripted(broken, FakeChatBackend(broken.fake_turns), strict_text=False)
        assert result.passed is False
        assert any("delegations: expected ['artist'] got ['writer']" in f for f in result.failures), result.failures

    def test_delegating_on_a_pure_question_is_a_named_failure(self) -> None:
        conv = conversation("routing-question-delegates-to-nobody")
        extra = json.loads(json.dumps(conv.fake_turns))
        extra.insert(1, [tool_use(DELEGATE_TOOL, specialist="level_designer", task="look around")])
        noisy = ScriptedConversation(**{**conv.__dict__, "name": "question-but-delegated", "fake_turns": extra})
        result = run_scripted(noisy, FakeChatBackend(noisy.fake_turns), strict_text=False)
        assert result.passed is False
        assert any("delegations: expected [] got ['level_designer']" in f for f in result.failures), result.failures

    def test_the_delegation_check_survives_a_freed_wording_check(self) -> None:
        """Row A8's provider-swap leg frees the wording; routing stays strict —
        that is the whole point of measuring it on a real backend."""
        conv = conversation("routing-design-and-art")
        broken = self._misroute(conv, "playtester")
        result = run_scripted(broken, FakeChatBackend(broken.fake_turns), strict_text=False)
        assert any(f.startswith("delegations:") for f in result.failures)


# ---------------------------------------------------------------------------
# 2. The verify loop: break, catch, repair — on real files
# ---------------------------------------------------------------------------


class TestBreakAndRepair:
    def test_the_agent_breaks_a_level_and_repairs_it_unprompted_in_the_same_run(self, pack: Path) -> None:
        """The gate: a write makes l1 invalid, the MANDATORY post-mutation
        validation catches it without anyone asking, and the specialist's one
        corrected retry fixes it — ending with a level that is valid ON DISK."""
        good_exit = list(level_json(pack, "l1")["exit"])
        assert validate(pack, "l1")["ok"] is True, "the fixture starts valid"

        router = Router()
        router.push(
            "level_designer",
            # (1) the break: the exit is moved into open air, behind nothing
            # solid — reachability dies and the level becomes uncompletable.
            [tool_use("apply_level_edit", level_id="l1", sparse_edits={"exit": [good_exit[0], 2]})],
            text("Moved the exit up to the ledge. Done."),
            # (2) the repair, driven only by the verdict the manager fed back.
            [tool_use("apply_level_edit", level_id="l1", sparse_edits={"exit": good_exit})],
            text("The exit had no floor under it; I put it back on solid ground and re-validated."),
        )
        manager = manager_for(pack, FakeChatBackend(router))
        result, events = delegate_once(manager, "level_designer", "Move l1's exit somewhere more interesting")
        run = manager.runs[result["run_id"]]

        # The files on disk are valid again — not a claim, the validator.
        assert validate(pack, "l1")["ok"] is True
        assert level_json(pack, "l1")["exit"] == good_exit

        # The verdict rode back with the run, and the repair was the ONE
        # corrected retry (§5.5's budget, not a second retry path).
        assert result["status"] == "ok"
        assert result["verify"]["status"] == "ok"
        assert result["verify"]["levels"] == ["l1"]
        assert run.failures == 1 and run.failures < RUN_FAILURE_LIMIT
        assert [step["tool"] for step in run.steps] == ["apply_level_edit", "apply_level_edit"]
        assert len(run.artifacts) == 2, "both writes are attributed to this run"

        # The repair turn was a follow-up on the SAME run's history, carrying
        # the verdict — the specialist was told what broke, not re-briefed.
        designer_requests = [q for role, q in router.calls if role == "level_designer"]
        assert len(designer_requests) == 4
        repair_turn = designer_requests[2].messages[-1]
        assert repair_turn["role"] == "user"
        assert "MANDATORY POST-MUTATION VALIDATION FAILED" in repair_turn["content"]
        assert "no solid ground" in repair_turn["content"]
        assert designer_requests[2].messages[0]["content"].startswith("Move l1's exit")

        # A5's run card sees the verdict on run_end.
        end = next(data for name, data in events if name == "run_end")
        assert end["verify"]["status"] == "ok"
        progress = [d for name, d in events if name == "run_progress" and d["event"].get("type") == "verify"]
        assert progress and progress[0]["event"]["status"] == "failed", "the caught break is visible, never swallowed"

    def test_a_still_failing_verify_is_a_structured_failure_not_a_claim_of_done(self, pack: Path) -> None:
        good_exit = list(level_json(pack, "l1")["exit"])
        router = Router()
        router.push(
            "level_designer",
            [tool_use("apply_level_edit", level_id="l1", sparse_edits={"exit": [good_exit[0], 2]})],
            text("Done!"),
            [tool_use("apply_level_edit", level_id="l1", sparse_edits={"exit": [good_exit[0], 3]})],
            text("Definitely done now!"),
        )
        manager = manager_for(pack, FakeChatBackend(router))
        result, _ = delegate_once(manager, "level_designer", "Move l1's exit up")

        assert result["status"] == "failed", "a red validation can never come back as done"
        body = json.loads(result["error"])
        assert body["error"] == "verify_failed" and body["retried"] is True
        assert body["verify"]["status"] == "failed"
        problems = body["verify"]["checks"][0]["problems"]
        assert any("exit" in problem for problem in problems)
        stage = level_dir(pack, "l1").parent.name
        assert body["artifacts_touched"] == [f"level:{stage}/l1/level"] * 2
        assert validate(pack, "l1")["ok"] is False, "nothing is auto-reverted — undo is the user's act (doctrine 6)"

    def test_a_run_that_mutates_nothing_is_not_verified(self, pack: Path) -> None:
        router = Router()
        router.push("playtester", [tool_use("validate_level", level_id="l1")], text("l1 looks fine."))
        manager = manager_for(pack, FakeChatBackend(router))
        result, _ = delegate_once(manager, "playtester", "look at l1")
        assert result["status"] == "ok" and result["verify"] is None
        assert result["artifacts_touched"] == []

    def test_the_verdict_names_what_it_could_not_validate(self, pack: Path) -> None:
        """Doctrine 4: a verification we cannot run is reported with its
        reason, never as a pass. The platformer seed declares no ROW
        validators, so a row edit says exactly that."""
        enemy_id = sorted(p.stem for p in (pack / "enemy").glob("*.json"))[0]
        router = Router()
        router.push(
            "writer",
            [tool_use("update_row", type="enemy", id=enemy_id, fields={"name": "Ashen Thing"})],
            text("Renamed."),
        )
        manager = manager_for(pack, FakeChatBackend(router), grants=("update_row",))
        result, _ = delegate_once(manager, "writer", f"rename {enemy_id}")
        assert result["status"] == "ok"
        verdict = result["verify"]
        assert verdict["status"] == "skipped"
        check = verdict["checks"][0]
        assert check["kind"] == "rows" and check["ok"] is None
        assert "declares no row validators" in check["reason"]
        assert check["targets"] == [f"enemy:{enemy_id}"]

    def test_vlm_qa_is_optional_advisory_and_never_built_by_the_manager(self, pack: Path) -> None:
        """Doctrine 3: the manager constructs no provider — a judge is passed
        in, and in tests it is the FAKE. Its answer is an opinion beside the
        validators, never the thing that decides the verdict."""
        good_exit = list(level_json(pack, "l1")["exit"])
        router = Router()
        router.push(
            "level_designer",
            [tool_use("apply_level_edit", level_id="l1", sparse_edits={"exit": good_exit})],
            text("Re-seated the exit."),
        )
        judge = FakeVLMBackend(lambda prompt, images: '{"passed": false, "notes": "the water reads as sky"}')
        manager = manager_for(pack, FakeChatBackend(router))
        assert RunManager(
            pack_dir=pack, registry=manager.registry, backend=manager.backend, store=manager.store
        ).vlm is None, "no judge by default — nothing may spend on an opinion"
        manager.vlm = judge

        result, _ = delegate_once(manager, "level_designer", "nudge l1's exit")
        verdict = result["verify"]
        assert result["status"] == "ok" and verdict["status"] == "ok", (
            "a judge's 'no' is an opinion, not a defect — the validators decide"
        )
        opinion = verdict["opinions"][0]
        assert opinion["kind"] == "vlm" and opinion["target"] == "l1"
        assert opinion["ok"] is False and "water" in opinion["notes"]
        assert opinion["frames"] > 0 and opinion["model"] == "fake-vlm"
        assert judge.calls and judge.calls[0]["prompt"] == VLM_VERIFY_PROMPT
        assert all(size > 0 for size in judge.calls[0]["image_sizes"])

    def test_a_demoted_capture_is_skipped_rather_than_fired_unasked(self, pack: Path) -> None:
        """ASSUMPTION-6a's escape hatch outranks an advisory look."""
        settings_path = pack / AGENT_SETTINGS_FILE
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps({"tool_tiers": {"capture_frames": "ask"}}), encoding="utf-8")
        good_exit = list(level_json(pack, "l1")["exit"])
        router = Router()
        router.push(
            "level_designer",
            [tool_use("apply_level_edit", level_id="l1", sparse_edits={"exit": good_exit})],
            text("Done."),
        )
        judge = FakeVLMBackend(lambda prompt, images: '{"passed": true}')
        manager = manager_for(pack, FakeChatBackend(router))
        manager.vlm = judge
        result, _ = delegate_once(manager, "level_designer", "nudge l1's exit")
        opinion = result["verify"]["opinions"][0]
        assert opinion["ok"] is None and "demoted to ask" in opinion["reason"]
        assert judge.calls == [], "no headless spawn, no judge call"
        assert result["status"] == "ok"

    def test_a_judge_that_mumbles_is_neither_a_pass_nor_a_failure(self, pack: Path) -> None:
        good_exit = list(level_json(pack, "l1")["exit"])
        router = Router()
        router.push(
            "level_designer",
            [tool_use("apply_level_edit", level_id="l1", sparse_edits={"exit": good_exit})],
            text("Done."),
        )
        manager = manager_for(pack, FakeChatBackend(router))
        manager.vlm = FakeVLMBackend(lambda prompt, images: "looks fine to me!")
        result, _ = delegate_once(manager, "level_designer", "nudge l1's exit")
        opinion = result["verify"]["opinions"][0]
        assert opinion["ok"] is None and "JSON shape" in opinion["reason"]
        assert opinion["raw"] == "looks fine to me!"
        assert result["status"] == "ok"

    def test_a_specialist_that_already_spent_its_retry_gets_no_second_one(self, pack: Path) -> None:
        """The verify loop shares ``RUN_FAILURE_LIMIT`` with tool failures —
        one corrected retry per run, whatever spent it."""
        good_exit = list(level_json(pack, "l1")["exit"])
        router = Router()
        router.push(
            "level_designer",
            # A refused tool spends the run's one retry…
            [tool_use("update_row", type="enemy", id="nope", fields={"name": "x"})],
            # …then the write breaks the level, and no retry is left.
            [tool_use("apply_level_edit", level_id="l1", sparse_edits={"exit": [good_exit[0], 2]})],
            text("Done."),
        )
        manager = manager_for(pack, FakeChatBackend(router))
        result, _ = delegate_once(manager, "level_designer", "shuffle l1")
        body = json.loads(result["error"])
        assert result["status"] == "failed"
        assert body["error"] == "verify_failed" and body["retried"] is False
        assert "no corrected retry was left" in body["message"]
        designer_requests = [q for role, q in router.calls if role == "level_designer"]
        assert len(designer_requests) == 3, "no repair turn was started"
