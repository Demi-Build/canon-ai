"""The dungeon estimator (row P0-7): the count function's arithmetic against
a real fake-mode run of ``compose_pipeline``, the anchor sanity, the
``world estimate --template dungeon`` verb's shape (== the platformer's +
``template``), and the run hook."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

import pytest

from canon import LLMClient, run_pipeline
from canon.backends.testing import FakeImageBackend, FakeLLMBackend, FakeMusicBackend, FakeSFXBackend
from canon.estimator import ADDITIVE_KEYS, VLM_ADDITIVE_KEYS, strip_additive
from canon.packs.dungeon.compose import compose_pipeline
from canon.packs.dungeon.estimate import ESTIMATOR, count_dungeon, estimate_cradle, estimate_run
from canon.packs.dungeon.fakes import make_fake_responder

REPO = Path(__file__).resolve().parents[1]
CANON = [sys.executable, "-m", "canon.cli.main"]

FULL_API = {"llm": "anthropic", "image": "fal", "music": "lyria", "sfx": "elevenlabs", "vlm": "none"}


def _task_of(label: str) -> str:
    if label.startswith("spell_pool"):
        return "spell_pool"
    if label.endswith(":spell") or label.endswith(":ability"):
        return "classes:loadout"
    return label


class _CountingClient(LLMClient):
    def __init__(self, backend, log: list[str]) -> None:
        super().__init__(backend)
        self._log = log

    def generate(self, request, *, phase=None):
        self._log.append(phase or self.phase)
        return super().generate(request, phase=phase)


def _fake_run(num_maps: int) -> tuple[Counter, int, int, int]:
    """Run the real pipeline with the fake responder + fake asset backends:
    LLM calls per task, images, music, sfx."""
    log: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        phases, ctx = compose_pipeline(seed="count", num_maps=num_maps, output_dir=td)
        ctx.llm = _CountingClient(FakeLLMBackend(make_fake_responder(num_maps)), log)
        img, mus, sfx = FakeImageBackend(), FakeMusicBackend(), FakeSFXBackend()
        ctx.image_backend, ctx.music_backend, ctx.sfx_backend = img, mus, sfx
        for phase in phases:
            if type(phase).__name__ == "AssetPhase":
                phase.skip_image = phase.skip_music = phase.skip_sfx = False
        run_pipeline(phases, ctx)
    return Counter(_task_of(p) for p in log), len(img.calls), len(mus.calls), len(sfx.calls)


def _counts(**counts: int) -> dict:
    return count_dungeon({"cost_model": ESTIMATOR.cost_model(), "counts": counts}, None)


class TestCountFunction:
    @pytest.mark.parametrize("num_maps", [1, 3])
    def test_matches_a_fake_run_of_the_real_pipeline(self, num_maps: int) -> None:
        """Every LLM task the pipeline fires is counted, at the call count
        the phases issue; every asset the AssetPhase generates is counted.
        (The fake responder fails the batched JSON-array validation, so the
        loadout/pool calls retry ×3 in fake mode — a real model returns the
        array first time; the count function prices the real 1-per-group.)"""
        by_task, images, music, sfx = _fake_run(num_maps)
        counted = _counts(rooms=num_maps)
        llm = counted["llm"]
        for task in ("story", "classes", "db:item", "db:monster", "db:npc", "db:event", "db:quest",
                     "dialogue", "narrative"):
            assert llm[task] == by_task[task], (task, llm[task], by_task[task])
        assert set(llm) == set(by_task)
        assert llm["classes:loadout"] == 5 and by_task["classes:loadout"] == 15
        assert llm["spell_pool"] == 4 and by_task["spell_pool"] == 12
        assert counted["images"] == images
        assert counted["music"] == music
        assert counted["sfx"] == sfx
        assert counted["vlm"] == {}

    def test_arithmetic_on_a_known_config(self) -> None:
        c = _counts(rooms=3)  # P.4.4 defaults: npc 2 / monster 2 / item 3 / event 4 / quest 2 / class 4
        assert c["llm"] == {
            "story": 1, "classes": 4, "classes:loadout": 5, "spell_pool": 4,
            "db:item": 9, "db:monster": 6, "db:npc": 6, "db:event": 12, "db:quest": 6,
            "dialogue": 15,  # per room: the giver's 4 variants + 1 for the other NPC
            "narrative": 6,  # synopsis + 3 intros + victory + defeat
        }
        assert c["images"] == 3 * 13 + 4 + 3 == 46
        assert c["music"] == 5 + 3 and c["sfx"] == 12 + 3
        assert sum(c["llm"].values()) == 74

    def test_counts_scale_and_degrade(self) -> None:
        big = _counts(rooms=10, npc=3, quest=0)
        assert big["llm"]["dialogue"] == 30  # no quests → no givers → one tree per NPC
        assert big["music"] == 5 + 8 and big["sfx"] == 12 + 8  # 8 environments cycle
        assert "db:quest" not in big["llm"]
        none = _counts(rooms=2, npc=0, monster=0, item=0, event=0, quest=0)
        assert "dialogue" not in none["llm"] and "db:npc" not in none["llm"]
        assert none["images"] == 4 + 2  # classes + environments only
        assert _counts(**{"class": 0})["llm"].get("classes") is None
        assert _counts(**{"class": 99})["llm"]["classes"] == 4  # compose slices the loadout list


class TestAnchor:
    def test_three_map_full_api_estimate(self) -> None:
        """The 3-map full-API forecast (anthropic DEFAULT_MODEL, fal, Lyria,
        ElevenLabs) — printed and recorded. Recorded 2026-09-01: best $3.84 /
        worst $6.25 (74 LLM calls $0.81 best, 46 portraits $1.79, 8 tracks
        $0.64, 15 SFX $0.60).

        The $30/3-map anchor (examples/run_mazeworld_full.py; master §5 open
        item) is MazeWorld's ORIGINAL pipeline's measured run
        (MazeWorld/data/generation_stats.json: $33.76 = 688 images $27.38 +
        211 LLM calls $4.49 + 38 audio units $1.88). Canon's dungeon composes
        46 portraits for 3 maps, not 688, so its honest forecast is an order
        of magnitude under the anchor; the per-UNIT rates are what the
        anchor checks (``test_unit_rates_reproduce_the_anchor``). The band
        here is the plausible one for THIS pipeline — the spec's $10 floor
        assumed the anchor's image count (flagged in the row report)."""
        est = estimate_cradle("world", counts={"rooms": 3}, backends=FULL_API)
        best, worst = est["total_usd"]["best"], est["total_usd"]["worst"]
        print(f"\nDUNGEON 3-map full-API estimate: best ${best:.2f} / worst ${worst:.2f} "
              f"(llm {est['llm']['calls']:.0f} calls ${est['llm']['usd']['best']}, "
              f"images {est['assets']['images']['count']} ${est['assets']['images']['usd']}, "
              f"music {est['assets']['music']['count']} ${est['assets']['music']['usd']}, "
              f"sfx {est['assets']['sfx']['count']} ${est['assets']['sfx']['usd']})")
        assert 1.0 <= best <= 90.0, best
        assert best < worst <= 90.0
        assert est["assets"]["images"]["count"] == 46
        assert est["llm"]["calls"] == 74
        assert est["warnings"] == []

    def test_unit_rates_reproduce_the_anchor(self) -> None:
        """The estimator's unit prices × the anchor run's MEASURED unit
        counts (688 images, 10 tracks, 28 SFX, 211 LLM calls at this
        pipeline's mean per-call cost) land in the $10–$90 band around the
        $30 anchor."""
        est = estimate_cradle("world", counts={"rooms": 3}, backends=FULL_API)
        per_image = est["assets"]["images"]["usd"] / est["assets"]["images"]["count"]
        per_track = est["assets"]["music"]["usd"] / est["assets"]["music"]["count"]
        per_sfx = est["assets"]["sfx"]["usd"] / est["assets"]["sfx"]["count"]
        per_call = est["llm"]["usd"]["best"] / est["llm"]["calls"]
        anchor = 688 * per_image + 10 * per_track + 28 * per_sfx + 211 * per_call
        print(f"\nanchor reproduced from measured unit counts: ${anchor:.2f} (anchor run: $33.76)")
        assert 10.0 <= anchor <= 90.0, anchor

    def test_model_override_and_backend_mask(self) -> None:
        fake = estimate_cradle("world", backends={"llm": "fake", "image": "fake", "music": "none", "sfx": "none"})
        assert fake["total_usd"] == {"best": 0.0, "worst": 0.0}
        assert fake["assets"]["images"]["count"] == 46  # counts survive the mask
        sonnet5 = estimate_cradle("world", backends=FULL_API, model="claude-sonnet-5")
        default = estimate_cradle("world", backends=FULL_API)
        assert sonnet5["llm"]["usd"]["best"] < default["llm"]["usd"]["best"]
        assert sonnet5["model"] == "claude-sonnet-5"
        assert all(t["model"] == "claude-sonnet-5" for t in sonnet5["llm"]["by_task"].values())
        unpriced = estimate_cradle("world", backends=FULL_API, model="not-a-model")
        assert unpriced["llm"]["usd"]["best"] == 0.0
        assert any("not-a-model" in w and "canon.pricing.LLM" in w for w in unpriced["warnings"])


def _cli(*args: str) -> dict:
    proc = subprocess.run([*CANON, *args], capture_output=True, text=True, cwd=REPO)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _shape(d: object) -> object:
    """Key structure only (dict keys recursively, leaves as their type)."""
    if isinstance(d, dict):
        return {k: _shape(v) for k, v in d.items()}
    return type(d).__name__


class TestWorldEstimateVerb:
    def test_dungeon_shape_is_the_platformer_shape_plus_template(self) -> None:
        backends = ["--llm-backend", "anthropic", "--image-backend", "fal",
                    "--music-backend", "lyria", "--sfx-backend", "elevenlabs"]
        plat = _cli("world", "estimate", *backends)["estimate"]
        dung = _cli("world", "estimate", "--template", "dungeon", *backends)["estimate"]
        assert plat["template"] == "platformer" and dung["template"] == "dungeon"
        assert list(plat) == list(dung)
        for block in ("images", "music", "sfx"):
            assert _shape(plat["assets"][block]) == _shape(dung["assets"][block])
            assert set(ADDITIVE_KEYS) <= set(dung["assets"][block])
        assert set(plat["llm"]) == set(dung["llm"])
        plat_task = next(iter(plat["llm"]["by_task"].values()))
        dung_task = next(iter(dung["llm"]["by_task"].values()))
        assert _shape(plat_task) == _shape(dung_task)
        assert set(VLM_ADDITIVE_KEYS) <= set(plat["assets"]["vlm"]) or plat["assets"]["vlm"] == {}
        assert dung["assets"]["vlm"] == {}
        # the pre-P0-7 contract (cradle's CostEstimate) is the same for both
        assert set(strip_additive(dung)) == {"scope", "backends", "llm", "assets", "total_usd", "warnings"}
        assert dung["scope"] == "world" and dung["backends"]["llm"] == "anthropic"
        assert dung["low"] == dung["total_usd"]["best"] and dung["high"] == dung["total_usd"]["worst"]
        assert dung["backend"] == "anthropic" and dung["unitCount"] == 74 + 46 + 8 + 15

    def test_dungeon_count_flags_and_defaults(self) -> None:
        default = _cli("world", "estimate", "--template", "dungeon", "--llm-backend", "anthropic")["estimate"]
        assert default["llm"]["calls"] == 74  # P.4.4 defaults
        bigger = _cli(
            "world", "estimate", "--template", "dungeon", "--llm-backend", "anthropic",
            "--rooms", "5", "--npcs", "3", "--monsters", "1", "--items", "2",
            "--events", "1", "--quests", "1", "--classes", "2",
        )["estimate"]
        assert bigger["llm"]["by_task"]["db:npc"]["calls"] == 15
        assert bigger["llm"]["by_task"]["db:item"]["calls"] == 10
        assert bigger["llm"]["by_task"]["classes"]["calls"] == 2
        assert bigger["assets"]["images"]["count"] == 5 * 8 + 2 + 5
        assert bigger["assets"]["images"]["usd"] == 0.0  # image backend defaults to fake
        modelled = _cli(
            "world", "estimate", "--template", "dungeon", "--llm-backend", "anthropic",
            "--model", "claude-haiku-4-5",
        )["estimate"]
        assert modelled["model"] == "claude-haiku-4-5"
        assert modelled["total_usd"]["best"] < default["total_usd"]["best"]

    def test_platformer_default_is_unchanged_and_model_flag_is_noted(self) -> None:
        from canon.packs.platformer.estimate import estimate_cradle as plat_estimate

        via_cli = _cli("world", "estimate", "--llm-backend", "anthropic", "--image-backend", "fal")["estimate"]
        direct = plat_estimate(
            "world", counts={}, backends={"llm": "anthropic", "image": "fal", "music": "none",
                                          "sfx": "none", "vlm": "none"},
        )
        assert via_cli == direct
        noted = _cli("world", "estimate", "--llm-backend", "anthropic", "--model", "claude-opus-5")["estimate"]
        assert any("--model" in w and "models.json" in w for w in noted["warnings"])
        assert noted["total_usd"] == direct["total_usd"] or noted["total_usd"]["best"] > 0

    def test_foreign_count_flags_are_ignored_WITH_a_reason(self) -> None:
        """Doctrine 4: a count flag that belongs to the other template is
        disabled with a reason, never silently dropped (the same treatment
        ``--model`` gets on the platformer)."""
        dung = _cli(
            "world", "estimate", "--template", "dungeon", "--llm-backend", "anthropic",
            "--stages", "9", "--levels", "9", "--rooms", "3",
        )["estimate"]
        note = next(w for w in dung["warnings"] if "ignored" in w)
        assert "--stages" in note and "--levels" in note and "dungeon" in note
        assert "--rooms" in note.split("counts are")[1]
        assert dung["llm"]["calls"] == 74  # the dropped flags changed nothing
        plat = _cli("world", "estimate", "--llm-backend", "anthropic", "--rooms", "5")["estimate"]
        assert any("--rooms" in w and "platformer" in w for w in plat["warnings"])
        clean = _cli("world", "estimate", "--template", "dungeon", "--llm-backend", "anthropic")["estimate"]
        assert clean["warnings"] == []

    def test_unknown_template_is_a_structured_error(self) -> None:
        proc = subprocess.run(
            [*CANON, "world", "estimate", "--template", "nope"], capture_output=True, text=True, cwd=REPO,
        )
        assert proc.returncode != 0
        err = json.loads(proc.stderr)
        assert "nope" in err["error"] and "dungeon" in err["error"]


class TestRunHook:
    def test_estimate_run_prices_the_config_shape(self, tmp_path: Path) -> None:
        phases, ctx = compose_pipeline(seed="hook", num_maps=2, output_dir=tmp_path, counts={"npc": 1})
        result = estimate_run(ctx, [], ctx.bible)
        assert result["mode"] == "fresh" and result["template"] == "dungeon"
        assert result["llm"]["by_task"]["db:npc"]["calls"] == 2
        assert result["assets"]["images"]["backend"] == "fal"  # real-API rates, no mask
        assert result["total_usd"]["best"] > 0
        assert result["low"] == result["total_usd"]["best"]
