from copy import deepcopy
from typing import Any

from bareloop.background_system import inject_background_results
from bareloop.compact import CONTEXT_LIMIT, compact_history, micro_compact, tool_budget_result
from bareloop.cron_scheduler import acknowledge_cron_jobs, consume_cron_queue, restore_cron_jobs
from bareloop.hook import trigger_hook
from bareloop.memory import consolidate_memories, extract_memories, load_memories
from bareloop.mode import AgentMode
from bareloop.settings import PRIMARY_MODEL, client, tokenizer
from bareloop.tools.dispatcher import dispatch_tool
from bareloop.tools.registry import get_tool_schemas
from bareloop.trace import TraceWriter
from bareloop.utils import normalize_tool_call


def agent_loop(
    messages: list,
    tw: TraceWriter,
    mode: AgentMode = AgentMode.NORMAL,
):
    fired = consume_cron_queue()
    scheduled_messages: list[dict[str, str]] = []
    turn_messages = (
        [deepcopy(messages[-1])] if messages and messages[-1].get("role") == "user" else []
    )
    delivery_state = {"accepted": False}
    try:
        completed = _run_agent_loop(
            messages,
            tw,
            fired,
            scheduled_messages,
            turn_messages,
            delivery_state,
            mode,
        )
    except BaseException:
        if delivery_state["accepted"]:
            acknowledge_cron_jobs(fired)
        else:
            _remove_messages(messages, scheduled_messages)
            restore_cron_jobs(fired)
        raise
    if completed or delivery_state["accepted"]:
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
    turn_messages: list[dict[str, Any]],
    delivery_state: dict[str, bool],
    mode: AgentMode,
) -> bool:
    if fired:
        tw.write(event_type="收集定时任务", data=fired)
    for job in fired:
        scheduled_message = {"role": "user", "content": f"[Scheduled] {job.prompt}"}
        messages.append(scheduled_message)
        scheduled_messages.append(scheduled_message)
        turn_messages.append(deepcopy(scheduled_message))
        print(f"  [cron] delivered {job.id}: {job.prompt[:60]}")
    rounds_since_todo = 0
    memories_content = load_memories(messages)
    tw.write(event_type="提取相关记忆", data=memories_content)
    while True:
        message_count = len(messages)
        inject_background_results(messages)
        turn_messages.extend(deepcopy(messages[message_count:]))
        if rounds_since_todo >= 3 and messages:
            rounds_since_todo = 0
            messages.append(
                {"role": "developer", "content": "<reminder>Update your todos.</reminder>"}
            )
        messages[:] = tool_budget_result(messages)
        messages[:] = micro_compact(messages)
        tool_schemas = get_tool_schemas()
        current_token = tokenizer.apply_chat_template(
            messages,
            tools=tool_schemas,
            tokenize=True,
            add_generation_prompt=True,
        )
        if len(current_token) > CONTEXT_LIMIT:
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
                        f"{memories_content} \n {request_messages[memory_target_index]['content']}"
                    ),
                }
                break
        try:
            response = client.chat.completions.create(
                model=PRIMARY_MODEL, messages=request_messages, tools=tool_schemas
            )
        except Exception as e:
            print(f"Error: {e}")
            return False
            # messages[:] = reactive_compact(messages)
        message = response.choices[0].message
        if fired:
            delivery_state["accepted"] = True

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
        turn_messages.append(deepcopy(assistant_message))
        if not message.tool_calls:
            if mode == AgentMode.GOAL:
                decision = trigger_hook("StopGoalGate", messages)
                # goal eval后未完成目标
                if decision and decision.action == "block":
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                f"[Goal still active]\n"
                                f"Evaluator: {decision.reason}\n"
                                "Continue working."
                            ),
                        }
                    )
                    continue
            tool_count = trigger_hook("Stop", messages)
            print(message.content)
            if tool_count:
                print(f"本轮对话结束: 共调用工具次数:{tool_count}")
            extract_memories(turn_messages, 0)
            consolidate_memories()
            return True
        rounds_since_todo += 1
        if message.tool_calls:
            for tool in message.tool_calls:
                normal_tool = normalize_tool_call(tool)
                blocked = trigger_hook("PreToolUse", normal_tool)
                if blocked:
                    tool_message = {
                        "role": "tool",
                        "tool_call_id": normal_tool["id"],
                        "content": blocked,
                    }
                    messages.append(tool_message)
                    turn_messages.append(deepcopy(tool_message))
                    continue
                output = dispatch_tool(
                    normal_tool["name"],
                    normal_tool["arguments"],
                    call_id=normal_tool["id"],
                )
                tool_message = {
                    "role": "tool",
                    "tool_call_id": normal_tool["id"],
                    "content": output,
                }
                messages.append(tool_message)
                turn_messages.append(deepcopy(tool_message))
