"""Smoke tests for the agent package and CLI surface."""

import pytest
from aintegrator_agent import __version__
from aintegrator_agent.cli import build_parser, main


def test_package_version() -> None:
    assert __version__ == "0.1.0"


def test_cli_help_exits_cleanly(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "aintegrator" in capsys.readouterr().out


def test_run_parser_exposes_phase_2_flags() -> None:
    args = build_parser().parse_args(["run", "check coverage", "--json", "--approve", "auto"])
    assert args.command == "run"
    assert args.json is True
    assert args.approve == "auto"


def test_chat_parser_exposes_initial_task(capsys: pytest.CaptureFixture[str]) -> None:
    args = build_parser().parse_args(["chat", "hello"])
    assert args.task == "hello"
    assert args.verbose is False
    assert capsys.readouterr().out == ""


def test_approvals_parser_exposes_pending_and_approve() -> None:
    pending = build_parser().parse_args(["approvals", "pending"])
    assert pending.approval_command == "pending"

    approve = build_parser().parse_args(["approvals", "approve", "id-1"])
    assert approve.approval_command == "approve"
    assert approve.approval_id == "id-1"
