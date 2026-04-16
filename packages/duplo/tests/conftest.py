"""Shared test fixtures for duplo tests."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest


def _fake_subprocess_run(*args, **kwargs):
    """Return a fake CompletedProcess that mimics claude -p output.

    Returns ``"[]"`` on stdout — a minimal valid JSON response that all
    callers handle gracefully via their fallback/parse-error paths.
    """
    cmd = args[0] if args else kwargs.get("args", [])
    if isinstance(cmd, (list, tuple)) and cmd and cmd[0] == "claude":
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="[]", stderr="")
    # Allow non-claude subprocesses (ffmpeg, etc.) through.
    return _original_subprocess_run(*args, **kwargs)


_original_subprocess_run = subprocess.run


@pytest.fixture(autouse=True)
def _no_real_llm_calls():
    """Prevent any test from invoking the real claude CLI.

    Patches ``subprocess.run`` to intercept ``claude`` commands and
    return ``"[]"`` — a minimal valid JSON response. Non-claude
    subprocesses (ffmpeg, etc.) are passed through to the real
    ``subprocess.run``.

    This catches all import patterns (module-level ``from`` imports
    and function-level deferred imports) because it patches at the
    subprocess layer rather than the ``duplo.claude_cli`` namespace.
    """
    with patch("subprocess.run", side_effect=_fake_subprocess_run):
        yield

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

    def _guarded_run(cmd, *args, **kwargs):
        if isinstance(cmd, (list, tuple)) and cmd:
            binary = str(cmd[0])
            if (
                binary in ("claude", "codex")
                or binary.endswith("/claude")
                or binary.endswith("/codex")
            ):
                raise RuntimeError(
                    f"Test made a real LLM subprocess call: {cmd!r}. "
                    f"Mock the LLM path to prevent this. "
                    f"If this test genuinely needs a real LLM call, "
                    f"mark it with @pytest.mark.llm."
                )
        return _real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(_mcloop_subprocess, "run", _guarded_run)

