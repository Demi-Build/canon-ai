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
class ImageEditBackend(Protocol):
    """Protocol for image *editing* (img2img) provider adapters.

    Editing is a strictly MORE capable surface than ``ImageBackend``'s
    text-to-image: given an existing image plus a prompt, return a new one
    (e.g. nano-banana's ``/edit`` endpoint). A backend that supports editing
    implements BOTH protocols structurally; a consumer gates the edit path on
    ``isinstance(backend, ImageEditBackend)`` and falls back loudly when it's
    absent.

    Kept SEPARATE from ``ImageBackend`` — NOT added as a method there — on
    purpose. ``ImageBackend`` is ``@runtime_checkable``, so adding a member
    would make ``isinstance(x, ImageBackend)`` require it, breaking every
    existing implementor that lacks img2img (``LocalImageBackend`` has no edit
    pipeline; third-party backends need not gain one). This is the same
    reasoning the ``prefers_serial`` note on ``LLMBackend`` records: optional
    capabilities stay off the required Protocol surface.

    Example::

        if isinstance(backend, ImageEditBackend):
            sheet = backend.edit(base_png, "4-frame walk cycle", 2048, 512)
        else:
            ...  # no img2img — keep the static sprite (loud fallback)
    """

    def edit(self, image_bytes: bytes, prompt: str, width: int, height: int) -> bytes:
        """Edit ``image_bytes`` per ``prompt`` (img2img).

        Args:
            image_bytes: The source image (PNG-encoded) to edit.
            prompt: Text instructions describing the desired edit.
            width: Target width in pixels.
            height: Target height in pixels.

        Returns:
            Raw image bytes, conformed to exactly ``width`` x ``height`` (the
            same code-enforced size contract as ``ImageBackend.generate``).
        """
        ...

    async def edit_async(
        self, image_bytes: bytes, prompt: str, width: int, height: int
    ) -> bytes:
        """Async variant of ``edit``."""
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
class VLMBackend(Protocol):
    """Protocol for vision-language judge adapters.

    A VLM backend *judges* existing images against a prompt and returns a
    text (typically JSON) verdict — it never generates assets, so there is
    no ``generate_and_save`` / async fan-out surface here. Callers own
    prompt construction, response parsing, and retry.

    Example::

        backend = AnthropicVLMBackend()
        verdict = backend.judge("Do these two renders match?", [png_a, png_b])
    """

    def judge(self, prompt: str, images: list[bytes], max_tokens: int = 1024) -> str:
        """Judge the given PNG images against the prompt.

        Args:
            prompt: The full judgment instructions (criteria + response format).
            images: PNG-encoded images, in the order the prompt references them.
            max_tokens: Response budget.

        Returns:
            The model's text response as a plain string.
        """
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
