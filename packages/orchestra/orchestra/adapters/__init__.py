"""Adapter set: the four-operation contract, the slice-1 mocks, and
the shipped CLI-backed adapters (Claude Code and Codex)."""

from orchestra.adapters.base import Adapter
from orchestra.adapters.claude_code_agent import ClaudeCodeAgentAdapter
from orchestra.adapters.claude_code_text import ClaudeCodeTextAdapter
from orchestra.adapters.codex_agent import CodexAgentAdapter
from orchestra.adapters.codex_text import CodexTextAdapter
from orchestra.adapters.mock_human import MockHumanAdapter
from orchestra.adapters.mock_model import MockModelAdapter
from orchestra.adapters.mock_shell import MockShellAdapter

__all__ = [
    "Adapter",
    "ClaudeCodeAgentAdapter",
    "ClaudeCodeTextAdapter",
    "CodexAgentAdapter",
    "CodexTextAdapter",
    "MockHumanAdapter",
    "MockModelAdapter",
    "MockShellAdapter",
]
