"""Service-backed workflows against the running simulated providers.

These tests are deselected from the default unit run. Start the services with
``make up`` and run them with ``pytest -m 'contract or integration'``.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from uuid import uuid4

import httpx
import pytest
from aintegrator_integration.config import Settings
from aintegrator_integration.domain import (
    Address,
    Customer,
    EligibilityStatus,
    Order,
    OrderIntent,
    OrderStatus,
)
from aintegrator_integration.factory import build_gateways
from aintegrator_integration.persistence import (
    init_db,
    make_engine,
    make_session_maker,
)
from aintegrator_integration.reconcile import reconcile_once
from aintegrator_integration.service import ConnectivityService
from aintegrator_integration.webhooks import create_app, sign_body
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

ADDRESS = Address(
    street="Fictional Street", number="12", postal_code="28001", city="Madrid", country="ES"
)
CUSTOMER = Customer(name="Ada Example", email="ada@example.invalid")
SECRET = "skylink-secret"


def _reset_providers(settings: Settings) -> None:
    for base_url in (
        settings.fibernet_base_url,
        settings.legacytel_base_url,
        settings.skylink_base_url,
    ):
        with httpx.Client(timeout=5) as client:
            client.post(f"{base_url}/_control/reset").raise_for_status()


@pytest.fixture
async def workflow(tmp_path) -> AsyncIterator[tuple[Settings, ConnectivityService, TestClient]]:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'workflow.sqlite3'}",
        skylink_webhook_secret=SECRET,
        reconcile_after_s=0,
        write_rate_limit_per_minute=100,
    )
    engine = make_engine(settings.database_url)
    await init_db(engine)
    session_maker = make_session_maker(engine)
    service = ConnectivityService(
        build_gateways(settings, session_maker=session_maker),
        session_maker,
        settings=settings,
    )
    client = TestClient(create_app(service, settings))
    _reset_providers(settings)
    try:
        yield settings, service, client
    finally:
        await service.aclose()
        await engine.dispose()
        _reset_providers(settings)


def _post_webhook(client: TestClient, payload: dict[str, object], *, secret: str = SECRET):
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    header = sign_body(secret, raw, int(time.time()))
    return client.post("/webhooks/skylink", content=raw, headers={"X-Skylink-Signature": header})


async def _skylink_order(service: ConnectivityService) -> Order:
    results = await service.check_eligibility(ADDRESS)
    skylink = next(result for result in results if result.provider == "skylink")
    assert skylink.status is EligibilityStatus.OK and skylink.offers
    intent = OrderIntent(
        offer=skylink.offers[0],
        address=ADDRESS,
        customer=CUSTOMER,
        idempotency_key=f"workflow-{uuid4()}",
        client_reference=f"workflow-{uuid4()}",
    )
    return await service.create_order(intent)


async def test_signed_webhooks_dedupe_and_never_regress(workflow) -> None:
    _, service, client = workflow
    order = await _skylink_order(service)
    reference = str(order.provider_order_id)

    confirmed = {
        "eventId": f"evt-{uuid4()}",
        "type": "order.confirmed",
        "sequence": 1,
        "occurredAt": int(time.time() * 1000),
        "orderReference": reference,
        "data": {"orderId": reference},
    }
    assert _post_webhook(client, confirmed).json()["status"] == "accepted"
    assert (await service.get_order(order.order_id)).status is OrderStatus.ACCEPTED
    assert _post_webhook(client, confirmed).json()["status"] == "duplicate"

    working = {
        **confirmed,
        "eventId": f"evt-{uuid4()}",
        "type": "order.working",
        "sequence": 2,
    }
    assert _post_webhook(client, working).json()["status"] == "accepted"
    assert (await service.get_order(order.order_id)).status is OrderStatus.IN_PROGRESS

    stale = {
        **confirmed,
        "eventId": f"evt-{uuid4()}",
        "sequence": 0,
        "occurredAt": int(time.time() * 1000) - 60_000,
    }
    assert _post_webhook(client, stale).json()["status"] == "accepted"
    final = await service.get_order(order.order_id)
    assert final.status is OrderStatus.IN_PROGRESS
    # pending, submitted, confirmed, working and the stale confirmation are all recorded
    assert len(final.status_history) == 5


async def test_reconciler_recovers_when_every_webhook_is_dropped(workflow) -> None:
    settings, service, _ = workflow
    order = await _skylink_order(service)
    assert (await service.get_order(order.order_id)).status is OrderStatus.SUBMITTED

    # Move the provider to a terminal state but deliver no webhook at all.
    with httpx.Client(timeout=5) as client:
        client.post(f"{settings.skylink_base_url}/_control/advance").raise_for_status()
    assert (await service.get_order(order.order_id)).status is OrderStatus.SUBMITTED

    changes = await reconcile_once(service, settings)
    assert any(str(order.order_id) in change for change in changes)
    recovered = await service.get_order(order.order_id)
    assert recovered.status in (OrderStatus.COMPLETED, OrderStatus.REJECTED)
