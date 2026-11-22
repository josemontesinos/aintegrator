"""Shared in-memory service fixture for agent tests (no external services)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from aintegrator_integration.config import Settings
from aintegrator_integration.domain import (
    Address,
    Capabilities,
    EligibilityResult,
    EligibilityStatus,
    Money,
    Offer,
    Order,
    OrderIntent,
    OrderStatus,
    Technology,
    UnsupportedOperation,
)
from aintegrator_integration.persistence import (
    SessionMaker,
    init_db,
    make_engine,
    make_session_maker,
)
from aintegrator_integration.service import ConnectivityService

CAPS = Capabilities(
    supports_idempotency_key=True,
    supports_cancel=True,
    eligibility_mode="SYNC",
    eligibility_cacheable=True,
    supports_lookup_by_reference=True,
)


def make_offer(provider: str = "fibernet", offer_id: str = "fibernet:A-1") -> Offer:
    return Offer(
        offer_id=offer_id,
        provider=provider,
        plan_name="Fictional Basic",
        technology=Technology.FTTH,
        down_mbps=300,
        up_mbps=100,
        monthly_price=Money(amount_minor=4900),
        install_fee=Money(amount_minor=0),
        lead_time_days=3,
    )


class FakeGateway:
    name = "fibernet"
    capabilities = CAPS

    def __init__(self) -> None:
        self.orders: dict[str, Order] = {}

    def _order(self, intent: OrderIntent, order_id: UUID | None = None) -> Order:
        now = datetime.now(UTC)
        return Order(
            order_id=order_id or uuid4(),
            provider=self.name,
            provider_order_id=f"A-{len(self.orders) + 1}",
            client_reference=intent.client_reference,
            offer=intent.offer,
            address=intent.address,
            customer=intent.customer,
            status=OrderStatus.SUBMITTED,
            supports_cancel=True,
            created_at=now,
            updated_at=now,
        )

    async def check_eligibility(self, address: Address) -> EligibilityResult:
        del address
        return EligibilityResult(
            provider=self.name,
            status=EligibilityStatus.OK,
            offers=(make_offer(),),
            fetched_at=datetime.now(UTC),
        )

    async def create_order(self, intent: OrderIntent, *, order_id: UUID | None = None) -> Order:
        order = self._order(intent, order_id)
        self.orders[intent.client_reference] = order
        return order

    async def get_order(self, reference: str, **kwargs: object) -> Order:
        del kwargs
        if reference not in self.orders:
            for order in self.orders.values():
                if order.provider_order_id == reference:
                    return order
            raise KeyError(reference)
        return self.orders[reference]

    async def find_by_reference(self, reference: str, **kwargs: object) -> Order | None:
        del kwargs
        return self.orders.get(reference)

    async def cancel_order(self, reference: str, **kwargs: object) -> Order:
        del kwargs
        order = await self.get_order(reference)
        cancelled = order.model_copy(
            update={"status": OrderStatus.CANCELLED, "supports_cancel": True}
        )
        self.orders[order.client_reference] = cancelled
        return cancelled

    async def poll_eligibility(self, request_ref: str) -> EligibilityResult:
        raise UnsupportedOperation("not async")


@pytest.fixture
async def agent_service() -> AsyncIterator[tuple[ConnectivityService, FakeGateway]]:
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    session_maker: SessionMaker = make_session_maker(engine)
    gateway = FakeGateway()
    service = ConnectivityService(
        {gateway.name: gateway}, session_maker, settings=Settings(write_rate_limit_per_minute=100)
    )
    try:
        yield service, gateway
    finally:
        await engine.dispose()


ADDRESS = {
    "street": "Fictional Street",
    "number": "12",
    "postal_code": "28001",
    "city": "Madrid",
    "country": "ES",
}
CUSTOMER = {"customer_name": "Ada Example", "customer_email": "ada@example.invalid"}
