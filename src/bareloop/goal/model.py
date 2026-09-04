from typing import Any
from dataclasses import dataclass

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
            events: list[dict[str, Any]] | None = None
    ):
        if block_num < 1:
            raise ValueError("Block number must be greater than 0")
        self.evaluator = evaluator
        self.block_num = block_num
        self.events = events
        self.active: GoalState | None = None
        self.last_status: dict[str, Any] | None = None
        self.consecutive_blocks = 0



