"""Run one eval repeat against an in-memory service with the real security policy."""

from __future__ import annotations

import time
from typing import Any

from aintegrator_agent.approval import ApprovalPolicy, ApprovalPolicyExecutor
from aintegrator_agent.loop import AgentLoop
from aintegrator_agent.tools import InProcessTools
from aintegrator_integration.config import Settings
from aintegrator_integration.persistence import (
    Repository,
    SessionMaker,
    init_db,
    make_engine,
    make_session_maker,
    session_scope,
)
from aintegrator_integration.security import build_tool_security
from aintegrator_integration.service import ConnectivityService

from aintegrator_evals.cassette import CassetteLLM
from aintegrator_evals.gateway import build_gateways
from aintegrator_evals.graders import grade_case
from aintegrator_evals.models import Case, CaseResult

__all__ = ["build_eval_settings", "capture_state", "run_repeat"]


def build_eval_settings(case: Case) -> Settings:
    """Build isolated settings: no .env, no inherited environment overrides."""
    settings = dict(case.setup.settings)
    settings.setdefault("mcp_tool_profile", case.setup.profile)
    return Settings(database_url="sqlite+aiosqlite:///:memory:", _env_file=None, **settings)


async def capture_state(session_maker: SessionMaker) -> dict[str, Any]:
    """Snapshot the parts of the database that graders inspect."""
    async with session_scope(session_maker) as session:
        repository = Repository(session)
        orders = await repository.list_orders(limit=100)
        audit = await repository.list_audit(limit=100)
        approvals = await repository.list_approvals(limit=100)
    return {
        "orders": [
            {
                "order_id": str(order.order_id),
                "provider": order.provider,
                "status": order.status.value,
                "client_reference": order.client_reference,
                "idempotency_key": order.idempotency_key or "",
                "cancellable": order.cancellable,
            }
            for order in orders
        ],
        "audit": [
            {
                "tool": row.tool,
                "approval_id": row.approval_id or "",
                "ok": bool(row.result_json.get("ok")),
                "error_code": row.result_json.get("error_code") or "",
            }
            for row in audit
        ],
        "approvals": [{"action": row.action, "status": row.status} for row in approvals],
    }


async def run_repeat(
    case: Case,
    *,
    address: dict[str, Any],
    customer: dict[str, Any],
    repeat: int,
    judge: Any = None,
) -> CaseResult:
    """Execute a case once, from a fresh database and a fresh cassette."""
    started = time.perf_counter()
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    session_maker: SessionMaker = make_session_maker(engine)
    settings = build_eval_settings(case)
    gateways = build_gateways(case.setup.scenario)
    service = ConnectivityService(gateways, session_maker, settings=settings, engine=engine)
    security = build_tool_security(session_maker, settings)
    approve: ApprovalPolicy = case.approve if case.approve in {"never", "ask", "auto"} else "ask"
    tools = ApprovalPolicyExecutor(
        InProcessTools(service, run_id=case.id, security=security),
        security.approvals,
        policy=approve,
        prompt=lambda _prompt: "y",
        output=lambda _message: None,
    )
    llm = CassetteLLM(
        case.cassette,
        case_id=f"{case.id}-{repeat}",
        context={"address": address, "customer": customer},
    )
    loop = AgentLoop(
        llm,
        tools,
        max_steps=case.setup.max_steps,
        redactor=security.redactor,
    )
    try:
        result = await loop.run(case.task)
        state = await capture_state(session_maker)
        checks, detail = await grade_case(case, result, state, judge=judge)
        return CaseResult(
            case_id=case.id,
            repeat=repeat,
            passed=all(checks.values()) if checks else True,
            checks=checks,
            checks_detail=detail,
            agent=result,
            state=state,
            duration_ms=(time.perf_counter() - started) * 1000,
        )
    except Exception as exc:  # keep a failing case from aborting the suite
        return CaseResult(
            case_id=case.id,
            repeat=repeat,
            passed=False,
            checks={"execution": False},
            checks_detail={"execution": f"{type(exc).__name__}: {exc}"},
            duration_ms=(time.perf_counter() - started) * 1000,
            error=f"{type(exc).__name__}: {exc}",
        )
    finally:
        await service.aclose()
