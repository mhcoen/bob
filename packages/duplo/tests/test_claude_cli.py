"""Tests for duplo.claude_cli."""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from duplo import call_log
from duplo.claude_cli import ClaudeCliError, query, query_with_images


def _completed(stdout: str = "", returncode: int = 0, stderr: str = ""):
    """Build a fake subprocess.CompletedProcess."""
    return type(
        "CP",
        (),
        {"stdout": stdout, "stderr": stderr, "returncode": returncode},
    )()


def _stream_json(
    *,
    text: str,
    input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
    output_tokens: int = 0,
    with_result: bool = True,
) -> str:
    """Build one turn of ``claude --output-format stream-json`` stdout."""
    lines = [
        {"type": "system", "subtype": "init"},
        {
            "type": "stream_event",
            "event": {
                "type": "message_start",
                "message": {
                    "usage": {
                        "input_tokens": input_tokens,
                        "cache_creation_input_tokens": cache_creation_input_tokens,
                        "cache_read_input_tokens": cache_read_input_tokens,
                        "output_tokens": 1,
                    }
                },
            },
        },
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": text},
            },
        },
        {
            "type": "stream_event",
            "event": {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": output_tokens},
            },
        },
        {"type": "stream_event", "event": {"type": "message_stop"}},
    ]
    if with_result:
        lines.append({"type": "result", "subtype": "success", "result": text})
    return "\n".join(json.dumps(line) for line in lines) + "\n"


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
        # poll never completes; monotonic jumps past the timeout.
        # Retries up to 3 times, so supply monotonic values for every attempt.
        monkeypatch.setattr(
            "duplo.claude_cli.subprocess.Popen",
            _popen_factory(poll_results=[None, None, None, None]),
        )
        times = iter([0.0, 100.0, 601.0] * 3)
        monkeypatch.setattr("duplo.claude_cli.time.monotonic", lambda: next(times))
        with pytest.raises(ClaudeCliError, match="timed out"):
            query("prompt")

    def test_retries_on_failure_then_succeeds(self, monkeypatch):
        """query() retries on ClaudeCliError and returns a later successful attempt."""
        calls = {"n": 0}
        fail_class = _popen_factory(stderr_text="boom", poll_results=[1], final_returncode=1)
        success_class = _popen_factory(stdout_text="ok", poll_results=[0])

        def dispatch(cmd, **kwargs):
            calls["n"] += 1
            cls = fail_class if calls["n"] <= 2 else success_class
            return cls(cmd, **kwargs)

        monkeypatch.setattr("duplo.claude_cli.subprocess.Popen", dispatch)
        assert query("prompt") == "ok"
        assert calls["n"] == 3

    def test_raises_after_three_failed_attempts(self, monkeypatch):
        """query() re-raises ClaudeCliError when every attempt fails."""
        calls = {"n": 0}
        fail_class = _popen_factory(stderr_text="boom", poll_results=[1], final_returncode=1)

        def dispatch(cmd, **kwargs):
            calls["n"] += 1
            return fail_class(cmd, **kwargs)

        monkeypatch.setattr("duplo.claude_cli.subprocess.Popen", dispatch)
        with pytest.raises(ClaudeCliError, match="boom"):
            query("prompt")
        assert calls["n"] == 3

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

    def test_retries_on_timeout_then_succeeds(self):
        """query_with_images() retries TimeoutExpired and returns a later success."""
        side_effects = [
            subprocess.TimeoutExpired(cmd="claude", timeout=300),
            subprocess.TimeoutExpired(cmd="claude", timeout=300),
            _completed("result"),
        ]
        with patch("duplo.claude_cli.subprocess.run", side_effect=side_effects) as m:
            assert query_with_images("go", [Path("/x.png")]) == "result"
        assert m.call_count == 3

    def test_raises_after_three_timeouts(self):
        """query_with_images() re-raises ClaudeCliError after 3 timeouts."""
        side_effects = [
            subprocess.TimeoutExpired(cmd="claude", timeout=300),
            subprocess.TimeoutExpired(cmd="claude", timeout=300),
            subprocess.TimeoutExpired(cmd="claude", timeout=300),
        ]
        with patch("duplo.claude_cli.subprocess.run", side_effect=side_effects) as m:
            with pytest.raises(ClaudeCliError, match="timed out"):
                query_with_images("go", [Path("/x.png")])
        assert m.call_count == 3


def _records(tmp_path):
    path = tmp_path / call_log.LOGS_ROOT / "run-test" / call_log.CALLS_FILENAME
    with open(path) as fh:
        return [json.loads(line) for line in fh if line.strip()]


class TestQueryCallLogging:
    """query()/query_with_images() emit one full-fidelity call_log record per call."""

    def test_success_records_full_fidelity_fields(self, tmp_path, monkeypatch):
        call_log.start_run(target_dir=tmp_path, run_id="run-test")
        monkeypatch.setattr(
            "duplo.claude_cli.subprocess.Popen",
            _popen_factory(stdout_text="  the answer  ", poll_results=[0]),
        )
        query(
            "a very long prompt " * 100,
            system="be precise " * 50,
            model="opus",
            call_site="phase_003:gap_detect",
        )
        rec = _records(tmp_path)[0]
        assert rec["call_site"] == "phase_003:gap_detect"
        assert rec["model"] == "opus"
        assert rec["outcome"] == "ok"
        assert rec["attempt"] == 1
        assert rec["prompt"] == "a very long prompt " * 100  # not truncated
        assert rec["system"] == "be precise " * 50  # not truncated
        assert rec["response"] == "the answer"
        assert isinstance(rec["duration_seconds"], (int, float))
        assert "error" not in rec

    def test_call_site_defaults_to_empty(self, tmp_path, monkeypatch):
        call_log.start_run(target_dir=tmp_path, run_id="run-test")
        monkeypatch.setattr(
            "duplo.claude_cli.subprocess.Popen",
            _popen_factory(stdout_text="ok", poll_results=[0]),
        )
        query("p")
        assert _records(tmp_path)[0]["call_site"] == ""

    def test_records_successful_attempt_number_after_retries(self, tmp_path, monkeypatch):
        call_log.start_run(target_dir=tmp_path, run_id="run-test")
        calls = {"n": 0}
        fail_class = _popen_factory(stderr_text="boom", poll_results=[1], final_returncode=1)
        success_class = _popen_factory(stdout_text="ok", poll_results=[0])

        def dispatch(cmd, **kwargs):
            calls["n"] += 1
            cls = fail_class if calls["n"] <= 1 else success_class
            return cls(cmd, **kwargs)

        monkeypatch.setattr("duplo.claude_cli.subprocess.Popen", dispatch)
        query("p", call_site="cs")
        rec = _records(tmp_path)[0]
        assert rec["outcome"] == "ok"
        assert rec["attempt"] == 2

    def test_nonzero_exit_records_error_outcome(self, tmp_path, monkeypatch):
        call_log.start_run(target_dir=tmp_path, run_id="run-test")
        monkeypatch.setattr(
            "duplo.claude_cli.subprocess.Popen",
            _popen_factory(stderr_text="kaboom", poll_results=[1], final_returncode=1),
        )
        with pytest.raises(ClaudeCliError):
            query("p", call_site="cs")
        rec = _records(tmp_path)[0]
        assert rec["call_site"] == "cs"
        assert rec["outcome"] == "error"
        assert "kaboom" in rec["error"]
        assert rec["attempt"] == 3
        assert "response" not in rec

    def test_timeout_records_timeout_outcome(self, tmp_path, monkeypatch):
        call_log.start_run(target_dir=tmp_path, run_id="run-test")
        monkeypatch.setattr(
            "duplo.claude_cli.subprocess.Popen",
            _popen_factory(poll_results=[None, None, None, None]),
        )
        times = iter([0.0, 100.0, 601.0] * 3)
        monkeypatch.setattr("duplo.claude_cli.time.monotonic", lambda: next(times))
        with pytest.raises(ClaudeCliError):
            query("p")
        rec = _records(tmp_path)[0]
        assert rec["outcome"] == "timeout"
        assert "timed out" in rec["error"]

    def test_query_with_images_records_outcome_and_call_site(self, tmp_path):
        call_log.start_run(target_dir=tmp_path, run_id="run-test")
        with patch("duplo.claude_cli.subprocess.run", return_value=_completed("done")):
            query_with_images("analyze", [Path("/tmp/a.png")], call_site="phase_002:frames")
        rec = _records(tmp_path)[0]
        assert rec["call_site"] == "phase_002:frames"
        assert rec["outcome"] == "ok"
        assert rec["attempt"] == 1
        assert rec["response"] == "done"
        assert rec["extra"]["image_paths"] == ["/tmp/a.png"]


class TestStreamJsonUsage:
    """query() emits stream-json and records extracted token usage."""

    def test_uses_stream_json_output_format(self, monkeypatch):
        factory = _popen_factory(
            stdout_text=_stream_json(text="hi", input_tokens=1, output_tokens=1),
            poll_results=[0],
        )
        monkeypatch.setattr("duplo.claude_cli.subprocess.Popen", factory)
        query("prompt")
        cmd = factory.last_instance.cmd
        idx = cmd.index("--output-format")
        assert cmd[idx + 1] == "stream-json"
        assert "--verbose" in cmd
        assert "--include-partial-messages" in cmd

    def test_reconstructs_response_from_stream(self, monkeypatch):
        monkeypatch.setattr(
            "duplo.claude_cli.subprocess.Popen",
            _popen_factory(stdout_text=_stream_json(text="the answer"), poll_results=[0]),
        )
        assert query("prompt") == "the answer"

    def test_records_token_usage(self, tmp_path, monkeypatch):
        call_log.start_run(target_dir=tmp_path, run_id="run-test")
        monkeypatch.setattr(
            "duplo.claude_cli.subprocess.Popen",
            _popen_factory(
                stdout_text=_stream_json(
                    text="ok",
                    input_tokens=10,
                    cache_creation_input_tokens=20,
                    cache_read_input_tokens=30,
                    output_tokens=42,
                ),
                poll_results=[0],
            ),
        )
        query("prompt", call_site="cs")
        usage = _records(tmp_path)[0]["usage"]
        assert usage == {
            "input_tokens": 10,
            "cache_creation_input_tokens": 20,
            "cache_read_input_tokens": 30,
            "output_tokens": 42,
        }

    def test_sums_usage_across_turns(self, tmp_path, monkeypatch):
        turn1 = _stream_json(
            text="part1 ",
            input_tokens=10,
            cache_creation_input_tokens=5,
            cache_read_input_tokens=100,
            output_tokens=7,
            with_result=False,
        )
        turn2 = _stream_json(
            text="part2",
            input_tokens=3,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=120,
            output_tokens=11,
        )
        call_log.start_run(target_dir=tmp_path, run_id="run-test")
        monkeypatch.setattr(
            "duplo.claude_cli.subprocess.Popen",
            _popen_factory(stdout_text=turn1 + turn2, poll_results=[0]),
        )
        query("prompt")
        usage = _records(tmp_path)[0]["usage"]
        assert usage == {
            "input_tokens": 13,
            "cache_creation_input_tokens": 5,
            "cache_read_input_tokens": 220,
            "output_tokens": 18,
        }

    def test_falls_back_to_raw_text_without_usage(self, tmp_path, monkeypatch):
        """Non-stream-json output records the call with no token counts."""
        call_log.start_run(target_dir=tmp_path, run_id="run-test")
        monkeypatch.setattr(
            "duplo.claude_cli.subprocess.Popen",
            _popen_factory(stdout_text="  plain text reply  ", poll_results=[0]),
        )
        assert query("prompt") == "plain text reply"
        rec = _records(tmp_path)[0]
        assert rec["response"] == "plain text reply"
        assert "usage" not in rec

    def test_query_with_images_records_usage(self, tmp_path):
        call_log.start_run(target_dir=tmp_path, run_id="run-test")
        stdout = _stream_json(text="done", input_tokens=4, output_tokens=9)
        with patch("duplo.claude_cli.subprocess.run", return_value=_completed(stdout)):
            query_with_images("analyze", [Path("/tmp/a.png")])
        rec = _records(tmp_path)[0]
        assert rec["response"] == "done"
        assert rec["usage"]["input_tokens"] == 4
        assert rec["usage"]["output_tokens"] == 9
