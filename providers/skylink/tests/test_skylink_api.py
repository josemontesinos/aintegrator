"""API, envelope and inbound fault-injection tests for provider C."""

from __future__ import annotations

import json
import time
from unittest.mock import patch

from aintegrator_provider_common import FaultProfile
from aintegrator_skylink.models import AvailabilityRequest, Order, OutboxEvent
from aintegrator_skylink.runtime import get_injector
from django.conf import settings
from django.test import TestCase
from rest_framework.test import APIClient

ADDRESS = {"street": "17 Cloudberry Lane", "city": "Nimbus City", "postcode": "2040"}
CUSTOMER = {"name": "Grace Example", "email": "grace@example.invalid"}


class SkylinkApiTests(TestCase):
    """Exercise the public API with the background worker disabled."""

    def setUp(self) -> None:
        self.client = APIClient()
        get_injector().reset()

    def _post(self, path: str, payload: object) -> object:
        return self.client.post(
            path, payload, format="json", HTTP_X_API_KEY=settings.SKYLINK_API_KEY
        )

    def _get(self, path: str) -> object:
        return self.client.get(path, HTTP_X_API_KEY=settings.SKYLINK_API_KEY)

    @staticmethod
    def _body(response: object) -> dict[str, object]:
        data = getattr(response, "data", None)
        assert isinstance(data, dict)
        return data

    def _assert_envelope(self, response: object) -> dict[str, object]:
        body = self._body(response)
        self.assertEqual(set(body), {"data", "meta", "errors"})
        self.assertEqual(body["errors"], [])
        generated_at = body["meta"]["generatedAt"]
        self.assertIsInstance(generated_at, int)
        self.assertLess(abs(int(generated_at) - int(time.time() * 1000)), 5000)
        return body

    @staticmethod
    def _set_fault(kind: str, operation: str = "orders.create", **params: object) -> None:
        get_injector().set_profile(
            FaultProfile.model_validate(
                {
                    "seed": 42,
                    "rules": [
                        {
                            "operation": operation,
                            "kind": kind,
                            "probability": 1.0,
                            "params": params,
                        }
                    ],
                }
            )
        )

    def test_availability_create_poll_and_webhook_event(self) -> None:
        response = self._post(
            "/api/v2/availability-requests",
            {"address": ADDRESS, "notes": "Fictional lookup for test."},
        )
        self.assertEqual(response.status_code, 202)
        body = self._assert_envelope(response)
        created = body["data"]
        request_id = str(created["requestId"])
        self.assertRegex(request_id, r"^[0-9a-f]{8}-[0-9a-f]{4}")
        self.assertEqual(created["status"], "QUEUED")
        self.assertEqual(created["notes"], "Fictional lookup for test.")
        self.assertEqual(AvailabilityRequest.objects.count(), 1)

        queued = self._assert_envelope(self._get(f"/api/v2/availability-requests/{request_id}"))
        self.assertEqual(queued["data"]["status"], "QUEUED")

        advanced = self._body(self.client.post("/_control/advance"))
        self.assertEqual(advanced["data"]["availabilityCompleted"], 1)

        done = self._assert_envelope(self._get(f"/api/v2/availability-requests/{request_id}"))
        self.assertEqual(done["data"]["status"], "DONE")
        offers = done["data"]["offers"]
        self.assertIsInstance(offers, list)
        self.assertGreaterEqual(len(offers), 2)
        for offer in offers:
            self.assertRegex(offer["offerId"], r"^SL-[0-9A-F]{10}$")
            self.assertRegex(offer["price"]["amount"], r"^\d+\.\d{2}$")
            self.assertEqual(offer["price"]["currency"], "EUR")
            self.assertIsInstance(offer["notes"], str)
        event = OutboxEvent.objects.get(type="availability.completed")
        self.assertEqual(event.sequence, 1)
        self.assertEqual(event.order_reference, request_id)
        self.assertEqual(event.payload["eventId"], str(event.event_id))
        self.assertEqual(event.payload["data"]["offers"], offers)

    def test_offers_are_deterministic_for_the_same_address(self) -> None:
        first = self._post("/api/v2/availability-requests", {"address": ADDRESS})
        second = self._post("/api/v2/availability-requests", {"address": ADDRESS})
        self.client.post("/_control/advance")
        first_data = self._get(
            f"/api/v2/availability-requests/{self._body(first)['data']['requestId']}"
        )
        second_data = self._get(
            f"/api/v2/availability-requests/{self._body(second)['data']['requestId']}"
        )
        self.assertEqual(
            self._body(first_data)["data"]["offers"], self._body(second_data)["data"]["offers"]
        )

    def test_order_create_lookup_and_advancement(self) -> None:
        reference = "client-ref-100"
        response = self._post(
            "/api/v2/orders",
            {
                "clientReference": reference,
                "offerId": "SL-ABCDEF0123",
                "address": ADDRESS,
                "customer": CUSTOMER,
            },
        )
        self.assertEqual(response.status_code, 202)
        created = self._assert_envelope(response)["data"]
        self.assertRegex(str(created["orderId"]), r"^C-[0-9A-F]{10}$")
        self.assertEqual(created["status"], "QUEUED")
        self.assertEqual(created["clientReference"], reference)

        found = self._assert_envelope(self._get(f"/api/v2/orders?clientReference={reference}"))
        self.assertEqual(found["meta"]["count"], 1)
        self.assertEqual(found["data"][0]["orderId"], created["orderId"])

        detail = self._assert_envelope(self._get(f"/api/v2/orders/{created['orderId']}"))
        self.assertEqual(detail["data"]["customer"], CUSTOMER)
        self.assertIsInstance(detail["data"]["createdAt"], int)
        self.assertEqual(detail["data"]["webhookEvents"], [])

        advanced = self._body(self.client.post("/_control/advance"))
        self.assertEqual(advanced["data"]["ordersAdvanced"], 3)
        self.assertEqual(advanced["data"]["eventsCreated"], 3)
        final = self._assert_envelope(self._get(f"/api/v2/orders/{created['orderId']}"))
        self.assertIn(final["data"]["status"], ("DONE", "DECLINED"))
        event_types = [event["type"] for event in final["data"]["webhookEvents"]]
        self.assertEqual(
            event_types,
            [
                "order.confirmed",
                "order.working",
                "order.done" if final["data"]["status"] == "DONE" else "order.declined",
            ],
        )
        sequences = [event["sequence"] for event in final["data"]["webhookEvents"]]
        self.assertEqual(sequences, [1, 2, 3])

    def test_api_key_is_required(self) -> None:
        self.assertEqual(self.client.post("/api/v2/orders", {}, format="json").status_code, 401)
        response = self.client.post(
            "/api/v2/orders",
            {},
            format="json",
            HTTP_X_API_KEY="wrong-key",
        )
        self.assertEqual(response.status_code, 401)
        body = self._body(response)
        self.assertEqual(body["errors"][0]["code"], "unauthorized")
        self.assertEqual(Order.objects.count(), 0)

    def test_missing_fields_and_unknown_order(self) -> None:
        response = self._post("/api/v2/orders", {"clientReference": "only-one-field"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._body(response)["errors"][0]["code"], "validation_error")
        missing = self._get("/api/v2/orders/C-UNKNOWN")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(self._body(missing)["errors"][0]["code"], "not_found")

    def test_http_error_and_malformed_injection(self) -> None:
        self._set_fault("http_error", status=503)
        response = self._post(
            "/api/v2/orders",
            {"clientReference": "r", "offerId": "o", "address": ADDRESS, "customer": CUSTOMER},
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self._body(response)["errors"][0]["code"], "injected_http_error")

        self._set_fault("malformed_response")
        response = self._post(
            "/api/v2/orders",
            {"clientReference": "r", "offerId": "o", "address": ADDRESS, "customer": CUSTOMER},
        )
        self.assertEqual(response.status_code, 200)
        with self.assertRaises(json.JSONDecodeError):
            json.loads(response.content)
        self.assertEqual(Order.objects.count(), 0)

    def test_partial_latency_and_timeout_injection(self) -> None:
        self._set_fault("partial_response", operation="availability.create")
        partial = self._post("/api/v2/availability-requests", {"address": ADDRESS})
        self.assertEqual(partial.status_code, 200)
        self.assertTrue(self._body(partial)["meta"]["partial"])
        self.assertEqual(AvailabilityRequest.objects.count(), 0)

        self._set_fault("latency", "availability.get", fixed_ms=25)
        with patch("aintegrator_skylink.api.sleep") as mocked_sleep:
            self._get("/api/v2/availability-requests/00000000-0000-0000-0000-000000000000")
        mocked_sleep.assert_called_once_with(0.025)

        self._set_fault("timeout", "orders.get", delay_ms=1)
        with patch("aintegrator_skylink.api.sleep"):
            timeout = self._get("/api/v2/orders/C-UNKNOWN")
        self.assertEqual(timeout.status_code, 504)
        self.assertEqual(self._body(timeout)["errors"][0]["code"], "upstream_timeout")

    def test_rate_limit_and_token_expiry_injection(self) -> None:
        self._set_fault("rate_limit", "orders.get", retry_after_s=7)
        limited = self._get("/api/v2/orders/C-UNKNOWN")
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited["Retry-After"], "7")
        self._set_fault("token_expiry", "orders.get")
        expired = self._get("/api/v2/orders/C-UNKNOWN")
        self.assertEqual(expired.status_code, 401)
        self.assertEqual(self._body(expired)["errors"][0]["code"], "token_expired")
