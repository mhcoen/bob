"""Wrapper tests for the code-edit dispatch.

Two tests prove the wrapper interface works on both backends:

1. The direct path uses the legacy run_task body (now lifted into
   invoke_code_edit's direct backend). The runner private helpers are
   patched on the mcloop.runner module namespace so any caller
   (including invoke_code_edit) sees the patches.
2. The orchestra path mocks orchestra.run_workflow itself, the only
   public boundary the wrapper crosses. The test asserts that the
   model, timeout, log_dir, and project_dir the caller passed all
   arrive in invocation_options, and that the WorkflowRunResult the
   stub returns is converted into the expected CodeEditResult shape.

Both backends produce a CodeEditResult; the assertions confirm the
shape and the structural fields the plan calls out (success,
exit_code, changed_files, log_path). The orchestra-backend test
patches only the public ``orchestra.run_workflow`` symbol so internal
orchestra refactors do not break this suite.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
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


def _make_workflow_run_result(log_path: Path) -> Any:
    """Return a duck-typed stand-in for ``WorkflowRunResult``.

    The wrapper only reads ``terminal``, ``log_path``, and
    ``summary``. Patching at the ``run_workflow`` boundary means the
    test does not need to construct the full real type, and a future
    orchestra refactor that adds required fields to
    ``WorkflowRunResult`` cannot break this test as long as the three
    consumed fields keep their names.
    """
    summary = {
        "terminal": "done",
        "output": "ok\n",
        "exit_code": 0,
        "changed_files": ["src/example.py"],
        "files_changed": True,
        "adapter_log": str(log_path),
    }
    return SimpleNamespace(
        run_id="run-test-1",
        terminal="done",
        envelope=None,
        artifacts={},
        log_path=log_path,
        summary=summary,
    )


def test_orchestra_backend_returns_code_edit_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When .orchestra/config.json maps code_edit to single, the
    orchestra backend fires. The wrapper crosses one public boundary
    (``orchestra.run_workflow``); patch only that symbol and assert
    the wrapper threads ``model``, ``timeout``, ``log_dir``, and
    ``project_dir`` through ``invocation_options``, and converts the
    returned ``WorkflowRunResult`` into the expected ``CodeEditResult``.
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
    log_dir.mkdir()
    adapter_log = log_dir / "session.log"
    adapter_log.write_text("ok\n")

    inputs = _representative_inputs(project_dir)
    inputs["log_dir"] = log_dir
    inputs["timeout"] = 1234

    captured: dict[str, Any] = {}

    def _stub_run_workflow(
        workflow_name: str,
        inputs_arg: dict[str, Any],
        config: Any,
        *,
        invocation_options: dict[str, Any] | None = None,
        project_dir: Any = None,
        data_root: Any = None,
        **extra: Any,
    ) -> Any:
        captured["workflow_name"] = workflow_name
        captured["inputs"] = inputs_arg
        captured["invocation_options"] = invocation_options
        captured["project_dir"] = project_dir
        captured["data_root"] = data_root
        return _make_workflow_run_result(adapter_log)

    import mcloop.code_edit as _ce

    monkeypatch.setattr("orchestra.run_workflow", _stub_run_workflow)
    monkeypatch.setattr(_ce, "run_workflow", _stub_run_workflow, raising=False)

    assert _select_backend(project_dir) == "orchestra"
    result = invoke_code_edit(**inputs)

    assert captured["workflow_name"] == "code_edit"
    assert captured["invocation_options"] is not None
    invo = captured["invocation_options"]
    assert invo["model"] == "opus"
    assert invo["timeout"] == 1234
    assert invo["log_dir"] == str(log_dir)
    assert invo["project_dir"] == str(project_dir)
    assert Path(captured["project_dir"]) == project_dir
    assert Path(captured["data_root"]) == log_dir / "orchestra-runs"
    assert captured["inputs"]["instruction"] == inputs["instruction"]

    assert isinstance(result, CodeEditResult)
    assert result.success is True
    assert result.exit_code == 0
    assert result.output == "ok\n"
    assert result.changed_files == ["src/example.py"]
    assert result.summary is not None
    assert result.summary.get("terminal") == "done"
    assert result.summary.get("exit_code") == 0
    assert result.log_path == adapter_log


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
