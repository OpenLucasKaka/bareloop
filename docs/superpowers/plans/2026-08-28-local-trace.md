# Local Trace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dependency-free local JSONL trace API to the existing `bareloop.trace` package without instrumenting BareLoop call sites.

**Architecture:** A context-manager API owns trace/span IDs through `contextvars`, while a lock-protected writer appends structured records to a daily JSONL file. The package initializer exposes only the stable integration surface that future LLM, tool, and subagent call sites need.

**Tech Stack:** Python 3.12 standard library, pytest, Ruff

---

### Task 1: Specify local trace behavior with tests

**Files:**
- Create: `tests/test_trace.py`

- [ ] **Step 1: Write the failing tests**

```python
import json
from pathlib import Path

import pytest

from bareloop.trace import configure_trace, current_trace_id, trace_event, trace_span


@pytest.fixture(autouse=True)
def isolated_trace(tmp_path: Path):
    configure_trace(enabled=True, directory=tmp_path, console=False)
    yield tmp_path
    configure_trace(enabled=False, directory=tmp_path, console=False)


def read_records(directory: Path) -> list[dict]:
    paths = list(directory.glob("trace-*.jsonl"))
    assert len(paths) == 1
    return [json.loads(line) for line in paths[0].read_text().splitlines()]


def test_nested_spans_and_events_share_trace(isolated_trace: Path):
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


def test_span_records_and_reraises_exception(isolated_trace: Path):
    with pytest.raises(RuntimeError, match="boom"):
        with trace_span("tool.execute"):
            raise RuntimeError("boom")

    end = read_records(isolated_trace)[-1]
    assert end["status"] == "error"
    assert end["error_type"] == "RuntimeError"
    assert end["error_message"] == "boom"


def test_disabled_trace_writes_nothing(isolated_trace: Path):
    configure_trace(enabled=False, directory=isolated_trace, console=False)

    with trace_span("disabled") as span:
        span.set_attribute("ignored", True)
        trace_event("ignored")

    assert current_trace_id() is None
    assert list(isolated_trace.iterdir()) == []


def test_writer_failure_does_not_affect_application(tmp_path: Path, capsys):
    invalid_directory = tmp_path / "file"
    invalid_directory.write_text("not a directory")
    configure_trace(enabled=True, directory=invalid_directory, console=False)

    with trace_span("first"):
        pass
    with trace_span("second"):
        pass

    assert capsys.readouterr().err.count("[trace] disabled") == 1
```

- [ ] **Step 2: Run tests and verify the API is missing**

Run: `uv run pytest tests/test_trace.py -q`

Expected: collection fails because `bareloop.trace` does not export the trace API.

### Task 2: Implement the local trace package

**Files:**
- Create: `src/bareloop/trace/index.py`
- Create: `src/bareloop/trace/__init__.py`

- [ ] **Step 1: Implement the recorder in `src/bareloop/trace/index.py`**

```python
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any
from uuid import uuid4


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class _TraceSettings:
    enabled: bool
    directory: Path
    console: bool


@dataclass(slots=True)
class TraceSpan:
    name: str
    trace_id: str
    span_id: str
    parent_span_id: str | None
    attributes: dict[str, Any] = field(default_factory=dict)
    _started_ns: int = field(default_factory=time.monotonic_ns, repr=False)

    def set_attribute(self, name: str, value: Any) -> None:
        self.attributes[name] = value


_settings = _TraceSettings(
    enabled=_env_flag("TRACE_ENABLED", True),
    directory=Path(os.getenv("TRACE_DIR", "logs/trace")),
    console=_env_flag("TRACE_CONSOLE", True),
)
_current_span: ContextVar[TraceSpan | None] = ContextVar("bareloop_trace_span", default=None)
_write_lock = threading.Lock()
_write_failed = False


def configure_trace(
    *,
    enabled: bool | None = None,
    directory: str | Path | None = None,
    console: bool | None = None,
) -> None:
    global _settings, _write_failed
    with _write_lock:
        _settings = _TraceSettings(
            enabled=_settings.enabled if enabled is None else enabled,
            directory=_settings.directory if directory is None else Path(directory),
            console=_settings.console if console is None else console,
        )
        _write_failed = False


def current_trace_id() -> str | None:
    span = _current_span.get()
    return None if span is None else span.trace_id


def _timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _write_record(record: dict[str, Any]) -> None:
    global _write_failed
    if not _settings.enabled or _write_failed:
        return

    warning: str | None = None
    with _write_lock:
        if _write_failed:
            return
        try:
            _settings.directory.mkdir(parents=True, exist_ok=True)
            day = datetime.now().astimezone().strftime("%Y-%m-%d")
            path = _settings.directory / f"trace-{day}.jsonl"
            line = json.dumps(record, ensure_ascii=False, default=str)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(f"{line}\n")
        except Exception as error:
            _write_failed = True
            warning = f"[trace] disabled after write failure: {type(error).__name__}: {error}"

    if warning is not None:
        print(warning, file=sys.stderr)
    elif _settings.console and record["event"] == "span.end":
        print(
            f"[trace {record['trace_id'][:8]}] {record['name']} "
            f"{record['duration_ms']:.2f}ms {record['status']}"
        )


def _span_record(span: TraceSpan, event: str) -> dict[str, Any]:
    return {
        "timestamp": _timestamp(),
        "event": event,
        "name": span.name,
        "trace_id": span.trace_id,
        "span_id": span.span_id,
        "parent_span_id": span.parent_span_id,
        "attributes": dict(span.attributes),
    }


@contextmanager
def trace_span(name: str, **attributes: Any) -> Iterator[TraceSpan]:
    if not name.strip():
        raise ValueError("trace span name must not be empty")

    if not _settings.enabled:
        yield TraceSpan(name=name, trace_id="", span_id="", parent_span_id=None, attributes=attributes)
        return

    parent = _current_span.get()
    span = TraceSpan(
        name=name,
        trace_id=parent.trace_id if parent is not None else uuid4().hex,
        span_id=uuid4().hex[:16],
        parent_span_id=parent.span_id if parent is not None else None,
        attributes=dict(attributes),
    )
    token = _current_span.set(span)
    _write_record(_span_record(span, "span.start"))
    try:
        yield span
    except BaseException as error:
        record = _span_record(span, "span.end")
        record.update(
            status="error",
            duration_ms=(time.monotonic_ns() - span._started_ns) / 1_000_000,
            error_type=type(error).__name__,
            error_message=str(error),
        )
        _write_record(record)
        raise
    else:
        record = _span_record(span, "span.end")
        record.update(
            status="ok",
            duration_ms=(time.monotonic_ns() - span._started_ns) / 1_000_000,
        )
        _write_record(record)
    finally:
        _current_span.reset(token)


def trace_event(name: str, **attributes: Any) -> None:
    if not _settings.enabled:
        return
    if not name.strip():
        raise ValueError("trace event name must not be empty")

    span = _current_span.get()
    _write_record(
        {
            "timestamp": _timestamp(),
            "event": name,
            "name": name,
            "trace_id": span.trace_id if span is not None else uuid4().hex,
            "span_id": span.span_id if span is not None else uuid4().hex[:16],
            "parent_span_id": span.parent_span_id if span is not None else None,
            "attributes": dict(attributes),
        }
    )
```

- [ ] **Step 2: Export the stable API from `src/bareloop/trace/__init__.py`**

```python
from .index import (
    TraceSpan as TraceSpan,
    configure_trace as configure_trace,
    current_trace_id as current_trace_id,
    trace_event as trace_event,
    trace_span as trace_span,
)

__all__ = ["TraceSpan", "configure_trace", "current_trace_id", "trace_event", "trace_span"]
```

- [ ] **Step 3: Run focused tests**

Run: `uv run pytest tests/test_trace.py -q`

Expected: `4 passed`.

### Task 3: Harden reviewed failure boundaries

**Files:**
- Modify: `tests/test_trace.py`
- Modify: `src/bareloop/trace/index.py`

- [ ] **Step 1: Add failing regressions**

Add tests proving that an exception with a broken `__str__` is re-raised
unchanged, disabled state is rechecked after acquiring the writer lock,
`KeyboardInterrupt` propagates while the active context is restored, and
NaN/Infinity never produce invalid JSON. The concrete test cases are:

```python
class BrokenStringError(Exception):
    def __str__(self) -> str:
        raise RuntimeError("string conversion failed")


error = BrokenStringError()
with pytest.raises(BrokenStringError) as captured, trace_span("tool.execute"):
    raise error
assert captured.value is error
assert read_records(isolated_trace)[-1]["error_message"] == (
    "<unprintable BrokenStringError>"
)
```

```python
class DisableTraceLock:
    def __enter__(self) -> None:
        trace_index._settings.enabled = False

    def __exit__(self, *args: object) -> None:
        pass


monkeypatch.setattr(trace_index, "_write_lock", DisableTraceLock())
with trace_span("racing-disable"):
    pass
assert list(isolated_trace.iterdir()) == []
```

```python
def interrupt_serialization(*args: object, **kwargs: object) -> str:
    raise KeyboardInterrupt


monkeypatch.setattr(trace_index.json, "dumps", interrupt_serialization)
with pytest.raises(KeyboardInterrupt), trace_span("interrupted-write"):
    pass
assert current_trace_id() is None
```

```python
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
```

Import `bareloop.trace.index as trace_index` for the two controlled race and
serialization tests.

- [ ] **Step 2: Run tests and verify the reviewed defects are RED**

Run: `uv run pytest tests/test_trace.py -q`

Expected: the new tests fail because the original exception can be replaced,
disabled state is not rechecked under the lock, interrupts are swallowed, and
non-finite floats are serialized.

- [ ] **Step 3: Apply minimal hardening**

Import `suppress` with `contextmanager`. Add safe error formatting:

```python
def _safe_error_message(error: BaseException) -> str:
    try:
        return str(error)
    except BaseException:
        return f"<unprintable {type(error).__name__}>"
```

Inside `_write_record`, recheck `not _settings.enabled or _write_failed` after
acquiring the lock, use `allow_nan=False`, catch only `Exception`, and guard
stderr/console printing with `suppress(Exception)`. Do not swallow
`KeyboardInterrupt` or `SystemExit` in ordinary writer paths.

After setting the ContextVar token, wrap the start record, body, and end record
in an outer `try/finally` that always calls `_current_span.reset(token)`. When
the body raises a `BaseException`, protect trace error recording with
`suppress(BaseException)` and then use bare `raise` so the original application
exception is preserved.

- [ ] **Step 4: Run focused tests**

Run: `uv run pytest tests/test_trace.py -q`

Expected: `10 passed`.

### Task 4: Verify the package

**Files:**
- Verify: `src/bareloop/trace/index.py`
- Verify: `src/bareloop/trace/__init__.py`
- Verify: `tests/test_trace.py`

- [ ] **Step 1: Run Ruff for changed files**

Run: `uv run ruff check src/bareloop/trace tests/test_trace.py`

Expected: `All checks passed!`.

- [ ] **Step 2: Verify formatting**

Run: `uv run ruff format --check src/bareloop/trace tests/test_trace.py`

Expected: `3 files already formatted`.

- [ ] **Step 3: Run the complete test suite**

Run: `uv run pytest -q`

Expected: `11 passed`.

- [ ] **Step 4: Review the diff without committing**

Run: `git diff --check && git status --short`

Expected: no whitespace errors; the trace implementation, tests, design, and plan remain uncommitted. Existing user changes remain untouched.
