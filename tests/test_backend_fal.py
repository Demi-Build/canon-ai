"""Tests for FalImageBackend.

Requires ``fal-client`` in the test environment. If it is not installed the
whole module is skipped via ``pytest.importorskip``.  No live API calls are
made — both ``fal_client.subscribe`` and ``urllib.request.urlopen`` are fully
mocked.

Async tests use ``asyncio.run()`` directly to match the project convention
(no pytest-asyncio in the test suite).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

fal_client = pytest.importorskip("fal_client")  # skip entire module if not installed

from canon.backends.base import ImageBackend  # noqa: E402
from canon.backends.image_fal import DEFAULT_MODEL, FalImageBackend  # noqa: E402

# ---------------------------------------------------------------------------
# Shared helpers / constants
# ---------------------------------------------------------------------------

FAKE_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64  # recognisable fake PNG header
FAKE_IMAGE_URL = "http://cdn.fal.ai/test/img.png"

FAL_RESPONSE_IMAGES = {"images": [{"url": FAKE_IMAGE_URL}]}
FAL_RESPONSE_IMAGE_DICT = {"image": {"url": FAKE_IMAGE_URL}}
FAL_RESPONSE_IMAGE_STR = {"image": FAKE_IMAGE_URL}


def _make_urlopen_mock(data: bytes = FAKE_PNG_BYTES):
    """Return a mock suitable for ``urllib.request.urlopen`` as a context manager."""
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=cm)
    cm.__exit__ = MagicMock(return_value=False)
    cm.read = MagicMock(return_value=data)
    return cm


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestFalImageBackendConstruction:
    def test_constructs_with_defaults(self) -> None:
        backend = FalImageBackend()
        assert backend.model == DEFAULT_MODEL
        assert backend.last_cost == 0.0

    def test_custom_model(self) -> None:
        backend = FalImageBackend(model="fal-ai/custom-model")
        assert backend.model == "fal-ai/custom-model"

    def test_api_key_sets_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FAL_KEY", raising=False)
        FalImageBackend(api_key="test-key-123")
        import os

        assert os.environ["FAL_KEY"] == "test-key-123"

    def test_no_api_key_no_crash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Construction should not raise even if FAL_KEY is absent."""
        monkeypatch.delenv("FAL_KEY", raising=False)
        # Should not raise
        FalImageBackend()


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


class TestFalImageBackendProtocol:
    def test_isinstance_image_backend(self) -> None:
        backend = FalImageBackend()
        assert isinstance(backend, ImageBackend)


# ---------------------------------------------------------------------------
# generate() — sync
# ---------------------------------------------------------------------------


class TestFalImageBackendGenerate:
    def test_generate_calls_subscribe_with_correct_args(self) -> None:
        backend = FalImageBackend()
        urlopen_mock = _make_urlopen_mock(FAKE_PNG_BYTES)

        with (
            patch.object(backend._fal, "subscribe", return_value=FAL_RESPONSE_IMAGES) as mock_sub,
            patch("urllib.request.urlopen", return_value=urlopen_mock),
        ):
            result = backend.generate("a red dragon", 512, 256)

        mock_sub.assert_called_once_with(
            DEFAULT_MODEL,
            arguments={
                "prompt": "a red dragon",
                "image_size": {"width": 512, "height": 256},
                # nano-banana ignores image_size; the aspect enum is its
                # only size control (2:1 request → nearest wide ratio).
                "aspect_ratio": "16:9",
            },
        )
        assert result == FAKE_PNG_BYTES

    def test_non_nano_banana_models_skip_aspect_ratio(self) -> None:
        backend = FalImageBackend(model="fal-ai/flux/dev")
        urlopen_mock = _make_urlopen_mock(FAKE_PNG_BYTES)

        with (
            patch.object(backend._fal, "subscribe", return_value=FAL_RESPONSE_IMAGES) as mock_sub,
            patch("urllib.request.urlopen", return_value=urlopen_mock),
        ):
            backend.generate("a red dragon", 512, 256)

        assert "aspect_ratio" not in mock_sub.call_args.kwargs["arguments"]

    def test_generate_alternate_image_dict_shape(self) -> None:
        backend = FalImageBackend()
        urlopen_mock = _make_urlopen_mock(FAKE_PNG_BYTES)

        with (
            patch.object(backend._fal, "subscribe", return_value=FAL_RESPONSE_IMAGE_DICT),
            patch("urllib.request.urlopen", return_value=urlopen_mock),
        ):
            result = backend.generate("a castle", 256, 256)

        assert result == FAKE_PNG_BYTES

    def test_generate_alternate_image_str_shape(self) -> None:
        backend = FalImageBackend()
        urlopen_mock = _make_urlopen_mock(FAKE_PNG_BYTES)

        with (
            patch.object(backend._fal, "subscribe", return_value=FAL_RESPONSE_IMAGE_STR),
            patch("urllib.request.urlopen", return_value=urlopen_mock),
        ):
            result = backend.generate("a forest", 128, 128)

        assert result == FAKE_PNG_BYTES


# ---------------------------------------------------------------------------
# Size-contract enforcement — models are advisory, code owns the contract
# ---------------------------------------------------------------------------


def _real_png(width: int, height: int) -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (10, 120, 40)).save(buffer, format="PNG")
    return buffer.getvalue()


class TestSizeContract:
    def test_closest_aspect_ratio_mapping(self) -> None:
        from canon.backends.image_fal import closest_aspect_ratio

        assert closest_aspect_ratio(512, 512) == "1:1"
        assert closest_aspect_ratio(512, 256) == "16:9"  # 2:1 → nearest wide
        assert closest_aspect_ratio(2100, 900) == "21:9"
        assert closest_aspect_ratio(256, 512) == "9:16"

    def test_square_return_is_cropped_to_requested_band(self) -> None:
        """The white-bar bug: a 1024x1024 return for a 512x256 band
        request must come back exactly 512x256 (center strip)."""
        import io

        from PIL import Image

        out = FalImageBackend._conform_size(_real_png(1024, 1024), 512, 256)
        assert Image.open(io.BytesIO(out)).size == (512, 256)

    def test_matching_size_passes_through_untouched(self) -> None:
        data = _real_png(512, 256)
        assert FalImageBackend._conform_size(data, 512, 256) is data

    def test_undecodable_bytes_pass_through(self) -> None:
        """Garbage payloads defer to the consumer's loud per-asset
        fallback instead of dying inside the backend."""
        assert (
            FalImageBackend._conform_size(FAKE_PNG_BYTES, 512, 256)
            is FAKE_PNG_BYTES
        )

    def test_generate_conforms_oversized_return(self) -> None:
        import io

        from PIL import Image

        backend = FalImageBackend()
        urlopen_mock = _make_urlopen_mock(_real_png(1024, 1024))
        with (
            patch.object(backend._fal, "subscribe", return_value=FAL_RESPONSE_IMAGES),
            patch("urllib.request.urlopen", return_value=urlopen_mock),
        ):
            result = backend.generate("a scenery band", 512, 256)
        assert Image.open(io.BytesIO(result)).size == (512, 256)


# ---------------------------------------------------------------------------
# _extract_image_url — URL extraction edge cases
# ---------------------------------------------------------------------------


class TestExtractImageUrl:
    def test_images_list_shape(self) -> None:
        url = FalImageBackend._extract_image_url({"images": [{"url": "http://x/a.png"}]})
        assert url == "http://x/a.png"

    def test_image_dict_shape(self) -> None:
        url = FalImageBackend._extract_image_url({"image": {"url": "http://x/b.png"}})
        assert url == "http://x/b.png"

    def test_image_str_shape(self) -> None:
        url = FalImageBackend._extract_image_url({"image": "http://x/c.png"})
        assert url == "http://x/c.png"

    def test_unexpected_shape_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unexpected fal response shape"):
            FalImageBackend._extract_image_url({"result": "something"})

    def test_empty_images_list_falls_through(self) -> None:
        """An empty images list should fall through to ValueError."""
        with pytest.raises(ValueError, match="Unexpected fal response shape"):
            FalImageBackend._extract_image_url({"images": []})


# ---------------------------------------------------------------------------
# generate_and_save()
# ---------------------------------------------------------------------------


class TestFalImageBackendGenerateAndSave:
    def test_writes_file_on_success(self, tmp_path: Path) -> None:
        backend = FalImageBackend()
        out_file = tmp_path / "portraits" / "hero.png"
        urlopen_mock = _make_urlopen_mock(FAKE_PNG_BYTES)

        with (
            patch.object(backend._fal, "subscribe", return_value=FAL_RESPONSE_IMAGES),
            patch("urllib.request.urlopen", return_value=urlopen_mock),
        ):
            result = backend.generate_and_save("a hero", str(out_file), 256, 256)

        assert result is True
        assert out_file.exists()
        assert out_file.read_bytes() == FAKE_PNG_BYTES

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        backend = FalImageBackend()
        out_file = tmp_path / "deep" / "nested" / "dir" / "img.png"
        urlopen_mock = _make_urlopen_mock(FAKE_PNG_BYTES)

        with (
            patch.object(backend._fal, "subscribe", return_value=FAL_RESPONSE_IMAGES),
            patch("urllib.request.urlopen", return_value=urlopen_mock),
        ):
            result = backend.generate_and_save("a dungeon", str(out_file), 64, 64)

        assert result is True
        assert out_file.exists()

    def test_returns_false_on_exception(self, tmp_path: Path) -> None:
        backend = FalImageBackend()
        out_file = tmp_path / "img.png"

        with patch.object(backend._fal, "subscribe", side_effect=RuntimeError("API error")):
            result = backend.generate_and_save("a scene", str(out_file), 256, 256)

        assert result is False
        assert not out_file.exists()


# ---------------------------------------------------------------------------
# generate_async() and generate_and_save_async()
# ---------------------------------------------------------------------------


class TestFalImageBackendAsync:
    def test_generate_async_calls_subscribe_async(self) -> None:
        backend = FalImageBackend()
        urlopen_mock = _make_urlopen_mock(FAKE_PNG_BYTES)

        async def _run():
            with (
                patch.object(
                    backend._fal,
                    "subscribe_async",
                    new=AsyncMock(return_value=FAL_RESPONSE_IMAGES),
                ),
                patch("urllib.request.urlopen", return_value=urlopen_mock),
            ):
                return await backend.generate_async("a mountain", 512, 512)

        result = asyncio.run(_run())
        assert result == FAKE_PNG_BYTES

    def test_generate_and_save_async_writes_file(self, tmp_path: Path) -> None:
        backend = FalImageBackend()
        out_file = tmp_path / "async_output.png"
        urlopen_mock = _make_urlopen_mock(FAKE_PNG_BYTES)

        async def _run():
            with (
                patch.object(
                    backend._fal,
                    "subscribe_async",
                    new=AsyncMock(return_value=FAL_RESPONSE_IMAGES),
                ),
                patch("urllib.request.urlopen", return_value=urlopen_mock),
            ):
                return await backend.generate_and_save_async(
                    "a valley", str(out_file), 256, 256
                )

        result = asyncio.run(_run())
        assert result is True
        assert out_file.read_bytes() == FAKE_PNG_BYTES

    def test_generate_and_save_async_returns_false_on_exception(
        self, tmp_path: Path
    ) -> None:
        backend = FalImageBackend()
        out_file = tmp_path / "img.png"

        async def _run():
            with patch.object(
                backend._fal,
                "subscribe_async",
                new=AsyncMock(side_effect=RuntimeError("async API error")),
            ):
                return await backend.generate_and_save_async("a storm", str(out_file), 256, 256)

        result = asyncio.run(_run())
        assert result is False


# ---------------------------------------------------------------------------
# register()
# ---------------------------------------------------------------------------


class TestFalImageBackendRegister:
    def test_register_adds_fal_to_registry(self) -> None:
        from canon.backends import BackendRegistry
        from canon.backends.image_fal import register

        BackendRegistry.reset()
        register()
        # Verify 'fal' is registered — BackendRegistry.image('fal') should work
        backend = BackendRegistry.image("fal")
        assert isinstance(backend, FalImageBackend)
        BackendRegistry.reset()
