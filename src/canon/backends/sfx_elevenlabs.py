"""ElevenLabs SFX backend.

Optional dependency: install with ``pip install canon-ai[audio]``.
Uses the ``elevenlabs`` SDK's ``text_to_sound_effects.convert`` endpoint.

Downstream code that needs the real ``ImportError`` should import directly::

    from canon.backends.sfx_elevenlabs import ElevenLabsSFXBackend

Code that only needs to check availability can use the lazy re-export from
``canon.backends`` which returns ``None`` when elevenlabs is absent.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from canon import pricing as _pricing

if TYPE_CHECKING:
    pass  # no top-level elevenlabs import

#: USD per auto-duration effect — a VIEW of ``canon.pricing.SFX["elevenlabs"]``
#: (the only price source, master §3.0-C, row P0-7); same name as before. A
#: flat list price is ``estimated`` (P0 paper P.9 J3).
COST_PER_EFFECT = _pricing.SFX["elevenlabs"]["usd"]


class ElevenLabsSFXBackend:
    """SFX backend using ElevenLabs text-to-sound-effects.

    Implements ``canon.backends.base.SFXBackend``.

    The ElevenLabs SDK is sync-only as of writing; async methods wrap
    the sync call via ``asyncio.to_thread``.

    The ``loop`` parameter is passed through to the SDK if the SDK supports
    it. As of elevenlabs>=1.0 the ``text_to_sound_effects.convert`` method
    does not expose a ``loop`` kwarg, so passing it results in a
    ``TypeError`` from the SDK. The backend records the intent but silently
    drops unknown kwargs to remain forward-compatible (see
    ``_safe_convert``).

    Args:
        api_key: If provided, passed to ``ElevenLabs(api_key=...)``. If
            omitted, the SDK reads ``ELEVENLABS_API_KEY`` from the
            environment.

    Raises:
        ImportError: If ``elevenlabs`` is not installed.
    """

    def __init__(
        self,
        api_key: str | None = None,
    ) -> None:
        try:
            from elevenlabs import ElevenLabs
        except ImportError as e:
            raise ImportError(
                "ElevenLabsSFXBackend requires the `elevenlabs` package. "
                "Install with: pip install canon-ai[audio]"
            ) from e
        self._client = ElevenLabs(api_key=api_key or os.environ.get("ELEVENLABS_API_KEY"))
        self.last_cost: float = 0.0
        #: Priced from the table, never provider-reported (P.9 J3).
        self.last_cost_accuracy: str = _pricing.ESTIMATED

    def generate(self, prompt: str, duration_seconds: float, loop: bool) -> bytes:
        """Synchronously generate a sound effect.

        Args:
            prompt: Text prompt describing the desired sound.
            duration_seconds: Requested effect duration in seconds.
            loop: Whether the SFX should be designed for seamless looping
                (e.g. ambience tracks). Passed to the SDK if supported.

        Returns:
            Raw audio bytes (typically MP3).
        """
        kwargs: dict = {"text": prompt, "duration_seconds": duration_seconds}
        if loop:
            kwargs["loop"] = True  # if SDK supports it; harmless if ignored
        chunks = self._safe_convert(**kwargs)
        data = b"".join(chunks)
        self.last_cost = COST_PER_EFFECT
        return data

    async def generate_async(self, prompt: str, duration_seconds: float, loop: bool) -> bytes:
        """Async SFX generation via ``asyncio.to_thread``.

        The ElevenLabs SDK does not expose a native async API; this method
        offloads the sync call to a thread pool.
        """
        import asyncio

        return await asyncio.to_thread(self.generate, prompt, duration_seconds, loop)

    def generate_and_save(
        self, prompt: str, filepath: str, duration_seconds: float, loop: bool
    ) -> bool:
        """Generate a sound effect and write it to ``filepath``.

        Creates parent directories as needed. Returns ``True`` on success,
        ``False`` on any exception (network error, API error, etc.).
        """
        try:
            data = self.generate(prompt, duration_seconds, loop)
            Path(filepath).parent.mkdir(parents=True, exist_ok=True)
            Path(filepath).write_bytes(data)
            return True
        except Exception:
            return False

    async def generate_and_save_async(
        self, prompt: str, filepath: str, duration_seconds: float, loop: bool
    ) -> bool:
        """Async variant of ``generate_and_save``."""
        try:
            data = await self.generate_async(prompt, duration_seconds, loop)
            Path(filepath).parent.mkdir(parents=True, exist_ok=True)
            Path(filepath).write_bytes(data)
            return True
        except Exception:
            return False

    def _safe_convert(self, **kwargs):
        """Call ``text_to_sound_effects.convert``, dropping unsupported kwargs.

        The ElevenLabs SDK evolves; ``loop`` may not be accepted in all
        versions.  We attempt the call with all kwargs first; if a
        ``TypeError`` is raised (unexpected keyword argument), we retry
        without ``loop`` so the rest of the call succeeds.
        """
        try:
            return self._client.text_to_sound_effects.convert(**kwargs)
        except TypeError:
            # SDK doesn't accept one of the extra kwargs (e.g. loop).
            # Retry without loop to preserve forward-compatibility.
            kwargs.pop("loop", None)
            return self._client.text_to_sound_effects.convert(**kwargs)


def register() -> None:
    """Register ``ElevenLabsSFXBackend`` with ``BackendRegistry`` as ``'elevenlabs'``."""
    from canon.backends.registry import BackendRegistry

    BackendRegistry.register_sfx("elevenlabs", lambda: ElevenLabsSFXBackend())
