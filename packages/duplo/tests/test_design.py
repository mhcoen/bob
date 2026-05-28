"""Tests for ``duplo.design.run_iterative_design``."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from duplo.design import IterativeDesignError, run_iterative_design
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


def test_converged_returns_artifact():
    fake = _make_result("CONVERGED", final_artifact="final text", rounds_completed=2)
    with patch("orchestra.run_role", return_value=fake) as m:
        result = run_iterative_design("seed")
    assert result == "final text"
    m.assert_called_once()
    assert m.call_args.args == ("design",)
    assert m.call_args.kwargs["seed_input"] == "seed"
    assert callable(m.call_args.kwargs.get("progress_callback"))


def test_capped_returns_artifact_and_logs_warning(caplog):
    fake = _make_result(
        "CAPPED",
        final_artifact="partial",
        rounds_completed=4,
        run_id="capped-run",
    )
    with patch("orchestra.run_role", return_value=fake):
        with caplog.at_level(logging.WARNING, logger="duplo.design"):
            result = run_iterative_design("seed")
    assert result == "partial"
    matches = [r for r in caplog.records if "did not converge" in r.getMessage()]
    assert matches, "expected a 'did not converge' warning on duplo.design"
    msg = matches[0].getMessage()
    assert "capped-run" in msg
    assert "max_rounds" in msg


def test_error_raises_iterative_design_error():
    err = ErrorRecord(kind="adapter_failure", message="boom", state="judge")
    fake = _make_result(
        "ERROR",
        error=err,
        transcript_path=Path("/tmp/some/transcript.jsonl"),
        run_id="error-run",
    )
    with patch("orchestra.run_role", return_value=fake):
        with pytest.raises(IterativeDesignError) as exc_info:
            run_iterative_design("seed")
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
        with pytest.raises(IterativeDesignError) as exc_info:
            run_iterative_design("seed")
    assert exc_info.value.error.kind == "runner_failure"
    assert exc_info.value.run_id == "no-record"


def test_run_iterative_design_translates_config_missing_to_iterative_design_error(monkeypatch):
    """When orchestra.run_role raises WorkflowApiError (e.g. no 'design' role
    configured), run_iterative_design must translate that to IterativeDesignError
    so callers like extract_design that handle IterativeDesignError uniformly
    see the same exception type for both runtime and config-time failures."""
    from orchestra.api import WorkflowApiError

    def fake_run_role(*args, **kwargs):
        raise WorkflowApiError("unknown role 'design'. Configured role_bindings: []")

    monkeypatch.setattr("duplo.design.orchestra.run_role", fake_run_role)

    with pytest.raises(IterativeDesignError) as exc_info:
        run_iterative_design("any seed")

    assert exc_info.value.error.kind == "config_missing"
    assert "unknown role" in exc_info.value.error.message
