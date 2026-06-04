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
from typing import Any, cast

from orchestra.executor.executor import Executor, new_run_id
from orchestra.loader import load_workflow
from orchestra.loader.parser import parse_workflow
from orchestra.log import LogReader, LogWriter
from orchestra.registry.registry import with_core
from orchestra.spine import NO_INITIAL, Workflow
from orchestra.store import ArtifactStore


def _parse_only(src: Path) -> Workflow:
    """Parse a workflow file without running validate().

    Used by tests that exercise executor robustness on workflow
    shapes the validator now rejects (specifically: a fan-out child
    declaring ``reads`` on a sibling-written artifact, which the
    forward must-reach analysis flags because no writer dominates
    the read site). The executor's snapshot-isolation contract still
    handles such reads correctly (returns the snapshot value, which
    is None when the artifact has no initial), and the tests below
    assert exactly that. Parsing without validating is the smallest
    way to keep those executor-level guarantees pinned without
    adding a spurious ``initial null`` to the .orc text just to
    placate the loader.
    """
    return parse_workflow(src.read_text(encoding="utf-8"), src.resolve())


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
        enter = [r for r in records if r.event == "state_enter" and r.state_id == child]
        exit_ = [r for r in records if r.event == "state_exit" and r.state_id == child]
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
    join_exits = [r for r in records if r.event == "state_exit" and r.state_id == "join_state"]
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
            r.fields.get("invocation_id") for r in records_for if "invocation_id" in r.fields
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


# --------------------------------------------------------------------
# Error path, replay, sibling visibility (Slice A part 6)
# --------------------------------------------------------------------


def _failing_response_workflow(tmp_path: Path) -> Path:
    """A fan-out workflow where one child's mock model returns an
    'error' verdict, forcing the group to route to error_target."""
    _write_dummy_template(tmp_path)
    src = tmp_path / "fan.orc"
    src.write_text(
        """spec 0.1
workflow fan_err
  external_input topic text
  max_total_steps 30
  model m_a
  model m_b
  model m_join
  model m_abort
  model m_parent
  artifact a_out text
  artifact b_out text
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
    on complete fan_out [advise_a, advise_b] join join_state on error abort_state
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
  state join_state
    actor model m_join
    role joiner
    reads a_out, b_out
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


def test_fan_out_error_path_routes_to_error_target(tmp_path: Path) -> None:
    """When a fan-out child errors, the group routes to the
    error_target (abort_state) and fan_out_end records aggregate=error."""
    workflow_path = _failing_response_workflow(tmp_path)
    registry = with_core()
    workflow = load_workflow(workflow_path, registry)
    # Inject a model adapter that errors on advise_b's model.
    from orchestra.adapters.mock_model import MockModelAdapter

    class _ErrorOnB:
        """Adapter that returns a failing payload when invoked on
        advise_b's model id (m_b)."""

        backing = "model"

        def prepare(self, request: Any) -> Any:
            return MockModelAdapter().prepare(request)

        def invoke(self, prepared: Any) -> dict[str, Any]:
            model_id = prepared.summary.get("model")
            if model_id == "m_b":
                raise RuntimeError("synthetic adapter failure for advise_b")
            return MockModelAdapter().invoke(prepared)

        def cancel(self, prepared: Any) -> None:
            return None

        def describe(self) -> dict[str, Any]:
            return {"backing": "model"}

    # Replace the model factory so every model-backed state uses our
    # error-injecting adapter.
    registry.actor_backings["model"] = lambda: _ErrorOnB()
    registry._adapter_cache.pop("model", None)

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
        external_inputs={"topic": "hello"},
    )
    terminal = executor.run_to_completion()
    log.write("run_end", fields={"terminal": terminal})
    log.close()
    store.close()
    records = LogReader(run_dir / "log.jsonl").read_all()
    fan_end = next(r for r in records if r.event == "fan_out_end")
    assert fan_end.fields["aggregate"] == "error"
    assert fan_end.fields["target"] == "abort_state"
    # advise_a (success) and advise_b (error) both have outcomes
    # attributed in the per-child map.
    per_child = fan_end.fields["per_child_outcome"]
    # advise_b errored; advise_a may or may not have completed
    # depending on thread scheduling. Either way the group routes
    # to the error_target.
    assert per_child.get("advise_b") == "error"


def test_fan_out_replay_skips_completed_group(tmp_path: Path) -> None:
    """A successful fan-out leaves a fan_out_end record that replay
    sees as a durable routing decision. ReplayState.last_fan_out_target
    captures the next state."""
    run_dir = _run(tmp_path)
    from orchestra.resume import replay_log

    rep = replay_log(str(run_dir / "log.jsonl"))
    assert rep.last_fan_out_target == "join_state"
    # The visibility-status rebuild includes every successful child.
    inv_a = "::advise_a::"
    inv_b = "::advise_b::"
    inv_c = "::advise_c::"
    assert any(inv_a in k for k in rep.visibility_statuses)
    assert any(inv_b in k for k in rep.visibility_statuses)
    assert any(inv_c in k for k in rep.visibility_statuses)
    successes = {k: v for k, v in rep.visibility_statuses.items() if v == "success"}
    # The three advisors plus parent plus join_state all completed
    # successfully.
    assert sum(1 for k in successes if "::advise_" in k) == 3


def test_fan_out_replay_open_group_on_partial(tmp_path: Path) -> None:
    """A log truncated between fan_out_start and fan_out_end leaves
    ReplayState.open_fan_out set so the executor knows to re-run the
    group on resume."""
    run_dir = _run(tmp_path)
    log_path = run_dir / "log.jsonl"
    # Truncate the log to the fan_out_start record (drop everything
    # after it).
    records = LogReader(log_path).read_all()
    cutoff = next(i for i, r in enumerate(records) if r.event == "fan_out_start")
    truncated = records[: cutoff + 1]
    log_path.write_text("\n".join(r.to_json() for r in truncated) + "\n", encoding="utf-8")
    from orchestra.resume import replay_log

    rep = replay_log(str(log_path))
    assert rep.open_fan_out is not None
    assert rep.open_fan_out["parent_state"] == "launch"
    assert rep.last_fan_out_target is None


def test_visibility_not_success_until_state_exit_durable(tmp_path: Path) -> None:
    """The executor writes ``state_exit`` BEFORE updating the
    VisibilityIndex. While ``state_exit`` is being persisted, the
    artifact written by that state_invocation is still hidden by the
    visibility rule. After the visibility update lands, it becomes
    visible. Tests the Blocker 3 reorder."""
    import threading

    from orchestra.log import LogWriter as _LogWriter
    from orchestra.visibility import VisibilityIndex, make_invocation_id

    workflow_path = _fan_out_fixture_workflow(tmp_path)
    registry = with_core()
    workflow = load_workflow(workflow_path, registry)

    # A LogWriter wrapper that pauses on the FIRST state_exit write,
    # so the test can observe the visibility-vs-durability order
    # from another thread.
    paused = threading.Event()
    released = threading.Event()
    saw_state_exit = threading.Event()

    real_writer = _LogWriter(tmp_path / "log.jsonl", "test-run")

    class _PausingWriter:
        def __init__(self, inner):
            self._inner = inner

        @property
        def lock(self):
            return self._inner.lock

        def critical_section(self):
            return self._inner.critical_section()

        @property
        def next_seq(self):
            return self._inner.next_seq

        def close(self):
            self._inner.close()

        def write(self, event, *, state_id=None, attempt=None, fields=None):
            rec = self._inner.write(event, state_id=state_id, attempt=attempt, fields=fields)
            if event == "state_exit" and not saw_state_exit.is_set():
                saw_state_exit.set()
                paused.set()
                released.wait(timeout=5)
            return rec

    log = cast(LogWriter, _PausingWriter(real_writer))
    log.write("run_start", fields={})

    store = _initialize_store(workflow, tmp_path / "store.sqlite")
    persist = tmp_path / "visibility.json"
    idx = VisibilityIndex(persist_path=persist)
    executor = Executor(
        workflow=workflow,
        registry=registry,
        store=store,
        log=log,
        run_dir=tmp_path,
        run_id="test-run",
        external_inputs={"topic": "hello"},
        visibility_index=idx,
    )

    invocation_id = make_invocation_id("test-run", "launch", 1)
    observed_visibility: dict[str, Any] = {}

    def _runner() -> None:
        executor.run_to_completion()

    def _observer() -> None:
        paused.wait(timeout=5)
        # state_exit has been written for some state. Inspect the
        # visibility index status for the launch state's
        # invocation_id BEFORE the executor's mark_success call
        # runs (the runner thread is blocked inside the wrapper's
        # write).
        observed_visibility["status_during_pause"] = idx.status(invocation_id)
        released.set()

    runner_thread = threading.Thread(target=_runner)
    observer_thread = threading.Thread(target=_observer)
    runner_thread.start()
    observer_thread.start()
    runner_thread.join()
    observer_thread.join()

    # The first state_exit that fires is for the parent state
    # ``launch``. At the moment that record was just written but
    # the visibility-index update hasn't happened yet, the index
    # should still report "pending".
    assert observed_visibility.get("status_during_pause") == "pending"
    # After the run completes, the index reports success.
    assert idx.status(invocation_id) == "success"


def test_fan_out_sibling_reads_use_snapshot_not_live_store(
    tmp_path: Path,
) -> None:
    """A fan-out child reads from the captured snapshot, not from
    the live store. A sibling artifact written mid-fan-out is NOT
    visible to a child that runs later in the same group, even if
    the sibling has a durable state_exit.

    Tests Blocker 1's snapshot threading.
    """
    import threading
    import time

    from orchestra.adapters.mock_model import MockModelAdapter

    _write_dummy_template(tmp_path)
    src = tmp_path / "fan.orc"
    src.write_text(
        """spec 0.1
workflow sib
  external_input topic text
  max_total_steps 30
  model m_parent
  model m_fast
  model m_slow
  model m_join
  model m_abort
  artifact frame_out text
  artifact fast_out text
  artifact slow_out text
  artifact joined text
  artifact aborted text
  role parent_role
    prompt template "templates/dummy.md"
  role fast_role
    prompt template "templates/dummy.md"
  role slow_role
    prompt template "templates/dummy.md"
  role joiner
    prompt template "templates/dummy.md"
  role aborter
    prompt template "templates/dummy.md"
  state frame
    actor model m_parent
    role parent_role
    reads topic
    writes frame_out text
    on complete fan_out [fast, slow] join join_state on error abort_state
    on error => stop
    on timeout => stop
  state fast
    actor model m_fast
    role fast_role
    reads frame_out
    writes fast_out text
    on complete => done
    on error => stop
    on timeout => stop
  state slow
    actor model m_slow
    role slow_role
    reads frame_out, fast_out
    writes slow_out text
    on complete => done
    on error => stop
    on timeout => stop
  state join_state
    actor model m_join
    role joiner
    reads fast_out, slow_out
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

    registry = with_core()
    # ``slow`` reads ``fast_out`` (a sibling-written artifact) which
    # the validator's dominator analysis rejects because no writer of
    # ``fast_out`` dominates ``slow``. The executor still tolerates
    # this via snapshot isolation (the read returns the captured
    # pre-fan-out value, which is None for an unset artifact), and
    # this test asserts exactly that. Parse without validating to
    # keep that executor-level guarantee pinned.
    workflow = _parse_only(src)

    # The adapter records every prepared invocation's reads dict so
    # the test can inspect what each child actually saw.
    fast_done = threading.Event()
    invocations: list[tuple[str, dict[str, Any]]] = []
    inv_lock = threading.Lock()

    class _Recording(MockModelAdapter):
        def invoke(self, prepared: Any) -> dict[str, Any]:
            model_id = prepared.summary.get("model")
            with inv_lock:
                invocations.append((str(model_id), prepared.request.reads))
            if model_id == "m_fast":
                # Run fast first, then signal slow to proceed.
                result = super().invoke(prepared)
                # Note: state_exit and the artifact commit have not
                # yet happened at this point inside invoke; the
                # commit lands shortly after invoke returns. The
                # ``fast_done`` event is set in fast's adapter cancel
                # hook below, after the worker has finished.
                return result
            if model_id == "m_slow":
                # Slow waits for fast to reach a durable state_exit
                # before continuing inside invoke. Even if a buggy
                # implementation reached past _read_artifacts to
                # call store.read_latest, the wrap below records the
                # call and blocks until fast_done so the test can
                # observe both the call and (if any) returned value.
                fast_done.wait(timeout=5)
            return super().invoke(prepared)

    registry.actor_backings["model"] = lambda: _Recording()
    registry._adapter_cache.pop("model", None)

    run_id = new_run_id()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    store = _initialize_store(workflow, run_dir / "store.sqlite")

    # Test rigor (TEST GAP 1): wrap ``store.read_latest`` with a
    # per-artifact, per-thread-class call counter so the test can
    # assert no fan-out worker hit the live store while reading its
    # declared ``reads`` artifacts. A correct snapshot-using
    # implementation never calls ``read_latest`` from a fan-out
    # worker thread (the snapshot is captured on the controller
    # thread before workers start). Other paths -- snapshot capture
    # itself, transition selection, the linear join_state body --
    # legitimately read the live store, but those run on the
    # controller / linear-loop thread, not on a worker.
    worker_read_latest_calls: dict[str, int] = {}
    rl_lock = threading.Lock()
    real_read_latest = store.read_latest

    def _wrapped_read_latest(name: str) -> Any:
        if threading.current_thread().name.startswith("orchestra-fan-out"):
            with rl_lock:
                worker_read_latest_calls[name] = worker_read_latest_calls.get(name, 0) + 1
        return real_read_latest(name)

    store.read_latest = _wrapped_read_latest  # type: ignore[method-assign]

    log = LogWriter(run_dir / "log.jsonl", run_id)
    log.write("run_start", fields={"workflow_path": str(src)})
    executor = Executor(
        workflow=workflow,
        registry=registry,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=run_id,
        external_inputs={"topic": "hello"},
    )

    # Helper thread: poll the log until fast's state_exit is
    # durable, then set fast_done so slow can proceed. This places
    # the barrier AFTER fast has a durable state_exit (per
    # TEST GAP 1's "ensure the race the test claims to exercise
    # actually happens").
    def _watch_for_fast_durable() -> None:
        log_path = run_dir / "log.jsonl"
        deadline = time.time() + 10
        while time.time() < deadline:
            if log_path.exists():
                try:
                    records = LogReader(log_path).read_all()
                except Exception:
                    records = []
                if any(r.event == "state_exit" and r.state_id == "fast" for r in records):
                    fast_done.set()
                    return
            time.sleep(0.02)

    watcher = threading.Thread(target=_watch_for_fast_durable, daemon=True)
    watcher.start()

    terminal = executor.run_to_completion()
    log.write("run_end", fields={"terminal": terminal})
    log.close()
    store.close()
    watcher.join(timeout=2)
    assert terminal == "done"

    # Find slow's recorded reads. frame_out should be populated
    # (parent state's commit landed before fan-out, and lives in the
    # snapshot). fast_out should be None (its commit landed during
    # fan-out, which the snapshot does not see).
    slow_reads = next(reads for model_id, reads in invocations if model_id == "m_slow")
    assert slow_reads["frame_out"]["value"] is not None
    assert slow_reads["frame_out"]["__version_id"] == "snapshot"
    assert slow_reads["fast_out"]["value"] is None
    assert slow_reads["fast_out"]["__version_id"] == ""

    # TEST GAP 1: the per-thread wrap proves no live-store reads
    # were issued FROM A WORKER THREAD for the fan-out children's
    # declared reads. A correct snapshot-using implementation has
    # workers consult only the snapshot dict; only the controller
    # (snapshot capture) and the linear loop (transition selection,
    # post-fan-out states) read the live store. Anything > 0 in
    # this map is a worker hitting the live store.
    assert worker_read_latest_calls == {}, (
        f"no fan-out worker should hit the live store for any "
        f"artifact; got {worker_read_latest_calls!r}"
    )


def test_fan_out_child_retry_budget_is_per_entry(tmp_path: Path) -> None:
    """A fan-out child whose state declares
    ``on error retry max 2 then stop`` retries up to twice on error.
    Each retry mints a fresh invocation_id (new attempt_seq). The
    final invocation succeeds; the worker returns success and the
    group's aggregate is success.

    Tests Blocker 2's child-local retry support.
    """
    import threading

    from orchestra.adapters.mock_model import MockModelAdapter

    _write_dummy_template(tmp_path)
    src = tmp_path / "fan.orc"
    src.write_text(
        """spec 0.1
workflow retry
  external_input topic text
  max_total_steps 30
  model m_parent
  model m_flaky
  model m_join
  model m_abort
  artifact parent_out text
  artifact flaky_out text
  artifact joined text
  artifact aborted text
  role pr
    prompt template "templates/dummy.md"
  role fr
    prompt template "templates/dummy.md"
  role jr
    prompt template "templates/dummy.md"
  role ar
    prompt template "templates/dummy.md"
  state launch
    actor model m_parent
    role pr
    reads topic
    writes parent_out text
    on complete fan_out [flaky] join join_state on error abort_state
    on error => stop
    on timeout => stop
  state flaky
    actor model m_flaky
    role fr
    reads topic
    writes flaky_out text
    on complete => done
    on error retry max 2 then stop
    on timeout => stop
  state join_state
    actor model m_join
    role jr
    reads flaky_out
    writes joined text
    on complete => done
    on error => stop
    on timeout => stop
  state abort_state
    actor model m_abort
    role ar
    reads topic
    writes aborted text
    on complete => stop
    on error => stop
    on timeout => stop
"""
    )

    registry = with_core()
    workflow = load_workflow(src, registry)

    flaky_calls = {"n": 0}
    inv_lock = threading.Lock()

    class _Flaky(MockModelAdapter):
        def invoke(self, prepared: Any) -> dict[str, Any]:
            model_id = prepared.summary.get("model")
            if model_id == "m_flaky":
                with inv_lock:
                    flaky_calls["n"] += 1
                    n = flaky_calls["n"]
                if n <= 2:
                    raise RuntimeError(f"synthetic flaky failure #{n}")
            return super().invoke(prepared)

    registry.actor_backings["model"] = lambda: _Flaky()
    registry._adapter_cache.pop("model", None)

    run_id = new_run_id()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    store = _initialize_store(workflow, run_dir / "store.sqlite")
    log = LogWriter(run_dir / "log.jsonl", run_id)
    log.write("run_start", fields={"workflow_path": str(src)})
    executor = Executor(
        workflow=workflow,
        registry=registry,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=run_id,
        external_inputs={"topic": "hello"},
    )
    terminal = executor.run_to_completion()
    log.write("run_end", fields={"terminal": terminal})
    log.close()
    store.close()
    # Two failures + one success = three adapter invocations on the
    # flaky child.
    assert flaky_calls["n"] == 3
    # The child eventually succeeded so the group routes to the
    # join target.
    assert terminal == "done"

    # Three distinct invocation_ids on the flaky child's state_enter
    # records (one per attempt).
    records = LogReader(run_dir / "log.jsonl").read_all()
    enters = [r for r in records if r.event == "state_enter" and r.state_id == "flaky"]
    assert len(enters) == 3
    inv_ids: list[str] = []
    for record in enters:
        invocation_id = record.fields.get("invocation_id")
        assert isinstance(invocation_id, str)
        inv_ids.append(invocation_id)
    assert len(set(inv_ids)) == 3
    # attempt_seq monotonic 1, 2, 3.
    seqs = sorted(int(i.split("::")[2]) for i in inv_ids)
    assert seqs == [1, 2, 3]
    # The fan_out_end aggregate is success.
    fan_end = next(r for r in records if r.event == "fan_out_end")
    assert fan_end.fields["aggregate"] == "success"
    assert fan_end.fields["per_child_outcome"]["flaky"] == "success"


def test_per_child_cancellation_isolation_under_pressure(
    tmp_path: Path,
) -> None:
    """Per-child cancellation isolation under concurrent stress.

    Five fan-out children A-E. A errors after a short delay; B/C/D/E
    block on individual barriers, then return success after their
    own cancel callback runs. Asserts:

      1. Each of B/C/D/E receives ``cancel(handle)`` with its OWN
         prepared handle (no cross-child handle leakage).
      2. After cancel, B/C/D/E drain naturally and write durable
         state_exit records.
      3. The group aggregate is error; per_child_outcome contains
         all five children's outcomes.
      4. No registry-internal exception (KeyError, AttributeError,
         RuntimeError) escapes during concurrent cancellation.

    Tests Cleanup TEST 3.
    """
    import threading

    from orchestra.adapters.mock_model import MockModelAdapter

    _write_dummy_template(tmp_path)
    src = tmp_path / "fan.orc"
    src.write_text(
        """spec 0.1
workflow stress_cancel
  external_input topic text
  max_total_steps 30
  model m_parent
  model m_a
  model m_b
  model m_c
  model m_d
  model m_e
  model m_join
  model m_abort
  artifact parent_out text
  artifact a_out text
  artifact b_out text
  artifact c_out text
  artifact d_out text
  artifact e_out text
  artifact joined text
  artifact aborted text
  role pr
    prompt template "templates/dummy.md"
  role lens
    prompt template "templates/dummy.md"
  role jr
    prompt template "templates/dummy.md"
  role abr
    prompt template "templates/dummy.md"
  state launch
    actor model m_parent
    role pr
    reads topic
    writes parent_out text
    on complete fan_out [a, b, c, d, e] join join_state on error abort_state
    on error => stop
    on timeout => stop
  state a
    actor model m_a
    role lens
    reads topic
    writes a_out text
    on complete => done
    on error => stop
    on timeout => stop
  state b
    actor model m_b
    role lens
    reads topic
    writes b_out text
    on complete => done
    on error => stop
    on timeout => stop
  state c
    actor model m_c
    role lens
    reads topic
    writes c_out text
    on complete => done
    on error => stop
    on timeout => stop
  state d
    actor model m_d
    role lens
    reads topic
    writes d_out text
    on complete => done
    on error => stop
    on timeout => stop
  state e
    actor model m_e
    role lens
    reads topic
    writes e_out text
    on complete => done
    on error => stop
    on timeout => stop
  state join_state
    actor model m_join
    role jr
    reads a_out, b_out, c_out, d_out, e_out
    writes joined text
    on complete => done
    on error => stop
    on timeout => stop
  state abort_state
    actor model m_abort
    role abr
    reads topic
    writes aborted text
    on complete => stop
    on error => stop
    on timeout => stop
"""
    )

    registry = with_core()
    workflow = load_workflow(src, registry)

    # Per-child release events. Each non-A child blocks in invoke
    # until ITS event is set; the adapter's cancel(handle) sets
    # the event for the child whose handle is being cancelled.
    release_events: dict[str, threading.Event] = {
        "m_b": threading.Event(),
        "m_c": threading.Event(),
        "m_d": threading.Event(),
        "m_e": threading.Event(),
    }
    cancel_calls: list[tuple[str, Any]] = []
    cancel_lock = threading.Lock()
    captured_exceptions: list[BaseException] = []

    class _IsolatingAdapter(MockModelAdapter):
        def invoke(self, prepared: Any) -> dict[str, Any]:
            try:
                model_id = str(prepared.summary.get("model"))
                if model_id == "m_a":
                    # Small delay so all the others reach invoke and
                    # are registered before a errors. The exact race
                    # the test exercises: cancel arrives while
                    # multiple children are simultaneously in invoke.
                    import time as _time

                    _time.sleep(0.05)
                    raise RuntimeError("synthetic failure on a")
                if model_id in release_events:
                    release_events[model_id].wait(timeout=5)
                return super().invoke(prepared)
            except RuntimeError:
                raise
            except BaseException as exc:  # noqa: BLE001
                captured_exceptions.append(exc)
                raise

        def cancel(self, prepared: Any) -> None:
            try:
                model_id = str(prepared.summary.get("model"))
                with cancel_lock:
                    cancel_calls.append((model_id, prepared))
                # Releasing the corresponding child's event lets it
                # drain. The handle that arrives MUST be the same
                # PreparedInvocation the worker received for that
                # child; if the registry leaks a different child's
                # handle, the wrong event would be set and a
                # different child would unblock.
                if model_id in release_events:
                    release_events[model_id].set()
            except BaseException as exc:  # noqa: BLE001
                captured_exceptions.append(exc)
                raise

    registry.actor_backings["model"] = lambda: _IsolatingAdapter()
    registry._adapter_cache.pop("model", None)

    run_id = new_run_id()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    store = _initialize_store(workflow, run_dir / "store.sqlite")
    log = LogWriter(run_dir / "log.jsonl", run_id)
    log.write("run_start", fields={"workflow_path": str(src)})
    executor = Executor(
        workflow=workflow,
        registry=registry,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=run_id,
        external_inputs={"topic": "stress"},
    )
    terminal = executor.run_to_completion()
    log.write("run_end", fields={"terminal": terminal})
    log.close()
    store.close()

    # No exception escaped through the registry's concurrent
    # cancellation paths.
    assert captured_exceptions == [], (
        f"unexpected exceptions during stress cancellation: {captured_exceptions!r}"
    )

    # Each of B/C/D/E received exactly one cancel(handle) call with
    # ITS OWN handle. Group cancel_calls by model_id and verify
    # uniqueness.
    by_model: dict[str, list[Any]] = {}
    for model_id, prepared in cancel_calls:
        by_model.setdefault(model_id, []).append(prepared)

    for model_id in ("m_b", "m_c", "m_d", "m_e"):
        handles = by_model.get(model_id, [])
        assert len(handles) == 1, (
            f"{model_id} should have received exactly one "
            f"cancel(handle); got {len(handles)} (full: {by_model!r})"
        )
        # The handle's summary's model field must match the model_id
        # under which it was recorded -- proving no cross-child
        # leakage of handles.
        assert handles[0].summary.get("model") == model_id, (
            f"{model_id} received a cancel(handle) for a different "
            f"child: handle.summary={handles[0].summary!r}"
        )

    # All four cancelled children's prepared handles are pairwise
    # distinct objects (each adapter.prepare returns a new instance).
    cancelled_handles = [by_model[m][0] for m in ("m_b", "m_c", "m_d", "m_e")]
    assert len(set(id(h) for h in cancelled_handles)) == 4, (
        "expected four distinct prepared-invocation handles "
        "across the cancelled children; got duplicates"
    )

    # B/C/D/E each have a durable state_exit recording whatever
    # outcome they ended up producing (all success since cancel
    # released them and they returned MockModelAdapter's payload).
    records = LogReader(run_dir / "log.jsonl").read_all()
    for child in ("b", "c", "d", "e"):
        exits = [r for r in records if r.event == "state_exit" and r.state_id == child]
        assert len(exits) == 1, f"child {child} missing durable state_exit"

    # Group aggregate is error (a errored), routed to abort_state.
    # per_child_outcome contains all five children.
    fan_end = next(r for r in records if r.event == "fan_out_end")
    assert fan_end.fields["aggregate"] == "error"
    assert fan_end.fields["target"] == "abort_state"
    per_child = fan_end.fields["per_child_outcome"]
    assert set(per_child.keys()) == {"a", "b", "c", "d", "e"}
    assert per_child["a"] == "error"


def test_crash_mid_retry_then_replay_with_fresh_budget(
    tmp_path: Path,
) -> None:
    """A child with ``on error retry max 2 then stop`` errors,
    retries, and the run crashes mid-retry. On resume the child's
    retry budget is fresh (per the re-entry retry budget rule):
    retries[child] resets to 0 so the re-entered invocation can
    fail up to two more times before the budget is exhausted.

    Concretely the test runs a flaky adapter that errors twice
    then succeeds. We truncate the log AFTER attempt 1's durable
    error ``state_exit`` and attempt 2's ``state_enter`` (no exit).
    On resume, the new adapter (a fresh-process instance) runs
    once for the resumed entry and succeeds. The resumed
    ``state_enter`` must show ``retries[child] == 0`` (fresh
    budget); ``fan_out_end`` records the success with the
    resumed attempt's invocation_id.

    Tests Cleanup TEST 2.
    """
    import threading

    from orchestra.adapters.mock_model import MockModelAdapter

    _write_dummy_template(tmp_path)
    src = tmp_path / "fan.orc"
    src.write_text(
        """spec 0.1
workflow crash_retry
  external_input topic text
  max_total_steps 30
  model m_parent
  model m_flaky
  model m_join
  model m_abort
  artifact parent_out text
  artifact flaky_out text
  artifact joined text
  artifact aborted text
  role pr
    prompt template "templates/dummy.md"
  role fr
    prompt template "templates/dummy.md"
  role jr
    prompt template "templates/dummy.md"
  role ar
    prompt template "templates/dummy.md"
  state launch
    actor model m_parent
    role pr
    reads topic
    writes parent_out text
    on complete fan_out [flaky] join join_state on error abort_state
    on error => stop
    on timeout => stop
  state flaky
    actor model m_flaky
    role fr
    reads topic
    writes flaky_out text
    on complete => done
    on error retry max 2 then stop
    on timeout => stop
  state join_state
    actor model m_join
    role jr
    reads flaky_out
    writes joined text
    on complete => done
    on error => stop
    on timeout => stop
  state abort_state
    actor model m_abort
    role ar
    reads topic
    writes aborted text
    on complete => stop
    on error => stop
    on timeout => stop
"""
    )

    registry = with_core()
    workflow = load_workflow(src, registry)

    # First run: flaky adapter errors twice, succeeds on third
    # invocation. Run normally, then truncate the log to simulate
    # a crash mid-retry.
    flaky_calls = {"n": 0}
    inv_lock = threading.Lock()

    class _Flaky(MockModelAdapter):
        def invoke(self, prepared: Any) -> dict[str, Any]:
            model_id = prepared.summary.get("model")
            if model_id == "m_flaky":
                with inv_lock:
                    flaky_calls["n"] += 1
                    n = flaky_calls["n"]
                if n <= 2:
                    raise RuntimeError(f"synthetic flaky failure #{n}")
            return super().invoke(prepared)

    registry.actor_backings["model"] = lambda: _Flaky()
    registry._adapter_cache.pop("model", None)

    run_id = new_run_id()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    store = _initialize_store(workflow, run_dir / "store.sqlite")
    log = LogWriter(run_dir / "log.jsonl", run_id)
    log.write("run_start", fields={"workflow_path": str(src)})
    executor = Executor(
        workflow=workflow,
        registry=registry,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=run_id,
        external_inputs={"topic": "hello"},
    )
    executor.run_to_completion()
    log.close()
    store.close()
    assert flaky_calls["n"] == 3

    log_path = run_dir / "log.jsonl"
    records = LogReader(log_path).read_all()
    # Drop run_end so replay does not short-circuit. Then truncate
    # to simulate "crash while attempt 2 is in flight": keep
    # records up to and including flaky's attempt-2 state_enter,
    # and drop attempt-2's actor_*, attempt-3's full trace, the
    # fan_out_end, and any post-fan-out state. The resume path
    # should see attempt 1 with durable error state_exit, attempt
    # 2 with state_enter only.
    truncated: list[Any] = []
    seen_attempt2_enter = False
    for r in records:
        if r.event == "run_end":
            continue
        truncated.append(r)
        if r.event == "state_enter" and r.state_id == "flaky" and r.attempt == 2:
            seen_attempt2_enter = True
            break
    assert seen_attempt2_enter
    log_path.write_text(
        "\n".join(r.to_json() for r in truncated) + "\n",
        encoding="utf-8",
    )

    # Sanity-check the truncated log:
    pre_records = LogReader(log_path).read_all()
    flaky_enters = [r for r in pre_records if r.event == "state_enter" and r.state_id == "flaky"]
    flaky_exits = [r for r in pre_records if r.event == "state_exit" and r.state_id == "flaky"]
    assert [r.attempt for r in flaky_enters] == [1, 2]
    assert [r.attempt for r in flaky_exits] == [1]
    assert flaky_exits[0].fields["status"] == "error"
    assert not any(r.event == "fan_out_end" for r in pre_records)

    # Second run: a SUCCEEDING adapter for the resumed entry. If
    # the fresh-budget rule is honored the resumed attempt has
    # retries=0 and the call succeeds.
    class _Succeed(MockModelAdapter):
        pass

    # Patch the registry used by ``_resume_open_fan_out`` to use
    # the succeeding adapter. The helper builds its own registry
    # via ``with_core()``, so monkey-patch the registry factory.
    def _resume_with_succeeding_adapter() -> tuple[str, dict[str, Any]]:
        from orchestra.resume import replay_log
        from orchestra.visibility import VisibilityIndex

        replay = replay_log(str(log_path))
        registry = with_core()
        registry.actor_backings["model"] = lambda: _Succeed()
        registry._adapter_cache.pop("model", None)
        workflow_resume = load_workflow(src, registry)
        store_resume = ArtifactStore(run_dir / "store.sqlite")
        log_resume = LogWriter(log_path, replay.last_run_id, start_seq=replay.next_seq)
        visibility_index = VisibilityIndex(persist_path=run_dir / "visibility.json")
        visibility_index.replace_from(replay.visibility_statuses)

        executor_resume = Executor(
            workflow=workflow_resume,
            registry=registry,
            store=store_resume,
            log=log_resume,
            run_dir=run_dir,
            run_id=replay.last_run_id,
            external_inputs={"topic": "hello"},
            attempts=replay.attempts,
            retries=replay.retries,
            envelopes=replay.envelopes,
            current_state=replay.current_state,
            step_count=replay.step_count,
            visibility_index=visibility_index,
        )
        assert replay.open_fan_out is not None
        of = replay.open_fan_out
        children_list = [str(c) for c in of["children"]]
        # A child mid-retry (older state_exit envelope plus a newer
        # state_enter without a matching exit) is still pending
        # and must be re-launched. Match cli.cmd_resume's
        # envelope-attempt-matches-latest-state-enter check.
        completed = {
            n: env
            for n, env in replay.envelopes.items()
            if n in children_list and env.attempt == replay.attempts.get(n)
        }
        executor_resume.resume_fan_out(
            parent_state_name=str(of["parent_state"]),
            children=children_list,
            join_target=str(of["join_target"]),
            error_target=str(of["error_target"]),
            completed_children=completed,
        )
        terminal_resume = executor_resume.run_to_completion()
        log_resume.close()
        store_resume.close()
        return terminal_resume, {}

    terminal2, _ = _resume_with_succeeding_adapter()
    assert terminal2 == "done"

    resumed_records = LogReader(log_path).read_all()

    # The resumed entry has retries[flaky] = 0 in its state_enter
    # snapshot (fresh budget rule).
    flaky_enters_after = [
        r for r in resumed_records if r.event == "state_enter" and r.state_id == "flaky"
    ]
    # Attempts 1 and 2 from pre-crash + 1 fresh resumed attempt.
    assert len(flaky_enters_after) == 3
    resumed_enter = flaky_enters_after[-1]
    retries_snapshot = resumed_enter.fields.get("retries", {})
    assert retries_snapshot.get("flaky") == 0, (
        f"resumed entry's retries snapshot should show fresh budget (0); got {retries_snapshot}"
    )

    # The resumed attempt's adapter call succeeded; flaky has a
    # durable success state_exit. fan_out_end records success.
    resumed_exits = [
        r for r in resumed_records if r.event == "state_exit" and r.state_id == "flaky"
    ]
    # Pre-crash exit (attempt 1, error) + new exit (resumed
    # attempt, success).
    assert len(resumed_exits) == 2
    final_exit = resumed_exits[-1]
    assert final_exit.fields["status"] == "ok"
    final_inv = str(final_exit.fields["invocation_id"])
    assert "::flaky::" in final_inv

    fan_end = next(r for r in resumed_records if r.event == "fan_out_end")
    assert fan_end.fields["per_child_outcome"]["flaky"] == "success"
    assert fan_end.fields["aggregate"] == "success"
    # The aggregate's invocation_id matches the final state_exit's.
    assert fan_end.fields["child_invocation_ids"]["flaky"] == final_inv


def test_cancellation_race_preserves_concurrent_success(
    tmp_path: Path,
) -> None:
    """Cancellation race rule: once any child errors the routing
    decision is fixed at the error target, but other children that
    successfully complete BEFORE ``fan_out_end`` is written must
    have their outcomes recorded in the per-child outcome map. This
    is the "A errors while B succeeds nearly simultaneously"
    scenario the plan calls out.

    Plus: the routing is stable across replay. A complete log
    routes identically on replay. A truncated log without
    ``fan_out_end`` (case 5) re-creates ``fan_out_end`` with both
    children's outcomes from the durable child state_exits.

    Tests Cleanup TEST 1.
    """
    import threading

    from orchestra.adapters.mock_model import MockModelAdapter

    _write_dummy_template(tmp_path)
    src = tmp_path / "fan.orc"
    src.write_text(
        """spec 0.1
workflow race_test
  external_input topic text
  max_total_steps 30
  model m_parent
  model m_a
  model m_b
  model m_join
  model m_abort
  artifact parent_out text
  artifact a_out text
  artifact b_out text
  artifact joined text
  artifact aborted text
  role pr
    prompt template "templates/dummy.md"
  role ar
    prompt template "templates/dummy.md"
  role br
    prompt template "templates/dummy.md"
  role jr
    prompt template "templates/dummy.md"
  role abr
    prompt template "templates/dummy.md"
  state launch
    actor model m_parent
    role pr
    reads topic
    writes parent_out text
    on complete fan_out [a, b] join join_state on error abort_state
    on error => stop
    on timeout => stop
  state a
    actor model m_a
    role ar
    reads topic
    writes a_out text
    on complete => done
    on error => stop
    on timeout => stop
  state b
    actor model m_b
    role br
    reads topic
    writes b_out text
    on complete => done
    on error => stop
    on timeout => stop
  state join_state
    actor model m_join
    role jr
    reads a_out, b_out
    writes joined text
    on complete => done
    on error => stop
    on timeout => stop
  state abort_state
    actor model m_abort
    role abr
    reads topic
    writes aborted text
    on complete => stop
    on error => stop
    on timeout => stop
"""
    )

    registry = with_core()
    workflow = load_workflow(src, registry)

    # ``b_proceed`` releases b's invoke after a has errored, so b
    # finishes after the controller has already requested
    # cancellation. The plan calls this the "subsequent successful
    # child outcomes do not change routing" race; b's success must
    # still appear in per_child_outcome but routing must stay at
    # the error target.
    a_invoked = threading.Event()
    b_in_invoke = threading.Event()
    b_proceed = threading.Event()

    class _Race(MockModelAdapter):
        def invoke(self, prepared: Any) -> dict[str, Any]:
            model_id = prepared.summary.get("model")
            if model_id == "m_a":
                # Only error once b's worker has entered adapter.invoke
                # (it is past the worker's pre-invoke cancel check, so
                # its entry is ``registered``). Without this gate a can
                # error while b is still ``pending``, in which case
                # request_cancel_all flags b's worker and it short
                # circuits before invoking: b_in_invoke would never
                # fire and the race the test means to exercise (a
                # registered child draining to success after cancel)
                # would not occur.
                b_in_invoke.wait(timeout=5)
                a_invoked.set()
                raise RuntimeError("synthetic failure on a")
            if model_id == "m_b":
                # Signal that b has reached adapter.invoke, then wait
                # for a to error so the controller has called
                # request_cancel_all by the time b completes. The
                # mock adapter's cancel is a no-op, so b's invoke
                # drains and returns success.
                b_in_invoke.set()
                a_invoked.wait(timeout=5)
                # Small additional delay so a's drain reaches the
                # controller and request_cancel_all has fired.
                import time as _time

                _time.sleep(0.05)
                b_proceed.set()
            return super().invoke(prepared)

    registry.actor_backings["model"] = lambda: _Race()
    registry._adapter_cache.pop("model", None)

    run_id = new_run_id()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    store = _initialize_store(workflow, run_dir / "store.sqlite")
    log = LogWriter(run_dir / "log.jsonl", run_id)
    log.write("run_start", fields={"workflow_path": str(src)})
    executor = Executor(
        workflow=workflow,
        registry=registry,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=run_id,
        external_inputs={"topic": "hello"},
    )
    terminal = executor.run_to_completion()
    log.write("run_end", fields={"terminal": terminal})
    log.close()
    store.close()

    # b's invoke completed after a's error, so b_proceed was set.
    assert b_proceed.is_set()

    # First-pass: per_child_outcome shows BOTH a=error AND b=success.
    records = LogReader(run_dir / "log.jsonl").read_all()
    fan_end = next(r for r in records if r.event == "fan_out_end")
    per_child = fan_end.fields["per_child_outcome"]
    assert per_child["a"] == "error", f"a errored; got {per_child!r}"
    assert per_child["b"] == "success", (
        f"b's success after a's error must still be recorded "
        f"(cancellation race rule); got {per_child!r}"
    )
    assert fan_end.fields["aggregate"] == "error"
    assert fan_end.fields["target"] == "abort_state"

    # Step 2: replay of the COMPLETE log routes identically. A run
    # whose log already contains ``fan_out_end`` does not re-run any
    # child on resume; the durable target is reused.
    from orchestra.resume import replay_log

    rep = replay_log(str(run_dir / "log.jsonl"))
    assert rep.last_fan_out_target == "abort_state"
    assert rep.open_fan_out is None

    # Step 3: replay of a TRUNCATED log (drop fan_out_end and
    # everything after) re-creates fan_out_end from the durable
    # child state_exits. Both children's state_exits remain on
    # disk; the resume path's resume_fan_out sees a's error in
    # completed_children and short-circuits per RA-B2 (no pending
    # children to launch since both already have state_exit, so
    # all children are "completed").
    truncated_dir = tmp_path / "truncated"
    truncated_dir.mkdir()
    log_path = truncated_dir / "log.jsonl"
    # Copy SQLite store and the payloads directory; resume hydrates
    # envelopes from durable payload files referenced by state_exit
    # records, so the truncated run directory must carry them.
    import shutil

    shutil.copy(run_dir / "store.sqlite", truncated_dir / "store.sqlite")
    shutil.copytree(run_dir / "payloads", truncated_dir / "payloads")
    # Copy log without fan_out_end and everything after.
    keep = []
    for r in records:
        if r.event in (
            "fan_out_end",
            "transition",
            "state_enter",
            "actor_prepare",
            "actor_invoke_start",
            "actor_invoke_end",
            "artifact_write",
            "state_exit",
            "run_end",
        ):
            # Keep state_enter / state_exit / artifact_write etc.
            # only if they belong to launch / a / b (drop
            # post-fan-out abort_state records and fan_out_end).
            if r.event == "fan_out_end":
                continue
            if r.state_id in ("launch", "a", "b") or r.state_id is None:
                keep.append(r)
            # Drop abort_state records (which ran AFTER fan_out_end).
            continue
        keep.append(r)
    # Drop everything after the (now-removed) fan_out_end. The
    # simpler approach: keep all records up to but not including
    # fan_out_end.
    keep = []
    for r in records:
        if r.event == "fan_out_end":
            break
        keep.append(r)
    log_path.write_text("\n".join(r.to_json() for r in keep) + "\n", encoding="utf-8")

    # Replay: open_fan_out should be set, with completed_children
    # carrying both a (error) and b (success) envelopes.
    rep_truncated = replay_log(str(log_path))
    assert rep_truncated.open_fan_out is not None
    assert rep_truncated.envelopes["a"].status == "error"
    assert rep_truncated.envelopes["b"].status == "ok"

    # Now drive resume_fan_out via the helper. RA-B2 short-circuits
    # because completed_children includes a's error; pending
    # children (none in this case, since a and b both completed)
    # are not launched. fan_out_end is written with the per-child
    # outcomes reconstructed from completed_children.
    terminal2, _ = _resume_open_fan_out(truncated_dir, src, {"topic": "hello"})

    # The resumed log has a fresh fan_out_end with both outcomes.
    resumed_records = LogReader(log_path).read_all()
    fan_ends = [r for r in resumed_records if r.event == "fan_out_end"]
    assert len(fan_ends) == 1
    fan_end_resumed = fan_ends[0]
    assert fan_end_resumed.fields["per_child_outcome"]["a"] == "error"
    assert fan_end_resumed.fields["per_child_outcome"]["b"] == "success"
    assert fan_end_resumed.fields["aggregate"] == "error"
    assert fan_end_resumed.fields["target"] == "abort_state"


def test_lock_order_deadlock_prevention(tmp_path: Path) -> None:
    """The plan's lock-ordering rule: LogWriter is the OUTER lock,
    ArtifactStore is the INNER lock. Anywhere both locks are held,
    the LogWriter lock must be acquired first. A code path that
    acquired them in the opposite order against a concurrent path
    using the correct order would deadlock.

    This test instruments both locks with an acquisition recorder,
    runs a fan-out group end-to-end (which exercises the
    snapshot-capture critical section that legitimately holds
    both), and asserts:

      1. The test completes without deadlocking (pytest-timeout
         catches a deadlock as a test failure).
      2. No thread acquires the store lock while already holding
         only the log lock without then releasing them in the
         right order, and no thread acquires the log lock while
         already holding the store lock (which would be the
         deadlock-prone reversed order).

    Tests Cleanup 2 (the "fan_out_start under both locks" reorder)
    plus the missing "lock-order deadlock prevention with direct
    instrumentation" test from earlier audit gaps.
    """
    import threading
    import time

    workflow_path = _fan_out_fixture_workflow(tmp_path)
    registry = with_core()
    workflow = load_workflow(workflow_path, registry)

    run_id = new_run_id()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    store = _initialize_store(workflow, run_dir / "store.sqlite")
    log = LogWriter(run_dir / "log.jsonl", run_id)
    log.write("run_start", fields={"workflow_path": str(workflow_path)})

    # Per-thread held set, plus a global event log of (thread, op,
    # lock-name). The recording wrapper holds the real lock; only
    # the bookkeeping is added.
    held: dict[str, set[str]] = {}
    events: list[tuple[str, str, str]] = []
    rec_lock = threading.Lock()

    class _RecordingRLock:
        """Wraps a real RLock so the test can observe acquire and
        release events. Forwards acquire/release/__enter__/__exit__
        to the underlying lock."""

        def __init__(self, real: Any, name: str) -> None:
            self._real = real
            self._name = name

        def __enter__(self) -> Any:
            self._real.__enter__()
            tname = threading.current_thread().name
            with rec_lock:
                held.setdefault(tname, set()).add(self._name)
                events.append((tname, "acquire", self._name))
            return self

        def __exit__(self, *args: Any) -> Any:
            tname = threading.current_thread().name
            with rec_lock:
                held.get(tname, set()).discard(self._name)
                events.append((tname, "release", self._name))
            return self._real.__exit__(*args)

        def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
            ok = bool(self._real.acquire(blocking, timeout))
            if ok:
                tname = threading.current_thread().name
                with rec_lock:
                    held.setdefault(tname, set()).add(self._name)
                    events.append((tname, "acquire", self._name))
            return ok

        def release(self) -> None:
            tname = threading.current_thread().name
            with rec_lock:
                held.get(tname, set()).discard(self._name)
                events.append((tname, "release", self._name))
            self._real.release()

    # Replace ``_lock`` on both objects with the recording wrapper.
    # The ``lock`` property returns ``_lock`` directly, so external
    # callers (the executor's snapshot-capture path) get the
    # wrapper transparently.
    log._lock = _RecordingRLock(log._lock, "log")  # type: ignore[assignment]
    store._lock = _RecordingRLock(store._lock, "store")  # type: ignore[assignment]

    # Track for each thread: at every store-lock acquire, is the
    # log lock currently held by that thread? (Required: yes.) At
    # every log-lock acquire, is the store lock currently held by
    # that thread? (Forbidden: that would be reversed order.) The
    # property "store-lock acquired without log-lock held" is fine
    # for the workers' own commit_tentative / read_latest calls;
    # they don't also take the log lock.
    violation: list[str] = []

    # Re-wrap the wrappers to also assert ordering at acquire time.
    # We do this by inspecting ``held`` at the moment of acquire.
    real_store_enter = store._lock.__enter__
    real_log_enter = log._lock.__enter__

    def _store_enter_checked() -> Any:
        # Acquiring the store lock while already holding only log
        # lock is fine (correct order). Acquiring while holding
        # NEITHER is fine. Acquiring while holding the store lock
        # is fine (RLock, same thread re-entry).
        return real_store_enter()

    def _log_enter_checked() -> Any:
        tname = threading.current_thread().name
        with rec_lock:
            already_held = set(held.get(tname, set()))
        # Acquiring log lock while already holding the store lock
        # is the reversed order (deadlock-prone). Flag it.
        if "store" in already_held and "log" not in already_held:
            violation.append(
                f"{tname} acquired log lock while holding store "
                f"lock but not log lock (reversed order)"
            )
        result = real_log_enter()
        return result

    store._lock.__enter__ = _store_enter_checked  # type: ignore[method-assign, assignment]
    log._lock.__enter__ = _log_enter_checked  # type: ignore[method-assign, assignment]

    executor = Executor(
        workflow=workflow,
        registry=registry,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=run_id,
        external_inputs={"topic": "hello world"},
    )

    # Run with a watchdog that fails fast if the run deadlocks.
    # pytest-timeout (configured at the test level) is the primary
    # safeguard; this watchdog gives a clearer failure message if
    # the test setup is at fault.
    deadline = time.time() + 10.0
    done_flag: list[str] = []

    def _runner() -> None:
        terminal = executor.run_to_completion()
        done_flag.append(terminal)

    runner = threading.Thread(target=_runner, daemon=True)
    runner.start()
    runner.join(timeout=deadline - time.time())
    assert not runner.is_alive(), (
        "fan-out workflow did not complete within 10s; possible "
        "deadlock. acquisition events tail: "
        f"{events[-20:]!r}"
    )
    assert done_flag and done_flag[0] == "done"
    assert violation == [], f"lock-order violations: {violation!r}"

    # The snapshot-capture critical section legitimately holds
    # both locks. Verify the recorded events show at least one
    # such pair, and that the order is log-then-store (not the
    # reverse).
    seq_by_thread: dict[str, list[tuple[str, str]]] = {}
    for tname, op, lname in events:
        seq_by_thread.setdefault(tname, []).append((op, lname))

    # Inspect the controller-thread sequence (the linear-loop
    # thread, named "MainThread" or similar -- we just look for
    # the first thread that acquires both within a window).
    found_both = False
    for _tname, ops in seq_by_thread.items():
        # Scan: find a "log acquire" then "store acquire" before
        # any matching releases.
        held_set: set[str] = set()
        for op, lname in ops:
            if op == "acquire":
                if lname == "store" and "log" not in held_set:
                    # Store acquired without log -- that's fine for
                    # workers (commit_tentative etc.) but not for
                    # the snapshot-capture path. Skip.
                    pass
                elif lname == "log":
                    # Log acquired first (correct).
                    pass
                if lname == "store" and "log" in held_set:
                    found_both = True
                held_set.add(lname)
            else:
                held_set.discard(lname)
            if found_both:
                break
        if found_both:
            break
    assert found_both, (
        "expected at least one thread to acquire log THEN store "
        "(snapshot-capture path); none observed"
    )
    log.close()
    store.close()


def test_fan_out_end_records_final_retry_invocation_id(
    tmp_path: Path,
) -> None:
    """When a fan-out child retries (Blocker 2's child-local retry
    loop), its final invocation_id is keyed to the attempt that
    actually produced the durable ``state_exit`` and any artifact
    commits. The aggregate ``fan_out_end`` record's
    ``child_invocation_ids[child]`` must match that final attempt,
    not the initial attempt the controller submitted.

    The bug: the controller computed the per-child invocation_id
    from ``per_child_attempt[child_name]``, which captures
    ``attempt_seq=1``. After two retries the actual successful
    invocation is ``attempt_seq=3``. Downstream tooling that joined
    fan_out_end's child_invocation_ids back to the per-state log
    records found a mismatch.

    Tests Re-audit P2.
    """
    import threading

    from orchestra.adapters.mock_model import MockModelAdapter

    _write_dummy_template(tmp_path)
    src = tmp_path / "fan.orc"
    src.write_text(
        """spec 0.1
workflow retry_inv_id
  external_input topic text
  max_total_steps 30
  model m_parent
  model m_flaky
  model m_join
  model m_abort
  artifact parent_out text
  artifact flaky_out text
  artifact joined text
  artifact aborted text
  role pr
    prompt template "templates/dummy.md"
  role fr
    prompt template "templates/dummy.md"
  role jr
    prompt template "templates/dummy.md"
  role ar
    prompt template "templates/dummy.md"
  state launch
    actor model m_parent
    role pr
    reads topic
    writes parent_out text
    on complete fan_out [flaky] join join_state on error abort_state
    on error => stop
    on timeout => stop
  state flaky
    actor model m_flaky
    role fr
    reads topic
    writes flaky_out text
    on complete => done
    on error retry max 2 then stop
    on timeout => stop
  state join_state
    actor model m_join
    role jr
    reads flaky_out
    writes joined text
    on complete => done
    on error => stop
    on timeout => stop
  state abort_state
    actor model m_abort
    role ar
    reads topic
    writes aborted text
    on complete => stop
    on error => stop
    on timeout => stop
"""
    )

    registry = with_core()
    workflow = load_workflow(src, registry)

    flaky_calls = {"n": 0}
    inv_lock = threading.Lock()

    class _Flaky(MockModelAdapter):
        def invoke(self, prepared: Any) -> dict[str, Any]:
            model_id = prepared.summary.get("model")
            if model_id == "m_flaky":
                with inv_lock:
                    flaky_calls["n"] += 1
                    n = flaky_calls["n"]
                if n <= 2:
                    raise RuntimeError(f"synthetic flaky failure #{n}")
            return super().invoke(prepared)

    registry.actor_backings["model"] = lambda: _Flaky()
    registry._adapter_cache.pop("model", None)

    run_id = new_run_id()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    store = _initialize_store(workflow, run_dir / "store.sqlite")
    log = LogWriter(run_dir / "log.jsonl", run_id)
    log.write("run_start", fields={"workflow_path": str(src)})
    executor = Executor(
        workflow=workflow,
        registry=registry,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=run_id,
        external_inputs={"topic": "hello"},
    )
    terminal = executor.run_to_completion()
    log.write("run_end", fields={"terminal": terminal})
    log.close()
    store.close()
    assert flaky_calls["n"] == 3
    assert terminal == "done"

    records = LogReader(run_dir / "log.jsonl").read_all()
    fan_end = next(r for r in records if r.event == "fan_out_end")

    # The aggregate's per-child invocation_id is the FINAL attempt
    # (attempt_seq=3), matching the durable state_exit that
    # produced the success.
    flaky_inv = fan_end.fields["child_invocation_ids"]["flaky"]
    assert isinstance(flaky_inv, str)
    parts = flaky_inv.split("::")
    assert parts[1] == "flaky"
    assert parts[2] == "3", (
        f"expected final attempt_seq=3 in fan_out_end's child_invocation_ids; got {flaky_inv!r}"
    )

    # And the durable state_exit at attempt 3 carries the same
    # invocation_id (the records agree).
    flaky_exits = [r for r in records if r.event == "state_exit" and r.state_id == "flaky"]
    # The final state_exit (the one that succeeded) is at attempt 3.
    final_exit = next(
        r for r in flaky_exits if str(r.fields.get("invocation_id")).endswith("::flaky::3")
    )
    assert final_exit.fields["status"] == "ok"
    assert final_exit.fields["invocation_id"] == flaky_inv


def test_cancellation_registered_child_calls_adapter_cancel(
    tmp_path: Path,
) -> None:
    """When one fan-out child errors, the controller calls
    ``request_cancel_all``. For children that are already
    ``registered`` (their ``adapter.prepare`` returned and the worker
    is mid-invoke), the registry now calls
    ``adapter.cancel(invocation_handle)`` on the appropriate adapter
    so it has the chance to abort cooperatively. The worker still
    drains to a durable ``state_exit``; the cancel call is best
    effort.

    Tests Blocker 6's invocation_handle threading.
    """
    import threading

    from orchestra.adapters.mock_model import MockModelAdapter
    from orchestra.spine import PreparedInvocation

    _write_dummy_template(tmp_path)
    src = tmp_path / "fan.orc"
    src.write_text(
        """spec 0.1
workflow cancel_test
  external_input topic text
  max_total_steps 30
  model m_parent
  model m_a
  model m_b
  model m_join
  model m_abort
  artifact parent_out text
  artifact a_out text
  artifact b_out text
  artifact joined text
  artifact aborted text
  role pr
    prompt template "templates/dummy.md"
  role ar
    prompt template "templates/dummy.md"
  role br
    prompt template "templates/dummy.md"
  role jr
    prompt template "templates/dummy.md"
  role abr
    prompt template "templates/dummy.md"
  state launch
    actor model m_parent
    role pr
    reads topic
    writes parent_out text
    on complete fan_out [a, b] join join_state on error abort_state
    on error => stop
    on timeout => stop
  state a
    actor model m_a
    role ar
    reads topic
    writes a_out text
    on complete => done
    on error => stop
    on timeout => stop
  state b
    actor model m_b
    role br
    reads topic
    writes b_out text
    on complete => done
    on error => stop
    on timeout => stop
  state join_state
    actor model m_join
    role jr
    reads a_out, b_out
    writes joined text
    on complete => done
    on error => stop
    on timeout => stop
  state abort_state
    actor model m_abort
    role abr
    reads topic
    writes aborted text
    on complete => stop
    on error => stop
    on timeout => stop
"""
    )

    registry = with_core()
    workflow = load_workflow(src, registry)

    b_in_invoke = threading.Event()
    cancel_signal = threading.Event()
    cancel_calls: list[tuple[str, PreparedInvocation]] = []
    cancel_lock = threading.Lock()

    class _Cancelable(MockModelAdapter):
        def invoke(self, prepared: Any) -> dict[str, Any]:
            model_id = prepared.summary.get("model")
            if model_id == "m_a":
                # Wait for b to enter invoke before erroring, so the
                # controller's request_cancel_all observes b in the
                # ``registered`` state with a stored handle.
                b_in_invoke.wait(timeout=5)
                raise RuntimeError("synthetic failure on a")
            if model_id == "m_b":
                b_in_invoke.set()
                # Block until the controller calls adapter.cancel(),
                # which sets cancel_signal. The worker then drains
                # naturally to a durable state_exit.
                cancel_signal.wait(timeout=5)
                return super().invoke(prepared)
            return super().invoke(prepared)

        def cancel(self, prepared: Any) -> None:
            model_id = prepared.summary.get("model")
            with cancel_lock:
                cancel_calls.append((str(model_id), prepared))
            cancel_signal.set()

    registry.actor_backings["model"] = lambda: _Cancelable()
    registry._adapter_cache.pop("model", None)

    run_id = new_run_id()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    store = _initialize_store(workflow, run_dir / "store.sqlite")
    log = LogWriter(run_dir / "log.jsonl", run_id)
    log.write("run_start", fields={"workflow_path": str(src)})
    executor = Executor(
        workflow=workflow,
        registry=registry,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=run_id,
        external_inputs={"topic": "hello"},
    )
    terminal = executor.run_to_completion()
    log.write("run_end", fields={"terminal": terminal})
    log.close()
    store.close()

    # The registry called cancel on b's adapter exactly once, with
    # b's prepared handle. (a's worker raised, so a was never in the
    # ``registered`` state with a stored handle; only registered
    # children receive cancel.)
    b_cancels = [c for c in cancel_calls if c[0] == "m_b"]
    assert len(b_cancels) == 1, (
        f"expected exactly one cancel(prepared) call for b, got {cancel_calls}"
    )
    _, prepared = b_cancels[0]
    assert prepared.summary.get("model") == "m_b"

    # b's worker drained to a durable state_exit despite the cancel.
    records = LogReader(run_dir / "log.jsonl").read_all()
    b_exits = [r for r in records if r.event == "state_exit" and r.state_id == "b"]
    assert len(b_exits) == 1

    # fan_out_end records the per-child outcome map and routes to
    # the error_target (a errored).
    fan_end = next(r for r in records if r.event == "fan_out_end")
    assert "b" in fan_end.fields["per_child_outcome"]
    assert fan_end.fields["aggregate"] == "error"
    assert fan_end.fields["target"] == "abort_state"


def test_pending_cancellation_caught_between_register_and_invoke(
    tmp_path: Path,
) -> None:
    """The cancellation registry's ``request_cancel_all`` can fire
    AFTER a worker exits ``adapter.prepare()`` (so the registry has
    transitioned to ``registered`` and the handle is stored) but
    BEFORE the worker enters ``actor_invoke_start`` /
    ``adapter.invoke()``. Without a re-check at that boundary the
    pending-flag path's ``future.cancel()`` is a no-op against a
    running future, the registered-cancel path's
    ``adapter.cancel(handle)`` lands on a not-yet-invoked handle,
    and the worker happily fires ``adapter.invoke`` regardless.

    The fix re-checks ``cancel_requested`` after ``on_prepared`` and
    before ``actor_invoke_start``; ``request_cancel_all`` now also
    sets the flag for ``registered`` entries so the worker observes
    the cancellation regardless of which branch the controller took.

    Tests Re-audit Blocker 3.
    """
    import threading

    from orchestra.adapters.mock_model import MockModelAdapter

    _write_dummy_template(tmp_path)
    src = tmp_path / "fan.orc"
    src.write_text(
        """spec 0.1
workflow cancel_pre_invoke
  external_input topic text
  max_total_steps 30
  model m_parent
  model m_a
  model m_b
  model m_join
  model m_abort
  artifact parent_out text
  artifact a_out text
  artifact b_out text
  artifact joined text
  artifact aborted text
  role pr
    prompt template "templates/dummy.md"
  role ar
    prompt template "templates/dummy.md"
  role br
    prompt template "templates/dummy.md"
  role jr
    prompt template "templates/dummy.md"
  role abr
    prompt template "templates/dummy.md"
  state launch
    actor model m_parent
    role pr
    reads topic
    writes parent_out text
    on complete fan_out [a, b] join join_state on error abort_state
    on error => stop
    on timeout => stop
  state a
    actor model m_a
    role ar
    reads topic
    writes a_out text
    on complete => done
    on error => stop
    on timeout => stop
  state b
    actor model m_b
    role br
    reads topic
    writes b_out text
    on complete => done
    on error => stop
    on timeout => stop
  state join_state
    actor model m_join
    role jr
    reads a_out, b_out
    writes joined text
    on complete => done
    on error => stop
    on timeout => stop
  state abort_state
    actor model m_abort
    role abr
    reads topic
    writes aborted text
    on complete => stop
    on error => stop
    on timeout => stop
"""
    )

    registry = with_core()
    workflow = load_workflow(src, registry)

    # b errors immediately. a's prepare() blocks on ``a_prepare_release``
    # until b's error has propagated through the controller and
    # ``request_cancel_all`` has set a's cancel_requested flag. Once
    # released, a's prepare returns; on_prepared transitions a to
    # ``registered``; the new post-register cancel check observes
    # the flag and takes the cancelled path WITHOUT calling
    # adapter.invoke.
    a_prepare_entered = threading.Event()
    a_prepare_release = threading.Event()
    invoke_calls: list[str] = []
    invoke_lock = threading.Lock()

    class _Coordinated(MockModelAdapter):
        def prepare(self, request: Any) -> Any:
            model_id = (request.actor_binding or {}).get("model")
            if model_id == "m_a":
                a_prepare_entered.set()
                # Wait for the test to release: by the time we
                # release, the controller has already processed b's
                # error and called request_cancel_all, flipping a's
                # cancel_requested flag. on_prepared then transitions
                # the registry to ``registered``, and the worker's
                # post-register check sees the flag.
                a_prepare_release.wait(timeout=5)
            return super().prepare(request)

        def invoke(self, prepared: Any) -> dict[str, Any]:
            model_id = prepared.summary.get("model")
            with invoke_lock:
                invoke_calls.append(str(model_id))
            if model_id == "m_b":
                raise RuntimeError("synthetic failure on b")
            return super().invoke(prepared)

    registry.actor_backings["model"] = lambda: _Coordinated()
    registry._adapter_cache.pop("model", None)

    run_id = new_run_id()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    store = _initialize_store(workflow, run_dir / "store.sqlite")
    log = LogWriter(run_dir / "log.jsonl", run_id)
    log.write("run_start", fields={"workflow_path": str(src)})
    executor = Executor(
        workflow=workflow,
        registry=registry,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=run_id,
        external_inputs={"topic": "hello"},
    )

    # Releaser: wait until a is in prepare, give the controller a
    # moment to drive b through invoke -> error -> request_cancel_all,
    # then release a. This drive ordering is what the production
    # race looks like: b errors first, controller flags a, a returns
    # from prepare and is registered, post-register check fires.
    def _releaser() -> None:
        a_prepare_entered.wait(timeout=5)
        # Give the controller time to process b's error and call
        # request_cancel_all. b's invoke runs, raises, the drain
        # loop catches it and calls request_cancel_all. We then
        # release a's prepare. Polling on b's invoke completion is
        # cleaner than a fixed sleep but a small sleep is enough
        # for the test's purposes; the assertion below catches a
        # genuinely-broken implementation regardless.
        import time as _time

        _time.sleep(0.2)
        a_prepare_release.set()

    releaser = threading.Thread(target=_releaser)
    releaser.start()
    try:
        terminal = executor.run_to_completion()
    finally:
        releaser.join(timeout=5)
    log.write("run_end", fields={"terminal": terminal})
    log.close()
    store.close()

    # b is the only fan-out child whose adapter.invoke was called.
    # a was cancelled at the post-register boundary and never
    # invoked. (m_parent and m_abort run before/after the fan-out
    # and are unrelated; filter to the two fan-out child models.)
    fan_invoke_calls = [c for c in invoke_calls if c in ("m_a", "m_b")]
    assert fan_invoke_calls == ["m_b"], (
        f"expected only m_b's invoke to run among fan-out children; "
        f"got {fan_invoke_calls!r} (full: {invoke_calls!r})"
    )

    records = LogReader(run_dir / "log.jsonl").read_all()

    # a has a state_exit with outcome=cancelled (the
    # cancelled-post-register path).
    a_exits = [r for r in records if r.event == "state_exit" and r.state_id == "a"]
    assert len(a_exits) == 1
    assert a_exits[0].fields["outcome"] == "cancelled"

    # a wrote no actor_invoke_start (skipped invoke entirely).
    a_invoke_starts = [r for r in records if r.event == "actor_invoke_start" and r.state_id == "a"]
    assert a_invoke_starts == []

    # The group routes to the error target because b errored.
    fan_end = next(r for r in records if r.event == "fan_out_end")
    assert fan_end.fields["aggregate"] == "error"
    assert fan_end.fields["target"] == "abort_state"


# --------------------------------------------------------------------
# Resume of an open fan-out group (Blockers 4 + 5)
# --------------------------------------------------------------------


def _filter_log_to_open_fan_out(
    log_path: Path,
    keep_completed: list[str],
    keep_started_only: list[str],
) -> None:
    """Rewrite ``log_path`` to simulate a crash mid-fan-out.

    Keeps:
      - every record up to and including the parent's ``state_exit``
      - the ``fan_out_start`` record
      - all records whose ``state_id`` is in ``keep_completed`` (so
        that child has a durable state_enter+state_exit and any
        artifact_write records)
      - only ``state_enter`` records whose ``state_id`` is in
        ``keep_started_only`` (no exit, no artifact_write -- the
        in-flight crash window)

    Drops everything else after ``fan_out_start``: no
    ``fan_out_end``, no records for children NOT in either list, and
    no records past the cutoff.
    """
    records = LogReader(log_path).read_all()
    out: list[Any] = []
    seen_fan_start = False
    for rec in records:
        if rec.event == "fan_out_start":
            out.append(rec)
            seen_fan_start = True
            continue
        if not seen_fan_start:
            out.append(rec)
            continue
        # After fan_out_start: filter by child state_id.
        sid = rec.state_id
        if sid in keep_completed:
            out.append(rec)
        elif sid in keep_started_only:
            if rec.event == "state_enter":
                out.append(rec)
        # else: drop (other children, fan_out_end, post-fan-out states)
    # Renumber the kept records contiguously. The strict log reader
    # rejects sequence gaps as corruption; this synthetic fixture
    # filters out middle records to simulate a crash mid-fan-out, so
    # we collapse the seqs to keep the resulting log valid.
    for new_seq, rec in enumerate(out):
        rec.seq = new_seq
    log_path.write_text("\n".join(r.to_json() for r in out) + "\n", encoding="utf-8")


def _resume_open_fan_out(
    run_dir: Path,
    workflow_path: Path,
    external_inputs: dict[str, Any],
    *,
    visibility_overrides: dict[str, str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Mirror cmd_resume's open-fan-out path. Returns
    ``(terminal, visibility_snapshot_after_resume)``.

    If ``visibility_overrides`` is provided, those entries are
    written directly into the persisted ``visibility.json`` BEFORE
    resume runs, simulating a stale on-disk cache that the
    log-derived rebuild must overwrite.
    """
    from orchestra.resume import replay_log
    from orchestra.visibility import VisibilityIndex

    log_path = run_dir / "log.jsonl"
    replay = replay_log(str(log_path))

    registry = with_core()
    workflow = load_workflow(workflow_path, registry)
    store = ArtifactStore(run_dir / "store.sqlite")
    log = LogWriter(log_path, replay.last_run_id, start_seq=replay.next_seq)

    if visibility_overrides is not None:
        # Stale persisted cache (whatever it was previously) is
        # overwritten so the test scenario starts from a known-stale
        # state. The log is the source of truth; replace_from must
        # win regardless of what was on disk.
        import json as _json

        (run_dir / "visibility.json").write_text(
            _json.dumps(visibility_overrides), encoding="utf-8"
        )

    visibility_index = VisibilityIndex(persist_path=run_dir / "visibility.json")
    visibility_index.replace_from(replay.visibility_statuses)

    executor = Executor(
        workflow=workflow,
        registry=registry,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=replay.last_run_id,
        external_inputs=external_inputs,
        attempts=replay.attempts,
        retries=replay.retries,
        envelopes=replay.envelopes,
        current_state=replay.current_state,
        step_count=replay.step_count,
        visibility_index=visibility_index,
    )

    assert replay.open_fan_out is not None
    of = replay.open_fan_out
    children_list = [str(c) for c in of["children"]]
    # Mirror cli.cmd_resume's "envelope.attempt matches latest
    # state_enter" check so a child mid-retry (older state_exit
    # envelope but a newer state_enter without a matching exit) is
    # treated as pending, not completed.
    completed = {
        n: env
        for n, env in replay.envelopes.items()
        if n in children_list and env.attempt == replay.attempts.get(n)
    }
    executor.resume_fan_out(
        parent_state_name=str(of["parent_state"]),
        children=children_list,
        join_target=str(of["join_target"]),
        error_target=str(of["error_target"]),
        completed_children=completed,
        parent_attempt=replay.open_fan_out_attempt,
    )

    terminal = executor.run_to_completion()
    snapshot = visibility_index.snapshot()
    log.write("run_end", fields={"terminal": terminal})
    log.close()
    store.close()
    return terminal, dict(snapshot)


def test_resume_open_fan_out_relaunches_only_incomplete_children(
    tmp_path: Path,
) -> None:
    """A log truncated mid-fan-out (advise_a complete, advise_b
    in-flight, nothing for advise_c, no fan_out_end) is recoverable.

    Resume:
      - applies the rebuilt VisibilityIndex from the log,
      - dispatches to ``Executor.resume_fan_out``,
      - re-runs only advise_b and advise_c with fresh
        invocation_ids (new attempt_seq),
      - leaves advise_a's existing committed artifact in place
        (its state_exit is durable, so its visibility stays
        ``success`` and the per-child outcome carries over),
      - writes ``fan_out_end`` with aggregate=success and
        target=join_state,
      - lets the linear loop continue from join_state to terminal.

    Tests Blocker 4.
    """
    workflow_path = _fan_out_fixture_workflow(tmp_path)
    run_dir = _run(tmp_path)
    log_path = run_dir / "log.jsonl"
    # Strip the trailing run_end so replay's terminal-state shortcut
    # does not bypass the resume path; then truncate to mid-fan-out.
    records = LogReader(log_path).read_all()
    records = [r for r in records if r.event != "run_end"]
    log_path.write_text("\n".join(r.to_json() for r in records) + "\n", encoding="utf-8")
    _filter_log_to_open_fan_out(
        log_path,
        keep_completed=["advise_a"],
        keep_started_only=["advise_b"],
    )

    terminal, _ = _resume_open_fan_out(run_dir, workflow_path, {"topic": "hello world"})
    assert terminal == "done"

    records = LogReader(log_path).read_all()

    # advise_a was NOT re-run: it has exactly one state_enter (the
    # original) and one state_exit (the original), both with
    # attempt_seq 1.
    a_enters = [r for r in records if r.event == "state_enter" and r.state_id == "advise_a"]
    a_exits = [r for r in records if r.event == "state_exit" and r.state_id == "advise_a"]
    assert len(a_enters) == 1
    assert len(a_exits) == 1
    a_inv = a_enters[0].fields.get("invocation_id")
    assert isinstance(a_inv, str)
    assert a_inv.endswith("::advise_a::1")

    # advise_b was re-run: the original state_enter (attempt 1) is
    # still in the log, and a fresh state_enter+state_exit pair was
    # appended with attempt 2 carrying a different invocation_id.
    b_enters = [r for r in records if r.event == "state_enter" and r.state_id == "advise_b"]
    b_exits = [r for r in records if r.event == "state_exit" and r.state_id == "advise_b"]
    assert len(b_enters) == 2
    assert len(b_exits) == 1
    b_inv_ids = {r.fields.get("invocation_id") for r in b_enters}
    assert any(str(inv).endswith("::advise_b::1") for inv in b_inv_ids)
    assert any(str(inv).endswith("::advise_b::2") for inv in b_inv_ids)
    # The state_exit's invocation_id is the new one (attempt 2).
    assert str(b_exits[0].fields.get("invocation_id")).endswith("::advise_b::2")

    # advise_c was run for the first time on resume: one state_enter
    # and one state_exit. After Cleanup 1, attempt_seq is minted at
    # state_enter time (not pre-seeded by the controller), so a
    # never-entered pending child gets attempt_seq=1 on its first
    # entry rather than an inflated counter.
    c_enters = [r for r in records if r.event == "state_enter" and r.state_id == "advise_c"]
    c_exits = [r for r in records if r.event == "state_exit" and r.state_id == "advise_c"]
    assert len(c_enters) == 1
    assert len(c_exits) == 1
    c_inv = str(c_enters[0].fields.get("invocation_id"))
    assert c_inv.endswith("::advise_c::1"), (
        f"after Cleanup 1 advise_c should mint attempt 1 on first entry; got {c_inv}"
    )
    assert c_inv == str(c_exits[0].fields.get("invocation_id"))

    # fan_out_end is now durable; aggregate is success and the
    # group routes to join_state.
    fan_ends = [r for r in records if r.event == "fan_out_end"]
    assert len(fan_ends) == 1
    assert fan_ends[0].fields["aggregate"] == "success"
    assert fan_ends[0].fields["target"] == "join_state"

    # The linear loop continued past the join target to terminal.
    join_exits = [r for r in records if r.event == "state_exit" and r.state_id == "join_state"]
    assert len(join_exits) == 1


def test_resume_visibility_log_wins_over_persisted_json(
    tmp_path: Path,
) -> None:
    """The persisted ``visibility.json`` is a best-effort cache; the
    log is the source of truth. ``cmd_resume`` calls
    ``VisibilityIndex.replace_from(replay.visibility_statuses)``
    BEFORE constructing the Executor, so any stale entries in the
    persisted file are overwritten by the log-derived rebuild.

    Tests Blocker 5.
    """
    workflow_path = _fan_out_fixture_workflow(tmp_path)
    run_dir = _run(tmp_path)
    log_path = run_dir / "log.jsonl"
    records = LogReader(log_path).read_all()
    records = [r for r in records if r.event != "run_end"]
    log_path.write_text("\n".join(r.to_json() for r in records) + "\n", encoding="utf-8")
    # Truncate to mid-fan-out so resume actually runs (terminal logs
    # bypass the resume path entirely).
    _filter_log_to_open_fan_out(
        log_path,
        keep_completed=["advise_a"],
        keep_started_only=["advise_b"],
    )

    # advise_a's invocation_id is in the truncated log as success.
    records = LogReader(log_path).read_all()
    a_exit = next(r for r in records if r.event == "state_exit" and r.state_id == "advise_a")
    a_inv = str(a_exit.fields["invocation_id"])

    # Stale cache: claim advise_a errored AND introduce a phantom
    # invocation_id with success that is not in the log at all.
    stale = {
        a_inv: "error",  # log says success; stale says error
        "phantom-run::ghost::1": "success",
    }
    terminal, snapshot = _resume_open_fan_out(
        run_dir,
        workflow_path,
        {"topic": "hello world"},
        visibility_overrides=stale,
    )
    assert terminal == "done"

    # The log wins: advise_a is success in the in-memory snapshot
    # (taken AFTER resume) and the phantom entry is gone.
    assert snapshot.get(a_inv) == "success"
    assert "phantom-run::ghost::1" not in snapshot


def test_resume_pending_child_does_not_see_completed_sibling_output(
    tmp_path: Path,
) -> None:
    """The fan-out invariant: no child sees any sibling's output,
    regardless of when the sibling completed. After
    ``cli.cmd_resume`` applies the log-derived VisibilityIndex, the
    completed sibling's invocation is marked ``success`` and its
    committed artifacts are visible to ``read_latest`` per the
    visibility rule. ``resume_fan_out``'s reconstructed snapshot
    must EXCLUDE those artifacts so a pending child re-entered
    after the crash still sees only the pre-fan-out state, not the
    completed sibling's output.

    Tests Re-audit Blocker 1.
    """
    import threading

    # Use the sibling-visibility fixture: parent ``frame`` writes
    # ``frame_out`` before the fan-out; ``fast`` writes ``fast_out``
    # quickly; ``slow`` declares ``reads frame_out, fast_out``.
    # Run, then truncate so ``fast`` is durable-success and ``slow``
    # is unstarted; resume; assert ``slow`` sees frame_out (parent,
    # pre-fan-out) but NOT fast_out (sibling).
    _write_dummy_template(tmp_path)
    src = tmp_path / "fan.orc"
    src.write_text(
        """spec 0.1
workflow sib_resume
  external_input topic text
  max_total_steps 30
  model m_parent
  model m_fast
  model m_slow
  model m_join
  model m_abort
  artifact frame_out text
  artifact fast_out text
  artifact slow_out text
  artifact joined text
  artifact aborted text
  role parent_role
    prompt template "templates/dummy.md"
  role fast_role
    prompt template "templates/dummy.md"
  role slow_role
    prompt template "templates/dummy.md"
  role joiner
    prompt template "templates/dummy.md"
  role aborter
    prompt template "templates/dummy.md"
  state frame
    actor model m_parent
    role parent_role
    reads topic
    writes frame_out text
    on complete fan_out [fast, slow] join join_state on error abort_state
    on error => stop
    on timeout => stop
  state fast
    actor model m_fast
    role fast_role
    reads frame_out
    writes fast_out text
    on complete => done
    on error => stop
    on timeout => stop
  state slow
    actor model m_slow
    role slow_role
    reads frame_out, fast_out
    writes slow_out text
    on complete => done
    on error => stop
    on timeout => stop
  state join_state
    actor model m_join
    role joiner
    reads fast_out, slow_out
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

    # Step 1: run the workflow to completion so a real durable log
    # exists with both fast and slow's commits. We will then
    # truncate to simulate a crash with fast complete and slow not.
    registry = with_core()
    # Same sibling-read shape as test_fan_out_sibling_reads_use_snapshot_not_live_store:
    # ``slow`` reads ``fast_out`` and the dominator check rejects it.
    # The executor still tolerates the read via snapshot isolation,
    # which is what this test verifies on the resume path; parse
    # without validating to bypass the dominator rule while keeping
    # the executor-level invariant pinned.
    workflow = _parse_only(src)

    run_id = new_run_id()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    store = _initialize_store(workflow, run_dir / "store.sqlite")
    log = LogWriter(run_dir / "log.jsonl", run_id)
    log.write("run_start", fields={"workflow_path": str(src)})
    executor = Executor(
        workflow=workflow,
        registry=registry,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=run_id,
        external_inputs={"topic": "hello"},
    )
    executor.run_to_completion()
    log.close()
    store.close()

    log_path = run_dir / "log.jsonl"
    # Truncate: keep fast (success) complete, drop slow's records,
    # drop fan_out_end and everything after.
    _filter_log_to_open_fan_out(
        log_path,
        keep_completed=["fast"],
        keep_started_only=[],
    )

    # Step 2: capture what slow sees on resume by recording every
    # prepared invocation's reads dict from the worker's
    # InvocationRequest (which is what _read_artifacts populated).
    from orchestra.adapters.mock_model import MockModelAdapter

    slow_request_reads: dict[str, dict[str, Any]] = {}

    class _Recording(MockModelAdapter):
        def invoke(self, prepared: Any) -> dict[str, Any]:
            model_id = prepared.summary.get("model")
            if model_id == "m_slow":
                slow_request_reads.update(prepared.request.reads)
            return super().invoke(prepared)

    # Step 3: also wrap read_latest to confirm that during slow's
    # resume invocation the worker NEVER hits the live store for
    # fast_out (which would otherwise be visible because fast's
    # invocation_id is success in the rebuilt index).
    # Same sibling-read shape; bypass validate as in the live-path
    # load above.
    workflow_resume = _parse_only(src)
    resume_registry = with_core()
    resume_registry.actor_backings["model"] = lambda: _Recording()
    resume_registry._adapter_cache.pop("model", None)

    from orchestra.resume import replay_log
    from orchestra.visibility import VisibilityIndex

    replay = replay_log(str(log_path))

    resume_store = ArtifactStore(run_dir / "store.sqlite")
    # Per-thread call counter on read_latest (TEST GAP 1 style).
    worker_reads: dict[str, int] = {}
    rl_lock = threading.Lock()
    real_read_latest = resume_store.read_latest

    def _wrapped_read_latest(name: str) -> Any:
        if threading.current_thread().name.startswith("orchestra-fan-out"):
            with rl_lock:
                worker_reads[name] = worker_reads.get(name, 0) + 1
        return real_read_latest(name)

    resume_store.read_latest = _wrapped_read_latest  # type: ignore[method-assign]

    resume_log = LogWriter(log_path, replay.last_run_id, start_seq=replay.next_seq)
    visibility_index = VisibilityIndex(persist_path=run_dir / "visibility.json")
    visibility_index.replace_from(replay.visibility_statuses)

    # Sanity: the visibility index marks fast's invocation as
    # success, so a naive ``read_latest("fast_out")`` would return
    # the committed value (we are exercising the path the bug
    # makes vulnerable).
    fast_inv_id = next(
        inv
        for inv, status in replay.visibility_statuses.items()
        if "::fast::" in inv and status == "success"
    )
    assert visibility_index.status(fast_inv_id) == "success"
    naive_fast_read = real_read_latest("fast_out")
    assert naive_fast_read is not None and naive_fast_read.value is not None

    resume_executor = Executor(
        workflow=workflow_resume,
        registry=resume_registry,
        store=resume_store,
        log=resume_log,
        run_dir=run_dir,
        run_id=replay.last_run_id,
        external_inputs={"topic": "hello"},
        attempts=replay.attempts,
        retries=replay.retries,
        envelopes=replay.envelopes,
        current_state=replay.current_state,
        step_count=replay.step_count,
        visibility_index=visibility_index,
    )

    assert replay.open_fan_out is not None
    of = replay.open_fan_out
    children_list = [str(c) for c in of["children"]]
    completed = {n: env for n, env in replay.envelopes.items() if n in children_list}
    resume_executor.resume_fan_out(
        parent_state_name=str(of["parent_state"]),
        children=children_list,
        join_target=str(of["join_target"]),
        error_target=str(of["error_target"]),
        completed_children=completed,
    )
    resume_executor.run_to_completion()
    resume_log.close()
    resume_store.close()

    # frame_out is visible (parent ran before fan-out; not a
    # completed sibling of THIS fan-out group's children).
    assert slow_request_reads["frame_out"]["value"] is not None
    assert slow_request_reads["frame_out"]["__version_id"] == "snapshot"
    # fast_out is NOT visible: fast is a completed sibling of this
    # group, and the fix excludes it from the reconstructed
    # snapshot. Without the fix, slow would see fast's committed
    # value here.
    assert slow_request_reads["fast_out"]["value"] is None, (
        f"slow saw fast_out's committed value, violating the "
        f"sibling-visibility rule on resume; got "
        f"{slow_request_reads['fast_out']!r}"
    )
    assert slow_request_reads["fast_out"]["__version_id"] == ""

    # No worker thread hit the live store: snapshot threading still
    # holds across the resume path.
    assert worker_reads == {}, (
        f"no fan-out worker should hit the live store on resume; got {worker_reads!r}"
    )


def test_attempt_seq_minted_at_state_enter(tmp_path: Path) -> None:
    """Per the plan's "Invocation identity" subsection,
    ``attempt_seq`` is a monotonic counter incremented on every
    entry and re-entry of a state, minted at ``state_enter`` time.
    Pre-Cleanup-1 the fan-out controller pre-seeded ``_attempts``
    for every child at submission time. A pending child that
    never entered (because of a crash before its turn) saw its
    counter inflated even though no ``state_enter`` was logged for
    it. On resume, the counter would be re-incremented from the
    inflated value, so a never-entered child resumed at
    ``attempt_seq=2`` -- a different number than the one true entry
    it had seen.

    After Cleanup 1, the increment moves into the reentrant
    ``_execute_state_body`` immediately before the ``state_enter``
    log write. A never-entered child has no counter bump in the
    log, replay does not seed it, and resume's first entry mints
    ``attempt_seq=1``.

    Tests Cleanup 1.
    """
    # Setup: run the 3-child fan-out fixture normally, then truncate
    # the log to keep only advise_a's full trace (success). advise_b
    # and advise_c never entered in the truncated log; resume must
    # mint their attempt_seq=1.
    workflow_path = _fan_out_fixture_workflow(tmp_path)
    run_dir = _run(tmp_path)
    log_path = run_dir / "log.jsonl"
    records = LogReader(log_path).read_all()
    records = [r for r in records if r.event != "run_end"]
    log_path.write_text("\n".join(r.to_json() for r in records) + "\n", encoding="utf-8")
    _filter_log_to_open_fan_out(
        log_path,
        keep_completed=["advise_a"],
        keep_started_only=[],
    )

    terminal, _ = _resume_open_fan_out(run_dir, workflow_path, {"topic": "hello world"})
    assert terminal == "done"

    records = LogReader(log_path).read_all()

    # advise_a entered exactly once (first run); attempt_seq=1.
    a_enters = [r for r in records if r.event == "state_enter" and r.state_id == "advise_a"]
    assert len(a_enters) == 1
    a_inv = str(a_enters[0].fields["invocation_id"])
    assert a_inv.endswith("::advise_a::1"), (
        f"advise_a's only entry should be attempt 1; got {a_inv}"
    )

    # advise_b never entered in the truncated log. After resume, its
    # first (and only) entry is attempt 1, NOT attempt 2 (which is
    # what controller pre-seeding would have produced).
    b_enters = [r for r in records if r.event == "state_enter" and r.state_id == "advise_b"]
    assert len(b_enters) == 1
    b_inv = str(b_enters[0].fields["invocation_id"])
    assert b_inv.endswith("::advise_b::1"), (
        f"advise_b's resumed entry should mint attempt 1; got {b_inv}"
    )

    # advise_c same as advise_b: never entered before crash, attempt
    # 1 on resume.
    c_enters = [r for r in records if r.event == "state_enter" and r.state_id == "advise_c"]
    assert len(c_enters) == 1
    c_inv = str(c_enters[0].fields["invocation_id"])
    assert c_inv.endswith("::advise_c::1"), (
        f"advise_c's resumed entry should mint attempt 1; got {c_inv}"
    )


def test_resume_open_fan_out_with_errored_completed_child_does_not_launch_pending(
    tmp_path: Path,
) -> None:
    """When a fan-out group's log shows one child completed success
    and another completed error before the crash, the group has
    already failed: the cancellation race rule fixes the routing
    decision at error. Resume must NOT launch new invocations for
    the still-pending child during replay; doing so would create new
    fan-out invocations of an already-failed group.

    The fix short-circuits inside ``resume_fan_out`` when any
    completed child has an error status: pending children are
    flagged ``not_launched`` in ``per_child_outcome``, the cleanup
    pass runs, ``fan_out_end`` is written with aggregate=error,
    and the group routes to the error target.

    Tests Re-audit Blocker 2.
    """
    workflow_path = _fan_out_fixture_workflow(tmp_path)
    registry = with_core()
    workflow = load_workflow(workflow_path, registry)
    # Inject an adapter that errors on advise_b's model so the fan
    # out completes with one success and one error before our
    # truncation cuts it off.
    from orchestra.adapters.mock_model import MockModelAdapter

    class _ErrorOnB:
        backing = "model"

        def prepare(self, request: Any) -> Any:
            return MockModelAdapter().prepare(request)

        def invoke(self, prepared: Any) -> dict[str, Any]:
            model_id = prepared.summary.get("model")
            if model_id == "m_b":
                raise RuntimeError("synthetic adapter failure for advise_b")
            return MockModelAdapter().invoke(prepared)

        def cancel(self, prepared: Any) -> None:
            return None

        def describe(self) -> dict[str, Any]:
            return {"backing": "model"}

    registry.actor_backings["model"] = lambda: _ErrorOnB()
    registry._adapter_cache.pop("model", None)

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
    executor.run_to_completion()
    log.close()
    store.close()

    log_path = run_dir / "log.jsonl"
    # Truncate to mid-fan-out: keep advise_a (success) and advise_b
    # (error) complete; drop advise_c entirely and drop fan_out_end
    # plus everything after.
    _filter_log_to_open_fan_out(
        log_path,
        keep_completed=["advise_a", "advise_b"],
        keep_started_only=[],
    )

    # Resume: the fix should observe advise_b's error in
    # completed_children and short-circuit BEFORE submitting any
    # future for advise_c.
    terminal, _ = _resume_open_fan_out(run_dir, workflow_path, {"topic": "hello world"})

    # The run reaches a terminal state via the abort_state branch.
    assert terminal in ("done", "stop")

    records = LogReader(log_path).read_all()

    # advise_c was NOT launched: there is no fresh state_enter for
    # advise_c added by the resume path. (The truncated log had no
    # advise_c records to begin with.)
    c_enters = [r for r in records if r.event == "state_enter" and r.state_id == "advise_c"]
    assert c_enters == [], (
        f"advise_c should not have been launched on resume; got state_enters {c_enters}"
    )

    # advise_a and advise_b retain exactly their original
    # state_enter / state_exit pair (no resume re-entry of either).
    for completed in ("advise_a", "advise_b"):
        enters = [r for r in records if r.event == "state_enter" and r.state_id == completed]
        exits = [r for r in records if r.event == "state_exit" and r.state_id == completed]
        assert len(enters) == 1
        assert len(exits) == 1

    # fan_out_end records aggregate=error, routes to abort_state,
    # and the per_child_outcome map carries a=success, b=error,
    # c=not_launched.
    fan_ends = [r for r in records if r.event == "fan_out_end"]
    assert len(fan_ends) == 1
    fan_end = fan_ends[0]
    assert fan_end.fields["aggregate"] == "error"
    assert fan_end.fields["target"] == "abort_state"
    per_child = fan_end.fields["per_child_outcome"]
    assert per_child.get("advise_a") == "success"
    assert per_child.get("advise_b") == "error"
    assert per_child.get("advise_c") == "not_launched"


def test_discard_stale_tentatives_respects_fk(tmp_path: Path) -> None:
    """``Executor._discard_stale_tentatives`` deletes tentative rows
    left over from a crashed prior attempt. The store has
    ``PRAGMA foreign_keys=ON``, and ``tentative_handles.seq``
    references ``versions.seq``: deleting ``versions`` first violates
    the FK and raises IntegrityError. The fix mirrors
    ``store.discard_tentative``: delete the handle row first, then
    the version row.

    Tests Follow-up 1.
    """
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
        external_inputs={"topic": "hello"},
    )

    # Stage a tentative write attributed to advise_a's first attempt
    # but never commit or discard. This simulates a crash that
    # interrupted between ``tentative_write`` and ``commit_tentative``.
    store.tentative_write(
        "a_out",
        "tentative leftover",
        written_by="advise_a#1",
        invocation_id=f"{run_id}::advise_a::1",
    )

    # Sanity: the tentative row is staged.
    cur = store._conn.cursor()
    pre_versions = cur.execute(
        "SELECT seq FROM versions WHERE is_tentative = 1 AND written_by LIKE 'advise_a#%'"
    ).fetchall()
    assert len(pre_versions) == 1
    pre_handles = cur.execute("SELECT seq FROM tentative_handles").fetchall()
    assert len(pre_handles) == 1

    # The discard must not raise: under FK=ON, the wrong delete
    # order would trip ``IntegrityError``. The fix mirrors
    # store.discard_tentative's handle-first-then-version order.
    executor._discard_stale_tentatives("advise_a")

    # Both rows are gone.
    post_versions = cur.execute(
        "SELECT seq FROM versions WHERE is_tentative = 1 AND written_by LIKE 'advise_a#%'"
    ).fetchall()
    assert len(post_versions) == 0
    post_handles = cur.execute("SELECT seq FROM tentative_handles").fetchall()
    assert len(post_handles) == 0

    log.close()
    store.close()


# ====================================================================
# Round-3 regressions: resume writes parent transition after
# fan_out_end, and parent_attempt is preserved through resume.
# ====================================================================


class _DenyChildrenAdapter:
    """Wraps MockModelAdapter; raises if any state in ``denied`` is
    asked to prepare or invoke. Used to prove resume does not
    re-dispatch fan-out children when the parent transition is the
    only thing missing."""

    def __init__(self, denied: set[str]) -> None:
        from orchestra.adapters.mock_model import MockModelAdapter

        self._inner = MockModelAdapter()
        self.denied = set(denied)
        self.invocations: list[str] = []

    def prepare(self, request: Any) -> Any:
        if request.state_id in self.denied:
            raise AssertionError(f"state {request.state_id!r} must not be re-invoked on resume")
        self.invocations.append(f"prepare:{request.state_id}")
        return self._inner.prepare(request)

    def invoke(self, prepared: Any) -> Any:
        sid = prepared.request.state_id
        if sid in self.denied:
            raise AssertionError(f"state {sid!r} must not be re-invoked on resume")
        self.invocations.append(f"invoke:{sid}")
        return self._inner.invoke(prepared)

    def cancel(self, prepared: Any) -> None:
        return None

    def describe(self) -> dict[str, Any]:
        return self._inner.describe()


def _registry_with_deny(deny: _DenyChildrenAdapter) -> Any:
    from orchestra.adapters.mock_human import MockHumanAdapter as _Human
    from orchestra.adapters.mock_shell import MockShellAdapter as _Shell
    from orchestra.executor.parsers import identity_text_parser
    from orchestra.registry.registry import ProfileRegistry

    reg = ProfileRegistry()
    for type_name in ("text", "json", "messages", "prompt", "schema", "document"):
        reg.register_artifact_type(type_name)
    reg.register_actor_backing("model", lambda: deny)
    reg.register_actor_backing("human", _Human)
    reg.register_actor_backing("shell", _Shell)
    reg.register_result_parser(identity_text_parser)
    return reg


def _resume_with_registry(
    run_dir: Path,
    workflow_path: Path,
    registry: Any,
    external_inputs: dict[str, Any],
) -> str:
    """Mirror cmd_resume's full machinery using the supplied registry
    and the new ``pending_fan_out_transition`` plus ``parent_attempt``
    threading."""
    from orchestra.resume import replay_log, run_resume_hooks
    from orchestra.visibility import VisibilityIndex

    log_path = run_dir / "log.jsonl"
    replay = replay_log(str(log_path))
    workflow = load_workflow(workflow_path, registry)
    store = ArtifactStore(run_dir / "store.sqlite")
    log = LogWriter(log_path, replay.last_run_id, start_seq=replay.next_seq)
    run_resume_hooks(workflow, registry, replay, log)
    vi = VisibilityIndex(persist_path=run_dir / "visibility.json")
    vi.replace_from(replay.visibility_statuses)
    executor = Executor(
        workflow=workflow,
        registry=registry,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=replay.last_run_id,
        external_inputs=external_inputs,
        attempts=replay.attempts,
        retries=replay.retries,
        envelopes=replay.envelopes,
        current_state=replay.current_state,
        step_count=replay.step_count,
        visibility_index=vi,
    )
    try:
        if (
            replay.state_exit_without_transition
            and replay.current_state is not None
            and replay.current_state not in {"done", "stop"}
        ):
            executor.resume_pending_transition(replay.current_state)
        if replay.pending_fan_out_transition is not None and replay.open_fan_out is None:
            pft = replay.pending_fan_out_transition
            executor.close_fan_out_pending_transition(
                parent_state_name=str(pft["parent_state"]),
                parent_attempt=int(pft["attempt"]),
                target=str(pft["target"]),
            )
        if replay.open_fan_out is not None:
            of = replay.open_fan_out
            children_field = of.get("children") or []
            if not isinstance(children_field, list):
                children_field = []
            children_list = [str(c) for c in children_field]
            completed = {
                name: env
                for name, env in replay.envelopes.items()
                if (name in children_list and env.attempt == replay.attempts.get(name))
            }
            executor.resume_fan_out(
                parent_state_name=str(of.get("parent_state", "")),
                children=children_list,
                join_target=str(of.get("join_target", "")),
                error_target=str(of.get("error_target", "")),
                completed_children=completed,
                parent_attempt=replay.open_fan_out_attempt,
            )
        terminal = executor.run_to_completion()
    finally:
        log.close()
        store.close()
    return terminal


def test_resume_after_fan_out_end_writes_missing_parent_transition(
    tmp_path: Path,
) -> None:
    """Crash window between live ``fan_out_end`` and the parent
    ``transition``: resume must close the missing transition record
    without re-dispatching the fan-out children. The durable
    routing decision is in ``fan_out_end``; resume just needs to
    write the matching transition.
    """
    run_dir = _run(tmp_path)
    log_path = run_dir / "log.jsonl"

    # Truncate everything from the parent's transition onward. We
    # keep run_start, all fan-out and child records, and fan_out_end.
    records = _read_records(run_dir)
    cutoff: int | None = None
    seen_fan_out_end = False
    for i, r in enumerate(records):
        if r.event == "fan_out_end":
            seen_fan_out_end = True
            continue
        if seen_fan_out_end and r.event == "transition" and r.state_id == "launch":
            cutoff = i
            break
    assert cutoff is not None, "the fixture must emit a parent transition after fan_out_end"
    truncated = records[:cutoff]
    log_path.write_text(
        "\n".join(r.to_json() for r in truncated) + "\n",
        encoding="utf-8",
    )

    from orchestra.resume import replay_log

    replay = replay_log(str(log_path))
    assert replay.pending_fan_out_transition is not None
    pft = replay.pending_fan_out_transition
    assert pft["parent_state"] == "launch"
    assert pft["target"] == "join_state"
    assert isinstance(pft["attempt"], int) and pft["attempt"] >= 1

    deny = _DenyChildrenAdapter(denied={"launch", "advise_a", "advise_b", "advise_c"})
    reg2 = _registry_with_deny(deny)
    workflow_path = run_dir.parent / "fan.orc"
    terminal = _resume_with_registry(
        run_dir,
        workflow_path,
        reg2,
        {"topic": "hello world"},
    )
    assert terminal == "done"
    # No fan-out child or parent invocation should have run.
    assert all(not s.startswith("prepare:advise_") for s in deny.invocations)
    assert all(not s.startswith("invoke:advise_") for s in deny.invocations)
    assert "prepare:launch" not in deny.invocations

    records2 = _read_records(run_dir)
    launch_transitions = [r for r in records2 if r.event == "transition" and r.state_id == "launch"]
    assert len(launch_transitions) == 1, "resume must write exactly one parent transition record"
    assert launch_transitions[0].fields["target"] == "join_state"
    # The closure transition's attempt matches fan_out_end's attempt.
    fan_out_end = next(r for r in records2 if r.event == "fan_out_end")
    assert launch_transitions[0].attempt == fan_out_end.attempt


def test_resume_open_fan_out_writes_parent_transition_with_correct_attempt(
    tmp_path: Path,
) -> None:
    """Resume of an open fan-out group ends with the parent's
    ``transition`` record carrying the same attempt as the original
    ``fan_out_start``. Round-3 fix: the previous code lost
    ``parent_attempt`` so resumed ``fan_out_end`` and (now-added)
    transition records carried ``attempt=None``. The expected log
    sequence is fan_out_start, fan_out_resume, child events,
    fan_out_end, transition.
    """
    run_dir = _run(tmp_path)
    log_path = run_dir / "log.jsonl"

    # Truncate the log so fan_out_start is durable but no children
    # have completed (drop everything after fan_out_start).
    records = _read_records(run_dir)
    cutoff = next(i for i, r in enumerate(records) if r.event == "fan_out_start")
    truncated = records[: cutoff + 1]
    fan_out_start_attempt = records[cutoff].attempt
    assert isinstance(fan_out_start_attempt, int)
    log_path.write_text(
        "\n".join(r.to_json() for r in truncated) + "\n",
        encoding="utf-8",
    )

    workflow_path = run_dir.parent / "fan.orc"
    registry = with_core()
    terminal = _resume_with_registry(
        run_dir,
        workflow_path,
        registry,
        {"topic": "hello world"},
    )
    assert terminal == "done"

    records2 = _read_records(run_dir)

    # The expected sequence around the resumed fan-out group:
    # fan_out_start, fan_out_resume, child events..., fan_out_end,
    # transition (parent).
    fan_starts = [r for r in records2 if r.event == "fan_out_start"]
    fan_resumes = [r for r in records2 if r.event == "fan_out_resume"]
    fan_ends = [r for r in records2 if r.event == "fan_out_end"]
    parent_transitions = [r for r in records2 if r.event == "transition" and r.state_id == "launch"]
    assert len(fan_starts) == 1
    assert len(fan_resumes) == 1
    assert len(fan_ends) == 1
    assert len(parent_transitions) == 1

    # All four records carry the same parent attempt.
    assert fan_starts[0].attempt == fan_out_start_attempt
    assert fan_resumes[0].attempt == fan_out_start_attempt
    assert fan_ends[0].attempt == fan_out_start_attempt
    assert parent_transitions[0].attempt == fan_out_start_attempt

    # Order: fan_out_start < fan_out_resume < fan_out_end < transition.
    assert fan_starts[0].seq < fan_resumes[0].seq
    assert fan_resumes[0].seq < fan_ends[0].seq
    assert fan_ends[0].seq < parent_transitions[0].seq


def test_resume_after_fan_out_end_does_not_double_count_parent_step(
    tmp_path: Path,
) -> None:
    """The parent's transition closes the same step the original
    execution would have closed, not a new step. After resume, the
    final ``transition.step_count`` must match what the live path
    would have written (2 for the standard fixture: launch is step 1,
    join_state is step 2).
    """
    run_dir = _run(tmp_path)
    log_path = run_dir / "log.jsonl"

    # First record the live-path step counts as the contract.
    live_records = _read_records(run_dir)
    live_transitions = [r for r in live_records if r.event == "transition"]
    live_step_counts = [int(r.fields["step_count"]) for r in live_transitions]
    assert live_step_counts == sorted(live_step_counts), (
        "the live path's step_counts must be monotonically increasing"
    )
    assert live_step_counts[0] >= 1

    # Truncate after fan_out_end (drop launch's transition and
    # everything after).
    cutoff: int | None = None
    seen_end = False
    for i, r in enumerate(live_records):
        if r.event == "fan_out_end":
            seen_end = True
            continue
        if seen_end and r.event == "transition" and r.state_id == "launch":
            cutoff = i
            break
    assert cutoff is not None
    log_path.write_text(
        "\n".join(r.to_json() for r in live_records[:cutoff]) + "\n",
        encoding="utf-8",
    )

    workflow_path = run_dir.parent / "fan.orc"
    registry = with_core()
    terminal = _resume_with_registry(
        run_dir,
        workflow_path,
        registry,
        {"topic": "hello world"},
    )
    assert terminal == "done"

    resumed_records = _read_records(run_dir)
    resumed_transitions = [r for r in resumed_records if r.event == "transition"]
    resumed_step_counts = [int(r.fields["step_count"]) for r in resumed_transitions]
    # The launch transition that resume wrote must close the same
    # step the live launch transition closed: step_counts identical.
    assert resumed_step_counts == live_step_counts, (
        f"step counts diverged: live={live_step_counts}, "
        f"resumed={resumed_step_counts}; resume must not double-count"
    )


def test_fan_out_payload_files_do_not_collide_under_barrier(
    tmp_path: Path,
) -> None:
    """Three children released simultaneously must each land in a
    distinct payload file, even when their actor invocations finish
    at the same instant.

    The pre-fix implementation derived the payload filename from
    ``LogWriter.next_seq``. Two children that reached the
    actor_invoke_end window concurrently could both read the same
    seq value, both write ``<run_id>-<seq>.json``, and let the
    later write clobber the earlier one. The two log records still
    landed under the writer's lock with distinct seqs, so the log
    looked clean while one child's payload had been overwritten.

    This test forces the concurrency by holding all three children at
    a barrier inside ``invoke()`` and releasing them as a group, so
    the actor_invoke_end / payload_write window overlaps. The
    assertions cover the durable artifacts a guard or replay would
    consult: distinct filenames, distinct file contents, and three
    state_exit records each pointing at a payload file that exists
    and contains that child's payload.
    """
    import json
    import threading

    from orchestra.adapters.mock_model import MockModelAdapter

    workflow_path = _fan_out_fixture_workflow(tmp_path)
    registry = with_core()
    workflow = load_workflow(workflow_path, registry)

    barrier = threading.Barrier(3)
    invoke_index = 0
    invoke_index_lock = threading.Lock()

    class _Barrier(MockModelAdapter):
        def invoke(self, prepared: Any) -> dict[str, Any]:
            nonlocal invoke_index
            model_id = prepared.summary.get("model")
            if model_id in {"m_a", "m_b", "m_c"}:
                # Stamp a unique marker so we can prove which child
                # owns which payload file after the run.
                with invoke_index_lock:
                    my_idx = invoke_index
                    invoke_index += 1
                # Block all three children at the barrier so their
                # post-invoke write window opens at the same instant
                # in three threads.
                barrier.wait(timeout=10)
                base = super().invoke(prepared)
                base["_barrier_marker"] = f"{model_id}::{my_idx}"
                # The marker key starts with ``_`` and is therefore
                # stripped by ``write_payload`` before it lands on
                # disk. Use a non-internal key for the durable check.
                base["barrier_marker"] = f"{model_id}::{my_idx}"
                return base
            return super().invoke(prepared)

    registry.actor_backings["model"] = lambda: _Barrier()
    registry._adapter_cache.pop("model", None)

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
    assert terminal == "done"

    records = LogReader(run_dir / "log.jsonl").read_all()
    child_exits = [
        r
        for r in records
        if r.event == "state_exit" and r.state_id in {"advise_a", "advise_b", "advise_c"}
    ]
    assert len(child_exits) == 3, (
        f"expected one state_exit per child, got "
        f"{[(r.state_id, r.fields.get('outcome')) for r in child_exits]}"
    )

    payload_refs: dict[str, Any] = {}
    for record in child_exits:
        assert record.state_id is not None
        payload_refs[record.state_id] = record.fields["payload_ref"]
    assert len(set(payload_refs.values())) == 3, (
        f"payload_refs collided across children: {payload_refs}"
    )
    for state_id, ref in payload_refs.items():
        assert isinstance(ref, str) and ref
        path = run_dir / ref
        assert path.exists(), f"{state_id}: payload file missing at {path}"
        body = json.loads(path.read_text(encoding="utf-8"))
        marker = body.get("barrier_marker")
        assert isinstance(marker, str)
        # Marker prefix encodes the model_id; the model_id maps 1-to-1
        # to a child name, so we can pin which payload belongs to
        # which child.
        expected_model = {
            "advise_a": "m_a",
            "advise_b": "m_b",
            "advise_c": "m_c",
        }[state_id]
        assert marker.startswith(expected_model + "::"), (
            f"{state_id}: payload at {ref} carries marker {marker!r}, "
            f"expected one starting with {expected_model!r} "
            f"(payload was overwritten by a concurrent sibling)"
        )


def test_fan_out_child_guard_reads_attempts_from_snapshot_not_live(
    tmp_path: Path,
) -> None:
    """Pass-6 fix: a fan-out child guard that references
    ``attempts.<sibling>`` must read the snapshot value captured at
    fan_out_start, not the live ``self._attempts`` dict that
    sibling threads are mutating.

    Pre-fix, _select_transition_decl used live self._attempts /
    self._retries / self._envelopes for guard evaluation even when a
    snapshot was supplied; only artifacts came from the snapshot. So
    ``on error when attempts.slow > 0 => stop`` followed by
    ``on error retry max 1 then stop`` would route or retry based
    on whether the sibling thread had already entered ``slow`` and
    incremented ``self._attempts['slow']``. Post-fix the snapshot
    holds attempts/retries as well, and the current child's own
    counters are layered on top so self-references still work.

    Construct a synthetic FanOutSnapshot with attempts.slow == 0
    while the live executor's _attempts['slow'] is 5; the guard
    evaluation must see 0.
    """
    from orchestra.executor.executor import (
        Executor,
        FanOutSnapshot,
        new_run_id,
    )
    from orchestra.spine import Envelope

    _write_dummy_template(tmp_path)
    src = tmp_path / "fan.orc"
    src.write_text(
        """spec 0.1
workflow guard_check
  external_input topic text
  max_total_steps 10
  model m_parent
  model m_fast
  model m_slow
  model m_join
  model m_abort
  artifact parent_out text
  artifact fast_out text
  artifact slow_out text
  artifact joined text
  artifact aborted text
  role r
    prompt template "templates/dummy.md"
  state launch
    actor model m_parent
    role r
    reads topic
    writes parent_out text
    on complete fan_out [fast, slow] join join_state on error abort_state
    on error => stop
    on timeout => stop
  state fast
    actor model m_fast
    role r
    reads topic
    writes fast_out text
    on complete => done
    on error when attempts.slow > 0 => stop
    on error retry max 1 then stop
    on timeout => stop
  state slow
    actor model m_slow
    role r
    reads topic
    writes slow_out text
    on complete => done
    on error => stop
    on timeout => stop
  state join_state
    actor model m_join
    role r
    reads fast_out, slow_out
    writes joined text
    on complete => done
    on error => stop
    on timeout => stop
  state abort_state
    actor model m_abort
    role r
    reads topic
    writes aborted text
    on complete => stop
    on error => stop
    on timeout => stop
"""
    )
    registry = with_core()
    workflow = load_workflow(src, registry)

    run_id = new_run_id()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    store = _initialize_store(workflow, run_dir / "store.sqlite")
    log = LogWriter(run_dir / "log.jsonl", run_id)
    log.write("run_start", fields={"workflow_path": str(src)})
    executor = Executor(
        workflow=workflow,
        registry=registry,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=run_id,
        external_inputs={"topic": "x"},
    )

    # Simulate the live state a sibling thread might leave behind:
    # slow has already been entered five times in the live counters.
    executor._attempts["slow"] = 5
    executor._retries["slow"] = 4
    # The fast child has just run once and produced an error envelope.
    executor._attempts["fast"] = 1

    fast_state = workflow.state("fast")
    fast_envelope = Envelope(
        state_id="fast",
        attempt=1,
        actor_binding={},
        status="error",
        outcome="error",
        started_at="",
        ended_at="",
        duration_ms=0,
        inputs_read=[],
        artifacts_written=[],
        payload={},
        error=None,
    )

    # The snapshot reflects pre-fan-out state: nobody has entered yet.
    snapshot = FanOutSnapshot(
        envelopes={},
        artifacts={},
        attempts={"fast": 0, "slow": 0},
        retries={"fast": 0, "slow": 0},
    )

    selected = executor._select_transition_decl(fast_state, fast_envelope, snapshot=snapshot)
    # Pre-fix: live attempts.slow == 5 satisfies ``> 0``, so the
    # first transition (=> stop) wins and selected.target == "stop"
    # with retry_max == None. The fast child does NOT retry.
    # Post-fix: snapshot attempts.slow == 0 fails ``> 0``, so the
    # guard skips that transition and the next match is the retry
    # transition (retry_max == 1) with target == "stop". The fast
    # child retries once.
    assert selected is not None
    assert selected.outcome == "error"
    assert selected.retry_max == 1, (
        "snapshot attempts.slow == 0 should fail the > 0 guard, "
        "letting selection fall through to the retry transition. "
        "Pre-fix selected would lack retry_max because the live "
        "_attempts['slow']=5 satisfied the guard."
    )

    log.close()
    store.close()


def test_fan_out_child_retry_respects_first_match_transition(
    tmp_path: Path,
) -> None:
    """Pass-5 fix #3: a fan-out child with ``on error => stop`` declared
    BEFORE ``on error retry max 1 then stop`` must select the first
    matching transition and route the child to stop without retrying.

    Pre-fix the executor scanned every transition for one with the
    matching outcome AND retry_max set, ignoring declaration order.
    Post-fix the executor uses the same first-match-then-check-retry
    semantics the linear path uses; declaration order is honored and
    a non-retry transition that comes first wins.
    """
    import threading

    from orchestra.adapters.mock_model import MockModelAdapter

    _write_dummy_template(tmp_path)
    src = tmp_path / "fan.orc"
    src.write_text(
        """spec 0.1
workflow retry_order
  external_input topic text
  max_total_steps 30
  model m_parent
  model m_flaky
  model m_join
  model m_abort
  artifact parent_out text
  artifact flaky_out text
  artifact joined text
  artifact aborted text
  role pr
    prompt template "templates/dummy.md"
  role fr
    prompt template "templates/dummy.md"
  role jr
    prompt template "templates/dummy.md"
  role ar
    prompt template "templates/dummy.md"
  state launch
    actor model m_parent
    role pr
    reads topic
    writes parent_out text
    on complete fan_out [flaky] join join_state on error abort_state
    on error => stop
    on timeout => stop
  state flaky
    actor model m_flaky
    role fr
    reads topic
    writes flaky_out text
    on complete => done
    on error => stop
    on error retry max 1 then stop
    on timeout => stop
  state join_state
    actor model m_join
    role jr
    reads flaky_out
    writes joined text
    on complete => done
    on error => stop
    on timeout => stop
  state abort_state
    actor model m_abort
    role ar
    reads topic
    writes aborted text
    on complete => stop
    on error => stop
    on timeout => stop
"""
    )

    registry = with_core()
    workflow = load_workflow(src, registry)

    flaky_calls = {"n": 0}
    inv_lock = threading.Lock()

    class _Flaky(MockModelAdapter):
        def invoke(self, prepared: Any) -> dict[str, Any]:
            model_id = prepared.summary.get("model")
            if model_id == "m_flaky":
                with inv_lock:
                    flaky_calls["n"] += 1
                # Fail every time. Pre-fix the second transition's
                # retry would mask the failure on attempt 1; post-fix
                # the first transition (=> stop) wins and the child
                # routes to error, which the parent's fan_out
                # error_target handles.
                raise RuntimeError("synthetic always-fail")
            return super().invoke(prepared)

    registry.actor_backings["model"] = lambda: _Flaky()
    registry._adapter_cache.pop("model", None)

    run_id = new_run_id()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    store = _initialize_store(workflow, run_dir / "store.sqlite")
    log = LogWriter(run_dir / "log.jsonl", run_id)
    log.write("run_start", fields={"workflow_path": str(src)})
    executor = Executor(
        workflow=workflow,
        registry=registry,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=run_id,
        external_inputs={"topic": "hello"},
    )
    terminal = executor.run_to_completion()
    log.write("run_end", fields={"terminal": terminal})
    log.close()
    store.close()

    # First-match selection picks `on error => stop`, so the child
    # invokes exactly once. Pre-fix the scan-for-retry would have
    # tripped the retry-shaped transition and called invoke twice.
    assert flaky_calls["n"] == 1, (
        f"first-match selection should not retry; got flaky_calls={flaky_calls['n']}"
    )

    # Aggregate result of the fan_out group: error (the child errored
    # on its only invocation). Routing goes through abort_state.
    records = LogReader(run_dir / "log.jsonl").read_all()
    abort_enters = [r for r in records if r.event == "state_enter" and r.state_id == "abort_state"]
    join_enters = [r for r in records if r.event == "state_enter" and r.state_id == "join_state"]
    assert len(abort_enters) == 1, "abort_state must run on child error"
    assert join_enters == [], "join_state must not run on child error"
