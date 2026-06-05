"""Attach declared acceptance annotations to authored plan tasks."""

from __future__ import annotations

import dataclasses
import re
import shlex
from dataclasses import dataclass

from bob_tools.planfile import Phase, Plan, Task
from bob_tools.planfile.validation import AcceptParseError, parse_accept_value

from duplo.batch_coverage import _is_test_task

_BATCH_FLAG = "BATCH"
_COMMAND_RE = re.compile(r"`([^`\n]+)`")
_PATH_RE = re.compile(r"[\w./-]+\.[A-Za-z0-9]+\b")
_PY_PATH_RE = re.compile(r"[\w./-]+\.py\b")
_TEST_TASK_RE = re.compile(r"\b(?:add|create|write|implement)\b.*\btests?\b", re.I)


class AcceptanceAuthoringError(RuntimeError):
    """Raised when duplo cannot prove an authored leaf implementation task."""


@dataclass(frozen=True)
class _Leaf:
    task: Task
    phase_index: int
    order: int
    batch_ids: tuple[str, ...]


def _accept_value(value: str) -> str:
    parsed = parse_accept_value(value)
    if isinstance(parsed, AcceptParseError):
        raise AcceptanceAuthoringError(parsed.message)
    return value


def _accept_annotations(task: Task) -> list[str]:
    return [value for key, value in task.annotations if key == "accept"]


def _validate_existing_accept(task: Task) -> bool:
    values = _accept_annotations(task)
    if not values:
        return False
    if len(values) > 1:
        raise AcceptanceAuthoringError(
            f"task {task.task_id or task.text!r} has more than one accept annotation"
        )
    parsed = parse_accept_value(values[0])
    if isinstance(parsed, AcceptParseError):
        raise AcceptanceAuthoringError(
            f"task {task.task_id or task.text!r} has invalid accept annotation: {parsed.message}"
        )
    return True


def _is_leaf_implementation(task: Task) -> bool:
    return not task.children and "USER" not in task.flag_tags and task.action_tag is None


def _walk_leaves(
    tasks: tuple[Task, ...],
    *,
    phase_index: int,
    order: int,
    batch_ids: tuple[str, ...] = (),
) -> tuple[list[_Leaf], int]:
    leaves: list[_Leaf] = []
    for task in tasks:
        current_batches = batch_ids
        if _BATCH_FLAG in task.flag_tags and task.task_id is not None:
            current_batches = batch_ids + (task.task_id,)
        if _is_leaf_implementation(task):
            leaves.append(
                _Leaf(
                    task=task,
                    phase_index=phase_index,
                    order=order,
                    batch_ids=current_batches,
                )
            )
            order += 1
        elif task.children:
            child_leaves, order = _walk_leaves(
                task.children,
                phase_index=phase_index,
                order=order,
                batch_ids=current_batches,
            )
            leaves.extend(child_leaves)
        else:
            order += 1
    return leaves, order


def _walk_proof_tasks(
    tasks: tuple[Task, ...],
    *,
    phase_index: int,
    order: int,
) -> tuple[list[_Leaf], int]:
    proofs: list[_Leaf] = []
    for task in tasks:
        if task.action_tag is not None and task.task_id is not None:
            proofs.append(_Leaf(task=task, phase_index=phase_index, order=order, batch_ids=()))
            order += 1
        elif task.children:
            child_proofs, order = _walk_proof_tasks(
                task.children,
                phase_index=phase_index,
                order=order,
            )
            proofs.extend(child_proofs)
        else:
            order += 1
    return proofs, order


def _phase_leaves(plan: Plan) -> list[_Leaf]:
    leaves: list[_Leaf] = []
    order = 0
    for phase_index, phase in enumerate(plan.phases):
        phase_leaves, order = _walk_leaves(
            phase.tasks,
            phase_index=phase_index,
            order=order,
        )
        leaves.extend(phase_leaves)
        for subsection in phase.subsections:
            subsection_leaves, order = _walk_leaves(
                subsection.tasks,
                phase_index=phase_index,
                order=order,
            )
            leaves.extend(subsection_leaves)
    return leaves


def _phase_proofs(plan: Plan) -> list[_Leaf]:
    proofs: list[_Leaf] = []
    order = 0
    for phase_index, phase in enumerate(plan.phases):
        phase_proofs, order = _walk_proof_tasks(
            phase.tasks,
            phase_index=phase_index,
            order=order,
        )
        proofs.extend(phase_proofs)
        for subsection in phase.subsections:
            subsection_proofs, order = _walk_proof_tasks(
                subsection.tasks,
                phase_index=phase_index,
                order=order,
            )
            proofs.extend(subsection_proofs)
    return proofs


def _batch_ids_with_pytest_proof(leaves: list[_Leaf]) -> set[str]:
    proven: set[str] = set()
    for leaf in leaves:
        if not _is_pytest_task(leaf.task.text):
            continue
        proven.update(leaf.batch_ids)
    return proven


def _is_pytest_task(text: str) -> bool:
    return _is_test_task(text) or _TEST_TASK_RE.search(text) is not None


def _command_from_text(text: str) -> str | None:
    matches = [match.strip() for match in _COMMAND_RE.findall(text)]
    if len(matches) != 1:
        return None
    command = matches[0]
    try:
        shlex.split(command)
    except ValueError:
        return None
    return command if command else None


def _mentions_python_change(text: str) -> bool:
    return bool(_PY_PATH_RE.search(text)) and not _is_pytest_task(text)


def _mentions_non_python_change(text: str) -> bool:
    return bool(_PATH_RE.search(text)) and not bool(_PY_PATH_RE.search(text))


def _direct_acceptance(leaf: _Leaf, pytest_batches: set[str]) -> str | None:
    if _is_pytest_task(leaf.task.text):
        return _accept_value("pytest")
    if any(batch_id in pytest_batches for batch_id in leaf.batch_ids):
        return _accept_value("pytest")
    command = _command_from_text(leaf.task.text)
    if command is not None:
        return _accept_value(f"command-exit: {command}")
    return None


def _find_downstream_proof(
    leaf: _Leaf,
    proof_leaves: list[_Leaf],
    accept_by_id: dict[str, str],
    accepted_ids: set[str],
) -> str | None:
    for proof in proof_leaves:
        task_id: str | None = proof.task.task_id
        if task_id is None:
            continue
        if proof.phase_index != leaf.phase_index or proof.order <= leaf.order:
            continue
        if proof.task.action_tag is not None or task_id in accept_by_id or task_id in accepted_ids:
            return task_id
    return None


def _with_accept(task: Task, value: str) -> Task:
    return dataclasses.replace(task, annotations=task.annotations + (("accept", value),))


def _apply_acceptance(task: Task, accept_by_id: dict[str, str]) -> Task:
    children = tuple(_apply_acceptance(child, accept_by_id) for child in task.children)
    if children != task.children:
        task = dataclasses.replace(task, children=children)
    if task.task_id is None or task.task_id not in accept_by_id:
        return task
    return _with_accept(task, accept_by_id[task.task_id])


def _apply_phase(phase: Phase, accept_by_id: dict[str, str]) -> Phase:
    return dataclasses.replace(
        phase,
        tasks=tuple(_apply_acceptance(task, accept_by_id) for task in phase.tasks),
        subsections=tuple(
            dataclasses.replace(
                subsection,
                tasks=tuple(_apply_acceptance(task, accept_by_id) for task in subsection.tasks),
            )
            for subsection in phase.subsections
        ),
    )


def ensure_acceptance_annotations(plan: Plan) -> Plan:
    """Return ``plan`` with declared acceptance on every leaf implementation task.

    The input must already carry task ids so waived annotations can name
    a concrete downstream proof task.
    """
    leaves = _phase_leaves(plan)
    proof_leaves = leaves + _phase_proofs(plan)
    pytest_batches = _batch_ids_with_pytest_proof(leaves)
    accept_by_id: dict[str, str] = {}
    accepted_ids: set[str] = set()
    unresolved: list[_Leaf] = []

    for leaf in leaves:
        task = leaf.task
        if task.task_id is None:
            raise AcceptanceAuthoringError(
                f"task {task.text!r} has no task id; cannot author accept annotation"
            )
        if _validate_existing_accept(task):
            accepted_ids.add(task.task_id)
            continue
        value = _direct_acceptance(leaf, pytest_batches)
        if value is None:
            unresolved.append(leaf)
        else:
            accept_by_id[task.task_id] = value

    for leaf in unresolved:
        task = leaf.task
        task_id = task.task_id
        assert task_id is not None
        proof_id = _find_downstream_proof(
            leaf,
            proof_leaves,
            accept_by_id,
            accepted_ids,
        )
        if (
            proof_id is not None
            and not _mentions_python_change(task.text)
            and _mentions_non_python_change(task.text)
        ):
            accept_by_id[task_id] = _accept_value(
                f"waived: non-Python change covered by downstream proof; covered-by={proof_id}"
            )
            continue
        raise AcceptanceAuthoringError(
            f"cannot derive accept annotation for leaf implementation task {task_id}: {task.text}"
        )

    if not accept_by_id:
        return plan
    return dataclasses.replace(
        plan,
        phases=tuple(_apply_phase(phase, accept_by_id) for phase in plan.phases),
    )


__all__ = ["AcceptanceAuthoringError", "ensure_acceptance_annotations"]
