"""Sanctioned in-session test-invocation adapter.

A thin wrapper over the existing scoped verdict path -- ``run_checks``
with an explicit ``changed_files`` set. It is NOT a second test runner:
it derives the changed-file set deterministically from the task's
pre-edit baseline and delegates the actual verdict to ``run_checks``.

Why this exists: when the inner agent wants to verify its own edits it
must not run raw pytest and self-interpret the output (a vacuous green
-- nothing collected, all skipped, an unparseable summary -- would read
as success). Routing through ``run_checks(changed_files=...)`` reuses
the same signal predicate the loop applies at the per-task gate, and
the adapter exits non-zero on any no-signal or failure so the agent
cannot reinterpret the result.

It NEVER calls the unscoped ``run_checks(project_dir)`` form; that is
the phase-boundary full-suite path. If the baseline or the changed-file
set cannot be resolved, the adapter fails closed (non-zero) rather than
falling back to a full suite or returning a vacuous pass.
"""

from __future__ import annotations

from pathlib import Path

from mcloop.git_ops import _changed_files_since, _read_task_baseline

# Exit codes. 0 = scoped checks passed. 1 = scoped checks failed / no
# valid test signal. 2 = fail-closed (could not resolve the baseline or
# the changed-file set). Anything non-zero means "do not trust this as a
# pass."
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_FAIL_CLOSED = 2


def run_verify(project_dir: str | Path) -> tuple[int, str]:
    """Run scoped checks for the in-session adapter.

    Resolves the task's pre-edit baseline from ``.mcloop/task-baseline``,
    derives the changed-file set against it (working tree included), and
    delegates to ``run_checks(project_dir, changed_files=...)``.

    Returns ``(exit_code, message)``. ``exit_code`` is 0 only when the
    scoped run produces valid passing signal. It fails closed
    (``EXIT_FAIL_CLOSED``) when the baseline cannot be resolved or the
    changed-file set cannot be resolved or is empty; it returns
    ``EXIT_FAILED`` when ``run_checks`` reports a failure / no valid
    signal.
    """
    # Imported lazily so tests can patch ``mcloop.checks.run_checks`` and
    # so the adapter never resolves the unscoped full-suite path.
    from mcloop.checks import run_checks

    project_dir = Path(project_dir)

    baseline = _read_task_baseline(project_dir)
    if not baseline:
        return (
            EXIT_FAIL_CLOSED,
            "no task baseline recorded (.mcloop/task-baseline missing);"
            " cannot scope verification -- failing closed",
        )

    changed_files = _changed_files_since(project_dir, baseline)
    if changed_files is None:
        return (
            EXIT_FAIL_CLOSED,
            f"could not resolve changed files against baseline {baseline[:12]}"
            " -- failing closed rather than running the full suite",
        )
    if not changed_files:
        return (
            EXIT_FAIL_CLOSED,
            "no changed files resolved against the task baseline;"
            " nothing to verify -- failing closed rather than passing vacuously",
        )

    result = run_checks(project_dir, changed_files=changed_files)
    if result.passed:
        return EXIT_OK, f"scoped checks passed: {result.command}"
    return (
        EXIT_FAILED,
        f"scoped checks failed: {result.command}\n{result.output}",
    )
