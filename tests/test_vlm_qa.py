"""VLM QA loop v1 — backends, code checks, verdict handling, report
shape, and the durable-warning contract (the layout_fallback pattern
applied to QA findings)."""

from __future__ import annotations

import io
import json
import random
from pathlib import Path
from types import SimpleNamespace

import pytest

from canon import CanonConfig, FakeLLMBackend, LLMClient, run_pipeline
from canon.backends.testing import FakeVLMBackend
from canon.bible.models import Bible
from canon.pipeline.runner import PipelineContext
from examples.platformer_pack import PlatformerPrompts, compose_pipeline
from examples.platformer_pack.vlm_qa import (
    DIMENSIONS,
    SPRITE_MIN_FILL,
    VlmQaPhase,
    _sanitize_verdict,
    _sprite_checks,
    build_vlm_judge,
    derive_qa_warnings,
    make_fake_vlm_responder,
    qa_report_rel,
    run_code_checks,
)
from examples.run_platformer_slice import make_fake_responder


def _run_slice(output_dir: Path, vlm_judge=None) -> PipelineContext:
    seed = "emberfall_001"
    ctx = PipelineContext(
        bible=Bible.empty(seed=seed),
        config=CanonConfig(seed=seed, output_dir=output_dir),
        rng=random.Random(seed),
        llm=LLMClient(FakeLLMBackend(make_fake_responder())),
        prompts=PlatformerPrompts(),
    )
    run_pipeline(compose_pipeline(vlm_judge=vlm_judge), ctx)
    return ctx


def _fake_judge() -> FakeVLMBackend:
    return FakeVLMBackend(make_fake_vlm_responder())


_PROMPT_MARKERS = (
    "### LEVEL: {lid}\n"
    "### TARGETS: l9, enemy:ash_wraith, player, tileset:ashen_depths"
)


# ---------------------------------------------------------------------------
# Backends + factory
# ---------------------------------------------------------------------------


class TestBuildVlmJudge:
    def test_none_and_empty_mean_no_judge(self) -> None:
        assert build_vlm_judge(None) is None
        assert build_vlm_judge("") is None
        assert build_vlm_judge("none") is None

    def test_fake_builds_deterministic_judge(self) -> None:
        judge = build_vlm_judge("fake")
        assert isinstance(judge, FakeVLMBackend)
        assert judge.model == "fake-vlm"

    def test_anthropic_fails_fast_without_key(self, monkeypatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
            build_vlm_judge("anthropic")

    def test_unknown_backend_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown vlm backend"):
            build_vlm_judge("gemini")

    def test_fake_backend_records_calls(self) -> None:
        judge = _fake_judge()
        judge.judge(_PROMPT_MARKERS.format(lid="l1"), [b"png1", b"png22"])
        assert judge.calls[0]["image_sizes"] == [4, 5]


class TestFakeResponder:
    def test_default_level_passes_all_dimensions(self) -> None:
        verdict = json.loads(
            make_fake_vlm_responder()(_PROMPT_MARKERS.format(lid="l1"), [])
        )
        assert all(verdict[dim]["passed"] for dim in DIMENSIONS)
        assert verdict["suggested_regen_targets"] == []

    def test_l2_fails_readability_and_suggests_offered_enemy(self) -> None:
        """The canned failure that keeps the warning path covered at $0."""
        verdict = json.loads(
            make_fake_vlm_responder()(_PROMPT_MARKERS.format(lid="l2"), [])
        )
        assert verdict["readability"]["passed"] is False
        assert verdict["fidelity"]["passed"] is True
        assert verdict["suggested_regen_targets"] == ["enemy:ash_wraith"]


# ---------------------------------------------------------------------------
# Verdict sanitization (code owns the contract, not the model)
# ---------------------------------------------------------------------------


class TestSanitizeVerdict:
    TARGETS = ["l1", "enemy:a", "tileset:s"]

    def _verdict(self, passed: bool) -> dict:
        return {
            dim: {"passed": passed, "notes": "n"} for dim in DIMENSIONS
        }

    def test_unknown_suggestions_dropped(self) -> None:
        obj = self._verdict(False) | {
            "suggested_regen_targets": ["enemy:a", "world", "enemy:a", 7]
        }
        entry = _sanitize_verdict(obj, self.TARGETS)
        assert entry["suggested_regen_targets"] == ["enemy:a"]

    def test_suggestions_dropped_when_everything_passed(self) -> None:
        obj = self._verdict(True) | {"suggested_regen_targets": ["enemy:a"]}
        assert _sanitize_verdict(obj, self.TARGETS)["suggested_regen_targets"] == []

    def test_notes_clamped(self) -> None:
        obj = self._verdict(True) | {"notes": "x" * 5000}
        entry = _sanitize_verdict(obj, self.TARGETS)
        assert len(entry["notes"]) == 300
        assert len(entry["verdicts"]) == len(DIMENSIONS)


# ---------------------------------------------------------------------------
# Code checks (the computable half — code-not-LLM)
# ---------------------------------------------------------------------------


def _png(size: tuple[int, int], box: tuple[int, int, int, int] | None) -> bytes:
    """A transparent canvas with an opaque rectangle at ``box`` (or fully
    transparent when None)."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", size, (0, 0, 0, 0))
    if box is not None:
        ImageDraw.Draw(img).rectangle(box, fill=(200, 40, 40, 255))
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


class TestSpriteChecks:
    def _ctx(self, tmp_path: Path) -> SimpleNamespace:
        from canon.adapters import JsonOutputAdapter

        return SimpleNamespace(adapter=JsonOutputAdapter(tmp_path))

    def test_missing_file_fails_loud(self, tmp_path: Path) -> None:
        checks = _sprite_checks(self._ctx(tmp_path), "enemy:x", "sprite/enemy/x/base.png")
        assert [c["check"] for c in checks] == ["sprite_file"]
        assert checks[0]["passed"] is False
        assert "missing on disk" in checks[0]["detail"]

    def test_full_canvas_sprite_passes(self, tmp_path: Path) -> None:
        rel = "sprite/enemy/x/base.png"
        (tmp_path / rel).parent.mkdir(parents=True)
        (tmp_path / rel).write_bytes(_png((32, 32), (1, 1, 30, 30)))
        checks = _sprite_checks(self._ctx(tmp_path), "enemy:x", rel)
        assert [c["passed"] for c in checks] == [True, True]

    def test_corner_hugging_sprite_fails_bbox(self, tmp_path: Path) -> None:
        """A sprite occupying a corner sliver renders far smaller than the
        hitbox the placement validator footprinted — computable, so code
        flags it; the VLM never has to."""
        rel = "sprite/enemy/x/base.png"
        (tmp_path / rel).parent.mkdir(parents=True)
        (tmp_path / rel).write_bytes(_png((32, 32), (0, 0, 9, 9)))
        checks = _sprite_checks(self._ctx(tmp_path), "enemy:x", rel)
        bbox = next(c for c in checks if c["check"] == "sprite_bbox")
        assert bbox["passed"] is False
        assert f"min {SPRITE_MIN_FILL:.2f}" in bbox["detail"]


class TestCodeChecksOnSliceTree:
    def test_placeholder_tree_palette_conforms(self, tmp_path: Path) -> None:
        """The placeholder sheet is painted with the exact palette hexes —
        every conformance check passes at distance 0, and with no sprites
        generated there is nothing else to check (deterministic shape)."""
        ctx = _run_slice(tmp_path / "run")
        checks = run_code_checks(ctx, "ashen_depths")
        assert checks, "expected palette checks for the tileset slots"
        assert all(c["check"] == "palette_conformance" for c in checks)
        assert all(c["passed"] for c in checks)

    def test_sprite_checks_join_the_report(self, tmp_path: Path) -> None:
        ctx = _run_slice(tmp_path / "run")
        enemy_id = sorted(ctx.bible.enemy_definitions)[0]
        enemy = ctx.bible.enemy_definitions[enemy_id]
        enemy.sprite_path = f"sprite/enemy/{enemy_id}/base.png"
        path = ctx.adapter.resolve_path(enemy.sprite_path)
        path.parent.mkdir(parents=True)
        path.write_bytes(_png((32, 32), (0, 0, 9, 9)))
        checks = run_code_checks(ctx, "ashen_depths")
        by_check = {(c["check"], c["target"]): c for c in checks}
        assert by_check[("sprite_file", f"enemy:{enemy_id}")]["passed"] is True
        assert by_check[("sprite_bbox", f"enemy:{enemy_id}")]["passed"] is False


# ---------------------------------------------------------------------------
# Warning derivation
# ---------------------------------------------------------------------------


class TestLayoutFallbackEcho:
    def test_fallback_level_fails_the_report(self, tmp_path: Path) -> None:
        """A fallback level must not read all-green in qa_report.json —
        VLM verdicts judge render truth and an empty level renders
        faithfully; the code check carries the failure."""
        ctx = _run_slice(tmp_path / "run")
        ctx.bible.levels["l3"].layout_fallback = True
        checks = run_code_checks(ctx, "ashen_depths")
        echo = next(c for c in checks if c["check"] == "layout_fallback")
        assert echo["target"] == "l3" and echo["passed"] is False
        assert "l3_layout_attempts.json" in echo["detail"]
        # Report-only: the durable layout warning owns the manifest line.
        report = {"stage_id": "ashen_depths", "code_checks": [echo], "levels": {}}
        assert derive_qa_warnings(report) == []


class TestDeriveQaWarnings:
    def test_clean_report_yields_nothing(self) -> None:
        report = {
            "stage_id": "s",
            "code_checks": [{"check": "sprite_file", "target": "player", "passed": True}],
            "levels": {
                "l1": {
                    "verdicts": {
                        dim: {"passed": True, "notes": ""} for dim in DIMENSIONS
                    },
                    "suggested_regen_targets": [],
                }
            },
        }
        assert derive_qa_warnings(report) == []

    def test_failures_and_errors_become_messages(self) -> None:
        report = {
            "stage_id": "s",
            "code_checks": [
                {
                    "check": "sprite_bbox",
                    "target": "enemy:x",
                    "passed": False,
                    "detail": "opaque bbox spans 0.10 of the canvas",
                }
            ],
            "levels": {
                "l1": {
                    "verdicts": {
                        "fidelity": {"passed": False, "notes": "missing enemy"},
                        "readability": {"passed": True, "notes": ""},
                        "style_coherence": {"passed": True, "notes": ""},
                    },
                    "suggested_regen_targets": ["enemy:x"],
                },
                "l2": {"error": "review render(s) missing: review/s/l2.png"},
            },
        }
        messages = derive_qa_warnings(report)
        assert len(messages) == 3
        assert "vlm_qa code-check sprite_bbox FAILED for enemy:x" in messages[0]
        assert "vlm_qa l1: fidelity FAILED — missing enemy" in messages[1]
        assert "suggested mark-only targets: enemy:x" in messages[1]
        assert "regen stays user-controlled" in messages[1]
        assert messages[2].startswith("vlm_qa l2: no verdict —")
        assert all(qa_report_rel("s") in m for m in messages)


# ---------------------------------------------------------------------------
# End-to-end (fake judge through the full slice)
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_report_shape_and_manifest_warning(self, tmp_path: Path) -> None:
        out = tmp_path / "run"
        ctx = _run_slice(out, vlm_judge=_fake_judge())

        report = json.loads((out / "review/ashen_depths/qa_report.json").read_text())
        assert report["stage_id"] == "ashen_depths"
        assert report["vlm_model"] == "fake-vlm"
        assert sorted(report["levels"]) == ["l1", "l2", "l3"]
        for entry in report["levels"].values():
            assert set(entry["verdicts"]) == set(DIMENSIONS)
        assert report["levels"]["l2"]["verdicts"]["readability"]["passed"] is False
        # The suggestion survived sanitization → it names a real artifact.
        suggested = report["levels"]["l2"]["suggested_regen_targets"]
        assert suggested and suggested[0].split(":", 1)[1] in ctx.bible.enemy_definitions

        warnings = json.loads((out / "manifest.json").read_text())["warnings"]
        qa_warnings = [w for w in warnings if w.startswith("vlm_qa ")]
        assert len(qa_warnings) == 1
        assert "l2: readability FAILED" in qa_warnings[0]
        assert "suggested mark-only targets" in qa_warnings[0]

    def test_byte_determinism(self, tmp_path: Path) -> None:
        _run_slice(tmp_path / "a", vlm_judge=_fake_judge())
        _run_slice(tmp_path / "b", vlm_judge=_fake_judge())
        for rel in ("review/ashen_depths/qa_report.json", "manifest.json"):
            assert (tmp_path / "a" / rel).read_bytes() == (
                tmp_path / "b" / rel
            ).read_bytes(), f"{rel} differs between identical runs"

    def test_warnings_survive_a_judgeless_rerun(self, tmp_path: Path) -> None:
        """The durability contract: the manifest re-derives QA warnings
        from the on-disk report, so a resume WITHOUT --vlm-backend never
        launders a failing report (layout_fallback pattern)."""
        out = tmp_path / "run"
        _run_slice(out, vlm_judge=_fake_judge())
        report_bytes = (out / "review/ashen_depths/qa_report.json").read_bytes()

        _run_slice(out, vlm_judge=None)  # same tree, no judge
        assert (out / "review/ashen_depths/qa_report.json").read_bytes() == (
            report_bytes
        ), "a judgeless run must leave the report standing"
        warnings = json.loads((out / "manifest.json").read_text())["warnings"]
        assert any(
            w.startswith("vlm_qa l2: readability FAILED") for w in warnings
        )

    def test_no_judge_means_no_report(self, tmp_path: Path) -> None:
        out = tmp_path / "run"
        _run_slice(out, vlm_judge=None)
        assert not (out / "review/ashen_depths/qa_report.json").exists()

    def test_unvalidatable_verdict_is_loud_not_fatal(self, tmp_path: Path) -> None:
        out = tmp_path / "run"
        _run_slice(out, vlm_judge=FakeVLMBackend(lambda prompt, images: "not json"))
        report = json.loads((out / "review/ashen_depths/qa_report.json").read_text())
        assert all(
            entry["error"] == "verdict never validated after retries"
            for entry in report["levels"].values()
        )
        warnings = json.loads((out / "manifest.json").read_text())["warnings"]
        assert sum(1 for w in warnings if "no verdict" in w) == 3

    def test_judge_sees_three_images_per_level(self, tmp_path: Path) -> None:
        judge = _fake_judge()
        _run_slice(tmp_path / "run", vlm_judge=judge)
        assert len(judge.calls) == 3  # one judgment per level, no retries
        for call in judge.calls:
            assert len(call["image_sizes"]) == 3  # block + skinned + legend
            assert "### TASK: vlm_qa" in call["prompt"]
            assert "### TARGETS: " in call["prompt"]


class TestCliFactoryEnv:
    def test_env_knob_builds_the_judge(self, monkeypatch) -> None:
        """CANON_PLAT_VLM_BACKEND mirrors --vlm-backend for the `canon
        run/resume/regen` factories — same explicit opt-in, empty = no QA."""
        from examples.platformer_pack.dag import VlmQaDagPhase, cli_phases_factory

        monkeypatch.setenv("CANON_PLAT_VLM_BACKEND", "fake")
        phases = cli_phases_factory(None)
        qa = next(p for p in phases if isinstance(p, VlmQaDagPhase))
        assert isinstance(qa._phase.judge, FakeVLMBackend)

        monkeypatch.delenv("CANON_PLAT_VLM_BACKEND")
        phases = cli_phases_factory(None)
        qa = next(p for p in phases if isinstance(p, VlmQaDagPhase))
        assert qa._phase.judge is None


class TestPhaseDirect:
    def test_no_op_without_judge_stamps_metadata(self, tmp_path: Path) -> None:
        ctx = _run_slice(tmp_path / "run")
        before = list(ctx.bible.metadata.phases_run)
        VlmQaPhase(judge=None).run(ctx)
        assert ctx.bible.metadata.phases_run == [*before, "plat:vlm_qa"]
        assert not ctx.adapter.resolve_path(qa_report_rel("ashen_depths")).exists()

    def test_missing_render_is_an_error_entry(self, tmp_path: Path) -> None:
        ctx = _run_slice(tmp_path / "run")
        ctx.adapter.resolve_path("review/ashen_depths/l1_skinned.png").unlink()
        VlmQaPhase(judge=_fake_judge()).run(ctx)
        report = json.loads(
            ctx.adapter.resolve_path(qa_report_rel("ashen_depths")).read_text()
        )
        assert "l1_skinned.png" in report["levels"]["l1"]["error"]
        assert "verdicts" in report["levels"]["l2"]  # others still judged
