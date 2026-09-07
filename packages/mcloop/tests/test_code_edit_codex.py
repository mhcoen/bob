"""Coding CLI selection through the configured Orchestra workflow."""

import json
from unittest.mock import Mock

import pytest
from orchestra.api.bindings import _resolve_role_binding
from orchestra.config import OrchestraConfig

from mcloop.code_edit import _editor_config
from mcloop.runner import run_task


@pytest.fixture
def config(tmp_path, monkeypatch):
    cfg = {
        "roles": {
            "editor": {
                "adapter": "claude_code_agent",
                "model": "opus",
                "tools": "Read,Edit",
                "parameters": {"default_model": "opus"},
            },
            "drafter": {"adapter": "claude_code_text", "model": "fable"},
            "adjudicator": {"adapter": "claude_code_text", "model": "sonnet"},
        },
        "workflows": {
            "code_edit": {
                "pattern": "draft_then_adjudicate",
                "role_overrides": {"editor": {"model": "sonnet"}},
            }
        },
    }
    monkeypatch.setattr("orchestra.config.global_config_path", lambda: tmp_path / "absent")
    folder = tmp_path / ".orchestra"
    folder.mkdir()
    (folder / "config.json").write_text(json.dumps(cfg))
    return cfg


@pytest.mark.parametrize("cli,model", [("codex", "gpt-6-astra"), ("claude", "fable")])
def test_chain_selection_runs_configured_workflow(
    tmp_path, monkeypatch, capsys, config, cli, model
):
    calls = []

    def session(cmd, cwd, **kwargs):
        calls.append(cmd)
        if cmd[0] == "claude":
            return json.dumps({"type": "result", "result": "Reviewed."}), 0
        return "Implemented.", 0

    # Exercise dispatch, role resolution, prompt construction and adapter preparation.
    for adapter in ("codex_agent", "claude_code_agent", "claude_code_text"):
        monkeypatch.setattr(f"orchestra.adapters.{adapter}.run_session", session)
    monkeypatch.setattr("mcloop.runner._run_session", Mock(side_effect=AssertionError("bypassed")))
    preflight = Mock()
    monkeypatch.setattr("mcloop.runner.ensure_subscription_preflight", preflight)
    result = run_task("Create a file", cli, tmp_path, tmp_path / "logs", model=model)
    assert result.success, result.output
    assert [(c[0], c[c.index("--model") + 1]) for c in calls] == [
        ("claude", "fable"),
        ("claude", "sonnet"),
        (cli, model),
    ]
    assert preflight.call_args.kwargs["cli"] == cli
    assert preflight.call_args.kwargs["model"] == model
    stderr = capsys.readouterr().err
    assert "bypassed for this call" not in stderr
    assert f"editor ({'codex_agent' if cli == 'codex' else 'claude_code_agent'}:{model})" in stderr
    assert json.loads((tmp_path / ".orchestra/config.json").read_text()) == config


@pytest.mark.parametrize("pattern", [None, "direct"])
def test_codex_without_orchestra_uses_direct_runner(tmp_path, monkeypatch, pattern):
    monkeypatch.setattr("orchestra.config.global_config_path", lambda: tmp_path / "absent")
    if pattern:
        (tmp_path / ".orchestra").mkdir()
        (tmp_path / ".orchestra/config.json").write_text(
            json.dumps(
                {
                    "workflows": {"code_edit": {"pattern": pattern}},
                }
            )
        )
    session = Mock(return_value=("Implemented.", 0))
    monkeypatch.setattr("mcloop.runner._run_session", session)
    result = run_task("Create a file", "codex", tmp_path, tmp_path / "logs", model="gpt-6-astra")
    assert result.success
    cmd = session.call_args.args[0]
    assert cmd[0] == "codex"
    assert cmd[cmd.index("--model") + 1] == "gpt-6-astra"
    assert "--json" in cmd


def test_adapter_switch_clears_incompatible_settings(config):
    original = OrchestraConfig.from_dict(config)
    selected = _editor_config(original, cli="codex", model=None)
    editor = _resolve_role_binding("code_edit", "editor", selected)
    assert editor.adapter == "codex_agent"
    assert editor.model is None
    assert editor.tools == "default"
    assert editor.parameters == {}
    assert _resolve_role_binding("code_edit", "editor", original).model == "sonnet"


def test_same_adapter_without_model_keeps_workflow_override(config):
    config["roles"]["editor"]["parameters"] = {}
    selected = _editor_config(OrchestraConfig.from_dict(config), cli="claude", model=None)
    assert _resolve_role_binding("code_edit", "editor", selected).model == "sonnet"


def test_custom_tool_list_retains_explicit_bypass(tmp_path, monkeypatch, capsys, config):
    session = Mock(return_value=("Implemented.", 0))
    monkeypatch.setattr("mcloop.runner._run_session", session)
    result = run_task("Create a file", "claude", tmp_path, tmp_path / "logs", allowed_tools="Read")
    assert result.success
    assert "bypassed for this call (custom allowed_tools)" in capsys.readouterr().err


def test_workflow_failure_is_returned_without_direct_retry(tmp_path, monkeypatch, config):
    monkeypatch.setattr(
        "orchestra.adapters.claude_code_text.run_session",
        lambda *args, **kwargs: ("review failed", 1),
    )
    direct = Mock(side_effect=AssertionError("unexpected direct retry"))
    editor = Mock(side_effect=AssertionError("editor ran after failed review"))
    monkeypatch.setattr("mcloop.runner._run_session", direct)
    monkeypatch.setattr("orchestra.adapters.codex_agent.run_session", editor)
    result = run_task("Create a file", "codex", tmp_path, tmp_path / "logs", model="gpt-6-astra")
    assert not result.success
    direct.assert_not_called()
    editor.assert_not_called()
