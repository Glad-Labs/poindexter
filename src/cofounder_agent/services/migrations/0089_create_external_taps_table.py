"""Migration 0089: external_taps table.

Phase C of the Declarative Data Plane RFC; implements GH-103.

Every external data source that pulls content into Poindexter declares
itself as a row here. The tap runner
(``services/integrations/tap_runner.py``) walks enabled rows on each
scheduled tick and dispatches to the handler named in ``handler_name``.

### Handler types in scope for v1

- ``builtin_topic_source`` — adapts an existing in-repo scraper plugin
  (hackernews, devto, web_search, codebase, knowledge) into the
  declarative model so operators can enable/disable per source via a
  single row update instead of chasing ``plugin.topic_source.*``
  app_settings keys.
- ``singer_subprocess`` — runs any Singer-spec tap binary as a
  subprocess, routes SCHEMA/RECORD/STATE messages to the target
  table, persists ``state`` back on success. Scaffolding only in
  v1 (NotImplementedError); fills in once an operator wants to wire
  a concrete Singer tap.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


SQL_UP = """
CREATE TABLE IF NOT EXISTS external_taps (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Stable slug, e.g. "hackernews", "devto", "stripe_charges".
    name                 text NOT NULL UNIQUE,
    -- Registered handler under the "tap" surface. v1 set:
    -- builtin_topic_source, singer_subprocess (stub).
    handler_name         text NOT NULL,
    -- Free-form label describing what the tap connects to. For
    -- builtin_topic_source rows this is the name of the in-repo
    -- topic_source plugin (e.g. "hackernews"). For singer_subprocess
    -- rows this is typically the Singer package ID (e.g.
    -- "singer-io/tap-stripe").
    tap_type             text NOT NULL,
    -- Target PostgreSQL table the records land in. For
    -- builtin_topic_source this is usually "content_tasks"
    -- (topic candidates become draft tasks). For singer_subprocess
    -- the operator points at whichever table the handler should
    -- write to via a pluggable record_handler.
    target_table         text,
    -- Optional registered handler name that transforms/filters each
    -- incoming record before INSERT. Shared with the webhook framework
    -- handler registry — a revenue_event_writer handler could consume
    -- records from both a webhook and a Singer tap.
    record_handler       text,
    -- Cron-ish schedule for when to fire this tap. Interpreted by the
    -- tap_runner's scheduler wrapper. Ex: "every 1 hour", "0 * * * *".
    schedule             text,
    -- Per-tap configuration — API keys as *references to secrets*
    -- (never raw), account IDs, date ranges, pagination markers.
    -- Encrypted API keys live in app_settings under keys referenced
    -- here via ``config.credentials_ref``.
    config               jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- Singer-style incremental sync state. Persisted by the runner
    -- at the end of each successful run; read at the start of the
    -- next run so the tap resumes from the last bookmark.
    state                jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- Every row ships disabled. Activation is always deliberate.
    enabled              boolean NOT NULL DEFAULT false,
    metadata             jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- Observability counters updated by the runner after every run.
    last_run_at          timestamptz,
    last_run_duration_ms int,
    last_run_status      text,           -- 'success' | 'failed'
    last_run_records     bigint,
    last_error           text,
    total_runs           bigint NOT NULL DEFAULT 0,
    total_records        bigint NOT NULL DEFAULT 0,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_external_taps_enabled
    ON external_taps (enabled);

CREATE INDEX IF NOT EXISTS idx_external_taps_name
    ON external_taps (name);

CREATE OR REPLACE FUNCTION external_taps_touch_updated_at()
RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS external_taps_touch_updated_at_trg ON external_taps;
CREATE TRIGGER external_taps_touch_updated_at_trg
    BEFORE UPDATE ON external_taps
    FOR EACH ROW EXECUTE FUNCTION external_taps_touch_updated_at();
"""


SQL_DOWN = """
DROP TRIGGER IF EXISTS external_taps_touch_updated_at_trg ON external_taps;
DROP FUNCTION IF EXISTS external_taps_touch_updated_at();
DROP TABLE IF EXISTS external_taps;
"""


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SQL_UP)
        logger.info("0089: Created external_taps table + indexes + trigger")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SQL_DOWN)
        logger.info("0089: Dropped external_taps table")
