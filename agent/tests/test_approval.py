"""CLI approval policy: never / ask / auto on top of the server-side gate."""

from __future__ import annotations

import json
from typing import Any, Literal, cast

import pytest
from aintegrator_agent.approval import ApprovalPolicyExecutor
from aintegrator_agent.llm import ToolCall, ToolDefinition
from aintegrator_integration.domain import ApprovalDenied
from aintegrator_integration.security import ApprovalService

WRITE_ARGS = {"provider": "fibernet", "offer_id": "fibernet:A-1"}
APPROVAL_ID = "11111111-1111-1111-1111-111111111111"


def _approval_required() -> str:
    return json.dumps(
        {
            "ok": False,
            "error": {
                "code": "APPROVAL_REQUIRED",
                "message": "Approval required",
                "retryable": False,
                "details": {
                    "approval_id": APPROVAL_ID,
                    "summary": "Create fibernet order",
                    "call_arguments": {**WRITE_ARGS, "idempotency_key": "fixed"},
                },
            },
        }
    )


class RecordingTools:
    """Fake executor that returns APPROVAL_REQUIRED until the stub grants it."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.granted = False

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return (
            ToolDefinition(name="create_order", description="write", input_schema={}),
            ToolDefinition(name="check_eligibility", description="read", input_schema={}),
        )

    async def execute(self, call: ToolCall) -> str:
        self.calls.append({"name": call.name, "arguments": dict(call.arguments)})
        if call.name == "check_eligibility":
            return json.dumps({"ok": True, "value": "read-result"})
        if not self.granted:
            return _approval_required()
        assert call.arguments.get("approval_id") == APPROVAL_ID
        return json.dumps({"ok": True, "order": {"order_id": "created"}})


class StubApprovals:
    """Minimal grant surface; grant failures can be injected per test."""

    def __init__(self, failure: Exception | None = None) -> None:
        self.granted_ids: list[str] = []
        self.failure = failure
        self.on_grant: Any = None

    async def grant(self, approval_id: str) -> None:
        if self.failure is not None:
            raise self.failure
        self.granted_ids.append(approval_id)
        if self.on_grant is not None:
            self.on_grant()


def _build(
    inner: RecordingTools,
    approvals: StubApprovals,
    policy: Literal["never", "ask", "auto"],
    *,
    answer: str = "n",
    prompts: list[str] | None = None,
) -> ApprovalPolicyExecutor:
    def prompt(message: str) -> str:
        if prompts is not None:
            prompts.append(message)
        return answer

    approvals.on_grant = lambda: setattr(inner, "granted", True)
    return ApprovalPolicyExecutor(
        inner,
        cast(ApprovalService, approvals),
        policy=policy,
        prompt=prompt,
        output=lambda _message: None,
    )


def _call(name: str) -> ToolCall:
    return ToolCall(id="1", name=name, arguments=dict(WRITE_ARGS))


async def test_never_blocks_writes_but_reads_always_pass() -> None:
    inner = RecordingTools()
    executor = _build(inner, StubApprovals(), "never")
    blocked = json.loads(await executor.execute(_call("create_order")))
    assert blocked["error"]["code"] == "TOOL_NOT_ALLOWED"
    assert inner.calls == []

    read = json.loads(
        await executor.execute(ToolCall(id="2", name="check_eligibility", arguments={}))
    )
    assert read == {"ok": True, "value": "read-result"}
    assert [c["name"] for c in inner.calls] == ["check_eligibility"]


async def test_ask_grants_after_confirmation_and_retries_with_bound_args() -> None:
    inner = RecordingTools()
    approvals = StubApprovals()
    prompts: list[str] = []
    executor = _build(inner, approvals, "ask", answer="yes", prompts=prompts)

    result = json.loads(await executor.execute(_call("create_order")))

    assert result["ok"] is True
    assert len(prompts) == 1
    assert "Create fibernet order" in prompts[0]
    assert approvals.granted_ids == [APPROVAL_ID]
    assert inner.calls[0]["arguments"] == WRITE_ARGS
    assert inner.calls[1]["arguments"]["approval_id"] == APPROVAL_ID
    assert inner.calls[1]["arguments"]["idempotency_key"] == "fixed"


@pytest.mark.parametrize("answer", ["", "n", "no", "anything"])
async def test_ask_denies_on_decline(answer: str) -> None:
    inner = RecordingTools()
    approvals = StubApprovals()
    executor = _build(inner, approvals, "ask", answer=answer)

    result = json.loads(await executor.execute(_call("create_order")))

    assert result["error"]["code"] == "APPROVAL_DENIED"
    assert len(inner.calls) == 1
    assert approvals.granted_ids == []
    inner.granted = True  # would succeed if the retry had happened
    assert len(inner.calls) == 1


async def test_auto_grants_without_prompting() -> None:
    inner = RecordingTools()
    approvals = StubApprovals()
    prompts: list[str] = []
    executor = _build(inner, approvals, "auto", prompts=prompts)

    result = json.loads(await executor.execute(_call("create_order")))

    assert result["ok"] is True
    assert prompts == []
    assert approvals.granted_ids == [APPROVAL_ID]


async def test_grant_failure_is_surfaced_as_structured_error() -> None:
    inner = RecordingTools()
    approvals = StubApprovals(failure=ApprovalDenied("approval expired"))
    executor = _build(inner, approvals, "auto")

    result = json.loads(await executor.execute(_call("create_order")))

    assert result["error"]["code"] == "APPROVAL_DENIED"
    assert "expired" in result["error"]["message"]
    assert len(inner.calls) == 1


async def test_definitions_are_forwarded_unchanged() -> None:
    inner = RecordingTools()
    executor = _build(inner, StubApprovals(), "never")
    assert [d.name for d in executor.definitions()] == ["create_order", "check_eligibility"]
