from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import bareloop
from bareloop import Agent, Plugin, ToolDefinition, hook, tool


class MockClient:
    def __init__(self, responses: list[Any]):
        self._responses = iter(responses)
        self.requests = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create),
        )

    def _create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        return next(self._responses)


def make_tool_call(call_id: str, name: str, arguments: str) -> Any:
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def make_model_response(content: str | None = None, tool_calls: list[Any] | None = None) -> Any:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=tool_calls),
            )
        ],
        usage=None,
    )


def test_tool_decorator_schema_inference():
    @tool
    def search_database(query: str, limit: int = 10, is_active: bool = True) -> str:
        """Search the active database records."""
        return f"result for {query}"

    defn: ToolDefinition = search_database.__tool_definition__
    assert defn.name == "search_database"
    assert "Search the active database" in defn.description
    assert defn.parameters["type"] == "object"
    assert defn.parameters["properties"]["query"]["type"] == "string"
    assert defn.parameters["properties"]["limit"]["type"] == "integer"
    assert defn.parameters["properties"]["is_active"]["type"] == "boolean"
    assert defn.parameters["required"] == ["query"]

    # Handler works
    assert search_database(query="test") == "result for test"


def test_tool_decorator_custom_name_and_desc():
    @tool(name="calc_add", description="Add two numbers")
    def add(a: int, b: int) -> int:
        return a + b

    defn: ToolDefinition = add.__tool_definition__
    assert defn.name == "calc_add"
    assert defn.description == "Add two numbers"
    assert defn.parameters["required"] == ["a", "b"]
    assert add(1, 2) == 3


def test_plugin_bundle_tools_and_hooks():
    class MathPlugin(Plugin):
        name = "math"

        def __init__(self):
            self.setup_called = False

        def setup(self, agent: Any) -> None:
            self.setup_called = True

        @tool
        def multiply(self, x: int, y: int) -> int:
            return x * y

        @hook("PreToolUse")
        def audit_pre_tool(self, block: dict) -> None:
            return None

    plugin = MathPlugin()
    tools = plugin.get_tools()
    assert len(tools) == 1
    assert tools[0].name == "multiply"

    hooks = plugin.get_hooks()
    assert "PreToolUse" in hooks
    assert len(hooks["PreToolUse"]) == 1

    agent = Agent(client=MockClient([]))
    agent.register_plugin(plugin)
    assert plugin.setup_called is True
    assert "multiply" in agent._custom_tools


def test_agent_run_with_custom_tool(tmp_path: Path):
    @tool
    def get_weather(city: str) -> str:
        return f"{city} is sunny, 25C"

    # Turn 1: model calls tool 'get_weather'
    # Turn 2: model answers with final text
    client = MockClient(
        [
            make_model_response(
                tool_calls=[make_tool_call("call-1", "get_weather", '{"city": "Tokyo"}')]
            ),
            make_model_response(content="Tokyo is sunny, 25C today!"),
        ]
    )

    agent = Agent(
        client=client,
        workdir=tmp_path,
        tools=[get_weather],
        max_rounds=5,
    )

    result = agent.run("What's the weather in Tokyo?")
    assert result.completed is True
    assert result.tool_calls == 1
    assert "Tokyo is sunny" in result.final_output
    assert len(agent.messages) >= 3

    # Conversation history persists across turns
    assert agent.messages[-1]["role"] == "assistant"
    assert agent.messages[-1]["content"] == "Tokyo is sunny, 25C today!"

    # Reset clears back to initial system prompt
    agent.reset()
    assert len(agent.messages) == 1
    assert agent.messages[0]["role"] == "system"


def test_agent_hook_interception(tmp_path: Path):
    @tool
    def dangerous_action(target: str) -> str:
        return f"destroyed {target}"

    client = MockClient(
        [
            make_model_response(
                tool_calls=[make_tool_call("call-2", "dangerous_action", '{"target": "db"}')]
            ),
            make_model_response(content="Could not perform dangerous action."),
        ]
    )

    agent = Agent(
        client=client,
        workdir=tmp_path,
        tools=[dangerous_action],
        max_rounds=5,
    )

    # Register hook that blocks the dangerous action
    def block_dangerous(block: dict) -> str | None:
        if block["name"] == "dangerous_action":
            return "Operation blocked by security hook"
        return None

    agent.on("PreToolUse", block_dangerous)

    result = agent.run("Destroy db")
    assert result.completed is True
    # Verify that tool result returned the blocked message
    tool_msgs = [m for m in agent.messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert "Operation blocked by security hook" in tool_msgs[0]["content"]


def test_top_level_package_exports():
    assert hasattr(bareloop, "Agent")
    assert hasattr(bareloop, "Plugin")
    assert hasattr(bareloop, "tool")
    assert hasattr(bareloop, "hook")
    assert hasattr(bareloop, "ToolDefinition")
    assert hasattr(bareloop, "AgentSession")
    assert hasattr(bareloop, "RunResult")
    assert hasattr(bareloop, "discover_plugins")
    assert bareloop.__version__ == "0.1.0"
