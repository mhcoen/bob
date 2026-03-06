"""Tests for duplo.notifier."""

from __future__ import annotations

from unittest.mock import patch

from duplo.notifier import _send_macos_notification, notify_phase_complete


class TestNotifyPhaseComplete:
    def test_prints_banner_with_phase_name(self, capsys):
        with patch("duplo.notifier._send_macos_notification"):
            notify_phase_complete("Phase 1")
        out = capsys.readouterr().out
        assert "Phase 1" in out
        assert "ready for testing" in out

    def test_prints_banner_with_default_phase(self, capsys):
        with patch("duplo.notifier._send_macos_notification"):
            notify_phase_complete()
        out = capsys.readouterr().out
        assert "Phase" in out
        assert "ready for testing" in out

    def test_calls_send_macos_notification(self):
        with patch("duplo.notifier._send_macos_notification") as mock_notify:
            notify_phase_complete("Phase 2")
        mock_notify.assert_called_once_with("Phase 2 complete", "Ready for testing")


class TestSendMacosNotification:
    def test_calls_osascript(self):
        with patch("duplo.notifier.subprocess.run") as mock_run:
            _send_macos_notification("Title", "Message")
        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        assert args[0][0] == "osascript"
        assert "Title" in args[0][2]
        assert "Message" in args[0][2]
        assert kwargs["check"] is False
        assert kwargs["capture_output"] is True

    def test_silently_ignores_missing_osascript(self):
        with patch("duplo.notifier.subprocess.run", side_effect=FileNotFoundError):
            _send_macos_notification("Title", "Message")  # must not raise

    def test_does_not_raise_on_nonzero_exit(self):
        import subprocess

        completed = subprocess.CompletedProcess(args=[], returncode=1)
        with patch("duplo.notifier.subprocess.run", return_value=completed):
            _send_macos_notification("Title", "Message")  # must not raise
