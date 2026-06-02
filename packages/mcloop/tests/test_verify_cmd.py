"""Tests for the sanctioned in-session test-invocation adapter.

The adapter (``mcloop verify`` -> ``verify_cmd.run_verify``) is a thin
wrapper over the scoped verdict path. It must:
- exit 0 on a passing scoped run,
- exit non-zero with a reason on a no-signal / failed run,
- fail closed when the baseline or changed-file set cannot be resolved,
- NEVER call the unscoped ``run_checks(project_dir)`` full-suite form.
"""

from __future__ import annotations

from unittest import mock

from mcloop import verify_cmd
from mcloop.checks import CheckResult
from mcloop.verify_cmd import (
    EXIT_FAIL_CLOSED,
    EXIT_FAILED,
    EXIT_OK,
    run_verify,
)


def _patch(baseline, changed):
    """Patch the adapter's baseline + changed-file resolvers."""
    return (
        mock.patch.object(verify_cmd, "_read_task_baseline", return_value=baseline),
        mock.patch.object(verify_cmd, "_changed_files_since", return_value=changed),
    )


def test_passing_scoped_run_exits_zero(tmp_path):
    base_p, changed_p = _patch("abc123", ["mcloop/foo.py"])
    with (
        base_p,
        changed_p,
        mock.patch(
            "mcloop.checks.run_checks",
            return_value=CheckResult(passed=True, output="ok", command="pytest tests/test_foo.py"),
        ) as run_checks,
    ):
        code, message = run_verify(tmp_path)
    assert code == EXIT_OK
    assert "passed" in message
    # Scoped: changed_files must be passed; never the unscoped form.
    _, kwargs = run_checks.call_args
    assert kwargs.get("changed_files") == ["mcloop/foo.py"]


def test_no_signal_run_exits_nonzero_with_reason(tmp_path):
    base_p, changed_p = _patch("abc123", ["mcloop/foo.py"])
    with (
        base_p,
        changed_p,
        mock.patch(
            "mcloop.checks.run_checks",
            return_value=CheckResult(
                passed=False,
                output="[no valid test signal: all skipped]",
                command="pytest tests/test_foo.py",
            ),
        ),
    ):
        code, message = run_verify(tmp_path)
    assert code == EXIT_FAILED
    assert code != 0
    assert "failed" in message
    assert "no valid test signal" in message


def test_missing_baseline_fails_closed(tmp_path):
    base_p, changed_p = _patch("", ["mcloop/foo.py"])
    with base_p, changed_p, mock.patch("mcloop.checks.run_checks") as run_checks:
        code, message = run_verify(tmp_path)
    assert code == EXIT_FAIL_CLOSED
    assert code != 0
    assert "baseline" in message
    # Fail-closed must never reach a runner.
    run_checks.assert_not_called()


def test_unresolvable_changed_set_fails_closed(tmp_path):
    base_p, changed_p = _patch("abc123", None)
    with base_p, changed_p, mock.patch("mcloop.checks.run_checks") as run_checks:
        code, message = run_verify(tmp_path)
    assert code == EXIT_FAIL_CLOSED
    assert "failing closed" in message
    run_checks.assert_not_called()


def test_empty_changed_set_fails_closed_not_vacuous_pass(tmp_path):
    base_p, changed_p = _patch("abc123", [])
    with base_p, changed_p, mock.patch("mcloop.checks.run_checks") as run_checks:
        code, message = run_verify(tmp_path)
    assert code == EXIT_FAIL_CLOSED
    assert code != 0
    # An empty changed set must not be turned into a full-suite run nor a
    # silent green.
    run_checks.assert_not_called()


def test_adapter_never_calls_unscoped_run_checks(tmp_path):
    base_p, changed_p = _patch("abc123", ["mcloop/foo.py"])
    with (
        base_p,
        changed_p,
        mock.patch(
            "mcloop.checks.run_checks",
            return_value=CheckResult(passed=True, output="ok", command="pytest"),
        ) as run_checks,
    ):
        run_verify(tmp_path)
    # Exactly one call, and it carries an explicit changed_files set --
    # the phase-boundary unscoped form run_checks(project_dir) is never used.
    assert run_checks.call_count == 1
    args, kwargs = run_checks.call_args
    assert "changed_files" in kwargs
    assert kwargs["changed_files"]
    assert len(args) <= 1  # only project_dir may be positional
