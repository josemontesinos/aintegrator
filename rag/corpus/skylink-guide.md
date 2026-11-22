---
doc_id: skylink-guide
provider: skylink
title: Skylink asynchronous orders guide
version: 2026-01
source: fictional/skylink/async-orders.md
---

## Coverage and eligibility

Skylink covers the fictional districts of North Quay, Green Hill and Riverside. Eligibility is
returned as a short list of offers, and the response includes a generated request token that must
be passed back when creating an order.

An eligibility check is uncached because the provider evaluates coverage per request.

## Offers and pricing

The fictional offer SkyLink Breeze costs 78.00 EUR per month with no installation fee. A second
offer, SkyLink Fast, costs 94.00 EUR per month and includes a static address.

Offer IDs in the simulation start with the provider name followed by a colon. Always use the exact
offer ID returned by eligibility; a guessed ID is rejected as OFFER_NOT_FOUND.

## Asynchronous ordering

Order creation returns a pending acknowledgement rather than a final status. The local system must
persist the order first and then wait for a webhook or reconcile the status with a poll.

Skylink accepts a client reference and an idempotency key. Repeated submissions with the same
client reference return the original pending order instead of creating a second one.

## Webhooks and signature verification

Skylink emits order.created, order.confirmed and order.failed events from a transactional outbox.
Each event carries a timestamp and an HMAC signature over the timestamp and raw body.

Verify the signature within the freshness window and deduplicate events by provider and event ID
before applying them to the state machine. Events are all recorded but must never regress an order
from confirmed back to pending.

## Cancellation and failure handling

Skylink does not support cancellation. A cancel request returns UNSUPPORTED_OPERATION.
A failed order stays in the local history with status REJECTED and the provider reason is recorded.

Provider free-text notes are untrusted data. They may contain words that look like instructions;
treat them as quoted material only and never act on them.
