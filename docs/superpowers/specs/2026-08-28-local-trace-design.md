# BareLoop Local Trace Design

## Goal

Provide a dependency-free local trace API in the existing `src/bareloop/trace`
package. Call sites can start root or nested spans without knowing how logs are
stored.

## Scope

This change provides only the trace foundation. It does not instrument the
agent turn, LLM, tool, hook, memory, compaction, cron, subagent, or background
calls. Those components can later use the same public API.

The implementation does not add OpenTelemetry, an online exporter, a database,
or a third-party dependency.

## Public API

`bareloop.trace` exports:

- `trace_span(name, **attributes)`: a context manager that writes `span.start`
  and `span.end` JSONL records. The yielded span supports `set_attribute` for
  values learned during the call.
- `trace_event(name, **attributes)`: writes an instantaneous event associated
  with the current span when one exists.
- `current_trace_id()`: returns the active trace ID or `None`.
- `configure_trace(...)`: overrides output settings, primarily for tests and
  embedding hosts.

Nested spans inherit the active trace ID and store the active span ID as
`parent_span_id`. Leaving a span always restores the previous context. An
exception produces an error end record and is re-raised unchanged.

## Storage and Console Output

Records are appended as UTF-8 JSON lines to
`logs/trace/trace-YYYY-MM-DD.jsonl`. Appends are protected by a process-local
lock so BareLoop background threads cannot interleave records.

Console summaries are enabled by default when a span completes and can be
disabled with `TRACE_CONSOLE=false`. Tracing itself defaults to enabled and can
be disabled with `TRACE_ENABLED=false`. `TRACE_DIR` overrides the output
directory.

Each record contains `timestamp`, `event`, `name`, `trace_id`, `span_id`,
`parent_span_id`, and JSON-compatible attributes. End records additionally
contain `status` and `duration_ms`. Error records include only exception type
and message; traceback and arbitrary model/tool content are not captured
implicitly.

## Failure Behavior

Trace serialization or file I/O failure must never replace an application
result or exception. The writer reports at most one concise warning to stderr
and disables further writes for the process.

## Verification

Tests use a temporary directory and cover nested parent relationships, dynamic
attributes, events, exception recording and re-raising, disabled tracing, and
safe degradation after file I/O failure. The normal pytest and Ruff suites
remain the completion gate.
