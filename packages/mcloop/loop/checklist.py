"""Markdown checklist parser — read/write `- [ ]` items."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

CHECKBOX_RE = re.compile(r"^(\s*)- \[([ xX!])\] (.+)$")


@dataclass
class Task:
    text: str
    checked: bool
    failed: bool
    line_number: int
    indent_level: int
    children: list[Task] = field(default_factory=list)


def parse(path: str | Path) -> list[Task]:
    """Read a markdown file and return a tree of Task objects."""
    lines = Path(path).read_text().splitlines()
    root_tasks: list[Task] = []
    stack: list[Task] = []  # (task, indent_level) ancestry

    for i, line in enumerate(lines):
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
        )

        # Find parent: walk stack back to find a task with smaller indent
        while stack and stack[-1].indent_level >= indent:
            stack.pop()

        if stack:
            stack[-1].children.append(task)
        else:
            root_tasks.append(task)

        stack.append(task)

    return root_tasks


def find_next(tasks: list[Task]) -> Task | None:
    """Depth-first search for the first unchecked leaf task.

    Subtasks before parents: if a task has unchecked children, recurse into
    children first. A parent with all children done is itself a candidate.
    """
    for task in tasks:
        if task.checked or task.failed:
            continue

        if task.children:
            # Try to find an unchecked child first
            child = find_next(task.children)
            if child:
                return child
            # All children are done — parent is the next candidate
            # (it will be auto-checked by the main loop)
            return task

        return task

    return None


def check_off(path: str | Path, task: Task) -> None:
    """Rewrite `- [ ]` to `- [x]` at the task's line number.

    Also auto-checks parent tasks when all their children are done.
    """
    p = Path(path)
    lines = p.read_text().splitlines()
    _check_line(lines, task.line_number)

    # Re-parse to check for parent auto-completion
    p.write_text("\n".join(lines) + "\n")
    _auto_check_parents(p)


def mark_failed(path: str | Path, task: Task) -> None:
    """Rewrite `- [ ]` to `- [!]` at the task's line number."""
    p = Path(path)
    lines = p.read_text().splitlines()
    line = lines[task.line_number]
    lines[task.line_number] = line.replace("- [ ]", "- [!]", 1)
    p.write_text("\n".join(lines) + "\n")


def _check_line(lines: list[str], line_number: int) -> None:
    """Replace `- [ ]` with `- [x]` on the given line."""
    line = lines[line_number]
    lines[line_number] = line.replace("- [ ]", "- [x]", 1)


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
                    _check_line(lines, task.line_number)
                    changed = True

    visit(tasks)
    if changed:
        path.write_text("\n".join(lines) + "\n")
