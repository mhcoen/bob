"""Workspace-compatible test root.

Adds this directory to ``sys.path`` so that intra-tests imports like
``from helpers.legacy_prompt_manifest import compute_prompt_manifest``
resolve regardless of where pytest is invoked from. The accompanying
removal of ``tests/__init__.py`` is what lets workspace-root pytest
disambiguate this conftest from sibling packages' tests/conftest.py
(see /Users/mhcoen/proj/bob/.scratch/workspace-pytest-fix/diagnosis.md).
"""

from __future__ import annotations

import sys
from pathlib import Path

_TESTS_DIR = str(Path(__file__).resolve().parent)
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

# Pin the repo root ahead of site-packages so the suite always tests
# the working-tree ``orchestra`` package. Without this, a stale
# non-editable orchestra installed in whatever venv happens to be
# active shadows the working tree and the suite tests the wrong code.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT in sys.path:
    sys.path.remove(_REPO_ROOT)
sys.path.insert(0, _REPO_ROOT)
for _mod_name in [m for m in list(sys.modules) if m == "orchestra" or m.startswith("orchestra.")]:
    _mod = sys.modules[_mod_name]
    _file = getattr(_mod, "__file__", None)
    if _file is not None and not str(_file).startswith(_REPO_ROOT):
        del sys.modules[_mod_name]

# mcloop:llm-guard
# Auto-injected by mcloop. Blocks real claude/codex subprocess calls
# during pytest so unmocked LLM paths fail fast instead of silently
# burning 5-15 seconds per call. Opt out with @pytest.mark.llm.
import subprocess as _mcloop_subprocess  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _mcloop_block_real_llm_calls(request, monkeypatch):
    """Prevent tests from making real LLM subprocess calls."""
    if request.node.get_closest_marker("llm"):
        return  # Test opted out via @pytest.mark.llm
    _real_run = _mcloop_subprocess.run
    _real_popen = _mcloop_subprocess.Popen

    def _is_llm_binary(cmd):
        if isinstance(cmd, (list, tuple)) and cmd:  # noqa: UP038
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
