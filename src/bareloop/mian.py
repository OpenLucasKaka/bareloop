import logging
import os

os.environ["TRANSFORMERS_VERBOSITY"] = "error"
os.environ["HF_HUB_VERBOSITY"] = "error"

for logger_name in (
        "httpx2",
        "httpcore",
        "mcp",
        "transformers",
        "huggingface_hub",
):
    logging.getLogger(logger_name).setLevel(logging.ERROR)

import asyncio
from contextlib import suppress
from bareloop.cron_scheduler import agent_lock, start_cron_scheduler
from bareloop.loop import agent_loop
from bareloop.skills import _scan_skills, list_skills
from bareloop.settings import WORKDIR, PROMPT_SESSION
from bareloop.hook import hook as init_hooks, trigger_hook
from bareloop.trace import TraceWriter
from bareloop.agent_team import BUS, consume_lead_inbox, active_teammates
from bareloop.mcp_integration import mcp_init
from bareloop.utils import format_team_events


def run_agent_turn_locked(messages, tw: TraceWriter, user_input: str | None = None, ):
    if user_input is not None:
        trigger_hook("PreUserPromptInput", user_input)
        messages.append({"role": "user", "content": user_input})
    agent_loop(messages, tw)




def build_system():
    return f"""
        你是一个 coding agent，工作目录是 {WORKDIR}。

        以下是已经扫描并注册的 Skill：
        {list_skills()}

        你需要自行判断用户请求是否匹配某个 Skill。
        如果决定使用 Skill，必须先调用 load_skill，并传入目录中的精确 Skill name。
        Skill 已经注册，不要使用 glob、read、bash 等工具在文件系统中搜索 Skill。
        获得 load_skill 返回的完整内容后，严格按照 Skill 执行。

        如果工作目录中没有用户需要的普通文件，可以查找工作区之外的目录。
        """


def create_session():
    pass


async def wait_for_cli_event():
    prompt_task = asyncio.create_task(
        PROMPT_SESSION.prompt_async("请输入> ")
    )
    try:
        while prompt_task.done():
            if BUS.peek("lead"):
                prompt_task.cancel()
                with suppress(asyncio.CancelledError):
                    await prompt_task
                return "wake", None
            await asyncio.sleep(0.25)

        content = await prompt_task
    except (EOFError, KeyboardInterrupt):
        return "quit", None
    if content in {"quit", "q", "exit"}:
        return "quit", None
    return "user", content




def init_agent():
    # 注册hooks
    init_hooks()
    # 启动mcp
    asyncio.run(mcp_init())
    # 扫描skill
    _scan_skills()
    # 启动cron定时任务队列
    start_cron_scheduler()
    had_teammates = False
    TW = TraceWriter()
    print('输入问题，回车发送。输入 q 退出。\n')
    messages = [
        {'role': 'system', "content": build_system()}
    ]
    while True:
        try:
            kind, input_prompt = asyncio.run(wait_for_cli_event())
            if kind == 'user':
                TW.write(event_type='用户输入', data=input_prompt)
            if kind == "quit":
                TW.write(event_type='停止对话')
                break
            if kind == 'wake':
                inbox = consume_lead_inbox()
                if not inbox:
                    continue
                messages.append({
                    "role": "user",
                    "content": format_team_events(inbox),
                })
                print(f"[wake: {len(inbox)} team event(s) -> new turn]")
        except (EOFError, KeyboardInterrupt):
            break
        with agent_lock:
            run_agent_turn_locked(messages, TW, input_prompt)
        if active_teammates:
            had_teammates = True
        if not input_prompt:
            continue
        elif had_teammates and not BUS.peek("lead"):
            print("[all teammates shut down]")
            had_teammates = False


if __name__ == "__main__":
    asyncio.run(init_agent())