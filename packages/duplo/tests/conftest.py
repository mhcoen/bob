"""Shared test fixtures for duplo tests."""

# mcloop:llm-guard (satisfied by _no_real_llm_calls below)

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest


_original_subprocess_run = subprocess.run


def _fake_subprocess_run(*args, **kwargs):
    """Return a fake CompletedProcess that mimics claude -p output.

    Returns ``"[]"`` on stdout — a minimal valid JSON response that all
    callers handle gracefully via their fallback/parse-error paths.
    Non-claude subprocesses (ffmpeg, etc.) are passed through to the
    real ``subprocess.run``.
    """
    cmd = args[0] if args else kwargs.get("args", [])
    if isinstance(cmd, (list, tuple)) and cmd and cmd[0] == "claude":
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="[]", stderr="")
    return _original_subprocess_run(*args, **kwargs)


@pytest.fixture(autouse=True)
def _no_real_llm_calls():
    """Prevent any test from invoking the real claude CLI.

    Serves as mcloop's llm-guard: the marker comment above satisfies
    mcloop.conftest_guard.ensure_conftest_guard so mcloop does not
    auto-inject its own fixture. Behaviorally equivalent to mcloop's
    guard (no real LLM calls reach the network) but returns a deterministic
    empty response instead of raising, which lets legacy tests that
    don't explicitly mock the LLM path continue to pass without needing
    per-test updates.

    Patches ``subprocess.run`` to intercept ``claude`` commands and
    return ``"[]"``. Non-claude subprocesses (ffmpeg, etc.) are passed
    through to the real ``subprocess.run``.
    """
    with patch("subprocess.run", side_effect=_fake_subprocess_run):
        yield
