"""Markdown checklist parser. Reads and writes `- [ ]` items."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

CHECKBOX_RE = re.compile(r"^(\s*)- \[([ xX!])\] (.+)$")
STAGE_RE = re.compile(r"^##\s+Stage\s+\d+", re.IGNORECASE)
BUGS_RE = re.compile(r"^##\s+Bugs\s*$", re.IGNORECASE)
_USER_TAG = "[USER]"
_BATCH_TAG = "[BATCH]"
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
    eliminated: list[str] = field(default_factory=list)


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
    """Return True if there are unchecked tasks in the ``## Bugs`` section."""
    return _search_in_stage(tasks, "Bugs") is not None


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
        if m and m.group(3).strip() == task.text and len(m.group(1)) == task.indent_level:
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
        if not m or m.group(3).strip() != task.text:
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
    """Remove all checked-off items from the ## Bugs section.

    Called after the last open bug is completed. Bug tasks are
    generated noise from the reviewer, not design decisions
    worth preserving.
    """
    p = Path(path)
    lines = p.read_text().splitlines()
    bugs_start = None
    bugs_end = len(lines)

    for i, line in enumerate(lines):
        if BUGS_RE.match(line.strip()):
            bugs_start = i
        elif bugs_start is not None and line.strip().startswith("## "):
            bugs_end = i
            break

    if bugs_start is None:
        return

    # Keep lines that are not checked-off tasks
    new_lines = lines[: bugs_start + 1]
    for line in lines[bugs_start + 1 : bugs_end]:
        m = CHECKBOX_RE.match(line)
        if m and m.group(2) in ("x", "X"):
            continue
        new_lines.append(line)
    new_lines.extend(lines[bugs_end:])

    p.write_text("\n".join(new_lines) + "\n")


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


def task_label(tasks: list[Task], target: Task) -> str:
    """Return a label like '6.3' or '6.3.2' for a task's position.

    The first number is the stage number (extracted from the
    ``## Stage N:`` header).  Tasks without a stage header use
    a global positional index.  Subtask numbers are relative to
    their parent.
    """
    # Extract stage number from the stage string (e.g. "Stage 6: ..." -> "6")
    stage_num = ""
    if target.stage and target.stage.startswith("Stage "):
        rest = target.stage[len("Stage ") :]
        num_part = rest.split(":")[0].split()[0]
        if num_part.isdigit():
            stage_num = num_part

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
