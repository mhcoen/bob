"""Tests for duplo.parsing."""

from __future__ import annotations

from duplo.parsing import extract_json, strip_fences


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
