"""Describe UI states shown in accepted video frames using Claude Vision."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from duplo.claude_cli import query_with_images
from duplo.parsing import strip_fences

_SYSTEM = """\
You are a UI analyst. Given a batch of application screenshots, describe
the UI state each one shows. Identify the view type (e.g. main view,
settings panel, dialog, menu, sidebar, form, list view, detail view,
modal, onboarding, login screen, dashboard, etc.) and briefly note the
key visible elements.

Return ONLY a JSON object:
{
  "descriptions": [
    {"index": 0, "state": "Settings panel", "detail": "Shows app preferences with theme toggle and notification options"},
    {"index": 1, "state": "Main dashboard", "detail": "Grid layout with project cards and a sidebar navigation"}
  ]
}

The "index" corresponds to the order images were presented (0-based).
Be specific but concise.
"""

_BATCH_SIZE = 10


@dataclass
class FrameDescription:
    """Description of the UI state shown in a single frame."""

    path: Path
    state: str
    detail: str


def describe_frames(
    frames: list[Path],
    *,
    batch_size: int = _BATCH_SIZE,
) -> list[FrameDescription]:
    """Send frames to Claude Vision and describe each one's UI state.

    Frames are sent in batches of *batch_size* to stay within limits.
    Returns a :class:`FrameDescription` for every input frame.
    """
    if not frames:
        return []

    descriptions: list[FrameDescription] = []
    for start in range(0, len(frames), batch_size):
        batch = frames[start : start + batch_size]
        batch_descriptions = _describe_batch(batch)
        descriptions.extend(batch_descriptions)

    return descriptions


def _describe_batch(frames: list[Path]) -> list[FrameDescription]:
    """Describe a single batch of frames via ``claude -p``."""
    prompt = (
        "Describe the UI state of each frame above. "
        "Return ONLY the JSON object with a descriptions array "
        "as described."
    )
    raw = query_with_images(prompt, frames, system=_SYSTEM)
    return _parse_descriptions(raw, frames)


def _parse_descriptions(raw: str, frames: list[Path]) -> list[FrameDescription]:
    """Parse the JSON response into FrameDescription objects.

    Falls back to "unknown" state if parsing fails.
    """
    text = strip_fences(raw)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return [FrameDescription(path=f, state="unknown", detail="parse error") for f in frames]

    if not isinstance(data, dict):
        return [FrameDescription(path=f, state="unknown", detail="parse error") for f in frames]

    raw_descs = data.get("descriptions", [])
    if not isinstance(raw_descs, list):
        return [FrameDescription(path=f, state="unknown", detail="parse error") for f in frames]

    # Build a lookup by index.
    by_index: dict[int, dict] = {}
    for item in raw_descs:
        if isinstance(item, dict) and "index" in item:
            try:
                by_index[int(item["index"])] = item
            except (ValueError, TypeError):
                continue

    results: list[FrameDescription] = []
    for i, frame in enumerate(frames):
        if i in by_index:
            item = by_index[i]
            state = str(item.get("state", "unknown"))
            detail = str(item.get("detail", ""))
        else:
            state = "unknown"
            detail = "not described"
        results.append(FrameDescription(path=frame, state=state, detail=detail))

    return results
