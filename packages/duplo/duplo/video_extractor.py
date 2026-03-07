"""Extract frames from video files at scene change points using ffmpeg."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

try:
    from PIL import Image

    _PILLOW = True
except ImportError:  # pragma: no cover
    _PILLOW = False


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

    before = len(frames)
    frames = deduplicate_frames(frames)
    if before > len(frames):
        # Informational — caller sees the deduplicated list.
        pass

    return ExtractionResult(source=video, frames=frames)


def _run_ffmpeg_scene_detect(
    video: Path,
    output_dir: Path,
    stem: str,
    threshold: float,
    max_frames: int,
) -> list[Path] | str:
    """Run ffmpeg scene detection and return frame paths or error string."""
    # Remove stale frames from prior runs so they don't appear in results.
    prefix = f"{stem}_scene_"
    for old in output_dir.iterdir():
        if old.name.startswith(prefix) and old.name.endswith(".png"):
            old.unlink()

    safe_stem = stem.replace("%", "%%")
    pattern = str(output_dir / f"{safe_stem}_scene_%04d.png")
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
    prefix = f"{stem}_scene_"
    frames = sorted(
        p for p in output_dir.iterdir() if p.name.startswith(prefix) and p.name.endswith(".png")
    )
    return frames


def _dhash(image: Image.Image, size: int = 8) -> int:
    """Compute a difference hash (dHash) for an image.

    The image is resized to (*size* + 1) x *size* and converted to grayscale.
    Each bit in the returned integer indicates whether a pixel is brighter than
    the one to its right.
    """
    resized = image.convert("L").resize((size + 1, size), Image.LANCZOS)
    pixels = list(resized.tobytes())
    width = size + 1
    bits = 0
    for row in range(size):
        for col in range(size):
            idx = row * width + col
            if pixels[idx] > pixels[idx + 1]:
                bits |= 1 << (row * size + col)
    return bits


def _hamming(a: int, b: int) -> int:
    """Return the Hamming distance between two integers."""
    return bin(a ^ b).count("1")


def deduplicate_frames(
    frames: list[Path],
    *,
    max_distance: int = 6,
    hash_size: int = 8,
) -> list[Path]:
    """Remove near-duplicate frames using perceptual difference hashing.

    Compares each frame against all previously kept frames. If the minimum
    Hamming distance to any kept frame is <= *max_distance*, the frame is
    considered a duplicate and its file is deleted.

    Args:
        frames: Sorted list of frame paths.
        max_distance: Maximum Hamming distance to consider two frames
            as duplicates. Default 6 (out of 64 bits).
        hash_size: Size parameter for dHash. Produces *hash_size*^2 bit
            hashes (default 8 → 64-bit hashes).

    Returns:
        The deduplicated list of frame paths (subset of *frames*).
    """
    if not _PILLOW:
        return frames

    if not frames:
        return frames

    kept: list[tuple[Path, int | None]] = []
    for frame in frames:
        try:
            img = Image.open(frame)
            h = _dhash(img, hash_size)
        except Exception:
            # Can't hash → keep the frame but use None so it never
            # matches valid hashes during deduplication.
            kept.append((frame, None))
            continue

        is_dup = any(kh is not None and _hamming(h, kh) <= max_distance for _, kh in kept)
        if is_dup:
            frame.unlink(missing_ok=True)
        else:
            kept.append((frame, h))

    return [p for p, _ in kept]


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
