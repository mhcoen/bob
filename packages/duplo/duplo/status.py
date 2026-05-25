"""Display and progress-status helpers used by duplo's CLI flow.

These are the user-facing print helpers and PLAN.md state predicates.
They are split out of ``duplo.main`` so the entry-point module stays
focused on argument parsing and subcommand dispatch.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

from duplo.extractor import Feature
from duplo.saver import get_current_phase

_FEATURE_FIELDS = {fld.name for fld in dataclasses.fields(Feature)}


def _feature_from_dict(d: dict) -> Feature:
    """Build a :class:`Feature` from a raw dict, ignoring unknown keys."""
    return Feature(**{k: v for k, v in d.items() if k in _FEATURE_FIELDS})


@dataclasses.dataclass
class UpdateSummary:
    """Accumulates what was found and added during a subsequent run."""

    files_added: int = 0
    files_changed: int = 0
    files_removed: int = 0
    images_analyzed: int = 0
    videos_found: int = 0
    video_frames_extracted: int = 0
    pdfs_extracted: int = 0
    text_files_read: int = 0
    pages_rescraped: int = 0
    examples_rescraped: int = 0
    new_features: int = 0
    missing_features: int = 0
    missing_examples: int = 0
    design_refinements: int = 0
    tasks_appended: int = 0
    collected_text: str = ""


def _partition_features(
    data: dict,
) -> tuple[list[Feature], list[Feature]]:
    """Split features into implemented and remaining lists.

    Returns ``(implemented, remaining)`` where *implemented* contains
    features with ``status == "implemented"`` and *remaining* contains
    everything else (``"pending"``, ``"partial"``, or missing status).
    """
    implemented: list[Feature] = []
    remaining: list[Feature] = []
    for f in data.get("features", []):
        feat = _feature_from_dict(f)
        if f.get("status", "pending") == "implemented":
            implemented.append(feat)
        else:
            remaining.append(feat)
    return implemented, remaining


def _print_feature_status(data: dict) -> None:
    """Print a summary of implemented vs remaining features."""
    implemented, remaining = _partition_features(data)
    total = len(implemented) + len(remaining)
    if total == 0:
        return
    print(f"\nFeature status: {len(implemented)}/{total} implemented")
    if implemented:
        print("  Implemented:")
        for f in implemented:
            phase = f" ({f.implemented_in})" if f.implemented_in else ""
            print(f"    - {f.name}{phase}")
    if remaining:
        print("  Remaining:")
        for f in remaining:
            status = f.status if f.status != "pending" else ""
            label = f" [{status}]" if status else ""
            print(f"    - {f.name}{label}")


def _print_status(data: dict, *, plan_exists: bool = False) -> None:
    """Print current phase number, features implemented vs remaining, and open issues."""
    phases_completed = len(data.get("phases", []))
    current_phase = phases_completed + 1

    implemented, remaining = _partition_features(data)
    total = len(implemented) + len(remaining)

    issues = data.get("issues", [])
    open_issues = [i for i in issues if i.get("status", "open") == "open"]

    app_name = data.get("app_name", "")
    prefix = f"{app_name}: " if app_name else ""
    if phases_completed > 0:
        phase_part = f"Phase {phases_completed} complete"
    elif plan_exists:
        phase_part = f"Phase {current_phase} in progress"
    else:
        phase_part = f"Ready to generate Phase {current_phase}"
    issue_part = f", {len(open_issues)} open issues" if open_issues else ""
    print(f"\n{prefix}{phase_part}. {len(implemented)}/{total} features implemented{issue_part}.")


def _print_summary(summary: UpdateSummary) -> None:
    """Print a consolidated summary of what was found and added."""
    found_lines: list[str] = []
    if summary.files_added or summary.files_changed or summary.files_removed:
        parts = []
        if summary.files_added:
            parts.append(f"{summary.files_added} added")
        if summary.files_changed:
            parts.append(f"{summary.files_changed} changed")
        if summary.files_removed:
            parts.append(f"{summary.files_removed} removed")
        found_lines.append(f"  Files: {', '.join(parts)}")
    if summary.images_analyzed:
        found_lines.append(f"  Images analyzed: {summary.images_analyzed}")
    if summary.videos_found:
        found_lines.append(f"  Videos found: {summary.videos_found}")
    if summary.video_frames_extracted:
        found_lines.append(f"  Video frames extracted: {summary.video_frames_extracted}")
    if summary.pdfs_extracted:
        found_lines.append(f"  PDFs extracted: {summary.pdfs_extracted}")
    if summary.text_files_read:
        found_lines.append(f"  Text files read: {summary.text_files_read}")
    if summary.pages_rescraped:
        found_lines.append(f"  Pages re-scraped: {summary.pages_rescraped}")
    if summary.examples_rescraped:
        found_lines.append(f"  Code examples updated: {summary.examples_rescraped}")
    if summary.new_features:
        found_lines.append(f"  New features extracted: {summary.new_features}")

    added_lines: list[str] = []
    if summary.missing_features:
        added_lines.append(f"  Missing features: {summary.missing_features}")
    if summary.missing_examples:
        added_lines.append(f"  Missing examples: {summary.missing_examples}")
    if summary.design_refinements:
        added_lines.append(f"  Design refinements: {summary.design_refinements}")
    if summary.tasks_appended:
        added_lines.append(f"  Tasks appended to PLAN.md: {summary.tasks_appended}")

    if not found_lines and not added_lines:
        print("\nSummary: No changes detected, nothing added.")
        return

    print("\n--- Update summary ---")
    if found_lines:
        print("Found:")
        for line in found_lines:
            print(line)
    if added_lines:
        print("Added:")
        for line in added_lines:
            print(line)
    if not added_lines:
        print("No new tasks added.")
    print("---------------------")


def _current_phase_content(content: str) -> str:
    """Return lines belonging to the current phase section in PLAN.md.

    Looks for a heading matching ``# ... Phase N: ...`` where *N* is the
    current phase number from duplo.json.  Returns text from that heading
    to the next phase heading (or end of file).  If no matching heading is
    found, returns the full content as a fallback.
    """
    phase_num, _ = get_current_phase()
    if phase_num == 0:
        return content

    lines = content.splitlines(keepends=True)
    phase_pattern = re.compile(rf"^#\s+.*(?:Phase|Stage)\s+{phase_num}\s*:", re.IGNORECASE)
    next_phase_pattern = re.compile(r"^#\s+.*(?:Phase|Stage)\s+\d+\s*:", re.IGNORECASE)

    start: int | None = None
    end: int | None = None
    for idx, line in enumerate(lines):
        if start is None:
            if phase_pattern.match(line):
                start = idx
        else:
            if next_phase_pattern.match(line):
                end = idx
                break

    if start is None:
        return content  # heading not found - fall back to full file
    return "".join(lines[start:end])


def _plan_is_complete() -> bool:
    """Return True if PLAN.md exists and all checkboxes are checked."""
    plan_path = Path("PLAN.md")
    if not plan_path.exists():
        return False
    content = plan_path.read_text(encoding="utf-8")
    section = _current_phase_content(content)
    has_tasks = False
    for line in section.splitlines():
        stripped = line.strip()
        if stripped.startswith("- [ ]") or stripped.startswith("- [!]"):
            return False
        if stripped.startswith("- [x]") or stripped.startswith("- [X]"):
            has_tasks = True
    return has_tasks


def _plan_has_unchecked_tasks() -> bool:
    """Return True if PLAN.md exists and contains at least one unchecked task."""
    plan_path = Path("PLAN.md")
    if not plan_path.exists():
        return False
    content = plan_path.read_text(encoding="utf-8")
    section = _current_phase_content(content)
    for line in section.splitlines():
        stripped = line.strip()
        if stripped.startswith("- [ ]") or stripped.startswith("- [!]"):
            return True
    return False


def _plan_ready(phase_label: str) -> None:
    """Print a message telling the user to run mcloop."""
    print(f"\nPlan ready for {phase_label}.")
    print("Run mcloop to start building.")
