"""Adapter for fibernet A: REST/JSON, OAuth2, idempotency keys."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
from pydantic import ValidationError

from aintegrator_integration.adapters.fibernet.dto import (
    FibernetCoverageDTO,
    FibernetOrderDTO,
    FibernetTokenDTO,
)
from aintegrator_integration.adapters.fibernet.mapper import map_offer, map_order
from aintegrator_integration.config import Settings
from aintegrator_integration.domain import (
    Address,
    Capabilities,
    EligibilityResult,
    EligibilityStatus,
    IdempotencyConflict,
    NotCancellable,
    NotFound,
    Offer,
    Order,
    OrderIntent,
    ProviderProtocolError,
    ProviderRejected,
)
from aintegrator_integration.resilience import Clock, ResilientHttpClient, SystemClock

__all__ = ["FibernetAdapter"]

CAPABILITIES = Capabilities(
    supports_idempotency_key=True,
    supports_cancel=True,
    eligibility_mode="SYNC",
    eligibility_cacheable=True,
    supports_lookup_by_reference=False,
)


class FibernetAdapter:
    name = "fibernet"
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
        self._intents: dict[str, tuple[OrderIntent, dict[str, object]]] = {}
        self._token: str | None = None
        self._token_expires_at = 0.0

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _fetch_token(self, *, force: bool = False) -> str:
        if not force and self._token and self.clock.now() < self._token_expires_at:
            return self._token
        response = await self._http.request(
            "POST",
            f"{self.settings.fibernet_base_url}/oauth/token",
            operation="token",
            idempotent=True,
            data={
                "grant_type": "client_credentials",
                "client_id": self.settings.fibernet_client_id,
                "client_secret": self.settings.fibernet_client_secret,
            },
        )
        try:
            token = FibernetTokenDTO.model_validate(response.json())
        except (ValidationError, ValueError) as exc:
            raise ProviderProtocolError("Invalid fibernet token response") from exc
        self._token = token.access_token
        self._token_expires_at = self.clock.now() + max(1.0, token.expires_in - 30)
        return self._token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        idempotent: bool = False,
        run_id: str | None = None,
        headers: dict[str, str] | None = None,
        **kwargs: object,
    ) -> httpx.Response:
        token = await self._fetch_token()
        url = f"{self.settings.fibernet_base_url}{path}"
        auth_headers = {"Authorization": f"Bearer {token}", **(headers or {})}
        response = await self._http.request(
            method,
            url,
            operation=operation,
            idempotent=idempotent,
            run_id=run_id,
            headers=auth_headers,
            **kwargs,
        )
        if response.status_code == 401:
            token = await self._fetch_token(force=True)
            response = await self._http.request(
                method,
                url,
                operation=operation,
                idempotent=idempotent,
                run_id=run_id,
                headers={"Authorization": f"Bearer {token}", **(headers or {})},
                **kwargs,
            )
        self._map_http_error(response)
        return response

    @staticmethod
    def _map_http_error(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        detail = ""
        try:
            payload = response.json()
            detail = str(payload.get("detail") or payload.get("title") or "")
        except ValueError:
            detail = response.text[:200]
        if response.status_code == 404:
            raise NotFound(detail or "fibernet order not found")
        if response.status_code == 409:
            raise NotCancellable(detail or "fibernet order cannot be cancelled")
        if response.status_code == 422:
            raise IdempotencyConflict(detail or "fibernet idempotency conflict")
        raise ProviderRejected(detail or f"fibernet returned {response.status_code}", reason=detail)

    async def check_eligibility(self, address: Address) -> EligibilityResult:
        offers: list[Offer] = []
        cursor: str | None = None
        for _ in range(20):
            params: dict[str, object] = {
                "postal_code": address.postal_code,
                "street": address.street,
                "number": address.number,
                "limit": 50,
            }
            if cursor:
                params["cursor"] = cursor
            response = await self._request(
                "GET", "/v1/coverage", operation="coverage", idempotent=True, params=params
            )
            try:
                payload = FibernetCoverageDTO.model_validate(response.json())
            except (ValidationError, ValueError) as exc:
                raise ProviderProtocolError("Invalid fibernet coverage response") from exc
            offers.extend(map_offer(offer) for offer in payload.offers)
            cursor = payload.next_cursor
            if not cursor:
                break
        return EligibilityResult(
            provider=self.name,
            status=EligibilityStatus.OK if offers else EligibilityStatus.UNAVAILABLE,
            offers=tuple(offers),
            fetched_at=datetime.now(UTC),
        )

    async def poll_eligibility(self, request_ref: str) -> EligibilityResult:
        raise ProviderProtocolError("fibernet eligibility is synchronous")

    def _order_body(self, intent: OrderIntent) -> dict[str, object]:
        return {
            "offer_id": intent.offer.offer_id.removeprefix("fibernet:"),
            "client_reference": intent.client_reference,
            "address": intent.address.model_dump(mode="json"),
            "customer": intent.customer.model_dump(mode="json"),
        }

    async def create_order(self, intent: OrderIntent, *, order_id: UUID | None = None) -> Order:
        body = self._order_body(intent)
        self._intents[intent.client_reference] = (intent, body)
        response = await self._request(
            "POST",
            "/v1/orders",
            operation="orders.create",
            idempotent=True,
            run_id=intent.run_id,
            headers={"Idempotency-Key": intent.idempotency_key},
            json=body,
        )
        try:
            dto = FibernetOrderDTO.model_validate(response.json())
        except (ValidationError, ValueError) as exc:
            raise ProviderProtocolError("Invalid fibernet order response") from exc
        order = map_order(
            dto, intent, order_id=order_id or uuid4(), now=datetime.now(UTC), created=True
        )
        self._intents[order.provider_order_id or ""] = (intent, body)
        return order

    async def get_order(
        self,
        reference: str,
        *,
        intent: OrderIntent | None = None,
        order_id: UUID | None = None,
    ) -> Order:
        if intent is None:
            raise NotFound("fibernet get_order needs the persisted order context")
        response = await self._request(
            "GET",
            f"/v1/orders/{reference}",
            operation="orders.get",
            idempotent=True,
            run_id=intent.run_id,
        )
        try:
            dto = FibernetOrderDTO.model_validate(response.json())
        except (ValidationError, ValueError) as exc:
            raise ProviderProtocolError("Invalid fibernet order response") from exc
        return map_order(dto, intent, order_id=order_id or uuid4())

    async def cancel_order(
        self,
        reference: str,
        *,
        intent: OrderIntent | None = None,
        order_id: UUID | None = None,
    ) -> Order:
        if intent is None:
            raise NotFound("fibernet cancel_order needs the persisted order context")
        response = await self._request(
            "POST",
            f"/v1/orders/{reference}/cancel",
            operation="orders.cancel",
            run_id=intent.run_id,
        )
        try:
            dto = FibernetOrderDTO.model_validate(response.json())
        except (ValidationError, ValueError) as exc:
            raise ProviderProtocolError("Invalid fibernet cancel response") from exc
        return map_order(dto, intent, order_id=order_id or uuid4())

    async def find_by_reference(
        self,
        client_reference: str,
        *,
        intent: OrderIntent | None = None,
        order_id: UUID | None = None,
    ) -> Order | None:
        """Fibernet has no lookup endpoint: replay the original idempotent POST."""
        if intent is None:
            return None
        body = self._order_body(intent)
        response = await self._request(
            "POST",
            "/v1/orders",
            operation="orders.replay",
            idempotent=True,
            run_id=intent.run_id,
            headers={"Idempotency-Key": intent.idempotency_key},
            json=body,
        )
        try:
            dto = FibernetOrderDTO.model_validate(response.json())
        except (ValidationError, ValueError) as exc:
            raise ProviderProtocolError("Invalid fibernet replay response") from exc
        return map_order(dto, intent, order_id=order_id or uuid4())
