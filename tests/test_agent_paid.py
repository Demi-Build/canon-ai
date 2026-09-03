"""Row P1-A6 — the $-tier tools, their estimates and their gate.

Four things this row promises and this file holds:

1. a paid tool ASKS, and the request carries an ``estimate`` so the chip can
   render ``Accept · spend up to $X`` before the body runs;
2. ``"always"`` is refused for a paid request, in every mode;
3. a $0 all-fake selection is **ask**-tier, not paid — "free never
   spend-confirms" (doctrine 3 / master §8 A-5) — while still asking;
4. the cost block reaches the journal AND the derived spend row, and that row
   carries ``journal_ref`` so no reconciler counts it twice (P.8.7).

Doctrine 3 also binds the tests themselves: every backend here is ``fake`` or
``none``, and the paid classifications are exercised through the tier resolver
rather than by spending anything.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from canon.agent.actors import agent_actor, bind_call
from canon.agent.permissions import AlwaysNotAllowed, PermissionEngine, PermissionRequest
from canon.agent.registry import ToolRefused, ToolRegistry
from canon.agent.tools_paid import (
    PAID_TIER,
    PAID_TOOL_NAMES,
    estimate_payload,
    paid_tier_for,
    register_paid_tools,
    selected_backends,
    spends_money,
)
from canon.provenance import all_events, cost_cents, read_events, summarize_events
from canon.spend import read_spend

REPO = Path(__file__).resolve().parents[1]
CONVERSATION = "mason"
ACTOR = agent_actor(CONVERSATION, "artist")

#: Every category off: the $0 preview selection Phase 1 §4 calls ask-tier.
ALL_FAKE = {"llm_backend": "fake", "image_backend": "fake", "music_backend": "none",
            "sfx_backend": "none", "vlm_backend": "none"}


@pytest.fixture(scope="module")
def built_pack(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("a6_paid")
    subprocess.run(
        [
            sys.executable, "-m", "canon.packs.platformer.run_slice",
            "--backend", "fake", "--engine", "json", "--image-backend", "fake",
            "--music-backend", "none", "--sfx-backend", "none",
            "--num-stages", "1", "--num-levels", "1", "--num-enemies", "2",
            "--output-dir", str(out),
        ],
        check=True, capture_output=True, cwd=REPO,
    )
    return out


@pytest.fixture
def pack(built_pack: Path, tmp_path: Path) -> Path:
    copy = tmp_path / "pack"
    shutil.copytree(built_pack, copy)
    return copy


@pytest.fixture
def registry(pack: Path) -> ToolRegistry:
    reg = ToolRegistry(PermissionEngine(pack, default_mode="allow"))
    names = register_paid_tools(reg, pack, actor_for=lambda: _call())
    assert names == list(PAID_TOOL_NAMES)
    return reg


def _call():
    from canon.agent.actors import CallContext

    return CallContext(actor=ACTOR, conversation=CONVERSATION)


class _Answer:
    """A listener that answers the first request it sees, and keeps it."""

    def __init__(self, engine: PermissionEngine, decision: str, reason: str | None = None) -> None:
        self.engine, self.decision, self.reason = engine, decision, reason
        self.seen: list[PermissionRequest] = []
        self.error: Exception | None = None

    def on_request(self, request: PermissionRequest) -> None:
        self.seen.append(request)
        threading.Thread(target=self._answer, args=(request,), daemon=True).start()

    def _answer(self, request: PermissionRequest) -> None:
        try:
            self.engine.decide(request.request_id, self.decision, self.reason)
        except Exception as exc:  # noqa: BLE001 — surfaced to the test
            self.error = exc
            self.engine.decide(request.request_id, "reject", "always was refused")

    def on_decision(self, request: PermissionRequest, record: dict) -> None:
        pass


class MeteredChat:
    """A scripted chat backend that reports token usage.

    Doctrine 3 holds: no provider is called. ``FakeChatBackend`` reports
    ``Usage()`` — honest zeros for a $0 leg — and a run that burns nothing is
    not a cost row, so a metered fake is the only way to exercise the token
    lane at all. ``id`` stays ``fake`` (nothing is billable); the MODEL is what
    ``canon.pricing`` prices, exactly as it does for a real turn.
    """

    id = "fake"

    def __init__(self, turns: list, usage) -> None:
        from canon.backends.testing import FakeChatBackend

        self._inner = FakeChatBackend(turns)
        self._usage = usage
        self.model = self._inner.model

    def stream(self, request):
        from dataclasses import replace

        from canon.llm.chat import MessageStop

        for event in self._inner.stream(request):
            yield replace(event, usage=self._usage) if isinstance(event, MessageStop) else event


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


class TestGate:
    def test_a_paid_tool_asks_with_an_estimate_in_the_payload(self, pack: Path, registry: ToolRegistry) -> None:
        engine = registry.permissions
        listener = _Answer(engine, "accept")
        with engine.listen(CONVERSATION, on_request=listener.on_request, on_decision=listener.on_decision), \
                bind_call(ACTOR, CONVERSATION):
            registry.execute(
                "place_enemies", {"level_id": "l1", "llm_backend": "fake"},
                actor=ACTOR, conversation=CONVERSATION,
            )
        assert len(listener.seen) == 1, "even a free selection asks — it is the CHIP that changes, not the gate"
        request = listener.seen[0]
        payload = request.payload()
        assert payload["tool"] == "place_enemies"
        assert payload["target"] == "place enemies in l1", "the chip copy names the verb and the target"
        # A fake selection is ask-tier, so no $ card and no estimate (README §6:
        # the middle button is the difference, not a second gate).
        assert payload["tier"] == "ask" and payload["estimate"] is None
        assert payload["always_allowed"] is True, "ask-tier in allow mode CAN be granted"

    def test_a_paid_selection_carries_the_estimate_the_card_renders(self, pack: Path, registry: ToolRegistry) -> None:
        engine = registry.permissions
        tool = registry.get("place_enemies")
        paid_input = {"level_id": "l1", "llm_backend": "anthropic"}
        assert engine.effective_tier(tool, paid_input) == PAID_TIER
        estimate = engine.estimate_for(tool, paid_input)
        assert estimate is not None, "a paid call must be able to say what it will cost"
        assert set(estimate) >= {"low", "high", "backend", "model", "unitCount", "unitLabel"}
        assert estimate["backend"] == "anthropic" and estimate["high"] >= estimate["low"] > 0
        # and the payload carries BOTH the contract shape and the card's view
        request = PermissionRequest(
            request_id="perm_test", conversation=CONVERSATION, tool="place_enemies",
            input=paid_input, tier=PAID_TIER, actor=ACTOR, specialist="artist",
            target="place enemies in l1", touches="", mode="allow",
            always_allowed=False, always_reason=None, pack=str(pack), estimate=estimate,
        )
        payload = request.payload()
        assert payload["estimate"] == estimate
        assert payload["paid"]["state"] == "estimate"
        assert payload["paid"]["high"] == estimate["high"]
        assert payload["paid"]["requestId"] == "perm_test"

    def test_always_is_refused_for_a_paid_request(self, pack: Path, registry: ToolRegistry) -> None:
        engine = registry.permissions
        listener = _Answer(engine, "always")
        with engine.listen(CONVERSATION, on_request=listener.on_request, on_decision=listener.on_decision), \
                bind_call(ACTOR, CONVERSATION), pytest.raises(ToolRefused):
            registry.execute(
                "place_enemies", {"level_id": "l1", "llm_backend": "anthropic"},
                actor=ACTOR, conversation=CONVERSATION,
            )
        request = listener.seen[0]
        assert request.tier == PAID_TIER
        assert request.always_allowed is False
        assert "never Always-allowable" in (request.always_reason or "")
        assert isinstance(listener.error, AlwaysNotAllowed)
        assert engine.grants is not None and engine.grants.find("place_enemies") is None

    def test_a_zero_dollar_all_fake_create_is_ask_tier_not_paid(self, registry: ToolRegistry) -> None:
        """Master §8 A-5 / doctrine 3: an action whose selected backends are
        all fake/none is ask-tier — the chip shows "$0", never the accent
        spend card. Naming a paid backend flips the SAME tool back to $."""
        tool = registry.get("create_project")
        engine = registry.permissions
        assert engine.effective_tier(tool, {"name": "Preview", **ALL_FAKE}) == "ask"
        assert engine.effective_tier(tool, {"name": "Real", **ALL_FAKE, "image_backend": "fal"}) == PAID_TIER
        # …and the classification is the engine's, so the chip agrees
        free = engine.classify(tool, {"name": "Preview", **ALL_FAKE},
                               actor=ACTOR, conversation=CONVERSATION, mode="allow")
        assert free.outcome == "ask" and "ask-tier" in free.reason
        paid = engine.classify(tool, {"name": "Real", **ALL_FAKE, "llm_backend": "anthropic"},
                               actor=ACTOR, conversation=CONVERSATION, mode="allow")
        assert paid.outcome == "ask" and "paid-tier" in paid.reason

    def test_every_paid_tool_registers_at_the_paid_tier(self, registry: ToolRegistry) -> None:
        for name in PAID_TOOL_NAMES:
            assert registry.get(name).tier == PAID_TIER
            assert registry.get(name).touches, "doctrine: every tool says what it touches"

    def test_a_call_naming_no_backend_stays_paid(self) -> None:
        """Guessing free would skip the spend confirm — the one guess that
        costs the user money."""
        assert spends_money("generate_asset", {"target": "enemy:x"}) is True
        assert selected_backends("generate_asset", {"target": "enemy:x"}) == {}

    @pytest.mark.parametrize(
        ("name", "tool_input", "tier"),
        [
            ("generate_asset", {"target": "enemy:x", "image_backend": "fake"}, "ask"),
            ("generate_asset", {"target": "enemy:x", "image_backend": "fal"}, PAID_TIER),
            ("animate_asset", {"target": "enemy:x", "image_backend": "fake", "vlm_backend": "none"}, "ask"),
            ("animate_asset", {"target": "enemy:x", "image_backend": "fake",
                               "vlm_backend": "anthropic"}, PAID_TIER),
            ("generate_music", {"level_id": "l1", "music_backend": "fake"}, "ask"),
            ("generate_music", {"level_id": "l1", "music_backend": "lyria"}, PAID_TIER),
            ("improve_layout", {"level_id": "l1", "instruction": "x", "llm_backend": "fake"}, "ask"),
            ("complete_row", {"type": "enemy", "id": "e", "llm_backend": "anthropic"}, PAID_TIER),
        ],
    )
    def test_the_tier_follows_the_selection_per_tool(self, name: str, tool_input: dict, tier: str) -> None:
        assert paid_tier_for(name)(tool_input) == tier

    @pytest.mark.parametrize(
        ("name", "tool_input"),
        [
            ("generate_layout", {"level_id": "l1"}),
            ("improve_layout", {"level_id": "l1", "instruction": "x"}),
            ("place_enemies", {"level_id": "l1"}),
            ("place_items", {"level_id": "l1"}),
            ("generate_level", {"stage_id": "s1"}),
            ("generate_music", {"level_id": "l1"}),
            ("complete_row", {"type": "enemy", "id": "e"}),
        ],
    )
    def test_a_call_that_omits_an_optional_backend_takes_the_body_s_own_default(
        self, name: str, tool_input: dict,
    ) -> None:
        """"Free never spend-confirms" (master §8 A-5) reaches the OMITTED
        field too. ``llm_backend`` / ``music_backend`` are in no schema's
        ``required`` list, so the model leaving one out is ordinary — and every
        one of these bodies then runs on ``fake``. Reading the call without the
        body's default classified a $0 run as paid and raised the accent
        spend-confirm card for money that is never spent.
        """
        assert selected_backends(name, tool_input), "the default is the selection"
        assert spends_money(name, tool_input) is False
        assert paid_tier_for(name)(tool_input) == "ask"
        # …and naming a paid backend still classifies paid.
        field = "music_backend" if name == "generate_music" else "llm_backend"
        paid = {**tool_input, field: "lyria" if field == "music_backend" else "anthropic"}
        assert paid_tier_for(name)(paid) == PAID_TIER


# ---------------------------------------------------------------------------
# Estimates
# ---------------------------------------------------------------------------


class TestEstimates:
    def test_a_free_selection_still_estimates_at_zero_with_the_counts(self, pack: Path) -> None:
        """The estimator masks unpaid categories to $0 but keeps the counts —
        good "what would an upgrade cost" UX, and never a missing card."""
        estimate = estimate_payload(pack, "place_enemies", {"level_id": "l1", "llm_backend": "fake"})
        assert estimate is not None
        assert estimate["low"] == 0.0 and estimate["high"] == 0.0
        assert estimate["unitCount"] >= 1 and estimate["unitLabel"] == "enemy placements"

    def test_an_unpriceable_call_yields_no_estimate_rather_than_a_confident_zero(self, pack: Path) -> None:
        """Doctrine 3: no estimate is NOT $0. The gate still opens; the card
        says the price is unknown."""
        assert estimate_payload(pack, "place_enemies", {"level_id": "nope"}) is None
        assert estimate_payload(pack, "create_project", {"name": "x"}) is not None

    @pytest.mark.parametrize(
        ("name", "tool_input", "backend", "label"),
        [
            ("generate_asset", {"target": "enemy:x", "image_backend": "fal"}, "fal", "one asset"),
            ("complete_row", {"type": "enemy", "id": "e", "llm_backend": "anthropic"},
             "anthropic", "one row"),
        ],
    )
    def test_a_paid_tool_canon_cannot_price_still_carries_a_paid_block(
        self, pack: Path, name: str, tool_input: dict, backend: str, label: str,
    ) -> None:
        """``estimate_cradle`` has no per-sprite or per-row scope, so these two
        get no PRICE. They must still get a paid BLOCK: without one,
        ``PermissionRequest.payload`` emits no ``paid`` view and the client
        degrades to a pending chip that says nothing about money — the two most
        common paid tools losing the card that exists to price them. A zero
        range renders the documented "— not estimated" state and
        ``Accept · spend on <backend>``, never a confident "$0.00".
        """
        estimate = estimate_payload(pack, name, tool_input)
        assert estimate is not None
        assert set(estimate) >= {"low", "high", "backend", "model", "unitCount", "unitLabel"}
        assert estimate["low"] == 0.0 and estimate["high"] == 0.0
        assert estimate["backend"] == backend and estimate["unitLabel"] == label
        request = PermissionRequest(
            request_id="r1", conversation=CONVERSATION, tool=name, input=tool_input,
            tier=PAID_TIER, actor=ACTOR, specialist="artist", target="…", touches="",
            mode="ask", always_allowed=False,
            always_reason="paid is never always-allowable", pack=str(pack), estimate=estimate,
        )
        assert request.payload()["paid"]["state"] == "estimate"

    def test_a_free_call_needs_no_price_block_at_all(self, pack: Path) -> None:
        """An all-fake selection is ask-tier and gets the ordinary chip — the
        unknown-price payload is for PAID calls only."""
        assert estimate_payload(pack, "generate_asset",
                                {"target": "enemy:x", "image_backend": "fake"}) is None

    def test_a_broken_estimator_never_blocks_the_gate(self, registry: ToolRegistry) -> None:
        engine = registry.permissions

        def boom(_input: dict) -> dict:
            raise RuntimeError("estimator exploded")

        engine.estimate_with("place_enemies", boom)
        assert engine.estimate_for(registry.get("place_enemies"), {"level_id": "l1"}) is None

    def test_a_broken_tier_resolver_fails_closed_to_paid(self, registry: ToolRegistry) -> None:
        engine = registry.permissions

        def boom(_input: dict) -> str:
            raise RuntimeError("resolver exploded")

        engine.tier_with("place_enemies", boom)
        assert engine.effective_tier(registry.get("place_enemies"), {"level_id": "l1"}) == PAID_TIER
        engine.tier_with("place_enemies", lambda _i: "nonsense")
        assert engine.effective_tier(registry.get("place_enemies"), {"level_id": "l1"}) == PAID_TIER


# ---------------------------------------------------------------------------
# The money reaches both ledgers
# ---------------------------------------------------------------------------


class TestCostReachesTheLedgers:
    def test_the_cost_block_reaches_the_journal_and_a_spend_row_with_journal_ref(
        self, pack: Path, registry: ToolRegistry
    ) -> None:
        engine = registry.permissions
        listener = _Answer(engine, "accept")
        before = len(all_events(pack))
        with engine.listen(CONVERSATION, on_request=listener.on_request, on_decision=listener.on_decision), \
                bind_call(ACTOR, CONVERSATION):
            raw = registry.execute(
                "place_enemies", {"level_id": "l1", "llm_backend": "fake"},
                actor=ACTOR, conversation=CONVERSATION,
            )
        import json

        result = json.loads(raw)
        assert "cost" in result, "the tool returns the verb's own cost block"
        assert result["journal"], "and the events this call appended"

        fresh = all_events(pack)[before:]
        costed = [e for e in fresh if "costCents" in e]
        assert len(costed) == 1
        assert costed[0]["identity"] == ACTOR and costed[0]["session"] == CONVERSATION
        assert costed[0]["genKind"] == "text" and costed[0]["accuracy"] == "measured"

        row = read_spend(pack)[-1]
        assert row["journal_ref"] == costed[0]["ts"], (
            "P.8.7: the spend row is a DERIVED index — journal_ref is what stops "
            "a reconciler counting the same money twice"
        )
        assert row["identity"] == ACTOR and row["session"] == CONVERSATION
        assert row["category"] == "generation" and row["scope"] == "place_enemies"
        assert result["spend"]["journal_ref"] == row["journal_ref"]

    def test_create_project_runs_through_the_gate_into_the_project_store(
        self, registry: ToolRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Row A9 wired the body this row registered: the gate is unchanged
        (an all-fake create still ASKS, at the ask tier) and the run now lands
        a real pack in the project store instead of the "not yet" refusal.

        The store is redirected at ``tmp_path`` — the suite never writes into
        the user's own ``~/CradleProjects``. The gate itself lives in
        ``tests/test_agent_create_flow.py``; what this asserts is that the
        A6 permission path still fronts it."""
        from canon.agent.tools_paid import PROJECT_STORE_ENV

        monkeypatch.setenv(PROJECT_STORE_ENV, str(tmp_path / "store"))
        engine = registry.permissions
        listener = _Answer(engine, "accept")
        with engine.listen(CONVERSATION, on_request=listener.on_request, on_decision=listener.on_decision), \
                bind_call(ACTOR, CONVERSATION):
            out = registry.execute(
                "create_project",
                {"name": "New", "template": "platformer",
                 "counts": {"stages": 1, "levels": 1, "enemies": 1, "items": 1}, **ALL_FAKE},
                actor=ACTOR, conversation=CONVERSATION,
            )
        assert listener.seen and listener.seen[0].tier == "ask", (
            "an all-fake create is ask-tier — free never spend-confirms"
        )
        created = Path(json.loads(out)["pack_dir"])
        assert created.is_dir() and created.parent == tmp_path / "store"
        assert (created / "manifest.json").is_file()

    def test_no_paid_tool_calls_a_real_provider_in_this_suite(self, registry: ToolRegistry) -> None:
        """Doctrine 3 stated as an assertion: the whole fixture pack was built
        with fake/none, and every tier probe above names a backend explicitly
        rather than letting a default reach out."""
        for name in PAID_TOOL_NAMES:
            assert paid_tier_for(name)({**ALL_FAKE}) in ("ask", PAID_TIER)


# ---------------------------------------------------------------------------
# The token lane: every identity that burns tokens is metered
# ---------------------------------------------------------------------------


class TestSpecialistTokens:
    def test_a_delegated_run_journals_its_tokens_under_the_specialist(self, pack: Path) -> None:
        """A specialist run drives its OWN ``run_conversation``, so its usage
        never passed through the turn meter: the run card showed it, the journal
        did not, and README §12's by-identity ``tokens`` column was structurally
        $0 for every specialist while cradle's panel counted the same tokens off
        ``run_end`` — two surfaces, two answers, for one conversation.
        """
        from canon.agent.conversations import ConversationStore
        from canon.agent.roster import load_roster
        from canon.agent.runs import RunManager
        from canon.agent.tools_read import register_read_tools
        from canon.llm.chat import Usage

        registry = ToolRegistry(PermissionEngine(pack, default_mode="allow"))
        register_read_tools(registry, pack)
        backend = MeteredChat(
            [[{"type": "text", "text": "l1 reads fine."}]],
            Usage(input_tokens=2140, output_tokens=380),
        )
        manager = RunManager(
            pack_dir=pack, registry=registry, backend=backend,
            store=ConversationStore(pack), roster=load_roster(),
            model="claude-sonnet-4-6",
        )
        manager.register_tools()
        conversation = manager.store.create("fake", None, None)
        before = len(all_events(pack))
        with manager.turn(conversation, emit=lambda e, d: None):
            result = manager.delegate(
                conversation=conversation, specialist="level_designer", task="look at l1",
            )
        assert result["status"] == "ok"

        fresh = all_events(pack)[before:]
        rows = [e for e in fresh if e.get("genKind") == "tokens"]
        assert len(rows) == 1, "one finished run is one token row"
        row = rows[0]
        assert row["artifact_id"] == f"conversation:{conversation}"
        assert row["identity"] == agent_actor(conversation, "level_designer")
        assert row["session"] == conversation
        assert row["detail"]["kind"] == "run" and row["detail"]["run_id"] == result["run_id"]
        assert "before_hash" not in row and "after_hash" not in row, "P.8.5: a token row is hash-less"
        assert row["gen"]["input_tokens"] == 2140 and row["gen"]["output_tokens"] == 380
        assert row["costCents"] == cost_cents(row["gen"]["cost_usd"]) > 0

        # …and it reconciles: the by-identity table now carries the specialist.
        summary = summarize_events(read_events(pack))
        by_identity = {r["identity"]: r for r in summary["byIdentity"]}
        specialist = by_identity[agent_actor(conversation, "level_designer")]
        assert specialist["tokensCents"] == row["costCents"]
        assert specialist["conversation"] == conversation
        assert specialist["specialist"] == "level_designer"
