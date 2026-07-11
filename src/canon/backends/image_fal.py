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

import asyncio
import logging
import math
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass  # no top-level fal_client import

# TODO(v0.2.x): track per-call cost when fal exposes it; today self.last_cost stays 0

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "fal-ai/nano-banana"

#: nano-banana's img2img (edit) endpoint — takes ``image_urls`` + ``prompt``
#: and returns the same ``{"images": [{"url": ...}]}`` shape as generation.
#: Confirmed working against a real sprite-sheet spike (2026-07-10).
DEFAULT_EDIT_MODEL = "fal-ai/nano-banana/edit"

#: nano-banana takes an ``aspect_ratio`` enum and IGNORES ``image_size``
#: (unknown arguments pass silently) — discovered on the first real
#: backdrop run: a 512x256 band request came back 1024x1024, a square
#: scene whose white misty bottom stretched into a bar behind every
#: playfield. Values from the published input schema.
NANO_BANANA_RATIOS: dict[str, float] = {
    "21:9": 21 / 9,
    "16:9": 16 / 9,
    "3:2": 3 / 2,
    "4:3": 4 / 3,
    "5:4": 5 / 4,
    "1:1": 1.0,
    "4:5": 4 / 5,
    "3:4": 3 / 4,
    "2:3": 2 / 3,
    "9:16": 9 / 16,
}


def closest_aspect_ratio(width: int, height: int) -> str:
    """The supported enum value nearest the requested aspect (log-scale,
    so 2:1 compares fairly against both 16:9 and 21:9)."""
    target = math.log(width / height)
    return min(
        NANO_BANANA_RATIOS,
        key=lambda name: abs(math.log(NANO_BANANA_RATIOS[name]) - target),
    )


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
        edit_model: str = DEFAULT_EDIT_MODEL,
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
        self.edit_model = edit_model
        if api_key:
            os.environ["FAL_KEY"] = api_key
        elif "FAL_KEY" not in os.environ:
            # Don't fail at construction; only at first generate call
            pass
        self.last_cost: float = 0.0

    def _arguments(self, prompt: str, width: int, height: int) -> dict:
        """Request arguments per model dialect: ``image_size`` for models
        that honor it; nano-banana additionally gets the closest
        ``aspect_ratio`` enum value (its only size control — it silently
        ignores ``image_size``)."""
        arguments: dict = {
            "prompt": prompt,
            "image_size": {"width": width, "height": height},
        }
        if "nano-banana" in self.model:
            arguments["aspect_ratio"] = closest_aspect_ratio(width, height)
        return arguments

    def generate(self, prompt: str, width: int, height: int) -> bytes:
        """Sync image generation.

        Args:
            prompt: Text prompt describing the image.
            width: Image width in pixels.
            height: Image height in pixels.

        Returns:
            Raw image bytes, conformed to exactly ``width`` x ``height``
            (models are advisory about size; the contract is enforced here
            in code — see ``_conform_size``).
        """
        result = self._fal.subscribe(
            self.model,
            arguments=self._arguments(prompt, width, height),
        )
        url = self._extract_image_url(result)
        return self._conform_size(self._download_url(url), width, height)

    async def generate_async(self, prompt: str, width: int, height: int) -> bytes:
        """Async image generation via ``fal_client.subscribe_async``.

        Args:
            prompt: Text prompt describing the image.
            width: Image width in pixels.
            height: Image height in pixels.

        Returns:
            Raw image bytes, conformed to exactly ``width`` x ``height``.
        """
        result = await self._fal.subscribe_async(
            self.model,
            arguments=self._arguments(prompt, width, height),
        )
        url = self._extract_image_url(result)
        # NOTE: download is sync via urllib; could be async with httpx in v0.3
        return self._conform_size(self._download_url(url), width, height)

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

    # -- img2img (implements ``ImageEditBackend``) --------------------------

    def edit(self, image_bytes: bytes, prompt: str, width: int, height: int) -> bytes:
        """Sync img2img: upload ``image_bytes``, edit it per ``prompt`` via
        the ``edit_model`` endpoint, return bytes conformed to
        ``width`` x ``height`` (same size contract as ``generate``).

        Args:
            image_bytes: PNG-encoded source image to edit.
            prompt: Text instructions describing the desired edit.
            width: Output width in pixels.
            height: Output height in pixels.
        """
        image_url = self._fal.upload(image_bytes, "image/png")
        result = self._fal.subscribe(
            self.edit_model,
            arguments={"prompt": prompt, "image_urls": [image_url]},
        )
        url = self._extract_image_url(result)
        return self._conform_size(self._download_url(url), width, height)

    async def edit_async(
        self, image_bytes: bytes, prompt: str, width: int, height: int
    ) -> bytes:
        """Async img2img via ``fal_client.subscribe_async``. The upload is
        sync in fal-client, so it is offloaded with ``asyncio.to_thread``."""
        image_url = await asyncio.to_thread(self._fal.upload, image_bytes, "image/png")
        result = await self._fal.subscribe_async(
            self.edit_model,
            arguments={"prompt": prompt, "image_urls": [image_url]},
        )
        url = self._extract_image_url(result)
        return self._conform_size(self._download_url(url), width, height)

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
    def _conform_size(data: bytes, width: int, height: int) -> bytes:
        """Enforce the ``ImageBackend`` size contract in code: models treat
        the request as advisory (nano-banana returns ~1MP at an enum
        aspect), so any mismatch is center-cropped to the requested aspect
        and resized to exactly ``width`` x ``height``. Undecodable bytes
        pass through untouched — the consumer's per-asset fallback owns
        that failure, with its existing loud warning."""
        import io

        try:
            from PIL import Image
        except ImportError:  # pragma: no cover — pack installs Pillow
            return data
        try:
            img = Image.open(io.BytesIO(data))
            img.load()
        except Exception:  # noqa: BLE001 — non-image payload: pass through
            return data
        if img.size == (width, height):
            return data
        src_w, src_h = img.size
        scale = min(src_w / width, src_h / height)
        crop_w, crop_h = round(width * scale), round(height * scale)
        left = (src_w - crop_w) // 2
        top = (src_h - crop_h) // 2
        img = img.crop((left, top, left + crop_w, top + crop_h)).resize(
            (width, height), Image.LANCZOS
        )
        logger.info(
            "fal image conformed from %dx%d to requested %dx%d "
            "(center-crop + resize).", src_w, src_h, width, height,
        )
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        return buffer.getvalue()

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
