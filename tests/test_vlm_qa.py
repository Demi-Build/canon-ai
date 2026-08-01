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
    _STATE_BRIEF,
    _STATE_ORDER,
    ANIM_DEFAULT_FRAMES,
    ANIM_FRAMES_MAX,
    ANIM_FRAMES_MIN,
    ANIM_MOTION_MAX_CHARS,
    ANIMATION_QA_DIMENSIONS,
    ANIMATION_STATES,
    BACKDROP_SEAM_TOLERANCE,
    COMPOSITE_MIN_HUE_SEP,
    COMPOSITE_MIN_LUMA,
    DIMENSIONS,
    PLAYER_ANIM_FRAMES_MAX,
    PLAYER_ANIMATION_STATES,
    SPRITE_MIN_FILL,
    STATE_LOOP_MODES,
    VFX_MIN_FILL,
    VlmQaPhase,
    _animation_checks,
    _composite_contrast_checks,
    _flip_review_status,
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
    enemy_animation_states,
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

    def test_fake_judge_authors_the_full_player_state_set(self) -> None:
        # the canned fake must cover fall/land/skid — a state missing from
        # canned_frames kills ALL animation for the actor in fake runs
        spec = author_animation_spec(
            _fake_judge(), "player", "the hero", b"x",
            states=PLAYER_ANIMATION_STATES, frames_max=PLAYER_ANIM_FRAMES_MAX,
        )
        assert set(spec) == set(PLAYER_ANIMATION_STATES)
        assert spec["fall"]["frames"] == 3
        assert spec["land"]["frames"] == 2
        assert spec["skid"]["frames"] == 2

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


class TestStateVocabulary:
    """G4 — the cross-check that keeps a new state from half-landing: every
    state either surface can pick must carry a brief (two raw-index KeyError
    sites), a default frame count, a loop mode, and a contact-sheet row."""

    def test_every_state_fully_registered(self) -> None:
        union = set(PLAYER_ANIMATION_STATES) | set(ANIMATION_STATES) | {"jump"}
        for state in union:
            assert state in _STATE_BRIEF, state
            assert state in ANIM_DEFAULT_FRAMES, state
            assert state in STATE_LOOP_MODES, state
            assert state in _STATE_ORDER, state

    def test_player_states_include_the_new_trio(self) -> None:
        assert {"fall", "land", "skid"} <= set(PLAYER_ANIMATION_STATES)

    def test_enemy_states_widen_only_for_hopper(self) -> None:
        for archetype in ("", "patroller", "sentry", "swimmer", "flyer"):
            enemy = EnemyDefinition(enemy_id="e", archetype=archetype)
            assert enemy_animation_states(enemy) == ANIMATION_STATES
        hopper = EnemyDefinition(enemy_id="h", archetype="hopper")
        assert enemy_animation_states(hopper) == (*ANIMATION_STATES, "jump")


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


def _strip_ctx(tmp_path: Path):
    from canon.adapters import JsonOutputAdapter

    return SimpleNamespace(adapter=JsonOutputAdapter(tmp_path))


def _write_strip(
    tmp_path: Path, boxes: list[tuple[int, int, int, int]],
    fw: int = 32, fh: int = 32, state: str = "walk",
) -> dict:
    """One opaque rect per frame (frame-local coords) → a strip on disk +
    the animation manifest naming it — normalize_frames-style content."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (fw * len(boxes), fh), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for i, (x0, y0, x1, y1) in enumerate(boxes):
        draw.rectangle(
            [x0 + i * fw, y0, x1 + i * fw, y1], fill=(200, 40, 40, 255)
        )
    rel = f"sprite/enemy/x/{state}.png"
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    path.write_bytes(buffer.getvalue())
    return {
        "states": {
            state: {
                "path": rel, "frames": len(boxes), "frame_width": fw,
                "frame_height": fh, "duration_ms": 120,
            }
        }
    }


class TestAnimationWanderMeter:
    """Ticket 6 — the wander METER that replaced the tautological
    post-normalize registration check (which measured jitter AFTER
    normalize_frames had re-anchored every frame — it structurally could not
    fail). The meter reads the PRE-normalize baseline spread the animation
    phase recorded at generation time; a spike warns, never gates."""

    def _with_wander(self, tmp_path: Path, frac) -> dict:
        anim = _write_strip(tmp_path, [(8, 10, 24, 31)] * 4)
        if frac is not None:
            anim["states"]["walk"]["raw_bottom_wander_frac"] = frac
        return anim

    def test_recorded_low_wander_passes(self, tmp_path: Path) -> None:
        anim = self._with_wander(tmp_path, 0.05)
        checks = _animation_checks(_strip_ctx(tmp_path), "enemy:x", anim)
        rec = next(c for c in checks if c["check"] == "animation_wander")
        assert rec["passed"] is True
        assert rec["subject"] == "walk"

    def test_spike_fires_the_meter(self, tmp_path: Path) -> None:
        # The corrupted-fixture requirement: a kept gate must be PROVEN able
        # to fire — a 50%-of-sheet-height baseline drift is a real backend
        # registration failure the old check could never see.
        anim = self._with_wander(tmp_path, 0.5)
        checks = _animation_checks(_strip_ctx(tmp_path), "enemy:x", anim)
        rec = next(c for c in checks if c["check"] == "animation_wander")
        assert rec["passed"] is False
        assert "pre-normalize baseline wander 50%" in rec["detail"]

    def test_old_trees_without_the_key_stay_silent(
        self, tmp_path: Path
    ) -> None:
        anim = self._with_wander(tmp_path, None)
        checks = _animation_checks(_strip_ctx(tmp_path), "enemy:x", anim)
        assert not any(c["check"] == "animation_wander" for c in checks)

    def test_registration_check_is_gone(self, tmp_path: Path) -> None:
        # The tautological check must not resurface.
        anim = self._with_wander(tmp_path, 0.05)
        checks = _animation_checks(_strip_ctx(tmp_path), "enemy:x", anim)
        assert not any(
            c["check"] == "animation_registration" for c in checks
        )


class TestAnimationAtlasCheck:
    """G4 — the packed-atlas code check: silent when no atlas.json exists
    (old trees), one per-actor record validating png + counts + bounds."""

    def _atlas_json(
        self, tmp_path: Path, n_rects: int, atlas_px: int = 64
    ) -> None:
        from PIL import Image

        d = tmp_path / "sprite/enemy/x"
        d.mkdir(parents=True, exist_ok=True)
        buffer = io.BytesIO()
        Image.new("RGBA", (atlas_px, atlas_px), (0, 0, 0, 0)).save(
            buffer, format="PNG"
        )
        (d / "atlas.png").write_bytes(buffer.getvalue())
        (d / "atlas.json").write_text(json.dumps({
            "path": "sprite/enemy/x/atlas.png",
            "frame_size": [32, 32],
            "states": {
                "walk": {
                    "loop": "loop",
                    "durations_ms": [120] * n_rects,
                    "frames": [
                        {"x": i * 17, "y": 0, "w": 16, "h": 22, "ox": 8, "oy": 10}
                        for i in range(n_rects)
                    ],
                }
            },
        }))

    def test_silent_when_no_atlas(self, tmp_path: Path) -> None:
        anim = _write_strip(tmp_path, [(8, 10, 24, 31)] * 4)
        checks = _animation_checks(_strip_ctx(tmp_path), "enemy:x", anim)
        assert not any(c["check"] == "animation_atlas" for c in checks)

    def test_matching_atlas_passes(self, tmp_path: Path) -> None:
        anim = _write_strip(tmp_path, [(8, 10, 24, 31)] * 3)
        self._atlas_json(tmp_path, n_rects=3)
        checks = _animation_checks(_strip_ctx(tmp_path), "enemy:x", anim)
        atlas = next(c for c in checks if c["check"] == "animation_atlas")
        assert atlas["passed"] is True
        assert atlas["subject"] == "atlas"

    def test_count_mismatch_fails(self, tmp_path: Path) -> None:
        anim = _write_strip(tmp_path, [(8, 10, 24, 31)] * 4)
        self._atlas_json(tmp_path, n_rects=3)
        checks = _animation_checks(_strip_ctx(tmp_path), "enemy:x", anim)
        atlas = next(c for c in checks if c["check"] == "animation_atlas")
        assert atlas["passed"] is False
        assert "3 atlas frame(s) vs manifest 4" in atlas["detail"]

    def test_out_of_bounds_rect_fails(self, tmp_path: Path) -> None:
        anim = _write_strip(tmp_path, [(8, 10, 24, 31)] * 3)
        self._atlas_json(tmp_path, n_rects=3, atlas_px=32)  # 2*17+16 > 32
        checks = _animation_checks(_strip_ctx(tmp_path), "enemy:x", anim)
        atlas = next(c for c in checks if c["check"] == "animation_atlas")
        assert atlas["passed"] is False
        assert "outside atlas" in atlas["detail"]

    def test_missing_atlas_png_fails(self, tmp_path: Path) -> None:
        anim = _write_strip(tmp_path, [(8, 10, 24, 31)] * 3)
        self._atlas_json(tmp_path, n_rects=3)
        (tmp_path / "sprite/enemy/x/atlas.png").unlink()
        checks = _animation_checks(_strip_ctx(tmp_path), "enemy:x", anim)
        atlas = next(c for c in checks if c["check"] == "animation_atlas")
        assert atlas["passed"] is False
        assert "missing on disk" in atlas["detail"]


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


def _bare_ctx(tmp_path: Path, **bible_extra) -> SimpleNamespace:
    """A minimal run_code_checks context: empty bible collections plus
    whatever ``bible_extra`` injects (props, backdrops, ...)."""
    from canon.adapters import JsonOutputAdapter

    bible = SimpleNamespace(
        stages={}, levels={}, tilesets={}, enemy_definitions={},
        items={}, player=None, props={}, backdrops={},
    )
    for key, value in bible_extra.items():
        setattr(bible, key, value)
    return SimpleNamespace(adapter=JsonOutputAdapter(tmp_path), bible=bible)


class TestVfxPropMinFill:
    def test_wispy_vfx_prop_passes_relaxed_fill(self, tmp_path: Path) -> None:
        """dust/splash/sparkle are mostly empty space BY DESIGN — their
        sprite_bbox gate relaxes to VFX_MIN_FILL while gameplay props
        (and enemies, per TestSpriteChecks) keep SPRITE_MIN_FILL."""
        from canon.bible.platformer import StageProps

        assert VFX_MIN_FILL < SPRITE_MIN_FILL
        sparse = _png((32, 32), (0, 0, 4, 4))  # bbox span 5/32 ≈ 0.16
        prop_paths = {}
        for name in ("sparkle", "checkpoint"):
            rel = f"sprite/prop/s/{name}.png"
            (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / rel).write_bytes(sparse)
            prop_paths[name] = rel
        props = StageProps(
            artifact_id="props:s", stage_id="s", prop_paths=prop_paths
        )
        ctx = _bare_ctx(tmp_path, props={"s": props})
        checks = run_code_checks(ctx, "s")
        bbox = {c["subject"]: c for c in checks if c["check"] == "sprite_bbox"}
        assert bbox["sprite/prop/s/sparkle.png"]["passed"] is True
        assert f"min {VFX_MIN_FILL:.2f}" in bbox["sprite/prop/s/sparkle.png"]["detail"]
        assert bbox["sprite/prop/s/checkpoint.png"]["passed"] is False


class TestBackdropTiling:
    def test_seamless_passes_hard_seam_fails_and_flips_review(
        self, tmp_path: Path
    ) -> None:
        """backdrop_tiling compares a band's left/right edge columns (the
        horizontal wrap every consumer draws); a hard seam fails and
        flips the Backdrop's review gate, a clean recompute flips it
        back."""
        from PIL import Image

        from canon.bible.platformer import Backdrop

        (tmp_path / "backdrop/s").mkdir(parents=True)
        seamless = Image.new("RGB", (64, 32), (80, 90, 100))
        seamless.save(tmp_path / "backdrop/s/band_0.png")
        seam = Image.new("RGB", (64, 32), (0, 0, 0))
        for yy in range(32):
            for xx in range(32, 64):
                seam.putpixel((xx, yy), (255, 255, 255))
        seam.save(tmp_path / "backdrop/s/band_1.png")

        backdrop = Backdrop(
            artifact_id="backdrop:s", stage_id="s",
            band_paths=["backdrop/s/band_0.png", "backdrop/s/band_1.png"],
            depths=[0.2, 0.5],
        )
        ctx = _bare_ctx(tmp_path, backdrops={"s": backdrop})
        checks = run_code_checks(ctx, "s")
        tiling = {
            c["subject"]: c for c in checks if c["check"] == "backdrop_tiling"
        }
        assert set(tiling) == set(backdrop.band_paths)
        assert all(c["target"] == "backdrop:s" for c in tiling.values())
        assert tiling["backdrop/s/band_0.png"]["passed"] is True
        assert tiling["backdrop/s/band_1.png"]["passed"] is False
        assert f"tolerance {BACKDROP_SEAM_TOLERANCE:.0f}" in (
            tiling["backdrop/s/band_1.png"]["detail"]
        )
        # The review gate covers backdrops: fail → needs-rework, and the
        # idempotent recompute approves once only passing checks remain.
        _flip_review_status(ctx, checks)
        assert backdrop.review_status == "needs-rework"
        _flip_review_status(ctx, [tiling["backdrop/s/band_0.png"]])
        assert backdrop.review_status == "approved"

    def test_absent_backdrop_adds_no_records(self, tmp_path: Path) -> None:
        checks = run_code_checks(_bare_ctx(tmp_path), "s")
        assert not any(c["check"] == "backdrop_tiling" for c in checks)


class TestPaletteDriftMeter:
    """Ticket 6 — the pre-conform palette drift METER + the 16x floor
    dedupe. The meter reads the tileset phase's snapshot of how far each RAW
    generated tile sat from the role hex BEFORE conform_to_palette repaired
    it; a spike warns but NEVER gates approval (the name-skip contract)."""

    def test_meter_reads_snapshot_and_flags_only_spikes(
        self, tmp_path: Path
    ) -> None:
        ctx = _bare_ctx(tmp_path)
        (tmp_path / "review/s").mkdir(parents=True)
        (tmp_path / "review/s/palette_drift.json").write_text(
            json.dumps({"floor": 30.0, "lava": 150.0})
        )
        checks = run_code_checks(ctx, "s")
        drift = {
            c["subject"]: c for c in checks if c["check"] == "palette_drift"
        }
        assert set(drift) == {"floor", "lava"}
        assert drift["floor"]["passed"] is True
        assert drift["lava"]["passed"] is False
        assert "spike at 96" in drift["lava"]["detail"]
        assert all(
            c["target"] == "tileset:s" for c in drift.values()
        )

    def test_absent_snapshot_is_silent(self, tmp_path: Path) -> None:
        checks = run_code_checks(_bare_ctx(tmp_path), "s")
        assert not any(c["check"] == "palette_drift" for c in checks)

    def test_drift_spike_never_flips_review_status(
        self, tmp_path: Path
    ) -> None:
        # The meter is advisory: even a hand-corrupted failing record aimed
        # straight at the tileset must not demote it (composite_contrast
        # precedent — the skip is the contract).
        from canon.bible.platformer import Tileset

        tileset = Tileset(artifact_id="tileset:s", stage_id="s")
        tileset.review_status = "approved"
        ctx = _bare_ctx(tmp_path, tilesets={"s": tileset})
        record = {
            "check": "palette_drift", "target": "tileset:s",
            "subject": "floor", "passed": False, "detail": "spike",
        }
        _flip_review_status(ctx, [record])
        assert tileset.review_status == "approved"

    def test_floor_variants_dedupe_to_one_record(self, tmp_path: Path) -> None:
        # 16 mean-preserving shaded copies of ONE generated square used to
        # yield 16 structurally-identical records — now one per tile NAME.
        from PIL import Image

        from canon.bible.platformer import Tileset, TileSlot
        from examples.platformer_pack.tiles import DEFAULT_TILES

        by = {t.name: t for t in DEFAULT_TILES.tiles}
        palette = {
            by["floor"].color_role: "#405060",
            by["wall"].color_role: "#605040",
        }
        hexes = ["#405060", "#405060", "#605040"]
        sheet = Image.new("RGBA", (96, 32))
        for i, hx in enumerate(hexes):
            color = tuple(
                int(hx.lstrip("#")[j : j + 2], 16) for j in (0, 2, 4)
            ) + (255,)
            sheet.paste(Image.new("RGBA", (32, 32), color), (i * 32, 0))
        (tmp_path / "tileset/s").mkdir(parents=True)
        sheet.save(tmp_path / "tileset/s/sheet.png")
        tileset = Tileset(
            artifact_id="tileset:s", stage_id="s",
            tilesheet_path="tileset/s/sheet.png",
            palette=palette,
            slots=[
                TileSlot(
                    index=0, tile_type=1, name="floor",
                    px_region=(0, 0, 32, 32), collision="solid",
                    params={"autotile_mask": 0},
                ),
                TileSlot(
                    index=1, tile_type=1, name="floor",
                    px_region=(32, 0, 32, 32), collision="solid",
                    params={"autotile_mask": 5},
                ),
                TileSlot(
                    index=2, tile_type=3, name="wall",
                    px_region=(64, 0, 32, 32), collision="solid",
                ),
            ],
        )
        ctx = _bare_ctx(tmp_path, tilesets={"s": tileset})
        checks = run_code_checks(ctx, "s")
        palette_subjects = [
            c["subject"] for c in checks if c["check"] == "palette_conformance"
        ]
        assert palette_subjects == ["floor", "wall"]  # mask-5 deduped away


class TestVerdictDemotesReviewStatus:
    """postmortem ticket 4: a failing VLM per-level verdict that names an
    asset in its sanitized ``suggested_regen_targets`` demotes that asset's
    review_status (any single verdict is enough) — before this the review
    gate was fed CODE checks only, so a backdrop the judge blamed still read
    'approved'. Oracle = the real plat_ember_paid l4 verdict (readability
    FAILED, blamed backdrop:cinder_depths + three enemies)."""

    def _ctx(self, tmp_path: Path):
        from canon.bible.platformer import Backdrop, EnemyDefinition

        backdrop = Backdrop(
            artifact_id="backdrop:cinder_depths", stage_id="cinder_depths",
            band_paths=[], depths=[],
        )
        enemy = EnemyDefinition(
            enemy_id="ember_sentinel", archetype="sentry", size=1.0,
        )
        return _bare_ctx(
            tmp_path,
            backdrops={"cinder_depths": backdrop},
            enemy_definitions={"ember_sentinel": enemy},
        )

    def _paid_l4_levels(self) -> dict:
        # verbatim shape of plat_ember_paid/review/cinder_depths/qa_report.json
        return {
            "l4": {
                "verdicts": {
                    "fidelity": {"passed": True},
                    "style_coherence": {"passed": True},
                    "readability": {"passed": False},
                },
                "suggested_regen_targets": [
                    "backdrop:cinder_depths", "enemy:ember_sentinel",
                ],
            }
        }

    def test_failing_verdict_demotes_backdrop_and_enemy(
        self, tmp_path: Path
    ) -> None:
        ctx = self._ctx(tmp_path)
        _flip_review_status(ctx, [], self._paid_l4_levels())
        assert ctx.bible.backdrops["cinder_depths"].review_status == (
            "needs-rework"
        )
        assert ctx.bible.enemy_definitions["ember_sentinel"].review_status == (
            "needs-rework"
        )

    def test_all_passing_verdict_leaves_approved(self, tmp_path: Path) -> None:
        ctx = self._ctx(tmp_path)
        levels = {
            "l4": {
                "verdicts": {"readability": {"passed": True}},
                "suggested_regen_targets": [],
            }
        }
        _flip_review_status(ctx, [], levels)
        # no code checks, no failing verdict → nothing touches the gate,
        # so review_status stays at its generated default (draft)
        assert ctx.bible.backdrops["cinder_depths"].review_status == "draft"

    def test_error_entry_is_skipped(self, tmp_path: Path) -> None:
        ctx = self._ctx(tmp_path)
        levels = {
            "l4": {"error": "images missing",
                   "suggested_regen_targets": ["backdrop:cinder_depths"]}
        }
        _flip_review_status(ctx, [], levels)
        assert ctx.bible.backdrops["cinder_depths"].review_status == "draft"

    def test_verdict_overrides_a_passing_code_check(
        self, tmp_path: Path
    ) -> None:
        ctx = self._ctx(tmp_path)
        passing = {
            "check": "backdrop_tiling", "target": "backdrop:cinder_depths",
            "subject": "band_0", "passed": True, "detail": "",
        }
        _flip_review_status(ctx, [passing], self._paid_l4_levels())
        # merge semantics: a failing verdict forces False even though the
        # code check passed
        assert ctx.bible.backdrops["cinder_depths"].review_status == (
            "needs-rework"
        )

    def test_rejected_is_never_overwritten(self, tmp_path: Path) -> None:
        ctx = self._ctx(tmp_path)
        ctx.bible.backdrops["cinder_depths"].review_status = "rejected"
        _flip_review_status(ctx, [], self._paid_l4_levels())
        assert ctx.bible.backdrops["cinder_depths"].review_status == "rejected"

    def test_demotion_is_idempotent_across_runs(self, tmp_path: Path) -> None:
        ctx = self._ctx(tmp_path)
        _flip_review_status(ctx, [], self._paid_l4_levels())
        # a carried-unchanged verdict re-derives the identical blame set,
        # so a second recompute lands the same status (no oscillation)
        _flip_review_status(ctx, [], self._paid_l4_levels())
        assert ctx.bible.backdrops["cinder_depths"].review_status == (
            "needs-rework"
        )


class TestCodeChecksOnSliceTree:
    def test_placeholder_tree_palette_conforms(self, tmp_path: Path) -> None:
        """The placeholder sheet is painted with the exact palette hexes —
        every conformance check passes at distance 0, and with no sprites
        generated there is nothing else to check (deterministic shape)."""
        ctx = _run_slice(tmp_path / "run")
        checks = run_code_checks(ctx, "ashen_depths")
        assert checks, "expected palette checks for the tileset slots"
        # The coverage LEDGER record (graphics arc) and the ADVISORY
        # composite_contrast block (RB3) are informational and always
        # present; everything else here is palette conformance.
        assert all(
            c["check"] in ("palette_conformance", "coverage", "composite_contrast")
            for c in checks
        )
        assert any(c["check"] == "coverage" for c in checks)
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
# Composite contrast (RB3 — ADVISORY-ONLY by user lock: never blocks)
# ---------------------------------------------------------------------------


_COMPO_PX = 16
_COMPO_GRID = (24, 12)  # cells


def _composite_ctx(
    tmp_path: Path,
    entities: list[tuple[int, int]],
    *,
    bg: str = "#3a3a80",
    enemy: EnemyDefinition | None = None,
    items: list[tuple[int, int]] | None = None,
    level_ids: tuple[str, ...] = ("l1",),
    secret_rooms: tuple[str, ...] = (),
    skip_png: tuple[str, ...] = (),
) -> SimpleNamespace:
    """A minimal one-stage ctx whose skinned renders are FLAT ``bg``
    images — placements read against exactly that color, so camouflage
    is a fixture choice, not an emergent render. The default enemy/item
    placeholder MATCHES the default bg (fully camouflaged)."""
    from PIL import Image

    from canon.adapters import JsonOutputAdapter
    from canon.bible.platformer import ItemDefinition

    enemy = enemy or EnemyDefinition(
        enemy_id="shade", name="Shade", archetype="patroller", size=1.0,
        stats={"placeholder_color": "#3a3a80"},
        behavior={"patrol_range": 4},
    )
    item = ItemDefinition(
        item_id="coin", name="Coin", stats={"placeholder_color": "#3a3a80"}
    )
    w, h = _COMPO_GRID
    levels: dict[str, SimpleNamespace] = {}
    for lid in (*level_ids, *secret_rooms):
        levels[lid] = SimpleNamespace(
            level_id=lid, grid_width=w, grid_height=h,
            entities=[
                SimpleNamespace(
                    ref=f"enemy:{enemy.enemy_id}", pos=pos, overrides={}
                )
                for pos in entities
            ],
            items=[
                SimpleNamespace(ref="item:coin", pos=pos, overrides={})
                for pos in (items or [])
            ],
            secret_rooms=list(secret_rooms) if lid == level_ids[0] else [],
        )
        if lid not in skip_png:
            path = tmp_path / f"review/s/{lid}_skinned.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            rgb = tuple(int(bg.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
            Image.new("RGB", (w * _COMPO_PX, h * _COMPO_PX), rgb).save(path)
    bible = SimpleNamespace(
        stages={"s": SimpleNamespace(stage_id="s", level_ids=list(level_ids))},
        levels=levels,
        tilesets={
            "s": SimpleNamespace(
                slots=[SimpleNamespace(px_region=(0, 0, _COMPO_PX, _COMPO_PX))]
            )
        },
        enemy_definitions={enemy.enemy_id: enemy},
        items={"coin": item},
        player=None, props={}, backdrops={},
    )
    return SimpleNamespace(adapter=JsonOutputAdapter(tmp_path), bible=bible)


def _composite(ctx: SimpleNamespace) -> list[dict]:
    from examples.platformer_pack.graphics import DEFAULT_GRAPHICS
    from examples.platformer_pack.tiles import DEFAULT_TILES
    from examples.platformer_pack.variants import DEFAULT_VARIANTS

    return _composite_contrast_checks(
        ctx, "s", DEFAULT_TILES, DEFAULT_GRAPHICS, DEFAULT_VARIANTS
    )


class TestCompositeContrast:
    """The advisory-only contract is STRUCTURAL, not conventional: every
    record ships passed=True hard-coded, and the check name is skipped
    outright by _flip_review_status and derive_qa_warnings — a corrupted
    record can never block, flip, or warn."""

    def test_camouflaged_placement_flags_advisory_record(
        self, tmp_path: Path
    ) -> None:
        checks = _composite(_composite_ctx(tmp_path, [(10, 8)]))
        placement = [c for c in checks if c["target"] == "l1"]
        assert len(placement) == 1
        rec = placement[0]
        assert rec["check"] == "composite_contrast"
        assert rec["subject"] == "enemy:shade@(10,8)"
        assert rec["passed"] is True  # hard-coded, ADVISORY by construction
        assert rec["detail"].startswith("ADVISORY — review l1 near (10,8)")
        assert f"floor {COMPOSITE_MIN_LUMA:.0f}" in rec["detail"]
        assert f"floor {COMPOSITE_MIN_HUE_SEP:.0f}" in rec["detail"]
        # patroller: strip = patrol_range (4) cells around column 10
        assert "patrol strip x in [6,14] cells" in rec["detail"]

    def test_readable_placement_yields_no_record(self, tmp_path: Path) -> None:
        checks = _composite(_composite_ctx(tmp_path, [(10, 8)], bg="#e8e8f0"))
        assert [c["subject"] for c in checks] == ["composite readability ledger"]
        assert "sampled 1 placement(s) across 1 level(s); 0 flagged" in (
            checks[0]["detail"]
        )

    def test_hue_separation_alone_clears_the_flag(self, tmp_path: Path) -> None:
        # deltaL ~9 (under the floor) but red-vs-teal hue separation ~180:
        # camouflage needs BOTH floors broken, so no record.
        enemy = EnemyDefinition(
            enemy_id="shade", archetype="patroller", size=1.0,
            stats={"placeholder_color": "#a03030"},
            behavior={"patrol_range": 4},
        )
        checks = _composite(
            _composite_ctx(tmp_path, [(10, 8)], bg="#1a6060", enemy=enemy)
        )
        assert [c["subject"] for c in checks] == ["composite readability ledger"]

    def test_ledger_always_present_even_on_bare_tree(
        self, tmp_path: Path
    ) -> None:
        checks = run_code_checks(_bare_ctx(tmp_path), "s")
        ledger = [c for c in checks if c["check"] == "composite_contrast"]
        assert len(ledger) == 1
        assert ledger[0]["target"] == "stage:s"
        assert ledger[0]["passed"] is True
        assert "sampled 0 placement(s) across 0 level(s); 0 flagged" in (
            ledger[0]["detail"]
        )

    def test_ledger_counts_enemies_and_items(self, tmp_path: Path) -> None:
        checks = _composite(
            _composite_ctx(tmp_path, [(4, 8), (10, 8)], items=[(16, 5)])
        )
        # items sample too (1-cell ring), against the same camouflaged bg
        assert "item:coin@(16,5)" in [c["subject"] for c in checks]
        assert "sampled 3 placement(s) across 1 level(s); 3 flagged" in (
            checks[-1]["detail"]
        )

    def test_sentry_margin_is_one_cell(self, tmp_path: Path) -> None:
        sentry = EnemyDefinition(
            enemy_id="shade", archetype="sentry", size=1.0,
            stats={"placeholder_color": "#3a3a80"},
            behavior={"patrol_range": 5},  # ignored: sentries hold position
        )
        checks = _composite(_composite_ctx(tmp_path, [(10, 8)], enemy=sentry))
        rec = next(c for c in checks if c["target"] == "l1")
        assert "patrol strip x in [9,11] cells" in rec["detail"]

    def test_level_edge_samples_one_sided(self, tmp_path: Path) -> None:
        # column 0: no left sub-crop exists — the right side alone still
        # measures (and flags) the placement instead of skipping it
        checks = _composite(_composite_ctx(tmp_path, [(0, 8)]))
        rec = next(c for c in checks if c["target"] == "l1")
        assert rec["subject"] == "enemy:shade@(0,8)"
        assert "patrol strip x in [0,4] cells" in rec["detail"]

    def test_missing_skinned_counted_and_skipped(self, tmp_path: Path) -> None:
        checks = _composite(
            _composite_ctx(
                tmp_path, [(10, 8)], level_ids=("l1", "l2"), skip_png=("l2",)
            )
        )
        assert not any(c["target"] == "l2" for c in checks)
        assert "skinned missing for: l2" in checks[-1]["detail"]
        assert "sampled 1 placement(s) across 1 level(s); 1 flagged" in (
            checks[-1]["detail"]
        )

    def test_secret_room_levels_are_sampled(self, tmp_path: Path) -> None:
        checks = _composite(
            _composite_ctx(tmp_path, [(10, 8)], secret_rooms=("l1r1",))
        )
        assert [
            c["target"] for c in checks if c["target"] != "stage:s"
        ] == ["l1", "l1r1"]
        assert "across 2 level(s)" in checks[-1]["detail"]

    def test_never_a_manifest_warning_even_hand_corrupted(
        self, tmp_path: Path
    ) -> None:
        checks = _composite(_composite_ctx(tmp_path, [(10, 8)]))
        report = {"stage_id": "s", "code_checks": checks, "levels": {}}
        assert derive_qa_warnings(report) == []
        # structural, not conventional: hand-corrupted passed=False
        # composite records STILL derive nothing
        corrupted = [dict(c, passed=False) for c in checks]
        assert derive_qa_warnings(
            {"stage_id": "s", "code_checks": corrupted, "levels": {}}
        ) == []

    def test_review_status_untouched(self, tmp_path: Path) -> None:
        ctx = _composite_ctx(tmp_path, [(10, 8)])
        enemy = ctx.bible.enemy_definitions["shade"]
        enemy.review_status = "approved"
        checks = _composite(ctx)
        # even a corrupted failing record aimed straight at the enemy
        # must not flip the review gate — the name-skip is the contract
        corrupted = dict(checks[0], passed=False, target="enemy:shade")
        _flip_review_status(ctx, [*checks, corrupted])
        assert enemy.review_status == "approved"


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
        # Every level INCLUDING rolled secret rooms gets a QA row.
        assert sorted(report["levels"]) == sorted(ctx.bible.levels)
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
        ctx = _run_slice(
            out, vlm_judge=FakeVLMBackend(lambda prompt, images: "not json")
        )
        report = json.loads((out / "review/ashen_depths/qa_report.json").read_text())
        assert all(
            entry["error"] == "verdict never validated after retries"
            for entry in report["levels"].values()
        )
        warnings = json.loads((out / "manifest.json").read_text())["warnings"]
        assert sum(1 for w in warnings if "no verdict" in w) == len(
            ctx.bible.levels
        )

    def test_judge_sees_five_images_per_level(self, tmp_path: Path) -> None:
        judge = _fake_judge()
        ctx = _run_slice(tmp_path / "run", vlm_judge=judge)
        # One judgment per level (secret rooms included), no retries.
        assert len(judge.calls) == len(ctx.bible.levels)
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


class TestAnimationScaleCheck:
    """The check whose absence let the per-state sizing bug ship: every other
    animation check is PER STATE, so none of them could see that the actor was
    a different size in each one. Frames share one square sized from the whole
    actor, so at most the single largest pose may reach the cell edge."""

    def _two_states(
        self, tmp_path: Path, idle: tuple, jump: tuple
    ) -> dict:
        """Two states on disk, each an opaque rect of the given (w, h)
        bottom-anchored in a 32px cell."""
        anim: dict = {"states": {}}
        for state, (w, h) in (("idle", idle), ("jump", jump)):
            x0 = (32 - w) // 2
            one = _write_strip(
                tmp_path, [(x0, 32 - h, x0 + w - 1, 31)] * 2, state=state
            )
            anim["states"][state] = one["states"][state]
        return anim

    def test_flags_states_that_each_fill_the_cell(self, tmp_path: Path) -> None:
        # The shipped pathology, measured on plat_lantern_paid: a WIDE idle
        # (32x22) and a TALL jump (26x32) — both stretched to their own cell,
        # so the actor grows ~45% the instant it leaves the ground.
        anim = self._two_states(tmp_path, idle=(32, 22), jump=(26, 32))
        checks = _animation_checks(_strip_ctx(tmp_path), "enemy:x", anim)
        rec = next(c for c in checks if c["check"] == "animation_scale")
        assert rec["passed"] is False
        assert "idle, jump" in rec["detail"]
        assert rec["subject"] == "states"

    def test_one_state_at_the_edge_is_correct(self, tmp_path: Path) -> None:
        # Correctly sized: the largest pose defines the square, everything
        # else is strictly smaller.
        anim = self._two_states(tmp_path, idle=(28, 20), jump=(24, 32))
        checks = _animation_checks(_strip_ctx(tmp_path), "enemy:x", anim)
        rec = next(c for c in checks if c["check"] == "animation_scale")
        assert rec["passed"] is True
        assert "'jump'" in rec["detail"]

    def test_single_state_actors_are_not_judged(self, tmp_path: Path) -> None:
        # One state can't be inconsistent with itself — no record at all.
        anim = _write_strip(tmp_path, [(0, 0, 31, 31)] * 2, state="idle")
        checks = _animation_checks(_strip_ctx(tmp_path), "enemy:x", anim)
        assert not any(c["check"] == "animation_scale" for c in checks)
