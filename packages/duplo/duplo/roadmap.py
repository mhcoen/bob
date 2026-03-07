"""Generate and manage a phased build roadmap."""

from __future__ import annotations

import dataclasses

from duplo.claude_cli import query
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
) -> list[dict]:
    """Generate a phased build roadmap.

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

    prompt = f"""\
Product: {source_url}

Platform: {prefs["platform"]}
Language/stack: {prefs["language"]}
Constraints:
{constraints}
Preferences:
{other_prefs}

Features to include:
{features_text}

Generate the roadmap now.
"""

    raw = query(prompt, system=_SYSTEM)
    return _parse_roadmap(raw)


def _parse_roadmap(raw: str) -> list[dict]:
    """Parse the JSON roadmap from Claude's response."""
    import json

    text = raw.strip()
    # Strip markdown fences if present
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

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []

    if not isinstance(data, list):
        return []

    roadmap = []
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
