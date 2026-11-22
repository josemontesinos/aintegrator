"""Smoke test for the RAG package."""

from aintegrator_rag import __version__


def test_package_version() -> None:
    assert __version__ == "0.6.0"
