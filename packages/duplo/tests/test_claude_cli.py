"""Tests for duplo.claude_cli."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from duplo.claude_cli import ClaudeCliError, query, query_with_images


def _completed(stdout: str = "", returncode: int = 0, stderr: str = ""):
    """Build a fake subprocess.CompletedProcess."""
    return type(
        "CP",
        (),
        {"stdout": stdout, "stderr": stderr, "returncode": returncode},
    )()


class TestQuery:
    def test_returns_stripped_stdout(self):
        with patch("duplo.claude_cli.subprocess.run", return_value=_completed("  hello  ")):
            assert query("prompt") == "hello"

    def test_passes_model_flag(self):
        with patch("duplo.claude_cli.subprocess.run", return_value=_completed("ok")) as m:
            query("prompt", model="sonnet")
        cmd = m.call_args[0][0]
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "sonnet"

    def test_passes_system_prompt_flag(self):
        with patch("duplo.claude_cli.subprocess.run", return_value=_completed("ok")) as m:
            query("prompt", system="Be helpful.")
        cmd = m.call_args[0][0]
        idx = cmd.index("--system-prompt")
        assert cmd[idx + 1] == "Be helpful."

    def test_omits_system_prompt_when_empty(self):
        with patch("duplo.claude_cli.subprocess.run", return_value=_completed("ok")) as m:
            query("prompt")
        cmd = m.call_args[0][0]
        assert "--system-prompt" not in cmd

    def test_sends_prompt_via_stdin(self):
        with patch("duplo.claude_cli.subprocess.run", return_value=_completed("ok")) as m:
            query("my prompt text")
        assert m.call_args.kwargs["input"] == "my prompt text"

    def test_raises_on_nonzero_exit(self):
        with patch(
            "duplo.claude_cli.subprocess.run",
            return_value=_completed(returncode=1, stderr="fail"),
        ):
            with pytest.raises(ClaudeCliError, match="fail"):
                query("prompt")

    def test_raises_claude_cli_error_on_timeout(self):
        with patch(
            "duplo.claude_cli.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=300),
        ):
            with pytest.raises(ClaudeCliError, match="timed out"):
                query("prompt")


class TestQueryWithImages:
    def test_includes_image_paths_in_prompt(self):
        paths = [Path("/tmp/a.png"), Path("/tmp/b.png")]
        with patch("duplo.claude_cli.subprocess.run", return_value=_completed("ok")) as m:
            query_with_images("analyze", paths)
        prompt = m.call_args.kwargs["input"]
        assert "/tmp/a.png" in prompt
        assert "/tmp/b.png" in prompt

    def test_enables_read_tool(self):
        with patch("duplo.claude_cli.subprocess.run", return_value=_completed("ok")) as m:
            query_with_images("analyze", [Path("/tmp/x.png")])
        cmd = m.call_args[0][0]
        idx = cmd.index("--tools")
        assert cmd[idx + 1] == "Read"

    def test_returns_stripped_stdout(self):
        with patch("duplo.claude_cli.subprocess.run", return_value=_completed("  result  ")):
            assert query_with_images("go", [Path("/x.png")]) == "result"

    def test_raises_on_nonzero_exit(self):
        with patch(
            "duplo.claude_cli.subprocess.run",
            return_value=_completed(returncode=1, stderr="err"),
        ):
            with pytest.raises(ClaudeCliError, match="err"):
                query_with_images("go", [Path("/x.png")])

    def test_raises_claude_cli_error_on_timeout(self):
        with patch(
            "duplo.claude_cli.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=300),
        ):
            with pytest.raises(ClaudeCliError, match="timed out"):
                query_with_images("go", [Path("/x.png")])
