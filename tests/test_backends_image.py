"""Tests for ImageBackend protocol, FakeImageBackend, and registry image methods."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from canon.backends import (
    BackendRegistry,
    FakeImageBackend,
    ImageBackend,
    ImageEditBackend,
)

# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


class TestImageBackendProtocol:
    def test_fake_satisfies_protocol(self) -> None:
        fake = FakeImageBackend()
        assert isinstance(fake, ImageBackend)

    def test_fake_has_all_four_methods(self) -> None:
        fake = FakeImageBackend()
        assert callable(getattr(fake, "generate", None))
        assert callable(getattr(fake, "generate_async", None))
        assert callable(getattr(fake, "generate_and_save", None))
        assert callable(getattr(fake, "generate_and_save_async", None))

    def test_object_missing_methods_does_not_satisfy_protocol(self) -> None:
        class NotAnImageBackend:
            pass

        assert not isinstance(NotAnImageBackend(), ImageBackend)

    def test_partial_implementation_does_not_satisfy_protocol(self) -> None:
        class PartialBackend:
            def generate(self, prompt: str, width: int, height: int) -> bytes:
                return b""

        # Missing generate_async, generate_and_save, generate_and_save_async
        assert not isinstance(PartialBackend(), ImageBackend)


# ---------------------------------------------------------------------------
# ImageEditBackend — the SEPARATE img2img capability protocol
# ---------------------------------------------------------------------------


class TestImageEditBackendProtocol:
    """edit() lives on its own protocol, NOT on ImageBackend — so adding it
    never forces a text-to-image-only backend to fail isinstance(ImageBackend).
    A backend that supports editing satisfies BOTH protocols structurally."""

    def test_fake_satisfies_edit_protocol(self) -> None:
        assert isinstance(FakeImageBackend(), ImageEditBackend)

    def test_fake_still_satisfies_base_protocol(self) -> None:
        # The two protocols are independent; the fake satisfies both.
        fake = FakeImageBackend()
        assert isinstance(fake, ImageBackend)
        assert isinstance(fake, ImageEditBackend)

    def test_generate_only_backend_is_not_an_edit_backend(self) -> None:
        class GenerateOnly:
            def generate(self, prompt: str, width: int, height: int) -> bytes:
                return b""

            async def generate_async(self, prompt: str, width: int, height: int) -> bytes:
                return b""

            def generate_and_save(
                self, prompt: str, filepath: str, width: int, height: int
            ) -> bool:
                return True

            async def generate_and_save_async(
                self, prompt: str, filepath: str, width: int, height: int
            ) -> bool:
                return True

        backend = GenerateOnly()
        # A full text-to-image backend is still an ImageBackend...
        assert isinstance(backend, ImageBackend)
        # ...but NOT an edit backend — this is the whole point of the split
        # (LocalImageBackend has no img2img; consumers gate on this).
        assert not isinstance(backend, ImageEditBackend)


class TestFakeImageBackendEdit:
    def test_edit_returns_requested_size_decodable_sheet(self) -> None:
        from io import BytesIO

        from PIL import Image

        fake = FakeImageBackend()
        data = fake.edit(b"source-png-bytes", "a 4-frame walk cycle", 2048, 512)
        img = Image.open(BytesIO(data))
        img.load()  # must fully decode, not just open
        assert img.size == (2048, 512)

    def test_edit_records_call(self) -> None:
        fake = FakeImageBackend()
        fake.edit(b"abc", "walk", 800, 200)
        assert fake.calls[-1] == {
            "op": "edit",
            "prompt": "walk",
            "width": 800,
            "height": 200,
            "image_bytes": 3,
        }

    def test_edit_is_deterministic_across_instances(self) -> None:
        # Byte-identity bar: the canned sheet depends only on (width, height),
        # so two fake runs are byte-identical regardless of the source image.
        a = FakeImageBackend().edit(b"x", "one", 1024, 256)
        b = FakeImageBackend().edit(b"different", "two", 1024, 256)
        assert a == b

    def test_edit_sheet_is_frame_segmentable(self) -> None:
        """The canned sheet is evenly-spaced opaque blobs on white, so a
        content-aware slicer (the animation phase's) recovers exactly
        ``_EDIT_SHEET_FRAMES`` frames — proving the $0 path is real."""
        from io import BytesIO

        from PIL import Image

        data = FakeImageBackend().edit(b"x", "walk", 2048, 512)
        img = Image.open(BytesIO(data)).convert("RGB")
        w, h = img.size
        px = list(img.get_flattened_data())
        # "ink" column = has any clearly non-white pixel
        ink = [
            any(sum(px[y * w + x]) < 720 for y in range(h))
            for x in range(w)
        ]
        runs = sum(1 for i, v in enumerate(ink) if v and not (i and ink[i - 1]))
        assert runs == FakeImageBackend._EDIT_SHEET_FRAMES

    def test_edit_async_matches_sync(self) -> None:
        fake = FakeImageBackend()
        sync = fake.edit(b"x", "walk", 512, 128)
        got = asyncio.run(fake.edit_async(b"x", "walk", 512, 128))
        assert got == sync


# ---------------------------------------------------------------------------
# FakeImageBackend — generate
# ---------------------------------------------------------------------------


class TestFakeImageBackendGenerate:
    def test_generate_returns_bytes(self) -> None:
        fake = FakeImageBackend()
        result = fake.generate("a red dragon", 256, 256)
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_generate_returns_fully_decodable_png(self) -> None:
        """Full DECODE, not just open: PIL's lazy Image.open() accepted the
        old truncated-IDAT placeholder, then every real consumer blew up
        at convert()/load() time with 'broken data stream'."""
        import io

        from PIL import Image

        raw = FakeImageBackend().generate("anything", 512, 512)
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        assert img.size == (1, 1)

    def test_generate_records_call(self) -> None:
        fake = FakeImageBackend()
        fake.generate("a red dragon", 512, 512)
        assert len(fake.calls) == 1
        call = fake.calls[0]
        assert call["prompt"] == "a red dragon"
        assert call["width"] == 512
        assert call["height"] == 512

    def test_calls_starts_empty(self) -> None:
        fake = FakeImageBackend()
        assert fake.calls == []

    def test_multiple_generates_accumulate_calls(self) -> None:
        fake = FakeImageBackend()
        fake.generate("prompt one", 256, 256)
        fake.generate("prompt two", 512, 512)
        assert len(fake.calls) == 2
        assert fake.calls[0]["prompt"] == "prompt one"
        assert fake.calls[1]["prompt"] == "prompt two"

    def test_different_prompts_return_same_bytes(self) -> None:
        """Placeholder is deterministic regardless of prompt."""
        fake = FakeImageBackend()
        b1 = fake.generate("castle", 256, 256)
        b2 = fake.generate("dragon", 512, 256)
        assert b1 == b2


# ---------------------------------------------------------------------------
# FakeImageBackend — generate_and_save
# ---------------------------------------------------------------------------


class TestFakeImageBackendGenerateAndSave:
    def test_generate_and_save_creates_file(self, tmp_path: Path) -> None:
        fake = FakeImageBackend()
        filepath = str(tmp_path / "output.png")
        result = fake.generate_and_save("a blue sky", filepath, 256, 256)
        assert result is True
        assert Path(filepath).exists()

    def test_generate_and_save_writes_non_empty_bytes(self, tmp_path: Path) -> None:
        fake = FakeImageBackend()
        filepath = str(tmp_path / "output.png")
        fake.generate_and_save("a blue sky", filepath, 256, 256)
        assert Path(filepath).stat().st_size > 0

    def test_generate_and_save_records_call(self, tmp_path: Path) -> None:
        fake = FakeImageBackend()
        filepath = str(tmp_path / "output.png")
        fake.generate_and_save("a blue sky", filepath, 128, 128)
        assert len(fake.calls) == 1
        call = fake.calls[0]
        assert call["prompt"] == "a blue sky"
        assert call["filepath"] == filepath
        assert call["width"] == 128
        assert call["height"] == 128

    def test_generate_and_save_creates_parent_dirs(self, tmp_path: Path) -> None:
        fake = FakeImageBackend()
        nested = tmp_path / "deep" / "nested" / "dir" / "output.png"
        fake.generate_and_save("forest", str(nested), 256, 256)
        assert nested.exists()

    def test_generate_and_save_bytes_match_generate(self, tmp_path: Path) -> None:
        """File content should equal what generate() returns."""
        fake = FakeImageBackend()
        filepath = str(tmp_path / "output.png")
        expected = fake.generate("test", 256, 256)
        # reset calls to keep them clean for this assertion
        fake.calls.clear()
        fake.generate_and_save("test", filepath, 256, 256)
        written = Path(filepath).read_bytes()
        assert written == expected


# ---------------------------------------------------------------------------
# FakeImageBackend — async variants
# ---------------------------------------------------------------------------


class TestFakeImageBackendAsync:
    def test_generate_async_returns_bytes(self) -> None:
        fake = FakeImageBackend()
        result = asyncio.run(fake.generate_async("a sunset", 256, 256))
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_generate_async_same_bytes_as_sync(self) -> None:
        fake = FakeImageBackend()
        sync_result = fake.generate("a sunset", 256, 256)
        fake.calls.clear()
        async_result = asyncio.run(fake.generate_async("a sunset", 256, 256))
        assert sync_result == async_result

    def test_generate_and_save_async_creates_file(self, tmp_path: Path) -> None:
        fake = FakeImageBackend()
        filepath = str(tmp_path / "async_output.png")
        result = asyncio.run(
            fake.generate_and_save_async("a mountain", filepath, 256, 256)
        )
        assert result is True
        assert Path(filepath).exists()
        assert Path(filepath).stat().st_size > 0

    def test_generate_and_save_async_creates_parent_dirs(self, tmp_path: Path) -> None:
        fake = FakeImageBackend()
        nested = tmp_path / "async" / "nested" / "output.png"
        asyncio.run(
            fake.generate_and_save_async("volcano", str(nested), 256, 256)
        )
        assert nested.exists()


# ---------------------------------------------------------------------------
# FakeImageBackend — custom placeholder
# ---------------------------------------------------------------------------


class TestFakeImageBackendPlaceholder:
    def test_uses_placeholder_file_when_provided(self, tmp_path: Path) -> None:
        # Write a fake PNG file to use as placeholder
        placeholder = tmp_path / "custom.png"
        placeholder.write_bytes(b"FAKEPNGDATA")
        fake = FakeImageBackend(placeholder=placeholder)
        result = fake.generate("anything", 256, 256)
        assert result == b"FAKEPNGDATA"

    def test_uses_asset_dir_png_when_provided(self, tmp_path: Path) -> None:
        asset_dir = tmp_path / "assets"
        asset_dir.mkdir()
        (asset_dir / "test.png").write_bytes(b"DIRPNGDATA")
        fake = FakeImageBackend(asset_dir=asset_dir)
        result = fake.generate("anything", 256, 256)
        assert result == b"DIRPNGDATA"

    def test_placeholder_takes_precedence_over_asset_dir(self, tmp_path: Path) -> None:
        asset_dir = tmp_path / "assets"
        asset_dir.mkdir()
        (asset_dir / "test.png").write_bytes(b"DIRPNG")
        placeholder = tmp_path / "custom.png"
        placeholder.write_bytes(b"PLACEHOLDERPNG")
        fake = FakeImageBackend(asset_dir=asset_dir, placeholder=placeholder)
        result = fake.generate("anything", 256, 256)
        assert result == b"PLACEHOLDERPNG"

    def test_falls_back_to_min_png_when_no_assets(self) -> None:
        from canon.backends.testing import _MIN_PNG_BYTES

        fake = FakeImageBackend()
        result = fake.generate("anything", 256, 256)
        assert result == _MIN_PNG_BYTES
        # Verify it looks like a PNG (starts with PNG magic)
        assert result[:4] == b"\x89PNG"


# ---------------------------------------------------------------------------
# Registry — image
# ---------------------------------------------------------------------------


class TestRegistryImage:
    def setup_method(self) -> None:
        BackendRegistry.reset()

    def teardown_method(self) -> None:
        BackendRegistry.reset()

    def test_register_and_retrieve(self) -> None:
        BackendRegistry.register_image("fake", lambda: FakeImageBackend())
        backend = BackendRegistry.image("fake")
        assert isinstance(backend, FakeImageBackend)

    def test_instance_is_cached(self) -> None:
        BackendRegistry.register_image("fake", lambda: FakeImageBackend())
        first = BackendRegistry.image("fake")
        second = BackendRegistry.image("fake")
        assert first is second

    def test_unknown_name_raises_key_error(self) -> None:
        with pytest.raises(KeyError, match="unknown_img"):
            BackendRegistry.image("unknown_img")

    def test_key_error_message_is_descriptive(self) -> None:
        with pytest.raises(KeyError) as exc_info:
            BackendRegistry.image("mybackend")
        assert "mybackend" in str(exc_info.value)

    def test_re_register_clears_cached_instance(self) -> None:
        BackendRegistry.register_image("fake", lambda: FakeImageBackend())
        first = BackendRegistry.image("fake")
        BackendRegistry.register_image("fake", lambda: FakeImageBackend())
        second = BackendRegistry.image("fake")
        assert first is not second

    def test_reset_clears_image_state(self) -> None:
        BackendRegistry.register_image("fake", lambda: FakeImageBackend())
        _ = BackendRegistry.image("fake")
        BackendRegistry.reset()
        with pytest.raises(KeyError):
            BackendRegistry.image("fake")

    def test_reset_clears_all_four_backend_types(self) -> None:
        from canon.backends import FakeMusicBackend, FakeSFXBackend

        BackendRegistry.register_image("img", lambda: FakeImageBackend())
        BackendRegistry.register_music("mus", lambda: FakeMusicBackend())
        BackendRegistry.register_sfx("sfx", lambda: FakeSFXBackend())
        BackendRegistry.register_llm("llm", lambda: FakeLLMBackendForReset())
        BackendRegistry.reset()
        with pytest.raises(KeyError):
            BackendRegistry.image("img")
        with pytest.raises(KeyError):
            BackendRegistry.music("mus")
        with pytest.raises(KeyError):
            BackendRegistry.sfx("sfx")

    def test_factory_called_once(self) -> None:
        call_count = [0]

        def factory() -> FakeImageBackend:
            call_count[0] += 1
            return FakeImageBackend()

        BackendRegistry.register_image("counted", factory)
        BackendRegistry.image("counted")
        BackendRegistry.image("counted")
        assert call_count[0] == 1


# ---------------------------------------------------------------------------
# Helper for reset test
# ---------------------------------------------------------------------------


class FakeLLMBackendForReset:
    """Minimal LLM backend used only to verify reset() clears LLM state too."""

    def generate(self, request: object) -> str:
        return "ok"
