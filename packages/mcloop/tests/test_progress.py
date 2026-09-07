"""Direct sessions report activity without treating heartbeats as work."""

import json
import queue
from unittest.mock import patch

import pytest

from mcloop.progress import SessionProgress


def command(kind="item.started", *, identity="item_1", code=None):
    return json.dumps(
        {
            "type": kind,
            "item": {
                "type": "command_execution",
                "id": identity,
                "command": "swift test",
                "exit_code": code,
                "status": "completed" if kind == "item.completed" else "in_progress",
                "aggregated_output": "raw command output must not appear in status",
            },
        }
    )


def test_silent_process_gets_periodic_status_without_claiming_progress():
    progress = SessionProgress("codex", 100)
    assert progress.report(129) == ""
    report = progress.report(130)
    assert "30s elapsed; no output received" in report
    assert "No tool activity reported yet" in report
    assert progress.report(159) == ""
    assert "60s elapsed" in progress.report(160)


def test_command_completion_and_activity_age_survive_silence():
    progress = SessionProgress("codex", 0)
    progress.observe(command(), 1)
    assert "Command running: swift test" in progress.report(30)
    progress.observe(command("item.completed", code=1), 35)
    report = progress.report(60)
    assert "last output 25s ago" in report
    assert "Last activity (25s ago): Command completed (exit 1): swift test" in report
    assert "raw command output" not in report
    assert "Command running" not in report
    assert "last output 55s ago" in progress.report(90)


def test_duplicate_events_do_not_refresh_activity_age_but_new_actions_do():
    progress = SessionProgress("codex", 0)
    progress.observe(command(), 1)
    progress.observe(command(), 20)
    report = progress.report(30)
    assert "last output 10s ago" in report
    assert "Last activity (29s ago)" in report
    progress.observe(command(identity="item_2"), 50)
    assert "Last activity (10s ago)" in progress.report(60)


@pytest.mark.parametrize(
    "line",
    [
        "not json",
        "[]",
        "9",
        '{"type":"item.started","item":[]}',
        '{"type":"assistant","message":null}',
        '{"type":"stream_event","event":null}',
    ],
)
def test_unrecognized_output_does_not_invent_activity(line):
    progress = SessionProgress("codex", 0)
    progress.observe(line, 5)
    assert "No tool activity reported yet" in progress.report(30)


def test_reasoning_is_not_disclosed():
    progress = SessionProgress("codex", 0)
    progress.observe(
        json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "reasoning", "text": "private reasoning text", "id": "r1"},
            }
        ),
        5,
    )
    report = progress.report(30)
    assert "private reasoning" not in report
    assert "last output 25s ago" in report


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        (
            {"type": "file_change", "status": "completed", "changes": [{"path": "docs/state.md"}]},
            "File changes (completed): docs/state.md",
        ),
        (
            {"type": "mcp_tool_call", "tool": "read_file", "status": "completed"},
            "Tool read_file: completed",
        ),
        (
            {"type": "agent_message", "text": "Checking\nthe design."},
            "Agent message: Checking the design.",
        ),
    ],
)
def test_codex_activity_types(item, expected):
    progress = SessionProgress("codex", 0)
    progress.observe(json.dumps({"type": "item.completed", "item": item}), 2)
    assert expected in progress.report(30)


def test_claude_partial_and_final_tool_events_share_activity_identity():
    progress = SessionProgress("claude", 0)
    block = {"type": "tool_use", "id": "tool1", "name": "Read", "input": {"file_path": "SPEC.md"}}
    progress.observe(
        json.dumps(
            {
                "type": "stream_event",
                "event": {"type": "content_block_start", "content_block": block},
            }
        ),
        5,
    )
    progress.observe(json.dumps({"type": "assistant", "message": {"content": [block]}}), 20)
    report = progress.report(30)
    assert "Last activity (25s ago): Tool requested: Read SPEC.md" in report


def test_pending_approval_remains_visible_and_details_are_bounded():
    progress = SessionProgress("codex", 0)
    report = progress.report(30, waiting="\x1b[31mBash:\n" + "x" * 500)
    assert "Waiting for Telegram approval: Bash: " in report
    assert "\x1b" not in report
    assert len(report) < 300


@pytest.mark.parametrize("silent", [True, False])
def test_direct_runner_reports_during_silence_and_continuous_output(
    tmp_path, capsys, monkeypatch, silent
):
    from mcloop import runner

    clock = [0.0]
    events = iter(
        [
            (1, command()),
            (31, None if silent else '{"type":"item.updated","item":{"type":"reasoning"}}'),
            (32, command("item.completed", code=0)),
            (62, None if silent else '{"type":"turn.completed"}'),
            (63, runner._SENTINEL),
        ]
    )

    class FakeQueue:
        def get(self, timeout):
            clock[0], event = next(events)
            if event is None:
                raise queue.Empty
            return event

    monkeypatch.setattr(runner.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(runner.queue, "Queue", FakeQueue)
    monkeypatch.setattr(runner, "_interrupted", False)
    with (
        patch.object(runner.threading, "Thread"),
        patch.object(runner.subprocess, "Popen") as spawn,
    ):
        process = spawn.return_value
        process.pid = 12345
        process.returncode = 0
        output, code = runner._run_session(["codex", "exec", "--json"], tmp_path, env={})
    assert code == 0
    assert '"command": "swift test"' in output
    report = capsys.readouterr().out
    assert "[codex] 31s elapsed" in report
    assert "[codex] 62s elapsed" in report
    assert "Command completed (exit 0): swift test" in report
