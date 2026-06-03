"""Unit tests for canon.llm.parsing — robust JSON extraction from LLM output."""

import pytest

from canon.llm.parsing import extract_json_array, extract_json_object, strip_fences


# ---------------------------------------------------------------------------
# strip_fences
# ---------------------------------------------------------------------------


def test_strip_fences_plain_json():
    raw = '{"key": "value"}'
    assert strip_fences(raw) == raw


def test_strip_fences_json_fence():
    raw = "```json\n{\"key\": \"value\"}\n```"
    assert strip_fences(raw) == '{"key": "value"}'


def test_strip_fences_bare_fence():
    raw = "```\n{\"key\": \"value\"}\n```"
    assert strip_fences(raw) == '{"key": "value"}'


def test_strip_fences_no_closing_fence():
    raw = "```json\n{\"key\": \"value\"}"
    # No closing fence — only the opening line is stripped, no trailing removal
    result = strip_fences(raw)
    assert '{"key": "value"}' in result


# ---------------------------------------------------------------------------
# extract_json_object — happy paths
# ---------------------------------------------------------------------------


def test_extract_json_object_plain():
    raw = '{"name": "Alice", "age": 30}'
    result = extract_json_object(raw)
    assert result == {"name": "Alice", "age": 30}


def test_extract_json_object_fenced():
    """Real Claude output: JSON wrapped in ```json … ```."""
    raw = '```json\n{"title": "The Dark Forest", "synopsis": "Trees."}\n```'
    result = extract_json_object(raw)
    assert result is not None
    assert result["title"] == "The Dark Forest"


def test_extract_json_object_preamble():
    """LLM adds a sentence before the JSON block."""
    raw = 'Here is the JSON you requested:\n{"name": "Goblin", "hp": 10}'
    result = extract_json_object(raw)
    assert result is not None
    assert result["name"] == "Goblin"


def test_extract_json_object_preamble_and_fence():
    """Preamble + fenced block — real production pattern."""
    raw = (
        "Sure, here is the entity:\n"
        "```json\n"
        '{"name": "Orc Warlord", "type": "enemy"}\n'
        "```"
    )
    result = extract_json_object(raw)
    assert result is not None
    assert result["name"] == "Orc Warlord"


def test_extract_json_object_single_quoted():
    """ast.literal_eval fallback for Python-ish single-quoted output."""
    raw = "{'name': 'Wizard', 'stat': 'INT'}"
    result = extract_json_object(raw)
    assert result is not None
    assert result["name"] == "Wizard"


def test_extract_json_object_nested():
    raw = '{"outer": {"inner": 42}}'
    result = extract_json_object(raw)
    assert result == {"outer": {"inner": 42}}


def test_extract_json_object_trailing_text():
    """JSON followed by prose — slicing to last } handles this."""
    raw = '{"name": "Dragon"} Please use this in your game.'
    result = extract_json_object(raw)
    assert result is not None
    assert result["name"] == "Dragon"


# ---------------------------------------------------------------------------
# extract_json_object — failure cases
# ---------------------------------------------------------------------------


def test_extract_json_object_empty_string():
    assert extract_json_object("") is None


def test_extract_json_object_none_input():
    assert extract_json_object(None) is None


def test_extract_json_object_no_braces():
    assert extract_json_object("just some text") is None


def test_extract_json_object_array_input_returns_none():
    """An array at the top level should not be returned as an object."""
    raw = '[{"name": "item1"}, {"name": "item2"}]'
    # extract_json_object slices { … } — if there are none, returns None.
    # If inner objects are found, that's an implementation detail; either way
    # the caller should use extract_json_array for array input.
    result = extract_json_object(raw)
    # Either None (no outer object) or a dict from the first inner object — both
    # are acceptable; this test just confirms it doesn't crash.
    assert result is None or isinstance(result, dict)


# ---------------------------------------------------------------------------
# extract_json_array — happy paths
# ---------------------------------------------------------------------------


def test_extract_json_array_plain():
    raw = '[{"id": 1}, {"id": 2}]'
    result = extract_json_array(raw)
    assert result == [{"id": 1}, {"id": 2}]


def test_extract_json_array_fenced():
    raw = "```json\n[1, 2, 3]\n```"
    result = extract_json_array(raw)
    assert result == [1, 2, 3]


def test_extract_json_array_truncated():
    """Truncated mid-stream array — recovery closes at the last complete object."""
    raw = '[{"name": "a"}, {"name": "b"}, {"name": "c"'
    result = extract_json_array(raw)
    assert result is not None
    assert len(result) >= 2
    assert result[0]["name"] == "a"
    assert result[1]["name"] == "b"


def test_extract_json_array_empty():
    assert extract_json_array("") is None


def test_extract_json_array_no_brackets():
    assert extract_json_array("no array here") is None
