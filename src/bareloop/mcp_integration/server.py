import os
from datetime import UTC, datetime

from mcp.server.mcpserver import MCPServer


server = MCPServer(
    name="demo-tools",
    version="1.0.0",
    instructions="提供基础演示工具。",
)


@server.tool()
async def add(a: int, b: int) -> int:
    """计算两个整数之和。"""
    return a + b


@server.tool()
async def current_time() -> str:
    """获取服务器当前时间。"""
    return datetime.now(UTC).isoformat()


if __name__ == "__main__":
    server.run(
        transport="streamable-http",
        host=os.getenv("MCP_HOST", "127.0.0.1"),
        port=int(os.getenv("MCP_PORT", "8000")),
        streamable_http_path="/mcp_integration",
    )