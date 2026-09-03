"""PixelLab pixel-art image backend.

Optional dependency: ``requests`` (install with ``pip install requests``).
The import is deferred to ``__init__`` so this module is importable without
requests installed.

Downstream code that needs the real ``ImportError`` should import directly::

    from canon.backends.image_pixellab import PixelLabBackend

API shape verified against the live OpenAPI spec at
https://api.pixellab.ai/v1/openapi.json (fetched 2026-07-17):

- ``POST /generate-image-pixflux`` — ``description`` (str, required),
  ``image_size`` ``{"width": int, "height": int}`` (required, 16-400 px per
  dimension), ``no_background`` (bool), ``seed`` (int), plus optional style
  knobs (``negative_description``, ``text_guidance_scale``, ``outline``,
  ``shading``, ``detail``).
- ``POST /generate-image-bitforge`` — same core shape plus ``style_image``
  conditioning; area capped at 200x200.
- Auth: ``Authorization: Bearer <token>``.
- Response: ``{"image": {"type": "base64", "base64": ...},
  "usage": {"type": "usd", "usd": ...}}``.
- Native animation (``/animate-with-skeleton``, ``/animate-with-text``) and
  rotation endpoints exist — reflected in ``capabilities`` even though the
  v1 pipeline only calls ``generate()``.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from pathlib import Path

from canon import pricing as _pricing

logger = logging.getLogger(__name__)

API_BASE_URL = "https://api.pixellab.ai/v1"

DEFAULT_MODEL = "pixflux"

#: Both generation endpoints share the request/response shape; bitforge adds
#: style-image conditioning and caps AREA at 200x200 (pixflux caps each
#: dimension at 400 instead).
GENERATE_ENDPOINTS: dict[str, str] = {
    "pixflux": "/generate-image-pixflux",
    "bitforge": "/generate-image-bitforge",
}

#: Per-dimension bounds enforced by the API (422 otherwise) — checked here in
#: code so the failure names the constraint instead of surfacing a raw 422.
MIN_DIM = 16
MAX_DIM = 400
BITFORGE_MAX_AREA = 200 * 200

#: The graceful-degradation contract: consumers gate optional pipeline stages
#: on membership here, and only ``static_sprite`` (``generate()``) is called
#: in v1. ``animation_sheets``/``tilesets`` map to PixelLab's native skeleton
#: animation and tileset support; ``remove_bg`` to ``no_background``;
#: ``seeds`` to the ``seed`` request field.
CAPABILITIES: frozenset[str] = frozenset(
    {"static_sprite", "animation_sheets", "tilesets", "remove_bg", "seeds", "native_pixels"}
)


class PixelLabBackend:
    """Image backend for the PixelLab REST API (pixel-art generation).

    Implements ``canon.backends.base.ImageBackend``. PixelLab generates at
    the exact requested pixel size (it is a pixel-art model, not a ~1MP
    diffusion endpoint), so no conform/crop pass is needed on the response.

    Args:
        model: ``"pixflux"`` (default, text-to-image) or ``"bitforge"``
            (style-conditioned; smaller max area).
        api_key: If provided, sets the ``PIXELLAB_SECRET`` env var. If
            omitted, ``PIXELLAB_SECRET`` (or the dashboard's own name for
            it, ``PIXELLAB_API_KEY``) must be set before the first
            ``generate`` call — construction never fails on a missing key.
        seed: Optional deterministic seed sent with every request (the
            ``seeds`` capability). ``None`` lets the API roll its own.
        timeout: Per-request timeout in seconds.

    Note:
        ``last_cost`` starts at ``0.0`` and is updated after each call from
        the response's ``usage.usd`` field.
    """

    capabilities: frozenset[str] = CAPABILITIES

    #: Native generation envelope (px per dimension) — callers that honor
    #: ``native_pixels`` request TRUE art sizes clamped into this range
    #: instead of diffusion-scale canvases the API would reject.
    min_native_dim: int = MIN_DIM
    max_native_dim: int = MAX_DIM

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        seed: int | None = None,
        timeout: float = 120.0,
    ) -> None:
        try:
            import requests
        except ImportError as e:
            raise ImportError(
                "PixelLabBackend requires the `requests` package. "
                "Install with: pip install requests"
            ) from e
        self._requests = requests
        if model not in GENERATE_ENDPOINTS:
            raise ValueError(
                f"Unknown PixelLab model {model!r}; "
                f"expected one of {sorted(GENERATE_ENDPOINTS)}"
            )
        self.model = model
        self.seed = seed
        self.timeout = timeout
        if api_key:
            os.environ["PIXELLAB_SECRET"] = api_key
        # Missing key is NOT an error here — fail at first generate call.
        self.last_cost: float = 0.0
        #: ``usage.usd`` is provider-reported per response — measured
        #: (P0 paper P.8.8); the ``canon.pricing`` row's range is only the
        #: pre-spend ESTIMATE.
        self.last_cost_accuracy: str = _pricing.MEASURED

    # -- request plumbing ----------------------------------------------------

    def _token(self) -> str:
        # PIXELLAB_API_KEY is the dashboard's name for the same token —
        # accepted so a pasted .env line works without renaming.
        token = os.environ.get("PIXELLAB_SECRET", "") or os.environ.get(
            "PIXELLAB_API_KEY", ""
        )
        if not token:
            raise RuntimeError(
                "Neither PIXELLAB_SECRET nor PIXELLAB_API_KEY is set. "
                "Get an API token from https://www.pixellab.ai and export it "
                "(or pass api_key= to PixelLabBackend)."
            )
        return token

    def _validate_size(self, width: int, height: int) -> None:
        if not (MIN_DIM <= width <= MAX_DIM and MIN_DIM <= height <= MAX_DIM):
            raise ValueError(
                f"PixelLab image_size must be {MIN_DIM}-{MAX_DIM}px per "
                f"dimension; got {width}x{height}"
            )
        if self.model == "bitforge" and width * height > BITFORGE_MAX_AREA:
            raise ValueError(
                f"bitforge caps area at 200x200 ({BITFORGE_MAX_AREA}px^2); "
                f"got {width}x{height} ({width * height}px^2)"
            )

    def _payload(self, prompt: str, width: int, height: int) -> dict:
        payload: dict = {
            "description": prompt,
            "image_size": {"width": width, "height": height},
            # Sprites/tiles need alpha; PixelLab bakes background removal
            # into generation rather than as a separate pass.
            "no_background": True,
        }
        if self.seed is not None:
            payload["seed"] = self.seed
        return payload

    def _post(self, payload: dict) -> dict:
        url = API_BASE_URL + GENERATE_ENDPOINTS[self.model]
        resp = self._requests.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {self._token()}"},
            timeout=self.timeout,
        )
        if not resp.ok:
            # Include the body: PixelLab 422s carry the offending field name.
            raise RuntimeError(
                f"PixelLab API error {resp.status_code} at {url}: "
                f"{resp.text[:500]}"
            )
        return resp.json()

    @staticmethod
    def _decode_image(data: dict) -> bytes:
        image = data.get("image")
        if not isinstance(image, dict) or "base64" not in image:
            raise ValueError(
                f"Unexpected PixelLab response shape: {sorted(data)}"
            )
        b64 = image["base64"]
        # Defensive: tolerate a data-URI prefix even though the spec sends
        # the bare base64 string.
        if b64.startswith("data:"):
            b64 = b64.split(",", 1)[-1]
        return base64.b64decode(b64)

    # -- ImageBackend surface ------------------------------------------------

    def generate(self, prompt: str, width: int, height: int) -> bytes:
        """Sync pixel-art generation.

        Args:
            prompt: Text prompt (PixelLab's ``description`` field).
            width: Image width in pixels (16-400).
            height: Image height in pixels (16-400).

        Returns:
            PNG bytes at exactly ``width`` x ``height`` — PixelLab generates
            at the requested size, so the size contract holds without a
            conform pass.
        """
        self._validate_size(width, height)
        data = self._post(self._payload(prompt, width, height))
        usage = data.get("usage", {})
        self.last_cost = float(usage.get("usd", 0.0))
        return self._decode_image(data)

    async def generate_async(self, prompt: str, width: int, height: int) -> bytes:
        """Async variant of ``generate``. PixelLab has no async API, so the
        sync call is offloaded with ``asyncio.to_thread``."""
        return await asyncio.to_thread(self.generate, prompt, width, height)

    def generate_and_save(
        self, prompt: str, filepath: str, width: int, height: int
    ) -> bool:
        """Generate an image and write it to ``filepath``.

        Creates parent directories as needed. Returns ``True`` on success,
        ``False`` on any exception (network error, API error, etc.).
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
    """Register ``PixelLabBackend`` with ``BackendRegistry`` as ``'pixellab'``."""
    from canon.backends.registry import BackendRegistry

    BackendRegistry.register_image("pixellab", lambda: PixelLabBackend())
