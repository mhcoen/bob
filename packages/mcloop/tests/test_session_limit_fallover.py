"""Regression tests for T-000027: rate-limit fail-open in sub-session paths.

Before the fix, run_audit / run_bug_verify / run_bug_fix / run_post_fix_review
/ run_diagnostic (and batch sessions mid-run) treated a 429 / session-limit
rejection as an ordinary failure, because their consumers branched only on
RunResult.success / .exit_code and never inspected .output. These tests pin
the new behavior: the limit is DETECTED and handled (wait / fallover, and
defer-as-inconclusive when the bounded budget is exhausted) rather than
becoming a hard failure -- while a genuine non-limit failure still fails.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from mcloop.audit import AuditResult, _run_audit_fix_cycle
from mcloop.ratelimit import (
    RateLimitState,
    SessionOutcome,
    classify_session_result,
    run_session_with_fallover,
)
from mcloop.runner import (
    RunResult,
    run_audit,
    run_bug_fix,
    run_bug_verify,
    run_diagnostic,
    run_post_fix_review,
)

# Modeled on the real Opus 5-hour-limit output: a stream-json rate_limit_event
# metadata line (which ratelimit._strip_metadata_lines deliberately ignores)
# followed by the actual error result carrying the session-limit text.
LIMIT_OUTPUT = (
    '{"type":"system","subtype":"init",'
    '"rate_limit_event":{"rateLimitType":"five_hour","status":"rejected"}}\n'
    '{"type":"result","subtype":"error_during_execution","is_error":true,'
    '"result":"API Error: 429 You\'ve hit your session limit. Resets 5pm."}\n'
)

GENUINE_FAILURE_OUTPUT = (
    '{"type":"result","subtype":"error","is_error":true,'
    '"result":"Traceback: NameError: name \'foo\' is not defined"}\n'
)


def _result(output: str, exit_code: int, *, tmp: Path) -> RunResult:
    return RunResult(
        success=exit_code == 0,
        output=output,
        exit_code=exit_code,
        log_path=tmp / "session.log",
    )


# ---------------------------------------------------------------------------
# classify_session_result
# ---------------------------------------------------------------------------


def test_classify_ok_on_success():
    assert classify_session_result("anything", 0, True) == "ok"


def test_classify_limited_on_session_limit_text():
    assert classify_session_result(LIMIT_OUTPUT, 1, False) == "limited"


def test_classify_limited_on_429():
    assert classify_session_result("HTTP 429 too many requests", 1, False) == "limited"


def test_classify_failed_on_genuine_error():
    assert classify_session_result(GENUINE_FAILURE_OUTPUT, 1, False) == "failed"


# ---------------------------------------------------------------------------
# run_session_with_fallover (the shared helper)
# ---------------------------------------------------------------------------


def test_helper_ok_runs_once(tmp_path):
    calls = []

    def run_fn(cli):
        calls.append(cli)
        return _result("done", 0, tmp=tmp_path)

    out = run_session_with_fallover(run_fn, state=RateLimitState(), context="t")
    assert isinstance(out, SessionOutcome)
    assert out.status == "ok"
    assert len(calls) == 1


def test_helper_genuine_failure_runs_once(tmp_path):
    calls = []

    def run_fn(cli):
        calls.append(cli)
        return _result(GENUINE_FAILURE_OUTPUT, 1, tmp=tmp_path)

    out = run_session_with_fallover(run_fn, state=RateLimitState(), context="t")
    assert out.status == "failed"
    assert len(calls) == 1  # no retry loop on a real failure


def test_helper_defers_after_bounded_retries(tmp_path):
    calls = []

    def run_fn(cli):
        calls.append(cli)
        return _result(LIMIT_OUTPUT, 1, tmp=tmp_path)

    slept = []
    state = RateLimitState()
    out = run_session_with_fallover(
        run_fn,
        state=state,
        context="audit",
        max_attempts=3,
        sleep_fn=slept.append,
    )
    assert out.status == "deferred"  # NOT "failed"
    assert len(calls) == 3  # bounded, did not loop forever
    assert state.is_limited("claude")
    assert slept  # waited for reset between attempts


def test_helper_recovers_after_wait(tmp_path):
    seq = [LIMIT_OUTPUT, "recovered"]
    codes = [1, 0]
    calls = {"n": 0}

    def run_fn(cli):
        i = calls["n"]
        calls["n"] += 1
        return _result(seq[i], codes[i], tmp=tmp_path)

    out = run_session_with_fallover(
        run_fn,
        state=RateLimitState(),
        context="t",
        sleep_fn=lambda _s: None,
    )
    assert out.status == "ok"
    assert calls["n"] == 2  # one limited attempt, then success


def test_helper_surfaces_limit_to_stdout(tmp_path, capsys):
    def run_fn(cli):
        return _result(LIMIT_OUTPUT, 1, tmp=tmp_path)

    run_session_with_fallover(
        run_fn,
        state=RateLimitState(),
        context="audit",
        max_attempts=1,
        sleep_fn=lambda _s: None,
    )
    captured = capsys.readouterr().out
    assert "rate/session-limited" in captured
    assert "audit" in captured


def test_helper_echo_fn_used_when_provided(tmp_path):
    lines = []

    def run_fn(cli):
        return _result(LIMIT_OUTPUT, 1, tmp=tmp_path)

    run_session_with_fallover(
        run_fn,
        state=RateLimitState(),
        context="bug-fix",
        max_attempts=1,
        echo_fn=lines.append,
        sleep_fn=lambda _s: None,
    )
    assert any("rate/session-limited" in line for line in lines)


# ---------------------------------------------------------------------------
# Each of the five runner sub-session paths, exercised end-to-end through the
# helper with the subprocess mocked. A limit must be DETECTED + handled
# (deferred), and a genuine failure must still surface as "failed".
# ---------------------------------------------------------------------------

_SUBSESSION_PATHS = {
    "audit": lambda pd, ld: run_audit(pd, ld, existing_bugs=""),
    "bug-verify": lambda pd, ld: run_bug_verify(pd, ld, "## bug\nsome bug"),
    "bug-fix": lambda pd, ld: run_bug_fix(pd, ld),
    "post-fix-review": lambda pd, ld: run_post_fix_review(pd, ld, "bug", "diff"),
    "diagnostic": lambda pd, ld: run_diagnostic(
        pd, ld, {"exception_type": "ValueError", "description": "x"}
    ),
}


@pytest.mark.parametrize("name", list(_SUBSESSION_PATHS))
def test_subsession_limit_is_detected_and_deferred(name, tmp_path):
    run_path = _SUBSESSION_PATHS[name]

    with (
        patch("mcloop.runner._run_session", return_value=(LIMIT_OUTPUT, 1)),
        patch("mcloop.ratelimit.time.sleep", lambda _s: None),
    ):
        out = run_session_with_fallover(
            lambda _cli: run_path(tmp_path, tmp_path),
            state=RateLimitState(),
            context=name,
            max_attempts=2,
        )
    # The limit was recognized rather than treated as an ordinary failure.
    assert out.status == "deferred"


@pytest.mark.parametrize("name", list(_SUBSESSION_PATHS))
def test_subsession_genuine_failure_still_fails(name, tmp_path):
    run_path = _SUBSESSION_PATHS[name]

    with patch("mcloop.runner._run_session", return_value=(GENUINE_FAILURE_OUTPUT, 1)):
        out = run_session_with_fallover(
            lambda _cli: run_path(tmp_path, tmp_path),
            state=RateLimitState(),
            context=name,
            max_attempts=2,
        )
    assert out.status == "failed"  # the fix must not swallow real failures


# ---------------------------------------------------------------------------
# Audit cycle: a limit defers (inconclusive), NOT a terminal "Audit failed".
# ---------------------------------------------------------------------------


def test_audit_cycle_defers_on_limit(tmp_path):
    limit = RunResult(success=False, output=LIMIT_OUTPUT, exit_code=1, log_path=tmp_path / "a.log")
    with (
        patch("mcloop.audit.run_audit", return_value=limit),
        patch("mcloop.ratelimit.time.sleep", lambda _s: None),
    ):
        result = _run_audit_fix_cycle(tmp_path, tmp_path)
    assert result == AuditResult.deferred
    assert result != AuditResult.failed


def test_audit_cycle_still_fails_on_genuine_error(tmp_path):
    boom = RunResult(
        success=False,
        output=GENUINE_FAILURE_OUTPUT,
        exit_code=1,
        log_path=tmp_path / "a.log",
    )
    with patch("mcloop.audit.run_audit", return_value=boom):
        result = _run_audit_fix_cycle(tmp_path, tmp_path)
    assert result == AuditResult.failed


# ---------------------------------------------------------------------------
# Batch path: a mid-batch limit marks the CLI limited and returns "failed"
# (so the batch retry loop waits for reset), not a silent fail-open.
# ---------------------------------------------------------------------------


def test_run_batch_detects_limit_and_marks_state(tmp_path):
    from mcloop import main as _main
    from mcloop._planfile_compat import Task
    from mcloop.session_context import SessionContext

    children = [
        Task(text="step one", checked=False, failed=False, line_number=2, indent_level=1),
        Task(text="step two", checked=False, failed=False, line_number=3, indent_level=1),
    ]
    parent = Task(
        text="parent",
        checked=False,
        failed=False,
        line_number=1,
        indent_level=0,
        children=children,
    )
    tasks = [parent, *children]
    checklist = tmp_path / "PLAN.md"
    checklist.write_text("- [ ] parent\n  - [ ] step one\n  - [ ] step two\n")

    state = RateLimitState()
    limit = RunResult(success=False, output=LIMIT_OUTPUT, exit_code=1, log_path=tmp_path / "b.log")

    with (
        patch.object(_main, "run_task", return_value=limit),
        patch.object(_main, "_checkpoint", lambda *a, **k: None),
        patch.object(_main, "_snapshot_worktree", lambda *a, **k: ([], [])),
        patch.object(_main, "get_eliminated", lambda *a, **k: []),
    ):
        status, _detail = _main._run_batch(
            children,
            tasks,
            checklist,
            tmp_path,
            tmp_path,
            "desc",
            "1.1",
            SessionContext(),
            state,
            "claude",
            "opus",
            None,
            3,
            [],
            None,
            0.0,
            [],
            None,
        )

    assert status == "failed"  # triggers the batch retry loop
    assert state.is_limited("claude")  # CLI marked so retry waits for reset
