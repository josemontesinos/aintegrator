"""Ports through which the service reaches simulated providers."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from aintegrator_integration.domain import (
    Address,
    Capabilities,
    EligibilityResult,
    Order,
    OrderIntent,
)

__all__ = ["ProviderGateway"]


class ProviderGateway(Protocol):
    """Uniform interface implemented by every provider adapter.

    Adapters own protocol-specific DTOs and mappers; the rest of the system only
    sees domain objects. Providers are reached over HTTP, never imported.

    The optional ``intent``/``order_id`` context lets the service pass the
    already-persisted snapshot when it asks a provider about an existing order.
    """

    @property
    def name(self) -> str: ...

    @property
    def capabilities(self) -> Capabilities: ...

    async def check_eligibility(self, address: Address) -> EligibilityResult: ...

    async def poll_eligibility(self, request_ref: str) -> EligibilityResult: ...

    async def create_order(self, intent: OrderIntent, *, order_id: UUID | None = None) -> Order: ...

    async def get_order(
        self,
        reference: str,
        *,
        intent: OrderIntent | None = None,
        order_id: UUID | None = None,
    ) -> Order: ...

    async def cancel_order(
        self,
        reference: str,
        *,
        intent: OrderIntent | None = None,
        order_id: UUID | None = None,
    ) -> Order: ...

    async def find_by_reference(
        self,
        client_reference: str,
        *,
        intent: OrderIntent | None = None,
        order_id: UUID | None = None,
    ) -> Order | None: ...
