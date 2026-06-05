"""End-to-end tests for the iterative PLAN.md authoring path.

Unlike ``tests/test_plan_author_adapter.py`` (which mocks
``orchestra.run_role`` and asserts the adapter's translation in
isolation) and ``tests/test_plan_author_workflow.py`` (which only
parses ``plan_author.orc``), these tests drive the WHOLE iterative
authoring path with the real Orchestra executor: ``generate_phase_plan``
-> ``run_plan_author`` -> ``orchestra.run_role`` -> the
proposer/reviewer/judge/validate state machine -> the real
``validate_plan_body`` gate -> the ``typed_plan_from_synthesizer_text``
-> ``save_plan`` persistence tail. Only the three LLM leaf actors
(proposer, reviewer, judge) are mocked, by a scripted model adapter
returning canned per-state responses; no subprocess or network call is
made.

What is exercised:

  - The validation-state feedback loop. A first proposer draft that
    fails canonical validation (wrong phase id) routes back to the
    proposer with the gate's ``validation_feedback``; the next draft
    uses the runtime phase id and converges. We assert the proposer
    actually SEES that feedback on the retry round (and not before).
  - The converged body persists to a canonical PLAN.md carrying the
    runtime-supplied phase id.
  - A body that never validates within ``max_rounds`` fails closed:
    ``PlanAuthorCappedError`` is raised, no PLAN.md is written, and the
    workflow terminates at ``done`` (CAPPED) rather than looping to
    ``stop`` (which would be derived as ERROR).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

import duplo
from duplo import planner
from duplo import plan_author_adapter
from duplo.extractor import Feature
from duplo.init import _ORCHESTRA_COUNCIL_CONFIG
from duplo.plan_author_adapter import PlanAuthorCappedError, PlanAuthorRunError
from duplo.plan_author_role import PLAN_AUTHOR_CRITERIA
from duplo.questioner import BuildPreferences

_WORKFLOWS_SRC = Path(duplo.__file__).resolve().parent / "workflows"

# A wrong-phase-id body: the runtime computes ``phase_001`` for an empty
# project, so this fails the canonical-validation gate and triggers the
# feedback loop. The fragment ``phase_999`` is what the gate echoes back.
_WRONG_BODY = "## Phase phase_999: Wrong\n\n- [ ] do the thing [accept: command-exit: true]\n"
# A canonical body that uses the runtime-supplied phase id and validates.
_VALID_BODY = (
    "## Phase phase_001: Bring up scaffold\n\n- [ ] do the thing [accept: command-exit: true]\n"
)

# Substring of the gate's wrong-phase-id feedback. It cannot appear in
# the proposer's first-round prompt (it is produced only after a draft
# fails validation), so it cleanly marks the retry round.
_FEEDBACK_MARK = "not present in synthesized plan body"


class _ScriptedModelAdapter:
    """Model backing that returns canned per-state responses.

    Each workflow state (``propose`` / ``review`` / ``judge``) has its
    own response queue keyed by ``state_id``; each invocation pops the
    next entry. Captures every invocation's rendered prompt so a test
    can assert what the proposer actually received on each round. Makes
    no real LLM call.
    """

    backing = "model"

    def __init__(self, responses: dict[str, list[str]]) -> None:
        self._responses = {k: list(v) for k, v in responses.items()}
        self.calls: list[dict[str, Any]] = []

    def prepare(self, request: Any) -> Any:
        from orchestra.spine import PreparedInvocation

        prompt = request.prompt_artifact or ""
        self.calls.append({"state_id": request.state_id, "prompt": prompt})
        return PreparedInvocation(
            request=request,
            summary={"kind": "model"},
            inner={"state_id": request.state_id, "prompt": prompt},
        )

    def invoke(self, prepared: Any) -> dict[str, Any]:
        state_id = prepared.inner["state_id"]
        queue = self._responses.get(state_id) or []
        if not queue:
            raise AssertionError(f"scripted adapter has no response for {state_id!r}")
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

    def cancel(self, prepared: Any) -> None:
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

    def proposer_prompts(self) -> list[str]:
        return [c["prompt"] for c in self.calls if c["state_id"] == "propose"]


def _verdict(decision: str, *, compliant: bool = True) -> str:
    """A judge verdict reporting exactly the configured criterion ids.

    The role declares three required acceptance criteria; every verdict
    must report a ``criteria_compliance`` entry for each one (no missing,
    no extra ids) or the executor's decision-consistency check rejects it.
    Sourcing the ids from :data:`PLAN_AUTHOR_CRITERIA` -- the same tuple the
    binding feeds the executor -- means the canned verdict carries precisely
    the ids the runtime check expects, so these tests track the live
    configuration instead of a hardcoded copy that could drift.
    """
    return json.dumps(
        {
            "decision": decision,
            "feedback": "ok",
            "criteria_compliance": [
                {"criterion_id": c["id"], "observed_value": "ok", "compliant": compliant}
                for c in PLAN_AUTHOR_CRITERIA
            ],
        }
    )


def _accept_verdict() -> str:
    """A judge ``accept`` verdict that satisfies the plan_author role's
    decision-consistency invariant (every required criterion compliant)."""
    return _verdict("accept", compliant=True)


def _deploy_project(project_dir: Path, *, max_rounds: int | None = None) -> None:
    """Lay down a duplo-managed project: the plan_author workflow assets
    plus ``.orchestra/config.json`` binding the ``plan_author`` role.

    ``max_rounds`` overrides the role's round cap so the CAPPED path can
    be reached in a couple of rounds instead of the default six.
    """
    workflows_dst = project_dir / ".orchestra" / "workflows"
    workflows_dst.mkdir(parents=True, exist_ok=True)
    shutil.copytree(_WORKFLOWS_SRC, workflows_dst, dirs_exist_ok=True)

    config = json.loads(json.dumps(_ORCHESTRA_COUNCIL_CONFIG, default=str))
    if max_rounds is not None:
        config["role_bindings"]["plan_author"]["max_rounds"] = max_rounds
    (project_dir / ".orchestra" / "config.json").write_text(json.dumps(config), encoding="utf-8")


def _install_scripted_adapter(
    monkeypatch: pytest.MonkeyPatch, adapter: _ScriptedModelAdapter
) -> None:
    """Route every model-kind actor through ``adapter`` while keeping the
    real ``validate_plan_body`` transform.

    ``run_plan_author`` registers the gate via
    ``register_validate_plan_body`` through ``run_role``'s
    ``registry_customizer`` hook. We wrap that callback so it ALSO swaps
    the runtime registry's ``model`` backing for the scripted adapter --
    the only injection seam the adapter exposes -- leaving the transform
    registration (and the rest of the real executor) intact.
    """
    real_register = plan_author_adapter.register_validate_plan_body

    def patched_register(required_phase_id: str):
        inner = real_register(required_phase_id)

        def customizer(registry: Any) -> None:
            inner(registry)
            registry.actor_backings["model"] = lambda: adapter
            registry._adapter_cache.pop("model", None)

        return customizer

    monkeypatch.setattr(plan_author_adapter, "register_validate_plan_body", patched_register)


def _build_inputs() -> tuple[list[Feature], BuildPreferences, dict[str, Any]]:
    features = [Feature(name="X", description="d", category="c")]
    prefs = BuildPreferences(platform="macos", language="swift", constraints=[], preferences=[])
    phase = {"phase": 1, "title": "Bring up scaffold", "goal": "g", "features": ["X"], "test": ""}
    return features, prefs, phase


@pytest.fixture
def _isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point HOME at a throwaway dir so config merge and run storage stay
    hermetic (no real ``~/.orchestra`` is read or written)."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


def _final_transition(transcript_path: Path) -> dict[str, Any]:
    """Return the last ``transition`` record from the run log that sits
    next to ``transcript_path``."""
    log_path = transcript_path.parent / "log.jsonl"
    last: dict[str, Any] = {}
    for line in log_path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record.get("event") == "transition":
            last = record
    return last


def test_converges_through_validation_feedback_to_canonical_plan(
    _isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A first draft with the wrong phase id fails the gate; the gate's
    feedback reaches the proposer, whose next draft uses the runtime
    phase id and converges. The converged body persists as a canonical
    PLAN.md carrying that phase id."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    _deploy_project(project_dir)

    adapter = _ScriptedModelAdapter(
        {
            "propose": [_WRONG_BODY, _VALID_BODY],
            "review": ["looks fine", "looks fine"],
            "judge": [_accept_verdict(), _accept_verdict()],
        }
    )
    _install_scripted_adapter(monkeypatch, adapter)

    features, prefs, phase = _build_inputs()
    plan = planner.generate_phase_plan(
        "http://example.com",
        features,
        prefs,
        phase=phase,
        project_name="App",
        target_dir=project_dir,
    )

    # The proposer was invoked twice (initial draft + one revision) and
    # received the gate's wrong-phase-id feedback ONLY on the retry.
    proposer_prompts = adapter.proposer_prompts()
    assert len(proposer_prompts) == 2
    assert _FEEDBACK_MARK not in proposer_prompts[0]
    assert "phase_999" not in proposer_prompts[0]
    assert _FEEDBACK_MARK in proposer_prompts[1]
    assert "phase_999" in proposer_prompts[1]

    # The converged plan persists as a canonical PLAN.md with the
    # runtime-supplied phase id.
    plan_path = planner.save_plan(plan, target_dir=project_dir)
    assert plan_path == (project_dir / "PLAN.md").resolve()
    from bob_tools.planfile import load as planfile_load

    persisted = planfile_load(plan_path)  # raises if not canonical
    assert [p.phase_id for p in persisted.phases] == ["phase_001"]

    text = plan_path.read_text(encoding="utf-8")
    assert "phase_id: phase_001" in text
    assert "do the thing" in text


def test_never_validating_body_caps_fail_closed_without_writing_plan(
    _isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A draft that never validates within ``max_rounds`` fails closed:
    ``PlanAuthorCappedError`` (not ``PlanAuthorRunError``), no PLAN.md
    write, and the workflow terminates at ``done`` (CAPPED) rather than
    spinning to ``stop`` (ERROR)."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    _deploy_project(project_dir, max_rounds=2)

    adapter = _ScriptedModelAdapter(
        {
            "propose": [_WRONG_BODY, _WRONG_BODY, _WRONG_BODY],
            "review": ["looks fine", "looks fine", "looks fine"],
            "judge": [_accept_verdict(), _accept_verdict(), _accept_verdict()],
        }
    )
    _install_scripted_adapter(monkeypatch, adapter)

    features, prefs, phase = _build_inputs()
    with pytest.raises(PlanAuthorCappedError) as exc_info:
        planner.generate_phase_plan(
            "http://example.com",
            features,
            prefs,
            phase=phase,
            project_name="App",
            target_dir=project_dir,
        )

    raised = exc_info.value
    # Fail-closed disposition is CAPPED, never ERROR.
    assert not isinstance(raised, PlanAuthorRunError)
    # The best-so-far body is retained for audit only; it is the
    # still-invalid draft, never written as a plan.
    assert "phase_999" in raised.best_so_far

    # No PLAN.md was written.
    assert not (project_dir / "PLAN.md").exists()

    # At the workflow level, the cap routed the never-valid body to the
    # terminal ``done`` (the CAPPED disposition), not ``stop`` (which
    # run_role would derive as ERROR).
    final = _final_transition(raised.transcript_path)
    assert final.get("target") == "done"
    assert final.get("outcome") not in {"error", "stuck", "timeout", "cancelled"}


def test_iterate_verdict_with_configured_ids_survives_without_missing_ids(
    _isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Regression for the plan_author ``missing_ids`` bug, exercised through
    the WHOLE real loop: a judge ``iterate`` verdict that reports exactly the
    three configured criterion ids clears the executor's decision-consistency
    check, routes back to the proposer (``attempts.judge < max_rounds``), and
    the next round's ``accept`` converges to a canonical PLAN.md.

    Had the verdict reported the wrong ids (the original bug, where the judge
    invented ids from the ``_PHASE_SYSTEM`` prose), the consistency check
    would flag ``missing_ids``, drive the judge state through its error
    outcome, and ``generate_phase_plan`` would raise ``PlanAuthorRunError``.
    So a clean convergence here is the regression guard: the loop SURVIVES
    the iterate verdict.
    """
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    _deploy_project(project_dir)

    # First judge call iterates (a required criterion non-compliant, the
    # realistic iterate shape); the loop must survive it and re-draft. The
    # second judge call accepts and the body validates.
    adapter = _ScriptedModelAdapter(
        {
            "propose": [_VALID_BODY, _VALID_BODY],
            "review": ["needs work", "looks fine"],
            "judge": [_verdict("iterate", compliant=False), _accept_verdict()],
        }
    )
    _install_scripted_adapter(monkeypatch, adapter)

    features, prefs, phase = _build_inputs()
    # No PlanAuthorRunError: the iterate verdict's ids matched the configured
    # set, so no missing_ids violation routed the judge to its error outcome.
    plan = planner.generate_phase_plan(
        "http://example.com",
        features,
        prefs,
        phase=phase,
        project_name="App",
        target_dir=project_dir,
    )

    # The iterate verdict looped the loop: judge ran twice and the proposer
    # was re-invoked for the post-iterate re-draft.
    judge_calls = [c for c in adapter.calls if c["state_id"] == "judge"]
    assert len(judge_calls) == 2
    assert len(adapter.proposer_prompts()) == 2

    # The verdict the loop survived reported precisely the configured ids --
    # no more, no fewer -- which is why the consistency check passed.
    first_verdict = json.loads(_verdict("iterate", compliant=False))
    emitted_ids = [e["criterion_id"] for e in first_verdict["criteria_compliance"]]
    assert emitted_ids == [c["id"] for c in PLAN_AUTHOR_CRITERIA]
    assert len(emitted_ids) == 3

    # And the converged body persists as a canonical PLAN.md.
    plan_path = planner.save_plan(plan, target_dir=project_dir)
    from bob_tools.planfile import load as planfile_load

    persisted = planfile_load(plan_path)
    assert [p.phase_id for p in persisted.phases] == ["phase_001"]
