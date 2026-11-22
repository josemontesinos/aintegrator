"""Server-side controls for LLM-facing tools.

These controls are deliberately in ``integration``: both the MCP server and the
in-process fallback must enforce the same policy, and the policy must not rely
on the model following instructions. Approvals, tool profiles, redaction and the
append-only audit log live here.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from aintegrator_integration.config import Settings, get_settings
from aintegrator_integration.domain import ApprovalDenied, ApprovalRequired, ToolNotAllowed
from aintegrator_integration.persistence import Repository, SessionMaker, session_scope

__all__ = [
    "ALL_TOOLS",
    "READ_ONLY_TOOLS",
    "WRITE_TOOLS",
    "ApprovalRequest",
    "ApprovalService",
    "AuditService",
    "Redactor",
    "ToolProfile",
    "ToolSecurity",
    "build_tool_security",
    "digest",
    "parse_tool_profile",
]

READ_TOOLS = frozenset({"check_eligibility", "get_order", "list_orders"})
WRITE_TOOLS = frozenset({"create_order", "cancel_order"})
ALL_TOOLS = READ_TOOLS | WRITE_TOOLS
READ_ONLY_TOOLS = READ_TOOLS


class ToolProfile(StrEnum):
    READ_ONLY = "read-only"
    STANDARD = "standard"


ALLOWED_TOOLS: dict[ToolProfile, frozenset[str]] = {
    ToolProfile.READ_ONLY: READ_ONLY_TOOLS,
    ToolProfile.STANDARD: ALL_TOOLS,
}


def parse_tool_profile(value: str) -> ToolProfile:
    try:
        return ToolProfile(value.strip().lower())
    except ValueError as exc:
        choices = ", ".join(profile.value for profile in ToolProfile)
        raise ValueError(f"unknown tool profile {value!r}; expected one of: {choices}") from exc


def digest(value: Any) -> str:
    """Stable hash for approval parameter binding and audit argument references."""
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


_SECRET_KEY = re.compile(
    r"(?i)(api[_-]?key|secret|password|passwd|token|authorization|bearer|credential)"
)
_TOKEN_PATTERNS = (
    re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._-]{8,}"),
)


class Redactor:
    """Remove configured secrets and credential-shaped strings from tool output."""

    def __init__(self, secrets: Sequence[str] = ()) -> None:
        self._secrets = tuple(
            sorted(
                (secret for secret in secrets if secret and len(secret) >= 4),
                key=len,
                reverse=True,
            )
        )

    def text(self, value: str | None) -> str:
        result = "" if value is None else str(value)
        for secret in self._secrets:
            result = result.replace(secret, "[REDACTED]")
        for pattern in _TOKEN_PATTERNS:
            result = pattern.sub("[REDACTED]", result)
        return result

    def value(self, value: Any) -> Any:
        """Recursively redact secret-looking keys in dicts passed to the audit log."""
        if isinstance(value, Mapping):
            return {
                str(key): "[REDACTED]" if _SECRET_KEY.search(str(key)) else self.value(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self.value(item) for item in value]
        if isinstance(value, str):
            return self.text(value)
        return value


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    id: UUID
    action: str
    params_hash: str
    summary: str
    expires_at: datetime
    status: str = "PENDING"
    call_arguments: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "approval_id": str(self.id),
            "action": self.action,
            "summary": self.summary,
            "expires_at": self.expires_at.isoformat(),
            "status": self.status,
        }


class ApprovalService:
    """DB-backed, single-use, parameter-bound and expiring write approvals."""

    def __init__(
        self,
        session_maker: SessionMaker,
        settings: Settings | None = None,
        *,
        now_fn: Callable[[], datetime] | None = None,
        redactor: Redactor | None = None,
    ) -> None:
        self._session_maker = session_maker
        self._settings = settings or get_settings()
        self._now = now_fn or (lambda: datetime.now(UTC))
        self._redactor = redactor or Redactor()

    @staticmethod
    def _parse_id(approval_id: str) -> UUID:
        try:
            return UUID(approval_id)
        except ValueError as exc:
            raise ApprovalDenied(f"invalid approval ID: {approval_id}") from exc

    def _to_request(self, row: Any) -> ApprovalRequest:
        return ApprovalRequest(
            id=row.id,
            action=row.action,
            params_hash=row.params_hash,
            summary=row.summary or "",
            expires_at=_aware(row.expires_at),
            status=row.status,
        )

    async def request(
        self,
        action: str,
        params: Mapping[str, Any],
        *,
        summary: str,
        call_arguments: Mapping[str, Any] | None = None,
    ) -> ApprovalRequest:
        now = self._now()
        request = ApprovalRequest(
            id=uuid4(),
            action=action,
            params_hash=digest(params),
            summary=self._redactor.text(summary)[:500],
            expires_at=now + timedelta(seconds=self._settings.approval_ttl_s),
            call_arguments=dict(call_arguments or {}),
        )
        async with session_scope(self._session_maker) as session:
            await Repository(session).create_approval(
                request.id,
                action,
                request.params_hash,
                request.summary,
                "PENDING",
                now,
                request.expires_at,
            )
        return request

    async def require(
        self,
        action: str,
        params: Mapping[str, Any],
        *,
        summary: str,
        call_arguments: Mapping[str, Any] | None = None,
        approval_id: str | None = None,
    ) -> None:
        """Raise on the first call; consume a granted token on the second."""
        if not approval_id:
            request = await self.request(
                action, params, summary=summary, call_arguments=call_arguments
            )
            raise ApprovalRequired(
                f"Approval required for {summary}. "
                f"Grant approval {request.id} with `aintegrator approvals approve {request.id}` "
                "and repeat the call with that approval_id.",
                approval_id=str(request.id),
                expires_at=request.expires_at,
                call_arguments=request.call_arguments,
                summary=request.summary,
            )
        await self.consume(approval_id, action, params)

    async def grant(self, approval_id: str) -> ApprovalRequest:
        parsed = self._parse_id(approval_id)
        now = self._now()
        async with session_scope(self._session_maker) as session:
            repository = Repository(session)
            row = await repository.get_approval(parsed)
            if row is None:
                raise ApprovalDenied(f"unknown approval: {approval_id}")
            if row.status != "PENDING":
                raise ApprovalDenied(f"approval {approval_id} is already {row.status.lower()}")
            if _aware(row.expires_at) <= now:
                raise ApprovalDenied(f"approval {approval_id} has expired")
            if not await repository.grant_approval(parsed, now):
                raise ApprovalDenied(f"approval {approval_id} is already granted")
            return self._to_request(row)

    async def consume(self, approval_id: str, action: str, params: Mapping[str, Any]) -> None:
        parsed = self._parse_id(approval_id)
        now = self._now()
        async with session_scope(self._session_maker) as session:
            repository = Repository(session)
            row = await repository.get_approval(parsed)
            if row is None:
                raise ApprovalDenied(f"unknown approval: {approval_id}")
            if row.status == "CONSUMED":
                raise ApprovalDenied(f"approval {approval_id} was already used")
            if row.status != "GRANTED":
                raise ApprovalDenied(f"approval {approval_id} has not been granted")
            if _aware(row.expires_at) <= now:
                raise ApprovalDenied(f"approval {approval_id} has expired")
            if row.action != action or row.params_hash != digest(params):
                raise ApprovalDenied(f"approval {approval_id} does not match this action")
            # Conditional UPDATE makes single-use safe under concurrent callers.
            if not await repository.consume_approval(parsed):
                raise ApprovalDenied(f"approval {approval_id} was already used")

    async def pending(self, limit: int = 50) -> list[ApprovalRequest]:
        async with session_scope(self._session_maker) as session:
            rows = await Repository(session).list_approvals("PENDING", limit)
        return [self._to_request(row) for row in rows]


@dataclass(frozen=True, slots=True)
class AuditService:
    """Append-only record of every tool call, with redacted payloads only."""

    session_maker: SessionMaker
    redactor: Redactor
    enabled: bool = True
    now_fn: Callable[[], datetime] = field(default_factory=lambda: lambda: datetime.now(UTC))

    async def record(
        self,
        *,
        run_id: str | None,
        tool: str,
        arguments: Mapping[str, Any],
        result: str,
        approval_id: str | None,
        duration_ms: float,
    ) -> None:
        if not self.enabled:
            return
        try:
            payload = json.loads(result)
        except ValueError:
            payload = {}
        error = payload.get("error") if isinstance(payload, dict) else None
        result_json: dict[str, Any] = {
            "ok": isinstance(payload, dict) and payload.get("ok") is True,
            "error_code": error.get("code") if isinstance(error, dict) else None,
            "duration_ms": round(duration_ms, 3),
            "result": self.redactor.text(result)[:2000],
        }
        async with session_scope(self.session_maker) as session:
            await Repository(session).append_audit(
                run_id or "mcp",
                tool,
                digest(self.redactor.value(arguments)),
                result_json,
                approval_id,
                self.now_fn(),
            )


@dataclass(frozen=True, slots=True)
class ToolSecurity:
    """One policy object shared by the MCP server and the local fallback."""

    approvals: ApprovalService
    audit: AuditService
    redactor: Redactor
    profile: ToolProfile

    def allows(self, tool: str) -> bool:
        return tool in ALLOWED_TOOLS[self.profile]

    def check_tool(self, tool: str) -> None:
        if not self.allows(tool):
            raise ToolNotAllowed(
                f"tool {tool!r} is not available in the {self.profile.value} profile"
            )


def build_tool_security(
    session_maker: SessionMaker,
    settings: Settings | None = None,
    *,
    redactor: Redactor | None = None,
    now_fn: Callable[[], datetime] | None = None,
) -> ToolSecurity:
    config = settings or get_settings()
    safe_redactor = redactor or Redactor(
        (
            config.fibernet_client_secret,
            config.legacytel_password,
            config.skylink_api_key,
            config.skylink_webhook_secret,
        )
    )
    return ToolSecurity(
        approvals=ApprovalService(session_maker, config, now_fn=now_fn, redactor=safe_redactor),
        audit=AuditService(
            session_maker=session_maker,
            redactor=safe_redactor,
            enabled=config.audit_enabled,
        ),
        redactor=safe_redactor,
        profile=parse_tool_profile(config.mcp_tool_profile),
    )
