"""Render a typed Plan object back to canonical PLAN.md text.

The renderer is the inverse of :func:`bob_tools.planfile.parser.parse_plan`:
``parse(render(plan)) == plan`` (modulo line numbers) and
``render(parse(text))`` is the canonical-form fixed point. The output is
always written in canonical form per design doc section 4.2 Notes:
two-space indentation per nesting level, phase-id always rendered as a
``<!-- phase_id: ... -->`` comment (even when the input used the legacy
``## Phase phase_NNN: ...`` header form), and exactly one trailing
newline at end of file.

Phase content ordering note: PLAN.md section 4.1's spec line for phase
rendering reads "subsections in order, then tasks in order". Rendering
subsections before phase-level tasks would break round-trip because the
parser captures any task that follows a ``###`` subsection heading into
that subsection (the indent stack does not auto-close on subsection
boundaries). The renderer therefore emits phase-level tasks first, then
subsections — see NOTES.md (2026-05-16, task 4.2.1) for the discrepancy.
"""

from __future__ import annotations

import dataclasses

from bob_tools.planfile.model import (
    BugsSection,
    Phase,
    Plan,
    Subsection,
    Task,
    TaskStatus,
)

_STATUS_CHAR: dict[TaskStatus, str] = {
    TaskStatus.TODO: " ",
    TaskStatus.DONE: "x",
    TaskStatus.FAILED: "!",
}


def render_plan(plan: Plan) -> str:
    """Render ``plan`` to canonical PLAN.md text.

    The result is the byte sequence the parser would emit for this plan
    given two-space indentation, comment-form phase ids, and one
    trailing newline. The output is a fixed point: ``render(parse(s))``
    on a canonical input equals ``s``, and ``render(parse(render(p)))``
    equals ``render(p)`` for any plan.
    """
    lines: list[str] = []

    if plan.magic_version is not None:
        lines.append(f"<!-- bob-plan-format: {plan.magic_version} -->")
        lines.append("")

    if plan.project_title:
        lines.append(f"# {plan.project_title}")
        lines.append("")

    if plan.preamble:
        lines.extend(plan.preamble.split("\n"))
        lines.append("")

    for phase in plan.phases:
        _render_phase_into(lines, phase)

    if plan.bugs is not None:
        _render_bugs_into(lines, plan.bugs)

    while lines and not lines[-1]:
        lines.pop()

    return "\n".join(lines) + "\n"


def _render_phase_into(lines: list[str], phase: Phase) -> None:
    """Append the canonical line sequence for ``phase`` to ``lines``.

    Emits: ``## {keyword} {ordinal}: {title}`` heading; on the next
    line a ``<!-- phase_id: ... -->`` comment whenever
    ``phase_id_source`` is anything other than ``"none"`` (the parser
    migrates ``"explicit_header"`` to the comment form on render per
    design doc section 7.1); a blank line; the phase prose if any plus
    a blank line; the phase-level tasks; a blank line; then each
    subsection in order.
    """
    lines.append(f"## {phase.keyword} {phase.ordinal}: {phase.title}")
    if phase.phase_id is not None and phase.phase_id_source != "none":
        lines.append(f"<!-- phase_id: {phase.phase_id} -->")
    lines.append("")

    if phase.prose:
        lines.extend(phase.prose.split("\n"))
        lines.append("")

    for task in phase.tasks:
        lines.extend(_render_task_lines(task, depth=0))
    if phase.tasks:
        lines.append("")

    for sub in phase.subsections:
        _render_subsection_into(lines, sub)


def _render_subsection_into(lines: list[str], sub: Subsection) -> None:
    """Append the canonical line sequence for ``sub`` to ``lines``.

    Subsections always render at indent zero — they group tasks for
    humans but do not nest structurally, per design doc section 11
    Q5. The ``### Title`` heading is followed by an explicit blank
    line; the parser would treat a missing blank as prose continuation.
    """
    lines.append(f"### {sub.title}")
    lines.append("")

    if sub.prose:
        lines.extend(sub.prose.split("\n"))
        lines.append("")

    for task in sub.tasks:
        lines.extend(_render_task_lines(task, depth=0))
    if sub.tasks:
        lines.append("")


def _render_bugs_into(lines: list[str], bugs: BugsSection) -> None:
    """Append the canonical line sequence for the Bugs section."""
    lines.append("## Bugs")
    lines.append("")
    for task in bugs.tasks:
        lines.extend(_render_task_lines(task, depth=0))
    if bugs.tasks:
        lines.append("")


def _render_task_lines(task: Task, *, depth: int) -> list[str]:
    """Render ``task`` and its dependent siblings (``@deps``, ``[RULEDOUT]``,
    children) into a list of lines at canonical two-space indentation.

    ``depth`` is the nesting level (0 for root tasks); the parser's
    observed ``task.indent_level`` is intentionally discarded so the
    output uses two-space-per-level regardless of input indentation,
    per design doc section 4.2 Notes. Sibling lines emit at
    ``depth + 1`` indent so a re-parse attaches them to the same
    parent. Children render recursively at ``depth + 1`` so the
    parser's indent-stack walker reconstructs the original tree.
    """
    indent = "  " * depth
    child_indent = "  " * (depth + 1)
    status_char = _STATUS_CHAR[task.status]

    body_parts: list[str] = []
    if task.task_id is not None:
        body_parts.append(f"{task.task_id}:")
    for tag in task.flag_tags:
        body_parts.append(f"[{tag}]")
    if task.action_tag is not None:
        action, args = task.action_tag
        body_parts.append(f"[AUTO:{action}]")
        if args:
            body_parts.append(args)
    if task.text:
        body_parts.append(task.text)
    for key, value in task.annotations:
        body_parts.append(f"[{key}: {value}]")

    body = " ".join(body_parts)
    lines = [f"{indent}- [{status_char}] {body}".rstrip()]

    if task.deps:
        lines.append(f"{child_indent}@deps " + " ".join(task.deps))

    for ruled in task.ruled_out:
        if ruled.text:
            lines.append(f"{child_indent}[RULEDOUT] {ruled.text}")
        else:
            lines.append(f"{child_indent}[RULEDOUT]")

    for child in task.children:
        lines.extend(_render_task_lines(child, depth=depth + 1))

    return lines


def normalize_positions(plan: Plan) -> Plan:
    """Return ``plan`` with fields that legitimately differ across
    parse-render-parse cycles normalized so round-trip equality holds.

    The canonicalization performed by ``render_plan`` changes three
    fields that the parser stores from its raw input observations:

    * ``line_number`` (on Plan, Phase, Subsection, BugsSection, Task,
      RuledOut): the rendered text has its own line layout, so source
      coordinates are not preserved.
    * ``Task.indent_level``: the parser stores the raw indent it
      observed, but the renderer always emits two-space-per-level. A
      4-space-indented source maps to ``indent_level=4`` on first parse
      and ``indent_level=2`` after re-render.
    * ``Phase.phase_id_source``: the renderer migrates the legacy
      ``"explicit_header"`` (from ``## Phase phase_NNN: ...``) to the
      canonical ``"explicit_comment"`` form on output, per design doc
      section 7.1. Any explicit source is therefore normalized to
      ``"explicit_comment"``; ``"none"`` is left alone because no
      comment is emitted when there is no id to record.

    All other fields participate in equality unchanged. Frozen
    dataclasses derive ``__eq__`` from their fields, so a deep equal
    check on the normalized values is a faithful round-trip oracle.
    """
    return dataclasses.replace(
        plan,
        phases=tuple(_normalize_phase(p) for p in plan.phases),
        bugs=_normalize_bugs(plan.bugs) if plan.bugs is not None else None,
    )


def _normalize_phase(phase: Phase) -> Phase:
    canonical_source = "explicit_comment" if phase.phase_id_source != "none" else "none"
    return dataclasses.replace(
        phase,
        line_number=0,
        phase_id_source=canonical_source,
        subsections=tuple(_normalize_subsection(s) for s in phase.subsections),
        tasks=tuple(_normalize_task(t, depth=0) for t in phase.tasks),
    )


def _normalize_subsection(sub: Subsection) -> Subsection:
    return dataclasses.replace(
        sub,
        line_number=0,
        tasks=tuple(_normalize_task(t, depth=0) for t in sub.tasks),
    )


def _normalize_bugs(bugs: BugsSection) -> BugsSection:
    return dataclasses.replace(
        bugs,
        line_number=0,
        tasks=tuple(_normalize_task(t, depth=0) for t in bugs.tasks),
    )


def _normalize_task(task: Task, *, depth: int) -> Task:
    return dataclasses.replace(
        task,
        line_number=0,
        indent_level=depth * 2,
        children=tuple(_normalize_task(c, depth=depth + 1) for c in task.children),
        ruled_out=tuple(dataclasses.replace(r, line_number=0) for r in task.ruled_out),
    )
