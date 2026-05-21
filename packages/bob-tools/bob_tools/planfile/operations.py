"""Pure operations on typed Plan objects (validate, migrate, mutate, schedule)."""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any, Protocol

from bob_tools.planfile.model import (
    BugsSection,
    Outcome,
    Phase,
    Plan,
    PlanInconsistencyError,
    PlanValidationError,
    RuledOut,
    Settlement,
    Subsection,
    Task,
    TaskContext,
    TaskStatus,
)
from bob_tools.planfile.parser import parse_plan
from bob_tools.planfile.renderer import render_plan

_POSITIONAL_LABEL_RE = re.compile(r"^\d+(?:\.\d+)+$")

# Leading-position bracket form whose content has the shape of an
# operational tag: an all-uppercase identifier of two or more chars,
# optionally followed by ``:word`` (the AUTO action-tag form). Matches
# ``[USER]``, ``[BATCH]``, ``[AUTO:run]``, ``[FOO]``, ``[FOO:bar]``;
# does NOT match lowercase or single-char brackets like ``[x]`` or
# ``[note]`` (those are prose per design doc section 4.3). When the
# parser succeeds at extracting a known leading tag the bracket is
# removed from ``task.text``, so a surviving match here is either a
# tag form the parser does not recognize (``[FOO]``) or a known tag
# in a non-leading-after-other-tags position the parser refused to
# strip — both cases are unknown bracket tags by validation.
_LEADING_TAG_LIKE_RE = re.compile(r"^\[([A-Z][A-Z0-9_]+(?::\w+)?)\]")

# Trailing bracket form. The opening ``[`` must abut either start of
# string or whitespace (mirrors ``_extract_annotations``' separation
# requirement) and the closing ``]`` must be the last non-whitespace
# character. Used to detect malformed annotations: a bracket form the
# parser left in text because its content did not parse as
# ``key: value``.
_TRAILING_BRACKET_RE = re.compile(r"(?:(?<=\s)|^)\[([^\[\]\n]+)\]\s*$")

# Annotation content shape per the parser's ``_ANNOTATION_CONTENT_RE``:
# identifier-shaped key, colon, then mandatory whitespace and value.
# A bracket whose content has a key-and-colon prefix but fails this
# pattern is the canonical "malformed annotation" signal — the author
# intended an annotation but missed the required whitespace or value.
_ANNOTATION_OK_RE = re.compile(r"^[A-Za-z_]\w*:\s+\S.*$", re.DOTALL)

# Annotation-attempt prefix: identifier-shaped key followed immediately
# by a colon. Used to distinguish "this trailing bracket looks like an
# annotation attempt" (``[feat:foo]``) from "this trailing bracket is
# just prose ending in brackets" (``[some text]``); only the former is
# flagged as malformed.
_ANNOTATION_KEY_RE = re.compile(r"^[A-Za-z_]\w*:")

_KNOWN_LEADING_FLAGS = frozenset({"USER", "BATCH"})

# Bracket forms reserved by the grammar for non-task-tag constructs.
# Per design doc section 4.3 (planfile.md:415-417), ``[RULEDOUT]`` is
# **not** a task tag; it is a sibling line at the child indent under
# the task it pertains to. When the literal token appears at the
# leading position of a task body — e.g. a task whose title describes
# the RULEDOUT feature itself (mcloop/PLAN.EXAMPLE.md:243) — it is
# prose, not an attempted unknown tag. Flagging it would conflate
# "unknown task tag" (a real validation concern) with "task title
# legitimately mentions a reserved keyword" (prose by design).
_RESERVED_SIBLING_MARKERS = frozenset({"RULEDOUT"})

_TASK_REF_RE = re.compile(r"^T-\d{6}$")
_ACTION_NAME_RE = re.compile(r"^\w+$")
_ANNOTATION_KEY_ONLY_RE = re.compile(r"^[A-Za-z_]\w*$")


def _contains_newline(value: str) -> bool:
    return "\n" in value or "\r" in value


def _task_path_label(path: tuple[int, ...]) -> str:
    if not path:
        return "task"
    return "task.children" + "".join(f"[{index}]" for index in path)


def _iter_task_tree_with_paths(
    task: Task, path: tuple[int, ...] = ()
) -> Iterator[tuple[tuple[int, ...], Task]]:
    yield path, task
    for index, child in enumerate(task.children):
        yield from _iter_task_tree_with_paths(child, (*path, index))


def _validate_scalar(value: str, field: str, errors: list[str]) -> None:
    if _contains_newline(value):
        errors.append(f"{field} contains an embedded newline")


def _validate_task_for_construction(task: Task, errors: list[str]) -> None:
    for path, node in _iter_task_tree_with_paths(task):
        prefix = _task_path_label(path)
        if node.task_id is not None and _TASK_REF_RE.fullmatch(node.task_id) is None:
            errors.append(f"{prefix}.task_id has invalid task id {node.task_id!r}")
        _validate_scalar(node.text, f"{prefix}.text", errors)
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
    )
    _assert_task_field_stability(task)
    return task


def bug_count(plan: Plan) -> int:
    """Return the number of bug tasks in ``plan``.

    Returns ``0`` when ``plan.bugs is None`` (no Bugs section was parsed)
    and the count of top-level bug tasks otherwise. Verification scripts
    use this to surface a concrete count rather than the ambiguous
    ``bugs={p.bugs is not None}`` boolean (where ``bugs=False`` can be
    misread as "the Bugs section exists but is empty" instead of "no
    Bugs section was found"). The count covers root tasks only; nested
    subtask counts are left for callers that need them.
    """
    if plan.bugs is None:
        return 0
    return len(plan.bugs.tasks)


def _iter_tasks(tasks: tuple[Task, ...]) -> Iterator[Task]:
    """Yield every task in ``tasks``, descending into ``children`` first."""
    for task in tasks:
        yield task
        yield from _iter_tasks(task.children)


def _iter_plan_tasks(plan: Plan) -> Iterator[Task]:
    """Yield every Task in the plan: phase tasks, subsection tasks, bugs."""
    for phase in plan.phases:
        yield from _iter_tasks(phase.tasks)
        for subsection in phase.subsections:
            yield from _iter_tasks(subsection.tasks)
    if plan.bugs is not None:
        yield from _iter_tasks(plan.bugs.tasks)


def _find_task_by_id(plan: Plan, task_id: str) -> Task | None:
    """Return the task whose ``task_id`` equals ``task_id``, or ``None``.

    Walks the parsed tree via :func:`_iter_plan_tasks` and compares
    ``task.task_id == task_id``. The library MUST NOT resolve task
    references with substring matching (e.g. ``task_id in line``)
    because ``T-NNNNNN`` IDs prefix-overlap: ``T-000001`` is a substring
    of ``T-0000010``, so a substring search would conflate the two. Per
    design doc section 7.2 caveat. Returns the first match in iteration
    order; a well-formed plan has unique IDs, so the order only matters
    when callers want to surface a duplicate-id diagnostic separately.
    """
    for task in _iter_plan_tasks(plan):
        if task.task_id == task_id:
            return task
    return None


def _task_ref(task: Task) -> str:
    """Return a short reference for ``task`` for use in validator messages.

    Prefers the stable ``T-NNNNNN`` id when present, falling back to the
    1-based source line number for compat-mode tasks (no id). Validation
    messages must locate the offending task uniquely; both forms appear
    elsewhere in the codebase (``T-...`` in deps references, ``line N``
    in mcloop's parser diagnostics), so reusing them keeps the human
    fix-it experience consistent.
    """
    return task.task_id if task.task_id is not None else f"line {task.line_number}"


def _check_leading_bracket_tag(task: Task, errors: list[str]) -> None:
    """Flag a leading bracket form that does not match a known tag.

    Per design doc section 4.3, leading-position tags are ``[USER]``,
    ``[BATCH]``, and ``[AUTO:<word>]``. The parser strips known tags
    from the task body, so any tag-shaped bracket form still at the
    leading position of ``task.text`` is by definition unknown to this
    library (either a typo or an attempt to add a new tag without a
    library change). ``[RULEDOUT]`` is a sibling line, not a task tag
    (design doc section 4.3, planfile.md:415-417); when it appears at
    the leading position of a task body it is prose (the task title
    documents the RULEDOUT feature itself), so it is skipped here
    rather than reported as an unknown tag.

    Lowercase bracket forms and multi-word bracket forms are prose
    (``_LEADING_TAG_LIKE_RE`` requires an all-caps identifier of two
    or more characters), so a task that legitimately starts with prose
    like ``[note] do thing`` is not flagged. ``[USER]`` and ``[BATCH]``
    appearing here are skipped: if the parser left them in text it is
    a parser bug, not a validation concern, and double-reporting would
    confuse the user fixing the file.
    """
    m = _LEADING_TAG_LIKE_RE.match(task.text)
    if m is None:
        return
    content = m.group(1)
    if content in _KNOWN_LEADING_FLAGS:
        return
    if content in _RESERVED_SIBLING_MARKERS:
        return
    if ":" in content and content.split(":", 1)[0] == "AUTO":
        return
    errors.append(f"task {_task_ref(task)} has unknown bracket tag [{content}]")


def _check_trailing_annotation(task: Task, errors: list[str]) -> None:
    """Flag a trailing bracket form that looks like a broken annotation.

    Per design doc section 4.2, an annotation is ``[key: value]``: an
    identifier-shaped key, a colon, mandatory whitespace, then a
    non-empty value. The parser strips well-formed annotations from
    the task body; a trailing bracket still in ``task.text`` whose
    content has the ``key:`` prefix but does not satisfy
    ``key: value`` (missing whitespace, empty value, etc.) is the
    canonical malformed-annotation case.

    Bracket forms that do not look like annotation attempts at all
    (no colon, or no identifier-shaped prefix before the colon) are
    treated as prose and left alone — flagging ``[some text]`` at end
    of a task description would produce more false positives than
    real catches. The malformed signal is the *colon* that the author
    typed when reaching for an annotation.
    """
    m = _TRAILING_BRACKET_RE.search(task.text)
    if m is None:
        return
    content = m.group(1)
    if _ANNOTATION_KEY_RE.match(content) is None:
        return
    if _ANNOTATION_OK_RE.match(content) is not None:
        return
    errors.append(f"task {_task_ref(task)} has malformed annotation [{content}]")


def validate_plan(plan: Plan, *, constructed: bool = False) -> None:
    """Validate structural and referential integrity of ``plan``.

    Raises :class:`PlanValidationError` carrying one message per problem
    found; validation does not short-circuit on the first failure so a
    single run surfaces every fix the user needs to make. Checks, in
    the order they are reported:

    1. **Duplicate task ids.** Each ``T-NNNNNN`` must occur exactly
       once in the plan. Tasks without an id (compat-mode) are not
       counted. Per design doc section 7.2: task ids are the canonical
       reference, so two tasks sharing one id makes ``@deps`` ambiguous
       and ``complete_task`` / ``fail_task`` non-deterministic.
    2. **Unknown bracket tags.** A bracket form at the leading position
       of any task body that does not match a known tag (``[USER]``,
       ``[BATCH]``, ``[AUTO:<word>]``) — per design doc section 4.2
       Notes, "unknown bracket tags are rejected by validation, not
       silently ignored. New tags require a library change." Detection
       is delegated to :func:`_check_leading_bracket_tag`.
    3. **Malformed annotations.** A trailing bracket form that looks
       like an annotation attempt (``[key:value]``) but does not match
       the ``key: value`` shape the parser accepts. Detection is
       delegated to :func:`_check_trailing_annotation`.
    4. **Unknown ``@deps`` references.** Every task id listed in any
       task's ``deps`` must resolve to a known task id in the plan
       (design doc section 8 phase A: "validation requires referenced
       IDs to exist in the plan"). Duplicate ids are still added to
       the known set so dep references resolve, since the duplicate
       diagnostic above already reports the underlying problem.

    Parse-time concerns (syntax, structure of headings) are not
    re-checked here; the parser raises :class:`PlanSyntaxError` for
    those.

    When ``constructed=True`` (per v4 Contract 4), additionally enforces
    the construction-API invariants: ``magic_version == 1``; phase
    ordinals unique and contiguous ``1..N``; ``keyword`` in ``{"Phase",
    "Stage"}``; every phase has ``phase_id`` and ``phase_id_source !=
    "none"``; every task carries a ``T-NNNNNN`` id; no duplicate phase
    ids; no ``trailing_lines`` on any task; and semantic field-stability
    over every task plus the non-task scalars (``project_title``,
    ``preamble``, each ``Phase.title`` / ``Phase.prose``, each
    ``Subsection.title`` / ``Subsection.prose``) per the v4 R3 oracle.
    ``constructed=False`` preserves the task-centric behavior above
    exactly; the Stage 10 task field-stability harness is reused
    for the per-task check rather than duplicated here.
    """
    errors: list[str] = []

    id_lines: dict[str, list[int]] = {}
    for task in _iter_plan_tasks(plan):
        if task.task_id is None:
            continue
        id_lines.setdefault(task.task_id, []).append(task.line_number)

    for task_id, lines in id_lines.items():
        if len(lines) > 1:
            locs = ", ".join(str(n) for n in lines)
            errors.append(f"duplicate task id {task_id} at lines {locs}")

    known_ids: set[str] = set(id_lines.keys())

    for task in _iter_plan_tasks(plan):
        _check_leading_bracket_tag(task, errors)
        _check_trailing_annotation(task, errors)

    for task in _iter_plan_tasks(plan):
        for dep in task.deps:
            if dep not in known_ids:
                errors.append(f"task {_task_ref(task)} references unknown dep {dep}")

    if constructed:
        _check_constructed_invariants(plan, errors)

    if errors:
        raise PlanValidationError(errors)


def _plan_phase_path(phase_index: int) -> str:
    return f"phases[{phase_index}]"


def _plan_subsection_path(phase_index: int, sub_index: int) -> str:
    return f"phases[{phase_index}].subsections[{sub_index}]"


def _iter_plan_top_level_tasks_with_label(
    plan: Plan,
) -> Iterator[tuple[str, Task]]:
    """Yield ``(label, task)`` for each top-level task in the plan.

    ``label`` locates the task within the plan structure
    (``phases[i].tasks[j]``, ``phases[i].subsections[j].tasks[k]``, or
    ``bugs.tasks[i]``) so validator messages can identify which task's
    round-trip failed without descending into children. Child tasks are
    not yielded: field-stability is checked per-tree, since the Stage 10
    harness recurses through children when invoked on the root task.
    """
    for phase_index, phase in enumerate(plan.phases):
        for task_index, task in enumerate(phase.tasks):
            yield f"{_plan_phase_path(phase_index)}.tasks[{task_index}]", task
        for sub_index, sub in enumerate(phase.subsections):
            for task_index, task in enumerate(sub.tasks):
                yield (
                    f"{_plan_subsection_path(phase_index, sub_index)}"
                    f".tasks[{task_index}]",
                    task,
                )
    if plan.bugs is not None:
        for task_index, task in enumerate(plan.bugs.tasks):
            yield f"bugs.tasks[{task_index}]", task


def _iter_plan_tasks_with_label(
    plan: Plan,
) -> Iterator[tuple[str, Task]]:
    """Yield ``(label, task)`` for every task in the plan, including children.

    Labels follow :func:`_iter_plan_top_level_tasks_with_label` with the
    child path appended (``...children[i]...``). Used by the
    constructed-mode checks that operate per-node (``task_id`` presence
    and ``trailing_lines`` shape) rather than per-tree.
    """
    for label, root_task in _iter_plan_top_level_tasks_with_label(plan):
        for path, node in _iter_task_tree_with_paths(root_task):
            yield label + _child_path_suffix(path), node


def _child_path_suffix(path: tuple[int, ...]) -> str:
    return "".join(f".children[{index}]" for index in path)


def _check_constructed_invariants(plan: Plan, errors: list[str]) -> None:
    """Add v4 Contract 4 ``constructed=True`` violations to ``errors``.

    Order matches the contract text so error output is stable across
    runs: magic_version, phase ordinals, per-phase keyword and
    phase_id, duplicate phase ids, per-task id and trailing_lines,
    non-task scalar field-stability oracles (v4 R3), then per-task
    field-stability via the Stage 10 harness.
    """
    if plan.magic_version != 1:
        errors.append(
            f"plan.magic_version must be 1 on constructed plans, "
            f"got {plan.magic_version!r}"
        )

    expected_ordinals = list(range(1, len(plan.phases) + 1))
    actual_ordinals = [phase.ordinal for phase in plan.phases]
    if actual_ordinals != expected_ordinals:
        errors.append(
            f"phase ordinals must be contiguous 1..{len(plan.phases)}, "
            f"got {actual_ordinals}"
        )

    phase_id_positions: dict[str, list[int]] = {}
    for phase_index, phase in enumerate(plan.phases):
        if phase.keyword not in ("Phase", "Stage"):
            errors.append(
                f"{_plan_phase_path(phase_index)}.keyword must be "
                f"'Phase' or 'Stage', got {phase.keyword!r}"
            )
        if phase.phase_id is None or phase.phase_id_source == "none":
            errors.append(
                f"{_plan_phase_path(phase_index)} missing phase_id "
                f"(source {phase.phase_id_source!r})"
            )
        if phase.phase_id is not None:
            phase_id_positions.setdefault(phase.phase_id, []).append(phase_index)

    for phase_id, positions in phase_id_positions.items():
        if len(positions) > 1:
            errors.append(f"duplicate phase_id {phase_id} at phases {positions}")

    for label, task in _iter_plan_tasks_with_label(plan):
        if task.task_id is None:
            errors.append(f"{label}.task_id is missing on constructed task")
        elif _TASK_REF_RE.fullmatch(task.task_id) is None:
            errors.append(
                f"{label}.task_id is malformed on constructed task: {task.task_id!r}"
            )
        if task.trailing_lines:
            errors.append(f"{label}.trailing_lines must be empty on constructed tasks")

    _check_non_task_scalar_field_stability(plan, errors)
    _check_each_task_field_stability(plan, errors)


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


def _check_non_task_scalar_field_stability(plan: Plan, errors: list[str]) -> None:
    """Run the v4 R3 oracles for each non-task scalar in ``plan``.

    Pre-filters reject embedded ``\\n``/``\\r`` unconditionally — there
    is no multi-line prose exception (v4 R3). Each value is rendered
    inside a minimal canonical plan, the result re-parsed, and the
    parsed scalar required to equal the candidate; inequality surfaces
    a ``...failed to round-trip`` message naming the offending field
    so a rephrase loop can target it.
    """
    if _contains_newline(plan.project_title):
        errors.append("project_title contains an embedded newline")
    else:
        _round_trip_scalar(
            _construction_sentinel_plan(project_title=plan.project_title),
            lambda parsed: parsed.project_title,
            plan.project_title,
            "project_title",
            errors,
        )

    if _contains_newline(plan.preamble):
        errors.append("preamble contains an embedded newline")
    else:
        _round_trip_scalar(
            _construction_sentinel_plan(preamble=plan.preamble),
            lambda parsed: parsed.preamble,
            plan.preamble,
            "preamble",
            errors,
        )

    for phase_index, phase in enumerate(plan.phases):
        title_field = f"{_plan_phase_path(phase_index)}.title"
        if _contains_newline(phase.title):
            errors.append(f"{title_field} contains an embedded newline")
        else:
            _round_trip_scalar(
                _construction_sentinel_plan(
                    phase=_construction_sentinel_phase(title=phase.title)
                ),
                lambda parsed: parsed.phases[0].title,
                phase.title,
                title_field,
                errors,
            )

        prose_field = f"{_plan_phase_path(phase_index)}.prose"
        if _contains_newline(phase.prose):
            errors.append(f"{prose_field} contains an embedded newline")
        else:
            _round_trip_scalar(
                _construction_sentinel_plan(
                    phase=_construction_sentinel_phase(prose=phase.prose)
                ),
                lambda parsed: parsed.phases[0].prose,
                phase.prose,
                prose_field,
                errors,
            )

        for sub_index, sub in enumerate(phase.subsections):
            sub_title_field = f"{_plan_subsection_path(phase_index, sub_index)}.title"
            if _contains_newline(sub.title):
                errors.append(f"{sub_title_field} contains an embedded newline")
            else:
                _round_trip_scalar(
                    _construction_sentinel_plan(
                        phase=_construction_sentinel_phase(
                            tasks=(),
                            subsections=(
                                Subsection(
                                    title=sub.title,
                                    prose="",
                                    tasks=(_construction_sentinel_task(),),
                                    line_number=0,
                                ),
                            ),
                        )
                    ),
                    lambda parsed: parsed.phases[0].subsections[0].title,
                    sub.title,
                    sub_title_field,
                    errors,
                )

            sub_prose_field = f"{_plan_subsection_path(phase_index, sub_index)}.prose"
            if _contains_newline(sub.prose):
                errors.append(f"{sub_prose_field} contains an embedded newline")
            else:
                _round_trip_scalar(
                    _construction_sentinel_plan(
                        phase=_construction_sentinel_phase(
                            tasks=(),
                            subsections=(
                                Subsection(
                                    title="S",
                                    prose=sub.prose,
                                    tasks=(_construction_sentinel_task(),),
                                    line_number=0,
                                ),
                            ),
                        )
                    ),
                    lambda parsed: parsed.phases[0].subsections[0].prose,
                    sub.prose,
                    sub_prose_field,
                    errors,
                )


def _check_each_task_field_stability(plan: Plan, errors: list[str]) -> None:
    """Run the Stage 10 per-task harness for every top-level task in ``plan``.

    The Stage 10 harness (:func:`_assert_task_field_stability`) recurses
    through ``children``, so iterating only the top-level tasks is
    sufficient. Per-task harness failures are re-prefixed with the
    task's plan-location label so the user knows which task in the
    full plan failed to round-trip without losing the per-field
    diagnostic the harness already produced.
    """
    for label, task in _iter_plan_top_level_tasks_with_label(plan):
        try:
            _assert_task_field_stability(task)
        except PlanValidationError as exc:
            for message in exc.messages:
                errors.append(f"{label}: {message}")


# Mirrors mcloop._planfile_precondition._INCOMPLETE_RE
# (mcloop/_planfile_precondition.py:56). Identical pattern so the
# R1-equivalent below decides the same way as mcloop's real
# ``enforce_canonical`` would on the same rendered text; the cross-repo
# parity test (Stage 17) pins that equivalence.
_INCOMPLETE_CHECKBOX_RE = re.compile(r"^\s*- \[ \] .+$", re.MULTILINE)


def _normalize_plan_for_semantic_compare(plan: Plan) -> Plan:
    """Return ``plan`` with non-semantic position fields normalized.

    Per v4 Contract 5: line numbers, ``Task.indent_level``,
    ``Plan.source_path``, ``Task.trailing_lines``, and the
    ``explicit_header`` / ``explicit_comment`` equivalence are the only
    differences allowed between a constructed plan and its
    render→parse image. Every other field participates in equality.
    The renderer's :func:`bob_tools.planfile.renderer.normalize_positions`
    does most of this but leaves ``source_path`` and ``trailing_lines``
    untouched (renderer.py:269; intentional for compat-mode use), so
    Contract 5 needs a dedicated normalizer.
    """
    return dataclasses.replace(
        plan,
        source_path=None,
        phases=tuple(_normalize_phase_for_semantic_compare(p) for p in plan.phases),
        bugs=(
            _normalize_bugs_for_semantic_compare(plan.bugs)
            if plan.bugs is not None
            else None
        ),
    )


def _normalize_phase_for_semantic_compare(phase: Phase) -> Phase:
    canonical_source = "explicit_comment" if phase.phase_id_source != "none" else "none"
    return dataclasses.replace(
        phase,
        line_number=0,
        phase_id_source=canonical_source,
        subsections=tuple(
            _normalize_subsection_for_semantic_compare(s) for s in phase.subsections
        ),
        tasks=tuple(_normalize_task_for_position(t, depth=0) for t in phase.tasks),
    )


def _normalize_subsection_for_semantic_compare(sub: Subsection) -> Subsection:
    return dataclasses.replace(
        sub,
        line_number=0,
        tasks=tuple(_normalize_task_for_position(t, depth=0) for t in sub.tasks),
    )


def _normalize_bugs_for_semantic_compare(bugs: BugsSection) -> BugsSection:
    return dataclasses.replace(
        bugs,
        line_number=0,
        tasks=tuple(_normalize_task_for_position(t, depth=0) for t in bugs.tasks),
    )


def _normalize_task_for_position(task: Task, *, depth: int) -> Task:
    """Clear position fields and ``trailing_lines`` on ``task`` and its tree.

    The Stage 10 task-only normalizer rejects nonempty ``trailing_lines``
    rather than clearing them, because at construction time any opaque
    retained markdown is precisely the escape hatch Path 1 forbids.
    McLoop's canonical input contract is intentionally narrower: it
    normalizes ``trailing_lines`` as position/source trivia while the
    R1-equivalent count check below catches the meaningful leak class —
    checkbox lines that did not surface as tasks. That lets canonical
    save operate on mcloop-canonical plans regardless of whether they
    were produced by the construction API.
    """
    return dataclasses.replace(
        task,
        line_number=0,
        indent_level=depth * 2,
        trailing_lines=(),
        children=tuple(
            _normalize_task_for_position(c, depth=depth + 1) for c in task.children
        ),
        ruled_out=tuple(dataclasses.replace(r, line_number=0) for r in task.ruled_out),
    )


def _collect_plan_semantic_diff(
    intended: Plan, parsed: Plan, errors: list[str]
) -> None:
    for field in ("magic_version", "project_title", "preamble"):
        intended_value = getattr(intended, field)
        parsed_value = getattr(parsed, field)
        if intended_value != parsed_value:
            errors.append(
                f"plan.{field} failed semantic round-trip: "
                f"intended {intended_value!r}, parsed {parsed_value!r}"
            )
    if len(intended.phases) != len(parsed.phases):
        errors.append(
            f"plan.phases count failed semantic round-trip: "
            f"intended {len(intended.phases)}, parsed {len(parsed.phases)}"
        )
    else:
        for index, (intended_phase, parsed_phase) in enumerate(
            zip(intended.phases, parsed.phases, strict=True)
        ):
            _collect_phase_semantic_diff(
                intended_phase, parsed_phase, f"phases[{index}]", errors
            )
    if (intended.bugs is None) != (parsed.bugs is None):
        intended_state = "present" if intended.bugs is not None else "absent"
        parsed_state = "present" if parsed.bugs is not None else "absent"
        errors.append(
            f"plan.bugs presence failed semantic round-trip: "
            f"intended {intended_state}, parsed {parsed_state}"
        )
    elif intended.bugs is not None and parsed.bugs is not None:
        _collect_bugs_semantic_diff(intended.bugs, parsed.bugs, errors)


def _collect_phase_semantic_diff(
    intended: Phase, parsed: Phase, label: str, errors: list[str]
) -> None:
    for field in (
        "phase_id",
        "phase_id_source",
        "ordinal",
        "keyword",
        "title",
        "prose",
    ):
        intended_value = getattr(intended, field)
        parsed_value = getattr(parsed, field)
        if intended_value != parsed_value:
            errors.append(
                f"{label}.{field} failed semantic round-trip: "
                f"intended {intended_value!r}, parsed {parsed_value!r}"
            )
    if len(intended.tasks) != len(parsed.tasks):
        errors.append(
            f"{label}.tasks count failed semantic round-trip: "
            f"intended {len(intended.tasks)}, parsed {len(parsed.tasks)}"
        )
    else:
        for index, (ta, tb) in enumerate(
            zip(intended.tasks, parsed.tasks, strict=True)
        ):
            _collect_task_semantic_diff(ta, tb, f"{label}.tasks[{index}]", errors)
    if len(intended.subsections) != len(parsed.subsections):
        errors.append(
            f"{label}.subsections count failed semantic round-trip: "
            f"intended {len(intended.subsections)}, parsed {len(parsed.subsections)}"
        )
    else:
        for index, (sa, sb) in enumerate(
            zip(intended.subsections, parsed.subsections, strict=True)
        ):
            _collect_subsection_semantic_diff(
                sa, sb, f"{label}.subsections[{index}]", errors
            )


def _collect_subsection_semantic_diff(
    intended: Subsection, parsed: Subsection, label: str, errors: list[str]
) -> None:
    for field in ("title", "prose"):
        intended_value = getattr(intended, field)
        parsed_value = getattr(parsed, field)
        if intended_value != parsed_value:
            errors.append(
                f"{label}.{field} failed semantic round-trip: "
                f"intended {intended_value!r}, parsed {parsed_value!r}"
            )
    if len(intended.tasks) != len(parsed.tasks):
        errors.append(
            f"{label}.tasks count failed semantic round-trip: "
            f"intended {len(intended.tasks)}, parsed {len(parsed.tasks)}"
        )
    else:
        for index, (ta, tb) in enumerate(
            zip(intended.tasks, parsed.tasks, strict=True)
        ):
            _collect_task_semantic_diff(ta, tb, f"{label}.tasks[{index}]", errors)


def _collect_bugs_semantic_diff(
    intended: BugsSection, parsed: BugsSection, errors: list[str]
) -> None:
    if len(intended.tasks) != len(parsed.tasks):
        errors.append(
            f"bugs.tasks count failed semantic round-trip: "
            f"intended {len(intended.tasks)}, parsed {len(parsed.tasks)}"
        )
        return
    for index, (ta, tb) in enumerate(zip(intended.tasks, parsed.tasks, strict=True)):
        _collect_task_semantic_diff(ta, tb, f"bugs.tasks[{index}]", errors)


def _collect_task_semantic_diff(
    intended: Task, parsed: Task, label: str, errors: list[str]
) -> None:
    for field in (
        "task_id",
        "text",
        "status",
        "flag_tags",
        "action_tag",
        "annotations",
        "deps",
    ):
        intended_value = getattr(intended, field)
        parsed_value = getattr(parsed, field)
        if intended_value != parsed_value:
            errors.append(
                f"{label}.{field} failed semantic round-trip: "
                f"intended {intended_value!r}, parsed {parsed_value!r}"
            )
    if len(intended.ruled_out) != len(parsed.ruled_out):
        errors.append(
            f"{label}.ruled_out count failed semantic round-trip: "
            f"intended {len(intended.ruled_out)}, parsed {len(parsed.ruled_out)}"
        )
    else:
        for index, (ra, rb) in enumerate(
            zip(intended.ruled_out, parsed.ruled_out, strict=True)
        ):
            if ra.text != rb.text:
                errors.append(
                    f"{label}.ruled_out[{index}].text failed semantic round-trip: "
                    f"intended {ra.text!r}, parsed {rb.text!r}"
                )
    if len(intended.children) != len(parsed.children):
        errors.append(
            f"{label}.children count failed semantic round-trip: "
            f"intended {len(intended.children)}, parsed {len(parsed.children)}"
        )
        return
    for index, (ca, cb) in enumerate(
        zip(intended.children, parsed.children, strict=True)
    ):
        _collect_task_semantic_diff(ca, cb, f"{label}.children[{index}]", errors)


def _count_todo_tasks(plan: Plan) -> int:
    return sum(1 for task in _iter_plan_tasks(plan) if task.status is TaskStatus.TODO)


def assert_mcloop_canonical(plan: Plan, *, source_path: Path | None = None) -> str:
    """Validate ``plan`` to mcloop's canonical-input contract; return rendered text.

    Per v4 Contract 5 as amended: renders the plan; re-parses the
    rendered text; requires SEMANTIC equality of parsed-vs-intended
    after normalizing only ``line_number``, ``Task.indent_level``,
    ``Plan.source_path``, and ``Task.trailing_lines`` (NOT a byte fixed
    point — the v3 leak class can byte-fixed-point while semantically
    diverging); then enforces the R1/R2 equivalents independently, so
    mcloop is not imported.

    This is deliberately separate from the construction-API contract in
    :func:`validate_plan` with ``constructed=True``. McLoop's real
    ``enforce_canonical`` does not require ``magic_version == 1`` or the
    constructed-mode field-stability invariants; callers that need those
    construction guarantees must request them explicitly via
    ``validate_plan(plan, constructed=True)``.

    * **R1 (grammar-narrowing equivalent).** Every ``- [ ]`` line in
      the rendered text must surface as a parsed ``TaskStatus.TODO``
      task. Pattern lifted from
      ``mcloop._planfile_precondition._INCOMPLETE_RE`` so the predicate
      decides the same way as mcloop's real ``enforce_canonical`` on
      the same text; the cross-repo parity test pins that equivalence.
    * **R2 (id-less equivalent).** Every parsed task must carry a
      ``T-NNNNNN`` id.

    Returns the validated rendered text so the caller persists exactly
    what was checked. Raises :class:`PlanValidationError` on any
    violation; :class:`bob_tools.planfile.PlanSyntaxError` from the
    re-parse propagates unchanged (a re-parse failure on rendered
    output is a library bug, not user input error).

    ``source_path`` is forwarded to the re-parse so a re-parse syntax
    error surfaces with the correct file context for the caller.
    """
    rendered = render_plan(plan)
    reparsed = parse_plan(rendered, source_path=source_path)

    errors: list[str] = []

    intended_normalized = _normalize_plan_for_semantic_compare(plan)
    parsed_normalized = _normalize_plan_for_semantic_compare(reparsed)
    _collect_plan_semantic_diff(intended_normalized, parsed_normalized, errors)

    src_incomplete = len(_INCOMPLETE_CHECKBOX_RE.findall(rendered))
    plan_incomplete = _count_todo_tasks(reparsed)
    if src_incomplete > plan_incomplete:
        dropped = src_incomplete - plan_incomplete
        errors.append(
            f"rendered text contains {src_incomplete} incomplete checkbox "
            f"line(s) but parsed plan surfaced only {plan_incomplete} "
            f"TODO task(s); {dropped} task(s) silently dropped"
        )

    idless_lines = [
        task.line_number for task in _iter_plan_tasks(reparsed) if task.task_id is None
    ]
    if idless_lines:
        shown = idless_lines[:10]
        locs = ", ".join(f"line {n}" for n in shown)
        more = (
            "" if len(idless_lines) <= 10 else f", plus {len(idless_lines) - 10} more"
        )
        errors.append(
            f"parsed plan has {len(idless_lines)} task(s) without "
            f"stable T-NNNNNN id(s) ({locs}{more})"
        )

    if errors:
        raise PlanValidationError(errors)

    return rendered


def _task_matches_label(task: Task, ref: str) -> bool:
    """Return ``True`` when ``ref`` resolves to ``task``.

    Match order (each step decisive; no fall-through ambiguity):

    1. Exact match against ``task.task_id``. This is the canonical
       form and is the only path that survives once :func:`migrate`
       has assigned ids to every task. Per design doc section 7.2
       caveat, the comparison is whole-string equality so the
       ``T-000001`` / ``T-0000010`` prefix overlap cannot conflate
       two distinct tasks.
    2. Exact match against ``task.text``.
    3. Prefix match where ``task.text`` begins with ``ref`` followed
       by a structural separator (``:``, ``)``, or whitespace). Pre-id
       PLAN.md files put a duplo-style label at the start of the task
       text (``task-001: Bring up scaffold``); matching with a required
       trailing separator keeps ``task-001`` from matching
       ``task-0010: ...``.

    Substring matches without a trailing separator are deliberately
    rejected: that was the bug the design doc calls out as the
    motivation for moving resolution off raw-text scanning and onto
    parsed task entries.
    """
    if task.task_id is not None and task.task_id == ref:
        return True
    if task.text == ref:
        return True
    return any(task.text.startswith(ref + sep) for sep in (":", ")", " ", "\t"))


def _resolve_positional_label(plan: Plan, label: str) -> tuple[Phase, int, Task] | None:
    """Resolve a positional label like ``1.3.2`` to ``(phase, doc_index, task)``.

    Mirrors mcloop's ``task_label`` output (``checklist.py``): the first
    dot-separated token is the phase ordinal as printed in the
    ``Stage N`` / ``Phase N`` heading (i.e. ``Phase.ordinal``); each
    subsequent token is a 1-based positional index into the task tree.
    The second token picks a root task from ``phase.tasks``, the third
    picks a child of that task, and so on. Subsection tasks are
    intentionally not addressable by positional label — they sit under
    a sub-heading whose stage number is empty in mcloop, so mcloop never
    produces ``N.M`` labels for them. Mirroring that here keeps the
    resolver consistent with the strings mcloop actually emits.

    ``doc_index`` is the 1-based position of the matched phase within
    ``plan.phases``, surfaced so :func:`resolve_task_context` can
    synthesize an ordinal-fallback phase_id when the matched phase has
    ``phase_id_source == "none"`` (design doc section 7.1). The
    distinction matters: ``Phase.ordinal`` comes from the heading text
    (``Stage 5`` → ``ordinal == 5``), but a plan whose first phase is
    ``Stage 5`` still has that phase at ``doc_index == 1``.

    Returns ``None`` when ``label`` is not in ``N.M[.K...]`` form (the
    pattern is anchored — at least two dot-separated all-numeric
    tokens), when no phase has the requested ordinal, or when any
    positional index along the path is out of range. Bare ``N`` (no
    dots) is intentionally not a positional label: it would be too
    easy to collide with an unrelated single-digit literal in
    ``task.text``, and mcloop's ``task_label`` always emits at least
    a stage-plus-position pair for stage-headed sections.

    Tokenization is explicit: ``label.split(".")`` and integer
    conversion under the anchored regex above. The resolver MUST NOT
    fall back to ``ref in task.text``-style substring scanning — that
    is the bug the design doc section 7.2 caveat calls out and the
    reason this helper exists.
    """
    if _POSITIONAL_LABEL_RE.match(label) is None:
        return None
    parts = [int(token) for token in label.split(".")]
    phase_ordinal, *task_indexes = parts

    matched_phase: Phase | None = None
    matched_doc_index: int = 0
    for doc_index, phase in enumerate(plan.phases, start=1):
        if phase.ordinal == phase_ordinal:
            matched_phase = phase
            matched_doc_index = doc_index
            break
    if matched_phase is None:
        return None

    current_tasks: tuple[Task, ...] = matched_phase.tasks
    matched_task: Task | None = None
    for index in task_indexes:
        if index < 1 or index > len(current_tasks):
            return None
        matched_task = current_tasks[index - 1]
        current_tasks = matched_task.children

    if matched_task is None:
        return None
    return matched_phase, matched_doc_index, matched_task


def _iter_phase_tasks_with_phase(
    plan: Plan,
) -> Iterator[tuple[Task, Phase, int]]:
    """Yield ``(task, phase, doc_index)`` for every task inside a phase.

    ``doc_index`` is the 1-based position of ``phase`` within
    ``plan.phases``; :func:`resolve_task_context` uses it to synthesize
    a ``phase_NNN`` ordinal fallback when the containing phase has no
    explicit id (design doc section 7.1, "Ordinal fallback. The n-th
    phase heading in document order"). The plan's positional addressing
    uses 1-based indexes throughout, so the synthesized id starts at
    ``phase_001``.

    Bug tasks are not yielded here; resolve_task_context handles them
    in a separate pass so the ``phase`` parameter is never ``None``
    inside this iterator. Subsection tasks share their parent phase's
    identity for resolution purposes (design doc section 11 question 5:
    subsections are humans-only grouping; they have no phase_id of
    their own).
    """
    for doc_index, phase in enumerate(plan.phases, start=1):
        for task in _iter_tasks(phase.tasks):
            yield task, phase, doc_index
        for subsection in phase.subsections:
            for task in _iter_tasks(subsection.tasks):
                yield task, phase, doc_index


def _phase_id_for_task_context(phase: Phase, doc_index: int) -> tuple[str | None, str]:
    """Return the ``(phase_id, phase_id_source)`` pair for a TaskContext.

    Pass-through when the phase already has an explicit identifier
    (``"explicit_comment"`` or ``"explicit_header"``). When the
    containing phase has ``phase_id_source == "none"`` the function
    synthesizes the ordinal-derived id ``phase_{doc_index:03d}`` and
    reports ``phase_id_source == "ordinal"``, per design doc section
    7.1 ("Ordinal fallback. The n-th phase heading in document order")
    and section 2.4 (the explicit-required / ordinal-degraded
    contract). The synthesis lets callers proceed with a usable
    phase_id rather than having to thread a separate
    ``ordinal_index`` argument through the resolver, which is exactly
    the simplification that section 7.1's shim sketch is aiming at.
    """
    if phase.phase_id_source == "none":
        return f"phase_{doc_index:03d}", "ordinal"
    return phase.phase_id, phase.phase_id_source


def resolve_task_context(plan: Plan, task_label_or_id: str) -> TaskContext:
    """Resolve a task reference to its containing phase context.

    Replaces ``ledger_emit.find_explicit_phase_id_for_task``. Per design
    doc section 7.1, this is the single resolver for the task → phase
    mapping; ``ledger_emit.resolve_phase_id`` becomes a thin shim that
    promotes the returned :class:`TaskContext` to the ledger's
    ``PhaseIdResolution`` shape and supplies the ordinal-degraded
    fallback when the explicit lookup misses.

    Resolution walks parsed :class:`Task` entries (not raw text) using
    :func:`_task_matches_label`. Phase tasks are searched first, in
    document order, including subsection tasks. Bug-section tasks are
    searched last and return ``phase_id=None`` / ``phase_id_source=
    "none"`` since bugs have no containing phase. Unresolved references
    fall through to the same none-shaped :class:`TaskContext` so
    callers branch on ``phase_id is None`` (or on ``task_id is None``
    when they need to distinguish "matched a bug" from "matched
    nothing"), not on exceptions.

    When the matched task's containing phase has ``phase_id_source ==
    "none"`` (no ``<!-- phase_id: ... -->`` comment and no legacy
    ``## Phase phase_NNN: ...`` header), the resolver synthesizes an
    ordinal-derived ``phase_NNN`` id from the phase's 1-based position
    in ``plan.phases`` and reports ``phase_id_source == "ordinal"``.
    This implements the ordinal-fallback half of the explicit-required
    / ordinal-degraded contract inside the library, per design doc
    sections 2.4 and 7.1, so the ledger_emit shim does not need a
    separate ``ordinal_index`` pass over the plan to recover a usable
    phase_id. Bug-section tasks and unresolved references stay
    ``phase_id_source == "none"`` — they are not contained in a phase,
    so there is no ordinal to attribute to.

    ``plan_phase_count`` is always populated from ``len(plan.phases)``
    so the ordinal-fallback shim has the count it needs without a
    second pass over the plan.

    Positional labels (``N.M[.K...]``, as emitted by mcloop's
    ``task_label``) are tried first and bypass the task-walk
    entirely: tokenized index lookup is unambiguous, so a
    well-formed positional reference cannot be confused with a
    ``task_id`` or with task text — see :func:`_resolve_positional_label`
    for the contract. When the format does not match, when no phase
    has the requested ordinal, or when an index along the path is out
    of range, positional resolution returns ``None`` and the walk
    falls through to ``task_id`` / text matching.
    """
    plan_phase_count = len(plan.phases)

    positional = _resolve_positional_label(plan, task_label_or_id)
    if positional is not None:
        phase, doc_index, task = positional
        phase_id, phase_id_source = _phase_id_for_task_context(phase, doc_index)
        return TaskContext(
            task_id=task.task_id,
            phase_id=phase_id,
            phase_id_source=phase_id_source,
            label=task_label_or_id,
            plan_phase_count=plan_phase_count,
        )

    for task, phase, doc_index in _iter_phase_tasks_with_phase(plan):
        if _task_matches_label(task, task_label_or_id):
            phase_id, phase_id_source = _phase_id_for_task_context(phase, doc_index)
            return TaskContext(
                task_id=task.task_id,
                phase_id=phase_id,
                phase_id_source=phase_id_source,
                label=task_label_or_id,
                plan_phase_count=plan_phase_count,
            )

    if plan.bugs is not None:
        for task in _iter_tasks(plan.bugs.tasks):
            if _task_matches_label(task, task_label_or_id):
                return TaskContext(
                    task_id=task.task_id,
                    phase_id=None,
                    phase_id_source="none",
                    label=task_label_or_id,
                    plan_phase_count=plan_phase_count,
                )

    return TaskContext(
        task_id=None,
        phase_id=None,
        phase_id_source="none",
        label=task_label_or_id,
        plan_phase_count=plan_phase_count,
    )


def _build_done_ids(plan: Plan) -> set[str]:
    """Return the set of ``task_id`` values for every DONE task in ``plan``.

    Used by :func:`next_tasks` to evaluate ``@deps`` satisfaction in a
    single pass per call rather than re-walking the plan per task. Tasks
    without a ``task_id`` cannot be referenced by ``@deps`` (the
    canonical form uses ``T-NNNNNN``), so they are excluded.
    """
    return {
        task.task_id
        for task in _iter_plan_tasks(plan)
        if task.status == TaskStatus.DONE and task.task_id is not None
    }


def _deps_satisfied(task: Task, done_ids: set[str]) -> bool:
    """Return ``True`` when every ``@deps`` reference resolves to a DONE task.

    Per design doc section 6: dependencies that do not resolve to a known
    task are validation errors (raised by :func:`validate_plan`), not
    actionability blockers. ``next_tasks`` does not raise on unknown
    refs — it simply treats the dep as unsatisfied, so the task stays
    blocked. Callers run :func:`validate_plan` before scheduling to
    surface unknown refs as errors instead.
    """
    return all(dep in done_ids for dep in task.deps)


def _all_tasks_done(tasks: tuple[Task, ...]) -> bool:
    """Return ``True`` when every task in ``tasks`` (and descendants) is DONE."""
    for task in tasks:
        if task.status != TaskStatus.DONE:
            return False
        if not _all_tasks_done(task.children):
            return False
    return True


def _phase_complete(phase: Phase) -> bool:
    """Return ``True`` when every task in ``phase`` (root + subsections) is DONE.

    A FAILED task does not count as complete (the phase is stuck, not
    done); a TODO task does not count either. Mirrors mcloop's
    ``_stage_complete`` semantics.
    """
    if not _all_tasks_done(phase.tasks):
        return False
    return all(_all_tasks_done(sub.tasks) for sub in phase.subsections)


def _get_batch_children(parent: Task) -> tuple[Task, ...]:
    """Collect consecutive batchable children under a ``[BATCH]`` parent.

    Mirrors mcloop's ``get_batch_children`` (``checklist.py`` line 654):
    start from the first unchecked child and collect until hitting a
    ``[USER]`` or ``[AUTO:...]`` child, or running out of children.
    Already-DONE children are skipped (and bump ``seen_non_failed``).
    A FAILED child stops collection once at least one non-failed child
    has been seen — before that point, leading FAILED children are
    skipped, matching the mcloop behavior of treating early-failed
    children as a prelude rather than a dependency barrier.
    """
    batch: list[Task] = []
    seen_non_failed = False
    for child in parent.children:
        if child.status == TaskStatus.DONE:
            seen_non_failed = True
            continue
        if child.status == TaskStatus.FAILED:
            if batch or seen_non_failed:
                break
            continue
        if "USER" in child.flag_tags or child.action_tag is not None:
            break
        batch.append(child)
    return tuple(batch)


def _surface_batch_parent(parent: Task) -> Task:
    """Return a copy of ``parent`` whose ``children`` is the batch unit.

    Per design doc section 6: a ``[BATCH]`` parent surfaces as one unit
    with its actionable children joined. The surfaced Task carries the
    same id, text, status, tags, deps, and source position as the
    parent — only ``children`` is replaced with the result of
    :func:`_get_batch_children` so callers receive the batchable
    children directly without recomputing them.
    """
    return dataclasses.replace(parent, children=_get_batch_children(parent))


def _walk_actionable(
    tasks: tuple[Task, ...],
    *,
    is_subtask: bool,
    done_ids: set[str],
) -> Iterator[Task]:
    """Yield actionable tasks from ``tasks`` in depth-first document order.

    Mirrors mcloop's ``_search_tasks`` (``checklist.py`` line 356) with
    three additions: ``@deps`` satisfaction (design doc section 6.2),
    ``[BATCH]`` parent surfacing (design doc section 6 "A `[BATCH]`
    parent surfaces as one unit"), and the change from "return one
    task" to "yield each in order" so callers can satisfy ``limit > 1``.

    Per design doc section 6, a task is actionable iff:

    1. Status is TODO.
    2. Every ``@deps`` reference is DONE (per ``done_ids``).
    3. No FAILED ancestor — enforced by the walk skipping FAILED tasks
       and never descending into them; a FAILED root-level task is
       skipped (``continue``) and a FAILED subtask returns (``return``),
       blocking later siblings under the same parent (item 3 of the
       priority/scoping list, "Failed subtasks block later siblings").
    4. Leaf-before-parent: a task with children recurses first; the
       parent itself is yielded only when no descendant is actionable.

    ``is_subtask`` distinguishes root-level lists (phase tasks, bug
    tasks, subsection root tasks) — where FAILED is skipped — from
    nested child lists, where FAILED stops the walk and blocks later
    siblings.
    """
    for task in tasks:
        if task.status == TaskStatus.FAILED:
            if is_subtask:
                return
            continue
        if task.status == TaskStatus.DONE:
            continue
        if not _deps_satisfied(task, done_ids):
            continue

        if not task.children:
            yield task
            continue

        sub_iter = _walk_actionable(task.children, is_subtask=True, done_ids=done_ids)
        first_child = next(sub_iter, None)
        if first_child is not None:
            if "BATCH" in task.flag_tags:
                batch_children = _get_batch_children(task)
                if batch_children:
                    yield dataclasses.replace(task, children=batch_children)
                    # The BATCH parent is the unit; drain the remaining
                    # actionable children so the iterator state is clean,
                    # but do not yield them individually (the surfaced
                    # parent carries the batch).
                    for _ in sub_iter:
                        pass
                    continue
                yield first_child
                yield from sub_iter
            else:
                yield first_child
                yield from sub_iter
            continue

        # No actionable descendant. If any child is FAILED, the parent
        # cannot complete (parent state is derived from children, design
        # doc section 6 priority list item 4) and a FAILED child at the
        # subtask level blocks later siblings the same way a direct
        # FAILED sibling would.
        if any(c.status == TaskStatus.FAILED for c in task.children):
            if is_subtask:
                return
            continue
        yield task


def next_tasks(plan: Plan, *, limit: int = 1) -> list[Task]:
    """Return the next actionable tasks in ``plan``.

    Per design doc section 6. Priority and scoping:

    1. Tasks under ``## Bugs`` have absolute priority over phase tasks
       (design doc section 6 priority list item 1, and mcloop's
       ``find_next`` which probes ``_search_in_stage(tasks, "Bugs")``
       before falling through). When any bug task is actionable, the
       phase walk is not performed — even if ``limit`` is not yet
       reached. This keeps bugs from being mixed into the same result
       list as phase work; callers that finish bugs and then want
       phase work call ``next_tasks`` again.
    2. Within phase tasks, only tasks in the **first incomplete phase**
       (document order) are searchable. Later phases are invisible
       until the current phase is fully DONE (priority list item 2).
       A phase is "complete" when every task (root tasks plus every
       subsection task, recursively) has ``status == DONE``; FAILED
       tasks do not count as complete (mcloop ``_stage_complete``
       semantics: a stage with failed tasks is stuck, not done).

    Actionability and BATCH surfacing live in :func:`_walk_actionable`.

    ``limit`` caps the output length. ``limit <= 0`` returns ``[]``
    without searching the plan. The default of ``1`` matches the
    typical caller, which acts on the first actionable task and then
    re-reads PLAN.md between iterations.
    """
    if limit < 1:
        return []

    results: list[Task] = []
    done_ids = _build_done_ids(plan)

    if plan.bugs is not None:
        for task in _walk_actionable(
            plan.bugs.tasks, is_subtask=False, done_ids=done_ids
        ):
            results.append(task)
            if len(results) >= limit:
                return results
        if results:
            return results

    for phase in plan.phases:
        if _phase_complete(phase):
            continue
        for root_list in (phase.tasks, *(sub.tasks for sub in phase.subsections)):
            for task in _walk_actionable(
                root_list, is_subtask=False, done_ids=done_ids
            ):
                results.append(task)
                if len(results) >= limit:
                    return results
        return results
    return results


def _flip_in_tree(
    tasks: tuple[Task, ...],
    task_id: str,
    new_status: TaskStatus,
    *,
    cascade: bool,
) -> tuple[tuple[Task, ...], list[Task], bool]:
    """Apply ``new_status`` to ``task_id`` inside ``tasks``; return new tree.

    Returns ``(new_tasks, newly_completed_ancestors, found)``.

    When ``cascade`` is True and the change flips the target to DONE, any
    ancestor whose children become all-DONE is also flipped to DONE and
    appended to ``newly_completed_ancestors`` in innermost-first order
    (the leaf's parent precedes the grandparent). When ``cascade`` is
    False, ancestor statuses are left untouched (used by fail_task and
    reset_task: failing or un-failing a child must not auto-complete
    ancestors, per design doc section 5).

    An ancestor whose ``status`` is already DONE is not re-appended even
    if its children are all DONE after the flip. The cascade fires on a
    transition from not-DONE to DONE; idempotently completing a task
    whose parents were already DONE produces no additional derived
    Settlements.
    """
    new_list: list[Task] = []
    found = False
    ancestors: list[Task] = []

    for task in tasks:
        if found:
            new_list.append(task)
            continue
        if task.task_id is not None and task.task_id == task_id:
            new_list.append(dataclasses.replace(task, status=new_status))
            found = True
            continue
        if task.children:
            new_children, child_ancestors, child_found = _flip_in_tree(
                task.children, task_id, new_status, cascade=cascade
            )
            if child_found:
                found = True
                ancestors.extend(child_ancestors)
                if (
                    cascade
                    and new_status == TaskStatus.DONE
                    and task.status != TaskStatus.DONE
                    and _all_tasks_done(new_children)
                ):
                    new_parent = dataclasses.replace(
                        task, children=new_children, status=TaskStatus.DONE
                    )
                    new_list.append(new_parent)
                    ancestors.append(new_parent)
                else:
                    new_list.append(dataclasses.replace(task, children=new_children))
                continue
        new_list.append(task)

    return tuple(new_list), ancestors, found


def _apply_to_phase(
    phase: Phase,
    task_id: str,
    new_status: TaskStatus,
    *,
    cascade: bool,
) -> tuple[Phase, list[Task], bool]:
    """Walk ``phase``'s root tasks then subsection tasks. Returns rebuilt phase."""
    new_tasks, ancestors, found = _flip_in_tree(
        phase.tasks, task_id, new_status, cascade=cascade
    )
    if found:
        return dataclasses.replace(phase, tasks=new_tasks), ancestors, True

    new_subs: list[Subsection] = []
    for sub in phase.subsections:
        if found:
            new_subs.append(sub)
            continue
        sub_tasks, sub_ancestors, sub_found = _flip_in_tree(
            sub.tasks, task_id, new_status, cascade=cascade
        )
        if sub_found:
            found = True
            ancestors.extend(sub_ancestors)
            new_subs.append(dataclasses.replace(sub, tasks=sub_tasks))
        else:
            new_subs.append(sub)

    if found:
        return (
            dataclasses.replace(phase, subsections=tuple(new_subs)),
            ancestors,
            True,
        )
    return phase, [], False


def _apply_status_to_plan(
    plan: Plan,
    task_id: str,
    new_status: TaskStatus,
    *,
    cascade: bool,
) -> tuple[Plan, list[Task]]:
    """Return ``(new_plan, newly_completed_ancestors)`` after the flip.

    Walks phases (each phase's root tasks then its subsections) before
    falling through to the Bugs section. The first match wins; subsequent
    sections are returned untouched. Raises :class:`ValueError` if no
    task in the plan has ``task_id``.
    """
    new_phases: list[Phase] = []
    found = False
    ancestors: list[Task] = []

    for phase in plan.phases:
        if found:
            new_phases.append(phase)
            continue
        new_phase, phase_ancestors, phase_found = _apply_to_phase(
            phase, task_id, new_status, cascade=cascade
        )
        if phase_found:
            found = True
            ancestors.extend(phase_ancestors)
            new_phases.append(new_phase)
        else:
            new_phases.append(phase)

    new_bugs = plan.bugs
    if not found and plan.bugs is not None:
        bug_tasks, bug_ancestors, bug_found = _flip_in_tree(
            plan.bugs.tasks, task_id, new_status, cascade=cascade
        )
        if bug_found:
            found = True
            ancestors.extend(bug_ancestors)
            new_bugs = dataclasses.replace(plan.bugs, tasks=bug_tasks)

    if not found:
        raise ValueError(f"task {task_id!r} not found in plan")

    return (
        dataclasses.replace(plan, phases=tuple(new_phases), bugs=new_bugs),
        ancestors,
    )


def _clear_failed_in_tasks(tasks: tuple[Task, ...]) -> tuple[tuple[Task, ...], bool]:
    """Return ``tasks`` with every FAILED task reset to TODO."""
    new_tasks: list[Task] = []
    changed = False

    for task in tasks:
        new_children = task.children
        if task.children:
            new_children, children_changed = _clear_failed_in_tasks(task.children)
            changed = changed or children_changed

        new_status = (
            TaskStatus.TODO if task.status == TaskStatus.FAILED else task.status
        )
        if new_status != task.status or new_children != task.children:
            new_tasks.append(
                dataclasses.replace(task, status=new_status, children=new_children)
            )
            changed = True
        else:
            new_tasks.append(task)

    return tuple(new_tasks), changed


def _direct_completion_kind(task: Task) -> str:
    """Pick the Settlement kind for a directly-completed task.

    Per design doc section 5 kind policy:

    - AUTO action tasks (``task.action_tag is not None``) and USER tasks
      (``"USER" in task.flag_tags``) settle as ``work_observed``: the
      task completed but did not produce a commit, so callers record the
      work observation without expecting a new commit on disk.
    - Every other task (the common case) settles as ``commit_landed``:
      the task produced a commit and the ledger emits the commit event.
    """
    if task.action_tag is not None or "USER" in task.flag_tags:
        return "work_observed"
    return "commit_landed"


def _settlement_phase_id(plan: Plan, task_id: str | None) -> str | None:
    """Return the resolved phase_id for ``task_id`` in ``plan``, or None.

    Thin wrapper around :func:`resolve_task_context` so Settlement
    construction does not repeat the ``task_id is None`` guard at every
    call site. Bug tasks resolve with ``phase_id=None``; unresolved
    references also return ``None``.
    """
    if task_id is None:
        return None
    return resolve_task_context(plan, task_id).phase_id


def complete_task(
    plan: Plan, task_id: str, outcome: Outcome | None = None
) -> tuple[Plan, tuple[Settlement, ...]]:
    """Flip ``task_id`` to DONE and return the new plan and its settlements.

    The first Settlement in the returned tuple is the **direct** result
    for ``task_id`` (kind picked by :func:`_direct_completion_kind`,
    always ``ledger_event_required=True``). When the flip causes one or
    more ancestor tasks to become complete because all of their
    children are now DONE, each newly-completed ancestor is appended as
    a **derived** Settlement with ``kind="none"`` and
    ``ledger_event_required=False``. Per design doc section 5 the
    derived sequence is innermost-outward: the leaf's immediate parent
    first, the grandparent next, and so on.

    Idempotency: completing a task whose ancestors were already DONE
    produces no derived Settlements (the cascade fires only on a
    transition into DONE).

    ``outcome`` is accepted for API symmetry with :func:`fail_task` and
    is currently unused by ``complete_task``. The signature is pinned
    by the design doc; future consumers can attach commit metadata
    without an API break.
    """
    original = _find_task_by_id(plan, task_id)
    if original is None:
        raise ValueError(f"task {task_id!r} not found in plan")
    _ = outcome  # Accepted for API symmetry; not currently consumed.

    new_plan, ancestors = _apply_status_to_plan(
        plan, task_id, TaskStatus.DONE, cascade=True
    )

    direct = Settlement(
        kind=_direct_completion_kind(original),  # type: ignore[arg-type]
        task_id=task_id,
        phase_id=_settlement_phase_id(new_plan, task_id),
        summary=original.text,
        failure_kind=None,
        ledger_event_required=True,
    )
    derived = tuple(
        Settlement(
            kind="none",
            task_id=ancestor.task_id,
            phase_id=_settlement_phase_id(new_plan, ancestor.task_id),
            summary=ancestor.text,
            failure_kind=None,
            ledger_event_required=False,
        )
        for ancestor in ancestors
    )
    return new_plan, (direct, *derived)


def fail_task(
    plan: Plan,
    task_id: str,
    reason: str,
    outcome: Outcome | None = None,
) -> tuple[Plan, tuple[Settlement, ...]]:
    """Flip ``task_id`` to FAILED and return a single ``test_failed`` Settlement.

    ``reason`` becomes the Settlement's ``summary`` (a human-readable
    description of what failed). ``failure_kind`` comes from
    ``outcome.failure_kind`` when an :class:`Outcome` is supplied with a
    non-empty value; otherwise defaults to ``"max_retries_exceeded"``
    per the task contract.

    Failing a task does not auto-complete ancestors — a failure inside
    a parent task leaves the parent unchecked (and stuck), per design
    doc section 5 and mcloop's behavior. The returned tuple therefore
    contains exactly one Settlement.
    """
    original = _find_task_by_id(plan, task_id)
    if original is None:
        raise ValueError(f"task {task_id!r} not found in plan")

    new_plan, _ = _apply_status_to_plan(plan, task_id, TaskStatus.FAILED, cascade=False)

    failure_kind = outcome.failure_kind if outcome is not None else None
    if not failure_kind:
        failure_kind = "max_retries_exceeded"

    settlement = Settlement(
        kind="test_failed",
        task_id=task_id,
        phase_id=_settlement_phase_id(new_plan, task_id),
        summary=reason,
        failure_kind=failure_kind,
        ledger_event_required=True,
    )
    return new_plan, (settlement,)


def reset_task(plan: Plan, task_id: str) -> tuple[Plan, tuple[Settlement, ...]]:
    """Flip ``task_id`` back to TODO and return a ``none``-kind Settlement.

    Mirrors mcloop's ``clear_failed_markers``: a FAILED checkbox is
    rewritten as TODO so the task is re-tried. Per design doc section
    5, reset is an operator decision about retry, not new evidence
    about implementation — the Settlement therefore has ``kind="none"``
    and ``ledger_event_required=False``. The operation is idempotent on
    non-FAILED tasks (TODO stays TODO, DONE is rewritten to TODO if the
    caller asks, with no error) so callers can apply it blindly to
    "clear any failed marker on this task".
    """
    original = _find_task_by_id(plan, task_id)
    if original is None:
        raise ValueError(f"task {task_id!r} not found in plan")

    new_plan, _ = _apply_status_to_plan(plan, task_id, TaskStatus.TODO, cascade=False)

    settlement = Settlement(
        kind="none",
        task_id=task_id,
        phase_id=_settlement_phase_id(new_plan, task_id),
        summary=original.text,
        failure_kind=None,
        ledger_event_required=False,
    )
    return new_plan, (settlement,)


def clear_failed(plan: Plan) -> Plan:
    """Reset every FAILED task in ``plan`` to TODO and return a new plan.

    Mirrors mcloop's bulk ``clear_failed_markers`` retry behavior:
    every failed checkbox becomes unchecked, DONE and TODO tasks are
    untouched, no parent cascade is applied, and no ledger Settlement is
    produced. The operation does not require task ids, so it works on
    compat-mode plans as well as migrated plans.
    """
    new_phases: list[Phase] = []
    for phase in plan.phases:
        phase_tasks, phase_tasks_changed = _clear_failed_in_tasks(phase.tasks)
        new_subsections: list[Subsection] = []
        subsections_changed = False
        for subsection in phase.subsections:
            sub_tasks, sub_changed = _clear_failed_in_tasks(subsection.tasks)
            subsections_changed = subsections_changed or sub_changed
            if sub_changed:
                new_subsections.append(dataclasses.replace(subsection, tasks=sub_tasks))
            else:
                new_subsections.append(subsection)

        if phase_tasks_changed or subsections_changed:
            new_phases.append(
                dataclasses.replace(
                    phase,
                    tasks=phase_tasks,
                    subsections=tuple(new_subsections),
                )
            )
        else:
            new_phases.append(phase)

    new_bugs = plan.bugs
    if plan.bugs is not None:
        bug_tasks, bugs_changed = _clear_failed_in_tasks(plan.bugs.tasks)
        if bugs_changed:
            new_bugs = dataclasses.replace(plan.bugs, tasks=bug_tasks)

    return dataclasses.replace(plan, phases=tuple(new_phases), bugs=new_bugs)


def _next_task_id(plan: Plan) -> str:
    """Return the next sequential ``T-NNNNNN`` id not yet used in ``plan``.

    Scans every task in the plan, takes the maximum numeric suffix on
    ids that match ``T-`` + digits, and returns ``T-`` formatted to six
    digits at ``max + 1``. Tasks without an id, or with non-conforming
    ids, are ignored. The scan covers phase tasks (with subsection
    descent) and bug tasks via :func:`_iter_plan_tasks`, so the
    returned id is globally unique within ``plan`` per design doc
    section 11 question 1 (global default).
    """
    max_num = 0
    for task in _iter_plan_tasks(plan):
        if task.task_id is None or not task.task_id.startswith("T-"):
            continue
        suffix = task.task_id[2:]
        if not suffix.isdigit():
            continue
        num = int(suffix)
        if num > max_num:
            max_num = num
    return f"T-{max_num + 1:06d}"


def _try_add_under_parent(
    tasks: tuple[Task, ...], parent_id: str, new_task: Task
) -> tuple[tuple[Task, ...], bool]:
    """Append ``new_task`` to the children of the task whose id == parent_id.

    Returns ``(new_tasks, found)`` so callers can fall through to a
    sibling list (phase root tasks → subsection tasks) when the parent
    is not in this list. Walks recursively so a nested parent matches.
    """
    new_list: list[Task] = []
    found = False
    for task in tasks:
        if found:
            new_list.append(task)
            continue
        if task.task_id is not None and task.task_id == parent_id:
            new_list.append(
                dataclasses.replace(task, children=(*task.children, new_task))
            )
            found = True
            continue
        if task.children:
            new_children, child_found = _try_add_under_parent(
                task.children, parent_id, new_task
            )
            if child_found:
                new_list.append(dataclasses.replace(task, children=new_children))
                found = True
                continue
        new_list.append(task)
    return tuple(new_list), found


def add_task(
    plan: Plan,
    phase_id: str,
    text: str,
    *,
    deps: tuple[str, ...] = (),
    parent_id: str | None = None,
) -> Plan:
    """Append a new TODO task to ``phase_id`` and return the new plan.

    The new task gets the next sequential globally-unique ``T-NNNNNN``
    id (:func:`_next_task_id`). When ``parent_id`` is supplied the task
    is nested under that task's children, searching first the phase's
    root tasks and then each subsection's tasks. When ``parent_id`` is
    ``None`` the task is appended to the phase's root task list.

    Raises :class:`ValueError` when ``phase_id`` does not match any
    phase, or when ``parent_id`` is supplied but does not match any
    task inside the named phase.
    """
    new_id = _next_task_id(plan)
    new_task = Task(
        task_id=new_id,
        text=text,
        status=TaskStatus.TODO,
        flag_tags=(),
        action_tag=None,
        annotations=(),
        deps=tuple(deps),
        children=(),
        ruled_out=(),
        indent_level=0,
        line_number=0,
    )

    new_phases: list[Phase] = []
    phase_found = False
    for phase in plan.phases:
        if phase_found or phase.phase_id != phase_id:
            new_phases.append(phase)
            continue
        phase_found = True
        if parent_id is None:
            new_phases.append(
                dataclasses.replace(phase, tasks=(*phase.tasks, new_task))
            )
            continue
        updated_root, added = _try_add_under_parent(phase.tasks, parent_id, new_task)
        if added:
            new_phases.append(dataclasses.replace(phase, tasks=updated_root))
            continue
        new_subs: list[Subsection] = []
        sub_added = False
        for sub in phase.subsections:
            if sub_added:
                new_subs.append(sub)
                continue
            sub_tasks, this_added = _try_add_under_parent(
                sub.tasks, parent_id, new_task
            )
            if this_added:
                sub_added = True
                new_subs.append(dataclasses.replace(sub, tasks=sub_tasks))
            else:
                new_subs.append(sub)
        if not sub_added:
            raise ValueError(
                f"parent task {parent_id!r} not found in phase {phase_id!r}"
            )
        new_phases.append(dataclasses.replace(phase, subsections=tuple(new_subs)))

    if not phase_found:
        raise ValueError(f"phase {phase_id!r} not found in plan")

    return dataclasses.replace(plan, phases=tuple(new_phases))


def add_phase_task(
    plan: Plan,
    phase_id: str,
    task: Task,
    *,
    parent_id: str | None = None,
    subsection_title: str | None = None,
) -> tuple[Plan, str]:
    """Append a Stage-10 field-stable task into ``phase_id``.

    Per v4 Contract 6. Returns ``(new_plan, assigned_id)`` where
    ``assigned_id`` is the ``T-NNNNNN`` carried by the appended task
    (the caller-supplied id when ``task.task_id`` is non-None, otherwise
    the next globally-unique sequential id assigned by
    :func:`_next_task_id`).

    Placement is selected by the keyword arguments:

    * Both ``parent_id`` and ``subsection_title`` ``None`` (default) —
      the task is appended to ``phase.tasks`` at the root of the named
      phase.
    * ``parent_id`` set — the task is nested under the task whose
      ``task_id`` equals ``parent_id``. Searches the phase's root tasks
      first, then each subsection's tasks (recursing through children),
      matching the convention :func:`add_task` already uses; the first
      match wins. The look-up is scoped to the named phase so a stray
      id elsewhere in the plan does not silently capture the insert.
    * ``subsection_title`` set — the task is appended at the root of
      the subsection whose ``title`` equals ``subsection_title``
      within the named phase.

    ``parent_id`` and ``subsection_title`` are mutually exclusive
    (Contract 6 enumerates the three placements as alternatives, not
    combinations); passing both raises :class:`PlanValidationError`.

    Validation is layered to surface the same diagnostics callers see
    elsewhere in the library. First the supplied ``task`` is run
    through the Stage 10 field-stability harness
    (:func:`_assert_task_field_stability`) so hand-constructed tasks
    cannot bypass the grammar contract that :func:`make_task` enforces.
    Then the placement keywords are resolved, the new task is spliced
    in with its id (assigned or honored), and the resulting plan is
    passed through :func:`validate_plan` with ``constructed=True``,
    which catches duplicate task ids and unknown ``@deps`` references.
    :class:`PlanValidationError` is the only exception this function
    raises; callers do not need to catch :class:`ValueError`.
    """
    _assert_task_field_stability(task)

    if parent_id is not None and subsection_title is not None:
        raise PlanValidationError(
            ["parent_id and subsection_title are mutually exclusive"]
        )

    phase_index = -1
    for index, phase in enumerate(plan.phases):
        if phase.phase_id == phase_id:
            phase_index = index
            break
    if phase_index == -1:
        raise PlanValidationError([f"phase {phase_id!r} not found in plan"])

    target_phase = plan.phases[phase_index]

    assigned_id = task.task_id if task.task_id is not None else _next_task_id(plan)
    appended = dataclasses.replace(task, task_id=assigned_id)

    if parent_id is not None:
        updated_root, root_added = _try_add_under_parent(
            target_phase.tasks, parent_id, appended
        )
        if root_added:
            new_phase = dataclasses.replace(target_phase, tasks=updated_root)
        else:
            new_subs: list[Subsection] = []
            sub_added = False
            for sub in target_phase.subsections:
                if sub_added:
                    new_subs.append(sub)
                    continue
                sub_tasks, this_added = _try_add_under_parent(
                    sub.tasks, parent_id, appended
                )
                if this_added:
                    sub_added = True
                    new_subs.append(dataclasses.replace(sub, tasks=sub_tasks))
                else:
                    new_subs.append(sub)
            if not sub_added:
                raise PlanValidationError(
                    [f"parent task {parent_id!r} not found in phase {phase_id!r}"]
                )
            new_phase = dataclasses.replace(target_phase, subsections=tuple(new_subs))
    elif subsection_title is not None:
        new_subs = []
        sub_found = False
        for sub in target_phase.subsections:
            if not sub_found and sub.title == subsection_title:
                sub_found = True
                new_subs.append(dataclasses.replace(sub, tasks=(*sub.tasks, appended)))
            else:
                new_subs.append(sub)
        if not sub_found:
            raise PlanValidationError(
                [f"subsection {subsection_title!r} not found in phase {phase_id!r}"]
            )
        new_phase = dataclasses.replace(target_phase, subsections=tuple(new_subs))
    else:
        new_phase = dataclasses.replace(
            target_phase, tasks=(*target_phase.tasks, appended)
        )

    new_phases = list(plan.phases)
    new_phases[phase_index] = new_phase
    new_plan = dataclasses.replace(plan, phases=tuple(new_phases))

    validate_plan(new_plan, constructed=True)

    return new_plan, assigned_id


def _normalize_bug_text(text: str) -> str:
    """Return ``text`` normalized for bug-dedup comparison.

    Strips leading/trailing whitespace and collapses every run of
    interior whitespace into a single space. The normalization is
    deliberately conservative: it absorbs incidental whitespace
    differences (a model re-emit with an extra space; copy-paste that
    introduced a leading newline) without merging tasks whose text
    differs in any non-whitespace character. ``"a b"``, ``"a  b"``,
    and ``"\\ta b\\n"`` all normalize to the same key; ``"a B"`` does
    not.
    """
    return " ".join(text.split())


def _bug_dedup_keys(task: Task, *, explicit: tuple[str, ...] = ()) -> set[str]:
    """Return the dedup-key set for ``task`` per v4 Contract 2.

    The set is the union of three sources, in the order the spec
    lists them: caller-supplied ``explicit`` keys, the value of every
    ``fix`` annotation on the task, then the task's text after
    :func:`_normalize_bug_text`. The ``fix`` annotation is the one
    Duplo's investigator emits so a regenerated bug task whose text
    drifted slightly still collapses against the same underlying
    issue (saver.py:1291 in duplo).
    """
    keys: set[str] = set(explicit)
    for key, value in task.annotations:
        if key == "fix":
            keys.add(value)
    keys.add(_normalize_bug_text(task.text))
    return keys


def add_bug_task(
    plan: Plan,
    task: Task,
    *,
    dedup_keys: tuple[str, ...] = (),
) -> tuple[Plan, str]:
    """Append ``task`` to the Bugs section with reopen-in-place semantics.

    Per v4 Contract 2. Returns ``(new_plan, outcome)`` where ``outcome``
    is one of ``"appended"``, ``"reopened"``, or ``"unchanged"``.

    ``task`` must already satisfy Stage 10 task field-stability
    (typically constructed via :func:`make_task`); the harness runs
    here too so a hand-built :class:`Task` cannot bypass the grammar
    contract. Failure raises :class:`PlanValidationError` naming the
    field that did not round-trip. Per the contract,
    :class:`PlanValidationError` is the only exception this function
    raises.

    Behavior:

    * **Bugs section creation.** When ``plan.bugs is None`` and the
      task is appended (no match found), a fresh :class:`BugsSection`
      with this one task is attached to the plan. An existing empty
      :class:`BugsSection` (parsed but with no tasks) is preserved
      and the task is appended to it.
    * **Root bug tasks only.** The supplied task is inserted at the
      Bugs root level. Any ``children`` on the task are preserved
      unchanged, but the task itself is never nested under an existing
      bug.
    * **Dedup match.** The incoming task's dedup-key set
      (:func:`_bug_dedup_keys` with the caller's ``dedup_keys``) is
      compared against each existing root bug task's dedup-key set
      (no explicit keys; just ``fix`` annotations and normalized
      text). The first existing task whose key set intersects the
      incoming key set is the match; subsequent matches are ignored
      (the "earliest match" rule).
    * **Outcome by match status:**

      - Match is TODO → return ``"unchanged"``. The plan is returned
        as-is; no field on any task changes. The incoming task is
        discarded — the bug is already open.
      - Match is DONE or FAILED → reopen the earliest match in place:
        flip ``status`` to TODO. ``task_id``, ``children``,
        ``annotations``, ``deps``, ``ruled_out``, and the task's
        position in ``plan.bugs.tasks`` are preserved. Return
        ``"reopened"``.
      - No match → append. ``status`` is forced to TODO regardless of
        the incoming task's status (an incoming DONE/FAILED was
        meaningless for a newly-tracked bug). When ``task.task_id is
        None`` the next globally-unique ``T-NNNNNN`` id is assigned
        via :func:`_next_task_id`; a caller-supplied id is honored
        verbatim (collision checking belongs to
        :func:`validate_plan`). Return ``"appended"``.
    """
    _assert_task_field_stability(task)

    incoming_keys = _bug_dedup_keys(task, explicit=dedup_keys)
    existing_bugs: tuple[Task, ...] = plan.bugs.tasks if plan.bugs is not None else ()

    for index, existing in enumerate(existing_bugs):
        if not (incoming_keys & _bug_dedup_keys(existing)):
            continue
        if existing.status == TaskStatus.TODO:
            return plan, "unchanged"
        reopened = dataclasses.replace(existing, status=TaskStatus.TODO)
        new_tasks = (
            *existing_bugs[:index],
            reopened,
            *existing_bugs[index + 1 :],
        )
        # ``existing_bugs`` is non-empty here, so ``plan.bugs`` is set;
        # narrow for the type checker.
        assert plan.bugs is not None
        new_bugs = dataclasses.replace(plan.bugs, tasks=new_tasks)
        return dataclasses.replace(plan, bugs=new_bugs), "reopened"

    assigned_id = task.task_id if task.task_id is not None else _next_task_id(plan)
    appended = dataclasses.replace(task, task_id=assigned_id, status=TaskStatus.TODO)
    if plan.bugs is None:
        new_bugs = BugsSection(tasks=(appended,), line_number=0)
    else:
        new_bugs = dataclasses.replace(plan.bugs, tasks=(*plan.bugs.tasks, appended))
    return dataclasses.replace(plan, bugs=new_bugs), "appended"


class _LedgerEvent(Protocol):
    """Structural type for events accepted by :func:`check_consistency`.

    Mirrors the shape of :class:`bob_tools.ledger.events.Event` (a frozen
    dataclass with ``event_id``, ``type``, and ``payload`` attributes)
    without importing it: per design doc section 3.1 the two libraries
    are peers and planfile must not depend on ledger. Any object whose
    attributes match this protocol — including the ledger's ``Event``
    dataclass and a thin test fixture — satisfies the contract.

    Attributes are declared as ``@property`` (read-only) so the
    matching is covariant: ``payload`` typed as ``dict[str, Any]`` on a
    concrete implementation satisfies ``Mapping[str, Any]`` here.
    ``type`` is returned as ``Any`` so both the ledger's ``EventType``
    StrEnum and a plain ``str`` (``"test_failed"``, ``"commit_landed"``)
    are accepted; the consistency check compares with ``==`` which works
    transparently for both because ``EventType`` is a ``StrEnum``.
    """

    @property
    def event_id(self) -> str: ...

    @property
    def type(self) -> Any: ...

    @property
    def payload(self) -> Mapping[str, Any]: ...


# Event-type → checkbox state implied when this is the most recent
# task-attributed lifecycle event for a task. Per design doc section 5
# kind policy: test_failed implies the task is currently FAILED;
# commit_landed and work_observed both imply the task is currently DONE
# (commit_landed is the commit-producing path, work_observed the
# AUTO/USER verification path). Other event types (phase lifecycle,
# findings, threshold crossings, etc.) do not carry a task-level
# state claim and are excluded.
_EVENT_TYPE_TO_EXPECTED_STATUS: dict[str, TaskStatus] = {
    "test_failed": TaskStatus.FAILED,
    "commit_landed": TaskStatus.DONE,
    "work_observed": TaskStatus.DONE,
}


def _event_task_id(event_type: str, payload: Mapping[str, Any]) -> str | None:
    """Return the task_id referenced by ``event``, or ``None`` when absent.

    Field lookup per event type:

    - ``test_failed`` → ``payload["test_id"]``. Per design doc section
      7.2: "Task identity flows through ``test_failed.test_id`` (which
      is the task label, per ``emit_task_lifecycle_events``)." After
      McLoop's settle hook moves to :func:`resolve_task_context`, the
      same field carries a stable ``T-NNNNNN`` id.
    - ``commit_landed`` / ``work_observed`` → ``payload
      ["attributed_task_id"]``. Today's ledger schema (SCHEMA.md §
      ``commit_landed``) attributes commits only to a phase
      (``attributed_phase_id``); the design doc section 7.2 / section
      10 flags ``attributed_task_id`` as a future schema bump. Reading
      the field here is forward-compatible: payloads that omit it
      (the current case) return ``None`` and the event contributes no
      task-level claim.

    Returns ``None`` for any other event type — phase lifecycle events,
    findings, invariants, assumptions, decisions, and the reserved
    ``threshold_crossed`` / ``plan_reauthored`` types do not carry a
    per-task state claim.
    """
    if event_type == "test_failed":
        value = payload.get("test_id")
    elif event_type in ("commit_landed", "work_observed"):
        value = payload.get("attributed_task_id")
    else:
        return None
    return str(value) if value is not None else None


def check_consistency(plan: Plan, events: Iterable[_LedgerEvent]) -> None:
    """Compare checkbox state against the most recent lifecycle event per task.

    Per design doc section 5: PLAN.md is the writable surface, the
    ledger is the append-only witness, and the two are coupled at the
    settle call site rather than enforced as a bidirectional invariant.
    ``check_consistency`` is the after-the-fact reconciliation hook —
    it raises :class:`PlanInconsistencyError` when a task's current
    checkbox state contradicts the most recent task-attributed
    lifecycle event for that task, and stays silent on every other
    situation.

    Per-task attribution. Events without a resolvable task_id
    (:func:`_event_task_id` returns ``None``) are ignored: today
    ``commit_landed`` and ``work_observed`` attribute only to a phase
    (SCHEMA.md), so the function can only check tasks for which the
    ledger carries a task-level claim. Once ``attributed_task_id`` lands
    on those events (design doc section 10, future schema bump), the
    same check applies to commit/work events without further change.

    "Most recent" is decided by ``event.event_id``. Ledger event_ids are
    UUIDv7 (SCHEMA.md envelope) whose 48-bit time prefix gives
    lexicographic order that matches emission order across writers;
    breaking ties by the same field is acceptable because the projector
    uses the same key for replay ordering. ``ts`` is explicitly not
    used — SCHEMA.md forbids relying on ``ts`` for ordering.

    What gets flagged (contradictions):

    - Checkbox is ``DONE`` but the most recent event is
      ``test_failed`` — the ledger says the task failed and no later
      success event landed.
    - Checkbox is ``FAILED`` but the most recent event is
      ``commit_landed`` / ``work_observed`` — the ledger says the task
      succeeded and no later failure event landed.
    - Checkbox is ``TODO`` but the most recent event is
      ``commit_landed`` / ``work_observed`` — the ledger says the task
      succeeded but PLAN.md no longer reflects it (a checkoff that
      regressed back to unchecked is a contradiction, not a reset).

    What does NOT get flagged (intentional gaps, per design doc § 5):

    - Checkbox is ``TODO`` and the most recent event is
      ``test_failed``: per design doc section 5, "Resetting ``[!]`` to
      ``[ ]`` via retry → no ledger event; it is an operator decision
      to retry existing work, not evidence about the implementation."
      :func:`reset_task` emits a ``"none"``-kind Settlement with
      ``ledger_event_required=False`` for exactly this reason, so the
      checker must accept TODO after a test_failed as the expected
      shape of a reset.
    - Derived parent completion: a parent that auto-checks because all
      of its children are DONE does not produce a ledger event
      (:func:`complete_task` returns a ``"none"``-kind Settlement with
      ``ledger_event_required=False``). The parent therefore has no
      task-attributed event of its own; the checker sees no event for
      the parent and stays silent.
    - Tasks with no task-attributed events at all: the absence of
      evidence is not a contradiction. Compat-mode tasks (no stable
      id) fall here too — they cannot be matched by ``test_id`` /
      ``attributed_task_id`` so the checker has nothing to compare
      against.

    Every contradiction is collected into one
    :class:`PlanInconsistencyError`; the function does not short-
    circuit on the first failure so a single run surfaces every fix.
    Messages are sorted by ``task_id`` for deterministic output.
    """
    latest_by_task: dict[str, tuple[str, str]] = {}
    for event in events:
        event_type = str(event.type)
        task_id = _event_task_id(event_type, event.payload)
        if task_id is None:
            continue
        if event_type not in _EVENT_TYPE_TO_EXPECTED_STATUS:
            continue
        prior = latest_by_task.get(task_id)
        if prior is None or event.event_id > prior[0]:
            latest_by_task[task_id] = (event.event_id, event_type)

    messages: list[str] = []
    for task in _iter_plan_tasks(plan):
        if task.task_id is None:
            continue
        latest = latest_by_task.get(task.task_id)
        if latest is None:
            continue
        _event_id, event_type = latest
        expected = _EVENT_TYPE_TO_EXPECTED_STATUS[event_type]
        if task.status == expected:
            continue
        if event_type == "test_failed" and task.status == TaskStatus.TODO:
            continue
        messages.append(
            f"task {task.task_id} checkbox is {task.status.value} but "
            f"most recent lifecycle event is {event_type}"
        )

    if messages:
        messages.sort()
        raise PlanInconsistencyError(messages)


def replace_phase(plan: Plan, phase_id: str, new_phase: Phase) -> Plan:
    """Substitute the phase whose ``phase_id`` == ``phase_id`` with ``new_phase``.

    Wholesale replacement: ``new_phase`` is inserted at the matched
    phase's position in ``plan.phases`` with no field-level merge.
    Used by Duplo on phase reauthor where the entire phase content has
    been regenerated; the caller is responsible for the new phase's
    contents (including its ``phase_id``, which may differ from the
    lookup key when the reauthor renamed the phase).

    Raises :class:`ValueError` when no phase matches ``phase_id``.
    Other phases are returned unchanged, including the Bugs section
    and the plan's preamble.
    """
    new_phases: list[Phase] = []
    found = False
    for phase in plan.phases:
        if not found and phase.phase_id == phase_id:
            new_phases.append(new_phase)
            found = True
        else:
            new_phases.append(phase)
    if not found:
        raise ValueError(f"phase {phase_id!r} not found in plan")
    return dataclasses.replace(plan, phases=tuple(new_phases))


def _iter_phase_task_tree_with_label(
    phase: Phase,
) -> Iterator[tuple[str, Task]]:
    """Yield ``(label, task)`` for every task in ``phase``, including children.

    Labels mirror :func:`_iter_plan_tasks_with_label` but are rooted at
    the phase (``tasks[i]...``, ``subsections[j].tasks[k]...``) so
    diagnostics from :func:`replace_phase_validated` locate the offending
    task within the supplied ``new_phase`` rather than within the plan
    (which has not yet been substituted at the point of the check).
    """
    for task_index, task in enumerate(phase.tasks):
        for path, node in _iter_task_tree_with_paths(task):
            yield f"tasks[{task_index}]{_child_path_suffix(path)}", node
    for sub_index, sub in enumerate(phase.subsections):
        for task_index, task in enumerate(sub.tasks):
            for path, node in _iter_task_tree_with_paths(task):
                yield (
                    f"subsections[{sub_index}].tasks[{task_index}]"
                    f"{_child_path_suffix(path)}",
                    node,
                )


def replace_phase_validated(
    plan: Plan,
    phase_id: str,
    new_phase: Phase,
    *,
    assign_missing_ids: bool = True,
    preserve_position: bool = True,
) -> Plan:
    """Substitute ``phase_id`` with ``new_phase`` and enforce constructed validity.

    Per v4 Contract 3. Replaces :func:`replace_phase` for callers that
    need the substituted plan to satisfy the construction-API invariants
    end-to-end; structural validity only, lineage policy stays in duplo.

    Behavior:

    * **Exactly one match.** ``phase_id`` must resolve to exactly one
      phase in ``plan.phases``. Zero matches (unknown phase) or two-plus
      matches (duplicate-id input) raise :class:`PlanValidationError`
      before any substitution is attempted; the function never silently
      picks one of several duplicates.
    * **Missing phase id.** When ``new_phase.phase_id is None`` and
      ``assign_missing_ids`` is ``True`` the substitute is given a fresh
      ``phase_NNN`` id one above the maximum existing suffix in ``plan``
      (mirrors :func:`migrate`'s convention) and
      ``phase_id_source="explicit_comment"`` so the renderer emits a
      ``<!-- phase_id: ... -->`` comment.
    * **Missing task ids.** When ``assign_missing_ids`` is ``True``,
      tasks in ``new_phase`` (root and subsection, recursing through
      ``children``) whose ``task_id`` is ``None`` receive sequential
      global-unique ``T-NNNNNN`` ids starting at one above the maximum
      existing suffix in ``plan`` (:func:`_assign_task_ids` with a
      shared counter); existing ids are preserved.
    * **assign_missing_ids=False.** When the flag is ``False`` and any
      phase or task id on ``new_phase`` is missing, raise
      :class:`PlanValidationError` listing every offender. The caller
      is asserting "I provided all the ids myself; reject if any are
      missing."
    * **Ordinal normalization.** When ``preserve_position`` is ``True``
      (default) ``new_phase.ordinal`` is overwritten with the replaced
      phase's ordinal so the substitution keeps document order. When
      ``False`` the supplied ordinal is honored, and
      :func:`validate_plan` rejects the result if the ordinals are no
      longer contiguous ``1..N``.
    * **Final validation.** The substituted plan is passed through
      :func:`validate_plan` with ``constructed=True``, which enforces
      the v4 Contract 4 invariants (no duplicate phase or task ids,
      ids well-formed, scalars/deps/tags valid, ordinals contiguous,
      and full per-task / non-task scalar field-stability). Any
      violation propagates as :class:`PlanValidationError`.

    Other phases, the Bugs section, and the plan preamble are returned
    unchanged. :class:`PlanValidationError` is the only exception this
    function raises; callers do not need to catch :class:`ValueError`.
    """
    matched = [
        (index, phase)
        for index, phase in enumerate(plan.phases)
        if phase.phase_id == phase_id
    ]
    if not matched:
        raise PlanValidationError([f"phase {phase_id!r} not found in plan"])
    if len(matched) > 1:
        positions = [index for index, _ in matched]
        raise PlanValidationError(
            [f"phase {phase_id!r} matches multiple phases at positions {positions}"]
        )
    match_index, matched_phase = matched[0]

    if not assign_missing_ids:
        missing: list[str] = []
        if new_phase.phase_id is None:
            missing.append("new_phase.phase_id is missing")
        for label, node in _iter_phase_task_tree_with_label(new_phase):
            if node.task_id is None:
                missing.append(f"new_phase.{label}.task_id is missing")
        if missing:
            raise PlanValidationError(missing)

    candidate = new_phase

    if candidate.phase_id is None:
        next_phase_num = _max_phase_id_number(plan) + 1
        candidate = dataclasses.replace(
            candidate,
            phase_id=f"phase_{next_phase_num:03d}",
            phase_id_source="explicit_comment",
        )

    if assign_missing_ids:
        # ``_next_task_id_number`` reads only the supplied plan; the
        # counter must also clear ids already present inside
        # ``new_phase`` so an auto-assigned id never collides with a
        # caller-supplied id (e.g. plan max is 7 but ``new_phase``
        # already carries T-000020 — assignment must continue from 21,
        # not 8). Compose a pre-substitute plan with the candidate in
        # the target slot so the helper sees every retained id.
        phases_for_max = list(plan.phases)
        phases_for_max[match_index] = candidate
        plan_for_max = dataclasses.replace(plan, phases=tuple(phases_for_max))
        counter = [_next_task_id_number(plan_for_max)]
        new_root_tasks = _assign_task_ids(candidate.tasks, counter)
        new_subsections = tuple(
            dataclasses.replace(sub, tasks=_assign_task_ids(sub.tasks, counter))
            for sub in candidate.subsections
        )
        candidate = dataclasses.replace(
            candidate, tasks=new_root_tasks, subsections=new_subsections
        )

    if preserve_position:
        candidate = dataclasses.replace(candidate, ordinal=matched_phase.ordinal)

    new_phases = list(plan.phases)
    new_phases[match_index] = candidate
    new_plan = dataclasses.replace(plan, phases=tuple(new_phases))

    validate_plan(new_plan, constructed=True)

    return new_plan


_PHASE_ID_RE = re.compile(r"^phase_(\d+)$")


def _max_phase_id_number(plan: Plan) -> int:
    """Return the maximum numeric suffix used in any ``phase_NNN`` id.

    Scans every phase's ``phase_id`` and returns the largest digit run
    after the ``phase_`` prefix; returns ``0`` when no phase has a
    conforming id. Used by :func:`migrate` to assign ids to phases
    whose ``phase_id_source`` is ``"none"`` without colliding with ids
    already in use.
    """
    max_num = 0
    for phase in plan.phases:
        if phase.phase_id is None:
            continue
        match = _PHASE_ID_RE.match(phase.phase_id)
        if match is None:
            continue
        num = int(match.group(1))
        if num > max_num:
            max_num = num
    return max_num


def _assign_task_ids(tasks: tuple[Task, ...], counter: list[int]) -> tuple[Task, ...]:
    """Return a copy of ``tasks`` with missing ``task_id`` fields assigned.

    ``counter`` is a single-element list holding the next id number to
    use; it is mutated in place so sibling subtrees share one running
    counter. Tasks that already have a ``task_id`` are returned with
    only their ``children`` re-walked (so a partially-migrated tree
    fills only the gaps).
    """
    new_tasks: list[Task] = []
    for task in tasks:
        new_children = _assign_task_ids(task.children, counter)
        if task.task_id is None:
            new_id = f"T-{counter[0]:06d}"
            counter[0] += 1
            new_tasks.append(
                dataclasses.replace(task, task_id=new_id, children=new_children)
            )
        else:
            new_tasks.append(dataclasses.replace(task, children=new_children))
    return tuple(new_tasks)


def migrate(plan: Plan) -> Plan:
    """Return ``plan`` with stable identifiers assigned to every task and phase.

    Per design doc section 3.2: identity migration is a one-shot
    transformation that fills in the structural information PLAN.md
    needs for deterministic addressing. ``canonicalize`` is the
    lossless formatter; ``migrate`` is the identity-mutating step.

    Rules:

    * Every existing ``T-NNNNNN`` is preserved unchanged. Tasks with no
      id receive sequential ids starting at ``max(existing) + 1`` (or
      ``T-000001`` when no task in the plan has a conforming id).
    * Every phase whose ``phase_id_source`` is ``"none"`` receives a
      synthesized ``phase_NNN`` id (sequential, starting at one greater
      than the maximum existing ``phase_NNN`` suffix) and has its
      ``phase_id_source`` set to ``"explicit_comment"`` so the
      renderer emits a ``<!-- phase_id: ... -->`` line.
    * Phases that already have an explicit id (any source other than
      ``"none"``) are left untouched.

    The operation is idempotent: ``migrate(migrate(plan))`` equals
    ``migrate(plan)``.
    """
    counter = [_next_task_id_number(plan)]
    new_phases: list[Phase] = []
    next_phase_num = _max_phase_id_number(plan) + 1
    for phase in plan.phases:
        new_root_tasks = _assign_task_ids(phase.tasks, counter)
        new_subsections = tuple(
            dataclasses.replace(sub, tasks=_assign_task_ids(sub.tasks, counter))
            for sub in phase.subsections
        )
        if phase.phase_id_source == "none":
            new_phase_id = f"phase_{next_phase_num:03d}"
            next_phase_num += 1
            new_phases.append(
                dataclasses.replace(
                    phase,
                    phase_id=new_phase_id,
                    phase_id_source="explicit_comment",
                    tasks=new_root_tasks,
                    subsections=new_subsections,
                )
            )
        else:
            new_phases.append(
                dataclasses.replace(
                    phase, tasks=new_root_tasks, subsections=new_subsections
                )
            )

    new_bugs = plan.bugs
    if new_bugs is not None:
        new_bugs = dataclasses.replace(
            new_bugs, tasks=_assign_task_ids(new_bugs.tasks, counter)
        )

    return dataclasses.replace(plan, phases=tuple(new_phases), bugs=new_bugs)


def _next_task_id_number(plan: Plan) -> int:
    """Return the integer suffix to use for the next assigned task id.

    Equivalent to :func:`_next_task_id` but returns the integer rather
    than the formatted ``T-NNNNNN`` string; used by :func:`migrate`,
    which mutates a counter as it walks the tree and needs to increment
    the raw number.
    """
    max_num = 0
    for task in _iter_plan_tasks(plan):
        if task.task_id is None or not task.task_id.startswith("T-"):
            continue
        suffix = task.task_id[2:]
        if not suffix.isdigit():
            continue
        num = int(suffix)
        if num > max_num:
            max_num = num
    return max_num + 1
