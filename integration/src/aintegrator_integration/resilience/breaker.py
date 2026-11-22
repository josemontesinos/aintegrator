"""Per-provider circuit breaker with closed/open/half-open states."""

from __future__ import annotations

from enum import StrEnum

from aintegrator_integration.domain import AIntegratorError
from aintegrator_integration.resilience.clock import Clock, SystemClock

__all__ = ["BreakerState", "CircuitBreaker", "CircuitOpenError"]


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(AIntegratorError):
    code = "PROVIDER_UNAVAILABLE"
    retryable = True

    def __init__(self, provider: str) -> None:
        super().__init__(f"Circuit breaker for {provider} is open")


class CircuitBreaker:
    """Trips after ``failure_threshold`` consecutive failures.

    After ``reset_s`` in the open state a single probe is allowed through; a
    success closes the breaker, a failure reopens it. All timing goes through
    the injected clock.
    """

    def __init__(
        self,
        provider: str,
        *,
        failure_threshold: int = 5,
        reset_s: float = 5.0,
        clock: Clock | None = None,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")
        self.provider = provider
        self.failure_threshold = failure_threshold
        self.reset_s = reset_s
        self.clock: Clock = clock or SystemClock()
        self.state = BreakerState.CLOSED
        self.failures = 0
        self._opened_at = 0.0
        self._in_flight = 0

    async def acquire(self) -> None:
        """Raise :class:`CircuitOpenError` when the provider must not be called."""
        if self.state is BreakerState.OPEN:
            if self.clock.now() - self._opened_at < self.reset_s:
                raise CircuitOpenError(self.provider)
            self.state = BreakerState.HALF_OPEN
            self._in_flight = 0
        if self.state is BreakerState.HALF_OPEN and self._in_flight >= 1:
            raise CircuitOpenError(self.provider)
        self._in_flight += 1

    async def record_success(self) -> None:
        self._in_flight = max(0, self._in_flight - 1)
        self.failures = 0
        self.state = BreakerState.CLOSED

    async def record_failure(self) -> None:
        self._in_flight = max(0, self._in_flight - 1)
        if self.state is BreakerState.HALF_OPEN:
            self._open()
            return
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self._open()

    def _open(self) -> None:
        self.state = BreakerState.OPEN
        self._opened_at = self.clock.now()
