"""MCP client implementation of ``ToolExecutor``.

The agent never imports the MCP *server* package. It discovers tools through
the MCP protocol and calls them over a stdio subprocess (or any injected
``MCPSession`` in tests).
"""

from __future__ import annotations

import json
import sys
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any, Protocol, cast

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, ListToolsResult, TextContent

from aintegrator_agent.llm import ToolCall, ToolDefinition

__all__ = ["MCPSession", "MCPToolExecutor"]


class MCPSession(Protocol):
    """The subset of ``ClientSession`` the executor needs."""

    async def list_tools(self, cursor: str | None = None) -> ListToolsResult: ...

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> CallToolResult: ...


def _text(result: CallToolResult) -> str | None:
    for block in result.content:
        if isinstance(block, TextContent):
            return block.text
    return None


def _failure(message: str, *, code: str = "MCP_TOOL_ERROR") -> str:
    return json.dumps(
        {"ok": False, "error": {"code": code, "message": message, "retryable": False}}
    )


class MCPToolExecutor:
    """Discovers MCP tools once, then maps loop calls to MCP tool calls."""

    def __init__(self, session: MCPSession, definitions: tuple[ToolDefinition, ...]) -> None:
        self._session = session
        self._definitions = definitions

    @classmethod
    async def from_session(cls, session: MCPSession) -> MCPToolExecutor:
        """Discover tools from a live, already-initialized session."""
        listing = await session.list_tools()
        definitions = tuple(
            ToolDefinition(
                name=tool.name,
                description=tool.description or "",
                input_schema=dict(tool.inputSchema),
            )
            for tool in listing.tools
        )
        return cls(session, definitions)

    @classmethod
    @asynccontextmanager
    async def connect_stdio(
        cls,
        *,
        command: str | None = None,
        args: Sequence[str] | None = None,
        cwd: str | None = None,
    ) -> AsyncIterator[MCPToolExecutor]:
        """Spawn the MCP server and yield a connected executor."""
        parameters = StdioServerParameters(
            command=command or sys.executable,
            args=list(args) if args is not None else ["-m", "aintegrator_mcp_server"],
            cwd=cwd,
        )
        async with (
            stdio_client(parameters) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            yield await cls.from_session(cast("MCPSession", session))

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return self._definitions

    async def execute(self, call: ToolCall) -> str:
        result = await self._session.call_tool(call.name, call.arguments)
        text = _text(result)
        if text is not None:
            try:
                json.loads(text)
            except ValueError:
                # Framework-level errors (unknown tool, schema validation before
                # our handler runs) arrive as plain text. Wrap them for the model.
                return _failure(f"MCP tool {call.name}: {text}")
            return text
        if result.isError:
            return _failure(f"MCP tool {call.name} failed without a text result")
        return _failure(f"MCP tool {call.name} returned an empty result", code="MCP_EMPTY_RESULT")
