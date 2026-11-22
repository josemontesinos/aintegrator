"""Process-local state for the simulated Fibernet provider.

The state object is intentionally in-memory: the provider is a test double, not a
production service. All mutations happen without awaiting, so the asyncio lock is
only needed to keep multi-step request handling atomic.
"""

from __future__ import annotations

import asyncio
import math
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from aintegrator_provider_common import FaultInjector, digest_int

from .models import (
    NEXT_STATUS,
    Order,
    OrderCreateRequest,
    OrderStatus,
    iso_utc,
    utc_now,
)

__all__ = [
    "OPERATIONS",
    "IdempotencyRecord",
    "ProviderState",
    "Settings",
    "TokenBucket",
    "TokenRecord",
    "state",
]

OPERATIONS = ("token", "coverage", "orders.create", "orders.get", "orders.cancel")
TOKEN_TTL_S = 300.0


def _float_env(name: str, default: float) -> float:
    """Read a positive float from the environment, falling back on bad input."""
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _int_env(name: str, default: int) -> int:
    """Read an integer from the environment, falling back on bad input."""
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime configuration, sourced from ``FIBERNET_*`` environment variables."""

    client_id: str = "fibernet-client"
    client_secret: str = "fibernet-secret"
    advance_after_s: float = 4.0
    seed: int = 20_260_401
    bucket_capacity: int = 20
    bucket_refill_per_s: float = 5.0

    @classmethod
    def from_env(cls) -> Settings:
        """Build settings from the environment with documented defaults."""
        return cls(
            client_id=os.getenv("FIBERNET_CLIENT_ID", "fibernet-client"),
            client_secret=os.getenv("FIBERNET_CLIENT_SECRET", "fibernet-secret"),
            advance_after_s=_float_env("FIBERNET_ADVANCE_AFTER_S", 4.0),
            seed=_int_env("FIBERNET_SEED", 20_260_401),
        )


@dataclass(slots=True)
class TokenBucket:
    """A simple token bucket; ``consume`` refills lazily using monotonic time."""

    capacity: int = 20
    refill_per_s: float = 5.0
    tokens: float = 20.0
    updated_at: float = field(default_factory=time.monotonic)

    def consume(self, now: float | None = None) -> tuple[bool, float]:
        """Consume one token, returning ``(allowed, seconds_until_available)``."""
        current = time.monotonic() if now is None else now
        elapsed = max(0.0, current - self.updated_at)
        self.tokens = min(float(self.capacity), self.tokens + elapsed * self.refill_per_s)
        self.updated_at = current
        if self.tokens < 1.0:
            return False, (1.0 - self.tokens) / self.refill_per_s
        self.tokens -= 1.0
        return True, 0.0


@dataclass(frozen=True, slots=True)
class TokenRecord:
    """An issued bearer token with a monotonic expiry deadline."""

    expires_at: float


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    """The stored response of a successfully accepted order creation."""

    body_hash: str
    status_code: int
    body: dict[str, Any]


class ProviderState:
    """All mutable data belonging to one simulated provider instance."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.from_env()
        self.injector = FaultInjector()
        self.orders: dict[str, Order] = {}
        self.tokens: dict[str, TokenRecord] = {}
        self.idempotency: dict[str, IdempotencyRecord] = {}
        self.buckets: dict[str, TokenBucket] = {}
        self.lock = asyncio.Lock()
        self.advance_task: asyncio.Task[None] | None = None

    def reset(self) -> None:
        """Clear orders, tokens, faults, idempotency records and rate limits."""
        self.injector.reset()
        self.orders.clear()
        self.tokens.clear()
        self.idempotency.clear()
        self.buckets.clear()
        self.lock = asyncio.Lock()

    def issue_token(self, ttl_s: float = TOKEN_TTL_S) -> tuple[str, int]:
        """Issue a bearer token, returning it with the advertised lifetime."""
        token = f"fibernet_{secrets.token_urlsafe(24)}"
        self.tokens[token] = TokenRecord(expires_at=time.monotonic() + ttl_s)
        return token, max(1, math.ceil(ttl_s))

    def verify_token(self, token: str) -> bool:
        """Return whether the bearer token exists and has not expired."""
        record = self.tokens.get(token)
        if record is None:
            return False
        if record.expires_at <= time.monotonic():
            del self.tokens[token]
            return False
        return True

    def rate_limit(self, key: str) -> int | None:
        """Consume a rate-limit token; return a retry delay when exhausted."""
        bucket = self.buckets.setdefault(
            key,
            TokenBucket(
                capacity=self.settings.bucket_capacity,
                refill_per_s=self.settings.bucket_refill_per_s,
                tokens=float(self.settings.bucket_capacity),
            ),
        )
        allowed, wait_s = bucket.consume()
        if allowed:
            return None
        return max(1, math.ceil(wait_s))

    def create_order(
        self,
        *,
        request: OrderCreateRequest,
        idempotency_key: str,
        body_hash: str,
    ) -> Order:
        """Create and store a new order in the ``received`` state."""
        offer_id = request.selected_offer_id
        number = digest_int(self.settings.seed, "order", idempotency_key, body_hash) & 0xFFFFFF
        order_id = f"A-{number:06X}"
        timestamp = iso_utc(utc_now())
        order = Order(
            order_id=order_id,
            id=order_id,
            status=OrderStatus.RECEIVED,
            created_at=timestamp,
            updated_at=timestamp,
            offer_id=offer_id,
            plan_id=offer_id,
            address=request.address.model_dump(exclude_none=True),
            customer=request.customer.model_dump(exclude_none=True),
        )
        self.orders[order_id] = order
        return order

    def advance_orders(self) -> int:
        """Advance every non-terminal order one step; return the number changed."""
        advanced = 0
        for order in sorted(self.orders.values(), key=lambda item: item.order_id):
            next_status = NEXT_STATUS.get(order.status)
            if next_status is None:
                continue
            order.status = next_status
            order.updated_at = iso_utc(utc_now())
            advanced += 1
        return advanced

    def counts(self) -> dict[str, int]:
        """Return total and per-status order counts."""
        counts = {"total": len(self.orders), **{status.value: 0 for status in OrderStatus}}
        for order in self.orders.values():
            counts[order.status.value] += 1
        return counts

    def control_snapshot(self) -> dict[str, Any]:
        """Return the local control-plane view of this provider."""
        return {
            "orders": self.counts(),
            "tokens": len(self.tokens),
            "idempotency_keys": len(self.idempotency),
            "fault_profile": self.injector.profile.model_dump(mode="json"),
            "fault_counters": {name: self.injector.call_index(name) for name in OPERATIONS},
        }


state = ProviderState()
"""The single state instance shared by the ASGI application and control routes."""
