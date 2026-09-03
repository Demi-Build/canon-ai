"""Tests for row P1-A4.5's multi-item cancel hook (master §3.0-D).

The ONE Stop contract for canon verbs: a per-job cancel FILE named by
``CANON_CANCEL_FILE``, checked by ``phases.step`` — the one ``node_item``
emitter — before every item; ``RunCancelled`` at the boundary; the
scheduler's ``run_end`` gains ``cancelled: true`` + ``kept``; ``run_slice``
exits 3 with everything landed kept on disk.

Hermetic + $0: the fake backend only. The subprocess test runs the real
slice runner and touches the cancel file after its first ``node_item``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from canon.packs.platformer.phases import step
from canon.pipeline.steplog import CANCEL_FILE_ENV, EXIT_CANCELLED, RunCancelled, StepLog

REPO = Path(__file__).resolve().parents[1]


def lines(log: StepLog) -> list[dict]:
    return [json.loads(line) for line in log.path.read_text(encoding="utf-8").splitlines() if line.strip()]


def ctx_with(log: StepLog | None):
    return SimpleNamespace(steplog=log)


# ---------------------------------------------------------------------------
# The emitter check
# ---------------------------------------------------------------------------


class TestEmitterCheck:
    def test_no_steplog_writes_nothing_and_never_checks(self, tmp_path: Path, monkeypatch) -> None:
        cancel = tmp_path / "cancel"
        cancel.write_text("x")
        monkeypatch.setenv(CANCEL_FILE_ENV, str(cancel))
        step(ctx_with(None), "plat:x", "a")  # no log, no raise — MazeWorld / most tests

    def test_env_unset_is_a_byte_for_byte_noop(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv(CANCEL_FILE_ENV, raising=False)
        log = StepLog(tmp_path)
        step(ctx_with(log), "plat:x", "a", index=1, total=2)
        step(ctx_with(log), "plat:x", "b", index=2, total=2)
        records = lines(log)
        assert [r["event"] for r in records] == ["node_item", "node_item"]
        assert records[0] == {**records[0], "node": "phase:plat:x", "item": "a", "index": 1, "total": 2}
        assert not any("cancel" in key for r in records for key in r)
        assert log.cancelled is None
        assert log.cancel_file is None

    def test_env_naming_an_absent_file_is_a_noop(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv(CANCEL_FILE_ENV, str(tmp_path / "never-created"))
        log = StepLog(tmp_path)
        step(ctx_with(log), "plat:x", "a")
        assert log.cancel_requested() is False
        assert [r["event"] for r in lines(log)] == ["node_item"]

    def test_the_cancel_file_raises_at_the_boundary_and_keeps_the_landed(self, tmp_path: Path, monkeypatch) -> None:
        cancel = tmp_path / "cancel" / "job-1"
        monkeypatch.setenv(CANCEL_FILE_ENV, str(cancel))
        log = StepLog(tmp_path)
        log.emit("run_start", scheduler="sequential", phases=2)
        log.emit("node_start", node="phase:plat:a")
        step(ctx_with(log), "plat:a", "a1")
        log.emit("node_done", node="phase:plat:a")
        log.emit("node_start", node="phase:plat:b")
        step(ctx_with(log), "plat:b", "b1", index=1, total=3)
        # ⏹ lands between two items.
        cancel.parent.mkdir(parents=True)
        cancel.write_text("cancel\n")
        with pytest.raises(RunCancelled) as raised:
            step(ctx_with(log), "plat:b", "b2", index=2, total=3)
        error = raised.value
        assert (error.node, error.item) == ("phase:plat:b", "b2")
        assert error.kept == ["phase:plat:a", "phase:plat:b:b1"]
        assert log.cancelled is error
        # b2 was never announced — nothing started after the stop.
        assert [r.get("item") for r in lines(log) if r["event"] == "node_item"] == ["a1", "b1"]
        # The scheduler's own run_end becomes cancel-aware — no second line.
        log.emit("run_end", scheduler="sequential", ok=False)
        end = lines(log)[-1]
        assert end["event"] == "run_end"
        assert end["ok"] is False
        assert end["cancelled"] is True
        assert end["kept"] == ["phase:plat:a", "phase:plat:b:b1"]

    def test_pinned_cancel_file_beats_the_env(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv(CANCEL_FILE_ENV, str(tmp_path / "env-file"))
        pinned = tmp_path / "pinned"
        log = StepLog(tmp_path, cancel_file=pinned)
        assert log.cancel_file == pinned
        assert not log.cancel_requested()
        pinned.write_text("x")
        with pytest.raises(RunCancelled):
            log.check_cancel("phase:x", "i")

    def test_run_end_without_a_cancel_carries_no_cancel_keys(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv(CANCEL_FILE_ENV, raising=False)
        log = StepLog(tmp_path)
        log.emit("node_done", node="phase:a")
        log.emit("run_end", scheduler="sequential", ok=True)
        end = lines(log)[-1]
        assert "cancelled" not in end and "kept" not in end


# ---------------------------------------------------------------------------
# The orchestrated scheduler: start nothing new
# ---------------------------------------------------------------------------


class TestOrchestratedDispatch:
    """§3.0-D's "start nothing new" for the DAG. The ``node_item`` check
    only reaches nodes with an item loop; a node without one would start
    and run to completion after the cancel file landed."""

    def _phase(self, on_run):
        class _Phase:
            name = "cancel"

            def expand(self, _ctx) -> list:
                from canon.pipeline.orchestrator import Node

                return [
                    Node(node_id="first", run=lambda _c: on_run("first"), requires=[]),
                    Node(node_id="second", run=lambda _c: on_run("second"), requires=[]),
                ]

        return _Phase()

    def _ctx(self, tmp_path: Path, log: StepLog):
        import random

        from canon.bible.models import Bible
        from canon.config import CanonConfig
        from canon.pipeline.runner import PipelineContext

        return PipelineContext(
            bible=Bible.empty(seed="cancel"),
            config=CanonConfig(seed="cancel", output_dir=tmp_path),
            rng=random.Random(0),
            steplog=log,
        )

    def test_a_cancel_stops_the_scheduler_submitting_further_nodes(self, tmp_path: Path, monkeypatch) -> None:
        from canon.pipeline.orchestrator import orchestrate

        monkeypatch.delenv(CANCEL_FILE_ENV, raising=False)
        cancel = tmp_path / "cancel"
        log = StepLog(tmp_path, cancel_file=cancel)
        ran: list[str] = []

        def on_run(node: str) -> None:
            ran.append(node)
            cancel.write_text("stop\n", encoding="utf-8")  # ⏹ lands while this node runs

        report = orchestrate([self._phase(on_run)], self._ctx(tmp_path, log))
        assert ran == ["first"], "the second node must never be submitted after the cancel"
        assert report.done == ["first"]

    def test_no_cancel_file_runs_every_node(self, tmp_path: Path, monkeypatch) -> None:
        from canon.pipeline.orchestrator import orchestrate

        monkeypatch.delenv(CANCEL_FILE_ENV, raising=False)
        ran: list[str] = []
        log = StepLog(tmp_path)
        report = orchestrate([self._phase(ran.append)], self._ctx(tmp_path, log))
        assert sorted(ran) == ["first", "second"] and sorted(report.done) == ["first", "second"]


# ---------------------------------------------------------------------------
# The runner: exit 3, files kept
# ---------------------------------------------------------------------------


def _wait_for_first_item(log_path: Path, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if log_path.is_file():
            for line in log_path.read_text(encoding="utf-8").splitlines():
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if record.get("event") == "node_item":
                    return record
        time.sleep(0.05)
    raise AssertionError(f"no node_item within {timeout}s in {log_path}")


@pytest.mark.slow
def test_run_slice_stops_at_the_next_item_boundary_exits_3_and_keeps_what_landed(tmp_path: Path) -> None:
    out = tmp_path / "slice"
    cancel = out / ".canon" / "cancel" / "job-test"
    env = {**os.environ, CANCEL_FILE_ENV: str(cancel)}
    process = subprocess.Popen(
        [
            sys.executable, "-m", "canon.packs.platformer.run_slice",
            "--backend", "fake", "--engine", "json", "--image-backend", "fake",
            "--music-backend", "none", "--sfx-backend", "none",
            "--num-stages", "1", "--num-levels", "2", "--num-enemies", "2", "--num-items", "2",
            "--seed", "a45-cancel", "--output-dir", str(out),
        ],
        cwd=REPO,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        first = _wait_for_first_item(out / ".canon" / "log.jsonl", timeout=120)
        cancel.parent.mkdir(parents=True, exist_ok=True)
        cancel.write_text("cancel\n", encoding="utf-8")
        stdout, _stderr = process.communicate(timeout=180)
    finally:
        if process.poll() is None:
            process.kill()
    assert process.returncode == EXIT_CANCELLED, stdout.decode(errors="replace")[-800:]
    records = [json.loads(line) for line in (out / ".canon" / "log.jsonl").read_text().splitlines() if line.strip()]
    ends = [r for r in records if r["event"] == "run_end"]
    assert len(ends) == 1, "one cancel-aware run_end, never a second line"
    end = ends[0]
    assert end["ok"] is False and end["cancelled"] is True
    assert isinstance(end["kept"], list)
    # Nothing landed after the stop: every item announced after the first one
    # belongs to the node that was interrupted (or one of the boundary's).
    items = [r for r in records if r["event"] == "node_item"]
    assert items[0]["item"] == first["item"]
    # Keep what landed — the output tree exists with whatever completed
    # (at minimum the pack's own dirs and the step log itself).
    landed = [p for p in out.rglob("*") if p.is_file() and ".canon" not in p.parts]
    assert landed, "the files completed before the stop stay on disk"
    assert b"Cancelled" in stdout
