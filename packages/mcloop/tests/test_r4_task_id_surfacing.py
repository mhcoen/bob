"""R4 = Option B: canonical task IDs surface in user-facing output and
commit messages, alongside (not fused into) ``task.text``.

Three layers of proof:

  (1) ``format_task_id`` returns ``"[T-NNNNNN] "`` for an id-bearing
      task and ``""`` for a task with ``task_id is None`` or empty.

  (2) An integration run of ``run_loop`` against a canonical phase +
      ``T-NNNNNN`` plan emits the id alongside the task text in stdout
      (task-start banner, checkpoint marker, completed list).

  (3) The commit message produced by ``run_loop`` for a canonical
      ID-bearing task contains the ``[T-NNNNNN]`` prefix.

The integration cases monkey-patch the LLM/git/edit-detection layer
so the run reaches commit + completion deterministically without any
external subprocess. They exercise the wire-in surface, not the
backend.
"""

from __future__ import annotations

import re
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mcloop.checklist import Task
from mcloop.formatting import format_task_id

# ---------------------------------------------------------------------------
# (1) Helper unit
# ---------------------------------------------------------------------------


def _task(text: str = "demo", task_id: str | None = "T-000123") -> Task:
    return Task(
        text=text,
        checked=False,
        failed=False,
        line_number=0,
        indent_level=0,
        task_id=task_id,
    )


def test_format_task_id_returns_bracketed_prefix_for_id_bearing_task() -> None:
    assert format_task_id(_task(task_id="T-000123")) == "[T-000123] "


def test_format_task_id_returns_empty_for_none() -> None:
    assert format_task_id(_task(task_id=None)) == ""


def test_format_task_id_returns_empty_for_empty_string() -> None:
    # Module-state and TaskEntry both default ``task_id`` to "" (empty
    # string) rather than None; the helper must treat empty as absent.
    assert format_task_id(_task(task_id="")) == ""


def test_format_task_id_handles_arbitrary_object_with_task_id_attr() -> None:
    class _Stub:
        task_id = "T-999999"

    assert format_task_id(_Stub()) == "[T-999999] "


def test_format_task_id_handles_object_without_task_id_attr() -> None:
    """Defensive: getattr(..., None) keeps the helper crash-safe."""

    class _Stub:
        pass

    assert format_task_id(_Stub()) == ""


# ---------------------------------------------------------------------------
# (2) + (3) Integration through run_loop
# ---------------------------------------------------------------------------


_CANONICAL_PLAN_WITH_ID = (
    "<!-- bob-plan-format: 1 -->\n"
    "\n"
    "# Demo\n"
    "\n"
    "## Stage 1: Setup\n"
    "<!-- phase_id: phase_001 -->\n"
    "\n"
    "- [ ] T-000123: write a hello file\n"
)


@pytest.fixture
def _canonical_repo(tmp_path: Path) -> Path:
    """A canonical PLAN.md plus the seed BUGS.md + a .git dir so the
    run_loop's git-precondition path is satisfied. The directory is
    structurally enough for the wire-in surface to be exercised;
    every downstream call (run_task, check, commit, push) is patched
    in the tests below."""
    (tmp_path / "PLAN.md").write_text(_CANONICAL_PLAN_WITH_ID)
    (tmp_path / "BUGS.md").write_text("## Bugs\n\n")
    (tmp_path / ".git").mkdir()
    return tmp_path / "PLAN.md"


def _drive_one_success(
    plan_md: Path,
    *,
    mock_commit: MagicMock,
    capture_summary_into: list | None = None,
) -> None:
    """Run ``run_loop`` against a canonical single-task plan and let it
    complete one task. Uses ExitStack rather than nested ``with``-
    blocks to stay under Python's static-block limit.
    """
    from mcloop.main import run_loop
    from mcloop.runner import RunResult

    success_result = RunResult(success=True, output="ok", exit_code=0, log_path=None)
    check_passed = MagicMock(passed=True, output="", command="", failures=())

    if capture_summary_into is None:
        _summary_patch = patch("mcloop.main.write_run_summary")
    else:

        def _capture_summary(_project_dir: Path, summary) -> Path:
            capture_summary_into.append(summary)
            return _project_dir / ".mcloop" / "runs" / "captured.json"

        _summary_patch = patch("mcloop.main.write_run_summary", side_effect=_capture_summary)

    patches = [
        patch("mcloop.main.run_task", return_value=success_result),
        patch("mcloop.main.run_checks", return_value=check_passed),
        patch("mcloop.main._has_meaningful_changes", return_value=True),
        patch("mcloop.main._has_uncommitted_changes", return_value=False),
        patch("mcloop.main._commit", mock_commit),
        patch("mcloop.main._push_or_die"),
        patch("mcloop.main._checkpoint"),
        patch("mcloop.main._ensure_git"),
        patch("mcloop.main._kill_orphan_sessions"),
        patch("mcloop.main._snapshot_worktree", return_value=([], [])),
        patch("mcloop.main._changed_files", return_value=[]),
        patch("mcloop.main._worktree_status", return_value=""),
        patch("mcloop.main._stage_safe"),
        patch("mcloop.main.ensure_conftest_guard", return_value=False),
        patch("mcloop.main.ensure_pytest_optimizations", return_value=False),
        patch("mcloop.main.validate_project_dependencies"),
        patch("mcloop.main.reconcile_pending"),
        patch("mcloop.main.notify"),
        patch("mcloop.main._purge_all_reviews"),
        patch("mcloop.main._cleanup_stale_reviews"),
        patch("mcloop.main._run_audit_fix_cycle"),
        patch("mcloop.main.run_autofix"),
        patch("mcloop.main._maybe_auto_wrap"),
        patch("mcloop.main._reinject_wrappers"),
        patch("mcloop.main.handle_sync"),
        patch("mcloop.main._check_user_input", return_value=None),
        _summary_patch,
    ]

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        run_loop(plan_md, max_retries=1, no_audit=True)


def test_run_loop_surfaces_task_id_in_stdout_and_commit_message(
    _canonical_repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The task-start banner, checkpoint marker, and completed-list line
    all show ``[T-NNNNNN]`` alongside the description, and the commit
    message body passed to ``_commit`` carries the ``[T-NNNNNN]``
    prefix."""
    mock_commit = MagicMock(return_value="abc123")
    _drive_one_success(_canonical_repo, mock_commit=mock_commit)

    captured = capsys.readouterr()
    combined = captured.out + captured.err

    assert re.search(r"\[T-000123\][^\n]*write a hello file", combined), (
        "task-start banner / completed line must show "
        f"'[T-000123] ... write a hello file', got:\n{combined!r}"
    )

    assert mock_commit.called, "_commit was never called"
    commit_msg = mock_commit.call_args.args[1]
    assert commit_msg.startswith("[T-000123] "), (
        f"commit message must start with '[T-000123] ', got: {commit_msg!r}"
    )
    assert "write a hello file" in commit_msg


def test_run_loop_surfaces_task_id_in_run_summary(
    _canonical_repo: Path,
) -> None:
    """The TaskEntry persisted in the run summary carries task_id as a
    first-class field, not fused into ``text``."""
    captured_summaries: list = []
    _drive_one_success(
        _canonical_repo,
        mock_commit=MagicMock(return_value="abc123"),
        capture_summary_into=captured_summaries,
    )

    assert captured_summaries, "write_run_summary was never called"
    summary = captured_summaries[-1]
    task_entries = [e for e in summary.tasks if e.outcome == "success"]
    assert task_entries, "no success TaskEntry produced"
    entry = task_entries[0]
    assert entry.task_id == "T-000123", (
        f"TaskEntry.task_id must be the canonical id, got {entry.task_id!r}"
    )
    # And the text field stays CLEAN — the id is NOT fused into it.
    assert entry.text == "write a hello file", (
        f"TaskEntry.text must be the clean description (no id prefix), got {entry.text!r}"
    )
