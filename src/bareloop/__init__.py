"""BareLoop: A transparent, OpenAI-compatible agent harness built from first principles."""

from bareloop.agent import Agent, SimpleTokenizer
from bareloop.plugin import Plugin, discover_plugins, hook, tool
from bareloop.session import AgentSession, RunResult
from bareloop.tools.models import ToolDefinition

__version__ = "0.1.0"

__all__ = [
    "Agent",
    "AgentSession",
    "Plugin",
    "RunResult",
    "SimpleTokenizer",
    "ToolDefinition",
    "discover_plugins",
    "hook",
    "tool",
    "__version__",
]
