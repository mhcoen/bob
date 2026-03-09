"""Tests for mcloop.process_monitor."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from mcloop import process_monitor
from mcloop.process_monitor import LaunchedProcess


class TestLaunch:
    def test_launch_returns_launched_process(self):
        proc = process_monitor.launch("echo hello")
        try:
            proc.process.wait(timeout=5)
            assert proc.pid == proc.process.pid
            assert proc.pid > 0
        finally:
            if proc.process.poll() is None:
                proc.process.kill()

    def test_launch_captures_output(self):
        proc = process_monitor.launch("echo hello")
        try:
            stdout, _ = proc.process.communicate(timeout=5)
            assert b"hello" in stdout
        finally:
            if proc.process.poll() is None:
                proc.process.kill()


class TestIsAlive:
    def test_alive_process(self):
        proc = subprocess.Popen(
            ["sleep", "10"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            assert process_monitor.is_alive(proc.pid) is True
        finally:
            proc.kill()
            proc.wait()

    def test_dead_process(self):
        proc = subprocess.Popen(
            ["true"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        proc.wait()
        assert process_monitor.is_alive(proc.pid) is False

    def test_nonexistent_pid(self):
        # PID 2^30 is unlikely to exist
        assert process_monitor.is_alive(1 << 30) is False


class TestIsHung:
    def test_not_hung_when_dead(self):
        proc = subprocess.Popen(
            ["true"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        proc.wait()
        lp = LaunchedProcess(
            pid=proc.pid,
            process=proc,
            last_output_at=time.monotonic() - 100,
        )
        assert process_monitor.is_hung(lp, timeout_seconds=1) is False

    def test_not_hung_when_producing_output(self):
        proc = subprocess.Popen(
            ["echo", "data"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        # Let it produce output
        time.sleep(0.1)
        lp = LaunchedProcess(
            pid=proc.pid,
            process=proc,
            last_output_at=time.monotonic() - 100,
        )
        # Even though last_output_at is old, is_hung reads new output
        result = process_monitor.is_hung(lp, timeout_seconds=1)
        proc.wait()
        # Output was available, so last_output_at gets updated
        assert result is False

    def test_hung_when_silent(self):
        proc = subprocess.Popen(
            ["sleep", "60"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        try:
            lp = LaunchedProcess(
                pid=proc.pid,
                process=proc,
                last_output_at=time.monotonic() - 10,
            )
            assert process_monitor.is_hung(lp, timeout_seconds=5) is True
        finally:
            proc.kill()
            proc.wait()


class TestSample:
    @patch("subprocess.run")
    def test_sample_returns_output(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="Call graph:\n  thread 0x1234",
            stderr="",
        )
        result = process_monitor.sample(12345, duration_seconds=1.0)
        assert "Call graph" in result
        mock_run.assert_called_once_with(
            ["sample", "12345", "1"],
            capture_output=True,
            text=True,
            timeout=11.0,
        )

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_sample_not_found(self, mock_run):
        result = process_monitor.sample(12345)
        assert "not found" in result

    @patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="sample", timeout=11),
    )
    def test_sample_timeout(self, mock_run):
        result = process_monitor.sample(12345)
        assert "timed out" in result


class TestKill:
    def test_kill_running_process(self):
        proc = subprocess.Popen(
            ["sleep", "60"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert process_monitor.kill(proc.pid) is True
        proc.wait()
        assert proc.poll() is not None

    def test_kill_already_dead(self):
        proc = subprocess.Popen(
            ["true"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        proc.wait()
        assert process_monitor.kill(proc.pid) is True

    @patch("os.kill", side_effect=PermissionError)
    def test_kill_permission_denied(self, mock_kill):
        assert process_monitor.kill(99999) is False


class TestReadCrashReport:
    def test_no_reports_dir(self, tmp_path):
        with patch.object(Path, "home", return_value=tmp_path):
            assert process_monitor.read_crash_report("MyApp") is None

    def test_no_matching_reports(self, tmp_path):
        reports_dir = tmp_path / "Library" / "Logs" / "DiagnosticReports"
        reports_dir.mkdir(parents=True)
        (reports_dir / "OtherApp-2024-01-01.ips").write_text("crash")
        with patch.object(Path, "home", return_value=tmp_path):
            assert process_monitor.read_crash_report("MyApp") is None

    def test_reads_newest_report(self, tmp_path):
        reports_dir = tmp_path / "Library" / "Logs" / "DiagnosticReports"
        reports_dir.mkdir(parents=True)
        old = reports_dir / "MyApp-2024-01-01.ips"
        old.write_text("old crash")
        time.sleep(0.05)
        new = reports_dir / "MyApp-2024-06-15.ips"
        new.write_text("new crash")
        with patch.object(Path, "home", return_value=tmp_path):
            result = process_monitor.read_crash_report("MyApp")
        assert result == "new crash"

    def test_ignores_non_ips_files(self, tmp_path):
        reports_dir = tmp_path / "Library" / "Logs" / "DiagnosticReports"
        reports_dir.mkdir(parents=True)
        (reports_dir / "MyApp-2024-01-01.log").write_text("not a crash")
        with patch.object(Path, "home", return_value=tmp_path):
            assert process_monitor.read_crash_report("MyApp") is None


class TestRunCLI:
    def test_successful_exit(self):
        result = process_monitor.run_cli("echo hello world")
        assert result.exit_code == 0
        assert "hello world" in result.output
        assert result.hung is False
        assert result.sample_output is None
        assert result.duration > 0

    def test_crash_nonzero_exit(self):
        result = process_monitor.run_cli(
            f"{sys.executable} -c \"import sys; print('boom'); sys.exit(42)\""
        )
        assert result.exit_code == 42
        assert "boom" in result.output
        assert result.hung is False

    def test_captures_stderr(self):
        result = process_monitor.run_cli(
            f"{sys.executable} -c \"import sys; sys.stderr.write('err msg\\n')\""
        )
        assert result.exit_code == 0
        assert "err msg" in result.output

    def test_hang_detected(self):
        with patch("mcloop.process_monitor.sample", return_value="sampled"):
            result = process_monitor.run_cli(
                "sleep 60",
                hang_seconds=0.3,
                timeout_seconds=5,
                poll_interval=0.05,
            )
        assert result.hung is True
        assert result.exit_code is None
        assert result.sample_output == "sampled"

    def test_wall_clock_timeout(self):
        # Process that produces output continuously but never exits.
        script = (
            "import time, sys\nwhile True:\n    print('.', flush=True)\n    time.sleep(0.05)\n"
        )
        with patch("mcloop.process_monitor.sample", return_value="sampled"):
            result = process_monitor.run_cli(
                f'{sys.executable} -c "{script}"',
                timeout_seconds=0.5,
                hang_seconds=60,
                poll_interval=0.05,
            )
        assert result.hung is True
        assert result.exit_code is None
        assert "." in result.output

    def test_multiline_output(self):
        script = "for i in range(5): print(f'line {i}')"
        result = process_monitor.run_cli(f'{sys.executable} -c "{script}"')
        assert result.exit_code == 0
        for i in range(5):
            assert f"line {i}" in result.output
