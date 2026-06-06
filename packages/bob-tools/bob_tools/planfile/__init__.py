"""bob_tools.planfile — deterministic PLAN.md parsing, rendering, and operations.

A single library that owns PLAN.md syntax so that mcloop, duplo, and
human editors all read and write the format through the same code path
rather than through ad-hoc per-tool Markdown parsing. PLAN.md becomes
machine-owned structurally while remaining hand-editable.

Authoritative design reference: /Users/mhcoen/proj/bob/design/planfile.md.

The ``__all__`` list below is the library's intended public surface.
All exported names are implemented and live.
"""

from __future__ import annotations

from bob_tools.planfile.canonical import (
    assert_mcloop_canonical,
    resolve_task_context,
)
from bob_tools.planfile.construction import make_task
from bob_tools.planfile.fileio import (
    ConcurrentUpdateError,
    TaskNotFoundError,
    load,
    resolve_global,
    save,
    update,
)
from bob_tools.planfile.iteration import bug_count
from bob_tools.planfile.migration import (
    migrate,
    replace_phase,
    replace_phase_validated,
)
from bob_tools.planfile.model import (
    BugsSection,
    Outcome,
    Phase,
    Plan,
    PlanInconsistencyError,
    PlanSyntaxError,
    PlanValidationError,
    RuledOut,
    Settlement,
    Subsection,
    Task,
    TaskContext,
    TaskStatus,
)
from bob_tools.planfile.parser import parse_plan
from bob_tools.planfile.plan_artifact import (
    PlanArtifactRejected,
    sanitize_plan_artifact,
)
from bob_tools.planfile.preflight import (
    PlanPreflightError,
    preflight_runtime_plan,
)
from bob_tools.planfile.renderer import canonicalize, render_plan
from bob_tools.planfile.scheduling import next_tasks
from bob_tools.planfile.status import (
    check_consistency,
    clear_failed,
    complete_task,
    fail_task,
    purge_done_bug_tasks,
    reset_task,
)
from bob_tools.planfile.task_addition import (
    add_bug_task,
    add_phase_task,
    add_task,
)
from bob_tools.planfile.validation import validate_plan

__all__ = [
    # Public surface. Sorted ASCII-alphabetically to satisfy ruff RUF022;
    # the grouping by stage that used to live here is recorded in module
    # docstrings instead of __all__ ordering.
    "BugsSection",
    "ConcurrentUpdateError",
    "Outcome",
    "Phase",
    "Plan",
    "PlanArtifactRejected",
    "PlanInconsistencyError",
    "PlanPreflightError",
    "PlanSyntaxError",
    "PlanValidationError",
    "RuledOut",
    "Settlement",
    "Subsection",
    "Task",
    "TaskContext",
    "TaskNotFoundError",
    "TaskStatus",
    "add_bug_task",
    "add_phase_task",
    "add_task",
    "assert_mcloop_canonical",
    "bug_count",
    "canonicalize",
    "check_consistency",
    "clear_failed",
    "complete_task",
    "fail_task",
    "load",
    "make_task",
    "migrate",
    "next_tasks",
    "parse_plan",
    "preflight_runtime_plan",
    "purge_done_bug_tasks",
    "render_plan",
    "replace_phase",
    "replace_phase_validated",
    "reset_task",
    "resolve_global",
    "resolve_task_context",
    "sanitize_plan_artifact",
    "save",
    "update",
    "validate_plan",
]
