"""MCP protocol tests for the server's five tools (in-memory transport)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from aintegrator_integration.config import Settings
from aintegrator_integration.security import ToolSecurity, build_tool_security
from aintegrator_integration.service import ConnectivityService
from aintegrator_mcp_server import create_server
from aintegrator_mcp_server import server as server_module
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import CallToolResult, TextContent

ADDRESS = {
    "street": "Fictional Street",
    "number": "12",
    "postal_code": "28001",
    "city": "Madrid",
    "country": "ES",
}
CUSTOMER = {"customer_name": "Ada Example", "customer_email": "ada@example.invalid"}


def _text(result: CallToolResult) -> str:
    for block in result.content:
        if isinstance(block, TextContent):
            return block.text
    raise AssertionError("tool returned no text content")


def _payload(result: CallToolResult) -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(_text(result))
    return parsed


async def _approved(
    session: Any,
    security: ToolSecurity,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Call a write tool over MCP, grant the approval, then retry with bound args."""
    first = _payload(await session.call_tool(name, arguments))
    assert first["ok"] is False
    assert first["error"]["code"] == "APPROVAL_REQUIRED"

    details = first["error"]["details"]
    approval_id = str(details["approval_id"])
    await security.approvals.grant(approval_id)

    retry = {**details["call_arguments"], "approval_id": approval_id}
    result = _payload(await session.call_tool(name, retry))
    assert result["ok"] is True, result
    return result


async def test_list_tools_exposes_the_expected_schema(mcp_service: ConnectivityService) -> None:
    async with create_connected_server_and_client_session(create_server(mcp_service)) as session:
        listing = await session.list_tools()
        names = [tool.name for tool in listing.tools]
        assert names == [
            "check_eligibility",
            "create_order",
            "get_order",
            "list_orders",
            "cancel_order",
        ]
        eligibility = next(tool for tool in listing.tools if tool.name == "check_eligibility")
        assert eligibility.inputSchema["properties"]["street"]["type"] == "string"
        assert "offer IDs" in (eligibility.description or "")


async def test_full_order_lifecycle_over_mcp(mcp_service: ConnectivityService) -> None:
    security = build_tool_security(mcp_service.session_maker, mcp_service.settings)
    async with create_connected_server_and_client_session(
        create_server(mcp_service, security=security)
    ) as session:
        eligibility = _payload(await session.call_tool("check_eligibility", ADDRESS))
        assert eligibility["ok"] is True
        offer_id = eligibility["providers"][0]["offers"][0]["offer_id"]

        created = await _approved(
            session,
            security,
            "create_order",
            {
                **ADDRESS,
                **CUSTOMER,
                "provider": "fibernet",
                "offer_id": offer_id,
                "idempotency_key": "mcp-test-1",
            },
        )
        order = created["order"]
        assert order["status"] == "SUBMITTED"
        assert order["provider_order_id"] == "A-1"

        fetched = _payload(await session.call_tool("get_order", {"order_id": order["order_id"]}))
        assert fetched["order"]["order_id"] == order["order_id"]

        listed = _payload(await session.call_tool("list_orders", {"status": "submitted"}))
        assert [item["order_id"] for item in listed["orders"]] == [order["order_id"]]

        cancelled = await _approved(
            session, security, "cancel_order", {"order_id": order["order_id"]}
        )
        assert cancelled["order"]["status"] == "CANCELLED"


async def test_read_only_profile_hides_and_blocks_write_tools(
    mcp_service: ConnectivityService,
) -> None:
    security = build_tool_security(
        mcp_service.session_maker, Settings(mcp_tool_profile="read-only")
    )
    async with create_connected_server_and_client_session(
        create_server(mcp_service, security=security)
    ) as session:
        listing = await session.list_tools()
        assert [tool.name for tool in listing.tools] == [
            "check_eligibility",
            "get_order",
            "list_orders",
        ]
        blocked = await session.call_tool("create_order", {**ADDRESS, **CUSTOMER})
        assert blocked.isError is True
        assert "create_order" in _text(blocked)


async def test_malformed_arguments_become_structured_errors(
    mcp_service: ConnectivityService,
) -> None:
    async with create_connected_server_and_client_session(create_server(mcp_service)) as session:
        result = await session.call_tool("check_eligibility", {**ADDRESS, "street": ""})
        payload = _payload(result)
        assert payload["ok"] is False
        assert payload["error"]["code"] == "INVALID_ARGUMENTS"


async def test_amain_owns_the_service_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    class StubService:
        async def aclose(self) -> None:
            events.append("close")

    class StubServer:
        async def run_stdio_async(self) -> None:
            events.append("run")

    monkeypatch.setattr(server_module, "build_mcp_service", StubService)
    monkeypatch.setattr(server_module, "create_server", lambda service: StubServer())

    await server_module.amain()

    assert events == ["run", "close"]
