"""CLI-side approval policy wrapper around a tool executor.

The model never grants approvals itself. It can only ask a write tool; the
server creates a pending, parameter-bound token, and this wrapper either prompts
the human, auto-grants because the human passed ``--approve auto``, or refuses.
The server still consumes and validates the token before the write happens.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any, Literal

from aintegrator_integration.domain import AIntegratorError
from aintegrator_integration.security import WRITE_TOOLS, ApprovalService

from aintegrator_agent.llm import ToolCall, ToolDefinition
from aintegrator_agent.tools import ToolExecutor

__all__ = ["ApprovalPolicy", "ApprovalPolicyExecutor"]

ApprovalPolicy = Literal["never", "ask", "auto"]


def _error(code: str, message: str) -> str:
    return json.dumps(
        {"ok": False, "error": {"code": code, "message": message, "retryable": False}}
    )


class ApprovalPolicyExecutor:
    """Enforce ``--approve`` without weakening the server-side gate."""

    def __init__(
        self,
        inner: ToolExecutor,
        approvals: ApprovalService,
        *,
        policy: ApprovalPolicy = "ask",
        prompt: Callable[[str], str] = input,
        output: Callable[[str], None] | None = None,
    ) -> None:
        self._inner = inner
        self._approvals = approvals
        self._policy = policy
        self._prompt = prompt
        self._output = output or (lambda message: print(message, file=sys.stderr))

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return self._inner.definitions()

    async def execute(self, call: ToolCall) -> str:
        if self._policy == "never" and call.name in WRITE_TOOLS:
            return _error(
                "TOOL_NOT_ALLOWED", f"write tool {call.name!r} is disabled by --approve never"
            )

        result = await self._inner.execute(call)
        details, payload = self._approval_details(result)
        if details is None or payload is None:
            return result

        approval_id = str(details.get("approval_id") or "")
        summary = str(details.get("summary") or payload.get("message") or call.name)
        call_arguments = details.get("call_arguments")
        if not isinstance(call_arguments, dict):
            call_arguments = dict(call.arguments)
        if not approval_id:
            return result

        if self._policy == "ask":
            answer = self._prompt(f"Approve: {summary} [y/N] ").strip().lower()
            if answer not in {"y", "yes"}:
                return _error("APPROVAL_DENIED", f"user declined approval {approval_id}")
        elif self._policy == "auto":
            self._output(f"[auto-approved] {summary} ({approval_id})")

        try:
            await self._approvals.grant(approval_id)
        except AIntegratorError as exc:
            return _error(exc.code, exc.message)

        retry = ToolCall(
            id=f"{call.id}-approved",
            name=call.name,
            arguments={**call_arguments, "approval_id": approval_id},
        )
        return await self._inner.execute(retry)

    @staticmethod
    def _approval_details(result: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        try:
            payload = json.loads(result)
        except (TypeError, ValueError):
            return None, None
        if not isinstance(payload, dict):
            return None, None
        error = payload.get("error")
        if not isinstance(error, dict) or error.get("code") != "APPROVAL_REQUIRED":
            return None, None
        details = error.get("details")
        return (details if isinstance(details, dict) else None), payload
