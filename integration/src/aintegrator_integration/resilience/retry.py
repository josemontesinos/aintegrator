"""Exponential backoff with full jitter, with optional Retry-After support."""

from __future__ import annotations

import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from aintegrator_integration.resilience.clock import Clock, SystemClock

__all__ = ["RetryPolicy", "with_retry"]


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_s: float = 0.05
    max_delay_s: float = 0.5
    respect_retry_after: bool = True

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")


async def with_retry[T](
    call: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy | None = None,
    clock: Clock | None = None,
    should_retry: Callable[[Exception], bool],
    retry_after: Callable[[Exception], float | None] | None = None,
    rng: Callable[[], float] | None = None,
) -> T:
    """Run ``call`` with retries; never retries forbidden operations by itself.

    The caller supplies ``should_retry``, which keeps reads/idempotent writes
    explicit: a non-idempotent provider call must opt in deliberately.
    """
    retry_policy = policy or RetryPolicy()
    timer = clock or SystemClock()
    random_value = rng or random.random
    last_error: Exception | None = None

    for attempt in range(retry_policy.max_attempts):
        try:
            return await call()
        except Exception as exc:
            last_error = exc
            if attempt + 1 >= retry_policy.max_attempts or not should_retry(exc):
                raise
            delay = min(
                retry_policy.max_delay_s,
                retry_policy.base_delay_s * (2**attempt),
            )
            if retry_policy.respect_retry_after and retry_after is not None:
                after = retry_after(exc)
                if after is not None:
                    delay = max(0.0, after)
            await timer.sleep(random_value() * delay)

    assert last_error is not None  # pragma: no cover - loop always raises or returns
    raise last_error
