"""Console entry point for the ``aintegrator`` command.

Only the version/help surface exists in phase 0. The chat/run/approval commands
are wired to the agent loop in phase 2 and to the MCP client in phase 3.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from aintegrator_agent import __version__

_PHASE_BY_COMMAND = {
    "chat": "phase 2",
    "run": "phase 2",
    "approvals": "phase 4",
}


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="aintegrator",
        description="AI assistant for the fictional connectivity marketplace.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    chat = subparsers.add_parser("chat", help="Interactive chat with inline approvals")
    chat.add_argument("task", nargs="?", help="Optional initial request")

    run = subparsers.add_parser("run", help="Run a single task non-interactively")
    run.add_argument("task", help="Task to execute")
    run.add_argument(
        "--approve",
        choices=("never", "ask", "auto"),
        default="ask",
        help="Approval policy (auto is only for evals)",
    )

    approvals = subparsers.add_parser("approvals", help="Manage pending approvals")
    approval_subparsers = approvals.add_subparsers(dest="approval_command")
    approve = approval_subparsers.add_parser("approve", help="Grant a pending approval")
    approve.add_argument("approval_id", help="ID of the approval to grant")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    phase = _PHASE_BY_COMMAND.get(str(args.command), "a later phase")
    print(f"`aintegrator {args.command}` is a scaffold and arrives in {phase}.")
    return 0


if __name__ == "__main__":  # pragma: no cover - manual entry point
    raise SystemExit(main())
