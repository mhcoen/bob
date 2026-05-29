"""Tests for duplo.call_log."""

from __future__ import annotations

import json
import re

import pytest

from duplo import call_log


@pytest.fixture(autouse=True)
def _reset_active(monkeypatch):
    """Keep the module-level logger isolated per test.

    Restored automatically by monkeypatch so an activated run never leaks
    into other test modules (e.g. claude_cli) that exercise the wrappers.
    """
    monkeypatch.setattr(call_log, "_active", None)


def _read_records(path):
    with open(path) as fh:
        return [json.loads(line) for line in fh if line.strip()]


def test_generate_run_id_format():
    run_id = call_log.generate_run_id()
    assert re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{6}", run_id)


def test_generate_run_id_is_unique():
    assert call_log.generate_run_id() != call_log.generate_run_id()


def test_log_call_is_noop_when_inactive(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    call_log.log_call(provider="claude_cli", model="sonnet", prompt="hi")
    assert not (tmp_path / call_log.LOGS_ROOT).exists()


def test_start_run_returns_logger_with_expected_paths(tmp_path):
    logger = call_log.start_run(target_dir=tmp_path, run_id="20260101T000000Z-abc123")
    assert logger.run_id == "20260101T000000Z-abc123"
    assert logger.run_dir == tmp_path / ".duplo/logs/20260101T000000Z-abc123"
    assert logger.calls_path == logger.run_dir / "calls.jsonl"
    assert call_log.current_run() is logger


def test_start_run_creates_no_directory_until_first_call(tmp_path):
    logger = call_log.start_run(target_dir=tmp_path, run_id="20260101T000000Z-abc123")
    assert not logger.run_dir.exists()


def test_log_call_writes_one_record(tmp_path):
    call_log.start_run(target_dir=tmp_path, run_id="20260101T000000Z-abc123")
    call_log.log_call(
        provider="claude_cli",
        model="sonnet",
        prompt="prompt text",
        system="be helpful",
        response="response text",
        duration_s=1.2345,
    )
    records = _read_records(call_log.current_run().calls_path)
    assert len(records) == 1
    rec = records[0]
    assert rec["run_id"] == "20260101T000000Z-abc123"
    assert rec["provider"] == "claude_cli"
    assert rec["model"] == "sonnet"
    assert rec["ok"] is True
    assert rec["prompt"] == "prompt text"
    assert rec["system"] == "be helpful"
    assert rec["response"] == "response text"
    assert rec["duration_s"] == 1.234
    assert "error" not in rec


def test_log_call_records_error(tmp_path):
    logger = call_log.start_run(target_dir=tmp_path, run_id="20260101T000000Z-abc123")
    logger.log_call(provider="claude_cli", model="sonnet", prompt="p", error="boom")
    rec = _read_records(logger.calls_path)[0]
    assert rec["ok"] is False
    assert rec["error"] == "boom"
    assert "response" not in rec


def test_log_call_appends_one_record_per_call(tmp_path):
    call_log.start_run(target_dir=tmp_path, run_id="20260101T000000Z-abc123")
    for i in range(3):
        call_log.log_call(provider="claude_cli", model="sonnet", prompt=f"p{i}")
    records = _read_records(call_log.current_run().calls_path)
    assert [r["prompt"] for r in records] == ["p0", "p1", "p2"]
