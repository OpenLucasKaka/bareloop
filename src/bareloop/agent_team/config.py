from bareloop.tools.registry import get_tool_definitions



TEAMMATE_TOOLS = [
    *get_tool_definitions('main'),
    {"name": "send_message",
     "description": "Send an intermediate message to 'lead' or an active teammate.",
     "input_schema": {"type": "object",
                      "properties": {"to": {"type": "string"},
                                     "content": {"type": "string"}},
                      "required": ["to", "content"]}},
    {"name": "submit_plan",
     "description": "Submit a work plan for Lead approval.",
     "input_schema": {"type": "object",
                      "properties": {"plan": {"type": "string"}},
                      "required": ["plan"]}},
    # next(tool for tool in TASK_TOOLS if tool["name"] == "list_tasks"),
    # next(tool for tool in TASK_TOOLS if tool["name"] == "claim_task"),
    # next(tool for tool in TASK_TOOLS if tool["name"] == "complete_task"),
]