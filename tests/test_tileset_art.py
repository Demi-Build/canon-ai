"""Diffusion tilesheet tests — tileset_art.py + the tileset phase's
producer seam (asset-regen rules: hashes, §6.1 edges, deterministic fake
path, loud fallback).

FakeImageBackend only: the diffusion path is exercised end-to-end at $0,
per the canned-responder rule (features a fake can't reach go blind).
"""

from __future__ import annotations

import io
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from PIL import Image  # noqa: E402

from canon.backends.testing import FakeImageBackend, FakeLLMBackend  # noqa: E402
from canon.bible.models import Bible  # noqa: E402
from canon.config import CanonConfig  # noqa: E402
from canon.llm.client import LLMClient  # noqa: E402
from canon.pipeline.runner import PipelineContext, run_pipeline  # noqa: E402
from examples.platformer_pack import PlatformerPrompts, compose_pipeline  # noqa: E402
from examples.platformer_pack.graphics import DEFAULT_GRAPHICS  # noqa: E402
from examples.platformer_pack.tiles import DEFAULT_TILES  # noqa: E402
from examples.platformer_pack.tileset_art import (  # noqa: E402
    DiffusionSheetProducer,
    build_image_producer,
    conform_to_palette,
)
from examples.run_platformer_slice import make_fake_responder  # noqa: E402

SEED = "emberfall_001"
STAGE = "ashen_depths"
TILE_PX = DEFAULT_GRAPHICS.tile_px


def _run(output_dir: Path, image_producer=None, **compose_kwargs) -> PipelineContext:
    ctx = PipelineContext(
        bible=Bible.empty(seed=SEED),
        config=CanonConfig(seed=SEED, output_dir=output_dir),
        rng=random.Random(SEED),
        llm=LLMClient(FakeLLMBackend(make_fake_responder())),
        prompts=PlatformerPrompts(),
    )
    run_pipeline(
        compose_pipeline(image_producer=image_producer, **compose_kwargs), ctx
    )
    return ctx


def _textured_png(size: int = 64) -> bytes:
    """A deterministic non-flat PNG — brightness varies per pixel, so
    palette conformance has real texture to preserve."""
    img = Image.new("RGB", (size, size))
    img.putdata(
        [(40 + (x * 3 + y * 5) % 160,) * 3 for y in range(size) for x in range(size)]
    )
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def _mean_rgb(img: Image.Image) -> tuple[float, float, float]:
    # Alpha-aware, mirroring vlm_qa._mean_rgb: object-like tiles (hazards)
    # are cut to a transparent backdrop, so conformance is measured over
    # the VISIBLE pixels; fully-opaque fills average every pixel as before.
    pixels = list(img.convert("RGBA").get_flattened_data())
    visible = [(r, g, b) for r, g, b, a in pixels if a > 0] or [
        p[:3] for p in pixels
    ]
    n = len(visible)
    return tuple(sum(p[i] for p in visible) / n for i in range(3))


class TestConformToPalette:
    def test_mean_color_lands_on_the_role_hex(self) -> None:
        img = Image.open(io.BytesIO(_textured_png())).convert("RGB")
        out = conform_to_palette(img, "#b8804a")
        mean = _mean_rgb(out)
        # Hue/sat are forced exactly; the mean brightness is scaled onto
        # the target's, so the average sample consumers take resolves to
        # the style palette within rounding.
        assert abs(mean[0] - 0xB8) < 6
        assert abs(mean[1] - 0x80) < 6
        assert abs(mean[2] - 0x4A) < 6

    def test_texture_survives(self) -> None:
        img = Image.open(io.BytesIO(_textured_png())).convert("RGB")
        out = conform_to_palette(img, "#b8804a").convert("RGB")
        values = {out.getpixel((x, 7))[0] for x in range(out.width)}
        assert len(values) > 4, "conform flattened the texture away"

    def test_internal_hue_variation_survives(self) -> None:
        """Conformance SHIFTS the mean hue onto the role hex instead of
        forcing every pixel — plank tans stay distinct from base browns
        (the second real run's 'everything is the same mush' finding)."""
        import colorsys as cs

        img = Image.new("RGB", (32, 32))
        for y in range(32):
            for x in range(32):
                # Two distinct source hues, moderately saturated.
                img.putpixel((x, y), (180, 120, 60) if x < 16 else (120, 140, 60))
        out = conform_to_palette(img, "#6b4a2a").convert("RGB")
        hues = {
            round(cs.rgb_to_hsv(*(c / 255 for c in out.getpixel((x, 16))))[0], 2)
            for x in (4, 24)
        }
        assert len(hues) == 2, "hue shift collapsed distinct source hues"
        mean = _mean_rgb(out)
        # Brightness mean still lands (readability bar input) …
        target_lum = 0.299 * 0x6B + 0.587 * 0x4A + 0.114 * 0x2A
        got_lum = 0.299 * mean[0] + 0.587 * mean[1] + 0.114 * mean[2]
        assert abs(got_lum - target_lum) < 10, (got_lum, target_lum)

    def test_all_black_input_fills_flat_target(self) -> None:
        img = Image.new("RGB", (8, 8), (0, 0, 0))
        out = conform_to_palette(img, "#3a6ea5").convert("RGB")
        assert set(out.get_flattened_data()) == {(0x3A, 0x6E, 0xA5)}


class TestDiffusionSheetEndToEnd:
    def test_fake_backend_generates_conformed_deterministic_sheet(
        self, tmp_path: Path
    ) -> None:
        backend = FakeImageBackend(
            placeholder=self._placeholder(tmp_path)
        )
        ctx = _run(tmp_path / "out", DiffusionSheetProducer(backend))

        # One generation per registry tile, prompts built from data.
        # (The same producer also serves sprites/backdrops — filter.)
        tileset = ctx.bible.tilesets[STAGE]
        tile_calls = [
            c for c in backend.calls if "platformer tile:" in c["prompt"]
        ]
        assert len(tile_calls) == len(tileset.slots)
        theme = ctx.bible.stages[STAGE].theme
        for call, slot in zip(tile_calls, tileset.slots):
            assert slot.name in call["prompt"]
            assert theme in call["prompt"]

        # Sheet geometry follows TILE_PX; slots carry the regions.
        sheet = Image.open(tmp_path / "out" / tileset.tilesheet_path)
        assert sheet.size == (TILE_PX * len(tileset.slots), TILE_PX)
        assert tileset.slots[1].px_region == (TILE_PX, 0, TILE_PX, TILE_PX)

        # Every tile's REGION AVERAGE resolves to the style palette hex
        # recorded on the Tileset — conformance is code, not model luck.
        for slot in tileset.slots:
            role = next(
                t.color_role for t in DEFAULT_TILES.tiles if t.name == slot.name
            )
            tile_hex = tileset.palette[role]
            x, y, w, h = slot.px_region
            mean = _mean_rgb(sheet.crop((x, y, x + w, y + h)))
            expected = tuple(int(tile_hex.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
            for got, want in zip(mean, expected):
                assert abs(got - want) < 6, (slot.name, mean, tile_hex)

    def test_two_fake_image_runs_are_byte_identical(self, tmp_path: Path) -> None:
        placeholder = self._placeholder(tmp_path)
        a = _run(
            tmp_path / "a",
            DiffusionSheetProducer(FakeImageBackend(placeholder=placeholder)),
        )
        _run(
            tmp_path / "b",
            DiffusionSheetProducer(FakeImageBackend(placeholder=placeholder)),
        )
        rel = a.bible.tilesets[STAGE].tilesheet_path
        assert (tmp_path / "a" / rel).read_bytes() == (
            tmp_path / "b" / rel
        ).read_bytes()

    def test_image_model_folds_into_provenance(self, tmp_path: Path) -> None:
        plain = _run(tmp_path / "plain")
        art = _run(
            tmp_path / "art",
            DiffusionSheetProducer(
                FakeImageBackend(placeholder=self._placeholder(tmp_path))
            ),
        )
        assert (
            plain.bible.tilesets[STAGE].provenance_hash
            != art.bible.tilesets[STAGE].provenance_hash
        ), "an image-backend swap must invalidate like a model bump (§6.3)"

    @staticmethod
    def _placeholder(tmp_path: Path) -> Path:
        path = tmp_path / "fake_tile.png"
        if not path.exists():
            path.write_bytes(_textured_png())
        return path


class TestContentPolicyRetry:
    def test_flagged_prompt_retries_once_sanitized(self, tmp_path: Path) -> None:
        """A provider content-policy flag gets ONE retry with the flavor
        text stripped (real-run false positive on a single tile); any
        other error propagates to the phase's loud fallback."""

        class FlaggingBackend:
            model = "flagging"

            def __init__(self, placeholder: Path) -> None:
                self.placeholder = placeholder
                self.calls: list[str] = []

            def generate(self, prompt: str, width: int, height: int) -> bytes:
                self.calls.append(prompt)
                if "Smoldering" in prompt:  # the evocative flavor text
                    raise RuntimeError(
                        "[{'type': 'content_policy_violation', ...}]"
                    )
                return self.placeholder.read_bytes()

        placeholder = tmp_path / "tile.png"
        placeholder.write_bytes(_textured_png())
        backend = FlaggingBackend(placeholder)
        producer = DiffusionSheetProducer(backend)
        tile = next(t for t in DEFAULT_TILES.tiles if t.name == "floor")
        img = producer.tile_image(
            tile, "#4b3b2b", "Smoldering Ashen Woodland", "Cinders",
            DEFAULT_GRAPHICS,
        )
        assert img.size == (DEFAULT_GRAPHICS.tile_px, DEFAULT_GRAPHICS.tile_px)
        assert len(backend.calls) == 2  # flagged, then sanitized
        assert "Smoldering" not in backend.calls[1]

    def test_non_policy_errors_propagate(self) -> None:
        class BrokenBackend:
            model = "broken"

            def generate(self, prompt: str, width: int, height: int) -> bytes:
                raise RuntimeError("503 service unavailable")

        producer = DiffusionSheetProducer(BrokenBackend())
        tile = next(t for t in DEFAULT_TILES.tiles if t.name == "floor")
        try:
            producer.tile_image(tile, "#4b3b2b", "theme", "world", DEFAULT_GRAPHICS)
        except RuntimeError as e:
            assert "503" in str(e)
        else:
            raise AssertionError("expected RuntimeError")


class TestLoudFallback:
    def test_backend_failure_warns_per_tile_and_falls_back(
        self, tmp_path: Path
    ) -> None:
        class ExplodingBackend:
            model = "exploding"

            def generate(self, prompt: str, width: int, height: int) -> bytes:
                raise RuntimeError("boom")

        ctx = _run(tmp_path / "broken", DiffusionSheetProducer(ExplodingBackend()))
        tileset = ctx.bible.tilesets[STAGE]
        warnings = [
            w
            for w in ctx.artifacts.get("slice_warnings", [])
            if w.startswith("tileset art:")
        ]
        assert len(warnings) == len(tileset.slots)
        for slot, message in zip(tileset.slots, warnings):
            assert slot.name in message
            assert "placeholder" in message

        # The fallback sheet IS the placeholder sheet — byte-identical to
        # a run with no producer at all.
        plain = _run(tmp_path / "plain")
        assert (
            tmp_path / "broken" / tileset.tilesheet_path
        ).read_bytes() == (
            tmp_path / "plain" / plain.bible.tilesets[STAGE].tilesheet_path
        ).read_bytes()


class TestGraphicsSpec:
    """GraphicsSpec — target resolution + art style as per-game template
    data (values in data, categories in code), swappable per game."""

    def test_default_spec_matches_pack_template(self) -> None:
        assert DEFAULT_GRAPHICS.tile_px == 32
        assert DEFAULT_GRAPHICS.render_filter == "crisp"
        assert DEFAULT_GRAPHICS.posterize_levels == 16

    def test_unknown_keys_ride_inert_and_change_the_digest(self) -> None:
        from examples.platformer_pack.graphics import GraphicsSpec

        spec = GraphicsSpec.model_validate(
            {"tile_px": 32, "sprite_px": 64}  # future knob, no enforcement yet
        )
        assert spec.model_dump()["sprite_px"] == 64  # open carriage
        assert spec.digest() != DEFAULT_GRAPHICS.digest()

    def test_bounds_are_validated(self) -> None:
        import pydantic
        import pytest

        from examples.platformer_pack.graphics import GraphicsSpec

        with pytest.raises(pydantic.ValidationError):
            GraphicsSpec.model_validate({"tile_px": 4})
        with pytest.raises(pydantic.ValidationError):
            GraphicsSpec.model_validate({"render_filter": "cinematic"})

    def test_example_specs_prove_the_swap(self) -> None:
        from examples.platformer_pack.graphics import load_graphics

        root = Path(__file__).parent.parent / "examples" / "graphics_specs"
        snes = load_graphics(root / "snes_pixel.json")
        hd = load_graphics(root / "rendered_hd.json")
        assert (snes.tile_px, snes.render_filter) == (16, "crisp")
        assert (hd.tile_px, hd.render_filter) == (128, "smooth")
        assert hd.posterize_levels is None
        assert snes.art_style != hd.art_style

    def test_sheet_geometry_and_manifest_follow_the_spec(
        self, tmp_path: Path
    ) -> None:
        from examples.platformer_pack.graphics import load_graphics

        root = Path(__file__).parent.parent / "examples" / "graphics_specs"
        for name, tile_px, filt in (
            ("snes_pixel", 16, "crisp"),
            ("rendered_hd", 128, "smooth"),
        ):
            spec = load_graphics(root / f"{name}.json")
            ctx = _run(tmp_path / name, graphics=spec)
            tileset = ctx.bible.tilesets[STAGE]
            assert tileset.slots[1].px_region == (tile_px, 0, tile_px, tile_px)
            assert tileset.render_filter == filt
            sheet = Image.open(tmp_path / name / tileset.tilesheet_path)
            assert sheet.size == (tile_px * len(tileset.slots), tile_px)
            manifest = json.loads(
                (tmp_path / name / f"tileset/{STAGE}/manifest.json").read_text()
            )
            assert manifest["render_filter"] == filt

    def test_art_style_reaches_the_prompt(self, tmp_path: Path) -> None:
        backend = FakeImageBackend()
        _run(
            tmp_path / "out",
            DiffusionSheetProducer(backend),
        )
        assert backend.calls
        for call in backend.calls:
            assert DEFAULT_GRAPHICS.art_style in call["prompt"]
            assert call["width"] == DEFAULT_GRAPHICS.gen_px

    def test_posterize_bounds_brightness_levels(self) -> None:
        img = Image.open(io.BytesIO(_textured_png())).convert("RGB")
        out = conform_to_palette(img, "#b8804a", levels=8).convert("RGB")
        distinct = set(out.get_flattened_data())
        # The constant recenter shift preserves the level count (clamping
        # can only merge levels, never split them).
        assert len(distinct) <= 8
        assert len(distinct) > 2  # still textured, not flattened
        mean = _mean_rgb(out)
        for got, want in zip(mean, (0xB8, 0x80, 0x4A)):
            assert abs(got - want) < 6, mean

    def test_graphics_swap_invalidates_provenance(self, tmp_path: Path) -> None:
        """Same placeholder bytes (tile_px unchanged), different art_style:
        the spec digest must invalidate provenance anyway — a graphics
        swap is a generation-input change like a model bump (§6.3)."""
        from examples.platformer_pack.graphics import GraphicsSpec

        base = _run(tmp_path / "base")
        restyled_spec = GraphicsSpec.model_validate(
            {**DEFAULT_GRAPHICS.model_dump(), "art_style": "totally different"}
        )
        restyled = _run(tmp_path / "restyled", graphics=restyled_spec)

        rel = base.bible.tilesets[STAGE].tilesheet_path
        assert (tmp_path / "base" / rel).read_bytes() == (
            tmp_path / "restyled" / rel
        ).read_bytes(), "placeholder bytes should match — that's the point"
        assert (
            base.bible.tilesets[STAGE].provenance_hash
            != restyled.bible.tilesets[STAGE].provenance_hash
        )


class TestBuildImageProducer:
    def test_none_and_empty_stay_placeholder(self) -> None:
        assert build_image_producer(None) is None
        assert build_image_producer("") is None
        assert build_image_producer("none") is None

    def test_fake_builds_a_producer(self) -> None:
        producer = build_image_producer("fake")
        assert isinstance(producer, DiffusionSheetProducer)
        assert producer.model == "FakeImageBackend"

    def test_unknown_kind_raises(self) -> None:
        try:
            build_image_producer("dalle")
        except ValueError as e:
            assert "dalle" in str(e)
        else:
            raise AssertionError("expected ValueError")

    def test_fal_without_credentials_fails_fast(self, monkeypatch) -> None:
        """Missing credentials are known at launch — die BEFORE any paid
        LLM work, not 13 warnings into the art phases (first real run)."""
        for key in ("FAL_KEY", "FAL_KEY_ID", "FAL_KEY_SECRET"):
            monkeypatch.delenv(key, raising=False)
        try:
            build_image_producer("fal")
        except ValueError as e:
            assert "FAL_KEY" in str(e)
        else:
            raise AssertionError("expected ValueError")

    def test_fal_with_credentials_builds(self, monkeypatch) -> None:
        import pytest

        pytest.importorskip("fal_client")
        monkeypatch.setenv("FAL_KEY", "test-key")
        producer = build_image_producer("fal")
        assert producer.model == "fal-ai/nano-banana"
