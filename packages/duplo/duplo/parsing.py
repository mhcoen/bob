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
    for balanced ``{...}`` and ``[...]`` spans and returns the longest
    span that parses.  Preferring the longest span avoids returning an
    inner object when the outer structure is an array of objects.
    Returns *text* unchanged when no JSON structure is found.
    """
    stripped = strip_fences(text)
    try:
        json.loads(stripped)
        return stripped
    except (json.JSONDecodeError, ValueError):
        pass

    best: str = ""
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        for candidate in _balanced_spans(text, open_ch, close_ch):
            try:
                json.loads(candidate)
            except (json.JSONDecodeError, ValueError):
                continue
            if len(candidate) > len(best):
                best = candidate

    return best if best else text


def extract_all_json(text: str) -> list[str]:
    """Extract all valid JSON objects and arrays from *text*.

    Returns a list of JSON-parseable strings found via balanced-brace
    scanning.  Useful when output contains multiple JSON objects (e.g.
    tool-use metadata interleaved with the response).
    """
    results: list[str] = []
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        for candidate in _balanced_spans(text, open_ch, close_ch):
            try:
                json.loads(candidate)
                results.append(candidate)
            except (json.JSONDecodeError, ValueError):
                continue
    return results


def _balanced_spans(text: str, open_ch: str, close_ch: str) -> list[str]:
    """Yield substrings of *text* that are balanced ``open_ch…close_ch`` spans.

    Skips characters inside JSON string literals so that braces within
    strings don't confuse the count.
    """
    spans: list[str] = []
    i = 0
    while i < len(text):
        if text[i] == open_ch:
            depth = 0
            in_string = False
            escape = False
            start = i
            for j in range(i, len(text)):
                ch = text[j]
                if escape:
                    escape = False
                    continue
                if ch == "\\":
                    if in_string:
                        escape = True
                    continue
                if ch == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch == open_ch:
                    depth += 1
                elif ch == close_ch:
                    depth -= 1
                    if depth == 0:
                        spans.append(text[start : j + 1])
                        i = j + 1
                        break
            else:
                # Unbalanced — skip this opening brace.
                i += 1
        else:
            i += 1
    return spans
