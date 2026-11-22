"""Stdio transport against the live compose stack (opt-in service test)."""

from __future__ import annotations

import json
import sys
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent

pytestmark = pytest.mark.integration

ADDRESS = {
    "street": "Fictional Street",
    "number": "12",
    "postal_code": "28001",
    "city": "Madrid",
    "country": "ES",
}


async def test_stdio_server_lists_tools_and_checks_live_eligibility() -> None:
    parameters = StdioServerParameters(
        command=sys.executable, args=["-m", "aintegrator_mcp_server"]
    )
    async with (
        stdio_client(parameters) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        listing = await session.list_tools()
        assert "check_eligibility" in [tool.name for tool in listing.tools]

        result = await session.call_tool("check_eligibility", ADDRESS)
        assert not result.isError
        text = next(block.text for block in result.content if isinstance(block, TextContent))
        payload: dict[str, Any] = json.loads(text)
        assert payload["ok"] is True
        assert any(
            item["provider"] == "fibernet" and item["status"] == "OK"
            for item in payload["providers"]
        )
