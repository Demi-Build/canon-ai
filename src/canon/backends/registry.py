"""BackendRegistry — module-level singleton for LLM, image, music, and SFX backend factories.

Usage::

    from canon.backends import BackendRegistry

    # Register a configured backend factory (recommended for parameterized backends)
    BackendRegistry.register_llm("ollama", lambda: OllamaBackend(model="llama3"))

    # Or register a class directly (zero-arg construction)
    BackendRegistry.register_llm("my_backend", MyBackend)

    # Retrieve — lazy instantiation; same instance returned on every call
    backend = BackendRegistry.llm("ollama")

    # Register asset backends
    BackendRegistry.register_image("fal", lambda: FalImageBackend())
    BackendRegistry.register_music("lyria", lambda: LyriaMusicBackend())
    BackendRegistry.register_sfx("elevenlabs", lambda: ElevenLabsSFXBackend())

    # In tests — clear all registrations between test cases
    BackendRegistry.reset()
"""

from __future__ import annotations

from collections.abc import Callable

from canon.backends.base import ImageBackend, LLMBackend, MusicBackend, SFXBackend


class BackendRegistry:
    """Singleton registry for LLM, image, music, and SFX backend factories.

    Backends are registered as factory callables (typically backend classes
    or zero-arg lambdas). Lazy instantiation: ``llm("name")`` constructs once
    and caches.

    The factory pattern lets users register configured instances::

        BackendRegistry.register_llm("ollama", lambda: OllamaBackend(model="llama3"))

    rather than bare classes, which would require zero-arg construction.

    Built-in registrations done at module import time:
        - ``"anthropic"`` -> ``AnthropicBackend`` (registered by
          ``canon.backends.anthropic`` when imported; canon does NOT
          auto-register it to avoid a hard ``anthropic`` dependency).
    """

    _llm_factories: dict[str, Callable[[], LLMBackend]] = {}
    _llm_instances: dict[str, LLMBackend] = {}
    _image_factories: dict[str, Callable[[], ImageBackend]] = {}
    _image_instances: dict[str, ImageBackend] = {}
    _music_factories: dict[str, Callable[[], MusicBackend]] = {}
    _music_instances: dict[str, MusicBackend] = {}
    _sfx_factories: dict[str, Callable[[], SFXBackend]] = {}
    _sfx_instances: dict[str, SFXBackend] = {}

    @classmethod
    def register_llm(cls, name: str, factory: Callable[[], LLMBackend]) -> None:
        """Register an LLM backend factory under ``name``.

        Args:
            name: Registry key. Subsequent calls to ``llm(name)`` return an
                instance produced by this factory.
            factory: Zero-arg callable (class or lambda) that returns an
                ``LLMBackend`` instance.

        Note:
            Re-registering an existing name replaces the factory **and** clears
            any cached instance, so the new factory is used on next access.
        """
        cls._llm_factories[name] = factory
        cls._llm_instances.pop(name, None)

    @classmethod
    def llm(cls, name: str) -> LLMBackend:
        """Get an LLM backend by registered name. Raises ``KeyError`` if unknown.

        Lazy instantiation: the factory is called once on first access; the
        resulting instance is cached and returned on subsequent calls.

        Args:
            name: Registry key used in ``register_llm``.

        Returns:
            The cached ``LLMBackend`` instance.

        Raises:
            KeyError: If ``name`` was never registered.
        """
        if name not in cls._llm_factories:
            raise KeyError(
                f"BackendRegistry: no LLM backend registered for {name!r}. "
                f"Known backends: {list(cls._llm_factories)}"
            )
        if name not in cls._llm_instances:
            cls._llm_instances[name] = cls._llm_factories[name]()
        return cls._llm_instances[name]

    @classmethod
    def register_image(cls, name: str, factory: Callable[[], ImageBackend]) -> None:
        """Register an image backend factory under ``name``.

        Args:
            name: Registry key.
            factory: Zero-arg callable that returns an ``ImageBackend`` instance.

        Note:
            Re-registering an existing name replaces the factory **and** clears
            any cached instance.
        """
        cls._image_factories[name] = factory
        cls._image_instances.pop(name, None)

    @classmethod
    def image(cls, name: str) -> ImageBackend:
        """Get an image backend by registered name. Raises ``KeyError`` if unknown.

        Args:
            name: Registry key used in ``register_image``.

        Returns:
            The cached ``ImageBackend`` instance.

        Raises:
            KeyError: If ``name`` was never registered.
        """
        if name not in cls._image_factories:
            raise KeyError(
                f"BackendRegistry: no image backend registered for {name!r}. "
                f"Known backends: {list(cls._image_factories)}"
            )
        if name not in cls._image_instances:
            cls._image_instances[name] = cls._image_factories[name]()
        return cls._image_instances[name]

    @classmethod
    def register_music(cls, name: str, factory: Callable[[], MusicBackend]) -> None:
        """Register a music backend factory under ``name``.

        Args:
            name: Registry key.
            factory: Zero-arg callable that returns a ``MusicBackend`` instance.

        Note:
            Re-registering an existing name replaces the factory **and** clears
            any cached instance.
        """
        cls._music_factories[name] = factory
        cls._music_instances.pop(name, None)

    @classmethod
    def music(cls, name: str) -> MusicBackend:
        """Get a music backend by registered name. Raises ``KeyError`` if unknown.

        Args:
            name: Registry key used in ``register_music``.

        Returns:
            The cached ``MusicBackend`` instance.

        Raises:
            KeyError: If ``name`` was never registered.
        """
        if name not in cls._music_factories:
            raise KeyError(
                f"BackendRegistry: no music backend registered for {name!r}. "
                f"Known backends: {list(cls._music_factories)}"
            )
        if name not in cls._music_instances:
            cls._music_instances[name] = cls._music_factories[name]()
        return cls._music_instances[name]

    @classmethod
    def register_sfx(cls, name: str, factory: Callable[[], SFXBackend]) -> None:
        """Register an SFX backend factory under ``name``.

        Args:
            name: Registry key.
            factory: Zero-arg callable that returns an ``SFXBackend`` instance.

        Note:
            Re-registering an existing name replaces the factory **and** clears
            any cached instance.
        """
        cls._sfx_factories[name] = factory
        cls._sfx_instances.pop(name, None)

    @classmethod
    def sfx(cls, name: str) -> SFXBackend:
        """Get an SFX backend by registered name. Raises ``KeyError`` if unknown.

        Args:
            name: Registry key used in ``register_sfx``.

        Returns:
            The cached ``SFXBackend`` instance.

        Raises:
            KeyError: If ``name`` was never registered.
        """
        if name not in cls._sfx_factories:
            raise KeyError(
                f"BackendRegistry: no SFX backend registered for {name!r}. "
                f"Known backends: {list(cls._sfx_factories)}"
            )
        if name not in cls._sfx_instances:
            cls._sfx_instances[name] = cls._sfx_factories[name]()
        return cls._sfx_instances[name]

    @classmethod
    def reset(cls) -> None:
        """Clear all registrations and cached instances.

        Test-only helper. Call in ``setup``/``teardown`` to isolate registry
        state between test cases. Clears LLM, image, music, and SFX state.
        """
        cls._llm_factories.clear()
        cls._llm_instances.clear()
        cls._image_factories.clear()
        cls._image_instances.clear()
        cls._music_factories.clear()
        cls._music_instances.clear()
        cls._sfx_factories.clear()
        cls._sfx_instances.clear()
