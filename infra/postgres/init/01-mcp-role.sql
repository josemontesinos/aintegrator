-- Fictional local role used by the MCP server. Privileges are granted by Alembic
-- migration 0002 once the tables exist. No real credentials are stored here.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aintegrator_mcp') THEN
    CREATE ROLE aintegrator_mcp LOGIN PASSWORD 'aintegrator-mcp-secret';
  END IF;
END
$$;
