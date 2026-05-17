"""Local diffusion image backend (FLUX / SDXL via Hugging Face diffusers).

Optional dependency: install with ``pip install canon-ai[images-local]``.
The pipeline is instantiated lazily (first ``generate`` call) so construction
is cheap and tests can mock it without loading multi-GB model weights.

Downstream code that needs the real ``ImportError`` should import directly::

    from canon.backends.image_local import LocalImageBackend

Code that only needs to check availability can use the lazy re-export from
``canon.backends`` which returns ``None`` when diffusers/torch are absent.
"""

from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


class LocalImageBackend:
    """Image backend using local diffusion models.

    Defaults to FLUX.1-schnell on CUDA, SDXL Turbo on MPS/CPU.
    The diffusion pipeline is loaded lazily on the first ``generate`` call —
    construction is fast and safe to use in tests with a mocked pipe.

    ``prefers_serial = True`` signals to ``AssetPhase`` that this backend
    should not be parallelised within a single run — a GPU can only run one
    inference at a time and concurrent calls would serialise or OOM anyway.

    Args:
        model: HuggingFace model ID. Defaults to ``"black-forest-labs/FLUX.1-schnell"``
            on CUDA and ``"stabilityai/sdxl-turbo"`` on MPS/CPU.
        device: ``"cuda"``, ``"mps"``, or ``"cpu"``. Auto-detected if omitted.
    """

    prefers_serial: bool = True

    def __init__(
        self,
        model: str | None = None,
        device: str | None = None,
    ) -> None:
        try:
            import diffusers  # noqa: F401
            import torch
        except ImportError as e:
            raise ImportError(
                "LocalImageBackend requires `diffusers` and `torch`. "
                "Install with: pip install canon-ai[images-local]"
            ) from e
        self._torch = torch
        self.device = device or self._auto_device(torch)
        self.model = model or self._default_model_for_device(self.device)
        self._pipe = None  # lazy — set in _ensure_pipe()
        self.last_cost: float = 0.0

    @staticmethod
    def _auto_device(torch) -> str:  # noqa: ANN001
        """Return the best available torch device."""
        if torch.cuda.is_available():
            return "cuda"
        if hasattr(torch, "backends") and torch.backends.mps.is_available():
            return "mps"
        return "cpu"

    @staticmethod
    def _default_model_for_device(device: str) -> str:
        """Return the recommended model for the given device."""
        return (
            "black-forest-labs/FLUX.1-schnell"
            if device == "cuda"
            else "stabilityai/sdxl-turbo"
        )

    def _ensure_pipe(self) -> None:
        """Lazily load the diffusion pipeline on first call."""
        if self._pipe is not None:
            return
        from diffusers import AutoPipelineForText2Image

        torch_dtype = (
            self._torch.float16 if self.device == "cuda" else self._torch.float32
        )
        self._pipe = AutoPipelineForText2Image.from_pretrained(
            self.model, torch_dtype=torch_dtype
        ).to(self.device)

    def generate(self, prompt: str, width: int, height: int) -> bytes:
        """Run local diffusion and return PNG bytes.

        Args:
            prompt: Text prompt describing the image.
            width: Output image width in pixels.
            height: Output image height in pixels.

        Returns:
            PNG-encoded image bytes.
        """
        self._ensure_pipe()
        result = self._pipe(
            prompt=prompt,
            width=width,
            height=height,
            num_inference_steps=4,
        ).images[0]
        buf = BytesIO()
        result.save(buf, format="PNG")
        return buf.getvalue()

    async def generate_async(self, prompt: str, width: int, height: int) -> bytes:
        """Async variant: wraps sync ``generate`` via ``asyncio.to_thread``.

        Local diffusion is CPU/GPU-bound and not natively async. Wrapping in
        ``to_thread`` keeps the ``ImageBackend`` async contract without blocking
        the event loop.
        """
        return await asyncio.to_thread(self.generate, prompt, width, height)

    def generate_and_save(
        self, prompt: str, filepath: str, width: int, height: int
    ) -> bool:
        """Generate and write PNG to ``filepath``.

        Returns ``True`` on success, ``False`` on any exception.
        """
        try:
            data = self.generate(prompt, width, height)
            Path(filepath).parent.mkdir(parents=True, exist_ok=True)
            Path(filepath).write_bytes(data)
            return True
        except Exception:
            return False

    async def generate_and_save_async(
        self, prompt: str, filepath: str, width: int, height: int
    ) -> bool:
        """Async variant of ``generate_and_save``."""
        try:
            data = await self.generate_async(prompt, width, height)
            Path(filepath).parent.mkdir(parents=True, exist_ok=True)
            Path(filepath).write_bytes(data)
            return True
        except Exception:
            return False


def register() -> None:
    """Register ``LocalImageBackend`` with ``BackendRegistry`` as ``'local'``."""
    from canon.backends.registry import BackendRegistry

    BackendRegistry.register_image("local", lambda: LocalImageBackend())
