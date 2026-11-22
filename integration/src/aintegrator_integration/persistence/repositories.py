"""Data access for the application service. No business rules live here."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aintegrator_integration.domain import (
    EligibilityResult,
    Order,
    OrderEvent,
    OrderStatus,
)
from aintegrator_integration.persistence.models import (
    AuditLogRow,
    EligibilityCacheRow,
    EligibilityRequestRow,
    IdempotencyKeyRow,
    OrderEventRow,
    OrderRow,
    ProviderCallRow,
    WebhookEventRow,
)

__all__ = ["Repository"]


def _as_row(order: Order) -> OrderRow:
    return OrderRow(
        id=order.order_id,
        provider=order.provider,
        provider_order_id=order.provider_order_id,
        client_reference=order.client_reference,
        idempotency_key=order.idempotency_key,
        offer_json=order.offer.model_dump(mode="json"),
        address_json=order.address.model_dump(mode="json"),
        customer_json=order.customer.model_dump(mode="json"),
        status=order.status.value,
        supports_cancel=order.supports_cancel,
        created_at=order.created_at,
        updated_at=order.updated_at,
    )


def _as_event_row(event: OrderEvent) -> OrderEventRow:
    return OrderEventRow(
        id=event.event_id,
        order_id=event.order_id,
        status=event.status.value,
        occurred_at=event.occurred_at,
        source=event.source,
        raw_ref=event.raw_ref,
        note=event.note,
    )


def _as_order(row: OrderRow, events: tuple[OrderEvent, ...] = ()) -> Order:
    return Order.model_validate(
        {
            "order_id": row.id,
            "provider": row.provider,
            "provider_order_id": row.provider_order_id,
            "client_reference": row.client_reference,
            "idempotency_key": row.idempotency_key,
            "offer": row.offer_json,
            "address": row.address_json,
            "customer": row.customer_json,
            "status": row.status,
            "supports_cancel": row.supports_cancel,
            "status_history": [event.model_dump(mode="json") for event in events],
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
    )


class Repository:
    """All persistence operations used by ConnectivityService and the webhook receiver."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- orders ---------------------------------------------------------
    async def create_order(self, order: Order) -> Order:
        # Flush the parent before its events: without an ORM relationship, a
        # single flush can insert children first and violate the FK on Postgres.
        self.session.add(_as_row(order))
        await self.session.flush()
        self.session.add_all([_as_event_row(event) for event in order.status_history])
        await self.session.flush()
        return order

    async def _load(self, row: OrderRow) -> Order:
        result = await self.session.execute(
            select(OrderEventRow)
            .where(OrderEventRow.order_id == row.id)
            .order_by(OrderEventRow.occurred_at)
        )
        events = tuple(
            OrderEvent.model_validate(
                {
                    "event_id": event.id,
                    "order_id": event.order_id,
                    "status": event.status,
                    "occurred_at": event.occurred_at,
                    "source": event.source,
                    "raw_ref": event.raw_ref,
                    "note": event.note,
                }
            )
            for event in result.scalars().all()
        )
        return _as_order(row, events)

    async def get_order(self, order_id: UUID) -> Order | None:
        row = await self.session.get(OrderRow, order_id)
        return await self._load(row) if row else None

    async def get_by_reference(self, client_reference: str) -> Order | None:
        result = await self.session.execute(
            select(OrderRow).where(OrderRow.client_reference == client_reference)
        )
        row = result.scalar_one_or_none()
        return await self._load(row) if row else None

    async def get_by_provider_id(self, provider: str, provider_order_id: str) -> Order | None:
        result = await self.session.execute(
            select(OrderRow).where(
                OrderRow.provider == provider,
                OrderRow.provider_order_id == provider_order_id,
            )
        )
        row = result.scalar_one_or_none()
        return await self._load(row) if row else None

    async def list_orders(self, status: OrderStatus | None = None, limit: int = 50) -> list[Order]:
        statement = select(OrderRow).order_by(OrderRow.created_at.desc()).limit(min(limit, 100))
        if status is not None:
            statement = statement.where(OrderRow.status == status.value)
        rows = (await self.session.execute(statement)).scalars().all()
        return [await self._load(row) for row in rows]

    async def save_order(self, order: Order) -> None:
        row = await self.session.get(OrderRow, order.order_id)
        if row is None:
            raise ValueError(f"order {order.order_id} does not exist")
        row.provider_order_id = order.provider_order_id
        row.idempotency_key = order.idempotency_key
        row.status = order.status.value
        row.supports_cancel = order.supports_cancel
        row.updated_at = order.updated_at
        known = set(
            (
                await self.session.execute(
                    select(OrderEventRow.id).where(OrderEventRow.order_id == row.id)
                )
            )
            .scalars()
            .all()
        )
        self.session.add_all(
            _as_event_row(event) for event in order.status_history if event.event_id not in known
        )
        await self.session.flush()

    async def orders_due_for_reconciliation(self, now: datetime, after_s: int) -> list[Order]:
        cutoff = now - timedelta(seconds=after_s)
        result = await self.session.execute(
            select(OrderRow).where(
                OrderRow.updated_at < cutoff,
                OrderRow.status.notin_(
                    [
                        status.value
                        for status in (
                            OrderStatus.COMPLETED,
                            OrderStatus.REJECTED,
                            OrderStatus.CANCELLED,
                            OrderStatus.FAILED,
                        )
                    ]
                ),
            )
        )
        return [await self._load(row) for row in result.scalars().all()]

    async def orders_since(self, since: datetime) -> list[Order]:
        result = await self.session.execute(select(OrderRow).where(OrderRow.created_at >= since))
        return [await self._load(row) for row in result.scalars().all()]

    async def orders_needing_review(self) -> list[Order]:
        result = await self.session.execute(
            select(OrderRow).where(OrderRow.status == OrderStatus.NEEDS_REVIEW.value)
        )
        return [await self._load(row) for row in result.scalars().all()]

    # -- idempotency ----------------------------------------------------
    async def get_idempotency(self, operation: str, key: str) -> IdempotencyKeyRow | None:
        return await self.session.get(IdempotencyKeyRow, (operation, key))

    async def create_idempotency(
        self, operation: str, key: str, request_hash: str, now: datetime
    ) -> bool:
        if await self.get_idempotency(operation, key) is not None:
            return False
        self.session.add(
            IdempotencyKeyRow(
                operation=operation,
                key=key,
                request_hash=request_hash,
                status="pending",
                created_at=now,
            )
        )
        await self.session.flush()
        return True

    async def complete_idempotency(
        self, operation: str, key: str, result_json: dict[str, Any], now: datetime
    ) -> None:
        row = await self.get_idempotency(operation, key)
        if row is None:
            raise ValueError(f"idempotency key {operation}/{key} does not exist")
        row.result_json = result_json
        row.status = "done"
        row.completed_at = now
        await self.session.flush()

    # -- audit and cache -------------------------------------------------
    async def record_call(
        self,
        provider: str,
        operation: str,
        summary: str,
        latency_ms: float,
        outcome: str,
        run_id: str | None,
        now: datetime,
    ) -> None:
        self.session.add(
            ProviderCallRow(
                id=uuid4(),
                provider=provider,
                operation=operation,
                summary=summary[:500],
                latency_ms=latency_ms,
                outcome=outcome,
                run_id=run_id,
                created_at=now,
            )
        )
        await self.session.flush()

    async def append_audit(
        self,
        run_id: str,
        tool: str,
        args_hash: str,
        result_json: dict[str, Any],
        approval_id: str | None,
        now: datetime,
    ) -> None:
        self.session.add(
            AuditLogRow(
                id=uuid4(),
                run_id=run_id,
                tool=tool,
                args_hash=args_hash,
                result_json=result_json,
                approval_id=approval_id,
                created_at=now,
            )
        )
        await self.session.flush()

    async def get_cache(self, provider: str, cache_key: str) -> EligibilityResult | None:
        row = await self.session.get(EligibilityCacheRow, (provider, cache_key))
        if row is None or row.expires_at <= datetime.now(UTC):
            return None
        return EligibilityResult.model_validate(row.result_json).model_copy(
            update={"from_cache": True}
        )

    async def set_cache(
        self, provider: str, cache_key: str, result: EligibilityResult, expires_at: datetime
    ) -> None:
        row = await self.session.get(EligibilityCacheRow, (provider, cache_key))
        payload = result.model_dump(mode="json")
        if row is None:
            self.session.add(
                EligibilityCacheRow(
                    provider=provider,
                    cache_key=cache_key,
                    result_json=payload,
                    fetched_at=datetime.now(UTC),
                    expires_at=expires_at,
                )
            )
        else:
            row.result_json = payload
            row.expires_at = expires_at
        await self.session.flush()

    # -- async eligibility requests -------------------------------------
    async def save_request(
        self,
        request_id: str,
        provider: str,
        address: dict[str, Any],
        status: str,
        now: datetime,
        expires_at: datetime | None = None,
    ) -> None:
        row = await self.session.get(EligibilityRequestRow, request_id)
        if row is None:
            self.session.add(
                EligibilityRequestRow(
                    request_id=request_id,
                    provider=provider,
                    address_json=address,
                    status=status,
                    created_at=now,
                    expires_at=expires_at,
                )
            )
        else:
            row.status = status
        await self.session.flush()

    async def get_request(self, request_id: str) -> EligibilityRequestRow | None:
        return await self.session.get(EligibilityRequestRow, request_id)

    # -- webhooks --------------------------------------------------------
    async def store_webhook(
        self,
        provider: str,
        event_id: str,
        order_reference: str | None,
        event_type: str,
        occurred_at: datetime,
        sequence: int,
        raw: dict[str, Any],
        received_at: datetime,
    ) -> bool:
        """Return False for a duplicate event id, True when stored."""
        existing = await self.session.execute(
            select(WebhookEventRow.id).where(
                WebhookEventRow.provider == provider,
                WebhookEventRow.event_id == event_id,
            )
        )
        if existing.first() is not None:
            return False
        self.session.add(
            WebhookEventRow(
                id=uuid4(),
                provider=provider,
                event_id=event_id,
                order_reference=order_reference,
                event_type=event_type,
                occurred_at=occurred_at,
                sequence=sequence,
                raw_json=raw,
                received_at=received_at,
            )
        )
        await self.session.flush()
        return True
