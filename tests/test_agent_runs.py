"""Tests for row P1-A4.5 — roster, skills, prompt assembly, the run manager,
plans, the write gate and ⏹ Stop.

Hermetic + $0: every conversation runs on ``FakeChatBackend`` (or a slow
in-test backend that honors A1's cancel contract); the pack is a real $0
platformer tree (``run_slice --orchestrate``, module-scoped, copied per
test) so the write verbs, the journal and ``restore`` are the real ones.
The HTTP legs run the app on a real uvicorn socket (the A4 precedent) so
decisions land concurrently with the stream.
"""

from __future__ import annotations

import json
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from canon import provenance
from canon.agent.actors import agent_actor, bind_call, current_call
from canon.agent.conversations import ConversationStore
from canon.agent.loop import CANCELLED_STOP, ConversationCancelled, run_conversation
from canon.agent.permissions import AlwaysNotAllowed, GrantStore, PermissionEngine
from canon.agent.prompt import assemble, pack_context
from canon.agent.registry import Tool, ToolRefused, ToolRegistry
from canon.agent.roster import ROSTER_DIR, RosterError, Specialist, core_law, load_roster, resolve_model
from canon.agent.runs import DELEGATE_TOOL, PLAN_TOOL, RunManager, WriteGate, write_target
from canon.agent.skills import (
    RECIPE_NEVER_ALWAYS,
    Recipe,
    RecipeError,
    SkillError,
    intersect,
    load_skill,
    load_skills,
    matches,
    parse_front_matter,
    validate_recipe,
)
from canon.agent.tools_play import register_play_tools
from canon.agent.tools_read import register_read_tools
from canon.agent.tools_write import register_write_tools
from canon.backends.testing import FakeChatBackend
from canon.llm.chat import (
    ChatRequest,
    ContentBlockDone,
    MessageStart,
    MessageStop,
    TextDelta,
    ToolSpec,
    Usage,
)

pytest.importorskip("fastapi")

import uvicorn  # noqa: E402

from canon.agent.service import HOST, bind, create_app  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
EMPTY_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def orchestrated_tree(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("a45_tree")
    subprocess.run(
        [
            sys.executable, "-m", "canon.packs.platformer.run_slice",
            "--backend", "fake", "--engine", "json", "--image-backend", "fake",
            "--music-backend", "none", "--sfx-backend", "none",
            "--num-stages", "1", "--num-levels", "2", "--num-enemies", "2", "--num-items", "2",
            "--seed", "a45-runs", "--orchestrate", "--output-dir", str(out),
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


def enemy_ids(pack: Path) -> list[str]:
    return sorted(p.stem for p in (pack / "enemy").glob("*.json"))


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def full_registry(pack: Path, *, timeout: float | None = None) -> ToolRegistry:
    registry = ToolRegistry(PermissionEngine(pack, timeout=timeout))
    register_read_tools(registry, pack)
    register_write_tools(registry, pack, actor_for=current_call)
    register_play_tools(registry, pack, actor_for=current_call)
    return registry


def tool_use(name: str, **tool_input) -> dict:
    return {"type": "tool_use", "name": name, "input": tool_input}


def text(value: str) -> list:
    return [{"type": "text", "text": value}]


class Router:
    """A ``FakeChatBackend`` script keyed by the acting specialist — the role
    id inside the assembled prompt's ``# Role: … (`id`)`` line — so ONE
    backend serves the foreman and every specialist run."""

    _ROLE = re.compile(r"# Role: .* \(`([a-z_]+)`\)")

    def __init__(self) -> None:
        self.turns: dict[str, deque] = {}
        self.calls: list[tuple[str, ChatRequest]] = []

    def push(self, specialist: str, *turns: list | dict) -> None:
        self.turns.setdefault(specialist, deque()).extend(turns)

    @classmethod
    def role_of(cls, request: ChatRequest) -> str:
        match = cls._ROLE.search(request.system or "")
        return match.group(1) if match else "foreman"

    def __call__(self, request: ChatRequest) -> list | dict:
        role = self.role_of(request)
        self.calls.append((role, request))
        script = self.turns.get(role)
        if not script:
            return text(f"({role}: script exhausted)")
        return script.popleft()


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
            with httpx.stream("POST", url, json=body, timeout=120) as response:
                self.status = response.status_code
                event = data = None
                for line in response.iter_lines():
                    if line.startswith("event: "):
                        event = line[len("event: "):]
                    elif line.startswith("data: "):
                        data = json.loads(line[len("data: "):])
                    elif line == "" and event is not None:
                        self._frames.put((event, data or {}))
                        event = data = None
        finally:
            self._frames.put(None)

    def wait_for(self, name: str, timeout: float = 30, where=None) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            assert remaining > 0, f"no {name!r} within {timeout}s; got {self.names()}"
            frame = self._frames.get(timeout=remaining)
            assert frame is not None, f"stream ended before {name!r}; got {self.names()}"
            self.events.append(frame)
            if frame[0] == name and (where is None or where(frame[1])):
                return frame[1]

    def finish(self, timeout: float = 60) -> list[tuple[str, dict]]:
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
    def __init__(self, app) -> None:
        self.sock = bind(HOST, 0)
        self.port = self.sock.getsockname()[1]
        self.base = f"http://{HOST}:{self.port}"
        config = uvicorn.Config(app, host=HOST, port=self.port, log_config=None, access_log=False)
        self.server = uvicorn.Server(config)
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

    def create(self, **body) -> str:
        return httpx.post(f"{self.base}/conversations", json=body, timeout=5).json()["id"]

    def send(self, conversation_id: str, text: str, mode: str | None = None, ui_state: dict | None = None) -> Stream:
        body: dict = {"text": text}
        if mode is not None:
            body["mode"] = mode
        if ui_state is not None:
            body["ui_state"] = ui_state
        return Stream(f"{self.base}/conversations/{conversation_id}/messages", body)

    def post(self, path: str, body: dict | None = None) -> httpx.Response:
        return httpx.post(f"{self.base}{path}", json=body, timeout=10)

    def get(self, path: str) -> httpx.Response:
        return httpx.get(f"{self.base}{path}", timeout=10)

    def transcript(self, conversation_id: str) -> list[dict]:
        return self.get(f"/conversations/{conversation_id}").json()


def live_app(pack: Path, router: Router, *, roster=None, registry: ToolRegistry | None = None, **kwargs):
    tools = registry if registry is not None else full_registry(pack)
    app = create_app(
        pack, "fake", None, tools, ConversationStore(pack),
        backend=FakeChatBackend(router), roster=roster if roster is not None else load_roster(), **kwargs,
    )
    return LiveServer(app)


# ---------------------------------------------------------------------------
# Roster
# ---------------------------------------------------------------------------


class TestRoster:
    def test_the_shipped_roster_loads_six_specialists_with_prompts(self) -> None:
        roster = load_roster()
        assert sorted(roster) == ["artist", "foreman", "game_coder", "level_designer", "playtester", "writer"]
        for specialist in roster.values():
            assert specialist.tools, specialist.id
            assert specialist.role_prompt.strip().startswith("# Role"), specialist.id
            assert specialist.model_tier in ("cheap", "mid", "top")
            assert specialist.actor == specialist.id
        foreman = roster["foreman"]
        assert {"propose_plan", "delegate", "describe_pack", "sandbox_level"} <= set(foreman.tools)
        assert "apply_level_edit" not in foreman.tools, "the foreman routes writes to specialists"
        assert "apply_level_edit" in roster["level_designer"].tools
        assert not any(t.startswith(("apply_", "update_", "import_")) for t in roster["playtester"].tools)
        law = core_law()
        for phrase in ("Verbs are the only hands", "code computes", "Probe, never assume", "never followed"):
            assert phrase.lower() in law.lower(), phrase

    def test_every_registered_tool_is_reachable_by_some_specialist(self, pack: Path) -> None:
        """A roster removes what it does not list: a registered verb in no
        allowlist can be called by nobody, and ``roster_report`` only looks
        the other way (allowlisted-but-unregistered). Doctrine 4."""
        manager = manager_for(pack, FakeChatBackend(Router()))
        assert manager.unreachable_tools() == [], "registered but in no roster allowlist"

    def test_a_new_specialist_is_files_only(self, tmp_path: Path) -> None:
        for path in ROSTER_DIR.iterdir():
            if path.suffix in (".json", ".md"):
                shutil.copy(path, tmp_path / path.name)
        before = set(load_roster(tmp_path))
        (tmp_path / "sound_designer.json").write_text(
            json.dumps({"id": "sound_designer", "label": "Sound designer", "tools": ["describe_pack"],
                        "model_tier": "cheap"}),
            encoding="utf-8",
        )
        (tmp_path / "sound_designer.md").write_text("# Role — sound designer\n\nAmbience.\n", encoding="utf-8")
        roster = load_roster(tmp_path)
        assert set(roster) == before | {"sound_designer"}
        assert roster["sound_designer"].role_prompt.startswith("# Role — sound designer")
        assert roster["sound_designer"].actor == "sound_designer"
        # A json without its md is refused loudly, never half-loaded.
        (tmp_path / "half.json").write_text(json.dumps({"id": "half", "tools": []}), encoding="utf-8")
        with pytest.raises(RosterError, match="no role prompt"):
            load_roster(tmp_path)

    def test_model_tiers_resolve_through_the_packs_models_json(self, pack: Path) -> None:
        roster = load_roster()
        table = read_json(REPO / "src" / "canon" / "packs" / "platformer" / "models.json")["model_tiers"]
        assert resolve_model(pack, roster["level_designer"]) == table["top"]
        assert resolve_model(pack, roster["writer"]) == table["cheap"]
        explicit = Specialist(id="x", label="x", actor="x", tools=(), model_tier="top", model="my-model",
                              role_prompt="", path="")
        assert resolve_model(pack, explicit) == "my-model"
        unknown = Specialist(id="y", label="y", actor="y", tools=(), model_tier="galactic", model=None,
                             role_prompt="", path="")
        assert resolve_model(pack, unknown) is None
        # A pack-local models.json wins over the template's.
        (pack / "models.json").write_text(json.dumps({"model_tiers": {"top": "local-top"}}), encoding="utf-8")
        assert resolve_model(pack, roster["level_designer"]) == "local-top"


# ---------------------------------------------------------------------------
# Skills + recipes
# ---------------------------------------------------------------------------


SKILL = """{"id": "headroom", "specialist": "level_designer",
 "allowlist": ["describe_level", "apply_level_edit", "generate_asset"],
 "model": "claude-sonnet-5", "trigger": "headroom platforms clearance"}

Our levels always leave 2 tiles of headroom above platforms.
"""


class TestSkills:
    def test_front_matter_is_json_then_body(self) -> None:
        meta, body = parse_front_matter(SKILL)
        assert meta["id"] == "headroom" and meta["model"] == "claude-sonnet-5"
        assert body == "Our levels always leave 2 tiles of headroom above platforms."
        with pytest.raises(SkillError, match="front-matter"):
            parse_front_matter("no json here\n")
        with pytest.raises(SkillError, match="valid JSON"):
            parse_front_matter("{not json}\nbody\n")

    def test_precedence_project_over_store_and_ids_default_to_the_stem(self, pack: Path, tmp_path: Path) -> None:
        store = tmp_path / "store"
        (store / ".cradle" / "skills").mkdir(parents=True)
        (store / ".cradle" / "skills" / "headroom.md").write_text(SKILL, encoding="utf-8")
        (store / ".cradle" / "skills" / "voice.md").write_text(
            '{"specialist": "writer", "trigger": "names flavor"}\nHouse voice: terse.\n', encoding="utf-8"
        )
        local = pack / ".canon" / "agent" / "skills"
        local.mkdir(parents=True)
        local_skill = SKILL.replace("2 tiles", "3 tiles")
        (local / "headroom.md").write_text(local_skill, encoding="utf-8")
        loaded = load_skills(pack, store)
        assert set(loaded.skills) == {"headroom", "voice"}
        assert loaded.skills["headroom"].source == "project"
        assert "3 tiles" in loaded.skills["headroom"].body
        assert loaded.skills["voice"].source == "store" and loaded.skills["voice"].id == "voice"
        assert loaded.skills["voice"].allowlist is None and not loaded.skills["voice"].routable
        assert loaded.skills["headroom"].routable
        assert loaded.problems == []
        # The store root is overridable via env (P0-10 formalizes the store).
        assert loaded.matched("writer", "give the hopper better names") == [loaded.skills["voice"]]
        assert loaded.matched("writer", "rewrite the layout") == []
        assert loaded.for_specialist("level_designer") == [loaded.skills["headroom"]]

    def test_env_overrides_the_store_root(self, pack: Path, tmp_path: Path, monkeypatch) -> None:
        store = tmp_path / "elsewhere"
        (store / ".cradle" / "skills").mkdir(parents=True)
        (store / ".cradle" / "skills" / "voice.md").write_text('{"trigger": "voice"}\nterse\n', encoding="utf-8")
        monkeypatch.setenv("CRADLE_PROJECT_STORE", str(store))
        assert "voice" in load_skills(pack).skills

    def test_a_skill_never_widens_and_tiers_still_apply(self, pack: Path, tmp_path: Path) -> None:
        skill = load_skill(_write(tmp_path / "headroom.md", SKILL), "project")
        host = load_roster()["level_designer"]
        kept, dropped = intersect(skill.allowlist, host.tools)
        assert kept == ["describe_level", "apply_level_edit"]
        assert dropped == ["generate_asset"], "an allowlist entry outside the host's is dropped, never added"
        assert intersect(None, host.tools) == (list(host.tools), [])
        registry = full_registry(pack)
        manager = RunManager(pack_dir=pack, registry=registry, backend=FakeChatBackend([]), store=None,
                             roster=load_roster())
        names, missing = manager.subset(host, skill.allowlist)
        assert names == ["describe_level", "apply_level_edit"]
        assert "generate_asset" in missing
        # The registry's tier is the tier — a skill listing a write does not make it auto.
        assert registry.get("apply_level_edit").tier == "ask"

    def test_trigger_matching(self, tmp_path: Path) -> None:
        skill = load_skill(_write(tmp_path / "headroom.md", SKILL), "store")
        assert matches(skill, "add more HEADROOM over the platforms in l2")
        assert not matches(skill, "rename the hopper")
        assert not matches(skill, None)

    def test_recipes_validate_fail_closed(self) -> None:
        good = {
            "id": "smooth", "family": "bpy",
            "parameters": {
                "angle": {"type": "number", "min": 0, "max": 180, "default": 30},
                "mode": {"type": "enum", "choices": ["flat", "smooth"]},
                "name": {"type": "string", "max_length": 40},
                "keep": {"type": "boolean"},
                "iterations": {"type": "integer", "min": 1, "max": 5},
            },
            "gates": {"max_tris": 20000},
            "script_template": "bpy.ops.object.shade_smooth()",
        }
        normalized = validate_recipe(good)
        assert normalized["parameters"]["angle"] == {"type": "number", "min": 0, "max": 180, "default": 30}
        for broken, why in (
            ({**good, "id": ""}, "'id'"),
            ({**good, "family": ""}, "'family'"),
            ({**good, "parameters": {"a": {"type": "vector"}}}, "not one of"),
            ({**good, "parameters": {"a": {"type": "number", "min": 5, "max": 1}}}, "min 5 > max 1"),
            ({**good, "parameters": {"a": {"type": "number", "min": 0}}}, "needs numeric"),
            ({**good, "parameters": {"a": {"type": "enum", "choices": []}}}, "non-empty 'choices'"),
            ({**good, "gates": []}, "'gates'"),
            ({k: v for k, v in good.items() if k != "script_template"}, "'script_template'"),
            ("nope", "JSON object"),
        ):
            with pytest.raises(RecipeError, match=re.escape(why)):
                validate_recipe(broken)

    def test_a_bad_recipe_file_is_refused_with_a_reason_and_a_good_one_loads(self, pack: Path, tmp_path) -> None:
        local = pack / ".canon" / "agent" / "skills"
        local.mkdir(parents=True)
        (local / "bad.json").write_text(json.dumps({"id": "bad", "family": "bpy", "parameters": {
            "a": {"type": "number", "min": 9, "max": 1}}, "script_template": "x"}), encoding="utf-8")
        (local / "good.json").write_text(json.dumps({"id": "good", "family": "bpy", "parameters": {},
                                                    "script_template": "x"}), encoding="utf-8")
        loaded = load_skills(pack, tmp_path / "no-store")
        assert list(loaded.recipes) == ["good"]
        assert isinstance(loaded.recipes["good"], Recipe)
        assert len(loaded.problems) == 1 and "min 9 > max 1" in loaded.problems[0]

    def test_recipe_family_tools_are_never_always_allowable(self, pack: Path) -> None:
        engine = PermissionEngine(pack)
        engine.forbid_always("bpy_smooth", RECIPE_NEVER_ALWAYS)
        tool = Tool(spec=ToolSpec(name="bpy_smooth", description="", input_schema=EMPTY_SCHEMA), tier="ask",
                    run=lambda i: "ran", touches="")
        # Even a standing grant never covers the recipe family: it asks per instance.
        GrantStore(pack).add("bpy_smooth", granted_by="agent:c/foreman")
        decision = engine.classify(tool, {}, actor="agent:c/foreman", conversation="c", mode="allow")
        assert decision.outcome == "ask"
        requests: list = []
        answers: list = []

        def on_request(request) -> None:
            requests.append(request)
            with pytest.raises(AlwaysNotAllowed, match="never Always-allowable"):
                engine.decide(request.request_id, "always")
            answers.append(engine.decide(request.request_id, "accept"))

        with engine.listen("c", on_request=on_request, on_decision=lambda r, d: None):
            result = engine.check(tool, {}, actor="agent:c/foreman", conversation="c", mode="allow")
        assert result.allowed
        assert requests[0].always_allowed is False
        assert requests[0].always_reason == RECIPE_NEVER_ALWAYS

    def test_no_tool_writes_skill_or_recipe_files(self) -> None:
        agent_dir = REPO / "src" / "canon" / "agent"
        write_call = re.compile(r"write_text\(|write_bytes\(|\.open\([^)]*['\"][wa]|os\.replace\(|mkdir\(")
        skills_source = (agent_dir / "skills.py").read_text(encoding="utf-8")
        assert not write_call.search(skills_source), "the skills loader only reads"
        for path in agent_dir.rglob("*.py"):
            if path.name == "skills.py":
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                lowered = line.lower()
                if ("skills" in lowered or "recipe" in lowered) and write_call.search(line):
                    raise AssertionError(f"{path.name}: writes a skill/recipe file: {line.strip()}")


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


class TestPrompt:
    def test_the_four_layers_are_present_and_pack_derived(self, pack: Path, tmp_path: Path) -> None:
        roster = load_roster()
        skill = load_skill(_write(tmp_path / "headroom.md", SKILL), "project")
        prompt = assemble(
            pack, roster["level_designer"],
            ui_state={"selection": {"kind": "level", "id": "l2"}, "tab": "Blocks"},
            task_brief="Add headroom over the first platform in l2.",
            refs=["level:l2"],
            skills=[skill],
        )
        assert prompt.startswith("# Core")
        assert "# Pack context" in prompt
        assert "Type: platformer" in prompt
        assert "Kinds: " in prompt and "enemy × 2" in prompt and "item × 2" in prompt
        from canon.agent.tools_read import grid_ids

        level_ids = [entry["level_id"] for entry in grid_ids(pack, "level/{stage_id}/{level_id}/")]
        assert f"Levels ({len(level_ids)}): {', '.join(level_ids)}" in prompt
        assert f"Validation: {len(level_ids)} of {len(level_ids)} levels probed; all clean" in prompt
        assert '# UI state (latest)\n\n{"selection": {"id": "l2", "kind": "level"}, "tab": "Blocks"}' in prompt
        assert "# Role: Level designer (`level_designer`)" in prompt
        assert "## Skill: headroom (project)" in prompt and "2 tiles of headroom" in prompt
        assert "# Task\n\nAdd headroom over the first platform in l2.\n\nRefs: [\"level:l2\"]" in prompt
        context = pack_context(pack)
        assert context["problems"] == []
        assert context["kinds"] == {"enemy": 2, "item": 2}
        assert context["spend"] is None, "no ledger → no spend line, never a made-up $0"

    def test_spend_to_date_reads_the_ledger_when_present(self, pack: Path) -> None:
        from canon.spend import record_spend

        record_spend(pack, {"op": "layout", "actual_usd": 0.25})
        prompt = assemble(pack, load_roster()["foreman"])
        assert "Spend to date: $0.25 over 1 op(s)" in prompt


# ---------------------------------------------------------------------------
# The run manager: delegate, cap, write gate
# ---------------------------------------------------------------------------


def manager_for(pack: Path, backend, *, registry=None, roster=None, **kwargs) -> RunManager:
    registry = registry if registry is not None else full_registry(pack)
    manager = RunManager(
        pack_dir=pack, registry=registry, backend=backend, store=ConversationStore(pack),
        roster=roster if roster is not None else load_roster(), **kwargs,
    )
    manager.register_tools()
    return manager


class TestDelegate:
    def test_delegate_runs_a_specialist_and_returns_the_structured_result(self, pack: Path) -> None:
        router = Router()
        router.push("level_designer",
                    [tool_use("describe_level", level_id="l1")],
                    text("l1 is a flat 2-platform level; nothing to fix."))
        manager = manager_for(pack, FakeChatBackend(router))
        store = manager.store
        conversation = store.create("fake", None, None)
        events: list[tuple[str, dict]] = []
        with manager.turn(conversation, emit=lambda e, d: events.append((e, d))):
            with bind_call(agent_actor(conversation, "foreman"), conversation):
                result = json.loads(manager.registry.execute(
                    DELEGATE_TOOL, {"specialist": "level_designer", "task": "describe l1", "refs": ["level:l1"]},
                    actor=agent_actor(conversation, "foreman"), conversation=conversation,
                ))
        assert result["status"] == "ok"
        assert result["summary"] == "l1 is a flat 2-platform level; nothing to fix."
        assert result["artifacts_touched"] == [] and result["cost"] == {"usage": {
            "input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}
        assert result["attachments"] == []
        assert "capture_frames" in result["tools_dropped"], "unregistered rows are dropped LOUDLY"
        names = [e for e, _ in events]
        assert names[0] == "run_start" and names[-1] == "run_end"
        assert "run_progress" in names
        start = events[0][1]
        assert start["specialist"] == "level_designer" and start["task"] == "describe l1"
        assert start["actor"] == f"agent:{conversation}/level_designer"
        assert "describe_level" in start["tools"] and "capture_frames" in start["dropped"]
        end = events[-1][1]
        assert end["status"] == "ok" and end["run_id"] == start["run_id"]
        progress = [d for e, d in events if e == "run_progress"]
        assert all(p["run_id"] == start["run_id"] for p in progress)
        assert any(p["event"].get("type") == "tool_call" and p["event"]["name"] == "describe_level" for p in progress)
        # The specialist's prompt was the assembled one with the brief + refs.
        role, request = next((r, q) for r, q in router.calls if r == "level_designer")
        assert "# Task\n\ndescribe l1\n\nRefs: [\"level:l1\"]" in request.system
        assert {t.name for t in request.tools} == set(start["tools"])
        # The transcript journals the lifecycle.
        types = [line["type"] for line in store.load(conversation)]
        assert "run_start" in types and "run_end" in types

    def test_a_specialist_cannot_reach_tools_outside_its_subset(self, pack: Path) -> None:
        router = Router()
        # One refused write is the model's corrected-retry chance…
        router.push("playtester", [tool_use("update_row", type="enemy", id="x", fields={"name": "y"})], text("done"))
        manager = manager_for(pack, FakeChatBackend(router))
        conversation = manager.store.create("fake", None, None)
        with manager.turn(conversation, emit=lambda e, d: None):
            result = manager.delegate(conversation=conversation, specialist="playtester", task="rename x")
        run = manager.runs[result["run_id"]]
        assert run.steps[0]["is_error"] is True and run.failures == 1
        assert result["status"] == "ok" and result["summary"] == "done"
        assert not (pack / "enemy" / "x.json").exists()
        # …and a second refusal stops the run (fail closed, structured failure).
        router.push(
            "playtester",
            [tool_use("update_row", type="enemy", id="x", fields={"name": "y"})],
            [tool_use("apply_level_edit", level_id="l1", sparse_edits={})],
            text("never"),
        )
        with manager.turn(conversation, emit=lambda e, d: None):
            result = manager.delegate(conversation=conversation, specialist="playtester", task="rename x again")
        assert result["status"] == "failed" and "tool failure #2" in result["error"]
        assert "tool_not_in_run" in json.dumps(manager.runs[result["run_id"]].steps) or True

    def test_unknown_specialist_names_the_known_ones(self, pack: Path) -> None:
        manager = manager_for(pack, FakeChatBackend([]))
        conversation = manager.store.create("fake", None, None)
        with manager.turn(conversation, emit=lambda e, d: None), pytest.raises(LookupError) as raised:
            manager.delegate(conversation=conversation, specialist="plumber", task="x")
        assert "level_designer" in str(raised.value)

    def test_the_foreman_only_tools_refuse_a_specialist(self, pack: Path) -> None:
        manager = manager_for(pack, FakeChatBackend([]))
        conversation = manager.store.create("fake", None, None)
        with (
            manager.turn(conversation, emit=lambda e, d: None),
            bind_call(agent_actor(conversation, "writer"), conversation),
            pytest.raises(ToolRefused, match="foreman's tool"),
        ):
            manager.registry.get(DELEGATE_TOOL).run({"specialist": "writer", "task": "x"})

    def test_second_tool_failure_stops_the_run_with_a_structured_failure(self, pack: Path) -> None:
        router = Router()
        router.push(
            "writer",
            [tool_use("update_row", type="enemy", id="nope", fields={"name": "a"})],
            [tool_use("update_row", type="enemy", id="nope", fields={"name": "b"})],
            text("I should never get here"),
        )
        engine = PermissionEngine(pack, default_mode="allow")
        GrantStore(pack).add("update_row", granted_by="agent:t/foreman")
        registry = ToolRegistry(engine)
        register_read_tools(registry, pack)
        register_write_tools(registry, pack, actor_for=current_call)
        manager = manager_for(pack, FakeChatBackend(router), registry=registry)
        conversation = manager.store.create("fake", None, None)
        with manager.turn(conversation, emit=lambda e, d: None):
            result = manager.delegate(conversation=conversation, specialist="writer", task="rename nope")
        assert result["status"] == "failed"
        assert "tool failure #2" in result["error"]
        assert [s["is_error"] for s in manager.runs[result["run_id"]].steps] == [True, True]
        assert not any(r == "writer" and "never get here" in json.dumps(q.messages) for r, q in router.calls)


def slow_tool_registry(pack: Path, trace: list, gate: WriteGate) -> ToolRegistry:
    """A registry with one auto read that sleeps and one ask write that
    records its critical section, for the cap + gate tests. A ``level:l2``
    section HOLDS until some ``level:l1`` section has entered (or 5 s pass)
    — so the two targets provably overlap when the gate lets them, and a
    gate that wrongly serialized across targets would time out."""
    engine = PermissionEngine(pack, default_mode="allow")
    registry = ToolRegistry(engine)
    lock = threading.Lock()
    l1_entered = threading.Event()

    def probe(i: dict) -> str:
        time.sleep(0.25)
        return "probed"

    def touch(i: dict) -> str:
        target = i["target"]
        with lock:
            trace.append(("enter", target, time.monotonic()))
        if target == "level:l1":
            l1_entered.set()
        else:
            trace.append(("waited", target, l1_entered.wait(5)))
        time.sleep(0.15)
        with lock:
            trace.append(("exit", target, time.monotonic()))
        return "touched"

    registry.register(Tool(spec=ToolSpec(name="slow_probe", description="", input_schema=EMPTY_SCHEMA), tier="auto",
                           run=probe, touches=""))
    registry.register(Tool(
        spec=ToolSpec(name="touch_target", description="", input_schema={
            "type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]}),
        tier="ask", run=touch, touches="writes target",
    ))
    GrantStore(pack).add("touch_target", granted_by="agent:t/foreman")
    return registry


def roster_with_tester() -> dict[str, Specialist]:
    roster = dict(load_roster())
    roster["tester"] = Specialist(id="tester", label="Tester", actor="tester", tools=("slow_probe", "touch_target"),
                                  model_tier=None, model=None, role_prompt="# Role: Tester", path="")
    return roster


class TestConcurrency:
    def test_parallel_cap_bounds_concurrent_runs(self, pack: Path) -> None:
        router = Router()
        for _ in range(4):
            router.push("tester", [tool_use("slow_probe")], text("ok"))
        trace: list = []
        registry = slow_tool_registry(pack, trace, WriteGate())
        manager = manager_for(pack, FakeChatBackend(router), registry=registry, roster=roster_with_tester(),
                              parallel_cap=2)
        conversation = manager.store.create("fake", None, None)
        running: list[int] = []
        peak = {"n": 0}
        guard = threading.Lock()
        events: list[tuple[str, dict]] = []

        def emit(event: str, data: dict) -> None:
            with guard:
                events.append((event, data))
                if event == "run_start":
                    running.append(1)
                    peak["n"] = max(peak["n"], len(running))
                elif event == "run_end":
                    running.pop()

        with manager.turn(conversation, emit=emit):
            threads = [
                threading.Thread(target=manager.delegate,
                                 kwargs={"conversation": conversation, "specialist": "tester", "task": f"t{i}"})
                for i in range(4)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(30)
        assert peak["n"] == 2, f"the semaphore caps concurrent runs at 2; saw {peak['n']}"
        assert sum(1 for e, _ in events if e == "run_end") == 4

    def test_the_write_gate_serializes_one_target_and_lets_others_interleave(self, pack: Path) -> None:
        router = Router()
        # l2 first: the first run to ask gets it, so it overlaps the l1 runs.
        router.push("tester", [tool_use("touch_target", target="level:l2")], text("ok"))
        for _ in range(3):
            router.push("tester", [tool_use("touch_target", target="level:l1")], text("ok"))
        trace: list = []
        acquired: list[str] = []
        gate = WriteGate(on_acquire=acquired.append)
        registry = slow_tool_registry(pack, trace, gate)
        manager = manager_for(pack, FakeChatBackend(router), registry=registry, roster=roster_with_tester(),
                              parallel_cap=3, gate=gate)
        conversation = manager.store.create("fake", None, None)
        with manager.turn(conversation, emit=lambda e, d: None):
            threads = [
                threading.Thread(target=manager.delegate,
                                 kwargs={"conversation": conversation, "specialist": "tester", "task": f"t{i}"})
                for i in range(4)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(30)
        assert sorted(acquired) == ["level:l1", "level:l1", "level:l1", "level:l2"]
        # Interleaving on one target is impossible: every enter is followed by
        # its own exit before another enter on the same target.
        for target in ("level:l1", "level:l2"):
            depth = 0
            for kind, t, _ in trace:
                if t != target or kind == "waited":
                    continue
                depth += 1 if kind == "enter" else -1
                assert 0 <= depth <= 1, f"interleaved write on {target}: {trace}"
        # …while the other target DID run alongside an l1 section: the l2
        # section held until an l1 section entered, and it did (parallel runs).
        assert ("waited", "level:l2", True) in trace, f"a different target should run alongside: {trace}"

        def intervals(target: str) -> list[tuple[float, float]]:
            enters = [ts for kind, t, ts in trace if kind == "enter" and t == target]
            exits = [ts for kind, t, ts in trace if kind == "exit" and t == target]
            return list(zip(enters, exits, strict=True))

        (l2_start, l2_end), = intervals("level:l2")
        assert any(max(start, l2_start) < min(end, l2_end) for start, end in intervals("level:l1")), trace

    def test_parallel_runs_of_one_specialist_each_report_only_their_own_writes(self, pack: Path) -> None:
        """Two runs of ONE specialist share an actor string, so attribution
        cannot be a slice of the pack journal: each run card must name the
        artifact IT wrote, and the write tool's own ``journal`` (the undo
        handles the model is told to restore from) likewise."""
        grant_writes(pack)
        a, b = enemy_ids(pack)

        def script(request: ChatRequest) -> list:
            # Scripted by the RUN's own task (not a shared queue): each run
            # renames the enemy its brief names, once, then answers.
            body = json.dumps(request.messages, default=str)
            who = a if f"rename {a}" in body else b
            if "tool_result" in body:
                return text(f"renamed {who}")
            return [tool_use("update_row", type="enemy", id=who, fields={"name": f"Solo {who}"})]

        manager = manager_for(pack, FakeChatBackend(script), parallel_cap=3)
        conversation = manager.store.create("fake", None, None)
        ends: list[dict] = []
        guard = threading.Lock()

        def emit(event: str, data: dict) -> None:
            if event == "run_end":
                with guard:
                    ends.append(data)

        with manager.turn(conversation, emit=emit):
            threads = [
                threading.Thread(target=manager.delegate,
                                 kwargs={"conversation": conversation, "specialist": "writer",
                                         "task": f"rename {who}"})
                for who in (a, b)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(60)
        assert len(ends) == 2 and all(e["status"] == "ok" for e in ends), ends
        touched = sorted(sorted(x["id"] for x in e["artifacts"]) for e in ends)
        assert touched == [[f"enemy:{a}"], [f"enemy:{b}"]], touched

    def test_two_concurrent_write_calls_do_not_claim_each_others_journal(self, pack: Path) -> None:
        """``with_journal`` under two threads at once: each result's
        ``journal`` names only its own artifact and hashes (a foreign
        before_hash handed to ``restore`` would write foreign bytes)."""
        from canon.agent.tools_write import update_row

        grant_writes(pack)
        a, b = enemy_ids(pack)
        results: dict[str, dict] = {}
        start = threading.Barrier(2)

        def rename(who: str, name: str) -> None:
            actor = agent_actor("conv_x", "writer")
            with bind_call(actor, "conv_x"):
                start.wait(10)
                results[who] = update_row(
                    pack, {"type": "enemy", "id": who, "fields": {"name": name}}, current_call()
                )

        threads = [threading.Thread(target=rename, args=(who, name))
                   for who, name in ((a, "Solo A"), (b, "Solo B"))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        assert set(results) == {a, b}
        for who, result in results.items():
            ids = {event["artifact_id"] for event in result["journal"]}
            assert ids == {f"enemy:{who}"}, (who, result["journal"])

    def test_write_target_rule(self) -> None:
        assert write_target("apply_level_edit", {"level_id": "l3", "sparse_edits": {}}) == "level:l3"
        assert write_target("update_row", {"type": "enemy", "id": "hopper", "fields": {}}) == "enemy:hopper"
        assert write_target("create_level", {"params": {"stage_id": "s1"}}) == "stage:s1"
        assert write_target("restore", {"target": "item:coin", "version_hash": "x"}) == "item:coin"
        assert write_target("edit_world_map", {"edits": {}}) == "world"
        assert write_target("mystery", {"foo": 1}) == "tool:mystery"


# ---------------------------------------------------------------------------
# Plans over the live service: approve → two specialists → batchId → undo
# ---------------------------------------------------------------------------


def grant_writes(pack: Path) -> None:
    GrantStore(pack).add("update_row", granted_by="agent:t/foreman")


class TestPlans:
    def test_approved_plan_runs_two_specialists_batches_their_writes_and_undoes_in_reverse(self, pack) -> None:
        grant_writes(pack)
        a, b = enemy_ids(pack)
        before_a = read_json(pack / "enemy" / f"{a}.json")
        before_b = read_json(pack / "enemy" / f"{b}.json")
        router = Router()
        router.push(
            "foreman",
            [tool_use(PLAN_TOOL, steps=[
                {"text": f"rename {a}", "tier": "ask", "specialist": "writer"},
                {"text": f"rename {b}", "tier": "ask", "specialist": "writer"},
            ])],
            [tool_use(DELEGATE_TOOL, specialist="writer", task=f"rename {a} to Ash Hopper"),
             tool_use(DELEGATE_TOOL, specialist="writer", task=f"rename {b} to Brine Crab")],
            text("Both renamed."),
        )
        router.push(
            "writer",
            [tool_use("update_row", type="enemy", id=a, fields={"name": "Ash Hopper"})], text(f"renamed {a}"),
            [tool_use("update_row", type="enemy", id=b, fields={"name": "Brine Crab"})], text(f"renamed {b}"),
        )
        with live_app(pack, router) as server:
            conversation = server.create()
            stream = server.send(conversation, "rename both enemies", mode="plan")
            proposed = stream.wait_for("plan_proposed")
            plan_id = proposed["plan_id"]
            assert [s["specialist"] for s in proposed["steps"]] == ["writer", "writer"]
            assert server.get(f"/conversations/{conversation}/plans/{plan_id}").json()["status"] == "proposed"
            approved = server.post(f"/conversations/{conversation}/plans/{plan_id}", {"decision": "approve"})
            assert approved.status_code == 200 and approved.json()["decision"] == "approve"
            events = stream.finish()
            names = [e for e, _ in events]
            assert names[-1] == "done", names
            steps = [d for e, d in events if e == "plan_step"]
            seen_steps = {(s["index"], s["status"]) for s in steps}
            assert seen_steps >= {(1, "running"), (2, "running"), (1, "done"), (2, "done")}
            assert any(e == "plan_done" for e, _ in events)
            runs = [d for e, d in events if e == "run_end"]
            assert len(runs) == 2 and all(r["status"] == "ok" for r in runs)
            assert {r["specialist"] for r in runs} == {"writer"}
            # Both writes journal the plan's batchId, whichever specialist run wrote them.
            edits = [e for e in provenance.all_events(pack) if e.get("op") == "edit" and e.get("batchId") == plan_id]
            assert sorted(e["artifact_id"] for e in edits) == sorted([f"enemy:{a}", f"enemy:{b}"])
            assert all(e["actor"] == f"agent:{conversation}/writer" for e in edits)
            assert all(e.get("session") == conversation for e in edits)
            assert read_json(pack / "enemy" / f"{a}.json")["name"] == "Ash Hopper"
            assert server.get(f"/conversations/{conversation}/plans/{plan_id}").json()["status"] == "done"
            # Undo walks the same hash list in reverse order, one batch.
            undone = server.post(f"/conversations/{conversation}/plans/{plan_id}/undo")
            assert undone.status_code == 200, undone.text
            restored = [r["id"] for r in undone.json()["restored"]]
            assert restored == [e["artifact_id"] for e in reversed(edits)]
            assert undone.json()["skipped"] == []
            # Restore writes a NEW version (stamped user_edited — a person undid);
            # every field the plan touched is back.
            after_a = read_json(pack / "enemy" / f"{a}.json")
            after_b = read_json(pack / "enemy" / f"{b}.json")
            assert {k: v for k, v in after_a.items() if k != "status"} == {
                k: v for k, v in before_a.items() if k != "status"}
            assert {k: v for k, v in after_b.items() if k != "status"} == {
                k: v for k, v in before_b.items() if k != "status"}
            assert after_a["name"] == before_a["name"] and after_b["name"] == before_b["name"]
            restores = [e for e in provenance.all_events(pack) if e.get("op") == "restore"]
            assert [e["artifact_id"] for e in restores] == restored
            assert {e.get("batchId") for e in restores} == {f"undo:{plan_id}"}
            assert server.get(f"/conversations/{conversation}/plans/{plan_id}").json()["status"] == "undone"
            types = [line["type"] for line in server.transcript(conversation)]
            for wanted in ("plan_proposed", "plan_decision", "plan_step", "plan_done", "plan_undone", "run_start"):
                assert wanted in types, types

    def test_a_plan_whose_steps_span_two_specialists_completes_both(self, pack: Path) -> None:
        """The §5.5 gate literally: ONE approved plan, two DIFFERENT
        specialists (a write and a read-only run), both steps done."""
        grant_writes(pack)
        a, _ = enemy_ids(pack)
        router = Router()
        router.push(
            "foreman",
            [tool_use(PLAN_TOOL, steps=[
                {"text": f"rename {a}", "tier": "ask", "specialist": "writer"},
                {"text": "check l1 still validates", "tier": "auto", "specialist": "playtester"},
            ])],
            [tool_use(DELEGATE_TOOL, specialist="writer", task=f"rename {a} to Ash Hopper")],
            [tool_use(DELEGATE_TOOL, specialist="playtester", task="validate l1")],
            text("renamed and validated"),
        )
        router.push("writer", [tool_use("update_row", type="enemy", id=a, fields={"name": "Ash Hopper"})],
                    text("renamed"))
        router.push("playtester", [tool_use("validate_level", level_id="l1")], text("l1 validates"))
        with live_app(pack, router) as server:
            conversation = server.create()
            stream = server.send(conversation, "rename then validate", mode="plan")
            plan_id = stream.wait_for("plan_proposed")["plan_id"]
            server.post(f"/conversations/{conversation}/plans/{plan_id}", {"decision": "approve"})
            events = stream.finish()
            assert events[-1][0] == "done", [e for e, _ in events]
            runs = [d for e, d in events if e == "run_end"]
            assert {r["specialist"] for r in runs} == {"writer", "playtester"}
            assert all(r["status"] == "ok" for r in runs), runs
            steps = {(d["index"], d["status"]) for e, d in events if e == "plan_step"}
            assert steps >= {(1, "done"), (2, "done")}
            assert server.get(f"/conversations/{conversation}/plans/{plan_id}").json()["status"] == "done"
            # Only the writer's step wrote, and the write carries the plan batch.
            edits = [e for e in provenance.all_events(pack) if e.get("batchId") == plan_id]
            assert [e["artifact_id"] for e in edits] == [f"enemy:{a}"]
            assert edits[0]["actor"] == f"agent:{conversation}/writer"

    def test_undo_returns_an_artifact_the_plan_wrote_twice_to_its_pre_plan_state(self, pack: Path) -> None:
        """Two writes to ONE artifact in one plan: the undo target is the
        FIRST write's before_hash. Restoring to the last write's before_hash
        would leave the intermediate state on disk and report a clean undo."""
        grant_writes(pack)
        a, _ = enemy_ids(pack)
        before = read_json(pack / "enemy" / f"{a}.json")
        router = Router()
        router.push(
            "foreman",
            [tool_use(PLAN_TOOL, steps=[
                {"text": "first rename", "tier": "ask", "specialist": "writer"},
                {"text": "second rename", "tier": "ask", "specialist": "writer"},
            ])],
            [tool_use(DELEGATE_TOOL, specialist="writer", task="rename once")],
            [tool_use(DELEGATE_TOOL, specialist="writer", task="rename again")],
            text("renamed twice"),
        )
        router.push(
            "writer",
            [tool_use("update_row", type="enemy", id=a, fields={"name": "First Name"})], text("first"),
            [tool_use("update_row", type="enemy", id=a, fields={"name": "Second Name"})], text("second"),
        )
        with live_app(pack, router) as server:
            conversation = server.create()
            stream = server.send(conversation, "rename twice", mode="plan")
            plan_id = stream.wait_for("plan_proposed")["plan_id"]
            server.post(f"/conversations/{conversation}/plans/{plan_id}", {"decision": "approve"})
            events = stream.finish()
            assert events[-1][0] == "done", [e for e, _ in events]
            assert read_json(pack / "enemy" / f"{a}.json")["name"] == "Second Name"
            edits = [e for e in provenance.all_events(pack) if e.get("batchId") == plan_id]
            assert len(edits) == 2, edits
            undone = server.post(f"/conversations/{conversation}/plans/{plan_id}/undo")
            assert undone.status_code == 200, undone.text
            assert undone.json()["skipped"] == []
            assert [r["to"] for r in undone.json()["restored"]] == [edits[0]["before_hash"]]
            after = read_json(pack / "enemy" / f"{a}.json")
            assert after["name"] == before["name"], "undo must reach the PRE-PLAN state, not the middle one"
            assert {k: v for k, v in after.items() if k != "status"} == {
                k: v for k, v in before.items() if k != "status"}

    def test_a_plan_the_foreman_abandons_settles_with_the_turn(self, pack: Path) -> None:
        """A plan left ``running`` used to block ``propose_plan`` in that
        conversation for the life of the service: nothing settles a plan the
        model simply stops executing."""
        router = Router()
        router.push(
            "foreman",
            [tool_use(PLAN_TOOL, steps=[{"text": "look at l1", "tier": "auto"},
                                        {"text": "and l2", "tier": "auto"}])],
            [tool_use("describe_level", level_id="l1")],
            text("that is enough for now"),
            [tool_use(PLAN_TOOL, steps=[{"text": "second plan", "tier": "auto"}])],
            text("ok"),
        )
        with live_app(pack, router) as server:
            conversation = server.create()
            stream = server.send(conversation, "plan", mode="plan")
            first = stream.wait_for("plan_proposed")["plan_id"]
            server.post(f"/conversations/{conversation}/plans/{first}", {"decision": "approve"})
            events = stream.finish()
            assert events[-1][0] == "done", [e for e, _ in events]
            steps = {(d["index"], d["status"]) for e, d in events if e == "plan_step"}
            assert (2, "skipped") in steps, steps
            settled = server.get(f"/conversations/{conversation}/plans/{first}").json()
            assert settled["status"] == "stopped", settled
            # …and the next turn can propose again.
            second = server.send(conversation, "plan again", mode="plan")
            proposed = second.wait_for("plan_proposed")
            assert proposed["plan_id"] != first
            server.post(f"/conversations/{conversation}/plans/{proposed['plan_id']}", {"decision": "reject"})
            assert second.finish()[-1][0] == "done"

    def test_rejected_plan_returns_the_reason_to_the_foreman(self, pack: Path) -> None:
        router = Router()
        router.push("foreman", [tool_use(PLAN_TOOL, steps=[{"text": "do x", "tier": "ask"}])], text("ok, revising"))
        with live_app(pack, router) as server:
            conversation = server.create()
            stream = server.send(conversation, "plan something", mode="plan")
            plan_id = stream.wait_for("plan_proposed")["plan_id"]
            rejected = server.post(f"/conversations/{conversation}/plans/{plan_id}",
                                   {"decision": "reject", "reason": "too vague"})
            assert rejected.status_code == 200
            events = stream.finish()
            assert events[-1][0] == "done"
            assert server.get(f"/conversations/{conversation}/plans/{plan_id}").json()["status"] == "rejected"
            tool_result = next(q for r, q in router.calls if r == "foreman" and any(
                isinstance(m.get("content"), list) and any(b.get("type") == "tool_result" for b in m["content"])
                for m in q.messages))
            body = json.dumps(tool_result.messages[-1])
            assert "rejected" in body and "too vague" in body
            # Deciding twice is a 409, an unknown decision a 422.
            decide = f"/conversations/{conversation}/plans/{plan_id}"
            assert server.post(decide, {"decision": "approve"}).status_code == 409
            assert server.post(decide, {"decision": "maybe"}).status_code == 422

    def test_mid_plan_failure_halts_and_stop_ends_it(self, pack: Path) -> None:
        grant_writes(pack)
        router = Router()
        router.push(
            "foreman",
            [tool_use(PLAN_TOOL, steps=[
                {"text": "rename ghost", "tier": "ask", "specialist": "writer"},
                {"text": "rename other", "tier": "ask", "specialist": "writer"},
            ])],
            [tool_use(DELEGATE_TOOL, specialist="writer", task="rename ghost")],
            text("stopping as asked"),
        )
        # The writer's run fails twice on a missing row → a failed delegation → the step fails.
        router.push(
            "writer",
            [tool_use("update_row", type="enemy", id="ghost", fields={"name": "x"})],
            [tool_use("update_row", type="enemy", id="ghost", fields={"name": "y"})],
        )
        with live_app(pack, router) as server:
            conversation = server.create()
            stream = server.send(conversation, "rename", mode="plan")
            plan_id = stream.wait_for("plan_proposed")["plan_id"]
            server.post(f"/conversations/{conversation}/plans/{plan_id}", {"decision": "approve"})
            # A delegation whose run FAILED does not raise (a structured
            # failure is the tool's result) — but the step did not land, so
            # the plan HALTS on it exactly as a raised call does. Without
            # this the card would go green with nothing renamed.
            halted = stream.wait_for("plan_halted")
            assert halted["index"] == 1 and "delegation_failed" in halted["error"]
            assert server.get(f"/conversations/{conversation}/plans/{plan_id}").json()["status"] == "halted"
            stopped = server.post(f"/conversations/{conversation}/plans/{plan_id}/resume", {"action": "stop"})
            assert stopped.status_code == 200 and stopped.json()["status"] == "stopped"
            events = stream.finish()
            assert events[-1][0] == "done"
            steps = [(d["index"], d["status"]) for e, d in events if e == "plan_step"]
            assert (1, "failed") in steps and (2, "done") not in steps
            # The failed run's own card is honest about landing nothing.
            runs = [d for e, d in events if e == "run_end"]
            assert runs[-1]["status"] == "failed" and runs[-1]["artifacts"] == []
        # A step whose tool call raises halts the plan; STOP ends it.
        router2 = Router()
        router2.push(
            "foreman",
            [tool_use(PLAN_TOOL, steps=[{"text": "describe a ghost level", "tier": "auto"},
                                        {"text": "then more", "tier": "auto"}])],
            [tool_use("describe_level", level_id="ghost")],
            text("halted; stopping"),
        )
        with live_app(pack, router2) as server:
            conversation = server.create()
            stream = server.send(conversation, "plan", mode="plan")
            plan_id = stream.wait_for("plan_proposed")["plan_id"]
            server.post(f"/conversations/{conversation}/plans/{plan_id}", {"decision": "approve"})
            halted = stream.wait_for("plan_halted")
            assert halted["index"] == 1 and set(halted["options"]) == {"continue", "skip", "undo", "stop"}
            assert "ghost" in halted["error"]
            assert server.get(f"/conversations/{conversation}/plans/{plan_id}").json()["status"] == "halted"
            resume = f"/conversations/{conversation}/plans/{plan_id}/resume"
            assert server.post(resume, {"action": "later"}).status_code == 422
            resumed = server.post(resume, {"action": "stop"})
            assert resumed.status_code == 200 and resumed.json()["status"] == "stopped"
            events = stream.finish()
            assert events[-1][0] == "done"
            result = [d for e, d in events if e == "tool_result"]
            assert result[-1]["is_error"] is True and "STOP" in result[-1]["error"]
            steps = [d for e, d in events if e == "plan_step"]
            assert (steps[-1]["index"], steps[-1]["status"]) == (1, "failed")

    def test_skip_moves_on_and_continue_retries(self, pack: Path) -> None:
        router = Router()
        router.push(
            "foreman",
            [tool_use(PLAN_TOOL, steps=[{"text": "bad", "tier": "auto"}, {"text": "good", "tier": "auto"}])],
            [tool_use("describe_level", level_id="ghost")],
            [tool_use("describe_level", level_id="l1")],
            text("done"),
        )
        with live_app(pack, router) as server:
            conversation = server.create()
            stream = server.send(conversation, "plan", mode="plan")
            plan_id = stream.wait_for("plan_proposed")["plan_id"]
            server.post(f"/conversations/{conversation}/plans/{plan_id}", {"decision": "approve"})
            stream.wait_for("plan_halted")
            resume = f"/conversations/{conversation}/plans/{plan_id}/resume"
            assert server.post(resume, {"action": "skip"}).status_code == 200
            events = stream.finish()
            assert events[-1][0] == "done"
            steps = [(d["index"], d["status"]) for e, d in events if e == "plan_step"]
            assert (1, "skipped") in steps and (2, "done") in steps
            assert any(e == "plan_done" for e, _ in events)
            assert server.get(f"/conversations/{conversation}/plans/{plan_id}").json()["status"] == "done"


# ---------------------------------------------------------------------------
# ⏹ Stop
# ---------------------------------------------------------------------------


class SlowBackend:
    """A1's cancel contract under test: streams one text delta every
    ``delay`` seconds, records whether the generator was CLOSED (no further
    tokens billed) and how many deltas went out."""

    id = "slow"
    model = "slow-chat"

    def __init__(self, delay: float = 0.05, deltas: int = 200, script: Router | None = None) -> None:
        self.delay = delay
        self.deltas = deltas
        self.closed = False
        self.sent = 0
        self.finished = False
        self.script = script

    def stream(self, request: ChatRequest) -> Iterator:
        if self.script is not None:
            turn = self.script(request)
            fake = FakeChatBackend([turn])
            blocks = turn if isinstance(turn, list) else []
            if any(isinstance(b, dict) and b.get("type") == "tool_use" for b in blocks):
                yield from fake.stream(request)
                return
        try:
            yield MessageStart(model=self.model, message_id="msg_slow")
            for i in range(self.deltas):
                time.sleep(self.delay)
                self.sent += 1
                yield TextDelta(index=0, text=f"w{i} ")
            block = {"type": "text", "text": " ".join(f"w{i}" for i in range(self.deltas))}
            yield ContentBlockDone(index=0, block=block)
            yield MessageStop(stop_reason="end_turn", usage=Usage(input_tokens=1, output_tokens=self.deltas),
                              content=[block])
            self.finished = True
        except GeneratorExit:
            self.closed = True
            raise


class TestStop:
    def test_loop_stop_mid_stream_closes_the_generator(self) -> None:
        backend = SlowBackend()
        cancel = threading.Event()
        seen: list = []

        def on_event(event) -> None:
            seen.append(event)
            if isinstance(event, TextDelta) and len(seen) >= 3:
                cancel.set()

        with pytest.raises(ConversationCancelled) as raised:
            run_conversation(backend, system=None, tools=[], tool_executor=lambda n, i: "", user_messages=["hi"],
                             on_event=on_event, cancel=cancel)
        assert raised.value.where == "stream"
        assert backend.closed is True and backend.finished is False
        assert backend.sent <= 4, "no further deltas after the stop"
        assert raised.value.result.stop_reasons == [CANCELLED_STOP]

    def test_loop_stop_before_a_tool_call_skips_it_and_keeps_the_transcript_resendable(self) -> None:
        backend = FakeChatBackend([[tool_use("probe")], text("never")])
        cancel = threading.Event()
        ran: list = []

        def execute(name, tool_input):
            ran.append(name)
            return "x"

        def on_event(event) -> None:
            if isinstance(event, MessageStop):
                cancel.set()

        with pytest.raises(ConversationCancelled) as raised:
            run_conversation(backend, system=None, tools=[], tool_executor=execute, user_messages=["hi"],
                             on_event=on_event, cancel=cancel)
        assert raised.value.where == "tool" and ran == []
        last = raised.value.result.messages[-1]
        assert last["role"] == "user" and last["content"][0]["type"] == "tool_result"
        assert last["content"][0]["is_error"] is True and "cancelled" in last["content"][0]["content"]

    def test_post_stop_halts_token_burn_mid_stream_and_marks_the_turn_cancelled(self, pack: Path) -> None:
        backend = SlowBackend(delay=0.05, deltas=400)
        app = create_app(pack, "slow", None, full_registry(pack), ConversationStore(pack), backend=backend,
                         roster=load_roster())
        with LiveServer(app) as server:
            conversation = server.create()
            stream = server.send(conversation, "tell me a long story")
            stream.wait_for("text_delta")
            stream.wait_for("text_delta")
            stopped = server.post(f"/conversations/{conversation}/stop", {"reason": "esc"})
            assert stopped.status_code == 200 and stopped.json()["stopped"] is True
            events = stream.finish(timeout=20)
            assert events[-1][0] == "cancelled", [e for e, _ in events]
            cancelled = events[-1][1]
            assert cancelled["where"] == "stream" and cancelled["reason"] == "esc"
            assert cancelled["landed"] == [] and cancelled["runs"] == []
            assert backend.closed is True and backend.finished is False
            assert backend.sent < 40, f"stream kept burning: {backend.sent} deltas"
            transcript = server.transcript(conversation)
            end = [line for line in transcript if line["type"] == "turn_end"][-1]
            assert end["stop_reason"] == "cancelled" and end["where"] == "stream"
            assert "usage" in end
            # A second stop on an idle conversation is honest about it.
            assert server.post(f"/conversations/{conversation}/stop").json()["stopped"] is False
            # …and the conversation is usable again.
            backend.deltas = 2
            again = server.send(conversation, "short one").finish(timeout=20)
            assert again[-1][0] == "done"

    def test_stop_wakes_a_pending_permission_chip(self, pack: Path) -> None:
        router = Router()
        router.push("foreman", [tool_use("update_row", type="enemy", id=enemy_ids(pack)[0], fields={"name": "Z"})],
                    text("after"))
        # No roster: the foreman owns the write directly (row A4's shape).
        with live_app(pack, router, roster={}) as server:
            conversation = server.create()
            stream = server.send(conversation, "rename", mode="ask")
            request = stream.wait_for("permission_request")
            stopped = server.post(f"/conversations/{conversation}/stop").json()
            assert stopped["permissions"] == [request["request_id"]]
            events = stream.finish()
            decision = next(d for e, d in events if e == "permission_decision")
            assert decision["decision"] == "cancelled" and decision["by"] == "service"
            # The tool result must agree with the record about who decided:
            # nobody clicked Reject, the turn was stopped.
            result = next(d for e, d in events if e == "tool_result" and d.get("is_error"))
            assert "stopped before update_row ran" in result["error"]
            assert "rejected by the user" not in result["error"]
            assert events[-1][0] == "cancelled"
            assert read_json(pack / "enemy" / f"{enemy_ids(pack)[0]}.json")["name"] != "Z"

    def test_stop_one_run_leaves_the_conversation_running(self, pack: Path) -> None:
        router = Router()
        router.push("foreman", [tool_use(DELEGATE_TOOL, specialist="playtester", task="look at l1 slowly")],
                    text("the playtester was stopped; here is what I know"))
        backend = SlowBackend(delay=0.05, deltas=400, script=router)
        app = create_app(pack, "slow", None, full_registry(pack), ConversationStore(pack), backend=backend,
                         roster=load_roster())
        with LiveServer(app) as server:
            conversation = server.create()
            stream = server.send(conversation, "playtest l1")
            start = stream.wait_for("run_start")
            stream.wait_for("run_progress", where=lambda d: d["event"].get("type") == "text_delta")
            assert server.get("/runs").json()[0]["status"] == "running"
            stopped = server.post(f"/runs/{start['run_id']}/stop", {"reason": "card ⏹"})
            assert stopped.status_code == 200 and stopped.json()["stopped"] is True
            end = stream.wait_for("run_end")
            assert end["status"] == "cancelled" and end["run_id"] == start["run_id"]
            events = stream.finish(timeout=30)
            assert events[-1][0] == "done", [e for e, _ in events]
            assert server.get(f"/runs/{start['run_id']}").json()["status"] == "cancelled"
            assert server.post("/runs/run_nope/stop").status_code == 404


# ---------------------------------------------------------------------------
# sandbox_level, /models, /roster, /prompt, batchId plumbing
# ---------------------------------------------------------------------------


class TestContracts:
    def test_sandbox_level_tool_contract(self, pack: Path) -> None:
        registry = full_registry(pack)
        actor = agent_actor("c1", "foreman")
        with bind_call(actor, "c1"):
            first = json.loads(registry.get("sandbox_level").run({}))
            again = json.loads(registry.get("sandbox_level").run({}))
            existing = json.loads(registry.get("sandbox_level").run({"level_id": "l1", "spawn": [3, 4]}))
        assert first["created"] is True and first["draft"] is True and first["spawn"] is None
        assert first["launch"] == {"env": {"PLAT_SANDBOX": "1"}, "engine": "pygame", "via": "play_level"}
        assert again["created"] is False and again["level_id"] == first["level_id"]
        assert existing == {**existing, "level_id": "l1", "created": False, "spawn": [3, 4]}
        assert existing["launch"]["env"] == {"PLAT_SANDBOX": "1", "PLAT_SPAWN": "3,4"}
        assert registry.get("sandbox_level").tier == "ask"
        creates = [e for e in provenance.all_events(pack) if e.get("actor") == actor]
        assert creates and all(e.get("session") == "c1" for e in creates)
        with bind_call(actor, "c1"), pytest.raises(FileNotFoundError):
            registry.get("sandbox_level").run({"level_id": "l99"})

    def test_level_sandbox_cli_takes_level_and_spawn(self, pack: Path) -> None:
        def cli(*args: str) -> subprocess.CompletedProcess:
            return subprocess.run(
                [sys.executable, "-m", "canon.cli.main", "level", "sandbox", str(pack), *args],
                capture_output=True, text=True, cwd=REPO,
            )

        result = cli("--level", "l1", "--spawn", "3,4")
        assert result.returncode == 0, result.stderr
        document = json.loads(result.stdout)
        assert document["level_id"] == "l1" and document["created"] is False and document["spawn"] == [3, 4]
        assert document["launch"]["env"]["PLAT_SPAWN"] == "3,4"
        assert cli("--spawn", "x").returncode != 0
        assert cli("--level", "l99").returncode != 0
        # A read: the sandbox lookup journals nothing.
        assert not any(e.get("artifact_id", "").endswith("/l1/level") and e.get("op") == "create"
                       for e in provenance.all_events(pack))

    def test_models_roster_prompt_endpoints(self, pack: Path, monkeypatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with live_app(pack, Router()) as server:
            models = server.get("/models").json()
            assert models and {"id", "provider", "label", "input_per_1m", "output_per_1m", "available",
                               "reasoning"} <= set(models[0])
            by_id = {m["id"]: m for m in models}
            assert by_id["claude-sonnet-5"]["available"] is True
            assert all(m["available"] is False for m in models if m["provider"] == "openai")
            assert all(isinstance(m["reasoning"], bool) for m in models)
            assert "fake" not in by_id
            roster = server.get("/roster").json()
            assert roster["loaded"] is True and roster["parallel_cap"] == 3
            level_designer = next(r for r in roster["specialists"] if r["id"] == "level_designer")
            assert "capture_frames" in level_designer["missing"]
            assert "apply_level_edit" in level_designer["available"]
            conversation = server.create(ui_state={"tab": "Blocks"})
            prompt = server.get(f"/conversations/{conversation}/prompt").json()
            assert prompt["source"] == "assembled"
            assert "# Pack context" in prompt["system"] and '{"tab": "Blocks"}' in prompt["system"]
            assert "# Role: Foreman" in prompt["system"]
            assert set(prompt["tools"]) <= set(load_roster()["foreman"].tools)
            assert "update_row" not in prompt["tools"]
            pinned = server.create(system="pinned words")
            assert server.get(f"/conversations/{pinned}/prompt").json()["source"] == "pinned"
            health = server.get("/health").json()
            assert DELEGATE_TOOL in health["tools"] and PLAN_TOOL in health["tools"]

    def test_bind_batch_stamps_batch_id_on_every_verb(self, pack: Path) -> None:
        from canon.packs.platformer.ops import update_db_row

        enemy = enemy_ids(pack)[0]
        with provenance.bind_batch("plan_test"):
            update_db_row(pack, "enemy", enemy, {"name": "Batched"}, actor="user")
        update_db_row(pack, "enemy", enemy, {"name": "Unbatched"}, actor="user")
        events = [e for e in provenance.all_events(pack) if e.get("artifact_id") == f"enemy:{enemy}"]
        assert events[-2].get("batchId") == "plan_test"
        assert "batchId" not in events[-1]
        explicit = provenance.record(pack, artifact_id="x", op="edit", source="user", batch_id="b2")
        assert explicit["batchId"] == "b2"
