"""Tests for maintain mode: MAINTAIN.md parser, prompt, output parsing, and loop."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from mcloop.maintain import (
    MAINTAIN_TOOLS,
    InvariantResult,
    MaintainSummary,
    _build_maintain_prompt,
    _print_maintain_summary,
    _write_maintain_log,
    parse_invariants,
    parse_maintain_output,
    run_maintain,
)

# --- parse_invariants ---


def test_parse_invariants_basic(tmp_path):
    md = tmp_path / "MAINTAIN.md"
    md.write_text(
        "# Invariants\n\n"
        "- [ ] All public functions have docstrings\n"
        "- [ ] No TODO comments in production code\n"
    )
    result = parse_invariants(md)
    assert result == [
        "All public functions have docstrings",
        "No TODO comments in production code",
    ]


def test_parse_invariants_skips_checked(tmp_path):
    md = tmp_path / "MAINTAIN.md"
    md.write_text("- [x] Already retired invariant\n- [ ] Active invariant\n- [X] Also retired\n")
    result = parse_invariants(md)
    assert result == ["Active invariant"]


def test_parse_invariants_empty_file(tmp_path):
    md = tmp_path / "MAINTAIN.md"
    md.write_text("# Invariants\n\nNo items yet.\n")
    result = parse_invariants(md)
    assert result == []


def test_parse_invariants_missing_file(tmp_path):
    result = parse_invariants(tmp_path / "MAINTAIN.md")
    assert result == []


def test_parse_invariants_ignores_non_checkbox_lines(tmp_path):
    md = tmp_path / "MAINTAIN.md"
    md.write_text(
        "# Invariants\n\nSome prose description.\n- [ ] Real invariant\nNot a checkbox\n"
    )
    result = parse_invariants(md)
    assert result == ["Real invariant"]


# --- _build_maintain_prompt ---


def test_build_maintain_prompt_contains_invariant():
    prompt = _build_maintain_prompt("All tests pass")
    assert "All tests pass" in prompt
    assert "INVARIANT:" in prompt
    assert "SATISFIED" in prompt
    assert "FIXED" in prompt
    assert "FAILED" in prompt


def test_build_maintain_prompt_no_embedded_check_commands():
    """Check commands are now passed to run_task, not embedded in the prompt."""
    prompt = _build_maintain_prompt("Invariant X")
    # The prompt should reference CHECK COMMANDS conceptually but not embed them
    assert "INVARIANT:" in prompt
    assert "MAINTAIN RESULT" in prompt


# --- parse_maintain_output ---


def test_parse_maintain_output_satisfied():
    output = (
        "Some analysis...\n"
        "--- MAINTAIN RESULT ---\n"
        "OUTCOME: SATISFIED\n"
        "DETAIL: Invariant already holds\n"
        "--- END MAINTAIN ---\n"
    )
    outcome, detail = parse_maintain_output(output)
    assert outcome == "satisfied"
    assert detail == "Invariant already holds"


def test_parse_maintain_output_fixed():
    output = (
        "--- MAINTAIN RESULT ---\n"
        "OUTCOME: FIXED\n"
        "DETAIL: Added missing docstring\n"
        "--- END MAINTAIN ---\n"
    )
    outcome, detail = parse_maintain_output(output)
    assert outcome == "fixed"
    assert detail == "Added missing docstring"


def test_parse_maintain_output_failed():
    output = (
        "--- MAINTAIN RESULT ---\n"
        "OUTCOME: FAILED\n"
        "DETAIL: Could not resolve issue\n"
        "--- END MAINTAIN ---\n"
    )
    outcome, detail = parse_maintain_output(output)
    assert outcome == "failed"
    assert detail == "Could not resolve issue"


def test_parse_maintain_output_no_marker():
    output = "Session produced no structured output"
    outcome, detail = parse_maintain_output(output)
    assert outcome == "failed"
    assert "No result marker" in detail


def test_parse_maintain_output_bad_outcome():
    output = "--- MAINTAIN RESULT ---\nOUTCOME: MAYBE\nDETAIL: unclear\n--- END MAINTAIN ---\n"
    outcome, detail = parse_maintain_output(output)
    assert outcome == "failed"
    assert detail == "unclear"


def test_parse_maintain_output_bad_outcome_no_detail():
    output = "--- MAINTAIN RESULT ---\nOUTCOME: MAYBE\n--- END MAINTAIN ---\n"
    outcome, detail = parse_maintain_output(output)
    assert outcome == "failed"
    assert "Could not parse" in detail


# --- MaintainSummary ---


def test_maintain_summary_counts():
    summary = MaintainSummary(
        results=[
            InvariantResult(text="a", outcome="satisfied"),
            InvariantResult(text="b", outcome="fixed"),
            InvariantResult(text="c", outcome="failed"),
            InvariantResult(text="d", outcome="satisfied"),
        ]
    )
    assert summary.satisfied == 2
    assert summary.fixed == 1
    assert summary.failed == 1


def test_maintain_summary_autonomous():
    summary = MaintainSummary(
        results=[
            InvariantResult(
                text="a", outcome="fixed", autonomous=True, autonomous_note="no reply"
            ),
            InvariantResult(text="b", outcome="satisfied"),
        ]
    )
    assert len(summary.autonomous_decisions) == 1
    assert summary.autonomous_decisions[0].text == "a"


# --- _write_maintain_log ---


def test_write_maintain_log_creates_file(tmp_path):
    results = [
        InvariantResult(text="inv1", outcome="satisfied"),
        InvariantResult(text="inv2", outcome="fixed"),
    ]
    _write_maintain_log(tmp_path, results)
    log_path = tmp_path / ".mcloop" / "maintain-log.json"
    assert log_path.exists()
    data = json.loads(log_path.read_text())
    assert len(data) == 1
    assert len(data[0]["results"]) == 2
    assert data[0]["results"][0]["outcome"] == "satisfied"
    assert "timestamp" in data[0]


def test_write_maintain_log_appends(tmp_path):
    mcloop_dir = tmp_path / ".mcloop"
    mcloop_dir.mkdir()
    log_path = mcloop_dir / "maintain-log.json"
    log_path.write_text(json.dumps([{"timestamp": "old", "results": []}]))
    results = [InvariantResult(text="new", outcome="failed")]
    _write_maintain_log(tmp_path, results)
    data = json.loads(log_path.read_text())
    assert len(data) == 2


def test_write_maintain_log_autonomous_flag(tmp_path):
    results = [
        InvariantResult(
            text="auto-inv",
            outcome="fixed",
            autonomous=True,
            autonomous_note="decided independently",
        ),
    ]
    _write_maintain_log(tmp_path, results)
    log_path = tmp_path / ".mcloop" / "maintain-log.json"
    data = json.loads(log_path.read_text())
    entry = data[0]["results"][0]
    assert entry["autonomous"] is True
    assert entry["autonomous_note"] == "decided independently"


# --- run_maintain ---


def _mock_run_task(output_text, success=True, exit_code=0):
    """Create a mock RunResult."""
    mock = MagicMock()
    mock.success = success
    mock.output = output_text
    mock.exit_code = exit_code
    mock.log_path = Path("/tmp/fake.log")
    return mock


@patch("mcloop.maintain._push_or_die")
@patch("mcloop.maintain._ensure_git")
@patch("mcloop.maintain._kill_orphan_sessions")
@patch("mcloop.maintain._checkpoint")
@patch("mcloop.maintain.register_signal_handlers")
@patch("mcloop.maintain.notify")
@patch("mcloop.maintain.get_check_commands", return_value=["pytest"])
@patch("mcloop.maintain._has_meaningful_changes", return_value=False)
@patch("mcloop.maintain.run_task")
def test_run_maintain_satisfied(
    mock_run_task,
    mock_changes,
    mock_checks,
    mock_notify,
    mock_signals,
    mock_checkpoint,
    mock_orphans,
    mock_git,
    mock_push,
    tmp_path,
):
    md = tmp_path / "MAINTAIN.md"
    md.write_text("- [ ] All tests pass\n")
    (tmp_path / ".git").mkdir()

    mock_run_task.return_value = _mock_run_task(
        "--- MAINTAIN RESULT ---\n"
        "OUTCOME: SATISFIED\n"
        "DETAIL: Tests already pass\n"
        "--- END MAINTAIN ---\n"
    )

    summary = run_maintain(md, cli="claude")
    assert summary.satisfied == 1
    assert summary.fixed == 0
    assert summary.failed == 0
    # Verify check_commands are passed through to run_task
    call_kwargs = mock_run_task.call_args
    assert call_kwargs.kwargs.get("check_commands") == ["pytest"]


@patch("mcloop.maintain._push_or_die")
@patch("mcloop.maintain._ensure_git")
@patch("mcloop.maintain._kill_orphan_sessions")
@patch("mcloop.maintain._checkpoint")
@patch("mcloop.maintain.register_signal_handlers")
@patch("mcloop.maintain.notify")
@patch("mcloop.maintain.get_check_commands", return_value=["pytest"])
@patch("mcloop.maintain._has_meaningful_changes", return_value=True)
@patch("mcloop.maintain._commit")
@patch("mcloop.maintain.run_task")
def test_run_maintain_fixed_commits(
    mock_run_task,
    mock_commit,
    mock_changes,
    mock_checks,
    mock_notify,
    mock_signals,
    mock_checkpoint,
    mock_orphans,
    mock_git,
    mock_push,
    tmp_path,
):
    md = tmp_path / "MAINTAIN.md"
    md.write_text("- [ ] No unused imports\n")
    (tmp_path / ".git").mkdir()

    mock_run_task.return_value = _mock_run_task(
        "--- MAINTAIN RESULT ---\n"
        "OUTCOME: FIXED\n"
        "DETAIL: Removed unused imports\n"
        "--- END MAINTAIN ---\n"
    )

    summary = run_maintain(md, cli="claude")
    assert summary.fixed == 1
    mock_commit.assert_called_once()
    commit_msg = mock_commit.call_args[0][1]
    assert "maintain:" in commit_msg


@patch("mcloop.maintain._push_or_die")
@patch("mcloop.maintain._ensure_git")
@patch("mcloop.maintain._kill_orphan_sessions")
@patch("mcloop.maintain._checkpoint")
@patch("mcloop.maintain.register_signal_handlers")
@patch("mcloop.maintain.notify")
@patch("mcloop.maintain.get_check_commands", return_value=["pytest"])
@patch("mcloop.maintain._has_meaningful_changes", return_value=False)
@patch("mcloop.maintain.run_task")
def test_run_maintain_session_failure_does_not_stop_run(
    mock_run_task,
    mock_changes,
    mock_checks,
    mock_notify,
    mock_signals,
    mock_checkpoint,
    mock_orphans,
    mock_git,
    mock_push,
    tmp_path,
):
    md = tmp_path / "MAINTAIN.md"
    md.write_text("- [ ] First invariant\n- [ ] Second invariant\n")
    (tmp_path / ".git").mkdir()

    # First fails, second succeeds
    mock_run_task.side_effect = [
        _mock_run_task("", success=False, exit_code=1),
        _mock_run_task(
            "--- MAINTAIN RESULT ---\nOUTCOME: SATISFIED\nDETAIL: OK\n--- END MAINTAIN ---\n"
        ),
    ]

    summary = run_maintain(md, cli="claude")
    assert summary.failed == 1
    assert summary.satisfied == 1
    assert len(summary.results) == 2


@patch("mcloop.maintain._push_or_die")
@patch("mcloop.maintain._ensure_git")
@patch("mcloop.maintain._kill_orphan_sessions")
@patch("mcloop.maintain._checkpoint")
@patch("mcloop.maintain.register_signal_handlers")
@patch("mcloop.maintain.notify")
@patch("mcloop.maintain.get_check_commands", return_value=[])
@patch("mcloop.maintain.run_task")
def test_run_maintain_no_invariants(
    mock_run_task,
    mock_checks,
    mock_notify,
    mock_signals,
    mock_checkpoint,
    mock_orphans,
    mock_git,
    mock_push,
    tmp_path,
):
    md = tmp_path / "MAINTAIN.md"
    md.write_text("# Invariants\n\nNo items.\n")
    (tmp_path / ".git").mkdir()

    summary = run_maintain(md, cli="claude")
    assert summary.satisfied == 0
    assert summary.fixed == 0
    assert summary.failed == 0
    mock_run_task.assert_not_called()


# --- _cmd_maintain via _main ---


def test_maintain_subcommand_parse():
    from mcloop.main import _parse_args

    with patch("sys.argv", ["mcloop", "maintain"]):
        args = _parse_args()
    assert args.command == "maintain"


# --- _print_maintain_summary ---


def test_print_summary_output(capsys):
    summary = MaintainSummary(
        results=[
            InvariantResult(text="a", outcome="satisfied"),
            InvariantResult(
                text="b", outcome="fixed", autonomous=True, autonomous_note="no reply"
            ),
            InvariantResult(text="c", outcome="failed"),
        ]
    )
    _print_maintain_summary(summary)
    out = capsys.readouterr().out
    assert "1 satisfied" in out
    assert "1 fixed" in out
    assert "1 failed" in out
    assert "Autonomous" in out
    assert "no reply" in out


# --- MAINTAIN_TOOLS ---


def test_maintain_tools_includes_webfetch():
    """MAINTAIN_TOOLS must include WebFetch for external state verification."""
    tools = MAINTAIN_TOOLS.split(",")
    assert "WebFetch" in tools


def test_maintain_tools_includes_defaults():
    """MAINTAIN_TOOLS must include all default tools."""
    tools = set(MAINTAIN_TOOLS.split(","))
    defaults = {"Edit", "Write", "Bash", "Read", "Glob", "Grep"}
    assert defaults.issubset(tools)


@patch("mcloop.maintain._push_or_die")
@patch("mcloop.maintain._ensure_git")
@patch("mcloop.maintain._kill_orphan_sessions")
@patch("mcloop.maintain._checkpoint")
@patch("mcloop.maintain.register_signal_handlers")
@patch("mcloop.maintain.notify")
@patch("mcloop.maintain.get_check_commands", return_value=["pytest"])
@patch("mcloop.maintain._has_meaningful_changes", return_value=False)
@patch("mcloop.maintain.run_task")
def test_run_maintain_passes_allowed_tools(
    mock_run_task,
    mock_changes,
    mock_checks,
    mock_notify,
    mock_signals,
    mock_checkpoint,
    mock_orphans,
    mock_git,
    mock_push,
    tmp_path,
):
    """run_maintain passes MAINTAIN_TOOLS as allowed_tools to run_task."""
    md = tmp_path / "MAINTAIN.md"
    md.write_text("- [ ] Some invariant\n")
    (tmp_path / ".git").mkdir()

    mock_run_task.return_value = _mock_run_task(
        "--- MAINTAIN RESULT ---\nOUTCOME: SATISFIED\nDETAIL: OK\n--- END MAINTAIN ---\n"
    )

    run_maintain(md, cli="claude")
    call_kwargs = mock_run_task.call_args
    assert call_kwargs.kwargs.get("allowed_tools") == MAINTAIN_TOOLS


# --- stop_after_one ---


@patch("mcloop.maintain._push_or_die")
@patch("mcloop.maintain._ensure_git")
@patch("mcloop.maintain._kill_orphan_sessions")
@patch("mcloop.maintain._checkpoint")
@patch("mcloop.maintain.register_signal_handlers")
@patch("mcloop.maintain.notify")
@patch("mcloop.maintain.get_check_commands", return_value=["pytest"])
@patch("mcloop.maintain._has_meaningful_changes", return_value=False)
@patch("mcloop.maintain.run_task")
def test_run_maintain_stop_after_one_satisfied(
    mock_run_task,
    mock_changes,
    mock_checks,
    mock_notify,
    mock_signals,
    mock_checkpoint,
    mock_orphans,
    mock_git,
    mock_push,
    tmp_path,
):
    """stop_after_one exits after the first satisfied invariant, skipping the rest."""
    md = tmp_path / "MAINTAIN.md"
    md.write_text("- [ ] First invariant\n- [ ] Second invariant\n")
    (tmp_path / ".git").mkdir()

    mock_run_task.return_value = _mock_run_task(
        "--- MAINTAIN RESULT ---\nOUTCOME: SATISFIED\nDETAIL: OK\n--- END MAINTAIN ---\n"
    )

    summary = run_maintain(md, cli="claude", stop_after_one=True)

    # Only the first invariant was processed
    assert len(summary.results) == 1
    assert summary.satisfied == 1
    assert mock_run_task.call_count == 1

    # Sends the same stop notification as plan mode
    notify_calls = [c.args[0] for c in mock_notify.call_args_list]
    assert any("Stopped after one task" in msg for msg in notify_calls)

    # Log was still written
    log_path = tmp_path / ".mcloop" / "maintain-log.json"
    assert log_path.exists()


@patch("mcloop.maintain._push_or_die")
@patch("mcloop.maintain._ensure_git")
@patch("mcloop.maintain._kill_orphan_sessions")
@patch("mcloop.maintain._checkpoint")
@patch("mcloop.maintain.register_signal_handlers")
@patch("mcloop.maintain.notify")
@patch("mcloop.maintain.get_check_commands", return_value=["pytest"])
@patch("mcloop.maintain._has_meaningful_changes", return_value=True)
@patch("mcloop.maintain._commit")
@patch("mcloop.maintain.run_task")
def test_run_maintain_stop_after_one_fixed(
    mock_run_task,
    mock_commit,
    mock_changes,
    mock_checks,
    mock_notify,
    mock_signals,
    mock_checkpoint,
    mock_orphans,
    mock_git,
    mock_push,
    tmp_path,
):
    """stop_after_one exits after the first fixed invariant (post-commit boundary)."""
    md = tmp_path / "MAINTAIN.md"
    md.write_text("- [ ] First invariant\n- [ ] Second invariant\n")
    (tmp_path / ".git").mkdir()

    mock_run_task.return_value = _mock_run_task(
        "--- MAINTAIN RESULT ---\nOUTCOME: FIXED\nDETAIL: Fixed it\n--- END MAINTAIN ---\n"
    )

    summary = run_maintain(md, cli="claude", stop_after_one=True)

    # Only the first invariant was processed; commit happened then exit
    assert len(summary.results) == 1
    assert summary.fixed == 1
    mock_commit.assert_called_once()
    assert mock_run_task.call_count == 1

    # Distinct stop notification
    notify_calls = [c.args[0] for c in mock_notify.call_args_list]
    assert any("Stopped after one task" in msg for msg in notify_calls)


@patch("mcloop.maintain._push_or_die")
@patch("mcloop.maintain._ensure_git")
@patch("mcloop.maintain._kill_orphan_sessions")
@patch("mcloop.maintain._checkpoint")
@patch("mcloop.maintain.register_signal_handlers")
@patch("mcloop.maintain.notify")
@patch("mcloop.maintain.get_check_commands", return_value=["pytest"])
@patch("mcloop.maintain._has_meaningful_changes", return_value=False)
@patch("mcloop.maintain.run_task")
def test_run_maintain_stop_after_one_skips_failed(
    mock_run_task,
    mock_changes,
    mock_checks,
    mock_notify,
    mock_signals,
    mock_checkpoint,
    mock_orphans,
    mock_git,
    mock_push,
    tmp_path,
):
    """stop_after_one does NOT exit on a failed invariant; continues to the next."""
    md = tmp_path / "MAINTAIN.md"
    md.write_text("- [ ] First invariant\n- [ ] Second invariant\n")
    (tmp_path / ".git").mkdir()

    mock_run_task.side_effect = [
        # First invariant fails
        _mock_run_task("", success=False, exit_code=1),
        # Second invariant succeeds — this triggers the stop
        _mock_run_task(
            "--- MAINTAIN RESULT ---\nOUTCOME: SATISFIED\nDETAIL: OK\n--- END MAINTAIN ---\n"
        ),
    ]

    summary = run_maintain(md, cli="claude", stop_after_one=True)

    # Both invariants were processed (first failed, second satisfied)
    assert len(summary.results) == 2
    assert summary.failed == 1
    assert summary.satisfied == 1
    assert mock_run_task.call_count == 2

    # Stop notification sent after second (successful) invariant
    notify_calls = [c.args[0] for c in mock_notify.call_args_list]
    assert any("Stopped after one task" in msg for msg in notify_calls)
