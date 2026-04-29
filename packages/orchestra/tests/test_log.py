"""Unit tests for the JSONL log writer and reader."""

from __future__ import annotations

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
