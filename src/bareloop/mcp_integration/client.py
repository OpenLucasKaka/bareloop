import asyncio
import concurrent.futures
import ipaddress
import json
import os
import re
import sys
from contextlib import AsyncExitStack, suppress
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, Tool

from bareloop.tools.models import ToolDefinition
from bareloop.tools.registry import register_dynamic_tools


@dataclass(frozen=True)
class Endpoint:
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    trust_env: bool = True


@dataclass(frozen=True)
class MCPConfig:
    name: str
    endpoints: tuple[Endpoint, ...]


@dataclass
class MCPConnection:
    endpoint: Endpoint
    session: ClientSession
    tools: list[Tool]
    resources: AsyncExitStack


class MCPClientManager:
    def __init__(self) -> None:
        # 这里只存 Client 连接，不存 Server 实现。
        self.mcp_clients: dict[str, MCPConnection] = {}

        # 模型工具名 -> (Client 名称, Server 原始工具名)
        self._tool_routes: dict[str, tuple[str, str]] = {}

    async def __aenter__(self) -> "MCPClientManager":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def initialize(self, configs: list[MCPConfig]) -> None:
        """项目启动时连接并发现全部 MCP 工具。"""
        for config in configs:
            await self.connect(config)

    async def connect(self, config: MCPConfig) -> None:
        if config.name in self.mcp_clients:
            raise ValueError(f"MCP Client 已存在：{config.name}")

        errors: list[str] = []

        # 按顺序尝试：本地 -> 在线。
        for endpoint in config.endpoints:
            if not await self._reachable(endpoint):
                errors.append(f"{endpoint.url}: 无法访问")
                continue

            resources = AsyncExitStack()

            try:
                http_client = await resources.enter_async_context(
                    httpx2.AsyncClient(
                        headers=endpoint.headers,
                        follow_redirects=True,
                        trust_env=endpoint.trust_env,
                        timeout=httpx2.Timeout(10.0, read=300.0),
                    )
                )

                read_stream, write_stream = await resources.enter_async_context(
                    streamable_http_client(
                        endpoint.url,
                        http_client=http_client,
                    )
                )

                session = await resources.enter_async_context(
                    ClientSession(
                        read_stream,
                        write_stream,
                        read_timeout_seconds=60,
                    )
                )

                # MCP 初始化握手。
                await session.initialize()

                # tools/list：发现 Server 提供的工具。
                tools = list((await session.list_tools()).tools)

                pending_routes: dict[str, tuple[str, str]] = {}

                for tool in tools:
                    exposed_name = self._tool_name(config.name, tool.name)

                    if exposed_name in self._tool_routes or exposed_name in pending_routes:
                        raise ValueError(f"MCP 工具名冲突：{exposed_name}")

                    pending_routes[exposed_name] = (config.name, tool.name)

                self.mcp_clients[config.name] = MCPConnection(
                    endpoint=endpoint,
                    session=session,
                    tools=tools,
                    resources=resources,
                )
                self._tool_routes.update(pending_routes)

                # print(f"[MCP] {config.name} connected: {endpoint.url}")
                # print(f"[MCP] tools: {', '.join(pending_routes)}")
                return

            except Exception as exc:
                errors.append(f"{endpoint.url}: {type(exc).__name__}: {exc}")
                with suppress(Exception):
                    await resources.aclose()

        raise ConnectionError(f"MCP {config.name} 所有地址连接失败：\n" + "\n".join(errors))

    def tool_definitions(self) -> list[dict[str, Any]]:
        """转换成可以传给模型的工具定义。"""
        definitions: list[dict[str, Any]] = []

        for client_name, connection in self.mcp_clients.items():
            for tool in connection.tools:
                definitions.append(
                    {
                        "name": self._tool_name(client_name, tool.name),
                        "description": tool.description or "",
                        "input_schema": tool.input_schema,
                    }
                )

        return definitions

    async def call_tool(
        self,
        exposed_name: str,
        arguments: dict[str, Any],
    ) -> CallToolResult:
        """tools/call：通过对应 Client 调用 Server 工具。"""
        route = self._tool_routes.get(exposed_name)

        if route is None:
            raise KeyError(f"未知 MCP 工具：{exposed_name}")

        client_name, server_tool_name = route
        connection = self.mcp_clients[client_name]

        return await connection.session.call_tool(
            server_tool_name,
            arguments,
        )

    async def call_tool_text(
        self,
        exposed_name: str,
        arguments: dict[str, Any],
    ) -> str:
        """将 MCP 返回值转换成适合放进模型上下文的文本。"""
        result = await self.call_tool(exposed_name, arguments)

        if result.structured_content is not None:
            content = json.dumps(
                result.structured_content,
                ensure_ascii=False,
            )
        else:
            parts: list[str] = []

            for block in result.content:
                text = getattr(block, "text", None)
                if text is not None:
                    parts.append(text)
                else:
                    parts.append(block.model_dump_json(by_alias=True))

            content = "\n".join(parts)

        if result.is_error:
            return f"MCP error: {content}"

        return content

    async def close(self) -> None:
        for connection in reversed(list(self.mcp_clients.values())):
            await connection.resources.aclose()

        self.mcp_clients.clear()
        self._tool_routes.clear()

    async def _reachable(self, endpoint: Endpoint) -> bool:
        """连接前探测，避免本地 Server 未启动时卡住 MCP transport。"""
        try:
            async with httpx2.AsyncClient(
                headers=endpoint.headers,
                trust_env=endpoint.trust_env,
                follow_redirects=True,
                timeout=2,
            ) as client:
                # 不要求 2xx；只要收到 HTTP 响应，就说明地址可连接。
                await client.options(endpoint.url)

            return True
        except (httpx2.HTTPError, httpx2.InvalidURL):
            return False

    @staticmethod
    def _tool_name(client_name: str, tool_name: str) -> str:
        def normalize(value: str) -> str:
            return re.sub(r"[^a-zA-Z0-9_-]", "_", value)

        return f"mcp__{normalize(client_name)}__{normalize(tool_name)}"


async def _call_tool_once(
    config: MCPConfig,
    exposed_name: str,
    arguments: dict[str, Any],
) -> str:
    async with MCPClientManager() as manager:
        await manager.connect(config)
        return await manager.call_tool_text(exposed_name, arguments)


def _run_async_call(factory) -> str:
    """Run one MCP call from synchronous tool dispatch, even under an active loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(factory())).result()


def _make_sync_handler(config: MCPConfig, exposed_name: str):
    def handler(**arguments: Any) -> str:
        return _run_async_call(lambda: _call_tool_once(config, exposed_name, arguments))

    return handler


def _discovered_definitions(
    manager: MCPClientManager,
    configs: list[MCPConfig],
) -> list[ToolDefinition]:
    configs_by_name = {config.name: config for config in configs}
    definitions: list[ToolDefinition] = []
    for client_name, connection in manager.mcp_clients.items():
        config = configs_by_name[client_name]
        for tool in connection.tools:
            exposed_name = manager._tool_name(client_name, tool.name)
            definitions.append(
                ToolDefinition(
                    name=exposed_name,
                    description=tool.description or "",
                    parameters=dict(tool.input_schema),
                    handler=_make_sync_handler(config, exposed_name),
                )
            )
    return definitions


def _validate_authenticated_endpoint(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme == "https":
        return
    hostname = parsed.hostname
    is_loopback = hostname == "localhost"
    if hostname and not is_loopback:
        with suppress(ValueError):
            is_loopback = ipaddress.ip_address(hostname).is_loopback
    if parsed.scheme == "http" and is_loopback:
        return
    raise ValueError("Authenticated remote MCP endpoints require HTTPS")


async def mcp_init() -> list[ToolDefinition]:
    local_url = os.getenv(
        "MCP_LOCAL_URL",
        "http://127.0.0.1:8000/mcp",
    )
    remote_url = os.getenv("MCP_REMOTE_URL")
    remote_token = os.getenv("MCP_REMOTE_TOKEN")

    if remote_url and remote_token:
        try:
            _validate_authenticated_endpoint(remote_url)
        except ValueError as error:
            print(f"[mcp] unavailable: {error}", file=sys.stderr)
            return []

    endpoints = [
        Endpoint(
            url=local_url,
            # 避免系统代理拦截 localhost。
            trust_env=False,
        )
    ]

    if remote_url:
        headers = {}

        if remote_token:
            headers["Authorization"] = f"Bearer {remote_token}"

        endpoints.append(
            Endpoint(
                url=remote_url,
                headers=headers,
                trust_env=True,
            )
        )

    config = MCPConfig(
        name="demo",
        endpoints=tuple(endpoints),
    )

    configs = [config]
    try:
        async with MCPClientManager() as manager:
            await manager.initialize(configs)
            definitions = _discovered_definitions(manager, configs)
            register_dynamic_tools(definitions)
    except Exception as error:
        print(f"[mcp] unavailable: {error}", file=sys.stderr)
        return []

    print(f"[mcp]: 连接成功 已获取{len(definitions)}个工具")
    return definitions
