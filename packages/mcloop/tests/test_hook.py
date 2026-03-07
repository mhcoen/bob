"""Tests for telegram-permission-hook.py interactive session skip."""

import importlib.util
import json
import os
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

# Load the hook script as a module (it's not a package)
_hook_path = Path(__file__).resolve().parent.parent / "telegram-permission-hook.py"
_spec = importlib.util.spec_from_file_location("telegram_hook", _hook_path)
_hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_hook)


def _run_main(stdin_data):
    """Run hook main() capturing stdout, return parsed JSON output."""
    old_stdout = sys.stdout
    sys.stdout = buf = StringIO()
    old_stdin = sys.stdin
    sys.stdin = StringIO(json.dumps(stdin_data))
    try:
        with patch.object(_hook, "_dbg", lambda msg: None):
            _hook.main()
    finally:
        sys.stdout = old_stdout
        sys.stdin = old_stdin
    return json.loads(buf.getvalue())


def test_skips_when_no_task_label():
    """Without MCLOOP_TASK_LABEL, hook returns empty JSON (no opinion)."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("MCLOOP_TASK_LABEL", None)
        result = _run_main(
            {"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}},
        )
    assert result == {}


def test_proceeds_when_task_label_set():
    """With MCLOOP_TASK_LABEL set but no bot credentials, hook gets past
    the skip and hits the no-credentials exit."""
    with (
        patch.dict(os.environ, {"MCLOOP_TASK_LABEL": "test-task"}),
        patch.object(_hook, "BOT_TOKEN", ""),
        patch.object(_hook, "CHAT_ID", ""),
    ):
        result = _run_main(
            {"tool_name": "Bash", "tool_input": {"command": "ls"}},
        )
    assert result == {}
