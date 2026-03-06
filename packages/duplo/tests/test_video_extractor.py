"""Tests for duplo.video_extractor."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from duplo.video_extractor import (
    ExtractionResult,
    extract_all_videos,
    extract_scene_frames,
    ffmpeg_available,
)


def test_ffmpeg_available():
    result = ffmpeg_available()
    assert isinstance(result, bool)


def test_ffmpeg_available_missing():
    with patch("duplo.video_extractor.shutil.which", return_value=None):
        assert ffmpeg_available() is False


def test_extract_scene_frames_no_ffmpeg(tmp_path):
    video = tmp_path / "test.mp4"
    video.touch()
    with patch("duplo.video_extractor.ffmpeg_available", return_value=False):
        result = extract_scene_frames(video, tmp_path / "out")
    assert result.error == "ffmpeg not found on PATH"
    assert result.frames == []


def test_extract_scene_frames_missing_file(tmp_path):
    video = tmp_path / "nonexistent.mp4"
    result = extract_scene_frames(video, tmp_path / "out")
    assert "file not found" in result.error
    assert result.frames == []


def test_extract_scene_frames_ffmpeg_failure(tmp_path):
    video = tmp_path / "test.mp4"
    video.touch()
    mock_result = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="invalid data"
    )
    with patch("duplo.video_extractor.subprocess.run", return_value=mock_result):
        result = extract_scene_frames(video, tmp_path / "out")
    assert "ffmpeg failed" in result.error


def test_extract_scene_frames_timeout(tmp_path):
    video = tmp_path / "test.mp4"
    video.touch()
    with patch(
        "duplo.video_extractor.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=120),
    ):
        result = extract_scene_frames(video, tmp_path / "out")
    assert result.error == "ffmpeg timed out"


def test_extract_scene_frames_creates_output_dir(tmp_path):
    video = tmp_path / "test.mp4"
    video.touch()
    out_dir = tmp_path / "nested" / "out"
    mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    with patch("duplo.video_extractor.subprocess.run", return_value=mock_result):
        extract_scene_frames(video, out_dir)
    assert out_dir.is_dir()


def test_extract_scene_frames_collects_frames(tmp_path):
    video = tmp_path / "test.mp4"
    video.touch()
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    def fake_run(cmd, **kwargs):
        # Simulate ffmpeg creating frame files.
        for i in range(1, 4):
            (out_dir / f"test_scene_{i:04d}.png").write_bytes(b"fake png")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch("duplo.video_extractor.subprocess.run", side_effect=fake_run):
        result = extract_scene_frames(video, out_dir)
    assert result.error == ""
    assert len(result.frames) == 3
    assert all(f.name.startswith("test_scene_") for f in result.frames)


def test_extract_scene_frames_retry_lower_threshold(tmp_path):
    video = tmp_path / "test.mp4"
    video.touch()
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    call_count = 0

    def fake_run(cmd, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First call: no frames detected.
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        # Second call (lower threshold): create frames.
        for i in range(1, 3):
            (out_dir / f"test_scene_{i:04d}.png").write_bytes(b"fake png")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch("duplo.video_extractor.subprocess.run", side_effect=fake_run):
        result = extract_scene_frames(video, out_dir, threshold=0.3)
    assert call_count == 2
    assert len(result.frames) == 2


def test_extract_all_videos(tmp_path):
    v1 = tmp_path / "a.mp4"
    v2 = tmp_path / "b.mp4"
    v1.touch()
    v2.touch()
    out_dir = tmp_path / "out"

    mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    with patch("duplo.video_extractor.subprocess.run", return_value=mock_result):
        results = extract_all_videos([v1, v2], out_dir)
    assert len(results) == 2
    assert all(isinstance(r, ExtractionResult) for r in results)


def test_extract_scene_frames_ffmpeg_not_found_as_file_not_found(tmp_path):
    """FileNotFoundError from subprocess.run (ffmpeg binary missing)."""
    video = tmp_path / "test.mp4"
    video.touch()
    with patch(
        "duplo.video_extractor.subprocess.run",
        side_effect=FileNotFoundError("No such file"),
    ):
        result = extract_scene_frames(video, tmp_path / "out")
    assert "ffmpeg not found" in result.error


@pytest.fixture
def _real_test_video(tmp_path):
    """Create a short test video with two distinct scenes using ffmpeg."""
    video = tmp_path / "scenes.mp4"
    # Scene 1: red for 1 second, Scene 2: blue for 1 second.
    cmd_scene1 = [
        "ffmpeg",
        "-f",
        "lavfi",
        "-i",
        "color=c=red:size=320x240:duration=1:rate=10",
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:size=320x240:duration=1:rate=10",
        "-filter_complex",
        "[0:v][1:v]concat=n=2:v=1:a=0[out]",
        "-map",
        "[out]",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(video),
        "-y",
        "-loglevel",
        "error",
    ]
    proc = subprocess.run(cmd_scene1, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        pytest.skip(f"Could not create test video: {proc.stderr}")
    return video


def test_extract_scene_frames_real_ffmpeg(tmp_path, _real_test_video):
    """Integration test with real ffmpeg and a generated video."""
    out_dir = tmp_path / "frames"
    result = extract_scene_frames(_real_test_video, out_dir, threshold=0.3)
    assert result.error == ""
    # Should detect at least 1 scene change (red→blue transition).
    assert len(result.frames) >= 1
    for frame in result.frames:
        assert frame.exists()
        assert frame.stat().st_size > 0
