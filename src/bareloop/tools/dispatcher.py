from collections.abc import Mapping
from typing import Any

from bareloop.background_system import start_background_task
from bareloop.tools.registry import ToolScope, get_tool


def dispatch_tool(
    name: str,
    arguments: Mapping[str, Any],
    *,
    scope: ToolScope = "main",
    call_id: str | None = None,
) -> str:
    definition = get_tool(name)
    if definition is None:
        return f"Error: tool '{name}' not found"
    if scope == "subagent" and not definition.allow_subagent:
        return f"Error: tool '{name}' is not available to subagents"
    if scope not in ("main", "subagent"):
        return f"Error: unknown tool scope '{scope}'"

    tool_arguments = dict(arguments)
    try:
        if definition.supports_background and tool_arguments.get("shouldBack") is True:
            if call_id is None:
                return "Error: background tool call requires call_id"
            task_id = start_background_task({**tool_arguments, "id": call_id})
            return f"[Background task {task_id} started] The result will be collected later."

        result = definition.handler(**tool_arguments)
        return "(no output)" if result is None else str(result)
    except Exception as error:
        return f"Error: {type(error).__name__}: {error}"
