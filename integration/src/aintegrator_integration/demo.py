"""End-to-end Phase 1 demo: eligibility fan-out and one order per provider.

Run after ``make up`` (the webhook container applies the Alembic migration)::

    uv run python -m aintegrator_integration.demo
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

from aintegrator_integration.config import Settings, get_settings
from aintegrator_integration.domain import Address, Customer, EligibilityStatus, OrderIntent
from aintegrator_integration.factory import build_service

__all__ = ["main", "run"]

ADDRESS = Address(
    street="Fictional Street", number="12", postal_code="28001", city="Madrid", country="ES"
)
CUSTOMER = Customer(name="Ada Example", email="ada@example.invalid")


async def run(settings: Settings | None = None) -> None:
    service = build_service(settings or get_settings())
    try:
        results = await service.check_eligibility(ADDRESS)
        print("Eligibility:")
        for result in results:
            if result.status is EligibilityStatus.OK:
                detail = f"{len(result.offers)} offers"
            else:
                detail = result.error or ""
            print(f"  {result.provider:<10} {result.status.value:<12} {detail}")

        print("Orders:")
        for result in results:
            if result.status is not EligibilityStatus.OK or not result.offers:
                continue
            intent = OrderIntent(
                offer=result.offers[0],
                address=ADDRESS,
                customer=CUSTOMER,
                idempotency_key=f"demo-{uuid4()}",
                client_reference=f"demo-{uuid4()}",
            )
            order = await service.create_order(intent)
            print(
                f"  {order.provider:<10} {order.order_id} "
                f"provider={order.provider_order_id} status={order.status.value}"
            )
    finally:
        await service.aclose()


def main() -> int:
    asyncio.run(run())
    return 0


if __name__ == "__main__":  # pragma: no cover - manual entry point
    raise SystemExit(main())
