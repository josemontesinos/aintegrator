"""Smoke test for the MCP server package."""

from aintegrator_mcp_server import __version__, create_server, main


def test_package_version() -> None:
    assert __version__ == "0.1.0"


def test_public_api_is_exported() -> None:
    assert callable(create_server)
    assert callable(main)
