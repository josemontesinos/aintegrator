"""Control endpoints and Django admin registration tests."""

from __future__ import annotations

from aintegrator_skylink.admin import OrderAdmin, OutboxEventAdmin
from aintegrator_skylink.models import Order, OutboxEvent
from aintegrator_skylink.runtime import get_injector
from django.contrib import admin
from django.test import TestCase
from rest_framework.test import APIClient


class SkylinkControlTests(TestCase):
    """Local control endpoints need no API key and manipulate runtime state."""

    def setUp(self) -> None:
        self.client = APIClient()
        get_injector().reset()

    def test_faults_control_state_and_reset(self) -> None:
        profile = {
            "seed": 99,
            "rules": [
                {
                    "operation": "orders.create",
                    "kind": "http_error",
                    "probability": 1.0,
                    "params": {"status": 502},
                }
            ],
        }
        response = self.client.post("/_control/faults", profile, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["faults"]["seed"], 99)
        self.assertEqual(get_injector().profile.rules[0].operation, "orders.create")

        state = self.client.get("/_control/state")
        self.assertEqual(state.status_code, 200)
        self.assertEqual(state.data["data"]["faults"], response.data["data"]["faults"])
        self.assertIn("orders.create", state.data["data"]["callCounts"])
        self.assertIn("workerEnabled", state.data["data"])

        bad = self.client.post(
            "/_control/faults", {"rules": [{"kind": "not-a-kind"}]}, format="json"
        )
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(bad.data["errors"][0]["code"], "validation_error")

        reset = self.client.post("/_control/reset")
        self.assertEqual(reset.status_code, 200)
        self.assertTrue(reset.data["data"]["reset"])
        self.assertEqual(get_injector().profile.rules, [])

    def test_advance_and_reset_clear_all_state(self) -> None:
        created = self.client.post(
            "/api/v2/availability-requests",
            {"address": {"street": "1 Example Road"}},
            format="json",
            HTTP_X_API_KEY="skylink-key",
        )
        self.assertEqual(created.status_code, 202)
        advanced = self.client.post("/_control/advance")
        self.assertEqual(advanced.data["data"]["availabilityCompleted"], 1)
        self.assertEqual(advanced.data["data"]["eventsCreated"], 1)
        self.assertEqual(OutboxEvent.objects.count(), 1)

        self.client.post("/_control/reset")
        self.assertEqual(OutboxEvent.objects.count(), 0)
        self.assertEqual(Order.objects.count(), 0)
        state = self.client.get("/_control/state")
        self.assertEqual(state.data["data"]["outboxEvents"], [])

    def test_admin_registration_and_actions(self) -> None:
        self.assertTrue(admin.site.is_registered(Order))
        self.assertTrue(admin.site.is_registered(OutboxEvent))
        order_admin = admin.site._registry[Order]
        outbox_admin = admin.site._registry[OutboxEvent]
        self.assertIsInstance(order_admin, OrderAdmin)
        self.assertIsInstance(outbox_admin, OutboxEventAdmin)
        self.assertTrue(
            {"force_confirmed", "force_working", "force_done", "force_declined"}.issubset(
                order_admin.actions
            )
        )
        self.assertTrue(
            {"resend_events", "duplicate_events", "drop_events"}.issubset(outbox_admin.actions)
        )
