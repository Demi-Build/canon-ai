"""Generation statistics — cost, call, and run-clock tracking."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

#: The two ``to_dict`` keys that carry the run clock. They are the one part
#: of a stats snapshot a seed cannot reproduce, so a writer that copies the
#: snapshot INTO emitted pack content (the dungeon manifest nests one) drops
#: exactly these; the standalone ``generation_stats.json`` keeps them.
RUN_CLOCK_KEYS: tuple[str, ...] = ("generation_time_seconds", "generation_time_human")

#: Token-metered call lanes → the ``(calls, cost)`` bucket each one fills.
#: A lane is the kind of model the call went to: ``llm`` for text
#: generation, ``vlm`` for a vision judgment. Both are priced per token off
#: the same ``canon.pricing.LLM`` table, so they share the token totals and
#: the per-label ``by_phase`` index; only the roll-up bucket differs, the
#: way image and audio spend already keep their own ``*_cost_usd``.
_LANE_BUCKETS: dict[str, tuple[str, str]] = {
    "llm": ("llm_calls", "llm_cost_usd"),
    "vlm": ("vlm_calls", "vlm_cost_usd"),
}


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

    Vision lane (the VLM judge — motion-spec authoring, sheet verdicts, level
    judgments):
        vlm_backend, vlm_calls, vlm_cost_usd — filled by ``record_call`` with
        ``lane="vlm"``, exactly the way text calls fill the ``llm_*`` bucket.

    Asset failures:
        failures — one record per asset that did not land, appended by the
        asset phases' ``record_asset_failure`` and serialized on every
        snapshot (an empty list on a clean run), so ``generation_stats.json``
        says outright whether anything was lost.

    The run clock: ``start_timer`` / ``stop_timer`` (or the ``run_timer``
    context manager both schedulers wrap a run in) accumulate a monotonic
    elapsed into ``generation_time_seconds``; a snapshot taken while the
    clock runs (the manifest phase writes one mid-run) reads the elapsed so
    far.
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
    # Vision lane — the VLM judge's calls, its own bucket beside llm/image/audio
    # -----------------------------------------------------------------------
    vlm_backend: str = ""
    vlm_calls: int = 0
    vlm_cost_usd: float = 0.0

    # -----------------------------------------------------------------------
    # Asset failures — the durable list the asset phases append to
    # -----------------------------------------------------------------------
    #: ``kind`` / ``target`` / ``path`` plus the classified error, one per
    #: asset that did not land. Always emitted (``failures``), empty when
    #: nothing was lost.
    failures: list[dict] = field(default_factory=list)

    #: The running clock's ``time.monotonic()`` start, ``None`` when stopped.
    #: Process-local bookkeeping — a monotonic reading means nothing outside
    #: this process, so it is never serialized.
    _timer_start: float | None = field(default=None, init=False, repr=False, compare=False)

    # -----------------------------------------------------------------------
    # Computed properties
    # -----------------------------------------------------------------------

    @property
    def total_tokens(self) -> int:
        """Sum of input + output tokens across all token-metered calls."""
        return self.total_input_tokens + self.total_output_tokens

    @property
    def total_cost_usd(self) -> float:
        """Sum of llm + vlm + image + audio costs in USD."""
        return self.llm_cost_usd + self.vlm_cost_usd + self.image_cost_usd + self.audio_cost_usd

    @property
    def generation_time_human(self) -> str:
        """Human-readable generation time (e.g. '93m 00s')."""
        secs = int(self.elapsed_seconds())
        return f"{secs // 60}m {secs % 60:02d}s"

    @property
    def assets_placeholder(self) -> bool:
        """True when any asset backend was a fake/test backend.

        Signals that portraits/music/sfx in the output are silent/blank
        placeholders generated for testing, not real assets.  Consumers can
        check this (or the ``*_backend`` names) to know audio won't play.
        """
        return "fake" in (self.image_backend, self.music_backend, self.sfx_backend)

    # -----------------------------------------------------------------------
    # Mutation helpers
    # -----------------------------------------------------------------------

    def record_call(
        self,
        phase: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost: float = 0.0,
        lane: str = "llm",
    ) -> None:
        """Record a single token-metered model call for a given phase label.

        ``lane`` names the bucket the call rolls up into — ``"llm"`` (the
        default, unchanged) or ``"vlm"`` for a vision judgment. Every lane
        shares the token totals, ``total_cost``, and the per-label
        ``by_phase`` entry; the lane picks which ``*_calls`` / ``*_cost_usd``
        pair it counts under.
        """
        try:
            calls_field, cost_field = _LANE_BUCKETS[lane]
        except KeyError:
            raise ValueError(
                f"unknown call lane {lane!r} (one of {sorted(_LANE_BUCKETS)})"
            ) from None
        setattr(self, calls_field, getattr(self, calls_field) + 1)
        setattr(self, cost_field, getattr(self, cost_field) + cost)
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

    # -----------------------------------------------------------------------
    # The run clock
    # -----------------------------------------------------------------------

    def start_timer(self) -> None:
        """Start the run clock (monotonic). A clock already running keeps
        its start — the elapsed is measured from the FIRST start."""
        if self._timer_start is None:
            self._timer_start = time.monotonic()

    def stop_timer(self) -> float:
        """Stop the run clock, folding its elapsed into
        ``generation_time_seconds``. Accumulates across runs on one stats
        object the way every other counter here does (a two-pass bootstrap
        orchestrates twice). Returns the accumulated total; a stopped clock
        is a no-op."""
        if self._timer_start is not None:
            self.generation_time_seconds += time.monotonic() - self._timer_start
            self._timer_start = None
        return self.generation_time_seconds

    def elapsed_seconds(self) -> float:
        """``generation_time_seconds`` plus, while the clock runs, the time
        since it started — what a snapshot taken mid-run reports."""
        if self._timer_start is None:
            return self.generation_time_seconds
        return self.generation_time_seconds + (time.monotonic() - self._timer_start)

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
            "vlm_backend": self.vlm_backend,
            "image_backend": self.image_backend,
            "music_backend": self.music_backend,
            "sfx_backend": self.sfx_backend,
            # True when assets are silent/blank test placeholders (any
            # fake backend). Real audio is absent in this case.
            "assets_placeholder": self.assets_placeholder,
            # LLM counters (v0.1 keys preserved)
            "llm_calls": self.llm_calls,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cost": self.total_cost,
            # v0.2 token summary
            "input_tokens": self.total_input_tokens,
            "output_tokens": self.total_output_tokens,
            "total_tokens": self.total_tokens,
            # Vision-judge counters (the vlm lane)
            "vlm_calls": self.vlm_calls,
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
            # Cost breakdown (v0.2 + the vlm lane)
            "llm_cost_usd": self.llm_cost_usd,
            "vlm_cost_usd": self.vlm_cost_usd,
            "image_cost_usd": self.image_cost_usd,
            "audio_cost_usd": self.audio_cost_usd,
            "total_cost_usd": self.total_cost_usd,
            # Timing (v0.2) — the elapsed so far while the clock runs
            "generation_time_seconds": self.elapsed_seconds(),
            "generation_time_human": self.generation_time_human,
            # Assets that did not land — always present, empty on a clean run
            "failures": list(self.failures),
            # Per-phase breakdown (canon-only)
            "by_phase": self.by_phase,
        }


@contextmanager
def run_timer(stats: GenerationStats | None) -> Iterator[None]:
    """The ONE run clock both schedulers share: wrap a scheduler run in it
    and ``stats`` carries the run's elapsed — started at ``run_start``,
    stopped and folded into ``generation_time_seconds`` at ``run_end`` on
    every exit (ok, escalated, gated, cancelled, raised). ``None`` (a
    context with no stats) times nothing."""
    if stats is None:
        yield
        return
    stats.start_timer()
    try:
        yield
    finally:
        stats.stop_timer()
