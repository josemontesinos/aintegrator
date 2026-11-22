"""Smoke tests for the scaffolded agent package."""

import pytest
from aintegrator_agent import __version__
from aintegrator_agent.cli import main


def test_package_version() -> None:
    assert __version__ == "0.1.0"


def test_cli_help_exits_cleanly(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "aintegrator" in capsys.readouterr().out


def test_cli_command_is_a_scaffold(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["chat", "hello"]) == 0
    assert "phase 2" in capsys.readouterr().out
