"""Tests for retry_with_feedback's attempt_log capture.

The failure class this kills: attempt-failure WARNING lines only ever hit
the console, so a real run that exhausted retries (the l3 flat-fallback
incident, twice) left no diagnosable evidence. attempt_log hands callers a
persistable per-attempt trace: outcome, validation reasons, and the
rejected content.
"""

from __future__ import annotations

import pytest

from canon.pipeline.retry import retry_with_feedback


class TestAttemptLog:
    def test_pass_first_try_records_single_passed(self) -> None:
        log: list[dict] = []
        result = retry_with_feedback(
            generate_fn=lambda: "good",
            validate_fn=lambda c: (True, []),
            fallback="fb",
            attempt_log=log,
        )
        assert result == "good"
        assert log == [
            {"attempt": 1, "outcome": "passed", "reasons": [], "content": "good"}
        ]

    def test_fail_then_pass_records_full_trail(self) -> None:
        log: list[dict] = []
        outputs = iter(["bad-1", "bad-2", "good"])

        def validate(content: str) -> tuple[bool, list[str]]:
            if content.startswith("bad"):
                return False, [f"{content} rejected"]
            return True, []

        result = retry_with_feedback(
            generate_fn=lambda feedback=None: next(outputs),
            validate_fn=validate,
            fallback="fb",
            attempt_log=log,
        )
        assert result == "good"
        assert [a["outcome"] for a in log] == [
            "failed_validation", "failed_validation", "passed",
        ]
        assert log[0] == {
            "attempt": 1,
            "outcome": "failed_validation",
            "reasons": ["bad-1 rejected"],
            "content": "bad-1",
        }
        assert log[2]["attempt"] == 3

    def test_exhaustion_records_every_failure(self) -> None:
        log: list[dict] = []
        result = retry_with_feedback(
            generate_fn=lambda feedback=None: "never valid",
            validate_fn=lambda c: (False, ["nope"]),
            fallback="fb",
            max_retries=3,
            attempt_log=log,
        )
        assert result == "fb"
        assert len(log) == 3
        assert all(a["outcome"] == "failed_validation" for a in log)
        assert all(a["reasons"] == ["nope"] for a in log)
        assert all(a["content"] == "never valid" for a in log)

    def test_generation_error_recorded_with_no_content(self) -> None:
        log: list[dict] = []
        calls = {"n": 0}

        def flaky(feedback=None) -> str:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return "good"

        result = retry_with_feedback(
            generate_fn=flaky,
            validate_fn=lambda c: (True, []),
            fallback="fb",
            attempt_log=log,
        )
        assert result == "good"
        assert log[0] == {
            "attempt": 1,
            "outcome": "generation_error",
            "reasons": ["boom"],
            "content": None,
        }
        assert log[1]["outcome"] == "passed"

    def test_non_retryable_error_still_raises(self) -> None:
        log: list[dict] = []

        class AuthenticationError(Exception):
            status_code = 401

        def doomed() -> str:
            raise AuthenticationError("bad key")

        with pytest.raises(AuthenticationError):
            retry_with_feedback(
                generate_fn=doomed,
                validate_fn=lambda c: (True, []),
                fallback="fb",
                attempt_log=log,
            )
        # Abort-before-spend is not an attempt outcome; nothing recorded.
        assert log == []

    def test_default_none_changes_nothing(self) -> None:
        result = retry_with_feedback(
            generate_fn=lambda feedback=None: "never valid",
            validate_fn=lambda c: (False, ["nope"]),
            fallback="fb",
            max_retries=2,
        )
        assert result == "fb"
