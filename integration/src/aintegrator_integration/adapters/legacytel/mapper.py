"""Pure mappers for legacytel's legacy SOAP quirks."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID
from zoneinfo import ZoneInfo

from aintegrator_integration.adapters.legacytel.dto import LegacytelOfferDTO, LegacytelOrderDTO
from aintegrator_integration.domain import (
    Money,
    Offer,
    Order,
    OrderIntent,
    OrderStatus,
    ProviderProtocolError,
    ProviderRejected,
    Technology,
)

__all__ = [
    "map_offer",
    "map_offers",
    "map_order",
    "map_status",
    "parse_comma_decimal",
    "parse_paris_datetime",
]

_STATUS_MAP = {
    10: OrderStatus.SUBMITTED,
    20: OrderStatus.IN_PROGRESS,
    30: OrderStatus.COMPLETED,
    90: OrderStatus.REJECTED,
    99: OrderStatus.FAILED,
}

_TECHNOLOGY_MAP = {
    "FTTH": Technology.FTTH,
    "VDSL": Technology.VDSL,
    "FWA_5G": Technology.FWA_5G,
    "FWA5G": Technology.FWA_5G,
}


def parse_comma_decimal(value: str) -> Money:
    """Convert ``'49,90'`` or ``'1.234,56'`` into integer minor units."""
    if not isinstance(value, str):
        raise ProviderProtocolError(f"Invalid legacytel price: {value!r}")
    normalized = value.strip().replace(".", "").replace(",", ".")
    try:
        amount = Decimal(normalized)
    except InvalidOperation as exc:
        raise ProviderProtocolError(f"Invalid legacytel price: {value!r}") from exc
    return Money(amount_minor=int(amount * 100))


def parse_paris_datetime(value: str) -> datetime:
    """Parse naive ``DD/MM/YYYY HH:mm`` Europe/Paris time into UTC."""
    try:
        naive = datetime.strptime(value.strip(), "%d/%m/%Y %H:%M")
    except ValueError as exc:
        raise ProviderProtocolError(f"Invalid legacytel date: {value!r}") from exc
    return naive.replace(tzinfo=ZoneInfo("Europe/Paris")).astimezone(UTC)


def map_status(code: int | None) -> OrderStatus:
    status = _STATUS_MAP.get(code if code is not None else 10)
    if status is None:
        raise ProviderProtocolError(f"Unknown legacytel status code: {code!r}")
    return status


def _derived_offer_fields(offer_id: str) -> tuple[Technology, int, int, int]:
    """The legacy WSDL omits speeds; derive stable fictional values from the id."""
    digest = hashlib.sha256(offer_id.encode()).digest()
    technology = (Technology.FTTH, Technology.VDSL, Technology.FWA_5G)[digest[0] % 3]
    return technology, 50 + digest[1] * 10, 10 + digest[2] * 5, 1 + digest[3] % 14


def map_offer(dto: LegacytelOfferDTO) -> Offer:
    technology, down_mbps, up_mbps, lead_time_days = _derived_offer_fields(dto.OfferId)
    if dto.Name in _TECHNOLOGY_MAP:
        technology = _TECHNOLOGY_MAP[dto.Name.strip().upper()]
    return Offer(
        offer_id=f"legacytel:{dto.OfferId}",
        provider="legacytel",
        plan_name=dto.Name,
        technology=technology,
        down_mbps=down_mbps,
        up_mbps=up_mbps,
        monthly_price=parse_comma_decimal(dto.Price),
        install_fee=Money(amount_minor=0),
        lead_time_days=lead_time_days,
        untrusted_text=dto.Remarks,
    )


def map_offers(payload: object) -> list[Offer]:
    """Accept zeep's ``{'Offer': [...]}``, a bare list or a single dict."""
    if payload is None:
        return []
    if isinstance(payload, dict):
        nested = payload.get("Offer", payload.get("Offers"))
        return map_offers(nested)
    if isinstance(payload, (list, tuple)):
        return [map_offer(LegacytelOfferDTO.model_validate(item)) for item in payload]
    raise ProviderProtocolError(f"Unexpected legacytel offers payload: {type(payload)!r}")


def map_order(
    dto: LegacytelOrderDTO,
    intent: OrderIntent | None,
    *,
    order_id: UUID,
    now: datetime | None = None,
) -> Order:
    if intent is None:
        raise ProviderProtocolError("legacytel order mapping needs the persisted intent")
    timestamp = now or datetime.now(UTC)
    raw_date = dto.OrderDate or dto.LastUpdated
    return Order(
        order_id=order_id,
        provider="legacytel",
        provider_order_id=dto.ProviderOrderId,
        client_reference=dto.ExternalRef or intent.client_reference,
        idempotency_key=intent.idempotency_key,
        offer=intent.offer,
        address=intent.address,
        customer=intent.customer,
        status=map_status(dto.StatusCode),
        supports_cancel=False,
        status_history=(),
        created_at=parse_paris_datetime(raw_date) if raw_date else timestamp,
        updated_at=timestamp,
    )


def check_business_error(payload: dict[str, object]) -> None:
    """Legacytel reports business errors inside HTTP 200 responses."""
    if str(payload.get("ResultCode", "OK")).upper() != "NOK":
        return
    code = payload.get("ErrorCode", "")
    text = payload.get("ErrorText", "Legacytel business error")
    raise ProviderRejected(f"legacytel error {code}: {text}", reason=str(code))
