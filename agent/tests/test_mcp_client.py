"""MCP client tests over a real in-memory MCP session (no subprocess)."""

from __future__ import annotations

import json

from aintegrator_agent.llm import ToolCall
from aintegrator_agent.mcp_client import MCPToolExecutor
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session


def _test_server() -> FastMCP:
    mcp = FastMCP("test-tools")

    @mcp.tool(name="ping", description="Echo a value")
    async def ping(value: str) -> str:
        return json.dumps({"ok": True, "echo": value})

    @mcp.tool(name="reject", description="Return a structured error")
    async def reject() -> str:
        return json.dumps({"ok": False, "error": {"code": "X", "message": "no"}})

    return mcp


async def test_executor_discovers_tools_from_the_protocol() -> None:
    async with create_connected_server_and_client_session(_test_server()) as session:
        executor = await MCPToolExecutor.from_session(session)

        assert [definition.name for definition in executor.definitions()] == ["ping", "reject"]
        assert executor.definitions()[0].description == "Echo a value"
        assert executor.definitions()[0].input_schema["properties"]["value"]["type"] == "string"

        result = json.loads(
            await executor.execute(ToolCall(id="1", name="ping", arguments={"value": "hi"}))
        )
        assert result == {"ok": True, "echo": "hi"}

        failure = json.loads(await executor.execute(ToolCall(id="2", name="reject", arguments={})))
        assert failure["ok"] is False
        assert failure["error"]["code"] == "X"


async def test_unknown_tool_is_reported_as_structured_error() -> None:
    async with create_connected_server_and_client_session(_test_server()) as session:
        executor = await MCPToolExecutor.from_session(session)
        result = json.loads(await executor.execute(ToolCall(id="3", name="missing", arguments={})))
        assert result["ok"] is False
        assert result["error"]["code"] == "MCP_TOOL_ERROR"
