"""Shared test fixtures for duplo tests."""

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
