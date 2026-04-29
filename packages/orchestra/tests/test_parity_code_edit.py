"""Parity test: direct backend vs orchestra-via-single backend.

The integration plan calls for a wrapper interface ``invoke_code_edit``
with two backends, ``direct`` and ``orchestra``, that both produce a
``CodeEditResult``. This test constructs both backends in-process,
runs them on the same fixture task, and asserts structural equivalence
(matching ``success``, ``exit_code``, and ``changed_files``). Output
content is allowed to vary, since two real model invocations would
produce different text.

The test does not invoke real Claude Code. Both backends ultimately
call ``subprocess.Popen`` through the same helper in
``orchestra.adapters._subprocess`` (or a transcribed copy), which is
patched to return a canned stream-json transcript with a deterministic
exit code. ``git status --porcelain`` is also patched to return a
deterministic changed-files list.

The ``direct`` backend transcribes the body of mcloop's ``run_task``
into a function that produces a ``CodeEditResult``. The transcription
is structural: same command shape, same env passthrough, same log
write. It deliberately avoids importing mcloop so the orchestra repo
does not take a runtime dependency on a sibling project.
"""

from __future__ import annotations

import io
import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from orchestra.adapters import _subprocess as orch_sp
from orchestra.adapters.claude_code_agent import DEFAULT_ALLOWED_TOOLS
from orchestra.api import run_workflow
from orchestra.config import OrchestraConfig

# --------------------------------------------------------------------
# CodeEditResult (the wrapper-interface return shape)
# --------------------------------------------------------------------


@dataclass
class CodeEditResult:
    """Mcloop-facing result shape produced by ``invoke_code_edit``.

    Mirrors the plan's wrapper interface. ``summary`` is non-None for
    the orchestra backend and absent (None) for the direct backend.
    """

    success: bool
    output: str
    exit_code: int
    log_path: Path
    changed_files: list[str] = field(default_factory=list)
    summary: dict[str, Any] | None = None


# --------------------------------------------------------------------
# Direct backend: a transcription of mcloop's run_task body
# --------------------------------------------------------------------


_DIRECT_PASSTHROUGH_VARS = frozenset(
    {
        "PATH",
        "HOME",
        "TERM",
        "LANG",
        "LC_ALL",
        "TMPDIR",
        "USER",
        "LOGNAME",
        "SHELL",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "COLORTERM",
        "FORCE_COLOR",
        "NO_COLOR",
        "RTK_DB_PATH",
        "RTK_TEE",
        "RTK_TEE_DIR",
    }
)


def _direct_build_session_env(task_label: str) -> dict[str, str]:
    import os

    env = {k: v for k, v in os.environ.items() if k in _DIRECT_PASSTHROUGH_VARS}
    if task_label:
        env["MCLOOP_TASK_LABEL"] = task_label
    return env


def _direct_build_command(
    prompt: str,
    model: str | None,
    allowed_tools: str = DEFAULT_ALLOWED_TOOLS,
) -> list[str]:
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


def _direct_build_prompt(inputs: dict[str, Any]) -> str:
    parts = [
        inputs["instruction"],
        f"Project description: {inputs['description']}",
        f"Task label: {inputs['task_label']}",
        f"Recent session context:\n{inputs['context']}",
        f"Approaches already ruled out:\n{inputs['eliminated']}",
        f"Prior errors from the previous attempt (empty if first try):\n{inputs['prior_errors']}",
    ]
    return "\n\n".join(parts) + "\n"


def _direct_run_session(
    cmd: list[str], cwd: Path, env: dict[str, str], timeout: int
) -> tuple[str, int]:
    """Mirror mcloop's _run_session at the structural level."""
    process = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        start_new_session=True,
    )
    if process.stdout is None:
        raise RuntimeError("stdout is None despite stdout=PIPE")
    lines: list[str] = []
    for line in process.stdout:
        lines.append(line)
    process.wait()
    return "".join(lines), process.returncode


def _direct_write_log(
    log_dir: Path,
    task_text: str,
    cmd: list[str],
    output: str,
    exit_code: int,
) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = "".join(
        ch if ch.isalnum() else "-" for ch in task_text.lower()
    ).strip("-")[:50]
    log_path = log_dir / f"{timestamp}_{slug or 'task'}.log"
    log_path.write_text(
        f"Task: {task_text}\n"
        f"Command: {' '.join(cmd)}\n"
        f"Exit code: {exit_code}\n"
        f"{'=' * 60}\n"
        f"{output}\n"
    )
    return log_path


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
    """Direct backend: structural transcription of mcloop's run_task body."""
    prompt = _direct_build_prompt(inputs)
    cmd = _direct_build_command(prompt, model)
    env = _direct_build_session_env(inputs["task_label"])
    output, exit_code = _direct_run_session(cmd, project_dir, env, timeout)
    log_path = _direct_write_log(
        log_dir, inputs["instruction"], cmd, output, exit_code
    )
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
    enriched = dict(inputs)
    enriched.setdefault("project_dir", str(project_dir))
    result = run_workflow(
        "code_edit",
        enriched,
        config,
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
# Subprocess-layer mocks
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


class _FakePopen:
    """A subprocess.Popen replacement that yields a canned transcript."""

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
        self.pid = 12345
        self.returncode: int | None = None
        self.stdout = io.StringIO("".join(_TRANSCRIPT_LINES))

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def kill(self) -> None:
        if self.returncode is None:
            self.returncode = -9


_FAKE_GIT_STATUS_STDOUT = " M src/example.py\n"


def _fake_subprocess_run(*args: Any, **kwargs: Any) -> Any:
    cmd = args[0] if args else kwargs.get("args")
    completed = subprocess.CompletedProcess(
        args=cmd,
        returncode=0,
        stdout=_FAKE_GIT_STATUS_STDOUT,
        stderr="",
    )
    return completed


_EXPECTED_CHANGED_FILES = ["src/example.py"]


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
        "check_commands": ["pytest -q"],
        "is_bug_task": False,
    }


def _orchestra_config() -> OrchestraConfig:
    return OrchestraConfig.from_dict(
        {
            "workflows": {
                "code_edit": {
                    "pattern": "single",
                    "roles": {
                        "editor": {
                            "adapter": "claude_code_agent",
                            "model": "opus",
                            "tools": "default",
                            "parameters": {},
                        }
                    },
                }
            }
        }
    )


# --------------------------------------------------------------------
# The test
# --------------------------------------------------------------------


@pytest.fixture
def patched_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orch_sp.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run)


def test_parity_code_edit_single(
    tmp_path: Path, patched_subprocess: None
) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    direct_log_dir = tmp_path / "direct_logs"
    orchestra_log_dir = tmp_path / "orchestra_logs"
    data_root = tmp_path / "orchestra_runs"

    inputs = _representative_inputs(project_dir)

    direct = invoke_code_edit_direct(
        inputs,
        project_dir=project_dir,
        log_dir=direct_log_dir,
        model="opus",
    )
    orchestra = invoke_code_edit_orchestra(
        inputs,
        project_dir=project_dir,
        log_dir=orchestra_log_dir,
        model="opus",
        config=_orchestra_config(),
        data_root=data_root,
    )

    # Structural equivalence per the plan: matching success, exit_code,
    # changed_files. Output content is allowed to vary.
    assert direct.success == orchestra.success
    assert direct.exit_code == orchestra.exit_code
    assert direct.changed_files == orchestra.changed_files

    # Both should be successful with our happy-path mock.
    assert direct.success is True
    assert direct.exit_code == 0
    assert direct.changed_files == _EXPECTED_CHANGED_FILES

    # Both should have written a log file containing the canned
    # transcript.
    for result in (direct, orchestra):
        assert result.log_path.exists()
        content = result.log_path.read_text()
        for line in _TRANSCRIPT_LINES:
            assert line.strip() in content

    # The orchestra backend carries a summary dict; the direct backend
    # does not.
    assert orchestra.summary is not None
    assert orchestra.summary.get("terminal") == "done"
    assert orchestra.summary.get("exit_code") == 0
    assert direct.summary is None


def test_parity_code_edit_failure_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both backends must agree on a non-zero exit code as well."""

    class _FailingPopen(_FakePopen):
        def wait(self, timeout: float | None = None) -> int:
            self.returncode = 1
            return 1

    monkeypatch.setattr(orch_sp.subprocess, "Popen", _FailingPopen)
    monkeypatch.setattr(subprocess, "Popen", _FailingPopen)
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run)

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    inputs = _representative_inputs(project_dir)

    direct = invoke_code_edit_direct(
        inputs,
        project_dir=project_dir,
        log_dir=tmp_path / "direct_logs",
        model="opus",
    )
    orchestra = invoke_code_edit_orchestra(
        inputs,
        project_dir=project_dir,
        log_dir=tmp_path / "orchestra_logs",
        model="opus",
        config=_orchestra_config(),
        data_root=tmp_path / "orchestra_runs",
    )

    assert direct.success == orchestra.success
    assert direct.exit_code == orchestra.exit_code
    assert direct.success is False
    assert direct.exit_code == 1
