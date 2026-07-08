"""Tests for canon.pipeline.orchestrator — DAG scheduling, resume,
gates, determinism, and the CLI verbs (run/resume/status).

Synthetic phases only: the orchestrator schedules, it never generates.
"""

from __future__ import annotations

import json
import random
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from canon.bible.artifacts import ArtifactStatus
from canon.bible.models import Bible
from canon.config import CanonConfig
from canon.pipeline.orchestrator import (
    DagPhase,
    Node,
    build_nodes,
    orchestrate,
)
from canon.pipeline.rng import derive_rng
from canon.pipeline.runner import PipelineContext

CANON = [sys.executable, "-m", "canon.cli.main"]


def _ctx(tmp_path: Path, **config_kwargs) -> PipelineContext:
    return PipelineContext(
        bible=Bible.empty(seed="dag"),
        config=CanonConfig(seed="dag", output_dir=tmp_path, **config_kwargs),
        rng=random.Random(0),
    )


class RecorderPhase:
    """DagPhase producing an arbitrary node graph that records execution."""

    name = "recorder"

    def __init__(self, spec: dict[str, list[str]], body=None, gates=()) -> None:
        self.spec = spec
        self.body = body
        self.gates = set(gates)
        self.log: list[str] = []
        self.lock = threading.Lock()

    def expand(self, ctx) -> list[Node]:
        def make(node_id: str, requires: list[str]) -> Node:
            def run(_ctx, _nid=node_id) -> None:
                if self.body is not None:
                    self.body(_nid)
                with self.lock:
                    self.log.append(_nid)

            return Node(
                node_id=node_id, run=run, requires=requires,
                gate=node_id in self.gates,
            )

        return [make(nid, reqs) for nid, reqs in self.spec.items()]


DIAMOND = {"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"]}


class TestGraph:
    def test_topo_order_respected(self, tmp_path: Path) -> None:
        phase = RecorderPhase(DIAMOND)
        report = orchestrate([phase], _ctx(tmp_path))
        assert report.ok and set(report.done) == set(DIAMOND)
        order = {nid: i for i, nid in enumerate(phase.log)}
        for nid, requires in DIAMOND.items():
            for req in requires:
                assert order[req] < order[nid]

    def test_cycle_detection_names_the_cycle(self, tmp_path: Path) -> None:
        phase = RecorderPhase({"a": ["b"], "b": ["a"]})
        with pytest.raises(ValueError, match="Dependency cycle.*'a'.*'b'"):
            orchestrate([phase], _ctx(tmp_path))

    def test_legacy_phases_get_barrier_edges(self, tmp_path: Path) -> None:
        class Legacy:
            def __init__(self, name: str, log: list) -> None:
                self.name = name
                self._log = log

            def run(self, ctx) -> None:
                self._log.append(self.name)

        log: list[str] = []
        dag = RecorderPhase({"n1": [], "n2": []})
        dag.body = lambda nid: log.append(nid)
        nodes = build_nodes([Legacy("first", log), dag, Legacy("last", log)], _ctx(tmp_path))
        by_id = {n.node_id: n for n in nodes}
        # DagPhase nodes barrier on the preceding legacy phase...
        assert "phase:first" in by_id["n1"].requires
        assert "phase:first" in by_id["n2"].requires
        # ...and the following legacy phase barriers on ALL of them.
        assert set(by_id["phase:last"].requires) == {"n1", "n2"}

    def test_unknown_requires_treated_external(self, tmp_path: Path) -> None:
        phase = RecorderPhase({"a": ["tileset:s1"]})  # produced by no node
        report = orchestrate([phase], _ctx(tmp_path))
        assert report.done == ["a"]


class TestScheduling:
    def test_independent_nodes_overlap_when_cap_allows(self, tmp_path: Path) -> None:
        active = {"now": 0, "max": 0}
        lock = threading.Lock()

        def body(_nid: str) -> None:
            with lock:
                active["now"] += 1
                active["max"] = max(active["max"], active["now"])
            time.sleep(0.08)
            with lock:
                active["now"] -= 1

        phase = RecorderPhase({f"n{i}": [] for i in range(4)}, body=body)
        report = orchestrate([phase], _ctx(tmp_path), max_concurrency=4)
        assert report.ok
        assert active["max"] >= 2, "independent nodes never overlapped"

    def test_default_cap_is_sequential(self, tmp_path: Path) -> None:
        active = {"now": 0, "max": 0}
        lock = threading.Lock()

        def body(_nid: str) -> None:
            with lock:
                active["now"] += 1
                active["max"] = max(active["max"], active["now"])
            time.sleep(0.02)
            with lock:
                active["now"] -= 1

        phase = RecorderPhase({f"n{i}": [] for i in range(3)}, body=body)
        orchestrate([phase], _ctx(tmp_path))  # config default cap = 1
        assert active["max"] == 1

    def test_failure_isolates_descendants_only(self, tmp_path: Path) -> None:
        def body(nid: str) -> None:
            if nid == "b":
                raise RuntimeError("boom")

        phase = RecorderPhase(DIAMOND, body=body)
        ctx = _ctx(tmp_path)
        report = orchestrate([phase], ctx)
        assert report.escalated == ["b"]
        assert report.blocked == ["d"]  # descendant stays pending
        assert set(report.done) == {"a", "c"}  # unrelated branch completes
        assert ctx.bible.metadata.node_status["b"] is ArtifactStatus.ESCALATED

    def test_resume_skips_done_nodes(self, tmp_path: Path) -> None:
        runs: dict[str, int] = {}
        flag = {"fail": True}

        def body(nid: str) -> None:
            runs[nid] = runs.get(nid, 0) + 1
            if nid == "b" and flag["fail"]:
                raise RuntimeError("boom")

        ctx = _ctx(tmp_path)
        orchestrate([RecorderPhase(DIAMOND, body=body)], ctx)
        flag["fail"] = False
        report = orchestrate([RecorderPhase(DIAMOND, body=body)], ctx)
        assert report.ok
        assert set(report.skipped) == {"a", "c"}
        assert runs == {"a": 1, "c": 1, "b": 2, "d": 1}

    def test_per_node_rng_independent_of_cap(self, tmp_path: Path) -> None:
        """Same seed + graph -> same per-node streams at cap 1 and cap 4
        (nodes derive their rng from (seed, node_id), never share)."""

        def run_once(cap: int) -> dict[str, float]:
            values: dict[str, float] = {}
            lock = threading.Lock()

            def body(nid: str) -> None:
                value = derive_rng("dag", nid).random()
                with lock:
                    values[nid] = value

            phase = RecorderPhase({f"n{i}": [] for i in range(6)}, body=body)
            orchestrate([phase], _ctx(tmp_path / f"cap{cap}"), max_concurrency=cap)
            return values

        assert run_once(1) == run_once(4)


class TestEditSemantics:
    def test_user_edited_node_is_never_rerun(self, tmp_path: Path) -> None:
        """§6.3: the edit is authoritative. A node whose status is
        USER_EDITED is skipped as complete (its output satisfies
        dependents as-is); only STALE descendants re-run."""
        runs: dict[str, int] = {}
        ctx = _ctx(tmp_path)
        orchestrate(
            [RecorderPhase(DIAMOND, body=lambda n: runs.__setitem__(n, runs.get(n, 0) + 1))],
            ctx,
        )
        # Simulate detect_edits: b edited by the user, d stale behind it.
        ctx.bible.metadata.node_status["b"] = ArtifactStatus.USER_EDITED
        ctx.bible.metadata.node_status["d"] = ArtifactStatus.STALE
        report = orchestrate(
            [RecorderPhase(DIAMOND, body=lambda n: runs.__setitem__(n, runs.get(n, 0) + 1))],
            ctx,
        )
        assert report.ok
        assert "b" in report.skipped  # edited: skipped, NOT regenerated
        assert report.done == ["d"]  # stale descendant re-ran
        assert runs == {"a": 1, "b": 1, "c": 1, "d": 2}
        assert ctx.bible.metadata.node_status["d"] is ArtifactStatus.DONE
        # The user_edited mark survives as the broken-provenance record.
        assert (
            ctx.bible.metadata.node_status["b"] is ArtifactStatus.USER_EDITED
        )

    def test_always_node_reruns_every_orchestration(self, tmp_path: Path) -> None:
        runs: dict[str, int] = {}

        class AlwaysPhase:
            name = "always"

            def expand(self, ctx) -> list[Node]:
                def body(_ctx, _nid="derived") -> None:
                    runs[_nid] = runs.get(_nid, 0) + 1

                def base(_ctx) -> None:
                    runs["base"] = runs.get("base", 0) + 1

                return [
                    Node(node_id="base", run=base),
                    Node(
                        node_id="derived", run=body, requires=["base"],
                        always=True,
                    ),
                ]

        ctx = _ctx(tmp_path)
        orchestrate([AlwaysPhase()], ctx)
        report = orchestrate([AlwaysPhase()], ctx)
        assert report.skipped == ["base"]
        assert report.done == ["derived"]
        assert runs == {"base": 1, "derived": 2}

    def test_stale_owned_artifact_reruns_legacy_node(self, tmp_path: Path) -> None:
        """A legacy phase node is keyed phase:<name>, but the stale
        cascade marks the ENTITY ids it stamps (e.g. tileset:<stage>).
        An `owns(ctx)` hook maps those marks back to the node: owned-id
        STALE forces a re-run, and completion clears the mark to DONE."""
        runs: dict[str, int] = {}

        class OwningPhase:
            name = "maker"

            def run(self, ctx) -> None:
                runs["maker"] = runs.get("maker", 0) + 1

            def owns(self, ctx) -> list[str]:
                return ["tileset:s1"]

        ctx = _ctx(tmp_path)
        orchestrate([OwningPhase()], ctx)
        report = orchestrate([OwningPhase()], ctx)
        assert report.skipped == ["phase:maker"]  # DONE, nothing stale

        # Simulate mark_stale cascading to the owned entity id.
        ctx.bible.metadata.node_status["tileset:s1"] = ArtifactStatus.STALE
        report = orchestrate([OwningPhase()], ctx)
        assert report.done == ["phase:maker"]
        assert runs == {"maker": 2}
        # Cleared on completion — no infinite re-run on the next pass.
        assert (
            ctx.bible.metadata.node_status["tileset:s1"] is ArtifactStatus.DONE
        )
        report = orchestrate([OwningPhase()], ctx)
        assert report.skipped == ["phase:maker"]


class TestGates:
    def test_gate_pauses_cleanly_when_not_auto(self, tmp_path: Path) -> None:
        phase = RecorderPhase(DIAMOND, gates=("b",))
        ctx = _ctx(tmp_path, gates_auto_approve=False)
        report = orchestrate([phase], ctx)
        assert report.paused_at == "b"
        assert "d" not in report.done  # nothing behind the gate ran
        assert (
            ctx.bible.metadata.node_status["b"] is ArtifactStatus.AWAITING_REVIEW
        )
        # Approval = mark the node done (Cradle's future job), then resume.
        ctx.bible.metadata.node_status["b"] = ArtifactStatus.DONE
        report2 = orchestrate([RecorderPhase(DIAMOND, gates=("b",))], ctx)
        assert report2.ok and "d" in report2.done

    def test_gate_auto_approves_by_default(self, tmp_path: Path) -> None:
        phase = RecorderPhase(DIAMOND, gates=("b",))
        report = orchestrate([phase], _ctx(tmp_path))
        assert report.ok and set(report.done) == set(DIAMOND)


class TestProtocol:
    def test_dag_phase_protocol(self) -> None:
        assert isinstance(RecorderPhase({}), DagPhase)

        class Legacy:
            name = "x"

            def run(self, ctx) -> None: ...

        assert not isinstance(Legacy(), DagPhase)


# ---------------------------------------------------------------------------
# CLI verbs + crash-resume (subprocess)
# ---------------------------------------------------------------------------


def _run_cli(args: list[str], env: dict) -> tuple[int, object]:
    import os

    result = subprocess.run(
        CANON + args, capture_output=True, text=True,
        env={**os.environ, **env},
    )
    out = result.stdout.strip()
    try:
        return result.returncode, json.loads(out) if out else None
    except json.JSONDecodeError:
        return result.returncode, out


class TestCli:
    def _bible(self, tmp_path: Path) -> Path:
        path = tmp_path / "bible.json"
        Bible.empty(seed="dag").persist(path)
        return path

    def test_run_then_status(self, tmp_path: Path) -> None:
        bible = self._bible(tmp_path)
        env = {"CANON_DAG_OUT": str(tmp_path / "out")}
        code, payload = _run_cli(
            [
                "run", str(bible),
                "--pipeline", "tests.dag_fixture:ctx_factory",
                "--phases", "tests.dag_fixture:phases_factory",
            ],
            env,
        )
        assert code == 0, payload
        assert payload["report"]["done"] == ["a", "b", "c", "d"]

        code, payload = _run_cli(["status", str(bible)], {})
        assert code == 0
        assert payload["node_counts"] == {"done": 4}
        assert payload["attention"] == []

    def test_failure_reports_and_resume_completes(self, tmp_path: Path) -> None:
        bible = self._bible(tmp_path)
        out = tmp_path / "out"
        env = {"CANON_DAG_OUT": str(out)}
        code, payload = _run_cli(
            [
                "run", str(bible),
                "--pipeline", "tests.dag_fixture:ctx_factory",
                "--phases", "tests.dag_fixture:phases_factory",
            ],
            {**env, "CANON_DAG_FAIL_B": "1"},
        )
        assert code == 3  # incomplete
        assert payload["report"]["escalated"] == ["b"]
        assert payload["report"]["blocked"] == ["d"]

        code, payload = _run_cli(["status", str(bible)], {})
        assert "b" in payload["attention"]

        code, payload = _run_cli(
            [
                "resume", str(bible),
                "--pipeline", "tests.dag_fixture:ctx_factory",
                "--phases", "tests.dag_fixture:phases_factory",
            ],
            env,
        )
        assert code == 0, payload
        assert set(payload["report"]["skipped"]) == {"a", "c"}
        # a and c ran exactly once across both runs; b twice (fail + retry).
        assert (out / "a.count").read_text() == "1"
        assert (out / "c.count").read_text() == "1"
        assert (out / "b.count").read_text() == "2"
        assert (out / "d.count").read_text() == "1"

    def test_hard_kill_then_resume(self, tmp_path: Path) -> None:
        """Acceptance: kill -9 mid-run + resume completes the graph without
        re-executing committed nodes (Bible persisted after every node)."""
        bible = self._bible(tmp_path)
        out = tmp_path / "out"
        env = {"CANON_DAG_OUT": str(out)}
        code, _payload = _run_cli(
            [
                "run", str(bible),
                "--pipeline", "tests.dag_fixture:ctx_factory",
                "--phases", "tests.dag_fixture:phases_factory",
            ],
            {**env, "CANON_DAG_KILL_B": "1"},
        )
        assert code == 9  # process died mid-node

        code, payload = _run_cli(
            [
                "resume", str(bible),
                "--pipeline", "tests.dag_fixture:ctx_factory",
                "--phases", "tests.dag_fixture:phases_factory",
            ],
            env,
        )
        assert code == 0, payload
        assert "a" in payload["report"]["skipped"]  # committed before the kill
        assert (out / "a.count").read_text() == "1"
        assert (out / "d.count").read_text() == "1"
