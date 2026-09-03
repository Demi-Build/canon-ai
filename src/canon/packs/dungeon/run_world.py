"""The dungeon pack's create runner — ``canon world new --template dungeon``
spawns exactly this module (row P0-10, W2 work item 1).

The twin of ``canon.packs.platformer.run_slice``, and deliberately the same
shape so the registry can dispatch to either by data (``PackSpec.runner``):
argparse flags in, a wired ``PipelineContext`` with a **StepLog attached**,
``run_pipeline``, exit code out (0 ok · 1 failed · 3 cancelled).

W2 named two wiring blockers for the dungeon template. The estimator was one
(closed at P0-7); **no StepLog** was the other, and this module closes it:

- the schedulers already emit ``run_start`` / ``node_start`` / ``node_done`` /
  ``run_end`` for any context carrying a ``steplog`` (``pipeline/runner.py``) —
  attaching one is all a dungeon run needed to appear in cradle's progress
  relay with no relay change at all (master §3.0-E);
- ``node_item`` comes from the ONE emitter ``canon.pipeline.steplog.step``,
  which A4.5 put the cancel check inside — so ⏹ Stop works on a dungeon
  create for free, at an item boundary, keeping everything that landed
  (master §3.0-D). Row P0-10 only had to move that emitter down from the
  platformer pack and call it from the dungeon's item loops.

Everything paid is opt-in and defaults OFF: ``--backend fake`` +
``--assets none`` is a $0 run (doctrine 3 — nothing here calls a real
provider unless the user asked for it and supplied the key).

Deliberately absent, by row ownership: an orchestrated scheduler for the
dungeon (it has one linear pipeline; ``--orchestrate`` is a platformer
capability the registry declares per template), the pygame launch (W2.0's
pull-in), and per-step regen (the dungeon's grid verbs are P0-8's).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from canon.backends.testing import (
    FakeImageBackend,
    FakeLLMBackend,
    FakeMusicBackend,
    FakeSFXBackend,
)
from canon.llm.client import LLMClient
from canon.packs.dungeon.compose import compose_pipeline
from canon.packs.dungeon.fakes import make_fake_responder
from canon.pipeline.phases import AssetPhase
from canon.pipeline.runner import run_pipeline
from canon.pipeline.steplog import EXIT_CANCELLED, RunCancelled, StepLog

#: CLI flag → the ``counts`` key ``compose_pipeline`` reads. ``--num-maps`` is
#: separate (it is a pipeline argument, not an entity count).
COUNT_FLAGS: dict[str, str] = {
    "npcs": "npc",
    "monsters": "monster",
    "items": "item",
    "events": "event",
    "quests": "quest",
    "classes": "class",
}


def build_llm_backend(kind: str, num_maps: int, model: str | None = None):
    """The LLM backend for *kind* — ``fake`` is the $0 default and the only
    one that runs without a key (doctrine 3: paid legs are user-run)."""
    if kind == "fake":
        return FakeLLMBackend(make_fake_responder(num_maps))
    if kind == "anthropic":
        from canon.backends.anthropic import AnthropicBackend

        return AnthropicBackend(model=model) if model else AnthropicBackend()
    raise SystemExit(f"Unknown --backend: {kind!r}")


def build_asset_backends(image: str, music: str, sfx: str):
    """``(image, music, sfx)`` backends, each independently ``none`` / ``fake``
    / a real provider — the same per-generator split ``world new`` offers on
    the platformer, so one wizard drives both templates."""

    def one(kind: str, name: str):
        if name in ("", "none"):
            return None
        if name == "fake":
            return {"image": FakeImageBackend, "music": FakeMusicBackend, "sfx": FakeSFXBackend}[kind]()
        if kind == "image" and name == "fal":
            from canon.backends.image_fal import FalImageBackend

            return FalImageBackend()
        if kind == "image" and name == "local":
            from canon.backends.image_local import LocalImageBackend

            return LocalImageBackend()
        if kind == "music" and name == "lyria":
            from canon.backends.music_lyria import LyriaMusicBackend

            return LyriaMusicBackend()
        if kind == "sfx" and name == "elevenlabs":
            from canon.backends.sfx_elevenlabs import ElevenLabsSFXBackend

            return ElevenLabsSFXBackend()
        raise SystemExit(f"Unknown --{kind}-backend for the dungeon template: {name!r}")

    return one("image", image), one("music", music), one("sfx", sfx)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a dungeon-crawler pack.")
    parser.add_argument("--backend", choices=["fake", "anthropic"], default="fake")
    parser.add_argument("--model", default=None, help="LLM model id (anthropic only).")
    parser.add_argument("--output-dir", default="./dungeon_output")
    parser.add_argument("--seed", default="shadowspire_001")
    parser.add_argument("--num-maps", type=int, default=3, help="Rooms to generate.")
    parser.add_argument("--image-backend", default="none", help="none | fake | fal | local")
    parser.add_argument("--music-backend", default="none", help="none | fake | lyria")
    parser.add_argument("--sfx-backend", default="none", help="none | fake | elevenlabs")
    for flag in COUNT_FLAGS:
        parser.add_argument(f"--{flag}", type=int, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point — ``argv`` defaults to ``sys.argv[1:]``; returns the process
    exit code (``python -m canon.packs.dungeon.run_world``)."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)

    args = _parser().parse_args(argv)
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    counts = {
        key: getattr(args, flag)
        for flag, key in COUNT_FLAGS.items()
        if getattr(args, flag) is not None
    }
    phases, ctx = compose_pipeline(
        seed=args.seed,
        num_maps=args.num_maps,
        output_dir=output_dir,
        counts=counts,
        model=args.model,
    )

    # The StepLog is THE deliverable of this module (W2's blocker): attached to
    # the context, both schedulers emit the five event kinds into
    # `<pack>/.canon/log.jsonl`, which is exactly the file cradle's job worker
    # already tails for `job-progress` (no relay change — master §3.0-E).
    ctx.steplog = StepLog(output_dir)
    ctx.llm = LLMClient(build_llm_backend(args.backend, args.num_maps, args.model), stats=ctx.stats)
    ctx.stats.llm_backend = args.backend

    image, music, sfx = build_asset_backends(args.image_backend, args.music_backend, args.sfx_backend)
    if image is not None:
        ctx.image_backend = image
        ctx.stats.image_backend = args.image_backend
    if music is not None:
        ctx.music_backend = music
        ctx.stats.music_backend = args.music_backend
    if sfx is not None:
        ctx.sfx_backend = sfx
        ctx.stats.sfx_backend = args.sfx_backend
    # compose_pipeline seeds a skip-all AssetPhase; a wired backend replaces it
    # with the generating one (the example runner's rule, kept).
    if any(b is not None for b in (image, music, sfx)):
        phases = [
            AssetPhase(
                skip_image=image is None,
                skip_music=music is None,
                skip_sfx=sfx is None,
            )
            if p.name == "assets"
            else p
            for p in phases
        ]

    # Row P1-A4.5 (§3.0-D): ⏹ Stop reaches a running create as a per-job cancel
    # FILE (CANON_CANCEL_FILE, set by cradle's JobQueue at spawn). The
    # ``node_item`` emitter raises at the next item boundary; the scheduler's
    # own ``run_end`` carries ``cancelled: true`` + ``kept``; nothing that
    # landed is undone and this runner exits 3.
    try:
        run_pipeline(phases, ctx)
    except RunCancelled as cancelled:
        print(
            f"\n!! Cancelled before {cancelled.node} · {cancelled.item} — "
            f"{len(cancelled.kept)} completed item(s) kept "
            f"(see {output_dir}/.canon/log.jsonl)"
        )
        return EXIT_CANCELLED
    if ctx.steplog.cancelled is not None:
        cancelled = ctx.steplog.cancelled
        print(
            f"\n!! Cancelled at {cancelled.node} · {cancelled.item} — "
            f"kept {len(ctx.steplog.kept())} item(s) (see {output_dir}/.canon/log.jsonl)"
        )
        return EXIT_CANCELLED

    print(f"\nDungeon generated at {output_dir}/")
    print(f"  rooms:      {len(ctx.bible.maps)}")
    print(f"  classes:    {len(ctx.bible.class_archetypes)}")
    print(f"  LLM calls:  {ctx.stats.llm_calls}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
