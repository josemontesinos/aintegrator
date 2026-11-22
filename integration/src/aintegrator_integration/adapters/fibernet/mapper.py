"""Pure mappers between fibernet DTOs and the domain model."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from aintegrator_integration.adapters.fibernet.dto import FibernetOfferDTO, FibernetOrderDTO
from aintegrator_integration.domain import (
    Money,
    Offer,
    Order,
    OrderIntent,
    OrderStatus,
    ProviderProtocolError,
    Technology,
)

__all__ = ["map_offer", "map_order", "map_status"]

_STATUS_MAP = {
    "received": OrderStatus.SUBMITTED,
    "validating": OrderStatus.SUBMITTED,
    "provisioning": OrderStatus.IN_PROGRESS,
    "active": OrderStatus.COMPLETED,
    "rejected": OrderStatus.REJECTED,
    "cancelled": OrderStatus.CANCELLED,
}

_TECHNOLOGY_MAP = {
    "FTTH": Technology.FTTH,
    "VDSL": Technology.VDSL,
    "FWA_5G": Technology.FWA_5G,
    "FWA5G": Technology.FWA_5G,
}


def map_status(raw: str) -> OrderStatus:
    status = _STATUS_MAP.get(raw.strip().lower())
    if status is None:
        raise ProviderProtocolError(f"Unknown fibernet status: {raw!r}")
    return status


def map_offer(dto: FibernetOfferDTO) -> Offer:
    """Map an offer, validating ranges and normalising untrusted free text."""
    technology = _TECHNOLOGY_MAP.get(dto.technology.strip().upper())
    if technology is None:
        raise ProviderProtocolError(f"Unknown fibernet technology: {dto.technology!r}")
    install_fee = dto.install_fee_eur_cents or 0
    if dto.monthly_price_eur_cents < 0 or install_fee < 0:
        raise ProviderProtocolError("Negative fibernet price")
    if dto.down_mbps <= 0 or dto.up_mbps <= 0:
        raise ProviderProtocolError("Invalid fibernet speed")
    raw_id = dto.offer_id.strip()
    qualified_id = raw_id if raw_id.startswith("fibernet:") else f"fibernet:{raw_id}"
    return Offer(
        offer_id=qualified_id,
        provider="fibernet",
        plan_name=dto.plan_name,
        technology=technology,
        down_mbps=dto.down_mbps,
        up_mbps=dto.up_mbps,
        monthly_price=Money(amount_minor=dto.monthly_price_eur_cents),
        install_fee=Money(amount_minor=install_fee),
        lead_time_days=dto.lead_time_days or 0,
        valid_until=dto.valid_until,
        untrusted_text=dto.plan_description,
    )


def map_order(
    dto: FibernetOrderDTO,
    intent: OrderIntent,
    *,
    order_id: UUID,
    now: datetime | None = None,
    created: bool = False,
) -> Order:
    timestamp = now or datetime.now(UTC)
    status = OrderStatus.SUBMITTED if created else map_status(dto.status)
    return Order(
        order_id=order_id,
        provider="fibernet",
        provider_order_id=dto.order_id,
        client_reference=dto.client_reference or intent.client_reference,
        idempotency_key=intent.idempotency_key,
        offer=intent.offer,
        address=intent.address,
        customer=intent.customer,
        status=status,
        supports_cancel=True,
        status_history=(),
        created_at=dto.created_at or timestamp,
        updated_at=timestamp,
    )
