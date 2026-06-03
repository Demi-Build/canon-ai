"""Robust JSON extraction from LLM output.

Real models frequently wrap JSON in markdown fences (```json … ```) or add a
short preamble, so a bare ``json.loads()`` fails at char 0. This module ports
MazeWorld's proven approach (``src/generate/generators/llm_primitives.py`` —
``_strip_fences`` / ``_parse_json_response`` / ``_parse_json_array``): strip
fences, slice the outermost object/array, ``json.loads`` with an
``ast.literal_eval`` fallback, and recover from a truncated array by closing it
at the last complete object.

Game-agnostic — this is a generic LLM-output concern, so it lives in core.
"""

from __future__ import annotations

import ast
import json
import logging

logger = logging.getLogger(__name__)


def strip_fences(raw: str) -> str:
    """Remove markdown code fences (```json ... ```) from LLM output."""
    text = raw.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1 :]
    if text.rstrip().endswith("```"):
        text = text.rstrip()[:-3]
    return text.strip()


def extract_json_object(raw: str) -> dict | None:
    """Extract the outermost JSON object from (possibly fenced) LLM text.

    Returns the parsed dict, or ``None`` if no object could be parsed.
    Tries the fence-stripped text first, then the raw text; within each, slices
    from the first ``{`` to the last ``}`` and parses, with an
    ``ast.literal_eval`` fallback for single-quoted/Python-ish output.
    """
    if not raw:
        return None
    stripped = strip_fences(raw)
    for candidate in (stripped, raw):
        start = candidate.find("{")
        end = candidate.rfind("}") + 1
        if start != -1 and end > start:
            snippet = candidate[start:end]
            try:
                obj = json.loads(snippet)
            except (json.JSONDecodeError, ValueError):
                try:
                    obj = ast.literal_eval(snippet)
                except (ValueError, SyntaxError):
                    continue
            if isinstance(obj, dict):
                return obj
    logger.debug(
        "extract_json_object failed on input (first 300 chars): %r", raw[:300]
    )
    return None


def extract_json_array(raw: str) -> list | None:
    """Extract a JSON array from (possibly fenced) LLM text.

    Returns the parsed list, or ``None`` on failure. Includes truncation
    recovery: if the array was cut off mid-stream, close it at the last
    complete object and parse the partial array.
    """
    if not raw:
        return None
    stripped = strip_fences(raw)
    for candidate in (stripped, raw):
        start = candidate.find("[")
        end = candidate.rfind("]") + 1
        if start != -1 and end > start:
            snippet = candidate[start:end]
            try:
                obj = json.loads(snippet)
            except (json.JSONDecodeError, ValueError):
                try:
                    obj = ast.literal_eval(snippet)
                except (ValueError, SyntaxError):
                    obj = None
            if isinstance(obj, list):
                return obj

    # Truncation recovery: close the array at the last complete object.
    for candidate in (stripped, raw):
        start = candidate.find("[")
        if start == -1:
            continue
        text = candidate[start:]
        last_brace = text.rfind("}")
        if last_brace > 0:
            truncated = text[: last_brace + 1] + "]"
            try:
                obj = json.loads(truncated)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(obj, list) and obj:
                logger.info("Recovered %d items from truncated JSON array", len(obj))
                return obj
    return None
