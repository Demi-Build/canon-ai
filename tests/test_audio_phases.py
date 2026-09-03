"""Audio-track phase tests — the late audio node (music theme + SFX set).

Everything runs on FakeMusicBackend/FakeSFXBackend per the canned-fake
rule: deterministic bytes, zero spend, and the exact asset-artifact
contract the art track proved — content hashes on the owning StageAudio,
detect_edits adoption, pinnability, regen isolation, audio at the END.
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("PIL")

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from canon.backends.testing import (  # noqa: E402
    FakeLLMBackend,
    FakeMusicBackend,
    FakeSFXBackend,
)
from canon.bible.models import Bible  # noqa: E402
from canon.config import CanonConfig  # noqa: E402
from canon.llm.client import LLMClient  # noqa: E402
from canon.packs.platformer import PlatformerPrompts, compose_pipeline  # noqa: E402
from canon.packs.platformer.audio_phases import (  # noqa: E402
    SFX_EVENTS,
    build_music_producer,
    build_sfx_producer,
)
from canon.packs.platformer.dag import run_orchestrated  # noqa: E402
from canon.packs.platformer.run_slice import make_fake_responder  # noqa: E402
from canon.pipeline.orchestrator import detect_edits, mark_stale  # noqa: E402
from canon.pipeline.runner import PipelineContext, run_pipeline  # noqa: E402

SEED = "emberfall_001"
STAGE = "ashen_depths"


def _run(output_dir: Path, with_audio: bool = True) -> PipelineContext:
    ctx = PipelineContext(
        bible=Bible.empty(seed=SEED),
        config=CanonConfig(seed=SEED, output_dir=output_dir),
        rng=random.Random(SEED),
        llm=LLMClient(FakeLLMBackend(make_fake_responder())),
        prompts=PlatformerPrompts(),
    )
    run_pipeline(
        compose_pipeline(
            music_producer=FakeMusicBackend() if with_audio else None,
            sfx_producer=FakeSFXBackend() if with_audio else None,
        ),
        ctx,
    )
    return ctx


class TestAudioPhase:
    def test_audio_written_hashed_and_in_manifest(self, tmp_path: Path) -> None:
        run = tmp_path / "run"
        ctx = _run(run)
        audio = ctx.bible.audio[STAGE]
        assert audio.artifact_id == f"audio:{STAGE}"
        assert audio.music_path.startswith(f"music/{STAGE}/theme")
        assert len(audio.sfx_paths) == len(SFX_EVENTS)

        # §6.3 hash contract: stored hashes match a recompute from disk.
        actual = "sha256:" + hashlib.sha256(
            (run / audio.music_path).read_bytes()
        ).hexdigest()
        assert audio.music_hash == actual
        for rel, stored in audio.sfx_hashes.items():
            actual = "sha256:" + hashlib.sha256(
                (run / rel).read_bytes()
            ).hexdigest()
            assert stored == actual, rel

        manifest = json.loads((run / "manifest.json").read_text())
        assert manifest["audio"][STAGE]["music"] == audio.music_path
        assert set(manifest["audio"][STAGE]["sfx"]) == {e[0] for e in SFX_EVENTS}
        # The StageAudio manifest is on disk for reviewers.
        doc = json.loads((run / "audio" / STAGE / "manifest.json").read_text())
        assert doc["stage_id"] == STAGE

    def test_no_producers_is_silent_noop(self, tmp_path: Path) -> None:
        run = tmp_path / "run"
        ctx = _run(run, with_audio=False)
        assert ctx.bible.audio == {}
        assert not (run / "music").exists()
        assert not (run / "sfx").exists()
        manifest = json.loads((run / "manifest.json").read_text())
        assert manifest["audio"] == {STAGE: {"music": None, "sfx": {}}}

    def test_fake_audio_deterministic(self, tmp_path: Path) -> None:
        a, b = tmp_path / "a", tmp_path / "b"
        _run(a)
        _run(b)
        files = sorted(p.relative_to(a) for p in a.rglob("*") if p.is_file())
        for rel in files:
            assert (a / rel).read_bytes() == (b / rel).read_bytes(), rel

    def test_sfx_durations_clamped_into_backend_range(
        self, tmp_path: Path
    ) -> None:
        # ElevenLabs rejects duration_seconds < 0.5 (the first paid run lost
        # every 'jump' SFX at 0.4s). The vocabulary keeps a below-floor
        # value, so the clamp is what must keep the request valid.
        from canon.packs.platformer.audio_phases import (
            SFX_MAX_SECONDS,
            SFX_MIN_SECONDS,
        )

        assert any(e[0] == "jump" and e[2] < SFX_MIN_SECONDS for e in SFX_EVENTS)
        backend = FakeSFXBackend()
        ctx = PipelineContext(
            bible=Bible.empty(seed=SEED),
            config=CanonConfig(seed=SEED, output_dir=tmp_path / "run"),
            rng=random.Random(SEED),
            llm=LLMClient(FakeLLMBackend(make_fake_responder())),
            prompts=PlatformerPrompts(),
        )
        run_pipeline(compose_pipeline(sfx_producer=backend), ctx)
        assert backend.calls, "no sfx generated"
        for call in backend.calls:
            assert SFX_MIN_SECONDS <= call["duration_seconds"] <= SFX_MAX_SECONDS

    def test_hand_edited_theme_detected_and_adopted(
        self, tmp_path: Path
    ) -> None:
        """A hand-swapped theme marks only audio:<stage> user-edited —
        never the levels (own-artifact isolation, the Backdrop rule)."""
        run = tmp_path / "run"
        ctx = _run(run)
        theme = run / ctx.bible.audio[STAGE].music_path
        theme.write_bytes(b"my own track")

        report = detect_edits(ctx.bible, run)
        assert report.user_edited == [f"audio:{STAGE}"]
        assert not any(nid.startswith("level:") for nid in report.stale)
        # Adopted: next pass is clean.
        assert detect_edits(ctx.bible, run).user_edited == []

    def test_producer_builders_explicit_only(self) -> None:
        assert build_music_producer("") is None
        assert build_music_producer("none") is None
        assert build_sfx_producer(None) is None
        assert isinstance(build_music_producer("fake"), FakeMusicBackend)
        assert isinstance(build_sfx_producer("fake"), FakeSFXBackend)
        with pytest.raises(ValueError, match="unknown music backend"):
            build_music_producer("suno")

    def test_lyria_fails_fast_without_key(self, monkeypatch) -> None:
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        with pytest.raises(ValueError, match="GOOGLE_API_KEY"):
            build_music_producer("lyria")

    def test_elevenlabs_fails_fast_without_key(self, monkeypatch) -> None:
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
        with pytest.raises(ValueError, match="ELEVENLABS_API_KEY"):
            build_sfx_producer("elevenlabs")


class TestAudioOrchestrated:
    def _fresh(self, out: Path, with_audio: bool = True):
        ctx = PipelineContext(
            bible=Bible.empty(seed=SEED),
            config=CanonConfig(seed=SEED, output_dir=out),
            rng=random.Random(SEED),
            llm=LLMClient(FakeLLMBackend(make_fake_responder())),
            prompts=PlatformerPrompts(),
        )
        report = run_orchestrated(
            ctx, persist_path=out / "bible.json",
            music_producer=FakeMusicBackend() if with_audio else None,
            sfx_producer=FakeSFXBackend() if with_audio else None,
        )
        return ctx, report

    def _resume(self, out: Path, with_audio: bool = True):
        bible = Bible.load(out / "bible.json")
        ctx = PipelineContext(
            bible=bible,
            config=CanonConfig(seed=SEED, output_dir=out),
            rng=random.Random(SEED),
            llm=LLMClient(FakeLLMBackend(make_fake_responder())),
            prompts=PlatformerPrompts(),
        )
        detect_edits(bible, out)
        report = run_orchestrated(
            ctx, persist_path=out / "bible.json",
            music_producer=FakeMusicBackend() if with_audio else None,
            sfx_producer=FakeSFXBackend() if with_audio else None,
        )
        return ctx, report

    def test_audio_runs_after_level_nodes(self, tmp_path: Path) -> None:
        """Audio is END-of-loop work like art: no generation before the
        levels validate."""
        _ctx, report = self._fresh(tmp_path / "run")
        assert report.ok
        done = report.done
        last_level = max(
            i for i, nid in enumerate(done) if nid.startswith("level:")
        )
        assert done.index("phase:plat:audio") > last_level

    def test_regen_audio_rerolls_audio_only(self, tmp_path: Path) -> None:
        out = tmp_path / "run"
        self._fresh(out)
        bible = Bible.load(out / "bible.json")
        mark_stale(bible, ["phase:plat:audio"])
        bible.persist(out / "bible.json")

        _ctx, report = self._resume(out)
        assert "phase:plat:audio" in report.done
        assert not any(nid.startswith("level:") for nid in report.done)

    def test_pinned_audio_survives_reroll(self, tmp_path: Path) -> None:
        out = tmp_path / "run"
        ctx, _ = self._fresh(out)
        theme = out / ctx.bible.audio[STAGE].music_path
        theme.write_bytes(b"the theme I liked")
        detect_edits(ctx.bible, out)  # adopt the sentinel bytes
        ctx.bible.metadata.pinned = [f"audio:{STAGE}"]
        mark_stale(ctx.bible, ["phase:plat:audio"])
        ctx.bible.persist(out / "bible.json")

        _ctx2, report = self._resume(out)
        assert "phase:plat:audio" in report.done
        assert theme.read_bytes() == b"the theme I liked"
