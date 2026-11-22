"""Django admin with operational actions for simulated orders and outbox events."""

from __future__ import annotations

import uuid
from typing import Any

from django.contrib import admin
from django.http import HttpRequest
from django.utils import timezone

from .models import AvailabilityRequest, Order, OrderStatus, OutboxEvent, OutboxStatus
from .worker import force_order_status


def _force_status(modeladmin: Any, request: HttpRequest, queryset: Any, status: str) -> None:
    for order in queryset:
        force_order_status(order, status)
    modeladmin.message_user(request, f"Forced {queryset.count()} order(s) to {status}.")


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    """List orders and force their lifecycle status."""

    list_display = ("order_id", "client_reference", "status", "offer_id", "updated_at")
    list_filter = ("status",)
    search_fields = ("order_id", "client_reference")
    readonly_fields = ("created_at", "updated_at")
    actions = ("force_confirmed", "force_working", "force_done", "force_declined")

    @admin.action(description="Force status to CONFIRMED")
    def force_confirmed(self, request: HttpRequest, queryset: Any) -> None:
        _force_status(self, request, queryset, str(OrderStatus.CONFIRMED.value))

    @admin.action(description="Force status to WORKING")
    def force_working(self, request: HttpRequest, queryset: Any) -> None:
        _force_status(self, request, queryset, str(OrderStatus.WORKING.value))

    @admin.action(description="Force status to DONE")
    def force_done(self, request: HttpRequest, queryset: Any) -> None:
        _force_status(self, request, queryset, str(OrderStatus.DONE.value))

    @admin.action(description="Force status to DECLINED")
    def force_declined(self, request: HttpRequest, queryset: Any) -> None:
        _force_status(self, request, queryset, str(OrderStatus.DECLINED.value))


@admin.register(OutboxEvent)
class OutboxEventAdmin(admin.ModelAdmin):
    """Inspect webhooks and resend, duplicate or drop them."""

    list_display = ("type", "order_reference", "sequence", "status", "attempts", "created_at")
    list_filter = ("type", "status")
    search_fields = ("order_reference", "type")
    readonly_fields = ("event_id", "occurred_at", "created_at")
    actions = ("resend_events", "duplicate_events", "drop_events")

    @admin.action(description="Resend selected events")
    def resend_events(self, request: HttpRequest, queryset: Any) -> None:
        queryset.update(
            status=OutboxStatus.PENDING, attempts=0, next_attempt_at=None, last_error=""
        )
        self.message_user(request, f"Queued {queryset.count()} event(s) for redelivery.")

    @admin.action(description="Duplicate selected events")
    def duplicate_events(self, request: HttpRequest, queryset: Any) -> None:
        for event in queryset:
            OutboxEvent.objects.create(
                event_id=uuid.uuid4(),
                type=event.type,
                occurred_at=timezone.now(),
                sequence=event.sequence,
                order_reference=event.order_reference,
                payload=event.payload,
                status=OutboxStatus.PENDING,
            )
        self.message_user(request, f"Duplicated {queryset.count()} event(s).")

    @admin.action(description="Drop selected events")
    def drop_events(self, request: HttpRequest, queryset: Any) -> None:
        queryset.update(status=OutboxStatus.DROPPED, last_error="dropped by admin")
        self.message_user(request, f"Dropped {queryset.count()} event(s).")


@admin.register(AvailabilityRequest)
class AvailabilityRequestAdmin(admin.ModelAdmin):
    """Read-only view of asynchronous availability lookups."""

    list_display = ("request_id", "status", "due_at", "updated_at")
    list_filter = ("status",)
    readonly_fields = ("request_id", "created_at", "updated_at")
