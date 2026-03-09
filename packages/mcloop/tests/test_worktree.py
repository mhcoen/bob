"""Tests for mcloop.worktree."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from mcloop import worktree


class TestSlugify:
    def test_simple(self):
        assert worktree._slugify("Fix crash on startup") == "fix-crash-on-startup"

    def test_special_chars(self):
        assert worktree._slugify("Bug #42: can't open file") == "bug-42-can-t-open-file"

    def test_truncation(self):
        long = "a" * 100
        result = worktree._slugify(long)
        assert len(result) <= 60

    def test_empty_after_strip(self):
        assert worktree._slugify("!!!") == ""

    def test_trailing_hyphens_after_truncation(self):
        # 58 a's + space + long word -> slug gets cut, trailing hyphen stripped
        text = "a" * 58 + " bbbbb"
        result = worktree._slugify(text)
        assert not result.endswith("-")


class TestCurrentBranch:
    def test_returns_branch(self):
        result = subprocess.CompletedProcess(args=[], returncode=0, stdout="main\n")
        with patch.object(worktree, "_run_git", return_value=result):
            assert worktree.current_branch() == "main"

    def test_not_a_repo(self):
        result = subprocess.CompletedProcess(
            args=[], returncode=128, stdout="", stderr="fatal: not a git repo"
        )
        with patch.object(worktree, "_run_git", return_value=result):
            with pytest.raises(RuntimeError, match="Not a git repository"):
                worktree.current_branch()


class TestCreate:
    def _mock_git(self, branch="main", root="/repo"):
        """Return a side_effect function for _run_git."""

        def side_effect(*args, **kwargs):
            cmd = args[0] if args else ""
            if cmd == "rev-parse" and "--abbrev-ref" in args:
                return subprocess.CompletedProcess(args=[], returncode=0, stdout=f"{branch}\n")
            if cmd == "rev-parse" and "--show-toplevel" in args:
                return subprocess.CompletedProcess(args=[], returncode=0, stdout=f"{root}\n")
            if cmd == "worktree":
                return subprocess.CompletedProcess(args=[], returncode=0, stdout="")
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="")

        return side_effect

    def test_returns_path_and_branch(self):
        with patch.object(worktree, "_run_git", side_effect=self._mock_git()):
            path, branch = worktree.create("Fix crash on startup")
        assert branch == "investigate/fix-crash-on-startup"
        assert path == Path("/repo-investigate-fix-crash-on-startup")

    def test_empty_slug_raises(self):
        with pytest.raises(ValueError, match="empty slug"):
            worktree.create("!!!")

    def test_worktree_add_failure(self):
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            cmd = args[0] if args else ""
            if cmd == "rev-parse" and "--abbrev-ref" in args:
                return subprocess.CompletedProcess(args=[], returncode=0, stdout="main\n")
            if cmd == "rev-parse" and "--show-toplevel" in args:
                return subprocess.CompletedProcess(args=[], returncode=0, stdout="/repo\n")
            if cmd == "worktree":
                return subprocess.CompletedProcess(
                    args=[],
                    returncode=128,
                    stdout="",
                    stderr="fatal: branch already exists",
                )
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="")

        with patch.object(worktree, "_run_git", side_effect=side_effect):
            with pytest.raises(RuntimeError, match="Failed to create worktree"):
                worktree.create("some bug")


class TestExists:
    def test_exists_true(self):
        wts = [{"path": "/repo-investigate-fix-crash", "branch": "investigate/fix-crash"}]
        with patch.object(worktree, "list_worktrees", return_value=wts):
            assert worktree.exists("Fix crash") is True

    def test_exists_false(self):
        with patch.object(worktree, "list_worktrees", return_value=[]):
            assert worktree.exists("Fix crash") is False

    def test_empty_slug(self):
        assert worktree.exists("!!!") is False


class TestListWorktrees:
    def test_parses_porcelain(self):
        porcelain = (
            "worktree /repo\n"
            "HEAD abc123\n"
            "branch refs/heads/main\n"
            "\n"
            "worktree /repo-investigate-fix-crash\n"
            "HEAD def456\n"
            "branch refs/heads/investigate/fix-crash\n"
            "\n"
        )
        result = subprocess.CompletedProcess(args=[], returncode=0, stdout=porcelain)
        with patch.object(worktree, "_run_git", return_value=result):
            wts = worktree.list_worktrees()

        assert len(wts) == 1
        assert wts[0]["path"] == "/repo-investigate-fix-crash"
        assert wts[0]["branch"] == "investigate/fix-crash"
        assert wts[0]["commit"] == "def456"

    def test_filters_non_investigation(self):
        porcelain = "worktree /repo\nHEAD abc123\nbranch refs/heads/main\n\n"
        result = subprocess.CompletedProcess(args=[], returncode=0, stdout=porcelain)
        with patch.object(worktree, "_run_git", return_value=result):
            assert worktree.list_worktrees() == []

    def test_git_failure_returns_empty(self):
        result = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="error")
        with patch.object(worktree, "_run_git", return_value=result):
            assert worktree.list_worktrees() == []

    def test_no_trailing_blank_line(self):
        porcelain = (
            "worktree /repo-investigate-bug\nHEAD abc123\nbranch refs/heads/investigate/bug"
        )
        result = subprocess.CompletedProcess(args=[], returncode=0, stdout=porcelain)
        with patch.object(worktree, "_run_git", return_value=result):
            wts = worktree.list_worktrees()
        assert len(wts) == 1


class TestMerge:
    def test_successful_merge(self):
        result = subprocess.CompletedProcess(args=[], returncode=0, stdout="")
        with patch.object(worktree, "_run_git", return_value=result) as mock:
            worktree.merge("investigate/fix-crash")
        mock.assert_called_once_with("merge", "investigate/fix-crash", cwd=None)

    def test_not_investigation_branch(self):
        with pytest.raises(ValueError, match="Not an investigation branch"):
            worktree.merge("feature/foo")

    def test_merge_conflict(self):
        result = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="CONFLICT")
        with patch.object(worktree, "_run_git", return_value=result):
            with pytest.raises(RuntimeError, match="Merge failed"):
                worktree.merge("investigate/fix-crash")


class TestRemove:
    def test_removes_worktree_and_branch(self):
        wts = [{"path": "/repo-investigate-fix", "branch": "investigate/fix"}]
        calls = []

        def mock_git(*args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="")

        with patch.object(worktree, "list_worktrees", return_value=wts):
            with patch.object(worktree, "_run_git", side_effect=mock_git):
                worktree.remove("investigate/fix")

        # Should call worktree remove, then branch -d
        assert calls[0] == ("worktree", "remove", "/repo-investigate-fix")
        assert calls[1] == ("branch", "-d", "investigate/fix")

    def test_no_worktree_still_deletes_branch(self):
        """If worktree is already gone, still delete the branch."""
        calls = []

        def mock_git(*args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="")

        with patch.object(worktree, "list_worktrees", return_value=[]):
            with patch.object(worktree, "_run_git", side_effect=mock_git):
                worktree.remove("investigate/fix")

        assert len(calls) == 1
        assert calls[0] == ("branch", "-d", "investigate/fix")

    def test_not_investigation_branch(self):
        with pytest.raises(ValueError, match="Not an investigation branch"):
            worktree.remove("main")

    def test_worktree_remove_failure(self):
        wts = [{"path": "/repo-investigate-fix", "branch": "investigate/fix"}]
        result = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="error: dirty"
        )
        with patch.object(worktree, "list_worktrees", return_value=wts):
            with patch.object(worktree, "_run_git", return_value=result):
                with pytest.raises(RuntimeError, match="Failed to remove"):
                    worktree.remove("investigate/fix")
