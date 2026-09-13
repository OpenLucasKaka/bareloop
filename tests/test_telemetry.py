from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from bareloop.telemetry import (
    ProviderCallMetric,
    RunTelemetry,
    RunTermination,
    ToolCallMetric,
    persist_run_telemetry,
)


def test_persist_run_telemetry_writes_jsonl_snapshot(tmp_path: Path) -> None:
    telemetry = RunTelemetry(
        provider_calls=[ProviderCallMetric(12.5, True, 10, 3, 13)],
        tool_calls=[ToolCallMetric("read", "success")],
        security_blocks=1,
    )

    path = persist_run_telemetry(
        telemetry,
        path=tmp_path / "runs.jsonl",
        model="test-model",
        duration_ms=25.0,
        rounds=1,
        tool_calls=1,
        termination=RunTermination.COMPLETED,
    )

    record = json.loads(path.read_text(encoding="utf-8"))
    assert path == tmp_path / "runs.jsonl"
    assert record["model"] == "test-model"
    assert record["termination"] == "completed"
    assert record["duration_ms"] == 25.0
    assert record["rounds"] == 1
    assert record["tool_calls"] == 1
    assert record["provider_requests"] == 1
    assert record["provider_successes"] == 1
    assert record["input_tokens"] == 10
    assert record["output_tokens"] == 3
    assert record["total_tokens"] == 13
    assert record["security_blocks"] == 1
    assert record["tool_calls_detail"] == [
        {"name": "read", "outcome": "success", "error_kind": None}
    ]


def test_interactive_loop_persists_telemetry(monkeypatch) -> None:
    from bareloop import loop

    telemetry = RunTelemetry()
    captured = []
    monkeypatch.setattr(
        loop,
        "execute_agent_loop",
        lambda *_args, **_kwargs: SimpleNamespace(
            completed=True,
            final_output="done",
            rounds=1,
            tool_calls=0,
            model="test-model",
            telemetry=telemetry,
            termination=RunTermination.COMPLETED,
        ),
    )
    monkeypatch.setattr(
        loop, "persist_run_telemetry", lambda *args, **kwargs: captured.append((args, kwargs))
    )

    assert loop._run_agent_loop([], None, [], [], [], {"accepted": False}, loop.AgentMode.NORMAL)
    assert captured[0][0][0] is telemetry
    assert captured[0][1]["model"] == "test-model"
    assert captured[0][1]["rounds"] == 1
