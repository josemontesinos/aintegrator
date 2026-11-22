"""HTTP client combining timeouts, retry, circuit breaking and concurrency limits.

Only safe or explicitly idempotent calls should set ``idempotent=True``; the
service makes that decision, never the client. A 401 triggers exactly one token
refresh per logical request.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from aintegrator_observability import Tracer, get_tracer, trace_headers, traced

from aintegrator_integration.config import Settings
from aintegrator_integration.domain import ProviderRateLimited, ProviderTimeout, ProviderUnavailable
from aintegrator_integration.resilience.breaker import CircuitBreaker
from aintegrator_integration.resilience.clock import Clock, SystemClock
from aintegrator_integration.resilience.limits import ConcurrencyLimiter
from aintegrator_integration.resilience.retry import RetryPolicy, with_retry

__all__ = ["AuditSink", "ResilientHttpClient"]

AuditSink = Callable[[str, str, str, float, str, str | None], Awaitable[None]]
TokenGetter = Callable[[], Awaitable[str | None]]


class ResilientHttpClient:
    """Thin httpx wrapper with the resilience primitives wired together."""

    def __init__(
        self,
        provider: str,
        settings: Settings,
        *,
        clock: Clock | None = None,
        client: httpx.AsyncClient | None = None,
        breaker: CircuitBreaker | None = None,
        limiter: ConcurrencyLimiter | None = None,
        get_token: TokenGetter | None = None,
        refresh_token: TokenGetter | None = None,
        audit_sink: AuditSink | None = None,
        tracer: Tracer | None = None,
    ) -> None:
        self.provider = provider
        self.tracer: Tracer = tracer or get_tracer()
        self.settings = settings
        self.clock: Clock = clock or SystemClock()
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None
        self.breaker = breaker or CircuitBreaker(
            provider,
            failure_threshold=settings.provider_breaker_threshold,
            reset_s=settings.provider_breaker_reset_s,
            clock=self.clock,
        )
        self.limiter = limiter or ConcurrencyLimiter(settings.provider_concurrency_limit)
        self._get_token = get_token
        self._refresh_token = refresh_token
        self._audit_sink = audit_sink
        self._retry_policy = RetryPolicy(
            max_attempts=settings.provider_max_attempts,
            base_delay_s=settings.provider_retry_base_s,
            max_delay_s=settings.provider_retry_max_s,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @traced(
        "integration.http",
        attributes=lambda self, method, url, **kwargs: {
            "provider": self.provider,
            "operation": kwargs.get("operation", "http"),
            "method": method,
            "route": url.split("?", 1)[0],
            "idempotent": bool(kwargs.get("idempotent", False)),
        },
    )
    async def request(
        self,
        method: str,
        url: str,
        *,
        operation: str = "http",
        headers: dict[str, str] | None = None,
        idempotent: bool = False,
        run_id: str | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Perform a request, applying all configured resilience controls."""
        await self.breaker.acquire()
        started = self.clock.now()
        refreshed = False
        timeout = httpx.Timeout(
            connect=self.settings.provider_connect_timeout_s,
            read=self.settings.provider_timeout_s,
            write=self.settings.provider_timeout_s,
            pool=self.settings.provider_timeout_s,
        )
        kwargs.setdefault("timeout", timeout)

        async def attempt() -> httpx.Response:
            nonlocal refreshed
            request_headers = dict(headers or {})
            for key, value in trace_headers().items():
                request_headers.setdefault(key, value)
            if self._get_token is not None:
                token = await self._get_token()
                if token:
                    request_headers["Authorization"] = f"Bearer {token}"
            response = await self._client.request(method, url, headers=request_headers, **kwargs)
            if response.status_code == 401 and not refreshed and self._refresh_token is not None:
                refreshed = True
                new_token = await self._refresh_token()
                if new_token:
                    request_headers["Authorization"] = f"Bearer {new_token}"
                    response = await self._client.request(
                        method, url, headers=request_headers, **kwargs
                    )
            if response.status_code == 429:
                after = response.headers.get("Retry-After")
                raise ProviderRateLimited(
                    f"{self.provider} rate limited {operation}",
                    retry_after=float(after) if after and after.isdigit() else 1.0,
                )
            if response.status_code >= 500:
                raise ProviderUnavailable(
                    f"{self.provider} returned {response.status_code} for {operation}"
                )
            return response

        def should_retry(exc: Exception) -> bool:
            # A connect timeout happens before a request can be processed, so it
            # is safe to retry even for writes. Everything else needs an explicit
            # idempotency guarantee from the caller.
            if isinstance(exc, httpx.ConnectTimeout):
                return True
            if not idempotent:
                return False
            return isinstance(
                exc,
                (
                    httpx.TimeoutException,
                    httpx.TransportError,
                    ProviderRateLimited,
                    ProviderUnavailable,
                ),
            )

        def retry_after(exc: Exception) -> float | None:
            return exc.retry_after if isinstance(exc, ProviderRateLimited) else None

        try:
            async with self.limiter:
                response = await with_retry(
                    attempt,
                    policy=self._retry_policy,
                    clock=self.clock,
                    should_retry=should_retry,
                    retry_after=retry_after,
                )
        except httpx.TimeoutException as exc:
            outcome = ProviderTimeout.code
            await self._audit(started, operation, url, outcome, run_id)
            await self.breaker.record_failure()
            raise ProviderTimeout(f"{self.provider} timed out on {operation}") from exc
        except Exception as exc:
            outcome = getattr(exc, "code", "PROVIDER_UNAVAILABLE")
            await self._audit(started, operation, url, str(outcome), run_id)
            await self.breaker.record_failure()
            raise
        else:
            await self.breaker.record_success()
            await self._audit(started, operation, url, str(response.status_code), run_id)
            return response

    async def _audit(
        self,
        started: float,
        operation: str,
        url: str,
        outcome: str,
        run_id: str | None,
    ) -> None:
        if self._audit_sink is None:
            return
        latency_ms = (self.clock.now() - started) * 1000
        await self._audit_sink(self.provider, operation, url, latency_ms, outcome, run_id)
