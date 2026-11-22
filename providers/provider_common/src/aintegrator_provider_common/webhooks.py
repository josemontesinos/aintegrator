"""Deterministic transformations for simulated webhook delivery faults."""

from __future__ import annotations

from dataclasses import dataclass, replace

from aintegrator_provider_common.faults import FaultDecision, FaultKind

__all__ = ["WebhookEnvelope", "apply_delivery_fault"]


@dataclass(frozen=True, slots=True)
class WebhookEnvelope:
    """Minimal description of an outgoing webhook, independent of transport."""

    event_id: str
    order_reference: str
    sequence: int
    event_type: str
    payload: dict[str, object]


@dataclass(frozen=True, slots=True)
class DeliveryPlan:
    """What the delivery loop should actually send."""

    events: tuple[WebhookEnvelope, ...]
    delay_ms: int = 0
    dropped: bool = False


def apply_delivery_fault(
    events: tuple[WebhookEnvelope, ...], decision: FaultDecision | None
) -> DeliveryPlan:
    """Apply duplicate/reorder/drop/delay decisions to a batch of events.

    Latency and other non-webhook kinds are ignored: they belong to request handling.
    """
    if decision is None or not events:
        return DeliveryPlan(events=events)

    kind = decision.kind
    if kind is FaultKind.WEBHOOK_DUPLICATE:
        duplicate = replace(events[-1], event_id=f"{events[-1].event_id}-dup")
        return DeliveryPlan(events=(*events, duplicate))
    if kind is FaultKind.WEBHOOK_REORDER and len(events) > 1:
        reordered = list(events)
        reordered[0], reordered[1] = reordered[1], reordered[0]
        return DeliveryPlan(events=tuple(reordered))
    if kind is FaultKind.WEBHOOK_DROP:
        return DeliveryPlan(events=tuple(events[:-1]), dropped=True)
    if kind is FaultKind.WEBHOOK_DELAY:
        return DeliveryPlan(events=events, delay_ms=int(decision.params.get("delay_ms", 1000)))
    return DeliveryPlan(events=events)
