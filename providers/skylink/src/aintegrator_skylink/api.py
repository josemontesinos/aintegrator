"""DRF endpoints: public provider API plus unauthenticated local control endpoints."""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta
from time import sleep
from typing import Any

from aintegrator_provider_common import FaultKind, FaultProfile
from aintegrator_provider_common.effects import (
    error_status,
    latency_seconds,
    retry_after_seconds,
    should_hang,
)
from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import HttpResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from pydantic import ValidationError as PydanticValidationError
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed, NotAuthenticated
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.views import exception_handler as drf_exception_handler

from .models import AvailabilityRequest, Order, OutboxEvent, epoch_ms
from .runtime import INBOUND_OPERATIONS, get_injector, reset_runtime
from .worker import advance_pending

_ERROR_CODES = {
    400: "validation_error",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    429: "rate_limited",
}
_MAX_INBOUND_SLEEP_S = 2.0


@dataclass(frozen=True, slots=True)
class ApiKeyPrincipal:
    """Minimal authenticated principal returned by the static API-key scheme."""

    is_authenticated: bool = True
    is_anonymous: bool = False


def envelope(
    data: object,
    *,
    meta: dict[str, object] | None = None,
    errors: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    """Wrap data in the provider's standard ``data/meta/errors`` envelope."""
    metadata: dict[str, object] = {"generatedAt": epoch_ms(timezone.now())}
    if meta:
        metadata.update(meta)
    return {"data": data, "meta": metadata, "errors": errors or []}


def ok(
    data: object,
    *,
    status: int = 200,
    meta: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> Response:
    """Return a successful envelope response."""
    return Response(envelope(data, meta=meta), status=status, headers=headers)


def fail(
    code: str,
    message: str,
    status: int = 400,
    *,
    meta: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> Response:
    """Return an error envelope response."""
    return Response(
        envelope(None, meta=meta, errors=[{"code": code, "message": message}]),
        status=status,
        headers=headers,
    )


class StaticApiKeyAuthentication(BaseAuthentication):
    """Accept exactly the key configured by ``SKYLINK_API_KEY`` in ``X-Api-Key``."""

    def authenticate(self, request: Request) -> tuple[ApiKeyPrincipal, str] | None:
        supplied = request.META.get("HTTP_X_API_KEY")
        expected = settings.SKYLINK_API_KEY
        if not supplied or not secrets.compare_digest(supplied, expected):
            raise AuthenticationFailed("Invalid or missing X-Api-Key")
        return ApiKeyPrincipal(), supplied


def api_exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    """Make every DRF error use the provider envelope (including 401 responses)."""
    if isinstance(exc, (AuthenticationFailed, NotAuthenticated)):
        detail = str(getattr(exc, "detail", "authentication required"))
        return fail(
            "unauthorized",
            detail,
            401,
            headers={"WWW-Authenticate": "X-Api-Key"},
        )
    response = drf_exception_handler(exc, context)
    if response is None:
        return None
    status = response.status_code
    return fail(
        _ERROR_CODES.get(status, "api_error"),
        str(getattr(exc, "detail", "request failed")),
        status,
    )


def _inbound_fault(operation: str) -> HttpResponse | None:
    """Apply the next deterministic fault decision for an inbound operation."""
    decision = get_injector().decide(operation)
    if decision is None:
        return None
    if decision.kind is FaultKind.LATENCY:
        sleep(latency_seconds(decision))
        return None
    if should_hang(decision):
        delay_ms = int(decision.params.get("delay_ms", 0))
        sleep(min(max(delay_ms, 0) / 1000, _MAX_INBOUND_SLEEP_S))
        return fail("upstream_timeout", f"Injected timeout on {operation}", 504)
    if decision.kind is FaultKind.HTTP_ERROR:
        status = error_status(decision, 500)
        return fail("injected_http_error", f"Injected HTTP {status} on {operation}", status)
    if decision.kind is FaultKind.MALFORMED_RESPONSE:
        return HttpResponse("{ this is not valid JSON", status=200, content_type="application/json")
    if decision.kind is FaultKind.PARTIAL_RESPONSE:
        return ok(
            {"status": "QUEUED", "partial": True},
            meta={"partial": True, "operation": operation},
        )
    if decision.kind is FaultKind.RATE_LIMIT:
        return fail(
            "rate_limited",
            f"Injected rate limit on {operation}",
            429,
            headers={"Retry-After": str(retry_after_seconds(decision))},
        )
    if decision.kind is FaultKind.TOKEN_EXPIRY:
        return fail(
            "token_expired",
            f"Injected token expiry on {operation}",
            401,
            headers={"WWW-Authenticate": "X-Api-Key"},
        )
    return None


def _body(request: Request) -> dict[str, Any]:
    """Return the request body as a mapping, tolerating malformed client payloads."""
    return request.data if isinstance(request.data, dict) else {}


class ProviderApiView(APIView):
    """Base class for authenticated, enveloped provider endpoints."""

    authentication_classes = (StaticApiKeyAuthentication,)
    permission_classes = (IsAuthenticated,)


class AvailabilityRequestCreateView(ProviderApiView):
    """Queue a fictional availability lookup; the result arrives by webhook."""

    def post(self, request: Request) -> Response | HttpResponse:
        fault = _inbound_fault("availability.create")
        if fault is not None:
            return fault
        data = _body(request)
        if "address" not in data or data["address"] in (None, ""):
            return fail("validation_error", "address is required", 400)
        notes = str(data.get("notes", "Synthetic availability lookup; no real network queried."))
        availability = AvailabilityRequest.objects.create(
            address=data["address"],
            due_at=timezone.now() + timedelta(seconds=settings.SKYLINK_AVAILABILITY_DELAY_S),
            notes=notes,
        )
        return ok(
            {
                "requestId": str(availability.request_id),
                "status": availability.status,
                "notes": notes,
            },
            status=202,
            meta={
                "eventType": "availability.completed",
                "expectedDelayS": settings.SKYLINK_AVAILABILITY_DELAY_S,
            },
        )


class AvailabilityRequestDetailView(ProviderApiView):
    """Poll an availability request; ``offers`` appears once its status is DONE."""

    def get(self, request: Request, request_id: str) -> Response | HttpResponse:
        fault = _inbound_fault("availability.get")
        if fault is not None:
            return fault
        try:
            availability = AvailabilityRequest.objects.get(pk=request_id)
        except (AvailabilityRequest.DoesNotExist, DjangoValidationError, ValueError):
            return fail("not_found", f"Unknown availability request {request_id}", 404)
        return ok(availability.to_data(), meta={"requestId": str(availability.request_id)})


def _new_order_id() -> str:
    """Generate a collision-free, fictional order identifier such as ``C-A1B2...``."""
    for _ in range(10):
        candidate = f"C-{uuid.uuid4().hex[:10].upper()}"
        if not Order.objects.filter(pk=candidate).exists():
            return candidate
    raise RuntimeError("unable to allocate an order id")


class OrderListCreateView(ProviderApiView):
    """Create orders (202, no idempotency) or list them by ``clientReference``."""

    def post(self, request: Request) -> Response | HttpResponse:
        fault = _inbound_fault("orders.create")
        if fault is not None:
            return fault
        data = _body(request)
        required = ("clientReference", "offerId", "address", "customer")
        missing = [field for field in required if field not in data or data[field] in (None, "")]
        if missing:
            return fail("validation_error", f"Missing required fields: {', '.join(missing)}", 400)
        notes = str(data.get("notes", "Synthetic order; no real service is provisioned."))
        order = Order.objects.create(
            order_id=_new_order_id(),
            client_reference=str(data["clientReference"]),
            offer_id=str(data["offerId"]),
            address=data["address"],
            customer=data["customer"],
            notes=notes,
            next_step_at=timezone.now() + timedelta(seconds=settings.SKYLINK_ORDER_STEP_DELAY_S),
        )
        return ok(
            {
                "orderId": order.order_id,
                "clientReference": order.client_reference,
                "status": order.status,
                "notes": notes,
            },
            status=202,
            meta={
                "eventTypes": ["order.confirmed", "order.working", "order.done", "order.declined"],
                "idempotency": "not supported",
            },
        )

    def get(self, request: Request) -> Response | HttpResponse:
        fault = _inbound_fault("orders.get")
        if fault is not None:
            return fault
        reference = request.query_params.get("clientReference")
        if not reference:
            return fail("validation_error", "clientReference query parameter is required", 400)
        orders = Order.objects.filter(client_reference=reference).order_by("-created_at")
        items = [order.to_data() for order in orders]
        return ok(items, meta={"clientReference": reference, "count": len(items)})


class OrderDetailView(ProviderApiView):
    """Poll one order and the webhook events emitted for it."""

    def get(self, request: Request, order_id: str) -> Response | HttpResponse:
        fault = _inbound_fault("orders.get")
        if fault is not None:
            return fault
        try:
            order = Order.objects.get(pk=order_id)
        except Order.DoesNotExist:
            return fail("not_found", f"Unknown order {order_id}", 404)
        data = order.to_data()
        data["webhookEvents"] = order.event_summary()
        return ok(data, meta={"orderId": order.order_id})


@method_decorator(csrf_exempt, name="dispatch")
class ControlApiView(APIView):
    """Base for local-only control endpoints (intentionally unauthenticated)."""

    authentication_classes = ()
    permission_classes = ()


class FaultsControlView(ControlApiView):
    """Replace the active :class:`FaultProfile`."""

    def post(self, request: Request) -> Response:
        data = _body(request)
        try:
            profile = FaultProfile.model_validate(data)
        except PydanticValidationError as exc:
            return fail("validation_error", str(exc), 400)
        get_injector().set_profile(profile)
        return ok({"faults": profile.model_dump(mode="json")})


class ResetControlView(ControlApiView):
    """Delete all provider state and reset fault counters."""

    def post(self, request: Request) -> Response:
        counts = {
            "availabilityRequests": AvailabilityRequest.objects.count(),
            "orders": Order.objects.count(),
            "outboxEvents": OutboxEvent.objects.count(),
        }
        OutboxEvent.objects.all().delete()
        Order.objects.all().delete()
        AvailabilityRequest.objects.all().delete()
        reset_runtime()
        return ok({"deleted": counts, "reset": True})


class AdvanceControlView(ControlApiView):
    """Advance every pending state now, ignoring scheduled delays."""

    def post(self, request: Request) -> Response:
        result = advance_pending(force=True)
        return ok(
            {
                "availabilityCompleted": result.availability_completed,
                "ordersAdvanced": result.orders_advanced,
                "eventsCreated": result.events_created,
            }
        )


class StateControlView(ControlApiView):
    """Expose provider state, the active fault profile and call counters."""

    def get(self, request: Request) -> Response:
        injector = get_injector()
        data: dict[str, object] = {
            "availabilityRequests": [
                req.to_data() for req in AvailabilityRequest.objects.order_by("created_at")
            ],
            "orders": [order.to_data() for order in Order.objects.order_by("created_at")],
            "outboxEvents": [
                {
                    "eventId": str(event.event_id),
                    "type": event.type,
                    "sequence": event.sequence,
                    "orderReference": event.order_reference,
                    "status": event.status,
                    "attempts": event.attempts,
                    "occurredAt": epoch_ms(event.occurred_at),
                }
                for event in OutboxEvent.objects.order_by("created_at", "sequence")
            ],
            "faults": injector.profile.model_dump(mode="json"),
            "callCounts": {
                operation: injector.call_index(operation) for operation in INBOUND_OPERATIONS
            },
            "workerEnabled": settings.SKYLINK_WORKER_ENABLED,
        }
        return ok(data)
