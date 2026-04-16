"""Shared pytest configuration.

Integration tests (marked with @pytest.mark.integration) are skipped
unless the MCLOOP_INTEGRATION environment variable is set.
"""

from __future__ import annotations

import os

import pytest


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
