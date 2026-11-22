"""Server-side security controls: approvals, profiles, redaction and audit."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from aintegrator_integration.config import Settings
from aintegrator_integration.domain import ApprovalDenied, ApprovalRequired, ToolNotAllowed
from aintegrator_integration.persistence import Repository, SessionMaker, session_scope
from aintegrator_integration.security import (
    ALL_TOOLS,
    READ_ONLY_TOOLS,
    Redactor,
    ToolProfile,
    build_tool_security,
    digest,
    parse_tool_profile,
)

PARAMS = {"provider": "fibernet", "offer_id": "fibernet:A-1", "max_total_eur": 49.0}


@dataclass
class Clock:
    now: datetime

    def tick(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


def test_digest_is_stable_and_key_order_independent() -> None:
    assert digest({"a": 1, "b": 2}) == digest({"b": 2, "a": 1})
    assert digest({"a": 1}) != digest({"a": 2})


def test_redactor_removes_configured_secrets_and_token_shapes() -> None:
    redactor = Redactor(("super-secret-value",))
    text = "key=super-secret-value and sk-ant-abcdef123456 and Bearer abcdef123456"
    cleaned = redactor.text(text)
    assert "super-secret-value" not in cleaned
    assert "sk-ant-abcdef123456" not in cleaned
    assert "abcdef123456" not in cleaned
    assert cleaned.count("[REDACTED]") == 3


def test_redactor_value_scrubs_secret_looking_keys_recursively() -> None:
    redactor = Redactor()
    result = redactor.value(
        {
            "api_key": "abc",
            "nested": [{"password": "hunter2"}, {"safe": "visible"}],
        }
    )
    assert result == {
        "api_key": "[REDACTED]",
        "nested": [{"password": "[REDACTED]"}, {"safe": "visible"}],
    }


def test_parse_tool_profile_accepts_known_values_and_rejects_unknown() -> None:
    assert parse_tool_profile("read-only") is ToolProfile.READ_ONLY
    assert parse_tool_profile(" STANDARD ") is ToolProfile.STANDARD
    with pytest.raises(ValueError, match="unknown tool profile"):
        parse_tool_profile("admin")


async def test_read_only_profile_excludes_write_tools(session_maker: SessionMaker) -> None:
    settings = Settings(mcp_tool_profile="read-only")
    security = build_tool_security(session_maker, settings)
    assert security.profile is ToolProfile.READ_ONLY
    assert set(READ_ONLY_TOOLS) < set(ALL_TOOLS)
    for name in READ_ONLY_TOOLS:
        assert security.allows(name)
    for name in ALL_TOOLS - READ_ONLY_TOOLS:
        assert not security.allows(name)
        with pytest.raises(ToolNotAllowed):
            security.check_tool(name)


async def test_approval_requires_grant_then_consumes_once(session_maker: SessionMaker) -> None:
    clock = Clock(datetime.now(UTC))
    security = build_tool_security(session_maker, now_fn=lambda: clock.now)

    with pytest.raises(ApprovalRequired) as pending:
        await security.approvals.require(
            "create_order", PARAMS, summary="Create order", call_arguments=PARAMS
        )
    approval_id = pending.value.approval_id
    assert pending.value.expires_at > clock.now
    assert pending.value.call_arguments == PARAMS

    # Not granted yet.
    with pytest.raises(ApprovalDenied, match="not been granted"):
        await security.approvals.consume(approval_id, "create_order", PARAMS)

    await security.approvals.grant(approval_id)
    await security.approvals.consume(approval_id, "create_order", PARAMS)

    # Single-use: a second consume is refused.
    with pytest.raises(ApprovalDenied, match="already used"):
        await security.approvals.consume(approval_id, "create_order", PARAMS)


async def test_approval_is_parameter_bound(session_maker: SessionMaker) -> None:
    clock = Clock(datetime.now(UTC))
    security = build_tool_security(session_maker, now_fn=lambda: clock.now)

    with pytest.raises(ApprovalRequired) as pending:
        await security.approvals.require("create_order", PARAMS, summary="Create order")
    approval_id = pending.value.approval_id
    await security.approvals.grant(approval_id)

    with pytest.raises(ApprovalDenied, match="does not match"):
        await security.approvals.consume(
            approval_id, "create_order", {**PARAMS, "max_total_eur": 999.0}
        )
    with pytest.raises(ApprovalDenied, match="does not match"):
        await security.approvals.consume(approval_id, "cancel_order", PARAMS)

    # The original parameters still work because the token was not consumed.
    await security.approvals.consume(approval_id, "create_order", PARAMS)


async def test_approval_expires_and_cannot_be_granted_late(
    session_maker: SessionMaker,
) -> None:
    clock = Clock(datetime.now(UTC))
    settings = Settings(approval_ttl_s=60)
    security = build_tool_security(session_maker, settings, now_fn=lambda: clock.now)

    with pytest.raises(ApprovalRequired) as pending:
        await security.approvals.require("cancel_order", {"order_id": "x"}, summary="Cancel")
    approval_id = pending.value.approval_id

    clock.tick(61)
    with pytest.raises(ApprovalDenied, match="expired"):
        await security.approvals.grant(approval_id)


async def test_approval_grant_rejects_unknown_and_duplicate(
    session_maker: SessionMaker,
) -> None:
    security = build_tool_security(session_maker)
    with pytest.raises(ApprovalDenied, match="invalid approval ID"):
        await security.approvals.grant("not-a-uuid")

    with pytest.raises(ApprovalRequired) as pending:
        await security.approvals.require("create_order", PARAMS, summary="Create")
    approval_id = pending.value.approval_id
    await security.approvals.grant(approval_id)
    with pytest.raises(ApprovalDenied, match="already granted"):
        await security.approvals.grant(approval_id)


async def test_pending_lists_only_pending_requests(session_maker: SessionMaker) -> None:
    security = build_tool_security(session_maker)
    with pytest.raises(ApprovalRequired) as first:
        await security.approvals.require("create_order", PARAMS, summary="First")
    with pytest.raises(ApprovalRequired):
        await security.approvals.require("cancel_order", {"order_id": "x"}, summary="Second")

    pending = await security.approvals.pending()
    assert len(pending) == 2
    await security.approvals.grant(first.value.approval_id)
    assert len(await security.approvals.pending()) == 1


async def test_audit_log_records_redacted_tool_calls(session_maker: SessionMaker) -> None:
    security = build_tool_security(session_maker, redactor=Redactor(("sk-ant-super-secret",)))
    result = '{"ok":true,"order":{"id":"1","note":"sk-ant-super-secret"}}'
    await security.audit.record(
        run_id="run-1",
        tool="create_order",
        arguments={"api_key": "sk-ant-super-secret", "provider": "fibernet"},
        result=result,
        approval_id="approval-1",
        duration_ms=1.5,
    )

    async with session_scope(session_maker) as session:
        rows = await Repository(session).list_audit("run-1")
    assert len(rows) == 1
    row = rows[0]
    assert row.tool == "create_order"
    assert row.approval_id == "approval-1"
    payload = row.result_json
    assert payload["ok"] is True
    assert "sk-ant-super-secret" not in str(payload)
    assert "sk-ant-super-secret" not in row.args_hash


async def test_audit_can_be_disabled(session_maker: SessionMaker) -> None:
    security = build_tool_security(session_maker, Settings(audit_enabled=False))
    await security.audit.record(
        run_id=None,
        tool="get_order",
        arguments={},
        result='{"ok":true}',
        approval_id=None,
        duration_ms=0,
    )
    async with session_scope(session_maker) as session:
        assert await Repository(session).list_audit() == []
