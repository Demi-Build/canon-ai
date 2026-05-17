"""LLMClient — user-facing facade for LLM generation.

Wraps any ``LLMBackend``, wires ``GenerationStats``, and provides concurrent
batching via a thread pool.

# TODO(v0.2): Token-accurate stats accounting. Currently LLMClient zeros
#             input/output tokens; real backends (AnthropicBackend) should
#             override or wrap the response to record real counts. Refactor
#             when adding multi-backend cost-comparison features.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from canon.backends.base import LLMBackend
from canon.llm.request import LLMRequest
from canon.pipeline.stats import GenerationStats


class LLMClient:
    """User-facing facade. Wraps a backend, wires generation stats, and
    provides concurrent batching.

    Args:
        backend: Any object satisfying the ``LLMBackend`` protocol.
        stats: Optional ``GenerationStats`` instance. When provided, every
            successful ``generate`` call records a call via
            ``stats.record_call()``.
        phase: Default phase label used for stats recording. Override
            per-call via the ``phase`` keyword argument on ``generate`` /
            ``generate_batch``.

    Example::

        from canon.backends import FakeLLMBackend
        from canon.llm import LLMClient, LLMRequest
        from canon.pipeline.stats import GenerationStats

        stats = GenerationStats()
        client = LLMClient(backend=FakeLLMBackend(["ok"]), stats=stats, phase="story")
        result = client.generate(LLMRequest(system="s", user_message="go"))
        assert stats.llm_calls == 1
    """

    def __init__(
        self,
        backend: LLMBackend,
        stats: GenerationStats | None = None,
        phase: str = "default",
    ) -> None:
        self.backend = backend
        self.stats = stats
        self.phase = phase  # default phase label for stats wiring; can be overridden per-call

    def generate(self, request: LLMRequest, *, phase: str | None = None) -> str:
        """Generate a single response.

        Calls ``backend.generate(request)`` and, if ``stats`` was provided,
        records the call under ``phase`` (or the client's default phase).

        Args:
            request: The ``LLMRequest`` to send.
            phase: Optional per-call phase label. Overrides the client's
                default ``self.phase`` for this call only.

        Returns:
            The backend's text response.

        Note:
            Token counts are recorded as ``0`` in v0.1. Backends responsible
            for token-accurate accounting should wrap ``generate`` or use the
            v0.2 stats refactor when it lands.
        """
        response = self.backend.generate(request)
        if self.stats is not None:
            self.stats.record_call(
                phase=phase or self.phase,
                # v0.1: protocol-only backends don't surface token counts.
                # Real backends (AnthropicBackend) will provide accurate values
                # in v0.2 via a stats-aware wrapper.
                input_tokens=0,
                output_tokens=0,
                cost=0.0,
            )
        return response

    def generate_batch(
        self,
        requests: list[LLMRequest],
        max_workers: int = 8,
        phase: str | None = None,
    ) -> list[str | None]:
        """Concurrent batch generation. Returns one response per request.

        Runs requests concurrently via a ``ThreadPoolExecutor``. If a request
        raises an exception, the corresponding slot in the result list is
        ``None`` (the exception is swallowed). Successful responses are placed
        at the same index as the input request, preserving order.

        If ``backend.prefers_serial`` is truthy (checked via ``getattr`` to
        support backends that don't declare the attribute), ``max_workers`` is
        forced to ``1`` regardless of the argument.

        Args:
            requests: List of ``LLMRequest`` objects to process.
            max_workers: Thread-pool size. Ignored if ``backend.prefers_serial``
                is set.
            phase: Phase label propagated to ``generate`` for stats recording.

        Returns:
            A list of ``str | None`` the same length as ``requests``. Each
            element is the response string on success or ``None`` on error.
        """
        if getattr(self.backend, "prefers_serial", False):
            max_workers = 1

        results: list[str | None] = [None] * len(requests)
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {
                ex.submit(self.generate, req, phase=phase): i
                for i, req in enumerate(requests)
            }
            for future in futures:
                i = futures[future]
                try:
                    results[i] = future.result()
                except Exception:
                    results[i] = None

        return results
