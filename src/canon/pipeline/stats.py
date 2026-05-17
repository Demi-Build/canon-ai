"""Generation statistics — cost and call tracking."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GenerationStats:
    """Tracks LLM calls, tokens, costs, and asset generation across the pipeline.

    Existing fields (v0.1 — preserved for backward compat):
        llm_calls, total_input_tokens, total_output_tokens, total_cost,
        image_attempts, image_successes, by_phase

    New v0.2 fields:
        llm_backend, image_backend, music_backend, sfx_backend,
        music_attempted, music_succeeded, sfx_attempted, sfx_succeeded,
        llm_cost_usd, image_cost_usd, audio_cost_usd, generation_time_seconds
    """

    # -----------------------------------------------------------------------
    # Existing v0.1 fields (backward-compat)
    # -----------------------------------------------------------------------
    llm_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost: float = 0.0
    image_attempts: int = 0
    image_successes: int = 0
    by_phase: dict[str, dict] = field(default_factory=dict)

    # -----------------------------------------------------------------------
    # New v0.2 fields
    # -----------------------------------------------------------------------
    llm_backend: str = ""
    image_backend: str = ""
    music_backend: str = ""
    sfx_backend: str = ""
    music_attempted: int = 0
    music_succeeded: int = 0
    sfx_attempted: int = 0
    sfx_succeeded: int = 0
    llm_cost_usd: float = 0.0
    image_cost_usd: float = 0.0
    audio_cost_usd: float = 0.0
    generation_time_seconds: float = 0.0

    # -----------------------------------------------------------------------
    # Computed properties
    # -----------------------------------------------------------------------

    @property
    def total_tokens(self) -> int:
        """Sum of input + output tokens across all LLM calls."""
        return self.total_input_tokens + self.total_output_tokens

    @property
    def total_cost_usd(self) -> float:
        """Sum of llm + image + audio costs in USD."""
        return self.llm_cost_usd + self.image_cost_usd + self.audio_cost_usd

    @property
    def generation_time_human(self) -> str:
        """Human-readable generation time (e.g. '93m 00s')."""
        secs = int(self.generation_time_seconds)
        return f"{secs // 60}m {secs % 60:02d}s"

    # -----------------------------------------------------------------------
    # Mutation helpers
    # -----------------------------------------------------------------------

    def record_call(
        self,
        phase: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost: float = 0.0,
    ) -> None:
        """Record a single LLM call for a given phase."""
        self.llm_calls += 1
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_cost += cost
        self.llm_cost_usd += cost  # v0.2: also track in per-type bucket

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

    # -----------------------------------------------------------------------
    # Serialization
    # -----------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return a dict matching mazeworld's generation_stats.json shape.

        Includes all v0.1 keys (for backward compat with existing tests) and
        all v0.2 keys.  The ``by_phase`` key is canon-only (not in mazeworld's
        shape) but is present for debugging.
        """
        return {
            # Backend identification (v0.2)
            "llm_backend": self.llm_backend,
            "image_backend": self.image_backend,
            "music_backend": self.music_backend,
            "sfx_backend": self.sfx_backend,
            # LLM counters (v0.1 keys preserved)
            "llm_calls": self.llm_calls,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cost": self.total_cost,
            # v0.2 token summary
            "input_tokens": self.total_input_tokens,
            "output_tokens": self.total_output_tokens,
            "total_tokens": self.total_tokens,
            # Image counters (v0.1 keys preserved)
            "image_attempts": self.image_attempts,
            "image_successes": self.image_successes,
            # Image counters (v0.2 names — mazeworld shape)
            "images_attempted": self.image_attempts,
            "images_succeeded": self.image_successes,
            # Music/SFX counters (v0.2)
            "music_attempted": self.music_attempted,
            "music_succeeded": self.music_succeeded,
            "sfx_attempted": self.sfx_attempted,
            "sfx_succeeded": self.sfx_succeeded,
            # Cost breakdown (v0.2)
            "llm_cost_usd": self.llm_cost_usd,
            "image_cost_usd": self.image_cost_usd,
            "audio_cost_usd": self.audio_cost_usd,
            "total_cost_usd": self.total_cost_usd,
            # Timing (v0.2)
            "generation_time_seconds": self.generation_time_seconds,
            "generation_time_human": self.generation_time_human,
            # Per-phase breakdown (canon-only)
            "by_phase": self.by_phase,
        }
