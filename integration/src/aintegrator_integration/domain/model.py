"""Immutable Pydantic v2 domain models for the connectivity marketplace."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "Address",
    "Capabilities",
    "Customer",
    "EligibilityResult",
    "EligibilityStatus",
    "Money",
    "Offer",
    "Order",
    "OrderEvent",
    "OrderIntent",
    "OrderStatus",
    "Technology",
    "sanitize_untrusted_text",
]

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE = re.compile(r"\s+")
_UNTRUSTED_LIMIT = 500


def sanitize_untrusted_text(value: str | None, *, limit: int = _UNTRUSTED_LIMIT) -> str | None:
    """Strip control characters and cap length of provider-supplied free text."""
    if value is None:
        return None
    cleaned = _WHITESPACE.sub(" ", _CONTROL_CHARS.sub("", value)).strip()
    if not cleaned:
        return None
    return cleaned if len(cleaned) <= limit else f"{cleaned[: limit - 3]}..."


class Technology(StrEnum):
    FTTH = "FTTH"
    VDSL = "VDSL"
    FWA_5G = "FWA_5G"


class EligibilityStatus(StrEnum):
    OK = "OK"
    UNAVAILABLE = "UNAVAILABLE"
    ERROR = "ERROR"
    PENDING = "PENDING"
    TIMEOUT = "TIMEOUT"


class OrderStatus(StrEnum):
    PENDING_SUBMISSION = "PENDING_SUBMISSION"
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, use_enum_values=False)


class Money(_FrozenModel):
    amount_minor: int
    currency: str = "EUR"

    @property
    def as_eur(self) -> Decimal:
        return Decimal(self.amount_minor) / Decimal(100)

    def __str__(self) -> str:
        return f"{self.as_eur:.2f} {self.currency}"


class Address(_FrozenModel):
    street: str
    number: str
    postal_code: str
    city: str
    country: str
    floor: str | None = None
    door: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        result = dict(data)
        for key in ("street", "number", "postal_code", "city", "floor", "door"):
            value = result.get(key)
            if isinstance(value, str):
                result[key] = _WHITESPACE.sub(" ", value).strip() or None
        country = result.get("country")
        if isinstance(country, str):
            result["country"] = country.strip().upper()[:2]
        return result

    @property
    def one_line(self) -> str:
        extra = " ".join(part for part in (self.floor, self.door) if part)
        suffix = f" {extra}" if extra else ""
        return (
            f"{self.street} {self.number}{suffix}, {self.postal_code} {self.city}, {self.country}"
        )


class Customer(_FrozenModel):
    name: str
    email: str

    @field_validator("email")
    @classmethod
    def _fake_email(cls, value: str) -> str:
        return value.strip().lower()


class Offer(_FrozenModel):
    offer_id: str
    provider: str
    plan_name: str
    technology: Technology
    down_mbps: int
    up_mbps: int
    monthly_price: Money
    install_fee: Money
    lead_time_days: int
    valid_until: datetime | None = None
    untrusted_text: str | None = None

    @field_validator("untrusted_text")
    @classmethod
    def _clean_untrusted(cls, value: str | None) -> str | None:
        return sanitize_untrusted_text(value)


class EligibilityResult(_FrozenModel):
    provider: str
    status: EligibilityStatus
    offers: tuple[Offer, ...] = ()
    fetched_at: datetime
    from_cache: bool = False
    error: str | None = None
    # Async providers return an id here so the caller can poll later.
    request_ref: str | None = None

    @field_validator("offers", mode="before")
    @classmethod
    def _as_tuple(cls, value: Any) -> tuple[Offer, ...]:
        return tuple(value)


class OrderEvent(_FrozenModel):
    event_id: UUID = Field(default_factory=uuid4)
    order_id: UUID
    status: OrderStatus
    occurred_at: datetime
    source: str
    raw_ref: str | None = None
    note: str | None = None


class Order(_FrozenModel):
    order_id: UUID = Field(default_factory=uuid4)
    provider: str
    provider_order_id: str | None = None
    client_reference: str
    idempotency_key: str | None = None
    offer: Offer
    address: Address
    customer: Customer
    status: OrderStatus
    supports_cancel: bool = False
    status_history: tuple[OrderEvent, ...] = ()
    created_at: datetime
    updated_at: datetime

    @field_validator("status_history", mode="before")
    @classmethod
    def _as_tuple(cls, value: Any) -> tuple[OrderEvent, ...]:
        return tuple(value)

    @property
    def cancellable(self) -> bool:
        return self.supports_cancel and self.status in (
            OrderStatus.SUBMITTED,
            OrderStatus.ACCEPTED,
        )


class OrderIntent(_FrozenModel):
    offer: Offer
    address: Address
    customer: Customer
    idempotency_key: str
    client_reference: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str | None = None


class Capabilities(_FrozenModel):
    supports_idempotency_key: bool
    supports_cancel: bool
    eligibility_mode: str
    eligibility_cacheable: bool
    supports_lookup_by_reference: bool
