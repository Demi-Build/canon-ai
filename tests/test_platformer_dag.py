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


def _ctx(
    output_dir: Path, bible: Bible | None = None, responder=None
) -> PipelineContext:
    return PipelineContext(
        bible=bible if bible is not None else Bible.empty(seed=SEED),
        config=CanonConfig(seed=SEED, output_dir=output_dir),
        rng=random.Random(SEED),
        llm=LLMClient(FakeLLMBackend(responder or make_fake_responder())),
        prompts=PlatformerPrompts(),
    )


def _orchestrate_fresh(output_dir: Path, responder=None):
    ctx = _ctx(output_dir, responder=responder)
    report = run_orchestrated(ctx, persist_path=output_dir / "bible.json")
    return ctx, report


def _resume(output_dir: Path, responder=None):
    """The runner's resume path: reload the Bible, detect edits, re-run."""
    bible = Bible.load(output_dir / "bible.json")
    ctx = _ctx(output_dir, bible=bible, responder=responder)
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

    def _edit_l2_collision(self, run: Path) -> int:
        """Clear the pool cells on the ground row (dims are schema-rolled,
        so the row is computed, not hardcoded). Returns the edited row."""
        target = run / "level/ashen_depths/l2/collision.npz"
        with np.load(target) as data:
            grid = data["collision"].copy()
        ground = grid.shape[0] - 2
        grid[ground, 5:8] = 0  # the user carves a gap in the floor
        np.savez(target, collision=grid)
        return ground

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
        ground = self._edit_l2_collision(run)
        _resume(run)

        # The user's grid survives byte-for-byte in content terms...
        with np.load(run / "level/ashen_depths/l2/collision.npz") as data:
            assert (data["collision"][ground, 5:8] == 0).all()
        # ...and the regenerated terrain reflects it (reads from disk).
        with np.load(run / "level/ashen_depths/l2/terrain.npz") as data:
            terrain = data["terrain"]
        assert not (terrain == terrain_before).all()
        assert (terrain[ground, 5:8] == 0).all()

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


class TestRegenVerb:
    """`canon regen` (PRD §7.5): mark targets + §6.1 descendants stale,
    resume — only they re-run. Granularity: step, level, or phase node."""

    def test_mark_stale_resolves_and_cascades(self, tmp_path: Path) -> None:
        from canon.pipeline.orchestrator import mark_stale

        run = tmp_path / "run"
        ctx, _ = _orchestrate_fresh(run)
        plan = mark_stale(ctx.bible, ["level:ashen_depths/l2/collision"])
        # Descendants through step_parents — including the level manifest.
        for step in ("terrain", "background", "hazards", "triggers",
                     "entities", "foreground", "level"):
            assert f"level:ashen_depths/l2/{step}" in plan.marked, step
        assert not any("l1" in nid or "l3" in nid for nid in plan.marked)

    def test_mark_stale_level_shorthand(self, tmp_path: Path) -> None:
        from canon.pipeline.orchestrator import mark_stale

        run = tmp_path / "run"
        ctx, _ = _orchestrate_fresh(run)
        plan = mark_stale(ctx.bible, ["l2"])
        assert {nid for nid in plan.marked if nid.startswith("level:")} == {
            f"level:ashen_depths/l2/{step}"
            for step in ("collision", "hazards", "triggers", "terrain",
                         "background", "entities", "foreground", "level")
        }

    def test_mark_stale_unknown_target_names_levels(
        self, tmp_path: Path
    ) -> None:
        from canon.pipeline.orchestrator import mark_stale

        run = tmp_path / "run"
        ctx, _ = _orchestrate_fresh(run)
        with pytest.raises(KeyError, match=r"unknown regen target.*'l1'"):
            mark_stale(ctx.bible, ["l9"])

    def test_cascade_never_destroys_user_edits(self, tmp_path: Path) -> None:
        """Cascaded descendants keep user_edited; only an EXPLICIT target
        overrides an edit (asking to regen it = discarding it)."""
        from canon.bible.artifacts import ArtifactStatus
        from canon.pipeline.orchestrator import mark_stale

        run = tmp_path / "run"
        ctx, _ = _orchestrate_fresh(run)
        entities_id = "level:ashen_depths/l2/entities"
        ctx.bible.metadata.node_status[entities_id] = ArtifactStatus.USER_EDITED

        plan = mark_stale(ctx.bible, ["level:ashen_depths/l2/collision"])
        assert entities_id in plan.kept_user_edited
        assert (
            ctx.bible.metadata.node_status[entities_id]
            is ArtifactStatus.USER_EDITED
        )
        # Explicit target: the edit is deliberately discarded.
        plan = mark_stale(ctx.bible, [entities_id])
        assert entities_id in plan.marked

    def test_regen_level_keeps_brief_on_resume(self, tmp_path: Path) -> None:
        """Level.brief is persisted precisely so a surgical regen prompts
        with the original stage-planning context; the resume path must
        read it back rather than regenerate against an empty brief (the
        stage phase that fills ctx.artifacts['level_briefs'] is skipped)."""
        from canon.pipeline.orchestrator import mark_stale

        run = tmp_path / "run"
        ctx, _ = _orchestrate_fresh(run)
        original = ctx.bible.levels["l2"].brief
        assert original  # fresh runs carry the stage-planned brief

        bible = Bible.load(run / "bible.json")
        mark_stale(bible, ["l2"])
        bible.persist(run / "bible.json")

        ctx2, _edits, report = _resume(run)
        assert "level:ashen_depths/l2/collision" in report.done
        assert ctx2.bible.levels["l2"].brief == original

    def test_regen_level_keeps_view_on_resume(self, tmp_path: Path) -> None:
        """The per-level framing override is a stage-plan decision — a
        surgical level regen (stage phase skipped) must keep it, exactly
        like the brief."""
        from canon.pipeline.orchestrator import mark_stale

        run = tmp_path / "run"
        ctx, _ = _orchestrate_fresh(run)
        assert ctx.bible.levels["l3"].view_cells == 30  # canned vista finale

        bible = Bible.load(run / "bible.json")
        mark_stale(bible, ["l3"])
        bible.persist(run / "bible.json")

        ctx2, _edits, _report = _resume(run)
        assert ctx2.bible.levels["l3"].view_cells == 30

    def test_fallback_notice_survives_resume(self, tmp_path: Path) -> None:
        """plat:manifest is an always node: it rebuilds warnings every
        resume, which used to ERASE the fallback record while the level
        stayed fallback content. The notice must re-derive from the Bible."""
        good = make_fake_responder()

        def broken_layouts(request):
            if "### TASK: layout" in request.user_message:
                return "not a dsl at all"
            return good(request)

        run = tmp_path / "run"
        _orchestrate_fresh(run, responder=broken_layouts)
        manifest = json.loads((run / "manifest.json").read_text())
        assert any("FALLBACK" in w for w in manifest["warnings"])

        # A clean no-op resume (good responder, nothing marked) rebuilds
        # the manifest; the fallback notice must still be there.
        _resume(run)
        manifest = json.loads((run / "manifest.json").read_text())
        fallback_notes = [w for w in manifest["warnings"] if "FALLBACK" in w]
        assert len(fallback_notes) == 3  # one per fallback level, no dupes
        assert all("_layout_attempts.json" in w for w in fallback_notes)

    def test_regen_step_reruns_exactly_that_chain(self, tmp_path: Path) -> None:
        from canon.pipeline.orchestrator import mark_stale

        run = tmp_path / "run"
        _orchestrate_fresh(run)
        bible = Bible.load(run / "bible.json")
        mark_stale(bible, ["level:ashen_depths/l2/entities"])
        bible.persist(run / "bible.json")

        _ctx, _edits, report = _resume(run)
        level_nodes = {n for n in report.done if n.startswith("level:")}
        assert level_nodes == {
            "level:ashen_depths/l2/entities",
            "level:ashen_depths/l2/level",
        }

    def test_style_regen_cascades_to_tileset_and_terrain(
        self, tmp_path: Path
    ) -> None:
        """The tilesheet is style-seeded art (§6.1 edge on Tileset.parents),
        so a style regen must re-roll the sheet — and the terrain/background
        steps behind it — via the tileset node's `owns` mapping (the node is
        `phase:plat:tileset`; the cascade marks `tileset:<stage>`)."""
        from canon.pipeline.orchestrator import mark_stale

        run = tmp_path / "run"
        _orchestrate_fresh(run)
        bible = Bible.load(run / "bible.json")
        plan = mark_stale(bible, ["phase:plat:style"])
        assert "tileset:ashen_depths" in plan.marked
        bible.persist(run / "bible.json")

        _ctx, _edits, report = _resume(run)
        assert "phase:plat:style" in report.done
        assert "phase:plat:tileset" in report.done
        for lid in ("l1", "l2", "l3"):
            assert f"level:ashen_depths/{lid}/terrain" in report.done
        # Cleared on completion — the next pass is a no-op resume.
        _ctx, _edits, report = _resume(run)
        assert "phase:plat:tileset" not in report.done

    def test_tileset_only_regen_reads_style_seed_from_disk(
        self, tmp_path: Path
    ) -> None:
        """A tileset-only regen runs with fresh scratch artifacts (no
        in-process palette) — the phase must re-read the style artifact
        (style/<stage>/style.json), not fall back to placeholder colors."""
        from canon.pipeline.orchestrator import mark_stale

        run = tmp_path / "run"
        _orchestrate_fresh(run)
        sheet_rel = "tileset/ashen_depths/tilesheet.png"
        before = (run / sheet_rel).read_bytes()

        bible = Bible.load(run / "bible.json")
        mark_stale(bible, ["phase:plat:tileset"])
        bible.persist(run / "bible.json")
        _ctx, _edits, report = _resume(run)

        assert "phase:plat:tileset" in report.done
        assert "phase:plat:style" not in report.done  # style itself skipped
        assert (run / sheet_rel).read_bytes() == before, (
            "regenerated sheet must repaint the GENERATED palette from "
            "style.json — placeholder colors mean the seed was lost"
        )

    def test_regen_cli_verb(self, tmp_path: Path) -> None:
        run = tmp_path / "run"
        _orchestrate_fresh(run)
        result = subprocess.run(
            CANON + [
                "regen", str(run / "bible.json"), "l2",
                "--pipeline", "examples.platformer_pack.dag:cli_ctx_factory",
                "--phases", "examples.platformer_pack.dag:cli_phases_factory",
            ],
            capture_output=True, text=True,
            env={**os.environ, "CANON_PLAT_OUT": str(run), "CANON_PLAT_SEED": SEED},
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert (
            "level:ashen_depths/l2/collision" in payload["regen"]["marked"]
        )
        done_levels = [
            n for n in payload["report"]["done"] if n.startswith("level:")
        ]
        assert done_levels and all("/l2/" in n for n in done_levels)
        assert "level:ashen_depths/l1/collision" in payload["report"]["skipped"]

    def test_field_regen_rerolls_one_part_of_one_row(
        self, tmp_path: Path
    ) -> None:
        """Parts of rows: enemy:<id>#flavor re-rolls ONE field — name and
        mechanics locked, entity file rewritten, provenance re-stamped,
        nothing else in the Bible touched."""
        import hashlib

        from examples.platformer_pack.dag import regen_field

        run = tmp_path / "run"
        ctx, _ = _orchestrate_fresh(run)
        enemy = ctx.bible.enemy_definitions["cinder_beetle"]
        old_flavor = enemy.stats["flavor"]
        old_name = enemy.name
        old_provenance = enemy.provenance_hash

        result = regen_field(ctx, "enemy:cinder_beetle#flavor")
        assert result["changed"] and result["old"] == old_flavor
        assert enemy.stats["flavor"] != old_flavor
        assert enemy.name == old_name  # locked
        assert enemy.provenance_hash != old_provenance  # re-stamped
        # The entity FILE carries the new flavor (hash contract holds).
        doc = json.loads((run / "enemy/cinder_beetle.json").read_text())
        assert doc["stats"]["flavor"] == enemy.stats["flavor"]
        disk = (run / "enemy/cinder_beetle.json").read_bytes()
        assert enemy.provenance_hash != "sha256:" + hashlib.sha256(disk).hexdigest()  # provenance != content
        # Other enemies untouched.
        assert ctx.bible.enemy_definitions["ash_wraith"].stats["flavor"]

    def test_field_regen_unknown_field_names_supported(
        self, tmp_path: Path
    ) -> None:
        from examples.platformer_pack.dag import regen_field

        run = tmp_path / "run"
        ctx, _ = _orchestrate_fresh(run)
        with pytest.raises(KeyError, match=r"enemy:<id>#flavor"):
            regen_field(ctx, "enemy:cinder_beetle#portrait")
        with pytest.raises(KeyError, match="roster"):
            regen_field(ctx, "enemy:ghost#flavor")

    def test_field_regen_cli(self, tmp_path: Path) -> None:
        run = tmp_path / "run"
        _orchestrate_fresh(run)
        result = subprocess.run(
            CANON + [
                "regen", str(run / "bible.json"),
                "enemy:cinder_beetle#flavor",
                "--pipeline", "examples.platformer_pack.dag:cli_ctx_factory",
                "--field-ops", "examples.platformer_pack.dag:regen_field",
            ],
            capture_output=True, text=True,
            env={**os.environ, "CANON_PLAT_OUT": str(run), "CANON_PLAT_SEED": SEED},
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["fields"][0]["changed"] is True
        assert "regen winds" in payload["fields"][0]["new"]
        # Persisted: both the Bible and the entity file.
        bible = Bible.load(run / "bible.json")
        assert "regen winds" in bible.enemy_definitions["cinder_beetle"].stats["flavor"]

    def test_regen_mark_only(self, tmp_path: Path) -> None:
        run = tmp_path / "run"
        _orchestrate_fresh(run)
        result = subprocess.run(
            CANON + [
                "regen", str(run / "bible.json"),
                "level:ashen_depths/l3/foreground", "--mark-only",
            ],
            capture_output=True, text=True, env={**os.environ},
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["result"] == "marked"
        bible = Bible.load(run / "bible.json")
        assert (
            str(bible.metadata.node_status["level:ashen_depths/l3/foreground"])
            == "stale"
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
        grid[grid.shape[0] - 2, 5:8] = 0  # ground row — dims are rolled
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
