"""Tests for retry_with_feedback token escalation (A6).

Covers:
- Backward-compat: token_escalation=None (default) is identical to v0.1
- default_token_escalation produces correct 1.5x sequence: 1024 → 1536 → 2304
- Token cap: 6144 → 8192 (not 9216)
- Generators that don't accept max_tokens still work (kwarg stripped)
- Generators with **kwargs receive max_tokens correctly
- Generators accepting both feedback and max_tokens receive both
- Custom escalation function: lambda n, prev: prev + 100
"""

from __future__ import annotations

from canon.pipeline.retry import (
    INITIAL_MAX_TOKENS,
    TOKEN_CAP,
    default_token_escalation,
    retry_with_feedback,
)

# ---------------------------------------------------------------------------
# default_token_escalation unit tests
# ---------------------------------------------------------------------------


class TestDefaultTokenEscalation:
    def test_1_5x_per_attempt(self) -> None:
        assert default_token_escalation(1, 1024) == 1536
        assert default_token_escalation(2, 1536) == 2304

    def test_cap_at_token_cap(self) -> None:
        # Starting near the cap — should not exceed TOKEN_CAP
        result = default_token_escalation(1, 6144)
        assert result == TOKEN_CAP  # int(6144 * 1.5) = 9216 → capped to 8192

    def test_at_cap_stays_at_cap(self) -> None:
        result = default_token_escalation(1, TOKEN_CAP)
        assert result == TOKEN_CAP

    def test_small_value_scales(self) -> None:
        result = default_token_escalation(1, 100)
        assert result == 150

    def test_constants_have_expected_values(self) -> None:
        assert INITIAL_MAX_TOKENS == 1024
        assert TOKEN_CAP == 8192


# ---------------------------------------------------------------------------
# Backward compatibility: token_escalation=None (v0.1 behavior)
# ---------------------------------------------------------------------------


class TestRetryBackwardCompat:
    """token_escalation=None must be identical to the v0.1 implementation."""

    def test_passes_first_try(self) -> None:
        result = retry_with_feedback(
            generate_fn=lambda: {"name": "Sword"},
            validate_fn=lambda x: (True, []),
            fallback={"name": "Fallback"},
        )
        assert result["name"] == "Sword"

    def test_retries_on_failure(self) -> None:
        attempts: list = []

        def gen(feedback=None):
            attempts.append(feedback)
            if len(attempts) < 3:
                return {"name": ""}
            return {"name": "Fixed"}

        def validate(data):
            if not data.get("name"):
                return False, ["missing name"]
            return True, []

        result = retry_with_feedback(gen, validate, fallback={"name": "Fallback"})
        assert result["name"] == "Fixed"
        assert len(attempts) == 3

    def test_feedback_passed_to_generator(self) -> None:
        received_feedback: list = []

        def gen(feedback=None):
            received_feedback.append(feedback)
            if feedback:
                return {"name": "Fixed"}
            return {"name": ""}

        def validate(data):
            if not data.get("name"):
                return False, ["name is empty"]
            return True, []

        retry_with_feedback(gen, validate, fallback={})
        assert received_feedback[0] is None
        assert received_feedback[1] == ["name is empty"]

    def test_returns_fallback_on_exhaustion(self) -> None:
        result = retry_with_feedback(
            generate_fn=lambda **kw: {"broken": True},
            validate_fn=lambda x: (False, ["always fails"]),
            fallback={"name": "Fallback"},
            max_retries=2,
        )
        assert result["name"] == "Fallback"

    def test_handles_generator_exception(self) -> None:
        call_count = [0]

        def gen(feedback=None):
            call_count[0] += 1
            if call_count[0] < 3:
                raise ValueError("LLM error")
            return {"ok": True}

        result = retry_with_feedback(
            gen,
            validate_fn=lambda x: (True, []),
            fallback={"ok": False},
        )
        assert result["ok"]


# ---------------------------------------------------------------------------
# Token escalation: correct sequence
# ---------------------------------------------------------------------------


class TestTokenEscalationSequence:
    def test_three_attempts_produce_1_5x_sequence(self) -> None:
        """Attempts 1,2,3 → max_tokens 1024, 1536, 2304."""
        received_tokens: list[int | None] = []

        def gen(feedback=None, max_tokens=None):
            received_tokens.append(max_tokens)
            return {"val": "always fails"}

        retry_with_feedback(
            generate_fn=gen,
            validate_fn=lambda x: (False, ["nope"]),
            fallback={},
            max_retries=3,
            token_escalation=default_token_escalation,
            initial_max_tokens=1024,
        )
        # Attempt 1: initial 1024; attempt 2: 1536; attempt 3: 2304
        assert received_tokens == [1024, 1536, 2304]

    def test_token_cap_respected(self) -> None:
        """Starting at 6144 → 8192 on next attempt (not 9216)."""
        received_tokens: list[int] = []

        def gen(feedback=None, max_tokens=None):
            if max_tokens is not None:
                received_tokens.append(max_tokens)
            return {}

        retry_with_feedback(
            generate_fn=gen,
            validate_fn=lambda x: (False, ["fail"]),
            fallback={},
            max_retries=2,
            token_escalation=default_token_escalation,
            initial_max_tokens=6144,
        )
        assert received_tokens[0] == 6144
        assert received_tokens[1] == TOKEN_CAP  # 8192, not 9216

    def test_custom_escalation_plus_100(self) -> None:
        """lambda n, prev: prev + 100 → 1024, 1124, 1224."""
        received_tokens: list[int] = []

        def gen(feedback=None, max_tokens=None):
            if max_tokens is not None:
                received_tokens.append(max_tokens)
            return {}

        retry_with_feedback(
            generate_fn=gen,
            validate_fn=lambda x: (False, ["fail"]),
            fallback={},
            max_retries=3,
            token_escalation=lambda n, prev: prev + 100,
            initial_max_tokens=1024,
        )
        assert received_tokens == [1024, 1124, 1224]


# ---------------------------------------------------------------------------
# _call_generate: kwarg filtering
# ---------------------------------------------------------------------------


class TestCallGenerateKwargFiltering:
    """Verify the four generator signature cases."""

    def test_generator_accepts_neither_kwarg(self) -> None:
        """Generator with no params still works; kwargs are stripped."""

        def gen():
            return "ok"

        result = retry_with_feedback(
            generate_fn=gen,
            validate_fn=lambda x: (True, []),
            fallback="fallback",
            token_escalation=default_token_escalation,
        )
        assert result == "ok"

    def test_generator_accepts_only_feedback(self) -> None:
        """max_tokens kwarg is stripped for generators that don't declare it."""
        received: dict = {}

        def gen(feedback=None):
            received["feedback"] = feedback
            return "ok"

        result = retry_with_feedback(
            generate_fn=gen,
            validate_fn=lambda x: (True, []),
            fallback="fallback",
            token_escalation=default_token_escalation,
        )
        assert result == "ok"
        # max_tokens was NOT passed to the generator
        assert "max_tokens" not in received

    def test_generator_accepts_only_max_tokens(self) -> None:
        """feedback kwarg is stripped for generators that only declare max_tokens."""
        received: dict = {}

        def gen(max_tokens=None):
            received["max_tokens"] = max_tokens
            return {"ok": True}

        retry_with_feedback(
            generate_fn=gen,
            validate_fn=lambda x: (True, []),
            fallback={},
            token_escalation=default_token_escalation,
            initial_max_tokens=512,
        )
        assert received.get("max_tokens") == 512

    def test_generator_accepts_both(self) -> None:
        """Both feedback and max_tokens are forwarded."""
        received: dict = {}
        call_count = [0]

        def gen(feedback=None, max_tokens=None):
            call_count[0] += 1
            received["feedback"] = feedback
            received["max_tokens"] = max_tokens
            if call_count[0] < 2:
                return {}
            return {"done": True}

        def validate(data):
            if not data.get("done"):
                return False, ["not done yet"]
            return True, []

        retry_with_feedback(
            generate_fn=gen,
            validate_fn=validate,
            fallback={},
            max_retries=3,
            token_escalation=default_token_escalation,
            initial_max_tokens=1024,
        )
        # On the second call feedback should have been set
        assert received["feedback"] == ["not done yet"]
        assert received["max_tokens"] == 1536

    def test_generator_with_var_kwargs_receives_max_tokens(self) -> None:
        """Generators with **kwargs receive max_tokens correctly."""
        received: dict = {}

        def gen(**kwargs):
            received.update(kwargs)
            return "ok"

        retry_with_feedback(
            generate_fn=gen,
            validate_fn=lambda x: (True, []),
            fallback="fallback",
            token_escalation=default_token_escalation,
            initial_max_tokens=2048,
        )
        assert received.get("max_tokens") == 2048

    def test_no_token_escalation_never_passes_max_tokens(self) -> None:
        """With token_escalation=None, max_tokens is never forwarded."""
        received: dict = {}

        def gen(**kwargs):
            received.update(kwargs)
            return {}

        retry_with_feedback(
            generate_fn=gen,
            validate_fn=lambda x: (True, []),
            fallback={},
            token_escalation=None,
        )
        assert "max_tokens" not in received


# ---------------------------------------------------------------------------
# Non-retryable errors (auth/permission)
# ---------------------------------------------------------------------------


class _AuthError(Exception):
    status_code = 401


class _PermissionDeniedError(Exception):
    pass


class TestNonRetryableErrors:
    def test_auth_error_aborts_immediately(self) -> None:
        """A 401 never succeeds on retry — it must raise on the first
        attempt instead of burning the retry budget into a fallback."""
        calls = []

        def generate(feedback=None):
            calls.append(1)
            raise _AuthError("invalid x-api-key")

        import pytest

        with pytest.raises(_AuthError):
            retry_with_feedback(
                generate_fn=generate,
                validate_fn=lambda c: (True, []),
                fallback="fallback",
                max_retries=3,
                label="auth-test",
            )
        assert len(calls) == 1

    def test_permission_denied_by_class_name(self) -> None:
        import pytest

        def generate(feedback=None):
            raise _PermissionDeniedError("nope")

        with pytest.raises(_PermissionDeniedError):
            retry_with_feedback(
                generate_fn=generate,
                validate_fn=lambda c: (True, []),
                fallback="fallback",
                label="perm-test",
            )

    def test_transient_errors_still_retry_to_fallback(self) -> None:
        """Ordinary exceptions keep the existing retry-then-fallback path."""
        calls = []

        def generate(feedback=None):
            calls.append(1)
            raise RuntimeError("flaky network")

        result = retry_with_feedback(
            generate_fn=generate,
            validate_fn=lambda c: (True, []),
            fallback="fallback",
            max_retries=3,
            label="transient-test",
        )
        assert result == "fallback"
        assert len(calls) == 3
