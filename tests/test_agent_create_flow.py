"""Row P1-A9 — the create-flow gate: an ice world by conversation, $0.

The row's gate is one sentence: *an ice-world-by-conversation end to end on
FREE backends*. This file is that gate, plus the four promises the start page
makes around it (Phase 1 §2.4; agent-panel README §11):

1. the scripted conversation creates a real project on free backends, and the
   tree it leaves **exists, resolves and opens** — never a canned tool result;
2. it asks **at most two** clarifying questions before it answers with a plan;
3. an all-fake/none create is **ask**-tier showing "$0" — it never raises the
   accent spend card (doctrine 3 / master §8 A-5);
4. a create that dies mid-run **keeps the folder** it wrote before spending,
   and says so — the "you can stop at any step and keep what exists" promise
   (A4.5's cancel contract, stated on the canon side of it);
5. what is creatable comes from ``canon pack templates``, so an unknown
   template is refused BY NAME (doctrine 4) and a third template needs no
   change to the tool.

Doctrine 3 binds the file itself: every backend here is ``fake``/``none``,
``CRADLE_PROJECTS_DIR`` is redirected at a tmp_path so no test ever writes
into the user's real project store, and nothing reaches a provider.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from canon.agent.actors import CallContext
from canon.agent.eval import run_scripted
from canon.agent.evals import (
    CREATE_CONVERSATION,
    FREE_BACKENDS,
    ICE_WORLD_INPUT,
    MAX_CLARIFYING_QUESTIONS,
    conversation,
    create_ice_world,
)
from canon.agent.permissions import PermissionEngine
from canon.agent.registry import ToolRegistry
from canon.agent.tools_paid import (
    FREE_TIER,
    PAID_TIER,
    PROJECT_STORE_ENV,
    creatable_templates,
    create_argv,
    effective_counts,
    estimate_payload,
    paid_tier_for,
    project_store_root,
    register_paid_tools,
    slugify,
    unique_pack_dir,
)
from canon.backends.testing import FakeChatBackend
from canon.packs import PACKS, resolve_pack
from canon.spend import read_spend

CONVERSATION = "wick"
ACTOR = "agent:wick/foreman"


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway project store. Redirecting the env var is the same escape
    hatch cradle's Rust command honours, so the test drives the shipped rule
    rather than a test-only parameter."""
    root = tmp_path / "CradleProjects"
    monkeypatch.setenv(PROJECT_STORE_ENV, str(root))
    return root


def _call() -> CallContext:
    return CallContext(actor=ACTOR, conversation=CONVERSATION)


# ---------------------------------------------------------------------------
# 1. The gate
# ---------------------------------------------------------------------------


class TestIceWorldByConversation:
    def test_the_scripted_conversation_creates_a_world_that_resolves_and_opens(
        self, store: Path
    ) -> None:
        """The gate. Two user turns, one create, a tree on disk that
        ``resolve_pack`` recognises — which is what "opens editable" means to
        every cradle surface (they all resolve first)."""
        conv = conversation(CREATE_CONVERSATION)
        result = run_scripted(conv, FakeChatBackend(conv.fake_turns))
        assert result.failures == []
        assert "create_project" in result.tool_calls

        created = create_ice_world({**ICE_WORLD_INPUT, "parent_dir": str(store)})
        pack = Path(created["pack_dir"])
        assert pack.is_dir(), "the create must leave a tree on disk"
        assert (pack / "manifest.json").is_file()
        # …and it is a pack every verb and every cradle surface can open.
        assert resolve_pack(pack).pack_type == "platformer"
        # P0-10's registry stamp rode along, so the world is editable on day 1.
        assert (pack / ".canon" / "registry.json").is_file()
        assert created["engines"], "the create stamps the template's engines-block entry"

    def test_the_create_is_free_and_the_ledger_says_so(self, store: Path) -> None:
        """Doctrine 3: a fake/none selection bills nothing, and the ledger the
        create writes lands in the pack it CREATED (never the open one)."""
        created = create_ice_world({**ICE_WORLD_INPUT, "parent_dir": str(store)})
        pack = Path(created["pack_dir"])
        rows = read_spend(pack)
        assert rows, "the create records its own run in the created pack"
        row = rows[-1]
        assert row["scope"] == "create_project"
        assert row["identity"] == ACTOR and row["session"] == CONVERSATION
        assert float(row.get("actual_usd") or 0.0) == 0.0

    def test_at_most_two_clarifying_questions_before_the_plan(self) -> None:
        """Phase 1 §2.4 / README §11: *at most two* clarifying questions, then
        the numbered plan. The script is allowed to ask them in one turn or
        two — what is capped is the number of QUESTIONS the user must answer
        before anything is proposed."""
        conv = conversation(CREATE_CONVERSATION)
        before_plan: list[str] = []
        for turn in conv.fake_turns:
            blocks = turn if isinstance(turn, list) else turn.get("content", [])
            if any(b.get("type") == "tool_use" for b in blocks):
                break
            before_plan += [b.get("text", "") for b in blocks if b.get("type") == "text"]
        questions = sum(text.count("?") for text in before_plan)
        assert 0 < questions <= MAX_CLARIFYING_QUESTIONS, (
            f"the start page asks at most {MAX_CLARIFYING_QUESTIONS} questions "
            f"before proposing; this script asks {questions}"
        )
        # And the user only ever answers once — a second round would mean a
        # third user turn before the create.
        assert len(conv.user_messages) == 2


# ---------------------------------------------------------------------------
# 2. Free never spend-confirms
# ---------------------------------------------------------------------------


class TestTheZeroDollarPath:
    def test_a_free_create_is_ask_tier_and_a_paid_one_is_not(self) -> None:
        resolve = paid_tier_for("create_project")
        assert resolve({"name": "Coldhearth", **FREE_BACKENDS}) == FREE_TIER
        # …including the bare call: the body's own defaults ARE free, so
        # reading "no backend named" as paid would raise a spend card for a
        # run that bills nothing.
        assert resolve({"name": "Coldhearth"}) == FREE_TIER
        assert resolve({"name": "Coldhearth", "image_backend": "fal"}) == PAID_TIER

    def test_the_free_estimate_is_zero_and_still_carries_its_counts(self) -> None:
        """"$0" on an ordinary chip is not the same as "no estimate": the card
        still names the unit and the count it priced."""
        payload = estimate_payload(Path("."), "create_project", {**ICE_WORLD_INPUT})
        assert payload is not None
        assert payload["low"] == 0.0 and payload["high"] == 0.0
        assert payload["unitLabel"] == "a whole project"
        assert payload["unitCount"] > 0

    def test_the_estimate_follows_the_CHOSEN_template_not_the_open_pack(self) -> None:
        """The plan card quotes the money for the world about to be made — a
        dungeon proposed from a platformer session prices as a dungeon."""
        platformer = estimate_payload(
            Path("."), "create_project", {"name": "P", "template": "platformer", "llm_backend": "anthropic"}
        )
        dungeon = estimate_payload(
            Path("."), "create_project", {"name": "D", "template": "dungeon", "llm_backend": "anthropic"}
        )
        assert platformer is not None and dungeon is not None
        assert platformer["unitCount"] != dungeon["unitCount"], (
            "two templates priced identically means the open pack, not the "
            "chosen template, decided the estimate"
        )

    def test_the_permission_gate_agrees_with_the_tier(self, tmp_path: Path) -> None:
        registry = ToolRegistry(PermissionEngine(tmp_path, default_mode="allow"))
        register_paid_tools(registry, tmp_path, actor_for=_call)
        engine = registry.permissions
        tool = registry.get("create_project")
        free = engine.classify(
            tool, {"name": "Coldhearth", **FREE_BACKENDS},
            actor=ACTOR, conversation=CONVERSATION, mode="allow",
        )
        assert free.outcome == "ask" and "ask-tier" in free.reason
        paid = engine.classify(
            tool, {"name": "Coldhearth", **FREE_BACKENDS, "llm_backend": "anthropic"},
            actor=ACTOR, conversation=CONVERSATION, mode="allow",
        )
        assert paid.outcome == "ask" and "paid-tier" in paid.reason


# ---------------------------------------------------------------------------
# 3. Templates are data; the destination is the project store
# ---------------------------------------------------------------------------


class TestWhatIsCreatable:
    def test_creatable_templates_is_pack_templates(self) -> None:
        assert [t["id"] for t in creatable_templates()] == list(PACKS)

    def test_an_unknown_template_is_refused_by_name(self, store: Path) -> None:
        with pytest.raises(ValueError) as excinfo:
            create_ice_world({**ICE_WORLD_INPUT, "template": "roguelike", "parent_dir": str(store)})
        message = str(excinfo.value)
        assert "roguelike" in message
        for installed in PACKS:
            assert installed in message, "the refusal must list what IS installed (doctrine 4)"

    def test_counts_are_the_template_s_vocabulary_with_defaults_underneath(self) -> None:
        dungeon = effective_counts({"template": "dungeon", "counts": {"rooms": 9}})
        assert dungeon["rooms"] == 9
        assert dungeon["npc"] == PACKS["dungeon"].counts["npc"], "unset counts keep the template's default"
        platformer = effective_counts({"template": "platformer", "counts": {}})
        assert platformer == PACKS["platformer"].counts

    def test_the_argv_is_canon_world_new_with_the_counts_by_name(self) -> None:
        """One create pipeline: the tool spawns the SAME verb cradle's
        JobQueue command spawns, with the count keys sent by name."""
        argv = create_argv(Path("/tmp/x"), {**ICE_WORLD_INPUT}, actor=ACTOR)
        assert argv[1:6] == ["-m", "canon.cli.main", "world", "new", "/tmp/x"]
        assert "--actor" in argv and argv[argv.index("--actor") + 1] == ACTOR
        assert argv[argv.index("--stages") + 1] == "1"
        assert argv[argv.index("--llm-backend") + 1] == "fake"
        # Q6 made --orchestrate the default; passing it explicitly is exactly
        # what P0-10 warns about on a DAG-less template.
        assert "--orchestrate" not in argv and "--no-orchestrate" not in argv

    def test_the_store_is_the_env_var_then_home(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(PROJECT_STORE_ENV, "/tmp/a9-store")
        assert project_store_root() == Path("/tmp/a9-store")
        monkeypatch.setenv(PROJECT_STORE_ENV, "")
        assert project_store_root() == Path.home() / "CradleProjects"

    def test_a_second_world_of_the_same_name_uniquifies(self, tmp_path: Path) -> None:
        assert slugify("Ice World!") == "ice_world"
        first = unique_pack_dir(tmp_path, "ice_world")
        first.mkdir()
        (first / "manifest.json").write_text("{}", encoding="utf-8")
        assert unique_pack_dir(tmp_path, "ice_world").name == "ice_world_2"


# ---------------------------------------------------------------------------
# 4. Stop mid-create keeps what exists
# ---------------------------------------------------------------------------


class TestStopKeepsWhatExists:
    def test_the_folder_exists_before_anything_is_spent(self, store: Path, monkeypatch) -> None:
        """The start page's own footnote: *a folder is written to disk before
        anything is spent — you can stop at any step and keep what exists.*
        Proved by killing the runner: the directory is still there afterwards
        and the failure names it."""
        import subprocess

        from canon.agent import tools_paid

        seen: dict = {}

        def die(argv, **kwargs):
            # The folder must already exist at the moment the run starts —
            # this is the assertion the promise rests on.
            out = Path(argv[argv.index("new") + 1])
            seen["existed"] = out.is_dir()
            raise subprocess.CalledProcessError(1, argv, output=b"", stderr=b"stopped")

        monkeypatch.setattr(subprocess, "run", die)
        with pytest.raises(RuntimeError) as excinfo:
            tools_paid._create_project(store, {**ICE_WORLD_INPUT}, _call())
        assert seen["existed"] is True
        kept = store / "coldhearth"
        assert kept.is_dir(), "a stopped create keeps the folder it already wrote"
        assert str(kept) in str(excinfo.value), "and says what was kept, by path"
        assert "kept" in str(excinfo.value)

    def test_a_stopped_create_never_claims_a_pack(self, store: Path, monkeypatch) -> None:
        """Honest reporting: the kept folder is an EMPTY shell, not a project
        — ``resolve_pack`` refuses it, so no surface lists it as a world."""
        import subprocess

        from canon.agent import tools_paid
        from canon.packs import PackTypeError

        monkeypatch.setattr(
            subprocess, "run",
            lambda argv, **kw: (_ for _ in ()).throw(subprocess.CalledProcessError(1, argv, b"", b"stopped")),
        )
        with pytest.raises(RuntimeError):
            tools_paid._create_project(store, {**ICE_WORLD_INPUT}, _call())
        with pytest.raises((PackTypeError, FileNotFoundError, ValueError)):
            resolve_pack(store / "coldhearth")


# ---------------------------------------------------------------------------
# 5. The registered tool is the one the conversation drives
# ---------------------------------------------------------------------------


def test_the_foreman_may_call_create_project() -> None:
    """The tool is only real if the agent that drives the start page can reach
    it — a registered verb on nobody's allowlist is unreachable (the run
    manager logs exactly that)."""
    from canon.agent.roster import load_roster

    assert "create_project" in load_roster()["foreman"].tools


def test_the_tool_description_names_the_installed_templates() -> None:
    """"It reads ``pack templates`` for what is creatable" — the model is told
    what exists by the registry, not by a list in the source."""
    from canon.agent.tools_paid import paid_tool_specs

    spec = next(s for s in paid_tool_specs() if s.name == "create_project")
    for entry in creatable_templates():
        assert entry["id"] in spec.description
    assert "counts" in spec.input_schema["properties"]
    assert json.dumps(spec.input_schema)  # JSON-able, as every spec must be
