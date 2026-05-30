"""Shared pytest configuration.

Integration tests (marked with @pytest.mark.integration) are skipped
unless the MCLOOP_INTEGRATION environment variable is set.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Add this directory to sys.path so intra-tests imports (e.g.
# 'from plan_fixtures import ...') resolve regardless of where pytest is
# invoked from. The accompanying removal of tests/__init__.py is what
# lets workspace-root pytest disambiguate this conftest from sibling
# packages' tests/conftest.py
# (see /Users/mhcoen/proj/bob/.scratch/workspace-pytest-fix/diagnosis.md).
_TESTS_DIR = str(Path(__file__).resolve().parent)
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("MCLOOP_INTEGRATION"):
        return
    skip = pytest.mark.skip(reason="MCLOOP_INTEGRATION not set")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


# mcloop:llm-guard
# Auto-injected by mcloop. Blocks real claude/codex subprocess calls
# during pytest so unmocked LLM paths fail fast instead of silently
# burning 5-15 seconds per call. Opt out with @pytest.mark.llm.
import subprocess as _mcloop_subprocess  # noqa: E402


@pytest.fixture(autouse=True)
def _mcloop_block_real_llm_calls(request, monkeypatch):
    """Prevent tests from making real LLM subprocess calls."""
    if request.node.get_closest_marker("llm"):
        return  # Test opted out via @pytest.mark.llm
    _real_run = _mcloop_subprocess.run
    _real_popen = _mcloop_subprocess.Popen

    def _is_llm_binary(cmd) -> bool:
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
        # claude_cli-style paths spawn the LLM via Popen, not run; guard
        # both or the Popen path leaks a real call straight through.
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

    # The subscription preflight (mcloop/runner.py:ensure_subscription_preflight)
    # spawns its own `claude -p ok` probe to verify subscription auth.
    # That probe is the exact shape this guard rejects. The preflight
    # is correct in production; in tests it just needs to be a no-op
    # since the LLM path is mocked anyway. Patching it here avoids
    # threading a test-mode env var through production code.
    #
    # Skip for test_subscription_preflight.py itself: that module is
    # testing the preflight and supplies its own mocked subprocess.run
    # so the LLM guard above does not fire — the real preflight code
    # must be reachable there.
    test_path = str(request.node.fspath)
    if "test_subscription_preflight.py" not in test_path:
        import mcloop.runner as _mcloop_runner  # noqa: PLC0415

        monkeypatch.setattr(
            _mcloop_runner,
            "ensure_subscription_preflight",
            lambda **kwargs: None,
        )
        monkeypatch.setattr(_mcloop_runner, "_SUBSCRIPTION_PREFLIGHT_OK", True)

    # Unit tests construct project directories under pytest's temporary
    # root. In local runs that root can live inside a real checkout,
    # causing run_loop's git preflight/checkpoint/commit operations to
    # act on the developer's ancestor repository instead of the
    # synthetic project. Production git behavior is covered outside
    # this generic test harness guard; tests that need to assert calls
    # can still patch these symbols.
    import mcloop.main as _mcloop_main  # noqa: PLC0415

    monkeypatch.setattr(_mcloop_main, "_push_or_die", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_mcloop_main, "_checkpoint", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_mcloop_main, "_commit", lambda *_args, **_kwargs: "")
