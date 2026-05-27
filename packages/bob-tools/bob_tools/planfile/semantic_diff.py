"""Semantic-equivalence normalization and diff collection for round-trip checks."""

from __future__ import annotations

import dataclasses

from bob_tools.planfile.model import (
    BugsSection,
    Phase,
    Plan,
    Subsection,
    Task,
)
from bob_tools.planfile._shared import _INCOMPLETE_CHECKBOX_RE

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
    for field in ("magic_version", "task_namespace", "project_title", "preamble"):
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
        "created_at",
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
