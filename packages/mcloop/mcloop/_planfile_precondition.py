"""Canonical-form precondition check for mcloop PLAN.md inputs.

Canonicality is enforced by distinct predicates. R1 rejects grammar
narrowing where the canonical parser would silently drop incomplete
checkboxes. R2 rejects parsed tasks without stable ``T-NNNNNN`` ids
before mcloop routes mutations through the planfile shim.

Why a precondition exists at all
--------------------------------
``bob_tools.planfile.parse_plan`` only surfaces tasks that sit under a
``## Stage`` / ``## Phase`` header. The legacy ``mcloop.checklist.parse``
accepted bare-checkbox / ``# Plan``-only / ``# Project``-only inputs and
treated their checkboxes as tasks. If mcloop silently switches to the
canonical parser without enforcing canonical input, previously-legal
PLAN.md files render their tasks invisible — ``run_loop`` exits
"All tasks completed!" with work left to do. That is R1 in the audit
taxonomy.

The R1 discriminator
--------------------
A correct discriminator must read **both** the raw source text and the
parsed ``Plan`` — not the ``Plan`` alone. ``Plan`` is the artifact that
already dropped the lost tasks; inspecting it alone cannot tell us
whether the input was canonical or whether tasks were silently
discarded. We compare two counts:

    src_incomplete  — count of ``^\\s*- \\[ \\] .+$`` lines in source
    plan_incomplete — count of ``TaskStatus.TODO`` tasks in the parsed Plan
                      (recursively across phase tasks, subsection tasks,
                      and bugs tasks)

If ``src_incomplete > plan_incomplete``, the parser dropped one or more
incomplete tasks — the input was not canonical. We REJECT.
Otherwise the parser saw every incomplete task — we ALLOW. This also
correctly handles "genuinely empty" inputs (``# Plan\\n`` / all-DONE
plans / pure prose): both counts are zero, equal, ALLOW.

The same heuristic is robust to plans that mix a phase-bearing section
with bare checkboxes above it: the bare checkboxes are dropped by
``parse_plan`` so the count mismatch fires and the input is REJECTed,
even though the phase-bearing portion is canonical-looking.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from bob_tools.planfile import Plan, TaskStatus, count_unfenced_incomplete_checkboxes
from bob_tools.planfile import Task as PlanTask

# The incomplete-checkbox count MUST share bob-tools' fence rule: the
# canonical parser treats a checkbox inside a ``` fence as verbatim
# example content, so a fence-unaware count here would exceed the
# parsed task count for any plan carrying a fenced example, and the R1
# discriminator would reject a file that bob-plan fmt itself blesses --
# a permanent startup wedge whose prescribed remediation (fmt) is a
# no-op. The shared counter keeps the two sides of the contract from
# diverging again.

R1Verdict = Literal["ALLOW", "REJECT_GRAMMAR_NARROWED"]
R2Verdict = Literal["ALLOW", "REJECT_ID_LESS_TASKS"]


class PlanNotCanonicalError(Exception):
    """Raised by ``enforce_canonical`` when PLAN.md fails the precondition.

    Carries the source path (when known) so callers can produce
    user-facing diagnostics without re-deriving it.
    """

    def __init__(self, message: str, *, source_path: Path | None = None) -> None:
        super().__init__(message)
        self.source_path = source_path


def _count_incomplete_tasks(plan: Plan) -> int:
    """Total TaskStatus.TODO tasks across phases, subsections, and bugs."""

    def walk(tasks: tuple[PlanTask, ...]) -> int:
        total = 0
        for task in tasks:
            if task.status is TaskStatus.TODO:
                total += 1
            total += walk(task.children)
        return total

    total = 0
    for phase in plan.phases:
        total += walk(phase.tasks)
        for subsection in phase.subsections:
            total += walk(subsection.tasks)
    if plan.bugs is not None:
        total += walk(plan.bugs.tasks)
    return total


def _iter_tasks(plan: Plan) -> list[PlanTask]:
    """Return every task in phase, subsection, and bugs trees."""

    tasks: list[PlanTask] = []

    def walk(children: tuple[PlanTask, ...]) -> None:
        for task in children:
            tasks.append(task)
            walk(task.children)

    for phase in plan.phases:
        walk(phase.tasks)
        for subsection in phase.subsections:
            walk(subsection.tasks)
    if plan.bugs is not None:
        walk(plan.bugs.tasks)
    return tasks


def discriminate_r1(source_text: str, plan: Plan) -> tuple[R1Verdict, str]:
    """Classify a plan input on the R1 (grammar-narrowing) axis.

    Returns ``(verdict, reason)`` where ``verdict`` is one of
    ``"ALLOW"`` or ``"REJECT_GRAMMAR_NARROWED"``. ``reason`` is a human-
    readable explanation suitable for embedding in an error message or
    a diagnostic log; it carries the actual count values so failures
    are debuggable.
    """
    src_incomplete = count_unfenced_incomplete_checkboxes(source_text)
    plan_incomplete = _count_incomplete_tasks(plan)
    if src_incomplete > plan_incomplete:
        dropped = src_incomplete - plan_incomplete
        return (
            "REJECT_GRAMMAR_NARROWED",
            f"source contains {src_incomplete} incomplete checkbox line(s) "
            f"but the canonical parser surfaced only {plan_incomplete} "
            f"as task(s); {dropped} task(s) were silently dropped because "
            f"they sit outside a `## Stage` / `## Phase` header",
        )
    return (
        "ALLOW",
        f"source incomplete checkbox count ({src_incomplete}) matches "
        f"parsed incomplete task count ({plan_incomplete})",
    )


def discriminate_r2(plan: Plan) -> tuple[R2Verdict, str]:
    """Classify a parsed plan on the R2 (ID-less task) axis."""
    id_less_lines = [task.line_number for task in _iter_tasks(plan) if task.task_id is None]
    if id_less_lines:
        locs = ", ".join(f"line {line}" for line in id_less_lines[:10])
        extra = "" if len(id_less_lines) <= 10 else f", plus {len(id_less_lines) - 10} more"
        return (
            "REJECT_ID_LESS_TASKS",
            f"canonical parser surfaced {len(id_less_lines)} task(s) without "
            f"stable T-NNNNNN ids ({locs}{extra})",
        )
    return ("ALLOW", "all parsed tasks carry stable T-NNNNNN ids")


def _fmt_target(source_path: Path | None) -> str:
    """Path argument for the ``bob-plan fmt`` command named in error messages."""
    return str(source_path) if source_path is not None else "<path>"


def enforce_canonical(source_text: str, plan: Plan, *, source_path: Path | None = None) -> None:
    """Raise ``PlanNotCanonicalError`` if ``plan`` fails the precondition.

    Enforces R1 (grammar narrowing) and R2 (ID-less parsed tasks) as
    distinct predicates. Both ``source_text`` and ``plan`` are required
    arguments — R1 cannot be decided from ``plan`` alone (see module
    docstring), while R2 is a parsed-plan predicate.

    The remediation named in each message is ``bob-plan fmt`` — the only
    bob-plan subcommand that canonicalizes a file in place (there is no
    ``migrate`` subcommand). For R1 the tasks are invisible to the
    parser, so no tool can recover them: the user must first move the
    stray checkbox lines under a ``## Stage`` / ``## Phase`` heading
    (``bob-plan fmt`` refuses to save while it would drop them), and
    only then can ``fmt`` assign the ids.
    """
    verdict, reason = discriminate_r1(source_text, plan)
    if verdict == "REJECT_GRAMMAR_NARROWED":
        path_hint = f" {source_path}" if source_path is not None else ""
        raise PlanNotCanonicalError(
            f"PLAN.md{path_hint} is not in canonical bob-tools planfile form.\n"
            f"  {reason}.\n"
            f"  Move the stray checkbox lines under a `## Stage N: <title>` or\n"
            f"  `## Phase N: <title>` heading, then run: bob-plan fmt {_fmt_target(source_path)}\n"
            f"  (bob-plan fmt refuses to save while stray checkboxes would be\n"
            f"  dropped; its output is deterministic and idempotent.)",
            source_path=source_path,
        )
    r2_verdict, r2_reason = discriminate_r2(plan)
    if r2_verdict == "REJECT_ID_LESS_TASKS":
        path_hint = f" {source_path}" if source_path is not None else ""
        raise PlanNotCanonicalError(
            f"PLAN.md{path_hint} is not in canonical bob-tools planfile form.\n"
            f"  {r2_reason}.\n"
            f"  Run: bob-plan fmt {_fmt_target(source_path)}\n"
            f"  (bob-plan fmt assigns the missing T-NNNNNN ids in place; its\n"
            f"  output is deterministic and idempotent.)",
            source_path=source_path,
        )
