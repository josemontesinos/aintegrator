"""LLM-facing tool operations shared by the MCP server and the local fallback.

The integration layer owns domain errors, validation, JSON payloads and the
phase 4 security policy. Neither the MCP server nor the agent can bypass the
approval gate, profile allow-list, redaction or audit log by using a different
executor.
"""

from __future__ import annotations

import json
import time
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, ValidationError

from aintegrator_integration.domain import (
    Address,
    AIntegratorError,
    ApprovalRequired,
    Customer,
    EligibilityResult,
    Offer,
    Order,
    OrderIntent,
    OrderStatus,
    UnsupportedOperation,
)
from aintegrator_integration.security import ToolSecurity, build_tool_security
from aintegrator_integration.service import ConnectivityService

__all__ = [
    "TOOL_DESCRIPTIONS",
    "CreateOrderInput",
    "EligibilityInput",
    "ListOrdersInput",
    "OrderIdInput",
    "ToolOperations",
]


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


TOOL_DESCRIPTIONS = {
    "check_eligibility": (
        "Check all providers for offers at an address. Returns one result per provider, "
        "including offer IDs needed to place an order."
    ),
    "create_order": (
        "Place an order for a specific offer ID returned by check_eligibility. The first call "
        "returns an approval ID; repeat it with that ID after the user grants the approval."
    ),
    "get_order": "Get the current status and details of one order by its ID.",
    "list_orders": "List recent orders, optionally filtered by status.",
    "cancel_order": (
        "Cancel a cancellable order. The first call returns an approval ID; repeat it with "
        "that ID after the user grants the approval."
    ),
}


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _error(
    message: str, *, code: str = "INVALID_ARGUMENTS", retryable: bool = False
) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, "retryable": retryable}}


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


class ToolOperations:
    """Validate arguments, enforce security policy and call the service."""

    def __init__(
        self,
        service: ConnectivityService,
        *,
        run_id: str | None = None,
        security: ToolSecurity | None = None,
    ) -> None:
        self._service = service
        self._run_id = run_id or "mcp"
        self.security = security or build_tool_security(service.session_maker, service.settings)

    async def execute(self, name: str, arguments: dict[str, Any]) -> str:
        """Every call is profile-checked, audited and returned as structured JSON."""
        started = time.perf_counter()
        approval_id = arguments.get("approval_id")
        dispatch_arguments = {
            key: value for key, value in arguments.items() if key != "approval_id"
        }
        payload: dict[str, Any]
        try:
            self.security.check_tool(name)
            payload = await self._dispatch(name, dispatch_arguments, approval_id)
        except ApprovalRequired as exc:
            approval_id = exc.approval_id
            payload = {
                "ok": False,
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "retryable": exc.retryable,
                    "details": {
                        "approval_id": exc.approval_id,
                        "expires_at": exc.expires_at.isoformat(),
                        "summary": exc.summary,
                        "call_arguments": exc.call_arguments,
                    },
                },
            }
        except ValidationError as exc:
            payload = _error(f"invalid arguments: {exc.errors()}")
        except AIntegratorError as exc:
            payload = _error(exc.message, code=exc.code, retryable=exc.retryable)
        except (ValueError, TypeError) as exc:
            payload = _error(str(exc))
        except Exception as exc:  # pragma: no cover - defensive tool boundary
            payload = _error(f"unexpected tool failure: {type(exc).__name__}")

        result = _json(payload)
        await self.security.audit.record(
            run_id=self._run_id,
            tool=name,
            arguments=dispatch_arguments,
            result=result,
            approval_id=approval_id if isinstance(approval_id, str) else None,
            duration_ms=(time.perf_counter() - started) * 1000,
        )
        return self.security.redactor.text(result)

    async def _dispatch(
        self, name: str, arguments: dict[str, Any], approval_id: Any
    ) -> dict[str, Any]:
        if name == "check_eligibility":
            return await self.check_eligibility(EligibilityInput.model_validate(arguments))
        if name == "create_order":
            return await self.create_order(
                CreateOrderInput.model_validate(arguments),
                approval_id=approval_id if isinstance(approval_id, str) else None,
            )
        if name == "get_order":
            return await self.get_order(OrderIdInput.model_validate(arguments))
        if name == "list_orders":
            return await self.list_orders(ListOrdersInput.model_validate(arguments))
        if name == "cancel_order":
            return await self.cancel_order(
                OrderIdInput.model_validate(arguments),
                approval_id=approval_id if isinstance(approval_id, str) else None,
            )
        raise UnsupportedOperation(f"unknown tool: {name}", code="UNKNOWN_TOOL")

    async def check_eligibility(self, args: EligibilityInput) -> dict[str, Any]:
        results = await self._service.check_eligibility(args.to_domain())
        return {"ok": True, "providers": [_eligibility_json(item) for item in results]}

    async def create_order(
        self, args: CreateOrderInput, *, approval_id: str | None = None
    ) -> dict[str, Any]:
        address = args.to_domain()
        offer = await self._find_offer(args.provider, args.offer_id, address)
        if offer is None:
            return _error(
                f"offer {args.offer_id} is not available from {args.provider} at this address",
                code="OFFER_NOT_FOUND",
            )
        idempotency_key = args.idempotency_key or f"agent:{uuid4()}"
        client_reference = args.client_reference or f"agent-{uuid4()}"
        total = float(offer.monthly_price.as_eur + offer.install_fee.as_eur)
        params = {
            "provider": args.provider,
            "offer_id": args.offer_id,
            "street": args.street,
            "number": args.number,
            "postal_code": args.postal_code,
            "city": args.city,
            "country": args.country,
            "customer_name": args.customer_name,
            "customer_email": args.customer_email,
            "idempotency_key": idempotency_key,
            "client_reference": client_reference,
            "max_total_eur": round(total, 2),
        }
        call_arguments = {
            **args.model_dump(exclude_none=True),
            "idempotency_key": idempotency_key,
            "client_reference": client_reference,
        }
        summary = (
            f"Create {args.provider} order {offer.plan_name} for {args.customer_name} at "
            f"{address.one_line}, max {total:.2f} EUR"
        )
        await self.security.approvals.require(
            "create_order",
            params,
            summary=summary,
            call_arguments=call_arguments,
            approval_id=approval_id,
        )
        intent = OrderIntent(
            offer=offer,
            address=address,
            customer=Customer(name=args.customer_name, email=args.customer_email),
            idempotency_key=idempotency_key,
            client_reference=client_reference,
            run_id=self._run_id,
        )
        order = await self._service.create_order(intent)
        return {"ok": True, "order": _order_json(order)}

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

    async def get_order(self, args: OrderIdInput) -> dict[str, Any]:
        order_id = self._parse_order_id(args.order_id)
        if order_id is None:
            return _error(f"invalid order ID: {args.order_id}")
        order = await self._service.get_order(order_id)
        return {"ok": True, "order": _order_json(order)}

    async def list_orders(self, args: ListOrdersInput) -> dict[str, Any]:
        status: OrderStatus | None = None
        if args.status:
            try:
                status = OrderStatus(args.status.upper())
            except ValueError:
                return _error(f"unknown order status: {args.status}")
        orders = await self._service.list_orders(status, args.limit)
        return {"ok": True, "orders": [_order_json(order) for order in orders]}

    async def cancel_order(
        self, args: OrderIdInput, *, approval_id: str | None = None
    ) -> dict[str, Any]:
        order_id = self._parse_order_id(args.order_id)
        if order_id is None:
            return _error(f"invalid order ID: {args.order_id}")
        stored = await self._service.get_order(order_id)
        await self.security.approvals.require(
            "cancel_order",
            {"order_id": str(order_id)},
            summary=f"Cancel {stored.provider} order {order_id} (currently {stored.status.value})",
            call_arguments={"order_id": str(order_id)},
            approval_id=approval_id,
        )
        order = await self._service.cancel_order(order_id)
        return {"ok": True, "order": _order_json(order)}

    @staticmethod
    def _parse_order_id(value: str) -> UUID | None:
        try:
            return UUID(value)
        except ValueError:
            return None
