"""Webhook signature, deduplication and state-machine application tests."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from uuid import uuid4

from aintegrator_integration.config import Settings
from aintegrator_integration.domain import (
    Address,
    Capabilities,
    Customer,
    Money,
    Offer,
    Order,
    OrderIntent,
    OrderStatus,
    Technology,
)
from aintegrator_integration.persistence import (
    Repository,
    make_engine,
    make_session_maker,
    session_scope,
)
from aintegrator_integration.service import ConnectivityService
from aintegrator_integration.webhooks import create_app, sign_body
from fastapi.testclient import TestClient

SECRET = "test-secret"


class _Gateway:
    name = "skylink"
    capabilities = Capabilities(
        supports_idempotency_key=False,
        supports_cancel=False,
        eligibility_mode="ASYNC",
        eligibility_cacheable=False,
        supports_lookup_by_reference=True,
    )

    async def check_eligibility(self, address):  # pragma: no cover - not used here
        raise NotImplementedError

    async def poll_eligibility(self, request_ref):  # pragma: no cover
        raise NotImplementedError

    async def create_order(self, intent, *, order_id=None):
        now = datetime.now(UTC)
        return Order(
            order_id=order_id or uuid4(),
            provider="skylink",
            provider_order_id="C-1",
            client_reference=intent.client_reference,
            offer=intent.offer,
            address=intent.address,
            customer=intent.customer,
            status=OrderStatus.SUBMITTED,
            created_at=now,
            updated_at=now,
        )

    async def get_order(self, reference, *, intent=None, order_id=None):
        raise NotImplementedError

    async def cancel_order(self, reference, *, intent=None, order_id=None):
        raise NotImplementedError

    async def find_by_reference(self, client_reference, *, intent=None, order_id=None):
        return None


def _intent(key: str) -> OrderIntent:
    offer = Offer(
        offer_id="skylink:C-1",
        provider="skylink",
        plan_name="Basic",
        technology=Technology.FTTH,
        down_mbps=100,
        up_mbps=50,
        monthly_price=Money(amount_minor=3990),
        install_fee=Money(amount_minor=0),
        lead_time_days=1,
    )
    return OrderIntent(
        offer=offer,
        address=Address(
            street="Main", number="1", postal_code="28001", city="Madrid", country="ES"
        ),
        customer=Customer(name="Ada", email="ada@example.invalid"),
        idempotency_key=key,
        client_reference=f"ref-{key}",
    )


def _client(tmp_path) -> tuple[TestClient, ConnectivityService]:
    database = f"sqlite+aiosqlite:///{tmp_path / 'webhooks.sqlite3'}"
    engine = make_engine(database)
    settings = Settings(database_url=database, skylink_webhook_secret=SECRET)
    service = ConnectivityService(
        {"skylink": _Gateway()}, make_session_maker(engine), settings=settings
    )
    import asyncio

    from aintegrator_integration.persistence import init_db

    asyncio.run(init_db(engine))
    return TestClient(create_app(service, settings)), service


def _post(
    client: TestClient,
    payload: dict[str, object],
    *,
    secret: str = SECRET,
    timestamp: int | None = None,
):
    raw = json.dumps(payload).encode()
    stamp = timestamp if timestamp is not None else int(time.time())
    return client.post(
        "/webhooks/skylink",
        content=raw,
        headers={"X-Skylink-Signature": sign_body(secret, raw, stamp)},
    )


def test_valid_webhook_is_accepted_and_deduplicated(tmp_path) -> None:
    client, _ = _client(tmp_path)
    payload = {
        "eventId": "evt-1",
        "type": "order.confirmed",
        "sequence": 1,
        "occurredAt": int(time.time() * 1000),
        "data": {"orderId": "C-1"},
    }
    assert _post(client, payload).status_code == 200
    assert _post(client, payload).json()["status"] == "duplicate"


def test_bad_signature_and_old_timestamp_are_rejected(tmp_path) -> None:
    client, _ = _client(tmp_path)
    payload = {"eventId": "evt-2", "type": "order.done", "data": {"orderId": "C-1"}}
    assert _post(client, payload, secret="wrong").status_code == 401
    assert _post(client, payload, timestamp=int(time.time()) - 400).status_code == 401


async def test_webhook_advances_order_state_and_never_regresses(tmp_path) -> None:
    from aintegrator_integration.persistence import init_db, make_engine, make_session_maker

    database = f"sqlite+aiosqlite:///{tmp_path / 'state.sqlite3'}"
    engine = make_engine(database)
    await init_db(engine)
    settings = Settings(database_url=database, skylink_webhook_secret=SECRET)
    service = ConnectivityService(
        {"skylink": _Gateway()}, make_session_maker(engine), settings=settings
    )
    order = await service.create_order(_intent("state"))

    confirmed = await service.apply_webhook_event(
        "skylink", "order.confirmed", order.client_reference
    )
    assert confirmed is not None
    assert confirmed.status is OrderStatus.ACCEPTED

    late = await service.apply_webhook_event("skylink", "order.confirmed", order.client_reference)
    assert late is not None
    assert late.status is OrderStatus.ACCEPTED
    # pending + submitted + two confirmations: every event is stored, even no-ops
    assert len(late.status_history) == 4

    async with session_scope(service.session_maker) as session:
        stored = await Repository(session).get_order(order.order_id)
        assert stored is not None
        assert stored.status is OrderStatus.ACCEPTED
