"""Extract frames from video files at scene change points using ffmpeg."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ExtractionResult:
    """Result of extracting frames from a video file."""

    source: Path
    frames: list[Path]
    error: str = ""


def ffmpeg_available() -> bool:
    """Return True if ffmpeg is on PATH."""
    return shutil.which("ffmpeg") is not None


def extract_scene_frames(
    video: Path,
    output_dir: Path,
    *,
    threshold: float = 0.3,
    min_frames: int = 1,
    max_frames: int = 50,
) -> ExtractionResult:
    """Extract frames at scene change points from *video* using ffmpeg.

    Uses the ``select`` filter with ``gt(scene,threshold)`` to detect
    visual transition points. Extracted frames are saved as PNG files
    in *output_dir*.

    Args:
        video: Path to the video file.
        output_dir: Directory to write extracted frame images.
        threshold: Scene change sensitivity (0.0–1.0). Lower values
            detect more scene changes.
        min_frames: If fewer frames are detected, re-run with a lower
            threshold to ensure at least this many frames.
        max_frames: Maximum number of frames to extract.

    Returns:
        An :class:`ExtractionResult` with the list of extracted frame
        paths, or an error message if extraction failed.
    """
    if not ffmpeg_available():
        return ExtractionResult(source=video, frames=[], error="ffmpeg not found on PATH")

    if not video.is_file():
        return ExtractionResult(source=video, frames=[], error=f"file not found: {video}")

    output_dir.mkdir(parents=True, exist_ok=True)
    stem = video.stem

    frames = _run_ffmpeg_scene_detect(video, output_dir, stem, threshold, max_frames)
    if isinstance(frames, str):
        return ExtractionResult(source=video, frames=[], error=frames)

    # If we got too few frames, retry with a lower threshold.
    if len(frames) < min_frames and threshold > 0.1:
        lower = max(0.1, threshold - 0.15)
        retry = _run_ffmpeg_scene_detect(video, output_dir, stem, lower, max_frames)
        if isinstance(retry, list) and len(retry) > len(frames):
            # Clean up old frames that aren't in the new set.
            new_set = set(retry)
            for old in frames:
                if old not in new_set and old.exists():
                    old.unlink()
            frames = retry

    return ExtractionResult(source=video, frames=frames)


def _run_ffmpeg_scene_detect(
    video: Path,
    output_dir: Path,
    stem: str,
    threshold: float,
    max_frames: int,
) -> list[Path] | str:
    """Run ffmpeg scene detection and return frame paths or error string."""
    pattern = str(output_dir / f"{stem}_scene_%04d.png")
    cmd = [
        "ffmpeg",
        "-i",
        str(video),
        "-vf",
        f"select='gt(scene,{threshold})',setpts=N/FRAME_RATE/TB",
        "-frames:v",
        str(max_frames),
        "-vsync",
        "vfr",
        pattern,
        "-y",
        "-loglevel",
        "error",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return "ffmpeg timed out"
    except FileNotFoundError:
        return "ffmpeg not found on PATH"

    if proc.returncode != 0:
        return f"ffmpeg failed (exit {proc.returncode}): {proc.stderr.strip()}"

    # Collect extracted frames in order.
    frames = sorted(output_dir.glob(f"{stem}_scene_*.png"))
    return frames


def extract_all_videos(
    videos: list[Path],
    output_dir: Path,
    *,
    threshold: float = 0.3,
    max_frames_per_video: int = 50,
) -> list[ExtractionResult]:
    """Extract scene frames from multiple videos.

    Returns a list of :class:`ExtractionResult`, one per video.
    """
    results = []
    for video in videos:
        result = extract_scene_frames(
            video,
            output_dir,
            threshold=threshold,
            max_frames=max_frames_per_video,
        )
        results.append(result)
    return results
