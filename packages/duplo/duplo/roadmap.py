"""Generate and manage a phased build roadmap."""

from __future__ import annotations

import dataclasses
from typing import Any

from duplo.claude_cli import query
from duplo.parsing import strip_fences
from duplo.extractor import Feature
from duplo.questioner import BuildPreferences

_SYSTEM = """\
You are a senior software architect creating a phased build roadmap.

Given a product to duplicate, a list of features, and build preferences,
produce a roadmap that breaks the build into phases. Each phase must
produce something runnable and testable.

Rules:
- Phase 0 is always scaffolding: project structure, build system, empty
  window or entry point. It must compile/run and show a window or CLI
  output. Nothing else.
- Phase 1 is the core feature, end to end. One primary user flow working
  completely. No secondary features.
- Subsequent phases each add one major feature or a small group of
  closely related features.
- Later phases handle polish, settings, and edge cases.
- Each phase must be small enough to build in one McLoop run (roughly
  5-15 tasks).
- Each phase builds on the previous one. No phase should require
  rewriting what an earlier phase built.

Output ONLY a JSON array. No explanation, no markdown fences.
Each element is an object with these fields:
  "phase": integer (0, 1, 2, ...)
  "title": short title (e.g., "Scaffold", "Audio capture")
  "goal": one sentence describing what this phase produces
  "features": list of feature names included (empty for Phase 0)
  "test": how to verify this phase works (one sentence)
"""


def generate_roadmap(
    source_url: str,
    features: list[Feature],
    preferences: BuildPreferences,
    *,
    completion_history: list[dict] | None = None,
    spec_text: str = "",
    scope_include: list[str] | None = None,
) -> list[dict]:
    """Generate a phased build roadmap.

    Args:
        source_url: Product URL being duplicated.
        features: Features to include in the new roadmap.
        preferences: Build platform and language preferences.
        completion_history: Optional list of dicts with ``phase`` (label)
            and ``features`` (list of feature names implemented in that
            phase).  When provided, the prompt tells the model what has
            already been built so the new roadmap continues from there.
        scope_include: Feature names the user requires (from SPEC). Each
            such name is guaranteed a slot in a build phase after the LLM
            roadmap is parsed, so a required item is never left
            feature-only or stranded in the scaffold phase.

    Returns a list of phase dicts, each with phase, title, goal,
    features, and test.
    """
    features_text = "\n".join(f"- {f.name} ({f.category}): {f.description}" for f in features)
    prefs = dataclasses.asdict(preferences)
    constraints = (
        "\n".join(f"  - {c}" for c in prefs["constraints"]) if prefs["constraints"] else "  (none)"
    )
    other_prefs = (
        "\n".join(f"  - {p}" for p in prefs["preferences"]) if prefs["preferences"] else "  (none)"
    )

    history_section = ""
    if completion_history:
        history_lines = []
        for entry in completion_history:
            label = entry.get("phase", "Unknown")
            feats = entry.get("features", [])
            if feats:
                history_lines.append(f"- {label}: {', '.join(feats)}")
            else:
                history_lines.append(f"- {label}: (scaffolding/infrastructure)")
        history_section = (
            "\nAlready completed phases (DO NOT repeat these features):\n"
            + "\n".join(history_lines)
            + "\n"
        )

    spec_section = ""
    if spec_text:
        spec_section = f"\nProduct specification (authoritative, from the user):\n{spec_text}\n"

    prompt = f"""\
Product: {source_url}

Platform: {prefs["platform"]}
Language/stack: {prefs["language"]}
Constraints:
{constraints}
Preferences:
{other_prefs}
{history_section}{spec_section}
Features to include:
{features_text}

Generate the roadmap now.
"""

    raw = query(prompt, system=_SYSTEM, call_site="generate_roadmap")
    roadmap = _parse_roadmap(raw)
    return _reconcile_scope_into_roadmap(roadmap, scope_include)


def _parse_roadmap(raw: str) -> list[dict]:
    """Parse the JSON roadmap from Claude's response."""
    import json

    text = strip_fences(raw.strip())

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []

    if not isinstance(data, list):
        return []

    roadmap: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        roadmap.append(
            {
                "phase": item.get("phase", len(roadmap)),
                "title": item.get("title", "Untitled"),
                "goal": item.get("goal", ""),
                "features": item.get("features", []),
                "test": item.get("test", ""),
            }
        )

    return roadmap


def _is_build_phase(phase: dict) -> bool:
    """Return True if *phase* is a buildable phase (not the scaffold).

    Phase 0 is always scaffolding and carries no feature tasks, so a
    scope feature listed only there would never be built.  Every other
    phase produces real feature tasks for the planner.
    """
    return phase.get("phase") != 0


def _reconcile_scope_into_roadmap(
    roadmap: list[dict],
    scope_include: list[str] | None,
) -> list[dict]:
    """Guarantee every ``scope_include`` feature reaches a build phase.

    The LLM that generates the roadmap may drop a required feature
    entirely or list it only under the scaffold phase (Phase 0), which
    produces no feature tasks.  Either way the user's required scope item
    would never be built.  For each name in *scope_include* not already
    allocated to a build phase (case-insensitive match against every
    non-scaffold phase's ``features`` list), the name is appended to a
    build phase so the planner generates real tasks for it.

    Missing names are appended to the last existing build phase.  If the
    roadmap is scaffold-only (no build phase exists), a new build phase
    is synthesized to hold them.  The roadmap is mutated in place and
    also returned.  User spec scope is authoritative, so a required item
    is never left feature-only.
    """
    if not roadmap or not scope_include:
        return roadmap

    allocated = {
        str(name).strip().lower()
        for phase in roadmap
        if _is_build_phase(phase)
        for name in phase.get("features", [])
    }

    missing: list[str] = []
    seen: set[str] = set()
    for item in scope_include:
        name = item.strip()
        key = name.lower()
        if not key or key in allocated or key in seen:
            continue
        seen.add(key)
        missing.append(name)

    if not missing:
        return roadmap

    target: dict[str, Any] | None = None
    for phase in roadmap:
        if _is_build_phase(phase):
            target = phase
    if target is None:
        max_phase = max((p.get("phase", 0) for p in roadmap), default=-1)
        target = {
            "phase": max_phase + 1,
            "title": "Required scope features",
            "goal": "Implement user-specified scope features from the SPEC.",
            "features": [],
            "test": "Each required scope feature works end to end.",
        }
        roadmap.append(target)

    target["features"] = list(target.get("features", [])) + missing
    return roadmap


def format_roadmap(roadmap: list[dict]) -> str:
    """Format roadmap for terminal display."""
    lines = []
    for phase in roadmap:
        n = phase["phase"]
        title = phase["title"]
        goal = phase["goal"]
        test = phase["test"]
        features = phase.get("features", [])

        lines.append(f"Phase {n}: {title}")
        lines.append(f"  Goal: {goal}")
        if features:
            lines.append(f"  Features: {', '.join(features)}")
        lines.append(f"  Test: {test}")
        lines.append("")

    return "\n".join(lines)
