"""Hand-written SOAP 1.1 envelope parsing and building with lxml.

No server-side SOAP toolkit is used here: the module implements just the subset of
SOAP needed by the simulated provider. Element names, namespace handling and the
order of response fields deliberately mirror ``wsdl/legacytel.wsdl``.
"""

from __future__ import annotations

import base64
import binascii
import hmac
from collections.abc import Mapping
from pathlib import Path

from lxml import etree  # type: ignore[import-untyped]

__all__ = [
    "MALFORMED_RESPONSE_XML",
    "REQUEST_FIELDS",
    "RESPONSE_FIELDS",
    "SOAP_NS",
    "TARGET_NS",
    "WSDL_PATH",
    "WSSE_NS",
    "SoapProtocolError",
    "build_fault",
    "build_response",
    "load_wsdl",
    "parse_envelope",
    "validate_action",
    "validate_credentials",
]

SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"
TARGET_NS = "urn:legacytel:soap"
WSSE_NS = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
WSDL_PATH = Path(__file__).parent / "wsdl" / "legacytel.wsdl"

REQUEST_FIELDS: dict[str, tuple[str, ...]] = {
    "CheckAvailability": ("PostalCode", "Street", "Number"),
    "PlaceOrder": (
        "ExternalRef",
        "OfferId",
        "CustomerName",
        "CustomerEmail",
        "PostalCode",
        "Street",
        "Number",
    ),
    "GetOrderStatus": ("OrderId",),
    "FindOrdersByReference": ("ExternalRef",),
}

RESPONSE_FIELDS: dict[str, tuple[str, ...]] = {
    "CheckAvailabilityResponse": ("ResultCode", "ErrorCode", "ErrorText", "Offers"),
    "PlaceOrderResponse": (
        "ResultCode",
        "ErrorCode",
        "ErrorText",
        "ProviderOrderId",
        "ExternalRef",
        "StatusCode",
        "StatusText",
        "OrderDate",
        "Price",
        "Remarks",
    ),
    "GetOrderStatusResponse": (
        "ResultCode",
        "ErrorCode",
        "ErrorText",
        "ProviderOrderId",
        "ExternalRef",
        "StatusCode",
        "StatusText",
        "LastUpdated",
        "Remarks",
    ),
    "FindOrdersByReferenceResponse": (
        "ResultCode",
        "ErrorCode",
        "ErrorText",
        "Count",
        "Orders",
    ),
}

OFFER_FIELDS = ("OfferId", "Name", "Price", "AvailableFrom", "Remarks")
ORDER_FIELDS = (
    "ProviderOrderId",
    "ExternalRef",
    "StatusCode",
    "StatusText",
    "OrderDate",
    "Price",
    "Remarks",
)
OPTIONAL_FIELDS = frozenset({"AvailableFrom", "Remarks", "OrderDate", "LastUpdated", "StatusText"})

# Intentionally not well-formed XML: simulates a broken legacy responder.
MALFORMED_RESPONSE_XML = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
    b"<soapenv:Body>"
    b'<CheckAvailabilityResponse xmlns="urn:legacytel:soap">'
    b"<ResultCode>OK</ResultCode>"
)


class SoapProtocolError(Exception):
    """A request error that must be reported as a SOAP Fault with HTTP 500."""

    def __init__(self, message: str, *, code: str = "soapenv:Client") -> None:
        super().__init__(message)
        self.code = code


def load_wsdl() -> bytes:
    """Read the static WSDL document shipped inside the package."""
    return WSDL_PATH.read_bytes()


def _qname(name: str) -> str:
    return f"{{{TARGET_NS}}}{name}"


def _child(element: etree._Element, name: str) -> etree._Element | None:
    """Find a direct child, preferring the target namespace but tolerating no prefix."""
    child = element.find(_qname(name))
    return child if child is not None else element.find(name)


def parse_envelope(
    body: bytes,
) -> tuple[str, dict[str, str], etree._Element | None]:
    """Parse a SOAP request and return its operation, fields and optional header."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
    try:
        root = etree.fromstring(body, parser=parser)
    except etree.XMLSyntaxError as exc:
        raise SoapProtocolError(f"Malformed SOAP XML: {exc}") from exc

    if root.tag != _soap_tag("Envelope"):
        raise SoapProtocolError("The request root element must be a SOAP Envelope.")

    header = root.find(_soap_tag("Header"))
    body_element = root.find(_soap_tag("Body"))
    if body_element is None:
        raise SoapProtocolError("The SOAP Body is missing.")

    operations = [child for child in body_element if isinstance(child.tag, str)]
    if len(operations) != 1:
        raise SoapProtocolError("The SOAP Body must contain exactly one operation element.")

    operation_element = operations[0]
    qname = etree.QName(operation_element)
    if qname.namespace != TARGET_NS:
        raise SoapProtocolError(f"Operation must be in namespace {TARGET_NS!r}.")
    operation = qname.localname
    if operation not in REQUEST_FIELDS:
        raise SoapProtocolError(f"Unknown operation: {operation}", code="soapenv:Server")

    fields: dict[str, str] = {}
    for field in REQUEST_FIELDS[operation]:
        element = _child(operation_element, field)
        if element is None or element.text is None or not element.text.strip():
            raise SoapProtocolError(f"Missing required element: {field}")
        fields[field] = element.text.strip()
    return operation, fields, header


def validate_action(action: str | None, operation: str) -> None:
    """Accept an empty SOAPAction or ``urn:legacytel:soap/<Operation>``."""
    value = (action or "").strip().strip('"')
    if not value:
        return
    expected = f"{TARGET_NS}/{operation}"
    if value != expected:
        raise SoapProtocolError(f"SOAPAction {value!r} does not match {expected!r}.")


def validate_credentials(
    authorization: str | None,
    header: etree._Element | None,
    username: str,
    password: str,
) -> None:
    """Accept HTTP Basic credentials or a WS-Security UsernameToken."""
    if _basic_credentials_match(authorization, username, password):
        return
    if header is not None:
        token = header.find(f"{{{WSSE_NS}}}Security/{{{WSSE_NS}}}UsernameToken")
        if token is not None:
            user_element = token.find(f"{{{WSSE_NS}}}Username")
            password_element = token.find(f"{{{WSSE_NS}}}Password")
            user = (user_element.text or "").strip() if user_element is not None else ""
            secret = (password_element.text or "").strip() if password_element is not None else ""
            if hmac.compare_digest(user, username) and hmac.compare_digest(secret, password):
                return
    raise SoapProtocolError(
        "Authentication failed: valid Basic or UsernameToken credentials required."
    )


def build_fault(code: str, message: str) -> bytes:
    """Build a SOAP 1.1 Fault envelope."""
    envelope = etree.Element(_soap_tag("Envelope"), nsmap={"soapenv": SOAP_NS})
    body = etree.SubElement(envelope, _soap_tag("Body"))
    fault = etree.SubElement(body, _soap_tag("Fault"))
    etree.SubElement(fault, "faultcode").text = code
    etree.SubElement(fault, "faultstring").text = message
    return bytes(etree.tostring(envelope, xml_declaration=True, encoding="UTF-8"))


def build_response(
    operation: str,
    payload: Mapping[str, object],
    *,
    partial: bool = False,
) -> bytes:
    """Build a SOAP response envelope for ``<Operation>Response``.

    When ``partial`` is true, optional elements defined in the WSDL are omitted to
    emulate a legacy responder that drops fields under load.
    """
    response_name = f"{operation}Response"
    field_names = RESPONSE_FIELDS[response_name]
    envelope = etree.Element(_soap_tag("Envelope"), nsmap={"soapenv": SOAP_NS, "tns": TARGET_NS})
    body = etree.SubElement(envelope, _soap_tag("Body"))
    response = etree.SubElement(body, _qname(response_name))
    _append_fields(response, field_names, payload, partial=partial)
    return bytes(etree.tostring(envelope, xml_declaration=True, encoding="UTF-8"))


def _soap_tag(name: str) -> str:
    return f"{{{SOAP_NS}}}{name}"


def _basic_credentials_match(authorization: str | None, username: str, password: str) -> bool:
    if not authorization:
        return False
    scheme, separator, encoded = authorization.partition(" ")
    if not separator or scheme.lower() != "basic":
        return False
    try:
        decoded = base64.b64decode(encoded.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return False
    supplied_user, separator, supplied_password = decoded.partition(":")
    return (
        bool(separator)
        and hmac.compare_digest(supplied_user, username)
        and hmac.compare_digest(supplied_password, password)
    )


def _append_fields(
    parent: etree._Element,
    field_names: tuple[str, ...],
    values: Mapping[str, object],
    *,
    partial: bool,
) -> None:
    for name in field_names:
        value = values.get(name)
        if name in OPTIONAL_FIELDS and (partial or value is None):
            continue
        if name == "Offers":
            _append_items(parent, name, "Offer", OFFER_FIELDS, value, partial=partial)
        elif name == "Orders":
            _append_items(parent, name, "Order", ORDER_FIELDS, value, partial=partial)
        else:
            element = etree.SubElement(parent, _qname(name))
            element.text = "" if value is None else str(value)


def _append_items(
    parent: etree._Element,
    container_name: str,
    item_name: str,
    item_fields: tuple[str, ...],
    value: object,
    *,
    partial: bool,
) -> None:
    container = etree.SubElement(parent, _qname(container_name))
    if not isinstance(value, (list, tuple)):
        return
    for item in value:
        if not isinstance(item, Mapping):
            continue
        entry = etree.SubElement(container, _qname(item_name))
        _append_fields(entry, item_fields, item, partial=partial)
