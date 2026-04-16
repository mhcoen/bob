"""Filter video frames using Claude Vision to keep only clear UI screenshots."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from duplo.claude_cli import ClaudeCliError, query_with_images
from duplo.parsing import extract_json

_SYSTEM = """\
You are a UI screenshot quality filter. Given a batch of video frames,
classify each one. Keep frames that show a clear, stable screenshot of
an application with a distinct UI state. Discard frames that are:
- Mid-transition or motion-blurred
- Marketing overlays, splash screens, or promotional banners
- Loading screens or spinners
- Blank or nearly blank screens
- Browser chrome / OS UI without meaningful app content
- Duplicate UI states already covered by another kept frame

Return ONLY a JSON object:
{
  "decisions": [
    {"index": 0, "keep": true, "reason": "Clear settings page"},
    {"index": 1, "keep": false, "reason": "Motion blur during transition"}
  ]
}

The "index" corresponds to the order images were presented (0-based).
Be selective — it is better to keep fewer high-quality frames than many
low-quality ones.
"""

_BATCH_SIZE = 10


@dataclass
class FilterDecision:
    """Classification of a single frame."""

    path: Path
    keep: bool
    reason: str


def filter_frames(
    frames: list[Path],
    *,
    batch_size: int = _BATCH_SIZE,
) -> list[FilterDecision]:
    """Send frames to Claude Vision and classify each one.

    Frames are sent in batches of *batch_size* to stay within limits.
    Returns a :class:`FilterDecision` for every input frame.
    """
    if not frames:
        return []

    decisions: list[FilterDecision] = []
    for start in range(0, len(frames), batch_size):
        batch = frames[start : start + batch_size]
        batch_decisions = _filter_batch(batch)
        decisions.extend(batch_decisions)

    return decisions


def _filter_batch(frames: list[Path]) -> list[FilterDecision]:
    """Classify a single batch of frames via ``claude -p``."""
    prompt = (
        "Classify each frame above. Return ONLY the JSON object "
        "with a decisions array as described."
    )
    try:
        raw = query_with_images(prompt, frames, system=_SYSTEM)
    except ClaudeCliError:
        return [FilterDecision(path=f, keep=True, reason="cli error") for f in frames]
    return _parse_decisions(raw, frames)


def _parse_decisions(raw: str, frames: list[Path]) -> list[FilterDecision]:
    """Parse the JSON response into FilterDecision objects.

    Falls back to keeping all frames if parsing fails.
    """
    text = extract_json(raw)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return [FilterDecision(path=f, keep=True, reason="parse error") for f in frames]

    if not isinstance(data, dict):
        return [FilterDecision(path=f, keep=True, reason="parse error") for f in frames]

    raw_decisions = data.get("decisions", [])
    if not isinstance(raw_decisions, list):
        return [FilterDecision(path=f, keep=True, reason="parse error") for f in frames]

    # Build a lookup by index.
    by_index: dict[int, dict] = {}
    for item in raw_decisions:
        if isinstance(item, dict) and "index" in item:
            try:
                by_index[int(item["index"])] = item
            except (ValueError, TypeError):
                continue

    results: list[FilterDecision] = []
    for i, frame in enumerate(frames):
        if i in by_index:
            item = by_index[i]
            keep = bool(item.get("keep", True))
            reason = str(item.get("reason", ""))
        else:
            keep = True
            reason = "not classified"
        results.append(FilterDecision(path=frame, keep=keep, reason=reason))

    return results


def apply_filter(decisions: list[FilterDecision]) -> list[Path]:
    """Return kept frame paths and delete rejected frames from disk."""
    kept: list[Path] = []
    for dec in decisions:
        if dec.keep:
            kept.append(dec.path)
        else:
            dec.path.unlink(missing_ok=True)
    return kept
