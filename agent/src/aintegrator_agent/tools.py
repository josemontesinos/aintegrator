"""LLM-facing tools backed directly by ``ConnectivityService``.

Phase 3 replaces this executor with an MCP client that implements the same
protocol; the loop and prompts do not change.
"""

from __future__ import annotations

import json
from typing import Any, Protocol
from uuid import UUID, uuid4

from aintegrator_integration.domain import (
    Address,
    AIntegratorError,
    Customer,
    EligibilityResult,
    Offer,
    Order,
    OrderIntent,
    OrderStatus,
)
from aintegrator_integration.service import ConnectivityService
from pydantic import BaseModel, Field, ValidationError

from aintegrator_agent.llm import ToolCall, ToolDefinition

__all__ = ["InProcessTools", "ToolExecutor"]


class AddressInput(BaseModel):
    street: str = Field(min_length=1, max_length=200)
    number: str = Field(min_length=1, max_length=50)
    postal_code: str = Field(min_length=1, max_length=20)
    city: str = Field(min_length=1, max_length=100)
    country: str = Field(default="ES", min_length=2, max_length=2)

    def to_domain(self) -> Address:
        return Address(
            street=self.street,
            number=self.number,
            postal_code=self.postal_code,
            city=self.city,
            country=self.country,
        )


class EligibilityInput(AddressInput):
    pass


class CreateOrderInput(AddressInput):
    provider: str = Field(min_length=1, max_length=40)
    offer_id: str = Field(min_length=1, max_length=120)
    customer_name: str = Field(min_length=1, max_length=200)
    customer_email: str = Field(min_length=3, max_length=200)
    idempotency_key: str | None = Field(default=None, max_length=200)
    client_reference: str | None = Field(default=None, max_length=120)


class OrderIdInput(BaseModel):
    order_id: str = Field(min_length=1, max_length=80)


class ListOrdersInput(BaseModel):
    status: str | None = Field(default=None, max_length=40)
    limit: int = Field(default=20, ge=1, le=50)


class ToolExecutor(Protocol):
    """Contract used by the agent loop; implemented in-process now, via MCP later."""

    def definitions(self) -> tuple[ToolDefinition, ...]: ...

    async def execute(self, call: ToolCall) -> str: ...


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _error(message: str, *, code: str = "INVALID_ARGUMENTS", retryable: bool = False) -> str:
    return _json({"ok": False, "error": {"code": code, "message": message, "retryable": retryable}})


def _offer_json(offer: Offer) -> dict[str, Any]:
    return {
        "offer_id": offer.offer_id,
        "provider": offer.provider,
        "plan_name": offer.plan_name,
        "technology": offer.technology.value,
        "down_mbps": offer.down_mbps,
        "up_mbps": offer.up_mbps,
        "monthly_price_eur": float(offer.monthly_price.as_eur),
        "install_fee_eur": float(offer.install_fee.as_eur),
        "lead_time_days": offer.lead_time_days,
        "untrusted_text": offer.untrusted_text,
    }


def _eligibility_json(result: EligibilityResult) -> dict[str, Any]:
    return {
        "provider": result.provider,
        "status": result.status.value,
        "from_cache": result.from_cache,
        "error": result.error,
        "request_ref": result.request_ref,
        "offers": [_offer_json(offer) for offer in result.offers],
    }


def _order_json(order: Order) -> dict[str, Any]:
    return {
        "order_id": str(order.order_id),
        "provider": order.provider,
        "provider_order_id": order.provider_order_id,
        "client_reference": order.client_reference,
        "status": order.status.value,
        "plan_name": order.offer.plan_name,
        "monthly_price_eur": float(order.offer.monthly_price.as_eur),
        "address": order.address.one_line,
        "cancellable": order.cancellable,
        "supports_cancel": order.supports_cancel,
    }


class InProcessTools:
    """Typed tools calling the application service without a network hop."""

    def __init__(self, service: ConnectivityService, *, run_id: str | None = None) -> None:
        self._service = service
        self._run_id = run_id

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return (
            ToolDefinition(
                name="check_eligibility",
                description=(
                    "Check all providers for offers at an address. Returns one result per "
                    "provider, including offer IDs needed to place an order."
                ),
                input_schema=EligibilityInput.model_json_schema(),
            ),
            ToolDefinition(
                name="create_order",
                description=(
                    "Place an order for a specific offer ID returned by check_eligibility. "
                    "Only call this after the user explicitly confirms the order."
                ),
                input_schema=CreateOrderInput.model_json_schema(),
            ),
            ToolDefinition(
                name="get_order",
                description="Get the current status and details of one order by its ID.",
                input_schema=OrderIdInput.model_json_schema(),
            ),
            ToolDefinition(
                name="list_orders",
                description="List recent orders, optionally filtered by status.",
                input_schema=ListOrdersInput.model_json_schema(),
            ),
            ToolDefinition(
                name="cancel_order",
                description=(
                    "Cancel a cancellable order. Only call this after the user explicitly "
                    "confirms the cancellation."
                ),
                input_schema=OrderIdInput.model_json_schema(),
            ),
        )

    async def execute(self, call: ToolCall) -> str:
        """Execute a tool and always return a JSON string, never a traceback."""
        arguments: dict[str, Any] = call.arguments or {}
        try:
            result = await self._dispatch(call.name, arguments)
        except ValidationError as exc:
            result = _error(f"invalid arguments: {exc.errors()}")
        except AIntegratorError as exc:
            result = _error(exc.message, code=exc.code, retryable=exc.retryable)
        except (ValueError, TypeError) as exc:
            result = _error(str(exc))
        except Exception as exc:  # pragma: no cover - defensive LLM boundary
            result = _error(f"unexpected tool failure: {type(exc).__name__}")
        return result

    async def _dispatch(self, name: str, arguments: dict[str, Any]) -> str:
        if name == "check_eligibility":
            return await self._check_eligibility(EligibilityInput.model_validate(arguments))
        if name == "create_order":
            return await self._create_order(CreateOrderInput.model_validate(arguments))
        if name == "get_order":
            return await self._get_order(OrderIdInput.model_validate(arguments))
        if name == "list_orders":
            return await self._list_orders(ListOrdersInput.model_validate(arguments))
        if name == "cancel_order":
            return await self._cancel_order(OrderIdInput.model_validate(arguments))
        return _error(f"unknown tool: {name}", code="UNKNOWN_TOOL")

    async def _check_eligibility(self, args: EligibilityInput) -> str:
        results = await self._service.check_eligibility(args.to_domain())
        return _json({"ok": True, "providers": [_eligibility_json(item) for item in results]})

    async def _create_order(self, args: CreateOrderInput) -> str:
        address = args.to_domain()
        offer = await self._find_offer(args.provider, args.offer_id, address)
        if offer is None:
            return _error(
                f"offer {args.offer_id} is not available from {args.provider} at this address",
                code="OFFER_NOT_FOUND",
            )
        intent = OrderIntent(
            offer=offer,
            address=address,
            customer=Customer(name=args.customer_name, email=args.customer_email),
            idempotency_key=args.idempotency_key or f"agent:{uuid4()}",
            client_reference=args.client_reference or f"agent-{uuid4()}",
            run_id=self._run_id,
        )
        order = await self._service.create_order(intent)
        return _json({"ok": True, "order": _order_json(order)})

    async def _find_offer(self, provider: str, offer_id: str, address: Address) -> Offer | None:
        """Re-fetch eligibility instead of trusting model-supplied offer details."""
        results = await self._service.check_eligibility(address)
        for result in results:
            if result.provider != provider:
                continue
            for offer in result.offers:
                if offer.offer_id == offer_id:
                    return offer
        return None

    async def _get_order(self, args: OrderIdInput) -> str:
        order_id = self._parse_order_id(args.order_id)
        if order_id is None:
            return _error(f"invalid order ID: {args.order_id}")
        order = await self._service.get_order(order_id)
        return _json({"ok": True, "order": _order_json(order)})

    async def _list_orders(self, args: ListOrdersInput) -> str:
        status: OrderStatus | None = None
        if args.status:
            try:
                status = OrderStatus(args.status.upper())
            except ValueError:
                return _error(f"unknown order status: {args.status}")
        orders = await self._service.list_orders(status, args.limit)
        return _json({"ok": True, "orders": [_order_json(order) for order in orders]})

    async def _cancel_order(self, args: OrderIdInput) -> str:
        order_id = self._parse_order_id(args.order_id)
        if order_id is None:
            return _error(f"invalid order ID: {args.order_id}")
        order = await self._service.cancel_order(order_id)
        return _json({"ok": True, "order": _order_json(order)})

    @staticmethod
    def _parse_order_id(value: str) -> UUID | None:
        try:
            return UUID(value)
        except ValueError:
            return None
