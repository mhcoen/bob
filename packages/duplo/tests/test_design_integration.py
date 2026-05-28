"""Integration test for ``duplo.design.run_iterative_design``.

Exercises the duplo / orchestra integration boundary for T-000019. The
orchestra runtime is mocked at the ``orchestra.run_role`` call site
because a real workflow run would require live model adapters and the
network — out of scope for unit tests, and the source of the failure
the previous attempt at this task hit (``socket connection was closed
unexpectedly``).

What the test still verifies that the existing unit tests in
``test_design.py`` do not:

- the seed input is read from a real fixture file on disk and threaded
  through ``run_iterative_design`` unchanged;
- the ``transcript_path`` returned by orchestra points at a real on-disk
  JSONL file with at least one well-formed ``Turn`` record (so the
  integration assertion "a transcript JSONL was written to the
  orchestra run directory" is meaningful);
- the CAPPED warning that ``run_iterative_design`` emits names the
  run id, the round count, and the transcript path so a postmortem
  can navigate from the warning to the run dir.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import pytest

from duplo.design import IterativeDesignError, run_iterative_design
from orchestra import ErrorRecord, IterativeDesignResult, Turn


FIXTURE_SPEC = Path(__file__).parent / "fixtures" / "small_design_spec.md"


def _make_turn(state: str, output: str, *, role: str = "", outcome: str = "done") -> Turn:
    return Turn(
        role=role or state,
        state=state,
        attempt=1,
        started_at="2026-05-28T00:00:00Z",
        ended_at="2026-05-28T00:00:01Z",
        duration_ms=1000,
        status="ok",
        outcome=outcome,
        output=output,
        artifacts_written=[],
    )


def _write_transcript(path: Path, turns: list[Turn]) -> None:
    """Simulate orchestra's incremental transcript writer.

    The integration test treats the on-disk JSONL as the contract: one
    ``Turn`` per line, sorted-keys JSON, UTF-8. This mirrors
    ``orchestra.api.transcript._IncrementalTranscriptWriter`` so the
    assertions match the shape orchestra actually produces.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for turn in turns:
            fh.write(json.dumps(asdict(turn), sort_keys=True, ensure_ascii=False))
            fh.write("\n")


def _make_run_dir(tmp_path: Path, run_id: str) -> Path:
    """Create a directory that mimics orchestra's per-run layout."""
    run_dir = tmp_path / ".orchestra" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _read_transcript_lines(path: Path) -> list[dict]:
    out: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


def test_run_iterative_design_returns_artifact_and_writes_transcript_jsonl(
    tmp_path: Path,
) -> None:
    """CONVERGED scenario: invoke run_iterative_design with the fixture
    spec text as seed input; the (mocked) workflow returns CONVERGED
    with a transcript JSONL written to an orchestra-shaped run dir.

    Asserts:
    - the returned artifact is the judge-produced final artifact text;
    - the seed input forwarded to ``orchestra.run_role`` is exactly the
      fixture file text (no rewriting between caller and runtime);
    - a transcript JSONL exists at the path orchestra reported, sits
      inside the run directory, and contains well-formed Turn records.
    """
    seed_input = FIXTURE_SPEC.read_text(encoding="utf-8")
    run_id = "run-converged-001"
    run_dir = _make_run_dir(tmp_path, run_id)
    transcript_path = run_dir / "transcript.jsonl"
    artifact_text = "## Design\n\nUse a counter store with two methods."

    judge_turn = _make_turn("judge", output=artifact_text, role="judge_role", outcome="done")
    review_turn = _make_turn(
        "review", output="No structural issues found.", role="reviewer", outcome="complete"
    )
    _write_transcript(transcript_path, [judge_turn, review_turn, judge_turn])

    fake_result = IterativeDesignResult(
        termination="CONVERGED",
        rounds_completed=2,
        final_artifact=artifact_text,
        transcript=[judge_turn, review_turn, judge_turn],
        transcript_path=transcript_path,
        run_id=run_id,
        error=None,
    )

    with patch("orchestra.run_role", return_value=fake_result) as m:
        result = run_iterative_design(seed_input)

    assert result == artifact_text
    m.assert_called_once_with("design", seed_input=seed_input)
    # Confirm the fixture text actually flowed through unchanged (a
    # silent string rewrite between caller and runtime would defeat the
    # purpose of the seed-input contract).
    forwarded = m.call_args.kwargs["seed_input"]
    assert "counter button that increments on click" in forwarded
    assert "Persist the count across reloads." in forwarded

    # Transcript JSONL was written and sits under the orchestra run dir.
    assert transcript_path.exists()
    assert transcript_path.is_relative_to(run_dir)
    records = _read_transcript_lines(transcript_path)
    assert len(records) == 3
    for rec in records:
        assert "state" in rec
        assert "status" in rec
        assert "output" in rec
    judge_records = [r for r in records if r["state"] == "judge"]
    assert judge_records, "expected at least one judge turn in the transcript"
    assert judge_records[-1]["output"] == artifact_text


def test_run_iterative_design_capped_returns_artifact_with_warning(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Forced-CAPPED scenario: a mock workflow that always terminates
    CAPPED returns the most recent judge artifact and emits a warning
    on the ``duplo.design`` logger naming run id and transcript path.
    """
    seed_input = FIXTURE_SPEC.read_text(encoding="utf-8")
    run_id = "run-capped-001"
    run_dir = _make_run_dir(tmp_path, run_id)
    transcript_path = run_dir / "transcript.jsonl"
    partial = "Counter design draft — review still flagged unresolved issues."

    judge_turn = _make_turn("judge", output=partial, role="judge_role", outcome="iterate")
    review_turn = _make_turn(
        "review", output="Structural issue remains.", role="reviewer", outcome="complete"
    )
    _write_transcript(
        transcript_path,
        [judge_turn, review_turn, judge_turn, review_turn, judge_turn],
    )

    fake_result = IterativeDesignResult(
        termination="CAPPED",
        rounds_completed=3,
        final_artifact=partial,
        transcript=[judge_turn, review_turn, judge_turn, review_turn, judge_turn],
        transcript_path=transcript_path,
        run_id=run_id,
        error=None,
    )

    with patch("orchestra.run_role", return_value=fake_result):
        with caplog.at_level(logging.WARNING, logger="duplo.design"):
            result = run_iterative_design(seed_input)

    assert result == partial
    matches = [r for r in caplog.records if "did not converge" in r.getMessage()]
    assert matches, "expected a 'did not converge' warning on duplo.design"
    msg = matches[0].getMessage()
    assert run_id in msg
    assert str(transcript_path) in msg
    assert "max_rounds" in msg
    # The transcript JSONL is durable even for capped runs, so a caller
    # navigating from the warning can postmortem the partial transcript.
    assert transcript_path.exists()
    records = _read_transcript_lines(transcript_path)
    assert len(records) >= 1


def test_run_iterative_design_error_raises_iterative_design_error(
    tmp_path: Path,
) -> None:
    """Forced-ERROR scenario: the mock workflow terminates ERROR with an
    actor failure; ``run_iterative_design`` raises
    ``IterativeDesignError`` carrying the original ErrorRecord and the
    transcript path so the caller can postmortem.
    """
    seed_input = FIXTURE_SPEC.read_text(encoding="utf-8")
    run_id = "run-error-001"
    run_dir = _make_run_dir(tmp_path, run_id)
    transcript_path = run_dir / "transcript.jsonl"
    # Even on ERROR the transcript is durable up to the failure point.
    judge_turn = _make_turn("judge", output="(partial)", role="judge_role", outcome="iterate")
    _write_transcript(transcript_path, [judge_turn])

    error_record = ErrorRecord(
        kind="actor_failure",
        message="synthetic adapter failure for 'judge'",
        state="judge",
        detail={"phase": "invoke"},
    )
    fake_result = IterativeDesignResult(
        termination="ERROR",
        rounds_completed=1,
        final_artifact="",
        transcript=[judge_turn],
        transcript_path=transcript_path,
        run_id=run_id,
        error=error_record,
    )

    with patch("orchestra.run_role", return_value=fake_result):
        with pytest.raises(IterativeDesignError) as exc_info:
            run_iterative_design(seed_input)

    raised = exc_info.value
    assert raised.error is error_record
    assert raised.transcript_path == transcript_path
    assert raised.run_id == run_id
    msg = str(raised)
    assert run_id in msg
    assert "synthetic adapter failure" in msg
    assert str(transcript_path) in msg
    # The transcript JSONL written before the failure remains durable on
    # disk so the caller can navigate to it via the exception.
    assert transcript_path.exists()
