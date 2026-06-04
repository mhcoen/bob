"""Tests for ``duplo.plan_author_adapter.run_plan_author``."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from duplo.plan_author_adapter import (
    PlanAuthorCappedError,
    PlanAuthorRunError,
    PriorPhaseContext,
    build_history,
    run_plan_author,
)
from orchestra import ErrorRecord, IterativeDesignResult


def _make_result(
    termination: str,
    *,
    final_artifact: str = "",
    rounds_completed: int = 0,
    error: ErrorRecord | None = None,
    transcript_path: Path = Path("/tmp/transcript.jsonl"),
    run_id: str = "run-id",
) -> IterativeDesignResult:
    return IterativeDesignResult(
        termination=termination,  # type: ignore[arg-type]
        rounds_completed=rounds_completed,
        final_artifact=final_artifact,
        transcript=[],
        transcript_path=transcript_path,
        run_id=run_id,
        error=error,
    )


def test_converged_returns_proposal_body():
    fake = _make_result("CONVERGED", final_artifact="## Phase phase_001: x\n", rounds_completed=2)
    with patch("orchestra.run_role", return_value=fake) as m:
        body = run_plan_author(
            prompt="reference material",
            system="system directive",
            required_phase_id="phase_001",
        )
    assert body == "## Phase phase_001: x\n"
    m.assert_called_once()
    assert m.call_args.args == ("plan_author",)
    kwargs = m.call_args.kwargs
    assert kwargs["required_phase_id"] == "phase_001"
    assert callable(kwargs.get("registry_customizer"))
    assert callable(kwargs.get("progress_callback"))


def test_passes_criteria_block_rendered_from_binding():
    """The adapter threads the configured criteria into the judge prompt
    via the ``criteria_block`` input, rendered from the same
    ``PLAN_AUTHOR_CRITERIA`` that reaches the executor -- so the judge is
    asked for exactly the ids the consistency check enforces."""
    from duplo.plan_author_role import PLAN_AUTHOR_CRITERIA, render_criteria_block

    captured: dict[str, str] = {}

    def fake_run_role(role_name, **kwargs):
        captured["criteria_block"] = kwargs["criteria_block"]
        return _make_result("CONVERGED", final_artifact="body")

    with patch("orchestra.run_role", side_effect=fake_run_role):
        run_plan_author(
            prompt="reference material",
            system="system directive",
            required_phase_id="phase_001",
        )

    block = captured["criteria_block"]
    assert block == render_criteria_block()
    # Every configured id appears so the judge can emit one entry per id.
    for criterion in PLAN_AUTHOR_CRITERIA:
        assert criterion["id"] in block


def test_capped_raises_and_returns_no_plan():
    fake = _make_result(
        "CAPPED",
        final_artifact="best-so-far invalid body",
        rounds_completed=6,
        run_id="capped-run",
        transcript_path=Path("/tmp/capped/transcript.jsonl"),
    )
    with patch("orchestra.run_role", return_value=fake):
        with pytest.raises(PlanAuthorCappedError) as exc_info:
            run_plan_author(
                prompt="reference material",
                system="system directive",
                required_phase_id="phase_001",
            )
    raised = exc_info.value
    # Fail-closed: the best-so-far is retained for audit, never as a plan.
    assert raised.best_so_far == "best-so-far invalid body"
    assert raised.run_id == "capped-run"
    assert raised.transcript_path == Path("/tmp/capped/transcript.jsonl")
    assert "CAPPED" in str(raised)


def test_error_raises_with_transcript_path():
    err = ErrorRecord(kind="adapter_failure", message="boom", state="judge")
    fake = _make_result(
        "ERROR",
        error=err,
        transcript_path=Path("/tmp/some/transcript.jsonl"),
        run_id="error-run",
    )
    with patch("orchestra.run_role", return_value=fake):
        with pytest.raises(PlanAuthorRunError) as exc_info:
            run_plan_author(
                prompt="reference material",
                system="system directive",
                required_phase_id="phase_001",
            )
    raised = exc_info.value
    assert raised.error is err
    assert raised.transcript_path == Path("/tmp/some/transcript.jsonl")
    assert raised.run_id == "error-run"
    message = str(raised)
    assert "error-run" in message
    assert "boom" in message
    assert "/tmp/some/transcript.jsonl" in message


def test_error_without_error_record_still_raises():
    fake = _make_result("ERROR", error=None, run_id="no-record")
    with patch("orchestra.run_role", return_value=fake):
        with pytest.raises(PlanAuthorRunError) as exc_info:
            run_plan_author(
                prompt="reference material",
                system="system directive",
                required_phase_id="phase_001",
            )
    assert exc_info.value.error.kind == "runner_failure"
    assert exc_info.value.run_id == "no-record"


def test_config_missing_translates_to_run_error():
    from orchestra.api import WorkflowApiError

    def fake_run_role(*args, **kwargs):
        raise WorkflowApiError("unknown role 'plan_author'. Configured role_bindings: []")

    with patch("orchestra.run_role", side_effect=fake_run_role):
        with pytest.raises(PlanAuthorRunError) as exc_info:
            run_plan_author(
                prompt="reference material",
                system="system directive",
                required_phase_id="phase_001",
            )
    assert exc_info.value.error.kind == "config_missing"
    assert "unknown role" in exc_info.value.error.message


def test_history_carries_compact_fields_not_full_source():
    context = PriorPhaseContext(
        phases=(("phase_001", "Bootstrap"), ("phase_002", "Core")),
        completed_summaries=("phase_001 scaffolded the project",),
        files_created=("duplo/main.py", "pyproject.toml"),
        validation_failures=("required_phase_id 'phase_003' not present",),
    )
    captured: dict[str, str] = {}

    def fake_run_role(role_name, **kwargs):
        captured["query"] = kwargs["query"]
        captured["history"] = kwargs["history"]
        return _make_result("CONVERGED", final_artifact="body")

    with patch("orchestra.run_role", side_effect=fake_run_role):
        run_plan_author(
            prompt="CURRENT PHASE SOURCE SPEC",
            system="system directive",
            required_phase_id="phase_003",
            history=context,
        )

    history = captured["history"]
    # Compact prior-phase fields are present.
    assert "phase_001" in history
    assert "Bootstrap" in history
    assert "phase_002" in history
    assert "scaffolded the project" in history
    assert "duplo/main.py" in history
    assert "required_phase_id 'phase_003' not present" in history
    # The current phase's source/spec stays in query, never in history.
    assert "CURRENT PHASE SOURCE SPEC" not in history
    assert "CURRENT PHASE SOURCE SPEC" in captured["query"]
    assert "system directive" in captured["query"]


def test_build_history_empty_context_is_empty_string():
    assert build_history(PriorPhaseContext()) == ""
