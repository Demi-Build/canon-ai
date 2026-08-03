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
from dataclasses import replace

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
        model_resolver: Optional callable ``(phase_label) -> model_id |
            None`` — the per-agent model table (PRD §9.1). Consulted per
            call, but only when the backend declares
            ``supports_request_model`` (so a fake backend's runs — and
            their provenance stamps — are untouched). An explicit
            ``request.model`` always wins over the resolver.
        system_overrides: Optional ``{phase-label-prefix: system_text}`` map.
            Per-call system-prompt overrides (the "edit prompt" feature): when
            a registered key matches a call's effective phase label (exact, or
            a ``key:``-bounded prefix so ``plat:layout`` matches
            ``plat:layout:l1`` but never ``plat:layout_extra``), that call's
            ``request.system`` is swapped for the override text before the
            backend runs — keyed by label so a level generate's ``plat:layout``
            override never touches its ``plat:placement`` call. Empty map (the
            default) → byte-identical to no override.

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
        model_resolver=None,
        system_overrides: dict[str, str] | None = None,
    ) -> None:
        self.backend = backend
        self.stats = stats
        self.phase = phase  # default phase label for stats wiring; can be overridden per-call
        self.model_resolver = model_resolver
        # {phase-label-prefix -> system text}. Per-call system-prompt overrides
        # applied in generate(); empty = no override (byte-identical default).
        self.system_overrides: dict[str, str] = dict(system_overrides or {})

    def _override_for(self, eff_phase: str) -> str | None:
        """The override system text whose key best matches ``eff_phase``, or
        None. A key matches when it equals the phase or is a ``key:``-bounded
        prefix of it; the LONGEST matching key wins (most specific)."""
        if not self.system_overrides:
            return None
        best: str | None = None
        for key in self.system_overrides:
            if eff_phase == key or eff_phase.startswith(key + ":"):
                if best is None or len(key) > len(best):
                    best = key
        return self.system_overrides[best] if best is not None else None

    def generate(self, request: LLMRequest, *, phase: str | None = None) -> str:
        """Generate a single response.

        Calls ``backend.generate(request)`` and, if ``stats`` was provided,
        records the call under ``phase`` (or the client's default phase).

        When a ``system_overrides`` entry matches the effective phase label,
        this call's system prompt is swapped for the override text (the
        per-call "edit prompt" feature). The swap is applied to a COPY of the
        request, so the caller's object is never mutated.

        Args:
            request: The ``LLMRequest`` to send.
            phase: Optional per-call phase label. Overrides the client's
                default ``self.phase`` for this call only.

        Returns:
            The backend's text response.

        Note:
            Token/cost values are read from the backend's ``last_*`` attributes
            after each call (set by real backends like ``AnthropicBackend``).
            Backends that don't surface them (e.g. ``FakeLLMBackend``) report
            zeros via ``getattr`` defaults.
        """
        eff_phase = phase or self.phase
        if (
            self.model_resolver is not None
            and getattr(self.backend, "supports_request_model", False)
            and getattr(request, "model", None) is None
        ):
            request.model = self.model_resolver(eff_phase)
        override = self._override_for(eff_phase)
        if override is not None:
            request = replace(request, system=override)
        response = self.backend.generate(request)
        if self.stats is not None:
            self.stats.record_call(
                phase=phase or self.phase,
                input_tokens=getattr(self.backend, "last_input_tokens", 0),
                output_tokens=getattr(self.backend, "last_output_tokens", 0),
                cost=getattr(self.backend, "last_cost", 0.0),
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
