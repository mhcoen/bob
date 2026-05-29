"""Shared test fixtures for duplo tests."""

# mcloop:llm-guard (satisfied by _no_real_llm_calls below)

from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Hard-disable duplo's GitHub remote automation for the entire test
# session BEFORE any test imports duplo.git_ops. duplo.git_ops honors
# DUPLO_NO_GITHUB; setting it here (process env, inherited by any
# subprocess a test spawns) guarantees commit_artifact still commits
# locally but never runs `gh repo create` — which is what was creating
# private repos named after pytest tmp dirs on real GitHub. Real `duplo`
# runs do not set this var, so their commit + push + repo-create behavior
# is fully intact. (test_git_ops.py overrides it to "0" per-test to
# exercise the remote path against a mocked gh.)
os.environ["DUPLO_NO_GITHUB"] = "1"

# Add this directory to sys.path so intra-tests imports resolve regardless
# of where pytest is invoked from. The accompanying removal of
# tests/__init__.py is what lets workspace-root pytest disambiguate this
# conftest from sibling packages' tests/conftest.py
# (see /Users/mhcoen/proj/bob/.scratch/workspace-pytest-fix/diagnosis.md).
_TESTS_DIR = str(Path(__file__).resolve().parent)
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)


_original_subprocess_run = subprocess.run
_original_subprocess_popen = subprocess.Popen


def _is_claude_cmd(cmd) -> bool:
    """True if ``cmd`` is a claude/codex invocation (real LLM call)."""
    if isinstance(cmd, (list, tuple)) and cmd:
        head = cmd[0]
        if isinstance(head, str):
            base = head.rsplit("/", 1)[-1]
            return base in ("claude", "codex")
    return False


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
        base = head.rsplit("/", 1)[-1] if isinstance(head, str) else ""
        if base in ("claude", "codex"):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="[]", stderr="")
        if base == "appshot":
            return subprocess.CompletedProcess(args=cmd, returncode=-1, stdout="", stderr="")
        # Neutralize the per-artifact git/GitHub automation
        # (duplo.git_ops.commit_artifact). It shells out to ``git`` and
        # ``gh`` via subprocess.run; left unfaked, a test that drives
        # main() to a phase commit runs a REAL ``git init``/``commit`` and
        # a REAL ``gh repo create`` — a network side effect that fails
        # under GitHub's repo-creation rate limit and is never a thing a
        # unit test should do. Return success with empty output so
        # commit_artifact believes the local commit landed and the remote
        # was wired/pushed, with zero real effect. Tests that assert on
        # real git behavior do not exist in this suite (hashing tests use
        # duplo.hasher, not git subprocesses).
        if base in ("git", "gh"):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
    return _original_subprocess_run(*args, **kwargs)


class _FakePopen:
    """Minimal Popen stand-in for a claude/codex spawn.

    duplo's ``claude_cli.query`` (the main text path) spawns the CLI via
    ``subprocess.Popen`` — NOT ``subprocess.run`` — and consumes it by
    draining ``stdout``/``stderr`` in threads, writing the prompt to
    ``stdin``, polling ``poll()`` until non-None, then reading
    ``returncode``. A fake that only covered ``subprocess.run`` left this
    path spawning a REAL ``claude -p`` on every test that exercised
    ``query()`` (feature dedup/grouping, task matching, pipeline
    integration) — real token spend and 4-54s per test. This fake closes
    that hole: it satisfies exactly the interface ``_query_once`` touches
    and yields one valid stream-json ``result`` event so
    ``_parse_stream_json`` returns a benign empty response.
    """

    def __init__(self, cmd, *args, **kwargs):
        self.args = cmd
        self.returncode = 0
        text_mode = kwargs.get("text") or kwargs.get("universal_newlines")
        payload = '{"type": "result", "result": "[]"}\n'
        self.stdout = io.StringIO(payload) if text_mode else io.BytesIO(payload.encode())
        self.stderr = io.StringIO("") if text_mode else io.BytesIO(b"")
        self.stdin = io.StringIO() if text_mode else io.BytesIO()

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        return None

    def terminate(self):
        return None

    def communicate(self, input=None, timeout=None):
        return self.stdout.read(), self.stderr.read()


def _fake_subprocess_popen(*args, **kwargs):
    """Intercept claude/codex Popen spawns; pass everything else through."""
    cmd = args[0] if args else kwargs.get("args", [])
    if _is_claude_cmd(cmd):
        return _FakePopen(cmd, *args, **kwargs)
    return _original_subprocess_popen(*args, **kwargs)


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

    Patches both ``subprocess.run`` AND ``subprocess.Popen`` to intercept
    ``claude``/``codex`` commands. ``query_with_images`` uses ``run``;
    ``query`` (the main text path) uses ``Popen`` — both must be guarded
    or the ``Popen`` path leaks a real ``claude -p`` call (real tokens,
    4-54s per test). Non-LLM subprocesses (ffmpeg, etc.) pass through to
    the real implementations.
    """
    with (
        patch("subprocess.run", side_effect=_fake_subprocess_run),
        patch("subprocess.Popen", side_effect=_fake_subprocess_popen),
    ):
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
