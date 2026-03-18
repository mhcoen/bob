"""Generate PLAN.md files for building phases of an application."""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

from duplo.claude_cli import query
from duplo.extractor import Feature
from duplo.questioner import BuildPreferences

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
- When a parent task has multiple subtasks that are all specific
  enough to be executed without design decisions (file paths,
  function names, explicit conditionals, concrete values), mark
  the parent with [BATCH] so McLoop combines them into a single
  session. Do NOT use [BATCH] on tasks whose subtasks require
  significant design decisions or architectural exploration.
  Do NOT use [BATCH] if any subtask is marked [USER] or [AUTO];
  McLoop handles this automatically by stopping the batch at
  those boundaries, but the intent should be clear in the plan.
- The description at the top of PLAN.md should include the
  platform, language, build system, and any constraints.
- If visual design requirements are provided, include them
  verbatim as a section in the plan so the builder knows the
  exact colors, fonts, spacing, and component styles to use.
- If known issues are provided, generate fix tasks for each one.
  Order fix tasks before new feature work when a feature depends
  on the fix (e.g. a broken API must be fixed before building a
  feature that calls it). Fixes that are independent of upcoming
  features can be placed wherever they fit best.
- Every task line that implements one or more features from the
  input list MUST end with a [feat: "Feature Name"] annotation.
  If a task addresses multiple features, list them comma-separated:
  [feat: "Push-to-talk recording", "Global keyboard shortcuts"].
  Tasks that fix bugs or issues use [fix: "description"] instead.
  Scaffolding or structural tasks that do not map to any feature
  use no annotation.

Output ONLY the Markdown for PLAN.md. No explanation outside it.
Format:

# <AppName> — Phase N: <Title>

<Description with platform, language, constraints, and phase goal.>

- [ ] Set up project structure and build system
- [ ] [BATCH] Add user authentication [feat: "User authentication"]
  - [ ] Create `AuthService.swift` with `login(email:password:)` and `signup(email:password:)` methods
  - [ ] Add `LoginView.swift` with email/password fields and submit button
  - [ ] Wire `AuthService` into the app lifecycle, store session token in Keychain
- [ ] Fix input validation on signup [fix: "email format not checked"]
- [ ] ...

The heading MUST use the exact format shown: app name, em dash (—),
Phase N, colon, then the phase title. Use the project name as the
app name. The phase number and title are provided in the prompt.
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
- Every step that implements one or more features MUST end with a
  [feat: "Feature Name"] annotation. If a step addresses multiple
  features, list them comma-separated:
  [feat: "Push-to-talk recording", "Global keyboard shortcuts"].
  Steps that fix bugs or visual issues use [fix: "description"].
  Scaffolding or structural steps that do not map to any feature
  use no annotation.
- When a step has multiple subtasks that are all specific enough
  to be executed without design decisions, mark the parent step
  with [BATCH] so McLoop combines the subtasks into a single
  session for efficiency.

## Success criteria
A checklist of observable outcomes that confirm this phase is complete and working.

## Out of scope
A brief bullet list of items deliberately deferred to later phases.
"""

_PLAN_FILENAME = "PLAN.md"


@dataclasses.dataclass
class CompletedTask:
    """A checked task line parsed from PLAN.md."""

    text: str
    features: list[str] = dataclasses.field(default_factory=list)
    fixes: list[str] = dataclasses.field(default_factory=list)
    indent: int = 0


def parse_completed_tasks(plan_content: str) -> list[CompletedTask]:
    """Parse checked task lines from PLAN.md content.

    Finds all ``- [x]`` (case-insensitive) lines and extracts:
    - The task description text (without the checkbox prefix and annotation suffix)
    - Any ``[feat: "..."]`` feature annotations
    - Any ``[fix: "..."]`` fix annotations
    - The indentation level (number of leading spaces)

    Args:
        plan_content: Full Markdown content of PLAN.md.

    Returns:
        List of :class:`CompletedTask` for each checked line, in order.
    """
    tasks: list[CompletedTask] = []
    for line in plan_content.splitlines():
        stripped = line.lstrip()
        if not (stripped.startswith("- [x]") or stripped.startswith("- [X]")):
            continue
        indent = len(line) - len(stripped)
        # Remove the checkbox prefix.
        body = stripped[5:].strip()
        # Extract annotations.
        features: list[str] = []
        fixes: list[str] = []
        anno_match = re.search(
            r"\[(feat|fix):\s*(\"[^\"]+\"(?:,\s*\"[^\"]+\")*)\]\s*$",
            body,
        )
        if anno_match:
            kind = anno_match.group(1)
            raw_names = re.findall(r"\"([^\"]+)\"", anno_match.group(2))
            if kind == "feat":
                features = raw_names
            else:
                fixes = raw_names
            body = body[: anno_match.start()].rstrip()
        tasks.append(
            CompletedTask(
                text=body,
                features=features,
                fixes=fixes,
                indent=indent,
            )
        )
    return tasks


def _detect_next_phase_number(current_plan: str) -> int:
    """Return the next phase number inferred from *current_plan* heading."""
    match = re.search(r"#\s*.*?Phase\s+(\d+)", current_plan, re.IGNORECASE)
    return (int(match.group(1)) + 1) if match else 2


def generate_next_phase_plan(
    current_plan: str,
    feedback: str,
    issues_text: str = "",
) -> str:
    """Return the next phase PLAN.md content as a string.

    Uses ``claude -p`` to generate the plan based on the completed phase
    plan, user feedback, and visual issues from screenshot comparison.

    Args:
        current_plan: Markdown content of the just-completed PLAN.md.
        feedback: User feedback collected after testing the phase.
        issues_text: Optional visual issues text (e.g. from ISSUES.md).

    Returns:
        Markdown string suitable for writing to ``PLAN.md``.
    """
    next_phase = _detect_next_phase_number(current_plan)

    issues_section = (
        f"\nVisual issues identified in screenshots:\n{issues_text.strip()}\n"
        if issues_text.strip()
        else "\nNo visual issues reported.\n"
    )

    prompt = f"""\
Completed phase plan:
{current_plan.strip()}

User feedback:
{feedback.strip()}
{issues_section}
Generate Phase {next_phase} PLAN.md now.
"""

    return query(prompt, system=_NEXT_PHASE_SYSTEM)


def generate_phase_plan(
    source_url: str,
    features: list[Feature],
    preferences: BuildPreferences,
    phase: dict | None = None,
    *,
    project_name: str = "",
    design_section: str = "",
    phase_number: int | None = None,
    spec_text: str = "",
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
        phase_number: Override for the phase number in the heading.
            When provided, this is used instead of ``phase["phase"]``.
            Derived from the length of the ``phases`` history + 1.

    Returns:
        Markdown string suitable for writing to PLAN.md.
    """
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
        phase_num = phase_number if phase_number is not None else phase["phase"]
        phase_title = phase["title"]
        phase_goal = phase["goal"]
        phase_features = phase.get("features", [])
        phase_test = phase.get("test", "")
        phase_issues = phase.get("issues", [])
        features_text = "\n".join(f"- {name}" for name in phase_features) or "(scaffold only)"
    else:
        phase_num = phase_number if phase_number is not None else 1
        phase_title = "Core"
        phase_goal = "Smallest end-to-end working thing"
        features_text = "\n".join(f"- {f.name}: {f.description}" for f in features)
        phase_test = ""
        phase_issues = []

    design_block = ""
    if design_section:
        design_block = (
            f"\nVisual design requirements (from reference screenshots):\n{design_section}\n"
        )

    issues_block = ""
    if phase_issues:
        issues_text = "\n".join(f"- {desc}" for desc in phase_issues)
        issues_block = f"\nKnown issues to fix in this phase:\n{issues_text}\n"

    spec_block = ""
    if spec_text:
        spec_block = (
            "\nProduct specification (authoritative, from the user):\n"
            f"{spec_text}\n"
        )

    prompt = f"""\
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
{design_block}{issues_block}{spec_block}
Generate the PLAN.md now.
"""

    return query(prompt, system=_PHASE_SYSTEM)


def append_test_tasks(plan: str, test_tasks: list[str]) -> str:
    """Append documentation-example test tasks to a generated plan.

    Inserts the tasks before the final checklist item if one exists,
    or appends them at the end.
    """
    if not test_tasks:
        return plan
    lines = plan.rstrip().split("\n")
    # Find the last checklist item to insert before it.
    last_check_idx = None
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].lstrip().startswith("- ["):
            last_check_idx = i
            break
    if last_check_idx is not None:
        before = lines[:last_check_idx]
        after = lines[last_check_idx:]
        return "\n".join(before + test_tasks + after) + "\n"
    return "\n".join(lines) + "\n" + "\n".join(test_tasks) + "\n"


def save_plan(
    content: str,
    *,
    target_dir: Path | str = ".",
) -> Path:
    """Write *content* to ``PLAN.md`` in *target_dir*.

    If PLAN.md already exists, new content is appended after a blank
    line so that existing checked and unchecked items are preserved.
    Returns the path.
    """
    path = (Path(target_dir) / _PLAN_FILENAME).resolve()
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        path.write_text(existing.rstrip("\n") + "\n\n" + content + "\n", encoding="utf-8")
    else:
        path.write_text(content + "\n", encoding="utf-8")
    return path
