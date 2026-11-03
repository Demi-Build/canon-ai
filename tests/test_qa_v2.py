"""QA v2 — staleness-aware re-judging + play-scale crops.

Contracts under test:
- A re-run whose renders (and judge model) are unchanged CARRIES every
  level verdict — zero VLM level calls — and the report stays
  byte-identical (no carried flag in report content; the skip is
  observability-only).
- Changing ONE level's skinned render re-judges exactly that level.
- A different judge model re-judges everything.
- Every judged level ships deterministic play-scale crops (the
  view_cells x view_rows camera window around spawn/exit) that the
  judge also sees, hashed into ``judged_inputs``.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from canon.packs.platformer.vlm_qa import VlmQaPhase
from tests.test_vlm_qa import _fake_judge, _run_slice

REPORT = "review/ashen_depths/qa_report.json"


def _level_judge_calls(judge) -> list[str]:
    """Level ids the judge was actually called for (vlm_qa task only)."""
    lids = []
    for call in judge.calls:
        prompt = call["prompt"]
        if "### TASK: vlm_qa" in prompt:
            lids.append(prompt.split("### LEVEL: ")[1].split("\n")[0])
    return lids


class TestStaleness:
    def test_unchanged_rerun_carries_all_verdicts(self, tmp_path: Path) -> None:
        out = tmp_path / "run"
        first = _fake_judge()
        ctx = _run_slice(out, vlm_judge=first)
        # Every level — rolled secret rooms included — is judged once.
        assert sorted(_level_judge_calls(first)) == sorted(ctx.bible.levels)
        report_bytes = (out / REPORT).read_bytes()

        second = _fake_judge()
        _run_slice(out, vlm_judge=second)
        assert _level_judge_calls(second) == [], (
            "unchanged renders must carry verdicts without VLM calls"
        )
        assert (out / REPORT).read_bytes() == report_bytes, (
            "a carried report must stay byte-identical"
        )

    def test_changed_render_rejudges_exactly_that_level(
        self, tmp_path: Path
    ) -> None:
        """Direct phase invocation: a full pipeline re-run would first
        REGENERATE the renders (deterministically overwriting the tweak),
        so the stale-render scenario is the resume where a level's render
        genuinely changed — modelled here by tweaking the PNG and running
        the QA phase alone."""
        out = tmp_path / "run"
        ctx = _run_slice(out, vlm_judge=_fake_judge())

        skinned = out / "review/ashen_depths/l2_skinned.png"
        with Image.open(skinned) as img:
            img = img.convert("RGB")
            img.putpixel((0, 0), (255, 0, 255))
            img.save(skinned)

        second = _fake_judge()
        VlmQaPhase(judge=second).run(ctx)
        assert _level_judge_calls(second) == ["l2"]

    def test_model_change_rejudges_everything(self, tmp_path: Path) -> None:
        out = tmp_path / "run"
        ctx = _run_slice(out, vlm_judge=_fake_judge())

        second = _fake_judge()
        second.model = "fake-vlm-v2"  # instance override of the class attr
        _run_slice(out, vlm_judge=second)
        assert sorted(_level_judge_calls(second)) == sorted(ctx.bible.levels)

    def test_report_entries_carry_judged_inputs(self, tmp_path: Path) -> None:
        out = tmp_path / "run"
        _run_slice(out, vlm_judge=_fake_judge())
        report = json.loads((out / REPORT).read_text())
        for entry in report["levels"].values():
            judged = entry["judged_inputs"]
            assert set(judged) == {
                "block", "skinned", "legend", "play_spawn", "play_exit",
            }
            assert all(v.startswith("sha256:") for v in judged.values())


class TestPlayScaleCrops:
    def test_crops_written_at_camera_window_geometry(
        self, tmp_path: Path
    ) -> None:
        """Windows honor the PER-LEVEL view overrides (the fake stage
        plan marks the last level 'vista' = 30 cells), falling back to
        the game-global GraphicsSpec defaults (20 x 16)."""
        out = tmp_path / "run"
        ctx = _run_slice(out, vlm_judge=_fake_judge())

        seen_override = False
        for lid in ("l1", "l2", "l3"):
            level = ctx.bible.levels[lid]
            view_cells = level.view_cells or 20
            view_rows = level.view_rows or 16
            seen_override = seen_override or level.view_cells is not None
            with Image.open(out / f"review/ashen_depths/{lid}_skinned.png") as s:
                px = s.size[0] // level.grid_width
            expected = (
                min(view_cells, level.grid_width) * px,
                min(view_rows, level.grid_height) * px,
            )
            for name in ("play_spawn", "play_exit"):
                path = out / f"review/ashen_depths/{lid}_{name}.png"
                assert path.exists(), f"{lid} missing {name} crop"
                with Image.open(path) as crop:
                    assert crop.size == expected, (lid, name, crop.size)
        assert seen_override, "expected the fake plan's vista override"

    def test_crops_are_deterministic_across_runs(self, tmp_path: Path) -> None:
        _run_slice(tmp_path / "a", vlm_judge=_fake_judge())
        _run_slice(tmp_path / "b", vlm_judge=_fake_judge())
        rel = "review/ashen_depths/l1_play_spawn.png"
        assert (tmp_path / "a" / rel).read_bytes() == (
            tmp_path / "b" / rel
        ).read_bytes()

    def test_no_judge_no_crops(self, tmp_path: Path) -> None:
        out = tmp_path / "run"
        _run_slice(out, vlm_judge=None)
        assert not list(out.glob("review/*/*_play_*.png"))
