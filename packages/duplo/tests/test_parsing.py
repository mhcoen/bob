"""Tests for duplo.parsing."""

from __future__ import annotations

import json

from duplo.parsing import extract_all_json, extract_json, strip_fences


# --- strip_fences ---


def test_strip_fences_no_fences():
    assert strip_fences('{"key": "value"}') == '{"key": "value"}'


def test_strip_fences_json_fence():
    raw = '```json\n{"key": "value"}\n```'
    assert strip_fences(raw) == '{"key": "value"}'


def test_strip_fences_plain_fence():
    raw = '```\n{"key": "value"}\n```'
    assert strip_fences(raw) == '{"key": "value"}'


# --- extract_json ---


def test_extract_json_bare_object():
    assert extract_json('{"key": "value"}') == '{"key": "value"}'


def test_extract_json_fenced():
    raw = '```json\n{"key": "value"}\n```'
    assert extract_json(raw) == '{"key": "value"}'


def test_extract_json_prose_wrapped_object():
    raw = 'Here is the result:\n\n{"key": "value"}\n\nDone.'
    assert extract_json(raw) == '{"key": "value"}'


def test_extract_json_prose_wrapped_array():
    raw = "The items are:\n\n[1, 2, 3]\n\nThat's all."
    assert extract_json(raw) == "[1, 2, 3]"


def test_extract_json_no_json():
    raw = "No JSON here at all."
    assert extract_json(raw) == raw


def test_extract_json_prefers_fenced_over_scan():
    raw = '```json\n{"a": 1}\n```\n\n{"b": 2}'
    assert extract_json(raw) == '{"a": 1}'


def test_extract_json_multiple_objects():
    """When output contains multiple JSON objects (e.g. tool-use metadata
    followed by the actual response), return the first valid one."""
    raw = (
        '{"type": "tool_use", "name": "Read"}\n'
        '{"descriptions": [{"index": 0, "state": "Main", "detail": "ok"}]}\n'
    )
    result = extract_json(raw)
    # Should return the first valid JSON object, not span first-{ to last-}.
    parsed = json.loads(result)
    assert isinstance(parsed, dict)
    assert "type" in parsed or "descriptions" in parsed


def test_extract_json_multiple_objects_with_prose():
    """Multiple JSON objects interleaved with prose text."""
    raw = (
        "Tool result:\n"
        '{"status": "ok"}\n'
        "Analysis complete.\n"
        '{"descriptions": [{"index": 0, "state": "Login", "detail": "Form"}]}\n'
        "Done."
    )
    result = extract_json(raw)
    parsed = json.loads(result)
    assert isinstance(parsed, dict)


def test_extract_json_braces_in_strings():
    """Braces inside JSON string values should not confuse the parser."""
    raw = '{"key": "value with { and } inside"}'
    assert extract_json(raw) == raw


def test_extract_json_trailing_whitespace():
    """Trailing whitespace/newlines should not prevent parsing."""
    raw = '{"key": "value"}  \n\n  '
    result = extract_json(raw)
    parsed = json.loads(result)
    assert parsed == {"key": "value"}


def test_extract_json_leading_and_trailing_whitespace():
    raw = '  \n  {"key": "value"}  \n  '
    result = extract_json(raw)
    parsed = json.loads(result)
    assert parsed == {"key": "value"}


def test_extract_json_fenced_with_trailing_whitespace():
    raw = '```json\n{"key": "value"}\n```\n\n'
    result = extract_json(raw)
    parsed = json.loads(result)
    assert parsed == {"key": "value"}


def test_extract_json_unparseable_returns_original():
    """When the LLM returns something truly unparseable, extract_json returns
    the original text unchanged so callers can detect the failure."""
    raw = "I'm sorry, I cannot process these images."
    result = extract_json(raw)
    assert result == raw
    # Callers should detect this isn't valid JSON:
    with __import__("pytest").raises(json.JSONDecodeError):
        json.loads(result)


def test_extract_json_partial_json_returns_original():
    """Incomplete JSON (unbalanced braces) is not extractable."""
    raw = '{"key": "value"'
    result = extract_json(raw)
    assert result == raw


# --- extract_all_json ---


def test_extract_all_json_multiple_objects():
    raw = '{"type": "tool_use"}\nsome prose\n{"descriptions": [{"index": 0}]}\n'
    results = extract_all_json(raw)
    # Two objects plus the inner array [{"index": 0}] which is also valid JSON.
    assert len(results) == 3
    assert json.loads(results[0]) == {"type": "tool_use"}
    assert json.loads(results[1]) == {"descriptions": [{"index": 0}]}
    assert json.loads(results[2]) == [{"index": 0}]


def test_extract_all_json_no_json():
    assert extract_all_json("no json here") == []
