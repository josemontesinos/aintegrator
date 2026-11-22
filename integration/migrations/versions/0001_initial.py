"""Initial schema: orders, events, idempotency, audit, cache and webhooks.

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("provider_order_id", sa.String(120), nullable=True),
        sa.Column("client_reference", sa.String(120), nullable=False, unique=True),
        sa.Column("idempotency_key", sa.String(160), nullable=True),
        sa.Column("offer_json", sa.JSON(), nullable=False),
        sa.Column("address_json", sa.JSON(), nullable=False),
        sa.Column("customer_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("supports_cancel", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_orders_client_reference", "orders", ["client_reference"])
    op.create_index("ix_orders_status", "orders", ["status"])

    op.create_table(
        "order_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("order_id", sa.Uuid(), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(80), nullable=False),
        sa.Column("raw_ref", sa.String(120), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
    )
    op.create_index("ix_order_events_order_id", "order_events", ["order_id"])

    op.create_table(
        "idempotency_keys",
        sa.Column("operation", sa.String(60), primary_key=True),
        sa.Column("key", sa.String(160), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "provider_calls",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("operation", sa.String(80), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("outcome", sa.String(60), nullable=False),
        sa.Column("run_id", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_provider_calls_provider", "provider_calls", ["provider"])

    op.create_table(
        "eligibility_cache",
        sa.Column("provider", sa.String(40), primary_key=True),
        sa.Column("cache_key", sa.String(200), primary_key=True),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_eligibility_cache_expires_at", "eligibility_cache", ["expires_at"])

    op.create_table(
        "eligibility_requests",
        sa.Column("request_id", sa.String(120), primary_key=True),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("address_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "approvals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("action", sa.String(60), nullable=False),
        sa.Column("params_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.String(120), nullable=False),
        sa.Column("tool", sa.String(80), nullable=False),
        sa.Column("args_hash", sa.String(64), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("approval_id", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_log_run_id", "audit_log", ["run_id"])

    op.create_table(
        "webhook_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("event_id", sa.String(160), nullable=False),
        sa.Column("order_reference", sa.String(120), nullable=True),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("raw_json", sa.JSON(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider", "event_id", name="uq_webhook_provider_event"),
    )
    op.create_index("ix_webhook_order", "webhook_events", ["provider", "order_reference"])


def downgrade() -> None:
    op.drop_table("webhook_events")
    op.drop_table("audit_log")
    op.drop_table("approvals")
    op.drop_table("eligibility_requests")
    op.drop_table("eligibility_cache")
    op.drop_table("provider_calls")
    op.drop_table("idempotency_keys")
    op.drop_table("order_events")
    op.drop_table("orders")
