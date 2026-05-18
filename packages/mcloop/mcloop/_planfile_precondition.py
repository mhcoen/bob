"""Canonical-form precondition check for mcloop PLAN.md inputs.

INCREMENT 1 of B3 Stage B3.1 — the R1 (grammar-narrowing) discriminator only.
Not wired into ``mcloop/main.py`` in this increment. The R2 (ID-less task)
predicate and run_loop wire-in arrive in later increments of B3.1.

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

import re
from pathlib import Path
from typing import Literal

from bob_tools.planfile import Plan, TaskStatus
from bob_tools.planfile import Task as PlanTask

# Matches the same incomplete-checkbox shape that ``mcloop.checklist``
# (legacy) accepted and that the canonical parser refuses to surface
# when no ``## Stage`` / ``## Phase`` header is in scope.
_INCOMPLETE_RE = re.compile(r"^\s*- \[ \] .+$", re.MULTILINE)

R1Verdict = Literal["ALLOW", "REJECT_GRAMMAR_NARROWED"]


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


def discriminate_r1(source_text: str, plan: Plan) -> tuple[R1Verdict, str]:
    """Classify a plan input on the R1 (grammar-narrowing) axis.

    Returns ``(verdict, reason)`` where ``verdict`` is one of
    ``"ALLOW"`` or ``"REJECT_GRAMMAR_NARROWED"``. ``reason`` is a human-
    readable explanation suitable for embedding in an error message or
    a diagnostic log; it carries the actual count values so failures
    are debuggable.
    """
    src_incomplete = len(_INCOMPLETE_RE.findall(source_text))
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


def enforce_canonical(source_text: str, plan: Plan, *, source_path: Path | None = None) -> None:
    """Raise ``PlanNotCanonicalError`` if ``plan`` fails the precondition.

    This increment implements the R1 (grammar-narrowing) discriminator
    only. R2 (ID-less tasks under a phase) is a separate predicate to
    be added in a later increment of Stage B3.1 of the B3 re-plan; this
    function will be extended at that time. Both ``source_text`` and
    ``plan`` are required arguments — the precondition cannot be
    decided from ``plan`` alone (see module docstring).
    """
    verdict, reason = discriminate_r1(source_text, plan)
    if verdict == "REJECT_GRAMMAR_NARROWED":
        path_hint = f" {source_path}" if source_path is not None else ""
        raise PlanNotCanonicalError(
            f"PLAN.md{path_hint} is not in canonical bob-tools planfile form.\n"
            f"  {reason}.\n"
            f"  Run: bob-plan migrate <path>\n"
            f"  Migration is deterministic; bob-plan fmt produces the same "
            f"output each time, so this is reversible.",
            source_path=source_path,
        )
