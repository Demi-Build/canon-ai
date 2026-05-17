"""Fal.ai image backend.

Optional dependency: install with ``pip install canon-ai[images]``.
The SDK import is deferred to ``__init__`` so this module is importable
without fal-client installed.

Downstream code that needs the real ``ImportError`` should import directly::

    from canon.backends.image_fal import FalImageBackend

Code that only needs to check availability can use the lazy re-export from
``canon.backends`` which returns ``None`` when fal-client is absent.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass  # no top-level fal_client import

# TODO(v0.2.x): track per-call cost when fal exposes it; today self.last_cost stays 0

DEFAULT_MODEL = "fal-ai/nano-banana"


class FalImageBackend:
    """Image backend using fal.ai's hosted models.

    Implements ``canon.backends.base.ImageBackend``. Async path goes through
    ``fal_client.subscribe_async`` (real async); sync path uses
    ``fal_client.subscribe``.

    Args:
        model: fal model ID. Defaults to ``"fal-ai/nano-banana"``.
        api_key: If provided, sets ``FAL_KEY`` env var. If omitted, the
            env var must already be set before the first ``generate`` call.

    Note:
        ``last_cost`` is always ``0.0``; fal does not expose per-call cost in
        its response payload. Will be updated when fal adds that field.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
    ) -> None:
        try:
            import fal_client
        except ImportError as e:
            raise ImportError(
                "FalImageBackend requires the `fal-client` package. "
                "Install with: pip install canon-ai[images]"
            ) from e
        self._fal = fal_client
        self.model = model
        if api_key:
            os.environ["FAL_KEY"] = api_key
        elif "FAL_KEY" not in os.environ:
            # Don't fail at construction; only at first generate call
            pass
        self.last_cost: float = 0.0

    def generate(self, prompt: str, width: int, height: int) -> bytes:
        """Sync image generation.

        Args:
            prompt: Text prompt describing the image.
            width: Image width in pixels.
            height: Image height in pixels.

        Returns:
            Raw image bytes (typically PNG or JPEG depending on model).
        """
        result = self._fal.subscribe(
            self.model,
            arguments={"prompt": prompt, "image_size": {"width": width, "height": height}},
        )
        url = self._extract_image_url(result)
        return self._download_url(url)

    async def generate_async(self, prompt: str, width: int, height: int) -> bytes:
        """Async image generation via ``fal_client.subscribe_async``.

        Args:
            prompt: Text prompt describing the image.
            width: Image width in pixels.
            height: Image height in pixels.

        Returns:
            Raw image bytes.
        """
        result = await self._fal.subscribe_async(
            self.model,
            arguments={"prompt": prompt, "image_size": {"width": width, "height": height}},
        )
        url = self._extract_image_url(result)
        # NOTE: download is sync via urllib; could be async with httpx in v0.3
        return self._download_url(url)

    def generate_and_save(
        self, prompt: str, filepath: str, width: int, height: int
    ) -> bool:
        """Generate an image and write it to ``filepath``.

        Creates parent directories as needed. Returns ``True`` on success,
        ``False`` on any exception (network error, fal API error, etc.).
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

    @staticmethod
    def _extract_image_url(result: dict) -> str:
        """Extract image URL from fal response.

        fal returns ``{"images": [{"url": "..."}]}`` for most models, but
        some models use ``{"image": {"url": "..."}}`` or ``{"image": "<url>"}``.

        Raises:
            ValueError: If the response shape is unrecognised.
        """
        if "images" in result and result["images"]:
            return result["images"][0].get("url", "")
        if "image" in result:
            img = result["image"]
            return img.get("url", "") if isinstance(img, dict) else img
        raise ValueError(f"Unexpected fal response shape: {list(result.keys())}")

    @staticmethod
    def _download_url(url: str) -> bytes:
        """Download bytes from ``url`` using the stdlib (no extra deps)."""
        import urllib.request

        with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310
            return resp.read()


def register() -> None:
    """Register ``FalImageBackend`` with ``BackendRegistry`` as ``'fal'``."""
    from canon.backends.registry import BackendRegistry

    BackendRegistry.register_image("fal", lambda: FalImageBackend())
