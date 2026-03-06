"""Generate PLAN.md files for building phases of an application."""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import anthropic

from duplo.extractor import Feature
from duplo.questioner import BuildPreferences

_MODEL = "claude-haiku-4-5-20251001"

_PHASE_SYSTEM = """\
You are a senior software architect generating a build plan for one
phase of an application.

You will be given a roadmap phase (number, title, goal, features,
test criteria) along with build preferences. Generate a PLAN.md
that McLoop can execute. McLoop works through checklist items one
at a time, launching a fresh Claude Code session per task.

Rules for the plan:
- Each checklist item should be a single, focused unit of work.
- Items should be ordered so each leaves the project in a building
  and runnable state.
- Phase 0 (scaffold) should create the project structure, build
  system, and a minimal window or entry point. Nothing else.
- Later phases should build incrementally on existing code.
- Aim for 5-15 checklist items per phase.
- Use subtasks (indented items) for complex items.
- The description at the top of PLAN.md should include the
  platform, language, build system, and any constraints.
- If visual design requirements are provided, include them
  verbatim as a section in the plan so the builder knows the
  exact colors, fonts, spacing, and component styles to use.

Output ONLY the Markdown for PLAN.md. No explanation outside it.
Format:

# <Project name>

<Description with platform, language, constraints, and phase goal.>

- [ ] First task
  - [ ] Subtask if needed
- [ ] Second task
- [ ] ...
"""

_NEXT_PHASE_SYSTEM = """\
You are a senior software architect helping to plan the next phase of an application build.

Given the completed phase plan, user feedback, and (optionally) visual issues from
screenshot comparison, produce the next phase PLAN.md in Markdown. The next phase must:
- Build incrementally on what was completed in the previous phase
- Address all user feedback items
- Fix any visual issues identified in screenshot comparison
- Add the next most valuable batch of features (not everything at once)

Output ONLY the Markdown for PLAN.md. Do not add any explanation outside the Markdown.
Use the following structure exactly:

# Phase N: <short title>

## Objective
One or two sentences describing what this phase accomplishes.

## Addresses
A bullet list of user feedback items and visual issues being resolved in this phase.
Omit this section if there is no feedback or visual issues.

## Features in scope
A bullet list of new features or improvements being added.

## Implementation steps
A numbered list of concrete implementation steps. Each step must be specific enough
for a developer to act on without ambiguity.

## Success criteria
A checklist of observable outcomes that confirm this phase is complete and working.

## Out of scope
A brief bullet list of items deliberately deferred to later phases.
"""

_PLAN_FILENAME = "PLAN.md"


def _detect_next_phase_number(current_plan: str) -> int:
    """Return the next phase number inferred from *current_plan* heading."""
    match = re.search(r"#\s*Phase\s+(\d+)", current_plan, re.IGNORECASE)
    return (int(match.group(1)) + 1) if match else 2


def generate_next_phase_plan(
    current_plan: str,
    feedback: str,
    issues_text: str = "",
    *,
    client: anthropic.Anthropic | None = None,
) -> str:
    """Return the next phase PLAN.md content as a string.

    Uses Claude to generate the plan based on the completed phase plan, user
    feedback, and visual issues from screenshot comparison.

    Args:
        current_plan: Markdown content of the just-completed PLAN.md.
        feedback: User feedback collected after testing the phase.
        issues_text: Optional visual issues text (e.g. from ISSUES.md).
        client: Optional Anthropic client; a default client is created if omitted.

    Returns:
        Markdown string suitable for writing to ``PLAN.md``.
    """
    if client is None:
        client = anthropic.Anthropic()

    next_phase = _detect_next_phase_number(current_plan)

    issues_section = (
        f"\nVisual issues identified in screenshots:\n{issues_text.strip()}\n"
        if issues_text.strip()
        else "\nNo visual issues reported.\n"
    )

    user_content = f"""\
Completed phase plan:
{current_plan.strip()}

User feedback:
{feedback.strip()}
{issues_section}
Generate Phase {next_phase} PLAN.md now.
"""

    message = client.messages.create(
        model=_MODEL,
        max_tokens=2048,
        system=_NEXT_PHASE_SYSTEM,
        messages=[{"role": "user", "content": user_content}],
    )

    return message.content[0].text.strip()


def generate_phase_plan(
    source_url: str,
    features: list[Feature],
    preferences: BuildPreferences,
    phase: dict | None = None,
    *,
    project_name: str = "",
    design_section: str = "",
    client: anthropic.Anthropic | None = None,
) -> str:
    """Generate a PLAN.md for a specific roadmap phase.

    Args:
        source_url: The product URL that was scraped.
        features: All selected features.
        preferences: Build preferences.
        phase: A roadmap phase dict with phase, title, goal,
            features, and test. If None, generates a generic
            Phase 1 plan.
        project_name: Name for the project.
        design_section: Optional Markdown section with visual design
            requirements extracted from reference images.
        client: Optional Anthropic client.

    Returns:
        Markdown string suitable for writing to PLAN.md.
    """
    if client is None:
        client = anthropic.Anthropic()

    prefs_dict = dataclasses.asdict(preferences)
    constraints_text = (
        "\n".join(f"  - {c}" for c in prefs_dict["constraints"])
        if prefs_dict["constraints"]
        else "  (none)"
    )
    preferences_text = (
        "\n".join(f"  - {p}" for p in prefs_dict["preferences"])
        if prefs_dict["preferences"]
        else "  (none)"
    )

    if phase:
        phase_num = phase["phase"]
        phase_title = phase["title"]
        phase_goal = phase["goal"]
        phase_features = phase.get("features", [])
        phase_test = phase.get("test", "")
        features_text = "\n".join(f"- {name}" for name in phase_features) or "(scaffold only)"
    else:
        phase_num = 1
        phase_title = "Core"
        phase_goal = "Smallest end-to-end working thing"
        features_text = "\n".join(f"- {f.name}: {f.description}" for f in features)
        phase_test = ""

    design_block = ""
    if design_section:
        design_block = (
            f"\nVisual design requirements (from reference screenshots):\n{design_section}\n"
        )

    user_content = f"""\
Project: {project_name or source_url}
Source: {source_url}

Phase {phase_num}: {phase_title}
Goal: {phase_goal}
Test: {phase_test}

Platform: {prefs_dict["platform"]}
Language/stack: {prefs_dict["language"]}
Constraints:
{constraints_text}
Preferences:
{preferences_text}

Features for this phase:
{features_text}
{design_block}
Generate the PLAN.md now.
"""

    message = client.messages.create(
        model=_MODEL,
        max_tokens=2048,
        system=_PHASE_SYSTEM,
        messages=[
            {"role": "user", "content": user_content},
        ],
    )

    return message.content[0].text.strip()


def append_test_tasks(plan: str, test_tasks: list[str]) -> str:
    """Append documentation-example test tasks to a generated plan.

    Inserts the tasks before the final checklist item if one exists,
    or appends them at the end.
    """
    if not test_tasks:
        return plan
    return plan.rstrip() + "\n" + "\n".join(test_tasks) + "\n"


def save_plan(
    content: str,
    *,
    target_dir: Path | str = ".",
) -> Path:
    """Write *content* to ``PLAN.md`` in *target_dir*. Returns the path."""
    path = (Path(target_dir) / _PLAN_FILENAME).resolve()
    path.write_text(content + "\n", encoding="utf-8")
    return path
