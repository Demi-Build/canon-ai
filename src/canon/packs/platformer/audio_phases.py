"""Late audio phase — paid generation AT THE END of the loop (user rule),
exactly the art-track pattern: gameplay never depends on it, the fake path
is byte-deterministic, missing producers are a logged no-op (a silent game
IS the fallback), and every emitted file is a hashed §6.1 artifact.

- ``plat:audio`` — one looping music theme per stage
  (``music/<stage>/theme.<ext>``) + a small closed SFX event set
  (``sfx/<stage>/<event>.<ext>``), owned by a ``StageAudio`` artifact
  (``audio:<stage_id>``) so a re-rolled theme never cascades staleness
  through the stage's levels. Pinnable like any art artifact.

Backends are only ever constructed from an EXPLICIT flag
(``--music-backend lyria`` / ``--sfx-backend elevenlabs`` or their env
equivalents) — the LLM backend choice never implies audio spend, and
missing credentials fail at launch, before any generation money is spent.
"""

from __future__ import annotations

import logging
from typing import Any

from canon.bible.artifacts import make_artifact_id
from canon.bible.platformer import StageAudio
from canon.packs.platformer.phases import (
    _stamp_metadata,
    stamp_provenance,
    step,
    warn,
)
from canon.pipeline.orchestrator import pinned_ids

logger = logging.getLogger(__name__)

#: Closed v1 SFX vocabulary: (event, prompt fragment, seconds, loop).
#: Events are runtime seams both play surfaces already have — a new event
#: only becomes real with its trigger point in BOTH consumers.
SFX_EVENTS: tuple[tuple[str, str, float, bool], ...] = (
    ("jump", "a short soft retro jump hop whoosh", 0.4, False),
    ("checkpoint", "a warm two-note chime, checkpoint activated", 0.8, False),
    ("death", "a quick descending retro defeat thud", 0.6, False),
    ("win", "a bright short victory fanfare, level complete", 1.4, False),
)

#: SFX backends accept a bounded duration — ElevenLabs rejects anything
#: outside [0.5, 30]s (the first paid run lost every 'jump' SFX to a 0.4s
#: request, "invalid_generation_settings"). Clamp the vocabulary's seconds
#: into range at the call site so a short event degrades to the floor
#: instead of vanishing.
SFX_MIN_SECONDS = 0.5
SFX_MAX_SECONDS = 30.0

#: Theme length. Under 60s stays on Lyria's cheaper clip model.
MUSIC_SECONDS = 30


def _add_audio_cost(ctx: Any, producer: Any) -> None:
    """Accumulate a producer's most-recent per-call cost into
    ``ctx.stats.audio_cost_usd``. The producers are SYNCHRONOUS (no gather),
    so ``last_cost`` is unambiguously this call's — read it right after
    ``generate``. Music/SFX are flat-billed (Lyria/ElevenLabs per-call
    prices), so this IS the actual audio spend. No-op without stats."""
    stats = getattr(ctx, "stats", None)
    if stats is None:
        return
    cost = float(getattr(producer, "last_cost", 0.0) or 0.0)
    stats.audio_cost_usd = float(getattr(stats, "audio_cost_usd", 0.0)) + cost


def _audio_ext(data: bytes) -> str:
    """Extension by content sniff — backends emit whatever their API
    returns (Lyria wav/mp3, ElevenLabs mp3, fakes mp3) and consumers pick
    their loader by extension."""
    if data[:4] == b"RIFF":
        return ".wav"
    if data[:4] == b"OggS":
        return ".ogg"
    return ".mp3"


def build_music_producer(kind: str | None):
    """CLI/env wiring: a music-backend name → backend, or ``None`` for
    silence. Paid backends only from an explicit flag; missing keys die
    at launch, before any LLM spend."""
    if not kind or kind == "none":
        return None
    if kind == "fake":
        from canon.backends.testing import FakeMusicBackend

        return FakeMusicBackend()
    if kind == "lyria":
        import os

        if not os.environ.get("GOOGLE_API_KEY"):
            raise ValueError(
                "--music-backend lyria needs GOOGLE_API_KEY in the "
                "environment. A .env file is NOT read automatically — "
                "export it first, e.g.: set -a; source .env; set +a"
            )
        from canon.backends.music_lyria import LyriaMusicBackend

        return LyriaMusicBackend()
    raise ValueError(f"unknown music backend {kind!r} (none|fake|lyria).")


def build_sfx_producer(kind: str | None):
    """CLI/env wiring for SFX: ``None`` for silence, fake, or ElevenLabs."""
    if not kind or kind == "none":
        return None
    if kind == "fake":
        from canon.backends.testing import FakeSFXBackend

        return FakeSFXBackend()
    if kind == "elevenlabs":
        import os

        if not os.environ.get("ELEVENLABS_API_KEY"):
            raise ValueError(
                "--sfx-backend elevenlabs needs ELEVENLABS_API_KEY in the "
                "environment. A .env file is NOT read automatically — "
                "export it first, e.g.: set -a; source .env; set +a"
            )
        from canon.backends.sfx_elevenlabs import ElevenLabsSFXBackend

        return ElevenLabsSFXBackend()
    raise ValueError(f"unknown sfx backend {kind!r} (none|fake|elevenlabs).")


def _producer_model(producer: Any) -> str:
    return str(
        getattr(producer, "model", None)
        or getattr(producer, "model_clip", None)
        or type(producer).__name__
    )


class AudioPhase:
    """Music theme + SFX set per stage. Both producers optional and
    independent — a game can score music without SFX or vice versa."""

    name = "plat:audio"

    def __init__(
        self,
        music_producer: Any = None,
        sfx_producer: Any = None,
        music_seconds: int = MUSIC_SECONDS,
        music_prompt_override: str | None = None,
    ) -> None:
        self.music = music_producer
        self.sfx = sfx_producer
        self.music_seconds = music_seconds
        # Per-call MUSIC prompt override ("✎ edit prompt"); only the
        # single-stage `generate_asset` op sets it (its bible holds one
        # stage). SFX prompts are per-event and stay built. None = default.
        self.music_prompt_override = music_prompt_override

    def owns(self, ctx: Any) -> list[str]:
        return [
            make_artifact_id("audio", sid)
            for sid in getattr(ctx.bible, "stages", {})
        ]

    def run(self, ctx: Any) -> None:
        if self.music is None and self.sfx is None:
            logger.info("AudioPhase: no audio producers — silent game kept.")
            _stamp_metadata(ctx, self.name)
            return

        world_title = ctx.bible.world.title if ctx.bible.world else ""
        pinned = pinned_ids(ctx.bible)
        for stage_id, stage in ctx.bible.stages.items():
            aid = make_artifact_id("audio", stage_id)
            if aid in pinned:
                logger.info("AudioPhase: audio:%s is pinned — kept.", stage_id)
                continue
            audio = StageAudio(
                artifact_id=aid,
                stage_id=stage_id,
                parents=[make_artifact_id("stage", stage_id)],
            )
            if self.music is not None:
                step(ctx, self.name, f"{stage_id} · music")
                try:
                    data = self.music.generate(
                        (self.music_prompt_override or "").strip()
                        or (
                            f"Looping instrumental level theme for a retro "
                            f"platformer stage: {stage.theme}. World: "
                            f"{world_title}. Melodic, atmospheric, seamless "
                            f"loop, no vocals."
                        ),
                        self.music_seconds,
                    )
                    rel = f"music/{stage_id}/theme{_audio_ext(data)}"
                    audio.music_path = rel
                    audio.music_hash = ctx.adapter.write_binary(rel, data)
                    _add_audio_cost(ctx, self.music)
                except Exception as e:  # noqa: BLE001
                    warn(
                        ctx,
                        f"music: theme generation failed for stage "
                        f"{stage_id!r} ({type(e).__name__}: {e}); the game "
                        "stays silent.",
                    )
            if self.sfx is not None:
                for event, prompt, seconds, loop in SFX_EVENTS:
                    step(ctx, self.name, f"{stage_id} · sfx {event}")
                    clamped = min(SFX_MAX_SECONDS, max(SFX_MIN_SECONDS, seconds))
                    try:
                        data = self.sfx.generate(
                            f"{prompt} — retro platformer sound effect, "
                            f"{stage.theme}",
                            clamped,
                            loop,
                        )
                    except Exception as e:  # noqa: BLE001
                        warn(
                            ctx,
                            f"sfx: {event!r} generation failed for stage "
                            f"{stage_id!r} ({type(e).__name__}: {e}); that "
                            "event stays silent.",
                        )
                        continue
                    rel = f"sfx/{stage_id}/{event}{_audio_ext(data)}"
                    audio.sfx_paths[event] = rel
                    audio.sfx_hashes[rel] = ctx.adapter.write_binary(rel, data)
                    _add_audio_cost(ctx, self.sfx)

            manifest_hash = ctx.adapter.write_json_singleton(
                f"audio/{stage_id}/manifest.json",
                audio.model_dump(mode="json"),
            )
            model_bits = []
            if self.music is not None:
                model_bits.append(f"music:{_producer_model(self.music)}")
            if self.sfx is not None:
                model_bits.append(f"sfx:{_producer_model(self.sfx)}")
            stamp_provenance(
                ctx, audio, manifest_hash, model_extra="+".join(model_bits)
            )
            ctx.bible.audio[stage_id] = audio
            logger.info(
                "AudioPhase wrote %s + %d sfx for stage %s.",
                audio.music_path or "no music", len(audio.sfx_paths), stage_id,
            )
        _stamp_metadata(ctx, self.name)
