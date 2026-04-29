"""Wrapper tests for the code-edit dispatch.

Two tests prove the wrapper interface works on both backends:

1. The direct path uses the legacy run_task body (now lifted into
   invoke_code_edit's direct backend). Subprocess.Popen is mocked the
   same way the rest of mcloop's tests mock it: _build_command,
   _run_session, _write_log are patched on the mcloop.runner module
   namespace so any caller (including invoke_code_edit) sees the
   patches.
2. The orchestra path uses orchestra.run_workflow under a temporary
   .orchestra/config.json that maps code_edit to the single pattern.
   Subprocess.Popen and subprocess.run are patched the same way
   orchestra's parity test patches them, so the test runs offline and
   returns a deterministic transcript and changed-files list.

Both backends produce a CodeEditResult; the assertions confirm the
shape and the structural fields the plan calls out (success,
exit_code, changed_files, log_path).
"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from mcloop.code_edit import CodeEditResult, _select_backend, invoke_code_edit

# --------------------------------------------------------------------
# Fixture inputs
# --------------------------------------------------------------------


def _representative_inputs(project_dir: Path) -> dict[str, Any]:
    return {
        "instruction": "Add a dataclass to src/example.py",
        "context": "Recent: nothing relevant.",
        "prior_errors": "",
        "eliminated": ["use a NamedTuple"],
        "project_dir": project_dir,
        "log_dir": project_dir / "logs",
        "description": "Test project",
        "task_label": "Task 1",
        "check_commands": ["pytest -q"],
        "is_bug_task": False,
        "model": "opus",
    }


# --------------------------------------------------------------------
# Direct-backend test
# --------------------------------------------------------------------


def test_direct_backend_returns_code_edit_result(tmp_path: Path) -> None:
    """When .orchestra/config.json is absent, invoke_code_edit picks
    the direct backend and returns a populated CodeEditResult.

    The mcloop.runner private helpers are patched so the test never
    invokes a real CLI. The backend selector in code_edit.py checks
    the project_dir for the config file; without it the direct path
    fires.
    """
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    log_dir = tmp_path / "logs"
    inputs = _representative_inputs(project_dir)
    inputs["log_dir"] = log_dir

    captured: dict[str, Any] = {}

    def _fake_build_command(cli, prompt, **kwargs):
        captured["cli"] = cli
        captured["prompt"] = prompt
        captured["model"] = kwargs.get("model")
        return ["echo", "ok"]

    log_path = tmp_path / "session.log"
    with (
        patch(
            "mcloop.runner._build_command", side_effect=_fake_build_command
        ),
        patch("mcloop.runner._run_session", return_value=("ok\n", 0)),
        patch("mcloop.runner._write_log", return_value=log_path),
    ):
        result = invoke_code_edit(**inputs)

    assert _select_backend(project_dir) == "direct"
    assert isinstance(result, CodeEditResult)
    assert result.success is True
    assert result.exit_code == 0
    assert result.output == "ok\n"
    assert result.log_path == log_path
    assert result.changed_files == []
    assert result.summary is None

    # The direct path uses the normal-task prompt (no prior_errors,
    # not a bug-task), so the captured prompt should mention the
    # instruction text and not the bug-fix banner.
    assert captured["cli"] == "claude"
    assert captured["model"] == "opus"
    assert inputs["instruction"] in captured["prompt"]
    assert "BUG FIX" not in captured["prompt"]


# --------------------------------------------------------------------
# Orchestra-backend test
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
    """Subprocess.Popen replacement that yields a canned transcript.

    Mirrors the FakePopen orchestra's parity test uses. Watchdog
    spawns (sh -c) get a None stdout so they exit cleanly without
    interfering with the captured transcript.
    """

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
        is_watchdog = bool(cmd and cmd[0] == "sh" and "-c" in cmd)
        if is_watchdog:
            self.stdout = None
        else:
            self.stdout = io.StringIO("".join(_TRANSCRIPT_LINES))

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def kill(self) -> None:
        if self.returncode is None:
            self.returncode = -9


def _fake_subprocess_run(*args: Any, **kwargs: Any) -> Any:
    cmd = args[0] if args else kwargs.get("args")
    return subprocess.CompletedProcess(
        args=cmd,
        returncode=0,
        stdout=" M src/example.py\n",
        stderr="",
    )


def test_orchestra_backend_returns_code_edit_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When .orchestra/config.json maps code_edit to single, the
    orchestra backend fires and the result carries the orchestra
    summary plus the same structural fields the direct backend
    returns.
    """
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    config_dir = project_dir / ".orchestra"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps(
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
    )

    log_dir = tmp_path / "logs"
    inputs = _representative_inputs(project_dir)
    inputs["log_dir"] = log_dir

    # Patch the orchestra subprocess layer the same way orchestra's
    # parity test does. The orchestra adapter uses its own
    # subprocess.Popen import inside orchestra.adapters._subprocess.
    from orchestra.adapters import _subprocess as orch_sp

    # Isolate provider env from the developer's home directory.
    monkeypatch.setattr(
        orch_sp, "load_role_config", lambda role, source=None: None
    )
    monkeypatch.setattr(orch_sp.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run)

    assert _select_backend(project_dir) == "orchestra"
    result = invoke_code_edit(**inputs)

    assert isinstance(result, CodeEditResult)
    assert result.success is True
    assert result.exit_code == 0
    # The transcript reaches the wrapper as the captured output.
    for line in _TRANSCRIPT_LINES:
        assert line.strip() in result.output
    # The orchestra summary surfaces git-status changed files.
    assert result.changed_files == ["src/example.py"]
    assert result.summary is not None
    assert result.summary.get("terminal") == "done"
    assert result.summary.get("exit_code") == 0
    assert result.log_path.exists()


def test_orchestra_backend_falls_back_when_pattern_is_direct(
    tmp_path: Path,
) -> None:
    """The 'direct' sentinel in workflows.code_edit.pattern opts out
    of orchestra without removing the config file."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    config_dir = project_dir / ".orchestra"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps(
            {
                "workflows": {
                    "code_edit": {
                        "pattern": "direct",
                        "roles": {},
                    }
                }
            }
        )
    )
    assert _select_backend(project_dir) == "direct"


def test_select_backend_handles_malformed_config(tmp_path: Path) -> None:
    """A malformed config falls back to direct without raising."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    config_dir = project_dir / ".orchestra"
    config_dir.mkdir()
    (config_dir / "config.json").write_text("not json {{")
    assert _select_backend(project_dir) == "direct"
