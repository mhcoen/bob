"""Split-plan management: master plan, current phase, and phase transitions.

The split-plan design reduces per-session token consumption by keeping
only the active phase in CURRENT_PLAN.md while the full roadmap lives
in PLAN.md (the master). Bugs live in a standalone BUGS.md file.

Lifecycle:
  1. On startup, if CURRENT_PLAN.md is missing, extract the next
     unchecked phase from the master and write it.
  2. Sessions work against CURRENT_PLAN.md + BUGS.md only.
  3. When the current phase is fully checked off (and BUGS.md is empty),
     mark the phase complete in the master, extract the next phase,
     and write a fresh CURRENT_PLAN.md.
  4. When no more phases remain, the project is complete.
"""

from __future__ import annotations

from pathlib import Path

from mcloop.checklist import (
    BUGS_RE,
    CHECKBOX_RE,
    STAGE_RE,
    _stage_complete,
    get_stages,
    parse,
)

# Conventional filenames
MASTER_PLAN = "PLAN.md"
CURRENT_PLAN = "CURRENT_PLAN.md"
BUGS_FILE = "BUGS.md"


def extract_next_phase(master_path: Path) -> tuple[str, str] | None:
    """Find the first unchecked phase in the master and return its content.

    Returns ``(phase_name, phase_content)`` where *phase_content* is the
    raw text of the phase (header line through the line before the next
    phase header or end-of-file).

    Returns ``None`` when every phase is complete or the master has no
    tasks at all.

    For flat plans (no phase/stage headers), the entire task section
    is treated as a single implicit phase with ``phase_name=""``.
    """
    tasks = parse(master_path, check_structure=True)
    stages = get_stages(tasks)

    if not stages:
        return _extract_flat_tasks(master_path)

    for stage in stages:
        if not _stage_complete(tasks, stage):
            return _extract_stage_content(master_path, stage)

    return None


def _extract_stage_content(
    master_path: Path, target_stage: str
) -> tuple[str, str]:
    """Extract the raw lines for one stage from the master file."""
    lines = master_path.read_text().splitlines()
    start: int | None = None
    end = len(lines)

    for i, line in enumerate(lines):
        if STAGE_RE.match(line):
            stage_name = line.lstrip("#").strip()
            if stage_name == target_stage:
                start = i
            elif start is not None:
                end = i
                break
        elif start is not None and BUGS_RE.match(line):
            end = i
            break

    if start is None:
        raise ValueError(
            f"Stage '{target_stage}' not found in {master_path}"
        )

    content = "\n".join(lines[start:end]).rstrip() + "\n"
    return (target_stage, content)


def _extract_flat_tasks(master_path: Path) -> tuple[str, str] | None:
    """Extract all non-bug tasks from a flat (phaseless) plan.

    Returns ``("", content)`` if unchecked tasks exist, else ``None``.
    """
    lines = master_path.read_text().splitlines()
    task_lines: list[str] = []
    in_bugs = False
    collecting = False
    has_unchecked = False

    for line in lines:
        if BUGS_RE.match(line):
            in_bugs = True
            continue
        if in_bugs and line.strip().startswith("#"):
            in_bugs = False
        if in_bugs:
            continue
        # Skip stage headers (shouldn't exist in flat plans, but guard)
        if STAGE_RE.match(line):
            continue

        m = CHECKBOX_RE.match(line)
        if m:
            collecting = True
            if m.group(2) == " ":
                has_unchecked = True

        if collecting:
            task_lines.append(line)

    if not task_lines or not has_unchecked:
        return None

    content = "\n".join(task_lines).rstrip() + "\n"
    return ("", content)


def get_current_phase_name(current_plan_path: Path) -> str:
    """Read CURRENT_PLAN.md and return the phase/stage name.

    Returns ``""`` for flat plans (no stage header in the file).
    """
    lines = current_plan_path.read_text().splitlines()
    for line in lines:
        if STAGE_RE.match(line):
            return line.lstrip("#").strip()
    return ""


def mark_phase_complete(master_path: Path, phase_name: str) -> None:
    """Bulk-check all tasks in a phase within the master plan.

    Every ``- [ ]`` in the target phase becomes ``- [x]``. Called
    at phase transitions when CURRENT_PLAN.md is fully worked through.

    For flat plans (``phase_name=""``) this checks off every non-bug
    task in the master.
    """
    lines = master_path.read_text().splitlines()
    changed = False

    if phase_name:
        in_target = False
        for i, line in enumerate(lines):
            if STAGE_RE.match(line):
                in_target = line.lstrip("#").strip() == phase_name
            elif BUGS_RE.match(line):
                in_target = False
            elif in_target:
                m = CHECKBOX_RE.match(line)
                if m and m.group(2) == " ":
                    lines[i] = line.replace("- [ ]", "- [x]", 1)
                    changed = True
    else:
        # Flat plan: check off all non-bug tasks
        in_bugs = False
        for i, line in enumerate(lines):
            if BUGS_RE.match(line):
                in_bugs = True
                continue
            if in_bugs and line.strip().startswith("#"):
                in_bugs = False
            if in_bugs:
                continue
            m = CHECKBOX_RE.match(line)
            if m and m.group(2) == " ":
                lines[i] = line.replace("- [ ]", "- [x]", 1)
                changed = True

    if changed:
        master_path.write_text("\n".join(lines) + "\n")


def ensure_current_plan(
    master_path: Path,
    current_plan_path: Path,
) -> bool:
    """Ensure CURRENT_PLAN.md exists. Extract from master if missing.

    Returns ``True`` if a current plan is available (either pre-existing
    or freshly extracted). Returns ``False`` if the master has no
    remaining unchecked phases (project complete).

    If CURRENT_PLAN.md already exists, this function leaves it
    untouched regardless of its state. The run loop handles
    completion detection and phase transitions.
    """
    if current_plan_path.exists():
        return True

    result = extract_next_phase(master_path)
    if result is None:
        return False

    _, content = result
    current_plan_path.write_text(content)
    return True


def ensure_bugs_file(bugs_path: Path) -> None:
    """Create BUGS.md with an empty header if it does not exist."""
    if not bugs_path.exists():
        bugs_path.write_text("## Bugs\n\n")


def transition_phase(
    master_path: Path,
    current_plan_path: Path,
) -> str | None:
    """Complete the current phase and advance to the next.

    1. Reads the phase name from CURRENT_PLAN.md.
    2. Marks that phase complete in the master.
    3. Extracts the next unchecked phase from the master.
    4. Writes it to CURRENT_PLAN.md (replacing the old content).

    Returns the new phase name, or ``None`` if no more phases remain
    (project complete).
    """
    completed_phase = get_current_phase_name(current_plan_path)
    mark_phase_complete(master_path, completed_phase)

    result = extract_next_phase(master_path)
    if result is None:
        # All phases done. Remove CURRENT_PLAN.md so the next
        # startup doesn't try to resume a finished phase.
        current_plan_path.unlink(missing_ok=True)
        return None

    next_phase, content = result
    current_plan_path.write_text(content)
    return next_phase
