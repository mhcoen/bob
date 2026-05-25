"""Tests for the council_four workflow.

Pattern: a framer normalizes (state, question, ledger_slice,
design_context) into a council_brief; four proposers fire in parallel
from the same brief; a synthesizer reads all four proposals and
emits a structured verdict (decision + feedback +
agreements/disagreements/rejected_options + criteria_compliance) plus
a free-form plan artifact.

Tests cover:

- Workflow load + validate (states, fan-out + join, schema, extraction
  clauses, external inputs).
- Verdict schema shape (decision enum, required arrays).
- Distinct-actor invariant: missing roles, four-proposer pairwise
  distinct, synthesizer differs from each proposer.
- End-to-end smoke: scripted adapters fan out, synthesizer accepts,
  terminal=done; per-actor proposals retained as separate artifacts.
- End-to-end negative: synthesizer claims accept while a required
  criterion is non-compliant — F2.5a runtime invariant catches it,
  state exits via error outcome, terminal=stop.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from orchestra.api import (
    _pre_load_registry,
    _validate_role_bindings,
)
from orchestra.config import (
    ConfigError,
    CriterionDecl,
    OrchestraConfig,
    RoleBinding,
    WorkflowConfig,
)
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

# --------------------------------------------------------------------
# Scripted mock adapter
# --------------------------------------------------------------------


class _ScriptedModelAdapter:
    backing = "model"

    def __init__(self, responses: dict[str, list[str]]) -> None:
        self._responses = {k: list(v) for k, v in responses.items()}
        self.calls: list[dict[str, Any]] = []

    def prepare(self, request: InvocationRequest) -> PreparedInvocation:
        prompt = request.prompt_artifact or ""
        self.calls.append({"state_id": request.state_id, "prompt": prompt})
        return PreparedInvocation(
            request=request,
            summary={"kind": "model"},
            inner={"state_id": request.state_id, "prompt": prompt},
        )

    def invoke(self, prepared: PreparedInvocation) -> dict[str, Any]:
        sid: str = prepared.inner["state_id"]
        queue = self._responses.get(sid) or []
        if not queue:
            raise AssertionError(
                f"scripted adapter has no response for {sid!r}"
            )
        text = queue.pop(0)
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


def _run_council(
    tmp_path: Path,
    *,
    responses: dict[str, list[str]],
    criteria: tuple[CriterionDecl, ...] = (),
    inputs: dict[str, str] | None = None,
) -> tuple[_ScriptedModelAdapter, Path, str, ArtifactStore]:
    path = resolve_workflow_path("council_four", project_dir=None)
    registry = _pre_load_registry()
    workflow = load_workflow(path, registry)
    adapter = _ScriptedModelAdapter(responses)
    registry.actor_backings["model"] = lambda: adapter
    registry._adapter_cache.pop("model", None)
    rid = new_run_id()
    run_dir = tmp_path / f"run_{rid}"
    run_dir.mkdir(parents=True, exist_ok=True)
    store = _initialize_store(workflow, run_dir / "store.sqlite")
    log = LogWriter(run_dir / "log.jsonl", rid)
    log.write("run_start", fields={"workflow_path": str(path)})
    executor_inputs = {
        "state": "the current state",
        "question": "the question to be answered",
        "ledger_slice": "",
        "design_context": "",
    }
    if inputs:
        executor_inputs.update(inputs)
    executor = Executor(
        workflow=workflow,
        registry=registry,
        store=store,
        log=log,
        run_dir=run_dir,
        run_id=rid,
        external_inputs=executor_inputs,
        criteria=criteria,
        decision_consistency_mode=DecisionConsistencyMode.ACCEPT_ONLY,
    )
    terminal = executor.run_to_completion()
    log.write("run_end", fields={"terminal": terminal})
    log.close()
    return adapter, run_dir, terminal, store


# --------------------------------------------------------------------
# Load + validate
# --------------------------------------------------------------------


def test_council_workflow_loads_and_validates() -> None:
    path = resolve_workflow_path("council_four", project_dir=None)
    workflow = load_workflow(path, _pre_load_registry())
    assert workflow.name == "council_four"
    state_names = {s.name for s in workflow.states}
    assert state_names == {
        "frame",
        "propose_code",
        "propose_codex",
        "propose_kimi",
        "propose_deepseek",
        "synthesize",
    }
    external_input_names = {ei.name for ei in workflow.external_inputs}
    assert external_input_names == {
        "state",
        "question",
        "ledger_slice",
        "design_context",
    }
    verdict = next(a for a in workflow.artifacts if a.name == "judge_verdict")
    assert verdict.schema_path is not None
    assert verdict.schema_path.endswith("council_synthesis_verdict.json")
    extracts = {(e.source_field, e.target) for e in verdict.extractions}
    assert extracts == {
        ("decision", "judge_decision"),
        ("feedback", "judge_feedback"),
    }


# --------------------------------------------------------------------
# Schema shape: decision enum, required structured fields
# --------------------------------------------------------------------


def test_council_schema_decision_enum() -> None:
    path = (
        Path(__file__).parent.parent
        / "orchestra"
        / "workflows"
        / "schemas"
        / "council_synthesis_verdict.json"
    )
    schema = json.loads(path.read_text())
    assert schema["properties"]["decision"]["enum"] == [
        "accept",
        "reframe",
        "stuck",
    ]


def test_council_schema_requires_structured_arrays() -> None:
    path = (
        Path(__file__).parent.parent
        / "orchestra"
        / "workflows"
        / "schemas"
        / "council_synthesis_verdict.json"
    )
    schema = json.loads(path.read_text())
    required = set(schema["required"])
    assert {
        "decision",
        "feedback",
        "agreements",
        "disagreements",
        "rejected_options",
    }.issubset(required)
    assert schema["properties"]["agreements"]["type"] == "array"
    assert schema["properties"]["disagreements"]["type"] == "array"
    assert schema["properties"]["rejected_options"]["type"] == "array"
    diss = schema["properties"]["disagreements"]["items"]
    assert set(diss["required"]) == {"topic", "positions"}


def test_council_four_canonical_workflow_loads() -> None:
    """The canonical-mode workflow split (Slice D smoke surfaced
    that mixing canonical and reauthor under one workflow leaks
    template assumptions across modes; see
    orchestra/design/synthesizer-output-contract.md).
    """
    path = resolve_workflow_path("council_four_canonical", project_dir=None)
    workflow = load_workflow(path, _pre_load_registry())
    assert workflow.name == "council_four_canonical"
    verdict = next(a for a in workflow.artifacts if a.name == "judge_verdict")
    assert verdict.schema_path is not None
    assert verdict.schema_path.endswith(
        "council_synthesis_verdict_canonical.json"
    )


def test_canonical_workflow_takes_required_phase_id_input() -> None:
    """The canonical workflow MUST declare required_phase_id as an
    external input. Duplo computes the phase_id deterministically
    from the existing PLAN.md and injects it as a constraint;
    the council brief surfaces it for proposers and the
    synthesizer. See the "Per-call model authority is the wrong
    ownership boundary" section in
    orchestra/design/synthesizer-output-contract.md.
    """
    path = resolve_workflow_path("council_four_canonical", project_dir=None)
    workflow = load_workflow(path, _pre_load_registry())
    external_input_names = {ei.name for ei in workflow.external_inputs}
    assert external_input_names == {
        "state",
        "question",
        "ledger_slice",
        "design_context",
        "required_phase_id",
    }


def test_canonical_synthesizer_template_uses_required_phase_id() -> None:
    """The canonical synthesizer template MUST instruct verbatim
    use of required_phase_id (replacing the prior 'use phase_001,
    phase_002, etc. for first-time ids' guidance which gave the
    synthesizer authority over identifier choice). Pinned so a
    future template edit cannot silently re-grant that authority.
    """
    template_path = (
        Path(__file__).parent.parent
        / "orchestra"
        / "workflows"
        / "templates"
        / "council_synthesizer_canonical.md"
    )
    body = template_path.read_text()
    assert "required_phase_id" in body
    assert "VERBATIM" in body
    # The prior wording must not return.
    assert "use phase_001, phase_002, etc. for first-time ids" not in body


def test_canonical_synthesizer_template_requires_toolchain_discipline() -> None:
    """The canonical synthesizer template MUST carry a toolchain-
    discipline section instructing the synthesizer to verify any
    command-line tool it invokes is declared in the target
    project's pyproject.toml. Pairs with mcloop's pre-flight
    dependency validator: mcloop fails the run when declared deps
    are missing; the synthesized plan must not invoke tools that
    are not declared. Pinned so a future template edit cannot
    silently drop the discipline.
    """
    template_path = (
        Path(__file__).parent.parent
        / "orchestra"
        / "workflows"
        / "templates"
        / "council_synthesizer_canonical.md"
    )
    body = template_path.read_text().lower()
    assert "toolchain discipline" in body
    assert "[project.optional-dependencies].dev" in body
    assert "[project.dependencies]" in body
    assert "undeclared tools" in body
    assert "pytest-xdist" in body  # the canonical failure-mode example


def test_canonical_synthesizer_template_requires_check_green_per_task() -> None:
    """Every modifying task MUST leave the project's check command
    exit-zero. The synthesizer should combine setup + minimal
    implementation + first smoke test into one atomic task when
    the check command is test-driven and no tests exist yet.
    Pinned so a future template edit cannot silently drop the
    rule. The deliberate-no-op exemption ("do not modify" /
    "capture baseline" / "read-only") must also be documented so
    the synthesizer knows when read-only tasks are legal.
    """
    template_path = (
        Path(__file__).parent.parent
        / "orchestra"
        / "workflows"
        / "templates"
        / "council_synthesizer_canonical.md"
    )
    body = template_path.read_text()
    body_lower = body.lower()
    # Load-bearing phrases.
    assert "exit-zero" in body_lower
    assert "no tests collected" in body_lower or "exit 5" in body_lower
    assert "atomic task" in body_lower
    assert "first smoke test" in body_lower
    # Read-only exemption documented.
    assert "do not modify" in body_lower
    assert "read-only" in body_lower or "read only" in body_lower
    # The canonical failure mode named explicitly.
    assert "dead-lock" in body_lower or "deadlock" in body_lower


def test_canonical_synthesizer_template_validates_python_package_identifiers() -> None:
    """The canonical synthesizer template MUST instruct the
    synthesizer to fail-closed when a Python package name in the
    project's pyproject.toml is not a valid Python identifier
    (e.g., contains hyphens). The intended behavior is to set
    verdict.decision to "reframe" and surface the offending
    package(s) in feedback, not paper over the issue with
    importlib.import_module workarounds. Pinned so a future
    template edit cannot silently drop the validation rule.
    """
    template_path = (
        Path(__file__).parent.parent
        / "orchestra"
        / "workflows"
        / "templates"
        / "council_synthesizer_canonical.md"
    )
    body = template_path.read_text()
    body_lower = body.lower()
    assert "valid python identifier" in body_lower
    assert "PEP 8" in body
    assert '"reframe"' in body or "'reframe'" in body
    assert "no hyphens" in body_lower
    assert "importlib.import_module" in body


def test_canonical_synthesizer_template_forbids_h1_phase_heading() -> None:
    """The canonical synthesizer template MUST instruct the
    synthesizer NOT to author a `# <project> — Phase N: <title>`
    H1 heading. The runtime (Duplo) owns the PLAN.md envelope
    via strip-and-render, so any model-authored H1 is overwritten;
    the instruction makes the contract explicit so the synthesizer
    does not waste tokens on a heading that gets stripped. Pinned
    so a future template edit cannot silently restore the
    synthesizer's authority over the H1 envelope.
    """
    template_path = (
        Path(__file__).parent.parent
        / "orchestra"
        / "workflows"
        / "templates"
        / "council_synthesizer_canonical.md"
    )
    body = template_path.read_text()
    body_lower = body.lower()
    assert "do not author" in body_lower
    assert "h1 phase heading" in body_lower
    assert "stripped and replaced" in body_lower
    # The forbidden envelope shape is named explicitly.
    assert "# <project_name> — Phase N:" in body or (
        "<project_name>" in body and "Phase N" in body
    )


def test_reauthor_synthesizer_template_phase_ids_runtime_supplied() -> None:
    """The reauthor synthesizer template MUST instruct the
    synthesizer that phase ids are runtime-supplied: prior ids are
    listed verbatim in the state block, and new ids start at the
    runtime-supplied 'Next available phase id' value. This mirrors
    canonical mode's required_phase_id discipline. Pinned so a
    future template edit cannot silently re-grant the synthesizer
    authority over phase identifier choice (which collided with
    existing prior ids when the prior plan had gaps from earlier
    reauthor runs)."""
    template_path = (
        Path(__file__).parent.parent
        / "orchestra"
        / "workflows"
        / "templates"
        / "council_synthesizer_reauthor.md"
    )
    body = template_path.read_text()
    body_lower = body.lower()
    # The new section header is present.
    assert "runtime-supplied, not synthesizer-chosen" in body_lower
    # The runtime-supplied start id is named.
    assert "Next available phase id" in body
    # The state-block prior list is the source of truth for ancestor ids.
    assert "do not invent ancestor ids" in body_lower
    # Collisions with prior ids are explicitly forbidden.
    assert "never reuse a prior id" in body_lower
    # The discipline is tied back to canonical's required_phase_id pattern.
    assert "required_phase_id" in body


def test_reauthor_synthesizer_template_renders_under_str_format() -> None:
    """The reauthor template is consumed via str.format() with the
    five council fields. Any single-brace example JSON in the
    template body would crash format() with KeyError. Pinned so a
    future template edit cannot reintroduce unescaped braces."""
    template_path = (
        Path(__file__).parent.parent
        / "orchestra"
        / "workflows"
        / "templates"
        / "council_synthesizer_reauthor.md"
    )
    body = template_path.read_text()
    rendered = body.format(
        council_brief="X",
        proposal_code="X",
        proposal_codex="X",
        proposal_kimi="X",
        proposal_deepseek="X",
    )
    # Substitutions occurred; rendered output is non-trivial.
    assert "{council_brief}" not in rendered
    assert "{proposal_code}" not in rendered
    assert len(rendered) > 1000


def test_canonical_proposer_template_uses_required_phase_id() -> None:
    """The shared proposer template instructs proposers to use
    required_phase_id verbatim when present in the brief. Reauthor
    mode does not surface required_phase_id in its brief; the
    instruction only fires in canonical mode.
    """
    template_path = (
        Path(__file__).parent.parent
        / "orchestra"
        / "workflows"
        / "templates"
        / "council_proposer.md"
    )
    body = template_path.read_text()
    assert "required_phase_id" in body
    assert "verbatim" in body.lower()


def test_council_four_reauthor_workflow_loads() -> None:
    path = resolve_workflow_path("council_four_reauthor", project_dir=None)
    workflow = load_workflow(path, _pre_load_registry())
    assert workflow.name == "council_four_reauthor"
    verdict = next(a for a in workflow.artifacts if a.name == "judge_verdict")
    assert verdict.schema_path is not None
    assert verdict.schema_path.endswith(
        "council_synthesis_verdict_reauthor.json"
    )


def test_canonical_schema_omits_lineage() -> None:
    """Canonical-mode plan authoring is fresh authoring; there is no
    prior plan to track lineage against. The canonical schema must
    NOT carry a lineage field; lineage belongs to the re-author
    schema only.
    """
    path = (
        Path(__file__).parent.parent
        / "orchestra"
        / "workflows"
        / "schemas"
        / "council_synthesis_verdict_canonical.json"
    )
    schema = json.loads(path.read_text())
    assert "lineage" not in schema["properties"]
    assert set(schema["required"]) >= {
        "decision",
        "feedback",
        "agreements",
        "disagreements",
        "rejected_options",
    }


def test_reauthor_schema_keeps_lineage() -> None:
    path = (
        Path(__file__).parent.parent
        / "orchestra"
        / "workflows"
        / "schemas"
        / "council_synthesis_verdict_reauthor.json"
    )
    schema = json.loads(path.read_text())
    assert "lineage" in schema["properties"]
    lineage = schema["properties"]["lineage"]
    assert lineage["required"] == ["phases"]


# ---------------------------------------------------------------------
# commit_attributions slot (separate from lineage)
#
# Today's incident: synthesizer emitted attributed_commits and status
# on lineage.phases[3] for crossing 019e16e9-9de8-790c-84e7-5534e42b01bf.
# additionalProperties:false on lineage.phases[] rejected the verdict
# with no slot for commit attribution. Fix: top-level
# commit_attributions array on both reauthor + canonical schemas.
# ---------------------------------------------------------------------


def _reauthor_schema() -> dict[str, Any]:
    path = (
        Path(__file__).parent.parent
        / "orchestra"
        / "workflows"
        / "schemas"
        / "council_synthesis_verdict_reauthor.json"
    )
    return json.loads(path.read_text())


def _canonical_schema() -> dict[str, Any]:
    path = (
        Path(__file__).parent.parent
        / "orchestra"
        / "workflows"
        / "schemas"
        / "council_synthesis_verdict_canonical.json"
    )
    return json.loads(path.read_text())


def _base_reauthor_verdict() -> dict[str, Any]:
    return {
        "decision": "accept",
        "feedback": "synthesized",
        "agreements": ["a"],
        "disagreements": [],
        "rejected_options": [],
        "criteria_compliance": [
            {"criterion_id": "c1", "observed_value": "ok", "compliant": True}
        ],
        "lineage": {
            "phases": [
                {"id": "phase_001", "action": "preserve"},
                {"id": "phase_002", "action": "new"},
            ]
        },
    }


def test_reauthor_schema_accepts_commit_attributions_populated() -> None:
    """The reauthor verdict schema MUST accept verdicts that carry a
    populated commit_attributions array alongside lineage. This is
    the happy path the synthesizer needs when the triggering crossing
    is unattributable_commit.
    """
    import jsonschema

    schema = _reauthor_schema()
    verdict = _base_reauthor_verdict()
    verdict["commit_attributions"] = [
        {
            "commit_sha": "abcdef1234567",
            "phase_id": "phase_002",
            "rationale": "commit modifies parser introduced by phase_002",
        }
    ]
    jsonschema.validate(instance=verdict, schema=schema)


def test_reauthor_schema_rejects_unknown_fields_on_lineage_phases() -> None:
    """Regression for crossing 019e16e9 (2026-05-09): synthesizer wrote
    `attributed_commits` and `status` on lineage.phases[3] and the
    verdict was rejected. The schema MUST keep additionalProperties:
    false on lineage.phases[] items so attribution never sneaks back
    into the lineage slot.
    """
    import jsonschema

    schema = _reauthor_schema()
    verdict = _base_reauthor_verdict()
    verdict["lineage"]["phases"].append(
        {
            "id": "phase_003",
            "action": "new",
            "attributed_commits": ["abcdef1"],
            "status": "complete",
        }
    )
    with pytest.raises(jsonschema.ValidationError) as exc_info:
        jsonschema.validate(instance=verdict, schema=schema)
    # additionalProperties violations on lineage.phases[] entries
    # name the unexpected key in the error message.
    msg = str(exc_info.value.message).lower()
    assert "additional properties" in msg or "additionalproperties" in msg
    assert "attributed_commits" in msg or "status" in msg


def test_commit_attributions_items_reject_missing_required_fields() -> None:
    """Each commit_attributions item MUST carry commit_sha, phase_id,
    and rationale. Missing any required field is a schema error.
    """
    import jsonschema

    schema = _reauthor_schema()
    verdict = _base_reauthor_verdict()
    # Missing rationale.
    verdict["commit_attributions"] = [
        {"commit_sha": "abcdef1234567", "phase_id": "phase_002"}
    ]
    with pytest.raises(jsonschema.ValidationError) as exc_info:
        jsonschema.validate(instance=verdict, schema=schema)
    assert "rationale" in str(exc_info.value.message).lower()


def test_commit_attributions_items_reject_additional_unknown_properties() -> None:
    """commit_attributions items use additionalProperties:false; a
    synthesizer that tries to smuggle a `status` or `attributed_to`
    field through this slot must be rejected just as on lineage.
    """
    import jsonschema

    schema = _reauthor_schema()
    verdict = _base_reauthor_verdict()
    verdict["commit_attributions"] = [
        {
            "commit_sha": "abcdef1234567",
            "phase_id": "phase_002",
            "rationale": "ok",
            "status": "complete",
        }
    ]
    with pytest.raises(jsonschema.ValidationError) as exc_info:
        jsonschema.validate(instance=verdict, schema=schema)
    msg = str(exc_info.value.message).lower()
    assert "additional properties" in msg or "additionalproperties" in msg
    assert "status" in msg


def test_canonical_schema_accepts_commit_attributions_populated() -> None:
    """Canonical mode never has prior plan ids but can still encounter
    unattributable commits; the canonical schema carries the same
    commit_attributions slot (without lineage) so council_four
    canonical workflows can attribute commits the same way.
    """
    import jsonschema

    schema = _canonical_schema()
    verdict = {
        "decision": "accept",
        "feedback": "fresh authoring",
        "agreements": ["a"],
        "disagreements": [],
        "rejected_options": [],
        "criteria_compliance": [
            {"criterion_id": "c1", "observed_value": "ok", "compliant": True}
        ],
        "commit_attributions": [
            {
                "commit_sha": "abcdef1234567",
                "phase_id": "phase_001",
                "rationale": "first phase introduced this file",
            }
        ],
    }
    jsonschema.validate(instance=verdict, schema=schema)


def test_council_synthesizer_canonical_template_specifies_checklist_format() -> None:
    """The canonical synthesizer template MUST instruct McLoop-
    executable output (- [ ] task lines per phase). The Slice D
    smoke against the merged template produced narrative-prose
    plans McLoop could not run; this test pins the regression so a
    future template edit cannot drop the contract.
    """
    template_path = (
        Path(__file__).parent.parent
        / "orchestra"
        / "workflows"
        / "templates"
        / "council_synthesizer_canonical.md"
    )
    body = template_path.read_text()
    assert "- [ ]" in body
    assert "checklist" in body.lower()
    assert "McLoop-executable" in body


def test_council_four_alias_deprecated() -> None:
    """The merged council_four name remains as an alias for one
    release with a DeprecationWarning. The workflow file is still
    loadable so existing callers do not break; new callers must
    use the canonical or reauthor name.
    """
    import warnings

    from orchestra.api import run_workflow

    # The DeprecationWarning fires unconditionally inside
    # run_workflow when name == "council_four"; triggering the
    # full execution path is expensive, so we patch in a stub
    # config and intercept after the warning emits but before
    # the workflow body runs. The simplest reliable assertion is
    # to call run_workflow with a config that will fail late,
    # catch the inevitable error, and check the warning fired.
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        try:
            run_workflow(
                "council_four",
                inputs={},
                config={"workflows": {"council_four": {"pattern": "council_four"}}},
            )
        except Exception:  # noqa: BLE001 -- we expect downstream failure
            pass
    deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert any("council_four" in str(w.message) for w in deprecations), (
        f"expected DeprecationWarning naming council_four, got {[str(w.message) for w in caught]}"
    )


def test_council_schema_lineage_shape() -> None:
    """The verdict schema gains a lineage object whose phases[] entries
    carry a strict action enum and an optional from list.
    council_four's re-author consumer parses this sidecar instead of
    inferring lineage from markdown HTML comments.
    """
    path = (
        Path(__file__).parent.parent
        / "orchestra"
        / "workflows"
        / "schemas"
        / "council_synthesis_verdict.json"
    )
    schema = json.loads(path.read_text())
    lineage = schema["properties"]["lineage"]
    assert lineage["type"] == "object"
    assert lineage["required"] == ["phases"]
    phase_item = lineage["properties"]["phases"]["items"]
    assert set(phase_item["required"]) == {"id", "action"}
    assert phase_item["properties"]["action"]["enum"] == [
        "preserve",
        "supersede",
        "split",
        "merge",
        "new",
    ]
    assert phase_item["properties"]["from"]["type"] == "array"
    abandoned_item = lineage["properties"]["abandoned"]["items"]
    assert set(abandoned_item["required"]) == {"id", "reason"}
    # lineage itself is OPTIONAL at the verdict level (canonical-mode
    # synthesis without a prior plan still works without one);
    # presence is a duplo-side requirement on the re-author path.
    assert "lineage" not in schema["required"]


# --------------------------------------------------------------------
# Distinct-actor invariant
# --------------------------------------------------------------------


def _binding(adapter: str, model: str | None) -> RoleBinding:
    return RoleBinding(adapter=adapter, model=model)


def _make_council_config(
    proposers: dict[str, RoleBinding] | None = None,
    synthesizer: RoleBinding | None = None,
    framer: RoleBinding | None = None,
) -> OrchestraConfig:
    default_proposers = {
        "proposer_code":     _binding("claude_code_text",          "sonnet"),
        "proposer_codex":    _binding("codex_text",                "gpt-5.5"),
        "proposer_kimi":     _binding("claude_code_text_kimi",     "kimi-k2.6"),
        "proposer_deepseek": _binding("claude_code_text_deepseek", "deepseek-v4-pro"),
    }
    if proposers:
        default_proposers.update(proposers)
    return OrchestraConfig(
        roles={
            "framer": framer or _binding("claude_code_text", "haiku"),
            **default_proposers,
            "synthesizer": (
                synthesizer or _binding("claude_code_text", "opus")
            ),
        },
        workflows={
            "council_four": WorkflowConfig(pattern="council_four"),
        },
    )


def _load_council_workflow() -> Workflow:
    path = resolve_workflow_path("council_four", project_dir=None)
    return load_workflow(path, _pre_load_registry())


def test_distinct_actor_rule_passes_with_five_distinct() -> None:
    cfg = _make_council_config()
    workflow = _load_council_workflow()
    bindings = _validate_role_bindings(workflow, "council_four", cfg)
    assert set(bindings) == {
        "framer",
        "proposer_code",
        "proposer_codex",
        "proposer_kimi",
        "proposer_deepseek",
        "synthesizer",
    }


def test_distinct_actor_rule_passes_with_synthesizer_sharing_proposer_model() -> None:
    """Synthesizer MAY share a model string with a proposer.

    The original distinct-actor rule conflated role-binding distinctness
    (each role has its own template and conversation context) with
    model-string distinctness (no two roles use the same model). The
    same-model-judging concern that motivated the rule applies to
    single-prompt self-evaluation, not synthesis across four parallel
    proposals under a synthesis-specific prompt.

    Concretely: proposer_code = (claude_code_text, opus) and
    synthesizer = (claude_code_text, opus) is now a valid configuration.
    See ``design/council-actor-bindings.md``.
    """
    cfg = _make_council_config(
        proposers={
            "proposer_code": _binding("claude_code_text", "opus"),
        },
        synthesizer=_binding("claude_code_text", "opus"),
    )
    workflow = _load_council_workflow()
    bindings = _validate_role_bindings(workflow, "council_four", cfg)
    assert bindings["proposer_code"].model == "opus"
    assert bindings["synthesizer"].model == "opus"


def test_distinct_actor_rule_rejects_proposer_overlap() -> None:
    """Two proposers resolving to the same actor is also rejected."""
    cfg = _make_council_config(
        proposers={
            "proposer_kimi": _binding("codex_text", "gpt-5.5"),
        },
    )
    workflow = _load_council_workflow()
    with pytest.raises(ConfigError, match="distinct"):
        _validate_role_bindings(workflow, "council_four", cfg)


def test_distinct_actor_rule_missing_synthesizer_fails() -> None:
    cfg = OrchestraConfig(
        roles={
            "framer":            _binding("claude_code_text", "haiku"),
            "proposer_code":     _binding("claude_code_text", "sonnet"),
            "proposer_codex":    _binding("codex_text", "gpt-5.5"),
            "proposer_kimi":     _binding("claude_code_text_kimi", "kimi-k2.6"),
            "proposer_deepseek": _binding("claude_code_text_deepseek", "deepseek-v4-pro"),
        },
        workflows={
            "council_four": WorkflowConfig(pattern="council_four"),
        },
    )
    workflow = _load_council_workflow()
    with pytest.raises(ConfigError, match="synthesizer"):
        _validate_role_bindings(workflow, "council_four", cfg)


def test_framer_can_match_a_proposer() -> None:
    """Framer's identity is unconstrained; the rule only polices the
    four proposers and the synthesizer."""
    cfg = _make_council_config(
        framer=_binding("claude_code_text", "sonnet"),  # same as proposer_code
    )
    workflow = _load_council_workflow()
    # Should not raise.
    bindings = _validate_role_bindings(workflow, "council_four", cfg)
    assert "framer" in bindings


# --------------------------------------------------------------------
# End-to-end: scripted accept path
# --------------------------------------------------------------------


def _accept_verdict() -> str:
    return json.dumps(
        {
            "decision": "accept",
            "feedback": "all proposals converge on the same plan shape",
            "agreements": [
                "single propose-review-judge cycle",
                "deterministic acceptance criteria",
            ],
            "disagreements": [
                {
                    "topic": "whether to retry on stuck",
                    "positions": [
                        "yes, with bounded retries",
                        "no, surface stuck to caller",
                    ],
                }
            ],
            "rejected_options": ["unbounded retry budget"],
        }
    )


def test_council_e2e_fan_out_and_synthesize_to_done(tmp_path: Path) -> None:
    """Frame, four proposers in parallel, synthesizer accept => done."""
    responses = {
        "frame": ["COUNCIL BRIEF: the question, restated."],
        "propose_code":     ["proposal from code: phased rollout."],
        "propose_codex":    ["proposal from codex: phased rollout."],
        "propose_kimi":     ["proposal from kimi: single-pass authoring."],
        "propose_deepseek": ["proposal from deepseek: phased rollout."],
        "synthesize": [_accept_verdict()],
    }
    adapter, run_dir, terminal, store = _run_council(
        tmp_path, responses=responses
    )
    try:
        assert terminal == "done"
        states_called = [c["state_id"] for c in adapter.calls]
        # frame first, then four proposers in some order, then synthesize.
        assert states_called[0] == "frame"
        assert set(states_called[1:5]) == {
            "propose_code",
            "propose_codex",
            "propose_kimi",
            "propose_deepseek",
        }
        assert states_called[5] == "synthesize"

        # Each proposer's artifact landed independently.
        for art_name in (
            "proposal_code",
            "proposal_codex",
            "proposal_kimi",
            "proposal_deepseek",
        ):
            v = store.read_latest(art_name)
            assert v is not None and "proposal" in str(v.value)

        # The synthesizer's verdict is a valid council verdict.
        verdict = store.read_latest("judge_verdict")
        assert verdict is not None
        assert verdict.value["decision"] == "accept"
        assert verdict.value["agreements"]
        assert verdict.value["disagreements"][0]["topic"]
        assert verdict.value["rejected_options"]
        # decision and feedback got extracted.
        decision = store.read_latest("judge_decision")
        feedback = store.read_latest("judge_feedback")
        assert decision is not None and decision.value == "accept"
        assert feedback is not None and "converge" in feedback.value
    finally:
        store.close()


def test_council_synthesizer_reads_all_four_proposals(tmp_path: Path) -> None:
    """Synthesizer's prompt must contain content from each proposer."""
    responses = {
        "frame": ["COUNCIL BRIEF"],
        "propose_code":     ["UNIQUE-MARK-CODE"],
        "propose_codex":    ["UNIQUE-MARK-CODEX"],
        "propose_kimi":     ["UNIQUE-MARK-KIMI"],
        "propose_deepseek": ["UNIQUE-MARK-DEEPSEEK"],
        "synthesize": [_accept_verdict()],
    }
    adapter, _, terminal, store = _run_council(
        tmp_path, responses=responses
    )
    try:
        assert terminal == "done"
        synth_call = next(c for c in adapter.calls if c["state_id"] == "synthesize")
        prompt = synth_call["prompt"]
        for marker in (
            "UNIQUE-MARK-CODE",
            "UNIQUE-MARK-CODEX",
            "UNIQUE-MARK-KIMI",
            "UNIQUE-MARK-DEEPSEEK",
        ):
            assert marker in prompt, (
                f"synthesizer prompt missing {marker!r}"
            )
    finally:
        store.close()


# --------------------------------------------------------------------
# End-to-end: F2.5a accept-consistency catches a non-compliant accept
# --------------------------------------------------------------------


def test_council_accept_with_noncompliant_violates(tmp_path: Path) -> None:
    """Synthesizer claims accept while reporting compliant=false on a
    required criterion. Runtime decision-consistency invariant catches
    it; state exits via error outcome; terminal=stop."""
    responses = {
        "frame": ["COUNCIL BRIEF"],
        "propose_code":     ["proposal A"],
        "propose_codex":    ["proposal B"],
        "propose_kimi":     ["proposal C"],
        "propose_deepseek": ["proposal D"],
        "synthesize": [
            json.dumps(
                {
                    "decision": "accept",
                    "feedback": "claiming compliance",
                    "agreements": ["everyone agreed"],
                    "disagreements": [],
                    "rejected_options": [],
                    "criteria_compliance": [
                        {
                            "criterion_id": "must_have_phases",
                            "observed_value": "no phases",
                            "compliant": False,
                        }
                    ],
                }
            )
        ],
    }
    criteria = (
        CriterionDecl(
            id="must_have_phases",
            description="Plan defines explicit phases.",
            required=True,
        ),
    )
    _, run_dir, terminal, store = _run_council(
        tmp_path, responses=responses, criteria=criteria
    )
    try:
        assert terminal == "stop"
        records = LogReader(run_dir / "log.jsonl").read_all()
        consistency_events = [
            r for r in records if r.event == "decision_consistency"
        ]
        assert len(consistency_events) == 1
        fields = consistency_events[0].fields
        assert fields["outcome"] == "violation"
        assert fields["reason"] == "accept_with_noncompliant"
        assert fields["noncompliant_required_ids"] == ["must_have_phases"]
    finally:
        store.close()
