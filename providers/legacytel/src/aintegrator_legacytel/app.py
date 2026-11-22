"""Flask application exposing the simulated legacytel SOAP 1.1 service.

Run with::

    python -m flask --app aintegrator_legacytel.app:app run --host 0.0.0.0 --port 8002

The ``/_control/*`` endpoints exist for local test harnesses and need no auth.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from aintegrator_provider_common import (
    FaultKind,
    FaultProfile,
    error_status,
    latency_seconds,
    should_hang,
)
from flask import Flask, Response, jsonify, request
from pydantic import ValidationError

from . import soap, state

__all__ = ["app", "service"]

app = Flask(__name__)
service = state.ProviderState()

_CONTENT_TYPE = "text/xml; charset=utf-8"


@app.get("/soap")
def wsdl() -> Response:
    """Serve the static WSDL when ``?wsdl`` is present."""
    if "wsdl" not in request.args:
        return Response(
            "Use ?wsdl to retrieve the WSDL, or POST a SOAP request.", content_type="text/plain"
        )
    return Response(soap.load_wsdl(), content_type=_CONTENT_TYPE)


@app.post("/soap")
def soap_endpoint() -> Response:
    """Parse, authorise and dispatch one SOAP request."""
    try:
        operation, fields, header = soap.parse_envelope(request.get_data())
        soap.validate_action(request.headers.get("SOAPAction"), operation)
        soap.validate_credentials(
            request.headers.get("Authorization"),
            header,
            os.getenv("LEGACYTEL_USERNAME", "legacytel"),
            os.getenv("LEGACYTEL_PASSWORD", "legacytel-secret"),
        )
    except soap.SoapProtocolError as exc:
        return _fault_response(exc.code, str(exc))

    decision = service.injector.decide(state.FAULT_OPERATION_BY_SOAP_NAME[operation])
    if decision is not None and decision.kind is FaultKind.HTTP_ERROR:
        return _fault_response(
            "soapenv:Server",
            "Injected legacy backend failure.",
            status=error_status(decision, default=500),
        )
    if decision is not None and should_hang(decision):
        state.sleep(float(decision.params.get("seconds", state.DEFAULT_TIMEOUT_SECONDS)))
    elif decision is not None and decision.kind is FaultKind.LATENCY:
        state.sleep(latency_seconds(decision))
    if decision is not None and decision.kind is FaultKind.MALFORMED_RESPONSE:
        return Response(soap.MALFORMED_RESPONSE_XML, status=200, content_type=_CONTENT_TYPE)

    payload = _dispatch(operation, fields)
    partial = decision is not None and decision.kind is FaultKind.PARTIAL_RESPONSE
    return Response(
        soap.build_response(operation, payload, partial=partial), content_type=_CONTENT_TYPE
    )


@app.post("/_control/faults")
def control_faults() -> tuple[Response, int] | Response:
    """Install a :class:`FaultProfile` from a JSON body."""
    payload = request.get_json(silent=True) or {}
    try:
        profile = FaultProfile.model_validate(payload)
    except ValidationError as exc:
        return jsonify({"error": "Invalid fault profile", "details": exc.errors()}), 400
    service.set_fault_profile(profile)
    return jsonify({"ok": True, "profile": profile.model_dump(mode="json")})


@app.post("/_control/reset")
def control_reset() -> Response:
    """Clear orders, counters and fault configuration."""
    service.reset()
    return jsonify({"ok": True})


@app.post("/_control/advance")
def control_advance() -> tuple[Response, int] | Response:
    """Advance active orders toward completion."""
    payload = request.get_json(silent=True) or {}
    steps = payload.get("steps", 1) if isinstance(payload, Mapping) else None
    if not isinstance(steps, int) or isinstance(steps, bool) or not 1 <= steps <= 1000:
        return jsonify({"error": "steps must be an integer between 1 and 1000"}), 400
    return jsonify({"ok": True, "counts": service.advance(steps)})


@app.get("/_control/state")
def control_state() -> Response:
    """Return orders and fault counters for diagnostics."""
    return jsonify(service.snapshot())


def _fault_response(code: str, message: str, *, status: int = 500) -> Response:
    return Response(soap.build_fault(code, message), status=status, content_type=_CONTENT_TYPE)


def _dispatch(operation: str, fields: Mapping[str, str]) -> dict[str, object]:
    """Run the business logic for one parsed operation."""
    if operation in ("CheckAvailability", "PlaceOrder"):
        state.sleep(
            state.baseline_latency(fields["PostalCode"], fields["Street"], fields["Number"])
        )
    if operation == "CheckAvailability":
        ok, offers, error_code, error_text = service.check_availability(
            fields["PostalCode"], fields["Street"], fields["Number"]
        )
        if not ok:
            return _business_error(error_code, error_text)
        return _result(True) | {"Offers": [offer.as_dict() for offer in offers]}

    if operation == "PlaceOrder":
        record, error_code, error_text = service.place_order(
            fields["ExternalRef"],
            fields["OfferId"],
            fields["CustomerName"],
            fields["CustomerEmail"],
            fields["PostalCode"],
            fields["Street"],
            fields["Number"],
        )
        if record is None:
            return _business_error(error_code, error_text)
        return _result(True) | record.as_response()

    if operation == "GetOrderStatus":
        record = service.get_order(fields["OrderId"])
        if record is None:
            return _business_error(
                state.ERR_ORDER_NOT_FOUND, "The fictional order id does not exist."
            )
        return _result(True) | record.as_response() | {"LastUpdated": record.created_at}

    orders = service.find_by_reference(fields["ExternalRef"])
    return _result(True) | {
        "Count": len(orders),
        "Orders": [record.as_response() for record in orders],
    }


def _result(ok: bool) -> dict[str, object]:
    return {
        "ResultCode": "OK" if ok else "NOK",
        "ErrorCode": state.ERR_OK if ok else state.ERR_INVALID_REQUEST,
        "ErrorText": "",
    }


def _business_error(error_code: str, error_text: str) -> dict[str, object]:
    return {"ResultCode": "NOK", "ErrorCode": error_code, "ErrorText": error_text}
