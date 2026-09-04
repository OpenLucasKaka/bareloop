from typing import Any, Callable
from bareloop.goal import GoalController
from pathlib import Path


class AgentSession:
    def __init__(
            self,
            prompt: str,
            tool: list[dict],
            client:Any,
            goal: GoalController,
            workdir: Path,
            max_turns: int | None = None,
            background_running: Callable[[], bool] | None = None
    ):
        self.prompt = prompt
        self.client = client



