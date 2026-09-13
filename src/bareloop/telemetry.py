from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from bareloop.settings import WORKDIR

TELEMETRY_PATH = WORKDIR / ".bareloop" / ".telemetry" / "runs.jsonl"
_telemetry_lock = threading.Lock()


class RunTermination(StrEnum):
    COMPLETED = "completed"
    PROVIDER_ERROR = "provider_error"
    INVALID_TOOL_CALL = "invalid_tool_call"
    MAX_ROUNDS = "max_rounds"
    GOAL_BLOCKED = "goal_blocked"
    HARNESS_ERROR = "harness_error"


@dataclass(frozen=True)
class ProviderCallMetric:
    duration_ms: float
    succeeded: bool
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    error: str | None = None

    @classmethod
    def succeeded_call(cls, duration_ms: float, usage: object | None) -> ProviderCallMetric:
        return cls(
            duration_ms=duration_ms,
            succeeded=True,
            input_tokens=_usage_value(usage, "prompt_tokens", "input_tokens"),
            output_tokens=_usage_value(usage, "completion_tokens", "output_tokens"),
            total_tokens=_usage_value(usage, "total_tokens"),
        )

    @classmethod
    def failed_call(cls, duration_ms: float, error: BaseException) -> ProviderCallMetric:
        return cls(
            duration_ms=duration_ms,
            succeeded=False,
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            error=f"{type(error).__name__}: {error}",
        )


@dataclass(frozen=True)
class ToolCallMetric:
    name: str
    outcome: Literal["success", "error", "blocked", "invalid"]
    error_kind: str | None = None


@dataclass
class RunTelemetry:
    provider_calls: list[ProviderCallMetric] = field(default_factory=list)
    tool_calls: list[ToolCallMetric] = field(default_factory=list)
    security_blocks: int = 0
    safety_violations: int = 0

    @property
    def provider_request_count(self) -> int:
        return len(self.provider_calls)

    @property
    def provider_success_count(self) -> int:
        return sum(call.succeeded for call in self.provider_calls)

    @property
    def token_usage_available(self) -> bool:
        successful = [call for call in self.provider_calls if call.succeeded]
        return bool(successful) and all(
            call.input_tokens is not None
            and call.output_tokens is not None
            and call.total_tokens is not None
            for call in successful
        )

    @property
    def input_tokens(self) -> int | None:
        return self._token_total("input_tokens")

    @property
    def output_tokens(self) -> int | None:
        return self._token_total("output_tokens")

    @property
    def total_tokens(self) -> int | None:
        return self._token_total("total_tokens")

    def _token_total(self, field_name: str) -> int | None:
        if not self.token_usage_available:
            return None
        return sum(int(getattr(call, field_name)) for call in self.provider_calls if call.succeeded)


def _usage_value(usage: object | None, *names: str) -> int | None:
    if usage is None:
        return None
    for name in names:
        value = getattr(usage, name, None)
        if type(value) is int and value >= 0:
            return value
    return None


def persist_run_telemetry(
    telemetry: RunTelemetry,
    *,
    path: str | Path = TELEMETRY_PATH,
    model: str | None = None,
    duration_ms: float | None = None,
    rounds: int | None = None,
    tool_calls: int | None = None,
    termination: RunTermination | str | None = None,
) -> Path:
    """Append one local, JSONL snapshot of a completed run's telemetry."""

    output_path = Path(path)
    record = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "model": model,
        "duration_ms": duration_ms,
        "rounds": rounds,
        "tool_calls": tool_calls,
        "termination": (
            termination.value if isinstance(termination, RunTermination) else termination
        ),
        "provider_requests": telemetry.provider_request_count,
        "provider_successes": telemetry.provider_success_count,
        "provider_latency_ms": [call.duration_ms for call in telemetry.provider_calls],
        "token_usage_available": telemetry.token_usage_available,
        "input_tokens": telemetry.input_tokens,
        "output_tokens": telemetry.output_tokens,
        "total_tokens": telemetry.total_tokens,
        "provider_calls_detail": [asdict(call) for call in telemetry.provider_calls],
        "tool_calls_detail": [asdict(call) for call in telemetry.tool_calls],
        "tool_successes": sum(call.outcome == "success" for call in telemetry.tool_calls),
        "tool_errors": sum(call.outcome == "error" for call in telemetry.tool_calls),
        "tool_blocked": sum(call.outcome == "blocked" for call in telemetry.tool_calls),
        "invalid_tool_calls": sum(call.outcome == "invalid" for call in telemetry.tool_calls),
        "security_blocks": telemetry.security_blocks,
        "safety_violations": telemetry.safety_violations,
    }
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with _telemetry_lock, output_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as error:
        # Observability must never make an otherwise valid agent run fail.
        print(f"Telemetry write failed: {error}")
    return output_path
