"""Loader-level smoke tests for the three ask-flavored workflows.

Confirms each ``ask_*.orc`` file under ``orchestra/workflows/`` parses
and validates against the pre-load registry. No execution: a real
run requires live model adapters and is exercised through the CLI
end-to-end. These tests catch grammar and reference errors at the
file level.
"""

from __future__ import annotations

import pytest

from orchestra.api import _pre_load_registry
from orchestra.loader import load_workflow
from orchestra.loader.lookup import resolve_workflow_path


@pytest.mark.parametrize(
    "name,expected_states,expected_roles",
    [
        ("ask_single", ["answer"], ["responder"]),
        (
            "ask_draft_then_adjudicate",
            ["draft", "adjudicate", "answer"],
            ["adjudicator", "drafter", "responder"],
        ),
        (
            "ask_propose_critique_synthesize",
            ["propose", "critique", "synthesize", "answer"],
            ["critic", "proposer", "responder", "synthesizer"],
        ),
    ],
)
def test_ask_workflow_loads(
    name: str,
    expected_states: list[str],
    expected_roles: list[str],
) -> None:
    path = resolve_workflow_path(name, project_dir=None)
    workflow = load_workflow(path, _pre_load_registry())
    assert workflow.name == name
    assert [s.name for s in workflow.states] == expected_states
    assert sorted(s.role for s in workflow.states if s.role is not None) == (
        expected_roles
    )


@pytest.mark.parametrize(
    "name",
    [
        "ask_single",
        "ask_draft_then_adjudicate",
        "ask_propose_critique_synthesize",
    ],
)
def test_ask_workflow_takes_query_and_history_inputs(name: str) -> None:
    """Each ask workflow declares two text inputs: ``query`` for the
    user's question, and ``history`` for the prior conversation
    transcript the REPL passes through. ``history`` is the empty
    string when the verb is invoked outside the REPL."""
    path = resolve_workflow_path(name, project_dir=None)
    workflow = load_workflow(path, _pre_load_registry())
    names = [e.name for e in workflow.external_inputs]
    assert sorted(names) == ["history", "query"]
    by_name = {e.name: e for e in workflow.external_inputs}
    assert by_name["query"].type == "text"
    assert by_name["history"].type == "text"


@pytest.mark.parametrize(
    "name",
    [
        "ask_single",
        "ask_draft_then_adjudicate",
        "ask_propose_critique_synthesize",
    ],
)
def test_ask_workflow_states_are_all_model_kind(name: str) -> None:
    """The ask variants are conversational. No agent state should write
    to the workspace; every state's actor kind must be 'model'."""
    path = resolve_workflow_path(name, project_dir=None)
    workflow = load_workflow(path, _pre_load_registry())
    for state in workflow.states:
        assert state.actor.kind == "model", (
            f"{name}: state {state.name} has actor kind "
            f"{state.actor.kind}, expected 'model'"
        )


def test_ask_workflow_terminates_in_done() -> None:
    """The final ``answer`` state must transition to ``done`` so
    run_verb can read result.terminal == 'done' and return the text."""
    for name in (
        "ask_single",
        "ask_draft_then_adjudicate",
        "ask_propose_critique_synthesize",
    ):
        path = resolve_workflow_path(name, project_dir=None)
        workflow = load_workflow(path, _pre_load_registry())
        answer_state = next(s for s in workflow.states if s.name == "answer")
        targets = {t.target for t in answer_state.transitions}
        assert "done" in targets, (
            f"{name}: 'answer' state must transition to 'done'"
        )
