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
from duplo.parsing import extract_json
from duplo.questioner import BuildPreferences

_SYSTEM = """\
You are a software architect assistant. Given free-form prose
describing a project's architecture, extract structured build
preferences.

Return ONLY a JSON object with these fields:
  "platform"            – target platform (e.g. "web", "cli",
                          "desktop", "mobile-ios", "api"). Use a
                          short lowercase label. If unclear, use "".
  "language"            – primary programming language (e.g.
                          "Python", "TypeScript", "Swift"). If
                          unclear, use "".
  "framework"           – framework or toolkit if mentioned (e.g.
                          "React", "FastAPI", "SwiftUI"). If
                          unclear, use "".
  "dependencies"        – array of strings: explicit library or
                          service dependencies mentioned (e.g.
                          "PostgreSQL", "Redis", "Tailwind CSS").
                          Empty array if none.
  "other_constraints"   – array of strings: any other hard
                          constraints or preferences (e.g.
                          "macOS only", "prefer functional style",
                          "minimal dependencies"). Empty array if
                          none.

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
    try:
        data = json.loads(extract_json(raw))
    except (json.JSONDecodeError, ValueError):
        record_failure(
            "build_prefs:_parse_response",
            "llm",
            f"Failed to parse LLM response as JSON: {raw[:200]}",
        )
        return BuildPreferences(platform="", language="", constraints=[], preferences=[])

    if not isinstance(data, dict):
        return BuildPreferences(platform="", language="", constraints=[], preferences=[])

    platform = str(data.get("platform", "") or "")
    language = str(data.get("language", "") or "")
    framework = str(data.get("framework", "") or "")

    # Combine language and framework into one string when both present.
    if language and framework:
        combined_language = f"{language}/{framework}"
    elif framework:
        combined_language = framework
    else:
        combined_language = language

    dependencies = _str_list(data.get("dependencies", []))
    other_constraints = _str_list(data.get("other_constraints", []))

    return BuildPreferences(
        platform=platform,
        language=combined_language,
        constraints=dependencies,
        preferences=other_constraints,
    )


def is_all_defaults(prefs: BuildPreferences) -> bool:
    """Return True when *prefs* has no usable fields.

    All-defaults means the LLM could not extract any structured
    information from the ``## Architecture`` prose.
    """
    return (
        not prefs.platform
        and not prefs.language
        and not prefs.constraints
        and not prefs.preferences
    )


def validate_build_preferences(prefs: BuildPreferences) -> list[str]:
    """Return warning strings for *prefs*.

    Returns a one-element list when all fields are at their defaults
    (the LLM extracted nothing useful).  Returns an empty list when
    at least one field is populated.
    """
    if is_all_defaults(prefs):
        return [
            "Build preferences are all defaults — "
            "## Architecture may be too vague for the LLM to extract "
            "platform, language, or constraints. "
            "Plan generation will proceed but with less context."
        ]
    return []


def _str_list(value: object) -> list[str]:
    """Coerce *value* to a list of strings, tolerating bad input."""
    if isinstance(value, list):
        return [str(item) for item in value if item]
    return []
