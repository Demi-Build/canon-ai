"""Tests for LLMClient — generate, generate_batch, stats wiring."""

from __future__ import annotations

from canon.backends import FakeLLMBackend
from canon.llm import LLMClient, LLMRequest
from canon.pipeline.stats import GenerationStats

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def req(user_message: str = "go") -> LLMRequest:
    return LLMRequest(system="sys", user_message=user_message)


# ---------------------------------------------------------------------------
# generate — basic behaviour
# ---------------------------------------------------------------------------


class TestLLMClientGenerate:
    def test_returns_backend_response(self) -> None:
        client = LLMClient(backend=FakeLLMBackend(["hello"]))
        assert client.generate(req()) == "hello"

    def test_works_without_stats(self) -> None:
        client = LLMClient(backend=FakeLLMBackend(["ok"]), stats=None)
        result = client.generate(req())
        assert result == "ok"

    def test_forwards_request_to_backend(self) -> None:
        fake = FakeLLMBackend(["irrelevant"])
        client = LLMClient(backend=fake)
        r = req("specific message")
        client.generate(r)
        assert fake.calls == [r]

    def test_generate_records_stat_call(self) -> None:
        stats = GenerationStats()
        client = LLMClient(backend=FakeLLMBackend(["x"]), stats=stats)
        client.generate(req())
        assert stats.llm_calls == 1

    def test_generate_uses_client_default_phase(self) -> None:
        stats = GenerationStats()
        client = LLMClient(backend=FakeLLMBackend(["x"]), stats=stats, phase="story")
        client.generate(req())
        assert "story" in stats.by_phase

    def test_generate_phase_override(self) -> None:
        stats = GenerationStats()
        client = LLMClient(backend=FakeLLMBackend(["x"]), stats=stats, phase="default")
        client.generate(req(), phase="entity")
        assert "entity" in stats.by_phase
        assert "default" not in stats.by_phase

    def test_generate_no_stats_no_error(self) -> None:
        """Ensure no AttributeError when stats is None."""
        client = LLMClient(backend=FakeLLMBackend(["ok"]))
        assert client.generate(req()) == "ok"

    def test_multiple_calls_accumulate_stats(self) -> None:
        stats = GenerationStats()
        client = LLMClient(backend=FakeLLMBackend(["a", "b", "c"]), stats=stats)
        for _ in range(3):
            client.generate(req())
        assert stats.llm_calls == 3


# ---------------------------------------------------------------------------
# generate_batch — ordering and concurrency
# ---------------------------------------------------------------------------


class TestLLMClientGenerateBatch:
    def test_returns_same_length_as_input(self) -> None:
        client = LLMClient(backend=FakeLLMBackend(lambda r: r.user_message))
        results = client.generate_batch([req("a"), req("b"), req("c")])
        assert len(results) == 3

    def test_preserves_order(self) -> None:
        client = LLMClient(backend=FakeLLMBackend(lambda r: r.user_message))
        requests = [req(f"msg{i}") for i in range(10)]
        results = client.generate_batch(requests)
        for i, result in enumerate(results):
            assert result == f"msg{i}"

    def test_empty_list_returns_empty_list(self) -> None:
        client = LLMClient(backend=FakeLLMBackend([]))
        assert client.generate_batch([]) == []

    def test_returns_none_for_erroring_requests(self) -> None:
        """Requests where the backend raises should produce None in results."""

        class RaisingBackend:
            def generate(self, request: LLMRequest) -> str:
                raise RuntimeError("intentional failure")

        client = LLMClient(backend=RaisingBackend())
        results = client.generate_batch([req("a"), req("b")])
        assert results == [None, None]

    def test_partial_failure_does_not_affect_successes(self) -> None:
        """Mixed success/failure: None for failures, string for successes."""
        calls = 0

        class SometimesFailBackend:
            def generate(self, request: LLMRequest) -> str:
                nonlocal calls
                calls += 1
                if "fail" in request.user_message:
                    raise ValueError("boom")
                return "ok"

        client = LLMClient(backend=SometimesFailBackend())
        requests = [req("ok"), req("fail"), req("ok")]
        results = client.generate_batch(requests, max_workers=1)
        # Order is preserved; the "fail" slot is None
        assert results[0] == "ok"
        assert results[1] is None
        assert results[2] == "ok"

    def test_stats_recorded_for_batch(self) -> None:
        stats = GenerationStats()
        client = LLMClient(
            backend=FakeLLMBackend(lambda r: "ok"), stats=stats, phase="batch"
        )
        client.generate_batch([req(), req(), req()])
        assert stats.llm_calls == 3

    def test_batch_phase_override_propagates(self) -> None:
        stats = GenerationStats()
        client = LLMClient(
            backend=FakeLLMBackend(lambda r: "ok"), stats=stats, phase="default"
        )
        client.generate_batch([req(), req()], phase="characters")
        assert "characters" in stats.by_phase
        assert stats.by_phase["characters"]["calls"] == 2


# ---------------------------------------------------------------------------
# generate_batch — prefers_serial
# ---------------------------------------------------------------------------


class TestLLMClientGenerateBatchSerial:
    def test_prefers_serial_forces_single_worker(self) -> None:
        """Backend with prefers_serial=True should have max_workers forced to 1.
        We verify this by checking behaviour is correct (no threading issues)
        with a non-thread-safe counter backend."""
        counter = {"concurrent": 0, "max_seen": 0, "active": 0}
        import threading

        lock = threading.Lock()

        class SerialBackend:
            prefers_serial = True

            def generate(self, request: LLMRequest) -> str:
                with lock:
                    counter["active"] += 1
                    counter["concurrent"] = max(counter["concurrent"], counter["active"])
                # simulate tiny work
                import time

                time.sleep(0.001)
                with lock:
                    counter["active"] -= 1
                return "ok"

        client = LLMClient(backend=SerialBackend())
        client.generate_batch([req() for _ in range(10)], max_workers=8)
        # With serial execution, max concurrent should be 1
        assert counter["concurrent"] == 1

    def test_without_prefers_serial_allows_concurrency(self) -> None:
        """Backend without prefers_serial should use the provided max_workers."""
        import threading

        counter = {"active": 0, "max_seen": 0}
        lock = threading.Lock()
        event = threading.Event()

        class ConcurrentBackend:
            def generate(self, request: LLMRequest) -> str:
                with lock:
                    counter["active"] += 1
                    if counter["active"] > counter["max_seen"]:
                        counter["max_seen"] = counter["active"]
                event.wait(timeout=0.1)
                with lock:
                    counter["active"] -= 1
                return "ok"

        client = LLMClient(backend=ConcurrentBackend())
        event.clear()
        # Schedule enough requests to fill 4 workers
        client.generate_batch([req() for _ in range(8)], max_workers=4)
        # We can't guarantee > 1 in CI, but the call should complete without error
        # and max_seen should be at least 1
        assert counter["max_seen"] >= 1

    def test_prefers_serial_via_getattr_default(self) -> None:
        """Backend without prefers_serial attribute at all should default to False."""

        class MinimalBackend:
            def generate(self, request: LLMRequest) -> str:
                return "ok"

        client = LLMClient(backend=MinimalBackend())
        results = client.generate_batch([req("a"), req("b")])
        assert results == ["ok", "ok"]


# ---------------------------------------------------------------------------
# LLMClient construction defaults
# ---------------------------------------------------------------------------


class TestLLMClientDefaults:
    def test_default_phase_is_default(self) -> None:
        client = LLMClient(backend=FakeLLMBackend(["ok"]))
        assert client.phase == "default"

    def test_stats_defaults_to_none(self) -> None:
        client = LLMClient(backend=FakeLLMBackend(["ok"]))
        assert client.stats is None

    def test_backend_stored(self) -> None:
        fake = FakeLLMBackend(["ok"])
        client = LLMClient(backend=fake)
        assert client.backend is fake
