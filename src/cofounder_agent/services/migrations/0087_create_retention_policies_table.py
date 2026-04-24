"""Migration 0087: retention_policies table.

Phase 2 of the Declarative Data Plane RFC; implements GH-110.

Every append-only data source that needs lifecycle management
declares itself as a row in this table. The retention runner
(services/integrations/retention_runner.py) walks enabled rows daily
and dispatches to the handler named in ``handler_name``.

Seed rows land in migration 0088 (all disabled — policy-as-data means
every activation is a deliberate flip).
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


SQL_UP = """
CREATE TABLE IF NOT EXISTS retention_policies (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Stable slug, e.g. "embeddings.claude_sessions", "gpu_metrics",
    -- "audit_log". Unique so the retention runner can dedup and so
    -- the CLI/Grafana can address rows by name.
    name                 text NOT NULL UNIQUE,
    -- Registered handler under the "retention" surface. Current set:
    -- ttl_prune, downsample, temporal_summarize (deferred).
    handler_name         text NOT NULL,
    -- Target PostgreSQL table to prune/downsample/summarize from.
    table_name           text NOT NULL,
    -- Optional WHERE fragment for per-source filtering within a shared
    -- table — e.g. for the embeddings table we set filter_sql to
    -- "source_table = 'claude_sessions'" so claude_sessions embeddings
    -- can retain differently than audit embeddings.
    filter_sql           text,
    -- Which column to compare against TTL. Almost always 'created_at'
    -- but explicit so the row declares its assumption.
    age_column           text NOT NULL DEFAULT 'created_at',
    -- ttl_prune handler parameter: delete rows older than N days.
    -- NULL when the row uses downsample/summarize instead.
    ttl_days             int,
    -- downsample handler parameter. Expected shape:
    --   {"keep_raw_days": 30, "rollup_table": "gpu_metrics_hourly",
    --    "rollup_interval": "1 hour",
    --    "aggregations": [{"col": "utilization", "fn": "avg"}, ...]}
    downsample_rule      jsonb,
    -- temporal_summarize handler parameter. Names another registered
    -- handler (e.g. "claude_sessions_temporal") that takes a batch of
    -- rows and replaces them with an LLM-generated summary embedding.
    -- Deferred — scaffolding only in this migration.
    summarize_handler    text,
    -- Every row ships disabled. Activation is always a deliberate flip.
    enabled              boolean NOT NULL DEFAULT false,
    -- Free-form handler-specific options (batch size, dry-run, etc.).
    config               jsonb NOT NULL DEFAULT '{}'::jsonb,
    metadata             jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- Observability counters updated by the runner after every execution.
    last_run_at          timestamptz,
    last_run_duration_ms int,
    last_run_deleted     bigint,
    last_run_summarized  bigint,
    last_error           text,
    total_runs           bigint NOT NULL DEFAULT 0,
    total_deleted        bigint NOT NULL DEFAULT 0,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    -- At least one parameter must be set — a row with ttl_days NULL,
    -- downsample_rule NULL, AND summarize_handler NULL has nothing to
    -- do. Fail at insert time rather than silently no-op at runtime.
    CONSTRAINT retention_policies_parameter_required_chk CHECK (
        ttl_days IS NOT NULL
        OR downsample_rule IS NOT NULL
        OR summarize_handler IS NOT NULL
    )
);

CREATE INDEX IF NOT EXISTS idx_retention_policies_enabled
    ON retention_policies (enabled);

CREATE INDEX IF NOT EXISTS idx_retention_policies_name
    ON retention_policies (name);

-- Touch updated_at on any row mutation (same pattern as webhook_endpoints).
CREATE OR REPLACE FUNCTION retention_policies_touch_updated_at()
RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS retention_policies_touch_updated_at_trg ON retention_policies;
CREATE TRIGGER retention_policies_touch_updated_at_trg
    BEFORE UPDATE ON retention_policies
    FOR EACH ROW EXECUTE FUNCTION retention_policies_touch_updated_at();
"""


SQL_DOWN = """
DROP TRIGGER IF EXISTS retention_policies_touch_updated_at_trg ON retention_policies;
DROP FUNCTION IF EXISTS retention_policies_touch_updated_at();
DROP TABLE IF EXISTS retention_policies;
"""


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SQL_UP)
        logger.info("0087: Created retention_policies table + indexes + trigger")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SQL_DOWN)
        logger.info("0087: Dropped retention_policies table")
