"""Tests for the orchestra CLI verb dispatcher and help command.

Covers the verb-style surface only. ``orchestra run`` and
``orchestra resume`` are exercised through the existing end-to-end
tests against real workflows. The verb tests stub
``orchestra.cli.run_verb`` so the dispatch logic runs offline without
touching live model adapters.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestra import cli


def _write_global_config(home: Path, body: dict) -> Path:
    cfg_dir = home / ".orchestra"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    path = cfg_dir / "config.json"
    path.write_text(json.dumps(body))
    return path


def _ask_config_body() -> dict:
    return {
        "verbs": {
            "ask": {"workflow": "ask_single"},
            "council": {"workflow": "ask_propose_critique_synthesize"},
            "pair": {"workflow": "ask_draft_then_adjudicate"},
        },
        "roles": {
            "editor": {
                "adapter": "claude_code_text",
                "model": "opus",
                "parameters": {},
            },
            "drafter": {
                "adapter": "claude_code_text",
                "model": "kimi-k2.6",
                "parameters": {},
            },
            "adjudicator": {
                "adapter": "claude_code_text",
                "model": "opus",
                "parameters": {},
            },
            "proposer": {
                "adapter": "claude_code_text",
                "model": "kimi-k2.6",
                "parameters": {},
            },
            "critic": {
                "adapter": "claude_code_text",
                "model": "sonnet",
                "parameters": {},
            },
            "synthesizer": {
                "adapter": "claude_code_text",
                "model": "opus",
                "parameters": {},
            },
        },
        "workflows": {
            "ask_single": {"pattern": "ask_single"},
            "ask_propose_critique_synthesize": {
                "pattern": "ask_propose_critique_synthesize",
            },
            "ask_draft_then_adjudicate": {
                "pattern": "ask_draft_then_adjudicate",
            },
        },
    }


@pytest.fixture
def isolated_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Redirect ``~`` so the CLI's load_global_config reads from
    ``tmp_path/.orchestra/config.json`` instead of the developer's
    real home directory."""
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


# --------------------------------------------------------------------
# Verb dispatch
# --------------------------------------------------------------------


def test_verb_dispatch_runs_configured_verb(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_global_config(isolated_home, _ask_config_body())
    captured: dict[str, object] = {}

    def _stub_run_verb(verb_name, query, config):
        captured["verb"] = verb_name
        captured["query"] = query
        captured["config_verbs"] = sorted(config.verbs)
        return "Paris.\n"

    monkeypatch.setattr(cli, "run_verb", _stub_run_verb)
    rc = cli.main(["ask", "what", "is", "the", "capital", "of", "france"])
    assert rc == 0
    out = capsys.readouterr()
    assert out.out == "Paris.\n\n"
    assert captured["verb"] == "ask"
    assert captured["query"] == "what is the capital of france"
    assert "ask" in captured["config_verbs"]


def test_verb_dispatch_unknown_verb_exits_2(
    isolated_home: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_global_config(isolated_home, _ask_config_body())
    rc = cli.main(["bogus", "hello"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "unknown command: bogus" in err
    assert "orchestra help" in err


def test_verb_dispatch_missing_config_exits_1(
    isolated_home: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = cli.main(["ask", "anything"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "no config" in err
    assert "config.json" in err


def test_verb_dispatch_no_query_words_exits_2(
    isolated_home: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_global_config(isolated_home, _ask_config_body())
    rc = cli.main(["ask"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "no query supplied" in err


def test_verb_dispatch_propagates_run_verb_error(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from orchestra.api import WorkflowApiError

    _write_global_config(isolated_home, _ask_config_body())

    def _failing_run_verb(verb_name, query, config):
        raise WorkflowApiError("workflow blew up")

    monkeypatch.setattr(cli, "run_verb", _failing_run_verb)
    rc = cli.main(["ask", "anything"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "workflow blew up" in err


# --------------------------------------------------------------------
# Help command
# --------------------------------------------------------------------


def test_help_lists_configured_verbs(
    isolated_home: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_global_config(isolated_home, _ask_config_body())
    rc = cli.main(["help"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Configured verbs:" in out
    assert "ask" in out and "ask_single" in out
    assert "council" in out
    assert "pair" in out
    assert "Direct workflow execution:" in out
    assert "run <workflow.orc>" in out
    assert "resume <run_id>" in out


def test_no_args_prints_help_and_exits_0(
    isolated_home: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``orchestra`` with no arguments shows the help overview on
    stdout and exits 0. Treating this as an argparse "required: cmd"
    error is the wrong answer when the user is asking what the tool
    does."""
    _write_global_config(isolated_home, _ask_config_body())
    rc = cli.main([])
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.err == ""
    out = captured.out
    assert "Configured verbs:" in out
    assert "ask" in out and "ask_single" in out
    assert "Direct workflow execution:" in out
    assert "run <workflow.orc>" in out
    assert "resume <run_id>" in out


def test_no_args_with_no_global_config_still_prints_help(
    isolated_home: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Even without a global config, bare ``orchestra`` prints the
    help overview (with the "(none)" hint) and exits 0."""
    rc = cli.main([])
    captured = capsys.readouterr()
    assert rc == 0
    out = captured.out
    assert "Configured verbs:" in out
    assert "(none" in out
    assert "Direct workflow execution:" in out


def test_help_when_no_config_shows_setup_hint(
    isolated_home: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = cli.main(["help"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Configured verbs:" in out
    assert "(none" in out


def test_help_for_specific_verb_shows_workflow_and_bindings(
    isolated_home: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_global_config(isolated_home, _ask_config_body())
    rc = cli.main(["help", "ask"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "ask: runs workflow `ask_single`" in out
    assert "Required roles: editor" in out
    assert "editor:" in out
    assert "claude_code_text" in out
    assert "model=opus" in out


def test_help_for_unknown_verb_exits_2(
    isolated_home: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_global_config(isolated_home, _ask_config_body())
    rc = cli.main(["help", "bogus"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "unknown verb" in err


def test_help_for_verb_flags_unbound_role(
    isolated_home: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    body = _ask_config_body()
    body["roles"].pop("editor")
    _write_global_config(isolated_home, body)
    rc = cli.main(["help", "ask"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "editor: NOT CONFIGURED" in out


# --------------------------------------------------------------------
# Subparser still works
# --------------------------------------------------------------------


def test_run_subcommand_still_dispatches(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A reserved word like 'run' must reach the existing argparse
    subparser, not the verb dispatcher."""
    captured: dict[str, object] = {}

    def _stub_cmd_run(args):
        captured["workflow"] = args.workflow
        return 0

    monkeypatch.setattr(cli, "cmd_run", _stub_cmd_run)
    rc = cli.main(["run", "single"])
    assert rc == 0
    assert captured["workflow"] == "single"
