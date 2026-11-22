"""Security controls: approval summaries and least-privilege MCP grants.

Revision ID: 0002_security_controls
Revises: 0001_initial
Create Date: 2026-11-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_security_controls"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

_ROLE = "aintegrator_mcp"
_WRITABLE_TABLES = (
    "orders",
    "order_events",
    "idempotency_keys",
    "approvals",
    "provider_calls",
    "eligibility_cache",
    "eligibility_requests",
)
# Audit rows must be append-only for the tool executor: INSERT but never UPDATE.
_INSERT_ONLY_TABLES = ("audit_log",)


def _grant_sql() -> str:
    tables = ", ".join(_WRITABLE_TABLES)
    audit_tables = ", ".join(_INSERT_ONLY_TABLES)
    # The role is also created by infra/postgres/init/01-mcp-role.sql on a fresh
    # volume; creating it here too keeps upgrades of existing volumes working.
    return f"""
    DO $$
    BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_ROLE}') THEN
        CREATE ROLE {_ROLE} LOGIN PASSWORD 'aintegrator-mcp-secret';
      END IF;
      GRANT USAGE ON SCHEMA public TO {_ROLE};
      GRANT SELECT ON ALL TABLES IN SCHEMA public TO {_ROLE};
      GRANT INSERT, UPDATE ON {tables} TO {_ROLE};
      GRANT INSERT ON {audit_tables} TO {_ROLE};
      ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT SELECT, INSERT, UPDATE ON TABLES TO {_ROLE};
    END $$;
    """


def _revoke_sql() -> str:
    tables = ", ".join(_WRITABLE_TABLES)
    audit_tables = ", ".join(_INSERT_ONLY_TABLES)
    return f"""
    DO $$
    BEGIN
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_ROLE}') THEN
        REVOKE SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public FROM {_ROLE};
        REVOKE INSERT, UPDATE ON {tables} FROM {_ROLE};
        REVOKE INSERT ON {audit_tables} FROM {_ROLE};
        REVOKE USAGE ON SCHEMA public FROM {_ROLE};
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
          REVOKE SELECT, INSERT, UPDATE ON TABLES FROM {_ROLE};
      END IF;
    END $$;
    """


def upgrade() -> None:
    op.add_column("approvals", sa.Column("summary", sa.String(500), nullable=True))
    op.execute(_grant_sql())


def downgrade() -> None:
    op.execute(_revoke_sql())
    op.drop_column("approvals", "summary")
