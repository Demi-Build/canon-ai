"""Google Lyria music backend.

Optional dependency: install with ``pip install canon-ai[audio]``.
Uses google-genai SDK with response_modalities=["AUDIO", "TEXT"].

Downstream code that needs the real ``ImportError`` should import directly::

    from canon.backends.music_lyria import LyriaMusicBackend

Code that only needs to check availability can use the lazy re-export from
``canon.backends`` which returns ``None`` when google-genai is absent.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass  # no top-level google.genai import

# TODO(v0.2.x): handle response shape variations across genai SDK versions

from canon import pricing as _pricing

logger = logging.getLogger(__name__)

DEFAULT_MODEL_PRO = "lyria-3-pro-preview"
DEFAULT_MODEL_CLIP = "lyria-3-clip-preview"

#: Per-track ``{model: usd}`` — a VIEW of ``canon.pricing.MUSIC`` (the only
#: price source, master §3.0-C, row P0-7); same name and shape as before. A
#: flat list price is ``estimated`` (P0 paper P.9 J3): Lyria reports no
#: per-call cost.
PRICING = {model: row["usd"] for model, row in _pricing.MUSIC.items()}


class LyriaMusicBackend:
    """Music backend using Google Lyria 3 via google-genai SDK.

    Implements ``canon.backends.base.MusicBackend``.

    ``duration_seconds < 60`` uses the clip model; ``>= 60`` uses the pro model.
    ``last_cost`` is updated after each call based on the model used.

    Args:
        api_key: If provided, passed directly to ``genai.Client``. If omitted,
            the SDK reads ``GOOGLE_API_KEY`` from the environment.
        model_pro: Model ID for full-length tracks (≥ 60 s). Defaults to
            ``"lyria-3-pro-preview"``.
        model_clip: Model ID for short clips (< 60 s). Defaults to
            ``"lyria-3-clip-preview"``.

    Raises:
        ImportError: If ``google-genai`` is not installed.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model_pro: str = DEFAULT_MODEL_PRO,
        model_clip: str = DEFAULT_MODEL_CLIP,
    ) -> None:
        try:
            import google.genai as genai
        except ImportError as e:
            raise ImportError(
                "LyriaMusicBackend requires the `google-genai` package. "
                "Install with: pip install canon-ai[audio]"
            ) from e
        self._genai = genai
        self.model_pro = model_pro
        self.model_clip = model_clip
        self._client = genai.Client(api_key=api_key or os.environ.get("GOOGLE_API_KEY"))
        self.last_cost: float = 0.0
        #: Priced from the table, never provider-reported (P.9 J3).
        self.last_cost_accuracy: str = _pricing.ESTIMATED
        self._unpriced_models: set[str] = set()

    def _note_cost(self, model: str) -> None:
        """Price the track that just returned from the table (Lyria reports
        no cost); LOUD once per unpriced model, never a silent $0 (master
        §3.0-B) — the same shape as ``image_fal._note_cost``."""
        row = _pricing.music(model)
        if row is None:
            if model not in self._unpriced_models:
                self._unpriced_models.add(model)
                logger.warning(
                    "No canon.pricing.MUSIC row for Lyria model %r — its calls "
                    "report as $0 (accuracy %r). Add the row to canon.pricing.",
                    model, _pricing.ESTIMATED,
                )
            self.last_cost = 0.0
        else:
            self.last_cost = float(row["usd"])
        self.last_cost_accuracy = _pricing.ESTIMATED

    def _select_model(self, duration_seconds: int) -> str:
        """Return clip model for short requests, pro model for full-length."""
        return self.model_clip if duration_seconds < 60 else self.model_pro

    def generate(self, prompt: str, duration_seconds: int) -> bytes:
        """Synchronously generate a music track.

        Args:
            prompt: Text prompt describing the desired music.
            duration_seconds: Requested track length. Selects clip vs pro model.

        Returns:
            Raw audio bytes from the Lyria response.

        Raises:
            ValueError: If the response contains no audio part.
        """
        model = self._select_model(duration_seconds)
        response = self._client.models.generate_content(
            model=model,
            contents=prompt,
            config={"response_modalities": ["AUDIO", "TEXT"]},
        )
        audio_bytes = self._extract_audio(response)
        self._note_cost(model)
        return audio_bytes

    async def generate_async(self, prompt: str, duration_seconds: int) -> bytes:
        """Async music generation.

        Uses ``client.aio`` if available (native async); falls back to
        ``asyncio.to_thread`` wrapping the sync call.

        Args:
            prompt: Text prompt describing the desired music.
            duration_seconds: Requested track length.

        Returns:
            Raw audio bytes.
        """
        model = self._select_model(duration_seconds)
        if hasattr(self._client, "aio"):
            response = await self._client.aio.models.generate_content(
                model=model,
                contents=prompt,
                config={"response_modalities": ["AUDIO", "TEXT"]},
            )
            self._note_cost(model)
            return self._extract_audio(response)
        import asyncio

        return await asyncio.to_thread(self.generate, prompt, duration_seconds)

    def generate_and_save(self, prompt: str, filepath: str, duration_seconds: int) -> bool:
        """Generate a music track and write it to ``filepath``.

        Creates parent directories as needed. Returns ``True`` on success,
        ``False`` on any exception (network error, API error, etc.).
        """
        try:
            data = self.generate(prompt, duration_seconds)
            Path(filepath).parent.mkdir(parents=True, exist_ok=True)
            Path(filepath).write_bytes(data)
            return True
        except Exception:
            return False

    async def generate_and_save_async(
        self, prompt: str, filepath: str, duration_seconds: int
    ) -> bool:
        """Async variant of ``generate_and_save``."""
        try:
            data = await self.generate_async(prompt, duration_seconds)
            Path(filepath).parent.mkdir(parents=True, exist_ok=True)
            Path(filepath).write_bytes(data)
            return True
        except Exception:
            return False

    @staticmethod
    def _extract_audio(response) -> bytes:
        """Pull audio bytes out of the genai response.

        Response shape (per Lyria docs):
        ``response.candidates[0].content.parts[*].inline_data.data``
        Audio parts have ``mime_type`` starting with ``'audio/'``.

        Raises:
            ValueError: If no audio part is found in the response.
        """
        for candidate in getattr(response, "candidates", []) or []:
            for part in getattr(candidate.content, "parts", []) or []:
                inline = getattr(part, "inline_data", None)
                if inline and getattr(inline, "mime_type", "").startswith("audio/"):
                    return inline.data
        raise ValueError("No audio part found in Lyria response")


def register() -> None:
    """Register ``LyriaMusicBackend`` with ``BackendRegistry`` as ``'lyria'``."""
    from canon.backends.registry import BackendRegistry

    BackendRegistry.register_music("lyria", lambda: LyriaMusicBackend())
