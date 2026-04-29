"""Determinism check.

Definition of done item 4 from the implementation plan: a second test
run produces a byte-identical log to the first, modulo timestamps and
run IDs.

The check normalizes records by zeroing the nondeterministic fields
(``ts``, ``run_id``, ``duration_ms``, ``started_at``, ``ended_at``,
``payload_ref``) and asserts the remaining content is identical
between two runs.

If this test fails, the slice's spine has a hidden source of
nondeterminism (hash-map iteration order, time-sensitive logic outside
the timestamp fields, random number generation, environmental
dependence). Such a bug would corrupt the log's audit value and break
every downstream assumption that depends on log replay being a
function of (workflow, inputs).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from orchestra.adapters.mock_human import MockHumanAdapter
from orchestra.executor.executor import Executor, new_run_id
from orchestra.loader import load_workflow
from orchestra.log import LogWriter
from orchestra.registry.registry import with_core
from orchestra.spine import Workflow
from orchestra.store import ArtifactStore

FIXTURE = Path(__file__).parent / "fixtures" / "slice1" / "echo.orc"


# Field names whose values are nondeterministic by design (clock,
# random, or run-id-derived) and must be zeroed before comparison.
_NONDETERMINISTIC_RECORD_FIELDS = {"ts", "run_id"}
_NONDETERMINISTIC_NESTED_FIELDS = {
    "duration_ms",
    "started_at",
    "ended_at",
    "payload_ref",
}


def _normalize(record_text: str) -> dict[str, Any]:
    obj = json.loads(record_text)
    for k in _NONDETERMINISTIC_RECORD_FIELDS:
        if k in obj:
            obj[k] = "<nondet>"
    for k in list(obj.keys()):
        if k in _NONDETERMINISTIC_NESTED_FIELDS:
            obj[k] = "<nondet>"
    # Walk one level of nested structures looking for the nested
    # nondeterministic fields. The slice's records embed
    # ``payload_ref`` inside ``state_exit`` and ``actor_invoke_end``
    # records, and embed ``duration_ms`` similarly.
    for _k, v in obj.items():
        if isinstance(v, dict):
            for nk in list(v.keys()):
                if nk in _NONDETERMINISTIC_NESTED_FIELDS:
                    v[nk] = "<nondet>"
    return obj


def _initialize_store(workflow: Workflow, db_path: Path) -> ArtifactStore:
    store = ArtifactStore(db_path)
    for art in workflow.artifacts:
        qualifiers: dict[str, Any] = {}
        if art.initial is not None:
            qualifiers["initial"] = art.initial
        store.declare(art.name, art.type, qualifiers=qualifiers)
    return store


def _run_once(tmp_path: Path) -> list[dict[str, Any]]:
    MockHumanAdapter.clear_shared_script()
    MockHumanAdapter.set_shared_script(["accept"])
    registry = with_core()
    workflow = load_workflow(FIXTURE, registry)
    run_id = new_run_id()
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)
    store = _initialize_store(workflow, run_dir / "store.sqlite")
    log = LogWriter(run_dir / "log.jsonl", run_id)
    log.write("run_start", fields={"workflow_path": str(FIXTURE)})

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

    raw_lines = (run_dir / "log.jsonl").read_text(encoding="utf-8").splitlines()
    return [_normalize(line) for line in raw_lines if line]


def test_byte_identical_log_modulo_nondeterministic_fields(tmp_path: Path) -> None:
    """Two runs of the same workflow with the same inputs produce
    identical logs after stripping clock and run-id fields.
    """
    first = _run_once(tmp_path / "run1")
    second = _run_once(tmp_path / "run2")
    assert len(first) == len(second), (
        f"record count differs: {len(first)} vs {len(second)}"
    )
    for i, (a, b) in enumerate(zip(first, second, strict=True)):
        assert a == b, f"record {i} differs:\n  run1: {a}\n  run2: {b}"
