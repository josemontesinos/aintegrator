"""Wire DTOs for skylink C (camelCase envelope, epoch ms, string money)."""

from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

__all__ = ["SkylinkEnvelopeDTO", "SkylinkMoneyDTO", "SkylinkOfferDTO", "SkylinkOrderDTO"]


class _WireModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class SkylinkMoneyDTO(_WireModel):
    amount: str
    currency: str = "EUR"


class SkylinkOfferDTO(_WireModel):
    """Accept both the public availability shape and the legacy internal shape."""

    offer_id: str = Field(validation_alias=AliasChoices("offerId", "offer_id"))
    plan_name: str = Field(validation_alias=AliasChoices("name", "planName", "plan_name"))
    technology: str
    down_mbps: int | None = Field(
        default=None, validation_alias=AliasChoices("downMbps", "down_mbps")
    )
    up_mbps: int | None = Field(default=None, validation_alias=AliasChoices("upMbps", "up_mbps"))
    monthly_price: SkylinkMoneyDTO = Field(
        validation_alias=AliasChoices("price", "monthlyPrice", "monthly_price")
    )
    install_fee: SkylinkMoneyDTO | None = Field(
        default=None, validation_alias=AliasChoices("installFee", "install_fee")
    )
    lead_time_days: int | None = Field(
        default=None,
        validation_alias=AliasChoices("installmentDays", "leadTimeDays", "lead_time_days"),
    )
    valid_until: int | None = Field(
        default=None, validation_alias=AliasChoices("validUntil", "valid_until")
    )
    notes: str | None = None


class SkylinkOrderDTO(_WireModel):
    order_id: str = Field(validation_alias=AliasChoices("orderId", "order_id"))
    client_reference: str | None = Field(
        default=None, validation_alias=AliasChoices("clientReference", "client_reference")
    )
    status: str = "QUEUED"
    offer_id: str | None = Field(default=None, validation_alias=AliasChoices("offerId", "offer_id"))
    created_at: int | None = Field(
        default=None, validation_alias=AliasChoices("createdAt", "created_at")
    )
    notes: str | None = None
    offers: list[SkylinkOfferDTO] | None = None


class SkylinkEnvelopeDTO(_WireModel):
    data: Any = None
    meta: dict[str, Any] = Field(default_factory=dict)
    errors: list[Any] = Field(default_factory=list)
