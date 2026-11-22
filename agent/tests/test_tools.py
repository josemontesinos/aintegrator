"""In-process tools against a real service with a fake gateway."""

from __future__ import annotations

import json
from typing import Any

import pytest
from aintegrator_agent.llm import ToolCall
from aintegrator_agent.tools import InProcessTools
from aintegrator_integration.config import Settings
from aintegrator_integration.security import ToolSecurity, build_tool_security
from aintegrator_integration.service import ConnectivityService

ADDRESS = {
    "street": "Fictional Street",
    "number": "12",
    "postal_code": "28001",
    "city": "Madrid",
    "country": "ES",
}
CUSTOMER = {"customer_name": "Ada Example", "customer_email": "ada@example.invalid"}
FakeGateway = Any  # fixture type only


def _call(name: str, arguments: dict[str, Any]) -> ToolCall:
    return ToolCall(id="call-1", name=name, arguments=arguments)


def _payload(result: str) -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(result)
    return parsed


async def _approved(
    tools: InProcessTools,
    security: ToolSecurity,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Call a write tool, grant the pending approval, then retry with bound args."""
    first = _payload(await tools.execute(_call(name, arguments)))
    assert first["ok"] is False
    assert first["error"]["code"] == "APPROVAL_REQUIRED"

    details = first["error"]["details"]
    approval_id = str(details["approval_id"])
    await security.approvals.grant(approval_id)

    retry = {**details["call_arguments"], "approval_id": approval_id}
    result = _payload(await tools.execute(_call(name, retry)))
    assert result["ok"] is True, result
    return result


def test_definitions_cover_the_read_and_write_surface(
    agent_service: tuple[ConnectivityService, FakeGateway],
) -> None:
    tools = InProcessTools(agent_service[0])
    names = [definition.name for definition in tools.definitions()]
    assert names == [
        "check_eligibility",
        "create_order",
        "get_order",
        "list_orders",
        "cancel_order",
    ]
    assert all(
        definition.input_schema.get("type") == "object" for definition in tools.definitions()
    )


async def test_eligibility_returns_offer_ids(
    agent_service: tuple[ConnectivityService, FakeGateway],
) -> None:
    tools = InProcessTools(agent_service[0])
    result = _payload(await tools.execute(_call("check_eligibility", ADDRESS)))
    assert result["ok"] is True
    offers = result["providers"][0]["offers"]
    assert offers[0]["offer_id"] == "fibernet:A-1"
    assert offers[0]["monthly_price_eur"] == 49.0


async def test_order_lifecycle(
    agent_service: tuple[ConnectivityService, FakeGateway],
) -> None:
    service = agent_service[0]
    security = build_tool_security(service.session_maker, service.settings)
    tools = InProcessTools(service, security=security)
    eligibility = _payload(await tools.execute(_call("check_eligibility", ADDRESS)))
    offer_id = eligibility["providers"][0]["offers"][0]["offer_id"]

    created = await _approved(
        tools,
        security,
        "create_order",
        {
            **ADDRESS,
            **CUSTOMER,
            "provider": "fibernet",
            "offer_id": offer_id,
            "idempotency_key": "tool-test-1",
        },
    )
    order = created["order"]
    assert order["status"] == "SUBMITTED"
    assert order["provider_order_id"] == "A-1"

    fetched = _payload(await tools.execute(_call("get_order", {"order_id": order["order_id"]})))
    assert fetched["order"]["order_id"] == order["order_id"]

    listed = _payload(await tools.execute(_call("list_orders", {"status": "submitted"})))
    assert [item["order_id"] for item in listed["orders"]] == [order["order_id"]]

    cancelled = await _approved(tools, security, "cancel_order", {"order_id": order["order_id"]})
    assert cancelled["order"]["status"] == "CANCELLED"


async def test_read_only_profile_blocks_writes(
    agent_service: tuple[ConnectivityService, FakeGateway],
) -> None:
    service = agent_service[0]
    security = build_tool_security(service.session_maker, Settings(mcp_tool_profile="read-only"))
    tools = InProcessTools(service, security=security)
    assert [definition.name for definition in tools.definitions()] == [
        "check_eligibility",
        "get_order",
        "list_orders",
    ]
    result = _payload(
        await tools.execute(
            _call("create_order", {**ADDRESS, **CUSTOMER, "provider": "fibernet", "offer_id": "x"})
        )
    )
    assert result["error"]["code"] == "TOOL_NOT_ALLOWED"


async def test_tool_errors_are_structured_and_never_raise(
    agent_service: tuple[ConnectivityService, FakeGateway],
) -> None:
    tools = InProcessTools(agent_service[0])
    cases = [
        _call("unknown_tool", {}),
        _call("check_eligibility", {"street": ""}),
        _call("get_order", {"order_id": "not-a-uuid"}),
        _call("list_orders", {"status": "NOPE"}),
        _call(
            "create_order",
            {**ADDRESS, **CUSTOMER, "provider": "fibernet", "offer_id": "missing"},
        ),
        _call("get_order", {"order_id": "00000000-0000-0000-0000-000000000000"}),
    ]
    for call in cases:
        result = _payload(await tools.execute(call))
        assert result["ok"] is False
        assert result["error"]["code"]
        assert result["error"]["message"]


async def test_missing_gateway_is_a_domain_error(
    agent_service: tuple[ConnectivityService, FakeGateway],
) -> None:
    tools = InProcessTools(agent_service[0])
    result = _payload(
        await tools.execute(
            _call(
                "create_order",
                {
                    **ADDRESS,
                    **CUSTOMER,
                    "provider": "unknown-provider",
                    "offer_id": "x",
                },
            )
        )
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "OFFER_NOT_FOUND"


@pytest.mark.parametrize("status", ["SUBMITTED", "submitted"])
async def test_list_orders_status_is_case_insensitive(
    agent_service: tuple[ConnectivityService, FakeGateway], status: str
) -> None:
    tools = InProcessTools(agent_service[0])
    result = _payload(await tools.execute(_call("list_orders", {"status": status})))
    assert result["ok"] is True
