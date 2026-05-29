"""Ensure target projects have a conftest.py guard against unmocked LLM calls.

When mcloop generates tests, the inner Claude may write tests that
transitively invoke ``claude -p`` or ``codex exec`` without mocking.
Each real LLM round-trip costs 5-15 seconds and compounds across
hundreds of tests into minutes of wasted runtime.

This module injects an autouse pytest fixture into the target project's
``tests/conftest.py`` that raises ``RuntimeError`` if any test attempts
a real LLM subprocess call. Tests that genuinely need real LLM calls
can opt out with ``@pytest.mark.llm``.
"""

from __future__ import annotations

from pathlib import Path

_GUARD_MARKER = "# mcloop:llm-guard"

_GUARD_CODE = '''\
# mcloop:llm-guard
# Auto-injected by mcloop. Blocks real claude/codex subprocess calls
# during pytest so unmocked LLM paths fail fast instead of silently
# burning 5-15 seconds per call. Opt out with @pytest.mark.llm.
import subprocess as _mcloop_subprocess

import pytest


@pytest.fixture(autouse=True)
def _mcloop_block_real_llm_calls(request, monkeypatch):
    """Prevent tests from making real LLM subprocess calls."""
    if request.node.get_closest_marker("llm"):
        return  # Test opted out via @pytest.mark.llm
    _real_run = _mcloop_subprocess.run
    _real_popen = _mcloop_subprocess.Popen

    def _is_llm_binary(cmd):
        if isinstance(cmd, (list, tuple)) and cmd:
            binary = str(cmd[0])
            return (
                binary in ("claude", "codex")
                or binary.endswith("/claude")
                or binary.endswith("/codex")
            )
        return False

    def _guarded_run(cmd, *args, **kwargs):
        if _is_llm_binary(cmd):
            raise RuntimeError(
                f"Test made a real LLM subprocess call: {cmd!r}. "
                f"Mock the LLM path to prevent this. "
                f"If this test genuinely needs a real LLM call, "
                f"mark it with @pytest.mark.llm."
            )
        return _real_run(cmd, *args, **kwargs)

    def _guarded_popen(cmd, *args, **kwargs):
        # Some CLIs spawn the LLM via Popen rather than run; guard both
        # or the Popen path leaks a real call straight through.
        if _is_llm_binary(cmd):
            raise RuntimeError(
                f"Test made a real LLM subprocess call (Popen): {cmd!r}. "
                f"Mock the LLM path to prevent this. "
                f"If this test genuinely needs a real LLM call, "
                f"mark it with @pytest.mark.llm."
            )
        return _real_popen(cmd, *args, **kwargs)

    monkeypatch.setattr(_mcloop_subprocess, "run", _guarded_run)
    monkeypatch.setattr(_mcloop_subprocess, "Popen", _guarded_popen)
'''


def ensure_conftest_guard(project_dir: Path) -> bool:
    """Ensure tests/conftest.py has the LLM-call guard fixture.

    Returns True if the guard was added, False if it was already
    present or if there is no tests/ directory.
    """
    tests_dir = project_dir / "tests"
    if not tests_dir.is_dir():
        return False

    conftest = tests_dir / "conftest.py"
    if conftest.exists():
        content = conftest.read_text()
        if _GUARD_MARKER in content:
            return False
        # Append to existing file
        separator = "\n\n" if content.rstrip() else ""
        conftest.write_text(content.rstrip() + separator + _GUARD_CODE + "\n")
        return True
    else:
        conftest.write_text(_GUARD_CODE + "\n")
        return True
