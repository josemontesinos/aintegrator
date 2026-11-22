"""Adapter for skylink C: async REST with webhooks and request-id polling."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError

from aintegrator_integration.adapters.skylink.dto import SkylinkEnvelopeDTO, SkylinkOrderDTO
from aintegrator_integration.adapters.skylink.mapper import map_availability_payload, map_order
from aintegrator_integration.config import Settings
from aintegrator_integration.domain import (
    Address,
    Capabilities,
    EligibilityResult,
    EligibilityStatus,
    NotFound,
    Order,
    OrderIntent,
    ProviderProtocolError,
    ProviderRejected,
    UnsupportedOperation,
)
from aintegrator_integration.resilience import Clock, ResilientHttpClient, SystemClock

__all__ = ["SkylinkAdapter"]

CAPABILITIES = Capabilities(
    supports_idempotency_key=False,
    supports_cancel=False,
    eligibility_mode="ASYNC",
    eligibility_cacheable=False,
    supports_lookup_by_reference=True,
)


class SkylinkAdapter:
    name = "skylink"
    capabilities = CAPABILITIES

    def __init__(
        self,
        settings: Settings,
        *,
        http: ResilientHttpClient | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.settings = settings
        self.clock: Clock = clock or SystemClock()
        self._http = http or ResilientHttpClient(self.name, settings, clock=self.clock)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        idempotent: bool = False,
        run_id: str | None = None,
        **kwargs: object,
    ) -> Any:
        response = await self._http.request(
            method,
            f"{self.settings.skylink_base_url}{path}",
            operation=operation,
            idempotent=idempotent,
            run_id=run_id,
            headers={"X-Api-Key": self.settings.skylink_api_key},
            **kwargs,
        )
        if response.status_code >= 400:
            detail = ""
            try:
                payload = response.json()
                errors = payload.get("errors") or []
                detail = str(errors[0]) if errors else str(payload.get("meta", ""))
            except ValueError:
                detail = response.text[:200]
            if response.status_code == 404:
                raise NotFound(detail or "skylink resource not found")
            raise ProviderRejected(
                detail or f"skylink returned {response.status_code}", reason=detail
            )
        try:
            envelope = SkylinkEnvelopeDTO.model_validate(response.json())
        except (ValidationError, ValueError) as exc:
            raise ProviderProtocolError("Invalid skylink envelope") from exc
        if envelope.errors:
            raise ProviderRejected(f"skylink reported errors: {envelope.errors}")
        return envelope.data

    async def check_eligibility(self, address: Address) -> EligibilityResult:
        data = await self._request(
            "POST",
            "/api/v2/availability-requests",
            operation="availability.create",
            idempotent=True,
            json={
                "address": address.model_dump(mode="json"),
                "notes": "Fictional availability lookup; no real network queried.",
            },
        )
        request_ref = str(data.get("requestId") or "")
        if not request_ref:
            raise ProviderProtocolError("skylink availability response lacks requestId")
        return await self._poll_until_ready(request_ref)

    async def _poll_until_ready(self, request_ref: str) -> EligibilityResult:
        deadline = self.clock.now() + self.settings.eligibility_async_wait_s
        while True:
            result = await self.poll_eligibility(request_ref)
            if result.status is not EligibilityStatus.PENDING:
                return result
            if self.clock.now() >= deadline:
                return result
            await self.clock.sleep(min(0.5, max(0.0, deadline - self.clock.now())))

    async def poll_eligibility(self, request_ref: str) -> EligibilityResult:
        data = await self._request(
            "GET",
            f"/api/v2/availability-requests/{request_ref}",
            operation="availability.get",
            idempotent=True,
        )
        return map_availability_payload(data, request_ref)

    async def create_order(self, intent: OrderIntent, *, order_id: UUID | None = None) -> Order:
        data = await self._request(
            "POST",
            "/api/v2/orders",
            operation="orders.create",
            run_id=intent.run_id,
            json={
                "clientReference": intent.client_reference,
                "offerId": intent.offer.offer_id.removeprefix("skylink:"),
                "address": intent.address.model_dump(mode="json"),
                "customer": intent.customer.model_dump(mode="json"),
            },
        )
        dto = SkylinkOrderDTO.model_validate(data)
        return map_order(dto, intent, order_id=order_id or uuid4(), now=datetime.now(UTC))

    async def get_order(
        self,
        reference: str,
        *,
        intent: OrderIntent | None = None,
        order_id: UUID | None = None,
    ) -> Order:
        data = await self._request(
            "GET", f"/api/v2/orders/{reference}", operation="orders.get", idempotent=True
        )
        return map_order(SkylinkOrderDTO.model_validate(data), intent, order_id=order_id or uuid4())

    async def cancel_order(
        self,
        reference: str,
        *,
        intent: OrderIntent | None = None,
        order_id: UUID | None = None,
    ) -> Order:
        raise UnsupportedOperation("skylink does not support cancellation")

    async def find_by_reference(
        self,
        client_reference: str,
        *,
        intent: OrderIntent | None = None,
        order_id: UUID | None = None,
    ) -> Order | None:
        data = await self._request(
            "GET",
            "/api/v2/orders",
            operation="orders.lookup",
            idempotent=True,
            params={"clientReference": client_reference},
        )
        if isinstance(data, dict):
            orders = data.get("orders") or data.get("items") or []
            if isinstance(orders, dict):
                orders = orders.get("order", [])
        elif isinstance(data, list):
            orders = data
        else:
            orders = []
        if not orders:
            return None
        return map_order(
            SkylinkOrderDTO.model_validate(orders[0]),
            intent,
            order_id=order_id or uuid4(),
        )
