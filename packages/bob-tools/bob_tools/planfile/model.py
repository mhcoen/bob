"""Typed dataclasses for the parsed plan and the planfile exception types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


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
