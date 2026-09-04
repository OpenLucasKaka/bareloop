import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import bareloop.trace.index as trace_index
from bareloop.trace import TraceWriter


def read_records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_trace_writer_records_ordered_jsonl_events(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(trace_index, "TRACE_DIR", tmp_path)
    writer = TraceWriter()

    writer.write("user.input", content="hello")
    writer.write("session.stop")

    records = read_records(writer.path)
    assert [record["sequence"] for record in records] == [1, 2]
    assert [record["type"] for record in records] == ["user.input", "session.stop"]
    assert all(record["trace_id"] == writer.trace_id for record in records)
    assert records[0]["data"] == {"content": "hello"}


def test_trace_writer_serializes_non_json_values(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(trace_index, "TRACE_DIR", tmp_path)
    writer = TraceWriter()

    writer.write("path", value=tmp_path)

    assert read_records(writer.path)[0]["data"] == {"value": str(tmp_path)}


def test_trace_writer_keeps_threaded_sequence_consistent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(trace_index, "TRACE_DIR", tmp_path)
    writer = TraceWriter()

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda value: writer.write("worker", value=value), range(20)))

    records = read_records(writer.path)
    assert [record["sequence"] for record in records] == list(range(1, 21))


def test_trace_write_failure_does_not_escape(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(trace_index, "TRACE_DIR", tmp_path)
    writer = TraceWriter()
    writer.path = tmp_path

    writer.write("unwritable")

    assert "日志写入失败" in capsys.readouterr().out
