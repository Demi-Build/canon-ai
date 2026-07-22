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

        # One generation per unique tile NAME (autotile variants share
        # their base tile's art), prompts built from data. (The same
        # producer also serves sprites/backdrops — filter.)
        tileset = ctx.bible.tilesets[STAGE]
        tile_calls = [
            c for c in backend.calls if "platformer tile:" in c["prompt"]
        ]
        names = list(dict.fromkeys(s.name for s in tileset.slots))
        assert len(tile_calls) == len(names)
        theme = ctx.bible.stages[STAGE].theme
        for call, name in zip(tile_calls, names):
            assert name in call["prompt"]
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

    def test_water_deep_slot_repaints_liplessly(self, tmp_path: Path) -> None:
        """Water-striping fix: on TEXTURED art the submerged-interior
        sibling gets derive_water_deep'd pixels (different bytes than
        the surface slot) while resolving to the same palette mean —
        the region-average contract both consumers and QA sample."""
        # A dedicated placeholder with a BRIGHT TOP BAND (the painted
        # surface lip): the shared _textured_png is y-periodic with
        # period 16 at 32px, so its halves coincide and the derive
        # would be an (arithmetically legitimate) no-op on it.
        lip = Image.new("RGB", (64, 64))
        lip.putdata(
            [
                (230, 235, 240) if y < 16 else (40 + (x * 3 + y * 7) % 120,) * 3
                for y in range(64)
                for x in range(64)
            ]
        )
        lip_path = tmp_path / "lip_tile.png"
        lip.save(lip_path)
        ctx = _run(
            tmp_path / "out",
            DiffusionSheetProducer(FakeImageBackend(placeholder=lip_path)),
        )
        tileset = ctx.bible.tilesets[STAGE]
        surface = next(
            s for s in tileset.slots
            if s.collision == "volume" and not s.params.get("water_deep")
        )
        deep = next(
            s for s in tileset.slots if s.params.get("water_deep")
        )
        sheet = Image.open(tmp_path / "out" / tileset.tilesheet_path)
        x, y, w, h = surface.px_region
        surf_px = sheet.crop((x, y, x + w, y + h))
        x, y, w, h = deep.px_region
        deep_px = sheet.crop((x, y, x + w, y + h))
        assert list(surf_px.get_flattened_data()) != list(
            deep_px.get_flattened_data()
        ), "deep slot kept the surface pixels — derive never ran"
        for got, want in zip(_mean_rgb(deep_px), _mean_rgb(surf_px)):
            assert abs(got - want) < 3, "deep variant drifted the mean"

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

    def test_repaint_carries_text_provenance_placeholder_does_not(
        self, tmp_path: Path
    ) -> None:
        """G6 readable provenance: the producer-GENERATED repaint carries
        the canon:* tEXt stamp; the code-drawn placeholder sheet (same
        path, no producer) stays unstamped."""
        plain = _run(tmp_path / "plain")
        placeholder = Image.open(
            tmp_path / "plain" / plain.bible.tilesets[STAGE].tilesheet_path
        )
        assert "canon:generator" not in getattr(placeholder, "text", {})

        art = _run(
            tmp_path / "art",
            DiffusionSheetProducer(
                FakeImageBackend(placeholder=self._placeholder(tmp_path))
            ),
        )
        sheet = Image.open(
            tmp_path / "art" / art.bible.tilesets[STAGE].tilesheet_path
        )
        assert sheet.text["canon:generator"] == "canon-ai harness"
        assert sheet.text["canon:asset"] == f"tilesheet:{STAGE}"
        assert sheet.text["canon:backend-model"] == "FakeImageBackend"
        assert sheet.text["canon:image-request-id"] == ""  # fake reports none
        assert sheet.text["canon:image-seed"] == ""  # fake declares no seed
        assert sheet.text["canon:seed"] == SEED
        assert sheet.text["canon:gfx"] == DEFAULT_GRAPHICS.digest()
        assert sheet.text["canon:prompt-version"] == "slice-1"
        assert sheet.text["canon:provenance"] == "readable-v1"

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


class TestPngProvenance:
    """png_bytes + art_provenance — the G6 readable layer's encode seam."""

    def test_same_write_twice_is_byte_identical(self) -> None:
        from examples.platformer_pack.tileset_art import (
            art_provenance,
            png_bytes,
        )

        img = Image.open(io.BytesIO(_textured_png()))
        provenance = art_provenance(
            asset="sprite:enemy/x",
            graphics=DEFAULT_GRAPHICS,
            pipeline_seed="s",
        )
        first = png_bytes(img, provenance)
        assert first == png_bytes(img, provenance)
        decoded = Image.open(io.BytesIO(first))
        assert decoded.text["canon:asset"] == "sprite:enemy/x"
        assert decoded.text["canon:generator"] == "canon-ai harness"
        assert decoded.text["canon:backend-model"] == ""  # no producer

    def test_empty_provenance_encodes_bare(self) -> None:
        from examples.platformer_pack.tileset_art import png_bytes

        img = Image.open(io.BytesIO(_textured_png()))
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        # Placeholder/derived writes keep their old bytes exactly.
        assert png_bytes(img) == buffer.getvalue()
        assert png_bytes(img, {}) == buffer.getvalue()


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
        # One warn per unique tile NAME — generation (and so failure) is
        # once-per-name; autotile variants share the base tile's outcome.
        names = list(dict.fromkeys(s.name for s in tileset.slots))
        assert len(warnings) == len(names)
        for name, message in zip(names, warnings):
            assert name in message
            assert "placeholder" in message

        # The fallback sheet IS the placeholder sheet — PIXEL-identical
        # to a run with no producer at all. Bytes differ by exactly the
        # readable provenance layer: the (attempted-)producer repaint
        # stamps canon:* tEXt, the code-drawn placeholder never does.
        plain = _run(tmp_path / "plain")
        broken_sheet = Image.open(tmp_path / "broken" / tileset.tilesheet_path)
        plain_sheet = Image.open(
            tmp_path / "plain" / plain.bible.tilesets[STAGE].tilesheet_path
        )
        assert (
            broken_sheet.convert("RGBA").tobytes()
            == plain_sheet.convert("RGBA").tobytes()
        )
        assert broken_sheet.text["canon:generator"] == "canon-ai harness"
        assert "canon:generator" not in getattr(plain_sheet, "text", {})


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


# --- Fake-transparency gate (first-paid-run ticket 1) ---------------------
# nano painted CHECKERBOARDS/white instead of emitting alpha: band_fg
# 89%/71% opaque in the paid tree, hazard tiles on a baked white slab.
# Synthetic twins of those failures below; the real paid PNGs were run
# through the same gate during development (89.0%→22.9%, 71.2%→24.1%,
# and the correct mossy band untouched).

from examples.platformer_pack.tileset_art import (  # noqa: E402
    ALPHA_GATE_RETRIES,
    ALPHA_RETRY_SUFFIX,
    alpha_gate,
    detect_painted_backdrop,
    key_painted_backdrop,
)

_SUBJECT_RED = (168, 40, 24)  # saturated — never in the neutral family


def _png(img: Image.Image) -> bytes:
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def _checker(w: int, h: int, cell: int = 16) -> Image.Image:
    """The literal painted 'transparency' pattern: 240/224 gray cells."""
    img = Image.new("RGB", (w, h))
    img.putdata(
        [
            ((240,) * 3 if ((x // cell) + (y // cell)) % 2 == 0 else (224,) * 3)
            for y in range(h)
            for x in range(w)
        ]
    )
    return img


def _noise(w: int, h: int) -> Image.Image:
    """Saturated three-color scene stand-in: nothing neutral, nothing
    corner-keyable — the unkeyable failure mode."""
    colors = [(200, 30, 30), (30, 200, 30), (30, 30, 200)]
    img = Image.new("RGB", (w, h))
    img.putdata([colors[(x + y) % 3] for y in range(h) for x in range(w)])
    return img


def _blob_on_white(w: int, h: int) -> Image.Image:
    """A correct generation: saturated subject on the prompted solid
    white — the corner-flood cut handles this one on its own."""
    img = Image.new("RGB", (w, h), (255, 255, 255))
    cx, cy, r = w // 2, h // 2, min(w, h) // 4
    for y in range(cy - r, cy + r):
        for x in range(cx - r, cx + r):
            if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                img.putpixel((x, y), _SUBJECT_RED)
    return img


class TestAlphaGateJudgement:
    """alpha_gate / detect / key as pure functions on synthetic twins of
    the paid failures."""

    def test_checkerboard_band_is_detected_and_keyed(self) -> None:
        # Fully-opaque band: checker everywhere, vines along the bottom
        # (the fg prompt makes subjects FRAME the edges — which is what
        # mis-aimed remove_background's corner sampling in the paid run).
        img = _checker(256, 128).convert("RGBA")
        for y in range(96, 128):
            for x in range(256):
                img.putpixel((x, y), (*_SUBJECT_RED, 255))
        sig = detect_painted_backdrop(img)
        assert sig is not None
        assert sig["mode"] == "light"
        assert sig["pattern"] == "checkerboard"
        out, ok, note = alpha_gate(img, "band_fg")
        assert ok and "auto-keyed" in note
        assert out.getpixel((128, 10))[3] == 0  # checker gone
        assert out.getpixel((128, 110))[3] == 255  # vines kept
        from examples.platformer_pack.tileset_art import _opaque_fraction

        assert 0.2 <= _opaque_fraction(out) <= 0.3

    def test_correct_alpha_band_passes_untouched(self) -> None:
        # The mossy twin: sparse saturated occluders on REAL transparency.
        img = Image.new("RGBA", (256, 128), (0, 0, 0, 0))
        for y in range(108, 128):
            for x in range(256):
                img.putpixel((x, y), (*_SUBJECT_RED, 255))
        out, ok, note = alpha_gate(img, "band_fg")
        assert ok and note == ""
        assert out is img, "in-bounds image must not be modified"

    def test_flat_white_hazard_slab_is_keyed(self) -> None:
        # The baked-white hazard tile: spikes on a solid white field that
        # survived the corner cut (fed here as the fully-opaque revert).
        img = Image.new("RGBA", (128, 128), (236, 236, 236, 255))
        for y in range(64, 128):
            for x in range(128):
                img.putpixel((x, y), (*_SUBJECT_RED, 255))
        sig = detect_painted_backdrop(img)
        assert sig == {"mode": "light", "pattern": "flat", "fraction": 0.5}
        out, ok, note = alpha_gate(img, "tile_cutout")
        assert ok and "flat" in note
        assert out.getpixel((64, 10))[3] == 0
        assert out.getpixel((64, 100))[3] == 255

    def test_enclosed_light_region_survives_keying(self) -> None:
        # Border-connectivity is the subject-protection mechanism: a
        # white-hot core INSIDE the subject is unreachable from the
        # border and must keep its pixels.
        img = Image.new("RGBA", (128, 128), (236, 236, 236, 255))
        for y in range(40, 100):
            for x in range(40, 100):
                img.putpixel((x, y), (*_SUBJECT_RED, 255))
        for y in range(60, 80):
            for x in range(60, 80):
                img.putpixel((x, y), (250, 250, 250, 255))
        out = key_painted_backdrop(img, "light")
        assert out.getpixel((5, 5))[3] == 0  # border slab keyed
        assert out.getpixel((70, 70))[3] == 255  # enclosed core kept
        assert out.getpixel((45, 45))[3] == 255  # subject kept

    def test_painted_black_field_is_keyed(self) -> None:
        img = Image.new("RGBA", (128, 128), (12, 12, 12, 255))
        for y in range(80, 128):
            for x in range(32, 96):
                img.putpixel((x, y), (*_SUBJECT_RED, 255))
        out, ok, note = alpha_gate(img, "sprite")
        assert ok and "flat" in note
        assert out.getpixel((5, 5))[3] == 0
        assert out.getpixel((64, 100))[3] == 255

    def test_white_sprite_in_bounds_is_never_keyed(self) -> None:
        # The player is #f0f0f0: a properly-cut near-white subject sits
        # inside its bounds and must come through IDENTICAL — the gate
        # never runs global white-keying on an in-bounds image.
        img = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
        for y in range(20, 120):
            for x in range(30, 100):
                img.putpixel((x, y), (240, 240, 240, 255))
        out, ok, note = alpha_gate(img, "sprite")
        assert ok and note == ""
        assert out is img

    def test_soft_trigger_keys_checkerboard_inside_bounds(self) -> None:
        # The cinder failure sat at 71% — a lenient cap must not let a
        # checkerboard slip under it. In-bounds + two-tone pattern keys.
        img = Image.new("RGBA", (256, 128), (0, 0, 0, 0))
        checker = _checker(64, 128)
        for y in range(128):
            for x in range(64):
                img.putpixel((x, y), (*checker.getpixel((x, y)), 255))
            for x in range(64, 120):
                img.putpixel((x, y), (*_SUBJECT_RED, 255))
        out, ok, note = alpha_gate(img, "band_fg")  # ~47% opaque
        assert ok and "checkerboard" in note
        assert out.getpixel((10, 64))[3] == 0
        assert out.getpixel((90, 64))[3] == 255

    def test_soft_trigger_spares_flat_pale_field(self) -> None:
        # A single pale tone inside bounds could be legitimate foam/ice
        # art — only the unambiguous checkerboard keys in the soft band.
        img = Image.new("RGBA", (256, 128), (0, 0, 0, 0))
        for y in range(128):
            for x in range(64):
                img.putpixel((x, y), (232, 232, 232, 255))
            for x in range(64, 120):
                img.putpixel((x, y), (*_SUBJECT_RED, 255))
        out, ok, note = alpha_gate(img, "band_fg")
        assert ok and note == ""
        assert out is img

    def test_eaten_subject_asks_for_retry(self) -> None:
        img = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
        img.putpixel((64, 64), (*_SUBJECT_RED, 255))
        out, ok, note = alpha_gate(img, "sprite")
        assert not ok and "ate the subject" in note

    def test_unkeyable_scene_asks_for_retry(self) -> None:
        img = _noise(128, 128).convert("RGBA")
        out, ok, note = alpha_gate(img, "sprite")
        assert not ok and "no keyable" in note


class TestAlphaGateProducer:
    """The producer wiring: key-first, paid retry last, loud on failure;
    fake twins skip the gate wholesale."""

    GRAPHICS = DEFAULT_GRAPHICS

    def test_checkerboard_tile_keys_without_burning_a_retry(self) -> None:
        # Checker backdrop + subject patches ON the corners (defeating
        # the corner-sampled cut exactly like the paid failures): one
        # generation, keyed to alpha in code, no paid retry.
        class CheckerBackend:
            model = "checker"

            def __init__(self) -> None:
                self.calls: list[str] = []

            def generate(self, prompt: str, width: int, height: int) -> bytes:
                self.calls.append(prompt)
                img = _checker(width, height)
                for y in range(height * 2 // 3, height):
                    for x in range(width):
                        img.putpixel((x, y), _SUBJECT_RED)
                # Dark patches on all four corners mis-aim the corner-
                # sampled cut (it eats only these), like the paid run.
                for cx in (0, width - 8):
                    for cy in (0, height - 8):
                        for dx in range(8):
                            for dy in range(8):
                                img.putpixel((cx + dx, cy + dy), (90, 20, 10))
                return _png(img)

        backend = CheckerBackend()
        producer = DiffusionSheetProducer(backend)
        tile = next(t for t in DEFAULT_TILES.tiles if t.category == "hazard")
        out = producer.tile_image(tile, "#cc2200", "theme", "world", self.GRAPHICS)
        assert len(backend.calls) == 1
        assert out.getpixel((self.GRAPHICS.tile_px // 2, 2))[3] == 0
        assert out.getpixel(
            (self.GRAPHICS.tile_px // 2, self.GRAPHICS.tile_px - 2)
        )[3] == 255

    def test_unkeyable_sprite_retries_with_suffix_then_recovers(self) -> None:
        class RecoveringBackend:
            model = "recovering"

            def __init__(self) -> None:
                self.calls: list[str] = []

            def generate(self, prompt: str, width: int, height: int) -> bytes:
                self.calls.append(prompt)
                if len(self.calls) == 1:
                    return _png(_noise(width, height))
                return _png(_blob_on_white(width, height))

        backend = RecoveringBackend()
        producer = DiffusionSheetProducer(backend)
        out = producer.sprite_image(
            "imp", "a small imp", "#cc2200", "theme", "world",
            self.GRAPHICS, (32, 32),
        )
        assert len(backend.calls) == 2
        assert ALPHA_RETRY_SUFFIX not in backend.calls[0]
        assert ALPHA_RETRY_SUFFIX in backend.calls[1]
        alpha = [a for _r, _g, _b, a in out.convert("RGBA").get_flattened_data()]
        frac = sum(1 for a in alpha if a > 0) / len(alpha)
        assert 0.02 <= frac <= 0.90

    def test_exhausted_budget_warns_loudly_and_ships(self, caplog) -> None:
        import logging

        class HopelessBackend:
            model = "hopeless"

            def __init__(self) -> None:
                self.calls: list[str] = []

            def generate(self, prompt: str, width: int, height: int) -> bytes:
                self.calls.append(prompt)
                return _png(_noise(width, height))

        backend = HopelessBackend()
        producer = DiffusionSheetProducer(backend)
        with caplog.at_level(logging.WARNING):
            out = producer.sprite_image(
                "imp", "a small imp", "#cc2200", "theme", "world",
                self.GRAPHICS, (32, 32),
            )
        assert len(backend.calls) == ALPHA_GATE_RETRIES + 1
        assert out is not None  # least-bad image still ships
        assert any("FAILED all" in r.message for r in caplog.records)

    def test_seed_pinned_backend_gets_no_futile_retries(self, caplog) -> None:
        import logging

        class SeededBackend:
            model = "seeded"
            capabilities = frozenset({"seeds"})
            seed = 7

            def __init__(self) -> None:
                self.calls: list[str] = []

            def generate(self, prompt: str, width: int, height: int) -> bytes:
                self.calls.append(prompt)
                return _png(_noise(width, height))

        backend = SeededBackend()
        producer = DiffusionSheetProducer(backend)
        with caplog.at_level(logging.WARNING):
            producer.sprite_image(
                "imp", "a small imp", "#cc2200", "theme", "world",
                self.GRAPHICS, (32, 32),
            )
        assert len(backend.calls) == 1, "same seed, same pixels — no retry"

    def test_fake_backend_skips_the_gate_byte_identically(self) -> None:
        assert FakeImageBackend.trusted_alpha is True
        backend = FakeImageBackend()
        producer = DiffusionSheetProducer(backend)
        producer.sprite_image(
            "imp", "a small imp", "#cc2200", "theme", "world",
            self.GRAPHICS, (32, 32),
        )
        assert len(backend.calls) == 1
        assert ALPHA_RETRY_SUFFIX not in backend.calls[0]["prompt"]
