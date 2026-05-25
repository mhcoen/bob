"""End-to-end tests for the transform state primitive (Slice B).

Covers the registry contract (registration-time type checks, validator
rejection of mismatched ``reads``/``writes`` clauses), the executor's
runtime semantics (deterministic seed, no retry on Python exception,
runtime type checking on output values), and the replay rule that a
completed transform state is reused, not re-executed.

The fixtures register transforms by name with the workflow's expected
shape; the council workflow's specific schema is not assumed because
each test exercises a different shape.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from orchestra.errors import (
    ParseError,
    RegistryConflict,
    ValidationError,
)
from orchestra.executor.executor import Executor, new_run_id
from orchestra.loader import load_workflow
from orchestra.log import LogReader, LogWriter
from orchestra.registry import TransformContext
from orchestra.registry.registry import with_core
from orchestra.resume import replay_log
from orchestra.spine import NO_INITIAL, Workflow
from orchestra.store import ArtifactStore
from orchestra.transforms import anonymize_outputs


def _write_dummy_template(tmp_path: Path) -> None:
    """Create a minimal templates/dummy.md fixture so role
    declarations that reference it pass validation."""
    tdir = tmp_path / "templates"
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "dummy.md").write_text("dummy\n")


def _initialize_store(workflow: Workflow, db_path: Path) -> ArtifactStore:
    store = ArtifactStore(db_path)
    for art in workflow.artifacts:
        qualifiers: dict[str, Any] = {}
        if art.initial is not NO_INITIAL:
            qualifiers["initial"] = art.initial
        store.declare(art.name, art.type, qualifiers=qualifiers)
    return store


def _seed_advisor_artifacts(
    store: ArtifactStore,
    advisors: dict[str, str],
) -> None:
    """Pre-write advisor outputs as external versions so the transform
    state can read them without an upstream model state."""
    for name, value in advisors.items():
        store.write_external(name, value)


def _three_advisor_council_workflow_text(
    workflow_name: str = "council_test",
) -> str:
    return f"""spec 0.1
workflow {workflow_name}
  external_input topic text
  max_total_steps 10
  artifact advisor_a text
    initial null
  artifact advisor_b text
    initial null
  artifact advisor_c text
    initial null
  artifact anon_map json
  state anonymize
    actor transform anonymize_outputs
    reads advisor_a, advisor_b, advisor_c
    writes anon_map json
    on complete => done
    on error => stop
"""


def _register_council_anonymize(reg: Any) -> None:
    """Register ``anonymize_outputs`` with the three-advisor council
    schema used by these tests."""
    reg.register_transform(
        "anonymize_outputs",
        anonymize_outputs,
        input_schema={
            "advisor_a": str,
            "advisor_b": str,
            "advisor_c": str,
        },
        output_schema={"anon_map": dict[str, str]},
    )


def _run_workflow(
    tmp_path: Path,
    workflow_text: str,
    advisors: dict[str, str],
    *,
    register: Callable[[Any], None] = _register_council_anonymize,
    workflow_name: str = "council_test",
    run_id: str | None = None,
) -> tuple[Path, str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    src = tmp_path / f"{workflow_name}.orc"
    src.write_text(workflow_text)
    reg = with_core()
    register(reg)
    wf = load_workflow(src, reg)
    rid = run_id or new_run_id()
    run_dir = tmp_path / f"run_{rid}"
    run_dir.mkdir()
    store = _initialize_store(wf, run_dir / "store.sqlite")
    _seed_advisor_artifacts(store, advisors)
    log = LogWriter(run_dir / "log.jsonl", rid)
    log.write("run_start", fields={"workflow_path": str(src)})
    executor = Executor(
        workflow=wf,
        registry=reg,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=rid,
        external_inputs={"topic": "hello"},
    )
    terminal = executor.run_to_completion()
    log.write("run_end", fields={"terminal": terminal})
    log.close()
    store.close()
    assert terminal == "done", f"expected done, got {terminal!r}"
    return run_dir, rid


def _read_anon_map(run_dir: Path) -> dict[str, str]:
    """Open the run's store and read the committed ``anon_map``."""
    store = ArtifactStore(run_dir / "store.sqlite")
    try:
        v = store.read_latest("anon_map")
        assert v is not None
        assert isinstance(v.value, dict)
        return dict(v.value)
    finally:
        store.close()


# --------------------------------------------------------------------
# Registry-level tests: type validation at register time
# --------------------------------------------------------------------


def test_register_transform_rejects_unsupported_input_type() -> None:
    reg = with_core()
    with pytest.raises(RegistryConflict) as exc_info:
        reg.register_transform(
            "bad",
            lambda inputs, ctx: {"out": "x"},
            input_schema={"x": list[str]},
            output_schema={"out": str},
        )
    assert "list" in str(exc_info.value).lower()


def test_register_transform_rejects_unsupported_output_type() -> None:
    reg = with_core()
    with pytest.raises(RegistryConflict):
        reg.register_transform(
            "bad",
            lambda inputs, ctx: {"out": ()},
            input_schema={"x": str},
            output_schema={"out": tuple[str, ...]},
        )


def test_register_transform_accepts_supported_types() -> None:
    reg = with_core()
    reg.register_transform(
        "ok",
        lambda inputs, ctx: {"out_text": "x"},
        input_schema={"a_text": str, "a_int": int, "a_map": dict[str, str]},
        output_schema={"out_text": str},
    )
    assert "ok" in reg.transforms


def test_register_transform_rejects_duplicate_name() -> None:
    reg = with_core()
    reg.register_transform(
        "dup",
        lambda inputs, ctx: {"o": "x"},
        input_schema={"i": str},
        output_schema={"o": str},
    )
    with pytest.raises(RegistryConflict):
        reg.register_transform(
            "dup",
            lambda inputs, ctx: {"o": "y"},
            input_schema={"i": str},
            output_schema={"o": str},
        )


# --------------------------------------------------------------------
# Loader-level tests: parse-time and validator-level rejections
# --------------------------------------------------------------------


def test_arbitrary_python_in_orc_rejected_at_parse_time(
    tmp_path: Path,
) -> None:
    """A transform body cannot inline Python source. The ``actor
    transform <name>`` clause is the only legal way to reference a
    transform; an attempt to declare an inline ``python`` (or
    ``code``) clause is rejected by the parser."""
    src = tmp_path / "bad.orc"
    src.write_text(
        """spec 0.1
workflow bad
  external_input topic text
  max_total_steps 5
  artifact out text
  state s
    actor transform anonymize_outputs
    python "def f(x): return x"
    reads topic
    writes out text
    on complete => done
    on error => stop
"""
    )
    reg = with_core()
    with pytest.raises(ParseError):
        load_workflow(src, reg)


def test_validator_rejects_writes_not_matching_output_schema(
    tmp_path: Path,
) -> None:
    src = tmp_path / "bad.orc"
    # writes `wrong_out` instead of the registered transform's
    # `anon_map` output.
    src.write_text(
        """spec 0.1
workflow bad
  external_input topic text
  max_total_steps 5
  artifact advisor_a text
  artifact advisor_b text
  artifact advisor_c text
  artifact wrong_out json
  state anonymize
    actor transform anonymize_outputs
    reads advisor_a, advisor_b, advisor_c
    writes wrong_out json
    on complete => done
    on error => stop
"""
    )
    reg = with_core()
    _register_council_anonymize(reg)
    with pytest.raises(ValidationError) as exc_info:
        load_workflow(src, reg)
    msg = str(exc_info.value)
    assert "writes" in msg
    assert "anon_map" in msg


def test_validator_rejects_reads_not_matching_input_schema(
    tmp_path: Path,
) -> None:
    src = tmp_path / "bad.orc"
    # reads `extra_input` not declared in the schema.
    src.write_text(
        """spec 0.1
workflow bad
  external_input topic text
  max_total_steps 5
  artifact advisor_a text
  artifact advisor_b text
  artifact advisor_c text
  artifact extra_input text
  artifact anon_map json
  state anonymize
    actor transform anonymize_outputs
    reads advisor_a, advisor_b, advisor_c, extra_input
    writes anon_map json
    on complete => done
    on error => stop
"""
    )
    reg = with_core()
    _register_council_anonymize(reg)
    with pytest.raises(ValidationError) as exc_info:
        load_workflow(src, reg)
    msg = str(exc_info.value)
    assert "reads" in msg
    assert "extra_input" in msg


def test_validator_rejects_artifact_type_mismatch(tmp_path: Path) -> None:
    """The schema says ``anon_map: dict[str, str]`` (artifact type
    json) but the workflow declares ``anon_map`` as text. The
    validator catches this even though the key sets agree."""
    src = tmp_path / "bad.orc"
    src.write_text(
        """spec 0.1
workflow bad
  external_input topic text
  max_total_steps 5
  artifact advisor_a text
  artifact advisor_b text
  artifact advisor_c text
  artifact anon_map text
  state anonymize
    actor transform anonymize_outputs
    reads advisor_a, advisor_b, advisor_c
    writes anon_map text
    on complete => done
    on error => stop
"""
    )
    reg = with_core()
    _register_council_anonymize(reg)
    with pytest.raises(ValidationError) as exc_info:
        load_workflow(src, reg)
    msg = str(exc_info.value)
    assert "anon_map" in msg
    assert "json" in msg


# --------------------------------------------------------------------
# Executor-level tests: deterministic seed, replay, exceptions
# --------------------------------------------------------------------


def test_anonymize_deterministic_across_runs(tmp_path: Path) -> None:
    """Same inputs in the same run_id produce the same anonymized
    mapping. Two runs with the same run_id and inputs return identical
    anon_maps."""
    advisors = {
        "advisor_a": "alpha output",
        "advisor_b": "beta output",
        "advisor_c": "gamma output",
    }
    text = _three_advisor_council_workflow_text()
    run_dir1, _ = _run_workflow(
        tmp_path / "first",
        text,
        advisors,
        run_id="fixed-run-id",
    )
    run_dir2, _ = _run_workflow(
        tmp_path / "second",
        text,
        advisors,
        run_id="fixed-run-id",
    )
    map1 = _read_anon_map(run_dir1)
    map2 = _read_anon_map(run_dir2)
    assert map1 == map2
    # Sanity: the keys are the canonical A/B/C labels and the values
    # are the advisor outputs (in shuffled order).
    assert sorted(map1.keys()) == ["A", "B", "C"]
    assert sorted(map1.values()) == [
        "alpha output",
        "beta output",
        "gamma output",
    ]


def test_two_transform_states_in_one_run_produce_different_mappings(
    tmp_path: Path,
) -> None:
    """The seed includes ``state_name`` so two states using the same
    registered transform with the same inputs produce different
    mappings. Both states write to ``anon_map``; the test reads the
    two committed versions by their version_ids from the log."""
    src = tmp_path / "two_anon.orc"
    src.write_text(
        """spec 0.1
workflow two_anon
  external_input topic text
  max_total_steps 10
  artifact advisor_a text
    initial null
  artifact advisor_b text
    initial null
  artifact advisor_c text
    initial null
  artifact anon_map json
  state first_anon
    actor transform anonymize_outputs
    reads advisor_a, advisor_b, advisor_c
    writes anon_map json
    on complete => second_anon
    on error => stop
  state second_anon
    actor transform anonymize_outputs
    reads advisor_a, advisor_b, advisor_c
    writes anon_map json
    on complete => done
    on error => stop
"""
    )
    reg = with_core()
    _register_council_anonymize(reg)
    wf = load_workflow(src, reg)
    rid = "two-state-run"
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    store = _initialize_store(wf, run_dir / "store.sqlite")
    advisors = {
        "advisor_a": "alpha output",
        "advisor_b": "beta output",
        "advisor_c": "gamma output",
    }
    _seed_advisor_artifacts(store, advisors)
    log = LogWriter(run_dir / "log.jsonl", rid)
    log.write("run_start", fields={"workflow_path": str(src)})
    executor = Executor(
        workflow=wf,
        registry=reg,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=rid,
        external_inputs={"topic": "hello"},
    )
    terminal = executor.run_to_completion()
    log.write("run_end", fields={"terminal": terminal})
    log.close()
    store.close()
    assert terminal == "done"

    records = LogReader(run_dir / "log.jsonl").read_all()
    first_version_id: str | None = None
    second_version_id: str | None = None
    for r in records:
        if r.event != "artifact_write":
            continue
        if r.fields.get("artifact") != "anon_map":
            continue
        if r.state_id == "first_anon":
            first_version_id = r.fields["version_id"]
        elif r.state_id == "second_anon":
            second_version_id = r.fields["version_id"]
    assert first_version_id is not None
    assert second_version_id is not None

    store_open = ArtifactStore(run_dir / "store.sqlite")
    try:
        first = store_open.read_version("anon_map", first_version_id)
        second = store_open.read_version("anon_map", second_version_id)
        assert first is not None and isinstance(first.value, dict)
        assert second is not None and isinstance(second.value, dict)
        assert first.value != second.value, (
            "two transform states with the same inputs but different "
            "state names should produce different mappings; the seed "
            "must include state_name"
        )
    finally:
        store_open.close()


def test_replay_does_not_reexecute_completed_transform(
    tmp_path: Path,
) -> None:
    """The transform callable must NOT be re-invoked on resume after
    its ``state_exit`` is durable, even when the crash landed before
    the following ``transition`` record (the small window between
    state_exit and transition).

    The test wraps ``anonymize_outputs`` in a counter, runs the
    workflow to completion, truncates the log so ``state_exit`` for
    the transform survives but the trailing ``transition`` is
    dropped, then replays cmd_resume's machinery directly. The
    counter must remain at 1 (the one first-pass invocation) and the
    workflow must reach ``done``.
    """
    advisors = {
        "advisor_a": "alpha output",
        "advisor_b": "beta output",
        "advisor_c": "gamma output",
    }

    invocation_count = [0]

    def counted_anonymize(
        inputs: dict[str, Any], ctx: TransformContext
    ) -> dict[str, Any]:
        invocation_count[0] += 1
        return anonymize_outputs(inputs, ctx)

    def register_counted(reg: Any) -> None:
        reg.register_transform(
            "anonymize_outputs",
            counted_anonymize,
            input_schema={
                "advisor_a": str,
                "advisor_b": str,
                "advisor_c": str,
            },
            output_schema={"anon_map": dict[str, str]},
        )

    text = _three_advisor_council_workflow_text()
    run_dir, run_id = _run_workflow(
        tmp_path / "first",
        text,
        advisors,
        register=register_counted,
        run_id="replay-run",
    )
    assert invocation_count[0] == 1, (
        "first-pass run must invoke the transform exactly once"
    )

    log_path = run_dir / "log.jsonl"
    records = LogReader(log_path).read_all()
    truncate_at: int | None = None
    seen_state_exit = False
    for r in records:
        if (
            r.event == "state_exit"
            and r.state_id == "anonymize"
        ):
            seen_state_exit = True
            continue
        if seen_state_exit and r.event == "transition":
            truncate_at = r.seq
            break
    assert truncate_at is not None, (
        "expected a transition record after anonymize's state_exit"
    )
    keep = [r for r in records if r.seq < truncate_at]
    with open(log_path, "w", encoding="utf-8") as fh:
        for r in keep:
            fh.write(r.to_json() + "\n")

    # Mirror cmd_resume's wiring with a registry that uses the same
    # counted callable so a re-invocation would bump the counter.
    src = run_dir.parent / "council_test.orc"
    reg2 = with_core()
    register_counted(reg2)
    wf2 = load_workflow(src, reg2)
    replay = replay_log(str(log_path))
    assert replay.state_exit_without_transition is True
    assert "anonymize" in replay.envelopes

    store2 = ArtifactStore(run_dir / "store.sqlite")
    log2 = LogWriter(
        log_path, replay.last_run_id, start_seq=replay.next_seq
    )
    from orchestra.visibility import VisibilityIndex

    vi = VisibilityIndex(persist_path=run_dir / "visibility.json")
    vi.replace_from(replay.visibility_statuses)
    executor2 = Executor(
        workflow=wf2,
        registry=reg2,
        store=store2,
        log=log2,
        run_dir=run_dir,
        run_id=replay.last_run_id,
        external_inputs={"topic": "hello"},
        attempts=replay.attempts,
        retries=replay.retries,
        envelopes=replay.envelopes,
        current_state=replay.current_state,
        step_count=replay.step_count,
        visibility_index=vi,
    )
    if replay.state_exit_without_transition:
        assert replay.current_state is not None
        executor2.resume_pending_transition(replay.current_state)
    terminal = executor2.run_to_completion()
    log2.write("run_end", fields={"terminal": terminal})
    log2.close()
    store2.close()

    assert terminal == "done"
    assert invocation_count[0] == 1, (
        "transform callable was re-invoked on resume; replay must "
        "reuse the durable state_exit and write the missing "
        "transition without re-running the body"
    )

    # The committed anon_map remains visible.
    store_open = ArtifactStore(run_dir / "store.sqlite")
    try:
        v = store_open.read_latest("anon_map")
        assert v is not None
        assert isinstance(v.value, dict)
        assert sorted(v.value.keys()) == ["A", "B", "C"]
    finally:
        store_open.close()


def test_transform_state_runs_as_fan_out_child(tmp_path: Path) -> None:
    """A transform state runs alongside a model child in a fan-out
    group, reads a parent-produced artifact via the snapshot, and
    commits its declared output.

    Slice B fix: the fan-out child worker no longer eagerly looks
    up an adapter for the transform backing. Round-2 fix tightens
    the test so it actually exercises the snapshot-read path
    (parent writes an artifact, transform child reads that
    artifact) and asserts both the model child's and the
    transform child's outputs landed.
    """
    _write_dummy_template(tmp_path)
    src = tmp_path / "fan_transform.orc"
    src.write_text(
        """spec 0.1
workflow fan_transform
  external_input topic text
  max_total_steps 30
  model m_parent
  model m_a
  model m_abort
  artifact framing text
    initial null
  artifact a_out text
  artifact transform_out text
  artifact joined text
  artifact aborted text
  role parent_role
    prompt template "templates/dummy.md"
  role lens
    prompt template "templates/dummy.md"
  role aborter
    prompt template "templates/dummy.md"
  state launch
    actor model m_parent
    role parent_role
    reads topic
    writes framing text
    on complete fan_out [advise_a, transform_child] join join_state on error abort_state
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
  state transform_child
    actor transform reframe
    reads framing
    writes transform_out text
    on complete => done
    on error => stop
  state join_state
    actor transform combine
    reads a_out, transform_out
    writes joined text
    on complete => done
    on error => stop
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

    transform_calls: list[dict[str, Any]] = []

    def reframe(
        inputs: dict[str, Any], ctx: TransformContext
    ) -> dict[str, Any]:
        # The snapshot must already contain the parent's framing
        # artifact when the transform runs as a fan-out child.
        framing = inputs.get("framing")
        transform_calls.append({"framing": framing})
        if not isinstance(framing, str):
            raise AssertionError(
                f"transform child must read the parent's framing "
                f"artifact from the snapshot; got {framing!r}"
            )
        return {"transform_out": f"reframed:{framing}"}

    join_calls: list[dict[str, Any]] = []

    def combine(
        inputs: dict[str, Any], ctx: TransformContext
    ) -> dict[str, Any]:
        # Round-3 fix: the join must actually consume both fan-out
        # outputs. Raising here if either is missing or unexpected
        # turns the read into observable behavior, so the test can
        # prove the join consumed the values rather than just that
        # ``read_latest`` would have returned them.
        a_out = inputs.get("a_out")
        transform_out = inputs.get("transform_out")
        join_calls.append(
            {"a_out": a_out, "transform_out": transform_out}
        )
        if not isinstance(a_out, str) or not a_out:
            raise AssertionError(
                f"join did not see advise_a's a_out via reads; "
                f"got {a_out!r}"
            )
        if not isinstance(transform_out, str):
            raise AssertionError(
                f"join did not see transform_child's transform_out "
                f"via reads; got {transform_out!r}"
            )
        if not transform_out.startswith("reframed:"):
            raise AssertionError(
                "transform_out is present but does not have the "
                "reframed:<framing> shape the transform child wrote"
            )
        return {"joined": f"{a_out}|{transform_out}"}

    reg = with_core()
    reg.register_transform(
        "reframe",
        reframe,
        input_schema={"framing": str},
        output_schema={"transform_out": str},
    )
    reg.register_transform(
        "combine",
        combine,
        input_schema={"a_out": str, "transform_out": str},
        output_schema={"joined": str},
    )
    wf = load_workflow(src, reg)
    rid = "fan-transform-run"
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    store = _initialize_store(wf, run_dir / "store.sqlite")
    log = LogWriter(run_dir / "log.jsonl", rid)
    log.write("run_start", fields={"workflow_path": str(src)})
    executor = Executor(
        workflow=wf,
        registry=reg,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=rid,
        external_inputs={"topic": "hello"},
    )
    terminal = executor.run_to_completion()
    log.write("run_end", fields={"terminal": terminal})
    log.close()
    store.close()
    assert terminal == "done"

    records = LogReader(run_dir / "log.jsonl").read_all()
    fan_starts = [r for r in records if r.event == "fan_out_start"]
    assert len(fan_starts) == 1
    assert sorted(fan_starts[0].fields["children"]) == [
        "advise_a",
        "transform_child",
    ]

    transform_exit = next(
        r
        for r in records
        if r.event == "state_exit" and r.state_id == "transform_child"
    )
    advise_exit = next(
        r
        for r in records
        if r.event == "state_exit" and r.state_id == "advise_a"
    )
    assert transform_exit.fields["status"] == "ok"
    assert advise_exit.fields["status"] == "ok"

    fan_end = next(r for r in records if r.event == "fan_out_end")
    assert fan_end.fields["aggregate"] == "success"
    assert (
        fan_end.fields["per_child_outcome"]["transform_child"] == "success"
    )
    assert (
        fan_end.fields["per_child_outcome"]["advise_a"] == "success"
    )

    # The transform was invoked exactly once, with the parent's
    # framing artifact provided by the snapshot.
    assert len(transform_calls) == 1
    framing_seen = transform_calls[0]["framing"]
    assert isinstance(framing_seen, str) and framing_seen, (
        "transform child must observe the parent's framing artifact "
        "via the fan-out snapshot"
    )

    # Round-3 fix: prove the join state actually consumed both
    # fan-out outputs by inspecting what its body received. The
    # join transform raises if either read is missing, so the
    # workflow only reaches done if both reads were threaded
    # through.
    assert len(join_calls) == 1
    join_seen = join_calls[0]
    expected_transform_out = f"reframed:{framing_seen}"

    store_open = ArtifactStore(run_dir / "store.sqlite")
    try:
        v_a = store_open.read_latest("a_out")
        assert v_a is not None
        assert isinstance(v_a.value, str) and v_a.value, (
            "advise_a must commit a non-empty model output"
        )
        v_t = store_open.read_latest("transform_out")
        assert v_t is not None
        assert v_t.value == expected_transform_out
        v_join = store_open.read_latest("joined")
        assert v_join is not None
        assert v_join.value == f"{v_a.value}|{expected_transform_out}", (
            "joined must concatenate the exact a_out and "
            "transform_out values; the join body's behavior is the "
            "test surface, not just the visibility rule"
        )
        # The join saw exactly the values the children committed.
        assert join_seen["a_out"] == v_a.value
        assert join_seen["transform_out"] == expected_transform_out
    finally:
        store_open.close()


def test_register_transform_rejects_bytes(tmp_path: Path) -> None:
    """Slice B fix: ``bytes`` is rejected at registration time. The
    artifact store hashes via ``json.dumps`` which cannot serialize
    bytes, so a registered ``bytes`` schema would crash at
    ``tentative_write`` if it were allowed.
    """
    reg = with_core()
    with pytest.raises(RegistryConflict) as exc_info:
        reg.register_transform(
            "bytes_in",
            lambda inputs, ctx: {"o": "x"},
            input_schema={"i": bytes},
            output_schema={"o": str},
        )
    assert "bytes" in str(exc_info.value)
    with pytest.raises(RegistryConflict):
        reg.register_transform(
            "bytes_out",
            lambda inputs, ctx: {"o": b"x"},
            input_schema={"i": str},
            output_schema={"o": bytes},
        )


def test_anonymize_seed_pins_default_json_encoding(
    tmp_path: Path,
) -> None:
    """Slice B round-2 fix: the seed encoder must be the literal
    default ``json.dumps([run_id, state_name, sorted_input_keys])``.
    The earlier fix passed ``ensure_ascii=False`` which emits raw
    UTF-8 for non-ASCII characters; the default escapes them to
    ``\\uXXXX``. The two encodings hash to different SHA-256 values,
    so the seed contract was non-deterministic across encoders.

    The earlier round-1 test only checked equality across two
    calls of the same encoder, which any deterministic encoder
    satisfies and so did not pin the fix. This test computes the
    expected ``anon_map`` against the default encoding and the
    ``ensure_ascii=False`` encoding using the same RNG construction
    the implementation uses, then asserts the implementation matches
    the default and not the alternative.
    """
    inputs = {
        f"café_{c}": f"value-{c}" for c in ("a", "b", "c", "d", "e")
    }
    run_id = "run-é"
    state_name = "anonymïze"
    sorted_keys = sorted(inputs.keys())

    def expected_anon_map(default: bool) -> dict[str, str]:
        if default:
            seed_material = json.dumps(
                [run_id, state_name, sorted_keys]
            )
        else:
            seed_material = json.dumps(
                [run_id, state_name, sorted_keys], ensure_ascii=False
            )
        seed_hash = hashlib.sha256(
            seed_material.encode("utf-8")
        ).hexdigest()
        rng = random.Random(int(seed_hash, 16))
        shuffled = list(sorted_keys)
        rng.shuffle(shuffled)
        return {
            chr(ord("A") + i): inputs[k]
            for i, k in enumerate(shuffled)
        }

    expected_default = expected_anon_map(default=True)
    expected_alt = expected_anon_map(default=False)
    # Sanity: this set of inputs must produce different mappings
    # under the two encoders, otherwise the test would not pin
    # the contract for either side.
    assert expected_default != expected_alt, (
        "the inputs do not distinguish the two encoders; pick "
        "different ones for the regression test"
    )

    ctx = TransformContext(
        run_id=run_id,
        state_name=state_name,
        sorted_input_keys=list(sorted_keys),
    )
    actual = anonymize_outputs(inputs, ctx)["anon_map"]
    assert actual == expected_default, (
        "anonymize_outputs must seed with the default json.dumps "
        "encoder; the produced mapping does not match the default "
        "encoding's mapping"
    )
    assert actual != expected_alt, (
        "the implementation matches the ensure_ascii=False mapping; "
        "this is the regression the round-2 fix closes"
    )


def test_transform_python_exception_produces_error_state_exit_no_retry(
    tmp_path: Path,
) -> None:
    """A transform callable that raises produces an ``error``
    ``state_exit`` (with the exception message in the envelope) and
    the executor follows ``on error => <target>`` without retrying."""

    def explode(
        inputs: dict[str, Any], ctx: TransformContext
    ) -> dict[str, Any]:
        raise RuntimeError("boom")

    src = tmp_path / "explode.orc"
    src.write_text(
        """spec 0.1
workflow explode_test
  external_input topic text
  max_total_steps 10
  artifact in_text text
    initial null
  artifact out_text text
  state s
    actor transform explode
    reads in_text
    writes out_text text
    on complete => done
    on error => stop
"""
    )
    reg = with_core()
    reg.register_transform(
        "explode",
        explode,
        input_schema={"in_text": str},
        output_schema={"out_text": str},
    )
    wf = load_workflow(src, reg)
    rid = "explode-run"
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    store = _initialize_store(wf, run_dir / "store.sqlite")
    store.write_external("in_text", "seed")
    log = LogWriter(run_dir / "log.jsonl", rid)
    log.write("run_start", fields={})
    executor = Executor(
        workflow=wf,
        registry=reg,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=rid,
        external_inputs={"topic": "hello"},
    )
    terminal = executor.run_to_completion()
    log.write("run_end", fields={"terminal": terminal})
    log.close()
    assert terminal == "stop"

    records = LogReader(run_dir / "log.jsonl").read_all()
    enters = [
        r for r in records if r.event == "state_enter" and r.state_id == "s"
    ]
    exits = [
        r for r in records if r.event == "state_exit" and r.state_id == "s"
    ]
    # No retry: exactly one enter and exit, attempt 1.
    assert len(enters) == 1
    assert len(exits) == 1
    assert enters[0].attempt == 1
    assert exits[0].attempt == 1
    assert exits[0].fields["status"] == "error"
    err = exits[0].fields["error"]
    assert err is not None
    assert "boom" in err["message"]
    # No artifact was committed: out_text is not visible.
    store_open = ArtifactStore(run_dir / "store.sqlite")
    try:
        assert store_open.read_latest("out_text") is None
    finally:
        store_open.close()


def test_transform_runtime_type_violation_produces_error_state_exit(
    tmp_path: Path,
) -> None:
    """A transform that returns a value violating ``output_schema``
    types (e.g. a ``dict[str, str]`` schema with a non-string value)
    produces an error ``state_exit``. The output is not committed."""

    def bad_map(
        inputs: dict[str, Any], ctx: TransformContext
    ) -> dict[str, Any]:
        # Schema says dict[str, str] but the callable returns a dict
        # whose values include an int.
        return {"out_map": {"A": "ok", "B": 42}}

    src = tmp_path / "badmap.orc"
    src.write_text(
        """spec 0.1
workflow badmap_test
  external_input topic text
  max_total_steps 10
  artifact in_text text
    initial null
  artifact out_map json
  state s
    actor transform bad_map
    reads in_text
    writes out_map json
    on complete => done
    on error => stop
"""
    )
    reg = with_core()
    reg.register_transform(
        "bad_map",
        bad_map,
        input_schema={"in_text": str},
        output_schema={"out_map": dict[str, str]},
    )
    wf = load_workflow(src, reg)
    rid = "badmap-run"
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    store = _initialize_store(wf, run_dir / "store.sqlite")
    store.write_external("in_text", "seed")
    log = LogWriter(run_dir / "log.jsonl", rid)
    log.write("run_start", fields={})
    executor = Executor(
        workflow=wf,
        registry=reg,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=rid,
        external_inputs={"topic": "hello"},
    )
    terminal = executor.run_to_completion()
    log.write("run_end", fields={"terminal": terminal})
    log.close()
    assert terminal == "stop"

    records = LogReader(run_dir / "log.jsonl").read_all()
    exits = [
        r for r in records if r.event == "state_exit" and r.state_id == "s"
    ]
    assert len(exits) == 1
    assert exits[0].fields["status"] == "error"
    err = exits[0].fields["error"]
    assert err is not None
    assert "out_map" in err["message"]

    store_open = ArtifactStore(run_dir / "store.sqlite")
    try:
        assert store_open.read_latest("out_map") is None
    finally:
        store_open.close()
