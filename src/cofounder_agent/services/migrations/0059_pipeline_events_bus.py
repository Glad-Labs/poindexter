"""Migration 0059: Create pipeline_events table + NOTIFY trigger.

The foundation for event-driven architecture. Events are inserted into
pipeline_events, PostgreSQL NOTIFY wakes the EventBus listener instantly.
"""
import logging

logger = logging.getLogger(__name__)

UP = """
CREATE TABLE IF NOT EXISTS pipeline_events (
    id BIGSERIAL PRIMARY KEY,
    event_type VARCHAR(100) NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}',
    processed BOOLEAN DEFAULT FALSE,
    processed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pipeline_events_unprocessed
    ON pipeline_events(processed, created_at) WHERE NOT processed;
CREATE INDEX IF NOT EXISTS idx_pipeline_events_type
    ON pipeline_events(event_type);

CREATE OR REPLACE FUNCTION notify_pipeline_event() RETURNS TRIGGER AS $$
BEGIN
    PERFORM pg_notify('pipeline_events', NEW.id::TEXT);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_pipeline_event_notify ON pipeline_events;
CREATE TRIGGER trg_pipeline_event_notify
    AFTER INSERT ON pipeline_events
    FOR EACH ROW EXECUTE FUNCTION notify_pipeline_event();
"""

DOWN = """
DROP TRIGGER IF EXISTS trg_pipeline_event_notify ON pipeline_events;
DROP FUNCTION IF EXISTS notify_pipeline_event();
DROP TABLE IF EXISTS pipeline_events;
"""


async def up(pool):
    async with pool.acquire() as conn:
        await conn.execute(UP)
    logger.info("Created pipeline_events table with NOTIFY trigger")


async def down(pool):
    async with pool.acquire() as conn:
        await conn.execute(DOWN)
    logger.info("Dropped pipeline_events table and trigger")
