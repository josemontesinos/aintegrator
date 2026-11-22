"""Unit tests for webhook-delivery fault transformations."""

from aintegrator_provider_common import (
    FaultDecision,
    FaultKind,
    WebhookEnvelope,
    apply_delivery_fault,
)


def _events() -> tuple[WebhookEnvelope, ...]:
    return (
        WebhookEnvelope("e1", "o1", 1, "order.confirmed", {}),
        WebhookEnvelope("e2", "o1", 2, "order.working", {}),
    )


def _decision(kind: FaultKind, **params: object) -> FaultDecision:
    return FaultDecision(kind, params, "webhook", 0, 0)


def test_no_fault_keeps_events() -> None:
    plan = apply_delivery_fault(_events(), None)
    assert plan.events == _events()
    assert plan.delay_ms == 0
    assert not plan.dropped


def test_duplicate_adds_event_with_new_id() -> None:
    plan = apply_delivery_fault(_events(), _decision(FaultKind.WEBHOOK_DUPLICATE))
    assert len(plan.events) == 3
    assert plan.events[-1].event_id == "e2-dup"
    assert plan.events[-1].payload == _events()[-1].payload


def test_reorder_swaps_first_two() -> None:
    plan = apply_delivery_fault(_events(), _decision(FaultKind.WEBHOOK_REORDER))
    assert [event.event_id for event in plan.events] == ["e2", "e1"]


def test_drop_removes_last_event() -> None:
    plan = apply_delivery_fault(_events(), _decision(FaultKind.WEBHOOK_DROP))
    assert [event.event_id for event in plan.events] == ["e1"]
    assert plan.dropped


def test_delay_uses_params() -> None:
    plan = apply_delivery_fault(_events(), _decision(FaultKind.WEBHOOK_DELAY, delay_ms=250))
    assert plan.delay_ms == 250
