from collections.abc import Callable, Collection
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any

from bareloop.background_system import inject_background_results
from bareloop.cli_loading import ModelLoading
from bareloop.compact import CONTEXT_LIMIT, compact_history, micro_compact, tool_budget_result
from bareloop.cron_scheduler import acknowledge_cron_jobs, consume_cron_queue, restore_cron_jobs
from bareloop.goal import GoalController, stop_goal_gate
from bareloop.hook import trigger_hook
from bareloop.memory import consolidate_memories, extract_memories, load_memories
from bareloop.mode import AgentMode
from bareloop.settings import PRIMARY_MODEL, client, tokenizer
from bareloop.telemetry import (
    ProviderCallMetric,
    RunTelemetry,
    RunTermination,
    ToolCallMetric,
    persist_run_telemetry,
)
from bareloop.tools.dispatcher import dispatch_tool_result
from bareloop.tools.registry import get_tool_schemas
from bareloop.trace import TraceWriter
from bareloop.utils import normalize_tool_call

_MEMORY_MAINTENANCE_EXECUTOR = ThreadPoolExecutor(
    max_workers=1,  # 避免多个worker同时修改memory
    thread_name_prefix="bareloop-memory",
)


@dataclass(frozen=True)
class LoopExecutionResult:
    completed: bool
    final_output: str
    tool_calls: int
    rounds: int
    error: str | None = None
    model: str | None = None
    telemetry: RunTelemetry = field(default_factory=RunTelemetry)
    termination: RunTermination = RunTermination.COMPLETED


def _maintain_memories(
    turn_messages: list[dict[str, Any]],
    extract: Callable[[list[dict[str, Any]], int], Any],
    consolidate: Callable[[], Any],
) -> None:
    if extract(turn_messages, 0):
        consolidate()


def schedule_memory_maintenance(
    turn_messages: list[dict[str, Any]],
) -> Future[None]:
    # 让 memory extraction 在后台线程执行
    return _MEMORY_MAINTENANCE_EXECUTOR.submit(
        _maintain_memories,
        deepcopy(turn_messages),
        extract_memories,
        consolidate_memories,
    )


def wait_for_memory_maintenance() -> None:
    _MEMORY_MAINTENANCE_EXECUTOR.submit(lambda: None).result()


def agent_loop(
    messages: list,
    tw: TraceWriter,
    mode: AgentMode = AgentMode.NORMAL,
    goal_controller: GoalController | None = None,
):
    fired = consume_cron_queue()
    scheduled_messages: list[dict[str, str]] = []
    # 用于记忆提取的每次完整完整轮次的对话
    memory_evidence_messages = (
        [deepcopy(messages[-1])] if messages and messages[-1].get("role") == "user" else []
    )
    # 目前只处理模型调用可能失败的阶段
    cron_delivery_state = {
        "model_accepted": False
    }
    try:
        completed = _run_agent_loop(
            messages,
            tw,
            fired,
            scheduled_messages,
            memory_evidence_messages,
            cron_delivery_state,
            mode,
            goal_controller,
        )
    except BaseException:
        if cron_delivery_state["model_accepted"]:
            acknowledge_cron_jobs(fired)
        else:
            _remove_messages(messages, scheduled_messages)
            restore_cron_jobs(fired)
        raise
    if completed or cron_delivery_state["model_accepted"]:
        # 负责收尾cron任务
        acknowledge_cron_jobs(fired)
    else:
        _remove_messages(messages, scheduled_messages)
        restore_cron_jobs(fired)


def _remove_messages(messages: list, removed: list[dict[str, str]]) -> None:
    messages[:] = [message for message in messages if all(message is not item for item in removed)]


def _run_agent_loop(
    messages: list,
    tw: TraceWriter,
    fired: list,
    scheduled_messages: list[dict[str, str]],
    memory_evidence_messages: list[dict[str, Any]],
    cron_delivery_state: dict[str, bool],
    mode: AgentMode,
    goal_controller: GoalController | None = None,
) -> bool:
    started_at = perf_counter()
    result = execute_agent_loop(
        messages,
        client=client,
        model=PRIMARY_MODEL,
        tokenizer=tokenizer,
        workdir=None,
        max_rounds=None,
        allowed_tool_names=None,
        trace=tw,
        mode=mode,
        goal_controller=goal_controller,
        collect_cron_jobs=fired,
        scheduled_messages=scheduled_messages,
        memory_evidence_messages=memory_evidence_messages,
        cron_delivery_state=cron_delivery_state,
        enable_background=True,
        enable_memory=True,
        enable_loading=True,
        enable_goal_gate=True,
        enable_hooks=True,
        enable_finalizers=True,
        print_output=True,
    )
    persist_run_telemetry(
        result.telemetry,
        model=result.model,
        duration_ms=(perf_counter() - started_at) * 1000,
        rounds=result.rounds,
        tool_calls=result.tool_calls,
        termination=result.termination,
    )
    return result.completed


def execute_agent_loop(
    messages: list[dict[str, Any]],
    *,
    client: Any,
    model: str | None,
    tokenizer: Any,
    workdir: str | Path | None,
    max_rounds: int | None,
    allowed_tool_names: Collection[str] | None = None,
    trace: TraceWriter | None = None,
    mode: AgentMode = AgentMode.NORMAL,
    goal_controller: GoalController | None = None,
    collect_cron_jobs: list[Any] | None = None,
    scheduled_messages: list[dict[str, str]] | None = None,
    memory_evidence_messages: list[dict[str, Any]] | None = None,
    cron_delivery_state: dict[str, bool] | None = None, # cron任务的状态
    enable_background: bool = False,
    enable_memory: bool = False,
    enable_loading: bool = False,
    enable_goal_gate: bool = False,
    enable_hooks: bool = False,
    enable_finalizers: bool = False,
    print_output: bool = False,
) -> LoopExecutionResult:
    if max_rounds is not None and max_rounds < 1:
        raise ValueError("max_rounds must be at least 1")
    # 收集到的cron jobs
    collect_cron_jobs = collect_cron_jobs if collect_cron_jobs is not None else []
    scheduled_messages = scheduled_messages if scheduled_messages is not None else []
    memory_evidence_messages = memory_evidence_messages if memory_evidence_messages is not None else []
    cron_delivery_state = cron_delivery_state if cron_delivery_state is not None else {"accepted": False}
    first_round = True
    rounds = 0
    tool_calls = 0
    final_output = ""
    telemetry = RunTelemetry()

    if collect_cron_jobs and trace is not None:
        trace.write(event_type="收集定时任务", data=collect_cron_jobs)
    for job in collect_cron_jobs:
        scheduled_message = {"role": "user", "content": f"[Scheduled] {job.prompt}"}
        messages.append(scheduled_message)
        scheduled_messages.append(scheduled_message)
        memory_evidence_messages.append(deepcopy(scheduled_message))
        if print_output:
            print(f"  [cron] delivered {job.id}: {job.prompt[:60]}")

    # 记录当前轮次用于提醒当前规划任务
    rounds_since_todo = 0
    memories_content = ""

    while True:
        if max_rounds is not None and rounds >= max_rounds:
            return LoopExecutionResult(
                completed=False,
                final_output=final_output,
                tool_calls=tool_calls,
                rounds=rounds,
                error=f"maximum rounds reached ({max_rounds})",
                model=model,
                telemetry=telemetry,
                termination=RunTermination.MAX_ROUNDS,
            )
        provider_failed = False
        try:
            memory_messages = messages.copy()
            message_count = len(messages)
            if enable_background:
                inject_background_results(messages)
                memory_evidence_messages.extend(deepcopy(messages[message_count:]))
            loading_context = ModelLoading() if enable_loading else nullcontext()
            with loading_context:
                if first_round:
                    # 耗时记忆召回 成熟方案是通过本地检索 目前是通过llm调用
                    # 避免处理两个ModelLoading 将召回记忆放到循环中, 并用标识避免多次调用
                    if enable_memory:
                        memories_content = load_memories(memory_messages)
                        if trace is not None:
                            trace.write(event_type="提取相关记忆", data=memories_content)
                    first_round = False
                if rounds_since_todo >= 3 and messages:
                    rounds_since_todo = 0
                    messages.append(
                        {"role": "user", "content": "<reminder>Update your todos.</reminder>"}
                    )
                # 压缩策略L1: 将工具调用输出结果过大的落盘本地 通过占位符替换 需要时根据id去查找
                messages[:] = tool_budget_result(messages)
                # 压缩策略L1: 将距当前远的工具调用结果删除 需要时再重新调用工具
                messages[:] = micro_compact(messages)
                tool_schemas = get_tool_schemas()
                if allowed_tool_names is not None:
                    tool_schemas = [
                        tool
                        for tool in tool_schemas
                        if tool["function"]["name"] in allowed_tool_names
                    ]
                current_token = tokenizer.apply_chat_template(
                    messages,
                    tools=tool_schemas,
                    tokenize=True,
                    add_generation_prompt=True,
                )
                #压缩策略L2: 超过content限制主动压缩
                if len(current_token) > CONTEXT_LIMIT:
                    if print_output:
                        print("[auto compact]")
                    messages[:] = compact_history(messages)
                request_messages = messages
                if memories_content:
                    for memory_target_index in range(len(messages) - 1, -1, -1):
                        if messages[memory_target_index].get("role") != "user":
                            continue
                        request_messages = messages.copy()
                        request_messages[memory_target_index] = {
                            **request_messages[memory_target_index],
                            "content": (
                                f"{memories_content} \n "
                                f"{request_messages[memory_target_index]['content']}"
                            ),
                        }
                        break
                rounds += 1
                provider_started_at = perf_counter()
                try:
                    response = client.chat.completions.create(
                        model=model, messages=request_messages, tools=tool_schemas
                    )
                except Exception as error:
                    #压缩策略L3: 还需处理因context超出limit错误 被动压缩
                    # messages[:] = reactive_compact(messages)
                    provider_failed = True
                    telemetry.provider_calls.append(
                        ProviderCallMetric.failed_call(
                            (perf_counter() - provider_started_at) * 1000,
                            error,
                        )
                    )
                    raise
                telemetry.provider_calls.append(
                    ProviderCallMetric.succeeded_call(
                        (perf_counter() - provider_started_at) * 1000,
                        getattr(response, "usage", None),
                    )
                )
                message = response.choices[0].message
        except Exception as error:
            if print_output:
                print(f"Error: {error}")
            return LoopExecutionResult(
                completed=False,
                final_output=final_output,
                tool_calls=tool_calls,
                rounds=rounds,
                error=f"{type(error).__name__}: {error}",
                model=model,
                telemetry=telemetry,
                termination=(
                    RunTermination.PROVIDER_ERROR
                    if provider_failed
                    else RunTermination.HARNESS_ERROR
                ),
            )
        if collect_cron_jobs:
            cron_delivery_state["model_accepted"] = True

        response_tool_calls = message.tool_calls or []
        assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": message.content or "",
        }
        final_output = assistant_message["content"]
        if response_tool_calls:
            assistant_message["tool_calls"] = [
                {
                    "id": tool.id,
                    "type": "function",
                    "function": {
                        "name": tool.function.name,
                        "arguments": tool.function.arguments,
                    },
                }
                for tool in response_tool_calls
            ]
        messages.append(assistant_message)
        memory_evidence_messages.append(deepcopy(assistant_message))
        if not response_tool_calls:
            if (
                enable_goal_gate
                and mode == AgentMode.GOAL
                and goal_controller is not None
                and goal_controller.active is not None
            ):
                decision = stop_goal_gate(goal_controller, deepcopy(messages))
                if not decision.ok and not decision.impossible:
                    continuation = {
                        "role": "user",
                        "content": goal_controller.continuation_message(decision.reason),
                    }
                    messages.append(continuation)
                    memory_evidence_messages.append(deepcopy(continuation))
                    continue
                if not decision.ok:
                    if print_output:
                        print(message.content)
                        print(f"[goal blocked] {decision.reason}")
                    return LoopExecutionResult(
                        completed=False,
                        final_output=final_output,
                        tool_calls=tool_calls,
                        rounds=rounds,
                        error=decision.reason,
                        model=model,
                        telemetry=telemetry,
                        termination=RunTermination.GOAL_BLOCKED,
                    )
            hook_tool_count = (
                trigger_hook("Stop", messages) if enable_finalizers and enable_hooks else None
            )
            if print_output:
                print(message.content)
                if hook_tool_count:
                    print(f"本轮对话结束: 共调用工具次数:{hook_tool_count}")
            if enable_finalizers and enable_memory:
                schedule_memory_maintenance(memory_evidence_messages)
            return LoopExecutionResult(
                completed=True,
                final_output=final_output,
                tool_calls=tool_calls,
                rounds=rounds,
                model=model,
                telemetry=telemetry,
                termination=RunTermination.COMPLETED,
            )
        rounds_since_todo += 1
        tool_calls += len(response_tool_calls)
        if response_tool_calls:
            for tool in response_tool_calls:
                try:
                    normal_tool = normalize_tool_call(tool)
                except Exception as error:
                    function = getattr(tool, "function", None)
                    telemetry.tool_calls.append(
                        ToolCallMetric(
                            name=str(getattr(function, "name", "<invalid>")),
                            outcome="invalid",
                            error_kind="invalid_arguments",
                        )
                    )
                    if print_output:
                        print(f"Error: {error}")
                    return LoopExecutionResult(
                        completed=False,
                        final_output=final_output,
                        tool_calls=tool_calls,
                        rounds=rounds,
                        error=f"{type(error).__name__}: {error}",
                        model=model,
                        telemetry=telemetry,
                        termination=RunTermination.INVALID_TOOL_CALL,
                    )
                if allowed_tool_names is not None and normal_tool["name"] not in allowed_tool_names:
                    blocked = f"Error: tool '{normal_tool['name']}' is not allowed in this session"
                else:
                    blocked = trigger_hook("PreToolUse", normal_tool) if enable_hooks else None
                if blocked:
                    telemetry.tool_calls.append(
                        ToolCallMetric(
                            name=normal_tool["name"],
                            outcome="blocked",
                            error_kind=(
                                "allowlist"
                                if allowed_tool_names is not None
                                and normal_tool["name"] not in allowed_tool_names
                                else "hook"
                            ),
                        )
                    )
                    tool_message = {
                        "role": "tool",
                        "tool_call_id": normal_tool["id"],
                        "content": blocked,
                    }
                    messages.append(tool_message)
                    memory_evidence_messages.append(deepcopy(tool_message))
                    continue
                dispatch_result = dispatch_tool_result(
                    normal_tool["name"],
                    normal_tool["arguments"],
                    call_id=normal_tool["id"],
                    workspace=workdir,
                )
                telemetry.tool_calls.append(
                    ToolCallMetric(
                        name=normal_tool["name"],
                        outcome=dispatch_result.outcome,
                        error_kind=dispatch_result.error_kind,
                    )
                )
                if dispatch_result.security_block:
                    telemetry.security_blocks += 1
                if dispatch_result.safety_violation:
                    telemetry.safety_violations += 1
                tool_message = {
                    "role": "tool",
                    "tool_call_id": normal_tool["id"],
                    "content": dispatch_result.output,
                }
                messages.append(tool_message)
                memory_evidence_messages.append(deepcopy(tool_message))
