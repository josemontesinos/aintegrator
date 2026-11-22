"""Adapter for legacytel B: SOAP 1.1 consumed with zeep."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

import requests
from aintegrator_observability import Tracer, get_tracer, traced
from zeep import Client
from zeep.exceptions import Fault
from zeep.helpers import serialize_object
from zeep.transports import Transport

from aintegrator_integration.adapters.legacytel.dto import LegacytelOrderDTO
from aintegrator_integration.adapters.legacytel.mapper import (
    check_business_error,
    map_offers,
    map_order,
)
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
    ProviderTimeout,
    ProviderUnavailable,
    UnsupportedOperation,
)
from aintegrator_integration.resilience import (
    AuditSink,
    Clock,
    RetryPolicy,
    SystemClock,
    with_retry,
)

__all__ = ["LegacytelAdapter"]

CAPABILITIES = Capabilities(
    supports_idempotency_key=False,
    supports_cancel=False,
    eligibility_mode="SYNC",
    eligibility_cacheable=False,
    supports_lookup_by_reference=True,
)

SoapCall = Any  # zeep objects are untyped; serialized results are plain dicts.


class LegacytelAdapter:
    name = "legacytel"
    capabilities = CAPABILITIES

    def __init__(
        self,
        settings: Settings,
        *,
        clock: Clock | None = None,
        soap_call: Any = None,
        audit_sink: AuditSink | None = None,
        tracer: Tracer | None = None,
    ) -> None:
        self.settings = settings
        self.clock: Clock = clock or SystemClock()
        self._soap_call = soap_call
        self._client: Client | None = None
        self._audit_sink = audit_sink
        self.tracer: Tracer = tracer or get_tracer()

    def _build_client(self) -> Client:
        session = requests.Session()
        session.auth = (self.settings.legacytel_username, self.settings.legacytel_password)
        client_factory: Any = Client
        transport_factory: Any = Transport
        client = cast(
            Client,
            client_factory(
                f"{self.settings.legacytel_base_url}/soap?wsdl",
                transport=transport_factory(session=session),
            ),
        )
        # The shipped WSDL advertises localhost; override it so the same adapter
        # works when the simulated provider runs under a different hostname.
        binding_options: dict[str, Any] = getattr(client.service, "_binding_options", {})
        binding_options["address"] = f"{self.settings.legacytel_base_url}/soap"
        client.service._binding_options = binding_options
        return client

    @traced(
        "integration.http",
        attributes=lambda self, operation, payload, **kwargs: {
            "provider": self.name,
            "operation": operation,
            "protocol": "soap",
            "idempotent": bool(kwargs.get("idempotent", False)),
        },
    )
    async def _call(
        self, operation: str, payload: dict[str, object], *, idempotent: bool = False
    ) -> Any:
        """Call a SOAP operation off the event loop, retrying only safe reads."""

        def invoke() -> Any:
            try:
                if self._soap_call is not None:
                    raw = self._soap_call(operation, **payload)
                else:
                    if self._client is None:
                        self._client = self._build_client()
                    raw = getattr(self._client.service, operation)(**payload)
                serializer: Any = serialize_object
                return serializer(raw, dict) if raw is not None else {}
            except Fault as exc:
                fault_code = str(exc.code or "").lower()
                if "server" in fault_code:
                    raise ProviderUnavailable(f"legacytel server fault: {exc.message}") from exc
                if "client" in fault_code:
                    raise ProviderRejected(
                        f"legacytel rejected the request: {exc.message}"
                    ) from exc
                raise ProviderProtocolError(f"legacytel SOAP fault: {exc}") from exc
            except requests.Timeout as exc:
                raise ProviderTimeout("legacytel timed out") from exc
            except requests.RequestException as exc:
                raise ProviderUnavailable("legacytel transport error") from exc

        started = self.clock.now()
        try:
            if idempotent:
                result = await with_retry(
                    lambda: asyncio.to_thread(invoke),
                    policy=RetryPolicy(max_attempts=self.settings.provider_max_attempts),
                    clock=self.clock,
                    should_retry=lambda exc: isinstance(
                        exc, (ProviderUnavailable, ProviderTimeout)
                    ),
                )
            else:
                result = await asyncio.to_thread(invoke)
        except Exception as exc:
            if self._audit_sink is not None:
                await self._audit_sink(
                    self.name,
                    operation,
                    f"soap:{operation}",
                    (self.clock.now() - started) * 1000,
                    getattr(exc, "code", "PROVIDER_UNAVAILABLE"),
                    None,
                )
            raise
        if self._audit_sink is not None:
            await self._audit_sink(
                self.name,
                operation,
                f"soap:{operation}",
                (self.clock.now() - started) * 1000,
                "OK",
                None,
            )
        return result

    async def check_eligibility(self, address: Address) -> EligibilityResult:
        response = await self._call(
            "CheckAvailability",
            {"PostalCode": address.postal_code, "Street": address.street, "Number": address.number},
            idempotent=True,
        )
        check_business_error(response)
        offers = map_offers(response.get("Offers"))
        return EligibilityResult(
            provider=self.name,
            status=EligibilityStatus.OK if offers else EligibilityStatus.UNAVAILABLE,
            offers=tuple(offers),
            fetched_at=datetime.now(UTC),
        )

    async def poll_eligibility(self, request_ref: str) -> EligibilityResult:
        raise UnsupportedOperation("legacytel eligibility is synchronous")

    async def create_order(self, intent: OrderIntent, *, order_id: UUID | None = None) -> Order:
        response = await self._call(
            "PlaceOrder",
            {
                "ExternalRef": intent.client_reference,
                "OfferId": intent.offer.offer_id.removeprefix("legacytel:"),
                "CustomerName": intent.customer.name,
                "CustomerEmail": intent.customer.email,
                "PostalCode": intent.address.postal_code,
                "Street": intent.address.street,
                "Number": intent.address.number,
            },
        )
        check_business_error(response)
        order_data = response.get("Order") or response
        return map_order(
            LegacytelOrderDTO.model_validate(order_data),
            intent,
            order_id=order_id or uuid4(),
        )

    async def get_order(
        self,
        reference: str,
        *,
        intent: OrderIntent | None = None,
        order_id: UUID | None = None,
    ) -> Order:
        response = await self._call("GetOrderStatus", {"OrderId": reference}, idempotent=True)
        check_business_error(response)
        order_data = response.get("Order") or response
        return map_order(
            LegacytelOrderDTO.model_validate(order_data),
            intent,
            order_id=order_id or uuid4(),
        )

    async def cancel_order(
        self,
        reference: str,
        *,
        intent: OrderIntent | None = None,
        order_id: UUID | None = None,
    ) -> Order:
        raise UnsupportedOperation("legacytel does not support cancellation")

    async def find_by_reference(
        self,
        client_reference: str,
        *,
        intent: OrderIntent | None = None,
        order_id: UUID | None = None,
    ) -> Order | None:
        response = await self._call(
            "FindOrdersByReference", {"ExternalRef": client_reference}, idempotent=True
        )
        check_business_error(response)
        orders = response.get("Orders") or response
        if isinstance(orders, dict):
            orders = orders.get("Order", [])
        if isinstance(orders, dict):
            orders = [orders]
        if not orders:
            raise NotFound(f"no legacytel order for reference {client_reference}")
        return map_order(
            LegacytelOrderDTO.model_validate(orders[0]),
            intent,
            order_id=order_id or uuid4(),
        )
