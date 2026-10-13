from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from time import perf_counter
from typing import Any

from bareloop.loop import execute_agent_loop
from bareloop.plugin import Plugin, tool
from bareloop.session import RunResult
from bareloop.telemetry import persist_run_telemetry
from bareloop.tools.models import ToolDefinition
from bareloop.trace import TraceWriter


class SimpleTokenizer:
    """Lightweight fallback tokenizer when HuggingFace tokenizer is not configured."""

    def apply_chat_template(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tokenize: bool = True,
        add_generation_prompt: bool = True,
        **kwargs: Any,
    ) -> list[int] | str:
        total_chars = sum(len(str(m.get("content", ""))) for m in messages)
        if tools:
            total_chars += sum(len(str(t)) for t in tools)
        estimated_token_count = max(1, total_chars // 4)
        if tokenize:
            return [0] * estimated_token_count
        return str(messages)


class Agent:
    """High-level Python SDK interface for BareLoop Agent Runtime.

    Usage:
        from bareloop import Agent, tool

        @tool
        def calc(expression: str) -> str:
            return str(eval(expression))

        agent = Agent(
            model="gpt-4o",
            tools=[calc, "read", "write"],
        )
        result = agent.run("Calculate 2 + 2")
        print(result.final_output)
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        client: Any | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        system_prompt: str = "You are a helpful coding assistant.",
        workdir: str | Path | None = None,
        tools: Sequence[ToolDefinition | Callable[..., Any] | str] | None = None,
        plugins: Sequence[Plugin | type[Plugin]] | None = None,
        hooks: Mapping[str, Sequence[Callable[..., Any]]] | None = None,
        max_rounds: int = 20,
        tokenizer: Any | None = None,
        trace: TraceWriter | None = None,
        telemetry_path: str | Path | None = None,
        enable_background: bool = False,
        enable_memory: bool = False,
        enable_hooks: bool = True,
        enable_goal_gate: bool = False,
        print_output: bool = False,
    ) -> None:
        if max_rounds < 1:
            raise ValueError("max_rounds must be at least 1")

        self.model = (
            model
            or os.getenv("PRIMARY_MODEL")
            or os.getenv("OPENAI_MODEL")
            or "gpt-4o"
        )
        self.system_prompt = system_prompt
        self.workdir = Path(workdir).resolve() if workdir else Path.cwd().resolve()
        self.max_rounds = max_rounds
        self.trace = trace
        self.telemetry_path = (
            Path(telemetry_path)
            if telemetry_path is not None
            else self.workdir / ".bareloop" / ".telemetry" / "runs.jsonl"
        )
        self.enable_background = enable_background
        self.enable_memory = enable_memory
        self.enable_hooks = enable_hooks
        self.enable_goal_gate = enable_goal_gate
        self.print_output = print_output

        # Initialize OpenAI-compatible client
        if client is not None:
            self.client = client
        else:
            from openai import OpenAI

            resolved_api_key = (
                api_key
                or os.getenv("API_KEY")
                or os.getenv("OPENAI_API_KEY")
                or "bareloop-unconfigured"
            )
            resolved_base_url = base_url or os.getenv("BASE_URL") or None
            self.client = OpenAI(api_key=resolved_api_key, base_url=resolved_base_url)

        # Initialize tokenizer
        if tokenizer is not None:
            self.tokenizer = tokenizer
        else:
            tokenizer_model = os.getenv("TOKENIZER_MODEL")
            if tokenizer_model:
                try:
                    from bareloop.settings import tokenizer as default_tokenizer

                    self.tokenizer = default_tokenizer
                except Exception:
                    self.tokenizer = SimpleTokenizer()
            else:
                self.tokenizer = SimpleTokenizer()

        self._custom_tools: dict[str, ToolDefinition] = {}
        self._allowed_tool_names: set[str] | None = None
        self._hooks: dict[str, list[Callable[..., Any]]] = {}

        if hooks:
            for event, callbacks in hooks.items():
                self._hooks.setdefault(event, []).extend(callbacks)

        if tools is not None:
            self._allowed_tool_names = set()
            for t in tools:
                self.register_tool(t)

        if plugins:
            for p in plugins:
                self.register_plugin(p)

        self.messages: list[dict[str, Any]] = [{"role": "system", "content": self.system_prompt}]

    def register_tool(self, t: ToolDefinition | Callable[..., Any] | str) -> Agent:
        """Register a custom tool or allow a builtin tool by name."""
        if isinstance(t, str):
            if self._allowed_tool_names is None:
                self._allowed_tool_names = set()
            self._allowed_tool_names.add(t)
            return self

        defn: ToolDefinition
        if isinstance(t, ToolDefinition):
            defn = t
        elif hasattr(t, "__tool_definition__"):
            defn = t.__tool_definition__
        elif callable(t):
            wrapped = tool(t)
            defn = wrapped.__tool_definition__
        else:
            raise TypeError(f"Unsupported tool type: {type(t)!r}")

        self._custom_tools[defn.name] = defn
        if self._allowed_tool_names is not None:
            self._allowed_tool_names.add(defn.name)
        return self

    def add_tool(self, t: ToolDefinition | Callable[..., Any] | str) -> Agent:
        """Alias for register_tool."""
        return self.register_tool(t)

    def register_plugin(self, plugin: Plugin | type[Plugin]) -> Agent:
        """Register a Plugin instance or class."""
        inst = plugin() if isinstance(plugin, type) else plugin
        inst.setup(self)

        for t in inst.get_tools():
            self.register_tool(t)

        for event, callbacks in inst.get_hooks().items():
            self._hooks.setdefault(event, []).extend(callbacks)
        return self

    def on(self, event: str, callback: Callable[..., Any]) -> Agent:
        """Register a lifecycle hook callback for this Agent instance."""
        self._hooks.setdefault(event, []).append(callback)
        return self

    def reset(self) -> None:
        """Reset conversation history back to initial system prompt."""
        self.messages = [{"role": "system", "content": self.system_prompt}]

    def run(self, prompt: str) -> RunResult:
        """Execute one conversational turn with the given prompt."""
        self.messages.append({"role": "user", "content": prompt})
        started_at = perf_counter()

        loop_result = execute_agent_loop(
            self.messages,
            client=self.client,
            model=self.model,
            tokenizer=self.tokenizer,
            workdir=self.workdir,
            max_rounds=self.max_rounds,
            allowed_tool_names=self._allowed_tool_names,
            custom_tools=self._custom_tools,
            custom_hooks=self._hooks,
            trace=self.trace,
            enable_background=self.enable_background,
            enable_memory=self.enable_memory,
            enable_loading=False,
            enable_goal_gate=self.enable_goal_gate,
            enable_hooks=self.enable_hooks,
            enable_finalizers=False,
            print_output=self.print_output,
        )

        duration_ms = (perf_counter() - started_at) * 1000
        persist_run_telemetry(
            loop_result.telemetry,
            path=self.telemetry_path,
            model=loop_result.model,
            duration_ms=duration_ms,
            rounds=loop_result.rounds,
            tool_calls=loop_result.tool_calls,
            termination=loop_result.termination,
        )

        return RunResult(
            completed=loop_result.completed,
            final_output=loop_result.final_output,
            messages=deepcopy(self.messages),
            tool_calls=loop_result.tool_calls,
            rounds=loop_result.rounds,
            duration_ms=duration_ms,
            error=loop_result.error,
            model=loop_result.model,
            telemetry=deepcopy(loop_result.telemetry),
            termination=loop_result.termination,
        )
