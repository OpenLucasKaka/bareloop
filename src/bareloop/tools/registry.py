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


def _parameters(
    properties: dict[str, Any],
    required: tuple[str, ...] = (),
    **constraints: Any,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
        **constraints,
    }


def _string(description: str | None = None, **constraints: Any) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "string", **constraints}
    if description is not None:
        schema["description"] = description
    return schema


def _teammate_handler(name: str):
    def handler(**kwargs: Any):
        from bareloop.tools.adapters import teamate

        return getattr(teamate, name)(**kwargs)

    return handler


_TOOL_DEFINITIONS = (
    ToolDefinition(
        name="bash",
        description="执行终端命令",
        parameters=_parameters(
            {
                "command": _string("需要执行的命令"),
                "cwd": _string("可选的命令工作目录"),
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
                "cwd": _string("可选的文件操作根目录"),
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
                "cwd": _string("可选的文件操作根目录"),
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
                "cwd": _string("可选的文件操作根目录"),
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
            {
                "pattern": _string("相对于工作目录的 glob pattern"),
                "cwd": _string("可选的文件操作根目录"),
            },
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
    ToolDefinition(
        name="spawn_teammate",
        description="Spawn a persistent teammate.",
        parameters=_parameters(
            {
                "name": _string(pattern="^[A-Za-z0-9_-]{1,64}$"),
                "role": _string(),
                "prompt": _string(),
                "task_id": _string(pattern="^task_[0-9a-f]{8}$"),
                "require_plan": {"type": "boolean"},
            },
            ("name", "role", "prompt"),
        ),
        handler=_teammate_handler("run_spawn_teammate"),
    ),
    ToolDefinition(
        name="list_teammates",
        description="List active teammates.",
        parameters=_parameters({}),
        handler=_teammate_handler("run_list_teammates"),
    ),
    ToolDefinition(
        name="send_message",
        description="Message a teammate.",
        parameters=_parameters(
            {
                "to": _string(),
                "content": _string(),
            },
            ("to", "content"),
        ),
        handler=_teammate_handler("run_send_messages"),
    ),
    ToolDefinition(
        name="request_shutdown",
        description="Ask a teammate to shut down.",
        parameters=_parameters({"teammate": _string()}, ("teammate",)),
        handler=_teammate_handler("run_request_shutdown"),
    ),
    ToolDefinition(
        name="request_plan",
        description="Require a teammate plan before workspace changes.",
        parameters=_parameters(
            {
                "teammate": _string(),
                "task": _string(),
            },
            ("teammate", "task"),
        ),
        handler=_teammate_handler("run_request_plan"),
    ),
    ToolDefinition(
        name="review_plan",
        description="Approve or reject a plan.",
        parameters=_parameters(
            {
                "request_id": _string(),
                "approve": {"type": "boolean"},
                "feedback": _string(),
            },
            ("request_id", "approve"),
        ),
        handler=_teammate_handler("run_review_plan"),
    ),
    ToolDefinition(
        name="create_worktree",
        description="Create and bind a task worktree.",
        parameters=_parameters(
            {
                "name": _string(
                    pattern="^(?!.*\\.\\.)[A-Za-z0-9][A-Za-z0-9._-]{0,63}$",
                    maxLength=64,
                ),
                "task_id": _string(),
            },
            ("name", "task_id"),
        ),
        handler=_teammate_handler("run_create_worktree"),
    ),
)

_TOOLS_BY_NAME = {definition.name: definition for definition in _TOOL_DEFINITIONS}
_DYNAMIC_TOOLS_BY_NAME: dict[str, ToolDefinition] = {}
if len(_TOOLS_BY_NAME) != len(_TOOL_DEFINITIONS):
    raise RuntimeError("Duplicate tool name in registry")


def get_tool_definitions(scope: ToolScope = "main") -> tuple[ToolDefinition, ...]:
    if scope == "main":
        return (*_TOOL_DEFINITIONS, *_DYNAMIC_TOOLS_BY_NAME.values())
    if scope == "subagent":
        return tuple(tool for tool in _TOOL_DEFINITIONS if tool.allow_subagent)
    raise ValueError(f"Unknown tool scope: {scope}")


def get_tool_schemas(scope: ToolScope = "main") -> list[dict[str, Any]]:
    return [definition.as_openai_tool() for definition in get_tool_definitions(scope)]


def get_tool(name: str) -> ToolDefinition | None:
    return _DYNAMIC_TOOLS_BY_NAME.get(name) or _TOOLS_BY_NAME.get(name)


def register_dynamic_tools(definitions: list[ToolDefinition]) -> None:
    pending: dict[str, ToolDefinition] = {}
    for definition in definitions:
        if definition.name in _TOOLS_BY_NAME:
            raise ValueError(f"Tool name conflicts with builtin tool: {definition.name}")
        existing = pending.get(definition.name) or _DYNAMIC_TOOLS_BY_NAME.get(definition.name)
        if existing is not None and existing != definition:
            raise ValueError(f"Duplicate dynamic tool name: {definition.name}")
        pending[definition.name] = definition
    _DYNAMIC_TOOLS_BY_NAME.update(pending)


def clear_dynamic_tools() -> None:
    _DYNAMIC_TOOLS_BY_NAME.clear()
