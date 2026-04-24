"""Generation statistics — cost and call tracking."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GenerationStats:
    """Tracks LLM calls, tokens, and costs across the pipeline."""

    llm_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost: float = 0.0
    image_attempts: int = 0
    image_successes: int = 0
    by_phase: dict[str, dict] = field(default_factory=dict)

    def record_call(
        self,
        phase: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost: float = 0.0,
    ) -> None:
        self.llm_calls += 1
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_cost += cost

        if phase not in self.by_phase:
            self.by_phase[phase] = {
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost": 0.0,
            }
        self.by_phase[phase]["calls"] += 1
        self.by_phase[phase]["input_tokens"] += input_tokens
        self.by_phase[phase]["output_tokens"] += output_tokens
        self.by_phase[phase]["cost"] += cost

    def to_dict(self) -> dict:
        return {
            "llm_calls": self.llm_calls,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cost": self.total_cost,
            "image_attempts": self.image_attempts,
            "image_successes": self.image_successes,
            "by_phase": self.by_phase,
        }
