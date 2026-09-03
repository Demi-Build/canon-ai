"""Anthropic Claude backend for canon-ai.

This module provides ``AnthropicBackend``, a ``LLMBackend`` implementation
that uses the Anthropic Python SDK to call Claude models.

Registration is **explicit** — the ``anthropic`` package is an optional
dependency, so this module never auto-registers itself. Call ``register()``
to make this backend available via ``BackendRegistry.llm("anthropic")``.

Example::

    from canon.backends.anthropic import AnthropicBackend, register

    register()  # now BackendRegistry.llm("anthropic") works

    # Or use directly:
    backend = AnthropicBackend()
    response = backend.generate(LLMRequest(system="You are a writer.", user_message="Tell a story."))

# TODO(v0.2): prompt caching support — pass cache_control on system + examples for cost optimization
# TODO(v0.2): streaming support — return generator from generate_stream()
Pricing: ``PRICING`` is a per-token VIEW of ``canon.pricing.LLM`` (the
product's only price source, master §3.0-C, row P0-7) — same name, same
``{model: {"input", "output"}}`` per-token shape as before, built at import
so existing readers keep working; a model bump lands in ``canon.pricing``,
never here. Token counts are provider-reported, so ``last_cost`` is
``measured`` (P0 paper P.8.8: counts × the table).
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from canon import pricing as _pricing
from canon.backends.registry import BackendRegistry
from canon.llm.request import LLMRequest

if TYPE_CHECKING:
    from anthropic import Anthropic  # type: ignore[import]

logger = logging.getLogger(__name__)


# Default model: latest Sonnet for cost/quality balance.
# Per CLAUDE.md guidance, default to latest capable models for AI app builders.
DEFAULT_MODEL = "claude-sonnet-4-6"

#: Per-token ``{model: {"input", "output"}}`` for every Anthropic row of
#: ``canon.pricing.LLM`` — the re-export view (see the module docstring).
PRICING = _pricing.llm_per_token_view("anthropic")


class AnthropicBackend:
    """Claude LLM backend.

    Implements ``canon.backends.base.LLMBackend``.

    Costs are reported via ``last_cost``, ``last_input_tokens``,
    ``last_output_tokens`` after each ``generate()`` call. ``LLMClient``
    (or any orchestrator) can read these to wire into ``GenerationStats``.

    Args:
        model: The Claude model identifier. Defaults to ``DEFAULT_MODEL``
            (``claude-sonnet-4-6``).
        api_key: Anthropic API key. Falls back to the ``ANTHROPIC_API_KEY``
            environment variable if not provided.
        client: An already-constructed ``Anthropic`` client instance. Useful
            for testing (pass a mock) or to share a client across backends.
    """

    prefers_serial: bool = False
    #: Honors ``LLMRequest.model`` — the per-agent model table
    #: (canon.llm.client model_resolver) only applies to backends that
    #: declare this, so fake/test backends stay untouched.
    supports_request_model: bool = True

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        client: Anthropic | None = None,
    ) -> None:
        try:
            from anthropic import Anthropic
        except ImportError as e:
            raise ImportError(
                "AnthropicBackend requires the `anthropic` package. "
                "Install with: pip install canon-ai[anthropic]"
            ) from e

        self.model = model
        self._client = client or Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        self.last_input_tokens: int = 0
        self.last_output_tokens: int = 0
        self.last_cost: float = 0.0
        #: Token counts are provider-reported: counts × the table = measured.
        self.last_cost_accuracy: str = _pricing.MEASURED
        self._unpriced_models: set[str] = set()

    def generate(self, request: LLMRequest) -> str:
        """Send a request to Claude and return the text response.

        Translates ``LLMRequest`` fields to the Anthropic messages format:
        - ``request.system`` -> ``system`` parameter
        - ``request.examples`` -> alternating user/assistant messages
        - ``request.user_message`` -> final user message
        - ``request.max_tokens`` -> ``max_tokens`` parameter

        Token usage and cost are stored on ``last_input_tokens``,
        ``last_output_tokens``, and ``last_cost`` after each call.

        Args:
            request: The fully-formed LLMRequest to send.

        Returns:
            The model's text response as a plain string. Returns an empty
            string if the response contains no text blocks.
        """
        messages = []
        for user_msg, assistant_msg in request.examples:
            messages.append({"role": "user", "content": user_msg})
            messages.append({"role": "assistant", "content": assistant_msg})
        messages.append({"role": "user", "content": request.user_message})

        # Per-request model (the per-agent model table sets this via
        # LLMClient's resolver); None falls back to the constructed model.
        model = getattr(request, "model", None) or self.model
        response = self._client.messages.create(
            model=model,
            max_tokens=request.max_tokens,
            system=request.system,
            messages=messages,
        )

        # Cost tracking — priced on the model that actually served the
        # call. An unpriced model must be LOUD (once): a silent $0 makes
        # cost reports lie.
        self.last_input_tokens = response.usage.input_tokens
        self.last_output_tokens = response.usage.output_tokens
        pricing = PRICING.get(model)
        if pricing is None:
            if model not in self._unpriced_models:
                self._unpriced_models.add(model)
                logger.warning(
                    "No PRICING entry for model %r — its calls report as "
                    "$0 (accuracy %r). Add it to canon.pricing.LLM.",
                    model, _pricing.ESTIMATED,
                )
            pricing = {"input": 0.0, "output": 0.0}
            self.last_cost_accuracy = _pricing.ESTIMATED
        else:
            self.last_cost_accuracy = _pricing.MEASURED
        self.last_cost = (
            self.last_input_tokens * pricing["input"]
            + self.last_output_tokens * pricing["output"]
        )

        # Anthropic returns content as a list of blocks; return text from the first text block.
        for block in response.content:
            if hasattr(block, "text"):
                return block.text
        return ""


def register() -> None:
    """Register AnthropicBackend with BackendRegistry under the name 'anthropic'.

    Call this once at application startup (after installing ``canon-ai[anthropic]``)
    to make ``BackendRegistry.llm("anthropic")`` available.

    This function is intentionally NOT called at import time. Canon does not
    hard-depend on the ``anthropic`` package, so registration is opt-in.

    Example::

        from canon.backends.anthropic import register
        register()

        from canon.backends import BackendRegistry
        backend = BackendRegistry.llm("anthropic")
    """
    BackendRegistry.register_llm("anthropic", lambda: AnthropicBackend())
