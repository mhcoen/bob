"""Plan Ledger Slice D: McLoop event emission.

McLoop emits typed events to the project's Plan Ledger as it runs
tasks. Events emitted by McLoop carry ``writer_id="mcloop"`` to
distinguish them from events Duplo writes.

Public surface:

  - :func:`is_plan_ledger_enabled` -- per-run gate (config + CLI flag).
  - :func:`resolve_phase_id` -- map a McLoop task label to a Slice C
    phase_id, with explicit-required / ordinal-degraded contract.
  - :func:`emit_task_lifecycle_events` -- emit lifecycle events for
    one task's outcome.
  - :func:`emit_phase_started` -- emit a phase_started event when a
    new phase begins.
  - :func:`record_phase_id_fallback` -- emit a finding_observed event
    when phase-id resolution falls back to ordinal mapping.

The full design is in
``bob-tools/design/plan-ledger-slice-d.md``. The structural
rationale for the explicit-required / ordinal-degraded resolution
contract lives in the Q3 resolution section there.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcloop.git_ops import run_git_bounded

_PHASE_HEADER_RE = re.compile(r"^##\s+Phase\s+(?P<id>[A-Za-z0-9_]+):\s+(?P<title>.+?)\s*$")
_PHASE_ID_COMMENT_RE = re.compile(r"<!--\s*phase_id\s*:\s*(?P<id>[A-Za-z0-9_]+)\s*-->")
_LEDGER_DIR_DEFAULT_NAME = ".duplo/ledger"


class LedgerEmitError(RuntimeError):
    """Raised when ledger emission cannot proceed for a wiring reason.

    Distinguishes from bob_tools schema errors (which are emitter-
    contract failures inside the ledger library) and from
    LineageValidationError raised from duplo on re-author paths.
    """


@dataclass(frozen=True)
class PhaseIdResolution:
    """Result of mapping a task to a phase_id.

    ``source`` is one of:
      - ``"explicit"``: the resolution came from a header-prefixed
        phase_id in PLAN.md (production path).
      - ``"ordinal"``: degraded fallback. A finding_observed event
        was emitted at resolution time so the audit trail captures
        the degradation.
      - ``"none"``: no phase resolved. The task is not associated
        with a planned phase (audit work, ad-hoc commands, etc.).
    """

    phase_id: str | None
    source: str
    plan_phase_count: int


# ---------------------------------------------------------------------
# Phase id resolution
# ---------------------------------------------------------------------


def parse_plan_phase_ids(plan_text: str) -> list[str]:
    """Extract phase_ids from PLAN.md headers in source order.

    Reads only the strict ``## Phase <phase_id>: <title>`` form. The
    same regex Duplo's reauthor parser uses; keeping it identical
    means re-authored plans round-trip cleanly through both
    consumers without divergence.

    Pre-Slice C plans whose headers do not match this form return
    an empty list. The ordinal-fallback path expects this and emits
    findings on each task with no recognized phase_id.
    """
    ids: list[str] = []
    for line in plan_text.splitlines():
        match = _PHASE_HEADER_RE.match(line)
        if match is not None:
            ids.append(match.group("id"))
    return ids


def find_explicit_phase_id_for_task(plan_text: str, task_label: str) -> str | None:
    """Find the phase_id closest above ``task_label`` in PLAN.md.

    The Slice C synthesizer template puts phase_id in the header
    itself (``## Phase phase_001: Title``), and that header sits
    above all task lines belonging to the phase. Slice D treats the
    nearest preceding ``## Phase phase_NNN:`` header as the explicit
    phase_id for every task line within that phase.

    Returns None when ``task_label`` does not appear in the file or
    no phase header precedes it.
    """
    if not task_label:
        return None
    lines = plan_text.splitlines()
    current_phase_id: str | None = None
    label_token = task_label.strip()
    for line in lines:
        match = _PHASE_HEADER_RE.match(line)
        if match is not None:
            current_phase_id = match.group("id")
            continue
        if label_token and label_token in line:
            return current_phase_id
        comment_match = _PHASE_ID_COMMENT_RE.search(line)
        if comment_match is not None:
            current_phase_id = comment_match.group("id")
    return None


def resolve_phase_id(
    *,
    plan_path: Path,
    task_label: str,
    ordinal_index: int | None = None,
) -> PhaseIdResolution:
    """Resolve a task to a phase_id through ``bob_tools.planfile``.

    Stage B2 keeps this public signature and return type stable while
    moving lookup onto parsed plan entries. ``ordinal_index`` is the
    explicit opt-in for the old degraded ordinal path; callers that omit
    it (notably ``main._ledger_settle``) preserve today's
    ``source="none"`` emission for pre-migration plans with no explicit
    phase ids.
    """
    if not plan_path.exists():
        return PhaseIdResolution(phase_id=None, source="none", plan_phase_count=0)
    plan_text = plan_path.read_text(encoding="utf-8")
    from bob_tools.planfile import parse_plan, resolve_task_context

    plan = parse_plan(plan_text, source_path=plan_path)
    ctx = resolve_task_context(plan, task_label)
    plan_phase_count = ctx.plan_phase_count

    if ctx.phase_id_source in {"explicit_comment", "explicit_header"}:
        return PhaseIdResolution(
            phase_id=ctx.phase_id,
            source="explicit",
            plan_phase_count=plan_phase_count,
        )

    if ctx.phase_id_source == "ordinal" and ordinal_index is not None:
        return PhaseIdResolution(
            phase_id=ctx.phase_id,
            source="ordinal",
            plan_phase_count=plan_phase_count,
        )

    if (
        ctx.phase_id_source == "none"
        and ordinal_index is not None
        and 0 <= ordinal_index < plan_phase_count
    ):
        return PhaseIdResolution(
            phase_id=f"phase_{ordinal_index + 1:03d}",
            source="ordinal",
            plan_phase_count=plan_phase_count,
        )

    return PhaseIdResolution(phase_id=None, source="none", plan_phase_count=plan_phase_count)


def record_phase_id_fallback(
    *,
    storage: Any,
    task_label: str,
    resolution: PhaseIdResolution,
    run_id: str,
    git: Any | None = None,
) -> str | None:
    """Emit a stderr warning + finding_observed event when resolution
    fell back to ordinal mapping.

    No-ops for ``source != "ordinal"``. Returns the event_id of the
    emitted finding_observed, or None when no event was emitted.
    """
    if resolution.source != "ordinal":
        return None
    print(
        f"[mcloop] phase_id resolution fell back to ordinal mapping "
        f"for task {task_label!r}; degraded mode -- migrate PLAN.md "
        "to use explicit `## Phase phase_NNN: title` headers",
        file=sys.stderr,
    )
    from bob_tools.ledger.events import EventType, make_finding_observed_payload

    ev = storage.append(
        event_type=EventType.FINDING_OBSERVED,
        payload=make_finding_observed_payload(
            summary=(
                f"phase_id resolution fell back to ordinal mapping for "
                f"task {task_label!r} (resolved to "
                f"{resolution.phase_id!r})"
            ),
            phase_id=resolution.phase_id,
            tags=["plan_ledger", "phase_id_fallback", "degraded"],
        ),
        run_id=run_id,
        git=git,
    )
    return str(ev.event_id)


# ---------------------------------------------------------------------
# Task-lifecycle event emission
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class TaskOutcome:
    """A normalized view of one task's outcome for ledger emission.

    The mcloop runner returns a richer ``RunResult`` /
    ``CodeEditResult``; this struct is the slice of those that the
    ledger emitter actually consumes. Keeping the surface narrow
    means the emitter does not need to track changes to the runner
    types.
    """

    success: bool
    abandoned: bool
    summary: str
    changed_files: tuple[str, ...]
    failure_kind: str | None = None
    transcript_ref: str | None = None


def _classify_change(touched_paths: list[str]) -> str:
    """Best-effort change_class from file paths.

    The Slice A schema's CommitChangeClass enum is the canonical
    source. We choose the most-specific class that fits the touched
    paths; mixed sets fall through to "code".
    """
    if not touched_paths:
        return "code"
    is_test = all(
        path.startswith("tests/")
        or path.startswith("test/")
        or "/tests/" in path
        or path.endswith("_test.py")
        or path.endswith(".test.ts")
        or path.endswith(".test.js")
        for path in touched_paths
    )
    if is_test:
        return "test"
    is_docs = all(
        path.endswith(".md")
        or path.endswith(".rst")
        or path.startswith("docs/")
        or "/docs/" in path
        for path in touched_paths
    )
    if is_docs:
        return "docs"
    return "code"


def _git_head_sha(project_dir: Path) -> str | None:
    """Best-effort git HEAD sha; returns None outside a git checkout."""
    try:
        result = run_git_bounded(["git", "rev-parse", "HEAD"], str(project_dir))
    except (FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


def _git_subject(project_dir: Path, sha: str) -> str:
    """Return the commit subject for ``sha``; empty string on failure."""
    try:
        result = run_git_bounded(
            ["git", "log", "-1", "--format=%s", sha],
            str(project_dir),
        )
    except (FileNotFoundError, OSError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _git_author(project_dir: Path, sha: str) -> str:
    """Return the author for ``sha`` (``Name <email>``); empty on failure."""
    try:
        result = run_git_bounded(
            ["git", "log", "-1", "--format=%an <%ae>", sha],
            str(project_dir),
        )
    except (FileNotFoundError, OSError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _git_branch(project_dir: Path) -> str | None:
    """Return the current branch (no abbrev) or None outside git."""
    try:
        result = run_git_bounded(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            str(project_dir),
        )
    except (FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    out = result.stdout.strip()
    return out or None


def _git_parents(project_dir: Path, sha: str) -> list[str]:
    """Return the parent shas of ``sha`` (empty for root commits)."""
    try:
        result = run_git_bounded(
            ["git", "log", "-1", "--format=%P", sha],
            str(project_dir),
        )
    except (FileNotFoundError, OSError):
        return []
    if result.returncode != 0:
        return []
    return [p for p in result.stdout.strip().split() if p]


def _git_diff_numstat(project_dir: Path, sha: str) -> tuple[int, int, int]:
    """Return (files_changed, lines_added, lines_removed) for ``sha``."""
    try:
        result = run_git_bounded(
            ["git", "show", "--numstat", "--format=", sha],
            str(project_dir),
        )
    except (FileNotFoundError, OSError):
        return 0, 0, 0
    if result.returncode != 0:
        return 0, 0, 0
    files = 0
    added = 0
    removed = 0
    for line in result.stdout.splitlines():
        parts = line.strip().split("\t")
        if len(parts) < 3:
            continue
        files += 1
        try:
            added += int(parts[0]) if parts[0] != "-" else 0
            removed += int(parts[1]) if parts[1] != "-" else 0
        except ValueError:
            continue
    return files, added, removed


def emit_task_lifecycle_events(
    *,
    storage: Any,
    task_label: str,
    phase_id: str | None,
    outcome: TaskOutcome,
    project_dir: Path,
    run_id: str,
    git: Any | None = None,
) -> list[str]:
    """Emit lifecycle events for one task's outcome.

    Returns the event_ids of events written, in emission order.
    Slice D's task -> event mapping per the design doc:

      - success and a HEAD commit exists distinct from the prior
        HEAD: emit ``commit_landed`` with files_changed / change_class
        derived from the diff. The caller is responsible for snapping
        the prior HEAD before invoking the task; if that snapshot is
        unavailable the emitter still emits a commit_landed for the
        current HEAD.
      - failure (success=False, not abandoned): emit ``test_failed``
        with the outcome's summary and failure_kind.
      - abandoned: emit ``test_failed`` carrying the task's
        phase_id (when present) and the abandoned reason. When the
        task has no phase_id, emit ``finding_observed`` instead.

    Slice D originally emitted ``phase_abandoned`` for abandoned
    tasks with a phase_id, but that conflated execution-time
    failures (a single task exhausting max_retries) with
    project-level abandonment (a phase the team has stopped
    pursuing). The bob_tools threshold rule on phase_abandoned then
    fired ``reauthor_phase`` for what was actually a stuck task,
    burning council cycles on a problem reauthor cannot solve.
    ``phase_abandoned`` is now reserved for explicit project-level
    decisions; mcloop emits ``test_failed`` with
    failure_kind="max_retries_exceeded" (or whatever the caller set
    in ``outcome.failure_kind``) for retry-exhaustion.

    Per the Slice D Q5 resolution: McLoop emits ``test_failed`` for
    evidence; ``assumption_falsified`` is NOT auto-derived from a
    failing test. That derivation requires explicit assumption
    metadata which Slice D does not yet ship.
    """
    from bob_tools.ledger import CommitChangeClass, EventType
    from bob_tools.ledger.events import (
        make_commit_landed_payload,
        make_finding_observed_payload,
        make_test_failed_payload,
    )

    emitted: list[str] = []

    if outcome.abandoned:
        if phase_id is not None:
            ev = storage.append(
                event_type=EventType.TEST_FAILED,
                payload=make_test_failed_payload(
                    test_id=task_label,
                    phase_id=phase_id,
                    failure_kind=outcome.failure_kind or "task_abandoned",
                    summary=outcome.summary or "abandoned by mcloop",
                    transcript_ref=outcome.transcript_ref,
                ),
                run_id=run_id,
                git=git,
            )
            emitted.append(str(ev.event_id))
            return emitted
        ev = storage.append(
            event_type=EventType.FINDING_OBSERVED,
            payload=make_finding_observed_payload(
                summary=(
                    f"task {task_label!r} abandoned by mcloop: "
                    f"{outcome.summary or 'no reason given'}"
                ),
                phase_id=None,
                tags=["mcloop", "task_abandoned"],
            ),
            run_id=run_id,
            git=git,
        )
        emitted.append(str(ev.event_id))
        return emitted

    if outcome.success:
        sha = _git_head_sha(project_dir)
        if sha is None:
            return emitted
        files_changed, lines_added, lines_removed = _git_diff_numstat(project_dir, sha)
        change_class_str = _classify_change(list(outcome.changed_files))
        try:
            change_class = CommitChangeClass(change_class_str)
        except ValueError:
            change_class = CommitChangeClass.CODE
        ev = storage.append(
            event_type=EventType.COMMIT_LANDED,
            payload=make_commit_landed_payload(
                commit=sha,
                parent_commits=_git_parents(project_dir, sha),
                branch=_git_branch(project_dir),
                author=_git_author(project_dir, sha) or "unknown",
                subject=_git_subject(project_dir, sha) or task_label,
                attributed_phase_id=phase_id,
                files_changed=files_changed,
                lines_added=lines_added,
                lines_removed=lines_removed,
                change_class=change_class,
                touched_paths=list(outcome.changed_files) or None,
            ),
            run_id=run_id,
            git=git,
        )
        emitted.append(str(ev.event_id))
        return emitted

    ev = storage.append(
        event_type=EventType.TEST_FAILED,
        payload=make_test_failed_payload(
            test_id=task_label,
            phase_id=phase_id,
            failure_kind=outcome.failure_kind or "task_failed",
            summary=outcome.summary or "task failed without summary",
            transcript_ref=outcome.transcript_ref,
        ),
        run_id=run_id,
        git=git,
    )
    emitted.append(str(ev.event_id))
    return emitted


def emit_phase_started(
    *,
    storage: Any,
    phase_id: str,
    title: str,
    run_id: str,
    predecessor_phase_ids: tuple[str, ...] = (),
    git: Any | None = None,
) -> str:
    """Emit a phase_started event.

    Used at the boundary where mcloop begins work on a new plan
    phase. The caller is responsible for detecting the boundary
    (typically: the first task of a phase whose phase_id has not
    yet been opened in this run).
    """
    from bob_tools.ledger import EventType
    from bob_tools.ledger.events import make_phase_started_payload

    ev = storage.append(
        event_type=EventType.PHASE_STARTED,
        payload=make_phase_started_payload(
            phase_id=phase_id,
            title=title,
            predecessor_phase_ids=predecessor_phase_ids,
        ),
        run_id=run_id,
        git=git,
    )
    return str(ev.event_id)


# ---------------------------------------------------------------------
# Storage helper
# ---------------------------------------------------------------------


def open_mcloop_storage(ledger_dir: Path) -> Any:
    """Construct a Slice A Storage scoped to ``ledger_dir``.

    Slice D writers always identify as ``mcloop`` so events emitted
    by the runner are distinguishable from Duplo's writer ids.
    Multi-runner support (per the design doc's future-path
    section) will replace the writer_id constant with a runner_id-
    bearing scheme.
    """
    from bob_tools.ledger import Storage, allocate_writer_id

    writer_id = allocate_writer_id(prefix="mcloop")
    return Storage(ledger_dir, writer_id=writer_id)


def default_ledger_dir(project_dir: Path) -> Path:
    """Default ledger directory location for a Slice D-enabled project."""
    return Path(project_dir) / _LEDGER_DIR_DEFAULT_NAME


__all__ = [
    "LedgerEmitError",
    "PhaseIdResolution",
    "TaskOutcome",
    "default_ledger_dir",
    "emit_phase_started",
    "emit_task_lifecycle_events",
    "find_explicit_phase_id_for_task",
    "open_mcloop_storage",
    "parse_plan_phase_ids",
    "record_phase_id_fallback",
    "resolve_phase_id",
]
