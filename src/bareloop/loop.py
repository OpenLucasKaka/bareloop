from typing import Any

from bareloop.background_system import inject_background_results
from bareloop.compact import CONTEXT_LIMIT, compact_history, micro_compact, tool_budget_result
from bareloop.settings import PRIMARY_MODEL, client, tokenizer
from bareloop.cron_scheduler import consume_cron_queue
from bareloop.hook import trigger_hook
from bareloop.memory import consolidate_memories, extract_memories, load_memories
from bareloop.tools.dispatcher import dispatch_tool
from bareloop.tools.registry import get_tool_schemas
from bareloop.utils import normalize_tool_call
from bareloop.trace import TraceWriter


MAIN_TOOL_SCHEMAS = get_tool_schemas()

def agent_loop(messages: list, tw: TraceWriter):
    fired = consume_cron_queue()
    if fired:
        tw.write(event_type='收集定时任务', data=fired)
    for job in fired:
        messages.append({"role": "user", "content": f"[Scheduled] {job.prompt}"})
        print(f"  [cron] delivered {job.id}: {job.prompt[:60]}")
    rounds_since_todo = 0
    memories_content = load_memories(messages)
    tw.write(event_type='提取相关记忆', data=memories_content)
    altitude_index = len(messages) - 1 if messages and messages[-1]["role"] == "user" else None
    current_messages_count = len(messages) - 1
    while True:
        inject_background_results(messages)
        if rounds_since_todo >= 3 and messages:
            rounds_since_todo = 0
            messages.append(
                {"role": "developer", "content": "<reminder>Update your todos.</reminder>"}
            )
        messages[:] = tool_budget_result(messages)
        messages[:] = micro_compact(messages)
        current_token = tokenizer.apply_chat_template(
            messages,
            tools=MAIN_TOOL_SCHEMAS,
            tokenize=True,
            add_generation_prompt=True,
        )
        if len(current_token) > CONTEXT_LIMIT:
            print("[auto compact]")
            messages[:] = compact_history(messages)
        try:
            request_messages = messages
            if messages and 0 <= altitude_index <= len(messages) - 1:
                request_messages = messages.copy()
                request_messages[altitude_index] = {
                    **request_messages[altitude_index],
                    "content": (
                        f"{memories_content} \n "
                        f"{request_messages[altitude_index]['content']}"
                    ),
                }
        except Exception as e:
            print(f"Error: {e}")
            return
        try:
            response = client.chat.completions.create(
                model=PRIMARY_MODEL, messages=request_messages, tools=MAIN_TOOL_SCHEMAS
            )
        except Exception as e:
            print(f"Error: {e}")
            return
            # messages[:] = reactive_compact(messages)
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
            tool_count = trigger_hook("Stop", messages)
            print(message.content)
            if tool_count:
                print(f"本轮对话结束: 共调用工具次数:{tool_count}")
            extract_memories(messages, current_messages_count)
            consolidate_memories()
            return
        rounds_since_todo += 1
        if message.tool_calls:
            for tool in message.tool_calls:
                normal_tool = normalize_tool_call(tool)
                blocked = trigger_hook("PreToolUse", normal_tool)
                if blocked:
                    messages.append(
                        {"role": "tool", "tool_call_id": normal_tool["id"], "content": blocked}
                    )
                    continue
                output = dispatch_tool(
                    normal_tool["name"],
                    normal_tool["arguments"],
                    call_id=normal_tool["id"],
                )
                messages.append(
                    {"role": "tool", "tool_call_id": normal_tool["id"], "content": output}
                )
