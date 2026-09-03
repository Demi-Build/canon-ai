"""The chat-provider registrar map — one data map, two consumers (Phase 1 A2).

Row A1 kept this map inside ``canon.agent.eval`` (``_registrars``). Row A2's
service needs the identical resolution — "an id is data; run its explicit
registrar, then ask ``BackendRegistry.chat(id)``" — so the map moved here
and both ``python -m canon.agent.eval`` and ``python -m canon.agent.service``
import it. Adding a provider is one more entry; neither consumer changes.

``resolve_chat_backend`` is the shared resolution step. It deliberately
raises rather than prints: the eval runner and the service each report a
failure in their own voice (a stderr line + exit 2 in both cases today), and
``"fake"`` is not here at all — the fake backend is constructed by each
consumer from its own script source (the eval's ``fake_turns``, the
service's ``--fake-script``).

Row A4.5 adds ``list_models`` — the data behind ``GET /models`` for A5's
picker (Phase 1 §3.3): every LLM row of ``canon.pricing`` whose provider
is a chat provider (this map ∪ ``BackendRegistry.chat_ids()``), with its
per-1M prices, whether its key env var is present, and whether the
backend exposes a reasoning knob — ids are data; nothing is instantiated.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from canon.backends.base import ChatBackend
from canon.backends.registry import BackendRegistry


def registrars() -> dict[str, Callable[[str | None], None]]:
    """The real providers' explicit registrars, keyed by chat id — data, so
    a new provider is one more entry. Imported here, not at module load:
    each provider module defers its SDK import to construction, so the
    ``ImportError`` for a missing extra surfaces in the caller's handler
    with the install hint, never as a traceback."""
    from canon.backends import chat_anthropic, chat_openai

    return {
        "anthropic": chat_anthropic.register,
        "openai": chat_openai.register,
        "kimi": chat_openai.register_kimi,
    }


def key_envs() -> dict[str, str]:
    """Provider id → the env var its key lives in (the disabled-with-a-reason
    copy names it).

    Row P0-12 (master §6 S6, provider rows as DATA): this is now a VIEW of
    ``canon.providers.PROVIDER_ROWS`` — the one table cradle's Settings screen
    also renders — filtered to the chat providers this map registers. It used
    to be its own literal, which made two places to add a provider. A chat
    provider the table does not carry still resolves (its backend module's
    ``*_KEY_ENV`` constant is the fallback), so the registry stays the source
    of truth for ids.
    """
    from canon import providers as provider_rows
    from canon.backends import chat_openai

    fallback = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": getattr(chat_openai, "OPENAI_KEY_ENV", "OPENAI_API_KEY"),
        "kimi": getattr(chat_openai, "KIMI_KEY_ENV", "MOONSHOT_API_KEY"),
    }
    from_table = provider_rows.backend_key_vars().get("chat", {})
    return {**fallback, **from_table}


#: Provider id → whether its chat backend exposes a reasoning knob
#: (anthropic: thinking + ``effort``; openai / kimi: ``reasoning_effort``
#: behind ``reasoning=True``). Data — a row may override with ``reasoning``.
REASONING: dict[str, bool] = {"anthropic": True, "openai": True, "kimi": True}


def resolve_chat_backend(backend_id: str, model: str | None = None) -> ChatBackend:
    """Run ``backend_id``'s registrar (when the map has one) and fetch the
    registry's instance.

    Ids the map does not know still resolve when something else registered
    them (a test's ``register_chat("scripted", …)``, October's ``demi``
    gateway) — the registry, not this map, is the source of truth.

    Raises:
        KeyError: nothing is registered under ``backend_id`` (the message
            lists ``BackendRegistry.chat_ids()``).
        ImportError: the provider's extra is not installed (the message
            carries the install hint).
    """
    registrar = registrars().get(backend_id)
    if registrar is not None:
        registrar(model)
    return BackendRegistry.chat(backend_id)


def _label(model_id: str) -> str:
    return " ".join(part.capitalize() if part.isalpha() else part for part in model_id.replace("_", "-").split("-"))


def list_models(environ: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """``[{id, provider, label, input_per_1m, output_per_1m, available,
    key_env, reasoning, source, verified}]`` — one row per chat-provider
    model in ``canon.pricing.LLM``. ``available`` = the provider's key env
    var is set (non-empty) in ``environ`` (default ``os.environ``); the
    picker renders a missing key disabled-with-a-reason naming ``key_env``."""
    from canon import pricing

    env = environ if environ is not None else os.environ
    providers = list(dict.fromkeys([*registrars().keys(), *BackendRegistry.chat_ids()]))
    envs = key_envs()
    out: list[dict[str, Any]] = []
    for model_id, row in pricing.LLM.items():
        provider = row.get("provider")
        if provider not in providers:
            continue
        key_env = envs.get(provider)
        out.append(
            {
                "id": model_id,
                "provider": provider,
                "label": _label(model_id),
                "input_per_1m": row.get("input_per_1m"),
                "output_per_1m": row.get("output_per_1m"),
                "available": bool(key_env and env.get(key_env)),
                "key_env": key_env,
                "reasoning": bool(row.get("reasoning", REASONING.get(provider, False))),
                "source": row.get("source"),
                "verified": row.get("verified"),
                "note": row.get("note"),
            }
        )
    return out


__all__ = ["REASONING", "key_envs", "list_models", "registrars", "resolve_chat_backend"]
