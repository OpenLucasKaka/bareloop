from typing import Any

from bareloop.hook import trigger_hook
from bareloop.settings import PRIMARY_MODEL, client
from bareloop.tools.dispatcher import dispatch_tool
from bareloop.tools.registry import get_tool_schemas
from bareloop.utils import normalize_tool_call

SUBAGENT_TOOL_SCHEMAS = get_tool_schemas(scope="subagent")


def spawn_subagent(query: str) -> str:
    messages: list[dict[str, Any]] = [{"role": "user", "content": query}]
    for _ in range(30):
        response = client.chat.completions.create(
            model=PRIMARY_MODEL,
            messages=messages,
            tools=SUBAGENT_TOOL_SCHEMAS,
        )
        message = response.choices[0].message
        assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": message.content or "",
        }
        if message.tool_calls:
            assistant_message["tool_calls"] = [
                {
                    "id": tool.id,
                    "type": "function",
                    "function": {
                        "name": tool.function.name,
                        "arguments": tool.function.arguments,
                    },
                }
                for tool in message.tool_calls
            ]
        messages.append(assistant_message)

        if not message.tool_calls:
            trigger_hook("Stop", messages)
            return message.content or ""

        for tool in message.tool_calls:
            normal_tool = normalize_tool_call(tool)
            blocked = trigger_hook("PreToolUse", normal_tool)
            output = blocked or dispatch_tool(
                normal_tool["name"],
                normal_tool["arguments"],
                scope="subagent",
                call_id=normal_tool["id"],
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": normal_tool["id"],
                    "content": output,
                }
            )

    for item in reversed(messages):
        if item["role"] == "assistant" and item.get("content"):
            return str(item["content"])
    return "Error: subagent reached its turn limit without a response"
