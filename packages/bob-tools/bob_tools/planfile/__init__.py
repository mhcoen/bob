"""bob_tools.planfile — deterministic PLAN.md parsing, rendering, and operations.

A single library that owns PLAN.md syntax so that mcloop, duplo, and
human editors all read and write the format through the same code path
rather than through ad-hoc per-tool Markdown parsing. PLAN.md becomes
machine-owned structurally while remaining hand-editable.

Authoritative design reference: /Users/mhcoen/proj/bob/design/planfile.md.

The ``__all__`` list below is the library's intended public surface.
Names are uncommented as the stage that implements them lands; a
commented name is specified but not yet built. As of Stage 2 the
parser and the typed model are live; renderer, operations, file I/O,
and the CLI are subsequent stages.
"""

from __future__ import annotations

from bob_tools.planfile.model import (
    BugsSection,
    Phase,
    Plan,
    PlanInconsistencyError,
    PlanSyntaxError,
    PlanValidationError,
    RuledOut,
    Subsection,
    Task,
    TaskStatus,
)
from bob_tools.planfile.operations import bug_count
from bob_tools.planfile.parser import parse_plan
from bob_tools.planfile.renderer import render_plan

__all__ = [
    # Stage 2 — parser + typed model (live). Sorted ASCII-alphabetically
    # to satisfy ruff RUF022; the leading-comment grouping that used to
    # cluster types vs. errors vs. functions is reflected in module
    # docstrings rather than __all__ ordering.
    "BugsSection",
    "Phase",
    "Plan",
    "PlanInconsistencyError",
    "PlanSyntaxError",
    "PlanValidationError",
    "RuledOut",
    "Subsection",
    "Task",
    "TaskStatus",
    "bug_count",
    "parse_plan",
    "render_plan",
    # Stage 3 — strict-mode parser additions (no new public names;
    # parse_plan(strict=True) is the surface)
    # Stage 4 — renderer (render_plan live; canonicalize lands later)
    # "canonicalize",
    # Stage 5 — operations
    # "migrate",
    # "next_tasks",
    # "complete_task",
    # "fail_task",
    # "reset_task",
    # "add_task",
    # "replace_phase",
    # "resolve_task_context",
    # "check_consistency",
    # "Settlement",
    # "TaskContext",
    # Stage 6 — file I/O
    # "load",
    # "save",
    # "update",
]
