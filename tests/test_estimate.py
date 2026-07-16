"""Tests for `canon estimate` (PRD §9.2) — the initial_skips seam it
shares with orchestrate, the pack estimator's counting/pricing, and the
CLI verb's no-mutation contract."""

from __future__ import annotations

import copy
import json
import os
import random
import subprocess
import sys
from pathlib import Path

from canon.backends.testing import FakeLLMBackend
from canon.bible.artifacts import ArtifactStatus
from canon.bible.models import Bible
from canon.config import CanonConfig
from canon.llm.client import LLMClient
from canon.pipeline.orchestrator import Node, initial_skips
from canon.pipeline.runner import PipelineContext
from examples.platformer_pack.dag import run_orchestrated
from examples.platformer_pack.estimate import (
    _actuals_by_task,
    _sections_for_level,
    _task_calls,
    estimate_run,
)
from examples.platformer_pack.prompts import PlatformerPrompts
from examples.run_platformer_slice import make_fake_responder

CANON = [sys.executable, "-m", "canon.cli.main"]
PIPELINE = "examples.platformer_pack.dag:cli_ctx_factory"
PHASES = "examples.platformer_pack.dag:cli_phases_factory"
ESTIMATOR = "examples.platformer_pack.estimate:estimate_run"


def _node(nid: str, always: bool = False, owns: tuple = ()) -> Node:
    return Node(
        node_id=nid, run=lambda ctx: None, requires=[], always=always,
        owns=list(owns),
    )


class TestInitialSkips:
    def test_reasons(self) -> None:
        node_map = {
            "pinned": _node("pinned"),
            "edited": _node("edited"),
            "done": _node("done"),
            "stale": _node("stale"),
            "fresh": _node("fresh"),
            "always": _node("always", always=True),
        }
        status = {
            "edited": ArtifactStatus.USER_EDITED,
            "done": ArtifactStatus.DONE,
            "stale": ArtifactStatus.STALE,
            "always": ArtifactStatus.DONE,
        }
        skips = initial_skips(node_map, status, pinned={"pinned"})
        assert skips == {
            "pinned": "pinned", "edited": "user_edited", "done": "done",
        }

    def test_owns_staleness_reschedules(self) -> None:
        node_map = {"n": _node("n", owns=("owned:a",))}
        status = {
            "n": ArtifactStatus.DONE,
            "owned:a": ArtifactStatus.STALE,
        }
        assert initial_skips(node_map, status, pinned=set()) == {}


class _StubLevel:
    def __init__(self, width: int, height: int = 16, axis: str = "horizontal"):
        self.grid_width = width
        self.grid_height = height
        self.layout_axis = axis


class _StubBible:
    def __init__(self, stages=2, enemies=3, levels=None):
        self.stages = {f"s{i}": None for i in range(stages)}
        self.enemy_definitions = {f"e{i}": None for i in range(enemies)}
        self.levels = levels or {}


class TestEstimatorCounting:
    def test_sections_from_level_dims(self) -> None:
        bible = _StubBible(levels={
            "l1": _StubLevel(48),
            "l9": _StubLevel(26, 96, axis="vertical"),
        })
        assert _sections_for_level(bible, "l1", 4.0) == 2  # 48/20
        assert _sections_for_level(bible, "l9", 4.0) == 5  # 96/20 capped
        assert _sections_for_level(bible, "missing", 4.0) == 4.0

    def test_task_calls_per_node_family(self) -> None:
        bible = _StubBible(stages=2, enemies=3, levels={"l1": _StubLevel(48)})
        nodes = [
            _node("phase:plat:world"),
            _node("phase:plat:stage"),
            _node("phase:plat:enemies"),
            _node("phase:plat:style"),
            _node("level:s0/l1/collision"),
            _node("level:s0/l1/entities"),
            _node("level:s0/l1/foreground"),
            _node("level:s0/l1/terrain"),  # zero-LLM step
            _node("review:s0/l1"),
        ]
        calls = _task_calls(nodes, bible, {"sections_per_level_avg": 4.0})
        assert calls == {
            "plat:world": 1, "plat:stage": 2, "plat:enemies": 3,
            "plat:style": 2, "plat:layout": 2.0, "plat:placement": 1,
            "plat:decorator": 1,
        }

    def test_actuals_calibration(self, tmp_path: Path) -> None:
        (tmp_path / "generation_stats.json").write_text(json.dumps({
            "by_phase": {
                "plat:layout:l1:s0": {
                    "calls": 2, "input_tokens": 4000, "output_tokens": 1000,
                },
                "plat:layout:l2:s0": {
                    "calls": 2, "input_tokens": 2000, "output_tokens": 600,
                },
                "plat:decorator:l1": {
                    "calls": 1, "input_tokens": 0, "output_tokens": 0,
                },
            }
        }))
        actuals = _actuals_by_task(tmp_path)
        assert actuals["plat:layout"] == {
            "input_tokens": 1500.0, "output_tokens": 400.0,
        }
        # Zero-token (fake) entries never calibrate.
        assert "plat:decorator" not in actuals

    def test_fresh_mode_prices_the_fresh_plan(self, tmp_path: Path) -> None:
        ctx = PipelineContext(
            bible=Bible.empty(seed="est"),
            config=CanonConfig(seed="est", output_dir=tmp_path),
            rng=random.Random(0),
        )
        result = estimate_run(ctx, [], Bible.empty(seed="est"))
        assert result["mode"] == "fresh"
        assert result["calibration"] == "defaults"
        # fresh_plan defaults: 3 stages x 3 levels, 7 enemies -> the
        # macro+level call volume of a full run (~70 measured).
        assert 50 <= result["llm"]["calls"] <= 90
        assert result["total_usd"]["worst"] > result["total_usd"]["best"] > 0
        assert result["assets"]["images"]["count"] > 0


def _build_tree(output_dir: Path) -> Path:
    """A full fake tree + persisted bible via the CLI factories' shape."""
    bible_path = output_dir / "bible.json"
    ctx = PipelineContext(
        bible=Bible.empty(seed="emberfall_001"),
        config=CanonConfig(seed="emberfall_001", output_dir=output_dir),
        rng=random.Random("emberfall_001"),
        llm=LLMClient(FakeLLMBackend(make_fake_responder())),
        prompts=PlatformerPrompts(),
    )
    run_orchestrated(ctx, persist_path=bible_path)
    return bible_path


def _estimate(bible_path: Path, output_dir: Path, *targets: str) -> dict:
    env = dict(os.environ)
    env["CANON_PLAT_OUT"] = str(output_dir)
    env["CANON_PLAT_SEED"] = "emberfall_001"
    proc = subprocess.run(
        [*CANON, "estimate", str(bible_path), *targets,
         "--pipeline", PIPELINE, "--phases", PHASES,
         "--estimator", ESTIMATOR],
        capture_output=True, text=True, env=env,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


class TestEstimateCli:
    def test_completed_tree_prices_only_always_nodes(
        self, tmp_path: Path
    ) -> None:
        out = tmp_path / "game"
        bible_path = _build_tree(out)
        before = bible_path.read_bytes()

        payload = _estimate(bible_path, out)
        assert payload["result"] == "estimate"
        assert payload["nodes"]["to_run"] < payload["nodes"]["total"]
        assert payload["estimate"]["mode"] == "tree"
        assert payload["estimate"]["llm"]["calls"] == 0  # nothing stale

        assert bible_path.read_bytes() == before, "estimate must not write"

    def test_targeted_estimate_prices_subgraph_without_marking(
        self, tmp_path: Path
    ) -> None:
        out = tmp_path / "game"
        bible_path = _build_tree(out)
        before = bible_path.read_bytes()

        payload = _estimate(bible_path, out, "l2")
        by_task = payload["estimate"]["llm"]["by_task"]
        assert "plat:layout" in by_task and "plat:placement" in by_task
        assert payload["estimate"]["total_usd"]["worst"] > 0
        assert payload["regen"] is not None

        # The forecast marked staleness on a COPY: disk state untouched.
        assert bible_path.read_bytes() == before
        reloaded = Bible.load(bible_path)
        stale = [
            nid for nid, s in reloaded.metadata.node_status.items()
            if s is ArtifactStatus.STALE
        ]
        assert stale == []

    def test_hand_edit_is_forecast_like_a_run_would_see_it(
        self, tmp_path: Path
    ) -> None:
        """estimate runs detect_edits on its COPY: a hand-edited layer
        prices the stale descendants a real resume would execute — while
        the on-disk bible still carries no USER_EDITED/STALE marks."""
        out = tmp_path / "game"
        bible_path = _build_tree(out)
        before = bible_path.read_bytes()

        collision = next(out.glob("level/*/l2/collision.npz"))
        collision.write_bytes(collision.read_bytes() + b"edited")

        payload = _estimate(bible_path, out)
        assert payload["edit_detection"]["user_edited"], "edit undetected"
        by_task = payload["estimate"]["llm"]["by_task"]
        # collision's stale cascade reaches the LLM-priced siblings —
        # placement (entities) and decorator (foreground) re-run on a
        # real resume, so the forecast must price them.
        assert "plat:placement" in by_task and "plat:decorator" in by_task, (
            by_task
        )

        assert bible_path.read_bytes() == before, "estimate must not write"

    def test_estimate_is_idempotent(self, tmp_path: Path) -> None:
        out = tmp_path / "game"
        bible_path = _build_tree(out)
        a = _estimate(bible_path, out, "l2")
        b = _estimate(bible_path, out, "l2")
        assert a == b


class TestEstimatorMutationSafety:
    def test_estimate_run_leaves_ctx_bible_alone(self, tmp_path: Path) -> None:
        bible = Bible.empty(seed="est")
        snapshot = copy.deepcopy(bible.metadata.node_status)
        ctx = PipelineContext(
            bible=bible,
            config=CanonConfig(seed="est", output_dir=tmp_path),
            rng=random.Random(0),
        )
        estimate_run(ctx, [], bible)
        assert bible.metadata.node_status == snapshot
