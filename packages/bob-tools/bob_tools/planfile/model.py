"""Typed dataclasses for the parsed plan and the planfile exception types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal


class TaskStatus(Enum):
    """Three-valued checkbox state for PLAN.md tasks.

    Per design doc section 2.1: the PLAN.md checkbox is three-valued
    (``[ ] / [x] / [!]``). Both ``x`` and ``X`` map to ``DONE``.
    """

    TODO = "TODO"
    DONE = "DONE"
    FAILED = "FAILED"

    @classmethod
    def from_marker(cls, marker: str) -> TaskStatus:
        try:
            return CHECKBOX_MARKER_TO_STATUS[marker]
        except KeyError as exc:
            raise ValueError(
                f"unknown checkbox marker {marker!r}; "
                "expected one of ' ', 'x', 'X', '!'"
            ) from exc


CHECKBOX_MARKER_TO_STATUS = {
    " ": TaskStatus.TODO,
    "x": TaskStatus.DONE,
    "X": TaskStatus.DONE,
    "!": TaskStatus.FAILED,
}


@dataclass(frozen=True)
class RuledOut:
    """An indent-attached ``[RULEDOUT]`` line under a parent task.

    Per design doc section 4.2 and section 11 question 3: ``[RULEDOUT]``
    is a sibling line at the child indent under the task it pertains
    to; it is not a task tag and has no checkbox of its own.
    """

    text: str
    line_number: int


@dataclass(frozen=True)
class Task:
    """A single PLAN.md task entry, including nested children and tags."""

    task_id: str | None
    text: str
    status: TaskStatus
    flag_tags: tuple[str, ...]
    action_tag: tuple[str, str] | None
    annotations: tuple[tuple[str, str], ...]
    deps: tuple[str, ...]
    children: tuple[Task, ...]
    ruled_out: tuple[RuledOut, ...]
    indent_level: int
    line_number: int


@dataclass(frozen=True)
class Subsection:
    """A ``###`` subsection inside a phase, grouping tasks for humans.

    Per design doc section 11 question 5: subsections are parsed as
    structural and preserved through round-trip; they have no semantic
    effect on ``next_tasks`` or ``phase_id``.
    """

    title: str
    prose: str
    tasks: tuple[Task, ...]
    line_number: int


@dataclass(frozen=True)
class Phase:
    """A phase or stage section with its tasks and optional subsections.

    Per design doc section 2.5 and section 7.1: ``keyword`` is either
    ``"Stage"`` or ``"Phase"`` (cosmetic; identity travels via
    ``phase_id``). ``phase_id_source`` records how the id was resolved:
    ``"explicit_comment"`` (``<!-- phase_id: ... -->``),
    ``"explicit_header"`` (legacy ``## Phase phase_NNN: ...`` form),
    ``"ordinal"`` (degraded fallback), or ``"none"``.
    """

    phase_id: str | None
    phase_id_source: str
    ordinal: int
    keyword: str
    title: str
    prose: str
    subsections: tuple[Subsection, ...]
    tasks: tuple[Task, ...]
    line_number: int


@dataclass(frozen=True)
class BugsSection:
    """The ``## Bugs`` priority section. Per design doc section 6."""

    tasks: tuple[Task, ...]
    line_number: int


@dataclass(frozen=True)
class Plan:
    """The top-level parsed PLAN.md document."""

    magic_version: int | None
    project_title: str
    preamble: str
    phases: tuple[Phase, ...]
    bugs: BugsSection | None
    source_path: Path | None


@dataclass(frozen=True)
class TaskContext:
    """Result of resolving a task reference against a parsed :class:`Plan`.

    Returned by :func:`bob_tools.planfile.operations.resolve_task_context`.
    Per design doc section 7.1 the resolver is the single source of
    truth for "given a task reference, which phase does it belong to";
    ``ledger_emit.resolve_phase_id`` becomes a thin shim that reads
    ``phase_id`` and ``phase_id_source`` off this dataclass.

    Fields:

    - ``task_id``: the resolved task's stable ``T-NNNNNN`` id, or ``None``
      when the lookup matched a compat-mode task (no id) or matched
      nothing.
    - ``phase_id``: the containing phase's id, or ``None`` when the task
      lives in the Bugs section or could not be resolved.
    - ``phase_id_source``: how the containing phase's id was determined.
      ``"explicit_comment"`` / ``"explicit_header"`` for resolved phase
      tasks with an explicit identifier; ``"ordinal"`` when the
      containing phase had no explicit id and the resolver synthesized
      ``phase_NNN`` from the phase's 1-based document-order position
      (design doc section 7.1, "Ordinal fallback. The n-th phase
      heading in document order"); ``"none"`` for bug tasks or
      unresolved references. The shim in ``ledger_emit`` collapses
      both ``explicit_*`` values to ``"explicit"`` for its
      ``PhaseIdResolution.source`` field and passes ``"ordinal"``
      through unchanged.
    - ``label``: the input reference, echoed back so callers can include
      it in diagnostics without re-threading the original string.
    - ``plan_phase_count``: ``len(plan.phases)``, snapshotted at resolve
      time so the ordinal-fallback shim does not need a second pass
      over the plan.
    """

    task_id: str | None
    phase_id: str | None
    phase_id_source: str
    label: str
    plan_phase_count: int


@dataclass(frozen=True)
class Outcome:
    """Optional caller-supplied context that enriches a :class:`Settlement`.

    Mutating operations accept an ``outcome`` so callers (mcloop, duplo)
    can pass through the runner's observations — primarily the
    ``failure_kind`` label that ends up on a ``test_failed`` Settlement.
    Fields are individually optional; new fields land here as concrete
    consumer use-cases appear, rather than being speculated up front.
    """

    failure_kind: str | None = None


@dataclass(frozen=True)
class Settlement:
    """Outcome of a mutating operation, summarized for ledger emission.

    Per design doc section 5: every ``complete_task`` / ``fail_task`` /
    ``reset_task`` returns the new Plan paired with one or more
    Settlement values describing what happened, so the ledger_emit shim
    has structured data instead of having to diff the plan tree.

    ``kind`` selects how the consumer should react:

    - ``"commit_landed"``: direct success of a commit-producing task
      (no AUTO action tag, no USER flag). Ledger event required.
    - ``"work_observed"``: direct success of an AUTO action task or a
      successfully verified USER task (no commit involved). Ledger
      event still required so downstream replays observe the success.
    - ``"test_failed"``: direct task failure. ``failure_kind`` carries
      the failure category supplied by the caller (or
      ``"max_retries_exceeded"`` when the caller did not pass an
      :class:`Outcome`). Ledger event required.
    - ``"none"``: derived parent completion. A parent task was
      auto-checked because all of its children are now DONE; this is
      bookkeeping (the parent's state is a function of its children's
      state) and the ledger does not need a new event. Reset-to-TODO
      also produces a ``"none"`` settlement: per design doc section 5,
      reset is an operator decision to retry existing work, not new
      evidence about implementation.
    """

    kind: Literal["commit_landed", "test_failed", "work_observed", "none"]
    task_id: str | None
    phase_id: str | None
    summary: str
    failure_kind: str | None
    ledger_event_required: bool


class PlanSyntaxError(Exception):
    """Raised on malformed PLAN.md syntax. Carries line/column locator."""

    def __init__(
        self,
        message: str,
        line: int,
        column: int,
        path: Path | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.line = line
        self.column = column
        self.path = path

    def __str__(self) -> str:
        return (
            f"PLAN.md invalid at line {self.line}, column {self.column}: {self.message}"
        )


class PlanValidationError(Exception):
    """Raised when a parsed Plan fails validation. Carries every error."""

    def __init__(self, messages: list[str]) -> None:
        super().__init__("; ".join(messages) if messages else "")
        self.messages = list(messages)


class PlanInconsistencyError(Exception):
    """Raised when PLAN.md and the ledger disagree about settlement state."""

    def __init__(self, messages: list[str]) -> None:
        super().__init__("; ".join(messages) if messages else "")
        self.messages = list(messages)
