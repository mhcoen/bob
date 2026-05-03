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


# --------------------------------------------------------------------
# fan_out transitions (Slice A part 1 of the real-council plan)
# --------------------------------------------------------------------


def _write_dummy_template(tmp_path):
    """The validator checks that role prompt files exist on disk.
    Drop a no-op template into ``tmp_path/templates/dummy.md`` so the
    fan_out fixtures can be loaded without per-role real prompt
    files."""
    tdir = tmp_path / "templates"
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "dummy.md").write_text("dummy\n")


def _fan_out_workflow(tmp_path, body_extra=""):
    _write_dummy_template(tmp_path)
    src = tmp_path / "fan.orc"
    src.write_text(
        """spec 0.1
workflow fan
  external_input topic text
  max_total_steps 20
  model m_a
  model m_b
  model m_c
  model m_join
  artifact a_out text
  artifact b_out text
  artifact c_out text
  artifact joined text
  artifact aborted text
  role lens
    prompt template "templates/dummy.md"
  role joiner
    prompt template "templates/dummy.md"
  role aborter
    prompt template "templates/dummy.md"
  state launch
    actor model m_a
    role lens
    reads topic
    writes a_out text
    on complete fan_out [advise_a, advise_b, advise_c] join join_state on error abort_state
    on error => stop
    on timeout => stop
  state advise_a
    actor model m_a
    role lens
    reads topic
    writes a_out text
    on complete => done
    on error => stop
    on timeout => stop
  state advise_b
    actor model m_b
    role lens
    reads topic
    writes b_out text
    on complete => done
    on error => stop
    on timeout => stop
  state advise_c
    actor model m_c
    role lens
    reads topic
    writes c_out text
    on complete => done
    on error => stop
    on timeout => stop
  state join_state
    actor model m_join
    role joiner
    reads a_out, b_out, c_out
    writes joined text
    on complete => done
    on error => stop
    on timeout => stop
  state abort_state
    actor model m_join
    role aborter
    reads topic
    writes aborted text
    on complete => stop
    on error => stop
    on timeout => stop
"""
        + body_extra
    )
    return src


def test_fan_out_transition_parses(tmp_path):
    src = _fan_out_workflow(tmp_path)
    wf = load_workflow(src, with_core())
    launch = wf.state("launch")
    fan = next(t for t in launch.transitions if t.is_fan_out())
    assert fan.outcome == "complete"
    assert fan.fan_out == ("advise_a", "advise_b", "advise_c")
    assert fan.target == "join_state"
    assert fan.error_target == "abort_state"


def test_fan_out_unknown_child_rejected(tmp_path):
    _write_dummy_template(tmp_path)
    src = tmp_path / "bad.orc"
    src.write_text(
        """spec 0.1
workflow x
  external_input topic text
  max_total_steps 10
  model m
  artifact out text
  artifact joined text
  role r
    prompt template "templates/dummy.md"
  state launch
    actor model m
    role r
    reads topic
    writes out text
    on complete fan_out [missing_child] join join_state on error abort_state
    on error => stop
    on timeout => stop
  state join_state
    actor model m
    role r
    reads out
    writes joined text
    on complete => done
    on error => stop
    on timeout => stop
  state abort_state
    actor model m
    role r
    reads topic
    writes joined text
    on complete => stop
    on error => stop
    on timeout => stop
"""
    )
    with pytest.raises(ValidationError) as exc:
        load_workflow(src, with_core())
    assert "fan_out child 'missing_child'" in str(exc.value)


def test_fan_out_unknown_join_target_rejected(tmp_path):
    src = _fan_out_workflow(tmp_path).read_text().replace(
        "join join_state on error abort_state",
        "join missing_join on error abort_state",
    )
    p = tmp_path / "bad.orc"
    p.write_text(src)
    with pytest.raises(ValidationError) as exc:
        load_workflow(p, with_core())
    assert "transition target 'missing_join'" in str(exc.value)


def test_fan_out_unknown_error_target_rejected(tmp_path):
    src = _fan_out_workflow(tmp_path).read_text().replace(
        "join join_state on error abort_state",
        "join join_state on error missing_abort",
    )
    p = tmp_path / "bad.orc"
    p.write_text(src)
    with pytest.raises(ValidationError) as exc:
        load_workflow(p, with_core())
    assert "fan_out error target 'missing_abort'" in str(exc.value)


def test_fan_out_sibling_writes_same_artifact_rejected(tmp_path):
    _write_dummy_template(tmp_path)
    src = tmp_path / "bad.orc"
    src.write_text(
        """spec 0.1
workflow x
  external_input topic text
  max_total_steps 10
  model m
  artifact shared text
  artifact joined text
  role r
    prompt template "templates/dummy.md"
  state launch
    actor model m
    role r
    reads topic
    writes joined text
    on complete fan_out [child_a, child_b] join join_state on error abort_state
    on error => stop
    on timeout => stop
  state child_a
    actor model m
    role r
    reads topic
    writes shared text
    on complete => done
    on error => stop
    on timeout => stop
  state child_b
    actor model m
    role r
    reads topic
    writes shared text
    on complete => done
    on error => stop
    on timeout => stop
  state join_state
    actor model m
    role r
    reads shared
    writes joined text
    on complete => done
    on error => stop
    on timeout => stop
  state abort_state
    actor model m
    role r
    reads topic
    writes joined text
    on complete => stop
    on error => stop
    on timeout => stop
"""
    )
    with pytest.raises(ValidationError) as exc:
        load_workflow(src, with_core())
    msg = str(exc.value)
    assert "sibling writes" in msg
    assert "shared" in msg


def test_fan_out_empty_child_list_rejected(tmp_path):
    _write_dummy_template(tmp_path)
    src = tmp_path / "bad.orc"
    src.write_text(
        """spec 0.1
workflow x
  external_input topic text
  max_total_steps 10
  model m
  artifact joined text
  role r
    prompt template "templates/dummy.md"
  state launch
    actor model m
    role r
    reads topic
    writes joined text
    on complete fan_out [] join join_state on error abort_state
    on error => stop
    on timeout => stop
  state join_state
    actor model m
    role r
    reads topic
    writes joined text
    on complete => done
    on error => stop
    on timeout => stop
  state abort_state
    actor model m
    role r
    reads topic
    writes joined text
    on complete => stop
    on error => stop
    on timeout => stop
"""
    )
    with pytest.raises(ParseError) as exc:
        load_workflow(src, with_core())
    assert "fan_out requires at least one child" in str(exc.value)


def test_fan_out_duplicate_child_rejected(tmp_path):
    _write_dummy_template(tmp_path)
    src = tmp_path / "bad.orc"
    src.write_text(
        """spec 0.1
workflow x
  external_input topic text
  max_total_steps 10
  model m
  artifact a_out text
  artifact joined text
  role r
    prompt template "templates/dummy.md"
  state launch
    actor model m
    role r
    reads topic
    writes joined text
    on complete fan_out [child_a, child_a] join join_state on error abort_state
    on error => stop
    on timeout => stop
  state child_a
    actor model m
    role r
    reads topic
    writes a_out text
    on complete => done
    on error => stop
    on timeout => stop
  state join_state
    actor model m
    role r
    reads a_out
    writes joined text
    on complete => done
    on error => stop
    on timeout => stop
  state abort_state
    actor model m
    role r
    reads topic
    writes joined text
    on complete => stop
    on error => stop
    on timeout => stop
"""
    )
    with pytest.raises(ParseError) as exc:
        load_workflow(src, with_core())
    assert "appears more than once" in str(exc.value)


def test_fan_out_outcome_must_be_complete(tmp_path):
    _write_dummy_template(tmp_path)
    src = tmp_path / "bad.orc"
    src.write_text(
        """spec 0.1
workflow x
  external_input topic text
  max_total_steps 10
  model m
  artifact joined text
  role r
    prompt template "templates/dummy.md"
  state launch
    actor model m
    role r
    reads topic
    writes joined text
    on complete => done
    on error fan_out [child_a] join join_state on error abort_state
    on timeout => stop
  state child_a
    actor model m
    role r
    reads topic
    writes joined text
    on complete => done
    on error => stop
    on timeout => stop
  state join_state
    actor model m
    role r
    reads joined
    writes joined text
    on complete => done
    on error => stop
    on timeout => stop
  state abort_state
    actor model m
    role r
    reads topic
    writes joined text
    on complete => stop
    on error => stop
    on timeout => stop
"""
    )
    with pytest.raises(ParseError) as exc:
        load_workflow(src, with_core())
    assert "fan_out is legal only on 'complete'" in str(exc.value)


# --------------------------------------------------------------------
# Source qualifier rejection (audit finding #4)
# --------------------------------------------------------------------


def test_artifact_source_file_rejected_until_implemented(tmp_path):
    """The store's declare() materializes only 'initial' values; a
    'source file' qualifier parses but is never read at run start, so
    a state reading the artifact would silently see None. The
    validator rejects the qualifier up front so workflows are not
    loaded under the impression that the source data will be
    present."""
    src = tmp_path / "bad.orc"
    src.write_text(
        """spec 0.1
workflow x
  max_total_steps 5
  model m
  artifact a text
    source file "data.json"
  state s
    actor model m
    reads a
    writes a text
    on complete => done
    on error => stop
    on timeout => stop
"""
    )
    with pytest.raises(ValidationError) as exc:
        load_workflow(src, with_core())
    msg = str(exc.value)
    assert "source file" in msg
    assert "not implemented" in msg


def test_artifact_source_path_rejected_until_implemented(tmp_path):
    src = tmp_path / "bad.orc"
    src.write_text(
        """spec 0.1
workflow x
  external_input p text
  max_total_steps 5
  model m
  artifact a text
    source path p
  state s
    actor model m
    reads a
    writes a text
    on complete => done
    on error => stop
    on timeout => stop
"""
    )
    with pytest.raises(ValidationError) as exc:
        load_workflow(src, with_core())
    assert "source path" in str(exc.value)


# --------------------------------------------------------------------
# Required success transition (audit finding #7)
# --------------------------------------------------------------------


def test_model_state_without_success_outcome_rejected(tmp_path):
    """A model state declaring only 'on error' and 'on timeout' would
    crash mid-state on the first successful invocation: the actor
    runs, payloads are written, state_exit is emitted, then the
    transition selector fails with 'no transition matched outcome'.
    The validator must reject this up front."""
    src = tmp_path / "bad.orc"
    src.write_text(
        """spec 0.1
workflow x
  external_input topic text
  max_total_steps 5
  model m
  artifact a text
    initial null
  role r
    prompt template "templates/dummy.md"
  state s
    actor model m
    role r
    reads topic
    writes a text
    on error => stop
    on timeout => stop
"""
    )
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "dummy.md").write_text("dummy\n")
    with pytest.raises(ValidationError) as exc:
        load_workflow(src, with_core())
    assert "missing a success transition" in str(exc.value)


def test_agent_state_without_success_outcome_rejected(tmp_path):
    """Same as the model variant; agents derive 'complete' or a
    verdict the same way models do."""
    from orchestra.adapters.mock_model import MockModelAdapter

    reg = with_core()
    # Register a stand-in agent backing so the workflow loads past the
    # actor-backing check and reaches the success-transition rule.
    reg.register_actor_backing("agent", MockModelAdapter)

    src = tmp_path / "bad.orc"
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "dummy.md").write_text("dummy\n")
    src.write_text(
        """spec 0.1
workflow x
  external_input topic text
  max_total_steps 5
  model m
  agent ag
    model m
    adapter claude_code_agent
    context_policy fresh
  artifact a text
    initial null
  role r
    prompt template "templates/dummy.md"
  state s
    actor agent ag
    role r
    reads topic
    writes a text
    on error => stop
    on timeout => stop
"""
    )
    with pytest.raises(ValidationError) as exc:
        load_workflow(src, reg)
    assert "missing a success transition" in str(exc.value)


def test_model_state_with_verdict_outcomes_loads(tmp_path):
    """A schema-backed model state can route by verdict instead of
    'complete'. Any outcome that is not error/timeout/cancelled
    counts as a success branch, so a verdict-based state is
    accepted."""
    src = tmp_path / "ok.orc"
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "dummy.md").write_text("dummy\n")
    src.write_text(
        """spec 0.1
workflow x
  external_input topic text
  max_total_steps 5
  model m
  artifact a text
    initial null
  role r
    prompt template "templates/dummy.md"
  state s
    actor model m
    role r
    reads topic
    writes a text
    on approve => done
    on reject => stop
    on error => stop
    on timeout => stop
"""
    )
    wf = load_workflow(src, with_core())
    assert wf.name == "x"


def test_transform_state_without_complete_rejected(tmp_path):
    """Transforms emit either 'complete' or 'error'. A workflow that
    only handles 'error' would crash on any successful transform."""
    from typing import Any

    from orchestra.transforms import TransformContext

    def _id(inputs: dict[str, Any], ctx: TransformContext) -> dict[str, Any]:
        return {"out": inputs.get("inp", "")}

    reg = with_core()
    reg.register_transform(
        "passthrough",
        _id,
        input_schema={"inp": str},
        output_schema={"out": str},
    )

    src = tmp_path / "bad.orc"
    src.write_text(
        """spec 0.1
workflow x
  external_input topic text
  max_total_steps 5
  artifact inp text
    initial "x"
  artifact out text
  state s
    actor transform passthrough
    reads inp
    writes out text
    on error => stop
"""
    )
    with pytest.raises(ValidationError) as exc:
        load_workflow(src, reg)
    assert "missing 'on complete'" in str(exc.value)


def test_shell_state_requires_pass_and_fail(tmp_path):
    """Shell backings produce 'pass' or 'fail' outcomes. A workflow
    that handles only error/timeout misses both of those, leaving the
    transition selector with no match on every successful run and on
    every test failure."""
    src = tmp_path / "bad.orc"
    src.write_text(
        """spec 0.1
workflow x
  external_input topic text
  max_total_steps 5
  artifact a text
    initial null
  state s
    actor shell
    command "true"
    reads topic
    writes a text
    on error => stop
    on timeout => stop
"""
    )
    with pytest.raises(ValidationError) as exc:
        load_workflow(src, with_core())
    msg = str(exc.value)
    assert "missing 'on pass'" in msg or "missing 'on fail'" in msg


# --------------------------------------------------------------------
# Start-state read reachability (audit finding #8 minimum)
# --------------------------------------------------------------------


def test_start_state_reads_uninitialized_artifact_rejected(tmp_path):
    """The start state has no predecessors. An artifact it reads must
    have an 'initial' qualifier (or be an external input); otherwise
    the executor substitutes None on the very first prompt."""
    src = tmp_path / "bad.orc"
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "dummy.md").write_text("dummy\n")
    src.write_text(
        """spec 0.1
workflow x
  external_input topic text
  max_total_steps 5
  model m
  artifact a text
  artifact b text
  role r
    prompt template "templates/dummy.md"
  state s1
    actor model m
    role r
    reads topic, a
    writes b text
    on complete => s2
    on error => stop
    on timeout => stop
  state s2
    actor model m
    role r
    reads topic
    writes a text
    on complete => done
    on error => stop
    on timeout => stop
"""
    )
    with pytest.raises(ValidationError) as exc:
        load_workflow(src, with_core())
    msg = str(exc.value)
    assert "start state" in msg
    assert "'a'" in msg


def test_start_state_reads_external_input_loads(tmp_path):
    """External inputs are admissible reads for the start state."""
    src = tmp_path / "ok.orc"
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "dummy.md").write_text("dummy\n")
    src.write_text(
        """spec 0.1
workflow x
  external_input topic text
  max_total_steps 5
  model m
  artifact b text
  role r
    prompt template "templates/dummy.md"
  state s1
    actor model m
    role r
    reads topic
    writes b text
    on complete => done
    on error => stop
    on timeout => stop
"""
    )
    wf = load_workflow(src, with_core())
    assert wf.name == "x"


def test_start_state_reads_initial_artifact_loads(tmp_path):
    """An artifact declared with 'initial' is satisfied at start."""
    src = tmp_path / "ok.orc"
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "dummy.md").write_text("dummy\n")
    src.write_text(
        """spec 0.1
workflow x
  external_input topic text
  max_total_steps 5
  model m
  artifact a text
    initial null
  artifact b text
  role r
    prompt template "templates/dummy.md"
  state s1
    actor model m
    role r
    reads topic, a
    writes b text
    on complete => done
    on error => stop
    on timeout => stop
"""
    )
    wf = load_workflow(src, with_core())
    assert wf.name == "x"
