"""Markdown checklist parser. Reads and writes `- [ ]` items."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

CHECKBOX_RE = re.compile(r"^(\s*)- \[([ xX!])\] (.+)$")
_TASK_ID_RE = re.compile(r"^(T-\d{6}):\s*(.*)$")
# Section headers: any heading level (# or more) whose title contains
# "Stage N" or "Phase N" anywhere in the line. The number is captured.
STAGE_RE = re.compile(r"^#+\s+.*?\b(?:stage|phase)\s+(\d+)\b", re.IGNORECASE)
# Bugs header: any heading level followed by "Bugs" as the title.
BUGS_RE = re.compile(r"^#+\s+Bugs\s*$", re.IGNORECASE)
# Generic heading (any level). Used as a body-collection boundary for
# multi-line [USER] task bodies.
_ANY_HEADING_RE = re.compile(r"^#+\s")
# H1 headers (single `#` followed by space). Used by the structural
# sanity check to detect duplicated top-level headings (a hallmark
# corruption pattern in PLAN.md files that have been bad-edited).
_H1_RE = re.compile(r"^#\s+(.+)$")
# Pattern for extracting a stage/phase number from a stage string
# (the header text with leading # and whitespace stripped).
_STAGE_NUM_RE = re.compile(r"\b(?:stage|phase)\s+(\d+)\b", re.IGNORECASE)
_USER_TAG = "[USER]"
_BATCH_TAG = "[BATCH]"
_AUTO_TAG_RE = re.compile(r"\[AUTO:(\w+)\]")


def _strip_task_id(text: str) -> tuple[str | None, str]:
    """Split a leading canonical task id from checkbox text, if present."""
    match = _TASK_ID_RE.match(text)
    if match is None:
        return None, text
    return match.group(1), match.group(2).strip()


def _checkbox_task_text(line: str) -> str | None:
    """Return checkbox task text without a leading canonical id."""
    match = CHECKBOX_RE.match(line)
    if match is None:
        return None
    _, text = _strip_task_id(match.group(3).strip())
    return text


class PlanCorruptionError(Exception):
    """Raised when parse() detects structural corruption in a checklist.

    The check is conservative: only flags anomalies that are very
    unlikely to be intentional (duplicate top-level headings, multiple
    ``## Bugs`` sections, repeated phase/stage numbers). Auto-fixing
    is deliberately not attempted; an attempt to silently "correct"
    structural corruption is exactly the operation that produces it.

    Callers that intentionally pass malformed input (tests, dry-run
    inspection of an in-progress edit) can pass ``check_structure=False``
    to ``parse()``.
    """


@dataclass
class Task:
    text: str
    checked: bool
    failed: bool
    line_number: int
    indent_level: int
    stage: str = ""
    children: list[Task] = field(default_factory=list)
    eliminated: list[str] = field(default_factory=list)
    body: str = ""
    task_id: str | None = None
    flag_tags: tuple[str, ...] = ()
    action_tag: tuple[str, str] | None = None


def count_unchecked(tasks: list[Task]) -> int:
    """Recursively count unchecked, non-failed tasks."""
    n = 0
    for t in tasks:
        if not t.checked and not t.failed:
            n += 1
        n += count_unchecked(t.children)
    return n


def parse_description(path: str | Path) -> str:
    """Extract prose before the first checkbox as a project description."""
    lines = Path(path).read_text().splitlines()
    desc_lines = []
    for line in lines:
        if CHECKBOX_RE.match(line):
            break
        desc_lines.append(line)
    return "\n".join(desc_lines).strip()


def _check_structural_sanity(lines: list[str], path: str | Path) -> None:
    """Scan a checklist file for structural corruption signals.

    Three anomalies are flagged. Each has been observed in real
    PLAN.md corruption incidents and none has a legitimate use:

    1. Duplicate H1 headings with identical title text — typically
       indicates a botched insertion that left the original tail in
       place, producing two copies of the document's top header.
    2. Multiple ``## Bugs`` sections (any heading level) — there
       should only ever be one.
    3. Duplicate phase/stage numbers across stage headers — e.g. two
       different headers both numbered ``Phase 2``. This breaks
       ``task_label`` (which prefixes labels with the stage number)
       and almost always signals merged content from two attempts at
       the same phase.

    Raises ``PlanCorruptionError`` listing every anomaly found, with
    line numbers, so the user can locate and fix the corruption.
    """
    h1_titles: dict[str, list[int]] = {}
    bugs_lines: list[int] = []
    stage_nums: dict[str, list[int]] = {}

    for i, line in enumerate(lines):
        # Order matters: STAGE_RE matches lines that are also H1s
        # (e.g. ``# Phase 1: Bootstrapping``). A header is either a
        # stage header OR a plain H1 OR a Bugs header — never two of
        # those at once. Check stage first so we don't double-count
        # ``# Phase N`` as both an H1 duplicate and a stage duplicate.
        stage_match = STAGE_RE.match(line)
        if stage_match:
            num = stage_match.group(1)
            stage_nums.setdefault(num, []).append(i)
            continue

        if BUGS_RE.match(line):
            bugs_lines.append(i)
            continue

        h1_match = _H1_RE.match(line)
        if h1_match:
            # Only single-# headings (true H1s). _H1_RE already enforces
            # this; check that it's not actually a deeper heading caught
            # by accident.
            if line.startswith("# ") and not line.startswith("## "):
                title = h1_match.group(1).strip()
                h1_titles.setdefault(title, []).append(i)

    problems: list[str] = []

    for title, line_nums in h1_titles.items():
        if len(line_nums) > 1:
            locs = ", ".join(str(n + 1) for n in line_nums)
            problems.append(f"duplicate top-level heading '# {title}' at lines {locs}")

    if len(bugs_lines) > 1:
        locs = ", ".join(str(n + 1) for n in bugs_lines)
        problems.append(f"multiple Bugs sections at lines {locs}")

    for num, line_nums in stage_nums.items():
        if len(line_nums) > 1:
            locs = ", ".join(str(n + 1) for n in line_nums)
            problems.append(f"duplicate Phase/Stage {num} at lines {locs}")

    if problems:
        joined = "\n  - ".join(problems)
        raise PlanCorruptionError(
            f"Structural corruption detected in {path}:\n  - {joined}\n"
            "Fix the file manually. mcloop will not modify a corrupted"
            " PLAN.md because doing so risks compounding the corruption."
            " If the apparent corruption is intentional, pass"
            " check_structure=False to parse()."
        )


def parse(path: str | Path, check_structure: bool = True) -> list[Task]:
    """Read a markdown file and return a tree of Task objects.

    Section headers are any markdown heading (``#``, ``##``, ...)
    whose title contains ``Stage N`` or ``Phase N`` anywhere in
    the line.  Tasks under such a header are tagged with the full
    header text (minus leading ``#`` and whitespace).  Tasks
    before any section header have stage ``""``.

    The ``## Bugs`` header (any heading level) is treated as a
    special section with stage ``"Bugs"``.

    When ``check_structure`` is True (the default), the file is
    scanned for structural corruption first and ``PlanCorruptionError``
    is raised on detection. Set ``check_structure=False`` to skip
    the check (used by tests that intentionally pass malformed input).
    """
    lines = Path(path).read_text().splitlines()
    if check_structure:
        _check_structural_sanity(lines, path)
    root_tasks: list[Task] = []
    stack: list[Task] = []
    current_stage = ""

    for i, line in enumerate(lines):
        # Detect section headers (any heading level containing
        # "Stage N" or "Phase N")
        if STAGE_RE.match(line):
            current_stage = line.lstrip("#").strip()
            stack.clear()
            continue

        # Detect Bugs header (any heading level)
        if BUGS_RE.match(line):
            current_stage = "Bugs"
            stack.clear()
            continue

        # Collect [RULEDOUT] lines and attach to the parent task.
        # The line's indentation determines which task it belongs to:
        # it attaches to the nearest task with strictly less indentation.
        stripped = line.strip()
        if stripped.startswith("[RULEDOUT]"):
            elim_indent = len(line) - len(line.lstrip())
            for t in reversed(stack):
                if t.indent_level < elim_indent:
                    t.eliminated.append(stripped)
                    break
            else:
                # No parent found (top-level [RULEDOUT]), attach
                # to the most recent root task if one exists.
                if root_tasks:
                    root_tasks[-1].eliminated.append(stripped)
            continue

        m = CHECKBOX_RE.match(line)
        if not m:
            continue

        indent = len(m.group(1))
        marker = m.group(2)
        checked = marker in ("x", "X")
        failed = marker == "!"
        task_id, text = _strip_task_id(m.group(3).strip())
        task = Task(
            text=text,
            checked=checked,
            failed=failed,
            line_number=i,
            indent_level=indent,
            stage=current_stage,
            task_id=task_id,
        )
        if text == _USER_TAG or text.startswith(f"{_USER_TAG} "):
            task.body = _collect_body(lines, i + 1)

        while stack and stack[-1].indent_level >= indent:
            stack.pop()

        if stack:
            stack[-1].children.append(task)
        else:
            root_tasks.append(task)

        stack.append(task)

    return root_tasks


def get_eliminated(tasks: list[Task], target: Task) -> list[str]:
    """Collect all [RULEDOUT] entries for a task, including from ancestors.

    Walks the task tree to find the target and collects eliminated
    entries from every ancestor along the path.
    """
    result: list[str] = []

    def _search(task_list: list[Task], inherited: list[str]) -> bool:
        for task in task_list:
            combined = inherited + task.eliminated
            if task is target:
                result.extend(combined)
                return True
            if task.children and _search(task.children, combined):
                return True
        return False

    _search(tasks, [])
    return result


def find_parent(tasks: list[Task], target: Task) -> Task | None:
    """Find the parent of a target task in the tree.

    Returns None if the target is a root task.
    """

    def _search(task_list: list[Task]) -> Task | None:
        for task in task_list:
            if target in task.children:
                return task
            found = _search(task.children)
            if found is not None:
                return found
        return None

    return _search(tasks)


def get_stages(tasks: list[Task]) -> list[str]:
    """Return ordered list of unique stage names found in tasks.

    Returns ``[]`` if no tasks have stage labels (flat plan).
    """
    seen: set[str] = set()
    stages: list[str] = []

    def _collect(task_list: list[Task]) -> None:
        for task in task_list:
            if task.stage and task.stage != "Bugs" and task.stage not in seen:
                seen.add(task.stage)
                stages.append(task.stage)
            _collect(task.children)

    _collect(tasks)
    return stages


def _stage_complete(tasks: list[Task], stage: str) -> bool:
    """Return True if all tasks in the given stage are checked.

    Failed tasks ([!]) do NOT count as complete. A stage with
    failed tasks is stuck, not done.
    """

    def _check(task_list: list[Task]) -> bool:
        for task in task_list:
            if task.stage == stage:
                if not task.checked:
                    return False
            if not _check(task.children):
                return False
        return True

    return _check(tasks)


def current_stage(tasks: list[Task]) -> str | None:
    """Return the name of the first incomplete stage.

    Returns ``None`` if all stages are complete or there are no
    stages.
    """
    stages = get_stages(tasks)
    if not stages:
        return None
    for stage in stages:
        if not _stage_complete(tasks, stage):
            return stage
    return None


def stage_status(tasks: list[Task]) -> str:
    """Return a status string for the summary.

    Possible values:
    - ``"no_stages"``: plan has no stage headers
    - ``"in_progress"``: stages exist but none completed yet
    - ``"stage_complete:<name>"``: a stage just finished,
      more stages remain
    - ``"all_complete"``: all stages are done
    """
    stages = get_stages(tasks)
    if not stages:
        return "no_stages"

    last_complete = None
    for stage in stages:
        if _stage_complete(tasks, stage):
            last_complete = stage
        else:
            if last_complete:
                return f"stage_complete:{last_complete}"
            return "in_progress"

    return "all_complete"


def _search_tasks(
    task_list: list[Task],
    *,
    is_subtask: bool = False,
    required_stage: str | None = None,
    skip_stages: set[str] | None = None,
) -> Task | None:
    """Depth-first search for the next unchecked leaf task.

    Shared logic used by both ``find_next`` and ``_search_in_stage``.
    *required_stage* restricts matches to tasks in that stage.
    *skip_stages* skips tasks in those stages.
    """
    for task in task_list:
        # A failed subtask blocks all later siblings under the
        # same parent (implicit sequential dependency).
        # Root-level tasks are treated as independent.
        if task.failed:
            if is_subtask:
                return None
            continue

        if task.checked:
            continue

        if skip_stages and task.stage in skip_stages:
            continue

        if required_stage is not None and task.stage != required_stage:
            continue

        if task.children:
            child = _search_tasks(
                task.children,
                is_subtask=True,
                required_stage=required_stage,
                skip_stages=skip_stages,
            )
            if child:
                return child
            # Don't return the parent if any child failed;
            # the parent can never complete in that state.
            if any(c.failed for c in task.children):
                if is_subtask:
                    return None
                continue
            return task

        return task
    return None


def has_unchecked_bugs(tasks: list[Task]) -> bool:
    """Return True if there are unchecked bug tasks.

    Accepts any of these shapes:
    - Tasks under a ``## Bugs`` header (any heading level, any case).
    - A standalone bug file with no stage/phase headers: every
      unchecked task counts as a bug. This covers files with no
      header, a non-matching header (``## Known bugs``,
      ``## Open bugs``, ``# Bugs to fix``), or a bare checklist.
    """
    if _search_in_stage(tasks, "Bugs") is not None:
        return True
    if get_stages(tasks):
        return False

    def _any_unchecked(task_list: list[Task]) -> bool:
        for t in task_list:
            if not t.checked and not t.failed:
                return True
            if _any_unchecked(t.children):
                return True
        return False

    return _any_unchecked(tasks)


def find_next(tasks: list[Task]) -> Task | None:
    """Depth-first search for the next unchecked leaf task.

    Bug tasks (under ``## Bugs``) have absolute priority and are
    returned before any feature/stage tasks.

    If the plan uses stages (``## Stage N:`` headers), only
    returns tasks from the first incomplete stage.  Returns
    ``None`` when the current stage is fully complete, even if
    later stages have unchecked tasks.
    """
    # Priority: bug tasks first
    bug_task = _search_in_stage(tasks, "Bugs")
    if bug_task:
        return bug_task

    active_stage = current_stage(tasks)
    has_stages = len(get_stages(tasks)) > 0

    # Skip bug tasks (already handled above) and non-active stages
    skip_stages = {"Bugs"}

    return _search_tasks(
        tasks,
        required_stage=active_stage if has_stages else None,
        skip_stages=skip_stages,
    )


def _search_in_stage(tasks: list[Task], stage: str) -> Task | None:
    """Search for the next unchecked leaf in a specific stage."""

    def _search(task_list: list[Task], is_subtask: bool = False) -> Task | None:
        return _search_tasks(task_list, is_subtask=is_subtask, required_stage=stage)

    return _search(tasks)


def _find_task_line(lines: list[str], task: Task) -> int:
    """Find task line by line_number, falling back to text match.

    Primary key is the line_number recorded at parse time.  If the
    file was modified between parse and check-off (e.g. Claude Code
    edited PLAN.md during execution), the stored line_number may be
    stale, so we fall back to searching by text.
    """
    # Primary: use stored line_number if it still points to the right task.
    # Validate text, indent_level, and stage to detect stale line numbers
    # (e.g. file was edited externally and lines shifted).
    if task.line_number < len(lines):
        m = CHECKBOX_RE.match(lines[task.line_number])
        if (
            m
            and _checkbox_task_text(lines[task.line_number]) == task.text
            and len(m.group(1)) == task.indent_level
        ):
            # Verify stage by scanning headers above this line
            line_stage = ""
            for j in range(task.line_number):
                if STAGE_RE.match(lines[j]):
                    line_stage = lines[j].lstrip("#").strip()
                elif BUGS_RE.match(lines[j]):
                    line_stage = "Bugs"
            if line_stage == task.stage:
                return task.line_number

    # Fallback: text search with indent_level and stage validation.
    # Track the current stage as we scan so we can disambiguate
    # duplicate task texts that appear in different stages or at
    # different indentation levels.
    # Collect all candidates and pick the one nearest to the original
    # line_number, preferring unchecked tasks.
    current_stage = ""
    unchecked: list[int] = []
    checked: list[int] = []
    for i, line in enumerate(lines):
        if STAGE_RE.match(line):
            current_stage = line.lstrip("#").strip()
            continue
        if BUGS_RE.match(line):
            current_stage = "Bugs"
            continue
        m = CHECKBOX_RE.match(line)
        if not m or _checkbox_task_text(line) != task.text:
            continue
        indent = len(m.group(1))
        if indent != task.indent_level:
            continue
        if current_stage != task.stage:
            continue
        if m.group(2) == " ":
            unchecked.append(i)
        else:
            checked.append(i)
    # Prefer unchecked matches; fall back to checked ones.
    candidates = unchecked or checked
    if candidates:
        return min(candidates, key=lambda idx: abs(idx - task.line_number))

    raise IndexError(
        f"Task not found: line {task.line_number} stale and no text match for '{task.text}'"
    )


def check_off(path: str | Path, task: Task) -> None:
    """Rewrite `- [ ]` to `- [x]` at the task's line number.

    Also auto-checks parent tasks when all their children are done.
    If the task cannot be found (e.g. file was overwritten during
    execution), prints a warning instead of crashing.
    """
    p = Path(path)
    lines = p.read_text().splitlines()
    try:
        _check_line(lines, _find_task_line(lines, task))
    except (IndexError, ValueError):
        print(
            f"Warning: could not check off task (file may have been modified): {task.text}",
            flush=True,
        )
        return

    p.write_text("\n".join(lines) + "\n")
    try:
        _auto_check_parents(p)
    except (IndexError, ValueError):
        pass


def mark_failed(path: str | Path, task: Task) -> None:
    """Rewrite `- [ ]` or `- [x]` to `- [!]` at the task's line number.

    Claude Code sometimes checks off a task during execution before
    mcloop's post-task checks run.  If checks then fail, the line
    will contain ``- [x]`` rather than ``- [ ]``.  Handle both.
    """
    p = Path(path)
    lines = p.read_text().splitlines()
    try:
        idx = _find_task_line(lines, task)
    except IndexError:
        print(
            f"Warning: could not mark task as failed (file may have been modified): {task.text}",
            flush=True,
        )
        return
    line = lines[idx]
    if "- [ ]" in line:
        new_line = line.replace("- [ ]", "- [!]", 1)
    elif "- [x]" in line or "- [X]" in line:
        new_line = re.sub(r"- \[[xX]\]", "- [!]", line, count=1)
    else:
        new_line = line
    if new_line == line:
        print(
            f"Warning: could not mark task as failed (no checkbox found): {task.text}",
            flush=True,
        )
        return
    lines[idx] = new_line
    p.write_text("\n".join(lines) + "\n")


def clear_failed_markers(path: str | Path) -> int:
    """Rewrite every `- [!]` back to `- [ ]` in *path*.

    Returns the number of lines changed. Missing files return 0.
    Used by ``mcloop --retry`` to reset previously-failed tasks so the
    loop picks them up again on the next run. The file is only written
    when at least one line actually changes, to avoid spurious mtime
    bumps.
    """
    p = Path(path)
    if not p.exists():
        return 0
    lines = p.read_text().splitlines()
    changed = 0
    # Anchor to checkbox syntax: optional indent, the dash, a space, the
    # `[!]` marker, then a space. Matching anywhere in the line corrupted
    # prose that happened to contain the literal "- [!]" sequence.
    failed_marker_re = re.compile(r"^(\s*)- \[!\] ")
    for i, line in enumerate(lines):
        new_line = failed_marker_re.sub(r"\1- [ ] ", line, count=1)
        if new_line != line:
            lines[i] = new_line
            changed += 1
    if changed:
        p.write_text("\n".join(lines) + "\n")
    return changed


def _check_line(lines: list[str], line_number: int) -> None:
    """Replace `- [ ]` with `- [x]` on the given line."""
    line = lines[line_number]
    lines[line_number] = line.replace("- [ ]", "- [x]", 1)


def is_user_task(task: Task) -> bool:
    """Return True if the task requires user observation.

    Tasks whose text begins with ``[USER]`` require the user to
    perform an action and report back what they observed. Mentions of
    ``[USER]`` later in prose are descriptive text, not operational
    markers.
    """
    text = task.text.strip()
    return text == _USER_TAG or text.startswith(f"{_USER_TAG} ")


def _collect_body(lines: list[str], start: int) -> str:
    """Gather contiguous non-checkbox, non-heading lines for a [USER] body.

    Stops at the next checkbox or any heading. Leading and trailing blank
    lines are trimmed; internal blank lines and original indentation are
    preserved (no reflow).
    """
    body_lines: list[str] = []
    j = start
    while j < len(lines):
        line = lines[j]
        if CHECKBOX_RE.match(line) or _ANY_HEADING_RE.match(line):
            break
        body_lines.append(line)
        j += 1
    while body_lines and not body_lines[0].strip():
        body_lines.pop(0)
    while body_lines and not body_lines[-1].strip():
        body_lines.pop()
    return "\n".join(body_lines)


def user_task_instructions(task: Task) -> str:
    """Extract the instruction text from a [USER] task.

    Removes a leading [USER] tag from the checkbox line and appends any
    multi-line body captured at parse time, preserving newlines so the
    banner can render it line-structured.
    """
    text = task.text.strip()
    if text == _USER_TAG:
        head = ""
    elif text.startswith(f"{_USER_TAG} "):
        head = text[len(_USER_TAG) :].strip()
    else:
        head = text
    if task.body:
        return f"{head}\n{task.body}" if head else task.body
    return head


def is_batch_task(task: Task) -> bool:
    """Return True if the task is marked for batch execution.

    Tasks marked with [BATCH] in their text have all their
    unchecked children combined into a single session.
    """
    return _BATCH_TAG in task.text


def get_batch_children(task: Task) -> list[Task]:
    """Collect consecutive unchecked children eligible for batching.

    Starts from the first unchecked child and collects until
    hitting a [USER] or [AUTO] task, or running out of children.
    Already checked children are skipped.  Failed children are
    skipped when they appear before any non-failed child, but once
    at least one non-failed child has been seen (checked or collected)
    a failed child stops collection (the failure is a dependency
    barrier).
    """
    batch: list[Task] = []
    seen_non_failed = False
    for child in task.children:
        if child.checked:
            seen_non_failed = True
            continue
        if child.failed:
            if batch or seen_non_failed:
                break
            continue
        if is_user_task(child) or is_auto_task(child):
            break
        batch.append(child)
    return batch


def is_auto_task(task: Task) -> bool:
    """Return True if the task is an automated observation.

    Tasks marked with [AUTO:<action>] in their text are performed
    automatically by mcloop using process_monitor, app_interact,
    or web_interact, without pausing for user input.
    """
    return bool(_AUTO_TAG_RE.search(task.text))


def parse_auto_task(task: Task) -> tuple[str, str]:
    """Parse an [AUTO:<action>] task into (action, args).

    Returns (action, args) where action is the keyword after AUTO:
    (e.g. 'run_cli', 'run_gui', 'screenshot') and args is the
    remaining text after the tag.
    """
    m = _AUTO_TAG_RE.search(task.text)
    if not m:
        return ("", "")
    action = m.group(1)
    # Everything after the [AUTO:action] tag is the argument
    after_tag = task.text[m.end() :].strip()
    return (action, after_tag)


def purge_completed_bugs(path: str | Path) -> None:
    """Remove all checked-off items from BUGS.md.

    Called after the last open bug is completed. Bug tasks are
    generated noise from the reviewer or crash diagnostics, not
    design decisions worth preserving.

    Works for both standalone BUGS.md (entire file is bugs) and
    legacy plans with an inline ``## Bugs`` section.
    """
    p = Path(path)
    lines = p.read_text().splitlines()

    new_lines: list[str] = []
    for line in lines:
        m = CHECKBOX_RE.match(line)
        if m and m.group(2) in ("x", "X"):
            continue
        new_lines.append(line)

    p.write_text("\n".join(new_lines) + "\n")


def _auto_check_parents(path: Path) -> None:
    """Re-parse and check off any parent whose children are all done.

    Internal call: skip the structural sanity check. This function is
    invoked immediately after ``check_off`` writes a single line, so the
    file's structure has not changed since it was last validated by an
    external ``parse()`` call. Re-validating here would risk raising
    ``PlanCorruptionError`` from inside a check-off operation, which
    would obscure the real failure mode.
    """
    tasks = parse(path, check_structure=False)
    lines = path.read_text().splitlines()
    changed = False

    def visit(task_list: list[Task]) -> None:
        nonlocal changed
        for task in task_list:
            if task.children:
                visit(task.children)
                if not task.checked and all(c.checked for c in task.children):
                    _check_line(lines, _find_task_line(lines, task))
                    task.checked = True
                    changed = True

    visit(tasks)
    if changed:
        path.write_text("\n".join(lines) + "\n")


def task_label(tasks: list[Task], target: Task) -> str:
    """Return a label like '6.3' or '6.3.2' for a task's position.

    The first number is the stage/phase number extracted from
    the section header (any heading level containing ``Stage N``
    or ``Phase N``).  Tasks without a section header use a
    global positional index.  Subtask numbers are relative to
    their parent.
    """
    # Extract stage/phase number from the stage string.
    # Matches "Stage N" or "Phase N" anywhere in the header text.
    stage_num = ""
    if target.stage:
        m = _STAGE_NUM_RE.search(target.stage)
        if m:
            stage_num = m.group(1)

    # Filter root tasks to only those in the same stage
    if stage_num:
        stage_tasks = [t for t in tasks if t.stage == target.stage]
    else:
        stage_tasks = tasks

    def _search(task_list: list[Task], prefix: str) -> str | None:
        for i, task in enumerate(task_list, 1):
            lbl = f"{prefix}{i}" if prefix else str(i)
            if task is target:
                return lbl
            if task.children:
                found = _search(task.children, f"{lbl}.")
                if found:
                    return found
        return None

    result = _search(stage_tasks, f"{stage_num}." if stage_num else "")
    return result or "?"
