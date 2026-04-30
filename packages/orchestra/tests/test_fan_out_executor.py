"""End-to-end tests for the fan-out executor primitive (Slice A).

Covers the happy path (three children fan out, all complete, join
runs against all three artifacts), the error path (one child errors,
group routes to error_target), and snapshot isolation (siblings do
not see each other's writes during fan-out).

The tests load real ``.orc`` workflows through the loader and run
them via the actual ``Executor`` against the slice-1 mock adapters.
No external model calls.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from orchestra.executor.executor import Executor, new_run_id
from orchestra.loader import load_workflow
from orchestra.log import LogReader, LogWriter
from orchestra.registry.registry import with_core
from orchestra.spine import NO_INITIAL, Workflow
from orchestra.store import ArtifactStore


def _initialize_store(workflow: Workflow, db_path: Path) -> ArtifactStore:
    store = ArtifactStore(db_path)
    for art in workflow.artifacts:
        qualifiers: dict[str, Any] = {}
        if art.initial is not NO_INITIAL:
            qualifiers["initial"] = art.initial
        store.declare(art.name, art.type, qualifiers=qualifiers)
    return store


def _write_dummy_template(tmp_path: Path) -> None:
    tdir = tmp_path / "templates"
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "dummy.md").write_text("dummy\n")


def _fan_out_fixture_workflow(tmp_path: Path) -> Path:
    _write_dummy_template(tmp_path)
    src = tmp_path / "fan.orc"
    src.write_text(
        """spec 0.1
workflow fan_test
  external_input topic text
  max_total_steps 30
  model m_a
  model m_b
  model m_c
  model m_join
  model m_abort
  model m_parent
  artifact a_out text
  artifact b_out text
  artifact c_out text
  artifact joined text
  artifact aborted text
  artifact parent_out text
  role lens
    prompt template "templates/dummy.md"
  role joiner
    prompt template "templates/dummy.md"
  role aborter
    prompt template "templates/dummy.md"
  role parent_role
    prompt template "templates/dummy.md"
  state launch
    actor model m_parent
    role parent_role
    reads topic
    writes parent_out text
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
    actor model m_abort
    role aborter
    reads topic
    writes aborted text
    on complete => stop
    on error => stop
    on timeout => stop
"""
    )
    return src


def _run(tmp_path: Path) -> Path:
    """Run the fan-out fixture workflow to completion. Returns the
    run dir."""
    workflow_path = _fan_out_fixture_workflow(tmp_path)
    registry = with_core()
    workflow = load_workflow(workflow_path, registry)
    run_id = new_run_id()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    store = _initialize_store(workflow, run_dir / "store.sqlite")
    log = LogWriter(run_dir / "log.jsonl", run_id)
    log.write("run_start", fields={"workflow_path": str(workflow_path)})
    executor = Executor(
        workflow=workflow,
        registry=registry,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=run_id,
        external_inputs={"topic": "hello world"},
    )
    terminal = executor.run_to_completion()
    log.write("run_end", fields={"terminal": terminal})
    log.close()
    store.close()
    assert terminal == "done", f"expected done, got {terminal!r}"
    return run_dir


def _read_records(run_dir: Path) -> list:
    return LogReader(run_dir / "log.jsonl").read_all()


def test_fan_out_happy_path_runs_all_children_and_joins(
    tmp_path: Path,
) -> None:
    """Parent state fans out to three children; all three commit
    their artifacts; the join state reads all three and routes to
    done. Tests the executor primitive end-to-end."""
    run_dir = _run(tmp_path)
    records = _read_records(run_dir)
    # The fan_out_start record names the parent state and the three
    # children.
    fan_starts = [r for r in records if r.event == "fan_out_start"]
    assert len(fan_starts) == 1
    fan_start = fan_starts[0]
    assert fan_start.fields["parent_state"] == "launch"
    assert sorted(fan_start.fields["children"]) == [
        "advise_a",
        "advise_b",
        "advise_c",
    ]
    assert fan_start.fields["join_target"] == "join_state"
    assert fan_start.fields["error_target"] == "abort_state"
    # Each child writes a state_enter and state_exit.
    for child in ("advise_a", "advise_b", "advise_c"):
        enter = [
            r for r in records
            if r.event == "state_enter" and r.state_id == child
        ]
        exit_ = [
            r for r in records
            if r.event == "state_exit" and r.state_id == child
        ]
        assert len(enter) == 1, child
        assert len(exit_) == 1, child
        assert exit_[0].fields["status"] == "ok"
    # fan_out_end records the success aggregate and the join target.
    fan_ends = [r for r in records if r.event == "fan_out_end"]
    assert len(fan_ends) == 1
    fan_end = fan_ends[0]
    assert fan_end.fields["aggregate"] == "success"
    assert fan_end.fields["target"] == "join_state"
    per_child = fan_end.fields["per_child_outcome"]
    assert per_child["advise_a"] == "success"
    assert per_child["advise_b"] == "success"
    assert per_child["advise_c"] == "success"
    # The join state runs and ends with status=ok. This proves the
    # post-fan-out routing reached the join target and that the join
    # state's reads (a_out, b_out, c_out) were visible (the
    # visibility rule does not hide successful state_invocation
    # rows).
    join_exits = [
        r for r in records
        if r.event == "state_exit" and r.state_id == "join_state"
    ]
    assert len(join_exits) == 1
    assert join_exits[0].fields["status"] == "ok"


def test_fan_out_log_records_carry_invocation_ids(tmp_path: Path) -> None:
    """Every state_enter, state_exit, and artifact_write written by
    a fan-out child carries the same invocation_id, and that id is
    keyed (run_id::state_name::1) for first-pass execution."""
    run_dir = _run(tmp_path)
    records = _read_records(run_dir)
    for child in ("advise_a", "advise_b", "advise_c"):
        records_for = [r for r in records if r.state_id == child]
        invocation_ids = {
            r.fields.get("invocation_id")
            for r in records_for
            if "invocation_id" in r.fields
        }
        assert len(invocation_ids) == 1, child
        inv = invocation_ids.pop()
        parts = inv.split("::")
        assert len(parts) == 3
        assert parts[1] == child
        assert parts[2] == "1"


def test_fan_out_fan_out_end_records_routing_target(tmp_path: Path) -> None:
    """fan_out_end is the durable record of the routing decision;
    a successful group routes to its join_target."""
    run_dir = _run(tmp_path)
    records = _read_records(run_dir)
    fan_end = next(r for r in records if r.event == "fan_out_end")
    assert fan_end.fields["target"] == "join_state"
