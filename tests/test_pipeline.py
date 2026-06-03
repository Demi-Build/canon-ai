"""Tests for the pipeline framework — Phase protocol, runner, retry, stats."""

import random

import pytest

from canon.pipeline.retry import retry_with_feedback
from canon.pipeline.runner import Phase, PipelineContext, run_phase, run_pipeline
from canon.pipeline.stats import GenerationStats

# --- Minimal test fixtures ---


class FakeBible:
    """Minimal bible-like object for testing."""

    def __init__(self, seed="test"):
        self.seed = seed
        self.rooms = {}
        self._persisted = False

    def persist(self, path: str) -> None:
        self._persisted = True


class FakeConfig:
    seed = "test"
    num_maps = 1


# --- Phase protocol tests ---


class CounterPhase:
    """Phase that increments a counter in artifacts."""

    def __init__(self, name: str = "counter"):
        self.name = name

    def run(self, ctx: PipelineContext) -> None:
        count = ctx.artifacts.get("count", 0)
        ctx.artifacts["count"] = count + 1


class FailingPhase:
    name = "failing"

    def run(self, ctx: PipelineContext) -> None:
        raise RuntimeError("Phase failed")


class TestPhaseProtocol:
    def test_satisfies_protocol(self):
        phase = CounterPhase()
        assert isinstance(phase, Phase)

    def test_class_without_name_fails_protocol(self):
        class BadPhase:
            def run(self, ctx):
                pass

        assert not isinstance(BadPhase(), Phase)


# --- PipelineContext tests ---


class TestPipelineContext:
    def test_creation(self):
        ctx = PipelineContext(
            bible=FakeBible(),
            config=FakeConfig(),
            rng=random.Random(42),
        )
        assert ctx.artifacts == {}
        assert ctx.checkers == []
        assert ctx.validators == []

    def test_artifacts_persist_across_phases(self):
        ctx = PipelineContext(
            bible=FakeBible(),
            config=FakeConfig(),
            rng=random.Random(42),
        )
        phase1 = CounterPhase("first")
        phase2 = CounterPhase("second")
        run_pipeline([phase1, phase2], ctx)
        assert ctx.artifacts["count"] == 2


# --- run_pipeline tests ---


class TestRunPipeline:
    def test_runs_phases_in_order(self):
        order = []

        class TrackingPhase:
            def __init__(self, n):
                self.name = f"phase_{n}"
                self.n = n

            def run(self, ctx):
                order.append(self.n)

        ctx = PipelineContext(
            bible=FakeBible(), config=FakeConfig(), rng=random.Random(42)
        )
        run_pipeline(
            [TrackingPhase(1), TrackingPhase(2), TrackingPhase(3)], ctx
        )
        assert order == [1, 2, 3]

    def test_returns_bible(self):
        bible = FakeBible(seed="return_test")
        ctx = PipelineContext(
            bible=bible, config=FakeConfig(), rng=random.Random(42)
        )
        result = run_pipeline([CounterPhase()], ctx)
        assert result is bible

    def test_persist_after_each(self, tmp_path):
        bible = FakeBible()
        ctx = PipelineContext(
            bible=bible, config=FakeConfig(), rng=random.Random(42)
        )
        run_pipeline(
            [CounterPhase(), CounterPhase()],
            ctx,
            persist_after_each=str(tmp_path / "bible.json"),
        )
        assert bible._persisted

    def test_empty_pipeline(self):
        ctx = PipelineContext(
            bible=FakeBible(), config=FakeConfig(), rng=random.Random(42)
        )
        result = run_pipeline([], ctx)
        assert result is ctx.bible

    def test_phase_failure_propagates(self):
        ctx = PipelineContext(
            bible=FakeBible(), config=FakeConfig(), rng=random.Random(42)
        )
        with pytest.raises(RuntimeError, match="Phase failed"):
            run_pipeline([FailingPhase()], ctx)


# --- run_phase tests ---


class TestRunPhase:
    def test_runs_single_phase(self):
        ctx = PipelineContext(
            bible=FakeBible(), config=FakeConfig(), rng=random.Random(42)
        )
        run_phase(CounterPhase(), ctx)
        assert ctx.artifacts["count"] == 1


# --- retry_with_feedback tests ---


class TestRetryWithFeedback:
    def test_passes_first_try(self):
        result = retry_with_feedback(
            generate_fn=lambda: {"name": "Sword"},
            validate_fn=lambda x: (True, []),
            fallback={"name": "Fallback"},
        )
        assert result["name"] == "Sword"

    def test_retries_on_failure(self):
        attempts = []

        def gen(feedback=None):
            attempts.append(feedback)
            if len(attempts) < 3:
                return {"name": ""}
            return {"name": "Fixed"}

        def validate(data):
            if not data.get("name"):
                return False, ["missing name"]
            return True, []

        result = retry_with_feedback(gen, validate, fallback={"name": "Fallback"})
        assert result["name"] == "Fixed"
        assert len(attempts) == 3

    def test_feedback_passed_to_generator(self):
        received_feedback = []

        def gen(feedback=None):
            received_feedback.append(feedback)
            if feedback:
                return {"name": "Fixed"}
            return {"name": ""}

        def validate(data):
            if not data.get("name"):
                return False, ["name is empty"]
            return True, []

        retry_with_feedback(gen, validate, fallback={})
        assert received_feedback[0] is None
        assert received_feedback[1] == ["name is empty"]

    def test_returns_fallback_on_exhaustion(self):
        result = retry_with_feedback(
            generate_fn=lambda **kw: {"broken": True},
            validate_fn=lambda x: (False, ["always fails"]),
            fallback={"name": "Fallback"},
            max_retries=2,
        )
        assert result["name"] == "Fallback"

    def test_handles_generator_exception(self):
        call_count = [0]

        def gen(feedback=None):
            call_count[0] += 1
            if call_count[0] < 3:
                raise ValueError("LLM error")
            return {"ok": True}

        result = retry_with_feedback(
            gen,
            validate_fn=lambda x: (True, []),
            fallback={"ok": False},
        )
        assert result["ok"]


# --- GenerationStats tests ---


class TestGenerationStats:
    def test_empty_stats(self):
        stats = GenerationStats()
        assert stats.llm_calls == 0
        assert stats.total_cost == 0.0

    def test_record_call(self):
        stats = GenerationStats()
        stats.record_call("story", input_tokens=100, output_tokens=50, cost=0.01)
        stats.record_call("story", input_tokens=200, output_tokens=100, cost=0.02)
        assert stats.llm_calls == 2
        assert stats.total_input_tokens == 300
        assert stats.total_cost == pytest.approx(0.03)
        assert stats.by_phase["story"]["calls"] == 2

    def test_to_dict(self):
        stats = GenerationStats()
        stats.record_call("test", cost=0.05)
        d = stats.to_dict()
        assert d["llm_calls"] == 1
        assert "test" in d["by_phase"]

    # -----------------------------------------------------------------------
    # New v0.2 tests
    # -----------------------------------------------------------------------

    def test_total_cost_usd_is_sum_of_costs(self):
        """total_cost_usd = llm_cost_usd + image_cost_usd + audio_cost_usd."""
        stats = GenerationStats()
        stats.llm_cost_usd = 4.49
        stats.image_cost_usd = 27.38
        stats.audio_cost_usd = 1.88
        assert stats.total_cost_usd == pytest.approx(33.75)

    def test_generation_time_human_formats_correctly(self):
        """93 minutes 0 seconds -> '93m 00s'."""
        stats = GenerationStats()
        stats.generation_time_seconds = 5580.0
        assert stats.generation_time_human == "93m 00s"

    def test_generation_time_human_minutes_and_seconds(self):
        """2 minutes 5 seconds -> '2m 05s'."""
        stats = GenerationStats()
        stats.generation_time_seconds = 125.0
        assert stats.generation_time_human == "2m 05s"

    def test_to_dict_includes_v0_2_keys(self):
        """to_dict() includes all v0.2 keys for mazeworld compatibility."""
        stats = GenerationStats()
        d = stats.to_dict()
        v0_2_keys = [
            "llm_backend",
            "image_backend",
            "music_backend",
            "sfx_backend",
            "total_tokens",
            "images_attempted",
            "images_succeeded",
            "music_attempted",
            "music_succeeded",
            "sfx_attempted",
            "sfx_succeeded",
            "llm_cost_usd",
            "image_cost_usd",
            "audio_cost_usd",
            "total_cost_usd",
            "generation_time_seconds",
            "generation_time_human",
        ]
        for key in v0_2_keys:
            assert key in d, f"Missing key in to_dict(): {key!r}"

    def test_record_call_increments_llm_cost_usd(self):
        """record_call() increments llm_cost_usd alongside total_cost."""
        stats = GenerationStats()
        stats.record_call("story", cost=0.05)
        stats.record_call("story", cost=0.10)
        assert stats.llm_cost_usd == pytest.approx(0.15)
        assert stats.total_cost == pytest.approx(0.15)

    def test_total_tokens_property(self):
        """total_tokens == total_input_tokens + total_output_tokens."""
        stats = GenerationStats()
        stats.record_call("story", input_tokens=100, output_tokens=50)
        assert stats.total_tokens == 150

    def test_new_fields_start_at_zero_or_empty(self):
        """All new v0.2 fields have safe zero/empty defaults."""
        stats = GenerationStats()
        assert stats.llm_backend == ""
        assert stats.image_backend == ""
        assert stats.music_backend == ""
        assert stats.sfx_backend == ""
        assert stats.music_attempted == 0
        assert stats.music_succeeded == 0
        assert stats.sfx_attempted == 0
        assert stats.sfx_succeeded == 0
        assert stats.llm_cost_usd == 0.0
        assert stats.image_cost_usd == 0.0
        assert stats.audio_cost_usd == 0.0
        assert stats.generation_time_seconds == 0.0
