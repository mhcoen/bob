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
from orchestra.spine import NO_INITIAL, Workflow
from orchestra.store import ArtifactStore
from orchestra.transforms import anonymize_outputs


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
    """Replay walks the log of a completed transform run and observes
    the durable ``state_exit``; the transform callable is not invoked
    again. The committed ``anon_map`` artifact remains visible."""
    advisors = {
        "advisor_a": "alpha output",
        "advisor_b": "beta output",
        "advisor_c": "gamma output",
    }
    text = _three_advisor_council_workflow_text()
    run_dir, _ = _run_workflow(
        tmp_path / "first",
        text,
        advisors,
        run_id="replay-run",
    )
    records = LogReader(run_dir / "log.jsonl").read_all()
    enters = [
        r
        for r in records
        if r.event == "state_enter" and r.state_id == "anonymize"
    ]
    exits = [
        r
        for r in records
        if r.event == "state_exit" and r.state_id == "anonymize"
    ]
    assert len(enters) == 1
    assert len(exits) == 1
    assert exits[0].fields["status"] == "ok"

    # Open the store again and confirm the anon_map artifact is
    # visible (replay rule: completed transform's writes survive).
    store = ArtifactStore(run_dir / "store.sqlite")
    try:
        v = store.read_latest("anon_map")
        assert v is not None
        assert isinstance(v.value, dict)
        assert sorted(v.value.keys()) == ["A", "B", "C"]
    finally:
        store.close()


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
