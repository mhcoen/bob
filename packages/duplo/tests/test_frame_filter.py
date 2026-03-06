"""Tests for duplo.frame_filter."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from duplo.frame_filter import (
    FilterDecision,
    _parse_decisions,
    apply_filter,
    filter_frames,
)


def _make_png(path: Path, size: int = 4) -> Path:
    """Create a minimal valid PNG file at *path*."""
    # 1x1 white PNG (smallest valid PNG).
    data = (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx"
        b"\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00"
        b"\x00\x00\x00IEND\xaeB`\x82"
    )
    path.write_bytes(data)
    return path


def _mock_client(response_text: str) -> MagicMock:
    """Create a mock Anthropic client returning *response_text*."""
    client = MagicMock()
    msg = MagicMock()
    block = MagicMock()
    block.text = response_text
    msg.content = [block]
    client.messages.create.return_value = msg
    return client


# --- _parse_decisions ---


def test_parse_decisions_valid(tmp_path):
    frames = [tmp_path / "a.png", tmp_path / "b.png"]
    raw = '{"decisions": [{"index": 0, "keep": true, "reason": "clear"}, {"index": 1, "keep": false, "reason": "blurry"}]}'
    result = _parse_decisions(raw, frames)
    assert len(result) == 2
    assert result[0].keep is True
    assert result[0].reason == "clear"
    assert result[1].keep is False
    assert result[1].reason == "blurry"


def test_parse_decisions_with_code_fences(tmp_path):
    frames = [tmp_path / "a.png"]
    raw = '```json\n{"decisions": [{"index": 0, "keep": true, "reason": "ok"}]}\n```'
    result = _parse_decisions(raw, frames)
    assert len(result) == 1
    assert result[0].keep is True


def test_parse_decisions_invalid_json(tmp_path):
    frames = [tmp_path / "a.png", tmp_path / "b.png"]
    result = _parse_decisions("not json at all", frames)
    assert len(result) == 2
    assert all(d.keep is True for d in result)
    assert all(d.reason == "parse error" for d in result)


def test_parse_decisions_missing_index(tmp_path):
    frames = [tmp_path / "a.png", tmp_path / "b.png"]
    raw = '{"decisions": [{"index": 0, "keep": false, "reason": "bad"}]}'
    result = _parse_decisions(raw, frames)
    assert result[0].keep is False
    # Frame 1 not in response → kept by default.
    assert result[1].keep is True
    assert result[1].reason == "not classified"


def test_parse_decisions_not_a_dict(tmp_path):
    frames = [tmp_path / "a.png"]
    result = _parse_decisions("[1, 2, 3]", frames)
    assert len(result) == 1
    assert result[0].keep is True


def test_parse_decisions_decisions_not_a_list(tmp_path):
    frames = [tmp_path / "a.png"]
    result = _parse_decisions('{"decisions": "oops"}', frames)
    assert len(result) == 1
    assert result[0].keep is True


# --- apply_filter ---


def test_apply_filter_keeps_and_deletes(tmp_path):
    kept_file = _make_png(tmp_path / "keep.png")
    reject_file = _make_png(tmp_path / "reject.png")
    decisions = [
        FilterDecision(path=kept_file, keep=True, reason="good"),
        FilterDecision(path=reject_file, keep=False, reason="bad"),
    ]
    result = apply_filter(decisions)
    assert result == [kept_file]
    assert kept_file.exists()
    assert not reject_file.exists()


def test_apply_filter_missing_file_ok(tmp_path):
    missing = tmp_path / "gone.png"
    decisions = [FilterDecision(path=missing, keep=False, reason="bad")]
    result = apply_filter(decisions)
    assert result == []


def test_apply_filter_empty():
    assert apply_filter([]) == []


# --- filter_frames ---


def test_filter_frames_empty():
    assert filter_frames([]) == []


def test_filter_frames_single_batch(tmp_path):
    frames = [_make_png(tmp_path / "a.png"), _make_png(tmp_path / "b.png")]
    response = (
        '{"decisions": ['
        '{"index": 0, "keep": true, "reason": "clear UI"},'
        '{"index": 1, "keep": false, "reason": "loading screen"}'
        "]}"
    )
    client = _mock_client(response)
    result = filter_frames(frames, client=client)
    assert len(result) == 2
    assert result[0].keep is True
    assert result[1].keep is False
    client.messages.create.assert_called_once()


def test_filter_frames_multiple_batches(tmp_path):
    frames = [_make_png(tmp_path / f"f{i}.png") for i in range(5)]
    response = (
        '{"decisions": ['
        '{"index": 0, "keep": true, "reason": "ok"},'
        '{"index": 1, "keep": true, "reason": "ok"}'
        "]}"
    )
    client = _mock_client(response)
    result = filter_frames(frames, client=client, batch_size=2)
    assert len(result) == 5
    # 3 batches: [0,1], [2,3], [4]
    assert client.messages.create.call_count == 3


def test_filter_frames_api_error_keeps_all(tmp_path):
    frames = [_make_png(tmp_path / "a.png")]
    client = _mock_client("totally broken response {{{{")
    result = filter_frames(frames, client=client)
    assert len(result) == 1
    assert result[0].keep is True
