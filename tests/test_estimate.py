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
from canon.packs.platformer.dag import run_orchestrated
from canon.packs.platformer.estimate import (
    _actuals_by_task,
    _sections_for_level,
    _task_calls,
    estimate_run,
)
from canon.packs.platformer.prompts import PlatformerPrompts
from canon.packs.platformer.run_slice import make_fake_responder
from canon.pipeline.orchestrator import Node, initial_skips
from canon.pipeline.runner import PipelineContext

CANON = [sys.executable, "-m", "canon.cli.main"]
PIPELINE = "canon.packs.platformer.dag:cli_ctx_factory"
PHASES = "canon.packs.platformer.dag:cli_phases_factory"
ESTIMATOR = "canon.packs.platformer.estimate:estimate_run"


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

    def test_world_art_node_counts_one_splash_image(self) -> None:
        """Counts only — the engine (canon.estimator) prices them through
        canon.pricing; cost_model.json carries no dollar (row P0-7)."""
        from canon.packs.platformer.estimate import _asset_counts

        bible = _StubBible(stages=1, enemies=0)
        cost_model = {"assets": {"images_world": 1}}
        counted = _asset_counts([_node("phase:plat:world_art")], bible, cost_model)
        assert counted["images"] == 1
        assert _asset_counts([], bible, cost_model)["images"] == 0

    def test_cost_model_carries_no_price(self) -> None:
        """§3.0-C: the data file keeps counts/tokens only; every dollar is
        canon.pricing's."""
        from canon.packs.platformer.estimate import load_cost_model

        assets = load_cost_model()["assets"]
        assert not [k for k in assets if "usd" in k], assets

    def test_fresh_mode_prices_the_fresh_plan(self, tmp_path: Path) -> None:
        ctx = PipelineContext(
            bible=Bible.empty(seed="est"),
            config=CanonConfig(seed="est", output_dir=tmp_path),
            rng=random.Random(0),
        )
        result = estimate_run(ctx, [], Bible.empty(seed="est"))
        assert result["mode"] == "fresh"
        assert result["calibration"] == "defaults"
        # fresh_plan defaults: 3 stages x 3 levels, 7 enemies -> ~70
        # calls measured pre-rooms, plus ~5 expected SECRET ROOMS
        # (secret_rooms_avg 0.6/level) each priced as a small level
        # (multi-room arc) -> ~90.
        assert 70 <= result["llm"]["calls"] <= 115
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


class TestAnimateScope:
    """The `animate` scope prices ONE actor's animation run."""

    def _enemy_id(self, out: Path) -> str:
        return sorted(p.stem for p in (out / "enemy").glob("*.json"))[0]

    def test_estimate_animate_prices_by_states_not_frames(
        self, tmp_path: Path
    ) -> None:
        """PRICED BY STATES, NOT FRAMES — the whole point of this test.

        `_sheet_frames` (art_phases.py) issues exactly ONE
        ImageEditBackend.edit() per state per facing; the frame count only
        widens the reference sheet passed to that single call. The intuitive
        `states x frames` formula therefore over-charges roughly 4x. If you
        are here because you "fixed" the estimator to multiply by frames,
        re-read `_animate_actor`: the frame loop is INSIDE one edit().
        """
        from canon.packs.platformer.estimate import estimate_cradle
        from canon.packs.platformer.ops import (
            _animate_actor_spec,
            _sprite_bible,
            load_pack,
        )

        out = tmp_path / "game"
        _build_tree(out)
        eid = self._enemy_id(out)

        est = estimate_cradle(
            "animate", pack_dir=out, target=f"enemy:{eid}",
            backends={"image": "fal", "vlm": "anthropic"},
        )
        spec_in = _animate_actor_spec(_sprite_bible(load_pack(out), "enemy", eid),
                                      "enemy", eid)
        facings = 2 if spec_in.asymmetric else 1
        assert est["assets"]["images"]["count"] == len(spec_in.states) * facings

        # Inflating the stored spec's frame counts must NOT move the price.
        row = json.loads((out / "enemy" / f"{eid}.json").read_text())
        row.setdefault("stats", {})["animation"] = {
            "spec": {s: {"frames": 6, "motion": "x"} for s in spec_in.states}
        }
        (out / "enemy" / f"{eid}.json").write_text(json.dumps(row))
        after = estimate_cradle(
            "animate", pack_dir=out, target=f"enemy:{eid}",
            backends={"image": "fal", "vlm": "anthropic"},
        )
        assert after["assets"]["images"] == est["assets"]["images"]
        assert after["total_usd"] == est["total_usd"]

    def test_reuse_spec_drops_the_vlm_authoring_call(self, tmp_path: Path) -> None:
        from canon.packs.platformer.estimate import estimate_cradle

        out = tmp_path / "game"
        _build_tree(out)
        target = f"enemy:{self._enemy_id(out)}"
        backends = {"image": "fal", "vlm": "anthropic"}

        fresh = estimate_cradle("animate", pack_dir=out, target=target,
                                backends=backends)
        reused = estimate_cradle("animate", pack_dir=out, target=target,
                                 backends=backends, reuse_spec=True)
        assert fresh["assets"]["vlm"]["animation_authoring"] == 1
        assert reused["assets"]["vlm"] == {}
        # Same images either way; only the vision call goes away.
        assert reused["assets"]["images"] == fresh["assets"]["images"]
        assert reused["total_usd"]["best"] < fresh["total_usd"]["best"]

    def test_unpaid_backends_zero_the_usd_but_keep_the_counts(
        self, tmp_path: Path
    ) -> None:
        """The "what an upgrade costs" UX: fake/none price at $0 with the
        image count still visible."""
        from canon.packs.platformer.estimate import estimate_cradle

        out = tmp_path / "game"
        _build_tree(out)
        est = estimate_cradle(
            "animate", pack_dir=out, target=f"enemy:{self._enemy_id(out)}",
            backends={"image": "fake", "vlm": "none"},
        )
        assert est["total_usd"] == {"best": 0.0, "worst": 0.0}
        assert est["assets"]["images"]["count"] > 0
        assert est["assets"]["images"]["usd"] == 0.0


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
