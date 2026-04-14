"""Parse BuildPreferences from ## Architecture prose via an LLM call.

This module replaces the interactive ``ask_preferences()`` flow in
``questioner.py``.  Instead of prompting the user, it extracts
structured build preferences from the free-form ``## Architecture``
section of SPEC.md.

Results are cached in ``.duplo/duplo.json`` under ``preferences``.
The cache is invalidated when the SHA-256 of the comment-stripped
``spec.architecture`` content changes (stored as
``architecture_hash`` in duplo.json).
"""

from __future__ import annotations

import hashlib
import json

from duplo.claude_cli import ClaudeCliError, query
from duplo.diagnostics import record_failure
from duplo.parsing import strip_fences
from duplo.questioner import BuildPreferences

_SYSTEM = """\
You are a software architect assistant. Given free-form prose
describing a project's architecture, extract structured build
preferences.

Return ONLY a JSON object with these fields:
  "platform"          – target platform (e.g. "web", "cli",
                        "desktop", "mobile-ios", "api"). Use a
                        short lowercase label. If unclear, use "".
  "language"          – primary language or stack (e.g. "Python",
                        "TypeScript/React", "Swift/SwiftUI"). If
                        unclear, use "".
  "constraints"       – array of strings: hard constraints
                        mentioned (e.g. "must use PostgreSQL",
                        "macOS only"). Empty array if none.
  "preferences"       – array of strings: softer preferences or
                        style guidance (e.g. "prefer functional
                        style", "minimal dependencies"). Empty
                        array if none.

Rules:
1. Only extract what is EXPLICITLY stated or clearly implied by
   the prose. Do not invent constraints or preferences.
2. If a field cannot be determined, use its default ("" for
   strings, [] for arrays).
3. Return valid JSON only, no explanation.
"""


def parse_build_preferences(architecture_prose: str) -> BuildPreferences:
    """Parse free-form ## Architecture into structured fields.

    Calls Claude with a structured-output prompt asking for
    platform, language, constraints, and preferences extracted
    from the prose.

    Returns :class:`BuildPreferences` with whatever fields the LLM
    could populate.  Missing fields stay at default values.
    """
    if not architecture_prose.strip():
        return BuildPreferences(platform="", language="", constraints=[], preferences=[])

    try:
        raw = query(architecture_prose, system=_SYSTEM)
    except ClaudeCliError as exc:
        record_failure(
            "build_prefs:parse_build_preferences",
            "llm",
            f"LLM call failed: {exc}",
        )
        return BuildPreferences(platform="", language="", constraints=[], preferences=[])

    return _parse_response(raw)


def architecture_hash(architecture_prose: str) -> str:
    """Return the SHA-256 hex digest of *architecture_prose*.

    The input should be the comment-stripped ``spec.architecture``
    string.  The hash is stored in duplo.json as
    ``architecture_hash`` so that changes to ``## Architecture``
    trigger re-parsing.
    """
    return hashlib.sha256(architecture_prose.encode("utf-8")).hexdigest()


def _parse_response(raw: str) -> BuildPreferences:
    """Parse the LLM JSON response into a BuildPreferences."""
    text = strip_fences(raw)

    # Try to extract a JSON object if there is surrounding text.
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start != -1 and brace_end > brace_start:
        text = text[brace_start : brace_end + 1]

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        record_failure(
            "build_prefs:_parse_response",
            "llm",
            f"Failed to parse LLM response as JSON: {raw[:200]}",
        )
        return BuildPreferences(platform="", language="", constraints=[], preferences=[])

    if not isinstance(data, dict):
        return BuildPreferences(platform="", language="", constraints=[], preferences=[])

    return BuildPreferences(
        platform=str(data.get("platform", "") or ""),
        language=str(data.get("language", "") or ""),
        constraints=_str_list(data.get("constraints", [])),
        preferences=_str_list(data.get("preferences", [])),
    )


def _str_list(value: object) -> list[str]:
    """Coerce *value* to a list of strings, tolerating bad input."""
    if isinstance(value, list):
        return [str(item) for item in value if item]
    return []
