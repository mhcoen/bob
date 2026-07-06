"""Filter video frames using Claude Vision to keep only clear UI screenshots."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from duplo.claude_cli import ClaudeCliError, query_with_images
from duplo.parsing import extract_all_json, extract_json

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


def _find_decisions_object(raw: str) -> dict | None:
    """Find a JSON object with a ``decisions`` list in *raw*."""
    text = extract_json(raw)
    try:
        data = json.loads(text)
        if isinstance(data, dict) and isinstance(data.get("decisions"), list):
            return data
    except (json.JSONDecodeError, ValueError):
        pass

    for candidate in extract_all_json(raw):
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("decisions"), list):
            return data

    return None


def _parse_decisions(raw: str, frames: list[Path]) -> list[FilterDecision]:
    """Parse the JSON response into FilterDecision objects.

    Falls back to keeping all frames if parsing fails.
    """
    data = _find_decisions_object(raw)

    if data is None:
        return [FilterDecision(path=f, keep=True, reason="parse error") for f in frames]

    raw_decisions = data["decisions"]

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
            keep, verdict_ok = _coerce_keep(item.get("keep"))
            if verdict_ok:
                reason = str(item.get("reason", ""))
            else:
                # An entry without a usable verdict (missing "keep",
                # or a value like the STRING "false" -- which bool()
                # read as True, keeping a frame the model rejected and
                # treating it as vetted) is fail-open, not a vetting.
                reason = "not classified"
        else:
            keep = True
            reason = "not classified"
        results.append(FilterDecision(path=frame, keep=keep, reason=reason))

    return results


def _coerce_keep(value: object) -> tuple[bool, bool]:
    """Return ``(keep, verdict_ok)`` for a raw ``keep`` field.

    ``verdict_ok`` is False when the model gave no usable boolean --
    the frame is then kept but must be classified fail-open so it is
    re-filtered next run instead of frozen as vetted.
    """
    if isinstance(value, bool):
        return value, True
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "yes", "1", "keep"):
            return True, True
        if lowered in ("false", "no", "0", "reject", "drop"):
            return False, True
    if isinstance(value, int) and value in (0, 1):
        return bool(value), True
    return True, False


# Reasons meaning Vision never actually vetted the frame: the CLI call
# failed, the response was unparseable, or a syntactically valid
# response simply omitted the frame (an empty/short decisions array is
# a common LLM failure mode). Every fail-open decision keeps the frame,
# so callers deciding whether a video's frame set is trustworthy must
# check against THIS set, not an ad-hoc subset -- the "not classified"
# path was missed once and quietly froze unvetted frame sets.
FAIL_OPEN_REASONS = frozenset({"cli error", "parse error", "not classified"})


def apply_filter(decisions: list[FilterDecision]) -> list[Path]:
    """Return kept frame paths and delete rejected frames from disk."""
    kept: list[Path] = []
    for dec in decisions:
        if dec.keep:
            kept.append(dec.path)
        else:
            dec.path.unlink(missing_ok=True)
    return kept
