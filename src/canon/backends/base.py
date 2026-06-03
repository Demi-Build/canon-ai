"""Backend protocols for LLM, image, music, and SFX generation.

Implement ``LLMBackend`` to add a custom LLM provider (OpenAI, Ollama, etc.).
Register your backend via ``BackendRegistry.register_llm()``.

``ImageBackend``, ``MusicBackend``, and ``SFXBackend`` each carry sync + async
variants so ``AssetPhase`` can fan out via ``asyncio.gather()``.  Backends
without a real async API can wrap their sync method with ``asyncio.to_thread``
in the async variant.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from canon.llm.request import LLMRequest


@runtime_checkable
class LLMBackend(Protocol):
    """Protocol for LLM provider adapters.

    Implement this to add a custom provider (OpenAI, Ollama, HuggingFace,
    etc.). Register the implementation with ``BackendRegistry.register_llm()``.

    Optional attribute ``prefers_serial``: if ``True``, ``LLMClient.generate_batch``
    forces ``max_workers=1``. Useful for local backends (HuggingFace, Llama.cpp)
    that cannot safely run concurrent requests. API backends typically leave this
    unset (defaults to ``False`` via ``getattr``).

    Example::

        class OllamaBackend:
            prefers_serial = True

            def __init__(self, model: str = "llama3", host: str = "http://localhost:11434"):
                self.model = model
                self.host = host

            def generate(self, request: LLMRequest) -> str:
                # HTTP call to Ollama
                ...

        BackendRegistry.register_llm("ollama", lambda: OllamaBackend(model="llama3"))
    """

    def generate(self, request: LLMRequest) -> str:
        """Generate a text response for the given request.

        Args:
            request: The fully-formed LLMRequest (system, user_message, examples,
                max_tokens).

        Returns:
            The model's text response as a plain string.
        """
        ...

    # NOTE: ``prefers_serial`` is intentionally NOT declared as a Protocol member.
    # If declared here it becomes required for isinstance() checks, breaking every
    # backend that doesn't set it. LLMClient.generate_batch reads it via
    # ``getattr(backend, "prefers_serial", False)`` so backends can opt in without
    # implementing a full attribute contract.


@runtime_checkable
class ImageBackend(Protocol):
    """Protocol for image-generation provider adapters.

    Async variants exist so AssetPhase can fan out via asyncio.gather().
    Sync variants exist for simple call sites and registry-based access.

    Backends without a real async API can wrap their sync method with
    ``asyncio.to_thread`` in the async variant.

    Example::

        class FalAiBackend:
            async def generate_async(self, prompt, width, height) -> bytes:
                return await asyncio.to_thread(self.generate, prompt, width, height)

        BackendRegistry.register_image("fal", lambda: FalAiBackend())
    """

    def generate(self, prompt: str, width: int, height: int) -> bytes:
        """Generate an image from a text prompt, returning raw bytes."""
        ...

    async def generate_async(self, prompt: str, width: int, height: int) -> bytes:
        """Async variant of ``generate``."""
        ...

    def generate_and_save(self, prompt: str, filepath: str, width: int, height: int) -> bool:
        """Generate an image and write it directly to disk. Returns True on success."""
        ...

    async def generate_and_save_async(
        self, prompt: str, filepath: str, width: int, height: int
    ) -> bool:
        """Async variant of ``generate_and_save``."""
        ...


@runtime_checkable
class MusicBackend(Protocol):
    """Protocol for music-generation provider adapters (Lyria, Suno, etc.).

    Async variants exist so AssetPhase can fan out via asyncio.gather().

    Example::

        BackendRegistry.register_music("lyria", lambda: LyriaMusicBackend())
    """

    def generate(self, prompt: str, duration_seconds: int) -> bytes:
        """Generate a music track from a text prompt, returning raw bytes."""
        ...

    async def generate_async(self, prompt: str, duration_seconds: int) -> bytes:
        """Async variant of ``generate``."""
        ...

    def generate_and_save(self, prompt: str, filepath: str, duration_seconds: int) -> bool:
        """Generate a music track and write it directly to disk. Returns True on success."""
        ...

    async def generate_and_save_async(
        self, prompt: str, filepath: str, duration_seconds: int
    ) -> bool:
        """Async variant of ``generate_and_save``."""
        ...


@runtime_checkable
class SFXBackend(Protocol):
    """Protocol for SFX-generation provider adapters (ElevenLabs, etc.).

    ``loop`` indicates the SFX should be loopable (for ambience).

    Async variants exist so AssetPhase can fan out via asyncio.gather().

    Example::

        BackendRegistry.register_sfx("elevenlabs", lambda: ElevenLabsSFXBackend())
    """

    def generate(self, prompt: str, duration_seconds: float, loop: bool) -> bytes:
        """Generate a sound effect from a text prompt, returning raw bytes."""
        ...

    async def generate_async(self, prompt: str, duration_seconds: float, loop: bool) -> bytes:
        """Async variant of ``generate``."""
        ...

    def generate_and_save(
        self, prompt: str, filepath: str, duration_seconds: float, loop: bool
    ) -> bool:
        """Generate a SFX and write it directly to disk. Returns True on success."""
        ...

    async def generate_and_save_async(
        self, prompt: str, filepath: str, duration_seconds: float, loop: bool
    ) -> bool:
        """Async variant of ``generate_and_save``."""
        ...
