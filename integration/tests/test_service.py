"""Core correctness tests for order creation, idempotency and reconciliation."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from aintegrator_integration.config import Settings
from aintegrator_integration.domain import (
    Address,
    Capabilities,
    Customer,
    EligibilityResult,
    EligibilityStatus,
    Money,
    Offer,
    Order,
    OrderIntent,
    OrderStatus,
    ProviderRejected,
    ProviderTimeout,
    Technology,
    UnsupportedOperation,
)
from aintegrator_integration.persistence import Repository, session_scope
from aintegrator_integration.service import ConnectivityService

CAPS = Capabilities(
    supports_idempotency_key=True,
    supports_cancel=False,
    eligibility_mode="SYNC",
    eligibility_cacheable=True,
    supports_lookup_by_reference=True,
)


def _offer(price_eur: int = 49) -> Offer:
    return Offer(
        offer_id="fibernet:A-1",
        provider="fibernet",
        plan_name="Basic",
        technology=Technology.FTTH,
        down_mbps=300,
        up_mbps=100,
        monthly_price=Money(amount_minor=price_eur * 100),
        install_fee=Money(amount_minor=0),
        lead_time_days=3,
    )


def _intent(key: str = "key-1", price: int = 49) -> OrderIntent:
    return OrderIntent(
        offer=_offer(price),
        address=Address(
            street="Main", number="1", postal_code="28001", city="Madrid", country="ES"
        ),
        customer=Customer(name="Ada", email="ada@example.invalid"),
        idempotency_key=key,
        client_reference="ref-1",
    )


class FakeGateway:
    name = "fibernet"
    capabilities = CAPS

    def __init__(
        self, *, timeout_with_side_effect: bool = False, missing_on_lookup: bool = False
    ) -> None:
        self.calls = 0
        self.provider_orders: dict[str, Order] = {}
        self.timeout_with_side_effect = timeout_with_side_effect
        self.missing_on_lookup = missing_on_lookup

    def _build(self, intent: OrderIntent, order_id: UUID | None) -> Order:
        now = datetime.now(UTC)
        return Order(
            order_id=order_id or uuid4(),
            provider=self.name,
            provider_order_id=f"A-{len(self.provider_orders) + 1}",
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
        return EligibilityResult(
            provider=self.name,
            status=EligibilityStatus.OK,
            offers=(_offer(),),
            fetched_at=datetime.now(UTC),
        )

    async def poll_eligibility(self, request_ref: str) -> EligibilityResult:
        raise UnsupportedOperation("not async")

    async def create_order(self, intent: OrderIntent, *, order_id: UUID | None = None) -> Order:
        self.calls += 1
        order = self._build(intent, order_id)
        if self.timeout_with_side_effect and self.calls == 1:
            self.provider_orders[intent.client_reference] = order
            raise ProviderTimeout("ambiguous")
        if self.missing_on_lookup and self.calls == 1:
            raise ProviderTimeout("no side effect")
        self.provider_orders[intent.client_reference] = order
        return order

    async def get_order(self, reference, *, intent=None, order_id=None) -> Order:
        return self.provider_orders[reference]

    async def cancel_order(self, reference, *, intent=None, order_id=None) -> Order:
        raise UnsupportedOperation("no cancel in this fake")

    async def find_by_reference(
        self, client_reference, *, intent=None, order_id=None
    ) -> Order | None:
        if self.missing_on_lookup or client_reference not in self.provider_orders:
            return None
        return self.provider_orders[client_reference]


def _service(session_maker, gateway, **settings_kwargs) -> ConnectivityService:
    return ConnectivityService(
        {gateway.name: gateway}, session_maker, settings=Settings(**settings_kwargs)
    )


async def test_create_order_is_idempotent(session_maker) -> None:
    gateway = FakeGateway()
    service = _service(session_maker, gateway)

    first = await service.create_order(_intent())
    second = await service.create_order(_intent())

    assert first.order_id == second.order_id
    assert first.status is OrderStatus.SUBMITTED
    assert gateway.calls == 1
    assert len(gateway.provider_orders) == 1


async def test_ambiguous_timeout_is_reconciled_without_duplicate(session_maker) -> None:
    gateway = FakeGateway(timeout_with_side_effect=True)
    service = _service(session_maker, gateway)

    order = await service.create_order(_intent())
    again = await service.create_order(_intent())

    assert order.status is OrderStatus.SUBMITTED
    assert order.provider_order_id == "A-1"
    assert again.order_id == order.order_id
    assert len(gateway.provider_orders) == 1
    async with session_scope(session_maker) as session:
        stored = await Repository(session).get_order(order.order_id)
        assert stored is not None
        assert stored.status_history[-1].status is OrderStatus.SUBMITTED


async def test_retry_only_after_confirming_no_order_exists(session_maker) -> None:
    gateway = FakeGateway(missing_on_lookup=True)
    service = _service(session_maker, gateway)

    order = await service.create_order(_intent())

    assert order.status is OrderStatus.SUBMITTED
    assert gateway.calls == 2
    assert len(gateway.provider_orders) == 1


async def test_spend_cap_is_enforced(session_maker) -> None:
    gateway = FakeGateway()
    service = _service(session_maker, gateway, max_order_value_eur=10)

    with pytest.raises(ProviderRejected):
        await service.create_order(_intent(price=49))
    assert gateway.calls == 0


async def test_eligibility_reports_partial_failure_without_raising(session_maker) -> None:
    class TimeoutGateway(FakeGateway):
        name = "legacytel"

        async def check_eligibility(self, address: Address) -> EligibilityResult:
            raise ProviderTimeout("down")

    good, bad = FakeGateway(), TimeoutGateway()
    service = ConnectivityService(
        {"fibernet": good, "legacytel": bad},
        session_maker,
        settings=Settings(),
    )
    results = await service.check_eligibility(
        Address(street="Main", number="1", postal_code="28001", city="Madrid", country="ES")
    )
    assert [result.status for result in results] == [
        EligibilityStatus.OK,
        EligibilityStatus.TIMEOUT,
    ]
