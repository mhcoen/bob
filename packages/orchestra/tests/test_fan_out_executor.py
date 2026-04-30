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
    successes = {
        k: v
        for k, v in rep.visibility_statuses.items()
        if v == "success"
    }
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
    cutoff = next(
        i for i, r in enumerate(records) if r.event == "fan_out_start"
    )
    truncated = records[: cutoff + 1]
    log_path.write_text(
        "\n".join(r.to_json() for r in truncated) + "\n", encoding="utf-8"
    )
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
        def __init__(self, inner): self._inner = inner
        @property
        def lock(self): return self._inner.lock
        def critical_section(self): return self._inner.critical_section()
        @property
        def next_seq(self): return self._inner.next_seq
        def close(self): self._inner.close()
        def write(self, event, *, state_id=None, attempt=None, fields=None):
            rec = self._inner.write(
                event, state_id=state_id, attempt=attempt, fields=fields
            )
            if event == "state_exit" and not saw_state_exit.is_set():
                saw_state_exit.set()
                paused.set()
                released.wait(timeout=5)
            return rec

    log = _PausingWriter(real_writer)
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
    workflow = load_workflow(src, registry)

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
                worker_read_latest_calls[name] = (
                    worker_read_latest_calls.get(name, 0) + 1
                )
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
                if any(
                    r.event == "state_exit" and r.state_id == "fast"
                    for r in records
                ):
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
    slow_reads = next(
        reads for model_id, reads in invocations if model_id == "m_slow"
    )
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
    enters = [
        r for r in records
        if r.event == "state_enter" and r.state_id == "flaky"
    ]
    assert len(enters) == 3
    inv_ids = [r.fields.get("invocation_id") for r in enters]
    assert len(set(inv_ids)) == 3
    # attempt_seq monotonic 1, 2, 3.
    seqs = sorted(int(i.split("::")[2]) for i in inv_ids)
    assert seqs == [1, 2, 3]
    # The fan_out_end aggregate is success.
    fan_end = next(r for r in records if r.event == "fan_out_end")
    assert fan_end.fields["aggregate"] == "success"
    assert fan_end.fields["per_child_outcome"]["flaky"] == "success"


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
        f"expected final attempt_seq=3 in fan_out_end's "
        f"child_invocation_ids; got {flaky_inv!r}"
    )

    # And the durable state_exit at attempt 3 carries the same
    # invocation_id (the records agree).
    flaky_exits = [
        r for r in records
        if r.event == "state_exit" and r.state_id == "flaky"
    ]
    # The final state_exit (the one that succeeded) is at attempt 3.
    final_exit = next(
        r for r in flaky_exits
        if str(r.fields.get("invocation_id")).endswith("::flaky::3")
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
        f"expected exactly one cancel(prepared) call for b, got "
        f"{cancel_calls}"
    )
    _, prepared = b_cancels[0]
    assert prepared.summary.get("model") == "m_b"

    # b's worker drained to a durable state_exit despite the cancel.
    records = LogReader(run_dir / "log.jsonl").read_all()
    b_exits = [
        r for r in records
        if r.event == "state_exit" and r.state_id == "b"
    ]
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
    a_exits = [
        r for r in records
        if r.event == "state_exit" and r.state_id == "a"
    ]
    assert len(a_exits) == 1
    assert a_exits[0].fields["outcome"] == "cancelled"

    # a wrote no actor_invoke_start (skipped invoke entirely).
    a_invoke_starts = [
        r for r in records
        if r.event == "actor_invoke_start" and r.state_id == "a"
    ]
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
    log_path.write_text(
        "\n".join(r.to_json() for r in out) + "\n", encoding="utf-8"
    )


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
    completed = {
        n: env
        for n, env in replay.envelopes.items()
        if n in children_list
    }
    executor.resume_fan_out(
        parent_state_name=str(of["parent_state"]),
        children=children_list,
        join_target=str(of["join_target"]),
        error_target=str(of["error_target"]),
        completed_children=completed,
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
    log_path.write_text(
        "\n".join(r.to_json() for r in records) + "\n", encoding="utf-8"
    )
    _filter_log_to_open_fan_out(
        log_path,
        keep_completed=["advise_a"],
        keep_started_only=["advise_b"],
    )

    terminal, _ = _resume_open_fan_out(
        run_dir, workflow_path, {"topic": "hello world"}
    )
    assert terminal == "done"

    records = LogReader(log_path).read_all()

    # advise_a was NOT re-run: it has exactly one state_enter (the
    # original) and one state_exit (the original), both with
    # attempt_seq 1.
    a_enters = [
        r for r in records
        if r.event == "state_enter" and r.state_id == "advise_a"
    ]
    a_exits = [
        r for r in records
        if r.event == "state_exit" and r.state_id == "advise_a"
    ]
    assert len(a_enters) == 1
    assert len(a_exits) == 1
    a_inv = a_enters[0].fields.get("invocation_id")
    assert isinstance(a_inv, str)
    assert a_inv.endswith("::advise_a::1")

    # advise_b was re-run: the original state_enter (attempt 1) is
    # still in the log, and a fresh state_enter+state_exit pair was
    # appended with attempt 2 carrying a different invocation_id.
    b_enters = [
        r for r in records
        if r.event == "state_enter" and r.state_id == "advise_b"
    ]
    b_exits = [
        r for r in records
        if r.event == "state_exit" and r.state_id == "advise_b"
    ]
    assert len(b_enters) == 2
    assert len(b_exits) == 1
    b_inv_ids = {r.fields.get("invocation_id") for r in b_enters}
    assert any(str(inv).endswith("::advise_b::1") for inv in b_inv_ids)
    assert any(str(inv).endswith("::advise_b::2") for inv in b_inv_ids)
    # The state_exit's invocation_id is the new one (attempt 2).
    assert str(b_exits[0].fields.get("invocation_id")).endswith(
        "::advise_b::2"
    )

    # advise_c was run for the first time on resume: one state_enter
    # and one state_exit. The attempt_seq is whatever increment the
    # resume's attempt counter produced (it may not be 1 because the
    # ``attempts`` snapshot field on advise_b's preserved state_enter
    # carries the controller's per-state counters at fan_out_start
    # time, so replay seeds advise_c's count from there).
    c_enters = [
        r for r in records
        if r.event == "state_enter" and r.state_id == "advise_c"
    ]
    c_exits = [
        r for r in records
        if r.event == "state_exit" and r.state_id == "advise_c"
    ]
    assert len(c_enters) == 1
    assert len(c_exits) == 1
    c_inv = str(c_enters[0].fields.get("invocation_id"))
    assert "::advise_c::" in c_inv
    assert c_inv == str(c_exits[0].fields.get("invocation_id"))

    # fan_out_end is now durable; aggregate is success and the
    # group routes to join_state.
    fan_ends = [r for r in records if r.event == "fan_out_end"]
    assert len(fan_ends) == 1
    assert fan_ends[0].fields["aggregate"] == "success"
    assert fan_ends[0].fields["target"] == "join_state"

    # The linear loop continued past the join target to terminal.
    join_exits = [
        r for r in records
        if r.event == "state_exit" and r.state_id == "join_state"
    ]
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
    log_path.write_text(
        "\n".join(r.to_json() for r in records) + "\n", encoding="utf-8"
    )
    # Truncate to mid-fan-out so resume actually runs (terminal logs
    # bypass the resume path entirely).
    _filter_log_to_open_fan_out(
        log_path,
        keep_completed=["advise_a"],
        keep_started_only=["advise_b"],
    )

    # advise_a's invocation_id is in the truncated log as success.
    records = LogReader(log_path).read_all()
    a_exit = next(
        r for r in records
        if r.event == "state_exit" and r.state_id == "advise_a"
    )
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
    workflow_resume = load_workflow(src, with_core())  # fresh registry
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

    resume_log = LogWriter(
        log_path, replay.last_run_id, start_seq=replay.next_seq
    )
    visibility_index = VisibilityIndex(
        persist_path=run_dir / "visibility.json"
    )
    visibility_index.replace_from(replay.visibility_statuses)

    # Sanity: the visibility index marks fast's invocation as
    # success, so a naive ``read_latest("fast_out")`` would return
    # the committed value (we are exercising the path the bug
    # makes vulnerable).
    fast_inv_id = next(
        inv for inv, status in replay.visibility_statuses.items()
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
    completed = {
        n: env
        for n, env in replay.envelopes.items()
        if n in children_list
    }
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
        f"no fan-out worker should hit the live store on resume; "
        f"got {worker_reads!r}"
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
    terminal, _ = _resume_open_fan_out(
        run_dir, workflow_path, {"topic": "hello world"}
    )

    # The run reaches a terminal state via the abort_state branch.
    assert terminal in ("done", "stop")

    records = LogReader(log_path).read_all()

    # advise_c was NOT launched: there is no fresh state_enter for
    # advise_c added by the resume path. (The truncated log had no
    # advise_c records to begin with.)
    c_enters = [
        r for r in records
        if r.event == "state_enter" and r.state_id == "advise_c"
    ]
    assert c_enters == [], (
        f"advise_c should not have been launched on resume; "
        f"got state_enters {c_enters}"
    )

    # advise_a and advise_b retain exactly their original
    # state_enter / state_exit pair (no resume re-entry of either).
    for completed in ("advise_a", "advise_b"):
        enters = [
            r for r in records
            if r.event == "state_enter" and r.state_id == completed
        ]
        exits = [
            r for r in records
            if r.event == "state_exit" and r.state_id == completed
        ]
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
        "SELECT seq FROM versions WHERE is_tentative = 1 "
        "AND written_by LIKE 'advise_a#%'"
    ).fetchall()
    assert len(pre_versions) == 1
    pre_handles = cur.execute(
        "SELECT seq FROM tentative_handles"
    ).fetchall()
    assert len(pre_handles) == 1

    # The discard must not raise: under FK=ON, the wrong delete
    # order would trip ``IntegrityError``. The fix mirrors
    # store.discard_tentative's handle-first-then-version order.
    executor._discard_stale_tentatives("advise_a", attempt=2)

    # Both rows are gone.
    post_versions = cur.execute(
        "SELECT seq FROM versions WHERE is_tentative = 1 "
        "AND written_by LIKE 'advise_a#%'"
    ).fetchall()
    assert len(post_versions) == 0
    post_handles = cur.execute(
        "SELECT seq FROM tentative_handles"
    ).fetchall()
    assert len(post_handles) == 0

    log.close()
    store.close()
