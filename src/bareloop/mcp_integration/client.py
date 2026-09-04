import asyncio
import json
import os
import re
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, Tool


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

                    if exposed_name in self._tool_routes:
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
                errors.append(
                    f"{endpoint.url}: {type(exc).__name__}: {exc}"
                )
                try:
                    await resources.aclose()
                except Exception:
                    pass

        raise ConnectionError(
            f"MCP {config.name} 所有地址连接失败：\n" + "\n".join(errors)
        )

    def tool_definitions(self) -> list[dict[str, Any]]:
        """转换成可以传给模型的工具定义。"""
        definitions: list[dict[str, Any]] = []

        for client_name, connection in self.mcp_clients.items():
            for tool in connection.tools:
                definitions.append({
                    "name": self._tool_name(client_name, tool.name),
                    "description": tool.description or "",
                    "input_schema": tool.input_schema,
                })

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
                    parts.append(
                        block.model_dump_json(by_alias=True)
                    )

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
        except httpx2.HTTPError:
            return False

    @staticmethod
    def _tool_name(client_name: str, tool_name: str) -> str:
        normalize = lambda value: re.sub(
            r"[^a-zA-Z0-9_-]",
            "_",
            value,
        )
        return f"mcp__{normalize(client_name)}__{normalize(tool_name)}"


async def mcp_init() -> None:
    local_url = os.getenv(
        "MCP_LOCAL_URL",
        "http://127.0.0.1:8000/mcp",
    )
    remote_url = os.getenv("MCP_REMOTE_URL")
    remote_token = os.getenv("MCP_REMOTE_TOKEN")

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

    async with MCPClientManager() as manager:
        # 项目启动阶段初始化
        await manager.initialize([config])

        # 这些定义交给大模型
        print(f"[mcp]: 连接成功 已获取{len(manager.tool_definitions())}个工具")
        return manager.tool_definitions()