"""Regression tests for the exit-code-to-verdict mapping in the two
Claude Code subprocess adapters.

``run_session`` (adapters/_subprocess.py) exit-codes a stuck session
with -3 after ``IDLE_TIMEOUT_S`` and a wall-clock overrun with -2. Both
are genuine timeouts and must map to the ``timeout`` verdict so upstream
retry logic treats them alike, rather than surfacing the idle-kill as a
hard ``error``. A caller-initiated interrupt (130) is deliberately kept
as ``error`` so a run the caller aborted is not retried as a timeout.

No live CLI is invoked here; only the pure mapping function is exercised.
"""

from __future__ import annotations

from orchestra.adapters.claude_code_agent import (
    _verdict_for_exit_code as agent_verdict,
)
from orchestra.adapters.claude_code_text import (
    _verdict_for_exit_code as text_verdict,
)


def test_claude_code_text_verdict_mapping() -> None:
    assert text_verdict(0) == "complete"
    assert text_verdict(-2) == "timeout"
    # -3 is the idle-kill sentinel from run_session; it is a genuine
    # timeout, not a hard error.
    assert text_verdict(-3) == "timeout"
    assert text_verdict(1) == "error"
    # 130 (interrupt) is a deliberate caller-initiated cancel, kept as
    # error so it is not retried as a timeout would be.
    assert text_verdict(130) == "error"


def test_claude_code_agent_verdict_mapping() -> None:
    assert agent_verdict(0) == "complete"
    assert agent_verdict(-2) == "timeout"
    assert agent_verdict(-3) == "timeout"
    assert agent_verdict(1) == "error"
    assert agent_verdict(130) == "error"
