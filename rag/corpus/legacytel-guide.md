---
doc_id: legacytel-guide
provider: legacytel
title: Legacytel SOAP integration guide
version: 2025-11
source: fictional/legacytel/soap-guide.md
---

## Protocol quirks

Legacytel exposes a SOAP 1.1 endpoint. The namespace is fictional and every request must include a
Basic authentication header. WS-Security is accepted but not required by this simulated provider.

Responses sometimes use empty elements instead of null values. Map an empty price element to zero
and an empty status element to UNKNOWN rather than failing the whole eligibility fan-out.

## Coverage and eligibility

Legacytel covers the fictional districts of Central and Old Harbour. Its coverage register is
smaller than the other providers, so an address can be covered by Fibernet and not by Legacytel.

Eligibility results are not cacheable: the underlying register changes during the nightly import.
Always call the provider for a fresh answer instead of serving a cached one.

## Offers and pricing

The single fictional offer is called Legacy Broadband and costs 72.00 EUR per month. There is no
installation fee. The offer ID used in this simulation is legacytel:LEG-102.

The SOAP response reports the monthly price as a string with a currency suffix. Strip the suffix
and parse the value as decimal currency before applying spend caps.

## Ordering

Order creation uses the PlaceOrder operation. The provider has no native idempotency field, so the
client must pass its client reference in the request and use the reference lookup operation to
resolve ambiguous timeouts.

PlaceOrder returns a numeric provider order reference. Store it alongside the local order ID so
the reconciler can find the order again after a lost response.

## Reference lookup and cancellation

FindOrdersByReference is the authoritative lookup when a PlaceOrder response is lost. It accepts
the client reference and returns zero or more matching orders.

Legacytel does not support order cancellation. A cancel attempt returns UNSUPPORTED_OPERATION. Do not
present a cancellation promise to a customer for this provider.

## Maintenance windows

The nightly import runs between 01:00 and 02:00 UTC. During that hour eligibility may be reported
as unavailable. Retry once after the window; do not treat a maintenance response as "no offers".
