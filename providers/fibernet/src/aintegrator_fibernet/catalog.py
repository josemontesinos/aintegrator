"""Deterministic fictional offer catalog and opaque coverage cursors.

Every value is derived from a seed and the requested address, so repeated
queries produce byte-identical offers without any persistence.
"""

from __future__ import annotations

import base64
import binascii
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from aintegrator_provider_common import digest_int, pick, rate_from_hash

from .models import CoverageOffer, iso_utc

__all__ = [
    "InvalidCursorError",
    "address_key",
    "build_offers",
    "decode_cursor",
    "encode_cursor",
]


@dataclass(frozen=True, slots=True)
class _PlanTemplate:
    """A fictional plan used as the base for generated offers."""

    name: str
    technology: str
    down_mbps: int
    up_mbps: int
    monthly_price_eur_cents: int
    install_fee_eur_cents: int
    lead_time_days: int
    description: str


_PLAN_TEMPLATES: tuple[_PlanTemplate, ...] = (
    _PlanTemplate(
        name="Fibernet Lumen 300",
        technology="FTTH",
        down_mbps=300,
        up_mbps=100,
        monthly_price_eur_cents=3990,
        install_fee_eur_cents=9900,
        lead_time_days=7,
        description="Fictional fibre plan with a static public IP for small offices.",
    ),
    _PlanTemplate(
        name="Fibernet Aurora 1G",
        technology="FTTH",
        down_mbps=1000,
        up_mbps=500,
        monthly_price_eur_cents=6490,
        install_fee_eur_cents=4900,
        lead_time_days=10,
        description='Unlimited "aurora" bandwidth; speeds may vary during fictional storms.',
    ),
    _PlanTemplate(
        name="Fibernet Copperline 80",
        technology="VDSL",
        down_mbps=80,
        up_mbps=20,
        monthly_price_eur_cents=2990,
        install_fee_eur_cents=7900,
        lead_time_days=5,
        description="Legacy copper access, available while the fictional exchange is upgraded.",
    ),
    _PlanTemplate(
        name="Fibernet Breeze 5G",
        technology="FWA_5G",
        down_mbps=150,
        up_mbps=30,
        monthly_price_eur_cents=3490,
        install_fee_eur_cents=0,
        lead_time_days=2,
        description='<b>No cables needed!</b> "Best" effort service in the fictional FWA zone.',
    ),
    _PlanTemplate(
        name="Fibernet Hearth 600",
        technology="FTTH",
        down_mbps=600,
        up_mbps=300,
        monthly_price_eur_cents=5190,
        install_fee_eur_cents=6900,
        lead_time_days=8,
        description="Home plan with a fictional mesh router and priority support hours.",
    ),
    _PlanTemplate(
        name="Fibernet Meadow 40",
        technology="VDSL",
        down_mbps=40,
        up_mbps=10,
        monthly_price_eur_cents=2290,
        install_fee_eur_cents=5900,
        lead_time_days=4,
        description="Budget access for the fictional village of Meadowfall.",
    ),
)

_VALID_UNTIL_BASE = datetime(2099, 1, 1, tzinfo=UTC)
_CURSOR_SEED = 0xF1BE2


class InvalidCursorError(ValueError):
    """Raised when a cursor is malformed or belongs to a different address."""


def address_key(postal_code: str, street: str, number: str | int) -> str:
    """Return a normalized address identity used for hashing and cursor binding."""
    parts = (postal_code, street, str(number))
    return "|".join(part.strip().lower() for part in parts)


def build_offers(
    seed: int,
    postal_code: str,
    street: str,
    number: str | int,
) -> tuple[CoverageOffer, ...]:
    """Generate three to six deterministic, fictional offers for an address."""
    key = address_key(postal_code, street, number)
    count = 3 + digest_int(seed, "count", key) % 4
    offers: list[CoverageOffer] = []
    for index in range(count):
        template = pick(_PLAN_TEMPLATES, seed, "template", key, index)
        price_offset = rate_from_hash(seed, "price", key, index, minimum=-400, maximum=400)
        install_offset = rate_from_hash(seed, "install", key, index, minimum=-1000, maximum=1000)
        lead_offset = rate_from_hash(seed, "lead", key, index, minimum=-2, maximum=3)
        valid_days = rate_from_hash(seed, "valid-until", key, index, minimum=1, maximum=365)
        offer_id = digest_int(seed, "offer", key, index) & 0xFFFFFF
        offers.append(
            CoverageOffer(
                offer_id=f"fibernet:F-{offer_id:06X}",
                plan_name=template.name,
                technology=template.technology,
                down_mbps=template.down_mbps,
                up_mbps=template.up_mbps,
                monthly_price_eur_cents=max(990, template.monthly_price_eur_cents + price_offset),
                install_fee_eur_cents=max(0, template.install_fee_eur_cents + install_offset),
                lead_time_days=max(1, template.lead_time_days + lead_offset),
                valid_until=_valid_until(valid_days),
                plan_description=template.description,
            )
        )
    return tuple(offers)


def _valid_until(days: int) -> str:
    """Return a deterministic ISO-8601 UTC expiry timestamp."""
    return iso_utc(_VALID_UNTIL_BASE + timedelta(days=days))


def encode_cursor(key: str, offset: int) -> str:
    """Encode an offset into an opaque, address-bound cursor."""
    signature = digest_int(_CURSOR_SEED, "cursor", key, offset) & 0xFFFFFFFF
    return base64.urlsafe_b64encode(f"{offset}.{signature:08x}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str, key: str) -> int:
    """Decode a cursor, rejecting malformed or address-mismatched values."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = base64.urlsafe_b64decode(padded.encode()).decode()
        offset_text, signature = payload.split(".", 1)
        offset = int(offset_text)
    except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise InvalidCursorError("cursor is not a valid Fibernet cursor") from exc
    expected = f"{digest_int(_CURSOR_SEED, 'cursor', key, offset) & 0xFFFFFFFF:08x}"
    if offset < 0 or not hmac.compare_digest(signature, expected):
        raise InvalidCursorError("cursor does not match the requested address")
    return offset
