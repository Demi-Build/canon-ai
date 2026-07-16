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
from canon.bible.platformer import EnemyDefinition
from canon.pipeline.runner import PipelineContext
from examples.platformer_pack import PlatformerPrompts, compose_pipeline
from examples.platformer_pack.vlm_qa import (
    ANIM_DEFAULT_FRAMES,
    ANIM_FRAMES_MAX,
    ANIM_FRAMES_MIN,
    ANIM_MOTION_MAX_CHARS,
    ANIMATION_QA_DIMENSIONS,
    ANIMATION_STATES,
    DIMENSIONS,
    SPRITE_MIN_FILL,
    VlmQaPhase,
    _sanitize_animation_spec,
    _sanitize_animation_verdict,
    _sanitize_verdict,
    _sprite_checks,
    _validate_animation_spec,
    _validate_animation_verdict,
    animate_prompt,
    animate_qa_prompt,
    author_animation_spec,
    build_vlm_judge,
    derive_animation_qa_warnings,
    derive_qa_warnings,
    enemy_animation_subject,
    make_fake_vlm_responder,
    qa_report_rel,
    review_animations,
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


class TestAnimationAuthoring:
    """B2 — the VLM authors a per-state motion spec from the ACTUAL sprite.
    Reuses the qa judge machinery; the canned fake exercises the whole path
    (including the frame clamp) at $0."""

    def _enemy(self, **kw) -> EnemyDefinition:
        base = dict(
            enemy_id="hop_toad",
            name="Hop-toad",
            archetype="patroller",
            size=1.5,
            stats={"flavor": "a stubby wide-eyed toad"},
        )
        base.update(kw)
        return EnemyDefinition(**base)

    def _subject(self) -> str:
        return enemy_animation_subject(self._enemy())

    def test_prompt_carries_task_marker_schema_and_states(self) -> None:
        prompt = animate_prompt("enemy:hop_toad", self._subject())
        assert "### TASK: plat_animate" in prompt
        assert "### ACTOR: enemy:hop_toad" in prompt
        assert "'patroller'" in prompt  # archetype is passed as a HINT
        assert "a stubby wide-eyed toad" in prompt  # flavor grounds the model
        assert all(f'"{state}"' in prompt for state in ANIMATION_STATES)

    def test_fake_judge_authors_full_spec_with_clamp(self) -> None:
        spec = author_animation_spec(
            _fake_judge(), "enemy:hop_toad", self._subject(), b"png-bytes"
        )
        assert set(spec) == set(ANIMATION_STATES)
        # canned death=9 clamps to the ceiling; canned idle=2 sits at the floor
        assert spec["death"]["frames"] == ANIM_FRAMES_MAX
        assert spec["idle"]["frames"] == ANIM_FRAMES_MIN
        for state in ANIMATION_STATES:
            assert ANIM_FRAMES_MIN <= spec[state]["frames"] <= ANIM_FRAMES_MAX
            assert spec[state]["motion"]

    def test_player_frame_budget_allows_more_frames(self) -> None:
        from examples.platformer_pack.vlm_qa import (
            PLAYER_ANIM_FRAMES_MAX,
            PLAYER_ANIMATION_STATES,
        )

        # a fake that requests a smooth 9-frame walk + a jump state
        smooth = FakeVLMBackend(
            lambda prompt, images: json.dumps(
                {s: {"frames": 9, "motion": "m"} for s in PLAYER_ANIMATION_STATES}
            )
        )
        spec = author_animation_spec(
            smooth, "player", "the hero", b"x",
            states=PLAYER_ANIMATION_STATES, frames_max=PLAYER_ANIM_FRAMES_MAX,
        )
        assert set(spec) == set(PLAYER_ANIMATION_STATES)
        assert "jump" in spec
        assert spec["walk"]["frames"] == 9  # the enemy cap (6) would clip this

    def test_judge_receives_the_sprite_bytes(self) -> None:
        judge = _fake_judge()
        author_animation_spec(judge, "enemy:hop_toad", self._subject(), b"12345")
        assert judge.calls[-1]["image_sizes"] == [5]

    def test_spec_persists_on_enemy_stats(self) -> None:
        enemy = self._enemy()
        enemy.stats["animation"] = author_animation_spec(
            _fake_judge(), "enemy:hop_toad", self._subject(), b"x"
        )
        assert enemy.stats["animation"]["walk"]["frames"] == 4

    def test_sanitize_clamps_and_fills_missing_states(self) -> None:
        spec = _sanitize_animation_spec(
            {"idle": {"frames": 99, "motion": "x"}, "walk": {"frames": 0, "motion": "y"}}
        )
        assert spec["idle"]["frames"] == ANIM_FRAMES_MAX  # 99 -> 6
        assert spec["walk"]["frames"] == ANIM_FRAMES_MIN  # 0 -> 2
        # hurt + death absent → filled with the defaults and a brief motion
        assert set(spec) == set(ANIMATION_STATES)
        assert spec["hurt"]["frames"] == ANIM_DEFAULT_FRAMES["hurt"]
        assert spec["death"]["motion"]

    def test_sanitize_rounds_float_frames(self) -> None:
        spec = _sanitize_animation_spec(
            {s: {"frames": 3.6, "motion": "m"} for s in ANIMATION_STATES}
        )
        assert all(spec[s]["frames"] == 4 for s in ANIMATION_STATES)

    def test_sanitize_clamps_motion_length(self) -> None:
        spec = _sanitize_animation_spec(
            {s: {"frames": 3, "motion": "m" * 500} for s in ANIMATION_STATES}
        )
        assert all(
            len(spec[s]["motion"]) <= ANIM_MOTION_MAX_CHARS for s in ANIMATION_STATES
        )

    def test_validate_rejects_non_json(self) -> None:
        ok, problems = _validate_animation_spec("definitely not json")
        assert not ok and problems

    def test_validate_flags_each_missing_state(self) -> None:
        ok, problems = _validate_animation_spec(
            json.dumps({"idle": {"frames": 3, "motion": "x"}})
        )
        assert not ok
        assert len(problems) == len(ANIMATION_STATES) - 1  # 3 missing

    def test_validate_flags_bad_frames_and_motion(self) -> None:
        ok, problems = _validate_animation_spec(
            json.dumps({s: {"frames": "lots", "motion": ""} for s in ANIMATION_STATES})
        )
        assert not ok
        assert len(problems) == 2 * len(ANIMATION_STATES)  # frames + motion each

    def test_author_returns_none_when_never_validates(self) -> None:
        # a judge that always returns junk → retries exhaust → None (the
        # loud-fallback contract: caller keeps the static sprite)
        bad = FakeVLMBackend(lambda prompt, images: "not json ever")
        assert author_animation_spec(
            bad, "enemy:hop_toad", self._subject(), b"x", max_retries=2
        ) is None

    def test_animate_branch_does_not_regress_qa_branch(self) -> None:
        # one fake judge serves BOTH tasks — a qa prompt still yields verdicts
        reply = _fake_judge().judge(_PROMPT_MARKERS.format(lid="l1"), [b"a", b"b", b"c"])
        assert set(DIMENSIONS) <= set(json.loads(reply))


class TestAnimationQA:
    """B5 — the VLM reviews the generated animation (consistency/motion/
    readability), code checks the computable half. Warn-only; never regen."""

    def _enemy(self, **kw) -> EnemyDefinition:
        base = dict(enemy_id="hop_toad", name="Hop-toad", stats={})
        base.update(kw)
        return EnemyDefinition(**base)

    def test_prompt_carries_task_marker_and_state_order(self) -> None:
        prompt = animate_qa_prompt(
            "enemy:hop_toad", "Hop-toad", ["walk", "idle", "death"]
        )
        assert "### TASK: plat_animate_qa" in prompt
        assert "### ACTOR: enemy:hop_toad" in prompt
        # states are listed in canonical order, not input order
        assert "idle, walk, death" in prompt
        assert all(d in prompt for d in ANIMATION_QA_DIMENSIONS)

    def test_fake_responder_qa_and_authoring_branches_distinct(self) -> None:
        # plat_animate_qa must NOT be swallowed by the plat_animate branch
        # (substring). QA → a verdict; authoring → a motion spec.
        resp = make_fake_vlm_responder()
        qa = json.loads(resp("### TASK: plat_animate_qa\n### ACTOR: x\n", []))
        assert set(ANIMATION_QA_DIMENSIONS) <= set(qa)
        assert "walk" not in qa
        # the authoring branch returns a spec for the states the prompt lists
        author = json.loads(
            resp('### TASK: plat_animate\n### ACTOR: x\n  - "walk": move', [])
        )
        assert "walk" in author and "consistency" not in author

    def test_validate_rejects_junk_and_missing_dims(self) -> None:
        ok, _ = _validate_animation_verdict("not json")
        assert not ok
        ok2, problems = _validate_animation_verdict(
            json.dumps({"consistency": {"passed": True, "notes": "x"}})
        )
        assert not ok2
        assert len(problems) == len(ANIMATION_QA_DIMENSIONS) - 1  # 2 missing

    def test_sanitize_shape_and_clamp(self) -> None:
        raw = {d: {"passed": True, "notes": "n" * 500} for d in ANIMATION_QA_DIMENSIONS}
        raw["notes"] = "overall " * 100
        out = _sanitize_animation_verdict(raw)
        assert set(out["verdicts"]) == set(ANIMATION_QA_DIMENSIONS)
        assert all(
            len(out["verdicts"][d]["notes"]) <= 300 for d in ANIMATION_QA_DIMENSIONS
        )
        assert len(out["notes"]) <= 300

    def test_derive_warnings_from_failing_report(self) -> None:
        report = {
            "actors": {
                "enemy:toad": {
                    "code_checks": [
                        {"check": "animation_frames", "subject": "walk",
                         "passed": False, "detail": "frame 2 blank"},
                    ],
                    "verdicts": {
                        "consistency": {"passed": False, "notes": "morphs"},
                        "motion": {"passed": True, "notes": ""},
                        "readability": {"passed": True, "notes": ""},
                    },
                }
            }
        }
        warnings = derive_animation_qa_warnings(report)
        assert len(warnings) == 2  # one code-check + one verdict
        assert any("animation_frames FAILED" in w for w in warnings)
        assert any("consistency FAILED" in w and "enemy:toad" in w for w in warnings)

    def test_derive_warns_for_player_too(self) -> None:
        report = {
            "actors": {
                "player": {
                    "code_checks": [],
                    "verdicts": {
                        "consistency": {"passed": True, "notes": ""},
                        "motion": {"passed": False, "notes": "jump is stiff"},
                        "readability": {"passed": True, "notes": ""},
                    },
                }
            }
        }
        warnings = derive_animation_qa_warnings(report)
        assert len(warnings) == 1
        assert "player" in warnings[0] and "motion FAILED" in warnings[0]

    def test_derive_no_warnings_when_all_pass(self) -> None:
        report = {
            "actors": {
                "enemy:toad": {
                    "code_checks": [
                        {"check": "animation_strip", "subject": "walk",
                         "passed": True, "detail": "ok"},
                    ],
                    "verdicts": {
                        d: {"passed": True, "notes": ""}
                        for d in ANIMATION_QA_DIMENSIONS
                    },
                }
            }
        }
        assert derive_animation_qa_warnings(report) == []

    def test_review_skips_actors_without_animation(self) -> None:
        ctx = SimpleNamespace(
            bible=SimpleNamespace(
                enemy_definitions={"e": self._enemy()}, player=None
            ),
            adapter=None,
        )
        report = review_animations(ctx, judge=None)
        assert report["actors"] == {}
        assert report["vlm_model"] == "none"

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

    def test_judge_sees_five_images_per_level(self, tmp_path: Path) -> None:
        judge = _fake_judge()
        _run_slice(tmp_path / "run", vlm_judge=judge)
        assert len(judge.calls) == 3  # one judgment per level, no retries
        for call in judge.calls:
            # block + skinned + legend + the two play-scale crops (QA v2)
            assert len(call["image_sizes"]) == 5
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
