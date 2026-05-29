"""Shared test fixtures for duplo tests."""

# mcloop:llm-guard (satisfied by _no_real_llm_calls below)

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add this directory to sys.path so intra-tests imports resolve regardless
# of where pytest is invoked from. The accompanying removal of
# tests/__init__.py is what lets workspace-root pytest disambiguate this
# conftest from sibling packages' tests/conftest.py
# (see /Users/mhcoen/proj/bob/.scratch/workspace-pytest-fix/diagnosis.md).
_TESTS_DIR = str(Path(__file__).resolve().parent)
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)


_original_subprocess_run = subprocess.run


def _fake_subprocess_run(*args, **kwargs):
    """Return a fake CompletedProcess that mimics claude -p output.

    Returns ``"[]"`` on stdout for claude — a minimal valid JSON response
    that all callers handle gracefully via their fallback/parse-error
    paths. Short-circuits appshot to a not-found exit so tests don't
    launch real macOS apps. Other subprocesses (ffmpeg, etc.) are passed
    through to the real ``subprocess.run``.
    """
    cmd = args[0] if args else kwargs.get("args", [])
    if isinstance(cmd, (list, tuple)) and cmd:
        head = cmd[0]
        if head == "claude":
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="[]", stderr="")
        if isinstance(head, str) and head.rsplit("/", 1)[-1] == "appshot":
            return subprocess.CompletedProcess(args=cmd, returncode=-1, stdout="", stderr="")
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


@pytest.fixture(autouse=True)
def _reset_call_log():
    """Deactivate the per-run LLM call logger before each test.

    Keeps an activated run (e.g. from a test that drives ``main()``) from
    leaking into later tests, where the autouse subprocess stub would
    otherwise write ``.duplo/logs/`` records into an unexpected directory.
    """
    from duplo import call_log

    call_log._active = None
    yield
    call_log._active = None
