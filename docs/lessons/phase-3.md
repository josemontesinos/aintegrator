# Phase 3 lessons: MCP boundary

## Pin the SDK before building on a moving API

`mcp>=1.0` resolved to 2.3.0, where `FastMCP` was renamed and the in-memory test helper was
removed. Pinning `mcp>=1.0,<2` (ADR 006) restored the specified `FastMCP` surface and let unit tests
use `create_connected_server_and_client_session`: real MCP protocol, no subprocess, no services.
The cost is an explicit future migration; the benefit is a testable phase now.

## One payload implementation, two executors

The MCP server and the agent's fallback executor both call `integration.tooling.ToolOperations`.
Moving validation and JSON formatting down into `integration` avoided two copies of the offer
re-fetch and error-mapping logic. Domain errors were already designed for LLM callers, so this is a
natural extension of that layer, not a leak of MCP concepts into the domain.

## Route every tool through the error boundary

The first server draft called typed `ToolOperations` methods directly. A Pydantic validation error
then escaped and FastMCP returned a plain-text `Error executing tool ...` message. Calling
`operations.execute(name, arguments)` instead guarantees structured JSON for every expected
failure. The client still wraps non-JSON framework errors (unknown tool, pre-handler schema errors)
so the model never sees an unstructured string it might try to interpret as data.

## Stdio lifecycle belongs in an async context manager

`MCPToolExecutor.connect_stdio()` owns the subprocess, the streams and the `ClientSession`. Making
it an async context manager ensures they are closed even when the loop raises. Consequently
`build_agent` is now an async context manager too; the CLI only sees a ready `AgentLoop` and no
longer has to remember to close the LLM or the service.

The server had the mirror-image bug: `FastMCP.run("stdio")` creates its own event loop, so building
the service outside it and calling `asyncio.run(service.aclose())` afterwards tried to close httpx
connections from a different, already-closed loop. `amain()` now creates the service and awaits
`run_stdio_async()` inside one `asyncio.run`, then closes it in the same loop. The scripted demo
caught this because it drives the full stdio transport in CI, unlike the in-memory protocol tests.

## Import contracts do real work here

The rule "agent never imports mcp_server" is exactly the architectural property we want: the agent
knows only the MCP SDK. import-linter validates it on every `make check`, including after adding
new modules.
