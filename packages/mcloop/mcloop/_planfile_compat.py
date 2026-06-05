"""Checklist-shaped adapter backed by ``bob_tools.planfile``.

This is McLoop's runtime adapter between its historical checklist-shaped
task contract and ``bob_tools.planfile``. Callers continue to work with
``Task`` objects and checklist-style helper functions while PLAN.md parsing,
rendering, and mutation are delegated to the shared planfile package.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from bob_tools.planfile import (
    ConcurrentUpdateError,
    Plan,
    PlanSyntaxError,
    TaskStatus,
    clear_failed,
    complete_task,
    fail_task,
    load,
    parse_plan,
    purge_done_bug_tasks,
    update,
)
from bob_tools.planfile import Task as PlanTask
from bob_tools.planfile import (
    reset_task as _reset_task_op,
)


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


# Alias so callers that historically caught
# ``mcloop.checklist.PlanCorruptionError`` keep working. The behavior
# (parser rejection of structurally corrupt plans) is preserved by
# planfile's PlanSyntaxError — only the exception identity changes.
PlanCorruptionError = PlanSyntaxError

CHECKBOX_RE = re.compile(r"^(\s*)- \[([ xX!])\] (.+)$")
_STAGE_NUM_RE = re.compile(r"\b(?:stage|phase)\s+(\d+)\b", re.IGNORECASE)
_AUTO_TAG_RE = re.compile(r"\[AUTO:(\w+)\]")
_UPDATE_RETRIES = 2


def _stage_name(keyword: str, ordinal: int, title: str) -> str:
    return f"{keyword} {ordinal}: {title}" if title else f"{keyword} {ordinal}"


def _task_text(task: PlanTask) -> str:
    parts: list[str] = [f"[{tag}]" for tag in task.flag_tags]
    if task.action_tag is not None:
        action, args = task.action_tag
        parts.append(f"[AUTO:{action}]")
        if args:
            parts.append(args)
    elif task.text:
        parts.append(task.text)
    for key, value in task.annotations:
        parts.append(f"[{key}: {value}]")
    return " ".join(parts)


def _body_from_trailing_lines(task: PlanTask) -> str:
    body_lines = list(task.trailing_lines)
    while body_lines and not body_lines[0].strip():
        body_lines.pop(0)
    while body_lines and not body_lines[-1].strip():
        body_lines.pop()
    return "\n".join(body_lines)


def _convert_task(task: PlanTask, stage: str) -> Task:
    return Task(
        text=_task_text(task),
        checked=task.status == TaskStatus.DONE,
        failed=task.status == TaskStatus.FAILED,
        line_number=max(task.line_number - 1, 0),
        indent_level=task.indent_level,
        stage=stage,
        children=[_convert_task(child, stage) for child in task.children],
        eliminated=[f"[RULEDOUT] {entry.text}" for entry in task.ruled_out],
        body=_body_from_trailing_lines(task) if "USER" in task.flag_tags else "",
        task_id=task.task_id,
        flag_tags=task.flag_tags,
        action_tag=task.action_tag,
    )


def _tasks_from_plan(plan: Plan) -> list[Task]:
    tasks: list[Task] = []
    for phase in plan.phases:
        stage = _stage_name(phase.keyword, phase.ordinal, phase.title)
        tasks.extend(_convert_task(task, stage) for task in phase.tasks)
        for subsection in phase.subsections:
            tasks.extend(_convert_task(task, stage) for task in subsection.tasks)
    if plan.bugs is not None:
        tasks.extend(_convert_task(task, "Bugs") for task in plan.bugs.tasks)
    return tasks


def parse(path: str | Path, check_structure: bool = True) -> list[Task]:
    """Read a plan file and return checklist-shaped tasks.

    ``check_structure`` is accepted for signature parity with
    ``mcloop.checklist.parse``. ``bob_tools.planfile.parse_plan`` always runs
    its structural sanity check, so ``False`` cannot suppress corruption checks
    in this adapter.
    """
    _ = check_structure
    p = Path(path)
    return _tasks_from_plan(parse_plan(p.read_text(), source_path=p))


def parse_description(path: str | Path) -> str:
    """Extract prose before the first checkbox as a project description.

    Matches ``mcloop.checklist.parse_description`` exactly: read all
    lines, stop at the first ``- [ ]/[x]/[X]/[!]`` checkbox line, join
    the preceding lines and strip. Used by mcloop runtime to surface a
    project blurb to the LLM session prompt.
    """
    lines = Path(path).read_text().splitlines()
    desc_lines: list[str] = []
    for line in lines:
        if CHECKBOX_RE.match(line):
            break
        desc_lines.append(line)
    return "\n".join(desc_lines).strip()


def count_unchecked(tasks: list[Task]) -> int:
    return sum(
        (0 if task.checked or task.failed else 1) + count_unchecked(task.children)
        for task in tasks
    )


def get_eliminated(tasks: list[Task], target: Task) -> list[str]:
    result: list[str] = []

    def search(task_list: list[Task], inherited: list[str]) -> bool:
        for task in task_list:
            combined = inherited + task.eliminated
            if task is target:
                result.extend(combined)
                return True
            if search(task.children, combined):
                return True
        return False

    search(tasks, [])
    return result


def find_parent(tasks: list[Task], target: Task) -> Task | None:
    def search(task_list: list[Task]) -> Task | None:
        for task in task_list:
            if target in task.children:
                return task
            found = search(task.children)
            if found is not None:
                return found
        return None

    return search(tasks)


def _get_stages(tasks: list[Task]) -> list[str]:
    stages: list[str] = []

    def collect(task_list: list[Task]) -> None:
        for task in task_list:
            if task.stage and task.stage != "Bugs" and task.stage not in stages:
                stages.append(task.stage)
            collect(task.children)

    collect(tasks)
    return stages


def _stage_complete(tasks: list[Task], stage: str) -> bool:
    def check(task_list: list[Task]) -> bool:
        for task in task_list:
            if task.stage == stage and not task.checked:
                return False
            if not check(task.children):
                return False
        return True

    return check(tasks)


def _current_stage(tasks: list[Task]) -> str | None:
    for stage in _get_stages(tasks):
        if not _stage_complete(tasks, stage):
            return stage
    return None


def _search_tasks(
    task_list: list[Task],
    *,
    is_subtask: bool = False,
    required_stage: str | None = None,
    skip_stages: set[str] | None = None,
) -> Task | None:
    for task in task_list:
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
            if child is not None:
                return child
            if any(child_task.failed for child_task in task.children):
                if is_subtask:
                    return None
                continue
            return task

        return task
    return None


def _search_in_stage(tasks: list[Task], stage: str) -> Task | None:
    return _search_tasks(tasks, required_stage=stage)


def find_next(tasks: list[Task]) -> Task | None:
    """Return the next leaf task, normalizing planfile BATCH surfacing.

    Parity §2(c): ``bob_tools.planfile.next_tasks`` surfaces BATCH parents,
    while current mcloop ``find_next`` returns the first actionable leaf so
    ``run_loop`` can rediscover the parent and call ``get_batch_children``.
    The shim keeps the checklist return shape.
    """
    bug_task = _search_in_stage(tasks, "Bugs")
    if bug_task is not None:
        return bug_task

    active_stage = _current_stage(tasks)
    has_stages = bool(_get_stages(tasks))
    return _search_tasks(
        tasks,
        required_stage=active_stage if has_stages else None,
        skip_stages={"Bugs"},
    )


def has_unchecked_bugs(tasks: list[Task]) -> bool:
    if _search_in_stage(tasks, "Bugs") is not None:
        return True
    if _get_stages(tasks):
        return False

    def any_unchecked(task_list: list[Task]) -> bool:
        for task in task_list:
            if not task.checked and not task.failed:
                return True
            if any_unchecked(task.children):
                return True
        return False

    return any_unchecked(tasks)


def is_user_task(task: Task) -> bool:
    """Classify USER via planfile flag tags, with checklist text fallback.

    Primary classifier is the typed ``flag_tags`` populated by
    ``bob_tools.planfile.parse_plan`` (§2(d)). The text fallback
    preserves checklist's exact leading-tag semantics for ``Task``
    objects constructed outside ``parse_plan`` (e.g. unit-test
    fixtures that build ``Task`` directly without going through the
    parser). The §2(d) DONE prose-mention exception is unchanged: all
    prose-mention tasks are guaranteed ``[x]`` per the freeze
    invariant, so the scheduler skips them regardless of how they
    classify here.
    """
    if "USER" in task.flag_tags:
        return True
    text = task.text.strip()
    return text == "[USER]" or text.startswith("[USER] ")


def is_batch_task(task: Task) -> bool:
    """Classify BATCH via planfile flag tags, with checklist text fallback.

    See :func:`is_user_task` for the rationale on the dual-source check.
    """
    if "BATCH" in task.flag_tags:
        return True
    return "[BATCH]" in task.text


def is_auto_task(task: Task) -> bool:
    """Classify AUTO via planfile action tags, with checklist text fallback.

    See :func:`is_user_task` for the rationale on the dual-source check.
    """
    if task.action_tag is not None:
        return True
    return bool(_AUTO_TAG_RE.search(task.text))


def user_task_instructions(task: Task) -> str:
    text = task.text.strip()
    if text == "[USER]":
        head = ""
    elif text.startswith("[USER] "):
        head = text[len("[USER] ") :].strip()
    else:
        head = text
    if task.body:
        return f"{head}\n{task.body}" if head else task.body
    return head


def get_batch_children(task: Task) -> list[Task]:
    """Collect batch children with checklist's barrier rules (§2(c))."""
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


_RUN_CLI_BACKTICK_RE = re.compile(r"`([^`]+)`")


def parse_auto_task(task: Task) -> tuple[str, str]:
    if task.action_tag is None:
        return ("", "")
    action, args = task.action_tag
    if action == "run_cli":
        return _parse_run_cli_action(args)
    return task.action_tag


def _parse_run_cli_action(args: str) -> tuple[str, str]:
    """Extract the single backtick-delimited command from a run_cli task.

    A ``run_cli`` task must execute exactly one backtick-quoted command, not
    its surrounding prose description. When the task text holds exactly one
    backtick-delimited command, run that command verbatim. When it holds none
    (or is ambiguous with several), return an ``error`` action carrying a clear
    message so the loop reports a failure instead of passing prose to the
    shell.
    """
    commands = [m.group(1).strip() for m in _RUN_CLI_BACKTICK_RE.finditer(args)]
    commands = [c for c in commands if c]
    if len(commands) == 1:
        return ("run_cli", commands[0])
    if not commands:
        return ("error", f"run_cli task has no backtick-delimited command: {args!r}")
    return (
        "error",
        f"run_cli task has multiple backtick-delimited commands; expected one: {args!r}",
    )


def task_label(tasks: list[Task], target: Task) -> str:
    stage_num = ""
    if target.stage:
        m = _STAGE_NUM_RE.search(target.stage)
        if m is not None:
            stage_num = m.group(1)

    stage_tasks = [t for t in tasks if t.stage == target.stage] if stage_num else tasks

    def search(task_list: list[Task], prefix: str) -> str | None:
        for i, task in enumerate(task_list, 1):
            label = f"{prefix}{i}" if prefix else str(i)
            if task is target:
                return label
            found = search(task.children, f"{label}.")
            if found is not None:
                return found
        return None

    return search(stage_tasks, f"{stage_num}." if stage_num else "") or "?"


def _count_failed(plan: Plan) -> int:
    return sum(1 for task in _iter_plan_tasks(plan) if task.status == TaskStatus.FAILED)


def _iter_plan_tasks(plan: Plan) -> list[PlanTask]:
    tasks: list[PlanTask] = []

    def add(task_list: tuple[PlanTask, ...]) -> None:
        for task in task_list:
            tasks.append(task)
            add(task.children)

    for phase in plan.phases:
        add(phase.tasks)
        for subsection in phase.subsections:
            add(subsection.tasks)
    if plan.bugs is not None:
        add(plan.bugs.tasks)
    return tasks


def _update_with_retry(
    path: Path,
    operation: Callable[[Plan], Plan],
    *,
    validation: Literal["canonical", "unchecked"] = "canonical",
) -> Plan:
    """Apply ``operation`` with a bounded retry on concurrent edits (§2(g)).

    Two retries gives three total attempts. Each retry re-loads current bytes
    and re-derives the mutation by stable task id, preserving mcloop's current
    "survive an external edit" behavior without spinning indefinitely under hot
    contention.
    """
    last_exc: ConcurrentUpdateError | None = None
    for _attempt in range(_UPDATE_RETRIES + 1):
        try:
            return update(path, operation, validation=validation)
        except ConcurrentUpdateError as exc:
            last_exc = exc
    assert last_exc is not None
    raise last_exc


def _require_task_id(task: Task, operation: str) -> str:
    if task.task_id is None:
        raise ValueError(
            f"{operation} requires migrated PLAN.md task ids; "
            "read/select/classify support compat plans, but mutation is "
            "ID-targeted per planfile.complete_task/fail_task/reset_task"
        )
    return task.task_id


def check_off(path: str | Path, task: Task) -> None:
    """Complete ``task`` by id; derived parent completion has no event (§2(e))."""
    task_id = _require_task_id(task, "check_off")
    _update_with_retry(Path(path), lambda plan: complete_task(plan, task_id)[0])


def _split_line_ending(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    return line, ""


def _mark_failed_idless(path: Path, task: Task) -> None:
    """Mark an id-less loose BUGS.md task failed by source position."""
    lines = path.read_text().splitlines(keepends=True)
    candidate_indexes = [task.line_number]
    candidate_indexes.extend(
        idx
        for idx, line in enumerate(lines)
        if idx != task.line_number
        and (match := CHECKBOX_RE.match(_split_line_ending(line)[0]))
        and match.group(2) != "!"
        and match.group(3) == task.text
    )

    for idx in candidate_indexes:
        if idx < 0 or idx >= len(lines):
            continue
        body, ending = _split_line_ending(lines[idx])
        match = CHECKBOX_RE.match(body)
        if match is None:
            continue
        if match.group(2) == "!":
            return
        if match.group(3) != task.text:
            continue
        lines[idx] = f"{match.group(1)}- [!] {match.group(3)}{ending}"
        path.write_text("".join(lines))
        return

    raise ValueError(f"mark_failed could not locate id-less task at line {task.line_number + 1}")


def mark_failed(path: str | Path, task: Task) -> None:
    """Mark ``task`` failed by id; callers must reserve this for retry exhaustion.

    Parity §2(e): commit-failure is not represented by this function and must
    not write a checkbox; the later run_loop shim only calls this on the path
    that already uses ``checklist.mark_failed`` today.
    """
    if task.task_id is None:
        _mark_failed_idless(Path(path), task)
        return
    task_id = _require_task_id(task, "mark_failed")
    _update_with_retry(Path(path), lambda plan: fail_task(plan, task_id, "failed")[0])


def _reset_failed_idless(path: Path, task: Task) -> None:
    """Flip an id-less loose BUGS.md task from ``[!]`` back to ``[ ]`` by position."""
    lines = path.read_text().splitlines(keepends=True)
    candidate_indexes = [task.line_number]
    candidate_indexes.extend(
        idx
        for idx, line in enumerate(lines)
        if idx != task.line_number
        and (match := CHECKBOX_RE.match(_split_line_ending(line)[0]))
        and match.group(2) == "!"
        and match.group(3) == task.text
    )

    for idx in candidate_indexes:
        if idx < 0 or idx >= len(lines):
            continue
        body, ending = _split_line_ending(lines[idx])
        match = CHECKBOX_RE.match(body)
        if match is None:
            continue
        if match.group(3) != task.text:
            continue
        state = match.group(2)
        if state == " ":
            # Already pending; reset is idempotent.
            return
        if state != "!":
            # Never demote a [x] DONE task to pending via this path.
            continue
        lines[idx] = f"{match.group(1)}- [ ] {match.group(3)}{ending}"
        path.write_text("".join(lines))
        return

    raise ValueError(
        f"reset_task could not locate id-less failed task at line {task.line_number + 1}"
    )


def reset_task(path: str | Path, task: Task) -> None:
    """Flip a ``[!]``-failed ``task`` back to ``[ ]`` pending so it can be retried.

    The inverse of :func:`mark_failed`. A failed task is otherwise skipped
    permanently by the scheduler (``_search_tasks`` treats ``[!]`` as a hard
    stop); resetting it to pending makes it eligible again once whatever
    blocked it — a missing mapped test, an absent waiver, a stale baseline —
    has been cleared. By task id this delegates to planfile's ``reset_task``,
    which records a ``none``-kind settlement because retry is an operator
    decision, not new evidence about the implementation. Id-less loose
    BUGS.md tasks are flipped by source position, mirroring :func:`mark_failed`.
    The operation is idempotent: a task that is already pending is left
    untouched.
    """
    if task.task_id is None:
        _reset_failed_idless(Path(path), task)
        return
    task_id = _require_task_id(task, "reset_task")
    _update_with_retry(Path(path), lambda plan: _reset_task_op(plan, task_id)[0])


def clear_failed_markers(path: str | Path) -> int:
    p = Path(path)
    if not p.exists():
        return 0
    before = load(p)
    count = _count_failed(before)
    if count == 0:
        return 0
    _update_with_retry(p, clear_failed)
    return count


RESOLVED_BUGS_FILENAME = "BUGS-resolved.md"
_RESOLVED_HEADER = "## Resolved Bugs\n\n"


def _iter_bug_tasks_with_spans(plan: Plan, total_lines: int) -> list[tuple[PlanTask, int, int]]:
    """Return DFS-ordered ``(task, start_line, end_line_exclusive)`` for bug tasks.

    ``start_line`` is 1-indexed and matches ``PlanTask.line_number``.
    ``end_line_exclusive`` is the start of the next task in source order, or
    ``total_lines + 1`` for the last task in the bugs section. This lets the
    caller slice raw file lines verbatim, including any sub-bullets and
    trailing prose carried by the parser as ``trailing_lines``.
    """
    if plan.bugs is None or not plan.bugs.tasks:
        return []

    flat: list[PlanTask] = []

    def walk(tasks: tuple[PlanTask, ...]) -> None:
        for task in tasks:
            flat.append(task)
            if task.children:
                walk(task.children)

    walk(plan.bugs.tasks)
    flat.sort(key=lambda t: t.line_number)

    spans: list[tuple[PlanTask, int, int]] = []
    for idx, task in enumerate(flat):
        start = task.line_number
        end = flat[idx + 1].line_number if idx + 1 < len(flat) else total_lines + 1
        spans.append((task, start, end))
    return spans


def _collect_done_bug_text(plan: Plan, original_text: str) -> str:
    """Slice DONE bug entries from ``original_text`` verbatim.

    DONE subtrees are archived once at the topmost DONE ancestor: planfile's
    ``_purge_done_tasks`` drops the entire subtree of a DONE parent, so
    archiving children separately would double-record them. DONE children of
    TODO parents have no DONE ancestor and are archived standalone.
    """
    lines = original_text.splitlines(keepends=True)
    spans = _iter_bug_tasks_with_spans(plan, len(lines))
    if not spans:
        return ""

    chunks: list[str] = []
    skip_until = 0
    for task, start, end in spans:
        if start < skip_until:
            continue
        if task.status == TaskStatus.DONE:
            chunks.append("".join(lines[start - 1 : end - 1]))
            skip_until = end
    return "".join(chunks)


def _append_resolved(resolved_path: Path, archived_text: str) -> None:
    """Append ``archived_text`` to ``resolved_path`` (creating it if missing).

    On creation, write a one-line section header so the file is
    self-describing for humans browsing the repo; the loop never parses
    this file, so the header has no operational meaning.
    """
    if not resolved_path.exists():
        resolved_path.write_text(_RESOLVED_HEADER + archived_text)
        return
    existing = resolved_path.read_text()
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    with resolved_path.open("a") as fp:
        fp.write(prefix + archived_text)


def purge_completed_bugs(path: str | Path) -> None:
    """Move DONE bug entries out of ``path`` into ``BUGS-resolved.md``.

    The live queue (``BUGS.md``) shrinks to only TODO/FAILED entries so the
    loop's hot-path token cost does not grow. Resolved entries are appended
    verbatim — extracted from the original source text by line range, not
    re-rendered — to a sibling ``BUGS-resolved.md`` file before the purge
    rewrites the queue. The resolved file is git-tracked durability;
    callers (and the loop) never parse it.

    Standalone BUGS.md remains a loose bug queue, not a canonical PLAN.md, so
    id-less bug entries must not be rejected by PLAN.md canonical validation.
    """
    p = Path(path)
    archived_text = ""
    if p.exists():
        original_text = p.read_text()
        plan = parse_plan(original_text, source_path=p)
        archived_text = _collect_done_bug_text(plan, original_text)

    update(p, purge_done_bug_tasks, validation="unchecked")

    if archived_text:
        resolved_path = p.parent / RESOLVED_BUGS_FILENAME
        _append_resolved(resolved_path, archived_text)
