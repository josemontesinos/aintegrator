"""FastMCP server exposing the integration layer as LLM-facing tools."""

from aintegrator_mcp_server.server import create_server, main

__all__ = ["__version__", "create_server", "main"]

__version__ = "0.1.0"
