"""FastAPI webhook receiver for skylink.

Store the raw event first with a unique (provider, event_id) constraint, then
apply it to the state machine. Duplicates are acknowledged without reapplying.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from aintegrator_integration.config import Settings, get_settings
from aintegrator_integration.factory import get_service
from aintegrator_integration.persistence import Repository, session_scope
from aintegrator_integration.service import ConnectivityService
from aintegrator_integration.webhooks.signature import InvalidSignature, verify_signature

__all__ = ["app", "create_app"]

_PROVIDER = "skylink"


def _parse_occurred_at(value: object) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(UTC)


def create_app(
    service: ConnectivityService | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    configuration = settings or get_settings()
    app = FastAPI(title="AIntegrator webhook receiver", version="0.1.0")

    @app.post("/webhooks/skylink")
    async def receive(request: Request) -> JSONResponse:
        raw = await request.body()
        try:
            verify_signature(
                raw,
                request.headers.get("X-Skylink-Signature"),
                configuration.skylink_webhook_secret,
                tolerance_s=configuration.webhook_timestamp_tolerance_s,
            )
        except InvalidSignature as exc:
            raise HTTPException(status_code=401, detail=exc.message) from exc
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON body") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Webhook body must be an object")

        event_id = str(payload.get("eventId") or payload.get("event_id") or "")
        event_type = str(payload.get("type") or "")
        if not event_id or not event_type:
            raise HTTPException(status_code=400, detail="Missing eventId or type")

        data = payload.get("data")
        if not isinstance(data, dict):
            data = payload
        reference = ""
        for key in ("orderReference", "orderId", "order_id", "clientReference", "requestId"):
            if data.get(key):
                reference = str(data[key])
                break
        occurred_at = _parse_occurred_at(payload.get("occurredAt"))
        sequence = int(payload.get("sequence") or 0)
        active_service = service or get_service()

        async with session_scope(active_service.session_maker) as session:
            stored = await Repository(session).store_webhook(
                _PROVIDER,
                event_id,
                reference or None,
                event_type,
                occurred_at,
                sequence,
                payload,
                datetime.now(UTC),
            )
        if not stored:
            return JSONResponse({"status": "duplicate", "event_id": event_id}, status_code=200)

        if event_type.startswith("order."):
            await active_service.apply_webhook_event(
                _PROVIDER, event_type, reference, occurred_at=occurred_at, raw_ref=reference or None
            )
        # Availability webhooks are consumed by the adapter's polling path.
        return JSONResponse({"status": "accepted", "event_id": event_id}, status_code=200)

    return app


app = create_app()
