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

import pytest

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


# ---------------------------------------------------------------------------
# The two PER-UNIT scopes — one asset target, one db row (row D7)
# ---------------------------------------------------------------------------


def _stats(tree: Path, **fields: object) -> None:
    """Overwrite a tree's generation_stats.json with the recorded figures a
    forecast calibrates from. Unit-level tests of `actuals_by_unit` only —
    anything asserting that a REAL run calibrates goes through
    :func:`_measured_image_run`, which produces the file the way a run does."""
    (tree / "generation_stats.json").write_text(json.dumps(fields))


def _measured_image_run(
    tree: Path, monkeypatch, enemy_id: str, *, usd: float, backend: str = "pixellab"
) -> tuple[int, float]:
    """Run the REAL sprite art phase on a metering producer and persist the
    ``generation_stats.json`` that run produced (through the production
    serializer, not a hand-written dict). Returns ``(units, spent)``.

    The paid figure is forced onto the FAKE backend and the recorded backend
    name is the paid one — no provider is called, and what is under test is the
    whole chain: producer meter → ``ctx.stats`` → the stats file → the
    forecast's per-unit calibration.
    """
    from canon.backends.testing import FakeImageBackend
    from canon.packs.platformer.art_phases import SpriteArtPhase
    from canon.packs.platformer.ops import _sprite_bible, load_pack, make_ctx
    from canon.packs.platformer.tileset_art import build_image_producer
    from canon.pipeline.stats import GenerationStats

    monkeypatch.setattr(FakeImageBackend, "last_cost", usd, raising=False)
    info = load_pack(tree)
    stats = GenerationStats(image_backend=backend)
    producer = build_image_producer("fake", None, None, seed=info.seed)
    ctx = make_ctx(info, bible=_sprite_bible(info, "enemy", enemy_id), stats=stats)
    SpriteArtPhase(producer=producer, graphics=info.graphics).run(ctx)
    (tree / "generation_stats.json").write_text(json.dumps(stats.to_dict()))
    return stats.image_successes, stats.image_cost_usd


class TestUnitCalibration:
    """`actuals_by_unit` — the per-unit sibling of `actuals_by_task`."""

    def test_a_real_run_sets_the_measured_unit_price(self, tmp_path: Path) -> None:
        from canon.estimator import actuals_by_unit

        _stats(tmp_path, image_backend="pixellab", image_successes=40,
               image_cost_usd=1.60, music_backend="lyria", music_succeeded=4,
               audio_cost_usd=0.32)
        got = actuals_by_unit(tmp_path)
        assert got["image"] == {"usd": 0.04, "backend": "pixellab"}
        assert got["music"] == {"usd": 0.08, "backend": "lyria"}

    def test_a_fake_run_calibrates_nothing(self, tmp_path: Path) -> None:
        """The whole point of excluding them: a fake run produced units and
        spent $0, so its "measured" unit price is a lie the forecast would
        then repeat as $0 on a paid backend."""
        from canon.estimator import actuals_by_unit

        _stats(tmp_path, image_backend="fake", image_successes=40, image_cost_usd=0.0)
        assert actuals_by_unit(tmp_path) == {}
        assert actuals_by_unit(tmp_path / "nope") == {}

    def test_a_mixed_audio_run_never_invents_a_split(self, tmp_path: Path) -> None:
        """music and SFX share ONE audio_cost_usd bucket — a run that made both
        cannot say what either cost, so neither calibrates."""
        from canon.estimator import actuals_by_unit

        _stats(tmp_path, music_backend="lyria", music_succeeded=4,
               sfx_backend="elevenlabs", sfx_succeeded=12, audio_cost_usd=0.80)
        assert actuals_by_unit(tmp_path) == {}

    def test_a_music_only_run_records_the_denominator_it_spent_on(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """The audio half of the same regression: the platformer accumulated
        ``audio_cost_usd`` but never ``music_succeeded``/``sfx_succeeded``, so
        every audio forecast divided real dollars by a zero count and silently
        fell back to the table. A music-only run is the one that CAN calibrate
        (a mixed run shares one bucket, and refuses)."""
        from canon.backends.testing import FakeMusicBackend
        from canon.bible.models import Bible
        from canon.estimator import actuals_by_unit
        from canon.packs.platformer.audio_phases import AudioPhase, build_music_producer
        from canon.packs.platformer.ops import load_pack, make_ctx
        from canon.pipeline.stats import GenerationStats

        out = tmp_path / "game"
        _build_tree(out)
        sid = sorted(p.name for p in (out / "stage").iterdir())[0]
        monkeypatch.setattr(FakeMusicBackend, "last_cost", 0.36, raising=False)

        info = load_pack(out)
        stats = GenerationStats(music_backend="lyria")
        bible = Bible.empty(info.seed)
        bible.world, bible.stages[sid] = info.world, info.stages[sid]
        ctx = make_ctx(info, bible=bible, stats=stats)
        AudioPhase(music_producer=build_music_producer("fake")).run(ctx)
        (out / "generation_stats.json").write_text(json.dumps(stats.to_dict()))

        assert stats.music_succeeded == 1 and stats.sfx_succeeded == 0
        assert actuals_by_unit(out) == {"music": {"usd": 0.36, "backend": "lyria"}}

    def test_a_figure_measured_on_one_backend_never_prices_another(
        self, tmp_path: Path
    ) -> None:
        from canon.estimator import _unit_actual, actuals_by_unit

        _stats(tmp_path, image_backend="pixellab", image_successes=10, image_cost_usd=1.0)
        actuals = actuals_by_unit(tmp_path)
        assert _unit_actual(actuals, "image", "pixellab") == 0.1
        assert _unit_actual(actuals, "image", "fal") is None


class TestResolutionPricing:
    def test_a_size_priced_row_bills_at_the_tier_that_holds_the_request(self) -> None:
        """A 512px request on a 1K-base model is the 0.5K rate — the row's
        usd..usd_high span exists only while the size is unknown."""
        from canon.estimator import _resolution_price

        row = {"usd": 0.08, "usd_high": 0.16,
               "by_resolution": {"0.5K": 0.06, "1K": 0.08, "2K": 0.12, "4K": 0.16}}
        assert _resolution_price(row, 512) == 0.06
        assert _resolution_price(row, 1024) == 0.08
        assert _resolution_price(row, 9000) == 0.16  # above every tier → the top
        assert _resolution_price(row, None) is None
        assert _resolution_price({"usd": 0.039}, 512) is None  # flat-priced row


class TestAssetScope:
    """`generate_asset`'s scope: ONE target's units, counted off the phases."""

    def _ids(self, out: Path) -> tuple[str, str]:
        return (
            sorted(p.stem for p in (out / "enemy").glob("*.json"))[0],
            sorted(p.name for p in (out / "stage").iterdir())[0],
        )

    def test_counts_come_from_the_pack_not_a_constant(self, tmp_path: Path) -> None:
        from canon.packs.platformer.audio_phases import SFX_EVENTS
        from canon.packs.platformer.estimate import estimate_cradle
        from canon.packs.platformer.ops import load_pack

        out = tmp_path / "game"
        _build_tree(out)
        eid, sid = self._ids(out)
        graphics = load_pack(out).graphics

        sprite = estimate_cradle("asset", pack_dir=out, target=f"enemy:{eid}",
                                 backends={"image": "fal"})
        assert sprite["assets"]["images"]["count"] == 1
        assert sprite["unitCount"] == 1 and sprite["backend"] == "fal"

        backdrop = estimate_cradle("asset", pack_dir=out, target=f"backdrop:{sid}",
                                   backends={"image": "fal"})
        assert backdrop["assets"]["images"]["count"] == graphics.backdrop_bands

        audio = estimate_cradle("asset", pack_dir=out, target=f"audio:{sid}",
                                backends={"music": "lyria", "sfx": "elevenlabs"})
        assert audio["assets"]["music"]["count"] == 1
        assert audio["assets"]["sfx"]["count"] == len(SFX_EVENTS)
        assert audio["backend"] == "lyria"  # the primary is what the target makes

    def test_the_sprite_count_is_the_phases_own_total_not_a_copy_of_it(
        self, tmp_path: Path
    ) -> None:
        """REGRESSION: the estimator used to re-derive SpriteArtPhase's
        candidate sum instead of calling it, so the two could drift silently
        and the priciest tool would under-quote. Run the real phase and assert
        the forecast priced exactly the units it generated."""
        from canon.packs.platformer.art_phases import SpriteArtPhase
        from canon.packs.platformer.estimate import estimate_cradle
        from canon.packs.platformer.ops import _sprite_bible, load_pack, make_ctx
        from canon.packs.platformer.tileset_art import build_image_producer

        out = tmp_path / "game"
        _build_tree(out)
        eid, _sid = self._ids(out)

        info = load_pack(out)
        phase = SpriteArtPhase(
            producer=build_image_producer("fake", None, None, seed=info.seed),
            graphics=info.graphics,
        )
        phase.run(make_ctx(info, bible=_sprite_bible(info, "enemy", eid)))

        est = estimate_cradle("asset", pack_dir=out, target=f"enemy:{eid}",
                              backends={"image": "fal"})
        assert est["assets"]["images"]["count"] == phase._total

    def test_an_unmakeable_target_raises_rather_than_pricing_zero(
        self, tmp_path: Path
    ) -> None:
        """Unknown must read as unknown: a target the pack does not have is an
        ERROR the caller renders as "not estimated", never a confident $0."""
        import pytest

        from canon.packs.platformer.estimate import estimate_cradle

        out = tmp_path / "game"
        _build_tree(out)
        with pytest.raises(FileNotFoundError):
            estimate_cradle("asset", pack_dir=out, target="enemy:not_a_thing",
                            backends={"image": "fal"})
        with pytest.raises(ValueError):
            estimate_cradle("asset", pack_dir=out, target="quest:1",
                            backends={"image": "fal"})
        with pytest.raises(ValueError):
            # An audio target with neither backend generates nothing at all.
            estimate_cradle("asset", pack_dir=out,
                            target=f"audio:{self._ids(out)[1]}", backends={})
        with pytest.raises(ValueError):
            # …and an art target with no image backend does not run either.
            estimate_cradle("asset", pack_dir=out,
                            target=f"enemy:{self._ids(out)[0]}", backends={"music": "lyria"})

    def test_the_packs_own_measured_run_beats_the_published_range(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """The D7 rule: prefer calibration over the table. PixelLab publishes
        $0.008–$0.185 per image; a pack that has actually paid knows better,
        and the estimate says which of the two it used.

        The recorded run is a REAL ``SpriteArtPhase`` against a metering
        producer, serialized through ``GenerationStats.to_dict`` — the shape a
        run really writes. Hand-writing those fields hid the bug this covers:
        the platformer accumulated ``image_cost_usd`` but no ``image_successes``
        at all, so the denominator was always 0 and this calibration could
        never fire on the only pack that asks for it.
        """
        from canon.packs.platformer.estimate import estimate_cradle

        out = tmp_path / "game"
        _build_tree(out)
        eid, _sid = self._ids(out)
        units, spent = _measured_image_run(out, monkeypatch, eid, usd=0.055)
        assert units == 1 and spent == pytest.approx(0.055), "the phase must record its units"

        measured = estimate_cradle("asset", pack_dir=out, target=f"enemy:{eid}",
                                   backends={"image": "pixellab"})
        assert measured["calibration"] == "actuals"
        assert measured["low"] == pytest.approx(spent / units)
        assert "measured from this pack" in measured["unitLabel"]

        # A different backend than the one measured falls back to its table row.
        other = estimate_cradle("asset", pack_dir=out, target=f"enemy:{eid}",
                                backends={"image": "fal"})
        assert other["calibration"] == "defaults"
        assert "default rates" in other["unitLabel"]

    def test_a_measured_image_price_still_quotes_a_ceiling_above_it(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """REGRESSION: calibration used to collapse the image band to a point
        (``low == high == measured``), so the card's "spend up to $X" promised
        a ceiling the run can exceed — a measurement is the MEAN of what the
        producer billed, and it is metered per BILLED CALL, not per sprite
        (``_alpha_gated`` retries and the content-policy retry make one sprite
        several billed calls).

        The measured path now carries the same ``best × (1 + worst_retries)``
        headroom ``price_llm`` already applies to its calibrated tokens, from
        the same cost-model knob — so the two lanes of one estimate agree that
        a calibrated number owes a worst case.
        """
        from canon.packs.platformer.estimate import ESTIMATOR as PACK_ESTIMATOR
        from canon.packs.platformer.estimate import estimate_cradle

        out = tmp_path / "game"
        _build_tree(out)
        eid, _sid = self._ids(out)
        units, spent = _measured_image_run(out, monkeypatch, eid, usd=0.055)
        worst_mult = 1 + int(PACK_ESTIMATOR.cost_model()["worst_retries"])

        est = estimate_cradle("asset", pack_dir=out, target=f"enemy:{eid}",
                              backends={"image": "pixellab"})
        images = est["assets"]["images"]
        assert images["low"] == pytest.approx(spent / units)
        assert images["high"] == pytest.approx((spent / units) * worst_mult)
        assert est["high"] > est["low"], "a measured mean is not a ceiling"

    def test_a_measured_audio_price_is_a_point_not_a_retry_band(
        self, tmp_path: Path
    ) -> None:
        """The retry headroom is the IMAGE lane's alone. `_add_audio_cost`
        counts one billed call per clip at a flat per-call rate, so a measured
        music $/unit really is a point — widening it would over-quote."""
        from canon.packs.platformer.estimate import estimate_cradle

        out = tmp_path / "game"
        _build_tree(out)
        _stats(out, music_succeeded=3, audio_cost_usd=0.36, music_backend="lyria")

        est = estimate_cradle("asset", pack_dir=out, target=f"audio:{self._ids(out)[1]}",
                              backends={"music": "lyria"})
        assert est["calibration"] == "actuals"
        assert est["assets"]["music"]["low"] == est["assets"]["music"]["high"] \
            == pytest.approx(0.12)

    def test_a_measurement_never_prices_a_model_the_run_did_not_use(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """``generation_stats.json`` records the BACKEND, never the model, so a
        measured $/unit only speaks for that backend's default model. A call
        naming a different one prices off that model's own published row."""
        from canon import pricing
        from canon.packs.platformer.estimate import estimate_cradle

        out = tmp_path / "game"
        _build_tree(out)
        eid, _sid = self._ids(out)
        _measured_image_run(out, monkeypatch, eid, usd=0.055)

        named = estimate_cradle("asset", pack_dir=out, target=f"enemy:{eid}",
                                backends={"image": "pixellab"}, model="pixellab/bitforge")
        row = pricing.IMAGE["pixellab/bitforge"]
        assert named["model"] == "pixellab/bitforge"
        assert named["low"] == pytest.approx(row["usd"])
        assert named["high"] == pytest.approx(row["usd_high"])
        assert named["calibration"] == "defaults", "the measurement was another model's"

    def test_the_generation_size_picks_the_rows_resolution_tier(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """"How many units AT WHAT SIZE": the pack's own gen_px is what a
        size-priced SKU bills at — not the row's whole base-to-4K span.

        The label says the SPRITE's size and the price uses the DIFFUSION
        canvas, and the two are different numbers on purpose: the phase asks a
        general model for the big canvas and conforms it down."""
        from canon import pricing
        from canon.packs.platformer.estimate import estimate_cradle
        from canon.packs.platformer.ops import load_pack

        out = tmp_path / "game"
        _build_tree(out)
        eid, _sid = self._ids(out)
        graphics = load_pack(out).graphics
        gen_px = graphics.gen_px
        sku = "fal-ai/nano-banana-2"
        tier = pricing.IMAGE[sku]["by_resolution"]["0.5K" if gen_px <= 512 else "1K"]
        monkeypatch.setitem(pricing.BACKEND_DEFAULT_MODEL["image"], "fal", sku)

        est = estimate_cradle("asset", pack_dir=out, target=f"enemy:{eid}",
                              backends={"image": "fal"})
        assert est["assets"]["images"]["count"] == 1
        assert est["assets"]["images"]["usd"] == tier
        assert est["low"] == est["high"] == tier  # a known size is a POINT
        assert tier < pricing.IMAGE[sku]["usd_high"]
        assert f"at {graphics.sprite_size()}px" in est["unitLabel"]
        assert graphics.sprite_size() != gen_px, "the two sizes are not the same number"


class TestRowScope:
    """`complete_row`'s scope: ONE call at the kind's own task label."""

    def test_the_task_is_the_kinds_own_phase_label(self, tmp_path: Path) -> None:
        from canon.estimator import db_row_task
        from canon.packs.platformer.estimate import estimate_cradle

        out = tmp_path / "game"
        _build_tree(out)
        assert db_row_task(out, "enemy") == ("plat:enemies", "enemy")
        assert db_row_task(out, "item") == ("plat:items", "item")

        est = estimate_cradle("row", pack_dir=out, entity_type="enemy",
                              entity_id="ghoul", backends={"llm": "anthropic"})
        assert list(est["llm"]["by_task"]) == ["plat:enemies"]
        assert est["llm"]["calls"] == 1 and est["unitCount"] == 1
        assert est["unitLabel"].startswith("1 enemy row · ghoul")

    def test_an_unknown_db_type_raises_rather_than_pricing_zero(
        self, tmp_path: Path
    ) -> None:
        import pytest

        from canon.packs.platformer.estimate import estimate_cradle

        out = tmp_path / "game"
        _build_tree(out)
        with pytest.raises(ValueError):
            estimate_cradle("row", pack_dir=out, entity_type="spaceship",
                            backends={"llm": "anthropic"})

    def test_recorded_tokens_beat_the_cost_models_guess(self, tmp_path: Path) -> None:
        """The row scope rides the SAME per-task calibration the run hook uses,
        because the builder stamps the very label generation_stats.json keys."""
        from canon.packs.platformer.estimate import estimate_cradle

        out = tmp_path / "game"
        _build_tree(out)
        before = estimate_cradle("row", pack_dir=out, entity_type="enemy",
                                 backends={"llm": "anthropic"})
        assert before["calibration"] == "defaults"

        _stats(out, by_phase={"plat:enemies:0": {
            "calls": 2, "input_tokens": 20000, "output_tokens": 6000}})
        after = estimate_cradle("row", pack_dir=out, entity_type="enemy",
                                backends={"llm": "anthropic"})
        assert after["calibration"] == "actuals"
        assert after["llm"]["by_task"]["plat:enemies"]["input_tokens_per_call"] == 10000
        assert after["low"] > before["low"]
        assert "measured from this pack" in after["unitLabel"]


class TestCalibrationIsNeverSilent:
    def test_every_estimate_says_where_its_numbers_came_from(self, tmp_path: Path) -> None:
        from canon.packs.platformer.estimate import estimate_cradle

        out = tmp_path / "game"
        _build_tree(out)
        for est in (
            estimate_cradle("world", counts={"num_stages": 1}, backends={"llm": "anthropic"}),
            estimate_cradle("music", backends={"music": "lyria"}),
            estimate_cradle("layout", pack_dir=out, level_id="__preview__", width=48,
                            backends={"llm": "anthropic"}),
        ):
            assert est["calibration"] in ("actuals", "defaults")

    def test_a_stats_file_that_never_bites_still_reads_defaults(
        self, tmp_path: Path
    ) -> None:
        """"Did calibration actually bite", not "does a file exist": a tree
        whose recorded tasks are none of the ones this scope prices is priced
        from the shipped tables and must say so."""
        from canon.packs.platformer.estimate import estimate_cradle

        out = tmp_path / "game"
        _build_tree(out)
        _stats(out, by_phase={"plat:layout:l1": {
            "calls": 1, "input_tokens": 9000, "output_tokens": 900}})
        est = estimate_cradle("row", pack_dir=out, entity_type="enemy",
                              backends={"llm": "anthropic"})
        assert est["calibration"] == "defaults"

    def test_the_run_hooks_pre_split_calibration_key_survives_stripping(
        self, tmp_path: Path
    ) -> None:
        """`calibration` is additive on the cradle shapes but part of the run
        hook's own `{mode, calibration, …}` — strip_additive must not eat it."""
        from canon.estimator import strip_additive
        from canon.packs.platformer.estimate import estimate_cradle

        ctx = PipelineContext(
            bible=Bible.empty(seed="est"),
            config=CanonConfig(seed="est", output_dir=tmp_path),
            rng=random.Random(0),
        )
        run = estimate_run(ctx, [], Bible.empty(seed="est"))
        assert strip_additive(run)["calibration"] == "defaults"
        cradle = estimate_cradle("music", backends={"music": "lyria"})
        assert "calibration" in cradle and "calibration" not in strip_additive(cradle)
        assert "unitLabel" not in strip_additive(cradle)


class TestVlmCalibrationRow:
    """Each VLM family names the ``task`` label the run's metered judge
    records that call under — the same key ``actuals_by_task`` derives from
    ``generation_stats.json`` — so the pack's own recorded runs are the
    calibration row for the vision lane, exactly as they are for the LLM
    tasks. The token rows (``vlm_per_level`` / ``vlm_per_actor``) are
    untouched data beside it."""

    def test_families_name_the_labels_the_pipeline_records(self) -> None:
        from canon.packs.platformer.estimate import (
            _vlm_tasks,
            count_platformer,
            load_cost_model,
        )
        from canon.packs.platformer.vlm_qa import (
            ANIMATE_LABEL,
            ANIMATE_QA_LABEL,
            VlmQaPhase,
        )

        vlm = count_platformer({"scope": "world", "cost_model": load_cost_model()}, None)["vlm"]
        assert vlm["level_judgments"]["task"] == VlmQaPhase.name == "plat:vlm_qa"
        assert vlm["animation_qa"]["task"] == ANIMATE_QA_LABEL
        assert vlm["animation_authoring"]["task"] == ANIMATE_LABEL
        assert vlm["level_judgments"]["tokens"] == "vlm_per_level"
        assert vlm["animation_qa"]["tokens"] == vlm["animation_authoring"]["tokens"] == "vlm_per_actor"
        assert _vlm_tasks() == {
            "level_judgments": "plat:vlm_qa",
            "animation_qa": ANIMATE_QA_LABEL,
            "animation_authoring": ANIMATE_LABEL,
        }

    def test_a_metered_run_records_the_row_the_family_names(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Through the production chain: a fake judge reporting usage → the
        metered judge → ``ctx.stats`` → ``generation_stats.json`` →
        ``actuals_by_task`` — the task the level-judgment family names is a
        measured row afterwards (a $0 fake with no usage never calibrates)."""
        from canon.backends.testing import FakeVLMBackend
        from canon.estimator import actuals_by_task
        from canon.packs.platformer.compose import compose_pipeline
        from canon.packs.platformer.estimate import count_platformer, load_cost_model
        from canon.packs.platformer.vlm_qa import build_vlm_judge
        from canon.pipeline.runner import run_pipeline
        from canon.pipeline.stats import GenerationStats

        monkeypatch.setattr(FakeVLMBackend, "last_input_tokens", 2500, raising=False)
        monkeypatch.setattr(FakeVLMBackend, "last_output_tokens", 400, raising=False)
        stats = GenerationStats(llm_backend="fake", vlm_backend="fake")
        seed = "emberfall_001"
        ctx = PipelineContext(
            bible=Bible.empty(seed=seed),
            config=CanonConfig(seed=seed, output_dir=tmp_path),
            rng=random.Random(seed),
            stats=stats,
            llm=LLMClient(FakeLLMBackend(make_fake_responder()), stats=stats),
            prompts=PlatformerPrompts(),
        )
        run_pipeline(compose_pipeline(vlm_judge=build_vlm_judge("fake", stats=stats)), ctx)

        task = count_platformer(
            {"scope": "world", "cost_model": load_cost_model()}, None
        )["vlm"]["level_judgments"]["task"]
        measured = actuals_by_task(tmp_path)
        assert measured[task] == {"input_tokens": 2500.0, "output_tokens": 400.0}
        assert stats.vlm_calls == len(ctx.bible.levels)
