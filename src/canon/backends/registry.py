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

    # Register a chat (agent conversation) backend — Phase 1 A1
    BackendRegistry.register_chat("anthropic", lambda: AnthropicChatBackend())
    BackendRegistry.chat_ids()  # -> ["anthropic"]: the model picker's data

    # In tests — clear all registrations between test cases
    BackendRegistry.reset()
"""

from __future__ import annotations

from collections.abc import Callable

from canon.backends.base import ChatBackend, ImageBackend, LLMBackend, MusicBackend, SFXBackend


class BackendRegistry:
    """Singleton registry for LLM, chat, image, music, and SFX backend factories.

    Backends are registered as factory callables (typically backend classes
    or zero-arg lambdas). Lazy instantiation: ``llm("name")`` constructs once
    and caches.

    The factory pattern lets users register configured instances::

        BackendRegistry.register_llm("ollama", lambda: OllamaBackend(model="llama3"))

    rather than bare classes, which would require zero-arg construction.

    The ``chat`` namespace (Phase 1 A1) holds ``ChatBackend`` factories for
    the agent's conversation loop, following the same register/get/reset
    idiom. ``chat_ids()`` is the ids-as-data surface the panel's provider
    picker reads (Phase 1 §3.3) — provider ids are registry keys, never a
    hardcoded union.

    Built-in registrations done at module import time:
        - ``"anthropic"`` -> ``AnthropicBackend`` (registered by
          ``canon.backends.anthropic`` when imported; canon does NOT
          auto-register it to avoid a hard ``anthropic`` dependency).
        - the chat ``"anthropic"`` id likewise registers only via
          ``canon.backends.chat_anthropic.register()``, never at import.
    """

    _llm_factories: dict[str, Callable[[], LLMBackend]] = {}
    _llm_instances: dict[str, LLMBackend] = {}
    _chat_factories: dict[str, Callable[[], ChatBackend]] = {}
    _chat_instances: dict[str, ChatBackend] = {}
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
    def register_chat(cls, name: str, factory: Callable[[], ChatBackend]) -> None:
        """Register a chat (conversation) backend factory under ``name``.

        Args:
            name: Registry key — the provider id the panel's picker shows.
            factory: Zero-arg callable that returns a ``ChatBackend`` instance.

        Note:
            Re-registering an existing name replaces the factory **and** clears
            any cached instance, so the new factory is used on next access.
        """
        cls._chat_factories[name] = factory
        cls._chat_instances.pop(name, None)

    @classmethod
    def chat(cls, name: str) -> ChatBackend:
        """Get a chat backend by registered name. Raises ``KeyError`` if unknown.

        Lazy instantiation, same as ``llm()``: the factory runs once on first
        access and the instance is cached.

        Args:
            name: Registry key used in ``register_chat``.

        Returns:
            The cached ``ChatBackend`` instance.

        Raises:
            KeyError: If ``name`` was never registered.
        """
        if name not in cls._chat_factories:
            raise KeyError(
                f"BackendRegistry: no chat backend registered for {name!r}. "
                f"Known backends: {list(cls._chat_factories)}"
            )
        if name not in cls._chat_instances:
            cls._chat_instances[name] = cls._chat_factories[name]()
        return cls._chat_instances[name]

    @classmethod
    def chat_ids(cls) -> list[str]:
        """The registered chat backend ids, in registration order.

        This is the ids-as-data surface a provider picker reads — it never
        instantiates anything, so a registered-but-keyless provider still
        lists (and renders disabled-with-a-reason downstream).
        """
        return list(cls._chat_factories)

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
        state between test cases. Clears LLM, chat, image, music, and SFX state.
        """
        cls._llm_factories.clear()
        cls._llm_instances.clear()
        cls._chat_factories.clear()
        cls._chat_instances.clear()
        cls._image_factories.clear()
        cls._image_instances.clear()
        cls._music_factories.clear()
        cls._music_instances.clear()
        cls._sfx_factories.clear()
        cls._sfx_instances.clear()
