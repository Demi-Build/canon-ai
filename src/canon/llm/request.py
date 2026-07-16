"""LLMRequest — value type for all LLM calls in the canon pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LLMRequest:
    """Value type passed to LLMBackend.generate().

    Attributes:
        system: System-prompt string. Sets the model's persona/task framing.
        user_message: The user turn. Usually the generation task.
        examples: Few-shot (user, assistant) pairs inserted before the user
            turn. Order is preserved; earlier pairs appear first.
        max_tokens: Hard cap on response length. Backends may impose their own
            lower cap; callers should not assume the full budget is used.
        model: Optional per-request model id. Honored only by backends that
            declare ``supports_request_model`` (e.g. AnthropicBackend);
            others use their constructed model. ``None`` = backend default.
            Set by ``LLMClient``'s model resolver (per-agent model table) —
            callers rarely set it directly.
    """

    system: str
    user_message: str
    examples: list[tuple[str, str]] = field(default_factory=list)
    max_tokens: int = 1024
    model: str | None = None
