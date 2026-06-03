"""Tests for BackendRegistry, FakeLLMBackend, and backend protocol conformance."""

from __future__ import annotations

import pytest

from canon.backends import BackendRegistry, FakeLLMBackend, LLMBackend
from canon.llm.request import LLMRequest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_request(user_message: str = "hello") -> LLMRequest:
    return LLMRequest(system="test system", user_message=user_message)


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestLLMBackendProtocol:
    def test_fake_satisfies_protocol(self) -> None:
        fake = FakeLLMBackend(["ok"])
        assert isinstance(fake, LLMBackend)

    def test_custom_class_satisfies_protocol(self) -> None:
        class MyBackend:
            def generate(self, request: LLMRequest) -> str:
                return "custom"

        backend = MyBackend()
        assert isinstance(backend, LLMBackend)

    def test_object_without_generate_does_not_satisfy_protocol(self) -> None:
        class NotABackend:
            pass

        assert not isinstance(NotABackend(), LLMBackend)


# ---------------------------------------------------------------------------
# BackendRegistry — LLM
# ---------------------------------------------------------------------------


class TestBackendRegistryLLM:
    def setup_method(self) -> None:
        BackendRegistry.reset()

    def teardown_method(self) -> None:
        BackendRegistry.reset()

    def test_register_and_retrieve(self) -> None:
        BackendRegistry.register_llm("fake", lambda: FakeLLMBackend(["hi"]))
        backend = BackendRegistry.llm("fake")
        assert isinstance(backend, FakeLLMBackend)

    def test_retrieval_caches_instance(self) -> None:
        BackendRegistry.register_llm("fake", lambda: FakeLLMBackend(["hi"]))
        first = BackendRegistry.llm("fake")
        second = BackendRegistry.llm("fake")
        assert first is second

    def test_unknown_name_raises_key_error(self) -> None:
        with pytest.raises(KeyError, match="nonexistent"):
            BackendRegistry.llm("nonexistent")

    def test_re_register_clears_cached_instance(self) -> None:
        BackendRegistry.register_llm("fake", lambda: FakeLLMBackend(["first"]))
        first = BackendRegistry.llm("fake")
        BackendRegistry.register_llm("fake", lambda: FakeLLMBackend(["second"]))
        second = BackendRegistry.llm("fake")
        assert first is not second
        assert second.generate(make_request()) == "second"

    def test_reset_clears_registrations(self) -> None:
        BackendRegistry.register_llm("fake", lambda: FakeLLMBackend(["hi"]))
        BackendRegistry.reset()
        with pytest.raises(KeyError):
            BackendRegistry.llm("fake")

    def test_reset_clears_instances(self) -> None:
        BackendRegistry.register_llm("fake", lambda: FakeLLMBackend(["hi"]))
        _ = BackendRegistry.llm("fake")  # prime the cache
        BackendRegistry.reset()
        # After reset the registry is completely empty
        with pytest.raises(KeyError):
            BackendRegistry.llm("fake")

    def test_factory_callable_receives_no_args(self) -> None:
        """Factory must be zero-arg; class itself works as a factory if constructable."""
        call_log: list[int] = []

        def factory() -> FakeLLMBackend:
            call_log.append(1)
            return FakeLLMBackend(["ok"])

        BackendRegistry.register_llm("counted", factory)
        BackendRegistry.llm("counted")
        BackendRegistry.llm("counted")  # second call — should hit cache, not factory
        assert len(call_log) == 1  # factory called exactly once

    def test_multiple_backends_coexist(self) -> None:
        BackendRegistry.register_llm("a", lambda: FakeLLMBackend(["a"]))
        BackendRegistry.register_llm("b", lambda: FakeLLMBackend(["b"]))
        assert BackendRegistry.llm("a").generate(make_request()) == "a"
        assert BackendRegistry.llm("b").generate(make_request()) == "b"


# ---------------------------------------------------------------------------
# BackendRegistry — image
# ---------------------------------------------------------------------------


class TestBackendRegistryImage:
    def setup_method(self) -> None:
        BackendRegistry.reset()

    def teardown_method(self) -> None:
        BackendRegistry.reset()

    def test_unknown_image_backend_raises_key_error(self) -> None:
        with pytest.raises(KeyError, match="fal"):
            BackendRegistry.image("fal")

    def test_register_and_retrieve_image_backend(self) -> None:
        class MinimalImageBackend:
            def generate(self, prompt: str, width: int, height: int) -> bytes:
                return b"image"

            async def generate_async(self, prompt: str, width: int, height: int) -> bytes:
                return b"image"

            def generate_and_save(
                self, prompt: str, filepath: str, width: int, height: int
            ) -> bool:
                return True

            async def generate_and_save_async(
                self, prompt: str, filepath: str, width: int, height: int
            ) -> bool:
                return True

        BackendRegistry.register_image("fake_img", MinimalImageBackend)
        img_backend = BackendRegistry.image("fake_img")
        assert img_backend is BackendRegistry.image("fake_img")  # cached


# ---------------------------------------------------------------------------
# FakeLLMBackend — list mode
# ---------------------------------------------------------------------------


class TestFakeLLMBackendListMode:
    def test_returns_responses_in_order(self) -> None:
        fake = FakeLLMBackend(["first", "second", "third"])
        assert fake.generate(make_request()) == "first"
        assert fake.generate(make_request()) == "second"
        assert fake.generate(make_request()) == "third"

    def test_raises_index_error_when_exhausted(self) -> None:
        fake = FakeLLMBackend(["only"])
        fake.generate(make_request())
        with pytest.raises(IndexError):
            fake.generate(make_request())

    def test_single_response(self) -> None:
        fake = FakeLLMBackend(["hello"])
        assert fake.generate(make_request()) == "hello"

    def test_records_all_calls(self) -> None:
        fake = FakeLLMBackend(["a", "b"])
        req1 = make_request("one")
        req2 = make_request("two")
        fake.generate(req1)
        fake.generate(req2)
        assert fake.calls == [req1, req2]


# ---------------------------------------------------------------------------
# FakeLLMBackend — dict mode
# ---------------------------------------------------------------------------


class TestFakeLLMBackendDictMode:
    def test_matches_by_user_message_substring(self) -> None:
        fake = FakeLLMBackend({"dragon": "Here be dragons.", "sword": "A fine blade."})
        r = fake.generate(make_request("Tell me about the dragon in the cave."))
        assert r == "Here be dragons."

    def test_matches_second_key(self) -> None:
        fake = FakeLLMBackend({"dragon": "Here be dragons.", "sword": "A fine blade."})
        r = fake.generate(make_request("Describe the sword."))
        assert r == "A fine blade."

    def test_raises_key_error_when_no_key_matches(self) -> None:
        fake = FakeLLMBackend({"dragon": "Here be dragons."})
        with pytest.raises(KeyError, match="FakeLLMBackend"):
            fake.generate(make_request("What about a potion?"))

    def test_records_call_before_raising(self) -> None:
        fake = FakeLLMBackend({"dragon": "Here be dragons."})
        req = make_request("What about a potion?")
        with pytest.raises(KeyError):
            fake.generate(req)
        assert len(fake.calls) == 1
        assert fake.calls[0] is req


# ---------------------------------------------------------------------------
# FakeLLMBackend — callable mode
# ---------------------------------------------------------------------------


class TestFakeLLMBackendCallableMode:
    def test_callable_receives_request(self) -> None:
        received: list[LLMRequest] = []

        def handler(req: LLMRequest) -> str:
            received.append(req)
            return f"echo:{req.user_message}"

        fake = FakeLLMBackend(handler)
        req = make_request("ping")
        result = fake.generate(req)
        assert result == "echo:ping"
        assert received == [req]

    def test_lambda_callable(self) -> None:
        fake = FakeLLMBackend(lambda req: req.user_message.upper())
        assert fake.generate(make_request("hello")) == "HELLO"

    def test_records_calls_in_callable_mode(self) -> None:
        fake = FakeLLMBackend(lambda req: "ok")
        req = make_request("test")
        fake.generate(req)
        assert fake.calls == [req]


# ---------------------------------------------------------------------------
# FakeLLMBackend — .calls accumulation
# ---------------------------------------------------------------------------


class TestFakeLLMBackendCallsAccumulation:
    def test_calls_starts_empty(self) -> None:
        fake = FakeLLMBackend(["ok"])
        assert fake.calls == []

    def test_every_generate_appends_to_calls(self) -> None:
        fake = FakeLLMBackend(["a", "b", "c"])
        reqs = [make_request(f"msg{i}") for i in range(3)]
        for r in reqs:
            fake.generate(r)
        assert fake.calls == reqs

    def test_calls_appended_even_if_error_follows(self) -> None:
        """Dict-mode: call recorded before KeyError is raised."""
        fake = FakeLLMBackend({})
        req = make_request("anything")
        with pytest.raises(KeyError):
            fake.generate(req)
        assert fake.calls == [req]
