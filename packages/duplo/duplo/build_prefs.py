"""Parse BuildPreferences from ## Architecture via structured entries or LLM.

This module replaces the interactive ``ask_preferences()`` flow in
``questioner.py``.  It prefers structured ``PlatformEntry`` rows parsed
out of ``## Architecture`` (one per target stack) and falls back to an
LLM extraction pass over the free-form prose when no structured entries
are present.

Results are cached in ``.duplo/duplo.json`` under ``preferences``.
The cache is invalidated when the SHA-256 over the comment-stripped
``spec.architecture`` prose plus any structured entries changes (stored
as ``architecture_hash`` in duplo.json).
"""

from __future__ import annotations

import hashlib
import json

from duplo.claude_cli import ClaudeCliError, query
from duplo.diagnostics import record_failure
from duplo.parsing import extract_json
from duplo.questioner import BuildPreferences
from duplo.spec_reader import PlatformEntry

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


def parse_build_preferences(
    architecture_prose: str,
    *,
    structured_entries: list[PlatformEntry] | None = None,
) -> list[BuildPreferences]:
    """Return one :class:`BuildPreferences` per target stack.

    When *structured_entries* is non-empty, each entry maps directly to
    one :class:`BuildPreferences` — no LLM call is made.  Otherwise the
    LLM is asked to extract a single set of preferences from
    *architecture_prose*.

    The list always has at least one element so downstream code can
    index ``[0]`` without a length check: empty prose and no entries
    yield a single all-defaults entry.
    """
    if structured_entries:
        return [_entry_to_preferences(e) for e in structured_entries]

    if not architecture_prose.strip():
        return [_defaults()]

    try:
        raw = query(architecture_prose, system=_SYSTEM)
    except ClaudeCliError as exc:
        record_failure(
            "build_prefs:parse_build_preferences",
            "llm",
            f"LLM call failed: {exc}",
        )
        return [_defaults()]

    return [_parse_response(raw)]


def architecture_hash(
    architecture_prose: str,
    *,
    structured_entries: list[PlatformEntry] | None = None,
) -> str:
    """Return the SHA-256 digest of the architecture content.

    Combines *architecture_prose* with a canonical serialization of
    *structured_entries* so the cache invalidates whenever either piece
    of ``## Architecture`` changes.  When *structured_entries* is empty
    or ``None``, the digest reduces to ``sha256(architecture_prose)`` —
    same as before structured entries existed.
    """
    content = architecture_prose
    if structured_entries:
        entries_repr = "\n".join(
            f"{e.platform}|{e.language}|{e.build}" for e in structured_entries
        )
        content = content + "\n---entries---\n" + entries_repr
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _entry_to_preferences(entry: PlatformEntry) -> BuildPreferences:
    """Map a :class:`PlatformEntry` to :class:`BuildPreferences`.

    The ``build`` field becomes a preference string (``build: <value>``)
    so downstream consumers that only look at
    ``platform``/``language``/``preferences`` still see it.
    """
    prefs: list[str] = []
    if entry.build:
        prefs.append(f"build: {entry.build}")
    return BuildPreferences(
        platform=entry.platform,
        language=entry.language,
        constraints=[],
        preferences=prefs,
    )


def _defaults() -> BuildPreferences:
    return BuildPreferences(platform="", language="", constraints=[], preferences=[])


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
        return _defaults()

    if not isinstance(data, dict):
        return _defaults()

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


def validate_build_preferences(prefs: list[BuildPreferences]) -> list[str]:
    """Return warning strings for a list of :class:`BuildPreferences`.

    Emits one warning per all-defaults entry.  A single-entry list with
    an all-defaults entry produces the original "all defaults" message;
    multi-entry lists tag each warning with a stack index so the user
    can tell which stack needs more detail.
    """
    if not prefs:
        return [
            "Build preferences are all defaults — "
            "## Architecture may be too vague for the LLM to extract "
            "platform, language, or constraints. "
            "Plan generation will proceed but with less context."
        ]
    if len(prefs) == 1 and is_all_defaults(prefs[0]):
        return [
            "Build preferences are all defaults — "
            "## Architecture may be too vague for the LLM to extract "
            "platform, language, or constraints. "
            "Plan generation will proceed but with less context."
        ]
    warnings: list[str] = []
    for i, p in enumerate(prefs):
        if is_all_defaults(p):
            warnings.append(
                f"Stack {i + 1}: build preferences are all defaults — "
                "this structured platform entry has no usable fields."
            )
    return warnings


def _str_list(value: object) -> list[str]:
    """Coerce *value* to a list of strings, tolerating bad input."""
    if isinstance(value, list):
        return [str(item) for item in value if item]
    return []
