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

from unittest.mock import MagicMock, patch

import pytest

from mcloop.git_ops import _ensure_git


def test_ensure_git_standalone_no_pyproject_creates_repo(tmp_path):
    """No .git and no workspace pyproject anywhere -> proceeds with git init."""
    project = tmp_path / "standalone"
    project.mkdir()

    with (
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
        patch("mcloop.git_ops.subprocess.run") as mock_run,
        patch("mcloop.git_ops.notify"),
    ):
        mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
        _ensure_git(project)

    assert mock_run.call_args_list, "expected git init to be invoked"
    assert mock_run.call_args_list[0].args[0] == ["git", "init"]
