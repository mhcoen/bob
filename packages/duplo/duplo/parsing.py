"""Shared parsing helpers for LLM response processing."""

from __future__ import annotations

import json


def strip_fences(text: str) -> str:
    """Remove markdown code fences wrapping *text* if present.

    Handles ````` `` ``` ````` and ````` ```json ````` style fences.
    Returns the inner content with the opening and closing fence lines
    stripped.
    """
    fence_pos = text.find("```")
    if fence_pos != -1:
        text = text[fence_pos:]
        lines = text.splitlines()
        # strip opening fence
        lines = lines[1:]
        # strip closing fence (search from end in case text follows it)
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip().startswith("```"):
                lines = lines[:i]
                break
        text = "\n".join(lines)
    return text


def extract_json(text: str) -> str:
    """Extract a JSON object or array from LLM output.

    Tries ``strip_fences`` first.  If the result isn't valid JSON, scans
    for the outermost ``{...}`` or ``[...]`` and returns that substring.
    Returns *text* unchanged when no JSON structure is found.
    """
    stripped = strip_fences(text)
    try:
        json.loads(stripped)
        return stripped
    except (json.JSONDecodeError, ValueError):
        pass

    # Find outermost JSON object or array.
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = text.find(open_ch)
        if start == -1:
            continue
        end = text.rfind(close_ch)
        if end <= start:
            continue
        candidate = text[start : end + 1]
        try:
            json.loads(candidate)
            return candidate
        except (json.JSONDecodeError, ValueError):
            continue

    return text
