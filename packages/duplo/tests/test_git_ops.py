"""Tests for ``duplo.git_ops.commit_artifact`` — per-phase git commits."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from duplo.git_ops import commit_artifact, reset_logged_not_git_repo


@pytest.fixture(autouse=True)
def _reset_logged() -> None:
    reset_logged_not_git_repo()


def _init_repo(tmp_path: Path) -> None:
    subprocess.run(
        ["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "t@example.invalid"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "test"], cwd=tmp_path, check=True
    )
    # An initial commit so HEAD exists.
    seed = tmp_path / "seed.txt"
    seed.write_text("seed\n")
    subprocess.run(["git", "add", "seed.txt"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "seed"], cwd=tmp_path, check=True
    )


def test_commit_lands_in_a_real_git_repo(tmp_path: Path) -> None:
    """Happy path: tmp git repo, write artifact, commit_artifact lands
    a commit with the expected message shape."""
    _init_repo(tmp_path)
    artifact = tmp_path / "PLAN.md"
    artifact.write_text("# plan v1\n")

    ok = commit_artifact(artifact, "save_plan", push=False)

    assert ok is True
    log = subprocess.run(
        ["git", "log", "-1", "--pretty=%s"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert log == "duplo save_plan: PLAN.md"


def test_non_git_dir_logs_once_and_returns_false(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Outside a git repo: silent skip, log once, return False without raising."""
    artifact = tmp_path / "PLAN.md"
    artifact.write_text("# plan\n")

    ok = commit_artifact(artifact, "save_plan", push=False)
    assert ok is False
    err = capsys.readouterr().err
    assert "not in git repo" in err
    assert "skipping commits" in err

    # Second call must NOT re-log.
    ok2 = commit_artifact(artifact, "save_plan", push=False)
    assert ok2 is False
    err2 = capsys.readouterr().err
    assert "not in git repo" not in err2


def test_commit_hook_rejection_surfaces_error_and_returns_false(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A pre-commit hook that rejects: error surfaced to stderr, return
    False, no raise. Critically the caller's phase result is preserved."""
    _init_repo(tmp_path)
    hooks_dir = tmp_path / ".git" / "hooks"
    pre_commit = hooks_dir / "pre-commit"
    pre_commit.write_text("#!/bin/sh\necho 'rejected by hook' >&2\nexit 1\n")
    pre_commit.chmod(0o755)

    artifact = tmp_path / "PLAN.md"
    artifact.write_text("# plan\n")

    ok = commit_artifact(artifact, "save_plan", push=False)

    assert ok is False
    err = capsys.readouterr().err
    assert "git commit failed" in err
    assert "PLAN.md" in err
    # The artifact must still be on disk; the helper only skipped the git side.
    assert artifact.read_text() == "# plan\n"
