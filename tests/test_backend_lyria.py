"""Tests for LyriaMusicBackend.

Requires ``google-genai`` in the test environment. If it is not installed
the whole module is skipped via ``pytest.importorskip``. No live API calls
are made — both ``genai.Client`` and its ``models.generate_content`` are
fully mocked.

Async tests use ``asyncio.run()`` directly to match the project convention
(no pytest-asyncio in the test suite).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Skip entire module if google-genai is not installed
pytest.importorskip("google.genai")

from canon.backends.base import MusicBackend  # noqa: E402
from canon.backends.music_lyria import (  # noqa: E402
    DEFAULT_MODEL_CLIP,
    DEFAULT_MODEL_PRO,
    PRICING,
    LyriaMusicBackend,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

FAKE_AUDIO_BYTES = b"ID3" + b"\x00" * 64  # recognisable stub


def _make_genai_response(audio_bytes: bytes = FAKE_AUDIO_BYTES):
    """Build a minimal mock mimicking the genai response shape.

    response.candidates[0].content.parts[0].inline_data.data
    with mime_type = "audio/mp3"
    """
    inline_data = MagicMock()
    inline_data.mime_type = "audio/mp3"
    inline_data.data = audio_bytes

    part = MagicMock()
    part.inline_data = inline_data

    content = MagicMock()
    content.parts = [part]

    candidate = MagicMock()
    candidate.content = content

    response = MagicMock()
    response.candidates = [candidate]
    return response


def _make_backend(api_key: str = "test-key") -> LyriaMusicBackend:
    """Construct a LyriaMusicBackend with a mocked genai.Client."""
    with patch("google.genai.Client"):
        return LyriaMusicBackend(api_key=api_key)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestLyriaMusicBackendConstruction:
    def test_constructs_with_api_key(self) -> None:
        backend = _make_backend("x")
        assert backend.model_pro == DEFAULT_MODEL_PRO
        assert backend.model_clip == DEFAULT_MODEL_CLIP
        assert backend.last_cost == 0.0

    def test_custom_models(self) -> None:
        with patch("google.genai.Client"):
            backend = LyriaMusicBackend(
                api_key="k",
                model_pro="lyria-custom-pro",
                model_clip="lyria-custom-clip",
            )
        assert backend.model_pro == "lyria-custom-pro"
        assert backend.model_clip == "lyria-custom-clip"

    def test_missing_sdk_raises_import_error(self) -> None:
        """If google-genai is absent, ImportError with install hint."""
        import sys

        orig = sys.modules.get("google.genai")
        sys.modules["google.genai"] = None  # type: ignore[assignment]
        try:
            with pytest.raises(ImportError, match="canon-ai\\[audio\\]"):
                LyriaMusicBackend(api_key="x")
        finally:
            if orig is None:
                del sys.modules["google.genai"]
            else:
                sys.modules["google.genai"] = orig


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


class TestLyriaMusicBackendProtocol:
    def test_isinstance_music_backend(self) -> None:
        backend = _make_backend()
        assert isinstance(backend, MusicBackend)


# ---------------------------------------------------------------------------
# _select_model
# ---------------------------------------------------------------------------


class TestSelectModel:
    def test_short_duration_uses_clip(self) -> None:
        backend = _make_backend()
        assert backend._select_model(30) == DEFAULT_MODEL_CLIP

    def test_long_duration_uses_pro(self) -> None:
        backend = _make_backend()
        assert backend._select_model(120) == DEFAULT_MODEL_PRO

    def test_exactly_60_uses_pro(self) -> None:
        backend = _make_backend()
        assert backend._select_model(60) == DEFAULT_MODEL_PRO

    def test_59_uses_clip(self) -> None:
        backend = _make_backend()
        assert backend._select_model(59) == DEFAULT_MODEL_CLIP


# ---------------------------------------------------------------------------
# _extract_audio
# ---------------------------------------------------------------------------


class TestExtractAudio:
    def test_extracts_audio_from_valid_response(self) -> None:
        response = _make_genai_response(FAKE_AUDIO_BYTES)
        assert LyriaMusicBackend._extract_audio(response) == FAKE_AUDIO_BYTES

    def test_skips_non_audio_parts(self) -> None:
        """Parts with non-audio mime_type should be skipped."""
        text_part = MagicMock()
        text_part.inline_data = MagicMock()
        text_part.inline_data.mime_type = "text/plain"
        text_part.inline_data.data = b"lyrics"

        audio_part = MagicMock()
        audio_part.inline_data = MagicMock()
        audio_part.inline_data.mime_type = "audio/wav"
        audio_part.inline_data.data = FAKE_AUDIO_BYTES

        content = MagicMock()
        content.parts = [text_part, audio_part]
        candidate = MagicMock()
        candidate.content = content
        response = MagicMock()
        response.candidates = [candidate]

        assert LyriaMusicBackend._extract_audio(response) == FAKE_AUDIO_BYTES

    def test_no_audio_part_raises_value_error(self) -> None:
        response = MagicMock()
        response.candidates = []
        with pytest.raises(ValueError, match="No audio part found"):
            LyriaMusicBackend._extract_audio(response)

    def test_part_without_inline_data_is_skipped(self) -> None:
        """Parts where inline_data is None should not crash."""
        part = MagicMock()
        part.inline_data = None

        content = MagicMock()
        content.parts = [part]
        candidate = MagicMock()
        candidate.content = content
        response = MagicMock()
        response.candidates = [candidate]

        with pytest.raises(ValueError, match="No audio part found"):
            LyriaMusicBackend._extract_audio(response)


# ---------------------------------------------------------------------------
# generate() — sync
# ---------------------------------------------------------------------------


class TestLyriaMusicBackendGenerate:
    def test_generate_short_track_uses_clip_model(self) -> None:
        backend = _make_backend()
        response = _make_genai_response(FAKE_AUDIO_BYTES)

        backend._client.models.generate_content.return_value = response

        result = backend.generate("upbeat dungeon music", 30)

        backend._client.models.generate_content.assert_called_once_with(
            model=DEFAULT_MODEL_CLIP,
            contents="upbeat dungeon music",
            config={"response_modalities": ["AUDIO", "TEXT"]},
        )
        assert result == FAKE_AUDIO_BYTES

    def test_generate_long_track_uses_pro_model(self) -> None:
        backend = _make_backend()
        response = _make_genai_response(FAKE_AUDIO_BYTES)
        backend._client.models.generate_content.return_value = response

        result = backend.generate("epic boss battle", 90)

        call_kwargs = backend._client.models.generate_content.call_args
        assert call_kwargs.kwargs["model"] == DEFAULT_MODEL_PRO
        assert result == FAKE_AUDIO_BYTES

    def test_last_cost_updated_clip(self) -> None:
        backend = _make_backend()
        backend._client.models.generate_content.return_value = _make_genai_response()

        backend.generate("calm", 30)
        assert backend.last_cost == PRICING[DEFAULT_MODEL_CLIP]  # 0.04

    def test_last_cost_updated_pro(self) -> None:
        backend = _make_backend()
        backend._client.models.generate_content.return_value = _make_genai_response()

        backend.generate("epic", 120)
        assert backend.last_cost == PRICING[DEFAULT_MODEL_PRO]  # 0.08


# ---------------------------------------------------------------------------
# generate_and_save()
# ---------------------------------------------------------------------------


class TestLyriaMusicBackendGenerateAndSave:
    def test_writes_file_on_success(self, tmp_path: Path) -> None:
        backend = _make_backend()
        backend._client.models.generate_content.return_value = _make_genai_response(FAKE_AUDIO_BYTES)

        out_file = tmp_path / "tracks" / "dungeon.mp3"
        result = backend.generate_and_save("dark ambient", str(out_file), 30)

        assert result is True
        assert out_file.exists()
        assert out_file.read_bytes() == FAKE_AUDIO_BYTES

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        backend = _make_backend()
        backend._client.models.generate_content.return_value = _make_genai_response()

        out_file = tmp_path / "a" / "b" / "c" / "theme.mp3"
        result = backend.generate_and_save("theme", str(out_file), 45)

        assert result is True
        assert out_file.exists()

    def test_returns_false_on_exception(self, tmp_path: Path) -> None:
        backend = _make_backend()
        backend._client.models.generate_content.side_effect = RuntimeError("API error")

        out_file = tmp_path / "track.mp3"
        result = backend.generate_and_save("music", str(out_file), 30)

        assert result is False
        assert not out_file.exists()


# ---------------------------------------------------------------------------
# generate_async() and generate_and_save_async()
# ---------------------------------------------------------------------------


class TestLyriaMusicBackendAsync:
    def test_generate_async_via_aio(self) -> None:
        """If client has aio attr, use it."""
        backend = _make_backend()
        response = _make_genai_response(FAKE_AUDIO_BYTES)

        # Set up aio mock
        aio_mock = MagicMock()
        aio_mock.models.generate_content = AsyncMock(return_value=response)
        backend._client.aio = aio_mock

        async def _run():
            return await backend.generate_async("calm ambient", 30)

        result = asyncio.run(_run())

        aio_mock.models.generate_content.assert_called_once()
        assert result == FAKE_AUDIO_BYTES

    def test_generate_async_fallback_to_thread(self) -> None:
        """If client has no aio, fall back to asyncio.to_thread."""
        backend = _make_backend()
        response = _make_genai_response(FAKE_AUDIO_BYTES)
        backend._client.models.generate_content.return_value = response
        # Ensure no aio attr so fallback path is taken
        backend._client.aio = None  # hasattr returns True for None; use spec instead

        # Make hasattr(client, 'aio') return False by deleting the attribute
        # We patched Client so we can use a fresh MagicMock without aio
        with patch("google.genai.Client"):
            fresh_backend = LyriaMusicBackend(api_key="k")
        # Fresh MagicMock client will have aio as a magic attribute; patch hasattr
        fresh_backend._client = MagicMock(spec=[
            "models",  # only expose models, not aio
        ])
        fresh_backend._client.models.generate_content.return_value = response

        async def _run():
            return await fresh_backend.generate_async("calm ambient", 30)

        result = asyncio.run(_run())
        assert result == FAKE_AUDIO_BYTES

    def test_generate_and_save_async_writes_file(self, tmp_path: Path) -> None:
        backend = _make_backend()
        response = _make_genai_response(FAKE_AUDIO_BYTES)
        backend._client = MagicMock(spec=["models"])
        backend._client.models.generate_content.return_value = response

        out_file = tmp_path / "async_track.mp3"

        async def _run():
            return await backend.generate_and_save_async("upbeat", str(out_file), 45)

        result = asyncio.run(_run())

        assert result is True
        assert out_file.read_bytes() == FAKE_AUDIO_BYTES

    def test_generate_and_save_async_returns_false_on_exception(
        self, tmp_path: Path
    ) -> None:
        backend = _make_backend()
        backend._client = MagicMock(spec=["models"])
        backend._client.models.generate_content.side_effect = RuntimeError("fail")

        out_file = tmp_path / "track.mp3"

        async def _run():
            return await backend.generate_and_save_async("music", str(out_file), 30)

        result = asyncio.run(_run())
        assert result is False


# ---------------------------------------------------------------------------
# register()
# ---------------------------------------------------------------------------


class TestLyriaMusicBackendRegister:
    def test_register_adds_lyria_to_registry(self) -> None:
        from canon.backends import BackendRegistry
        from canon.backends.music_lyria import register

        BackendRegistry.reset()
        try:
            with patch("google.genai.Client"):
                register()
            backend = BackendRegistry.music("lyria")
            assert isinstance(backend, LyriaMusicBackend)
        finally:
            BackendRegistry.reset()
