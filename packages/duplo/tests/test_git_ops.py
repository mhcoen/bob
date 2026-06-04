"""Tests for ``duplo.git_ops.commit_artifact`` — per-phase git automation.

Local git runs for real against a tmp repo; every ``gh`` call and every
``git push`` is intercepted so no test ever touches real GitHub.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from duplo import git_ops
from duplo.git_ops import commit_artifact, reset_logged_not_git_repo

_REAL_RUN = subprocess.run


@pytest.fixture(autouse=True)
def _real_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """Undo the shared conftest's global ``git``/``gh`` subprocess stub.

    The duplo conftest fakes every ``git``/``gh`` call so pipeline tests
    can't touch real GitHub. These tests, by contrast, run local git for
    real against a tmp repo and intercept ``gh``/``git push`` themselves
    (via ``_FakeGit`` or ``push=False``). This fixture is module-local and
    autouse, so it is set up after the conftest patch and restores the
    real ``subprocess.run`` for the duration of each test here; tests that
    call ``_install`` then layer ``_FakeGit`` on top as before.
    """
    monkeypatch.setattr(subprocess, "run", _REAL_RUN)


@pytest.fixture(autouse=True)
def _force_enable_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force git_ops' remote path on for this module's remote-path tests.

    ``git_ops._remote_disabled`` defaults to suppressing all remote work
    under pytest (the hard safety switch that stopped tmp-dir-named repos
    from being created on real GitHub). These tests deliberately exercise
    that remote path with ``gh``/``git push`` intercepted by ``_FakeGit``,
    so they explicitly force it on via ``DUPLO_NO_GITHUB=0``. No real
    GitHub call can escape because the fake intercepts every ``gh`` and
    every ``git push``.
    """
    monkeypatch.setenv("DUPLO_NO_GITHUB", "0")


@pytest.fixture(autouse=True)
def _reset_logged() -> None:
    reset_logged_not_git_repo()


@pytest.fixture(autouse=True)
def _git_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give freshly ``git init``-ed repos a commit identity without
    depending on the machine's global git config."""
    monkeypatch.setenv("GIT_AUTHOR_NAME", "test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@example.invalid")


class _FakeGit:
    """Intercept ``gh`` and ``git push``; delegate all other git to real.

    Records every intercepted command in ``self.calls``. Behavior is
    tunable per test via the constructor flags.
    """

    def __init__(
        self,
        *,
        gh_authed: bool = True,
        create_outcome: str = "created",  # "created" | "exists" | "failed"
        push_ok: bool = True,
        ssh_url: str | None = "git@github.com:me/proj.git",
    ) -> None:
        self.gh_authed = gh_authed
        self.create_outcome = create_outcome
        self.push_ok = push_ok
        self.ssh_url = ssh_url
        self.calls: list[list[str]] = []

    def __call__(self, cmd, *args, **kwargs):  # noqa: ANN001
        self.calls.append(list(cmd))
        if cmd[0] == "gh":
            return self._gh(cmd, **kwargs)
        if cmd[:2] == ["git", "push"]:
            return self._completed(cmd, 0 if self.push_ok else 1, err="push rejected")
        return _REAL_RUN(cmd, *args, **kwargs)

    def _gh(self, cmd, **kwargs):  # noqa: ANN001
        if cmd[1] == "auth":
            return self._completed(cmd, 0 if self.gh_authed else 1)
        if cmd[1] == "repo" and cmd[2] == "create":
            if self.create_outcome == "created":
                # gh repo create --push wires origin + pushes in one shot;
                # emulate the origin wiring with real git so idempotency
                # (no second create) holds on the next call.
                _REAL_RUN(
                    ["git", "remote", "add", "origin", "file:///tmp/fake-origin.git"],
                    cwd=kwargs.get("cwd"),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                return self._completed(cmd, 0)
            if self.create_outcome == "exists":
                return self._completed(cmd, 1, err="GraphQL: Name already exists on this account")
            return self._completed(cmd, 1, err="some other gh failure")
        if cmd[1] == "repo" and cmd[2] == "view":
            return self._completed(cmd, 0, out=(self.ssh_url or "") + "\n")
        return self._completed(cmd, 0)

    @staticmethod
    def _completed(cmd, code, *, out: str = "", err: str = ""):  # noqa: ANN001
        return subprocess.CompletedProcess(cmd, code, out, err)


def _install(monkeypatch: pytest.MonkeyPatch, fake: _FakeGit) -> None:
    monkeypatch.setattr(git_ops.subprocess, "run", fake)


def _init_repo(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.invalid"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=tmp_path, check=True)
    seed = tmp_path / "seed.txt"
    seed.write_text("seed\n")
    subprocess.run(["git", "add", "seed.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=tmp_path, check=True)


def _last_subject(cwd: Path) -> str:
    return subprocess.run(
        ["git", "log", "-1", "--pretty=%s"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def test_commit_lands_in_a_real_git_repo(tmp_path: Path) -> None:
    """Happy local path: existing repo, commit lands with expected message."""
    _init_repo(tmp_path)
    artifact = tmp_path / "PLAN.md"
    artifact.write_text("# plan v1\n")

    ok = commit_artifact(artifact, "save_plan", push=False)

    assert ok is True
    assert _last_subject(tmp_path) == "duplo save_plan: PLAN.md"


def test_no_git_inits_repo_and_creates_github_on_first_call_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No .git: git init + branch main + commit; gh repo create invoked
    once with the expected args; the second call skips create and pushes."""
    fake = _FakeGit(gh_authed=True, create_outcome="created", push_ok=True)
    _install(monkeypatch, fake)

    a1 = tmp_path / "PLAN.md"
    a1.write_text("# plan\n")
    assert commit_artifact(a1, "save_plan_header") is True

    # Repo was initialized on branch main and the commit landed.
    assert (tmp_path / ".git").is_dir()
    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert branch == "main"
    assert _last_subject(tmp_path) == "duplo save_plan_header: PLAN.md"

    create_calls = [c for c in fake.calls if c[:3] == ["gh", "repo", "create"]]
    assert len(create_calls) == 1
    assert create_calls[0] == [
        "gh",
        "repo",
        "create",
        tmp_path.name,
        "--source=.",
        "--remote=origin",
        "--private",
        "--push",
    ]

    # Second call: origin now exists -> no second create, just push.
    a2 = tmp_path / ".duplo" / "duplo.json"
    a2.parent.mkdir()
    a2.write_text("{}\n")
    assert commit_artifact(a2, "complete_phase_0") is True

    create_calls = [c for c in fake.calls if c[:3] == ["gh", "repo", "create"]]
    assert len(create_calls) == 1  # still only the first call created
    push_calls = [c for c in fake.calls if c[:2] == ["git", "push"]]
    assert push_calls, "second call should have pushed to the existing origin"


def test_existing_repo_with_origin_pushes_without_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """.git + origin already present: no init, no gh create, commit + push."""
    _init_repo(tmp_path)
    subprocess.run(
        ["git", "remote", "add", "origin", "file:///tmp/already.git"],
        cwd=tmp_path,
        check=True,
    )
    fake = _FakeGit(push_ok=True)
    _install(monkeypatch, fake)

    artifact = tmp_path / "PLAN.md"
    artifact.write_text("# plan\n")
    assert commit_artifact(artifact, "save_plan") is True

    assert not [c for c in fake.calls if c[:3] == ["gh", "repo", "create"]]
    assert [c for c in fake.calls if c[:2] == ["git", "push"]]


def test_gh_repo_already_exists_wires_remote_from_ssh_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """gh repo create reports exists -> wire origin from gh repo view sshUrl."""
    fake = _FakeGit(
        gh_authed=True,
        create_outcome="exists",
        push_ok=True,
        ssh_url="git@github.com:me/clipbar.git",
    )
    _install(monkeypatch, fake)

    artifact = tmp_path / "PLAN.md"
    artifact.write_text("# plan\n")
    assert commit_artifact(artifact, "save_plan") is True

    # Fell back to viewing the existing repo and wiring its ssh url.
    assert [c for c in fake.calls if c[:3] == ["gh", "repo", "view"]]
    origin = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert origin == "git@github.com:me/clipbar.git"
    assert [c for c in fake.calls if c[:2] == ["git", "push"]]


def test_gh_unauthenticated_commits_locally_and_warns_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """gh auth nonzero: local commit lands, no abort, single warning, no create."""
    fake = _FakeGit(gh_authed=False)
    _install(monkeypatch, fake)

    a1 = tmp_path / "PLAN.md"
    a1.write_text("# plan\n")
    assert commit_artifact(a1, "save_plan") is True
    assert _last_subject(tmp_path) == "duplo save_plan: PLAN.md"

    err = capsys.readouterr().err
    assert "gh unavailable/unauthenticated" in err
    assert "committed locally" in err
    assert not [c for c in fake.calls if c[:3] == ["gh", "repo", "create"]]

    # Warn-once: a second call in the same run does not repeat the warning.
    a2 = tmp_path / "NOTES.md"
    a2.write_text("notes\n")
    assert commit_artifact(a2, "save_notes") is True
    assert "gh unavailable/unauthenticated" not in capsys.readouterr().err


def test_push_failure_is_a_warning_not_a_commit_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Push fails but commit succeeded: push-specific warning, returns True,
    and it is NOT reported as a commit failure."""
    _init_repo(tmp_path)
    subprocess.run(
        ["git", "remote", "add", "origin", "file:///tmp/already.git"],
        cwd=tmp_path,
        check=True,
    )
    fake = _FakeGit(push_ok=False)
    _install(monkeypatch, fake)

    artifact = tmp_path / "PLAN.md"
    artifact.write_text("# plan\n")
    ok = commit_artifact(artifact, "save_plan")

    assert ok is True  # commit landed locally
    assert _last_subject(tmp_path) == "duplo save_plan: PLAN.md"
    err = capsys.readouterr().err
    assert "git push failed" in err
    assert "git commit failed" not in err


def test_commit_hook_rejection_surfaces_commit_error_and_returns_false(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A pre-commit hook that rejects: commit-specific error, return False,
    artifact still on disk, phase result preserved."""
    _init_repo(tmp_path)
    pre_commit = tmp_path / ".git" / "hooks" / "pre-commit"
    pre_commit.write_text("#!/bin/sh\necho 'rejected by hook' >&2\nexit 1\n")
    pre_commit.chmod(0o755)

    artifact = tmp_path / "PLAN.md"
    artifact.write_text("# plan\n")

    ok = commit_artifact(artifact, "save_plan", push=False)

    assert ok is False
    err = capsys.readouterr().err
    assert "git commit failed" in err
    assert "PLAN.md" in err
    assert artifact.read_text() == "# plan\n"


class TestRemoteKillSwitch:
    """``_remote_disabled`` honors only the explicit ``DUPLO_NO_GITHUB``
    operational switch — production code does not sniff pytest. The test
    harness (tests/conftest.py) exports it to keep tmp-dir repos off real
    GitHub; real ``duplo`` runs never set it, so their remote behavior is
    intact."""

    def test_truthy_disables_remote(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for value in ("1", "true", "yes", "on"):
            monkeypatch.setenv("DUPLO_NO_GITHUB", value)
            assert git_ops._remote_disabled() is True

    def test_falsy_or_unset_enables_remote(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for value in ("0", "false", "no", ""):
            monkeypatch.setenv("DUPLO_NO_GITHUB", value)
            assert git_ops._remote_disabled() is False
        monkeypatch.delenv("DUPLO_NO_GITHUB", raising=False)
        assert git_ops._remote_disabled() is False

    def test_commit_never_calls_gh_when_disabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DUPLO_NO_GITHUB", "1")
        calls: list[list[str]] = []

        def _record(cmd, *args, **kwargs):  # noqa: ANN001
            calls.append(list(cmd))
            if cmd[0] == "gh":
                raise AssertionError("gh must never run when remote is disabled")
            return _REAL_RUN(cmd, *args, **kwargs)

        monkeypatch.setattr(git_ops.subprocess, "run", _record)

        artifact = tmp_path / "PLAN.md"
        artifact.write_text("# plan\n")
        # push=True (the production default) must still commit locally and
        # must NOT reach gh repo create.
        ok = commit_artifact(artifact, "save_plan", push=True)

        assert ok is True
        # The local commit landed...
        assert _last_subject(tmp_path) == "duplo save_plan: PLAN.md"
        # ...and gh was never invoked.
        assert not any(c and c[0] == "gh" for c in calls)
