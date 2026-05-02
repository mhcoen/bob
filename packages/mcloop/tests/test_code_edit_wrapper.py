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
# Global-config isolation
# --------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_orchestra_global_config(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Point ``orchestra.config.global_config_path`` at an empty tmp
    directory so the developer's real ``~/.orchestra/config.json`` does
    not influence test outcomes.

    Tests that want a global config write into the returned path.
    Without this fixture, every assertion that depends on
    ``_select_backend`` returning ``"direct"`` would be flaky on a
    developer machine where ``~/.orchestra/config.json`` happens to
    declare a ``code_edit`` workflow.
    """
    isolated_global = (
        tmp_path_factory.mktemp("isolated-orchestra") / "config.json"
    )
    monkeypatch.setattr(
        "orchestra.config.global_config_path", lambda: isolated_global
    )
    # Reset the project-override warning latch so each test starts
    # with the same notification state.
    monkeypatch.setattr(
        "mcloop.code_edit._PROJECT_OVERRIDE_NOTE_EMITTED", False
    )
    return isolated_global


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
    # Current orchestra schema: top-level roles table plus a
    # workflows.<name>.pattern that does not carry per-workflow roles.
    (config_dir / "config.json").write_text(
        json.dumps(
            {
                "roles": {
                    "editor": {
                        "adapter": "claude_code_agent",
                        "model": "opus",
                        "tools": "default",
                        "parameters": {},
                    }
                },
                "workflows": {
                    "code_edit": {"pattern": "single"},
                },
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
                    "code_edit": {"pattern": "direct"},
                }
            }
        )
    )
    assert _select_backend(project_dir) == "direct"


def test_select_backend_handles_malformed_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A malformed merged-config load is a loud fallback to direct."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    config_dir = project_dir / ".orchestra"
    config_dir.mkdir()
    (config_dir / "config.json").write_text("not json {{")
    assert _select_backend(project_dir) == "direct"
    err = capsys.readouterr().err
    # Malformed config trips the loud fallback path.
    assert "[orchestra] falling back to direct backend due to error" in err


# --------------------------------------------------------------------
# New behavior: global config is sufficient; project-local override
# emits a one-time note.
# --------------------------------------------------------------------


def test_select_backend_uses_global_config_when_no_project_file(
    tmp_path: Path,
    _isolate_orchestra_global_config: Path,
) -> None:
    """Global config alone with code_edit -> draft_then_adjudicate must
    select orchestra without any project-local file present."""
    _isolate_orchestra_global_config.parent.mkdir(parents=True, exist_ok=True)
    _isolate_orchestra_global_config.write_text(
        json.dumps(
            {
                "roles": {
                    "drafter": {"adapter": "claude_code_text", "model": "opus"},
                    "adjudicator": {
                        "adapter": "claude_code_text",
                        "model": "opus",
                    },
                    "editor": {
                        "adapter": "claude_code_agent",
                        "model": "opus",
                    },
                },
                "workflows": {
                    "code_edit": {"pattern": "draft_then_adjudicate"},
                },
            }
        )
    )
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    # No project-local file. Selection must read the global config and
    # return "orchestra".
    assert _select_backend(project_dir) == "orchestra"


def test_select_backend_emits_project_override_note_to_stderr(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A project-local .orchestra/config.json triggers a one-time note
    that the project is overriding the global config. The note must
    appear on stderr regardless of whether orchestra ends up
    dispatched."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    config_dir = project_dir / ".orchestra"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps(
            {"workflows": {"code_edit": {"pattern": "direct"}}}
        )
    )
    # Even when the override forces direct, the banner still fires.
    assert _select_backend(project_dir) == "direct"
    err = capsys.readouterr().err
    assert "[orchestra] PROJECT-LOCAL OVERRIDE DETECTED" in err
    assert str((config_dir / "config.json").resolve()) in err
    assert "overrides ~/.orchestra/config.json" in err
    assert "mcloop ack-orchestra-override" in err


def test_select_backend_project_override_note_fires_only_once(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Repeated calls to _select_backend in a single process must not
    re-emit the override note. The outer mcloop loop calls this once
    per task; the message would be noise on every iteration."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    config_dir = project_dir / ".orchestra"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps({"workflows": {"code_edit": {"pattern": "direct"}}})
    )

    _select_backend(project_dir)
    first = capsys.readouterr().err
    _select_backend(project_dir)
    _select_backend(project_dir)
    later = capsys.readouterr().err

    # The banner appears in the first call's stderr but not the later ones.
    assert "[orchestra] PROJECT-LOCAL OVERRIDE DETECTED" in first
    assert "[orchestra] PROJECT-LOCAL OVERRIDE DETECTED" not in later


def test_select_backend_global_with_local_override_to_direct(
    tmp_path: Path,
    _isolate_orchestra_global_config: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Global config selects orchestra; project-local override forces
    direct. Result must be direct, and the override note fires."""
    _isolate_orchestra_global_config.parent.mkdir(parents=True, exist_ok=True)
    _isolate_orchestra_global_config.write_text(
        json.dumps(
            {
                "roles": {
                    "drafter": {"adapter": "claude_code_text", "model": "opus"},
                    "adjudicator": {
                        "adapter": "claude_code_text",
                        "model": "opus",
                    },
                    "editor": {
                        "adapter": "claude_code_agent",
                        "model": "opus",
                    },
                },
                "workflows": {
                    "code_edit": {"pattern": "draft_then_adjudicate"},
                },
            }
        )
    )
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    config_dir = project_dir / ".orchestra"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps({"workflows": {"code_edit": {"pattern": "direct"}}})
    )

    # The merged config has code_edit.pattern == "direct" (project-local
    # overrides global per the merge rule), so the result is direct.
    assert _select_backend(project_dir) == "direct"
    # And the override banner fires because the local file is present.
    err = capsys.readouterr().err
    assert "[orchestra] PROJECT-LOCAL OVERRIDE DETECTED" in err


def test_bug_verify_direct_routes_third_party_provider_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``invoke_bug_verify`` must thread provider env through to the
    subprocess so third-party model aliases (kimi-k2.6, DeepSeek)
    actually route through the correct endpoint.

    The legacy ``run_bug_verify`` body skipped the provider env mutation
    and consequently sent kimi-k2.6 / DeepSeek bug-verify sessions to
    the default Anthropic endpoint. The fix builds a session env up
    front and passes it through ``_build_command`` so
    ``_apply_provider_env`` fires.

    The assertion is split into two capture points so the test cannot
    be satisfied by a regression that replaces ``_build_session_env``
    with a no-op:

    - ``captured_pre`` is a snapshot of the env returned by
      ``_build_session_env`` before ``_build_command`` runs. It must
      contain at least one passthrough variable (PATH) and must NOT
      contain the provider-routing keys yet. A regression where
      ``session_env = {}`` fails the PATH assertion. A regression
      where ``_build_session_env`` itself starts injecting provider
      routing fails the absence assertion.
    - ``captured_env`` is the env at ``_run_session``, after
      ``_build_command`` has called ``_apply_provider_env``. It must
      contain every provider-routing key. A regression that bypasses
      ``_apply_provider_env`` fails this assertion.

    A discard-and-replace regression (call ``_build_session_env``,
    drop the result, hand a fresh dict to ``_build_command``) is
    caught by the per-key survival loop at the end: every key from
    the pre snapshot must be present in the env at ``_run_session``
    with the same value. We deliberately do NOT assert object
    identity (``pre_obj is post_obj``). Identity would fail
    spuriously if a future legitimate refactor introduces a shallow
    copy of the env (``env=dict(session_env)``) somewhere along the
    path, even though subprocess behavior would be unchanged. The
    survival loop is the right level: it tracks the contract
    (every key from the pre env reaches the subprocess) without
    pinning the implementation (the same dict object).

    The user's real ``~/.mcloop/config.json`` is masked by stubbing
    ``mcloop.config.load_role_config`` so a developer with executor
    overrides locally does not fail this test for environmental
    reasons.
    """
    from mcloop.code_edit import invoke_bug_verify
    from mcloop.runner import _build_session_env as _orig_build_session_env

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

    monkeypatch.setattr("mcloop.config.load_role_config", _stub_load_role_config)

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    log_dir = tmp_path / "logs"

    captured_pre: dict[str, str] = {}
    captured_env: dict[str, str] = {}

    def _capturing_build_session_env(
        task_label: str = "", cli: str = "claude"
    ) -> dict[str, str]:
        # Call the real builder so the test sees what the wrapper
        # actually constructs. Snapshot a copy before _build_command
        # mutates it via _apply_provider_env. Return the original
        # dict so the in-place mutation lands as it would in
        # production.
        env = _orig_build_session_env(task_label=task_label, cli=cli)
        captured_pre.clear()
        captured_pre.update(env)
        return env

    def _capture_run_session(
        cmd: list[str],
        cwd: Path,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> tuple[str, int]:
        captured_env.update(env or {})
        return ("ok\n", 0)

    log_path = tmp_path / "session.log"
    with (
        patch(
            "mcloop.runner._build_session_env",
            side_effect=_capturing_build_session_env,
        ),
        patch("mcloop.runner._run_session", side_effect=_capture_run_session),
        patch("mcloop.runner._write_log", return_value=log_path),
    ):
        result = invoke_bug_verify(
            bugs_content="- [ ] Some bug",
            project_dir=project_dir,
            log_dir=log_dir,
            model="kimi-k2.6",
            timeout=600,
        )

    assert isinstance(result, CodeEditResult)
    assert result.success is True
    assert result.exit_code == 0

    # (a) captured_pre is the env BEFORE _build_command's mutation.
    # It must be a real session env, not an empty dict.
    assert captured_pre, (
        "_build_session_env returned an empty dict. A regression that "
        "replaced session_env with {} would silently satisfy the "
        "provider-routing assertions below because _build_command "
        "injects them on whatever object it receives."
    )
    assert "PATH" in captured_pre, (
        "PATH missing from session env; _build_session_env is not "
        "copying passthrough variables. session_env = {} regression."
    )
    # Provider-routing keys must NOT be present yet at this point;
    # _apply_provider_env runs inside _build_command. If they show up
    # here, _build_session_env grew an out-of-band provider mutation.
    assert "ANTHROPIC_BASE_URL" not in captured_pre
    assert "ANTHROPIC_AUTH_TOKEN" not in captured_pre
    assert "ANTHROPIC_MODEL" not in captured_pre

    # (b) captured_env is the env AFTER _build_command. Provider
    # routing keys must be present. A regression that skips
    # _apply_provider_env fails here.
    keys_to_set = [
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
    for key in keys_to_set:
        assert key in captured_env, (
            f"bug-verify env missing {key!r}: {sorted(captured_env)}"
        )

    assert captured_env["ANTHROPIC_BASE_URL"] == "https://openrouter.ai/api"
    assert captured_env["ANTHROPIC_AUTH_TOKEN"] == "test-or-key-1234"
    assert captured_env["ANTHROPIC_MODEL"] == "moonshotai/kimi-k2.6"
    assert captured_env["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "moonshotai/kimi-k2.6"
    assert captured_env["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "moonshotai/kimi-k2.6"
    assert captured_env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "moonshotai/kimi-k2.6"
    assert captured_env["CLAUDE_CODE_SUBAGENT_MODEL"] == "moonshotai/kimi-k2.6"
    assert captured_env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    assert captured_env["ENABLE_TOOL_SEARCH"] == "1"
    assert captured_env["ANTHROPIC_API_KEY"] == ""

    assert stub_call_count >= 1, (
        "load_role_config stub never fired; _apply_provider_env may "
        "have been skipped or load_role_config was bypassed"
    )

    # (c) Per-key survival loop. Every key from the pre snapshot
    # MUST be present in the env at _run_session with the same
    # value. A regression that calls _build_session_env, discards
    # the result, and passes a fresh {} to _build_command would
    # appear to satisfy the (b) provider-routing assertions because
    # _build_command's _apply_provider_env mutates whatever object
    # it receives, but would fail this loop because the passthrough
    # keys (PATH, etc.) from the pre snapshot would not be in the
    # substitute dict.
    #
    # Object identity (``pre_obj is post_obj``) is intentionally NOT
    # asserted. A future legitimate refactor that adds a shallow
    # copy of the env somewhere along the path would not change
    # subprocess behavior but would break an identity check. The
    # survival loop captures the actual contract (every pre-mutation
    # key reaches the subprocess) without pinning the implementation.
    for key, value in captured_pre.items():
        assert key in captured_env, (
            f"key {key!r} present at _build_session_env but missing "
            f"at _run_session"
        )
        assert captured_env[key] == value, (
            f"key {key!r} value drifted between _build_session_env "
            f"and _run_session: {value!r} -> {captured_env[key]!r}"
        )


def test_bug_verify_direct_native_env_matches_legacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Native-Anthropic bug verification must produce a subprocess env
    byte-identical to the legacy ``run_bug_verify`` body.

    The legacy path called ``_run_session(cmd, project_dir)`` with no
    env argument, which in turn called ``_build_session_env()`` with
    the default empty ``task_label``. That left ``MCLOOP_TASK_LABEL``
    unset. A regression where the wrapper calls
    ``_build_session_env(task_label="bug-verify", cli="claude")``
    would inject ``MCLOOP_TASK_LABEL=bug-verify`` into every
    bug-verify subprocess, which is a behavior change visible to
    inner CLI tooling.

    The user's real ``~/.mcloop/config.json`` is masked the same way
    as the third-party test so a developer with executor overrides
    locally does not fail this test for environmental reasons.
    """
    from mcloop.code_edit import invoke_bug_verify

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    def _stub_load_role_config(
        role: str, source: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        return None

    monkeypatch.setattr("mcloop.config.load_role_config", _stub_load_role_config)

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    log_dir = tmp_path / "logs"

    captured_env: dict[str, str] = {}

    def _capture_run_session(
        cmd: list[str],
        cwd: Path,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> tuple[str, int]:
        captured_env.update(env or {})
        return ("ok\n", 0)

    log_path = tmp_path / "session.log"
    with (
        patch("mcloop.runner._run_session", side_effect=_capture_run_session),
        patch("mcloop.runner._write_log", return_value=log_path),
    ):
        invoke_bug_verify(
            bugs_content="- [ ] Some bug",
            project_dir=project_dir,
            log_dir=log_dir,
            model="opus",
            timeout=600,
        )

    assert "MCLOOP_TASK_LABEL" not in captured_env, (
        "MCLOOP_TASK_LABEL leaked into the bug-verify subprocess env "
        "for a native Anthropic model. The legacy run_bug_verify body "
        "left this unset and the wrapper must match exactly."
    )
    # Provider-routing keys must NOT be present for a native model
    # (kimi-k2.6 sets them, opus does not). Catches a future bug where
    # apply_provider_env fires unconditionally.
    assert "ANTHROPIC_BASE_URL" not in captured_env
    assert "ANTHROPIC_AUTH_TOKEN" not in captured_env
    assert "ANTHROPIC_MODEL" not in captured_env
