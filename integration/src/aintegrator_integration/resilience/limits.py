"""Per-provider concurrency limiting."""

from __future__ import annotations

import asyncio
from types import TracebackType

__all__ = ["ConcurrencyLimiter"]


class ConcurrencyLimiter:
    """Async context manager backed by a semaphore with an optional wait timeout."""

    def __init__(self, limit: int = 10, *, wait_timeout_s: float | None = None) -> None:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        self._semaphore = asyncio.Semaphore(limit)
        self._wait_timeout_s = wait_timeout_s

    async def __aenter__(self) -> ConcurrencyLimiter:
        if self._wait_timeout_s is None:
            await self._semaphore.acquire()
        else:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=self._wait_timeout_s)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._semaphore.release()
