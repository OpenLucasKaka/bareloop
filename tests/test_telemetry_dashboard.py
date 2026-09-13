from __future__ import annotations

import json
from pathlib import Path

from bareloop.telemetry_dashboard import (
    aggregate_records,
    render_markdown,
    render_svg,
    update_readme,
)


def test_aggregate_records_calculates_runtime_rates_and_totals() -> None:
    summary = aggregate_records(
        [
            {
                "recorded_at": "2026-09-13T00:00:00+00:00",
                "termination": "completed",
                "provider_requests": 2,
                "provider_successes": 2,
                "provider_latency_ms": [10.0, 20.0],
                "token_usage_available": True,
                "input_tokens": 100,
                "output_tokens": 20,
                "total_tokens": 120,
                "tool_calls": 2,
                "tool_successes": 1,
                "tool_errors": 1,
                "tool_blocked": 0,
                "invalid_tool_calls": 0,
                "security_blocks": 0,
                "safety_violations": 0,
            },
            {
                "recorded_at": "2026-09-13T00:01:00+00:00",
                "termination": "provider_error",
                "provider_requests": 1,
                "provider_successes": 0,
                "provider_latency_ms": [30.0],
                "token_usage_available": False,
                "input_tokens": None,
                "output_tokens": None,
                "total_tokens": None,
                "tool_calls": 0,
                "tool_successes": 0,
                "tool_errors": 0,
                "tool_blocked": 0,
                "invalid_tool_calls": 0,
                "security_blocks": 1,
                "safety_violations": 0,
            },
        ]
    )

    assert summary["run_count"] == 2
    assert summary["completion_rate"] == 0.5
    assert summary["provider_success_rate"] == 2 / 3
    assert summary["provider_latency_ms"]["mean"] == 20.0
    assert summary["provider_latency_ms"]["p95"] == 30.0
    assert summary["token_runs"] == 1
    assert summary["input_tokens"] == 100
    assert summary["tool_calls"] == 2
    assert summary["tool_errors"] == 1
    assert summary["security_blocks"] == 1
    assert summary["termination_counts"] == {"completed": 1, "provider_error": 1}


def test_renderers_and_readme_update_are_deterministic(tmp_path: Path) -> None:
    summary = aggregate_records([])
    markdown = render_markdown(summary, source="telemetry.jsonl")
    svg = render_svg(summary)
    readme = tmp_path / "README.md"
    readme.write_text(
        "# Demo\n\n<!-- telemetry-dashboard:start -->\nold\n<!-- telemetry-dashboard:end -->\n",
        encoding="utf-8",
    )

    update_readme(readme, markdown)

    content = readme.read_text(encoding="utf-8")
    assert "<!-- telemetry-dashboard:start -->" in content
    assert "old" not in content
    assert "Run count" in content
    assert "telemetry-dashboard.svg" in content
    assert '<svg xmlns="http://www.w3.org/2000/svg"' in svg
    assert "No telemetry runs recorded" in markdown
    assert json.loads(json.dumps(summary))["run_count"] == 0
