from collections.abc import Callable
from pathlib import Path
from typing import Any

from bareloop.goal import GoalController


class AgentSession:
    def __init__(
        self,
        prompt: str,
        tool: list[dict],
        client: Any,
        goal: GoalController,
        workdir: Path,
        max_turns: int | None = None,
        background_running: Callable[[], bool] | None = None,
    ):
        self.prompt = prompt
        self.client = client
