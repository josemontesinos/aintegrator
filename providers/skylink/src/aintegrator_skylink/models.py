"""Persistent state for the simulated SkyLink provider.

All identifiers, addresses and offers are fictional. Timestamps exposed by the
API are epoch milliseconds; this module holds the shared conversion helper.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from django.db import models
from django.utils import timezone


def epoch_ms(value: datetime) -> int:
    """Convert an aware datetime to epoch milliseconds (the API wire format)."""
    return int(value.timestamp() * 1000)


def format_money(cents: int, currency: str = "EUR") -> dict[str, str]:
    """Format an integer amount of cents as the provider's money object."""
    return {"amount": f"{cents / 100:.2f}", "currency": currency}


class AvailabilityStatus(models.TextChoices):
    """Statuses used by availability requests (a subset of the order statuses)."""

    QUEUED = "QUEUED", "Queued"
    DONE = "DONE", "Done"


class OrderStatus(models.TextChoices):
    """Lifecycle of a simulated order."""

    QUEUED = "QUEUED", "Queued"
    CONFIRMED = "CONFIRMED", "Confirmed"
    WORKING = "WORKING", "Working"
    DONE = "DONE", "Done"
    DECLINED = "DECLINED", "Declined"


class OutboxStatus(models.TextChoices):
    """Delivery state of one signed webhook event."""

    PENDING = "PENDING", "Pending"
    DELIVERED = "DELIVERED", "Delivered"
    FAILED = "FAILED", "Failed"
    DROPPED = "DROPPED", "Dropped"


class CreatedAtModel(models.Model):
    """Abstract base adding a creation timestamp."""

    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        abstract = True


class AvailabilityRequest(CreatedAtModel):
    """An asynchronous availability lookup delivered via ``availability.completed``."""

    request_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    address = models.JSONField()
    status = models.CharField(
        max_length=16,
        choices=AvailabilityStatus.choices,
        default=AvailabilityStatus.QUEUED,
        db_index=True,
    )
    due_at = models.DateTimeField()
    result = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True)
    updated_at = models.DateTimeField(default=timezone.now)

    def __str__(self) -> str:
        return f"Availability {self.request_id} ({self.status})"

    def to_data(self) -> dict[str, object]:
        """Return the camelCase API representation of this request."""
        data: dict[str, object] = {
            "requestId": str(self.request_id),
            "status": self.status,
            "address": self.address,
            "notes": self.notes,
            "createdAt": epoch_ms(self.created_at),
            "dueAt": epoch_ms(self.due_at),
        }
        if self.status == AvailabilityStatus.DONE:
            data["offers"] = self.result.get("offers", [])
        return data


class Order(CreatedAtModel):
    """A simulated order; no real product, carrier or customer is contacted."""

    order_id = models.CharField(max_length=32, primary_key=True)
    client_reference = models.CharField(max_length=128, db_index=True)
    offer_id = models.CharField(max_length=128)
    address = models.JSONField()
    customer = models.JSONField()
    status = models.CharField(
        max_length=16,
        choices=OrderStatus.choices,
        default=OrderStatus.QUEUED,
        db_index=True,
    )
    notes = models.TextField(blank=True)
    next_step_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(default=timezone.now)

    def __str__(self) -> str:
        return f"{self.order_id} ({self.status})"

    def to_data(self) -> dict[str, object]:
        """Return the camelCase API representation of this order."""
        return {
            "orderId": self.order_id,
            "clientReference": self.client_reference,
            "offerId": self.offer_id,
            "address": self.address,
            "customer": self.customer,
            "status": self.status,
            "notes": self.notes,
            "createdAt": epoch_ms(self.created_at),
            "updatedAt": epoch_ms(self.updated_at),
        }

    def event_summary(self) -> list[dict[str, object]]:
        """Return a short summary of emitted webhook events for polling clients."""
        return [
            {
                "eventId": str(event.event_id),
                "type": event.type,
                "sequence": event.sequence,
                "occurredAt": epoch_ms(event.occurred_at),
                "deliveryStatus": event.status,
            }
            for event in OutboxEvent.objects.filter(order_reference=self.order_id)
        ]


class OutboxEvent(CreatedAtModel):
    """One webhook event waiting to be signed and delivered.

    ``sequence`` is monotonic per ``order_reference`` (an order id or an
    availability request id). ``payload`` is the exact JSON body that is signed
    and sent by ``deliver_webhooks``.
    """

    event_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    type = models.CharField(max_length=64, db_index=True)
    occurred_at = models.DateTimeField(default=timezone.now)
    sequence = models.PositiveIntegerField(default=0)
    order_reference = models.CharField(max_length=128, db_index=True)
    payload = models.JSONField(default=dict)
    status = models.CharField(
        max_length=16,
        choices=OutboxStatus.choices,
        default=OutboxStatus.PENDING,
        db_index=True,
    )
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        indexes = (
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["order_reference", "sequence"]),
        )
        ordering = ("created_at", "sequence")

    def __str__(self) -> str:
        return f"{self.type} #{self.sequence} for {self.order_reference}"
