# AIntegrator

An AI agent that handles natural-language requests about connectivity services (eligibility,
ordering, status, cancellation) across three deliberately inconsistent *simulated* providers.
It demonstrates heterogeneous integration, reliable side-effecting actions, MCP, a hand-written
tool-calling agent, server-side safety controls, evals, RAG and reproducible deployment.

All domain data is fictional. No real providers, telecom specifications or employer code are used.

## Status

This repository is built phase by phase; each phase ends with a green CI, an updated README and a
git tag. **Current phase: 3 — MCP server + MCP client (`v0.3-mcp`).**

| Phase | Deliverable | Tag | Status |
| --- | --- | --- | --- |
| 0 | uv workspace, CI, Compose + Postgres, ADR template | `v0.0-scaffold` | done |
| 1 | Providers + integration layer | `v0.1-integration` | done |
| 2 | Hand-written agent + CLI | `v0.2-agent` | done |
| 3 | MCP server + MCP client | `v0.3-mcp` | done |
| 4 | Security controls | `v0.4-security` | planned |
| 5 | Evals (30+ cases, cassettes) | `v0.5-evals` | planned |
| 6 | RAG over provider docs | `v0.6-rag` | planned |
| 7 | AWS/Terraform, tracing, cost report | `v1.0` | planned |

## Architecture

```mermaid
flowchart LR
    CLI[user / CLI] --> Agent[agent]
    Agent -->|LLM API| LLM[(Anthropic)]
    Agent -->|MCP protocol| MCP[mcp_server]
    MCP --> Integration[integration / ConnectivityService]
    Integration --> DB[(PostgreSQL + pgvector)]
    Integration -->|HTTP| A[fibernet A]
    Integration -->|HTTP/SOAP| B[legacytel B]
    Integration -->|HTTP + webhooks| C[skylink C]
```

Dependencies flow one way; import-linter enforces this in CI. See
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for details and sequence diagrams.

Phase 1 adds three in-memory simulated providers (FastAPI REST, Flask/SOAP 1.1, Django + DRF
with signed webhooks), pure mappers and adapters, an async SQLAlchemy persistence layer with an
Alembic migration, and a transport-agnostic `ConnectivityService`. Provider calls use timeouts,
full-jitter retries, a circuit breaker, concurrency limits and a token-refresh path. Orders are
persisted before any external call; ambiguous timeouts are reconciled via provider reference
lookup instead of blindly retrying. Signed webhooks are stored raw and deduplicated before their
events reach the state machine, and a DB-driven reconciler converges stale and `NEEDS_REVIEW`
orders even when every webhook is lost.

Phase 2 adds a hand-written tool-calling agent and the `aintegrator` CLI. The agent loop calls
typed in-process tools (eligibility, order create/read/list/cancel) against `ConnectivityService`,
records a trace of every call, and tracks token usage and estimated cost. The LLM boundary is an
interface: tests use a deterministic scripted client, so `make check` needs no API key. The system
prompt asks the model to confirm writes first; the hard server-side approval gate arrives in
phase 4.

Phase 3 moves those tools behind a real MCP boundary. The FastMCP server calls `integration`
in-process (ADR 003) and is launched by the agent over stdio; the agent discovers tools through
`list_tools` and calls them through `call_tool`, with no import from the server package. Tool
payload logic is shared in `integration.tooling`, and the MCP SDK is pinned to the 1.x line (ADR 006).

## Quickstart

Requires Python 3.12, [uv](https://docs.astral.sh/uv/), Docker and GNU make.

```bash
cp .env.example .env
make install       # uv sync --all-packages
make check         # lint, format, strict typing, import rules, unit tests
make up            # build/start Postgres, 3 providers, webhook receiver, reconciler
make service-test  # adapter contracts + webhook/reconciler workflows
make demo          # eligibility fan-out + one order per provider
make demo-agent    # scripted agent over MCP stdio, no LLM key needed
make down
```

With the stack running and `LLM_API_KEY` set in `.env`:

```bash
uv run aintegrator run "Check availability at Fictional Street 12, Madrid" --verbose
uv run aintegrator chat
```

The agent starts `python -m aintegrator_mcp_server` itself; `uv run aintegrator-mcp` starts the
same stdio server for manual inspection.

`make help` lists all targets. The only external dependency is the LLM API; unit tests and the
agent test suite use a scripted client and need no key.

Useful local URLs: webhook receiver `http://localhost:8000/docs`, fibernet
`http://localhost:8001/docs`, legacytel `http://localhost:8002/_control/state`, skylink
`http://localhost:8003/api/v2/orders`. Provider `_control/*` endpoints are intentionally
unauthenticated and local-only; never expose these services publicly.

## Evals

No eval results yet; the runner and the 30+ cases arrive in phase 5. The final table will report
task success, forbidden-action rate, duplicate-order rate, recovery rate, steps, tokens, cost and
latency p50/p95 against a checked-in baseline.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Decisions (ADRs)](docs/decisions/)
- [Lessons learned](docs/lessons/)
- [Security threat model](docs/SECURITY.md) (phase 4)
- [Evals](docs/EVALS.md) (phase 5)

## License

MIT — see [LICENSE](LICENSE).
