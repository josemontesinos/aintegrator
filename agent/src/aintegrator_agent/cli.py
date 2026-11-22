"""Console entry point for the ``aintegrator`` command."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence

from aintegrator_integration.domain import AIntegratorError
from aintegrator_integration.factory import build_service
from aintegrator_integration.security import ApprovalService

from aintegrator_agent import __version__
from aintegrator_agent.agent import build_agent
from aintegrator_agent.approval import ApprovalPolicy
from aintegrator_agent.config import AgentSettings, get_agent_settings
from aintegrator_agent.llm import Message
from aintegrator_agent.result import AgentResult

_APPROVE_HELP = (
    "write approval policy: never blocks writes, ask prompts for each pending approval, "
    "auto grants them on your behalf"
)


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="aintegrator",
        description="AI assistant for the fictional connectivity marketplace.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    chat = subparsers.add_parser("chat", help="Interactive chat with conversation history")
    chat.add_argument("task", nargs="?", help="Optional initial request")
    chat.add_argument(
        "--approve", choices=("never", "ask", "auto"), default="ask", help=_APPROVE_HELP
    )
    chat.add_argument("-v", "--verbose", action="store_true", help="Show tool calls")

    run = subparsers.add_parser("run", help="Run a single task non-interactively")
    run.add_argument("task", help="Task to execute")
    run.add_argument(
        "--approve", choices=("never", "ask", "auto"), default="ask", help=_APPROVE_HELP
    )
    run.add_argument("--json", action="store_true", help="Print the full result as JSON")
    run.add_argument("-v", "--verbose", action="store_true", help="Show tool calls on stderr")

    approvals = subparsers.add_parser("approvals", help="Manage pending write approvals")
    approval_commands = approvals.add_subparsers(dest="approval_command", required=True)
    approval_commands.add_parser("pending", help="List pending approvals as JSON lines")
    approve = approval_commands.add_parser("approve", help="Grant a pending approval")
    approve.add_argument("approval_id", help="ID of the approval to grant")

    return parser


def _print_traces(result: AgentResult) -> None:
    for trace in result.tool_traces:
        status = "ERROR" if trace.error else "ok"
        print(
            f"  [{status}] {trace.name} ({trace.duration_ms:.0f} ms) "
            f"{json.dumps(trace.arguments, ensure_ascii=False)}",
            file=sys.stderr,
        )
    print(
        f"  usage: {result.usage.input_tokens} in / {result.usage.output_tokens} out "
        f"/ ${result.usage.cost_usd:.6f}",
        file=sys.stderr,
    )


async def _run_task(
    task: str,
    settings: AgentSettings,
    *,
    as_json: bool,
    verbose: bool,
    approve: ApprovalPolicy,
) -> int:
    async with build_agent(settings, approve=approve) as loop:
        result = await loop.run(task)

    if as_json:
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    else:
        print(result.answer)
        if verbose:
            _print_traces(result)
    return 0


async def _chat(
    settings: AgentSettings,
    *,
    initial_task: str | None,
    verbose: bool,
    approve: ApprovalPolicy,
) -> int:
    history: tuple[Message, ...] = ()
    async with build_agent(settings, approve=approve) as loop:
        print("AIntegrator — type 'exit' or press Ctrl-D to quit.")
        pending = [initial_task] if initial_task else []
        while True:
            try:
                line = pending.pop(0) if pending else input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            if line.lower() in {"exit", "quit"}:
                break
            result = await loop.run(line, history=history)
            history = result.messages
            print(f"agent> {result.answer}")
            if verbose:
                _print_traces(result)
    return 0


async def _manage_approvals(args: argparse.Namespace) -> int:
    service = build_service()
    try:
        approvals = ApprovalService(service.session_maker, service.settings)
        if args.approval_command == "pending":
            items = await approvals.pending()
            if not items:
                print("No pending approvals.")
            for item in items:
                print(json.dumps(item.as_dict(), ensure_ascii=False))
            return 0

        granted = await approvals.grant(args.approval_id)
        print(f"Approved {granted.id}: {granted.summary}")
        return 0
    except AIntegratorError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 2
    finally:
        await service.aclose()


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0

    settings = get_agent_settings()
    if args.command == "approvals":
        return asyncio.run(_manage_approvals(args))
    if args.command == "run":
        return asyncio.run(
            _run_task(
                args.task,
                settings,
                as_json=args.json,
                verbose=args.verbose,
                approve=args.approve,
            )
        )
    return asyncio.run(
        _chat(
            settings,
            initial_task=args.task,
            verbose=args.verbose,
            approve=args.approve,
        )
    )


if __name__ == "__main__":  # pragma: no cover - manual entry point
    raise SystemExit(main())
