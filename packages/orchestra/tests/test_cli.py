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
from typing import Any

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
            "responder": {
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

    def _stub_run_verb(verb_name, query, config, **kwargs):
        captured["verb"] = verb_name
        captured["query"] = query
        captured["config_verbs"] = sorted(config.verbs)
        captured["progress_callback_passed"] = (
            kwargs.get("progress_callback") is not None
        )
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

    def _failing_run_verb(verb_name, query, config, **kwargs):
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


def test_no_args_invokes_repl(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``orchestra`` with no arguments drops into the interactive
    REPL. The CLI delegates to ``orchestra.repl.run_repl`` after
    loading the merged config; the test stubs that entry point so
    no terminal I/O fires and asserts it was called with the
    expected verb-configured config."""
    _write_global_config(isolated_home, _ask_config_body())
    captured: dict[str, Any] = {}

    def _stub_run_repl(config: Any, **kwargs: Any) -> int:
        captured["config_verbs"] = sorted(config.verbs)
        return 0

    import orchestra.repl as _repl

    monkeypatch.setattr(_repl, "run_repl", _stub_run_repl)
    rc = cli.main([])
    assert rc == 0
    assert captured["config_verbs"] == ["ask", "council", "pair"]


def test_no_args_with_no_config_exits_with_repl_setup_hint(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """With no global config, the REPL still launches, finds no
    verbs, and exits 1 with a setup hint. cli.main delegates so the
    real run_repl decides; this test stubs the real run_repl to
    emit the same hint shape."""
    import orchestra.repl as _repl

    def _stub_run_repl(config: Any, **kwargs: Any) -> int:
        # Reuse the real default-verb logic so the test stays close
        # to production behavior without spinning up a PromptSession.
        if not config.verbs:
            print(
                "no verbs configured; cannot start REPL.",
                file=__import__("sys").stderr,
            )
            return 1
        return 0

    monkeypatch.setattr(_repl, "run_repl", _stub_run_repl)
    rc = cli.main([])
    err = capsys.readouterr().err
    assert rc == 1
    assert "no verbs configured" in err


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
    assert "Required roles: responder" in out
    assert "responder:" in out
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
    body["roles"].pop("responder")
    _write_global_config(isolated_home, body)
    rc = cli.main(["help", "ask"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "responder: NOT CONFIGURED" in out


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


# --------------------------------------------------------------------
# Direct-execution surface restriction (orchestra run / resume)
# --------------------------------------------------------------------


_AGENT_FIXTURE = """spec 0.1
workflow agent_only
  external_input topic text
  max_total_steps 5
  agent ag1
    model some-model
    adapter claude_code_agent
    context_policy fresh
  artifact reply text
  role r
    prompt template "templates/dummy.md"
  state work
    actor agent ag1
    role r
    reads topic
    writes reply text
    on complete => done
    on error => stop
    on timeout => stop
"""


_TRANSFORM_FIXTURE = """spec 0.1
workflow transform_only
  external_input topic text
  max_total_steps 5
  artifact reply text
  state work
    actor transform anonymize_outputs
    reads topic
    writes reply text
    on complete => done
    on error => stop
    on timeout => stop
"""


_TEXT_ONLY_FIXTURE = """spec 0.1
workflow text_only
  external_input topic text
  max_total_steps 5
  model m1
  artifact reply text
  role r
    prompt template "templates/dummy.md"
  state work
    actor model m1
    role r
    reads topic
    writes reply text
    on complete => done
    on error => stop
    on timeout => stop
"""


def _write_workflow(tmp_path: Path, body: str) -> Path:
    tdir = tmp_path / "templates"
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "dummy.md").write_text("dummy\n")
    src = tmp_path / "wf.orc"
    src.write_text(body)
    return src


def test_reject_unsupported_direct_workflow_flags_agent_states(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    src = _write_workflow(tmp_path, _AGENT_FIXTURE)
    rc = cli._reject_unsupported_direct_workflow(src)
    assert rc == 2
    err = capsys.readouterr().err
    assert "does not support agent or transform" in err
    assert "work" in err
    assert "agent" in err


def test_reject_unsupported_direct_workflow_flags_transform_states(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    src = _write_workflow(tmp_path, _TRANSFORM_FIXTURE)
    rc = cli._reject_unsupported_direct_workflow(src)
    assert rc == 2
    err = capsys.readouterr().err
    assert "does not support agent or transform" in err
    assert "work" in err
    assert "transform" in err


def test_reject_unsupported_direct_workflow_passes_text_only(
    tmp_path: Path,
) -> None:
    src = _write_workflow(tmp_path, _TEXT_ONLY_FIXTURE)
    assert cli._reject_unsupported_direct_workflow(src) is None


def test_cmd_run_rejects_agent_workflow_before_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """End-to-end through cmd_run: an agent workflow must be rejected
    with the targeted error before load_workflow runs and produces a
    generic 'unknown actor backing' message."""
    src = _write_workflow(tmp_path, _AGENT_FIXTURE)
    called: dict[str, bool] = {"load": False}

    def _trip(*args: object, **kwargs: object) -> object:
        called["load"] = True
        raise AssertionError("load_workflow must not be reached")

    monkeypatch.setattr(cli, "load_workflow", _trip)

    args = type(
        "Args",
        (),
        {
            "workflow": str(src),
            "input": ["topic=hi"],
            "data_root": str(tmp_path / "runs"),
        },
    )()
    rc = cli.cmd_run(args)
    assert rc == 2
    assert called["load"] is False
    err = capsys.readouterr().err
    assert "agent or transform" in err
