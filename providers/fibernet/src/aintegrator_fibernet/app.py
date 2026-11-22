"""FastAPI application for simulated provider A (Fibernet).

Run it with ``uvicorn aintegrator_fibernet.app:app --port 8001``. The API uses
OAuth2 client credentials, integer-cent prices and RFC 7807 problem responses.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import hashlib
from collections.abc import AsyncIterator
from typing import Annotated, Any

from aintegrator_provider_common import (
    FaultDecision,
    FaultKind,
    FaultProfile,
    error_status,
    latency_seconds,
    retry_after_seconds,
    should_hang,
)
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.datastructures import FormData

from ._version import __version__
from .catalog import InvalidCursorError, address_key, build_offers, decode_cursor, encode_cursor
from .control import advance_orders_now, clear_all, set_fault_profile, snapshot
from .models import (
    CANCELLABLE_STATUSES,
    CoverageResponse,
    OrderCreateRequest,
    OrderStatus,
    iso_utc,
    utc_now,
)
from .state import TOKEN_TTL_S, IdempotencyRecord
from .state import state as provider_state

__all__ = ["TIMEOUT_HANG_SECONDS", "app"]

TIMEOUT_HANG_SECONDS = 30.0
"""How long a TIMEOUT fault hangs; tests may patch this module constant."""

PARTIAL_OFFER_FIELDS = (
    "valid_until",
    "plan_description",
    "install_fee_eur_cents",
    "lead_time_days",
)
PARTIAL_ORDER_FIELDS = ("id", "plan_id", "customer")


class InvalidTokenError(Exception):
    """Raised when a bearer token is missing, malformed or expired."""


class RateLimitedError(Exception):
    """Raised when the local token bucket has no token left."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"rate limit exceeded; retry after {retry_after}s")
        self.retry_after = retry_after


def problem_response(
    status_code: int,
    title: str,
    detail: str,
    *,
    slug: str = "error",
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Build an RFC 7807 ``application/problem+json`` response."""
    return JSONResponse(
        status_code=status_code,
        content={
            "type": f"https://errors.aintegrator.local/fibernet/{slug}",
            "title": title,
            "status": status_code,
            "detail": detail,
        },
        media_type="application/problem+json",
        headers=headers,
    )


def _order_not_found(order_id: str) -> JSONResponse:
    """Build the common 404 problem for an unknown order identifier."""
    return problem_response(
        404,
        "Order not found",
        f"No Fibernet order named {order_id!r}.",
        slug="not-found",
    )


def _client_key(request: Request) -> str:
    """Identify the caller for token-bucket accounting."""
    return request.client.host if request.client else "anonymous"


def _enforce_rate_limit(request: Request) -> None:
    """Raise :class:`RateLimitedError` when the caller has exhausted its bucket."""
    retry_after = provider_state.rate_limit(_client_key(request))
    if retry_after is not None:
        raise RateLimitedError(retry_after)


def _basic_credentials(request: Request) -> tuple[str, str] | None:
    """Decode HTTP Basic client credentials, if present."""
    scheme, _, encoded = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "basic":
        return None
    try:
        decoded = base64.b64decode(encoded.strip(), validate=True).decode()
    except (binascii.Error, UnicodeDecodeError):
        return "", ""
    client_id, _, secret = decoded.partition(":")
    return client_id, secret


def _token_ttl(decision: FaultDecision | None) -> float:
    """Return the configured token lifetime, honouring token_expiry faults."""
    if decision is None or decision.kind is not FaultKind.TOKEN_EXPIRY:
        return TOKEN_TTL_S
    try:
        ttl_s = float(decision.params.get("ttl_s", TOKEN_TTL_S))
    except (TypeError, ValueError):
        return TOKEN_TTL_S
    return max(0.001, ttl_s)


async def apply_fault(decision: FaultDecision | None) -> Response | None:
    """Apply an injected transport fault, returning a ready-made response if needed.

    Latency and timeout faults mutate no state and return ``None`` after waiting;
    the caller must then continue with the normal request handling. TOKEN_EXPIRY
    and webhook faults are handled elsewhere and are ignored here.
    """
    if decision is None:
        return None
    if decision.kind is FaultKind.LATENCY:
        await asyncio.sleep(latency_seconds(decision))
        return None
    if should_hang(decision):
        await asyncio.sleep(TIMEOUT_HANG_SECONDS)
        return None
    if decision.kind is FaultKind.HTTP_ERROR:
        return problem_response(
            error_status(decision, default=502),
            "Injected upstream error",
            "Fibernet fault injection forced this request to fail.",
            slug="injected-http-error",
        )
    if decision.kind is FaultKind.MALFORMED_RESPONSE:
        return Response(content='{"offer": ', status_code=200, media_type="application/json")
    if decision.kind is FaultKind.RATE_LIMIT:
        retry_after = retry_after_seconds(decision, default=1)
        return problem_response(
            429,
            "Too Many Requests",
            "Fibernet fault injection forced this request to be rate limited.",
            slug="injected-rate-limit",
            headers={"Retry-After": str(retry_after)},
        )
    return None


async def require_bearer(request: Request) -> str:
    """Authenticate a ``Bearer`` token on every ``/v1`` endpoint."""
    _enforce_rate_limit(request)
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not provider_state.verify_token(token.strip()):
        raise InvalidTokenError()
    return token


BearerToken = Annotated[str, Depends(require_bearer)]
Limit = Annotated[int, Query(ge=1, le=50)]


async def _advance_loop() -> None:
    """Advance all orders periodically until the application shuts down."""
    while True:
        await asyncio.sleep(provider_state.settings.advance_after_s)
        provider_state.advance_orders()


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Start and cleanly stop the background order-advance task."""
    provider_state.advance_task = asyncio.create_task(_advance_loop())
    try:
        yield
    finally:
        task = provider_state.advance_task
        provider_state.advance_task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


app = FastAPI(title="Fibernet (simulated provider A)", version=__version__, lifespan=lifespan)


@app.exception_handler(InvalidTokenError)
async def _invalid_token_handler(_request: Request, _exc: InvalidTokenError) -> JSONResponse:
    return JSONResponse(status_code=401, content={"error": "invalid_token"})


@app.exception_handler(RateLimitedError)
async def _rate_limited_handler(_request: Request, exc: RateLimitedError) -> JSONResponse:
    return problem_response(
        429,
        "Too Many Requests",
        "The Fibernet token bucket is empty.",
        slug="rate-limit",
        headers={"Retry-After": str(exc.retry_after)},
    )


@app.exception_handler(RequestValidationError)
async def _validation_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    return problem_response(422, "Validation Error", str(exc.errors()), slug="validation-error")


@app.exception_handler(InvalidCursorError)
async def _cursor_handler(_request: Request, exc: InvalidCursorError) -> JSONResponse:
    return problem_response(400, "Invalid cursor", str(exc), slug="invalid-cursor")


@app.exception_handler(HTTPException)
async def _http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
    slug = "not-found" if exc.status_code == 404 else "http-error"
    return problem_response(exc.status_code, str(exc.detail), str(exc.detail), slug=slug)


@app.post("/oauth/token")
async def oauth_token(request: Request) -> Response:
    """Issue an OAuth2 access token using client credentials.

    Credentials may be sent as form fields or HTTP Basic; when omitted entirely,
    the configured default client is used.
    """
    _enforce_rate_limit(request)
    form: FormData = await request.form()
    grant_type = str(form.get("grant_type") or "client_credentials")
    if grant_type != "client_credentials":
        return problem_response(
            400,
            "Unsupported grant type",
            f"Unsupported grant_type: {grant_type}",
            slug="grant",
        )

    basic = _basic_credentials(request)
    if basic is not None:
        client_id, client_secret = basic
    else:
        client_id = str(form.get("client_id") or "")
        client_secret = str(form.get("client_secret") or "")
    if not client_id and not client_secret:
        client_id = provider_state.settings.client_id
        client_secret = provider_state.settings.client_secret
    settings = provider_state.settings
    if client_id != settings.client_id or client_secret != settings.client_secret:
        return problem_response(
            401,
            "Invalid client",
            "Unknown client credentials.",
            slug="invalid-client",
        )

    decision = provider_state.injector.decide("token")
    if decision is None or decision.kind is not FaultKind.TOKEN_EXPIRY:
        fault_response = await apply_fault(decision)
        if fault_response is not None:
            return fault_response

    token, expires_in = provider_state.issue_token(_token_ttl(decision))
    body: dict[str, Any] = {"access_token": token, "token_type": "bearer", "expires_in": expires_in}
    if decision is not None and decision.kind is FaultKind.PARTIAL_RESPONSE:
        body.pop("expires_in")
    return JSONResponse(body)


@app.get("/v1/coverage", response_model=CoverageResponse)
async def coverage(
    postal_code: str,
    street: str,
    number: str,
    _token: BearerToken,
    cursor: str | None = None,
    limit: Limit = 10,
) -> Response:
    """Return a deterministic, cursor-paginated list of fictional offers."""
    decision = provider_state.injector.decide("coverage")
    fault_response = await apply_fault(decision)
    if fault_response is not None:
        return fault_response

    key = address_key(postal_code, street, number)
    try:
        offset = decode_cursor(cursor, key) if cursor else 0
    except InvalidCursorError as exc:
        return problem_response(400, "Invalid cursor", str(exc), slug="invalid-cursor")

    offers = build_offers(provider_state.settings.seed, postal_code, street, number)
    page = offers[offset : offset + limit]
    next_cursor = encode_cursor(key, offset + limit) if offset + limit < len(offers) else None
    body = CoverageResponse(offers=list(page), next_cursor=next_cursor).model_dump()
    if decision is not None and decision.kind is FaultKind.PARTIAL_RESPONSE:
        for offer in body["offers"]:
            for field in PARTIAL_OFFER_FIELDS:
                offer.pop(field, None)
    return JSONResponse(body)


@app.post("/v1/orders", status_code=201)
async def create_order(
    request: Request,
    payload: OrderCreateRequest,
    _token: BearerToken,
) -> Response:
    """Create an order, replaying previous responses for a repeated key+body."""
    idempotency_key = request.headers.get("idempotency-key")
    if not idempotency_key:
        return problem_response(
            422,
            "Missing Idempotency-Key",
            "POST /v1/orders requires a non-empty Idempotency-Key header.",
            slug="missing-idempotency-key",
        )
    body_hash = hashlib.sha256(await request.body()).hexdigest()

    decision = provider_state.injector.decide("orders.create")
    fault_response = await apply_fault(decision)
    if fault_response is not None:
        return fault_response

    async with provider_state.lock:
        existing = provider_state.idempotency.get(idempotency_key)
        if existing is not None:
            if existing.body_hash != body_hash:
                return problem_response(
                    422,
                    "Idempotency key conflict",
                    "This Idempotency-Key was already used with a different request body.",
                    slug="idempotency-conflict",
                )
            return JSONResponse(
                existing.body,
                status_code=existing.status_code,
                headers={"Idempotent-Replay": "true"},
            )

        order = provider_state.create_order(
            request=payload,
            idempotency_key=idempotency_key,
            body_hash=body_hash,
        )
        body = order.model_dump()
        provider_state.idempotency[idempotency_key] = IdempotencyRecord(
            body_hash=body_hash,
            status_code=201,
            body=body,
        )
    return JSONResponse(body, status_code=201)


@app.get("/v1/orders/{order_id}")
async def get_order(order_id: str, _token: BearerToken) -> Response:
    """Return one order by its provider identifier."""
    decision = provider_state.injector.decide("orders.get")
    fault_response = await apply_fault(decision)
    if fault_response is not None:
        return fault_response

    order = provider_state.orders.get(order_id)
    if order is None:
        return _order_not_found(order_id)
    body = order.model_dump()
    if decision is not None and decision.kind is FaultKind.PARTIAL_RESPONSE:
        for field in PARTIAL_ORDER_FIELDS:
            body.pop(field, None)
    return JSONResponse(body)


@app.post("/v1/orders/{order_id}/cancel")
async def cancel_order(order_id: str, _token: BearerToken) -> Response:
    """Cancel an order that has not yet entered provisioning."""
    decision = provider_state.injector.decide("orders.cancel")
    fault_response = await apply_fault(decision)
    if fault_response is not None:
        return fault_response

    async with provider_state.lock:
        order = provider_state.orders.get(order_id)
        if order is None:
            return _order_not_found(order_id)
        if order.status not in CANCELLABLE_STATUSES:
            return problem_response(
                409,
                "Order cannot be cancelled",
                f"Order {order_id} is {order.status.value} and can no longer be cancelled.",
                slug="cancel-conflict",
            )
        order.status = OrderStatus.CANCELLED
        order.updated_at = iso_utc(utc_now())
        body = order.model_dump()
    return JSONResponse(body)


@app.post("/_control/faults")
async def control_faults(profile: FaultProfile) -> dict[str, Any]:
    """Install a fault profile (no authentication; local test surface only)."""
    return set_fault_profile(provider_state, profile)


@app.post("/_control/reset")
async def control_reset() -> dict[str, Any]:
    """Reset all state and faults (no authentication; local test surface only)."""
    return clear_all(provider_state)


@app.post("/_control/advance")
async def control_advance() -> dict[str, int]:
    """Advance all non-terminal orders immediately."""
    return advance_orders_now(provider_state)


@app.get("/_control/state")
async def control_state() -> dict[str, Any]:
    """Return counts and the active fault profile."""
    return snapshot(provider_state)
