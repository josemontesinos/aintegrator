"""Signed webhook serialization, delivery and fault-plan application."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass, replace
from secrets import compare_digest
from time import sleep

import httpx
from aintegrator_provider_common import FaultKind, WebhookEnvelope, apply_delivery_fault
from django.conf import settings

from .models import OutboxEvent, OutboxStatus
from .runtime import WEBHOOK_OPERATION, get_injector

_MAX_DELAY_S = 5.0
_MAX_RETRY_SLEEP_S = 2.0


@dataclass(frozen=True, slots=True)
class DeliveryStats:
    """Summary of one delivery batch (also printed by the management command)."""

    delivered: int = 0
    failed: int = 0
    dropped: int = 0
    duplicated: int = 0
    reordered: bool = False
    delayed_ms: int = 0
    requests: int = 0
    sent_event_ids: tuple[str, ...] = ()


def sign_body(
    raw_body: bytes,
    *,
    timestamp: int | None = None,
    secret: str | None = None,
) -> str:
    """Build ``X-Skylink-Signature: t=<unix>,v1=<hex HMAC-SHA256>``."""
    moment = int(time.time()) if timestamp is None else timestamp
    key = settings.SKYLINK_WEBHOOK_SECRET if secret is None else secret
    message = f"{moment}.".encode() + raw_body
    digest = hmac.new(key.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return f"t={moment},v1={digest}"


def verify_signature(
    header: str,
    raw_body: bytes,
    *,
    secret: str | None = None,
    tolerance_s: int | None = None,
) -> bool:
    """Validate a signature header; ``tolerance_s`` optionally enforces freshness."""
    parts = dict(part.split("=", 1) for part in header.split(",") if "=" in part)
    if "t" not in parts or "v1" not in parts:
        return False
    try:
        timestamp = int(parts["t"])
    except ValueError:
        return False
    if tolerance_s is not None and abs(int(time.time()) - timestamp) > tolerance_s:
        return False
    expected = sign_body(raw_body, timestamp=timestamp, secret=secret)
    return compare_digest(parts["v1"], expected.split("v1=", 1)[1])


def serialize_event(event: OutboxEvent) -> bytes:
    """Serialize the stored payload into the exact bytes that are signed and sent."""
    return json.dumps(event.payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def webhook_envelope(event: OutboxEvent) -> WebhookEnvelope:
    """Adapt a database event to the framework-independent fault-plan type."""
    return WebhookEnvelope(
        event_id=str(event.event_id),
        order_reference=event.order_reference,
        sequence=event.sequence,
        event_type=event.type,
        payload=event.payload,
    )


def _deliver_batch(
    client: httpx.Client,
    *,
    max_events: int,
    apply_faults: bool,
) -> DeliveryStats:
    pending = list(OutboxEvent.objects.filter(status=OutboxStatus.PENDING)[:max_events])
    if not pending:
        return DeliveryStats()

    envelopes = tuple(webhook_envelope(event) for event in pending)
    decision = get_injector().decide(WEBHOOK_OPERATION) if apply_faults else None
    plan = apply_delivery_fault(envelopes, decision)
    if plan.delay_ms:
        sleep(min(plan.delay_ms / 1000, _MAX_DELAY_S))

    stats = DeliveryStats(
        duplicated=sum(1 for envelope in plan.events if envelope.event_id.endswith("-dup")),
        reordered=decision is not None
        and decision.kind is FaultKind.WEBHOOK_REORDER
        and len(plan.events) > 1,
        delayed_ms=plan.delay_ms,
    )
    by_id = {str(event.event_id): event for event in pending}
    sends: list[tuple[WebhookEnvelope, OutboxEvent]] = []
    for envelope in plan.events:
        source = by_id.get(envelope.event_id)
        if source is None:
            source = by_id.get(envelope.event_id.removesuffix("-dup"))
        if source is not None:
            sends.append((envelope, source))

    if plan.dropped and pending:
        dropped = pending[-1]
        dropped.status = OutboxStatus.DROPPED
        dropped.last_error = "dropped by fault injection"
        dropped.save(update_fields=["status", "last_error"])
        stats = replace(stats, dropped=1)

    url = settings.SKYLINK_WEBHOOK_URL
    secret = settings.SKYLINK_WEBHOOK_SECRET
    max_attempts = settings.SKYLINK_MAX_DELIVERY_ATTEMPTS
    retry_base = settings.SKYLINK_DELIVERY_RETRY_BASE_S
    sent_ids: list[str] = []
    for _envelope, event in sends:
        sent_ids.append(str(event.event_id))
        raw_body = serialize_event(event)
        headers = {
            "Content-Type": "application/json",
            "X-Skylink-Signature": sign_body(raw_body, secret=secret),
        }
        last_error = "delivery not attempted"
        for attempt in range(1, max_attempts + 1):
            event.attempts = attempt
            event.save(update_fields=["attempts"])
            stats = replace(stats, requests=stats.requests + 1)
            try:
                response = client.post(url, content=raw_body, headers=headers)
            except httpx.HTTPError as exc:
                last_error = str(exc)
            else:
                if 200 <= response.status_code < 300:
                    event.status = OutboxStatus.DELIVERED
                    event.last_error = ""
                    event.next_attempt_at = None
                    event.save(update_fields=["status", "last_error", "next_attempt_at"])
                    stats = replace(stats, delivered=stats.delivered + 1)
                    break
                last_error = f"HTTP {response.status_code}"
            if attempt < max_attempts:
                sleep(min(retry_base * 2 ** (attempt - 1), _MAX_RETRY_SLEEP_S))
        else:
            event.status = OutboxStatus.FAILED
            event.last_error = last_error
            event.next_attempt_at = None
            event.save(update_fields=["status", "last_error", "next_attempt_at"])
            stats = replace(stats, failed=stats.failed + 1)
    return replace(stats, sent_event_ids=tuple(sent_ids))


def deliver_pending_events(
    *,
    client: httpx.Client | None = None,
    max_events: int | None = None,
    apply_faults: bool = True,
) -> DeliveryStats:
    """Sign and POST one batch of pending events, retrying non-2xx responses.

    A caller-supplied ``httpx.Client`` is not closed, which lets tests use a mock
    transport. Otherwise a short-lived client is created here.
    """
    batch_size = max_events if max_events is not None else settings.SKYLINK_DELIVERY_BATCH_SIZE
    if client is not None:
        return _deliver_batch(client, max_events=batch_size, apply_faults=apply_faults)
    with httpx.Client(timeout=5.0) as owned_client:
        return _deliver_batch(owned_client, max_events=batch_size, apply_faults=apply_faults)
