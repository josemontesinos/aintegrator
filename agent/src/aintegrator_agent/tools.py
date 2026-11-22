"""Local fallback implementation of ``ToolExecutor``.

The MCP client is the production executor. This in-process executor remains as a
lightweight option for tests, but it delegates to the same security-aware
``ToolOperations`` so approvals, profiles, redaction and audit cannot be bypassed.
"""

from __future__ import annotations

from typing import Any, Protocol

from aintegrator_integration.security import ToolSecurity
from aintegrator_integration.service import ConnectivityService
from aintegrator_integration.tooling import (
    TOOL_DESCRIPTIONS,
    CreateOrderInput,
    EligibilityInput,
    ListOrdersInput,
    OrderIdInput,
    ToolOperations,
)

from aintegrator_agent.llm import ToolCall, ToolDefinition

__all__ = [
    "CreateOrderInput",
    "EligibilityInput",
    "InProcessTools",
    "ListOrdersInput",
    "OrderIdInput",
    "ToolExecutor",
]


class _ApprovedCreateOrderInput(CreateOrderInput):
    approval_id: str | None = None


class _ApprovedOrderIdInput(OrderIdInput):
    approval_id: str | None = None


_SCHEMAS = {
    "check_eligibility": EligibilityInput.model_json_schema(),
    "create_order": _ApprovedCreateOrderInput.model_json_schema(),
    "get_order": OrderIdInput.model_json_schema(),
    "list_orders": ListOrdersInput.model_json_schema(),
    "cancel_order": _ApprovedOrderIdInput.model_json_schema(),
}


class ToolExecutor(Protocol):
    """Contract used by the agent loop; implemented via MCP or in-process."""

    def definitions(self) -> tuple[ToolDefinition, ...]: ...

    async def execute(self, call: ToolCall) -> str: ...


class InProcessTools:
    """Typed tools calling the application service without a network hop."""

    def __init__(
        self,
        service: ConnectivityService,
        *,
        run_id: str | None = None,
        security: ToolSecurity | None = None,
    ) -> None:
        self._operations = ToolOperations(service, run_id=run_id, security=security)

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(
            ToolDefinition(name=name, description=description, input_schema=_SCHEMAS[name])
            for name, description in TOOL_DESCRIPTIONS.items()
            if self._operations.security.allows(name)
        )

    async def execute(self, call: ToolCall) -> str:
        arguments: dict[str, Any] = call.arguments or {}
        return await self._operations.execute(call.name, arguments)
