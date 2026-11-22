"""Wire DTOs for fibernet (REST/JSON, snake_case, cents)."""

from __future__ import annotations

from datetime import datetime

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

__all__ = ["FibernetCoverageDTO", "FibernetOfferDTO", "FibernetOrderDTO", "FibernetTokenDTO"]


class _WireModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class FibernetTokenDTO(_WireModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 300


class FibernetOfferDTO(_WireModel):
    offer_id: str
    plan_name: str
    technology: str
    down_mbps: int
    up_mbps: int
    monthly_price_eur_cents: int = Field(
        validation_alias=AliasChoices("monthly_price_eur_cents", "monthly_price_cents")
    )
    install_fee_eur_cents: int | None = Field(
        default=0, validation_alias=AliasChoices("install_fee_eur_cents", "install_fee_cents")
    )
    lead_time_days: int | None = 0
    valid_until: datetime | None = None
    plan_description: str | None = None


class FibernetCoverageDTO(_WireModel):
    offers: list[FibernetOfferDTO] = Field(default_factory=list)
    next_cursor: str | None = None


class FibernetOrderDTO(_WireModel):
    order_id: str = Field(validation_alias=AliasChoices("order_id", "id", "provider_order_id"))
    status: str
    offer_id: str | None = None
    client_reference: str | None = None
    created_at: datetime | None = None
