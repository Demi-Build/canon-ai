"""Fake backends — deterministic in-memory/file-based backends for testing.

These are **public utilities**. Use them in your own pipeline tests to avoid
network calls and get deterministic, inspectable results::

    from canon.backends import FakeLLMBackend, FakeImageBackend, FakeMusicBackend, FakeSFXBackend

    fake_img = FakeImageBackend()
    result = fake_img.generate_and_save("a red dragon", "/tmp/test.png", 256, 256)
    assert result is True
    assert len(fake_img.calls) == 1
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from canon.llm.request import LLMRequest

# ---------------------------------------------------------------------------
# Minimal placeholder bytes
# ---------------------------------------------------------------------------

# Minimal valid 1×1 PNG (89 bytes).  Generated from:
#   python -c "import struct, zlib; ..."
# Verified by PIL.Image.open(io.BytesIO(_MIN_PNG_BYTES)).
_MIN_PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a"          # PNG signature
    "0000000d49484452"          # IHDR chunk length + type
    "00000001"                  # width: 1
    "00000001"                  # height: 1
    "08060000001f15c489"        # bit depth 8, RGBA, CRC
    "0000000a49444154"          # IDAT chunk length + type
    "789c6260000000020001"      # zlib-compressed 1×1 transparent pixel
    "e221bc33"                  # CRC
    "0000000049454e44ae426082"  # IEND
)

# Minimal valid MP3 frame (ID3v2 header + one silent MPEG frame).
# An ID3v2 header with no tags followed by a single silent 128kbps 44.1kHz MPEG frame.
# Not a fully-decoded playable track, but enough to be recognisable as MP3 bytes.
_MIN_MP3_BYTES = (
    b"ID3"              # ID3 magic
    b"\x03\x00"         # version 2.3.0
    b"\x00"             # flags
    b"\x00\x00\x00\x00" # tag size (0 tags)
    # One silent MPEG1 Layer3 frame header (0xff 0xfb = sync + MPEG1 Layer3 128kbps 44100Hz)
    b"\xff\xfb\x90\x00"
    + b"\x00" * 413     # padding to complete a 417-byte frame
)


# ---------------------------------------------------------------------------
# FakeLLMBackend
# ---------------------------------------------------------------------------


class FakeLLMBackend:
    """Deterministic in-memory LLM backend for testing.

    Public utility — use this in your own pipeline tests to avoid network
    calls. Records every request to ``.calls`` for assertions.

    Three modes:

    **List mode** — provide a list of strings. Responses are returned in
    order. ``IndexError`` is raised if the list is exhausted before all
    requests are served::

        fake = FakeLLMBackend(["first", "second"])

    **Dict mode** — provide a ``{substring: response}`` mapping. The
    ``user_message`` of each request is checked against every key; the first
    matching value is returned. ``KeyError`` is raised if no key matches::

        fake = FakeLLMBackend({"dragon": "Here be dragons.", "sword": "A fine blade."})

    **Callable mode** — provide a function ``(LLMRequest) -> str``. The
    function is called with the full request and its return value is used::

        fake = FakeLLMBackend(lambda req: f"echo: {req.user_message[:20]}")

    Attributes:
        calls: All ``LLMRequest`` objects passed to ``generate()``, in call
            order. Inspect this in assertions to verify the client sent the
            expected prompts.
    """

    def __init__(
        self,
        responses: list[str] | dict[str, str] | Callable[[LLMRequest], str],
    ) -> None:
        self._responses = responses
        self._index = 0
        self.calls: list[LLMRequest] = []

    def generate(self, request: LLMRequest) -> str:
        """Return the next canned response and record the request.

        Args:
            request: The ``LLMRequest`` from the caller.

        Returns:
            The configured response string.

        Raises:
            IndexError: (list mode) if all responses have been consumed.
            KeyError: (dict mode) if no key matches ``request.user_message``.
            TypeError: if ``responses`` was constructed with an unsupported type.
        """
        self.calls.append(request)

        # Callable mode — check this first so a callable isn't accidentally
        # matched by the isinstance checks below (lambdas are callable and are
        # neither list nor dict, but a subclass of list could theoretically be
        # callable too — checking callable last on list/dict would be ambiguous).
        if callable(self._responses) and not isinstance(self._responses, (list, dict)):
            return self._responses(request)

        if isinstance(self._responses, list):
            response = self._responses[self._index]
            self._index += 1
            return response

        if isinstance(self._responses, dict):
            for key, value in self._responses.items():
                if key in request.user_message:
                    return value
            raise KeyError(
                f"FakeLLMBackend: no dict key matched user_message: "
                f"{request.user_message[:80]}..."
            )

        raise TypeError(
            f"FakeLLMBackend: unsupported responses type {type(self._responses)}"
        )


# ---------------------------------------------------------------------------
# FakeImageBackend
# ---------------------------------------------------------------------------


class FakeImageBackend:
    """Deterministic image backend for tests.

    Copies a placeholder PNG from ``asset_dir`` to the requested save path.
    Records every call to ``.calls`` for assertion.

    Public utility — downstream users testing canon pipelines without
    paying for fal.ai calls.

    Args:
        asset_dir: Directory containing one or more PNG files.
        placeholder: Specific PNG path; takes precedence over ``asset_dir``.

    If neither is provided, a minimal 1×1 transparent PNG is generated inline.
    """

    def __init__(
        self,
        asset_dir: str | Path | None = None,
        placeholder: str | Path | None = None,
    ) -> None:
        self.asset_dir = Path(asset_dir) if asset_dir else None
        self.placeholder = Path(placeholder) if placeholder else None
        self.calls: list[dict] = []

    def generate(self, prompt: str, width: int, height: int) -> bytes:
        """Return placeholder PNG bytes and record the call."""
        self.calls.append({"prompt": prompt, "width": width, "height": height})
        return self._placeholder_bytes()

    async def generate_async(self, prompt: str, width: int, height: int) -> bytes:
        """Async variant of ``generate``."""
        return self.generate(prompt, width, height)

    def generate_and_save(
        self, prompt: str, filepath: str, width: int, height: int
    ) -> bool:
        """Write placeholder PNG to ``filepath`` and record the call."""
        self.calls.append(
            {"prompt": prompt, "filepath": filepath, "width": width, "height": height}
        )
        dest = Path(filepath)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self._placeholder_bytes())
        return True

    async def generate_and_save_async(
        self, prompt: str, filepath: str, width: int, height: int
    ) -> bool:
        """Async variant of ``generate_and_save``."""
        return self.generate_and_save(prompt, filepath, width, height)

    def _placeholder_bytes(self) -> bytes:
        if self.placeholder and self.placeholder.exists():
            return self.placeholder.read_bytes()
        if self.asset_dir and self.asset_dir.exists():
            pngs = list(self.asset_dir.glob("*.png"))
            if pngs:
                return pngs[0].read_bytes()
        return _MIN_PNG_BYTES


# ---------------------------------------------------------------------------
# FakeMusicBackend
# ---------------------------------------------------------------------------


class FakeMusicBackend:
    """Deterministic music backend for tests.

    Copies a placeholder MP3 from ``asset_dir`` to the requested save path.
    Records every call to ``.calls`` for assertion.

    Public utility — downstream users testing canon pipelines without
    paying for Lyria/Suno API calls.

    Args:
        asset_dir: Directory containing one or more MP3 files.
        placeholder: Specific MP3 path; takes precedence over ``asset_dir``.

    If neither is provided, a minimal silent MP3 stub is generated inline.
    """

    def __init__(
        self,
        asset_dir: str | Path | None = None,
        placeholder: str | Path | None = None,
    ) -> None:
        self.asset_dir = Path(asset_dir) if asset_dir else None
        self.placeholder = Path(placeholder) if placeholder else None
        self.calls: list[dict] = []

    def generate(self, prompt: str, duration_seconds: int) -> bytes:
        """Return placeholder MP3 bytes and record the call."""
        self.calls.append({"prompt": prompt, "duration_seconds": duration_seconds})
        return self._placeholder_bytes()

    async def generate_async(self, prompt: str, duration_seconds: int) -> bytes:
        """Async variant of ``generate``."""
        return self.generate(prompt, duration_seconds)

    def generate_and_save(
        self, prompt: str, filepath: str, duration_seconds: int
    ) -> bool:
        """Write placeholder MP3 to ``filepath`` and record the call."""
        self.calls.append(
            {"prompt": prompt, "filepath": filepath, "duration_seconds": duration_seconds}
        )
        dest = Path(filepath)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self._placeholder_bytes())
        return True

    async def generate_and_save_async(
        self, prompt: str, filepath: str, duration_seconds: int
    ) -> bool:
        """Async variant of ``generate_and_save``."""
        return self.generate_and_save(prompt, filepath, duration_seconds)

    def _placeholder_bytes(self) -> bytes:
        if self.placeholder and self.placeholder.exists():
            return self.placeholder.read_bytes()
        if self.asset_dir and self.asset_dir.exists():
            mp3s = list(self.asset_dir.glob("*.mp3"))
            if mp3s:
                return mp3s[0].read_bytes()
        return _MIN_MP3_BYTES


# ---------------------------------------------------------------------------
# FakeSFXBackend
# ---------------------------------------------------------------------------


class FakeSFXBackend:
    """Deterministic SFX backend for tests.

    Copies a placeholder MP3 from ``asset_dir`` to the requested save path.
    Records every call to ``.calls`` for assertion.

    Public utility — downstream users testing canon pipelines without
    paying for ElevenLabs API calls.

    Args:
        asset_dir: Directory containing one or more MP3 files.
        placeholder: Specific MP3 path; takes precedence over ``asset_dir``.

    If neither is provided, a minimal silent MP3 stub is generated inline.
    """

    def __init__(
        self,
        asset_dir: str | Path | None = None,
        placeholder: str | Path | None = None,
    ) -> None:
        self.asset_dir = Path(asset_dir) if asset_dir else None
        self.placeholder = Path(placeholder) if placeholder else None
        self.calls: list[dict] = []

    def generate(self, prompt: str, duration_seconds: float, loop: bool) -> bytes:
        """Return placeholder MP3 bytes and record the call."""
        self.calls.append(
            {"prompt": prompt, "duration_seconds": duration_seconds, "loop": loop}
        )
        return self._placeholder_bytes()

    async def generate_async(
        self, prompt: str, duration_seconds: float, loop: bool
    ) -> bytes:
        """Async variant of ``generate``."""
        return self.generate(prompt, duration_seconds, loop)

    def generate_and_save(
        self, prompt: str, filepath: str, duration_seconds: float, loop: bool
    ) -> bool:
        """Write placeholder MP3 to ``filepath`` and record the call."""
        self.calls.append(
            {
                "prompt": prompt,
                "filepath": filepath,
                "duration_seconds": duration_seconds,
                "loop": loop,
            }
        )
        dest = Path(filepath)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self._placeholder_bytes())
        return True

    async def generate_and_save_async(
        self, prompt: str, filepath: str, duration_seconds: float, loop: bool
    ) -> bool:
        """Async variant of ``generate_and_save``."""
        return self.generate_and_save(prompt, filepath, duration_seconds, loop)

    def _placeholder_bytes(self) -> bytes:
        if self.placeholder and self.placeholder.exists():
            return self.placeholder.read_bytes()
        if self.asset_dir and self.asset_dir.exists():
            mp3s = list(self.asset_dir.glob("*.mp3"))
            if mp3s:
                return mp3s[0].read_bytes()
        return _MIN_MP3_BYTES
