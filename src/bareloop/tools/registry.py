from typing import Any, Literal

from bareloop.tools.adapters.skill import load_skill
from bareloop.tools.adapters.subagent import spaw_subagent
from bareloop.tools.adapters.task import (
    run_claim_task,
    run_complete_task,
    run_create_task,
    run_get_task,
    run_list_tasks,
)
from bareloop.tools.filesystem import run_edit, run_glob, run_read, run_write
from bareloop.tools.models import ToolDefinition
from bareloop.tools.shell import run_bash

ToolScope = Literal["main", "subagent"]


def _parameters(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _string(description: str) -> dict[str, str]:
    return {"type": "string", "description": description}


_TOOL_DEFINITIONS = (
    ToolDefinition(
        name="bash",
        description="执行终端命令",
        parameters=_parameters(
            {
                "command": _string("需要执行的命令"),
                "shouldBack": {
                    "type": "boolean",
                    "description": "是否在后台执行长任务",
                },
            },
            ("command",),
        ),
        handler=run_bash,
        allow_subagent=True,
        supports_background=True,
    ),
    ToolDefinition(
        name="edit",
        description="替换文件中的一处文本",
        parameters=_parameters(
            {
                "path": _string("相对于工作目录的文件路径"),
                "old_text": _string("需要替换的原文本"),
                "new_text": _string("替换后的新文本"),
            },
            ("path", "old_text", "new_text"),
        ),
        handler=run_edit,
        allow_subagent=True,
    ),
    ToolDefinition(
        name="write",
        description="写入文件内容",
        parameters=_parameters(
            {
                "path": _string("相对于工作目录的文件路径"),
                "content": _string("需要写入的内容"),
            },
            ("path", "content"),
        ),
        handler=run_write,
        allow_subagent=True,
    ),
    ToolDefinition(
        name="read",
        description="读取文本文件",
        parameters=_parameters(
            {
                "path": _string("相对于工作目录的文件路径"),
                "limit": {"type": "integer", "description": "最多读取的行数"},
            },
            ("path",),
        ),
        handler=run_read,
        allow_subagent=True,
    ),
    ToolDefinition(
        name="glob",
        description="按 glob pattern 递归查找文件",
        parameters=_parameters(
            {"pattern": _string("相对于工作目录的 glob pattern")},
            ("pattern",),
        ),
        handler=run_glob,
        allow_subagent=True,
    ),
    ToolDefinition(
        name="load_skill",
        description="加载已注册 Skill 的完整内容",
        parameters=_parameters({"name": _string("Skill 的精确名称")}, ("name",)),
        handler=load_skill,
    ),
    ToolDefinition(
        name="spaw_subagent",
        description="委派子 Agent 完成部分任务",
        parameters=_parameters({"query": _string("子 Agent 需要完成的任务")}, ("query",)),
        handler=spaw_subagent,
    ),
    ToolDefinition(
        name="create_task",
        description="创建规划任务",
        parameters=_parameters(
            {
                "subject": _string("任务标题"),
                "description": _string("任务说明"),
                "blockedBy": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "前置任务 ID 列表",
                },
            },
            ("subject",),
        ),
        handler=run_create_task,
    ),
    ToolDefinition(
        name="list_tasks",
        description="列出全部规划任务",
        parameters=_parameters({}),
        handler=run_list_tasks,
    ),
    ToolDefinition(
        name="get_task",
        description="读取一个规划任务",
        parameters=_parameters({"task_id": _string("任务 ID")}, ("task_id",)),
        handler=run_get_task,
    ),
    ToolDefinition(
        name="claim_task",
        description="领取一个未阻塞的规划任务",
        parameters=_parameters({"task_id": _string("任务 ID")}, ("task_id",)),
        handler=run_claim_task,
    ),
    ToolDefinition(
        name="complete_task",
        description="完成一个已领取的规划任务",
        parameters=_parameters({"task_id": _string("任务 ID")}, ("task_id",)),
        handler=run_complete_task,
    ),
)

_TOOLS_BY_NAME = {definition.name: definition for definition in _TOOL_DEFINITIONS}
if len(_TOOLS_BY_NAME) != len(_TOOL_DEFINITIONS):
    raise RuntimeError("Duplicate tool name in registry")


def get_tool_definitions(scope: ToolScope = "main") -> tuple[ToolDefinition, ...]:
    if scope == "main":
        return _TOOL_DEFINITIONS
    if scope == "subagent":
        return tuple(tool for tool in _TOOL_DEFINITIONS if tool.allow_subagent)
    raise ValueError(f"Unknown tool scope: {scope}")


def get_tool_schemas(scope: ToolScope = "main") -> list[dict[str, Any]]:
    return [definition.as_openai_tool() for definition in get_tool_definitions(scope)]


def get_tool(name: str) -> ToolDefinition | None:
    return _TOOLS_BY_NAME.get(name)
