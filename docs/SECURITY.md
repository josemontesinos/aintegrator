# Security

**Current status (phase 3).** Tool descriptions and the system prompt ask the model to request
explicit user confirmation before `create_order` or `cancel_order`. That is a UX measure, not a
security control: the MCP server currently accepts any well-formed tool call. The CLI's `--approve`
flag is parsed but not enforced. Do not treat this phase as safe for untrusted users or untrusted
provider text.

**Planned for phase 4 (`v0.4-security`).** This file will contain the threat model (assets,
actors, abuse cases), the prompt-injection strategy and the hard controls that do not depend on
the model:

- server-side approval gate for every write tool (single-use, parameter-bound, expiring),
- per-order and per-period spend caps,
- write-tool rate limits and per-profile tool allow-lists (including a read-only profile),
- least-privilege DB role for the MCP server,
- append-only audit log of every tool call,
- secret redaction in logs and traces, gitleaks in CI.
