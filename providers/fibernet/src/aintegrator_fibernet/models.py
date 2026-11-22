"""Wire models and lifecycle rules for the simulated Fibernet provider.

All identifiers, plans and customer data used by this package are fictional.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

__all__ = [
    "CANCELLABLE_STATUSES",
    "NEXT_STATUS",
    "TERMINAL_STATUSES",
    "Address",
    "CoverageOffer",
    "CoverageResponse",
    "Customer",
    "Order",
    "OrderCreateRequest",
    "OrderStatus",
    "iso_utc",
    "utc_now",
]


class OrderStatus(StrEnum):
    """Lifecycle states of a Fibernet order."""

    RECEIVED = "received"
    VALIDATING = "validating"
    PROVISIONING = "provisioning"
    ACTIVE = "active"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


NEXT_STATUS: dict[OrderStatus, OrderStatus] = {
    OrderStatus.RECEIVED: OrderStatus.VALIDATING,
    OrderStatus.VALIDATING: OrderStatus.PROVISIONING,
    OrderStatus.PROVISIONING: OrderStatus.ACTIVE,
}
"""The happy-path transition for every non-terminal order status."""

CANCELLABLE_STATUSES = frozenset({OrderStatus.RECEIVED, OrderStatus.VALIDATING})
TERMINAL_STATUSES = frozenset({OrderStatus.ACTIVE, OrderStatus.REJECTED, OrderStatus.CANCELLED})


def utc_now() -> datetime:
    """Return the current timezone-aware UTC time."""
    return datetime.now(UTC)


def iso_utc(value: datetime) -> str:
    """Format a UTC datetime as an ISO-8601 string with a ``Z`` suffix."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class Address(BaseModel):
    """A fictional service address. Extra keys are preserved for round-tripping."""

    model_config = ConfigDict(extra="allow")

    postal_code: str
    street: str
    number: str | int

    @field_validator("postal_code", "street", mode="before")
    @classmethod
    def _stringify(cls, value: object) -> str:
        return str(value)


class Customer(BaseModel):
    """Minimal customer details; providers must not receive more than necessary."""

    model_config = ConfigDict(extra="allow")

    name: str | None = None
    email: str | None = None
    phone: str | None = None


class OrderCreateRequest(BaseModel):
    """Accepted body of ``POST /v1/orders``.

    ``offer_id``, ``plan_id`` and ``plan`` are aliases for the selected offer so
    that adapters which use plan-oriented naming also work.
    """

    model_config = ConfigDict(extra="allow")

    offer_id: str | None = None
    plan_id: str | None = None
    plan: str | None = None
    address: Address
    customer: Customer

    @property
    def selected_offer_id(self) -> str:
        """Return the first non-empty offer identifier supplied by the caller."""
        return next((value for value in (self.offer_id, self.plan_id, self.plan) if value), "")

    @model_validator(mode="after")
    def _require_offer_reference(self) -> Self:
        if not self.selected_offer_id:
            raise ValueError("one of offer_id, plan_id or plan is required")
        return self


class CoverageOffer(BaseModel):
    """One fictional connectivity offer, with prices expressed in integer cents."""

    model_config = ConfigDict(extra="forbid")

    offer_id: str
    plan_name: str
    technology: str
    down_mbps: int
    up_mbps: int
    monthly_price_eur_cents: int
    install_fee_eur_cents: int | None = None
    lead_time_days: int | None = None
    valid_until: str | None = None
    plan_description: str | None = None


class CoverageResponse(BaseModel):
    """Cursor-paginated coverage response."""

    offers: list[CoverageOffer]
    next_cursor: str | None = None


class Order(BaseModel):
    """The provider's representation of an order, as returned over HTTP."""

    order_id: str
    id: str
    status: OrderStatus
    created_at: str
    updated_at: str
    offer_id: str
    plan_id: str
    address: dict[str, Any]
    customer: dict[str, Any]
