# 初版作为hook的来实现
from ctypes import DEFAULT_MODE
from dataclasses import dataclass
from typing import Any
import json

from bareloop.mode import AgentMode
from bareloop.settings import PRIMARY_MODEL, client
from .prompt_version import GOAL_GATE_SYSTEM_PROMPT

# from bareloop.settings import client, FALLBACK_MODEL

DEFAULT_STOP_HOOK_BLOCK_CAP = 8


@dataclass
class GoalState:
    condition: str
    iterations: int
    set_at: float
    tokens_at_start: int
    last_reason: str | None = None


class GoalController:
    def __init__(
        self,
        evaluator: Any,
        block_num: int = DEFAULT_STOP_HOOK_BLOCK_CAP,
        events: list[dict[str, Any]] | None = None,
    ):
        if block_num < 1:
            raise ValueError("Block number must be greater than 0")
        self.evaluator = evaluator
        self.block_num = block_num
        self.events = events
        self.active: GoalState | None = None
        self.last_status: dict[str, Any] | None = None
        self.consecutive_blocks = 0

"""
/goal 会保存一个 persistent goal：最初的 goal 文本既是第一条任务指令，也是长期完成标准。
运行中的后续消息只是 steer，用来补充上下文或调整约束，不会替换原始 goal
"""
def stop_goal_gate(messages):
    if DEFAULT_MODE != AgentMode.GOAL:
        return
    # 避免污染上下文
    evaluation_messages = [
        {
            "role": "system",
            "content": GOAL_GATE_SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "goal": condition,
                    "evidence": turn_messages,
                },
                ensure_ascii=False,
                default=str,
            ),
        },
    ]
    response = client.chat.completions.create(
        model=PRIMARY_MODEL,
        messages=evaluation_messages
    )
    pass
