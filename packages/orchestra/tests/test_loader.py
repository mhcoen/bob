"""Unit tests for the loader and validator."""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestra.errors import ParseError, ValidationError
from orchestra.loader import load_workflow
from orchestra.registry.registry import with_core


FIXTURE = Path(__file__).parent / "fixtures" / "slice1" / "echo.orc"


def test_loads_echo_workflow():
    reg = with_core()
    wf = load_workflow(FIXTURE, reg)
    assert wf.name == "echo"
    assert wf.spec_version == "0.1"
    assert wf.max_total_steps == 10
    assert [s.name for s in wf.states] == ["respond", "confirm"]
    assert [a.name for a in wf.artifacts] == ["response"]
    respond = wf.state("respond")
    assert respond.actor.kind == "model"
    assert respond.actor.ref == "mock-llm"
    assert respond.role == "responder"
    assert respond.reads == ("topic",)
    assert tuple(w.name for w in respond.writes) == ("response",)
    confirm = wf.state("confirm")
    assert confirm.actor.kind == "human"
    assert confirm.options == ("accept", "reject")


def test_missing_max_total_steps_fails(tmp_path):
    src = tmp_path / "bad.orc"
    src.write_text(
        """spec 0.1
workflow x
  external_input topic text
  artifact a text
  state s
    actor human
    options ok
    on ok => done
    on timeout => stop
    on cancelled => stop
"""
    )
    with pytest.raises(ValidationError):
        load_workflow(src, with_core())


def test_unknown_actor_fails(tmp_path):
    src = tmp_path / "bad.orc"
    src.write_text(
        """spec 0.1
workflow x
  max_total_steps 5
  state s
    actor mystery
    on complete => done
    on error => stop
    on timeout => stop
"""
    )
    with pytest.raises((ParseError, ValidationError)):
        load_workflow(src, with_core())


def test_writes_undeclared_artifact_fails(tmp_path):
    src = tmp_path / "bad.orc"
    src.write_text(
        """spec 0.1
workflow x
  max_total_steps 5
  model m
  state s
    actor model m
    writes ghost text
    on complete => done
    on error => stop
    on timeout => stop
"""
    )
    with pytest.raises(ValidationError):
        load_workflow(src, with_core())


def test_reads_orphan_artifact_fails(tmp_path):
    src = tmp_path / "bad.orc"
    src.write_text(
        """spec 0.1
workflow x
  max_total_steps 5
  model m
  artifact orphan text
  state s
    actor model m
    reads orphan
    writes orphan text
    on complete => done
    on error => stop
    on timeout => stop
"""
    )
    # writes can satisfy reads (state writes orphan), so this should
    # actually load fine. Replace with an artifact that no state writes.
    src.write_text(
        """spec 0.1
workflow x
  max_total_steps 5
  model m
  artifact orphan text
  artifact other text
  state s
    actor model m
    reads orphan
    writes other text
    on complete => done
    on error => stop
    on timeout => stop
"""
    )
    with pytest.raises(ValidationError):
        load_workflow(src, with_core())


def test_indentation_mixing_rejected(tmp_path):
    src = tmp_path / "bad.orc"
    # Tabs and spaces in the same indent.
    src.write_text("spec 0.1\nworkflow x\n \tmax_total_steps 5\n")
    with pytest.raises(ParseError):
        load_workflow(src, with_core())
