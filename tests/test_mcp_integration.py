import asyncio
from concurrent.futures import Future
from importlib import import_module
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.mark.parametrize("setting", ["false", "0", "no", "off", " FALSE ", "No", " Off "])
def test_disabled_mcp_does_not_start_background_discovery(monkeypatch, setting) -> None:
    from bareloop.mcp_integration import client as mcp_client

    monkeypatch.setenv("BARELOOP_ENABLE_MCP", setting)
    monkeypatch.setattr(mcp_client, "_mcp_init_future", None)
    executor = Mock(side_effect=AssertionError("disabled MCP must not create a thread"))
    monkeypatch.setattr(mcp_client.concurrent.futures, "ThreadPoolExecutor", executor)

    mcp_client.start_background_mcp_init()

    executor.assert_not_called()
    assert mcp_client._mcp_init_future is None
    assert mcp_client.wait_for_mcp_init() == []


@pytest.mark.parametrize("setting", ["false", "0", "no", "off", " FALSE ", "No", " Off "])
def test_disabled_mcp_skips_direct_discovery(monkeypatch, setting, capsys) -> None:
    from bareloop.mcp_integration import client as mcp_client

    monkeypatch.setenv("BARELOOP_ENABLE_MCP", setting)
    monkeypatch.setenv("MCP_LOCAL_URL", "http://127.0.0.1:8000/mcp")
    monkeypatch.setenv("MCP_REMOTE_URL", "https://example.invalid/mcp")
    manager = Mock(side_effect=AssertionError("disabled MCP must not discover endpoints"))
    register = Mock()
    monkeypatch.setattr(mcp_client, "MCPClientManager", manager)
    monkeypatch.setattr(mcp_client, "register_dynamic_tools", register)

    assert asyncio.run(mcp_client.mcp_init()) == []

    manager.assert_not_called()
    register.assert_not_called()
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("setting", [None, "true", "1", "yes", " TRUE ", "unrecognized", ""])
def test_enabled_mcp_schedules_discovery_once(monkeypatch, setting) -> None:
    from bareloop.mcp_integration import client as mcp_client

    monkeypatch.delenv("BARELOOP_ENABLE_MCP", raising=False)
    if setting is not None:
        monkeypatch.setenv("BARELOOP_ENABLE_MCP", setting)
    monkeypatch.setattr(mcp_client, "_mcp_init_future", None)
    future = Future()
    future.set_result([])
    executor = Mock()
    executor.submit.return_value = future
    monkeypatch.setattr(
        mcp_client.concurrent.futures, "ThreadPoolExecutor", Mock(return_value=executor)
    )

    mcp_client.start_background_mcp_init()
    mcp_client.start_background_mcp_init()

    executor.submit.assert_called_once()
    assert mcp_client.wait_for_mcp_init() == []


@pytest.mark.parametrize("setting", [None, "true", "1", "yes", " TRUE ", "unrecognized", ""])
def test_enabled_mcp_still_initializes_endpoints(monkeypatch, setting) -> None:
    from bareloop.mcp_integration import client as mcp_client

    monkeypatch.delenv("BARELOOP_ENABLE_MCP", raising=False)
    if setting is not None:
        monkeypatch.setenv("BARELOOP_ENABLE_MCP", setting)
    monkeypatch.delenv("MCP_REMOTE_URL", raising=False)
    initialize = AsyncMock()
    register = Mock()
    monkeypatch.setattr(mcp_client.MCPClientManager, "initialize", initialize)
    monkeypatch.setattr(mcp_client, "register_dynamic_tools", register)

    assert asyncio.run(mcp_client.mcp_init()) == []

    initialize.assert_awaited_once()
    register.assert_called_once_with([])


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

    monkeypatch.setenv("BARELOOP_ENABLE_MCP", "true")

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

    monkeypatch.setenv("BARELOOP_ENABLE_MCP", "true")
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
