"""Unit tests for ``CodexAgentAdapter``.

Mirrors ``tests/test_codex_text_adapter.py`` plus the agent-specific
``changed_files`` plumbing. Subprocess invocation is mocked so no live
``codex exec`` is required. A live-shaped smoke test is gated on the
Codex CLI being on PATH.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from orchestra.adapters import codex_agent as codex_agent_mod
from orchestra.adapters.codex_agent import (
    DEFAULT_SANDBOX,
    CodexAgentAdapter,
    _verdict_for_exit_code,
    register,
)
from orchestra.spine import InvocationRequest


def _request(
    *,
    state_id: str = "s",
    actor_binding: dict[str, Any] | None = None,
    backing_options: dict[str, Any] | None = None,
    external_inputs: dict[str, Any] | None = None,
    prompt: str | None = None,
    timeout_ms: int | None = None,
) -> InvocationRequest:
    return InvocationRequest(
        state_id=state_id,
        attempt=1,
        actor_binding=actor_binding or {"kind": "agent"},
        reads={},
        external_inputs=external_inputs or {},
        prompt_artifact=prompt,
        schema=None,
        backing_options=backing_options or {},
        timeout_ms=timeout_ms,
    )


# --------------------------------------------------------------------
# Command construction
# --------------------------------------------------------------------


def test_build_command_default_sandbox_no_model_no_prompt() -> None:
    adapter = CodexAgentAdapter()
    cmd = adapter._build_command(
        prompt="", model=None, sandbox=DEFAULT_SANDBOX
    )
    assert cmd == [
        "codex",
        "--ask-for-approval",
        "never",
        "--sandbox",
        "workspace-write",
        "exec",
        "--skip-git-repo-check",
    ]


def test_build_command_with_model_and_prompt() -> None:
    adapter = CodexAgentAdapter()
    cmd = adapter._build_command(
        prompt="edit foo.py", model="gpt-5-codex", sandbox="workspace-write"
    )
    assert cmd == [
        "codex",
        "--ask-for-approval",
        "never",
        "--sandbox",
        "workspace-write",
        "exec",
        "--skip-git-repo-check",
        "--model",
        "gpt-5-codex",
        "edit foo.py",
    ]


def test_build_command_sandbox_override() -> None:
    adapter = CodexAgentAdapter()
    cmd = adapter._build_command(
        prompt="x", model=None, sandbox="read-only"
    )
    assert "--sandbox" in cmd
    assert cmd[cmd.index("--sandbox") + 1] == "read-only"


def test_build_command_top_level_flags_precede_exec() -> None:
    """The Codex CLI rejects ``--ask-for-approval`` and ``--sandbox``
    when they appear after the ``exec`` subcommand. Pin the ordering."""
    adapter = CodexAgentAdapter()
    cmd = adapter._build_command(prompt="x", model="m", sandbox="workspace-write")
    exec_idx = cmd.index("exec")
    approval_idx = cmd.index("--ask-for-approval")
    sandbox_idx = cmd.index("--sandbox")
    assert approval_idx < exec_idx
    assert sandbox_idx < exec_idx


def test_build_command_skip_git_repo_check_follows_exec() -> None:
    """``--skip-git-repo-check`` is an ``exec`` subcommand flag in
    codex 0.128 (verified via ``codex exec --help``). Without it,
    codex refuses to run in untrusted directories and exits with
    status 1 before contacting the model. The flag must follow the
    ``exec`` subcommand and precede the trailing prompt."""
    adapter = CodexAgentAdapter()
    cmd = adapter._build_command(
        prompt="hi", model="gpt-5-codex", sandbox="workspace-write"
    )
    assert "--skip-git-repo-check" in cmd
    exec_idx = cmd.index("exec")
    skip_idx = cmd.index("--skip-git-repo-check")
    assert skip_idx > exec_idx, (
        "--skip-git-repo-check must follow the exec subcommand"
    )
    assert cmd[-1] == "hi"
    assert skip_idx < len(cmd) - 1


# --------------------------------------------------------------------
# prepare() shape
# --------------------------------------------------------------------


def test_prepare_summary_kind_agent_and_full_command(tmp_path: Path) -> None:
    adapter = CodexAgentAdapter(default_model="gpt-5-codex")
    req = _request(
        prompt="edit",
        external_inputs={
            "project_dir": str(tmp_path),
            "log_dir": str(tmp_path / "logs"),
            "task_label": "smoke",
        },
    )
    prepared = adapter.prepare(req)
    assert prepared.summary["kind"] == "agent"
    assert prepared.summary["adapter"] == "codex_agent"
    assert prepared.summary["cli"] == "codex"
    assert prepared.summary["model"] == "gpt-5-codex"
    assert prepared.summary["sandbox"] == "workspace-write"
    assert prepared.summary["command"] == [
        "codex",
        "--ask-for-approval",
        "never",
        "--sandbox",
        "workspace-write",
        "exec",
        "--skip-git-repo-check",
        "--model",
        "gpt-5-codex",
        "edit",
    ]
    assert prepared.summary["cwd"] == str(tmp_path)
    assert prepared.summary["log_dir"] == str(tmp_path / "logs")


def test_prepare_inner_carries_project_dir_for_changed_files(
    tmp_path: Path,
) -> None:
    adapter = CodexAgentAdapter()
    req = _request(
        prompt="x",
        external_inputs={"project_dir": str(tmp_path)},
    )
    prepared = adapter.prepare(req)
    assert prepared.inner["project_dir"] == tmp_path


def test_prepare_sandbox_override_via_backing_options(
    tmp_path: Path,
) -> None:
    adapter = CodexAgentAdapter()
    req = _request(
        prompt="x",
        external_inputs={"project_dir": str(tmp_path)},
        backing_options={"sandbox": "danger-full-access"},
    )
    prepared = adapter.prepare(req)
    assert prepared.summary["sandbox"] == "danger-full-access"
    assert "danger-full-access" in prepared.summary["command"]


def test_prepare_default_sandbox_constructor_argument(tmp_path: Path) -> None:
    adapter = CodexAgentAdapter(default_sandbox="read-only")
    req = _request(
        prompt="x",
        external_inputs={"project_dir": str(tmp_path)},
    )
    prepared = adapter.prepare(req)
    assert prepared.summary["sandbox"] == "read-only"


# --------------------------------------------------------------------
# invoke() passthrough behavior
# --------------------------------------------------------------------


def test_invoke_returns_stdout_unchanged_no_stream_json_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_stdout = (
        '{"type": "result", "subtype": "success", "result": "would-be-extracted"}'
    )
    monkeypatch.setattr(
        codex_agent_mod,
        "run_session",
        lambda cmd, cwd, env, timeout, silent: (fake_stdout, 0),
    )
    monkeypatch.setattr(
        codex_agent_mod,
        "write_log",
        lambda log_dir, task_label, cmd, output, exit_code: tmp_path / "log",
    )
    monkeypatch.setattr(
        codex_agent_mod, "_detect_changed_files", lambda project_dir: []
    )
    adapter = CodexAgentAdapter()
    prepared = adapter.prepare(
        _request(prompt="x", external_inputs={"project_dir": str(tmp_path)})
    )
    payload = adapter.invoke(prepared)
    assert payload["output"] == fake_stdout


def test_invoke_surfaces_changed_files_in_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        codex_agent_mod,
        "run_session",
        lambda cmd, cwd, env, timeout, silent: ("ok", 0),
    )
    monkeypatch.setattr(
        codex_agent_mod,
        "write_log",
        lambda log_dir, task_label, cmd, output, exit_code: tmp_path / "log",
    )
    monkeypatch.setattr(
        codex_agent_mod,
        "_detect_changed_files",
        lambda project_dir: ["src/a.py", "src/b.py"],
    )
    adapter = CodexAgentAdapter()
    prepared = adapter.prepare(
        _request(prompt="x", external_inputs={"project_dir": str(tmp_path)})
    )
    payload = adapter.invoke(prepared)
    assert payload["fields"]["changed_files"] == ["src/a.py", "src/b.py"]


def test_invoke_verdict_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        codex_agent_mod,
        "write_log",
        lambda log_dir, task_label, cmd, output, exit_code: tmp_path / "log",
    )
    monkeypatch.setattr(
        codex_agent_mod, "_detect_changed_files", lambda project_dir: []
    )
    adapter = CodexAgentAdapter()
    prepared = adapter.prepare(
        _request(prompt="x", external_inputs={"project_dir": str(tmp_path)})
    )
    for exit_code, expected in [(0, "complete"), (-2, "timeout"), (3, "error")]:
        monkeypatch.setattr(
            codex_agent_mod,
            "run_session",
            lambda cmd, cwd, env, timeout, silent, ec=exit_code: ("o", ec),
        )
        payload = adapter.invoke(prepared)
        assert payload["verdict"] == expected
        assert payload["fields"]["exit_code"] == exit_code


# --------------------------------------------------------------------
# verdict mapping helper
# --------------------------------------------------------------------


def test_verdict_for_exit_code_mapping() -> None:
    assert _verdict_for_exit_code(0) == "complete"
    assert _verdict_for_exit_code(-2) == "timeout"
    assert _verdict_for_exit_code(1) == "error"
    assert _verdict_for_exit_code(130) == "error"


# --------------------------------------------------------------------
# describe() and class attributes
# --------------------------------------------------------------------


def test_describe_metadata() -> None:
    desc = CodexAgentAdapter().describe()
    assert desc["backing"] == "codex_agent"
    assert desc["kind"] == "subprocess"
    assert desc["cli"] == "codex"
    assert desc["sandbox"] == "workspace-write"
    assert desc["supports_cancel"] is False
    assert desc["reports_cost"] is False
    assert desc["supports_streaming"] is False


def test_class_attributes() -> None:
    assert CodexAgentAdapter.backing == "codex_agent"
    assert CodexAgentAdapter.manages_own_timeout is True


def test_cancel_is_noop() -> None:
    a = CodexAgentAdapter()
    p = a.prepare(_request())
    assert a.cancel(p) is None


# --------------------------------------------------------------------
# register()
# --------------------------------------------------------------------


def test_register_idempotent() -> None:
    class _FakeRegistry:
        def __init__(self) -> None:
            self.actor_backings: dict[str, Any] = {}

        def register_actor_backing(self, name: str, factory: Any) -> None:
            if name in self.actor_backings:
                raise RuntimeError(f"duplicate {name}")
            self.actor_backings[name] = factory

    reg = _FakeRegistry()
    register(reg)
    assert "codex_agent" in reg.actor_backings
    register(reg)
    assert "codex_agent" in reg.actor_backings


def test_register_factory_constructs_adapter_with_default_model() -> None:
    class _FakeRegistry:
        def __init__(self) -> None:
            self.actor_backings: dict[str, Any] = {}

        def register_actor_backing(self, name: str, factory: Any) -> None:
            self.actor_backings[name] = factory

    reg = _FakeRegistry()
    register(reg, default_model="gpt-5-codex")
    instance = reg.actor_backings["codex_agent"]()
    assert isinstance(instance, CodexAgentAdapter)
    assert instance._default_model == "gpt-5-codex"
    assert instance._default_sandbox == "workspace-write"


# --------------------------------------------------------------------
# Live smoke (skipped when codex CLI is absent)
# --------------------------------------------------------------------


@pytest.mark.skipif(
    shutil.which("codex") is None or os.environ.get("ORCHESTRA_LIVE_CODEX") != "1",
    reason="live Codex test requires codex on PATH and ORCHESTRA_LIVE_CODEX=1",
)
def test_live_codex_agent_smoke(tmp_path: Path) -> None:
    """Live invocation: run Codex with sandbox=read-only on a trivial
    prompt that should not modify the workspace.

    Skipped automatically when the Codex CLI is missing. The default
    workspace-write sandbox is intentionally avoided here so a stray
    edit cannot leak into a developer's workspace.
    """
    adapter = CodexAgentAdapter(default_sandbox="read-only")
    req = _request(
        prompt="reply with the single word: ok",
        external_inputs={
            "project_dir": str(tmp_path),
            "log_dir": str(tmp_path / "logs"),
            "task_label": "live-smoke-agent",
        },
    )
    prepared = adapter.prepare(req)
    payload = adapter.invoke(prepared)
    assert payload["verdict"] == "complete"
    assert payload["output"]
    assert Path(payload["fields"]["log_path"]).exists()
    assert isinstance(payload["fields"]["changed_files"], list)
