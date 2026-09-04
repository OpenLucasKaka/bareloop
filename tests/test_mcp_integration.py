import asyncio
from importlib import import_module

import pytest


def test_mcp_server_uses_standard_path(monkeypatch: pytest.MonkeyPatch) -> None:
    server_module = import_module("bareloop.mcp_integration.server")

    calls = []
    monkeypatch.setattr(server_module.server, "run", lambda **kwargs: calls.append(kwargs))

    server_module.run_server()

    assert calls[0]["streamable_http_path"] == "/mcp"


def test_mcp_startup_degrades_when_endpoint_is_unreachable(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    from bareloop.mcp_integration import client as mcp_client

    async def fail_initialize(self, configs):
        raise ConnectionError("offline")

    monkeypatch.setattr(mcp_client.MCPClientManager, "initialize", fail_initialize)

    assert asyncio.run(mcp_client.mcp_init()) == []
    assert "offline" in capsys.readouterr().err


def test_invalid_mcp_url_is_treated_as_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    from bareloop.mcp_integration import client as mcp_client

    def reject_url(**_kwargs):
        raise mcp_client.httpx2.InvalidURL("invalid endpoint")

    monkeypatch.setattr(mcp_client.httpx2, "AsyncClient", reject_url)

    manager = mcp_client.MCPClientManager()

    assert asyncio.run(manager._reachable(mcp_client.Endpoint("://invalid"))) is False


def test_remote_mcp_token_requires_encrypted_transport(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    from bareloop.mcp_integration import client as mcp_client

    monkeypatch.setenv("MCP_REMOTE_URL", "http://example.com/mcp")
    monkeypatch.setenv("MCP_REMOTE_TOKEN", "do-not-log-this-token")

    async def unexpected_initialize(self, configs):
        raise AssertionError("insecure authenticated endpoint must not be connected")

    monkeypatch.setattr(mcp_client.MCPClientManager, "initialize", unexpected_initialize)

    assert asyncio.run(mcp_client.mcp_init()) == []
    error = capsys.readouterr().err
    assert "HTTPS" in error
    assert "do-not-log-this-token" not in error


def test_dynamic_mcp_tool_is_visible_and_synchronously_dispatchable() -> None:
    from bareloop.tools.dispatcher import dispatch_tool
    from bareloop.tools.models import ToolDefinition
    from bareloop.tools.registry import (
        clear_dynamic_tools,
        get_tool_schemas,
        register_dynamic_tools,
    )

    clear_dynamic_tools()
    try:
        register_dynamic_tools(
            [
                ToolDefinition(
                    name="mcp__demo__add",
                    description="add",
                    parameters={
                        "type": "object",
                        "properties": {
                            "a": {"type": "integer"},
                            "b": {"type": "integer"},
                        },
                        "required": ["a", "b"],
                        "additionalProperties": False,
                    },
                    handler=lambda a, b: str(a + b),
                )
            ]
        )

        schema_names = [item["function"]["name"] for item in get_tool_schemas()]
        assert "mcp__demo__add" in schema_names
        assert dispatch_tool("mcp__demo__add", {"a": 2, "b": 3}) == "5"
    finally:
        clear_dynamic_tools()


def test_loop_reads_tool_schema_after_dynamic_registration(monkeypatch) -> None:
    from bareloop import loop

    assert not hasattr(loop, "MAIN_TOOL_SCHEMAS")


def test_dynamic_tool_registration_is_atomic_on_collision() -> None:
    from bareloop.tools.models import ToolDefinition
    from bareloop.tools.registry import clear_dynamic_tools, get_tool, register_dynamic_tools

    first = ToolDefinition("mcp__demo__same", "first", {}, lambda: "first")
    second = ToolDefinition("mcp__demo__same", "second", {}, lambda: "second")
    clear_dynamic_tools()
    try:
        with pytest.raises(ValueError, match="Duplicate dynamic tool name"):
            register_dynamic_tools([first, second])
        assert get_tool(first.name) is None
    finally:
        clear_dynamic_tools()
