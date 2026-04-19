"""Tests for duplo.claude_cli."""

from __future__ import annotations

import io
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from duplo.claude_cli import ClaudeCliError, query, query_with_images


def _completed(stdout: str = "", returncode: int = 0, stderr: str = ""):
    """Build a fake subprocess.CompletedProcess."""
    return type(
        "CP",
        (),
        {"stdout": stdout, "stderr": stderr, "returncode": returncode},
    )()


class _FakePopen:
    """Mimics just enough of subprocess.Popen for query() to work.

    `poll_results` is an iterable of values returned from successive
    poll() calls; the first non-None value becomes the final returncode.
    """

    last_instance: "_FakePopen | None" = None

    def __init__(self, cmd, **kwargs):
        self.cmd = cmd
        self.kwargs = kwargs
        self.stdout = io.StringIO(self._stdout_text)
        self.stderr = io.StringIO(self._stderr_text)
        self.stdin = MagicMock()
        self.returncode: int | None = None
        self.killed = False
        self._poll_iter = iter(self._poll_results)
        _FakePopen.last_instance = self

    def poll(self):
        try:
            value = next(self._poll_iter)
        except StopIteration:
            value = self._final_returncode
        if value is not None:
            self.returncode = value
        return value

    def kill(self):
        self.killed = True
        self.returncode = -9


def _popen_factory(
    *,
    stdout_text: str = "",
    stderr_text: str = "",
    poll_results=(0,),
    final_returncode: int = 0,
    raises: type[BaseException] | None = None,
):
    """Build a Popen replacement class configured for one test."""

    class Configured(_FakePopen):
        _stdout_text = stdout_text
        _stderr_text = stderr_text
        _poll_results = list(poll_results)
        _final_returncode = final_returncode

        def __init__(self, cmd, **kwargs):
            if raises is not None:
                raise raises
            super().__init__(cmd, **kwargs)

    return Configured


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch):
    """Neutralize time.sleep so tests never actually block."""
    monkeypatch.setattr("duplo.claude_cli.time.sleep", lambda _s: None)


class TestQuery:
    def test_returns_stripped_stdout(self, monkeypatch):
        monkeypatch.setattr(
            "duplo.claude_cli.subprocess.Popen",
            _popen_factory(stdout_text="  hello  ", poll_results=[0]),
        )
        assert query("prompt") == "hello"

    def test_passes_model_flag(self, monkeypatch):
        factory = _popen_factory(stdout_text="ok", poll_results=[0])
        monkeypatch.setattr("duplo.claude_cli.subprocess.Popen", factory)
        query("prompt", model="sonnet")
        assert "--model" in factory.last_instance.cmd
        idx = factory.last_instance.cmd.index("--model")
        assert factory.last_instance.cmd[idx + 1] == "sonnet"

    def test_passes_system_prompt_flag(self, monkeypatch):
        factory = _popen_factory(stdout_text="ok", poll_results=[0])
        monkeypatch.setattr("duplo.claude_cli.subprocess.Popen", factory)
        query("prompt", system="Be helpful.")
        idx = factory.last_instance.cmd.index("--system-prompt")
        assert factory.last_instance.cmd[idx + 1] == "Be helpful."

    def test_omits_system_prompt_when_empty(self, monkeypatch):
        factory = _popen_factory(stdout_text="ok", poll_results=[0])
        monkeypatch.setattr("duplo.claude_cli.subprocess.Popen", factory)
        query("prompt")
        assert "--system-prompt" not in factory.last_instance.cmd

    def test_sends_prompt_via_stdin(self, monkeypatch):
        factory = _popen_factory(stdout_text="ok", poll_results=[0])
        monkeypatch.setattr("duplo.claude_cli.subprocess.Popen", factory)
        query("my prompt text")
        factory.last_instance.stdin.write.assert_called_once_with("my prompt text")
        factory.last_instance.stdin.close.assert_called_once()

    def test_raises_on_nonzero_exit(self, monkeypatch):
        monkeypatch.setattr(
            "duplo.claude_cli.subprocess.Popen",
            _popen_factory(stderr_text="fail", poll_results=[1], final_returncode=1),
        )
        with pytest.raises(ClaudeCliError, match="fail"):
            query("prompt")

    def test_raises_claude_cli_error_on_timeout(self, monkeypatch):
        # poll never completes; monotonic jumps past the 300s timeout.
        monkeypatch.setattr(
            "duplo.claude_cli.subprocess.Popen",
            _popen_factory(poll_results=[None, None, None, None]),
        )
        times = iter([0.0, 100.0, 301.0, 302.0])
        monkeypatch.setattr("duplo.claude_cli.time.monotonic", lambda: next(times))
        with pytest.raises(ClaudeCliError, match="timed out"):
            query("prompt")

    def test_raises_when_claude_cli_missing(self, monkeypatch):
        monkeypatch.setattr(
            "duplo.claude_cli.subprocess.Popen",
            _popen_factory(raises=FileNotFoundError()),
        )
        with pytest.raises(ClaudeCliError, match="not found"):
            query("prompt")

    def test_prints_dot_every_five_seconds_during_long_call(self, monkeypatch, capsys):
        # Simulate a 12-second LLM call: poll returns None while simulated time
        # advances 0s -> 1s -> 3s -> 5.5s -> 8s -> 11s -> 12s, then returns 0.
        monkeypatch.setattr(
            "duplo.claude_cli.subprocess.Popen",
            _popen_factory(
                stdout_text="result",
                poll_results=[None, None, None, None, None, None, 0],
            ),
        )
        fake_times = iter([0.0, 1.0, 3.0, 5.5, 8.0, 11.0, 12.0, 12.5, 13.0])
        monkeypatch.setattr("duplo.claude_cli.time.monotonic", lambda: next(fake_times))

        result = query("prompt")

        captured = capsys.readouterr()
        assert captured.err.count(".") >= 2
        assert captured.err.endswith("\n")
        assert result == "result"


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

    def test_resolves_relative_paths_to_absolute(self, tmp_path):
        rel = Path(".duplo/video_frames/frame.png")
        with patch("duplo.claude_cli.subprocess.run", return_value=_completed("ok")) as m:
            query_with_images("analyze", [rel])
        prompt = m.call_args.kwargs["input"]
        assert str(rel.resolve()) in prompt
        assert f"- {rel.resolve()}" in prompt

    def test_absolute_paths_remain_absolute(self):
        abs_path = Path("/tmp/screenshots/frame.png")
        with patch("duplo.claude_cli.subprocess.run", return_value=_completed("ok")) as m:
            query_with_images("analyze", [abs_path])
        prompt = m.call_args.kwargs["input"]
        assert f"- {abs_path.resolve()}" in prompt

    def test_raises_claude_cli_error_on_timeout(self):
        with patch(
            "duplo.claude_cli.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=300),
        ):
            with pytest.raises(ClaudeCliError, match="timed out"):
                query_with_images("go", [Path("/x.png")])
