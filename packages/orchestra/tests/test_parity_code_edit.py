"""Parity test: direct backend vs orchestra-via-single backend.

The integration plan calls for a wrapper interface ``invoke_code_edit``
with two backends, ``direct`` and ``orchestra``, that both produce a
``CodeEditResult``. This test runs both backends in-process on the
same fixture task and asserts:

1. Structural equivalence per the plan: ``success``, ``exit_code``,
   and ``changed_files`` match. Output content is allowed to vary
   since real model invocations would produce different text.
2. The orchestra wrapper preserves what mcloop's direct path produces
   at the subprocess boundary: same command shape including
   ``--model``, same merged environment including the task label, and
   the same captured stream-json transcript reaches the log file.
3. The wrapper honors per-call ``model``, ``log_dir``, and ``timeout``
   on both sides (the assertions verify this rather than ignoring the
   args).

The direct backend is a transcription of mcloop's ``run_task`` body.
It uses ``orchestra.prompts.build_code_edit_prompt`` (which is itself
a verbatim lift of mcloop's _build_normal_prompt /
_build_bug_task_prompt / _build_bug_prompt / _build_shared_parts) and
``orchestra.adapters._subprocess`` (which is itself a verbatim lift of
mcloop's _build_session_env / _run_session / _write_log plus the
watchdog and approval polling). It deliberately avoids importing
mcloop so the orchestra repo does not take a runtime dependency on a
sibling project.

The test does not invoke real Claude Code. ``subprocess.Popen`` and
``subprocess.run`` are patched to return a canned stream-json
transcript and a canned ``git status`` output, so the test runs
offline and never reaches a real CLI.
"""

from __future__ import annotations

import io
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from orchestra.adapters import _subprocess as orch_sp
from orchestra.adapters._subprocess import (
    build_session_env,
    run_session,
    write_log,
)
from orchestra.adapters.claude_code_agent import DEFAULT_ALLOWED_TOOLS
from orchestra.api import run_workflow
from orchestra.config import OrchestraConfig
from orchestra.prompts import build_code_edit_prompt

# --------------------------------------------------------------------
# CodeEditResult (the wrapper-interface return shape)
# --------------------------------------------------------------------


@dataclass
class CodeEditResult:
    """Mcloop-facing result shape produced by ``invoke_code_edit``."""

    success: bool
    output: str
    exit_code: int
    log_path: Path
    changed_files: list[str] = field(default_factory=list)
    summary: dict[str, Any] | None = None


# --------------------------------------------------------------------
# Direct backend: a transcription of mcloop's run_task body
# --------------------------------------------------------------------


def _direct_build_command(
    prompt: str,
    model: str | None,
    allowed_tools: str = DEFAULT_ALLOWED_TOOLS,
) -> list[str]:
    """Mirror of mcloop's _build_command for the claude CLI path."""
    cmd: list[str] = ["claude", "-p"]
    if prompt:
        cmd.append(prompt)
    cmd.extend(
        [
            "--allowedTools",
            allowed_tools,
            "--permission-mode",
            "default",
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-partial-messages",
        ]
    )
    if model:
        cmd.extend(["--model", model])
    return cmd


def _direct_detect_changed_files(project_dir: Path) -> list[str]:
    out = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=project_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if out.returncode != 0:
        return []
    files: list[str] = []
    for line in out.stdout.splitlines():
        path = line[3:].strip()
        if path:
            files.append(path)
    return files


def invoke_code_edit_direct(
    inputs: dict[str, Any],
    *,
    project_dir: Path,
    log_dir: Path,
    model: str | None,
    timeout: int = 600,
) -> CodeEditResult:
    """Direct backend: structural transcription of mcloop run_task body.

    Builds the prompt with the same builders mcloop uses, builds the
    command with the same flags, builds the env with the same
    passthrough plus billing-mode plus provider routing, runs the
    session through the same loop with watchdog and approval polling,
    writes the same per-invocation log file, and detects changed files
    via git status. All of this lives in orchestra modules that were
    transcribed from mcloop runner.py for the integration; we use them
    here so the test is faithful to mcloop without taking a runtime
    dependency on the mcloop package.
    """
    prompt = build_code_edit_prompt(inputs)
    cmd = _direct_build_command(prompt, model)
    env = build_session_env(
        task_label=str(inputs.get("task_label", "")),
        cli="claude",
        model=model,
    )
    output, exit_code = run_session(cmd, project_dir, env=env, timeout=timeout)
    log_path = write_log(log_dir, str(inputs.get("instruction", "task")), cmd, output, exit_code)
    changed = _direct_detect_changed_files(project_dir)
    return CodeEditResult(
        success=exit_code == 0,
        output=output,
        exit_code=exit_code,
        log_path=log_path,
        changed_files=changed,
        summary=None,
    )


# --------------------------------------------------------------------
# Orchestra backend: thin adapter from WorkflowRunResult
# --------------------------------------------------------------------


def invoke_code_edit_orchestra(
    inputs: dict[str, Any],
    *,
    project_dir: Path,
    log_dir: Path,
    model: str | None,
    config: OrchestraConfig | dict[str, Any],
    data_root: Path,
    timeout: int = 600,
) -> CodeEditResult:
    """Orchestra backend: call run_workflow and adapt the result.

    Threads ``log_dir``, ``model``, and ``timeout`` through
    ``invocation_options`` so the underlying adapter receives them.
    The adapter writes its session log under ``log_dir`` and uses the
    overridden model.
    """
    invocation_options: dict[str, Any] = {
        "log_dir": str(log_dir),
        "timeout": timeout,
    }
    if model is not None:
        invocation_options["model"] = model
    result = run_workflow(
        "code_edit",
        inputs,
        config,
        invocation_options=invocation_options,
        project_dir=project_dir,
        data_root=data_root,
    )
    summary = result.summary
    output = summary.get("output", "")
    exit_code = int(summary.get("exit_code", 1 if result.terminal != "done" else 0))
    changed_files = list(summary.get("changed_files") or [])
    adapter_log = summary.get("adapter_log")
    log_path = Path(adapter_log) if adapter_log else result.log_path
    return CodeEditResult(
        success=(result.terminal == "done"),
        output=output,
        exit_code=exit_code,
        log_path=log_path,
        changed_files=changed_files,
        summary=summary,
    )


# --------------------------------------------------------------------
# Subprocess-layer mocks plus a spy that records the cmd/cwd/env of
# every Popen call so the assertions can compare them.
# --------------------------------------------------------------------


_TRANSCRIPT_LINES = [
    json.dumps({"type": "system", "subtype": "init"}) + "\n",
    json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "ok"}]},
        }
    )
    + "\n",
    json.dumps({"type": "result", "subtype": "success"}) + "\n",
]


class _CapturingStringIO(io.StringIO):
    """A StringIO that snapshots its contents on close().

    run_session writes the prompt into the subprocess's stdin and
    then closes it; the fake needs the captured value to remain
    readable after close so the parity test can assert prompt
    equivalence between the direct and orchestra paths.
    """

    def __init__(self) -> None:
        super().__init__()
        self.captured: str = ""

    def close(self) -> None:
        try:
            self.captured = self.getvalue()
        finally:
            super().close()


@dataclass
class _PopenCall:
    cmd: list[str]
    cwd: Any
    env: dict[str, str] | None
    is_watchdog: bool
    stdin_buffer: _CapturingStringIO | None = None
    """Pass-7 fix: orchestra adapters pipe the prompt via stdin
    instead of putting it in argv. The fake captures the stdin sink
    so the parity test can assert the prompt actually reached the
    inner CLI on the orchestra side."""


_popen_calls: list[_PopenCall] = []


class _FakePopen:
    """A subprocess.Popen replacement that yields a canned transcript.

    Records each call into ``_popen_calls`` so the test can compare
    the cmd, cwd, env, and (for orchestra adapters) the stdin payload
    between the direct and orchestra paths.
    """

    return_code_default: int = 0

    def __init__(
        self,
        cmd: list[str],
        cwd: Any = None,
        stdin: Any = None,
        stdout: Any = None,
        stderr: Any = None,
        text: bool = True,
        env: dict[str, str] | None = None,
        start_new_session: bool = False,
    ) -> None:
        self.args = cmd
        self.cwd = cwd
        self.env = env
        self.pid = 12345 + len(_popen_calls)
        self.returncode: int | None = None
        is_watchdog = bool(cmd and cmd[0] == "sh" and "-c" in cmd)
        if not is_watchdog:
            self.stdout = io.StringIO("".join(_TRANSCRIPT_LINES))
        else:
            self.stdout = None
        self.stdin = _CapturingStringIO()
        _popen_calls.append(
            _PopenCall(
                cmd=list(cmd),
                cwd=cwd,
                env=env,
                is_watchdog=is_watchdog,
                stdin_buffer=self.stdin,
            )
        )

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            self.returncode = self.return_code_default
        return self.returncode

    def kill(self) -> None:
        if self.returncode is None:
            self.returncode = -9


class _FailingFakePopen(_FakePopen):
    return_code_default = 1


_FAKE_GIT_STATUS_STDOUT = " M src/example.py\n"
_EXPECTED_CHANGED_FILES = ["src/example.py"]


def _fake_subprocess_run(*args: Any, **kwargs: Any) -> Any:
    cmd = args[0] if args else kwargs.get("args")
    return subprocess.CompletedProcess(
        args=cmd,
        returncode=0,
        stdout=_FAKE_GIT_STATUS_STDOUT,
        stderr="",
    )


# --------------------------------------------------------------------
# Fixture inputs and config
# --------------------------------------------------------------------


def _representative_inputs(project_dir: Path) -> dict[str, Any]:
    return {
        "instruction": "Add a dataclass to src/example.py",
        "context": "Recent: nothing relevant.",
        "prior_errors": "",
        "eliminated": ["use a NamedTuple"],
        "project_dir": str(project_dir),
        "description": "Test project",
        "task_label": "Task 1",
        "task_id": "T-123456",
        "check_commands": ["pytest -q"],
        "is_bug_task": False,
    }


def _orchestra_config() -> OrchestraConfig:
    return OrchestraConfig.from_dict(
        {
            "roles": {
                "editor": {
                    "adapter": "claude_code_agent",
                    "model": "opus",
                    "tools": "default",
                    "parameters": {},
                },
            },
            "workflows": {
                "code_edit": {"pattern": "single"},
            },
        }
    )


def _inner_cli_calls() -> list[_PopenCall]:
    return [c for c in _popen_calls if not c.is_watchdog]


# --------------------------------------------------------------------
# Test fixtures
# --------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_popen_calls() -> None:
    _popen_calls.clear()


@pytest.fixture
def patched_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orch_sp.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run)


@pytest.fixture
def patched_failing_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orch_sp.subprocess, "Popen", _FailingFakePopen)
    monkeypatch.setattr(subprocess, "Popen", _FailingFakePopen)
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run)


# --------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------


def test_code_edit_prompt_includes_task_id_in_task_line_and_notes(
    tmp_path: Path,
) -> None:
    inputs = _representative_inputs(tmp_path)

    prompt = build_code_edit_prompt(inputs)

    assert (
        "Execute the following task now. Treat the Task line as the"
        " work order for this session, not as background context."
        " Make the required repository changes unless the task is"
        " explicitly read-only; do not ask what to work on.\n\n"
        "Task: [T-123456] Add a dataclass to src/example.py"
    ) in prompt
    assert (
        "current date and reference the task: [Task 1] [T-123456] "
        "Add a dataclass to src/example.py."
    ) in prompt


def test_parity_code_edit_single(tmp_path: Path, patched_subprocess: None) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    direct_log_dir = tmp_path / "direct_logs"
    orchestra_log_dir = tmp_path / "orchestra_logs"
    data_root = tmp_path / "orchestra_runs"

    inputs = _representative_inputs(project_dir)
    model = "opus"
    timeout = 900

    direct = invoke_code_edit_direct(
        inputs,
        project_dir=project_dir,
        log_dir=direct_log_dir,
        model=model,
        timeout=timeout,
    )
    direct_calls = list(_inner_cli_calls())

    _popen_calls.clear()
    orchestra = invoke_code_edit_orchestra(
        inputs,
        project_dir=project_dir,
        log_dir=orchestra_log_dir,
        model=model,
        timeout=timeout,
        config=_orchestra_config(),
        data_root=data_root,
    )
    orchestra_calls = list(_inner_cli_calls())

    # Plan-level structural equivalence.
    assert direct.success == orchestra.success
    assert direct.exit_code == orchestra.exit_code
    assert direct.changed_files == orchestra.changed_files
    assert direct.success is True
    assert direct.exit_code == 0
    assert direct.changed_files == _EXPECTED_CHANGED_FILES

    # Both paths must hit the inner CLI exactly once.
    assert len(direct_calls) == 1
    assert len(orchestra_calls) == 1

    direct_call = direct_calls[0]
    orch_call = orchestra_calls[0]

    # Pass-7 fix: orchestra adapters pipe the prompt via stdin
    # instead of placing it in argv. The mcloop direct path still
    # places the prompt as a positional argument (claude -p
    # <prompt>). Equivalence now means: the two paths feed the same
    # prompt bytes to the inner CLI. With the prompt off argv the
    # remaining command shape is otherwise identical.
    direct_prompt = direct_call.cmd[2]
    assert "Task: [T-123456] Add a dataclass to src/example.py" in direct_prompt
    assert orch_call.stdin_buffer is not None
    orch_prompt = orch_call.stdin_buffer.captured
    assert direct_prompt == orch_prompt, (
        "both paths must build the same prompt from the same inputs"
    )
    direct_cmd_no_prompt = direct_call.cmd[:2] + direct_call.cmd[3:]
    assert direct_cmd_no_prompt == list(orch_call.cmd), (
        "command shape must match apart from the prompt position"
    )
    assert direct_call.cwd == orch_call.cwd

    # The model override threaded through invocation_options must
    # appear in the orchestra path's command exactly as in the direct
    # path. The same flags must be present.
    assert "--model" in direct_call.cmd
    assert direct_call.cmd[direct_call.cmd.index("--model") + 1] == model
    assert "--model" in orch_call.cmd
    assert orch_call.cmd[orch_call.cmd.index("--model") + 1] == model
    assert "--allowedTools" in direct_call.cmd
    assert "--allowedTools" in orch_call.cmd

    # The task label gets folded into the env of the inner CLI on both
    # sides, proving log_dir / model / timeout are not the only knobs
    # threaded through; the rest of build_session_env runs too.
    assert direct_call.env is not None
    assert orch_call.env is not None
    assert direct_call.env.get("MCLOOP_TASK_LABEL") == inputs["task_label"]
    assert orch_call.env.get("MCLOOP_TASK_LABEL") == inputs["task_label"]

    # Each backend must have written a log file under its log_dir,
    # proving the per-call log_dir argument was honored on both sides.
    direct_logs = list(direct_log_dir.glob("*.log"))
    orch_logs = list(orchestra_log_dir.glob("*.log"))
    assert len(direct_logs) == 1
    assert len(orch_logs) == 1
    for log_file in direct_logs + orch_logs:
        content = log_file.read_text()
        for line in _TRANSCRIPT_LINES:
            assert line.strip() in content

    # The orchestra path carries a summary; the direct path does not.
    assert direct.summary is None
    assert orchestra.summary is not None
    assert orchestra.summary.get("terminal") == "done"
    assert orchestra.summary.get("exit_code") == 0


def test_parity_code_edit_failure_propagates(
    tmp_path: Path, patched_failing_subprocess: None
) -> None:
    """Both backends must agree on a non-zero exit code as well."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    inputs = _representative_inputs(project_dir)

    direct = invoke_code_edit_direct(
        inputs,
        project_dir=project_dir,
        log_dir=tmp_path / "direct_logs",
        model="opus",
        timeout=600,
    )
    _popen_calls.clear()
    orchestra = invoke_code_edit_orchestra(
        inputs,
        project_dir=project_dir,
        log_dir=tmp_path / "orchestra_logs",
        model="opus",
        timeout=600,
        config=_orchestra_config(),
        data_root=tmp_path / "orchestra_runs",
    )

    assert direct.success == orchestra.success
    assert direct.exit_code == orchestra.exit_code
    assert direct.success is False
    assert direct.exit_code == 1


def test_parity_code_edit_provider_env(
    tmp_path: Path,
    patched_subprocess: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both backends must produce identical provider env when the
    model is a third-party alias.

    Routes the canonical kimi-k2.6 alias through the OpenRouter
    provider config and asserts every env var apply_provider_env
    sets matches between the two backends, including the emptied
    ANTHROPIC_API_KEY. This catches regressions where the orchestra
    path forgets to thread executor config through to
    build_session_env.

    The user's real ~/.mcloop/config.json is masked for the duration
    of the test so a developer with executor overrides locally does
    not fail the test for environmental reasons. ``load_role_config``
    is replaced with a stub that returns an empty dict, and a sentinel
    asserts the patch was actually applied by every call site.
    """
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-or-key-1234")

    stub_call_count = 0

    def _stub_load_role_config(
        role: str, source: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        nonlocal stub_call_count
        stub_call_count += 1
        assert role == "executor", (
            f"only the executor role section should be read here, got {role!r}"
        )
        return None

    monkeypatch.setattr(orch_sp, "load_role_config", _stub_load_role_config)

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    inputs = _representative_inputs(project_dir)
    model = "kimi-k2.6"

    invoke_code_edit_direct(
        inputs,
        project_dir=project_dir,
        log_dir=tmp_path / "direct_logs",
        model=model,
        timeout=600,
    )
    direct_call = _inner_cli_calls()[0]
    assert direct_call.env is not None
    direct_env = dict(direct_call.env)

    _popen_calls.clear()
    invoke_code_edit_orchestra(
        inputs,
        project_dir=project_dir,
        log_dir=tmp_path / "orchestra_logs",
        model=model,
        timeout=600,
        config=_orchestra_config(),
        data_root=tmp_path / "orchestra_runs",
    )
    orch_call = _inner_cli_calls()[0]
    assert orch_call.env is not None
    orch_env = dict(orch_call.env)

    keys_to_match = [
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "CLAUDE_CODE_SUBAGENT_MODEL",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
        "ENABLE_TOOL_SEARCH",
        "ANTHROPIC_API_KEY",
    ]
    for key in keys_to_match:
        assert key in direct_env, f"direct env missing {key!r}: {sorted(direct_env)}"
        assert key in orch_env, f"orchestra env missing {key!r}: {sorted(orch_env)}"
        assert direct_env[key] == orch_env[key], (
            f"{key} differs: direct={direct_env[key]!r} orchestra={orch_env[key]!r}"
        )

    # Provider routing must have actually fired: BASE_URL points at
    # the OpenRouter endpoint, AUTH_TOKEN holds the faked key, the
    # model slug is the resolved provider/model form, and the API
    # key is emptied so the CLI does not bill against the wrong
    # account.
    assert direct_env["ANTHROPIC_BASE_URL"] == "https://openrouter.ai/api"
    assert direct_env["ANTHROPIC_AUTH_TOKEN"] == "test-or-key-1234"
    assert direct_env["ANTHROPIC_MODEL"] == "moonshotai/kimi-k2.6"
    assert direct_env["ANTHROPIC_API_KEY"] == ""

    # Sanity check: the load_role_config patch must have fired at
    # least once on each side (one direct call, one orchestra call).
    # If a future refactor bypasses the patch, this assertion fires
    # so the test does not silently start reading the developer's
    # real ~/.mcloop/config.json again.
    assert stub_call_count >= 2, (
        f"load_role_config stub was hit only {stub_call_count} times. "
        "Each backend should consult it once."
    )


def test_parity_code_edit_bug_task_branches_prompt(
    tmp_path: Path, patched_subprocess: None
) -> None:
    """When is_bug_task is true, both backends must produce the
    bug-task prompt, not the normal prompt."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    inputs = _representative_inputs(project_dir)
    inputs["is_bug_task"] = True

    invoke_code_edit_direct(
        inputs,
        project_dir=project_dir,
        log_dir=tmp_path / "direct_logs",
        model="opus",
        timeout=600,
    )
    direct_call = _inner_cli_calls()[0]

    _popen_calls.clear()
    invoke_code_edit_orchestra(
        inputs,
        project_dir=project_dir,
        log_dir=tmp_path / "orchestra_logs",
        model="opus",
        timeout=600,
        config=_orchestra_config(),
        data_root=tmp_path / "orchestra_runs",
    )
    orch_call = _inner_cli_calls()[0]

    direct_prompt = direct_call.cmd[2]
    # Pass-7 fix: orchestra adapter pipes prompt via stdin, not argv.
    assert orch_call.stdin_buffer is not None
    orch_prompt = orch_call.stdin_buffer.captured
    assert direct_prompt == orch_prompt
    assert "BUG FIX (MANDATORY CODE CHANGE)" in direct_prompt
    assert (
        "Execute the following task now. Treat the Task line as the"
        " work order for this session, not as background context."
        " Make the required repository changes unless the task is"
        " explicitly read-only; do not ask what to work on.\n\n"
        "Task: [T-123456] Add a dataclass to src/example.py"
    ) in direct_prompt
