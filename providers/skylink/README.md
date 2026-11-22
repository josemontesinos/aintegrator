# SkyLink — simulated provider C

Django 5+/DRF service on port **8003** with asynchronous availability lookups, orders and
signed webhooks. All data is fictional (`SL-…` offers, `C-…` orders, `Nimbus City` addresses).

## Run

```bash
# From the workspace root (dependencies are managed by the parent agent):
uv run python -m aintegrator_skylink.manage migrate --noinput
uv run python -m aintegrator_skylink.manage runserver 0.0.0.0:8003 --noreload

# In a second terminal, deliver signed webhooks:
uv run python -m aintegrator_skylink.manage deliver_webhooks --loop

# Unit tests (no services, worker disabled):
uv run pytest providers/skylink/tests -q
```

`manage.py` is executable as a module: `python -m aintegrator_skylink.manage <command>`.

## Environment

| Variable | Default | Meaning |
| --- | --- | --- |
| `SKYLINK_DB_PATH` | `/tmp/aintegrator_skylink.sqlite3` | SQLite database path (`:memory:` in tests) |
| `SKYLINK_API_KEY` | `skylink-key` | Required `X-Api-Key` header value |
| `SKYLINK_WEBHOOK_SECRET` | `skylink-secret` | HMAC-SHA256 signing secret |
| `SKYLINK_WEBHOOK_URL` | `http://localhost:8000/webhooks/skylink` | Webhook POST target |
| `SKYLINK_AVAILABILITY_DELAY_S` | `2` | Lookup delay, clamped to 2–60 s |
| `SKYLINK_ORDER_STEP_DELAY_S` | `1` | Delay between order lifecycle steps |
| `SKYLINK_SEED` | `20261122` | Deterministic offer/outcome seed |
| `SKYLINK_WORKER_ENABLED` | `1` | Set to `0` to disable the auto-advance thread |
| `SKYLINK_WORKER_POLL_INTERVAL_S` | `0.5` | Worker polling interval |
| `SKYLINK_DELIVERY_BATCH_SIZE` | `10` | Events per delivery batch |
| `SKYLINK_DELIVERY_LOOP_INTERVAL_S` | `0.2` | `--loop` sleep between batches |
| `SKYLINK_DELIVERY_RETRY_BASE_S` | `0.1` | Retry backoff base (max 5 attempts) |
| `SKYLINK_MAX_DELIVERY_ATTEMPTS` | `5` | Delivery attempts per event |

## API (port 8003)

All public endpoints require `X-Api-Key`. Responses use
`{"data": …, "meta": {"generatedAt": <epoch ms>, …}, "errors": [...]}`;
money is `{"amount": "49.90", "currency": "EUR"}`.

- `POST /api/v2/availability-requests` with `{"address": …, "notes": "…"}` → `202`

  ```json
  {"data": {"requestId": "<uuid>", "status": "QUEUED", "notes": "…"},
   "meta": {"generatedAt": 1760000000000, "eventType": "availability.completed", "expectedDelayS": 2},
   "errors": []}
  ```

- `GET /api/v2/availability-requests/{requestId}` → `200`; `data` gains `status: "DONE"`
  and `offers: [{offerId, name, technology, price, installmentDays, notes}, …]` when ready.
- `POST /api/v2/orders` with `clientReference`, `offerId`, `address`, `customer`
  (all required, no idempotency header, no cancel) → `202`

  ```json
  {"data": {"orderId": "C-A1B2C3D4E5", "clientReference": "ref-1", "status": "QUEUED", "notes": "…"},
   "meta": {"generatedAt": 1760000000000,
            "eventTypes": ["order.confirmed", "order.working", "order.done", "order.declined"],
            "idempotency": "not supported"},
   "errors": []}
  ```

- `GET /api/v2/orders?clientReference=ref-1` → `200`, `data` is a list of order objects,
  `meta.count` is its length.
- `GET /api/v2/orders/{orderId}` → `200`, includes `webhookEvents:
  [{eventId, type, sequence, occurredAt, deliveryStatus}, …]`.

Order statuses: `QUEUED → CONFIRMED → WORKING → DONE | DECLINED`. Availability statuses:
`QUEUED → DONE`.

## Webhooks

`OutboxEvent` rows carry `eventId` (UUID), `type`, `occurredAt` (epoch ms), a per-reference
monotonic `sequence`, the exact signed `payload`, `status` and `attempts`. Bodies are
canonical JSON (`sort_keys=True`, compact separators):

```json
{"eventId": "<uuid>", "type": "order.confirmed", "occurredAt": 1760000000000,
 "sequence": 2, "orderReference": "C-A1B2C3D4E5", "data": {"orderId": "…", "status": "CONFIRMED", …}}
```

Header: `X-Skylink-Signature: t=<unix>,v1=<hex HMAC-SHA256(secret, t + '.' + raw_body)>`.
Duplicates repeat the same body/eventId (at-least-once); reorders swap adjacent events;
drops mark the event `DROPPED`; delays sleep before posting. Non-2xx responses are retried
up to five times with exponential backoff before the event becomes `FAILED`.

## Local control endpoints (no API key)

- `POST /_control/faults` — set `FaultProfile`, e.g.
  `{"seed": 1, "rules": [{"operation": "orders.create", "kind": "http_error", "probability": 1.0, "params": {"status": 503}}]}`
- `POST /_control/reset` — delete all orders/requests/events and reset counters.
- `POST /_control/advance` — advance all pending states now and create events.
- `GET /_control/state` — current requests, orders, events, fault profile and call counts.

Inbound fault operations: `availability.create`, `availability.get`, `orders.create`,
`orders.get`; delivery faults use `webhook.delivery`. Supported inbound kinds: `latency`,
`timeout` (504), `http_error`, `malformed_response`, `partial_response`, `rate_limit`,
`token_expiry`.

Django admin (`/admin/`) supports forcing order status and resending, duplicating or
dropping outbox events.
