"""SQLAlchemy tables for orders, idempotency, audit, cache and webhooks."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from aintegrator_integration.persistence.database import Base

__all__ = [
    "ApprovalRow",
    "AuditLogRow",
    "EligibilityCacheRow",
    "EligibilityRequestRow",
    "IdempotencyKeyRow",
    "OrderEventRow",
    "OrderRow",
    "ProviderCallRow",
    "WebhookEventRow",
]


class OrderRow(Base):
    __tablename__ = "orders"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(40))
    provider_order_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    client_reference: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    offer_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    address_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    customer_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(40), index=True)
    supports_cancel: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OrderEventRow(Base):
    __tablename__ = "order_events"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(40))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(80))
    raw_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class IdempotencyKeyRow(Base):
    __tablename__ = "idempotency_keys"

    operation: Mapped[str] = mapped_column(String(60), primary_key=True)
    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProviderCallRow(Base):
    __tablename__ = "provider_calls"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    operation: Mapped[str] = mapped_column(String(80))
    summary: Mapped[str] = mapped_column(Text)
    latency_ms: Mapped[float] = mapped_column()
    outcome: Mapped[str] = mapped_column(String(60))
    run_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EligibilityCacheRow(Base):
    __tablename__ = "eligibility_cache"

    provider: Mapped[str] = mapped_column(String(40), primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class EligibilityRequestRow(Base):
    __tablename__ = "eligibility_requests"

    request_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    provider: Mapped[str] = mapped_column(String(40))
    address_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(40))
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApprovalRow(Base):
    __tablename__ = "approvals"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    action: Mapped[str] = mapped_column(String(60))
    params_hash: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    granted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLogRow(Base):
    __tablename__ = "audit_log"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(String(120), index=True)
    tool: Mapped[str] = mapped_column(String(80))
    args_hash: Mapped[str] = mapped_column(String(64))
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    approval_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WebhookEventRow(Base):
    __tablename__ = "webhook_events"
    __table_args__ = (
        UniqueConstraint("provider", "event_id", name="uq_webhook_provider_event"),
        Index("ix_webhook_order", "provider", "order_reference"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(40))
    event_id: Mapped[str] = mapped_column(String(160))
    order_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    event_type: Mapped[str] = mapped_column(String(80))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    raw_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
