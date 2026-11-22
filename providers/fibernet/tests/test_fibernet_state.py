"""Pure state and catalog tests for Fibernet (no ASGI application involved)."""

from __future__ import annotations

import time

import pytest
from aintegrator_fibernet.catalog import (
    InvalidCursorError,
    address_key,
    build_offers,
    decode_cursor,
    encode_cursor,
)
from aintegrator_fibernet.models import OrderCreateRequest, OrderStatus
from aintegrator_fibernet.state import ProviderState, Settings, TokenBucket, TokenRecord


def test_token_bucket_exhaustion_and_refill() -> None:
    bucket = TokenBucket(capacity=2, refill_per_s=1.0, tokens=2.0, updated_at=0.0)
    assert bucket.consume(now=0.0) == (True, 0.0)
    assert bucket.consume(now=0.0) == (True, 0.0)

    allowed, wait_s = bucket.consume(now=0.0)
    assert allowed is False
    assert wait_s == pytest.approx(1.0)
    assert bucket.consume(now=1.0) == (True, 0.0)


def test_orders_advance_until_terminal_status() -> None:
    provider_state = ProviderState(Settings(seed=7))
    order = provider_state.create_order(
        request=OrderCreateRequest(
            offer_id="fibernet:F-000001",
            address={"postal_code": "10115", "street": "Fictional Lane", "number": "12"},
            customer={"name": "Grace Example", "email": "grace@example.invalid"},
        ),
        idempotency_key="key-1",
        body_hash="abc123",
    )
    assert order.status is OrderStatus.RECEIVED

    for expected in (OrderStatus.VALIDATING, OrderStatus.PROVISIONING, OrderStatus.ACTIVE):
        assert provider_state.advance_orders() == 1
        assert order.status is expected
        assert provider_state.counts()[expected.value] == 1
    assert provider_state.advance_orders() == 0

    order.status = OrderStatus.CANCELLED
    assert provider_state.advance_orders() == 0


def test_expired_token_is_rejected() -> None:
    provider_state = ProviderState()
    token, expires_in = provider_state.issue_token(0.5)
    assert expires_in == 1
    assert provider_state.verify_token(token) is True

    provider_state.tokens[token] = TokenRecord(expires_at=time.monotonic() - 1.0)
    assert provider_state.verify_token(token) is False
    assert token not in provider_state.tokens


def test_cursors_are_opaque_and_address_bound() -> None:
    key = address_key("10115", "Fictional Lane", 12)
    cursor = encode_cursor(key, 2)
    assert cursor != "2"
    assert decode_cursor(cursor, key) == 2

    other_key = address_key("10115", "Another Street", 12)
    with pytest.raises(InvalidCursorError):
        decode_cursor(cursor, other_key)
    with pytest.raises(InvalidCursorError):
        decode_cursor("not-a-cursor", key)


def test_offers_are_deterministic_and_bounded() -> None:
    first = build_offers(42, "10115", "Fictional Lane", "12")
    second = build_offers(42, "10115", "Fictional Lane", "12")
    assert first == second
    assert 3 <= len(first) <= 6
    assert len({offer.offer_id for offer in first}) == len(first)
    assert all(offer.monthly_price_eur_cents > 0 for offer in first)


def test_reset_clears_all_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIBERNET_ADVANCE_AFTER_S", "2.5")
    provider_state = ProviderState(Settings.from_env())
    assert provider_state.settings.advance_after_s == 2.5

    provider_state.issue_token()
    provider_state.rate_limit("caller")
    assert provider_state.control_snapshot()["tokens"] == 1
    provider_state.reset()
    cleared = provider_state.control_snapshot()
    assert cleared["tokens"] == 0
    assert cleared["orders"]["total"] == 0
    assert cleared["fault_profile"]["rules"] == []
