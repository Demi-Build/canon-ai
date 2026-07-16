"""Tests for the per-agent model table (PRD §9.1) — models.json loader,
label-prefix resolution, LLMClient wiring, pricing warning, and the
provenance stamping seam.

The load-bearing safety property: the resolver only takes effect on
backends that declare ``supports_request_model``, so $0 fake runs (and
what they stamp into provenance) are byte-identical with or without a
table.
"""

from __future__ import annotations

import json
import logging
import random
from pathlib import Path

import pytest

from canon.backends.testing import FakeLLMBackend
from canon.bible.models import Bible
from canon.config import CanonConfig
from canon.llm.client import LLMClient
from canon.llm.request import LLMRequest
from canon.pipeline.runner import PipelineContext
from examples.platformer_pack.models import (
    DEFAULT_MODEL_TABLE,
    ModelTable,
    load_models,
)
from examples.platformer_pack.phases import resolved_model, stamp_provenance


class TestModelTable:
    def test_default_template_assignments(self) -> None:
        t = DEFAULT_MODEL_TABLE
        haiku = t.model_tiers["cheap"]
        sonnet = t.model_tiers["mid"]
        # Validator-backstopped agents ride cheap; structural ones mid.
        assert t.resolve("plat:enemies:3") == haiku
        assert t.resolve("plat:placement:l5") == haiku
        assert t.resolve("plat:decorator:l1") == haiku
        assert t.resolve("plat:layout:l5:s2") == sonnet
        assert t.resolve("plat:world") == sonnet

    def test_unknown_label_falls_to_default(self) -> None:
        assert (
            DEFAULT_MODEL_TABLE.resolve("plat:new_agent:x")
            == DEFAULT_MODEL_TABLE.model_tiers["mid"]
        )

    def test_prefix_match_is_colon_bounded_and_longest_wins(self) -> None:
        t = ModelTable(
            model_tiers={"a": "model-a", "b": "model-b", "c": "model-c"},
            agent_tiers={
                "_default": "c",
                "plat:layout": "a",
                "plat:layout:l9": "b",
            },
        )
        assert t.resolve("plat:layout:l1:s0") == "model-a"
        assert t.resolve("plat:layout:l9:s0") == "model-b"  # longest wins
        # "plat:layoutX" must NOT match the "plat:layout" prefix.
        assert t.resolve("plat:layoutX:l1") == "model-c"

    def test_no_default_returns_none(self) -> None:
        t = ModelTable(model_tiers={"a": "m"}, agent_tiers={"x": "a"})
        assert t.resolve("y:z") is None

    def test_undefined_tier_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "models.json"
        bad.write_text(json.dumps({
            "model_tiers": {"mid": "m"},
            "agent_tiers": {"_default": "mid", "plat:world": "huge"},
        }))
        with pytest.raises(ValueError, match="huge"):
            load_models(bad)


class _HonoringBackend:
    """Stub that declares supports_request_model and records requests."""

    supports_request_model = True
    model = "constructed-default"

    def __init__(self) -> None:
        self.seen: list[LLMRequest] = []

    def generate(self, request: LLMRequest) -> str:
        self.seen.append(request)
        return "ok"


class TestClientResolver:
    def test_fake_backend_never_gets_a_model(self) -> None:
        backend = FakeLLMBackend(["ok"])
        client = LLMClient(
            backend, model_resolver=lambda label: "should-not-apply"
        )
        request = LLMRequest(system="s", user_message="u")
        client.generate(request, phase="plat:enemies:0")
        assert request.model is None

    def test_honoring_backend_gets_resolved_model(self) -> None:
        backend = _HonoringBackend()
        client = LLMClient(
            backend,
            model_resolver=DEFAULT_MODEL_TABLE.resolve,
        )
        client.generate(
            LLMRequest(system="s", user_message="u"), phase="plat:enemies:0"
        )
        assert backend.seen[0].model == DEFAULT_MODEL_TABLE.model_tiers["cheap"]

    def test_explicit_request_model_wins(self) -> None:
        backend = _HonoringBackend()
        client = LLMClient(
            backend, model_resolver=DEFAULT_MODEL_TABLE.resolve
        )
        client.generate(
            LLMRequest(system="s", user_message="u", model="pinned"),
            phase="plat:enemies:0",
        )
        assert backend.seen[0].model == "pinned"


class TestPricingWarning:
    def test_unpriced_model_warns_once(self, caplog) -> None:
        anthropic_mod = pytest.importorskip("anthropic")  # noqa: F841
        from canon.backends.anthropic import AnthropicBackend

        class _Usage:
            input_tokens = 10
            output_tokens = 5

        class _Resp:
            usage = _Usage()
            content: list = []

        class _Messages:
            def create(self, **kwargs):
                return _Resp()

        class _Client:
            messages = _Messages()

        backend = AnthropicBackend(model="not-a-priced-model", client=_Client())
        request = LLMRequest(system="s", user_message="u")
        with caplog.at_level(logging.WARNING):
            backend.generate(request)
            backend.generate(request)
        warnings = [
            r for r in caplog.records if "No PRICING entry" in r.getMessage()
        ]
        assert len(warnings) == 1  # loud, but once
        assert backend.last_cost == 0.0

    def test_request_model_reaches_the_api_and_pricing(self) -> None:
        pytest.importorskip("anthropic")
        from canon.backends.anthropic import PRICING, AnthropicBackend

        seen: dict = {}

        class _Usage:
            input_tokens = 1_000_000
            output_tokens = 0

        class _Resp:
            usage = _Usage()
            content: list = []

        class _Messages:
            def create(self, **kwargs):
                seen.update(kwargs)
                return _Resp()

        class _Client:
            messages = _Messages()

        backend = AnthropicBackend(client=_Client())
        haiku = "claude-haiku-4-5-20251001"
        backend.generate(LLMRequest(system="s", user_message="u", model=haiku))
        assert seen["model"] == haiku
        assert backend.last_cost == pytest.approx(
            1_000_000 * PRICING[haiku]["input"]
        )


class _Entity:
    provenance_hash = ""


class TestProvenanceStamping:
    def _ctx(self, tmp_path: Path, backend) -> PipelineContext:
        return PipelineContext(
            bible=Bible.empty(seed="prov"),
            config=CanonConfig(seed="prov", output_dir=tmp_path),
            rng=random.Random(0),
            llm=LLMClient(
                backend, model_resolver=DEFAULT_MODEL_TABLE.resolve
            ),
        )

    def test_fake_backend_stamps_are_table_independent(
        self, tmp_path: Path
    ) -> None:
        ctx = self._ctx(tmp_path, FakeLLMBackend(["ok"]))
        assert resolved_model(ctx, "plat:enemies:0") == "fake"
        with_table, without = _Entity(), _Entity()
        stamp_provenance(ctx, with_table, "sha256:aa", label="plat:enemies")
        ctx.llm.model_resolver = None
        stamp_provenance(ctx, without, "sha256:aa", label="plat:enemies")
        assert with_table.provenance_hash == without.provenance_hash

    def test_honoring_backend_stamps_the_task_model(
        self, tmp_path: Path
    ) -> None:
        ctx = self._ctx(tmp_path, _HonoringBackend())
        assert (
            resolved_model(ctx, "plat:enemies:0")
            == DEFAULT_MODEL_TABLE.model_tiers["cheap"]
        )
        cheap_task, mid_task = _Entity(), _Entity()
        stamp_provenance(ctx, cheap_task, "sha256:aa", label="plat:enemies")
        stamp_provenance(ctx, mid_task, "sha256:aa", label="plat:world")
        # Same content, different authoring model → different provenance.
        assert cheap_task.provenance_hash != mid_task.provenance_hash
