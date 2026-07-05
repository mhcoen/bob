# mcloop:llm-guard
# Auto-injected by mcloop. Blocks real claude/codex subprocess calls
# during pytest so unmocked LLM paths fail fast instead of silently
# burning 5-15 seconds per call. Opt out with @pytest.mark.llm.
import subprocess as _mcloop_subprocess
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _mcloop_block_real_llm_calls(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prevent tests from making real LLM subprocess calls."""
    if request.node.get_closest_marker("llm"):
        return  # Test opted out via @pytest.mark.llm
    _real_run = _mcloop_subprocess.run
    _real_popen = _mcloop_subprocess.Popen

    def _is_llm_binary(cmd: object) -> bool:
        if isinstance(cmd, list | tuple) and cmd:
            binary = str(cmd[0])
            return (
                binary in ("claude", "codex")
                or binary.endswith("/claude")
                or binary.endswith("/codex")
            )
        return False

    def _guarded_run(cmd: Any, *args: Any, **kwargs: Any) -> Any:
        if _is_llm_binary(cmd):
            raise RuntimeError(
                f"Test made a real LLM subprocess call: {cmd!r}. "
                f"Mock the LLM path to prevent this. "
                f"If this test genuinely needs a real LLM call, "
                f"mark it with @pytest.mark.llm."
            )
        return _real_run(cmd, *args, **kwargs)

    def _guarded_popen(cmd: Any, *args: Any, **kwargs: Any) -> Any:
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
