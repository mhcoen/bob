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


# --- _bash_prefix tests ---


class TestBashPrefix:
    """Tests for _bash_prefix: extracts executable + subcommand."""

    def test_git_subcommand(self):
        assert _hook._bash_prefix("git add a.py") == "git add"

    def test_git_status(self):
        assert _hook._bash_prefix("git status") == "git status"

    def test_flag_only(self):
        """Second token starting with '-' is a flag, not a subcommand."""
        assert _hook._bash_prefix("ls -la") == "ls"

    def test_ruff_check(self):
        assert _hook._bash_prefix("ruff check .") == "ruff check"

    def test_single_command(self):
        assert _hook._bash_prefix("ls") == "ls"

    def test_empty(self):
        assert _hook._bash_prefix("") == ""

    def test_rtk_proxy(self):
        assert _hook._bash_prefix("rtk proxy swift build") == "rtk proxy"

    def test_python_flag(self):
        """python -m is a flag, so only 'python' is the prefix."""
        assert _hook._bash_prefix("python -m mcloop") == "python"

    def test_trailing_semicolon_on_first_token(self):
        """Compound 'pytest; echo done' — ';' on the first token is stripped."""
        assert _hook._bash_prefix("pytest; echo done") == "pytest echo"

    def test_trailing_semicolon_single_token(self):
        """Single command with trailing ';' — ';' is stripped."""
        assert _hook._bash_prefix("pytest;") == "pytest"

    def test_standalone_separator_ends_prefix(self):
        """Standalone '&&' between commands stops the prefix at the first token."""
        assert _hook._bash_prefix("pytest && echo done") == "pytest"

    def test_trailing_pipe(self):
        """Trailing '|' on a token is stripped."""
        assert _hook._bash_prefix("pytest| tee out.log") == "pytest tee"


# --- _load_session tests ---


class TestLoadSession:
    """Tests for _load_session: expiry and file self-clean."""

    def test_expired_session_deletes_file(self, tmp_path, monkeypatch):
        """Expired sessions (>24h) return empty AND delete the stale file."""
        session_file = tmp_path / "session.json"
        session_file.write_text(
            json.dumps({"created": 0, "patterns": ["Bash:pytest"]})
        )
        monkeypatch.setattr(_hook, "SESSION_FILE", session_file)
        assert _hook._load_session() == set()
        assert not session_file.exists()

    def test_fresh_session_preserves_file(self, tmp_path, monkeypatch):
        """Fresh sessions (<24h) return patterns and keep the file."""
        import time as _time

        session_file = tmp_path / "session.json"
        session_file.write_text(
            json.dumps({"created": _time.time(), "patterns": ["Bash:pytest"]})
        )
        monkeypatch.setattr(_hook, "SESSION_FILE", session_file)
        assert _hook._load_session() == {"Bash:pytest"}
        assert session_file.exists()


# --- _tool_pattern tests ---


class TestToolPattern:
    """Tests for _tool_pattern: session memory keys."""

    def test_bash_uses_prefix(self):
        """Bash patterns use command prefix, not full command."""
        pattern = _hook._tool_pattern("Bash", {"command": "git add a.py"})
        assert pattern == "Bash:git add"

    def test_bash_different_args_same_pattern(self):
        """Different arguments to same command produce the same pattern."""
        p1 = _hook._tool_pattern("Bash", {"command": "git add a.py"})
        p2 = _hook._tool_pattern("Bash", {"command": "git add b.py c.py"})
        assert p1 == p2

    def test_edit_uses_exact_path(self):
        """Non-Bash tools preserve exact-string matching."""
        pattern = _hook._tool_pattern("Edit", {"file_path": "/foo/bar.py"})
        assert pattern == "Edit:/foo/bar.py"

    def test_read_uses_exact_path(self):
        pattern = _hook._tool_pattern("Read", {"file_path": "/foo/bar.py"})
        assert pattern == "Read:/foo/bar.py"

    def test_write_uses_exact_path(self):
        pattern = _hook._tool_pattern("Write", {"file_path": "/foo/bar.py"})
        assert pattern == "Write:/foo/bar.py"

    def test_other_tool_uses_name_only(self):
        pattern = _hook._tool_pattern("Grep", {"pattern": "foo"})
        assert pattern == "Grep"
