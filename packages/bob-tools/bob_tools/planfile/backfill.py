"""Best-effort backfill of ``Task.completed_at`` from git history.

Existing ``[x]`` tasks across the workspace predate the ``completed_at``
field and so carry no checkoff timestamp. This module recovers one from
git: the real checkoff time is the commit that first flipped a task's
checkbox to ``[x]``. We locate that commit with a pickaxe search
(``git log -S``) for the done-marker substring of the task and use the
commit's author timestamp (UTC) as ``completed_at``.

Mirrors the policy of the ``created_at`` backfill (T-000002): best-effort,
ID-targeted, and conservative — where git cannot resolve a flip commit
(uncommitted edit, task with no id, tangled history), the task is left
``completed_at=None`` rather than guessed.

The git access is injected as ``run`` so the resolver is unit-testable
against a faked history.
"""

from __future__ import annotations

import dataclasses
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from bob_tools.planfile.iteration import _iter_plan_tasks
from bob_tools.planfile.model import Plan, Task, TaskStatus
from bob_tools.planfile.parser import parse_plan
from bob_tools.planfile.renderer import render_plan

# Signature of the subprocess runner the resolver depends on. The default
# is ``subprocess.run``; tests pass a fake that returns canned output.
Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def _epoch_to_iso_utc(epoch: int) -> str:
    """Format a unix timestamp as ``%Y-%m-%dT%H:%M:%SZ`` (UTC).

    Matches ``_shared._now_iso_utc`` so backfilled and freshly-stamped
    timestamps share one canonical form.
    """
    dt = datetime.fromtimestamp(epoch, tz=UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def resolve_completed_at(
    task_id: str,
    rel_path: str,
    *,
    repo_root: Path,
    run: Runner = subprocess.run,
) -> str | None:
    """Return the checkoff timestamp for ``task_id`` in ``rel_path``, or None.

    Uses ``git log -S'[x] <task_id>:'`` (and a ``[X]`` fallback) restricted
    to ``rel_path``: the pickaxe lists the commit that changed the count of
    the done-marker substring from zero to one — i.e. the flip to DONE.
    ``--reverse`` puts the oldest such commit first, and ``%at`` yields its
    author timestamp as a UTC epoch. Returns ``None`` when git errors or
    reports no introducing commit.
    """
    for marker in (f"[x] {task_id}:", f"[X] {task_id}:"):
        try:
            result = run(
                [
                    "git",
                    "log",
                    "-S",
                    marker,
                    "--reverse",
                    "--format=%at",
                    "--",
                    rel_path,
                ],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                check=False,
            )
        except (OSError, FileNotFoundError):
            return None
        if result.returncode != 0:
            continue
        lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
        if not lines:
            continue
        try:
            return _epoch_to_iso_utc(int(lines[0]))
        except ValueError:
            continue
    return None


def backfill_plan(
    plan: Plan,
    rel_path: str,
    *,
    repo_root: Path,
    run: Runner = subprocess.run,
) -> tuple[Plan, int, int]:
    """Stamp ``completed_at`` on resolvable DONE tasks in ``plan``.

    Returns ``(new_plan, backfilled, left_null)`` where ``backfilled`` is
    the count of DONE tasks that received a timestamp and ``left_null`` the
    count of DONE tasks (with an id) that git could not resolve. Tasks that
    already carry a ``completed_at`` are left untouched and counted in
    neither total.
    """
    resolved: dict[str, str | None] = {}
    backfilled = 0
    left_null = 0

    for task in _iter_plan_tasks(plan):
        if task.status != TaskStatus.DONE or task.completed_at is not None:
            continue
        if task.task_id is None:
            left_null += 1
            continue
        ts = resolve_completed_at(task.task_id, rel_path, repo_root=repo_root, run=run)
        resolved[task.task_id] = ts
        if ts is None:
            left_null += 1
        else:
            backfilled += 1

    def _apply(task: Task) -> Task:
        new_children = tuple(_apply(c) for c in task.children)
        ts = resolved.get(task.task_id) if task.task_id is not None else None
        if (
            task.status == TaskStatus.DONE
            and task.completed_at is None
            and ts is not None
        ):
            return dataclasses.replace(task, completed_at=ts, children=new_children)
        if new_children != task.children:
            return dataclasses.replace(task, children=new_children)
        return task

    new_phases = []
    for phase in plan.phases:
        new_tasks = tuple(_apply(t) for t in phase.tasks)
        new_subs = tuple(
            dataclasses.replace(sub, tasks=tuple(_apply(t) for t in sub.tasks))
            for sub in phase.subsections
        )
        new_phases.append(
            dataclasses.replace(phase, tasks=new_tasks, subsections=new_subs)
        )
    new_bugs = plan.bugs
    if plan.bugs is not None:
        new_bugs = dataclasses.replace(
            plan.bugs, tasks=tuple(_apply(t) for t in plan.bugs.tasks)
        )
    return (
        dataclasses.replace(plan, phases=tuple(new_phases), bugs=new_bugs),
        backfilled,
        left_null,
    )


def backfill_file(
    path: Path,
    *,
    repo_root: Path,
    run: Runner = subprocess.run,
) -> tuple[int, int]:
    """Backfill ``completed_at`` in the plan file at ``path`` in place.

    Parses the file in unchecked (compat-tolerant) mode, stamps resolvable
    DONE tasks, and rewrites the file canonically only when something
    changed. Returns ``(backfilled, left_null)``.
    """
    text = path.read_text(encoding="utf-8")
    plan = parse_plan(text, strict=False)
    rel_path = str(path.relative_to(repo_root))
    new_plan, backfilled, left_null = backfill_plan(
        plan, rel_path, repo_root=repo_root, run=run
    )
    if backfilled:
        path.write_text(render_plan(new_plan), encoding="utf-8")
    return backfilled, left_null
