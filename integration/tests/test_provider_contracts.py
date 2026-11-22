"""Adapter contract tests against the running simulated providers.

Run with ``make contract`` after ``make up``. These tests are deselected from the
default unit-test run by the root pytest markers.
"""

from __future__ import annotations

import json
import time
from uuid import uuid4

import httpx
import pytest
from aintegrator_integration.adapters import FibernetAdapter, LegacytelAdapter, SkylinkAdapter
from aintegrator_integration.config import Settings
from aintegrator_integration.domain import (
    Address,
    Customer,
    Offer,
    OrderIntent,
    ProviderProtocolError,
    ProviderRateLimited,
    ProviderUnavailable,
    UnsupportedOperation,
)
from aintegrator_integration.webhooks import sign_body

pytestmark = pytest.mark.contract

ADDRESS = Address(
    street="Fictional Street", number="12", postal_code="28001", city="Madrid", country="ES"
)
CUSTOMER = Customer(name="Ada Example", email="ada@example.invalid")


def _reset(base_url: str) -> None:
    with httpx.Client(timeout=5) as client:
        response = client.post(f"{base_url}/_control/reset")
        response.raise_for_status()


def _set_fault(base_url: str, operation: str, kind: str, **params: object) -> None:
    profile = {
        "seed": 7,
        "rules": [{"operation": operation, "kind": kind, "probability": 1.0, "params": params}],
    }
    with httpx.Client(timeout=5) as client:
        response = client.post(f"{base_url}/_control/faults", json=profile)
        response.raise_for_status()


def _offer(provider: str, offer_id: str) -> Offer:
    from aintegrator_integration.domain import Money, Technology

    return Offer(
        offer_id=offer_id,
        provider=provider,
        plan_name="Contract Plan",
        technology=Technology.FTTH,
        down_mbps=300,
        up_mbps=100,
        monthly_price=Money(amount_minor=4990),
        install_fee=Money(amount_minor=0),
        lead_time_days=3,
    )


def _intent(provider: str, offer_id: str) -> OrderIntent:
    return OrderIntent(
        offer=_offer(provider, offer_id),
        address=ADDRESS,
        customer=CUSTOMER,
        idempotency_key=f"contract-{uuid4()}",
        client_reference=f"contract-{uuid4()}",
    )


async def test_fibernet_contract() -> None:
    settings = Settings()
    _reset(settings.fibernet_base_url)
    adapter = FibernetAdapter(settings)
    try:
        eligibility = await adapter.check_eligibility(ADDRESS)
        assert eligibility.status.value == "OK"
        assert eligibility.offers
        assert not any(
            offer.offer_id.startswith("fibernet:fibernet:") for offer in eligibility.offers
        )

        offer = eligibility.offers[0]
        intent = _intent("fibernet", offer.offer_id)
        created = await adapter.create_order(intent)
        assert created.provider_order_id
        assert created.idempotency_key == intent.idempotency_key
        assert created.status.value == "SUBMITTED"

        replayed = await adapter.find_by_reference(
            intent.client_reference, intent=intent, order_id=created.order_id
        )
        assert replayed is not None
        assert replayed.provider_order_id == created.provider_order_id

        cancelled = await adapter.cancel_order(
            str(created.provider_order_id), intent=intent, order_id=created.order_id
        )
        assert cancelled.status.value == "CANCELLED"
    finally:
        await adapter.aclose()


async def test_fibernet_malformed_response_is_rejected() -> None:
    settings = Settings()
    _reset(settings.fibernet_base_url)
    _set_fault(settings.fibernet_base_url, "coverage", "malformed_response")
    adapter = FibernetAdapter(settings)
    try:
        with pytest.raises(ProviderProtocolError):
            await adapter.check_eligibility(ADDRESS)
    finally:
        await adapter.aclose()
        _reset(settings.fibernet_base_url)


async def test_legacytel_contract() -> None:
    settings = Settings()
    _reset(settings.legacytel_base_url)
    adapter = LegacytelAdapter(settings)
    eligibility = await adapter.check_eligibility(ADDRESS)
    assert eligibility.status.value == "OK"
    assert eligibility.offers
    assert all(offer.monthly_price.amount_minor > 0 for offer in eligibility.offers)

    offer = eligibility.offers[0]
    intent = _intent("legacytel", offer.offer_id)
    created = await adapter.create_order(intent)
    assert created.provider_order_id
    assert created.status.value == "SUBMITTED"

    fetched = await adapter.get_order(
        str(created.provider_order_id), intent=intent, order_id=created.order_id
    )
    assert fetched.provider_order_id == created.provider_order_id

    found = await adapter.find_by_reference(
        intent.client_reference, intent=intent, order_id=created.order_id
    )
    assert found is not None
    assert found.provider_order_id == created.provider_order_id

    with pytest.raises(UnsupportedOperation):
        await adapter.cancel_order(
            str(created.provider_order_id), intent=intent, order_id=created.order_id
        )


async def test_legacytel_http_error_is_rejected() -> None:
    settings = Settings()
    _reset(settings.legacytel_base_url)
    _set_fault(settings.legacytel_base_url, "check_availability", "http_error", status=502)
    adapter = LegacytelAdapter(settings)
    try:
        with pytest.raises(ProviderUnavailable):
            await adapter.check_eligibility(ADDRESS)
    finally:
        _reset(settings.legacytel_base_url)


async def test_skylink_contract() -> None:
    settings = Settings()
    _reset(settings.skylink_base_url)
    adapter = SkylinkAdapter(settings)
    try:
        eligibility = await adapter.check_eligibility(ADDRESS)
        assert eligibility.status.value == "OK"
        assert eligibility.offers
        assert all(offer.offer_id.startswith("skylink:") for offer in eligibility.offers)

        offer = eligibility.offers[0]
        intent = _intent("skylink", offer.offer_id)
        created = await adapter.create_order(intent)
        assert created.provider_order_id
        assert created.status.value == "SUBMITTED"

        fetched = await adapter.get_order(
            str(created.provider_order_id), intent=intent, order_id=created.order_id
        )
        assert fetched.provider_order_id == created.provider_order_id

        found = await adapter.find_by_reference(
            intent.client_reference, intent=intent, order_id=created.order_id
        )
        assert found is not None
        assert found.provider_order_id == created.provider_order_id

        with pytest.raises(UnsupportedOperation):
            await adapter.cancel_order(
                str(created.provider_order_id), intent=intent, order_id=created.order_id
            )
    finally:
        await adapter.aclose()


def test_webhook_receiver_contract() -> None:
    settings = Settings()
    url = f"{settings.webhook_receiver_base_url}/webhooks/skylink"
    event_id = f"contract-{uuid4()}"
    payload = {
        "eventId": event_id,
        "type": "order.confirmed",
        "sequence": 1,
        "occurredAt": 1_700_000_000_000,
        "data": {"orderId": "C-CONTRACT"},
    }
    raw = json.dumps(payload).encode()
    header = {
        "X-Skylink-Signature": sign_body(settings.skylink_webhook_secret, raw, int(time.time()))
    }
    with httpx.Client(timeout=5) as client:
        first = client.post(url, content=raw, headers=header)
        second = client.post(url, content=raw, headers=header)
    assert first.status_code == 200
    assert first.json()["status"] == "accepted"
    assert second.json()["status"] == "duplicate"

    with httpx.Client(timeout=5) as client:
        bad = client.post(url, content=raw, headers={"X-Skylink-Signature": "t=1,v1=deadbeef"})
        stale = client.post(
            url,
            content=raw,
            headers={
                "X-Skylink-Signature": sign_body(
                    settings.skylink_webhook_secret, raw, int(time.time()) - 400
                )
            },
        )
    assert bad.status_code == 401
    assert stale.status_code == 401


async def test_fibernet_rate_limit_is_reported() -> None:
    settings = Settings()
    _reset(settings.fibernet_base_url)
    _set_fault(settings.fibernet_base_url, "coverage", "rate_limit", retry_after=0)
    adapter = FibernetAdapter(settings)
    try:
        with pytest.raises(ProviderRateLimited):
            await adapter.check_eligibility(ADDRESS)
    finally:
        await adapter.aclose()
        _reset(settings.fibernet_base_url)
