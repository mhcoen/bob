"""Tests for duplo.logs (read-only run-log summary)."""

from __future__ import annotations

import json

import pytest

from duplo import logs
from duplo.call_log import CALLS_FILENAME, LOGS_ROOT


def _write_run(tmp_path, run_id, records):
    run_dir = tmp_path / LOGS_ROOT / run_id
    run_dir.mkdir(parents=True)
    calls_path = run_dir / CALLS_FILENAME
    with open(calls_path, "w") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")
    return calls_path


def _legacy(call_site, model="sonnet", duration=1.0, usage=None):
    rec = {
        "call_site": call_site,
        "path": "legacy",
        "model": model,
        "duration_seconds": duration,
    }
    if usage is not None:
        rec["usage"] = usage
    return rec


def _council(call_site, orchestra_run_id):
    return {
        "call_site": call_site,
        "path": "council",
        "orchestra_run_id": orchestra_run_id,
    }


def test_find_latest_run_id_picks_lexical_max(tmp_path):
    _write_run(tmp_path, "20260101T000000Z-aaa111", [])
    _write_run(tmp_path, "20260201T000000Z-bbb222", [])
    assert logs.find_latest_run_id(tmp_path) == "20260201T000000Z-bbb222"


def test_find_latest_run_id_none_when_no_runs(tmp_path):
    assert logs.find_latest_run_id(tmp_path) is None


def test_resolve_calls_path_defaults_to_latest(tmp_path):
    _write_run(tmp_path, "20260101T000000Z-aaa111", [])
    latest = _write_run(tmp_path, "20260201T000000Z-bbb222", [])
    assert logs.resolve_calls_path(tmp_path) == latest


def test_resolve_calls_path_raises_when_no_runs(tmp_path):
    with pytest.raises(logs.LogsError):
        logs.resolve_calls_path(tmp_path)


def test_resolve_calls_path_raises_for_unknown_run_id(tmp_path):
    _write_run(tmp_path, "20260101T000000Z-aaa111", [])
    with pytest.raises(logs.LogsError):
        logs.resolve_calls_path(tmp_path, run_id="nope")


def test_load_records_skips_blank_and_malformed_lines(tmp_path):
    calls_path = tmp_path / "calls.jsonl"
    calls_path.write_text('{"call_site": "a"}\n\nnot json\n{"call_site": "b"}\n')
    records = logs.load_records(calls_path)
    assert [r["call_site"] for r in records] == ["a", "b"]


def test_summarize_run_preserves_order_and_totals():
    records = [
        _legacy(
            "extract_features",
            duration=2.0,
            usage={
                "input_tokens": 100,
                "cache_creation_input_tokens": 1000,
                "cache_read_input_tokens": 500,
                "output_tokens": 50,
            },
        ),
        _legacy(
            "generate_roadmap",
            duration=3.0,
            usage={
                "input_tokens": 200,
                "cache_read_input_tokens": 4000,
                "output_tokens": 80,
            },
        ),
    ]
    summary = logs.summarize_run("run-1", records)
    assert [r.call_site for r in summary.rows] == ["extract_features", "generate_roadmap"]
    # cache column combines creation + read.
    assert summary.rows[0].cache_tokens == 1500
    assert summary.rows[1].cache_tokens == 4000
    assert summary.total_duration_seconds == 5.0
    assert summary.total_input_tokens == 300
    assert summary.total_cache_tokens == 5500
    assert summary.total_output_tokens == 130


def test_summarize_run_handles_council_pointer_rows():
    records = [_council("phase_plan:phase_002", "orc-xyz")]
    summary = logs.summarize_run("run-1", records)
    row = summary.rows[0]
    assert row.path == "council"
    assert row.orchestra_run_id == "orc-xyz"
    assert row.duration_seconds is None
    assert row.input_tokens == 0
    # Council pointers contribute nothing to the token totals.
    assert summary.total_input_tokens == 0
    assert summary.total_output_tokens == 0


def test_format_report_includes_calls_total_and_council_reference():
    records = [
        _legacy(
            "extract_features",
            duration=2.0,
            usage={"input_tokens": 100, "output_tokens": 50},
        ),
        _council("phase_plan:phase_002", "orc-xyz"),
    ]
    summary = logs.summarize_run("20260101T000000Z-abc123", records)
    report = logs.format_report(summary)
    assert "Run 20260101T000000Z-abc123  (2 calls)" in report
    assert "extract_features" in report
    assert "phase_plan:phase_002 -> orc-xyz" in report
    assert "TOTAL" in report
    # Token counts render with thousands separators in the total.
    assert "100" in report


def test_print_run_report_outputs_table(tmp_path, capsys):
    _write_run(
        tmp_path,
        "20260101T000000Z-abc123",
        [_legacy("extract_features", usage={"input_tokens": 1234, "output_tokens": 56})],
    )
    logs.print_run_report(target_dir=tmp_path)
    out = capsys.readouterr().out
    assert "extract_features" in out
    assert "1,234" in out
    assert "TOTAL" in out


def test_print_run_report_raises_logs_error_for_empty(tmp_path):
    with pytest.raises(logs.LogsError):
        logs.print_run_report(target_dir=tmp_path)
