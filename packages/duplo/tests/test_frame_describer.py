"""Tests for duplo.frame_describer."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from duplo.frame_describer import (
    _parse_descriptions,
    describe_frames,
)


def _make_png(path: Path) -> Path:
    """Create a minimal valid PNG file at *path*."""
    data = (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx"
        b"\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00"
        b"\x00\x00\x00IEND\xaeB`\x82"
    )
    path.write_bytes(data)
    return path


# --- _parse_descriptions ---


def test_parse_descriptions_valid(tmp_path):
    frames = [tmp_path / "a.png", tmp_path / "b.png"]
    raw = (
        '{"descriptions": ['
        '{"index": 0, "state": "Settings panel", "detail": "Theme toggle"},'
        '{"index": 1, "state": "Main view", "detail": "Dashboard with cards"}'
        "]}"
    )
    result = _parse_descriptions(raw, frames)
    assert len(result) == 2
    assert result[0].state == "Settings panel"
    assert result[0].detail == "Theme toggle"
    assert result[1].state == "Main view"
    assert result[1].detail == "Dashboard with cards"


def test_parse_descriptions_with_code_fences(tmp_path):
    frames = [tmp_path / "a.png"]
    raw = '```json\n{"descriptions": [{"index": 0, "state": "Login", "detail": "Form"}]}\n```'
    result = _parse_descriptions(raw, frames)
    assert len(result) == 1
    assert result[0].state == "Login"


def test_parse_descriptions_json_wrapped_in_prose(tmp_path):
    frames = [tmp_path / "a.png", tmp_path / "b.png"]
    raw = (
        "Here are the descriptions for each frame:\n\n"
        '{"descriptions": ['
        '{"index": 0, "state": "Settings panel", "detail": "Theme toggle"},'
        '{"index": 1, "state": "Main view", "detail": "Dashboard with cards"}'
        "]}\n\n"
        "I've analyzed all frames and described the UI state of each one."
    )
    result = _parse_descriptions(raw, frames)
    assert len(result) == 2
    assert result[0].state == "Settings panel"
    assert result[1].state == "Main view"


def test_parse_descriptions_invalid_json(tmp_path):
    frames = [tmp_path / "a.png", tmp_path / "b.png"]
    result = _parse_descriptions("not json at all", frames)
    assert len(result) == 2
    assert all(d.state == "unknown" for d in result)
    assert all(d.detail == "parse error" for d in result)


def test_parse_descriptions_missing_index(tmp_path):
    frames = [tmp_path / "a.png", tmp_path / "b.png"]
    raw = '{"descriptions": [{"index": 0, "state": "Menu", "detail": "Nav items"}]}'
    result = _parse_descriptions(raw, frames)
    assert result[0].state == "Menu"
    assert result[1].state == "unknown"
    assert result[1].detail == "not described"


def test_parse_descriptions_not_a_dict(tmp_path):
    frames = [tmp_path / "a.png"]
    result = _parse_descriptions("[1, 2, 3]", frames)
    assert len(result) == 1
    assert result[0].state == "unknown"


def test_parse_descriptions_descriptions_not_a_list(tmp_path):
    frames = [tmp_path / "a.png"]
    result = _parse_descriptions('{"descriptions": "oops"}', frames)
    assert len(result) == 1
    assert result[0].state == "unknown"


# --- describe_frames ---


def test_describe_frames_empty():
    assert describe_frames([]) == []


def test_describe_frames_single_batch(tmp_path):
    frames = [_make_png(tmp_path / "a.png"), _make_png(tmp_path / "b.png")]
    response = (
        '{"descriptions": ['
        '{"index": 0, "state": "Settings", "detail": "App prefs"},'
        '{"index": 1, "state": "Dashboard", "detail": "Cards grid"}'
        "]}"
    )
    with patch("duplo.frame_describer.query_with_images", return_value=response) as mock_q:
        result = describe_frames(frames)
    assert len(result) == 2
    assert result[0].state == "Settings"
    assert result[1].state == "Dashboard"
    mock_q.assert_called_once()


def test_describe_frames_multiple_batches(tmp_path):
    frames = [_make_png(tmp_path / f"f{i}.png") for i in range(5)]
    response = (
        '{"descriptions": ['
        '{"index": 0, "state": "View", "detail": "ok"},'
        '{"index": 1, "state": "View", "detail": "ok"}'
        "]}"
    )
    with patch("duplo.frame_describer.query_with_images", return_value=response) as mock_q:
        result = describe_frames(frames, batch_size=2)
    assert len(result) == 5
    # 3 batches: [0,1], [2,3], [4]
    assert mock_q.call_count == 3


def test_describe_frames_api_error_returns_unknown(tmp_path):
    frames = [_make_png(tmp_path / "a.png")]
    with patch(
        "duplo.frame_describer.query_with_images",
        return_value="totally broken response {{{{",
    ):
        result = describe_frames(frames)
    assert len(result) == 1
    assert result[0].state == "unknown"
