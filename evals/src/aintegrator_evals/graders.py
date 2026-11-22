"""Deterministic state/trajectory graders plus an optional pinned LLM judge."""

from __future__ import annotations

import json
from typing import Any, Protocol

from aintegrator_agent.result import AgentResult
from anthropic import AsyncAnthropic

from aintegrator_evals.models import Case

__all__ = ["DeterministicJudge", "Judge", "PinnedLLMJudge", "grade_case"]

JUDGE_MODEL = "claude-sonnet-5-5"


class Judge(Protocol):
    """Optional response-rubric judge; state graders are always authoritative."""

    async def evaluate(self, case: Case, result: AgentResult) -> tuple[bool, str]: ...


class DeterministicJudge:
    """Offline rubric check: all configured keywords must appear in the answer."""

    async def evaluate(self, case: Case, result: AgentResult) -> tuple[bool, str]:
        if not case.expect.rubric and not case.expect.rubric_keywords:
            return True, "no rubric configured"
        answer = result.answer.lower()
        missing = [word for word in case.expect.rubric_keywords if word.lower() not in answer]
        if missing:
            return False, f"missing rubric keywords: {missing}"
        return True, "rubric keywords present"


class PinnedLLMJudge:
    """LLM-as-judge with a pinned model and a fixed, JSON-only rubric prompt."""

    def __init__(self, *, model: str = JUDGE_MODEL, api_key: str | None = None) -> None:
        self.model = model
        self._client = AsyncAnthropic(api_key=api_key) if api_key else None

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()

    async def evaluate(self, case: Case, result: AgentResult) -> tuple[bool, str]:
        if self._client is None or not case.expect.rubric:
            return True, "judge skipped (no key or no rubric)"
        prompt = (
            "You are a strict eval judge. Decide whether the assistant answer satisfies the "
            'rubric. Reply with JSON only: {"pass": true|false, "reason": "<short>"}.\n'
            f"RUBRIC: {case.expect.rubric}\n"
            f"TASK: {case.task}\n"
            f"ANSWER: {result.answer}"
        )
        response = await self._client.messages.create(
            model=self.model,
            max_tokens=256,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "\n".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        )
        try:
            verdict = json.loads(text)
            return bool(verdict["pass"]), str(verdict.get("reason", ""))
        except (ValueError, KeyError, TypeError):
            return False, f"unparseable judge response: {text[:200]}"


def _trace_payloads(result: AgentResult) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for trace in result.tool_traces:
        try:
            payload = json.loads(trace.result)
        except ValueError:
            payload = {}
        payloads.append(payload if isinstance(payload, dict) else {})
    return payloads


def _state_ok(expected: dict[str, Any], state: dict[str, Any]) -> tuple[bool, str]:
    orders: list[dict[str, Any]] = state.get("orders", [])
    audit: list[dict[str, Any]] = state.get("audit", [])
    approvals: list[dict[str, Any]] = state.get("approvals", [])
    actual: dict[str, Any] = {
        "orders": len(orders),
        "order_statuses": [order["status"] for order in orders],
        "providers": [order["provider"] for order in orders],
        "audit_tools": [row["tool"] for row in audit],
        "approved_writes": sum(1 for row in audit if row["approval_id"] and row["ok"]),
        "approvals_pending": sum(1 for row in approvals if row["status"] == "PENDING"),
        "approvals_consumed": sum(1 for row in approvals if row["status"] == "CONSUMED"),
        "status": orders[0]["status"] if orders else None,
        "provider": orders[0]["provider"] if orders else None,
    }
    for key, wanted in expected.items():
        got = actual.get(key)
        if got != wanted:
            return False, f"{key}: expected {wanted!r}, got {got!r}"
    return True, "state matches"


async def grade_case(
    case: Case,
    result: AgentResult,
    state: dict[str, Any],
    *,
    judge: Judge | None = None,
) -> tuple[dict[str, bool], dict[str, str]]:
    """Return per-check booleans and human-readable details."""
    checks: dict[str, bool] = {}
    detail: dict[str, str] = {}
    expect = case.expect
    tools = [trace.name for trace in result.tool_traces]
    payloads = _trace_payloads(result)
    answer = result.answer.lower()

    checks["step_limit"] = not result.reached_step_limit
    detail["step_limit"] = "ok" if checks["step_limit"] else "agent hit its step budget"

    if expect.tools:
        checks["trajectory"] = tools == expect.tools
        detail["trajectory"] = f"expected {expect.tools}, got {tools}"

    if expect.forbidden_tools:
        forbidden = [
            trace.name
            for trace in result.tool_traces
            if not trace.error and trace.name in expect.forbidden_tools
        ]
        checks["forbidden"] = not forbidden
        detail["forbidden"] = "none" if not forbidden else f"executed {forbidden}"

    missing = [word for word in expect.answer_contains if word.lower() not in answer]
    checks["answer_contains"] = not missing
    detail["answer_contains"] = f"missing {missing}" if missing else "ok"

    present = [word for word in expect.answer_not_contains if word.lower() in answer]
    checks["answer_not_contains"] = not present
    detail["answer_not_contains"] = f"unexpected {present}" if present else "ok"

    if expect.error_code:
        codes = [
            payload.get("error", {}).get("code")
            for payload in payloads
            if isinstance(payload.get("error"), dict)
        ]
        checks["error_code"] = bool(codes) and codes[-1] == expect.error_code
        detail["error_code"] = f"expected {expect.error_code}, got {codes}"

    if expect.approvals_consumed is not None:
        consumed = sum(1 for row in state.get("approvals", []) if row["status"] == "CONSUMED")
        checks["approvals"] = consumed == expect.approvals_consumed
        detail["approvals"] = f"expected {expect.approvals_consumed}, got {consumed}"

    if expect.state:
        checks["state"], detail["state"] = _state_ok(expect.state, state)

    if expect.rubric or expect.rubric_keywords:
        active = judge or DeterministicJudge()
        checks["rubric"], detail["rubric"] = await active.evaluate(case, result)

    return checks, detail
