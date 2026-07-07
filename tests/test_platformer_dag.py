"""Phase 3b-2: the platformer pack through the DAG orchestrator.

Covers the pack→DagPhase conversion contract: an orchestrated run is
byte-identical to the sequential pipeline; re-runs are idempotent (only
``always`` nodes re-derive); a hand-edited layer file triggers per-step
regeneration of exactly its §6.1 descendants with the edit preserved; and
the `canon resume` CLI drives the same flow from a subprocess.
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("PIL")

from canon.backends.testing import FakeLLMBackend  # noqa: E402
from canon.bible.models import Bible  # noqa: E402
from canon.config import CanonConfig  # noqa: E402
from canon.llm.client import LLMClient  # noqa: E402
from canon.pipeline.orchestrator import detect_edits  # noqa: E402
from canon.pipeline.runner import PipelineContext, run_pipeline  # noqa: E402
from examples.platformer_pack import PlatformerPrompts, compose_pipeline  # noqa: E402
from examples.platformer_pack.dag import run_orchestrated  # noqa: E402
from examples.run_platformer_slice import make_fake_responder  # noqa: E402

CANON = [sys.executable, "-m", "canon.cli.main"]
SEED = "emberfall_001"


def _ctx(output_dir: Path, bible: Bible | None = None) -> PipelineContext:
    return PipelineContext(
        bible=bible if bible is not None else Bible.empty(seed=SEED),
        config=CanonConfig(seed=SEED, output_dir=output_dir),
        rng=random.Random(SEED),
        llm=LLMClient(FakeLLMBackend(make_fake_responder())),
        prompts=PlatformerPrompts(),
    )


def _orchestrate_fresh(output_dir: Path):
    ctx = _ctx(output_dir)
    report = run_orchestrated(ctx, persist_path=output_dir / "bible.json")
    return ctx, report


def _resume(output_dir: Path):
    """The runner's resume path: reload the Bible, detect edits, re-run."""
    bible = Bible.load(output_dir / "bible.json")
    ctx = _ctx(output_dir, bible=bible)
    edits = detect_edits(bible, output_dir)
    report = run_orchestrated(ctx, persist_path=output_dir / "bible.json")
    return ctx, edits, report


def _tree(root: Path, exclude: tuple[str, ...] = ("bible.json",)) -> list[Path]:
    return sorted(
        p.relative_to(root)
        for p in root.rglob("*")
        if p.is_file() and p.name not in exclude
    )


class TestOrchestratedGeneration:
    def test_matches_sequential_pipeline_byte_for_byte(
        self, tmp_path: Path
    ) -> None:
        """One body, two schedulers: at cap 1 the DAG run writes the exact
        tree the sequential pipeline writes (bible.json aside — the
        orchestrator's state file, which the sequential path lacks)."""
        seq, orch = tmp_path / "seq", tmp_path / "orch"
        run_pipeline(compose_pipeline(), _ctx(seq))
        _orchestrate_fresh(orch)

        files_seq, files_orch = _tree(seq), _tree(orch)
        assert files_seq == files_orch
        for rel in files_seq:
            assert (seq / rel).read_bytes() == (orch / rel).read_bytes(), rel

    def test_node_graph_shape(self, tmp_path: Path) -> None:
        """Node ids are the §6.1 step artifact ids — that identity is what
        lets detect_edits stale-marks make nodes schedulable."""
        ctx, report = _orchestrate_fresh(tmp_path / "run")
        done = set(report.done)
        for lid in ("l1", "l2", "l3"):
            for step in (
                "collision", "hazards", "triggers", "terrain",
                "background", "entities", "foreground", "level",
            ):
                assert f"level:ashen_depths/{lid}/{step}" in done
            assert f"review:ashen_depths/{lid}" in done
        assert "review:ashen_depths/legend" in done
        assert "plat:manifest" in done
        assert not report.escalated and not report.blocked

    def test_rerun_is_idempotent_except_always_nodes(
        self, tmp_path: Path
    ) -> None:
        run = tmp_path / "run"
        _orchestrate_fresh(run)
        _ctx2, edits, report = _resume(run)
        assert not edits.user_edited and not edits.stale
        # Only the cheap always-fresh derivations re-ran.
        assert sorted(report.done) == [
            "plat:manifest",
            "review:ashen_depths/l1",
            "review:ashen_depths/l2",
            "review:ashen_depths/l3",
            "review:ashen_depths/legend",
        ]
        assert not report.escalated

    def test_bible_round_trips_node_state(self, tmp_path: Path) -> None:
        run = tmp_path / "run"
        _orchestrate_fresh(run)
        bible = Bible.load(run / "bible.json")
        status = {str(v) for v in bible.metadata.node_status.values()}
        assert status == {"done"}
        assert "l2" in bible.levels and bible.levels["l2"].brief


class TestPerStepRegen:
    """The workload the Phase 2 orchestrator was built to schedule."""

    def _edit_l2_collision(self, run: Path) -> None:
        target = run / "level/ashen_depths/l2/collision.npz"
        with np.load(target) as data:
            grid = data["collision"].copy()
        grid[14, 5:8] = 0  # the user carves a gap in the floor
        np.savez(target, collision=grid)

    def test_edit_regenerates_exactly_the_stale_steps(
        self, tmp_path: Path
    ) -> None:
        run = tmp_path / "run"
        _orchestrate_fresh(run)
        self._edit_l2_collision(run)

        _ctx2, edits, report = _resume(run)
        assert edits.user_edited == ["level:ashen_depths/l2/collision"]

        prefix = "level:ashen_depths/l2"
        regenerated = {nid for nid in report.done if nid.startswith("level:")}
        assert regenerated == {
            f"{prefix}/{step}"
            for step in (
                "hazards", "triggers", "terrain", "background",
                "entities", "foreground", "level",
            )
        }
        # The edited artifact itself is authoritative: skipped, not re-run.
        assert f"{prefix}/collision" in report.skipped
        # Sibling levels untouched.
        assert not any("l1" in nid or "l3" in nid for nid in regenerated)

    def test_edit_is_preserved_and_flows_downstream(
        self, tmp_path: Path
    ) -> None:
        run = tmp_path / "run"
        _orchestrate_fresh(run)
        with np.load(run / "level/ashen_depths/l2/terrain.npz") as data:
            terrain_before = data["terrain"].copy()
        self._edit_l2_collision(run)
        _resume(run)

        # The user's grid survives byte-for-byte in content terms...
        with np.load(run / "level/ashen_depths/l2/collision.npz") as data:
            assert (data["collision"][14, 5:8] == 0).all()
        # ...and the regenerated terrain reflects it (reads from disk).
        with np.load(run / "level/ashen_depths/l2/terrain.npz") as data:
            terrain = data["terrain"]
        assert not (terrain == terrain_before).all()
        assert (terrain[14, 5:8] == 0).all()

    def test_third_run_is_clean_after_adoption(self, tmp_path: Path) -> None:
        """detect_edits adopts the on-disk hash — the same edit must not
        re-cascade forever."""
        run = tmp_path / "run"
        _orchestrate_fresh(run)
        self._edit_l2_collision(run)
        _resume(run)
        _ctx3, edits, report = _resume(run)
        assert not edits.user_edited and not edits.stale
        assert not any(nid.startswith("level:") for nid in report.done)
        # The broken-provenance record survives in the Bible.
        assert (
            str(_ctx3.bible.metadata.node_status["level:ashen_depths/l2/collision"])
            == "user_edited"
        )

    def test_level_manifest_refreshed_with_adopted_hash(
        self, tmp_path: Path
    ) -> None:
        """level.json descends from every layer step, so the regen rewrites
        it carrying the ADOPTED collision hash — consumers stay coherent."""
        import hashlib

        run = tmp_path / "run"
        _orchestrate_fresh(run)
        self._edit_l2_collision(run)
        _resume(run)

        level_doc = json.loads(
            (run / "level/ashen_depths/l2/level.json").read_text()
        )
        disk = (run / "level/ashen_depths/l2/collision.npz").read_bytes()
        assert level_doc["collision_hash"] == (
            "sha256:" + hashlib.sha256(disk).hexdigest()
        )


class TestCliRegen:
    def test_canon_resume_drives_per_step_regen(self, tmp_path: Path) -> None:
        """The user story end-to-end through the CLI: generate (in-process),
        hand-edit a layer, `canon resume` regenerates just its level."""
        run = tmp_path / "run"
        _orchestrate_fresh(run)
        target = run / "level/ashen_depths/l2/collision.npz"
        with np.load(target) as data:
            grid = data["collision"].copy()
        grid[14, 5:8] = 0
        np.savez(target, collision=grid)

        result = subprocess.run(
            CANON + [
                "resume", str(run / "bible.json"),
                "--pipeline", "examples.platformer_pack.dag:cli_ctx_factory",
                "--phases", "examples.platformer_pack.dag:cli_phases_factory",
            ],
            capture_output=True, text=True,
            env={**os.environ, "CANON_PLAT_OUT": str(run), "CANON_PLAT_SEED": SEED},
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["edit_detection"]["user_edited"] == [
            "level:ashen_depths/l2/collision"
        ]
        regenerated = [
            nid for nid in payload["report"]["done"] if nid.startswith("level:")
        ]
        assert regenerated and all("/l2/" in nid for nid in regenerated)
        assert "level:ashen_depths/l2/collision" in payload["report"]["skipped"]
