"""Tests for mcloop.claude_md_sync."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mcloop.claude_md_check import SyncResult
from mcloop.claude_md_sync import _pending_path, handle_sync, reconcile_pending


def _setup_project(tmp_path: Path) -> Path:
    """Create minimal project structure for tests."""
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    (tmp_path / "CLAUDE.md").write_text("# Project\n")
    return tmp_path


class TestHandleSync:
    """Tests for handle_sync()."""

    def test_transient_failed_writes_pending(self, tmp_path):
        project = _setup_project(tmp_path)
        with (
            patch("mcloop.claude_md_sync.check_claude_md_freshness", return_value=False),
            patch("mcloop.claude_md_sync.auto_update_claude_md", return_value=SyncResult.TRANSIENT_FAILED),
        ):
            result = handle_sync(project, "abc1234def", task_label="1.6")

        assert result is SyncResult.TRANSIENT_FAILED
        pending = _pending_path(project)
        assert pending.exists()
        entry = json.loads(pending.read_text())
        assert entry["commit_sha"] == "abc1234def"
        assert entry["attempts"] == 1
        assert "timestamp" in entry

    def test_permanent_failed_does_not_write_pending(self, tmp_path):
        project = _setup_project(tmp_path)
        with (
            patch("mcloop.claude_md_sync.check_claude_md_freshness", return_value=False),
            patch("mcloop.claude_md_sync.auto_update_claude_md", return_value=SyncResult.PERMANENT_FAILED),
        ):
            result = handle_sync(project, "abc1234")

        assert result is SyncResult.PERMANENT_FAILED
        assert not _pending_path(project).exists()

    def test_no_work_does_not_write_pending(self, tmp_path):
        project = _setup_project(tmp_path)
        with patch("mcloop.claude_md_sync.check_claude_md_freshness", return_value=True):
            result = handle_sync(project, "abc1234")

        assert result is SyncResult.NO_WORK
        assert not _pending_path(project).exists()

    def test_ok_clears_existing_pending(self, tmp_path):
        project = _setup_project(tmp_path)
        pending = _pending_path(project)
        pending.write_text(json.dumps({"commit_sha": "old", "attempts": 1}))
        with (
            patch("mcloop.claude_md_sync.check_claude_md_freshness", return_value=False),
            patch("mcloop.claude_md_sync.auto_update_claude_md", return_value=SyncResult.OK),
        ):
            result = handle_sync(project, "new1234")

        assert result is SyncResult.OK
        assert not pending.exists()

    def test_cap_exceeded_halts_and_notifies(self, tmp_path):
        project = _setup_project(tmp_path)
        pending = _pending_path(project)
        pending.write_text(json.dumps({
            "commit_sha": "first_sha",
            "timestamp": "2026-04-11T00:00:00+00:00",
            "attempts": 1,
        }))

        with (
            patch("mcloop.claude_md_sync.check_claude_md_freshness", return_value=False),
            patch("mcloop.claude_md_sync.auto_update_claude_md", return_value=SyncResult.TRANSIENT_FAILED),
            patch("mcloop.claude_md_sync.notify") as mock_notify,
        ):
            with pytest.raises(SystemExit) as exc_info:
                handle_sync(project, "second_sha", task_label="1.7")

        assert "2 commits behind" in str(exc_info.value)
        mock_notify.assert_called_once()
        call_args = mock_notify.call_args
        assert "2 commits behind" in call_args[0][0]
        assert "1.7" in call_args[0][0]
        assert call_args[1]["level"] == "error"


class TestReconcilePending:
    """Tests for reconcile_pending()."""

    def test_success_removes_entry_and_commits(self, tmp_path):
        project = _setup_project(tmp_path)
        pending = _pending_path(project)
        pending.write_text(json.dumps({
            "commit_sha": "abc1234",
            "timestamp": "2026-04-11T00:00:00+00:00",
            "attempts": 1,
        }))

        with (
            patch("mcloop.claude_md_sync.auto_update_claude_md", return_value=SyncResult.OK),
            patch("mcloop.claude_md_sync._commit") as mock_commit,
        ):
            reconcile_pending(project)

        assert not pending.exists()
        mock_commit.assert_called_once()
        commit_msg = mock_commit.call_args[0][1]
        assert "deferred from abc1234" in commit_msg

    def test_transient_failure_increments_attempts(self, tmp_path):
        project = _setup_project(tmp_path)
        pending = _pending_path(project)
        pending.write_text(json.dumps({
            "commit_sha": "abc1234",
            "timestamp": "2026-04-11T00:00:00+00:00",
            "attempts": 1,
        }))

        with patch("mcloop.claude_md_sync.auto_update_claude_md", return_value=SyncResult.TRANSIENT_FAILED):
            reconcile_pending(project)

        assert pending.exists()
        entry = json.loads(pending.read_text())
        assert entry["attempts"] == 2
        assert "last_attempt" in entry

    def test_idempotent_no_pending(self, tmp_path):
        project = _setup_project(tmp_path)
        # No pending file — should be a no-op.
        reconcile_pending(project)
        reconcile_pending(project)
        assert not _pending_path(project).exists()

    def test_no_work_removes_pending(self, tmp_path):
        project = _setup_project(tmp_path)
        pending = _pending_path(project)
        pending.write_text(json.dumps({
            "commit_sha": "abc1234",
            "attempts": 1,
        }))

        with patch("mcloop.claude_md_sync.auto_update_claude_md", return_value=SyncResult.NO_WORK):
            reconcile_pending(project)

        assert not pending.exists()


class TestSonnetFallbackEnvStripping:
    """Test that Sonnet fallback strips ANTHROPIC_API_KEY from env."""

    def test_sonnet_subprocess_strips_api_key(self):
        from mcloop.claude_md_check import _call_sonnet_fallback

        with patch("mcloop.claude_md_check.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            _call_sonnet_fallback("user message")

        mock_run.assert_called_once()
        call_env = mock_run.call_args[1]["env"]
        assert "ANTHROPIC_API_KEY" not in call_env
