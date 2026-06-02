"""Unit tests for the ``_refuse_nested_init`` guard in ``mcloop.git_ops``.

The guard fires inside ``_ensure_git`` to prevent ``git init`` from
creating a nested ``.git`` directory inside a uv workspace package, which
would shadow the workspace repository. The authoritative signal is a
``pyproject.toml`` declaring ``[tool.uv.workspace]`` at a strict ancestor
of the project directory -- ancestor names like ``packages`` are NOT
used, so the tests below cover both the true-positive case and several
false-positive shapes that must continue to proceed normally.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mcloop.checks import run_checks
from mcloop.git_ops import (
    _changed_files,
    _changed_files_since,
    _committed_files,
    _committed_files_since,
    _ensure_git,
    _get_committed_diff,
    _get_git_hash,
    _read_task_baseline,
    _snapshot_worktree,
    _worktree_status,
    _write_task_baseline,
)


def _init_repo(root: Path) -> None:
    """Initialize a git repo at *root* with a baseline commit."""
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=root,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    (root / "README.md").write_text("# baseline\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "baseline"],
        cwd=root,
        check=True,
    )


def test_ensure_git_standalone_no_pyproject_creates_repo(tmp_path):
    """No .git and no workspace pyproject anywhere -> proceeds with git init."""
    project = tmp_path / "standalone"
    project.mkdir()

    with (
        patch("mcloop.git_ops._find_ancestor_git", return_value=None),
        patch("mcloop.git_ops._find_uv_workspace_ancestor", return_value=None),
        patch("mcloop.git_ops.subprocess.run") as mock_run,
        patch("mcloop.git_ops.notify"),
    ):
        mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
        _ensure_git(project)

    # First subprocess call must be ``git init`` -- the guard did not fire
    # and ``_ensure_git`` proceeded into its normal init path.
    assert mock_run.call_args_list, "expected git init to be invoked"
    first_call_args = mock_run.call_args_list[0].args[0]
    assert first_call_args == ["git", "init"]


def test_ensure_git_existing_git_returns_early(tmp_path):
    """Existing .git directory -> guard passes, then returns without git init."""
    project = tmp_path / "with_git"
    project.mkdir()
    (project / ".git").mkdir()

    with (
        patch("mcloop.git_ops.subprocess.run") as mock_run,
        patch("mcloop.git_ops.notify"),
    ):
        _ensure_git(project)

    mock_run.assert_not_called()


def test_ensure_git_consolidated_layout_returns_early(tmp_path):
    """workspace/.git exists, project_dir is workspace/packages/mcloop.

    No nested ``.git`` is created and ``git init`` is not invoked --
    the ancestor walk in ``_ensure_git`` finds ``workspace/.git`` and
    returns early.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    project = workspace / "packages" / "mcloop"
    project.mkdir(parents=True)

    with (
        patch("mcloop.git_ops.subprocess.run") as mock_run,
        patch("mcloop.git_ops.notify"),
    ):
        _ensure_git(project)

    mock_run.assert_not_called()
    assert not (project / ".git").exists()


def test_ensure_git_ancestor_dotgit_file_returns_early(tmp_path):
    """``.git`` as a FILE at an ancestor (worktree layout) also returns early."""
    workspace = tmp_path / "wt_workspace"
    workspace.mkdir()
    (workspace / ".git").write_text("gitdir: /some/other/path\n")
    project = workspace / "packages" / "mcloop"
    project.mkdir(parents=True)

    with (
        patch("mcloop.git_ops.subprocess.run") as mock_run,
        patch("mcloop.git_ops.notify"),
    ):
        _ensure_git(project)

    mock_run.assert_not_called()
    assert not (project / ".git").exists()


def test_ensure_git_blocks_inside_uv_workspace_package(tmp_path):
    """Defense-in-depth: uv workspace ancestor with no ``.git`` anywhere
    up the tree -> SystemExit(1), no nested ``.git`` created.

    With the ancestor-walk in ``_ensure_git``, a consolidated workspace
    with a real ``.git`` returns early before reaching the guard. This
    test deliberately omits ``workspace/.git`` so the guard path is
    exercised; it is the scenario the guard is designed to backstop
    (a workspace whose git repo has not yet been initialized).

    Layout::

        tmp/bob/                       <- workspace root (no .git)
            pyproject.toml             <- declares [tool.uv.workspace]
            packages/orchestra/        <- project_dir (no .git)
    """
    workspace = tmp_path / "bob"
    workspace.mkdir()
    (workspace / "pyproject.toml").write_text(
        "[project]\nname = 'bob'\n\n[tool.uv.workspace]\nmembers = ['packages/*']\n"
    )
    package = workspace / "packages" / "orchestra"
    package.mkdir(parents=True)

    with (
        patch("mcloop.git_ops._find_ancestor_git", return_value=None),
        patch("mcloop.git_ops._find_uv_workspace_ancestor", return_value=workspace.resolve()),
        patch("mcloop.git_ops.subprocess.run") as mock_run,
        patch("mcloop.git_ops.notify") as mock_notify,
    ):
        with pytest.raises(SystemExit) as exc_info:
            _ensure_git(package)

    assert exc_info.value.code == 1
    mock_run.assert_not_called()
    assert not (package / ".git").exists()
    # Error notification fires with level="error" and names the workspace root.
    mock_notify.assert_called_once()
    msg = mock_notify.call_args.args[0]
    assert mock_notify.call_args.kwargs.get("level") == "error"
    assert str(workspace.resolve()) in msg


def test_ensure_git_packages_dir_without_workspace_pyproject_does_not_block(tmp_path):
    """A standalone repo whose layout happens to include ``packages/`` is
    not a uv workspace -- the guard must not block on the directory name.
    """
    project = tmp_path / "myproj" / "packages" / "inner"
    project.mkdir(parents=True)

    with (
        patch("mcloop.git_ops._find_ancestor_git", return_value=None),
        patch("mcloop.git_ops._find_uv_workspace_ancestor", return_value=None),
        patch("mcloop.git_ops.subprocess.run") as mock_run,
        patch("mcloop.git_ops.notify"),
    ):
        mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
        _ensure_git(project)

    assert mock_run.call_args_list, "expected git init to be invoked"
    assert mock_run.call_args_list[0].args[0] == ["git", "init"]


def test_ensure_git_ancestor_pyproject_without_workspace_table_does_not_block(tmp_path):
    """An ancestor ``pyproject.toml`` that does not declare
    ``[tool.uv.workspace]`` is not a workspace signal -- only the
    workspace table itself triggers the guard.
    """
    parent = tmp_path / "plain_parent"
    parent.mkdir()
    (parent / "pyproject.toml").write_text(
        "[project]\nname = 'plain'\n\n[tool.ruff]\nselect = ['E']\n"
    )
    project = parent / "sub"
    project.mkdir()

    with (
        patch("mcloop.git_ops._find_ancestor_git", return_value=None),
        patch("mcloop.git_ops._find_uv_workspace_ancestor", return_value=None),
        patch("mcloop.git_ops.subprocess.run") as mock_run,
        patch("mcloop.git_ops.notify"),
    ):
        mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
        _ensure_git(project)

    assert mock_run.call_args_list, "expected git init to be invoked"
    assert mock_run.call_args_list[0].args[0] == ["git", "init"]


# ── --relative path emission (T-000383) ──────────────────────────────
#
# These tests exercise real git via tmp_path repos (no mocks) because the
# entire point of the change is the interaction with git's path emission.
# Each test sets up either a standalone repo (cwd == repo root) or a
# consolidated workspace layout (cwd == workspace/packages/mcloop, repo
# root == workspace) and verifies the helpers return paths relative to
# the cwd in both layouts.


def test_changed_files_standalone_unchanged(tmp_path):
    """Standalone layout: ``_changed_files`` is unchanged by ``--relative``.

    When cwd is already the repo root, ``git diff --relative`` is a no-op
    and the returned list matches the prior behavior: a single
    file-relative path for the modified file.
    """
    _init_repo(tmp_path)
    (tmp_path / "foo.py").write_text("x = 1\n")

    files = _changed_files(tmp_path)

    assert files == ["foo.py"]


def test_changed_files_consolidated_layout_returns_package_relative(tmp_path):
    """Consolidated layout: ``_changed_files`` returns package-relative paths.

    Layout::

        tmp/workspace/                      <- repo root (.git here)
            packages/mcloop/foo.py          <- modified file; cwd is here

    The helper must return ``["foo.py"]``, not
    ``["packages/mcloop/foo.py"]`` -- callers like ``run_checks`` resolve
    relative paths against the subprocess cwd, so a workspace-rooted
    path would resolve to a nonexistent file.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    package = workspace / "packages" / "mcloop"
    package.mkdir(parents=True)
    _init_repo(workspace)
    # Create + commit a baseline file inside the package so we can modify it.
    (package / "foo.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "seed package"],
        cwd=workspace,
        check=True,
    )
    (package / "foo.py").write_text("x = 2\n")

    files = _changed_files(package)

    assert files == ["foo.py"]


def test_worktree_status_consolidated_layout_strips_prefix(tmp_path):
    """Consolidated layout: ``_worktree_status`` strips the package prefix.

    ``git status --porcelain`` does not accept ``--relative`` and emits
    repo-root paths. The helper must strip the cwd prefix so the lines
    reference package-relative paths -- otherwise pre/post-check
    set comparison in ``_run_task`` would compare against
    ``_changed_files`` (package-relative) and mismatch.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    package = workspace / "packages" / "mcloop"
    package.mkdir(parents=True)
    _init_repo(workspace)
    (package / "foo.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "seed package"],
        cwd=workspace,
        check=True,
    )
    (package / "foo.py").write_text("x = 2\n")
    # Untracked file too, to cover the ?? prefix.
    (package / "bar.py").write_text("y = 3\n")

    status = _worktree_status(package)
    lines = status.splitlines()

    # Every emitted path is package-relative -- no occurrence of the
    # workspace-rooted prefix anywhere in the output.
    assert "packages/mcloop/" not in status
    paths = [line[3:] for line in lines]
    assert "foo.py" in paths
    assert "bar.py" in paths


def test_changed_files_round_trip_resolves_at_cwd(tmp_path):
    """Round-trip: paths from ``_changed_files`` resolve under cwd.

    In the consolidated layout, the file returned by ``_changed_files``
    must exist at ``cwd / path`` -- this is the contract ``run_checks``,
    ``handle_sync``, and the batch rollback depend on. The test uses a
    Python source file so ``run_checks`` exercises the scoped linter
    path (``ruff check`` over the changed-file list), confirming the
    full callee chain treats the relative path correctly.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    package = workspace / "packages" / "mcloop"
    package.mkdir(parents=True)
    # Minimal pyproject.toml so checks.py's detection picks up ruff.
    (package / "pyproject.toml").write_text(
        "[tool.ruff]\nselect = ['E']\n",
    )
    _init_repo(workspace)
    (package / "foo.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "seed package"],
        cwd=workspace,
        check=True,
    )
    (package / "foo.py").write_text("x = 2\n")

    files = _changed_files(package)

    assert files == ["foo.py"]
    # Concrete round-trip: the path resolves to an existing file under cwd.
    assert (package / files[0]).is_file()
    # And run_checks invoked with that file does not raise / can be
    # invoked end-to-end; the result is incidental (ruff may not be
    # installed in this test env) but the path resolution path is what
    # we are guarding against regression.
    result = run_checks(package, changed_files=files)
    assert result is not None


def test_committed_files_consolidated_layout_returns_package_relative(tmp_path):
    """``_committed_files`` emits package-relative paths via ``--relative``."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    package = workspace / "packages" / "mcloop"
    package.mkdir(parents=True)
    _init_repo(workspace)
    (package / "foo.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "add foo"],
        cwd=workspace,
        check=True,
    )
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    files = _committed_files(package, sha)

    assert files == ["foo.py"]


def test_get_committed_diff_consolidated_layout_uses_relative_paths(tmp_path):
    """``_get_committed_diff`` emits diff headers with package-relative paths.

    The presence of ``--relative`` strips the workspace prefix from the
    ``a/`` and ``b/`` paths in the diff header, so downstream tools that
    parse the diff see paths rooted at the package.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    package = workspace / "packages" / "mcloop"
    package.mkdir(parents=True)
    _init_repo(workspace)
    (package / "foo.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "seed foo"],
        cwd=workspace,
        check=True,
    )
    (package / "foo.py").write_text("x = 2\n")
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "update foo"],
        cwd=workspace,
        check=True,
    )
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    diff = _get_committed_diff(package, sha)

    assert "packages/mcloop/foo.py" not in diff
    assert "foo.py" in diff


def test_snapshot_worktree_consolidated_layout_returns_package_relative(tmp_path):
    """``_snapshot_worktree`` returns package-relative paths in both lists."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    package = workspace / "packages" / "mcloop"
    package.mkdir(parents=True)
    _init_repo(workspace)
    (package / "foo.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "seed foo"],
        cwd=workspace,
        check=True,
    )
    (package / "foo.py").write_text("x = 2\n")
    (package / "bar.py").write_text("y = 3\n")

    modified, untracked = _snapshot_worktree(package)

    assert modified == ["foo.py"]
    assert untracked == ["bar.py"]


# ── _committed_files_since (T-000001) ─────────────────────────────────


def test_committed_files_since_empty_base_returns_empty(tmp_path):
    """An empty base SHA short-circuits to an empty list."""
    _init_repo(tmp_path)
    assert _committed_files_since(tmp_path, "") == []


def test_committed_files_since_no_repo_returns_empty(tmp_path):
    """No git repo -> empty list, no subprocess call."""
    assert _committed_files_since(tmp_path, "abc123") == []


def test_committed_files_since_head_at_base_returns_empty(tmp_path):
    """When HEAD has not advanced past the base SHA, nothing has landed."""
    _init_repo(tmp_path)
    head = _get_git_hash(tmp_path)
    assert _committed_files_since(tmp_path, head) == []


def test_committed_files_since_returns_cumulative_meaningful(tmp_path):
    """Cumulative diff across multiple commits, skipping metadata paths."""
    _init_repo(tmp_path)
    base = _get_git_hash(tmp_path)
    # First commit lands real work.
    (tmp_path / "src.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "mcloop: checkpoint"],
        cwd=tmp_path,
        check=True,
    )
    # Second commit only modifies a metadata path; this must be filtered.
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "run.log").write_text("noise\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "log noise"],
        cwd=tmp_path,
        check=True,
    )
    # Third commit edits the same source file again.
    (tmp_path / "src.py").write_text("x = 2\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "follow-up"],
        cwd=tmp_path,
        check=True,
    )

    files = _committed_files_since(tmp_path, base)

    # Deduplicated, metadata excluded, package-relative.
    assert files == ["src.py"]


def test_committed_files_since_skips_plan_md_and_mcloop(tmp_path):
    """PLAN.md and .mcloop/ touched in commits are still treated as noise."""
    _init_repo(tmp_path)
    base = _get_git_hash(tmp_path)
    (tmp_path / "PLAN.md").write_text("- [ ] task\n")
    (tmp_path / ".mcloop").mkdir()
    (tmp_path / ".mcloop" / "state.json").write_text("{}\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "metadata only"],
        cwd=tmp_path,
        check=True,
    )

    assert _committed_files_since(tmp_path, base) == []


def test_changed_files_since_empty_base_returns_none(tmp_path):
    """An empty base SHA cannot resolve a set -> None (fail-closed signal)."""
    _init_repo(tmp_path)
    assert _changed_files_since(tmp_path, "") is None


def test_changed_files_since_no_repo_returns_none(tmp_path):
    """No git repo -> None, the fail-closed signal for the adapter."""
    assert _changed_files_since(tmp_path, "abc123") is None


def test_changed_files_since_clean_tree_returns_empty_list(tmp_path):
    """Baseline resolves but nothing changed -> [] (distinct from None)."""
    _init_repo(tmp_path)
    head = _get_git_hash(tmp_path)
    assert _changed_files_since(tmp_path, head) == []


def test_changed_files_since_includes_uncommitted_working_tree(tmp_path):
    """Edits the agent has not committed yet are captured, metadata filtered."""
    _init_repo(tmp_path)
    base = _get_git_hash(tmp_path)
    # Uncommitted modification to a tracked file.
    (tmp_path / "src.py").write_text("x = 1\n")
    # Untracked new file.
    (tmp_path / "new.py").write_text("y = 2\n")
    # Metadata noise that must be filtered.
    (tmp_path / "PLAN.md").write_text("- [ ] task\n")
    (tmp_path / ".mcloop").mkdir()
    (tmp_path / ".mcloop" / "state.json").write_text("{}\n")

    files = _changed_files_since(tmp_path, base)

    assert files is not None
    assert sorted(files) == ["new.py", "src.py"]


def test_task_baseline_round_trips(tmp_path):
    """A written baseline reads back stripped; absent file reads as empty."""
    assert _read_task_baseline(tmp_path) == ""
    _write_task_baseline(tmp_path, "deadbeef")
    assert _read_task_baseline(tmp_path) == "deadbeef"
    # Empty SHA is a no-op (does not clobber an existing baseline).
    _write_task_baseline(tmp_path, "")
    assert _read_task_baseline(tmp_path) == "deadbeef"


def test_committed_files_since_consolidated_layout_is_package_relative(tmp_path):
    """Paths are emitted relative to the subprocess cwd, not the repo root."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    package = workspace / "packages" / "mcloop"
    package.mkdir(parents=True)
    _init_repo(workspace)
    base = _get_git_hash(package)
    (package / "foo.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "add foo"],
        cwd=workspace,
        check=True,
    )

    files = _committed_files_since(package, base)

    assert files == ["foo.py"]
