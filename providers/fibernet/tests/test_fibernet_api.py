"""HTTP-level tests for the Fibernet provider using ASGI transport only.

No service, socket or external clock is required: the app is driven in-process.
"""

from __future__ import annotations

import asyncio
import base64
import importlib
import json
import time
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any

import pytest
from aintegrator_fibernet.state import state as provider_state
from httpx import ASGITransport, AsyncClient

fibernet_app = importlib.import_module("aintegrator_fibernet.app")

COVERAGE_QUERY = {"postal_code": "10115", "street": "Fictional Lane", "number": "12"}
ORDER_BODY = {
    "offer_id": "fibernet:F-000123",
    "address": {"postal_code": "10115", "street": "Fictional Lane", "number": "12"},
    "customer": {"name": "Ada Example", "email": "ada@example.invalid"},
}


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Yield an in-process HTTP client with the app lifespan running."""
    provider_state.reset()
    async with (
        fibernet_app.app.router.lifespan_context(fibernet_app.app),
        AsyncClient(
            transport=ASGITransport(app=fibernet_app.app),
            base_url="http://fibernet.test",
        ) as http_client,
    ):
        yield http_client
    provider_state.reset()


@pytest.fixture
async def fast_client() -> AsyncIterator[AsyncClient]:
    """Yield a client whose background advance interval is very short."""
    provider_state.reset()
    original_settings = provider_state.settings
    provider_state.settings = replace(original_settings, advance_after_s=0.01)
    async with (
        fibernet_app.app.router.lifespan_context(fibernet_app.app),
        AsyncClient(
            transport=ASGITransport(app=fibernet_app.app),
            base_url="http://fibernet.test",
        ) as http_client,
    ):
        yield http_client
    provider_state.settings = original_settings
    provider_state.reset()


async def authenticate(client: AsyncClient, **overrides: str) -> dict[str, str]:
    """Obtain a bearer token and return the authorization header."""
    data = {
        "grant_type": "client_credentials",
        "client_id": "fibernet-client",
        "client_secret": "fibernet-secret",
        **overrides,
    }
    response = await client.post("/oauth/token", data=data)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["token_type"] == "bearer"
    return {"Authorization": f"Bearer {payload['access_token']}"}


async def create_order(
    client: AsyncClient,
    headers: dict[str, str],
    key: str = "key-1",
) -> dict[str, Any]:
    """Create one fictional order and return its JSON representation."""
    response = await client.post(
        "/v1/orders",
        json=ORDER_BODY,
        headers={**headers, "Idempotency-Key": key},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_token_endpoint_form_and_basic_auth(client: AsyncClient) -> None:
    headers = await authenticate(client)
    assert headers["Authorization"].startswith("Bearer fibernet_")

    basic = base64.b64encode(b"fibernet-client:fibernet-secret").decode()
    response = await client.post(
        "/oauth/token",
        data={"grant_type": "client_credentials"},
        headers={"Authorization": f"Basic {basic}"},
    )
    assert response.status_code == 200
    assert response.json()["expires_in"] == 300


async def test_missing_and_expired_tokens_are_rejected(client: AsyncClient) -> None:
    unauthenticated = await client.get("/v1/coverage", params=COVERAGE_QUERY)
    assert unauthenticated.status_code == 401
    assert unauthenticated.json() == {"error": "invalid_token"}

    bogus = await client.get(
        "/v1/coverage",
        params=COVERAGE_QUERY,
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert bogus.status_code == 401
    assert bogus.json() == {"error": "invalid_token"}


async def test_token_expiry_fault_shortens_lifetime(client: AsyncClient) -> None:
    profile = {
        "seed": 1,
        "rules": [
            {
                "operation": "token",
                "kind": "token_expiry",
                "probability": 1.0,
                "params": {"ttl_s": 0.05},
            }
        ],
    }
    assert (await client.post("/_control/faults", json=profile)).status_code == 200

    token_response = await client.post("/oauth/token", data={"grant_type": "client_credentials"})
    assert token_response.status_code == 200
    assert token_response.json()["expires_in"] == 1
    headers = {"Authorization": f"Bearer {token_response.json()['access_token']}"}
    healthy = await client.get("/v1/coverage", params=COVERAGE_QUERY, headers=headers)
    assert healthy.status_code == 200

    await asyncio.sleep(0.08)
    expired = await client.get("/v1/coverage", params=COVERAGE_QUERY, headers=headers)
    assert expired.status_code == 401
    assert expired.json() == {"error": "invalid_token"}


async def test_coverage_is_deterministic_and_cursor_paginated(client: AsyncClient) -> None:
    headers = await authenticate(client)
    first = await client.get("/v1/coverage", params=COVERAGE_QUERY, headers=headers)
    assert first.status_code == 200
    payload = first.json()
    assert set(payload) == {"offers", "next_cursor"}
    assert 3 <= len(payload["offers"]) <= 6

    offer = payload["offers"][0]
    required = {
        "offer_id",
        "plan_name",
        "technology",
        "down_mbps",
        "up_mbps",
        "monthly_price_eur_cents",
        "install_fee_eur_cents",
        "lead_time_days",
        "valid_until",
        "plan_description",
    }
    assert required <= offer.keys()
    assert offer["offer_id"].startswith("fibernet:F-")
    assert offer["technology"] in {"FTTH", "VDSL", "FWA_5G"}
    assert isinstance(offer["monthly_price_eur_cents"], int)
    assert offer["valid_until"].endswith("Z")

    repeated = await client.get("/v1/coverage", params=COVERAGE_QUERY, headers=headers)
    assert repeated.json() == payload

    collected: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(10):
        query = {**COVERAGE_QUERY, "limit": 2}
        if cursor:
            query["cursor"] = cursor
        page_response = await client.get("/v1/coverage", params=query, headers=headers)
        assert page_response.status_code == 200
        page = page_response.json()
        collected.extend(page["offers"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    else:
        pytest.fail("coverage pagination did not terminate")
    assert collected == payload["offers"]


async def test_coverage_rejects_out_of_range_limit(client: AsyncClient) -> None:
    headers = await authenticate(client)
    for limit in (0, 51):
        response = await client.get(
            "/v1/coverage",
            params={**COVERAGE_QUERY, "limit": limit},
            headers=headers,
        )
        assert response.status_code == 422
        assert response.headers["content-type"].startswith("application/problem+json")


async def test_unknown_order_returns_problem(client: AsyncClient) -> None:
    headers = await authenticate(client)
    response = await client.get("/v1/orders/A-000000", headers=headers)
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["status"] == 404


async def test_coverage_rejects_foreign_cursor(client: AsyncClient) -> None:
    headers = await authenticate(client)
    first = await client.get("/v1/coverage", params={**COVERAGE_QUERY, "limit": 2}, headers=headers)
    cursor = first.json()["next_cursor"]
    assert cursor is not None

    other_address = {**COVERAGE_QUERY, "street": "Different Street"}
    response = await client.get(
        "/v1/coverage",
        params={**other_address, "cursor": cursor},
        headers=headers,
    )
    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_idempotent_replay_and_conflict(client: AsyncClient) -> None:
    headers = await authenticate(client)
    first = await client.post(
        "/v1/orders",
        json=ORDER_BODY,
        headers={**headers, "Idempotency-Key": "idem-key"},
    )
    assert first.status_code == 201
    created = first.json()
    assert created["status"] == "received"
    assert created["order_id"].startswith("A-")
    assert created["id"] == created["order_id"]

    replay = await client.post(
        "/v1/orders",
        json=ORDER_BODY,
        headers={**headers, "Idempotency-Key": "idem-key"},
    )
    assert replay.status_code == 201
    assert replay.headers["Idempotent-Replay"] == "true"
    assert replay.json() == created

    conflict = await client.post(
        "/v1/orders",
        json={**ORDER_BODY, "offer_id": "fibernet:F-999999"},
        headers={**headers, "Idempotency-Key": "idem-key"},
    )
    assert conflict.status_code == 422
    assert conflict.headers["content-type"].startswith("application/problem+json")
    assert {"type", "title", "status", "detail"} <= conflict.json().keys()

    no_key = await client.post("/v1/orders", json=ORDER_BODY, headers=headers)
    assert no_key.status_code == 422
    assert no_key.headers["content-type"].startswith("application/problem+json")


async def test_cancel_allowed_then_too_late(client: AsyncClient) -> None:
    headers = await authenticate(client)
    order = await create_order(client, headers, key="cancel-early")
    cancelled = await client.post(f"/v1/orders/{order['order_id']}/cancel", headers=headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    second_cancel = await client.post(f"/v1/orders/{order['order_id']}/cancel", headers=headers)
    assert second_cancel.status_code == 409
    assert second_cancel.headers["content-type"].startswith("application/problem+json")

    late_order = await create_order(client, headers, key="cancel-late")
    assert (await client.post("/_control/advance")).json()["advanced"] >= 1
    assert (await client.post("/_control/advance")).json()["advanced"] >= 1
    too_late = await client.post(f"/v1/orders/{late_order['order_id']}/cancel", headers=headers)
    assert too_late.status_code == 409


async def test_status_advances_through_control_endpoint(client: AsyncClient) -> None:
    headers = await authenticate(client)
    order = await create_order(client, headers)

    async def status() -> str:
        response = await client.get(f"/v1/orders/{order['order_id']}", headers=headers)
        assert response.status_code == 200
        return response.json()["status"]

    assert await status() == "received"
    for expected in ("validating", "provisioning", "active"):
        assert (await client.post("/_control/advance")).json()["advanced"] >= 1
        assert await status() == expected
    assert (await client.post("/_control/advance")).json()["advanced"] == 0


async def test_background_task_advances_orders(fast_client: AsyncClient) -> None:
    assert provider_state.advance_task is not None
    headers = await authenticate(fast_client)
    order = await create_order(fast_client, headers)

    deadline = time.monotonic() + 2.0
    status = "received"
    while time.monotonic() < deadline:
        response = await fast_client.get(f"/v1/orders/{order['order_id']}", headers=headers)
        status = response.json()["status"]
        if status == "active":
            break
        await asyncio.sleep(0.02)
    assert status == "active"


async def test_rate_limit_returns_problem_with_retry_after(client: AsyncClient) -> None:
    headers = await authenticate(client)
    for bucket in provider_state.buckets.values():
        bucket.tokens = 0.0

    response = await client.get("/v1/coverage", params=COVERAGE_QUERY, headers=headers)
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) >= 1
    assert response.headers["content-type"].startswith("application/problem+json")


@pytest.mark.parametrize(
    ("kind", "params", "expected_status"),
    [
        ("latency", {"fixed_ms": 20, "jitter_ms": 0}, 200),
        ("http_error", {"status": 503}, 503),
        ("malformed_response", {}, 200),
        ("partial_response", {}, 200),
        ("rate_limit", {"retry_after_s": 7}, 429),
    ],
)
async def test_coverage_faults(
    client: AsyncClient,
    kind: str,
    params: dict[str, int],
    expected_status: int,
) -> None:
    profile = {
        "seed": 3,
        "rules": [{"operation": "coverage", "kind": kind, "probability": 1.0, "params": params}],
    }
    assert (await client.post("/_control/faults", json=profile)).status_code == 200
    headers = await authenticate(client)

    started = time.monotonic()
    response = await client.get("/v1/coverage", params=COVERAGE_QUERY, headers=headers)
    elapsed = time.monotonic() - started
    assert response.status_code == expected_status

    if kind == "latency":
        assert elapsed >= 0.015
    elif kind == "http_error":
        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.json()["status"] == 503
    elif kind == "malformed_response":
        with pytest.raises(json.JSONDecodeError):
            response.json()
    elif kind == "partial_response":
        offer = response.json()["offers"][0]
        assert {"valid_until", "plan_description"}.isdisjoint(offer)
        assert "monthly_price_eur_cents" in offer
    elif kind == "rate_limit":
        assert response.headers["Retry-After"] == "7"
        assert response.headers["content-type"].startswith("application/problem+json")


async def test_timeout_fault_hangs_and_is_patchable(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fibernet_app, "TIMEOUT_HANG_SECONDS", 0.5)
    profile = {
        "seed": 4,
        "rules": [{"operation": "coverage", "kind": "timeout", "probability": 1.0}],
    }
    assert (await client.post("/_control/faults", json=profile)).status_code == 200
    headers = await authenticate(client)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            client.get("/v1/coverage", params=COVERAGE_QUERY, headers=headers),
            timeout=0.05,
        )


async def test_order_create_fault_does_not_store_order(client: AsyncClient) -> None:
    profile = {
        "seed": 5,
        "rules": [
            {
                "operation": "orders.create",
                "kind": "http_error",
                "probability": 1.0,
                "params": {"status": 502},
            }
        ],
    }
    assert (await client.post("/_control/faults", json=profile)).status_code == 200
    headers = await authenticate(client)
    response = await client.post(
        "/v1/orders",
        json=ORDER_BODY,
        headers={**headers, "Idempotency-Key": "faulty-key"},
    )
    assert response.status_code == 502
    assert provider_state.counts()["total"] == 0
    assert "faulty-key" not in provider_state.idempotency


async def test_control_endpoints_reset_and_report_state(client: AsyncClient) -> None:
    initial = (await client.get("/_control/state")).json()
    assert initial["orders"]["total"] == 0
    assert initial["fault_profile"]["rules"] == []

    headers = await authenticate(client)
    await create_order(client, headers)
    profile = {
        "seed": 9,
        "rules": [{"operation": "orders.get", "kind": "latency", "probability": 0.25}],
    }
    assert (await client.post("/_control/faults", json=profile)).status_code == 200

    snapshot = (await client.get("/_control/state")).json()
    assert snapshot["orders"]["total"] == 1
    assert snapshot["fault_profile"]["seed"] == 9
    assert snapshot["fault_counters"]["orders.get"] == 0

    advanced = await client.post("/_control/advance")
    assert advanced.json() == {"advanced": 1}
    assert (await client.get("/_control/state")).json()["orders"]["validating"] == 1

    reset = await client.post("/_control/reset")
    assert reset.status_code == 200
    cleared = (await client.get("/_control/state")).json()
    assert cleared["orders"]["total"] == 0
    assert cleared["fault_profile"]["rules"] == []
    assert cleared["tokens"] == 0
