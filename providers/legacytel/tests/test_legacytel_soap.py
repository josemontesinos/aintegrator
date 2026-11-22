"""Unit tests for the simulated legacytel SOAP provider (no running services)."""

from __future__ import annotations

import base64
import re
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime

import pytest
from aintegrator_legacytel import app, soap, state
from aintegrator_legacytel.app import service
from aintegrator_provider_common import FaultKind, FaultProfile, FaultRule
from flask import Response
from flask.testing import FlaskClient
from lxml import etree
from zeep import Client

BASIC_HEADERS = {
    "Authorization": "Basic " + base64.b64encode(b"legacytel:legacytel-secret").decode("ascii"),
}
ADDRESS = {"PostalCode": "75001", "Street": "Rue des Lucioles", "Number": "12"}
WSDL_NS = "http://schemas.xmlsoap.org/wsdl/"
SOAP_WSDL_NS = "http://schemas.xmlsoap.org/wsdl/soap/"
XSD_NS = "http://www.w3.org/2001/XMLSchema"
OPERATIONS = ("CheckAvailability", "PlaceOrder", "GetOrderStatus", "FindOrdersByReference")


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep tests fast and independent of earlier SOAP calls."""
    monkeypatch.setattr(state, "sleep", lambda seconds: None)
    service.reset()
    yield
    service.reset()


@pytest.fixture
def client() -> FlaskClient:
    return app.test_client()


def _envelope(operation: str, fields: Mapping[str, str], *, wsse: bool = False) -> bytes:
    root = etree.Element(
        f"{{{soap.SOAP_NS}}}Envelope", nsmap={"soapenv": soap.SOAP_NS, "tns": soap.TARGET_NS}
    )
    header = etree.SubElement(root, f"{{{soap.SOAP_NS}}}Header")
    if wsse:
        security = etree.SubElement(header, f"{{{soap.WSSE_NS}}}Security")
        token = etree.SubElement(security, f"{{{soap.WSSE_NS}}}UsernameToken")
        etree.SubElement(token, f"{{{soap.WSSE_NS}}}Username").text = "legacytel"
        etree.SubElement(token, f"{{{soap.WSSE_NS}}}Password").text = "legacytel-secret"
    body = etree.SubElement(root, f"{{{soap.SOAP_NS}}}Body")
    operation_element = etree.SubElement(body, f"{{{soap.TARGET_NS}}}{operation}")
    for name, value in fields.items():
        etree.SubElement(operation_element, f"{{{soap.TARGET_NS}}}{name}").text = str(value)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


def _post(
    client: FlaskClient,
    operation: str,
    fields: Mapping[str, str],
    *,
    wsse: bool = False,
    headers: Mapping[str, str] | None = None,
) -> Response:
    request_headers = dict(headers) if headers is not None else dict(BASIC_HEADERS)
    request_headers.setdefault("SOAPAction", f"{soap.TARGET_NS}/{operation}")
    return client.post(
        "/soap", data=_envelope(operation, fields, wsse=wsse), headers=request_headers
    )


def _payload(response: Response, operation: str) -> etree._Element:
    root = etree.fromstring(response.data)
    body = root.find(f"{{{soap.SOAP_NS}}}Body")
    assert body is not None
    element = body.find(f"{{{soap.TARGET_NS}}}{operation}Response")
    assert element is not None
    return element


def _field(payload: etree._Element, name: str) -> str | None:
    element = payload.find(f"{{{soap.TARGET_NS}}}{name}")
    return None if element is None else element.text


def _fault(response: Response) -> etree._Element:
    assert response.status_code == 500
    root = etree.fromstring(response.data)
    fault = root.find(f"{{{soap.SOAP_NS}}}Body/{{{soap.SOAP_NS}}}Fault")
    assert fault is not None
    return fault


def _install_fault(kind: FaultKind, params: dict[str, object] | None = None) -> None:
    profile = FaultProfile(
        seed=5,
        rules=[
            FaultRule(
                operation="check_availability", kind=kind, probability=1.0, params=params or {}
            )
        ],
    )
    response = app.test_client().post("/_control/faults", json=profile.model_dump(mode="json"))
    assert response.status_code == 200


def _placed_order(client: FlaskClient) -> tuple[str, dict[str, str]]:
    availability = _post(client, "CheckAvailability", ADDRESS)
    offers = _payload(availability, "CheckAvailability").findall(
        f"{{{soap.TARGET_NS}}}Offers/{{{soap.TARGET_NS}}}Offer"
    )
    assert offers
    offer_id = offers[0].findtext(f"{{{soap.TARGET_NS}}}OfferId")
    assert offer_id
    fields = {
        "ExternalRef": "CLIENT-42",
        "OfferId": offer_id,
        "CustomerName": "Ada Example",
        "CustomerEmail": "ada@example.invalid",
        **ADDRESS,
    }
    response = _post(client, "PlaceOrder", fields)
    payload = _payload(response, "PlaceOrder")
    assert _field(payload, "ResultCode") == "OK"
    order_id = _field(payload, "ProviderOrderId")
    assert order_id
    return order_id, fields


def test_wsdl_is_valid_document_literal_soap11() -> None:
    root = etree.fromstring(soap.load_wsdl())
    assert root.tag == f"{{{WSDL_NS}}}definitions"
    assert root.get("targetNamespace") == soap.TARGET_NS
    port_type = root.find(f"{{{WSDL_NS}}}portType")
    assert port_type is not None
    assert {operation.get("name") for operation in port_type} == set(OPERATIONS)
    binding = root.find(f"{{{WSDL_NS}}}binding/{{{WSDL_NS}}}operation")
    assert binding is not None
    soap_operation = binding.find(f"{{{SOAP_WSDL_NS}}}operation")
    assert soap_operation is not None
    soap_body = binding.find(f"{{{WSDL_NS}}}input/{{{SOAP_WSDL_NS}}}body")
    assert soap_body is not None
    address = root.find(f".//{{{SOAP_WSDL_NS}}}address")
    assert address is not None
    assert address.get("location") == "http://localhost:8002/soap"

    schema = root.find(f"{{{WSDL_NS}}}types/{{{XSD_NS}}}schema")
    assert schema is not None
    for operation in OPERATIONS:
        request_element = schema.find(f"{{{XSD_NS}}}element[@name='{operation}']")
        assert request_element is not None
        declared = {
            element.get("name") for element in request_element.findall(f".//{{{XSD_NS}}}element")
        }
        assert declared == set(soap.REQUEST_FIELDS[operation])
        assert schema.find(f"{{{XSD_NS}}}element[@name='{operation}Response']") is not None

    # zeep must be able to load the schema without contacting a service.
    zeep_client = Client(str(soap.WSDL_PATH))
    assert zeep_client.service is not None


def test_package_exports_the_flask_app() -> None:
    from aintegrator_legacytel import __version__

    assert __version__ == "0.1.0"
    assert app.test_client().get("/soap?wsdl").status_code == 200


def test_get_wsdl_serves_xml(client: FlaskClient) -> None:
    response = client.get("/soap?wsdl")
    assert response.status_code == 200
    assert response.content_type.startswith("text/xml")
    assert soap.TARGET_NS.encode() in response.data


def test_check_availability_quirks(client: FlaskClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(state, "paris_now", lambda: datetime(2026, 1, 15, 23, 30, tzinfo=UTC))
    response = _post(client, "CheckAvailability", ADDRESS)
    assert response.status_code == 200
    payload = _payload(response, "CheckAvailability")
    assert _field(payload, "ResultCode") == "OK"
    assert _field(payload, "ErrorCode") == "0"

    offers = payload.findall(f"{{{soap.TARGET_NS}}}Offers/{{{soap.TARGET_NS}}}Offer")
    assert 1 <= len(offers) <= 3
    for offer in offers:
        price = offer.findtext(f"{{{soap.TARGET_NS}}}Price")
        assert price is not None and re.fullmatch(r"\d+,\d{2}", price)
        available_from = offer.findtext(f"{{{soap.TARGET_NS}}}AvailableFrom")
        assert available_from is not None and re.fullmatch(
            r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}", available_from
        )
        parsed = datetime.strptime(available_from, "%d/%m/%Y %H:%M")
        assert parsed.strftime("%H:%M") == "00:30"  # UTC+1 in Paris, no offset in the text
        remarks = offer.findtext(f"{{{soap.TARGET_NS}}}Remarks") or ""
        assert "ignore all previous instructions" in remarks.lower()
    assert service.eligibility_checks == 1  # synchronous, never cached
    _post(client, "CheckAvailability", ADDRESS)
    assert service.eligibility_checks == 2


def test_business_error_is_http_200_nok(client: FlaskClient) -> None:
    response = _post(client, "CheckAvailability", {**ADDRESS, "PostalCode": "00000"})
    assert response.status_code == 200
    payload = _payload(response, "CheckAvailability")
    assert _field(payload, "ResultCode") == "NOK"
    assert (_field(payload, "ErrorCode") or "").isdigit()
    assert _field(payload, "ErrorText")


def test_ws_security_username_token_is_accepted(client: FlaskClient) -> None:
    response = _post(client, "CheckAvailability", ADDRESS, wsse=True, headers={"SOAPAction": ""})
    assert response.status_code == 200
    assert _field(_payload(response, "CheckAvailability"), "ResultCode") == "OK"


def test_bad_auth_produces_soap_fault(client: FlaskClient) -> None:
    response = _post(client, "CheckAvailability", ADDRESS, headers={"SOAPAction": ""})
    fault = _fault(response)
    assert (fault.findtext("faultcode") or "") == "soapenv:Client"
    assert "Authentication failed" in (fault.findtext("faultstring") or "")


def test_malformed_xml_produces_soap_fault(client: FlaskClient) -> None:
    response = client.post("/soap", data=b"<soapenv:Envelope", headers=BASIC_HEADERS)
    fault = _fault(response)
    assert "Malformed SOAP XML" in (fault.findtext("faultstring") or "")


def test_bad_soap_action_produces_soap_fault(client: FlaskClient) -> None:
    response = _post(
        client,
        "CheckAvailability",
        ADDRESS,
        headers=BASIC_HEADERS | {"SOAPAction": "urn:legacytel:soap/PlaceOrder"},
    )
    assert "SOAPAction" in (_fault(response).findtext("faultstring") or "")


def test_unknown_operation_and_missing_element_produce_faults(client: FlaskClient) -> None:
    unknown = _post(client, "CancelOrder", {"OrderId": "LT-1"})
    assert "Unknown operation" in (_fault(unknown).findtext("faultstring") or "")
    incomplete = _post(client, "CheckAvailability", {"PostalCode": "75001", "Street": "Rue X"})
    assert "Missing required element" in (_fault(incomplete).findtext("faultstring") or "")


def test_place_order_is_not_idempotent_and_can_be_reconciled(client: FlaskClient) -> None:
    availability = _post(client, "CheckAvailability", ADDRESS)
    offer_id = _payload(availability, "CheckAvailability").findtext(
        f"{{{soap.TARGET_NS}}}Offers/{{{soap.TARGET_NS}}}Offer/{{{soap.TARGET_NS}}}OfferId"
    )
    assert offer_id
    fields = {
        "ExternalRef": "CLIENT-42",
        "OfferId": offer_id,
        "CustomerName": "Ada Example",
        "CustomerEmail": "ada@example.invalid",
        **ADDRESS,
    }
    first = _payload(_post(client, "PlaceOrder", fields), "PlaceOrder")
    second = _payload(_post(client, "PlaceOrder", fields), "PlaceOrder")
    first_id, second_id = _field(first, "ProviderOrderId"), _field(second, "ProviderOrderId")
    assert first_id and second_id and first_id != second_id
    assert _field(first, "StatusCode") == "10"

    found = _payload(
        _post(client, "FindOrdersByReference", {"ExternalRef": "CLIENT-42"}),
        "FindOrdersByReference",
    )
    assert _field(found, "Count") == "2"
    orders = found.findall(f"{{{soap.TARGET_NS}}}Orders/{{{soap.TARGET_NS}}}Order")
    assert {order.findtext(f"{{{soap.TARGET_NS}}}ProviderOrderId") for order in orders} == {
        first_id,
        second_id,
    }
    unknown = _payload(
        _post(client, "FindOrdersByReference", {"ExternalRef": "NOPE"}), "FindOrdersByReference"
    )
    assert _field(unknown, "Count") == "0"
    status = _payload(_post(client, "GetOrderStatus", {"OrderId": first_id}), "GetOrderStatus")
    assert _field(status, "StatusCode") == "10"
    assert _field(status, "ExternalRef") == "CLIENT-42"


def test_get_order_status_unknown_is_business_error(client: FlaskClient) -> None:
    response = _post(client, "GetOrderStatus", {"OrderId": "LT-9999999999"})
    assert response.status_code == 200
    payload = _payload(response, "GetOrderStatus")
    assert _field(payload, "ResultCode") == "NOK"
    assert _field(payload, "ErrorCode") == str(state.ERR_ORDER_NOT_FOUND)


def test_control_advance_and_state(client: FlaskClient) -> None:
    order_id, _ = _placed_order(client)
    advanced = client.post("/_control/advance", json={"steps": 2})
    assert advanced.status_code == 200
    assert advanced.get_json()["counts"][str(state.STATUS_DONE)] == 1
    status = _payload(_post(client, "GetOrderStatus", {"OrderId": order_id}), "GetOrderStatus")
    assert _field(status, "StatusCode") == "30"
    snapshot = client.get("/_control/state").get_json()
    assert snapshot["orders"][0]["status_code"] == state.STATUS_DONE
    assert snapshot["orders"][0]["StatusText"] == "Done"


def test_control_faults_state_reset_and_validation(client: FlaskClient) -> None:
    profile = {
        "seed": 11,
        "rules": [{"operation": "check_availability", "kind": "latency", "probability": 1.0}],
    }
    assert client.post("/_control/faults", json=profile).status_code == 200
    snapshot = client.get("/_control/state").get_json()
    assert snapshot["fault_profile"]["seed"] == 11
    assert snapshot["call_counts"]["check_availability"] == 0
    _post(client, "CheckAvailability", ADDRESS)
    assert client.get("/_control/state").get_json()["call_counts"]["check_availability"] == 1
    assert client.post("/_control/reset").status_code == 200
    snapshot = client.get("/_control/state").get_json()
    assert snapshot["orders"] == []
    assert snapshot["call_counts"]["check_availability"] == 0
    assert snapshot["fault_profile"]["rules"] == []
    bad = client.post("/_control/faults", json={"rules": [{"kind": "latency", "probability": 2.0}]})
    assert bad.status_code == 400


def test_latency_fault_adds_sleep(client: FlaskClient, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[float] = []
    monkeypatch.setattr(state, "sleep", calls.append)
    _install_fault(FaultKind.LATENCY, {"fixed_ms": 50})
    response = _post(client, "CheckAvailability", ADDRESS)
    assert response.status_code == 200
    assert len(calls) == 2  # baseline address latency plus injected latency
    assert calls[1] >= 0.05


def test_timeout_fault_uses_patchable_sleep(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[float] = []
    monkeypatch.setattr(state, "sleep", calls.append)
    _install_fault(FaultKind.TIMEOUT)
    response = _post(client, "CheckAvailability", ADDRESS)
    assert response.status_code == 200
    assert state.DEFAULT_TIMEOUT_SECONDS in calls


def test_http_error_fault_is_soap_fault(client: FlaskClient) -> None:
    _install_fault(FaultKind.HTTP_ERROR)
    fault = _fault(_post(client, "CheckAvailability", ADDRESS))
    assert "Injected legacy backend failure" in (fault.findtext("faultstring") or "")


def test_malformed_response_fault_is_invalid_xml(client: FlaskClient) -> None:
    _install_fault(FaultKind.MALFORMED_RESPONSE)
    response = _post(client, "CheckAvailability", ADDRESS)
    assert response.status_code == 200
    with pytest.raises(etree.XMLSyntaxError):
        etree.fromstring(response.data)


def test_partial_response_omits_optional_elements(client: FlaskClient) -> None:
    _install_fault(FaultKind.PARTIAL_RESPONSE)
    response = _post(client, "CheckAvailability", ADDRESS)
    assert response.status_code == 200
    payload = _payload(response, "CheckAvailability")
    assert _field(payload, "ResultCode") == "OK"
    offers = payload.findall(f"{{{soap.TARGET_NS}}}Offers/{{{soap.TARGET_NS}}}Offer")
    assert offers
    for offer in offers:
        assert offer.find(f"{{{soap.TARGET_NS}}}AvailableFrom") is None
        assert offer.find(f"{{{soap.TARGET_NS}}}Remarks") is None
        assert offer.find(f"{{{soap.TARGET_NS}}}Price") is not None
