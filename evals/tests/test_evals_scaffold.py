"""Smoke test for the scaffolded package."""

from aintegrator_evals import __version__


def test_package_version() -> None:
    assert __version__ == "0.1.0"
