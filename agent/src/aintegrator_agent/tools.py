"""Local fallback implementation of ``ToolExecutor``.

Phase 3 uses an MCP client in production; this in-process executor remains as a
lightweight option for tests and for environments where spawning a server is
undesirable. Tool payload logic lives in ``integration.tooling`` and is shared
with the MCP server.
"""

from __future__ import annotations

from typing import Any, Protocol

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

_SCHEMAS = {
    "check_eligibility": EligibilityInput.model_json_schema(),
    "create_order": CreateOrderInput.model_json_schema(),
    "get_order": OrderIdInput.model_json_schema(),
    "list_orders": ListOrdersInput.model_json_schema(),
    "cancel_order": OrderIdInput.model_json_schema(),
}


class ToolExecutor(Protocol):
    """Contract used by the agent loop; implemented via MCP or in-process."""

    def definitions(self) -> tuple[ToolDefinition, ...]: ...

    async def execute(self, call: ToolCall) -> str: ...


class InProcessTools:
    """Typed tools calling the application service without a network hop."""

    def __init__(self, service: ConnectivityService, *, run_id: str | None = None) -> None:
        self._operations = ToolOperations(service, run_id=run_id)

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(
            ToolDefinition(name=name, description=description, input_schema=_SCHEMAS[name])
            for name, description in TOOL_DESCRIPTIONS.items()
        )

    async def execute(self, call: ToolCall) -> str:
        arguments: dict[str, Any] = call.arguments or {}
        return await self._operations.execute(call.name, arguments)
