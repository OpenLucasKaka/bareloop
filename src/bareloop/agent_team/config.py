from copy import deepcopy

from bareloop.tools.registry import get_tool_schemas

_RUNTIME_TOOL_NAMES = {
    "bash",
    "read",
    "write",
    "edit",
    "glob",
    "send_message",
    "list_tasks",
    "claim_task",
    "complete_task",
}

TEAMMATE_TOOLS = [
    deepcopy(schema)
    for schema in get_tool_schemas("main")
    if schema["function"]["name"] in _RUNTIME_TOOL_NAMES
]
for schema in TEAMMATE_TOOLS:
    properties = schema["function"]["parameters"]["properties"]
    properties.pop("cwd", None)
    if schema["function"]["name"] == "bash":
        properties.pop("shouldBack", None)
TEAMMATE_TOOLS.append(
    {
        "type": "function",
        "function": {
            "name": "submit_plan",
            "description": "Submit a work plan for Lead approval.",
            "parameters": {
                "type": "object",
                "properties": {"plan": {"type": "string"}},
                "required": ["plan"],
                "additionalProperties": False,
            },
        },
    }
)
