"""Pure mappers for skylink's envelope, epoch-ms timestamps and money objects."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from aintegrator_integration.adapters.skylink.dto import (
    SkylinkMoneyDTO,
    SkylinkOfferDTO,
    SkylinkOrderDTO,
)
from aintegrator_integration.domain import (
    EligibilityResult,
    EligibilityStatus,
    Money,
    Offer,
    Order,
    OrderIntent,
    OrderStatus,
    ProviderProtocolError,
    Technology,
)

__all__ = ["map_offer", "map_order", "map_status", "parse_epoch_ms", "parse_money"]

_STATUS_MAP = {
    "QUEUED": OrderStatus.SUBMITTED,
    "CONFIRMED": OrderStatus.ACCEPTED,
    "WORKING": OrderStatus.IN_PROGRESS,
    "DONE": OrderStatus.COMPLETED,
    "DECLINED": OrderStatus.REJECTED,
}

_TECHNOLOGY_MAP = {
    "FTTH": Technology.FTTH,
    "VDSL": Technology.VDSL,
    "FWA_5G": Technology.FWA_5G,
    "FWA5G": Technology.FWA_5G,
    # The simulated provider deliberately uses fictional technology labels.
    "FICTIONAL-FIBER": Technology.FTTH,
    "FICTIONAL-WIRELESS": Technology.FWA_5G,
    "FICTIONAL-MESH": Technology.VDSL,
}


def parse_epoch_ms(value: int | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise ProviderProtocolError(f"Invalid skylink epoch value: {value!r}") from exc


def parse_money(dto: SkylinkMoneyDTO) -> Money:
    try:
        return Money(amount_minor=int(Decimal(dto.amount) * 100), currency=dto.currency)
    except (InvalidOperation, ValueError) as exc:
        raise ProviderProtocolError(f"Invalid skylink money: {dto.amount!r}") from exc


def map_status(raw: str) -> OrderStatus:
    status = _STATUS_MAP.get(raw.strip().upper())
    if status is None:
        raise ProviderProtocolError(f"Unknown skylink status: {raw!r}")
    return status


def map_offer(dto: SkylinkOfferDTO) -> Offer:
    technology = _TECHNOLOGY_MAP.get(dto.technology.strip().upper())
    if technology is None:
        raise ProviderProtocolError(f"Unknown skylink technology: {dto.technology!r}")
    # The public availability payload omits speeds and lead time. Derive stable
    # fictional values from the opaque offer id instead of inventing them per call.
    digest = hashlib.sha256(dto.offer_id.encode()).digest()
    down_mbps = dto.down_mbps or 100 + digest[0] * 10
    up_mbps = dto.up_mbps or 20 + digest[1] * 5
    lead_time_days = dto.lead_time_days or 1 + digest[2] % 14
    return Offer(
        offer_id=f"skylink:{dto.offer_id}",
        provider="skylink",
        plan_name=dto.plan_name,
        technology=technology,
        down_mbps=down_mbps,
        up_mbps=up_mbps,
        monthly_price=parse_money(dto.monthly_price),
        install_fee=parse_money(dto.install_fee) if dto.install_fee else Money(amount_minor=0),
        lead_time_days=lead_time_days,
        valid_until=parse_epoch_ms(dto.valid_until),
        untrusted_text=dto.notes,
    )


def map_order(
    dto: SkylinkOrderDTO,
    intent: OrderIntent | None,
    *,
    order_id: UUID,
    now: datetime | None = None,
) -> Order:
    if intent is None:
        raise ProviderProtocolError("skylink order mapping needs the persisted intent")
    status = map_status(dto.status)
    return Order(
        order_id=order_id,
        provider="skylink",
        provider_order_id=dto.order_id,
        client_reference=dto.client_reference or intent.client_reference,
        idempotency_key=intent.idempotency_key,
        offer=intent.offer,
        address=intent.address,
        customer=intent.customer,
        status=status,
        supports_cancel=False,
        status_history=(),
        created_at=parse_epoch_ms(dto.created_at) or now or datetime.now(UTC),
        updated_at=now or datetime.now(UTC),
    )


def map_availability_payload(data: Any, request_ref: str) -> EligibilityResult:
    """Map a completed availability webhook/poll payload into a domain result."""
    if not isinstance(data, dict):
        raise ProviderProtocolError("Invalid skylink availability payload")
    raw_offers = data.get("offers") or []
    if isinstance(raw_offers, dict):
        raw_offers = raw_offers.get("offer", [])
    if isinstance(raw_offers, dict):
        raw_offers = [raw_offers]
    offers = tuple(map_offer(SkylinkOfferDTO.model_validate(item)) for item in raw_offers)
    completed = str(data.get("status", "")).upper() in {"COMPLETED", "DONE", "OK"}
    return EligibilityResult(
        provider="skylink",
        status=EligibilityStatus.OK if offers and completed else EligibilityStatus.PENDING,
        offers=offers,
        fetched_at=datetime.now(UTC),
        request_ref=request_ref,
    )
