from enum import StrEnum
from dataclasses import dataclass
from typing import Any
from pathlib import Path
from collections.abc import Callable
from bareloop.goal import GoalController


class AgentMode(StrEnum):
    NORMAL = "normal"
    GOAL = "goal"


