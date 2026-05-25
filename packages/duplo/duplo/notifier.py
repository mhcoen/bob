"""Notify the user that a phase is complete and ready for testing."""

from __future__ import annotations

import subprocess


def notify_phase_complete(phase: str = "Phase") -> None:
    """Print a terminal banner and attempt a macOS system notification.

    Args:
        phase: Human-readable phase label (e.g. ``"Phase 1"``).
    """
    banner = f"{'=' * 60}\n  {phase} complete — ready for testing\n{'=' * 60}"
    print(banner)
    _send_macos_notification(f"{phase} complete", "Ready for testing")


def _send_macos_notification(title: str, message: str) -> None:
    """Fire a macOS Notification Center alert via osascript.

    Silently does nothing when ``osascript`` is unavailable or fails.
    """
    safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
    safe_message = message.replace("\\", "\\\\").replace('"', '\\"')
    script = f'display notification "{safe_message}" with title "{safe_title}"'
    try:
        subprocess.run(
            ["osascript", "-e", script],
            check=False,
            capture_output=True,
        )
    except FileNotFoundError:
        pass
