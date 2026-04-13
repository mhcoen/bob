"""Shared parsing helpers for LLM response processing."""

from __future__ import annotations


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
        # strip closing fence
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text
