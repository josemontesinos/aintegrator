"""Deterministic state advancement and the in-process worker thread."""

from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from aintegrator_provider_common.determinism import digest_int, pick, rate_from_hash, seeded_hash
from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .models import (
    AvailabilityRequest,
    AvailabilityStatus,
    Order,
    OrderStatus,
    OutboxEvent,
    OutboxStatus,
    epoch_ms,
    format_money,
)

logger = logging.getLogger(__name__)

PLAN_NAMES = ("SkyLink Breeze", "SkyLink Drift", "SkyLink Halo", "SkyLink Lantern")
TECHNOLOGIES = ("fictional-fiber", "fictional-wireless", "fictional-mesh")

_TERMINAL_STATUSES = frozenset({OrderStatus.DONE.value, OrderStatus.DECLINED.value})
_EVENT_TYPES: dict[str, str] = {
    OrderStatus.CONFIRMED.value: "order.confirmed",
    OrderStatus.WORKING.value: "order.working",
    OrderStatus.DONE.value: "order.done",
    OrderStatus.DECLINED.value: "order.declined",
}


@dataclass(frozen=True, slots=True)
class AdvanceResult:
    """Counts from one advancement pass, handy for control endpoints and tests."""

    availability_completed: int = 0
    orders_advanced: int = 0
    events_created: int = 0


def _address_key(address: object) -> str:
    """Normalize any JSON-ish address into a stable hashing input."""
    if isinstance(address, str):
        return address
    return json.dumps(address, sort_keys=True, default=str)


def offers_for_address(address: object) -> list[dict[str, object]]:
    """Derive 2-3 fictional offers deterministically from ``(seed, address)``."""
    key = _address_key(address)
    seed = settings.SKYLINK_SEED
    count = 2 + digest_int(seed, "offer-count", key) % 2
    offers: list[dict[str, object]] = []
    for index in range(count):
        name = pick(PLAN_NAMES, seed, "plan", key, index)
        offer_id = seeded_hash(seed, "offer-id", key, index).hex()[:10].upper()
        price_cents = rate_from_hash(seed, "price", key, index, minimum=2990, maximum=8990)
        eta_days = rate_from_hash(seed, "eta", key, index, minimum=2, maximum=14)
        offers.append(
            {
                "offerId": f"SL-{offer_id}",
                "name": name,
                "technology": pick(TECHNOLOGIES, seed, "technology", key, index),
                "price": format_money(price_cents),
                "installmentDays": eta_days,
                "notes": f"Fictional {name} plan; no real network is contacted.",
            }
        )
    return offers


def order_outcome(order: Order) -> str:
    """Choose the terminal status deterministically; 'decline' in an address forces DECLINED."""
    if "decline" in _address_key(order.address).lower():
        return str(OrderStatus.DECLINED.value)
    if digest_int(settings.SKYLINK_SEED, "order-outcome", order.order_id) % 11 == 0:
        return str(OrderStatus.DECLINED.value)
    return str(OrderStatus.DONE.value)


def create_outbox_event(
    event_type: str, order_reference: str, payload: dict[str, object]
) -> OutboxEvent:
    """Create a pending event with the next sequence number for this reference."""
    maximum = OutboxEvent.objects.filter(order_reference=order_reference).aggregate(Max("sequence"))
    sequence = int(maximum["sequence__max"] or 0) + 1
    occurred_at = timezone.now()
    event_id = uuid.uuid4()
    body: dict[str, object] = {
        "eventId": str(event_id),
        "type": event_type,
        "occurredAt": epoch_ms(occurred_at),
        "sequence": sequence,
        "orderReference": order_reference,
        "data": payload,
    }
    return OutboxEvent.objects.create(
        event_id=event_id,
        type=event_type,
        occurred_at=occurred_at,
        sequence=sequence,
        order_reference=order_reference,
        payload=body,
        status=OutboxStatus.PENDING,
    )


def advance_order(order: Order, *, now: datetime | None = None) -> OutboxEvent | None:
    """Advance one order by a single lifecycle step and emit its webhook event."""
    moment = now or timezone.now()
    if order.status == OrderStatus.QUEUED.value:
        target = str(OrderStatus.CONFIRMED.value)
        event_type = _EVENT_TYPES[target]
    elif order.status == OrderStatus.CONFIRMED.value:
        target = str(OrderStatus.WORKING.value)
        event_type = _EVENT_TYPES[target]
    elif order.status == OrderStatus.WORKING.value:
        target = order_outcome(order)
        event_type = _EVENT_TYPES[target]
    else:
        return None
    previous = order.status
    order.status = target
    order.updated_at = moment
    order.next_step_at = (
        None
        if target in _TERMINAL_STATUSES
        else moment + timedelta(seconds=settings.SKYLINK_ORDER_STEP_DELAY_S)
    )
    order.save(update_fields=["status", "updated_at", "next_step_at"])
    payload = order.to_data()
    payload["previousStatus"] = previous
    return create_outbox_event(event_type, order.order_id, payload)


def force_order_status(order: Order, status: str, *, emit_event: bool = True) -> OutboxEvent | None:
    """Force an order into ``status`` (used by control endpoints and admin actions)."""
    order.status = status
    order.updated_at = timezone.now()
    order.next_step_at = None if status in _TERMINAL_STATUSES else order.next_step_at
    order.save(update_fields=["status", "updated_at", "next_step_at"])
    if not emit_event or status not in _EVENT_TYPES:
        return None
    payload = order.to_data()
    payload["forced"] = True
    return create_outbox_event(_EVENT_TYPES[status], order.order_id, payload)


def advance_pending(*, force: bool = False, now: datetime | None = None) -> AdvanceResult:
    """Advance due requests/orders and create their webhook events.

    With ``force=True`` every pending state is advanced, ignoring due times; the
    control endpoint uses this to make the whole lifecycle observable instantly.
    """
    moment = now or timezone.now()
    result = AdvanceResult()
    with transaction.atomic():
        availability = AvailabilityRequest.objects.filter(status=AvailabilityStatus.QUEUED)
        if not force:
            availability = availability.filter(due_at__lte=moment)
        for request in availability.select_for_update():
            offers = offers_for_address(request.address)
            request.result = {
                "requestId": str(request.request_id),
                "status": AvailabilityStatus.DONE,
                "offers": offers,
                "notes": request.notes,
            }
            request.status = AvailabilityStatus.DONE
            request.updated_at = moment
            request.save(update_fields=["result", "status", "updated_at"])
            create_outbox_event("availability.completed", str(request.request_id), request.result)
            result = replace(
                result,
                availability_completed=result.availability_completed + 1,
                events_created=result.events_created + 1,
            )

        orders = Order.objects.filter(
            status__in=[OrderStatus.QUEUED, OrderStatus.CONFIRMED, OrderStatus.WORKING]
        )
        if not force:
            orders = orders.filter(next_step_at__lte=moment)
        for order in orders.select_for_update():
            while True:
                if advance_order(order, now=moment) is None:
                    break
                result = replace(
                    result,
                    orders_advanced=result.orders_advanced + 1,
                    events_created=result.events_created + 1,
                )
                if not force:
                    break
    return result


_worker_lock = threading.Lock()
_worker_started = False
_stop_event = threading.Event()


def _worker_loop() -> None:
    """Poll for due state transitions until asked to stop."""
    while not _stop_event.is_set():
        try:
            advance_pending()
        except Exception:  # a worker must survive transient database errors
            logger.exception("skylink worker advancement failed")
        _stop_event.wait(settings.SKYLINK_WORKER_POLL_INTERVAL_S)


def start_worker() -> threading.Thread | None:
    """Start the daemon worker once per process; return ``None`` if already running."""
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return None
        _stop_event.clear()
        _worker_started = True
    thread = threading.Thread(target=_worker_loop, name="skylink-worker", daemon=True)
    thread.start()
    return thread


def stop_worker(timeout: float = 2.0) -> None:
    """Signal the worker thread to stop (mainly useful for tests)."""
    global _worker_started
    _stop_event.set()
    with _worker_lock:
        _worker_started = False
