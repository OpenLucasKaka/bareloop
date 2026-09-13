from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from bareloop.background_system import start_background_task
from bareloop.tools.registry import ToolScope, get_tool

_WORKSPACE_AWARE_TOOLS = frozenset({"bash", "read", "write", "edit", "glob"})


@dataclass(frozen=True)
class DispatchResult:
    output: str
    outcome: Literal["success", "error", "blocked"]
    error_kind: str | None = None
    security_block: bool = False
    safety_violation: bool = False


def dispatch_tool_result(
    name: str,
    arguments: Mapping[str, Any],
    *,
    scope: ToolScope = "main",
    call_id: str | None = None,
    workspace: str | Path | None = None,
) -> DispatchResult:
    definition = get_tool(name)
    if definition is None:
        return DispatchResult(f"Error: tool '{name}' not found", "error", "unknown_tool")
    if scope == "subagent" and not definition.allow_subagent:
        return DispatchResult(
            f"Error: tool '{name}' is not available to subagents",
            "blocked",
            "subagent_policy",
        )
    if scope not in ("main", "subagent"):
        return DispatchResult(f"Error: unknown tool scope '{scope}'", "error", "invalid_scope")

    tool_arguments = dict(arguments)
    if workspace is not None and name in _WORKSPACE_AWARE_TOOLS:
        tool_arguments["cwd"] = Path(workspace).resolve()
    try:
        if definition.supports_background and tool_arguments.get("shouldBack") is True:
            if call_id is None:
                return DispatchResult(
                    "Error: background tool call requires call_id",
                    "error",
                    "missing_call_id",
                )
            task_id = start_background_task({**tool_arguments, "id": call_id})
            return DispatchResult(
                f"[Background task {task_id} started] The result will be collected later.",
                "success",
            )

        result = definition.handler(**tool_arguments)
        output = "(no output)" if result is None else str(result)
        if output.startswith("Error: path escapes working directory:"):
            return DispatchResult(output, "blocked", "workspace_escape", security_block=True)
        if output.startswith("Error:"):
            return DispatchResult(output, "error", "tool_error")
        return DispatchResult(output, "success")
    except Exception as error:
        return DispatchResult(
            f"Error: {type(error).__name__}: {error}",
            "error",
            "handler_exception",
        )


def dispatch_tool(
    name: str,
    arguments: Mapping[str, Any],
    *,
    scope: ToolScope = "main",
    call_id: str | None = None,
    workspace: str | Path | None = None,
) -> str:
    return dispatch_tool_result(
        name,
        arguments,
        scope=scope,
        call_id=call_id,
        workspace=workspace,
    ).output
