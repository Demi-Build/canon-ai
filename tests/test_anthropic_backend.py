"""Tests for AnthropicBackend.

All tests use a mocked Anthropic SDK client — no live API calls are made.
The module is skipped entirely if the ``anthropic`` package is not installed.
"""

from __future__ import annotations

import pytest

pytest.importorskip("anthropic")

from unittest.mock import MagicMock

from canon.backends.anthropic import DEFAULT_MODEL, PRICING, AnthropicBackend, register
from canon.backends.registry import BackendRegistry
from canon.llm.request import LLMRequest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_mock_response(text: str, input_tokens: int = 100, output_tokens: int = 50) -> MagicMock:
    """Build a mock Anthropic response with the expected shape."""
    block = MagicMock()
    block.text = text
    response = MagicMock()
    response.content = [block]
    response.usage = MagicMock(input_tokens=input_tokens, output_tokens=output_tokens)
    return response


def make_backend(model: str = DEFAULT_MODEL, response_text: str = "ok") -> tuple[AnthropicBackend, MagicMock]:
    """Return a (backend, mock_client) pair pre-wired with a simple response."""
    client = MagicMock()
    client.messages.create.return_value = make_mock_response(response_text)
    return AnthropicBackend(client=client, model=model), client


# ---------------------------------------------------------------------------
# Basic generation
# ---------------------------------------------------------------------------


def test_generate_returns_text() -> None:
    client = MagicMock()
    client.messages.create.return_value = make_mock_response("Hello world")
    backend = AnthropicBackend(client=client)
    result = backend.generate(LLMRequest(system="sys", user_message="hi"))
    assert result == "Hello world"


def test_generate_passes_correct_arguments() -> None:
    client = MagicMock()
    client.messages.create.return_value = make_mock_response("ok")
    backend = AnthropicBackend(client=client, model="claude-sonnet-4-6")
    backend.generate(
        LLMRequest(
            system="you are a writer",
            user_message="tell a story",
            examples=[("hi", "hello")],
            max_tokens=512,
        )
    )
    call = client.messages.create.call_args
    assert call.kwargs["model"] == "claude-sonnet-4-6"
    assert call.kwargs["max_tokens"] == 512
    assert call.kwargs["system"] == "you are a writer"
    assert call.kwargs["messages"] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "tell a story"},
    ]


def test_generate_no_examples() -> None:
    """With no examples, messages list contains only the user turn."""
    client = MagicMock()
    client.messages.create.return_value = make_mock_response("plain")
    backend = AnthropicBackend(client=client)
    backend.generate(LLMRequest(system="s", user_message="hello"))
    call = client.messages.create.call_args
    assert call.kwargs["messages"] == [{"role": "user", "content": "hello"}]


def test_generate_multiple_examples() -> None:
    """Multiple examples are all inserted before the final user message."""
    client = MagicMock()
    client.messages.create.return_value = make_mock_response("done")
    backend = AnthropicBackend(client=client)
    backend.generate(
        LLMRequest(
            system="s",
            user_message="final",
            examples=[("q1", "a1"), ("q2", "a2")],
        )
    )
    call = client.messages.create.call_args
    assert call.kwargs["messages"] == [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "final"},
    ]


# ---------------------------------------------------------------------------
# Response shape edge cases
# ---------------------------------------------------------------------------


def test_generate_returns_empty_string_when_no_text_blocks() -> None:
    """If the response has no blocks with a .text attribute, return empty string."""
    client = MagicMock()
    # A block without .text (e.g. a tool_use block)
    non_text_block = MagicMock(spec=[])  # spec=[] means hasattr(block, "text") is False
    response = MagicMock()
    response.content = [non_text_block]
    response.usage = MagicMock(input_tokens=10, output_tokens=0)
    client.messages.create.return_value = response
    backend = AnthropicBackend(client=client)
    result = backend.generate(LLMRequest(system="s", user_message="m"))
    assert result == ""


def test_generate_returns_empty_string_when_content_is_empty_list() -> None:
    """Empty content list (edge case) should return empty string, not raise."""
    client = MagicMock()
    response = MagicMock()
    response.content = []
    response.usage = MagicMock(input_tokens=5, output_tokens=0)
    client.messages.create.return_value = response
    backend = AnthropicBackend(client=client)
    result = backend.generate(LLMRequest(system="s", user_message="m"))
    assert result == ""


def test_generate_returns_first_text_block_only() -> None:
    """When multiple text blocks are returned, only the first is used."""
    client = MagicMock()
    block1 = MagicMock()
    block1.text = "first block"
    block2 = MagicMock()
    block2.text = "second block"
    response = MagicMock()
    response.content = [block1, block2]
    response.usage = MagicMock(input_tokens=20, output_tokens=10)
    client.messages.create.return_value = response
    backend = AnthropicBackend(client=client)
    result = backend.generate(LLMRequest(system="s", user_message="m"))
    assert result == "first block"


# ---------------------------------------------------------------------------
# Cost tracking
# ---------------------------------------------------------------------------


def test_cost_tracking() -> None:
    client = MagicMock()
    client.messages.create.return_value = make_mock_response("x", input_tokens=1000, output_tokens=500)
    backend = AnthropicBackend(client=client, model="claude-sonnet-4-6")
    backend.generate(LLMRequest(system="s", user_message="m"))
    assert backend.last_input_tokens == 1000
    assert backend.last_output_tokens == 500
    expected_cost = 1000 * PRICING["claude-sonnet-4-6"]["input"] + 500 * PRICING["claude-sonnet-4-6"]["output"]
    assert backend.last_cost == pytest.approx(expected_cost)


def test_unknown_model_zero_cost() -> None:
    client = MagicMock()
    client.messages.create.return_value = make_mock_response("x", input_tokens=100, output_tokens=50)
    backend = AnthropicBackend(client=client, model="unknown-model")
    backend.generate(LLMRequest(system="s", user_message="m"))
    assert backend.last_cost == 0.0


def test_cost_tokens_reset_on_each_call() -> None:
    """Token counts are overwritten (not accumulated) on each call."""
    client = MagicMock()
    backend = AnthropicBackend(client=client, model="claude-sonnet-4-6")

    client.messages.create.return_value = make_mock_response("a", input_tokens=100, output_tokens=50)
    backend.generate(LLMRequest(system="s", user_message="first"))
    assert backend.last_input_tokens == 100

    client.messages.create.return_value = make_mock_response("b", input_tokens=200, output_tokens=80)
    backend.generate(LLMRequest(system="s", user_message="second"))
    assert backend.last_input_tokens == 200
    assert backend.last_output_tokens == 80


def test_initial_cost_values_are_zero() -> None:
    """Before any generate() call, cost fields default to zero."""
    client = MagicMock()
    backend = AnthropicBackend(client=client)
    assert backend.last_input_tokens == 0
    assert backend.last_output_tokens == 0
    assert backend.last_cost == 0.0


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------


def test_register_adds_to_registry() -> None:
    BackendRegistry.reset()
    register()
    # Verify the factory was registered — don't actually instantiate it,
    # since that would require ANTHROPIC_API_KEY to be set in the environment.
    assert "anthropic" in BackendRegistry._llm_factories


def test_register_is_idempotent() -> None:
    """Calling register() twice should not raise; second call replaces factory."""
    BackendRegistry.reset()
    register()
    register()
    assert "anthropic" in BackendRegistry._llm_factories


def test_not_registered_at_import_time() -> None:
    """Importing the module must NOT auto-register. Only register() does that."""
    BackendRegistry.reset()
    # Module is already imported; registry should still be empty.
    import canon.backends.anthropic  # noqa: F401

    assert "anthropic" not in BackendRegistry._llm_factories


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_implements_llm_backend_protocol() -> None:
    from canon.backends.base import LLMBackend

    client = MagicMock()
    client.messages.create.return_value = make_mock_response("x")
    backend = AnthropicBackend(client=client)
    assert isinstance(backend, LLMBackend)


def test_prefers_serial_is_false() -> None:
    """AnthropicBackend is an API backend — parallel requests are fine."""
    client = MagicMock()
    backend = AnthropicBackend(client=client)
    assert backend.prefers_serial is False


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_default_model_is_sonnet() -> None:
    assert DEFAULT_MODEL == "claude-sonnet-4-6"


def test_pricing_table_has_expected_models() -> None:
    assert "claude-sonnet-4-6" in PRICING
    assert "claude-opus-4-7" in PRICING
    assert "claude-haiku-4-5-20251001" in PRICING


def test_pricing_table_values_are_per_token() -> None:
    """Spot-check that pricing values are per-token (not per-1M), i.e. very small."""
    for _model, prices in PRICING.items():
        assert prices["input"] < 1.0, "input price should be a per-token fraction"
        assert prices["output"] < 1.0, "output price should be a per-token fraction"
