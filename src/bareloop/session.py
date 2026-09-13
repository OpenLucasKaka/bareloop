from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

from bareloop.loop import execute_agent_loop
from bareloop.telemetry import RunTelemetry, RunTermination, persist_run_telemetry

_EVAL_TOOL_NAMES = frozenset({"read", "write", "edit", "glob"})


@dataclass
class RunResult:
    completed: bool
    final_output: str
    messages: list[dict[str, Any]]
    tool_calls: int
    rounds: int
    duration_ms: float
    error: str | None
    model: str | None
    telemetry: RunTelemetry
    termination: RunTermination


class AgentSession:
    def __init__(
        self,
        *,
        workdir: str | Path,
        system_prompt: str,
        client_instance: Any,
        model: str,
        tokenizer_instance: Any,
        max_rounds: int,
        trace: Any | None,
        telemetry_path: str | Path | None = None,
    ) -> None:
        if max_rounds < 1:
            raise ValueError("max_rounds must be at least 1")
        self.workdir = Path(workdir).resolve()
        self.system_prompt = system_prompt
        self.client = client_instance
        self.model = model
        self.tokenizer = tokenizer_instance
        self.max_rounds = max_rounds
        self.trace = trace
        self.telemetry_path = (
            Path(telemetry_path)
            if telemetry_path is not None
            else self.workdir / ".bareloop" / ".telemetry" / "runs.jsonl"
        )
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]

    def run(self, prompt: str) -> RunResult:
        self.messages.append({"role": "user", "content": prompt})
        started_at = perf_counter()
        result = execute_agent_loop(
            self.messages,
            client=self.client,
            model=self.model,
            tokenizer=self.tokenizer,
            workdir=self.workdir,
            max_rounds=self.max_rounds,
            allowed_tool_names=_EVAL_TOOL_NAMES,
            trace=self.trace,
            enable_background=False,
            enable_memory=False,
            enable_loading=False,
            enable_goal_gate=False,
            enable_hooks=False,
            enable_finalizers=False,
            print_output=False,
        )
        duration_ms = (perf_counter() - started_at) * 1000
        persist_run_telemetry(
            result.telemetry,
            path=self.telemetry_path,
            model=result.model,
            duration_ms=duration_ms,
            rounds=result.rounds,
            tool_calls=result.tool_calls,
            termination=result.termination,
        )
        return RunResult(
            completed=result.completed,
            final_output=result.final_output,
            messages=deepcopy(self.messages),
            tool_calls=result.tool_calls,
            rounds=result.rounds,
            duration_ms=duration_ms,
            error=result.error,
            model=result.model,
            telemetry=deepcopy(result.telemetry),
            termination=result.termination,
        )
