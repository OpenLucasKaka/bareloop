import json
from collections.abc import Iterator
from pathlib import Path

import pytest

import bareloop.trace.index as trace_index
from bareloop.trace import configure_trace, current_trace_id, trace_event, trace_span


@pytest.fixture(autouse=True)
def isolated_trace(tmp_path: Path) -> Iterator[Path]:
    configure_trace(enabled=True, directory=tmp_path, console=False)
    yield tmp_path
    configure_trace(enabled=False, directory=tmp_path, console=False)


def read_records(directory: Path) -> list[dict]:
    paths = list(directory.glob("trace-*.jsonl"))
    assert len(paths) == 1
    return [json.loads(line) for line in paths[0].read_text().splitlines()]


def test_nested_spans_and_events_share_trace(isolated_trace: Path) -> None:
    with trace_span("agent.turn", user_input=True) as outer:
        assert current_trace_id() == outer.trace_id
        with trace_span("llm.chat", model="fake") as inner:
            inner.set_attribute("output_tokens", 12)
            trace_event("response.received", finish_reason="stop")

    assert current_trace_id() is None
    records = read_records(isolated_trace)
    assert [record["event"] for record in records] == [
        "span.start",
        "span.start",
        "response.received",
        "span.end",
        "span.end",
    ]
    assert inner.trace_id == outer.trace_id
    assert inner.parent_span_id == outer.span_id
    assert records[2]["span_id"] == inner.span_id
    assert records[3]["attributes"]["output_tokens"] == 12
    assert records[3]["status"] == "ok"
    assert records[3]["duration_ms"] >= 0


def test_span_records_and_reraises_exception(isolated_trace: Path) -> None:
    with pytest.raises(RuntimeError, match="boom"), trace_span("tool.execute"):
        raise RuntimeError("boom")

    end = read_records(isolated_trace)[-1]
    assert end["status"] == "error"
    assert end["error_type"] == "RuntimeError"
    assert end["error_message"] == "boom"


def test_span_preserves_exception_when_its_message_cannot_be_formatted(
    isolated_trace: Path,
) -> None:
    class BrokenStringError(Exception):
        def __str__(self) -> str:
            raise RuntimeError("string conversion failed")

    error = BrokenStringError()

    with pytest.raises(BrokenStringError) as captured, trace_span("tool.execute"):
        raise error

    assert captured.value is error
    end = read_records(isolated_trace)[-1]
    assert end["status"] == "error"
    assert end["error_message"] == "<unprintable BrokenStringError>"


def test_disabled_trace_writes_nothing(isolated_trace: Path) -> None:
    configure_trace(enabled=False, directory=isolated_trace, console=False)

    with trace_span("disabled") as span:
        span.set_attribute("ignored", True)
        trace_event("ignored")

    assert current_trace_id() is None
    assert list(isolated_trace.iterdir()) == []


def test_writer_rechecks_enabled_after_acquiring_lock(
    isolated_trace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DisableTraceLock:
        def __enter__(self) -> None:
            trace_index._settings.enabled = False

        def __exit__(self, *args: object) -> None:
            pass

    monkeypatch.setattr(trace_index, "_write_lock", DisableTraceLock())

    with trace_span("racing-disable"):
        pass

    assert list(isolated_trace.iterdir()) == []


def test_writer_failure_does_not_affect_application(tmp_path: Path, capsys) -> None:
    invalid_directory = tmp_path / "file"
    invalid_directory.write_text("not a directory")
    configure_trace(enabled=True, directory=invalid_directory, console=False)

    with trace_span("first"):
        pass
    with trace_span("second"):
        pass

    assert capsys.readouterr().err.count("[trace] disabled") == 1


def test_writer_base_exception_propagates_and_restores_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def interrupt_serialization(*args: object, **kwargs: object) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr(trace_index.json, "dumps", interrupt_serialization)

    try:
        with pytest.raises(KeyboardInterrupt), trace_span("interrupted-write"):
            pass
    finally:
        assert current_trace_id() is None


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numbers_do_not_produce_invalid_json(
    isolated_trace: Path,
    capsys: pytest.CaptureFixture[str],
    value: float,
) -> None:
    with trace_span("non-finite", value=value):
        pass

    assert list(isolated_trace.glob("trace-*.jsonl")) == []
    assert capsys.readouterr().err.count("[trace] disabled") == 1
