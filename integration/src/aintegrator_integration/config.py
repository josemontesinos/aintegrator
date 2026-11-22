"""Environment-driven configuration for the integration layer."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["Settings", "get_settings"]


class Settings(BaseSettings):
    """All settings can be overridden by environment variables (see .env.example)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://aintegrator:aintegrator@localhost:5432/aintegrator"
    # The MCP server uses a DB role that can read state and append audit rows,
    # but cannot delete orders, rewrite audit rows or run DDL. See ADR 008.
    mcp_database_url: str = (
        "postgresql+asyncpg://aintegrator_mcp:aintegrator-mcp-secret@localhost:5432/aintegrator"
    )

    fibernet_base_url: str = "http://localhost:8001"
    legacytel_base_url: str = "http://localhost:8002"
    skylink_base_url: str = "http://localhost:8003"
    fibernet_client_id: str = "fibernet-client"
    fibernet_client_secret: str = "fibernet-secret"
    legacytel_username: str = "legacytel"
    legacytel_password: str = "legacytel-secret"
    skylink_api_key: str = "skylink-key"
    skylink_webhook_secret: str = "skylink-secret"
    webhook_receiver_base_url: str = "http://localhost:8000"

    provider_timeout_s: float = 2.0
    provider_connect_timeout_s: float = 1.0
    provider_max_attempts: int = 3
    provider_retry_base_s: float = 0.05
    provider_retry_max_s: float = 0.5
    provider_breaker_threshold: int = 5
    provider_breaker_reset_s: float = 5.0
    provider_concurrency_limit: int = 10

    eligibility_async_wait_s: float = 8.0
    reconcile_after_s: int = 300
    max_order_value_eur: int = 2000
    max_period_value_eur: int = 20000
    write_rate_limit_per_minute: int = 30
    webhook_timestamp_tolerance_s: int = 300

    # Tool-boundary security controls (phase 4).
    mcp_tool_profile: str = "standard"
    approval_ttl_s: int = 300
    audit_enabled: bool = True

    alembic_config_path: Path = Field(default=Path("integration/alembic.ini"))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
