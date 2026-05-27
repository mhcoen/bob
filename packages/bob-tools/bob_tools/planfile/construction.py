"""Task construction (``make_task``) and sentinel scaffolding for round-trip validation."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping
from datetime import UTC, datetime

from bob_tools.planfile.model import (
    BugsSection,
    Phase,
    Plan,
    PlanValidationError,
    RuledOut,
    Subsection,
    Task,
    TaskStatus,
)
from bob_tools.planfile.parser import parse_plan
from bob_tools.planfile.renderer import render_plan
from bob_tools.planfile._shared import (
    _ACTION_NAME_RE,
    _ANNOTATION_KEY_ONLY_RE,
    _KNOWN_LEADING_FLAGS,
    _TASK_ID_NUMERIC_RE,
    _TASK_REF_RE,
    _contains_newline,
    _now_iso_utc,
    _task_path_label,
)
from bob_tools.planfile.iteration import _iter_task_tree_with_paths

def _validate_scalar(value: str, field: str, errors: list[str]) -> None:
    if _contains_newline(value):
        errors.append(f"{field} contains an embedded newline")

def _validate_task_for_construction(task: Task, errors: list[str]) -> None:
    for path, node in _iter_task_tree_with_paths(task):
        prefix = _task_path_label(path)
        if node.task_id is not None and _TASK_REF_RE.fullmatch(node.task_id) is None:
            errors.append(f"{prefix}.task_id has invalid task id {node.task_id!r}")
        _validate_scalar(node.text, f"{prefix}.text", errors)
        if node.created_at is not None:
            _validate_scalar(node.created_at, f"{prefix}.created_at", errors)
        if not isinstance(node.status, TaskStatus):
            errors.append(f"{prefix}.status is not a TaskStatus")
        for flag_index, tag in enumerate(node.flag_tags):
            _validate_scalar(tag, f"{prefix}.flag_tags[{flag_index}]", errors)
            if tag not in _KNOWN_LEADING_FLAGS:
                errors.append(
                    f"{prefix}.flag_tags[{flag_index}] has unknown flag {tag!r}"
                )
        if node.action_tag is not None:
            action, args = node.action_tag
            _validate_scalar(action, f"{prefix}.action_tag.action", errors)
            _validate_scalar(args, f"{prefix}.action_tag.args", errors)
            if _ACTION_NAME_RE.fullmatch(action) is None:
                errors.append(
                    f"{prefix}.action_tag.action has invalid action {action!r}"
                )
        for annotation_index, (key, value) in enumerate(node.annotations):
            _validate_scalar(
                key, f"{prefix}.annotations[{annotation_index}].key", errors
            )
            _validate_scalar(
                value, f"{prefix}.annotations[{annotation_index}].value", errors
            )
            if _ANNOTATION_KEY_ONLY_RE.fullmatch(key) is None:
                errors.append(
                    f"{prefix}.annotations[{annotation_index}].key "
                    f"has invalid key {key!r}"
                )
        for dep_index, dep in enumerate(node.deps):
            _validate_scalar(dep, f"{prefix}.deps[{dep_index}]", errors)
            if _TASK_REF_RE.fullmatch(dep) is None:
                errors.append(f"{prefix}.deps[{dep_index}] has invalid dep {dep!r}")
        for child_index, child in enumerate(node.children):
            if not isinstance(child, Task):
                errors.append(f"{prefix}.children[{child_index}] is not a Task")
        for ruled_index, ruled in enumerate(node.ruled_out):
            if not isinstance(ruled, RuledOut):
                errors.append(f"{prefix}.ruled_out[{ruled_index}] is not a RuledOut")
                continue
            _validate_scalar(
                ruled.text, f"{prefix}.ruled_out[{ruled_index}].text", errors
            )
        if node.trailing_lines:
            errors.append(f"{prefix}.trailing_lines must be empty on constructed tasks")

def _explicit_task_ids(task: Task) -> set[str]:
    return {
        node.task_id
        for _, node in _iter_task_tree_with_paths(task)
        if node.task_id is not None
    }

def _next_sentinel(used: set[str], next_number: int) -> tuple[str, int]:
    candidate_number = next_number
    while True:
        candidate = f"T-{candidate_number:06d}"
        candidate_number += 1
        if candidate not in used:
            used.add(candidate)
            return candidate, candidate_number

def _apply_task_id_sentinels(
    task: Task,
    *,
    used_ids: set[str],
    path_to_sentinel: dict[tuple[int, ...], str],
    path: tuple[int, ...] = (),
    next_number: int = 1,
) -> tuple[Task, int]:
    task_id = task.task_id
    current_number = next_number
    if task_id is None:
        task_id, current_number = _next_sentinel(used_ids, current_number)
        path_to_sentinel[path] = task_id

    children: list[Task] = []
    for index, child in enumerate(task.children):
        updated_child, current_number = _apply_task_id_sentinels(
            child,
            used_ids=used_ids,
            path_to_sentinel=path_to_sentinel,
            path=(*path, index),
            next_number=current_number,
        )
        children.append(updated_child)

    return dataclasses.replace(
        task, task_id=task_id, children=tuple(children)
    ), current_number

def _restore_sentinel_none_ids(
    task: Task,
    *,
    path_to_sentinel: Mapping[tuple[int, ...], str],
    path: tuple[int, ...] = (),
) -> Task:
    task_id = task.task_id
    if path in path_to_sentinel and task_id == path_to_sentinel[path]:
        task_id = None
    children = tuple(
        _restore_sentinel_none_ids(
            child, path_to_sentinel=path_to_sentinel, path=(*path, index)
        )
        for index, child in enumerate(task.children)
    )
    return dataclasses.replace(task, task_id=task_id, children=children)

def _reject_trailing_lines(task: Task) -> None:
    errors = [
        f"{_task_path_label(path)}.trailing_lines must be empty on constructed tasks"
        for path, node in _iter_task_tree_with_paths(task)
        if node.trailing_lines
    ]
    if errors:
        raise PlanValidationError(errors)

def _normalize_task_for_semantic_compare(task: Task, *, depth: int = 0) -> Task:
    _reject_trailing_lines(task)
    return dataclasses.replace(
        task,
        indent_level=depth * 2,
        line_number=0,
        children=tuple(
            _normalize_task_for_semantic_compare(child, depth=depth + 1)
            for child in task.children
        ),
        ruled_out=tuple(
            dataclasses.replace(ruled, line_number=0) for ruled in task.ruled_out
        ),
    )

def _minimal_canonical_plan(task: Task) -> Plan:
    return Plan(
        magic_version=1,
        project_title="Project",
        preamble="",
        phases=(
            Phase(
                phase_id="phase_001",
                phase_id_source="explicit_comment",
                ordinal=1,
                keyword="Phase",
                title="P",
                prose="",
                subsections=(),
                tasks=(task,),
                line_number=0,
            ),
        ),
        bugs=None,
        source_path=None,
    )

def _field_value(task: Task, field: str) -> object:
    if field == "ruled_out":
        return tuple(ruled.text for ruled in task.ruled_out)
    return getattr(task, field)

def _compare_task_fields(
    intended: Task, parsed: Task, path: tuple[int, ...]
) -> list[str]:
    errors: list[str] = []
    prefix = _task_path_label(path)
    for field in (
        "task_id",
        "text",
        "status",
        "flag_tags",
        "action_tag",
        "annotations",
        "deps",
        "ruled_out",
        "created_at",
    ):
        intended_value = _field_value(intended, field)
        parsed_value = _field_value(parsed, field)
        if intended_value != parsed_value:
            errors.append(
                f"{prefix}.{field} failed to round-trip: "
                f"intended {intended_value!r}, parsed {parsed_value!r}"
            )
    if len(intended.children) != len(parsed.children):
        errors.append(
            f"{prefix}.children failed to round-trip: intended "
            f"{len(intended.children)}, parsed {len(parsed.children)}"
        )
        return errors
    for index, (intended_child, parsed_child) in enumerate(
        zip(intended.children, parsed.children, strict=True)
    ):
        errors.extend(
            _compare_task_fields(intended_child, parsed_child, (*path, index))
        )
    return errors

def _assert_task_field_stability(task: Task) -> None:
    errors: list[str] = []
    _validate_task_for_construction(task, errors)
    if errors:
        raise PlanValidationError(errors)

    path_to_sentinel: dict[tuple[int, ...], str] = {}
    task_for_render, _ = _apply_task_id_sentinels(
        task,
        used_ids=_explicit_task_ids(task),
        path_to_sentinel=path_to_sentinel,
    )
    rendered = render_plan(_minimal_canonical_plan(task_for_render))
    parsed_plan = parse_plan(rendered)
    parsed_task = parsed_plan.phases[0].tasks[0]
    parsed_task = _restore_sentinel_none_ids(
        parsed_task, path_to_sentinel=path_to_sentinel
    )

    intended_normalized = _normalize_task_for_semantic_compare(task)
    parsed_normalized = _normalize_task_for_semantic_compare(parsed_task)
    errors = _compare_task_fields(intended_normalized, parsed_normalized, ())
    if errors:
        raise PlanValidationError(errors)

def make_task(
    text: str,
    *,
    status: TaskStatus = TaskStatus.TODO,
    flag_tags: tuple[str, ...] = (),
    action_tag: tuple[str, str] | None = None,
    annotations: tuple[tuple[str, str], ...] = (),
    deps: tuple[str, ...] = (),
    children: tuple[Task, ...] = (),
    ruled_out: tuple[RuledOut, ...] = (),
    task_id: str | None = None,
    created_at: str | None = None,
) -> Task:
    """Construct a semantically field-stable PLAN.md task.

    The constructor validates model-provided scalar fields by rendering
    the candidate in a minimal strict plan, parsing it back, and requiring
    the parsed task fields to equal the intended fields after normalizing
    only source-position observations. Missing ids are represented with
    temporary sentinels inside the harness only; returned tasks keep
    ``task_id=None`` when no id was supplied.
    """
    task = Task(
        task_id=task_id,
        text=text,
        status=status,
        flag_tags=tuple(flag_tags),
        action_tag=action_tag,
        annotations=tuple(annotations),
        deps=tuple(deps),
        children=tuple(children),
        ruled_out=tuple(ruled_out),
        indent_level=0,
        line_number=0,
        trailing_lines=(),
        created_at=created_at,
    )
    _assert_task_field_stability(task)
    return task

def _construction_sentinel_task() -> Task:
    return Task(
        task_id="T-000001",
        text="t",
        status=TaskStatus.TODO,
        flag_tags=(),
        action_tag=None,
        annotations=(),
        deps=(),
        children=(),
        ruled_out=(),
        indent_level=0,
        line_number=0,
        trailing_lines=(),
    )

def _construction_sentinel_phase(
    *,
    title: str = "P",
    prose: str = "",
    subsections: tuple[Subsection, ...] = (),
    tasks: tuple[Task, ...] | None = None,
) -> Phase:
    phase_tasks = (_construction_sentinel_task(),) if tasks is None else tasks
    return Phase(
        phase_id="phase_001",
        phase_id_source="explicit_comment",
        ordinal=1,
        keyword="Phase",
        title=title,
        prose=prose,
        subsections=subsections,
        tasks=phase_tasks,
        line_number=0,
    )

def _construction_sentinel_plan(
    *,
    project_title: str = "Project",
    preamble: str = "",
    phase: Phase | None = None,
) -> Plan:
    return Plan(
        magic_version=1,
        project_title=project_title,
        preamble=preamble,
        phases=(phase if phase is not None else _construction_sentinel_phase(),),
        bugs=None,
        source_path=None,
    )

def _round_trip_scalar(
    plan: Plan,
    extract: Callable[[Plan], str],
    candidate: str,
    field: str,
    errors: list[str],
) -> None:
    parsed = parse_plan(render_plan(plan))
    try:
        parsed_value = extract(parsed)
    except (IndexError, AttributeError):
        errors.append(
            f"{field} failed to round-trip: intended {candidate!r}, "
            f"parsed structure changed"
        )
        return
    if parsed_value != candidate:
        errors.append(
            f"{field} failed to round-trip: intended {candidate!r}, "
            f"parsed {parsed_value!r}"
        )
