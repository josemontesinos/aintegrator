# Architecture

Phase 0 defines the boundaries; later phases fill in the components.

```mermaid
flowchart TD
    user[User / CLI] --> agent[agent]
    agent --> llm[LLM API]
    agent -->|MCP protocol| mcp[mcp_server]
    mcp --> service[integration / ConnectivityService]
    service --> adapters[Provider adapters + mappers]
    adapters --> pg[(PostgreSQL + pgvector)]
    adapters --> fibernet[fibernet A: REST/JSON]
    adapters --> legacytel[legacytel B: SOAP/XML]
    adapters --> skylink[skylink C: async + webhooks]
    skylink -->|signed webhooks| receiver[Webhook receiver]
    receiver --> reconciler[Reconciler]
    reconciler --> pg
```

## Dependency rules (enforced by import-linter)

- `agent` never imports `mcp_server`; it speaks the MCP protocol.
- `mcp_server` imports `integration`, never `agent` or a provider package.
- `integration` never imports `agent`, `mcp_server` or provider packages; it reaches providers
  only over HTTP.
- Each provider package imports only `provider_common`.
- `evals` and `rag` may import what they need.

## Repository layout

Every top-level package is a uv workspace member with its own `pyproject.toml` and tests:

```
providers/{provider_common,fibernet,legacytel,skylink}/
integration/  mcp_server/  agent/  evals/  rag/
infra/terraform/  docs/  docker-compose.yml  Makefile  .github/workflows/
```

## Phase 0 decisions

Recorded in [docs/decisions](decisions/): project name, embeddings default, in-process MCP server,
Step Functions as a complement to the reconciler, and the default LLM model.

The order-creation sequence diagram (including the ambiguous-timeout path) is added in phase 1.
