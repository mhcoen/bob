"""End-to-end smoke tests for the F2.5a decision-consistency invariant.

These exercise the executor's full schema-layer pipeline with mock
adapters, including the new criteria/decision-consistency check. Two
key paths:

1. A verdict that satisfies the configured criteria → no violation,
   normal terminal=done flow.
2. A verdict that claims accept while reporting a non-compliant
   required criterion → runtime catches it, logs a
   ``decision_consistency`` violation event, state exits via the
   error outcome, terminal=stop.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from orchestra.config import CriterionDecl
from orchestra.executor.criteria import DecisionConsistencyMode
from orchestra.executor.executor import Executor, new_run_id
from orchestra.loader import load_workflow
from orchestra.loader.lookup import resolve_workflow_path
from orchestra.log import LogReader, LogWriter
from orchestra.spine import (
    NO_INITIAL,
    InvocationRequest,
    PreparedInvocation,
    Workflow,
)
from orchestra.store import ArtifactStore


class _ScriptedAdapter:
    backing = "model"

    def __init__(self, responses: dict[str, list[str]]) -> None:
        self._responses = {k: list(v) for k, v in responses.items()}
        self.calls: list[dict[str, Any]] = []

    def prepare(self, request: InvocationRequest) -> PreparedInvocation:
        self.calls.append({"state_id": request.state_id})
        return PreparedInvocation(
            request=request,
            summary={"kind": "model"},
            inner={"state_id": request.state_id},
        )

    def invoke(self, prepared: PreparedInvocation) -> dict[str, Any]:
        sid: str = prepared.inner["state_id"]
        text = self._responses[sid].pop(0)
        return {
            "output": text,
            "verdict": None,
            "fields": {},
            "tokens_in": 0,
            "tokens_out": len(text),
            "cost_usd": None,
            "transcript_ref": None,
        }

    def cancel(self, prepared: PreparedInvocation) -> None:
        return None

    def describe(self) -> dict[str, Any]:
        return {
            "backing": "model",
            "kind": "scripted",
            "supports_cancel": False,
            "reports_cost": False,
            "supports_streaming": False,
            "workspace_mutation": "text_only",
        }


def _initialize_store(workflow: Workflow, db_path: Path) -> ArtifactStore:
    store = ArtifactStore(db_path)
    for art in workflow.artifacts:
        qualifiers: dict[str, Any] = {}
        if art.initial is not NO_INITIAL:
            qualifiers["initial"] = art.initial
        store.declare(art.name, art.type, qualifiers=qualifiers)
    return store


def _run(
    tmp_path: Path,
    responses: dict[str, list[str]],
    criteria: tuple[CriterionDecl, ...],
    mode: DecisionConsistencyMode,
) -> tuple[str, Path]:
    from orchestra.api import _pre_load_registry

    path = resolve_workflow_path("iterate_until_acceptable", project_dir=None)
    registry = _pre_load_registry()
    workflow = load_workflow(path, registry)
    adapter = _ScriptedAdapter(responses)
    registry.actor_backings["model"] = lambda: adapter
    registry._adapter_cache.pop("model", None)
    rid = new_run_id()
    run_dir = tmp_path / f"run_{rid}"
    run_dir.mkdir(parents=True, exist_ok=True)
    store = _initialize_store(workflow, run_dir / "store.sqlite")
    log = LogWriter(run_dir / "log.jsonl", rid)
    log.write("run_start", fields={"workflow_path": str(path)})
    executor = Executor(
        workflow=workflow,
        registry=registry,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=rid,
        external_inputs={"query": "Q", "history": ""},
        criteria=criteria,
        decision_consistency_mode=mode,
    )
    terminal = executor.run_to_completion()
    log.write("run_end", fields={"terminal": terminal})
    log.close()
    store.close()
    return terminal, run_dir


def _accept_verdict_compliant() -> str:
    return json.dumps(
        {
            "decision": "accept",
            "feedback": "ok",
            "criteria_compliance": [
                {
                    "criterion_id": "len",
                    "observed_value": "12",
                    "compliant": True,
                },
            ],
        }
    )


def _accept_verdict_noncompliant() -> str:
    return json.dumps(
        {
            "decision": "accept",
            "feedback": "claiming compliance",
            "criteria_compliance": [
                {
                    "criterion_id": "len",
                    "observed_value": "9",
                    "compliant": False,
                },
            ],
        }
    )


def _iterate_verdict_noncompliant() -> str:
    return json.dumps(
        {
            "decision": "iterate",
            "feedback": "needs fix",
            "criteria_compliance": [
                {
                    "criterion_id": "len",
                    "observed_value": "9",
                    "compliant": False,
                },
            ],
        }
    )


def _criteria_one_required() -> tuple[CriterionDecl, ...]:
    return (CriterionDecl(id="len", description="length", required=True),)


def test_e2e_accept_with_compliance_passes(tmp_path: Path) -> None:
    """Compliant accept ⇒ terminal=done, no decision_consistency violation."""
    responses = {
        "propose": ["DRAFT-1"],
        "review": ["REVIEW-1"],
        "judge": [_accept_verdict_compliant()],
    }
    terminal, run_dir = _run(
        tmp_path,
        responses,
        _criteria_one_required(),
        DecisionConsistencyMode.STRICT_BIDIRECTIONAL,
    )
    assert terminal == "done"
    records = LogReader(run_dir / "log.jsonl").read_all()
    consistency_events = [r for r in records if r.event == "decision_consistency"]
    assert len(consistency_events) == 1
    assert consistency_events[0].fields["outcome"] == "ok"
    assert consistency_events[0].fields["decision"] == "accept"


def test_e2e_accept_with_noncompliant_violates(tmp_path: Path) -> None:
    """The load-bearing case: judge claims accept on non-compliant criterion.

    Runtime must catch the violation, log a decision_consistency event
    with reason=accept_with_noncompliant, route to the error outcome
    (terminal=stop). This is the iter-anchor failure mode that
    motivated F2.5a.
    """
    responses = {
        "propose": ["DRAFT-1"],
        "review": ["REVIEW-1"],
        "judge": [_accept_verdict_noncompliant()],
    }
    terminal, run_dir = _run(
        tmp_path,
        responses,
        _criteria_one_required(),
        DecisionConsistencyMode.STRICT_BIDIRECTIONAL,
    )
    assert terminal == "stop"
    records = LogReader(run_dir / "log.jsonl").read_all()
    consistency_events = [r for r in records if r.event == "decision_consistency"]
    assert len(consistency_events) == 1
    fields = consistency_events[0].fields
    assert fields["outcome"] == "violation"
    assert fields["reason"] == "accept_with_noncompliant"
    assert fields["noncompliant_required_ids"] == ["len"]


def test_e2e_iterate_with_noncompliant_passes(tmp_path: Path) -> None:
    """Non-accept on non-compliant artifact: iterate is fine.

    Pairs with the next test to bracket the strict-bidirectional
    invariant. iterate is reasonable when at least one required
    criterion is non-compliant. (The judge-iterate fires another
    cycle; the proposer's next response must be queued, so this
    only checks the consistency check passes for cycle 1.)
    """
    responses = {
        "propose": ["DRAFT-1", "DRAFT-2"],
        "review": ["REVIEW-1", "REVIEW-2"],
        "judge": [
            _iterate_verdict_noncompliant(),
            _accept_verdict_compliant(),
        ],
    }
    terminal, run_dir = _run(
        tmp_path,
        responses,
        _criteria_one_required(),
        DecisionConsistencyMode.STRICT_BIDIRECTIONAL,
    )
    assert terminal == "done"
    records = LogReader(run_dir / "log.jsonl").read_all()
    consistency_events = [r for r in records if r.event == "decision_consistency"]
    # Two judge calls, both should produce ok consistency events.
    assert len(consistency_events) == 2
    for ev in consistency_events:
        assert ev.fields["outcome"] == "ok"
