"""Unified image-backend interface (graphics arc G2): capability
declarations + graceful derivation, the grid-snap pixel-discipline
post-process, and producer wiring for the new backends."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from canon.backends.base import IMAGE_CAPABILITIES, backend_capabilities
from canon.backends.testing import FakeImageBackend
from examples.platformer_pack.graphics import GraphicsSpec
from examples.platformer_pack.tileset_art import (
    DiffusionSheetProducer,
    build_image_producer,
    grid_snap,
)


class TestCapabilities:
    def test_derived_defaults(self) -> None:
        caps = backend_capabilities(FakeImageBackend())
        assert "static_sprite" in caps
        # FakeImageBackend implements edit() (ImageEditBackend) — derived.
        assert "animation_sheets" in caps
        assert "native_pixels" not in caps

    def test_declared_set_wins_and_is_vocabulary_bounded(self) -> None:
        backend = FakeImageBackend(
            capabilities={"static_sprite", "native_pixels", "not_a_cap"}
        )
        caps = backend_capabilities(backend)
        assert "native_pixels" in caps
        assert "not_a_cap" not in caps
        assert caps <= IMAGE_CAPABILITIES

    def test_static_sprite_is_always_present(self) -> None:
        backend = FakeImageBackend(capabilities={"seeds"})
        assert "static_sprite" in backend_capabilities(backend)


class TestGridSnap:
    def test_alpha_binarizes(self) -> None:
        img = Image.new("RGBA", (4, 4), (200, 40, 40, 255))
        img.putpixel((0, 0), (200, 40, 40, 90))  # fringe half-pixel
        img.putpixel((1, 0), (200, 40, 40, 180))
        out = grid_snap(img)
        assert out.getpixel((0, 0))[3] == 0
        assert out.getpixel((1, 0))[3] == 255

    def test_palette_membership_quantization(self) -> None:
        img = Image.new("RGBA", (2, 1), (250, 10, 10, 255))
        img.putpixel((1, 0), (10, 10, 250, 255))
        out = grid_snap(img, palette=["#ff0000", "#0000ff"])
        assert out.getpixel((0, 0))[:3] == (255, 0, 0)
        assert out.getpixel((1, 0))[:3] == (0, 0, 255)

    def test_transparent_pixels_left_alone(self) -> None:
        img = Image.new("RGBA", (1, 1), (77, 77, 77, 0))
        out = grid_snap(img, palette=["#ffffff"])
        assert out.getpixel((0, 0))[3] == 0

    def test_native_pixels_backend_skips_snap(self) -> None:
        """The producer's crisp-lane snap gates on the capability."""
        native = DiffusionSheetProducer(
            FakeImageBackend(capabilities={"static_sprite", "native_pixels"})
        )
        general = DiffusionSheetProducer(FakeImageBackend())
        crisp = GraphicsSpec()
        smooth = GraphicsSpec(render_filter="smooth", posterize_levels=None)
        img = Image.new("RGBA", (4, 4), (10, 200, 10, 90))  # fringe alpha
        assert native._maybe_grid_snap(img, crisp) is img
        assert general._maybe_grid_snap(img, smooth) is img
        snapped = general._maybe_grid_snap(img, crisp)
        assert snapped is not img
        assert snapped.getpixel((0, 0))[3] == 0  # binarized


class TestProducerWiring:
    def test_new_backend_names_fail_fast_without_keys(
        self, monkeypatch
    ) -> None:
        monkeypatch.delenv("RD_API_KEY", raising=False)
        monkeypatch.delenv("PIXELLAB_SECRET", raising=False)
        monkeypatch.delenv("PIXELLAB_API_KEY", raising=False)
        with pytest.raises(ValueError, match="RD_API_KEY"):
            build_image_producer("retro")
        with pytest.raises(ValueError, match="PIXELLAB_SECRET"):
            build_image_producer("pixellab")

    def test_request_dims_native_vs_diffusion(self) -> None:
        """native_pixels backends are asked for TRUE art sizes clamped
        into their declared envelope; general backends keep the gen
        canvas. (The paid repro: PixelLab 422'd on a 512px gen_px
        request whose real target was a 64px player sprite.)"""
        from canon.backends.testing import FakeImageBackend
        from examples.platformer_pack.tileset_art import (
            DiffusionSheetProducer,
        )

        general = DiffusionSheetProducer(FakeImageBackend())
        assert general._request_dims(512, 512, 64, 64) == (512, 512)

        native = DiffusionSheetProducer(
            FakeImageBackend(capabilities={"native_pixels"})
        )
        native.backend.min_native_dim = 16
        native.backend.max_native_dim = 400
        # True target passes through; over-cap scales aspect-preserving.
        assert native._request_dims(512, 512, 64, 64) == (64, 64)
        assert native._request_dims(512, 512, 800, 800) == (400, 400)
        assert native._request_dims(512, 256) == (400, 200)  # band 2:1
        assert native._request_dims(512, 512, 8, 8) == (16, 16)  # floor

    def test_pixellab_accepts_dashboard_key_name(self, monkeypatch) -> None:
        # PIXELLAB_API_KEY is what the dashboard calls the token — a
        # pasted .env line must work without renaming to PIXELLAB_SECRET.
        monkeypatch.delenv("PIXELLAB_SECRET", raising=False)
        monkeypatch.setenv("PIXELLAB_API_KEY", "pl-test-token")
        producer = build_image_producer("pixellab")
        assert producer.backend._token() == "pl-test-token"

    def test_unknown_backend_names_the_full_menu(self) -> None:
        with pytest.raises(ValueError, match="retro, pixellab"):
            build_image_producer("dalle")

    def test_edit_backend_defaults_to_the_sprite_backend(self) -> None:
        # Ticket 7: the animation img2img backend is its own slot, defaulting
        # to the sprite backend — every pre-split call sees no change.
        fake = FakeImageBackend()
        assert DiffusionSheetProducer(fake).edit_backend is fake
        other = FakeImageBackend()
        split = DiffusionSheetProducer(fake, other)
        assert split.backend is fake and split.edit_backend is other

    def test_build_image_producer_edit_kind_wiring(self, monkeypatch) -> None:
        # Same kind (or none) → the sprite backend is reused for edits; a
        # DIFFERENT edit kind builds its own backend (the pixellab-sheets +
        # nano-animator split, exercised here at $0 — retro's constructor is
        # inert, no network at build time).
        same = build_image_producer("fake", edit_kind="fake")
        assert same.edit_backend is same.backend
        none = build_image_producer("fake", edit_kind=None)
        assert none.edit_backend is none.backend
        monkeypatch.setenv("RD_API_KEY", "rd-test-token")
        split = build_image_producer("fake", edit_kind="retro")
        assert split.edit_backend is not split.backend
        assert type(split.edit_backend).__name__ == "RetroDiffusionBackend"

    def test_fake_edit_ignores_references_byte_identically(self) -> None:
        # Ticket 7: `references` steers real backends; the fake RECORDS the
        # count but its canned sheet must stay byte-identical (determinism).
        fake = FakeImageBackend()
        plain = fake.edit(b"src", "walk sheet", 256, 64)
        with_refs = fake.edit(
            b"src", "walk sheet", 256, 64, references=[b"base", b"stock"]
        )
        assert plain == with_refs
        assert [c["references"] for c in fake.calls] == [0, 2]

    def test_fake_sheet_pipeline_survives_grid_snap(self, tmp_path) -> None:
        """The $0 tile pipeline end-to-end with the snap in place —
        deterministic bytes, twice."""
        from examples.platformer_pack.tiles import DEFAULT_TILES

        producer = DiffusionSheetProducer(FakeImageBackend())
        tile = DEFAULT_TILES.by_name["floor"]
        a = producer.tile_image(tile, "#6e5a4e", "caves", "w", GraphicsSpec())
        b = producer.tile_image(tile, "#6e5a4e", "caves", "w", GraphicsSpec())
        assert np.array_equal(np.array(a), np.array(b))
        assert a.size == (32, 32)
