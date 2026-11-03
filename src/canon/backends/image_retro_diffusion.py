"""Retro Diffusion image backend.

Pixel-art generation via the Retro Diffusion REST API
(``POST https://api.retrodiffusion.ai/v1/inferences``). Unlike fal's
nano-banana, Retro Diffusion honors exact pixel dimensions — no
aspect-ratio enum, no post-hoc size conforming needed.

Optional dependency: ``requests``. The import is deferred to ``__init__``
so this module is importable without it installed.

API shape confirmed against the official Retro-Diffusion/api-examples
repo (``llms.txt``, fetched 2026-07-17):

- Auth: ``X-RD-Token: <key>`` header on every request; keys start
  with ``rdpk-``.
- There is NO ``model`` request field — the model family (rd_fast /
  rd_plus / rd_pro) is selected by the ``prompt_style`` prefix,
  e.g. ``rd_pro__platformer``.
- Success response: ``{"created_at": ..., "balance_cost": 0.019,
  "base64_images": ["iVBORw0K..."], "model": "rd_pro",
  "remaining_balance": 100.75}``. Entries in ``base64_images`` are raw
  base64 PNG (no ``data:image/png;base64,`` prefix).
- Errors arrive in two shapes: ``{"detail": {"code": ..., "message":
  ...}}`` and ``{"detail": [{"msg": ...}]}``; statuses 400/401/403/
  422/429/500.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from pathlib import Path

from canon import pricing as _pricing

logger = logging.getLogger(__name__)

API_URL = "https://api.retrodiffusion.ai/v1/inferences"

DEFAULT_PROMPT_STYLE = "rd_pro__platformer"

#: Hard API bound is 16-512px per side, but each style enforces tighter
#: limits (rd_pro styles: 64-256px, batch <= 4). Out-of-range sizes are
#: NOT clamped here — pixel art dimensions are exact by design, so a
#: silent resize would corrupt the asset; the API's 422 is surfaced
#: loudly instead.
_REQUEST_TIMEOUT_S = 120.0


class RetroDiffusionBackend:
    """Image backend for the Retro Diffusion pixel-art API.

    Implements ``canon.backends.base.ImageBackend``. The async variants
    wrap the sync HTTP call with ``asyncio.to_thread`` (the API has no
    native async surface worth depending on).

    Args:
        prompt_style: Retro Diffusion style id. Its prefix selects the
            model family (the API has no separate ``model`` field), so
            it is folded into ``self.model`` for provenance, e.g.
            ``"retro-diffusion/rd_pro__platformer"``.
        api_key: Explicit key. If omitted, ``RD_API_KEY`` must be in the
            environment by the first ``generate`` call — construction
            never fails on a missing key, so estimate/dry-run paths can
            build the backend without credentials.
        seed: Sent with every request when set (``"seeds"`` capability);
            ``None`` lets the API roll per call.
        remove_bg: Ask the API for transparent-background output
            (``"remove_bg"`` capability).

    Note:
        ``last_cost`` starts at ``0.0`` and is updated from the
        response's ``balance_cost`` after each successful call.
    """

    capabilities: frozenset[str] = frozenset(
        {"static_sprite", "tilesets", "remove_bg", "seeds", "native_pixels"}
    )

    #: Native generation envelope (rd_pro styles accept 64-256px per
    #: dimension) — callers that honor ``native_pixels`` clamp their
    #: TRUE art-size requests into this range; the API 422s outside it.
    min_native_dim: int = 64
    max_native_dim: int = 256

    def __init__(
        self,
        prompt_style: str = DEFAULT_PROMPT_STYLE,
        api_key: str | None = None,
        seed: int | None = None,
        remove_bg: bool = False,
    ) -> None:
        try:
            import requests
        except ImportError as e:
            raise ImportError(
                "RetroDiffusionBackend requires the `requests` package. "
                "Install with: pip install canon-ai[images] "
                "(or: pip install requests)"
            ) from e
        self._requests = requests
        self.prompt_style = prompt_style
        self.model = f"retro-diffusion/{prompt_style}"
        self.seed = seed
        self.remove_bg = remove_bg
        self._api_key = api_key
        self.last_cost: float = 0.0
        #: ``balance_cost`` is provider-reported per response — measured
        #: (P0 paper P.8.8); the ``canon.pricing`` row's range is only the
        #: pre-spend ESTIMATE.
        self.last_cost_accuracy: str = _pricing.MEASURED

    def _resolve_api_key(self) -> str:
        """The key from the constructor or ``RD_API_KEY`` — checked here,
        not in ``__init__``, so a missing key fails at first generate."""
        key = self._api_key or os.environ.get("RD_API_KEY")
        if not key:
            raise RuntimeError(
                "RD_API_KEY is not set. Export it (Retro Diffusion keys "
                "start with 'rdpk-') or pass api_key= to "
                "RetroDiffusionBackend."
            )
        return key

    def _payload(self, prompt: str, width: int, height: int) -> dict:
        payload: dict = {
            "prompt": prompt,
            "prompt_style": self.prompt_style,
            "width": width,
            "height": height,
            "num_images": 1,
        }
        # Optional fields are omitted (not sent as null/false) so the
        # request stays valid if the API tightens validation on them.
        if self.remove_bg:
            payload["remove_bg"] = True
        if self.seed is not None:
            payload["seed"] = self.seed
        return payload

    def generate(self, prompt: str, width: int, height: int) -> bytes:
        """Generate one pixel-art image.

        Args:
            prompt: Subject description only — style comes from
                ``prompt_style``, and the API's own prompt expansion
                handles enrichment.
            width: Image width in pixels (style-dependent bounds; see
                module comment).
            height: Image height in pixels.

        Returns:
            PNG bytes at exactly ``width`` x ``height``.

        Raises:
            RuntimeError: Missing API key, or a non-200 API response
                (auth, validation, balance, rate limit).
            ValueError: 200 response that carried no images.
        """
        resp = self._requests.post(
            API_URL,
            json=self._payload(prompt, width, height),
            headers={"X-RD-Token": self._resolve_api_key()},
            timeout=_REQUEST_TIMEOUT_S,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Retro Diffusion API error {resp.status_code}: "
                f"{self._error_message(resp)}"
            )
        body = resp.json()
        images = body.get("base64_images") or []
        if not images:
            raise ValueError(
                f"Retro Diffusion response had no images: {list(body.keys())}"
            )
        cost = body.get("balance_cost")
        if isinstance(cost, (int, float)):
            self.last_cost = float(cost)
        logger.debug(
            "retro-diffusion generate: cost=%s remaining=%s",
            body.get("balance_cost"),
            body.get("remaining_balance"),
        )
        # base64_images entries are raw base64 PNG, no data-URI prefix.
        return base64.b64decode(images[0])

    async def generate_async(self, prompt: str, width: int, height: int) -> bytes:
        """Async variant of ``generate`` (sync HTTP offloaded to a thread)."""
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

    @staticmethod
    def _error_message(resp: object) -> str:
        """Best-effort message from either documented error shape:
        ``{"detail": {"code": ..., "message": ...}}`` or
        ``{"detail": [{"msg": ...}]}``. Falls back to raw text for
        anything else (e.g. an HTML gateway error)."""
        try:
            detail = resp.json().get("detail")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 — non-JSON body: raw text is the message
            return getattr(resp, "text", "")[:200]
        if isinstance(detail, dict):
            return str(detail.get("message", detail))
        if isinstance(detail, list) and detail:
            first = detail[0]
            if isinstance(first, dict):
                return str(first.get("msg", first))
            return str(first)
        return str(detail)


def register() -> None:
    """Register ``RetroDiffusionBackend`` with ``BackendRegistry`` as
    ``'retro-diffusion'``."""
    from canon.backends.registry import BackendRegistry

    BackendRegistry.register_image(
        "retro-diffusion", lambda: RetroDiffusionBackend()
    )
