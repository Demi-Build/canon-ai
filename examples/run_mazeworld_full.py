"""Run canon-ai's full mazeworld-shape pipeline end-to-end.

Three modes:
    --backend fake       — FakeLLMBackend with canned responses; $0; deterministic
    --backend anthropic  — Real Claude calls via canon[anthropic]
    --backend none       — error; one of the above is required

Asset modes:
    --assets fake        — FakeImageBackend / FakeMusicBackend / FakeSFXBackend; $0
    --assets api         — FalImageBackend + LyriaMusicBackend + ElevenLabsSFXBackend (~$30 for 3 maps)
    --assets none        — skip all asset generation; placeholder paths

Output:
    A complete `data/` tree at --output-dir matching mazeworld's expected
    layout. Drop into MazeWorld/data_canon/ and point MazeWorld/config.py
    DATA_DIR there to test loading.

Usage:
    python examples/run_mazeworld_full.py --backend fake --assets fake \\
        --output-dir /tmp/canon_run --num-maps 3
    ANTHROPIC_API_KEY=sk-... FAL_KEY=... GOOGLE_API_KEY=... ELEVENLABS_API_KEY=... \\
        python examples/run_mazeworld_full.py --backend anthropic --assets api
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running from repo root without installing
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from canon import (  # noqa: E402
    AssetPhase,
    CanonConfig,
    FakeImageBackend,
    FakeLLMBackend,
    FakeMusicBackend,
    FakeSFXBackend,
    LLMClient,
    run_pipeline,
)
from canon.packs.dungeon import compose_pipeline  # noqa: E402
from canon.packs.dungeon.fakes import make_fake_responder  # noqa: E402


def build_llm_backend(kind: str, num_maps: int, model: str | None = None):
    """Return an LLM backend instance + (for fake mode) the responder."""
    if kind == "fake":
        responder = make_fake_responder(num_maps)
        return FakeLLMBackend(responder)
    if kind == "anthropic":
        from canon.backends.anthropic import AnthropicBackend  # noqa: E402

        return AnthropicBackend(model=model) if model else AnthropicBackend()
    raise SystemExit(f"Unknown --backend: {kind!r}")


def build_asset_backends(kind: str):
    """Return (image, music, sfx) backend instances or (None, None, None)."""
    if kind == "none":
        return None, None, None
    if kind == "fake":
        return FakeImageBackend(), FakeMusicBackend(), FakeSFXBackend()
    if kind == "api":
        from canon.backends.image_fal import FalImageBackend  # noqa: E402
        from canon.backends.music_lyria import LyriaMusicBackend  # noqa: E402
        from canon.backends.sfx_elevenlabs import ElevenLabsSFXBackend  # noqa: E402

        return FalImageBackend(), LyriaMusicBackend(), ElevenLabsSFXBackend()
    raise SystemExit(f"Unknown --assets: {kind!r}")


def main() -> int:
    # Surface canon's INFO-level progress logs (per-phase success/retry lines from
    # retry_with_feedback). Mirrors MazeWorld's pipeline.py logging setup; httpx /
    # anthropic are pinned to WARNING so their request chatter doesn't drown it out.
    import logging

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)

    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=["fake", "anthropic"], default="fake")
    parser.add_argument("--assets", choices=["none", "fake", "api"], default="fake")
    parser.add_argument("--config", default=None,
                        help="CanonConfig file (.toml/.json) — counts, model, seed, num_maps")
    parser.add_argument("--model", default=None,
                        help="LLM model id override, e.g. claude-haiku-4-5-20251001")
    # seed/num-maps default None so we can tell "not provided" from a value and
    # apply precedence CLI > --config > default.
    parser.add_argument("--seed", default=None)
    parser.add_argument("--num-maps", type=int, default=None)
    parser.add_argument("--output-dir", default="/tmp/canon_run")
    # Per-entity-type count overrides. These are mazeworld-specific runner flags
    # that populate the GENERIC CanonConfig.counts dict — core canon stays
    # game-agnostic; only the pack/runner knows these names.
    for _flag in ("npcs", "items", "monsters", "events", "quests", "classes"):
        parser.add_argument(f"--{_flag}", type=int, default=None)
    args = parser.parse_args()

    # Optional config file, then CLI overrides.
    # Precedence (highest first): CLI flag > --config file > pack default.
    cfg = None
    if args.config:
        cfg_path = Path(args.config)
        cfg = (
            CanonConfig.from_toml(cfg_path)
            if cfg_path.suffix.lower() == ".toml"
            else CanonConfig.from_json(cfg_path)
        )

    seed = args.seed or (cfg.seed if cfg else None) or "shadowspire_001"
    num_maps = args.num_maps if args.num_maps is not None else (cfg.num_maps if cfg else 3)
    model = args.model or (cfg.model if cfg else None)

    # CLI count flags → generic counts dict (flag name → entity_type key).
    _flag_to_type = {
        "npcs": "npc", "items": "item", "monsters": "monster",
        "events": "event", "quests": "quest", "classes": "class",
    }
    cli_counts = {
        etype: getattr(args, flag)
        for flag, etype in _flag_to_type.items()
        if getattr(args, flag) is not None
    }
    counts = {**(cfg.counts if cfg else {}), **cli_counts}

    print("==> Running canon mazeworld pipeline")
    print(f"    backend={args.backend}, assets={args.assets}, seed={seed!r}, maps={num_maps}")
    if model:
        print(f"    model={model}")
    if counts:
        print(f"    count overrides={counts}")
    print(f"    output={args.output_dir}")

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    phases, ctx = compose_pipeline(
        seed=seed, num_maps=num_maps, output_dir=output_dir, counts=counts, model=model
    )

    # Wire LLM
    llm_backend = build_llm_backend(args.backend, num_maps, model)
    ctx.llm = LLMClient(llm_backend, stats=ctx.stats)
    ctx.stats.llm_backend = args.backend

    # Wire assets — set them on ctx so AssetPhase finds them via getattr
    img, mus, sfx = build_asset_backends(args.assets)
    if img is not None:
        ctx.image_backend = img
        ctx.stats.image_backend = "fake" if args.assets == "fake" else "api"
    if mus is not None:
        ctx.music_backend = mus
        ctx.stats.music_backend = "fake" if args.assets == "fake" else "api"
    if sfx is not None:
        ctx.sfx_backend = sfx
        ctx.stats.sfx_backend = "fake" if args.assets == "fake" else "api"

    # If assets are wired, replace the AssetPhase that compose_pipeline added
    # (which defaults to skip-all) with a non-skipping one.
    if args.assets != "none":
        phases = [p if p.name != "assets" else AssetPhase() for p in phases]

    # Run
    run_pipeline(phases, ctx)

    print()
    print(f"==> Wrote {output_dir}/")
    print(f"    title:       {ctx.bible.story.title!r}")
    print(f"    factions:    {len(ctx.bible.story.factions)}")
    print(f"    maps:        {len(ctx.bible.maps)}")
    print(f"    archetypes:  {len(ctx.bible.class_archetypes)}")
    print(f"    characters:  {len(ctx.bible.characters)}")
    print(f"    dialogues:   {len(ctx.bible.dialogues)}")
    total_entities = sum(len(m.entities) for m in ctx.bible.maps.values())
    print(f"    entities:    {total_entities}")
    print(f"    LLM calls:   {ctx.stats.llm_calls}")
    print(f"    Image gen:   {ctx.stats.image_attempts} attempts, {ctx.stats.image_successes} succeeded")
    print(f"    Music gen:   {getattr(ctx.stats, 'music_attempted', 0)} attempts")
    print(f"    SFX gen:     {getattr(ctx.stats, 'sfx_attempted', 0)} attempts")
    if args.backend == "anthropic":
        print(f"    LLM cost:    ${ctx.stats.llm_cost_usd:.4f}")
    if args.assets == "api":
        print(f"    Image cost:  ${ctx.stats.image_cost_usd:.4f}")
        print(f"    Audio cost:  ${ctx.stats.audio_cost_usd:.4f}")
        print(f"    Total cost:  ${ctx.stats.total_cost_usd:.4f}")

    print()
    print("==> Files produced:")
    for p in sorted(output_dir.rglob("*")):
        if p.is_file():
            rel = p.relative_to(output_dir)
            print(f"    {rel}")

    if args.assets == "fake":
        print()
        print("    " + "=" * 64)
        print("    TEST ASSETS — generated with fake backends.")
        print("    Portraits are blank and SFX/music are SILENT placeholders;")
        print("    no real audio was generated. The game will load them without")
        print("    error but nothing will play. generation_stats.json records")
        print('    "assets_placeholder": true. Re-run with --assets api for real')
        print("    portraits + audio.")
        print("    " + "=" * 64)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
