"""Tests for the .canon/log.jsonl step log + platformer stats wiring.

The log is OBSERVABILITY, not state: opt-in via PipelineContext.steplog,
appended by both schedulers, and exempt from the byte-determinism
contract (tests/treediff.py DEFAULT_EXCLUDES) because events carry
wall-clock timestamps.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from canon.backends.testing import FakeLLMBackend
from canon.bible.models import Bible
from canon.config import CanonConfig
from canon.llm.client import LLMClient
from canon.pipeline.orchestrator import Node, orchestrate
from canon.pipeline.runner import PipelineContext, run_pipeline
from canon.pipeline.stats import GenerationStats
from canon.pipeline.steplog import StepLog
from examples.platformer_pack import PlatformerPrompts, compose_pipeline
from examples.run_platformer_slice import make_fake_responder
from tests.treediff import assert_trees_byte_identical


def _events(output_dir: Path) -> list[dict]:
    path = output_dir / ".canon" / "log.jsonl"
    assert path.exists(), "steplog file missing"
    return [json.loads(line) for line in path.read_text().splitlines()]


def _ctx(tmp_path: Path, *, steplog: bool = True) -> PipelineContext:
    return PipelineContext(
        bible=Bible.empty(seed="steplog"),
        config=CanonConfig(seed="steplog", output_dir=tmp_path),
        rng=random.Random(0),
        steplog=StepLog(tmp_path) if steplog else None,
    )


class _Ok:
    name = "ok"

    def run(self, ctx) -> None:
        pass


class _Boom:
    name = "boom"

    def run(self, ctx) -> None:
        raise RuntimeError("kaput")


class _TwoNodes:
    """DagPhase with two chained nodes."""

    name = "two"

    def expand(self, ctx) -> list[Node]:
        return [
            Node(node_id="n1", run=lambda _ctx: None, requires=[]),
            Node(node_id="n2", run=lambda _ctx: None, requires=["n1"]),
        ]


class TestStepLogEmitter:
    def test_appends_jsonl_with_ts_and_event(self, tmp_path: Path) -> None:
        log = StepLog(tmp_path)
        log.emit("run_start", nodes=3)
        log.emit("node_done", node="a")
        events = _events(tmp_path)
        assert [e["event"] for e in events] == ["run_start", "node_done"]
        assert all("ts" in e for e in events)
        assert events[0]["nodes"] == 3 and events[1]["node"] == "a"

    def test_survives_across_instances(self, tmp_path: Path) -> None:
        StepLog(tmp_path).emit("run_start")
        StepLog(tmp_path).emit("run_end")  # resume = a new process
        assert [e["event"] for e in _events(tmp_path)] == [
            "run_start", "run_end",
        ]


class TestSequentialPath:
    def test_lifecycle_events(self, tmp_path: Path) -> None:
        run_pipeline([_Ok()], _ctx(tmp_path))
        names = [e["event"] for e in _events(tmp_path)]
        assert names == ["run_start", "node_start", "node_done", "run_end"]
        assert _events(tmp_path)[0]["scheduler"] == "sequential"

    def test_failure_emits_and_reraises(self, tmp_path: Path) -> None:
        with pytest.raises(RuntimeError, match="kaput"):
            run_pipeline([_Ok(), _Boom()], _ctx(tmp_path))
        events = _events(tmp_path)
        assert [e["event"] for e in events] == [
            "run_start", "node_start", "node_done",
            "node_start", "node_failed", "run_end",
        ]
        failed = events[-2]
        assert failed["node"] == "phase:boom" and "kaput" in failed["error"]
        assert events[-1]["ok"] is False

    def test_no_steplog_no_file(self, tmp_path: Path) -> None:
        run_pipeline([_Ok()], _ctx(tmp_path, steplog=False))
        assert not (tmp_path / ".canon").exists()


class TestOrchestratedPath:
    def test_lifecycle_and_resume_skips(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path)
        orchestrate([_TwoNodes()], ctx)
        names = [e["event"] for e in _events(tmp_path)]
        assert names == [
            "run_start",
            "node_start", "node_done",
            "node_start", "node_done",
            "run_end",
        ]
        # Resume: both nodes DONE → skipped, appended to the same log.
        orchestrate([_TwoNodes()], ctx)
        events = _events(tmp_path)
        skipped = [e for e in events if e["event"] == "node_skipped"]
        assert {e["node"] for e in skipped} == {"n1", "n2"}
        assert all(e["reason"] == "done" for e in skipped)
        assert events[-1]["event"] == "run_end" and events[-1]["ok"] is True

    def test_failure_marks_node_failed(self, tmp_path: Path) -> None:
        class _FailNode:
            name = "failing"

            def expand(self, ctx) -> list[Node]:
                def boom(_ctx) -> None:
                    raise RuntimeError("kaput")

                return [Node(node_id="bad", run=boom, requires=[])]

        report = orchestrate([_FailNode()], _ctx(tmp_path))
        assert report.escalated == ["bad"]
        events = _events(tmp_path)
        assert any(
            e["event"] == "node_failed" and e["node"] == "bad"
            for e in events
        )
        assert events[-1]["event"] == "run_end" and events[-1]["ok"] is False


def _run_slice_observed(
    output_dir: Path, seed: str = "emberfall_001", image_producer=None
):
    """The runner main()'s observability wiring, test-shaped: one stats
    object shared by the LLM client and the manifest snapshot + a
    steplog. An ``image_producer`` turns the art phases ON — without one
    they early-return, which is exactly the difference between a $0 run
    and the paid run the progress display exists for."""
    stats = GenerationStats(llm_backend="fake")
    ctx = PipelineContext(
        bible=Bible.empty(seed=seed),
        config=CanonConfig(seed=seed, output_dir=output_dir),
        rng=random.Random(seed),
        stats=stats,
        llm=LLMClient(FakeLLMBackend(make_fake_responder()), stats=stats),
        prompts=PlatformerPrompts(),
        steplog=StepLog(output_dir),
    )
    run_pipeline(compose_pipeline(image_producer=image_producer), ctx)
    return ctx


def _fake_image_producer(tmp_path: Path):
    """The art phases' $0 producer — same harness tests/test_art_phases.py
    uses, so the art path here runs the real phase bodies."""
    import io

    from PIL import Image

    from canon.backends.testing import FakeImageBackend
    from examples.platformer_pack.tileset_art import DiffusionSheetProducer

    path = tmp_path / "blob.png"
    if not path.exists():
        img = Image.new("RGB", (64, 64), (255, 255, 255))
        for y in range(16, 48):
            for x in range(16, 48):
                img.putpixel((x, y), (180, 40, 40))
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        path.write_bytes(buffer.getvalue())
    return DiffusionSheetProducer(FakeImageBackend(placeholder=path))


class TestSubPhaseProgress:
    """`node_item` — the sub-phase heartbeat.

    Phase-level events are enough for a $0 run (three seconds end to end).
    A PAID run sits inside one node for minutes per asset, and a display
    frozen on "Sprite art" for ten minutes is indistinguishable from a
    crash — which is the whole reason cradle reads this file.
    """

    def test_step_is_a_noop_without_a_steplog(self) -> None:
        from examples.platformer_pack.phases import step

        class _Bare:
            pass

        step(_Bare(), "plat:sprite_art", "anything")  # must not raise

    def test_step_carries_the_item_and_its_position(
        self, tmp_path: Path
    ) -> None:
        from examples.platformer_pack.phases import step

        ctx = _ctx(tmp_path)
        step(ctx, "plat:sprite_art", "Cinder Beetle", index=3, total=15)
        step(ctx, "plat:audio", "stage_1 · music")
        first, second = _events(tmp_path)
        assert first == {
            "ts": first["ts"],
            "event": "node_item",
            "node": "phase:plat:sprite_art",
            "item": "Cinder Beetle",
            "index": 3,
            "total": 15,
        }
        # An unknown count omits the fields rather than inventing a total.
        assert "index" not in second and "total" not in second

    def test_slice_reports_items_inside_the_long_phases(
        self, tmp_path: Path
    ) -> None:
        out = tmp_path / "run"
        _run_slice_observed(
            out, image_producer=_fake_image_producer(tmp_path)
        )
        items = [e for e in _events(out) if e["event"] == "node_item"]
        assert items, "no sub-phase progress at all"
        by_node: dict[str, list[dict]] = {}
        for event in items:
            by_node.setdefault(event["node"], []).append(event)
        # The phases that dominate a paid run must all report.
        assert {
            "phase:plat:layout",
            "phase:plat:sprite_art",
            "phase:plat:tileset_art",
            "phase:plat:backdrop_art",
        } <= set(by_node)
        # Every counted phase counts HONESTLY: 1..total, no gaps, and the
        # promised total is the number actually delivered — a bar that stops
        # at 12/15 reads as a hang.
        for node, events in by_node.items():
            counted = [e for e in events if "index" in e]
            if not counted:
                continue
            total = counted[0]["total"]
            assert [e["index"] for e in counted] == list(
                range(1, len(counted) + 1)
            ), node
            assert all(e["total"] == total for e in counted), node
            assert len(counted) == total, node

    def test_progress_does_not_break_determinism(
        self, tmp_path: Path
    ) -> None:
        """`node_item` is observability like every other event here: the
        generated tree must not move a byte because of it."""
        run_a, run_b = tmp_path / "a", tmp_path / "b"
        producer = _fake_image_producer(tmp_path)
        _run_slice_observed(run_a, image_producer=producer)
        _run_slice_observed(run_b, image_producer=producer)
        assert any(
            e["event"] == "node_item" for e in _events(run_a)
        ), "test would pass vacuously without progress events"
        assert_trees_byte_identical(run_a, run_b)


class TestSliceObservability:
    def test_slice_writes_log_and_stats(self, tmp_path: Path) -> None:
        out = tmp_path / "run"
        ctx = _run_slice_observed(out)

        events = _events(out)
        assert events[0]["event"] == "run_start"
        assert events[-1]["event"] == "run_end" and events[-1]["ok"] is True
        started = {e["node"] for e in events if e["event"] == "node_start"}
        assert "phase:plat:manifest" in started

        stats = json.loads((out / "generation_stats.json").read_text())
        assert stats["llm_calls"] > 0
        assert stats["llm_backend"] == "fake"
        assert stats["by_phase"], "per-phase-label breakdown missing"
        # Fine-grained labels: the section-layout calls are per-level.
        assert any(k.startswith("plat:layout:") for k in stats["by_phase"])
        assert ctx.stats.llm_calls == stats["llm_calls"]

    def test_observed_runs_stay_byte_identical(self, tmp_path: Path) -> None:
        """The determinism contract holds with the observability files
        PRESENT — they are exempt (timestamps), everything else must
        still match byte-for-byte."""
        run_a, run_b = tmp_path / "a", tmp_path / "b"
        _run_slice_observed(run_a)
        _run_slice_observed(run_b)
        assert (run_a / ".canon" / "log.jsonl").exists()
        assert (run_a / "generation_stats.json").exists()
        assert_trees_byte_identical(run_a, run_b)
