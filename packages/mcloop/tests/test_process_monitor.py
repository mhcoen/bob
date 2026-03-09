"""Tests for mcloop.process_monitor."""

from __future__ import annotations

import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from mcloop import process_monitor
from mcloop.process_monitor import GUIResult, LaunchedProcess


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


class TestPgrep:
    @patch("subprocess.run")
    def test_returns_pids(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="1234\n5678\n",
        )
        pids = process_monitor.pgrep("MyApp")
        assert pids == [1234, 5678]
        mock_run.assert_called_once_with(
            ["pgrep", "-x", "MyApp"],
            capture_output=True,
            text=True,
            timeout=5,
        )

    @patch("subprocess.run")
    def test_no_matches(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert process_monitor.pgrep("NoSuchApp") == []

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_pgrep_not_found(self, mock_run):
        assert process_monitor.pgrep("MyApp") == []

    @patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="pgrep", timeout=5),
    )
    def test_pgrep_timeout(self, mock_run):
        assert process_monitor.pgrep("MyApp") == []


class TestIsMainThreadStuck:
    def test_empty_output(self):
        assert process_monitor.is_main_thread_stuck("") is False

    def test_stuck_on_mach_msg_trap(self):
        sample_text = (
            "Call graph:\n"
            "  Thread 0\n"
            "    + 100 main (in MyApp)\n"
            "    +   50 mach_msg_trap (in libsystem)\n"
            "  Thread 1\n"
            "    + 100 worker (in MyApp)\n"
        )
        assert process_monitor.is_main_thread_stuck(sample_text) is True

    def test_stuck_on_semaphore_wait(self):
        sample_text = (
            "Thread_0\n"
            "  + 100 main (in MyApp)\n"
            "  +   50 dispatch_semaphore_wait (in libdispatch)\n"
            "Thread_1\n"
            "  + 100 worker (in MyApp)\n"
        )
        assert process_monitor.is_main_thread_stuck(sample_text) is True

    def test_not_stuck_active_thread(self):
        sample_text = (
            "Thread 0\n"
            "  + 100 main (in MyApp)\n"
            "  +   50 doWork (in MyApp)\n"
            "  +   25 computeStuff (in MyApp)\n"
            "Thread 1\n"
            "  + 100 worker (in MyApp)\n"
        )
        assert process_monitor.is_main_thread_stuck(sample_text) is False

    def test_no_main_thread_section(self):
        sample_text = "Thread 5\n  + 100 worker (in MyApp)\n"
        assert process_monitor.is_main_thread_stuck(sample_text) is False

    def test_stuck_on_cfrunloop(self):
        sample_text = (
            "Thread 0\n"
            "  + 100 main (in MyApp)\n"
            "  +   50 CFRunLoopRunSpecific (in CoreFoundation)\n"
            "Thread 1\n"
        )
        assert process_monitor.is_main_thread_stuck(sample_text) is True


class TestRunGUI:
    @patch("mcloop.process_monitor.sample", return_value="healthy output")
    @patch("mcloop.process_monitor.is_alive", return_value=True)
    @patch("mcloop.process_monitor.pgrep", return_value=[12345])
    @patch("mcloop.process_monitor.read_crash_report", return_value=None)
    @patch("subprocess.Popen")
    @patch("time.sleep")
    def test_healthy_app(
        self,
        mock_sleep,
        mock_popen,
        mock_crash,
        mock_pgrep,
        mock_alive,
        mock_sample,
    ):
        result = process_monitor.run_gui(
            "open MyApp",
            "MyApp",
            timeout_seconds=0.01,
            check_interval=0.005,
            settle_seconds=0.0,
        )
        assert result.crashed is False
        assert result.hung is False
        assert result.sample_output is None
        assert isinstance(result, GUIResult)

    @patch("mcloop.process_monitor.pgrep", return_value=[])
    @patch("mcloop.process_monitor.read_crash_report", return_value="crash!")
    @patch("subprocess.Popen")
    @patch("time.sleep")
    def test_immediate_crash(
        self,
        mock_sleep,
        mock_popen,
        mock_crash,
        mock_pgrep,
    ):
        result = process_monitor.run_gui(
            "open CrashApp",
            "CrashApp",
            settle_seconds=0.0,
        )
        assert result.crashed is True
        assert result.hung is False
        assert result.crash_report == "crash!"

    @patch("mcloop.process_monitor.read_crash_report", return_value="crash!")
    @patch("mcloop.process_monitor.is_alive", return_value=False)
    @patch("mcloop.process_monitor.pgrep", return_value=[12345])
    @patch("subprocess.Popen")
    @patch("time.sleep")
    def test_crash_during_monitoring(
        self,
        mock_sleep,
        mock_popen,
        mock_pgrep,
        mock_alive,
        mock_crash,
    ):
        result = process_monitor.run_gui(
            "open CrashApp",
            "CrashApp",
            timeout_seconds=10,
            check_interval=0.005,
            settle_seconds=0.0,
        )
        assert result.crashed is True
        assert result.hung is False
        assert result.crash_report == "crash!"

    @patch("mcloop.process_monitor.is_main_thread_stuck", return_value=True)
    @patch(
        "mcloop.process_monitor.sample",
        return_value="Thread 0\n  mach_msg_trap",
    )
    @patch("mcloop.process_monitor.is_alive", return_value=True)
    @patch("mcloop.process_monitor.pgrep", return_value=[12345])
    @patch("subprocess.Popen")
    @patch("time.sleep")
    def test_hung_app(
        self,
        mock_sleep,
        mock_popen,
        mock_pgrep,
        mock_alive,
        mock_sample,
        mock_stuck,
    ):
        result = process_monitor.run_gui(
            "open HungApp",
            "HungApp",
            timeout_seconds=0.01,
            check_interval=0.005,
            settle_seconds=0.0,
        )
        assert result.crashed is False
        assert result.hung is True
        assert result.sample_output is not None


class TestRunCLIMocked:
    """run_cli tests using mock subprocesses for deterministic behavior."""

    def _make_mock_proc(self, poll_returns, read_data=b"", pid=100):
        """Build a mock Popen with controlled poll() and stdout."""
        mock_proc = MagicMock()
        mock_proc.pid = pid
        mock_proc.poll = MagicMock(side_effect=poll_returns)
        mock_stdout = MagicMock()
        mock_stdout.fileno.return_value = 99
        mock_stdout.read.return_value = read_data
        mock_proc.stdout = mock_stdout
        mock_proc.wait.return_value = None
        return mock_proc

    @patch("mcloop.process_monitor.kill")
    @patch("mcloop.process_monitor.sample", return_value="sample text")
    @patch("select.select", return_value=([], [], []))
    @patch("mcloop.process_monitor.launch")
    def test_hang_calls_sample_and_kill(self, mock_launch, mock_select, mock_sample, mock_kill):
        mock_proc = self._make_mock_proc([None] * 200)
        mock_launch.return_value = LaunchedProcess(
            pid=100,
            process=mock_proc,
            started_at=time.monotonic(),
            last_output_at=time.monotonic() - 20,
        )
        result = process_monitor.run_cli(
            "fake", hang_seconds=0.01, timeout_seconds=60, poll_interval=0
        )
        assert result.hung is True
        assert result.exit_code is None
        assert result.sample_output == "sample text"
        mock_sample.assert_called_once_with(100)
        mock_kill.assert_called_once_with(100)

    @patch("mcloop.process_monitor.kill")
    @patch("mcloop.process_monitor.sample", return_value="timeout sample")
    @patch("select.select", return_value=([99], [], []))
    @patch("os.read", return_value=b"output ")
    @patch("mcloop.process_monitor.launch")
    def test_wall_timeout_kills_process(
        self, mock_launch, mock_os_read, mock_select, mock_sample, mock_kill
    ):
        mock_proc = self._make_mock_proc([None] * 200)
        mock_launch.return_value = LaunchedProcess(
            pid=101,
            process=mock_proc,
            started_at=time.monotonic() - 100,
            last_output_at=time.monotonic(),
        )
        result = process_monitor.run_cli(
            "fake", timeout_seconds=0.0, hang_seconds=999, poll_interval=0
        )
        assert result.hung is True
        assert result.exit_code is None
        mock_kill.assert_called_once_with(101)

    @patch("select.select", return_value=([99], [], []))
    @patch("os.read", return_value=b"all output")
    @patch("mcloop.process_monitor.launch")
    def test_normal_exit_returns_code(self, mock_launch, mock_os_read, mock_select):
        mock_proc = self._make_mock_proc([None, None, 0], read_data=b" rest")
        mock_launch.return_value = LaunchedProcess(
            pid=102,
            process=mock_proc,
            started_at=time.monotonic(),
            last_output_at=time.monotonic(),
        )
        result = process_monitor.run_cli(
            "fake", timeout_seconds=60, hang_seconds=60, poll_interval=0
        )
        assert result.hung is False
        assert result.exit_code == 0
        assert "all output" in result.output

    @patch("select.select", return_value=([99], [], []))
    @patch("os.read", return_value=b"error output")
    @patch("mcloop.process_monitor.launch")
    def test_crash_exit_code(self, mock_launch, mock_os_read, mock_select):
        mock_proc = self._make_mock_proc([None, 137], read_data=b"")
        mock_launch.return_value = LaunchedProcess(
            pid=103,
            process=mock_proc,
            started_at=time.monotonic(),
            last_output_at=time.monotonic(),
        )
        result = process_monitor.run_cli(
            "fake", timeout_seconds=60, hang_seconds=60, poll_interval=0
        )
        assert result.hung is False
        assert result.exit_code == 137
        assert "error output" in result.output


class TestLaunchMocked:
    """launch() with mock subprocess to verify argument passing."""

    @patch("subprocess.Popen")
    def test_passes_cwd(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.pid = 42
        mock_popen.return_value = mock_proc
        result = process_monitor.launch("echo hi", cwd="/some/dir")
        mock_popen.assert_called_once_with(
            "echo hi",
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd="/some/dir",
        )
        assert result.pid == 42
        assert result.process is mock_proc

    @patch("subprocess.Popen")
    def test_defaults_cwd_none(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.pid = 43
        mock_popen.return_value = mock_proc
        process_monitor.launch("ls")
        mock_popen.assert_called_once_with(
            "ls",
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=None,
        )


class TestKillMocked:
    """kill() with mocks to test SIGTERM→SIGKILL escalation."""

    @patch("time.sleep")
    @patch("mcloop.process_monitor.is_alive")
    @patch("os.kill")
    def test_sigterm_succeeds(self, mock_kill, mock_alive, mock_sleep):
        mock_alive.return_value = False
        result = process_monitor.kill(555, graceful_timeout=1.0)
        assert result is True
        mock_kill.assert_called_once_with(555, signal.SIGTERM)

    @patch("time.sleep")
    @patch("mcloop.process_monitor.is_alive", return_value=True)
    @patch("os.kill")
    def test_escalates_to_sigkill(self, mock_kill, mock_alive, mock_sleep):
        result = process_monitor.kill(556, graceful_timeout=0.0)
        assert result is True
        calls = mock_kill.call_args_list
        assert calls[0] == ((556, signal.SIGTERM),)
        assert calls[1] == ((556, signal.SIGKILL),)

    @patch("os.kill", side_effect=ProcessLookupError)
    def test_already_dead_on_sigterm(self, mock_kill):
        result = process_monitor.kill(557)
        assert result is True


class TestIsHungMocked:
    """is_hung() with mock to test stdout-is-None branch."""

    def test_no_stdout(self):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stdout = None
        lp = LaunchedProcess(
            pid=200,
            process=mock_proc,
            last_output_at=time.monotonic() - 100,
        )
        assert process_monitor.is_hung(lp, timeout_seconds=5) is True

    def test_no_stdout_within_timeout(self):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stdout = None
        lp = LaunchedProcess(
            pid=201,
            process=mock_proc,
            last_output_at=time.monotonic(),
        )
        assert process_monitor.is_hung(lp, timeout_seconds=60) is False

    @patch("select.select", return_value=([], [], []))
    def test_no_readable_data_past_timeout(self, mock_select):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_stdout = MagicMock()
        mock_stdout.fileno.return_value = 88
        mock_proc.stdout = mock_stdout
        lp = LaunchedProcess(
            pid=202,
            process=mock_proc,
            last_output_at=time.monotonic() - 20,
        )
        assert process_monitor.is_hung(lp, timeout_seconds=5) is True

    @patch("os.read", return_value=b"data")
    @patch("select.select", return_value=([88], [], []))
    def test_readable_data_resets_timeout(self, mock_select, mock_read):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_stdout = MagicMock()
        mock_stdout.fileno.return_value = 88
        mock_proc.stdout = mock_stdout
        lp = LaunchedProcess(
            pid=203,
            process=mock_proc,
            last_output_at=time.monotonic() - 20,
        )
        assert process_monitor.is_hung(lp, timeout_seconds=5) is False

    @patch("os.read", return_value=b"")
    @patch("select.select", return_value=([88], [], []))
    def test_empty_read_does_not_reset(self, mock_select, mock_read):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_stdout = MagicMock()
        mock_stdout.fileno.return_value = 88
        mock_proc.stdout = mock_stdout
        lp = LaunchedProcess(
            pid=204,
            process=mock_proc,
            last_output_at=time.monotonic() - 20,
        )
        assert process_monitor.is_hung(lp, timeout_seconds=5) is True
