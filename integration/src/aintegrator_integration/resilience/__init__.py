"""Resilience primitives: injectable clock, retry, breaker, limits and HTTP client."""

from aintegrator_integration.resilience.breaker import (
    BreakerState,
    CircuitBreaker,
    CircuitOpenError,
)
from aintegrator_integration.resilience.clock import Clock, FakeClock, SystemClock
from aintegrator_integration.resilience.http import AuditSink, ResilientHttpClient
from aintegrator_integration.resilience.limits import ConcurrencyLimiter
from aintegrator_integration.resilience.retry import RetryPolicy, with_retry

__all__ = [
    "AuditSink",
    "BreakerState",
    "CircuitBreaker",
    "CircuitOpenError",
    "Clock",
    "ConcurrencyLimiter",
    "FakeClock",
    "ResilientHttpClient",
    "RetryPolicy",
    "SystemClock",
    "with_retry",
]
