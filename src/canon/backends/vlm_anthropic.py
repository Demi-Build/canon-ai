"""Anthropic Claude vision-judge backend for canon-ai.

``AnthropicVLMBackend`` implements ``canon.backends.base.VLMBackend``: it
sends PNG images plus judgment instructions to a vision-capable Claude
model and returns the text verdict. It never generates assets — judging
is its whole job (the platformer pack's ``plat:vlm_qa`` phase is the
first consumer).

Like the other paid backends, this module never auto-registers and the
``anthropic`` package stays an optional dependency. Credential checks are
the *caller's* job at construction time (``build_vlm_judge`` fails fast
before any generation money is spent); the SDK would otherwise defer the
failure to the first call.
"""

from __future__ import annotations

import base64
import os
from typing import TYPE_CHECKING

from canon import pricing as _pricing
from canon.backends.anthropic import DEFAULT_MODEL, PRICING

if TYPE_CHECKING:
    from anthropic import Anthropic  # type: ignore[import]


class AnthropicVLMBackend:
    """Claude vision-judge backend.

    Implements ``canon.backends.base.VLMBackend``. Costs are reported via
    ``last_cost`` / ``last_input_tokens`` / ``last_output_tokens`` after
    each ``judge()`` call, same contract as ``AnthropicBackend``.

    Args:
        model: Vision-capable Claude model id. Defaults to the text
            backend's ``DEFAULT_MODEL`` (all current Claude models accept
            images).
        api_key: Anthropic API key; falls back to ``ANTHROPIC_API_KEY``.
        client: Pre-built ``Anthropic`` client (testing / client sharing).
    """

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
                "AnthropicVLMBackend requires the `anthropic` package. "
                "Install with: pip install canon-ai[anthropic]"
            ) from e

        self.model = model
        self._client = client or Anthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY")
        )
        self.last_input_tokens: int = 0
        self.last_output_tokens: int = 0
        self.last_cost: float = 0.0
        #: Token counts are provider-reported: counts × the table = measured
        #: (P0 paper P.8.8); an unpriced model flips it to ``estimated``.
        self.last_cost_accuracy: str = _pricing.MEASURED

    def judge(self, prompt: str, images: list[bytes], max_tokens: int = 1024) -> str:
        """Send PNG images + instructions to Claude, return the verdict text."""
        content: list[dict] = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.standard_b64encode(data).decode("ascii"),
                },
            }
            for data in images
        ]
        content.append({"type": "text", "text": prompt})

        response = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": content}],
        )

        self.last_input_tokens = response.usage.input_tokens
        self.last_output_tokens = response.usage.output_tokens
        pricing = PRICING.get(self.model)
        self.last_cost_accuracy = _pricing.MEASURED if pricing else _pricing.ESTIMATED
        pricing = pricing or {"input": 0.0, "output": 0.0}
        self.last_cost = (
            self.last_input_tokens * pricing["input"]
            + self.last_output_tokens * pricing["output"]
        )

        for block in response.content:
            if hasattr(block, "text"):
                return block.text
        return ""
