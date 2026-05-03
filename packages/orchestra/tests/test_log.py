"""Unit tests for the JSONL log writer and reader."""

from __future__ import annotations

import json

import pytest

from orchestra.errors import ResumeError
from orchestra.log import LogReader, LogWriter


def test_write_and_read(tmp_path):
    path = tmp_path / "log.jsonl"
    w = LogWriter(path, "run-1")
    w.write("run_start", fields={"a": 1})
    w.write("state_enter", state_id="s", attempt=1)
    w.write("state_exit", state_id="s", attempt=1, fields={"outcome": "complete"})
    w.close()

    records = LogReader(path).read_all()
    assert [r.event for r in records] == ["run_start", "state_enter", "state_exit"]
    assert records[0].fields["a"] == 1
    assert records[1].state_id == "s"
    assert records[2].fields["outcome"] == "complete"


def test_seq_is_monotonic(tmp_path):
    path = tmp_path / "log.jsonl"
    w = LogWriter(path, "run-1")
    w.write("run_start")
    w.write("state_enter", state_id="s", attempt=1)
    w.write("state_exit", state_id="s", attempt=1)
    w.close()
    seqs = [r.seq for r in LogReader(path).read_all()]
    assert seqs == [0, 1, 2]


def test_truncated_last_line_is_dropped(tmp_path):
    path = tmp_path / "log.jsonl"
    w = LogWriter(path, "run-1")
    w.write("run_start")
    w.write("state_enter", state_id="s", attempt=1)
    w.close()

    # Append a partial line.
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('{"event": "state_exit", "incomp')
    records = LogReader(path).read_all()
    assert [r.event for r in records] == ["run_start", "state_enter"]


def test_resumed_writer_continues_seq(tmp_path):
    path = tmp_path / "log.jsonl"
    w1 = LogWriter(path, "run-1")
    w1.write("run_start")
    w1.write("state_enter", state_id="s", attempt=1)
    w1.close()

    records = LogReader(path).read_all()
    next_seq = records[-1].seq + 1
    w2 = LogWriter(path, "run-1", start_seq=next_seq)
    w2.write("state_exit", state_id="s", attempt=1)
    w2.close()
    seqs = [r.seq for r in LogReader(path).read_all()]
    assert seqs == [0, 1, 2]


def _good_record(seq: int, event: str, **extras) -> str:
    body = {
        "ts": "2026-01-01T00:00:00.000Z",
        "run_id": "run-1",
        "seq": seq,
        "event": event,
        "state_id": None,
        "attempt": None,
    }
    body.update(extras)
    return json.dumps(body, sort_keys=True)


def test_corrupt_middle_line_raises_resume_error(tmp_path):
    """A malformed non-final line is durable corruption. Replay must
    refuse rather than treat the later, intact records as if the
    crash had cut the log right after the good prefix."""
    path = tmp_path / "log.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(_good_record(0, "run_start") + "\n")
        fh.write("{not valid json}\n")
        fh.write(_good_record(2, "state_enter", state_id="s", attempt=1) + "\n")
    with pytest.raises(ResumeError) as excinfo:
        LogReader(path).read_all()
    msg = str(excinfo.value)
    assert "corrupt log line" in msg
    assert ":2" in msg


def test_sequence_gap_raises_resume_error(tmp_path):
    path = tmp_path / "log.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(_good_record(0, "run_start") + "\n")
        fh.write(_good_record(2, "state_enter", state_id="s", attempt=1) + "\n")
    with pytest.raises(ResumeError) as excinfo:
        LogReader(path).read_all()
    assert "sequence gap" in str(excinfo.value)


def test_missing_required_key_raises_resume_error(tmp_path):
    path = tmp_path / "log.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(_good_record(0, "run_start") + "\n")
        fh.write(json.dumps({"run_id": "run-1", "seq": 1}) + "\n")
    with pytest.raises(ResumeError) as excinfo:
        LogReader(path).read_all()
    assert "malformed log record" in str(excinfo.value)


def test_non_object_line_raises_resume_error(tmp_path):
    path = tmp_path / "log.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(_good_record(0, "run_start") + "\n")
        fh.write("[1, 2, 3]\n")
    with pytest.raises(ResumeError) as excinfo:
        LogReader(path).read_all()
    assert "not a JSON object" in str(excinfo.value)


def test_empty_middle_line_raises_resume_error(tmp_path):
    path = tmp_path / "log.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(_good_record(0, "run_start") + "\n")
        fh.write("\n")
        fh.write(_good_record(1, "state_enter", state_id="s", attempt=1) + "\n")
    with pytest.raises(ResumeError):
        LogReader(path).read_all()


def test_unterminated_complete_final_line_is_kept(tmp_path):
    """A final line whose JSON is fully written but whose terminator
    newline never reached disk is still durable from the writer's
    perspective: every byte the parser needs is present. Match the
    pre-existing behavior and keep the record. The single dropped
    case is unterminated AND unparseable."""
    path = tmp_path / "log.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(_good_record(0, "run_start") + "\n")
        fh.write(_good_record(1, "state_enter", state_id="s", attempt=1))
    records = LogReader(path).read_all()
    assert [r.event for r in records] == ["run_start", "state_enter"]


def test_unterminated_final_partial_json_dropped(tmp_path):
    path = tmp_path / "log.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(_good_record(0, "run_start") + "\n")
        fh.write('{"event": "state_exit", "incomp')
    records = LogReader(path).read_all()
    assert [r.event for r in records] == ["run_start"]
