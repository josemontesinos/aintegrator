"""ConnectivityService: the application service behind MCP tools and evals.

Order creation is the core correctness path: persist the intent before any
external call, use provider-native idempotency where available, and reconcile
instead of blindly retrying when an outcome is ambiguous.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncEngine

from aintegrator_integration.config import Settings, get_settings
from aintegrator_integration.domain import (
    Address,
    AIntegratorError,
    EligibilityResult,
    EligibilityStatus,
    IdempotencyConflict,
    NotCancellable,
    NotFound,
    Order,
    OrderEvent,
    OrderIntent,
    OrderStatus,
    ProviderRateLimited,
    ProviderRejected,
    ProviderTimeout,
    ProviderUnavailable,
    UnsupportedOperation,
    can_apply,
)
from aintegrator_integration.gateways import ProviderGateway
from aintegrator_integration.persistence import Repository, SessionMaker, session_scope
from aintegrator_integration.resilience import Clock, SystemClock

__all__ = ["ConnectivityService"]


def _intent_hash(intent: OrderIntent) -> str:
    payload = json.dumps(intent.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _address_key(address: Address) -> str:
    return hashlib.sha256(address.one_line.encode()).hexdigest()[:32]


def _value_eur(order: Order) -> float:
    return float(order.offer.monthly_price.as_eur + order.offer.install_fee.as_eur)


class ConnectivityService:
    """Application service; transport-agnostic and usable in-process by MCP."""

    def __init__(
        self,
        gateways: dict[str, ProviderGateway],
        session_maker: SessionMaker,
        *,
        settings: Settings | None = None,
        clock: Clock | None = None,
        engine: AsyncEngine | None = None,
    ) -> None:
        self.gateways = gateways
        self.session_maker = session_maker
        self.settings = settings or get_settings()
        self.clock: Clock = clock or SystemClock()
        self._engine = engine

    def _utcnow(self) -> datetime:
        return datetime.now(UTC)

    async def aclose(self) -> None:
        """Release gateway connections and an engine owned by this service."""
        for gateway in self.gateways.values():
            closer = getattr(gateway, "aclose", None)
            if callable(closer):
                await closer()
        if self._engine is not None:
            await self._engine.dispose()

    @staticmethod
    def _intent_from_order(order: Order) -> OrderIntent:
        return OrderIntent(
            offer=order.offer,
            address=order.address,
            customer=order.customer,
            client_reference=order.client_reference,
            idempotency_key=order.idempotency_key or f"reconcile:{order.client_reference}",
            run_id=None,
        )

    def _apply_event(
        self,
        order: Order,
        status: OrderStatus,
        source: str,
        *,
        raw_ref: str | None = None,
        note: str | None = None,
    ) -> Order:
        """Record every event; only advance the state when the rules allow it."""
        now = self._utcnow()
        event = OrderEvent(
            order_id=order.order_id,
            status=status,
            occurred_at=now,
            source=source,
            raw_ref=raw_ref,
            note=note,
        )
        updates: dict[str, Any] = {
            "status": status if can_apply(order.status, status) else order.status,
            "status_history": (*order.status_history, event),
            "updated_at": now,
        }
        if raw_ref and status in (
            OrderStatus.SUBMITTED,
            OrderStatus.ACCEPTED,
            OrderStatus.IN_PROGRESS,
        ):
            updates["provider_order_id"] = raw_ref
        return order.model_copy(update=updates)

    # -- eligibility ----------------------------------------------------
    async def check_eligibility(self, address: Address) -> list[EligibilityResult]:
        """Fan out with per-provider timeouts; partial failure is a normal result."""
        tasks = [
            asyncio.wait_for(
                self._check_one(name, gateway, address),
                timeout=self.settings.provider_timeout_s * 3,
            )
            for name, gateway in self.gateways.items()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        output: list[EligibilityResult] = []
        for (name, _), result in zip(self.gateways.items(), results, strict=True):
            if isinstance(result, BaseException):
                status = (
                    EligibilityStatus.TIMEOUT
                    if isinstance(result, (TimeoutError, asyncio.TimeoutError, ProviderTimeout))
                    else EligibilityStatus.ERROR
                )
                output.append(
                    EligibilityResult(
                        provider=name,
                        status=status,
                        fetched_at=self._utcnow(),
                        error=str(result),
                    )
                )
            else:
                output.append(result)
        return output

    async def _check_one(
        self, name: str, gateway: ProviderGateway, address: Address
    ) -> EligibilityResult:
        cache_key = _address_key(address)
        if gateway.capabilities.eligibility_cacheable:
            async with session_scope(self.session_maker) as session:
                cached = await Repository(session).get_cache(name, cache_key)
                if cached is not None and cached.from_cache:
                    return cached
        try:
            result = await gateway.check_eligibility(address)
        except ProviderTimeout as exc:
            result = EligibilityResult(
                provider=name,
                status=EligibilityStatus.TIMEOUT,
                fetched_at=self._utcnow(),
                error=exc.message,
            )
        except AIntegratorError as exc:
            result = EligibilityResult(
                provider=name,
                status=EligibilityStatus.ERROR,
                fetched_at=self._utcnow(),
                error=exc.message,
            )
        async with session_scope(self.session_maker) as session:
            repository = Repository(session)
            if gateway.capabilities.eligibility_cacheable and result.status is EligibilityStatus.OK:
                expiries = [offer.valid_until for offer in result.offers if offer.valid_until]
                default_expiry = self._utcnow() + timedelta(hours=1)
                expires_at = min([*expiries, default_expiry])
                await repository.set_cache(
                    name, cache_key, result.model_copy(update={"from_cache": True}), expires_at
                )
            if result.status is EligibilityStatus.PENDING and result.request_ref:
                await repository.save_request(
                    result.request_ref,
                    name,
                    address.model_dump(mode="json"),
                    "PENDING",
                    self._utcnow(),
                    expires_at=self._utcnow()
                    + timedelta(seconds=self.settings.eligibility_async_wait_s),
                )
        return result

    # -- orders ----------------------------------------------------------
    async def create_order(self, intent: OrderIntent) -> Order:
        operation = "create_order"
        request_hash = _intent_hash(intent)
        value = float(intent.offer.monthly_price.as_eur + intent.offer.install_fee.as_eur)
        if value > self.settings.max_order_value_eur:
            raise ProviderRejected(f"Order value {value:.2f} EUR exceeds per-order cap")

        async with session_scope(self.session_maker) as session:
            repository = Repository(session)
            existing = await repository.get_idempotency(operation, intent.idempotency_key)
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise IdempotencyConflict("Idempotency key reused with different parameters")
                if existing.result_json:
                    return Order.model_validate(existing.result_json)
                raise IdempotencyConflict(
                    "A request with this idempotency key is still in progress"
                )
            writes_last_minute = await repository.orders_since(
                self._utcnow() - timedelta(seconds=60)
            )
            if len(writes_last_minute) >= self.settings.write_rate_limit_per_minute:
                raise ProviderRateLimited("Write rate limit reached; try again shortly")
            await repository.create_idempotency(
                operation, intent.idempotency_key, request_hash, self._utcnow()
            )
            period_start = self._utcnow() - timedelta(days=30)
            recent = await repository.orders_since(period_start)
            if (
                sum(_value_eur(order) for order in recent) + value
                > self.settings.max_period_value_eur
            ):
                raise ProviderRejected("Order would exceed the 30-day spend cap")

        order_id = uuid4()
        now = self._utcnow()
        gateway = self._get_gateway(intent.offer.provider)
        pending = Order(
            order_id=order_id,
            provider=gateway.name,
            provider_order_id=None,
            client_reference=intent.client_reference,
            idempotency_key=intent.idempotency_key,
            offer=intent.offer,
            address=intent.address,
            customer=intent.customer,
            status=OrderStatus.PENDING_SUBMISSION,
            supports_cancel=gateway.capabilities.supports_cancel,
            status_history=(
                OrderEvent(
                    order_id=order_id,
                    status=OrderStatus.PENDING_SUBMISSION,
                    occurred_at=now,
                    source="service",
                ),
            ),
            created_at=now,
            updated_at=now,
        )
        async with session_scope(self.session_maker) as session:
            await Repository(session).create_order(pending)

        try:
            provider_order = await gateway.create_order(intent, order_id=order_id)
        except (ProviderTimeout, ProviderUnavailable) as exc:
            final = await self._recover_ambiguous(
                gateway, intent, order_id, f"ambiguous provider call: {exc.message}"
            )
        except ProviderRejected as exc:
            status = OrderStatus.REJECTED if exc.code == "PROVIDER_REJECTED" else OrderStatus.FAILED
            final = self._apply_event(pending, status, "provider", note=exc.message)
        except AIntegratorError as exc:
            final = self._apply_event(pending, OrderStatus.FAILED, "provider", note=exc.message)
        else:
            final = self._apply_event(
                pending,
                OrderStatus.SUBMITTED,
                "provider",
                raw_ref=provider_order.provider_order_id,
            )

        async with session_scope(self.session_maker) as session:
            repository = Repository(session)
            stored = await repository.get_order(order_id)
            if stored is not None:
                final = self._apply_status_from(stored, final)
                await repository.save_order(final)
            await repository.complete_idempotency(
                operation, intent.idempotency_key, final.model_dump(mode="json"), self._utcnow()
            )
        return final

    @staticmethod
    def _apply_status_from(stored: Order, candidate: Order) -> Order:
        """Keep the persisted history and add only events produced by this call."""
        existing_ids = {event.event_id for event in stored.status_history}
        new_events = tuple(
            event for event in candidate.status_history if event.event_id not in existing_ids
        )
        return stored.model_copy(
            update={
                "status": candidate.status
                if can_apply(stored.status, candidate.status)
                else stored.status,
                "provider_order_id": candidate.provider_order_id or stored.provider_order_id,
                "supports_cancel": candidate.supports_cancel,
                "status_history": stored.status_history + new_events,
                "updated_at": candidate.updated_at,
            }
        )

    async def _recover_ambiguous(
        self, gateway: ProviderGateway, intent: OrderIntent, order_id: UUID, note: str
    ) -> Order:
        async with session_scope(self.session_maker) as session:
            repository = Repository(session)
            stored = await repository.get_order(order_id)
            if stored is None:
                raise NotFound(f"order {order_id} disappeared during reconciliation")
            needs_review = self._apply_event(stored, OrderStatus.NEEDS_REVIEW, "service", note=note)
            await repository.save_order(needs_review)

        try:
            found = await gateway.find_by_reference(
                intent.client_reference, intent=intent, order_id=order_id
            )
        except Exception as exc:
            return self._apply_event(
                needs_review, OrderStatus.NEEDS_REVIEW, "reconcile", note=str(exc)
            )

        if found is None:
            try:
                found = await gateway.create_order(intent, order_id=order_id)
            except Exception as exc:
                return self._apply_event(
                    needs_review, OrderStatus.NEEDS_REVIEW, "reconcile", note=f"retry failed: {exc}"
                )

        return self._apply_event(
            needs_review,
            found.status if found.status is not OrderStatus.NEEDS_REVIEW else OrderStatus.SUBMITTED,
            "reconcile",
            raw_ref=found.provider_order_id,
            note="resolved by client_reference reconciliation",
        )

    async def get_order(self, order_id: UUID) -> Order:
        async with session_scope(self.session_maker) as session:
            order = await Repository(session).get_order(order_id)
        if order is None:
            raise NotFound(f"order {order_id} not found")
        return order

    async def list_orders(self, status: OrderStatus | None = None, limit: int = 50) -> list[Order]:
        async with session_scope(self.session_maker) as session:
            return await Repository(session).list_orders(status, limit)

    async def cancel_order(self, order_id: UUID) -> Order:
        async with session_scope(self.session_maker) as session:
            stored = await Repository(session).get_order(order_id)
        if stored is None:
            raise NotFound(f"order {order_id} not found")
        gateway = self._get_gateway(stored.provider)
        if not gateway.capabilities.supports_cancel:
            raise UnsupportedOperation(f"{gateway.name} does not support cancellation")
        if not stored.cancellable:
            raise NotCancellable(f"order {order_id} is {stored.status.value}, not cancellable")
        provider_order = await gateway.cancel_order(
            str(stored.provider_order_id),
            intent=self._intent_from_order(stored),
            order_id=stored.order_id,
        )
        final = self._apply_event(
            stored, OrderStatus.CANCELLED, "provider", raw_ref=provider_order.provider_order_id
        )
        async with session_scope(self.session_maker) as session:
            await Repository(session).save_order(final)
        return final

    async def apply_webhook_event(
        self,
        provider: str,
        event_type: str,
        order_reference: str,
        *,
        occurred_at: datetime | None = None,
        raw_ref: str | None = None,
    ) -> Order | None:
        """Apply a signed, deduplicated webhook to the local state machine."""
        status = self._webhook_status(provider, event_type)
        async with session_scope(self.session_maker) as session:
            repository = Repository(session)
            stored = await repository.get_by_reference(order_reference)
            if stored is None:
                stored = await repository.get_by_provider_id(provider, order_reference)
            if stored is None:
                return None
            updated = self._apply_event(
                stored, status, "webhook", raw_ref=raw_ref or order_reference
            )
            await repository.save_order(updated)
            return updated

    async def reconcile_order(self, order: Order) -> Order:
        """Ask the provider for its truth and converge the local state machine."""
        gateway = self._get_gateway(order.provider)
        intent = self._intent_from_order(order)
        reference = str(order.provider_order_id or order.client_reference)
        truth: Order | None
        try:
            truth = await gateway.get_order(reference, intent=intent, order_id=order.order_id)
        except NotFound:
            truth = await gateway.find_by_reference(
                order.client_reference, intent=intent, order_id=order.order_id
            )
        if truth is None:
            return order
        updated = self._apply_event(
            order, truth.status, "reconciler", raw_ref=truth.provider_order_id
        )
        async with session_scope(self.session_maker) as session:
            await Repository(session).save_order(updated)
        return updated

    @staticmethod
    def _webhook_status(provider: str, event_type: str) -> OrderStatus:
        if provider != "skylink":
            raise UnsupportedOperation(f"no webhook mapping for {provider}")
        mapping = {
            "order.confirmed": OrderStatus.ACCEPTED,
            "order.working": OrderStatus.IN_PROGRESS,
            "order.done": OrderStatus.COMPLETED,
            "order.declined": OrderStatus.REJECTED,
        }
        status = mapping.get(event_type)
        if status is None:
            raise UnsupportedOperation(f"unknown skylink webhook type: {event_type}")
        return status

    def _get_gateway(self, provider: str) -> ProviderGateway:
        try:
            return self.gateways[provider]
        except KeyError as exc:
            raise NotFound(f"no gateway configured for provider {provider}") from exc
