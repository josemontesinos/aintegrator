"""Reconciler CLI and loop: converge local state with provider truth.

The DB-driven reconciler is the authoritative convergence mechanism: it works
even when every webhook was dropped. Every NEEDS_REVIEW order is checked on each
pass, plus every non-terminal order older than RECONCILE_AFTER_S.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime

from aintegrator_observability import configure_tracing

from aintegrator_integration.config import Settings
from aintegrator_integration.factory import get_service
from aintegrator_integration.persistence import Repository, session_scope
from aintegrator_integration.service import ConnectivityService

__all__ = ["main", "reconcile_once"]


async def reconcile_once(
    service: ConnectivityService | None = None,
    settings: Settings | None = None,
) -> list[str]:
    active = service or get_service()
    config = settings or active.settings
    async with session_scope(active.session_maker) as session:
        repository = Repository(session)
        orders = await repository.orders_needing_review()
        orders.extend(
            await repository.orders_due_for_reconciliation(
                datetime.now(UTC), config.reconcile_after_s
            )
        )
    seen: set[str] = set()
    result: list[str] = []
    for order in orders:
        if str(order.order_id) in seen:
            continue
        seen.add(str(order.order_id))
        updated = await active.reconcile_order(order)
        result.append(f"{order.order_id}: {order.status.value} -> {updated.status.value}")
    return result


async def _loop(interval_s: float, service: ConnectivityService) -> None:
    while True:
        changes = await reconcile_once(service)
        if changes:
            print("\n".join(changes), flush=True)
        await asyncio.sleep(interval_s)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aintegrator-reconcile")
    parser.add_argument("--once", action="store_true", help="run a single pass and exit")
    parser.add_argument("--interval", type=float, default=10.0, help="loop interval in seconds")
    args = parser.parse_args(argv)
    configure_tracing(service_name="aintegrator-reconciler")
    service = get_service()
    if args.once:
        changes = asyncio.run(reconcile_once(service))
        print("\n".join(changes) if changes else "nothing to reconcile")
    else:
        asyncio.run(_loop(args.interval, service))
    return 0


if __name__ == "__main__":  # pragma: no cover - manual entry point
    raise SystemExit(main())
