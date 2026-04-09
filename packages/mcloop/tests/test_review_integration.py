"""Tests for mcloop.review_integration."""

from __future__ import annotations

import json
import os
import time
from unittest.mock import MagicMock, patch

from mcloop.review_integration import (
    _cleanup_stale_reviews,
    _collect_review_findings,
    _get_commit_hash,
    _purge_all_reviews,
    _reviewer_procs,
    _spawn_reviewer,
    _terminate_reviewers,
)
from mcloop.session_context import SessionContext


def test_get_commit_hash(tmp_path):
    """_get_commit_hash returns the HEAD commit hash."""
    with patch("mcloop.review_integration.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="abc123\n")
        h = _get_commit_hash(tmp_path)
    assert h == "abc123"
    mock_run.assert_called_once()
    assert mock_run.call_args[1]["cwd"] == tmp_path


def test_get_commit_hash_empty(tmp_path):
    """_get_commit_hash returns empty string on failure."""
    with patch("mcloop.review_integration.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="")
        h = _get_commit_hash(tmp_path)
    assert h == ""


def test_spawn_reviewer(tmp_path):
    """_spawn_reviewer spawns a subprocess and appends to _reviewer_procs."""
    proc = MagicMock()
    with (
        patch(
            "mcloop.review_integration._get_commit_hash",
            return_value="abc123",
        ),
        patch(
            "mcloop.review_integration.subprocess.Popen",
            return_value=proc,
        ) as mock_popen,
    ):
        saved = list(_reviewer_procs)
        _reviewer_procs.clear()
        try:
            _spawn_reviewer(tmp_path)
            assert proc in _reviewer_procs
            mock_popen.assert_called_once()
            call_args = mock_popen.call_args
            cmd = call_args[0][0]
            assert "-m" in cmd
            assert "mcloop.reviewer" in cmd
            assert "abc123" in cmd
            assert str(tmp_path) in cmd
            assert call_args[1]["start_new_session"] is True
        finally:
            _reviewer_procs.clear()
            _reviewer_procs.extend(saved)


def test_spawn_reviewer_no_hash(tmp_path):
    """_spawn_reviewer does nothing if commit hash is empty."""
    with (
        patch(
            "mcloop.review_integration._get_commit_hash",
            return_value="",
        ),
        patch(
            "mcloop.review_integration.subprocess.Popen",
        ) as mock_popen,
    ):
        _spawn_reviewer(tmp_path)
    mock_popen.assert_not_called()


def test_terminate_reviewers():
    """_terminate_reviewers terminates all procs and clears the list."""
    p1 = MagicMock()
    p2 = MagicMock()
    saved = list(_reviewer_procs)
    _reviewer_procs.clear()
    _reviewer_procs.extend([p1, p2])
    try:
        _terminate_reviewers()
        p1.terminate.assert_called_once()
        p2.terminate.assert_called_once()
        assert len(_reviewer_procs) == 0
    finally:
        _reviewer_procs.clear()
        _reviewer_procs.extend(saved)


def test_terminate_reviewers_oserror():
    """_terminate_reviewers handles OSError gracefully."""
    p = MagicMock()
    p.terminate.side_effect = OSError("gone")
    saved = list(_reviewer_procs)
    _reviewer_procs.clear()
    _reviewer_procs.append(p)
    try:
        _terminate_reviewers()
        assert len(_reviewer_procs) == 0
    finally:
        _reviewer_procs.clear()
        _reviewer_procs.extend(saved)


def test_cleanup_stale_reviews(tmp_path):
    """_cleanup_stale_reviews removes old files, keeps recent ones."""
    reviews_dir = tmp_path / ".mcloop" / "reviews"
    reviews_dir.mkdir(parents=True)
    old = reviews_dir / "old.json"
    old.write_text("{}")
    recent = reviews_dir / "recent.json"
    recent.write_text("{}")
    # Make old file appear old
    old_time = time.time() - 90000
    os.utime(old, (old_time, old_time))
    _cleanup_stale_reviews(tmp_path)
    assert not old.exists()
    assert recent.exists()


def test_cleanup_stale_reviews_no_dir(tmp_path):
    """_cleanup_stale_reviews does nothing if directory doesn't exist."""
    _cleanup_stale_reviews(tmp_path)  # Should not raise


def test_cleanup_stale_reviews_ignores_non_json(tmp_path):
    """_cleanup_stale_reviews ignores non-.json files."""
    reviews_dir = tmp_path / ".mcloop" / "reviews"
    reviews_dir.mkdir(parents=True)
    txt = reviews_dir / "notes.txt"
    txt.write_text("hello")
    old_time = time.time() - 90000
    os.utime(txt, (old_time, old_time))
    _cleanup_stale_reviews(tmp_path)
    assert txt.exists()


def test_collect_review_findings_no_dir(tmp_path):
    """_collect_review_findings does nothing if reviews dir missing."""
    plan = tmp_path / "PLAN.md"
    plan.write_text("# P\n")
    ctx = SessionContext()
    _collect_review_findings(tmp_path, plan, ctx)


def test_collect_review_findings_adds_to_context(tmp_path):
    """High-confidence findings below threshold go to context."""
    reviews_dir = tmp_path / ".mcloop" / "reviews"
    reviews_dir.mkdir(parents=True)
    findings = [
        {"confidence": "high", "severity": "warning", "file": "a.py", "description": "bad thing"},
    ]
    (reviews_dir / "abc123.json").write_text(json.dumps(findings))
    plan = tmp_path / "PLAN.md"
    plan.write_text("# P\n")
    ctx = SessionContext()
    _collect_review_findings(tmp_path, plan, ctx)
    assert "bad thing" in ctx.text()


def test_collect_review_findings_inserts_bugs(tmp_path):
    """3+ high-confidence error findings insert bugs into PLAN.md."""
    reviews_dir = tmp_path / ".mcloop" / "reviews"
    reviews_dir.mkdir(parents=True)
    findings = [
        {"confidence": "high", "severity": "error", "description": f"bug {i}"} for i in range(3)
    ]
    (reviews_dir / "abc123.json").write_text(json.dumps(findings))
    plan = tmp_path / "PLAN.md"
    plan.write_text("# P\n\n- [ ] task\n")
    ctx = SessionContext()
    _collect_review_findings(tmp_path, plan, ctx)
    text = plan.read_text()
    assert "## Bugs" in text
    assert "bug 0" in text


def test_collect_review_findings_skips_low_confidence(tmp_path):
    """Low-confidence findings are ignored."""
    reviews_dir = tmp_path / ".mcloop" / "reviews"
    reviews_dir.mkdir(parents=True)
    findings = [
        {"confidence": "low", "severity": "error", "description": "maybe"},
    ]
    (reviews_dir / "abc123.json").write_text(json.dumps(findings))
    plan = tmp_path / "PLAN.md"
    plan.write_text("# P\n")
    ctx = SessionContext()
    _collect_review_findings(tmp_path, plan, ctx)
    assert ctx.text() == ""


def test_collect_review_findings_invalid_json(tmp_path):
    """Invalid JSON files are silently removed."""
    reviews_dir = tmp_path / ".mcloop" / "reviews"
    reviews_dir.mkdir(parents=True)
    bad = reviews_dir / "bad.json"
    bad.write_text("not json{")
    plan = tmp_path / "PLAN.md"
    plan.write_text("# P\n")
    ctx = SessionContext()
    _collect_review_findings(tmp_path, plan, ctx)
    assert not bad.exists()


def test_collect_review_findings_dict_format(tmp_path):
    """Dict format with findings key and elapsed_seconds works."""
    reviews_dir = tmp_path / ".mcloop" / "reviews"
    reviews_dir.mkdir(parents=True)
    data = {
        "commit": "def456789",
        "elapsed_seconds": 12.5,
        "findings": [
            {"confidence": "high", "severity": "warning", "file": "b.py", "description": "issue"},
        ],
    }
    (reviews_dir / "def456.json").write_text(json.dumps(data))
    plan = tmp_path / "PLAN.md"
    plan.write_text("# P\n")
    ctx = SessionContext()
    _collect_review_findings(tmp_path, plan, ctx)
    assert "issue" in ctx.text()


def test_collect_review_findings_clean_review(tmp_path, capsys):
    """No high-confidence findings prints clean message."""
    reviews_dir = tmp_path / ".mcloop" / "reviews"
    reviews_dir.mkdir(parents=True)
    findings = [
        {"confidence": "low", "severity": "info", "description": "minor"},
    ]
    (reviews_dir / "abc123.json").write_text(json.dumps(findings))
    plan = tmp_path / "PLAN.md"
    plan.write_text("# P\n")
    ctx = SessionContext()
    _collect_review_findings(tmp_path, plan, ctx)
    out = capsys.readouterr().out
    assert "clean" in out


def test_purge_all_reviews(tmp_path):
    """_purge_all_reviews removes all .json files regardless of age."""
    reviews_dir = tmp_path / ".mcloop" / "reviews"
    reviews_dir.mkdir(parents=True)
    (reviews_dir / "old.json").write_text("{}")
    (reviews_dir / "new.json").write_text("{}")
    txt = reviews_dir / "notes.txt"
    txt.write_text("keep")
    _purge_all_reviews(tmp_path)
    assert not (reviews_dir / "old.json").exists()
    assert not (reviews_dir / "new.json").exists()
    assert txt.exists()


def test_purge_all_reviews_no_dir(tmp_path):
    """_purge_all_reviews does nothing if directory doesn't exist."""
    _purge_all_reviews(tmp_path)  # Should not raise
