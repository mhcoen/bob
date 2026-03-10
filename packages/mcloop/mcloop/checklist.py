"""Markdown checklist parser. Reads and writes `- [ ]` items."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

CHECKBOX_RE = re.compile(r"^(\s*)- \[([ xX!])\] (.+)$")
STAGE_RE = re.compile(r"^##\s+Stage\s+\d+", re.IGNORECASE)
BUGS_RE = re.compile(r"^##\s+Bugs\s*$", re.IGNORECASE)
_USER_TAG = "[USER]"
_AUTO_TAG_RE = re.compile(r"\[AUTO:(\w+)\]")


@dataclass
class Task:
    text: str
    checked: bool
    failed: bool
    line_number: int
    indent_level: int
    stage: str = ""
    children: list[Task] = field(default_factory=list)


def parse_description(path: str | Path) -> str:
    """Extract prose before the first checkbox as a project description."""
    lines = Path(path).read_text().splitlines()
    desc_lines = []
    for line in lines:
        if CHECKBOX_RE.match(line):
            break
        desc_lines.append(line)
    return "\n".join(desc_lines).strip()


def parse(path: str | Path) -> list[Task]:
    """Read a markdown file and return a tree of Task objects.

    Tasks under ``## Stage N: ...`` headers are tagged with the
    stage name.  Tasks before any stage header have stage ``""``.
    """
    lines = Path(path).read_text().splitlines()
    root_tasks: list[Task] = []
    stack: list[Task] = []
    current_stage = ""

    for i, line in enumerate(lines):
        # Detect stage headers
        if STAGE_RE.match(line):
            current_stage = line.lstrip("#").strip()
            stack.clear()
            continue

        # Detect ## Bugs header
        if BUGS_RE.match(line):
            current_stage = "Bugs"
            stack.clear()
            continue

        m = CHECKBOX_RE.match(line)
        if not m:
            continue

        indent = len(m.group(1))
        marker = m.group(2)
        checked = marker in ("x", "X")
        failed = marker == "!"
        text = m.group(3).strip()
        task = Task(
            text=text,
            checked=checked,
            failed=failed,
            line_number=i,
            indent_level=indent,
            stage=current_stage,
        )

        while stack and stack[-1].indent_level >= indent:
            stack.pop()

        if stack:
            stack[-1].children.append(task)
        else:
            root_tasks.append(task)

        stack.append(task)

    return root_tasks


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

    def _search(task_list: list[Task]) -> Task | None:
        for task in task_list:
            if task.checked or task.failed:
                continue

            # Skip bug tasks (already handled above)
            if task.stage == "Bugs":
                continue

            # Skip tasks not in the active stage
            if has_stages and task.stage != active_stage:
                continue

            if task.children:
                child = _search(task.children)
                if child:
                    return child
                return task

            return task
        return None

    return _search(tasks)


def _search_in_stage(tasks: list[Task], stage: str) -> Task | None:
    """Search for the next unchecked leaf in a specific stage."""

    def _search(task_list: list[Task]) -> Task | None:
        for task in task_list:
            if task.checked or task.failed:
                continue
            if task.stage != stage:
                continue
            if task.children:
                child = _search(task.children)
                if child:
                    return child
                return task
            return task
        return None

    return _search(tasks)


def _find_task_line(lines: list[str], task: Task) -> int:
    """Find task line by text match, falling back to line_number."""
    for i, line in enumerate(lines):
        m = CHECKBOX_RE.match(line)
        if m and m.group(3).strip() == task.text:
            return i
    if task.line_number >= len(lines):
        raise IndexError(
            f"Task line {task.line_number} out of range (file has {len(lines)} lines)"
        )
    return task.line_number


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
    _auto_check_parents(p)


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


def _check_line(lines: list[str], line_number: int) -> None:
    """Replace `- [ ]` with `- [x]` on the given line."""
    line = lines[line_number]
    lines[line_number] = line.replace("- [ ]", "- [x]", 1)


def is_user_task(task: Task) -> bool:
    """Return True if the task requires user observation.

    Tasks marked with [USER] in their text require the user to
    perform an action and report back what they observed.
    """
    return _USER_TAG in task.text


def user_task_instructions(task: Task) -> str:
    """Extract the instruction text from a [USER] task.

    Removes the [USER] tag and returns the remaining text,
    which describes what the user should do and observe.
    """
    return task.text.replace(_USER_TAG, "").strip()


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


def _auto_check_parents(path: Path) -> None:
    """Re-parse and check off any parent whose children are all done."""
    tasks = parse(path)
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
