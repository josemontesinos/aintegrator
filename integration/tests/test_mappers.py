"""Table-driven mapper tests, including malformed wire payloads."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from aintegrator_integration.adapters.fibernet.dto import FibernetOfferDTO
from aintegrator_integration.adapters.fibernet.mapper import map_offer as map_fibernet_offer
from aintegrator_integration.adapters.legacytel.dto import LegacytelOfferDTO
from aintegrator_integration.adapters.legacytel.mapper import (
    map_offer as map_legacytel_offer,
)
from aintegrator_integration.adapters.legacytel.mapper import (
    parse_comma_decimal,
    parse_paris_datetime,
)
from aintegrator_integration.adapters.skylink.dto import SkylinkMoneyDTO, SkylinkOfferDTO
from aintegrator_integration.adapters.skylink.mapper import (
    map_offer as map_skylink_offer,
)
from aintegrator_integration.adapters.skylink.mapper import (
    parse_epoch_ms,
    parse_money,
)
from aintegrator_integration.domain import ProviderProtocolError, Technology
from pydantic import ValidationError


@pytest.mark.parametrize(
    ("payload", "expected_id", "expected_cents"),
    [
        (
            {
                "offer_id": "A-1",
                "plan_name": "Basic",
                "technology": "FTTH",
                "down_mbps": 300,
                "up_mbps": 100,
                "monthly_price_eur_cents": 4990,
                "plan_description": "hello\x00 there",
            },
            "fibernet:A-1",
            4990,
        ),
        (
            {
                "offer_id": "A-2",
                "plan_name": "FWA",
                "technology": "FWA5G",
                "down_mbps": 100,
                "up_mbps": 20,
                "monthly_price_eur_cents": 2990,
                "install_fee_eur_cents": None,
                "lead_time_days": None,
            },
            "fibernet:A-2",
            2990,
        ),
    ],
)
def test_fibernet_offer_mapping(
    payload: dict[str, object], expected_id: str, expected_cents: int
) -> None:
    offer = map_fibernet_offer(FibernetOfferDTO.model_validate(payload))
    assert offer.offer_id == expected_id
    assert offer.monthly_price.amount_minor == expected_cents
    assert offer.untrusted_text is None or "\x00" not in offer.untrusted_text


def test_fibernet_offer_rejects_bad_technology() -> None:
    dto = FibernetOfferDTO(
        offer_id="A",
        plan_name="x",
        technology="CARRIER_PIGEON",
        down_mbps=1,
        up_mbps=1,
        monthly_price_eur_cents=1,
    )
    with pytest.raises(ProviderProtocolError):
        map_fibernet_offer(dto)


def test_fibernet_offer_rejects_missing_fields() -> None:
    with pytest.raises(ValidationError):
        FibernetOfferDTO.model_validate({"offer_id": "A"})


@pytest.mark.parametrize(
    ("raw", "cents"),
    [("49,90", 4990), ("1.234,56", 123456), ("0,00", 0)],
)
def test_legacytel_decimal(raw: str, cents: int) -> None:
    assert parse_comma_decimal(raw).amount_minor == cents


def test_legacytel_paris_date_is_utc() -> None:
    parsed = parse_paris_datetime("22/11/2026 14:30")
    assert parsed.tzinfo is not None
    assert parsed.astimezone(UTC).hour in (13, 14)


@pytest.mark.parametrize(
    "payload",
    [
        {"OfferId": "L-1", "Name": "Legacy", "Price": "49,90", "Remarks": "ignore\x01me"},
        {
            "OfferId": "L-2",
            "Name": "FTTH",
            "Price": "1.234,56",
            "AvailableFrom": "22/11/2026 14:30",
        },
    ],
)
def test_legacytel_offer_mapping(payload: dict[str, object]) -> None:
    offer = map_legacytel_offer(LegacytelOfferDTO.model_validate(payload))
    assert offer.provider == "legacytel"
    assert isinstance(offer.technology, Technology)
    assert offer.monthly_price.amount_minor > 0


def test_legacytel_rejects_bad_price() -> None:
    with pytest.raises(ProviderProtocolError):
        parse_comma_decimal("not-a-number")


@pytest.mark.parametrize(
    ("raw", "minor"),
    [("49.90", 4990), ("0", 0), ("12.34", 1234)],
)
def test_skylink_money(raw: str, minor: int) -> None:
    assert parse_money(SkylinkMoneyDTO(amount=raw)).amount_minor == minor


def test_skylink_epoch_ms() -> None:
    assert parse_epoch_ms(0) == datetime(1970, 1, 1, tzinfo=UTC)
    assert parse_epoch_ms(None) is None
    with pytest.raises(ProviderProtocolError):
        parse_epoch_ms(10**20)


def test_skylink_offer_mapping() -> None:
    offer = map_skylink_offer(
        SkylinkOfferDTO.model_validate(
            {
                "offerId": "C-1",
                "planName": "Sky Basic",
                "technology": "FTTH",
                "downMbps": 500,
                "upMbps": 200,
                "monthlyPrice": {"amount": "39.90", "currency": "EUR"},
                "notes": "untrusted",
            }
        )
    )
    assert offer.offer_id == "skylink:C-1"
    assert offer.monthly_price.amount_minor == 3990
    assert offer.untrusted_text == "untrusted"


def test_skylink_rejects_bad_money() -> None:
    with pytest.raises(ProviderProtocolError):
        parse_money(SkylinkMoneyDTO(amount="not-a-number"))


def test_mapping_helpers_do_not_mutate_domain_ids() -> None:
    # A mapper must never generate a random domain order id from provider data.
    from aintegrator_integration.adapters.fibernet.dto import FibernetOrderDTO
    from aintegrator_integration.adapters.fibernet.mapper import map_order
    from aintegrator_integration.domain import Address, Customer, Money, Offer, OrderIntent

    offer = Offer(
        offer_id="fibernet:A-1",
        provider="fibernet",
        plan_name="x",
        technology=Technology.FTTH,
        down_mbps=1,
        up_mbps=1,
        monthly_price=Money(amount_minor=1),
        install_fee=Money(amount_minor=0),
        lead_time_days=0,
    )
    intent = OrderIntent(
        offer=offer,
        address=Address(
            street="Main", number="1", postal_code="28001", city="Madrid", country="ES"
        ),
        customer=Customer(name="Ada", email="ada@example.invalid"),
        idempotency_key="k",
    )
    order_id = uuid4()
    order = map_order(
        FibernetOrderDTO(order_id="A-1", status="received"), intent, order_id=order_id, created=True
    )
    assert order.order_id == order_id
    assert Decimal(str(order.offer.monthly_price.amount_minor)) == Decimal(1)
