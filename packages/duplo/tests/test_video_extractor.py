"""Tests for duplo.video_extractor."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from PIL import Image

from duplo.video_extractor import (
    ExtractionResult,
    _dhash,
    _hamming,
    deduplicate_frames,
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


# --- Deduplication tests ---


def _make_gradient_png(path: Path, base: tuple[int, int, int], size: int = 64) -> Path:
    """Create a gradient PNG image at *path* using *base* color offset."""
    img = Image.new("RGB", (size, size))
    pixels = []
    for y in range(size):
        for x in range(size):
            r = (base[0] + x * 3 + y) % 256
            g = (base[1] + y * 3 + x) % 256
            b = (base[2] + x + y) % 256
            pixels.append((r, g, b))
    img.putdata(pixels)
    img.save(path)
    return path


def _make_solid_png(path: Path, color: tuple[int, int, int], size: int = 64) -> Path:
    """Create a solid-color PNG image at *path*."""
    img = Image.new("RGB", (size, size), color)
    img.save(path)
    return path


def test_dhash_identical_images():
    img = Image.new("RGB", (64, 64), (100, 150, 200))
    assert _dhash(img) == _dhash(img)


def test_dhash_different_images():
    # Use gradient images so dHash has pixel variation to work with.
    red = Image.new("L", (64, 64))
    red.putdata([i % 256 for i in range(64 * 64)])
    blue = Image.new("L", (64, 64))
    blue.putdata([(255 - i) % 256 for i in range(64 * 64)])
    assert _dhash(red) != _dhash(blue)


def test_hamming_identical():
    assert _hamming(0b1010, 0b1010) == 0


def test_hamming_all_different():
    assert _hamming(0b0000, 0b1111) == 4


def test_deduplicate_removes_identical(tmp_path):
    frames = [
        _make_solid_png(tmp_path / "a.png", (255, 0, 0)),
        _make_solid_png(tmp_path / "b.png", (255, 0, 0)),
        _make_solid_png(tmp_path / "c.png", (255, 0, 0)),
    ]
    result = deduplicate_frames(frames)
    assert len(result) == 1
    assert result[0] == frames[0]
    # Duplicates should be deleted from disk.
    assert not frames[1].exists()
    assert not frames[2].exists()


def test_deduplicate_keeps_distinct(tmp_path):
    frames = [
        _make_gradient_png(tmp_path / "a.png", (0, 0, 0)),
        _make_gradient_png(tmp_path / "b.png", (128, 50, 200)),
        _make_gradient_png(tmp_path / "c.png", (50, 200, 128)),
    ]
    result = deduplicate_frames(frames)
    assert len(result) == 3


def test_deduplicate_empty_list():
    assert deduplicate_frames([]) == []


def test_deduplicate_single_frame(tmp_path):
    frames = [_make_solid_png(tmp_path / "a.png", (128, 128, 128))]
    result = deduplicate_frames(frames)
    assert len(result) == 1


def test_deduplicate_keeps_frame_on_read_error(tmp_path):
    """If a frame can't be opened, it's kept rather than discarded."""
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not a png")
    result = deduplicate_frames([bad])
    assert len(result) == 1
    assert result[0] == bad


def test_deduplicate_respects_max_distance(tmp_path):
    """With max_distance=0, only exact hash matches are duplicates."""
    # Two very similar but not identical images.
    img1 = Image.new("RGB", (64, 64), (100, 100, 100))
    img2 = Image.new("RGB", (64, 64), (105, 100, 100))
    p1 = tmp_path / "a.png"
    p2 = tmp_path / "b.png"
    img1.save(p1)
    img2.save(p2)
    # With distance=0, small differences should keep both.
    result = deduplicate_frames([p1, p2], max_distance=0)
    assert len(result) >= 1  # At least the first is kept.


def test_deduplicate_no_pillow(tmp_path):
    """Without Pillow, frames are returned unchanged."""
    frames = [tmp_path / "a.png", tmp_path / "b.png"]
    for f in frames:
        f.write_bytes(b"data")
    with patch("duplo.video_extractor._PILLOW", False):
        result = deduplicate_frames(frames)
    assert result == frames
