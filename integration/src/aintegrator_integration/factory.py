"""Default wiring for local runs, the webhook service and the reconciler."""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache

from aintegrator_integration.adapters import FibernetAdapter, LegacytelAdapter, SkylinkAdapter
from aintegrator_integration.config import Settings, get_settings
from aintegrator_integration.gateways import ProviderGateway
from aintegrator_integration.persistence import (
    Repository,
    SessionMaker,
    make_engine,
    make_session_maker,
    session_scope,
)
from aintegrator_integration.resilience import AuditSink, Clock, ResilientHttpClient
from aintegrator_integration.service import ConnectivityService

__all__ = ["build_gateways", "build_mcp_service", "build_service", "get_service"]


def make_audit_sink(session_maker: SessionMaker) -> AuditSink:
    """Persist one row per logical provider call for later inspection."""

    async def record(
        provider: str,
        operation: str,
        url: str,
        latency_ms: float,
        outcome: str,
        run_id: str | None,
    ) -> None:
        async with session_scope(session_maker) as session:
            await Repository(session).record_call(
                provider,
                operation,
                url,
                latency_ms,
                outcome,
                run_id,
                datetime.now(UTC),
            )

    return record


def build_gateways(
    settings: Settings,
    *,
    clock: Clock | None = None,
    session_maker: SessionMaker | None = None,
) -> dict[str, ProviderGateway]:
    audit_sink = make_audit_sink(session_maker) if session_maker is not None else None
    fibernet_http = ResilientHttpClient("fibernet", settings, clock=clock, audit_sink=audit_sink)
    skylink_http = ResilientHttpClient("skylink", settings, clock=clock, audit_sink=audit_sink)
    return {
        "fibernet": FibernetAdapter(settings, clock=clock, http=fibernet_http),
        "legacytel": LegacytelAdapter(settings, clock=clock, audit_sink=audit_sink),
        "skylink": SkylinkAdapter(settings, clock=clock, http=skylink_http),
    }


def build_service(
    settings: Settings | None = None,
    *,
    clock: Clock | None = None,
    gateways: dict[str, ProviderGateway] | None = None,
    database_url: str | None = None,
) -> ConnectivityService:
    config = settings or get_settings()
    engine = make_engine(database_url or config.database_url)
    session_maker = make_session_maker(engine)
    return ConnectivityService(
        gateways or build_gateways(config, clock=clock, session_maker=session_maker),
        session_maker,
        settings=config,
        clock=clock,
        engine=engine,
    )


def build_mcp_service(
    settings: Settings | None = None, *, clock: Clock | None = None
) -> ConnectivityService:
    """Build the MCP server's service using its least-privilege DB role."""
    config = settings or get_settings()
    return build_service(
        config, clock=clock, database_url=config.mcp_database_url or config.database_url
    )


@lru_cache(maxsize=1)
def get_service() -> ConnectivityService:
    return build_service()
