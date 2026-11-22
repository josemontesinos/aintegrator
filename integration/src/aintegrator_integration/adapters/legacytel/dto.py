"""Wire DTOs for legacytel B (SOAP, comma decimals, Paris dates)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

__all__ = ["LegacytelOfferDTO", "LegacytelOrderDTO", "LegacytelResponseDTO"]


class _WireModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class LegacytelOfferDTO(_WireModel):
    # The WSDL is intentionally sparse: speeds/technology are derived by the mapper.
    OfferId: str
    Name: str
    Price: str
    AvailableFrom: str | None = None
    Remarks: str | None = None


class LegacytelOrderDTO(_WireModel):
    ProviderOrderId: str | None = None
    StatusCode: int | None = None
    ExternalRef: str | None = None
    OrderDate: str | None = None
    LastUpdated: str | None = None
    Remarks: str | None = None


class LegacytelResponseDTO(_WireModel):
    ResultCode: str = "OK"
    ErrorCode: str | None = None
    ErrorText: str | None = None
    Offers: Any = None
    Order: Any = None
    Orders: Any = None
