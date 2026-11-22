# Phase 2 lessons: agent and CLI

## Put the LLM behind a protocol from day one

The loop only knows `LLMClient.complete(...)`. `AnthropicLLM` implements it with the real SDK and
`ScriptedLLM` implements it with a queue of replies. That single seam is why the whole agent suite
runs in milliseconds with no API key, and why eval cassettes will fit in phase 5 without touching
the loop. The same trick applies to tools: `ToolExecutor` is a protocol, so phase 3 can swap
in-process calls for MCP without changing prompts or traces.

## Never trust model-supplied domain objects

The first draft of `create_order` accepted a full offer from the model. That is unsafe: a model can
hallucinate a price or an offer ID. The tool now accepts only `(provider, offer_id, address,
customer)` and re-runs eligibility through the service to obtain the authoritative `Offer` before
building the `OrderIntent`. Idempotency keys default to a fresh UUID when the model does not provide
one, so a repeated turn cannot accidentally reuse a stale key.

## Errors are data, not exceptions

Every tool returns a JSON string with `{"ok": false, "error": {"code", "message", "retryable"}}`
for expected failures. The model can explain a rejection or a rate limit to the user instead of
seeing a Python traceback, and the loop marks failed calls in the trace for eval scoring. Pydantic
validation errors are converted the same way.

## SQLite expiry exposed a latent Phase 1 bug

`InProcessTools` re-fetches offers, which made a second eligibility call hit the cache. SQLite
returns `DateTime(timezone=True)` columns as naive datetimes, so comparing `expires_at` with an aware
`datetime.now(UTC)` raised `TypeError`. `service.check_eligibility` then reported the provider as
`ERROR`. Fixed by normalizing naive timestamps to UTC in `Repository.get_cache` and adding a unit
test for cache round-trips. Postgres was never affected, which is exactly why the SQLite test path
matters.

## Confirmation in a prompt is UX, not a security control

The system prompt tells the model to ask before calling `create_order` or `cancel_order`, and the
chat command keeps conversation history so a confirmation can be a later turn. This is still only
model behavior. The CLI's `--approve` flag is intentionally parsed but not enforced; phase 4 will
replace it with single-use, parameter-bound, expiring approvals on the server.

## Tooling friction

`AnthropicLLM` first passed `model` and `max_tokens` to the `AsyncAnthropic` constructor; those
belong to `messages.create`. A monkeypatched `create` test caught it without a network call. Ruff's
`B007` also rejected a `for step in range(...)` loop variable that was only used after the loop;
an explicit `while step < max_steps` counter reads better and satisfies the linter.
