"""Tests for ElevenLabsSFXBackend.

Requires ``elevenlabs`` in the test environment. If it is not installed
the whole module is skipped via ``pytest.importorskip``. No live API calls
are made — ``ElevenLabs`` client construction and all convert calls are
fully mocked.

Async tests use ``asyncio.run()`` directly to match the project convention
(no pytest-asyncio in the test suite).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Skip entire module if elevenlabs is not installed
pytest.importorskip("elevenlabs")

from canon.backends.base import SFXBackend  # noqa: E402
from canon.backends.sfx_elevenlabs import COST_PER_EFFECT, ElevenLabsSFXBackend  # noqa: E402

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

FAKE_SFX_BYTES = b"ID3\x03\x00\x00" + b"\xff\xfb\x90\x00" + b"\x00" * 64


def _make_convert_mock(audio_bytes: bytes = FAKE_SFX_BYTES):
    """Return a mock for client.text_to_sound_effects.convert that yields chunks."""
    # SDK returns an iterator of bytes chunks
    return MagicMock(return_value=iter([audio_bytes[:32], audio_bytes[32:]]))


def _make_backend(api_key: str = "test-key") -> ElevenLabsSFXBackend:
    """Construct an ElevenLabsSFXBackend with a mocked ElevenLabs client."""
    with patch("elevenlabs.ElevenLabs"):
        return ElevenLabsSFXBackend(api_key=api_key)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestElevenLabsSFXBackendConstruction:
    def test_constructs_with_api_key(self) -> None:
        backend = _make_backend("x")
        assert backend.last_cost == 0.0

    def test_missing_sdk_raises_import_error(self) -> None:
        """If elevenlabs is absent, ImportError with install hint."""
        import sys

        orig = sys.modules.get("elevenlabs")
        sys.modules["elevenlabs"] = None  # type: ignore[assignment]
        try:
            with pytest.raises(ImportError, match="canon-ai\\[audio\\]"):
                ElevenLabsSFXBackend(api_key="x")
        finally:
            if orig is None:
                del sys.modules["elevenlabs"]
            else:
                sys.modules["elevenlabs"] = orig


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


class TestElevenLabsSFXBackendProtocol:
    def test_isinstance_sfx_backend(self) -> None:
        backend = _make_backend()
        assert isinstance(backend, SFXBackend)


# ---------------------------------------------------------------------------
# generate() — sync
# ---------------------------------------------------------------------------


class TestElevenLabsSFXBackendGenerate:
    def test_generate_calls_convert_with_correct_args(self) -> None:
        backend = _make_backend()
        backend._client.text_to_sound_effects.convert = _make_convert_mock(FAKE_SFX_BYTES)

        result = backend.generate("door creaking", 1.5, False)

        backend._client.text_to_sound_effects.convert.assert_called_once_with(
            text="door creaking", duration_seconds=1.5
        )
        assert result == FAKE_SFX_BYTES

    def test_generate_passes_loop_flag_if_supported(self) -> None:
        """When loop=True, the kwarg is forwarded to the SDK."""
        backend = _make_backend()
        backend._client.text_to_sound_effects.convert = _make_convert_mock(FAKE_SFX_BYTES)

        backend.generate("ambient hum", 5.0, True)

        call_kwargs = backend._client.text_to_sound_effects.convert.call_args.kwargs
        assert call_kwargs.get("loop") is True

    def test_generate_reassembles_chunks(self) -> None:
        """Multiple chunks are joined into a single bytes object."""
        chunk_a = b"CHUNK_A"
        chunk_b = b"CHUNK_B"
        backend = _make_backend()
        backend._client.text_to_sound_effects.convert = MagicMock(
            return_value=iter([chunk_a, chunk_b])
        )

        result = backend.generate("wind", 2.0, False)
        assert result == chunk_a + chunk_b

    def test_last_cost_updated_after_generate(self) -> None:
        backend = _make_backend()
        backend._client.text_to_sound_effects.convert = _make_convert_mock()

        backend.generate("explosion", 1.0, False)
        assert backend.last_cost == COST_PER_EFFECT  # 0.04

    def test_loop_flag_dropped_if_sdk_rejects_it(self) -> None:
        """If the SDK raises TypeError on loop kwarg, retry without it."""
        backend = _make_backend()

        call_count = [0]

        def convert(**kwargs):
            call_count[0] += 1
            if "loop" in kwargs:
                raise TypeError("unexpected keyword argument 'loop'")
            return iter([FAKE_SFX_BYTES])

        backend._client.text_to_sound_effects.convert = convert

        result = backend.generate("rain loop", 10.0, True)
        assert result == FAKE_SFX_BYTES
        # Was called twice: once with loop (TypeError), once without
        assert call_count[0] == 2


# ---------------------------------------------------------------------------
# generate_and_save()
# ---------------------------------------------------------------------------


class TestElevenLabsSFXBackendGenerateAndSave:
    def test_writes_file_on_success(self, tmp_path: Path) -> None:
        backend = _make_backend()
        backend._client.text_to_sound_effects.convert = _make_convert_mock(FAKE_SFX_BYTES)

        out_file = tmp_path / "sfx" / "door.mp3"
        result = backend.generate_and_save("door", str(out_file), 1.5, False)

        assert result is True
        assert out_file.exists()
        assert out_file.read_bytes() == FAKE_SFX_BYTES

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        backend = _make_backend()
        backend._client.text_to_sound_effects.convert = _make_convert_mock()

        out_file = tmp_path / "a" / "b" / "sfx.mp3"
        result = backend.generate_and_save("wind", str(out_file), 2.0, False)

        assert result is True
        assert out_file.exists()

    def test_returns_false_on_exception(self, tmp_path: Path) -> None:
        backend = _make_backend()
        backend._client.text_to_sound_effects.convert = MagicMock(
            side_effect=RuntimeError("API error")
        )

        out_file = tmp_path / "sfx.mp3"
        result = backend.generate_and_save("thunder", str(out_file), 1.0, False)

        assert result is False
        assert not out_file.exists()


# ---------------------------------------------------------------------------
# generate_async() and generate_and_save_async()
# ---------------------------------------------------------------------------


class TestElevenLabsSFXBackendAsync:
    def test_generate_async_produces_bytes(self) -> None:
        backend = _make_backend()
        backend._client.text_to_sound_effects.convert = _make_convert_mock(FAKE_SFX_BYTES)

        async def _run():
            return await backend.generate_async("click", 0.5, False)

        result = asyncio.run(_run())
        assert result == FAKE_SFX_BYTES

    def test_generate_and_save_async_writes_file(self, tmp_path: Path) -> None:
        backend = _make_backend()
        backend._client.text_to_sound_effects.convert = _make_convert_mock(FAKE_SFX_BYTES)

        out_file = tmp_path / "async_sfx.mp3"

        async def _run():
            return await backend.generate_and_save_async("whoosh", str(out_file), 1.0, False)

        result = asyncio.run(_run())

        assert result is True
        assert out_file.read_bytes() == FAKE_SFX_BYTES

    def test_generate_and_save_async_returns_false_on_exception(
        self, tmp_path: Path
    ) -> None:
        backend = _make_backend()
        backend._client.text_to_sound_effects.convert = MagicMock(
            side_effect=RuntimeError("fail")
        )

        out_file = tmp_path / "sfx.mp3"

        async def _run():
            return await backend.generate_and_save_async("boom", str(out_file), 1.0, False)

        result = asyncio.run(_run())
        assert result is False


# ---------------------------------------------------------------------------
# register()
# ---------------------------------------------------------------------------


class TestElevenLabsSFXBackendRegister:
    def test_register_adds_elevenlabs_to_registry(self) -> None:
        from canon.backends import BackendRegistry
        from canon.backends.sfx_elevenlabs import register

        BackendRegistry.reset()
        try:
            with patch("elevenlabs.ElevenLabs"):
                register()
            backend = BackendRegistry.sfx("elevenlabs")
            assert isinstance(backend, ElevenLabsSFXBackend)
        finally:
            BackendRegistry.reset()
