from __future__ import annotations

import asyncio
import logging
import os
from contextlib import suppress
from typing import Any

from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.shortcuts import radiolist_dialog

os.environ["TRANSFORMERS_VERBOSITY"] = "error"
os.environ["HF_HUB_VERBOSITY"] = "error"

for logger_name in (
    "openai",
    "httpx2",
    "httpx",
    "httpcore",
    "mcp",
    "transformers",
    "huggingface_hub",
):
    logging.getLogger(logger_name).setLevel(logging.ERROR)

from bareloop.agent_team import BUS, active_teammates, consume_lead_inbox  # noqa: E402
from bareloop.cron_scheduler import agent_lock, start_cron_scheduler  # noqa: E402
from bareloop.goal import GoalController, create_goal_controller  # noqa: E402
from bareloop.hook import hook as init_hooks  # noqa: E402
from bareloop.hook import trigger_hook  # noqa: E402, F401
from bareloop.loop import agent_loop  # noqa: E402
from bareloop.mcp_integration import (  # noqa: E402
    start_background_mcp_init,
    wait_for_mcp_init,
)
from bareloop.mode import AgentMode  # noqa: E402
from bareloop.settings import (  # noqa: E402
    CLI_STYLE,
    DEFAULT_MODE,
    PROMPT_SESSION,
    WORKDIR,
    DEFAUlT_MODEL,  # noqa: F401
)
from bareloop.skills import SKILL_REGISTRY, _scan_skills, list_skills  # noqa: E402
from bareloop.trace import TraceWriter  # noqa: E402
from bareloop.utils import format_team_events  # noqa: E402


def run_agent_turn_locked(
    messages: list[dict[str, Any]],
    tw: TraceWriter,
    user_input: str | None = None,
    mode: AgentMode = AgentMode.NORMAL,
    goal_controller: GoalController | None = None,
) -> None:
    # 确保在真正执行 Agent turn 之前，后台并发进行的 MCP 握手已完成
    wait_for_mcp_init()
    if user_input is not None:
        if mode == AgentMode.GOAL and goal_controller is not None:
            goal_controller.accept_user_input(user_input)
        messages.append({"role": "user", "content": user_input})
    result = agent_loop(messages, tw, mode, goal_controller)
    if result and result.final_output:
        print(result.final_output)


def build_system() -> str:
    """构建主 Agent 的系统引导提示词。

    注入工作目录、Skill 清单以及多 Agent 协同、任务看板和文件安全读写的引导性决策规范。
    """
    return f"""你是一个具备团队协作与工程规划能力的高级 Coding Agent，工作目录是 {WORKDIR}。

## 核心作业规范与工具决策树（必须严格执行）

1. 【只读深度调研 vs 上下文保护（Subagent）】：
   - 场景：全库排查、跨文件广泛检索、日志或错误排查分析。
   - 规则：必须调用 `spawn_subagent`。它在隔离沙箱中运行并仅返回提炼后的摘要报告，
     严禁在主会话中大量调用 grep/read 导致上下文膨胀和注意力稀释。

2. 【大任务规划与团队分工（Task & Agent Team）】：
   - 场景：涉及多模块开发、代码重构、或需要多角色并行推进的大型工程。
   - 规则：
     * 先调用 `create_task` 拆分子任务看板（设置合理的 blockedBy 依赖）；
     * 需要并行分工或独立协作者时，调用 `spawn_teammate` 创建团队成员并绑定 task_id；
     * 若需要审批修改方案，`spawn_teammate` 必须传入 `require_plan=True`，
       后续通过 `review_plan` 审批；
     * 需要隔离开发环境防止代码冲突时，配合 `create_worktree` 为任务绑定独立 Git 分支工作区。

3. 【代码安全读写规范（Edit vs Write vs Bash）】：
   - 查看现有文件：使用 `read`。
   - 修改已有文件：必须使用 `edit` 进行局部精准替换，
     绝对禁止使用 `write` 覆盖已有代码文件以防冲刷丢失上下文。
   - 严禁使用 bash 执行 cat、sed、echo 来查看或篡改代码文件。

4. 【定时与计划任务（Cron vs Bash）】：
   - 场景：周期性巡检（如每隔 X 分钟）、延时执行需求。
   - 规则：必须调用 `create_cron`，绝对禁止使用 bash 执行 sleep 循环阻塞终端。

5. 【轻量快速查询】：
   - 查看单个指定文件等简单任务，直接调用 `read` 或 `glob`，
     不要过度工程去调用 subagent 或建任务看板。

## 技能加载（Skill Registry）
以下是已经扫描并注册的 Skill：
{list_skills()}

你需要自行判断用户请求是否匹配某个 Skill。
如果决定使用 Skill，必须先调用 load_skill，并传入目录中的精确 Skill name。
Skill 已经注册，不要使用 glob、read、bash 等工具在文件系统中搜索 Skill。
获得 load_skill 返回的完整内容后，严格按照 Skill 执行。

如果工作目录中没有用户需要的普通文件，可以查找工作区之外的目录。"""


def format_cli_help() -> str:
    skill_word = "skill" if len(SKILL_REGISTRY) == 1 else "skills"
    return "\n".join(
        [
            "Available slash commands:",
            "  /mode   Toggle Normal / Goal execution mode",
            "  /clear  Reset conversation context",
            "  /help   Show this help message",
            "  q, quit, exit   Exit the agent session",
            "",
            "Keyboard shortcuts:",
            "  Enter          Send message",
            "  Esc+Enter      Insert newline",
            "  Ctrl+J         Insert newline",
            "",
            f"Workspace: {WORKDIR}",
            f"Loaded skills: {len(SKILL_REGISTRY)} {skill_word}",
        ]
    )


async def wait_for_cli_event(
    selected_mode: AgentMode = DEFAULT_MODE,
) -> tuple[str, str | None, AgentMode]:
    # 拦截后台的 stdout/stderr 避免后台任务打印覆盖
    with patch_stdout(raw=True):
        return await _wait_for_cli_event(selected_mode)


async def _wait_for_cli_event(selected_mode: AgentMode) -> tuple[str, str | None, AgentMode]:
    toolbar = (
        [("class:mode.goal", "  Goal mode · /mode 切换")]
        if selected_mode is AgentMode.GOAL
        else [("class:hint", "  /mode 切换")]
    )
    toolbar.append(("class:hint", f" · {WORKDIR}"))
    toolbar.insert(0, ("", "\n\n\n\n"))
    prompt_task = asyncio.create_task(
        PROMPT_SESSION.prompt_async(
            "› ",
            placeholder=[("class:placeholder", "请输入内容")],
            bottom_toolbar=toolbar,
            style=CLI_STYLE,
        )
    )
    try:
        while not prompt_task.done():
            if BUS.peek("lead"):
                prompt_task.cancel()
                with suppress(asyncio.CancelledError):
                    await prompt_task
                return "wake", None, selected_mode
            await asyncio.sleep(0.25)

        content = await prompt_task
        if content == "/mode":
            new_mode = await radiolist_dialog(
                title="Select mode",
                text="Choose execution mode",
                values=[
                    (AgentMode.NORMAL, "normal"),
                    (AgentMode.GOAL, "goal"),
                ],
                default=selected_mode,
            ).run_async()
            if new_mode is not None:
                selected_mode = new_mode
            return "next", None, selected_mode
        if content == "/help":
            print(format_cli_help())
            return "next", None, selected_mode
    except (EOFError, KeyboardInterrupt):
        return "quit", None, selected_mode
    if content in {"quit", "q", "exit"}:
        return "quit", None, selected_mode
    if content == "/clear":
        return "clear", None, selected_mode
    return "user", content, selected_mode


def create_session() -> None:
    init_hooks()
    start_background_mcp_init()
    _scan_skills()
    had_teammates = False
    tw = TraceWriter()
    goal_controller = create_goal_controller()
    messages: list[dict[str, Any]] = [{"role": "system", "content": build_system()}]
    start_cron_scheduler(messages, tw)
    print("输入问题，回车发送。输入 q 退出。\n")
    mode = DEFAULT_MODE
    while True:
        try:
            previous_mode = mode
            kind, input_prompt, mode = asyncio.run(wait_for_cli_event(mode))

            if mode != previous_mode:
                if mode == AgentMode.GOAL:
                    goal_controller.enter_goal_mode()
                else:
                    goal_controller.leave_goal_mode()

            if kind == "next":
                continue
            if kind == "clear":
                with agent_lock:
                    del messages[1:]
                print("Conversation cleared. Start a new topic.\n")
                continue
            if kind == "user":
                tw.write(event_type="用户输入", data=input_prompt)
            if kind == "quit":
                tw.write(event_type="停止对话")
                break
            if kind == "wake":
                inbox = consume_lead_inbox()
                if not inbox:
                    continue
                messages.append(
                    {
                        "role": "user",
                        "content": format_team_events(inbox),
                    }
                )
                print(f"[Cron Wake: {len(inbox)} team event(s) -> new turn]")
        except (EOFError, KeyboardInterrupt):
            break

        with agent_lock:
            run_agent_turn_locked(messages, tw, input_prompt, mode, goal_controller)
        if active_teammates:
            had_teammates = True
        if not input_prompt:
            continue
        elif had_teammates and not BUS.peek("lead") and active_teammates:
            print("[all teammates shut down]")
            had_teammates = False


# Aliases for standard CLI entry points
init_agent = create_session
main = create_session


if __name__ == "__main__":
    create_session()
