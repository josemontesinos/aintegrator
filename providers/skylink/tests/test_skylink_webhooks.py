"""Webhook signing, at-least-once delivery faults and retry tests."""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import patch

import httpx
from aintegrator_provider_common import FaultProfile, FaultRule
from aintegrator_skylink.management.commands import deliver_webhooks as command_module
from aintegrator_skylink.models import Order, OrderStatus, OutboxEvent, OutboxStatus
from aintegrator_skylink.runtime import get_injector
from aintegrator_skylink.webhooks import (
    DeliveryStats,
    deliver_pending_events,
    serialize_event,
    sign_body,
    verify_signature,
)
from aintegrator_skylink.worker import force_order_status
from django.conf import settings
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

WEBHOOK_URL = "https://skylink.test/webhooks/receiver"


class SkylinkWebhookTests(TestCase):
    """Delivery tests use an in-memory httpx transport, never a network service."""

    def setUp(self) -> None:
        get_injector().reset()

    @staticmethod
    def _make_events(count: int = 1) -> list[OutboxEvent]:
        order = Order.objects.create(
            order_id="C-TESTORDER1",
            client_reference="webhook-ref",
            offer_id="SL-TEST0001",
            address={"city": "Nimbus City"},
            customer={"name": "Test Example"},
            status=OrderStatus.QUEUED,
            next_step_at=timezone.now(),
        )
        for status in (OrderStatus.CONFIRMED, OrderStatus.WORKING)[:count]:
            force_order_status(order, status)
        return list(OutboxEvent.objects.filter(order_reference=order.order_id).order_by("sequence"))

    @staticmethod
    def _set_delivery_fault(kind: str, **params: object) -> None:
        get_injector().set_profile(
            FaultProfile(
                seed=7,
                rules=[
                    FaultRule(
                        operation="webhook.delivery", kind=kind, probability=1.0, params=params
                    )
                ],
            )
        )

    @staticmethod
    def _client(handler: object) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler), base_url=WEBHOOK_URL)

    def test_signature_format_and_verification(self) -> None:
        raw_body = b'{"eventId":"abc","type":"order.done"}'
        header = sign_body(raw_body, timestamp=1_700_000_000, secret="unit-secret")
        expected_digest = hmac.new(
            b"unit-secret",
            b"1700000000." + raw_body,
            hashlib.sha256,
        ).hexdigest()
        self.assertEqual(header, f"t=1700000000,v1={expected_digest}")
        self.assertTrue(verify_signature(header, raw_body, secret="unit-secret"))
        self.assertFalse(verify_signature(header, raw_body + b" ", secret="unit-secret"))
        self.assertFalse(verify_signature(header, raw_body, secret="other-secret"))
        self.assertFalse(verify_signature("garbage", raw_body, secret="unit-secret"))
        self.assertFalse(verify_signature(header, raw_body, secret="unit-secret", tolerance_s=1))

    def test_delivery_signs_exact_raw_body(self) -> None:
        event = self._make_events()[0]
        sent: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            sent.append(request)
            return httpx.Response(202, json={"received": True})

        with override_settings(SKYLINK_WEBHOOK_URL=WEBHOOK_URL), self._client(handler) as client:
            stats = deliver_pending_events(client=client)
        self.assertEqual(stats.delivered, 1)
        self.assertEqual(sent[0].url, httpx.URL(WEBHOOK_URL))
        self.assertEqual(sent[0].content, serialize_event(event))
        self.assertTrue(
            verify_signature(
                sent[0].headers["X-Skylink-Signature"],
                sent[0].content,
                secret=settings.SKYLINK_WEBHOOK_SECRET,
            )
        )
        event.refresh_from_db()
        self.assertEqual(event.status, OutboxStatus.DELIVERED)
        self.assertEqual(event.attempts, 1)

    def test_duplicate_delivery_plan(self) -> None:
        event = self._make_events()[0]
        self._set_delivery_fault("webhook_duplicate")
        sent: list[httpx.Request] = []
        with (
            override_settings(SKYLINK_WEBHOOK_URL=WEBHOOK_URL),
            self._client(lambda request: (sent.append(request), httpx.Response(200))[1]) as client,
        ):
            stats = deliver_pending_events(client=client)
        self.assertEqual(len(sent), 2)
        self.assertEqual(sent[0].content, sent[1].content)
        self.assertEqual(stats.duplicated, 1)
        self.assertEqual(stats.delivered, 2)
        event.refresh_from_db()
        self.assertEqual(event.status, OutboxStatus.DELIVERED)

    def test_reorder_delivery_plan(self) -> None:
        self._make_events(count=2)
        self._set_delivery_fault("webhook_reorder")
        sequences: list[int] = []
        with (
            override_settings(SKYLINK_WEBHOOK_URL=WEBHOOK_URL),
            self._client(
                lambda request: (
                    sequences.append(json.loads(request.content)["sequence"]),
                    httpx.Response(200),
                )[1]
            ) as client,
        ):
            stats = deliver_pending_events(client=client)
        self.assertEqual(sequences, [2, 1])
        self.assertTrue(stats.reordered)

    def test_drop_delivery_plan(self) -> None:
        event = self._make_events()[0]
        self._set_delivery_fault("webhook_drop")
        sent: list[httpx.Request] = []
        with (
            override_settings(SKYLINK_WEBHOOK_URL=WEBHOOK_URL),
            self._client(lambda request: (sent.append(request), httpx.Response(200))[1]) as client,
        ):
            stats = deliver_pending_events(client=client)
        self.assertEqual(sent, [])
        self.assertEqual(stats.dropped, 1)
        event.refresh_from_db()
        self.assertEqual(event.status, OutboxStatus.DROPPED)

    def test_delay_delivery_plan_is_patchable(self) -> None:
        self._make_events()
        self._set_delivery_fault("webhook_delay", delay_ms=10)
        with (
            override_settings(SKYLINK_WEBHOOK_URL=WEBHOOK_URL),
            self._client(lambda request: httpx.Response(200)) as client,
            patch("aintegrator_skylink.webhooks.sleep") as mocked_sleep,
        ):
            stats = deliver_pending_events(client=client)
        mocked_sleep.assert_called_once_with(0.01)
        self.assertEqual(stats.delayed_ms, 10)

    def test_non_2xx_is_retried_five_times(self) -> None:
        event = self._make_events()[0]
        sent: list[httpx.Request] = []
        with (
            override_settings(SKYLINK_WEBHOOK_URL=WEBHOOK_URL),
            self._client(lambda request: (sent.append(request), httpx.Response(500))[1]) as client,
            patch("aintegrator_skylink.webhooks.sleep"),
        ):
            stats = deliver_pending_events(client=client)
        self.assertEqual(len(sent), 5)
        self.assertEqual(stats.failed, 1)
        event.refresh_from_db()
        self.assertEqual(event.status, OutboxStatus.FAILED)
        self.assertEqual(event.attempts, 5)
        self.assertIn("HTTP 500", event.last_error)

    def test_delivery_command_loop_respects_max_batches(self) -> None:
        with (
            patch.object(
                command_module, "deliver_pending_events", return_value=DeliveryStats()
            ) as deliver,
            patch.object(command_module, "sleep") as mocked_sleep,
        ):
            call_command("deliver_webhooks", loop=True, interval=0, max_batches=2)
        self.assertEqual(deliver.call_count, 2)
        mocked_sleep.assert_called_once_with(0)
