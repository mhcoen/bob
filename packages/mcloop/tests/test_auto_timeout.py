"""AUTO checks can be quiet while remaining subject to a command deadline."""

import json
from unittest.mock import MagicMock, patch

import pytest

from mcloop import process_monitor
from mcloop.investigate_cmd import _dispatch_auto_action
from mcloop.main import _auto_response_failed
from mcloop.process_monitor import CLIResult, LaunchedProcess


@pytest.mark.parametrize("timeout,exit_code", [(90, 0), (40, None)])
def test_quiet_command_progress_and_deadline(timeout, exit_code):
    now = 0.0
    proc = MagicMock()
    proc.stdout.fileno.return_value = 99
    proc.stdout.read.return_value = b"final output"
    proc.poll.side_effect = lambda: exit_code if now >= 45 else None

    def select(*args):
        nonlocal now
        now += 15
        return [], [], []

    progress = []
    with (
        patch("mcloop.process_monitor.time.monotonic", side_effect=lambda: now),
        patch("mcloop.process_monitor.select.select", side_effect=select),
        patch("mcloop.process_monitor.launch", return_value=LaunchedProcess(100, proc, 0, 0)),
        patch("mcloop.process_monitor.sample", return_value="deadline sample") as sample,
        patch("mcloop.process_monitor.kill_process_group") as kill,
    ):
        result = process_monitor.run_cli(
            "quiet check",
            timeout_seconds=timeout,
            hang_seconds=None,
            on_progress=lambda elapsed, age: progress.append((elapsed, age)),
        )
    assert result.exit_code == exit_code
    assert result.output == "final output"
    assert progress == [(30, 30)]
    assert result.timed_out is (exit_code is None)
    if exit_code is None:
        sample.assert_called_once_with(100)
        kill.assert_called_once_with(100)
    else:
        sample.assert_not_called()
        kill.assert_not_called()


@pytest.mark.parametrize("timeout", [None, 600])
def test_auto_uses_plan_directory_and_check_timeout(tmp_path, timeout, monkeypatch):
    if timeout:
        (tmp_path / "mcloop.json").write_text(json.dumps({"check_timeout": timeout}))
    other = tmp_path / "other"
    other.mkdir()
    (other / "mcloop.json").write_text('{"check_timeout":1}')
    monkeypatch.chdir(other)
    with patch(
        "mcloop.process_monitor.run_cli", return_value=CLIResult(0, "done", False, 45)
    ) as run:
        result = _dispatch_auto_action("run_cli", "python3 verify.py", project_dir=tmp_path)
    assert run.call_args.args == ("python3 verify.py",)
    assert run.call_args.kwargs["cwd"] == tmp_path
    assert run.call_args.kwargs["timeout_seconds"] == (timeout or 300)
    assert run.call_args.kwargs["hang_seconds"] is None
    assert callable(run.call_args.kwargs["on_progress"])
    assert not _auto_response_failed(result)


def test_auto_deadline_reports_timeout(tmp_path):
    with patch(
        "mcloop.process_monitor.run_cli",
        return_value=CLIResult(None, "unfinished", True, 300, timed_out=True),
    ):
        result = _dispatch_auto_action("run_cli", "quiet check", project_dir=tmp_path)
    assert "STATUS: TIMEOUT (killed after 300s)" in result
    assert "HUNG" not in result
    assert _auto_response_failed(result)


def test_auto_runs_relative_command_in_plan_directory(tmp_path):
    (tmp_path / "verify.sh").write_text("test -f expected\n")
    (tmp_path / "expected").touch()
    result = _dispatch_auto_action("run_cli", "./verify.sh", project_dir=tmp_path)
    assert "exit_code: 0" in result
    assert not _auto_response_failed(result)


@pytest.mark.parametrize("code,status", [(1, "FAILED"), (2, "FAILED"), (-11, "CRASHED")])
def test_nonzero_auto_exit_reports_check_reason_without_claiming_a_crash(code, status):
    output = json.dumps(
        {
            "checks": [
                {"id": "contracts", "status": "passed", "detail": "Passed"},
                {"id": "malformed", "status": [], "detail": "Ignored"},
                {"id": "application", "status": "inconclusive", "detail": "invalidPreparation"},
            ]
        }
    )
    with patch(
        "mcloop.process_monitor.run_cli", return_value=CLIResult(code, output, False, None)
    ):
        result = _dispatch_auto_action("run_cli", "python3 acceptance.py")
    assert f"STATUS: {status}" in result
    assert "Check application (inconclusive): invalidPreparation" in result
    assert result.index("invalidPreparation") < result.index("output:")
    assert _auto_response_failed(result)
