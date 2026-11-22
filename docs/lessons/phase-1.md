# Phase 1 lessons: providers and integration

## Verify wire contracts against a running provider

Unit tests with hand-written DTOs all passed while the Skylink adapter was still sending the wrong
payload: a flattened availability address and `customerName`/`customerEmail` order fields. The
provider actually requires a nested `address` object on availability checks and a nested `customer`
object on order creation. Only a contract test against the live simulated provider caught it.
Keep both layers: unit tests for mapping logic, contract tests for the wire.

Fictional identifiers are part of the contract too. Skylink emits technologies such as
`fictional-fiber` and `fictional-mesh`; without explicit aliases the mapper rejected valid offers.

## SOAP faults need semantic classification

Zeep turns an HTTP 500/502 SOAP response into `zeep.exceptions.Fault`, so treating every fault as a
non-retryable protocol error loses the distinction between a bad request and a transient server
failure. Classify by fault code: `soapenv:Server` is retryable (`ProviderUnavailable`),
`soapenv:Client` is a rejection, anything else is a protocol error. This also makes the circuit
breaker and retry policy behave correctly.

## Fault-injection endpoints must be reset in `finally`

The provider `_control/faults` endpoints persist a profile until reset. A contract test that fails
to reset leaves the provider broken for every later test and for the demo. Always reset in a
`finally` block, both for the adapter and for the HTTP reset call.

## Persist the idempotency key on the order

Reconciliation and ambiguous-timeout recovery rebuild an `OrderIntent` from the stored order. If the
idempotency key is not persisted, the retry silently loses provider-native deduplication and can
create a duplicate. The key is now a first-class column on `orders`, covered by both the Alembic
migration and the ORM model.

## Cache expiry and hit semantics belong in the repository

An eligibility cache entry that ignores `expires_at` becomes permanent, and a cache hit that does
not set `from_cache` is indistinguishable from a fresh fetch. Both are now enforced in
`Repository.get_cache`, so the service can skip provider calls correctly.

## Keep state-machine events append-only

Webhook duplicates and stale/out-of-order events are still recorded, but a rank-based state machine
prevents them from regressing an order. This makes the event log useful for debugging while keeping
the current state monotonic. Deduplication happens earlier, on the raw `(provider, event_id)` key.

## SQLite hides Postgres integrity errors

Unit tests used in-memory SQLite, which ignores foreign keys by default. A single flush that added
an order and its event rows inserted the child first on Postgres and failed with
`order_events_order_id_fkey`; SQLite happily accepted it. Two fixes: flush the parent before the
events in `Repository.create_order`, and enable `PRAGMA foreign_keys=ON` for every SQLite engine so
unit tests enforce the same constraint as production. The demo against the real compose stack is
what surfaced the bug in the first place, so keep a service-backed smoke step in CI.

## Tooling friction

Ruff's formatter and its `E501` rule can disagree on nested ternaries: the formatter collapses an
expression the linter then flags. Prefer an explicit `if/else` statement over a long inline ternary.
Renaming `integration/tests/test_webhooks.py` to `test_webhook_receiver.py` avoided a pytest module
name collision with `provider_common/tests/test_webhooks.py` when running the whole workspace.
