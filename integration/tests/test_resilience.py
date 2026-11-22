"""Fake-clock tests for retry, circuit breaker and the resilient HTTP client."""

from __future__ import annotations

import httpx
import pytest
from aintegrator_integration.config import Settings
from aintegrator_integration.domain import ProviderRateLimited, ProviderTimeout, ProviderUnavailable
from aintegrator_integration.resilience import (
    CircuitBreaker,
    CircuitOpenError,
    FakeClock,
    ResilientHttpClient,
    RetryPolicy,
    with_retry,
)


async def test_retry_uses_full_jitter_and_eventually_succeeds() -> None:
    clock = FakeClock()
    calls = 0

    async def call() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise ProviderUnavailable("down")
        return "ok"

    result = await with_retry(
        call,
        policy=RetryPolicy(max_attempts=3, base_delay_s=0.1, max_delay_s=0.4),
        clock=clock,
        should_retry=lambda exc: isinstance(exc, ProviderUnavailable),
        rng=lambda: 0.5,
    )
    assert result == "ok"
    assert calls == 3
    assert clock.sleeps == [0.05, 0.1]


async def test_retry_honors_retry_after() -> None:
    clock = FakeClock()

    async def call() -> str:
        raise ProviderRateLimited("slow down", retry_after=0.3)

    with pytest.raises(ProviderRateLimited):
        await with_retry(
            call,
            policy=RetryPolicy(max_attempts=2, base_delay_s=0.01, max_delay_s=0.02),
            clock=clock,
            should_retry=lambda exc: isinstance(exc, ProviderRateLimited),
            retry_after=lambda exc: (
                exc.retry_after if isinstance(exc, ProviderRateLimited) else None
            ),
            rng=lambda: 1.0,
        )
    assert clock.sleeps == [0.3]


async def test_retry_does_not_retry_forbidden_errors() -> None:
    calls = 0

    async def call() -> None:
        nonlocal calls
        calls += 1
        raise ValueError("bad input")

    with pytest.raises(ValueError):
        await with_retry(call, should_retry=lambda exc: False)
    assert calls == 1


async def test_breaker_opens_after_threshold_and_recovers_via_half_open() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker("fibernet", failure_threshold=2, reset_s=5, clock=clock)

    await breaker.acquire()
    await breaker.record_failure()
    assert breaker.state.value == "closed"
    await breaker.acquire()
    await breaker.record_failure()
    assert breaker.state.value == "open"

    with pytest.raises(CircuitOpenError):
        await breaker.acquire()

    clock.advance(5)
    await breaker.acquire()
    assert breaker.state.value == "half_open"
    with pytest.raises(CircuitOpenError):
        await breaker.acquire()
    await breaker.record_success()
    assert breaker.state.value == "closed"
    assert breaker.failures == 0


async def test_breaker_half_open_failure_reopens() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker("legacytel", failure_threshold=1, reset_s=1, clock=clock)
    await breaker.acquire()
    await breaker.record_failure()
    clock.advance(1)
    await breaker.acquire()
    await breaker.record_failure()
    assert breaker.state.value == "open"


def _client(handler: object, settings: Settings | None = None) -> ResilientHttpClient:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return ResilientHttpClient(
        "test-provider",
        settings or Settings(provider_max_attempts=2, provider_retry_base_s=0.01),
        client=httpx.AsyncClient(transport=transport),
        clock=FakeClock(),
    )


async def test_http_retries_idempotent_5xx_but_not_writes() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True})

    client = _client(handler)
    response = await client.request("GET", "http://provider/test", idempotent=True)
    assert response.status_code == 200
    assert attempts == 2

    attempts = 0
    client = _client(handler)
    with pytest.raises(ProviderUnavailable):
        await client.request("POST", "http://provider/test", idempotent=False)
    assert attempts == 1


async def test_http_refreshes_token_once_on_401() -> None:
    seen_tokens: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_tokens.append(request.headers.get("Authorization"))
        return (
            httpx.Response(200, json={"ok": True}) if len(seen_tokens) > 1 else httpx.Response(401)
        )

    refreshed = False

    async def refresh() -> str:
        nonlocal refreshed
        refreshed = True
        return "new-token"

    transport = httpx.MockTransport(handler)
    client = ResilientHttpClient(
        "fibernet",
        Settings(),
        client=httpx.AsyncClient(transport=transport),
        get_token=None,
        refresh_token=refresh,
    )
    await client.request("GET", "http://provider/test")
    assert refreshed
    assert seen_tokens == [None, "Bearer new-token"]


async def test_http_maps_timeouts_to_provider_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    client = _client(handler)
    with pytest.raises(ProviderTimeout):
        await client.request("POST", "http://provider/test", idempotent=False)
