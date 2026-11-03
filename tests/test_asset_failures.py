"""Failures are acceptable; silence is not.

The paid dungeon run that motivated this lost 26 of 73 assets and reported
``ok: true`` — every paid asset backend's ``generate_and_save_async`` was
``except Exception: return False``. These tests pin the four halves of the
fix: the REASON is recorded (classified with the chat backends' retryable
taxonomy), retries are CLASSIFIED (a 429 is retried with backoff, a 403 is
called once), an impossible SFX catalog entry is refused before anything is
sent, and a pack that lost assets is finished for the price of what is
missing (``asset generate --target missing``) through the ordinary write
pipeline under ``--actor``.

Hermetic and $0: fake backends and mocked SDK clients only.
"""

from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path
from typing import Any

import pytest

from canon import Bible, BibleMetadata, CanonConfig, Character, PipelineContext
from canon.backends.failures import (
    ASSET_BACKOFF_SECONDS,
    ASSET_RETRIES,
    DEFAULT_REPAIR,
    AssetError,
    cancel_hook,
    classify_exception,
    retry_call,
)
from canon.backends.testing import (
    FakeImageBackend,
    FakeLLMBackend,
    FakeMusicBackend,
    FakeSFXBackend,
)
from canon.llm.chat import ChatError
from canon.pipeline.phases.asset import (
    ASSET_CONCURRENCY,
    SFX_CATALOG,
    AssetPhase,
    check_sfx_catalog,
    record_asset_failure,
    validate_sfx_catalog,
)
from canon.pipeline.stats import GenerationStats
from canon.pipeline.steplog import RunCancelled, StepLog

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Status(Exception):
    """A provider-ish exception carrying an HTTP status (fal / ElevenLabs shape)."""

    def __init__(self, status_code: int, message: str = "provider said no") -> None:
        super().__init__(message)
        self.status_code = status_code


class _Google(Exception):
    """google-genai's ``APIError`` shape: ``code`` + a gRPC ``status`` string."""

    def __init__(self, code: int, status: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.message = message


class FlakyImageBackend(FakeImageBackend):
    """A fake that fails the first ``fail_times`` calls for files matching
    ``only`` (default: every file) with ``exc`` — recording the reason the
    way the paid backends do — then succeeds. ``calls`` counts every call."""

    def __init__(self, fail_times: int, exc: Exception, only: str = "") -> None:
        super().__init__()
        self.fail_times = fail_times
        self.exc = exc
        self.only = only
        self.failed = 0
        self.last_error: AssetError | None = None

    def calls_for(self, fragment: str) -> int:
        return sum(1 for c in self.calls if fragment in str(c.get("filepath", "")))

    async def generate_and_save_async(self, prompt, filepath, width, height) -> bool:
        self.calls.append({"prompt": prompt, "filepath": filepath})
        self.last_error = None
        if self.only in str(filepath) and self.failed < self.fail_times:
            self.failed += 1
            self.last_error = classify_exception(self.exc, provider="flaky")
            return False
        dest = Path(filepath)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self._placeholder_bytes())
        return True


class RaisingImageBackend(FakeImageBackend):
    """A foreign backend that RAISES instead of returning False."""

    def __init__(self, exc: Exception) -> None:
        super().__init__()
        self.exc = exc

    async def generate_and_save_async(self, prompt, filepath, width, height) -> bool:
        self.calls.append({"prompt": prompt, "filepath": filepath})
        raise self.exc


class CountingSFXBackend(FakeSFXBackend):
    """Records the peak number of in-flight calls."""

    def __init__(self) -> None:
        super().__init__()
        self.in_flight = 0
        self.peak = 0

    async def generate_and_save_async(self, prompt, filepath, duration_seconds, loop) -> bool:
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        await asyncio.sleep(0.001)
        try:
            return super().generate_and_save(prompt, filepath, duration_seconds, loop)
        finally:
            self.in_flight -= 1


def _ctx(tmp_path: Path, *, characters: int = 1, steplog: bool = False, **backends: Any) -> PipelineContext:
    bible = Bible.empty(seed="failures")
    bible.metadata = BibleMetadata()
    for i in range(characters):
        bible.add_character(Character(
            character_id=f"npc_{i:04d}", name=f"NPC {i}", role="npc", portrait_prompt=f"npc {i}",
        ))
    ctx = PipelineContext(
        bible=bible,
        config=CanonConfig(seed="failures").model_copy(update={"output_dir": tmp_path}),
        rng=random.Random(0),
        steplog=StepLog(tmp_path) if steplog else None,
    )
    for name, backend in backends.items():
        setattr(ctx, f"{name}_backend", backend)
    return ctx


def _phase(**kw: Any) -> AssetPhase:
    kw.setdefault("skip_music", True)
    kw.setdefault("skip_sfx", True)
    kw.setdefault("retry_backoff", (0, 0, 0))
    return AssetPhase(**kw)


def _log_events(root: Path) -> list[dict]:
    path = root / ".canon" / "log.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


# ---------------------------------------------------------------------------
# 1. ONE taxonomy: AssetError is a ChatError, classified the chat way
# ---------------------------------------------------------------------------


class TestClassification:
    def test_an_asset_error_is_a_chat_error(self) -> None:
        err = classify_exception(_Status(429), provider="fal")
        assert isinstance(err, ChatError) and isinstance(err, AssetError)
        assert err.retryable is True and err.status == 429 and err.provider == "fal"
        assert err.kind == "_Status"

    @pytest.mark.parametrize("status", [401, 403, 400, 404, 422, 418])
    def test_4xx_is_not_retryable(self, status: int) -> None:
        assert classify_exception(_Status(status)).retryable is False

    @pytest.mark.parametrize("status", [429, 408, 500, 502, 503])
    def test_rate_limits_and_5xx_are_retryable(self, status: int) -> None:
        assert classify_exception(_Status(status)).retryable is True

    def test_google_permission_denied_is_permanent(self) -> None:
        """The paid run's music lane: 403 PERMISSION_DENIED at the billing
        gate — retrying wastes time, so it is called once and listed."""
        err = classify_exception(_Google(403, "PERMISSION_DENIED", "billing"), provider="lyria")
        assert err.retryable is False and err.status == 403
        assert "billing" in str(err)
        assert "lyria" in err.hint and "key" in err.hint

    def test_google_resource_exhausted_is_retryable(self) -> None:
        err = classify_exception(_Google(429, "RESOURCE_EXHAUSTED", "slow down"))
        assert err.retryable is True

    def test_timeouts_and_connection_drops_are_retryable(self) -> None:
        assert classify_exception(TimeoutError("t")).retryable is True
        assert classify_exception(ConnectionError("c")).retryable is True

    def test_a_malformed_request_is_permanent(self) -> None:
        assert classify_exception(ValueError("Unexpected fal response shape")).retryable is False

    def test_an_unknown_error_gets_the_retries(self) -> None:
        """The owner's rule is three retries; only a failure KNOWN to be
        permanent is spared them."""
        assert classify_exception(RuntimeError("API error")).retryable is True

    def test_an_existing_chat_error_keeps_its_flag(self) -> None:
        err = classify_exception(ChatError("boom", retryable=False, status=403))
        assert err.retryable is False and err.status == 403

    def test_the_hint_names_the_packs_own_repair(self) -> None:
        """The default hint names the pipeline's verb; a template whose verbs
        speak another grammar hands in its own clause and the hint never
        names a target that pack would refuse."""
        err = classify_exception(_Status(429), provider="fal")
        assert DEFAULT_REPAIR in err.hint and "--target missing" in err.hint
        err.repair = "regenerate it with `asset generate --target enemy:slime`"
        assert "enemy:slime" in err.hint and "missing" not in err.hint

    def test_the_record_names_family_and_error_separately(self) -> None:
        rec = classify_exception(_Status(500), provider="fal").to_record()
        assert rec["error"] == "_Status" and rec["provider"] == "fal"
        assert rec["retryable"] is True and rec["status"] == 500 and rec["hint"]
        assert "kind" not in rec, "the record's kind is the asset family, stamped by the recorder"


class TestRetryCall:
    def test_retries_a_retryable_failure_then_returns(self) -> None:
        calls: list[int] = []
        waits: list[float] = []

        def fn() -> str:
            calls.append(1)
            if len(calls) < 3:
                raise _Status(429)
            return "ok"

        # The suite's autouse fixture zeroes the live backoff (tests/conftest.py);
        # the SHIPPED schedule is the one imported above, passed explicitly.
        assert retry_call(fn, provider="p", sleep=waits.append, backoff=ASSET_BACKOFF_SECONDS) == "ok"
        assert len(calls) == 3 and waits == [1.0, 2.0]

    def test_the_shipped_backoff_is_exponential_seconds(self) -> None:
        assert ASSET_BACKOFF_SECONDS == (1.0, 2.0, 4.0)

    def test_a_stop_between_attempts_ends_the_retries(self) -> None:
        """⏹ Stop during a retryable failure: no further paid call is made,
        the failure is final (attempts honest), and it is still classified
        as the retryable error it was."""
        calls: list[int] = []
        stopped = False

        def fn() -> None:
            nonlocal stopped
            calls.append(1)
            stopped = True  # the Stop lands while this attempt is in flight
            raise _Status(429)

        with pytest.raises(AssetError) as raised:
            retry_call(fn, provider="p", sleep=lambda _s: None, should_stop=lambda: stopped)
        assert len(calls) == 1 and raised.value.attempts == 1 and raised.value.retryable is True

    def test_cancel_hook_reads_the_step_logs_cancel_file(self, tmp_path: Path) -> None:
        cancel = tmp_path / "cancel"
        ctx = _ctx(tmp_path)
        assert cancel_hook(ctx) is None, "no step log — never stopped"
        ctx.steplog = StepLog(tmp_path, cancel_file=cancel)
        probe = cancel_hook(ctx)
        assert probe is not None and probe() is False
        cancel.touch()
        assert probe() is True

    def test_a_permanent_failure_is_called_once(self) -> None:
        calls: list[int] = []

        def fn() -> None:
            calls.append(1)
            raise _Status(403)

        with pytest.raises(AssetError) as raised:
            retry_call(fn, provider="p", sleep=lambda _s: None)
        assert len(calls) == 1 and raised.value.attempts == 1 and raised.value.retryable is False

    def test_exhausts_after_the_configured_retries(self) -> None:
        calls: list[int] = []

        def fn() -> None:
            calls.append(1)
            raise _Status(503)

        with pytest.raises(AssetError) as raised:
            retry_call(fn, provider="p", sleep=lambda _s: None)
        assert len(calls) == ASSET_RETRIES + 1 and raised.value.attempts == ASSET_RETRIES + 1


# ---------------------------------------------------------------------------
# 2. Each paid backend records the reason instead of a bare False
# ---------------------------------------------------------------------------


class TestFalRecordsTheReason:
    def test_generate_and_save_keeps_the_classified_error(self, tmp_path: Path) -> None:
        pytest.importorskip("fal_client")
        from unittest.mock import patch

        from canon.backends.image_fal import FalImageBackend

        backend = FalImageBackend()
        with patch.object(backend._fal, "subscribe", side_effect=_Status(429, "rate limited")):
            ok = backend.generate_and_save("a", str(tmp_path / "a.png"), 64, 64)
        assert ok is False
        assert backend.last_error is not None
        assert backend.last_error.retryable is True and backend.last_error.status == 429
        assert backend.last_error.provider == "fal" and "rate limited" in str(backend.last_error)

    def test_a_success_clears_the_error(self, tmp_path: Path) -> None:
        pytest.importorskip("fal_client")
        from unittest.mock import MagicMock, patch

        from canon.backends.image_fal import FalImageBackend

        backend = FalImageBackend()
        backend.last_error = AssetError("stale")
        resp = MagicMock()
        resp.read.return_value = b"\x89PNG"
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda s, *a: None
        with (
            patch.object(backend._fal, "subscribe", return_value={"images": [{"url": "u"}]}),
            patch("urllib.request.urlopen", return_value=resp),
        ):
            assert backend.generate_and_save("a", str(tmp_path / "a.png"), 64, 64) is True
        assert backend.last_error is None


class TestElevenLabsRecordsTheReason:
    def _backend(self):
        pytest.importorskip("elevenlabs")
        from unittest.mock import patch

        from canon.backends.sfx_elevenlabs import ElevenLabsSFXBackend

        with patch("elevenlabs.ElevenLabs"):
            return ElevenLabsSFXBackend(api_key="k")

    def test_a_401_is_recorded_as_permanent(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock

        from elevenlabs.core.api_error import ApiError

        backend = self._backend()
        backend._client.text_to_sound_effects.convert = MagicMock(
            side_effect=ApiError(status_code=401, body="bad key")
        )
        ok = asyncio.run(backend.generate_and_save_async("boom", str(tmp_path / "b.mp3"), 1.0, False))
        assert ok is False
        assert backend.last_error is not None and backend.last_error.retryable is False
        assert backend.last_error.status == 401 and backend.last_error.provider == "elevenlabs"

    def test_a_sub_floor_duration_is_refused_before_the_call(self, tmp_path: Path) -> None:
        """The two 0.4 s catalog entries could never succeed: the backend
        refuses them as a permanent failure and never sends the request."""
        from unittest.mock import MagicMock

        backend = self._backend()
        backend._client.text_to_sound_effects.convert = MagicMock()
        ok = backend.generate_and_save("tick", str(tmp_path / "t.mp3"), 0.4, False)
        assert ok is False
        assert backend.last_error is not None and backend.last_error.retryable is False
        assert "0.4" in str(backend.last_error) and "0.5" in str(backend.last_error)
        backend._client.text_to_sound_effects.convert.assert_not_called()


class TestLyriaRecordsTheReason:
    def test_permission_denied_is_recorded_as_permanent(self, tmp_path: Path) -> None:
        pytest.importorskip("google.genai")
        from unittest.mock import patch

        from google.genai import errors

        from canon.backends.music_lyria import LyriaMusicBackend

        with patch("google.genai.Client"):
            backend = LyriaMusicBackend(api_key="k")
        exc = errors.APIError(403, {"error": {"status": "PERMISSION_DENIED", "message": "billing"}})
        backend._client.models.generate_content.side_effect = exc
        ok = backend.generate_and_save("calm", str(tmp_path / "c.mp3"), 30)
        assert ok is False
        assert backend.last_error is not None
        assert backend.last_error.retryable is False and backend.last_error.status == 403
        assert backend.last_error.provider == "lyria"


# ---------------------------------------------------------------------------
# 3. AssetPhase: classified retries, honest counts, a durable list
# ---------------------------------------------------------------------------


#: One character plus the four fixed portraits: what ``_ctx`` plans.
FIXED = 4


class TestAssetPhaseRetries:
    def test_a_429_is_retried_until_it_lands(self, tmp_path: Path) -> None:
        img = FlakyImageBackend(fail_times=2, exc=_Status(429), only="npcs/")
        ctx = _ctx(tmp_path, image=img)
        _phase().run(ctx)
        assert (tmp_path / "portraits" / "npcs" / "npc_npc_0000.png").is_file()
        assert img.calls_for("npcs/") == 3, "two failures, then the success"
        assert ctx.stats.image_attempts == FIXED + 3, "every call is an attempt"
        assert ctx.stats.image_successes == FIXED + 1
        assert getattr(ctx.stats, "failures", []) == []
        assert ctx.bible.characters[0].portrait_path == "portraits/npcs/npc_npc_0000.png"

    def test_a_403_is_called_once_and_listed(self, tmp_path: Path) -> None:
        img = FlakyImageBackend(fail_times=99, exc=_Status(403, "forbidden"), only="npcs/")
        ctx = _ctx(tmp_path, image=img, steplog=True)
        _phase().run(ctx)
        assert img.calls_for("npcs/") == 1, "a permanent failure is never retried"
        assert ctx.stats.image_attempts == FIXED + 1 and ctx.stats.image_successes == FIXED
        [rec] = ctx.stats.failures
        assert rec["kind"] == "image" and rec["target"] == "npc:npc_0000"
        assert rec["path"] == "portraits/npcs/npc_npc_0000.png"
        assert rec["attempts"] == 1 and rec["retryable"] is False and rec["status"] == 403
        assert "forbidden" in rec["message"] and rec["hint"]
        assert ctx.bible.characters[0].portrait_path is None or not ctx.bible.characters[0].portrait_path
        failed = [e for e in _log_events(tmp_path) if e["event"] == "asset_failed"]
        assert len(failed) == 1
        assert failed[0]["node"] == "phase:assets" and failed[0]["target"] == "npc:npc_0000"
        assert failed[0]["attempts"] == 1 and failed[0]["status"] == 403

    def test_a_persistent_5xx_exhausts_the_retries_then_lists(self, tmp_path: Path) -> None:
        img = FlakyImageBackend(fail_times=99, exc=_Status(503), only="npcs/")
        ctx = _ctx(tmp_path, image=img)
        _phase().run(ctx)
        assert img.calls_for("npcs/") == ASSET_RETRIES + 1
        [rec] = ctx.stats.failures
        assert rec["attempts"] == ASSET_RETRIES + 1 and rec["retryable"] is True

    def test_a_backend_that_raises_is_classified_too(self, tmp_path: Path) -> None:
        img = RaisingImageBackend(_Status(401, "no key"))
        ctx = _ctx(tmp_path, image=img)
        _phase().run(ctx)
        assert len(img.calls) == FIXED + 1, "every asset called once, none retried"
        assert len(ctx.stats.failures) == FIXED + 1
        rec = next(f for f in ctx.stats.failures if f["target"] == "npc:npc_0000")
        assert rec["status"] == 401 and rec["error"] == "_Status" and rec["retryable"] is False

    def test_one_failure_never_loses_the_others(self, tmp_path: Path) -> None:
        img = FlakyImageBackend(fail_times=1, exc=_Status(403), only="npc_npc_0001")
        ctx = _ctx(tmp_path, characters=3, image=img)
        _phase().run(ctx)
        assert [f["target"] for f in ctx.stats.failures] == ["npc:npc_0001"]
        assert ctx.stats.image_successes == FIXED + 2

    def test_the_stats_snapshot_carries_the_list(self, tmp_path: Path) -> None:
        """``generation_stats.json`` is ``GenerationStats.to_dict()``: the
        failure list must land there or a create with failures reads clean."""
        img = FlakyImageBackend(fail_times=99, exc=_Status(403), only="npcs/")
        ctx = _ctx(tmp_path, image=img)
        _phase().run(ctx)
        snapshot = ctx.stats.to_dict()
        assert snapshot["failures"][0]["target"] == "npc:npc_0000"
        assert snapshot["images_attempted"] == FIXED + 1 and snapshot["images_succeeded"] == FIXED

    def test_a_clean_snapshot_says_so_outright(self, tmp_path: Path) -> None:
        """No failures is a fact the file states, not a key it omits: a clean
        run emits ``"failures": []`` — and identically every time, so the
        dungeon manifest's embedded copy stays inside the determinism
        contract."""
        ctx = _ctx(tmp_path, image=FakeImageBackend())
        _phase().run(ctx)
        assert ctx.stats.to_dict()["failures"] == []
        assert json.dumps(ctx.stats.to_dict()["failures"]) == json.dumps(GenerationStats().to_dict()["failures"])
        assert GenerationStats().to_dict()["failures"] == []

    def test_a_stop_during_a_retry_makes_no_further_call(self, tmp_path: Path) -> None:
        """⏹ Stop while a job is being retried: that job makes no second
        call (a retry after Stop is spend the user refused), is recorded
        as failed, and the next job's boundary raises ``RunCancelled``."""
        cancel = tmp_path / "cancel"

        class StoppingBackend(FlakyImageBackend):
            async def generate_and_save_async(self, prompt, filepath, width, height) -> bool:
                cancel.touch()  # the Stop lands while the first call is in flight
                return await super().generate_and_save_async(prompt, filepath, width, height)

        img = StoppingBackend(fail_times=99, exc=_Status(429))
        ctx = _ctx(tmp_path, image=img)
        ctx.steplog = StepLog(tmp_path, cancel_file=cancel)
        with pytest.raises(RunCancelled):
            _phase(image_concurrency=1).run(ctx)
        assert len(img.calls) == 1, "one call, then Stop: no retry, no next job"
        [rec] = ctx.stats.failures
        assert rec["attempts"] == 1 and rec["retryable"] is True and rec["status"] == 429
        events = [e["event"] for e in _log_events(tmp_path)]
        assert "asset_failed" in events


class TestConcurrency:
    def test_sfx_concurrency_is_two_as_data(self) -> None:
        assert ASSET_CONCURRENCY["sfx"] == 2
        assert AssetPhase(skip_sfx=True).sfx_concurrency == 2

    def test_no_more_than_two_sfx_requests_in_flight(self, tmp_path: Path) -> None:
        sfx = CountingSFXBackend()
        ctx = _ctx(tmp_path, characters=0, sfx=sfx)
        AssetPhase(skip_image=True, skip_music=True).run(ctx)
        assert len(sfx.calls) == len(SFX_CATALOG)
        assert sfx.peak <= 2, f"{sfx.peak} sfx requests were in flight at once"

    def test_a_backend_may_declare_a_tighter_limit(self, tmp_path: Path) -> None:
        sfx = CountingSFXBackend()
        sfx.max_concurrency = 1
        ctx = _ctx(tmp_path, characters=0, sfx=sfx)
        AssetPhase(skip_image=True, skip_music=True).run(ctx)
        assert sfx.peak == 1


# ---------------------------------------------------------------------------
# 4. The SFX catalog is refused up front when an entry cannot be served
# ---------------------------------------------------------------------------


class TestSfxCatalogFloor:
    def test_the_shipped_catalog_is_within_the_provider_bounds(self) -> None:
        assert validate_sfx_catalog() == []
        stems = {stem: secs for stem, _p, secs, _l in SFX_CATALOG}
        # The two entries the paid run lost, now at the floor.
        assert stems["weapon_light_hit"] == 0.5 and stems["item_pickup"] == 0.5

    def test_a_0_4_entry_is_rejected_and_0_5_accepted(self) -> None:
        assert validate_sfx_catalog([("tick", "a tick", 0.4, False)]) == [
            "tick: 0.4s is below the 0.5–30.0s range"
        ]
        assert validate_sfx_catalog([("tick", "a tick", 0.5, False)]) == []
        assert validate_sfx_catalog([("long", "a drone", 31.0, True)]) == [
            "long: 31.0s is above the 0.5–30.0s range"
        ]

    def test_the_bounds_are_the_providers_own(self) -> None:
        from canon.backends.sfx_elevenlabs import SFX_DURATION_BOUNDS

        assert validate_sfx_catalog([("x", "p", SFX_DURATION_BOUNDS[0], False)]) == []

    def test_construction_refuses_an_impossible_catalog(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Skeleton time: the phase is built before any LLM money is spent,
        and that is where an impossible entry stops the run."""
        import canon.pipeline.phases.asset as asset_mod

        monkeypatch.setattr(asset_mod, "SFX_CATALOG", (("tick", "a tick", 0.4, False),))
        with pytest.raises(ValueError, match="tick: 0.4s is below"):
            check_sfx_catalog()
        with pytest.raises(ValueError, match="cannot serve"):
            AssetPhase()
        AssetPhase(skip_sfx=True)  # the lane is off: nothing to refuse


# ---------------------------------------------------------------------------
# 5. The platformer's phases retry and list the same way
# ---------------------------------------------------------------------------


class TestPlatformerAudioPhase:
    def _ctx(self, tmp_path: Path) -> PipelineContext:
        from canon.adapters import JsonOutputAdapter
        from canon.bible.platformer import Stage

        bible = Bible.empty(seed="plat")
        bible.metadata = BibleMetadata()
        bible.stages["s1"] = Stage(artifact_id="stage:s1", stage_id="s1", theme="lava")
        return PipelineContext(
            bible=bible,
            config=CanonConfig(seed="plat", output_dir=tmp_path),
            rng=random.Random(0),
            adapter=JsonOutputAdapter(tmp_path),
            steplog=StepLog(tmp_path),
        )

    def test_a_flaky_sfx_producer_is_retried_and_a_dead_one_listed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import canon.backends.failures as failures_mod
        from canon.packs.platformer.audio_phases import SFX_EVENTS, AudioPhase

        monkeypatch.setattr(failures_mod, "ASSET_BACKOFF_SECONDS", (0.0,))

        class FlakySFX(FakeSFXBackend):
            def __init__(self) -> None:
                super().__init__()
                self.n = 0

            def generate(self, prompt, duration_seconds, loop):
                self.n += 1
                if self.n == 1:
                    raise _Status(429)  # first event: transient, retried
                if self.n == 3:
                    raise _Status(403)  # second event: permanent, listed
                return super().generate(prompt, duration_seconds, loop)

        sfx = FlakySFX()
        ctx = self._ctx(tmp_path)
        AudioPhase(music_producer=None, sfx_producer=sfx).run(ctx)
        audio = ctx.bible.audio["s1"]
        assert len(audio.sfx_paths) == len(SFX_EVENTS) - 1
        [rec] = ctx.stats.failures
        assert rec["kind"] == "sfx" and rec["target"] == "audio:s1" and rec["status"] == 403
        assert rec["attempts"] == 1
        # The hint names a target the platformer's own verb accepts — never
        # the pipeline's `--target missing`, which that pack refuses.
        assert "asset generate --target audio:s1" in rec["hint"] and "missing" not in rec["hint"]
        assert any("generation failed" in w for w in ctx.artifacts["slice_warnings"])
        failed = [e for e in _log_events(tmp_path) if e["event"] == "asset_failed"]
        assert failed and failed[0]["node"] == "phase:plat:audio"


class TestRecordAssetFailure:
    def test_records_on_a_stats_object_without_the_field(self) -> None:
        stats = GenerationStats()
        ctx = PipelineContext(
            bible=Bible.empty(seed="r"), config=CanonConfig(seed="r"), rng=random.Random(0), stats=stats,
        )
        err = classify_exception(_Status(500), provider="fal")
        rec = record_asset_failure(ctx, family="image", target="npc:1", rel="p.png", error=err)
        assert rec["kind"] == "image" and rec["error"] == "_Status"
        assert ctx.stats.failures == [rec]


# ---------------------------------------------------------------------------
# 6. Repair without re-spending: `asset generate --target missing`
# ---------------------------------------------------------------------------


NUM_MAPS = 2


def _generate_dungeon(out: Path, *, seed: str = "repair-seed") -> Path:
    from canon import run_pipeline
    from canon.llm.client import LLMClient
    from canon.packs.dungeon.compose import compose_pipeline
    from canon.packs.dungeon.fakes import make_fake_responder

    phases, ctx = compose_pipeline(seed=seed, num_maps=NUM_MAPS, output_dir=out)
    ctx.llm = LLMClient(FakeLLMBackend(make_fake_responder(NUM_MAPS)))
    ctx.image_backend = FakeImageBackend()
    ctx.music_backend, ctx.sfx_backend = FakeMusicBackend(), FakeSFXBackend()
    for phase in phases:
        if phase.name == "assets":
            phase.skip_image = phase.skip_music = phase.skip_sfx = False
    run_pipeline(phases, ctx)
    return out


def _journal(pack: Path) -> list[dict]:
    path = pack / ".canon" / "journal.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@pytest.fixture
def wounded(tmp_path: Path) -> dict[str, Any]:
    """A generated dungeon pack that LOST two portraits and one sound effect
    — the files gone, the rows' paths cleared, and the stats listing one of
    them as a failure (the shape a paid run with failures leaves behind)."""
    pack = _generate_dungeon(tmp_path / "pack")
    monsters = json.loads((pack / "monsters" / "monsters.json").read_text(encoding="utf-8"))
    lost_rows = list(monsters.items())[:2]
    lost_files = []
    for _key, row in lost_rows:
        rel = row["profile_image"]
        (pack / rel).unlink()
        row["profile_image"] = None
        lost_files.append(rel)
    (pack / "monsters" / "monsters.json").write_text(json.dumps(monsters, indent=2), encoding="utf-8")
    (pack / "sfx" / "door_open.mp3").unlink()
    lost_files.append("sfx/door_open.mp3")
    stats_path = pack / "generation_stats.json"
    stats = json.loads(stats_path.read_text(encoding="utf-8"))
    stats["failures"] = [{
        "kind": "sfx", "target": "sfx:door_open", "path": "sfx/door_open.mp3",
        "error": "_Status", "provider": "elevenlabs", "message": "429", "status": 429,
        "retryable": True, "attempts": 4, "hint": "repair",
    }]
    stats_path.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    return {
        "pack": pack, "lost_files": lost_files,
        "lost_targets": [f"monster:{key}" for key, _row in lost_rows] + ["sfx:door_open"],
        "journal_before": len(_journal(pack)),
    }


class TestRepairMissing:
    def test_regenerates_only_what_is_missing(self, wounded: dict[str, Any]) -> None:
        from canon.db_ops import generate_asset

        pack = wounded["pack"]
        before = {p.relative_to(pack).as_posix(): p.stat().st_mtime_ns for p in pack.rglob("*.png")}
        result = generate_asset(
            pack, "missing", image_backend="fake", sfx_backend="fake",
            actor="agent:test/repair", session="test",
        )
        assert sorted(result["planned"]) == sorted(wounded["lost_targets"])
        assert sorted(result["landed"]) == sorted(wounded["lost_targets"])
        assert result["failures"] == [] and result["generated"] is True
        for rel in wounded["lost_files"]:
            assert (pack / rel).is_file(), rel
        after = {p.relative_to(pack).as_posix(): p.stat().st_mtime_ns for p in pack.rglob("*.png")}
        untouched = {k: v for k, v in before.items() if k in after}
        assert all(after[k] == v for k, v in untouched.items()), "an existing portrait was regenerated"
        # music was not wired: reported, not guessed — and nothing of it was missing anyway
        assert result["skipped"] == {}

    def test_the_rows_and_the_stats_are_repaired_through_the_write_core(
        self, wounded: dict[str, Any]
    ) -> None:
        from canon.db_ops import generate_asset

        pack = wounded["pack"]
        generate_asset(pack, "missing", image_backend="fake", sfx_backend="fake", actor="agent:test/repair")
        monsters = json.loads((pack / "monsters" / "monsters.json").read_text(encoding="utf-8"))
        for row in monsters.values():
            assert row["profile_image"] and (pack / row["profile_image"]).is_file()
        stats = json.loads((pack / "generation_stats.json").read_text(encoding="utf-8"))
        assert stats["failures"] == [], "the repaired sfx dropped out of the list"
        assert stats["image_successes"] >= 2 and stats["sfx_succeeded"] >= 1
        manifest = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["sfx"]["door_open"] == "sfx/door_open.mp3"
        events = _journal(pack)[wounded["journal_before"]:]
        assert events, "nothing was journaled"
        assert all(e["actor"] == "agent:test/repair" for e in events)
        assert {e["artifact_id"] for e in events} >= {"monsters/monsters.json", "generation_stats", "missing"}
        op = next(e for e in events if e["artifact_id"] == "missing")
        assert op["detail"]["kind"] == "asset_generate" and op["detail"]["landed"] == 3
        assert op["costCents"] == 0 and op["accuracy"] == "measured", "fakes are an honest $0"

    def test_a_family_without_a_backend_is_reported_not_guessed(self, wounded: dict[str, Any]) -> None:
        from canon.db_ops import generate_asset

        pack = wounded["pack"]
        result = generate_asset(pack, "missing", image_backend="fake", actor="user")
        assert "sfx" in result["skipped"] and "--sfx-backend" in result["skipped"]["sfx"]
        assert not (pack / "sfx" / "door_open.mp3").exists()
        assert sorted(result["landed"]) == sorted(wounded["lost_targets"][:2])
        stats = json.loads((pack / "generation_stats.json").read_text(encoding="utf-8"))
        assert [f["target"] for f in stats["failures"]] == ["sfx:door_open"], "still missing, still listed"

    def test_nothing_missing_means_nothing_spent(self, wounded: dict[str, Any]) -> None:
        from canon.db_ops import generate_asset

        pack = wounded["pack"]
        generate_asset(pack, "missing", image_backend="fake", sfx_backend="fake", actor="user")
        result = generate_asset(pack, "missing", image_backend="fake", sfx_backend="fake", actor="user")
        assert result["planned"] == [] and result["generated"] is False and result["landed"] == []

    def test_a_single_target_rerolls_exactly_one(self, wounded: dict[str, Any]) -> None:
        from canon.db_ops import generate_asset

        pack = wounded["pack"]
        result = generate_asset(pack, "portrait:player", image_backend="fake", actor="user")
        assert result["planned"] == ["portrait:player"] and result["landed"] == ["portrait:player"]
        assert result["changed_artifacts"] == ["portrait:player"]
        with pytest.raises(FileNotFoundError, match="not found"):
            generate_asset(pack, "monster:999999", image_backend="fake")
        with pytest.raises(ValueError, match="--image-backend"):
            generate_asset(pack, "portrait:player")

    def test_a_reroll_whose_backend_fails_is_not_a_success(
        self, wounded: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The old file staying on disk is not a landing: a dead backend on
        an existing target reports nothing generated, nothing changed, one
        failure — and the journal agrees with itself."""
        import canon.packs.dungeon.run_world as runner
        from canon.db_ops import generate_asset

        dead = FlakyImageBackend(fail_times=99, exc=_Status(403, "no billing"))
        monkeypatch.setattr(
            runner, "build_asset_backends", lambda image, music, sfx: (dead, None, None),
        )
        pack = wounded["pack"]
        rel = "portraits/player.png"
        before = (pack / rel).read_bytes()
        result = generate_asset(pack, "portrait:player", image_backend="fake", actor="user")
        assert len(dead.calls) == 1
        assert result["generated"] is False and result["changed"] is False
        assert result["landed"] == [] and result["changed_artifacts"] == []
        assert [f["target"] for f in result["failures"]] == ["portrait:player"]
        assert (pack / rel).read_bytes() == before, "the old file is untouched"
        op = next(e for e in _journal(pack) if e["artifact_id"] == "portrait:player")
        assert op["detail"]["landed"] == 0 and op["detail"]["failed"] == 1
        stats = json.loads((pack / "generation_stats.json").read_text(encoding="utf-8"))
        assert "portrait:player" in {f["target"] for f in stats["failures"]}

    def test_a_reroll_versions_the_bytes_it_overwrites(self, wounded: dict[str, Any]) -> None:
        """Every write is a version: the prior bytes are in the object store
        under the event's ``before_hash``, the new ones under ``after_hash``."""
        from canon import provenance
        from canon.db_ops import generate_asset

        pack = wounded["pack"]
        rel = "portraits/player.png"
        old = (pack / rel).read_bytes()
        generate_asset(pack, "portrait:player", image_backend="fake", actor="user")
        op = next(e for e in _journal(pack) if e["artifact_id"] == "portrait:player")
        assert op["op"] == "regenerate"
        assert op["before_hash"] and op["after_hash"]
        assert provenance.read_object(pack, op["before_hash"]) == old
        assert provenance.read_object(pack, op["after_hash"]) == (pack / rel).read_bytes()

    def test_missing_never_overwrites_a_file_that_exists(self, wounded: dict[str, Any]) -> None:
        """A listed target whose file has since appeared is a stale record,
        not a job: `missing` leaves the bytes alone and drops the record."""
        from canon.db_ops import generate_asset

        pack = wounded["pack"]
        (pack / "sfx" / "door_open.mp3").write_bytes(b"user-placed")
        result = generate_asset(pack, "missing", image_backend="fake", sfx_backend="fake", actor="user")
        assert "sfx:door_open" not in result["planned"]
        assert (pack / "sfx" / "door_open.mp3").read_bytes() == b"user-placed"
        stats = json.loads((pack / "generation_stats.json").read_text(encoding="utf-8"))
        assert stats["failures"] == [], "the stale record dropped out"
        op = next(e for e in _journal(pack) if e["artifact_id"] == "missing")
        assert op["op"] == "generate" and "before_hash" not in op

    def test_a_failed_repair_stays_on_the_list(self, wounded: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
        import canon.packs.dungeon.run_world as runner
        from canon.db_ops import generate_asset

        dead = FlakyImageBackend(fail_times=99, exc=_Status(403, "no billing"))
        monkeypatch.setattr(
            runner, "build_asset_backends", lambda image, music, sfx: (dead, None, None),
        )
        pack = wounded["pack"]
        result = generate_asset(pack, "missing", image_backend="fake", actor="user")
        assert result["generated"] is False and len(result["failures"]) == 2
        assert all("no billing" in w for w in result["warnings"][:2])
        stats = json.loads((pack / "generation_stats.json").read_text(encoding="utf-8"))
        assert {f["target"] for f in stats["failures"]} == set(wounded["lost_targets"])
        assert len(dead.calls) == 2, "a 403 is called once per asset"


class TestThePlanner:
    def test_the_plan_names_every_asset_the_pipeline_made(self, tmp_path: Path) -> None:
        from canon.adapters import ASSET_PLANNERS, grid_verb
        from canon.packs import resolve_pack

        pack = _generate_dungeon(tmp_path / "pack")
        resolved = resolve_pack(pack)
        plan = grid_verb(ASSET_PLANNERS, resolved.pack_type)
        assert plan is not None
        jobs = plan(pack, resolved.spec)
        on_disk = {
            p.relative_to(pack).as_posix()
            for p in pack.rglob("*") if p.suffix in (".png", ".mp3")
        }
        planned = {j.rel for j in jobs}
        assert planned == on_disk, (planned ^ on_disk)
        assert len({j.target for j in jobs}) == len(jobs), "targets are unique"

    def test_no_planner_for_the_platformer(self) -> None:
        from canon.adapters import ASSET_PLANNERS, grid_verb

        assert grid_verb(ASSET_PLANNERS, "platformer") is None


class TestTheCliRoute:
    def test_asset_generate_reaches_the_dungeon_verb(self, wounded: dict[str, Any]) -> None:
        """The hint in every failure record says `asset generate --target
        missing`; the CLI must route a dungeon pack to the verb that has it."""
        import subprocess
        import sys

        pack = wounded["pack"]
        result = subprocess.run(
            [sys.executable, "-m", "canon.cli.main", "asset", "generate", str(pack),
             "--target", "missing", "--image-backend", "fake", "--sfx-backend", "fake",
             "--actor", "agent:test/repair"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert sorted(payload["planned"]) == sorted(wounded["lost_targets"])
        assert payload["failures"] == []

    def test_one_blank_row_regenerates_that_row_alone_and_is_journaled(self, tmp_path: Path) -> None:
        """End to end through the CLI: a dungeon with ONE monster's portrait
        gone and its row's path blanked — `asset generate --target missing
        --actor test` regenerates that row and nothing else (every other
        portrait keeps its bytes), fills the path back in, and journals the
        write under the actor."""
        import subprocess
        import sys

        pack = _generate_dungeon(tmp_path / "pack", seed="one-row")
        monsters_path = pack / "monsters" / "monsters.json"
        monsters = json.loads(monsters_path.read_text(encoding="utf-8"))
        key, row = next(iter(monsters.items()))
        rel = row["profile_image"]
        (pack / rel).unlink()
        row["profile_image"] = None
        monsters_path.write_text(json.dumps(monsters, indent=2), encoding="utf-8")
        before = {p.relative_to(pack).as_posix(): p.read_bytes() for p in pack.rglob("*.png")}
        journal_before = len(_journal(pack))

        result = subprocess.run(
            [sys.executable, "-m", "canon.cli.main", "asset", "generate", str(pack),
             "--target", "missing", "--image-backend", "fake", "--actor", "test"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        target = f"monster:{key}"
        assert payload["planned"] == [target] and payload["landed"] == [target]
        assert payload["failures"] == [] and payload["generated"] is True
        # That row alone: its file is back and its path filled; every other
        # portrait is byte-for-byte what it was.
        assert (pack / rel).is_file()
        monsters = json.loads(monsters_path.read_text(encoding="utf-8"))
        assert monsters[key]["profile_image"] == rel
        after = {p.relative_to(pack).as_posix(): p.read_bytes() for p in pack.rglob("*.png")}
        assert set(after) == set(before) | {rel}
        assert all(after[k] == v for k, v in before.items()), "an existing portrait was rewritten"
        # …and the write is journaled under the actor: the row file, the
        # stats, and the op itself.
        events = _journal(pack)[journal_before:]
        assert events and all(e["actor"] == "test" for e in events)
        assert {e["artifact_id"] for e in events} >= {"monsters/monsters.json", "generation_stats", "missing"}
        op = next(e for e in events if e["artifact_id"] == "missing")
        assert op["op"] == "generate" and op["detail"]["kind"] == "asset_generate"
        assert op["detail"]["planned"] == 1 and op["detail"]["landed"] == 1
        stats = json.loads((pack / "generation_stats.json").read_text(encoding="utf-8"))
        assert stats["failures"] == []
