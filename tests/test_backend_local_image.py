"""Tests for LocalImageBackend.

Requires ``diffusers`` (and transitively ``torch``) in the test environment.
If not installed the whole module is skipped via ``pytest.importorskip``.
No actual model weights are loaded — the heavy diffusion pipeline is fully
mocked so these tests are fast and do not require a GPU.

Async tests use ``asyncio.run()`` directly to match the project convention
(no pytest-asyncio in the test suite).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Skip the entire module if diffusers (or its torch dependency) is absent.
pytest.importorskip("diffusers")

from canon.backends.base import ImageBackend  # noqa: E402
from canon.backends.image_local import LocalImageBackend  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Minimal valid 1×1 PNG bytes reused from testing.py's _MIN_PNG_BYTES.
_FAKE_PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a"
    "0000000d49484452"
    "00000001"
    "00000001"
    "08060000001f15c489"
    "0000000a49444154"
    "789c6260000000020001"
    "e221bc33"
    "0000000049454e44ae426082"
)


def _make_mock_pipe(png_bytes: bytes = _FAKE_PNG_BYTES) -> MagicMock:
    """Return a mock diffusion pipeline that returns a stub PIL-like image."""
    fake_image = MagicMock()
    # Pre-fill the buffer so that fake_image.save writes our bytes.
    fake_image.save = lambda buf, format: buf.write(png_bytes)  # type: ignore[assignment]

    pipe_result = MagicMock()
    pipe_result.images = [fake_image]

    pipe_callable = MagicMock(return_value=pipe_result)
    return pipe_callable


def _make_backend_with_mocked_pipe(png_bytes: bytes = _FAKE_PNG_BYTES) -> LocalImageBackend:
    """Construct a LocalImageBackend and inject a mock pipe, bypassing _ensure_pipe."""
    backend = LocalImageBackend(device="cpu")
    backend._pipe = _make_mock_pipe(png_bytes)
    return backend


# ---------------------------------------------------------------------------
# Construction — lazy; no GPU required
# ---------------------------------------------------------------------------


class TestLocalImageBackendConstruction:
    def test_constructs_without_loading_pipe(self) -> None:
        """Pipeline must not be loaded at construction time."""
        backend = LocalImageBackend(device="cpu")
        assert backend._pipe is None

    def test_last_cost_is_zero(self) -> None:
        backend = LocalImageBackend(device="cpu")
        assert backend.last_cost == 0.0

    def test_prefers_serial_is_true(self) -> None:
        assert LocalImageBackend.prefers_serial is True

    def test_explicit_model_stored(self) -> None:
        backend = LocalImageBackend(model="stabilityai/stable-diffusion-xl-base-1.0", device="cpu")
        assert backend.model == "stabilityai/stable-diffusion-xl-base-1.0"

    def test_explicit_device_stored(self) -> None:
        backend = LocalImageBackend(device="cpu")
        assert backend.device == "cpu"


# ---------------------------------------------------------------------------
# _auto_device
# ---------------------------------------------------------------------------


class TestAutoDevice:
    def test_returns_cuda_when_available(self) -> None:
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True
        assert LocalImageBackend._auto_device(mock_torch) == "cuda"

    def test_returns_mps_when_cuda_unavailable(self) -> None:
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        mock_torch.backends.mps.is_available.return_value = True
        assert LocalImageBackend._auto_device(mock_torch) == "mps"

    def test_returns_cpu_when_neither_available(self) -> None:
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        mock_torch.backends.mps.is_available.return_value = False
        assert LocalImageBackend._auto_device(mock_torch) == "cpu"


# ---------------------------------------------------------------------------
# _default_model_for_device
# ---------------------------------------------------------------------------


class TestDefaultModelForDevice:
    def test_cuda_returns_flux(self) -> None:
        assert "FLUX" in LocalImageBackend._default_model_for_device("cuda")

    def test_cpu_returns_sdxl_turbo(self) -> None:
        assert "sdxl-turbo" in LocalImageBackend._default_model_for_device("cpu")

    def test_mps_returns_sdxl_turbo(self) -> None:
        assert "sdxl-turbo" in LocalImageBackend._default_model_for_device("mps")


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


class TestLocalImageBackendProtocol:
    def test_isinstance_image_backend(self) -> None:
        backend = LocalImageBackend(device="cpu")
        assert isinstance(backend, ImageBackend)


# ---------------------------------------------------------------------------
# generate() — sync, with mocked pipe
# ---------------------------------------------------------------------------


class TestLocalImageBackendGenerate:
    def test_generate_returns_png_bytes(self) -> None:
        backend = _make_backend_with_mocked_pipe()
        result = backend.generate("a red dragon", 256, 256)
        # Verify it starts with the PNG signature
        assert result[:8] == bytes.fromhex("89504e470d0a1a0a")

    def test_generate_triggers_pipe_call(self) -> None:
        backend = _make_backend_with_mocked_pipe()
        backend.generate("a castle", 512, 256)
        backend._pipe.assert_called_once_with(
            prompt="a castle",
            width=512,
            height=256,
            num_inference_steps=4,
        )

    def test_ensure_pipe_loads_pipeline_lazily(self) -> None:
        """_ensure_pipe should instantiate self._pipe when it is None."""
        backend = LocalImageBackend(device="cpu")
        assert backend._pipe is None

        mock_pipe_instance = _make_mock_pipe()

        with patch("canon.backends.image_local.LocalImageBackend._ensure_pipe") as mock_ensure:
            # Replace _ensure_pipe with a side-effect that injects a mock pipe
            def _inject_pipe():
                backend._pipe = mock_pipe_instance

            mock_ensure.side_effect = _inject_pipe
            backend.generate("a forest", 128, 128)
            mock_ensure.assert_called_once()

    def test_generate_raises_on_pipe_failure(self) -> None:
        backend = LocalImageBackend(device="cpu")
        broken_pipe = MagicMock(side_effect=RuntimeError("CUDA OOM"))
        backend._pipe = broken_pipe

        with pytest.raises(RuntimeError, match="CUDA OOM"):
            backend.generate("a scene", 256, 256)


# ---------------------------------------------------------------------------
# generate_and_save()
# ---------------------------------------------------------------------------


class TestLocalImageBackendGenerateAndSave:
    def test_writes_file_on_success(self, tmp_path: Path) -> None:
        backend = _make_backend_with_mocked_pipe()
        out_file = tmp_path / "portraits" / "hero.png"
        result = backend.generate_and_save("a hero", str(out_file), 256, 256)

        assert result is True
        assert out_file.exists()
        data = out_file.read_bytes()
        assert data[:8] == bytes.fromhex("89504e470d0a1a0a")

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        backend = _make_backend_with_mocked_pipe()
        out_file = tmp_path / "a" / "b" / "c" / "img.png"
        result = backend.generate_and_save("a forest", str(out_file), 64, 64)

        assert result is True
        assert out_file.exists()

    def test_returns_false_on_exception(self, tmp_path: Path) -> None:
        backend = LocalImageBackend(device="cpu")
        broken_pipe = MagicMock(side_effect=RuntimeError("OOM"))
        backend._pipe = broken_pipe
        out_file = tmp_path / "img.png"

        result = backend.generate_and_save("a scene", str(out_file), 256, 256)

        assert result is False
        assert not out_file.exists()


# ---------------------------------------------------------------------------
# generate_async() and generate_and_save_async()
# ---------------------------------------------------------------------------


class TestLocalImageBackendAsync:
    def test_generate_async_returns_bytes(self) -> None:
        backend = _make_backend_with_mocked_pipe()
        result = asyncio.run(backend.generate_async("a mountain", 256, 256))
        assert result[:8] == bytes.fromhex("89504e470d0a1a0a")

    def test_generate_and_save_async_writes_file(self, tmp_path: Path) -> None:
        backend = _make_backend_with_mocked_pipe()
        out_file = tmp_path / "async_img.png"
        result = asyncio.run(backend.generate_and_save_async("a valley", str(out_file), 256, 256))

        assert result is True
        assert out_file.exists()

    def test_generate_and_save_async_returns_false_on_exception(
        self, tmp_path: Path
    ) -> None:
        backend = LocalImageBackend(device="cpu")
        broken_pipe = MagicMock(side_effect=RuntimeError("GPU error"))
        backend._pipe = broken_pipe
        out_file = tmp_path / "img.png"

        result = asyncio.run(backend.generate_and_save_async("a storm", str(out_file), 256, 256))

        assert result is False


# ---------------------------------------------------------------------------
# register()
# ---------------------------------------------------------------------------


class TestLocalImageBackendRegister:
    def test_register_adds_local_to_registry(self) -> None:
        from canon.backends import BackendRegistry
        from canon.backends.image_local import register

        BackendRegistry.reset()
        register()
        backend = BackendRegistry.image("local")
        assert isinstance(backend, LocalImageBackend)
        BackendRegistry.reset()
