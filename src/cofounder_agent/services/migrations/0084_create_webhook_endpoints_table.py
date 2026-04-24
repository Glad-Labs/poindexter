"""Migration 0084: webhook_endpoints table for the declarative
integrations framework (Phase 1).

See ``docs/architecture/declarative-data-plane-rfc-2026-04-24.md``
for the umbrella design. Every inbound and outbound webhook becomes
a row in this table:

- ``direction='inbound'`` rows are served by a catch-all
  ``POST /api/webhooks/{name}`` route (landing in the same phase).
- ``direction='outbound'`` rows are published to by the internal event
  bus; matching rows enqueue a delivery via ``webhook_events``.

Seeded rows for the three existing inbound endpoints (lemon_squeezy,
resend, alertmanager) and three existing outbound destinations
(discord_ops, telegram_ops, vercel_isr) land in migration 0085.
This migration only creates the schema — all rows disabled by default
means a worker restart on this migration alone is a no-op.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


SQL_UP = """
CREATE TABLE IF NOT EXISTS webhook_endpoints (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name                 text NOT NULL UNIQUE,
    direction            text NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    handler_name         text NOT NULL,
    -- Inbound-only: URL path, defaults to /api/webhooks/{name} when NULL
    path                 text,
    -- Outbound-only: destination URL
    url                  text,
    -- Signing config shared by both directions
    signing_algorithm    text NOT NULL DEFAULT 'none'
        CHECK (signing_algorithm IN ('none', 'hmac-sha256', 'svix', 'bearer')),
    -- app_settings key (NOT the plaintext value). Resolved via
    -- services.integrations.secret_resolver.resolve_secret().
    secret_key_ref       text,
    -- Outbound-only: filter rules describing which internal events
    -- trigger this webhook. Shape left loose for now; initial handlers
    -- consume {"events": ["post.published", ...]}.
    event_filter         jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- Every row ships disabled. Activation is always a deliberate flip.
    enabled              boolean NOT NULL DEFAULT false,
    -- Free-form handler-specific options (e.g. retry budget overrides).
    config               jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- Operator notes / tags.
    metadata             jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- Observability counters, updated by the dispatcher on every call.
    last_success_at      timestamptz,
    last_failure_at      timestamptz,
    last_error           text,
    total_success        bigint NOT NULL DEFAULT 0,
    total_failure        bigint NOT NULL DEFAULT 0,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    -- Inbound rows need a path (or derive one from name). Outbound
    -- rows need a url. Enforce at the DB so a half-configured row
    -- can't linger in 'enabled=true' state.
    CONSTRAINT webhook_endpoints_direction_config_chk CHECK (
        (direction = 'inbound'  AND url IS NULL) OR
        (direction = 'outbound' AND url IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_webhook_endpoints_direction_enabled
    ON webhook_endpoints (direction, enabled);

CREATE INDEX IF NOT EXISTS idx_webhook_endpoints_name
    ON webhook_endpoints (name);

-- Surface-by-surface trigger updates updated_at on any modification.
-- Using a trigger (vs. an app-layer convention) because the dispatcher
-- writes counter updates and we want the stamp to follow every write
-- regardless of which code path touched the row.
CREATE OR REPLACE FUNCTION webhook_endpoints_touch_updated_at()
RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS webhook_endpoints_touch_updated_at_trg ON webhook_endpoints;
CREATE TRIGGER webhook_endpoints_touch_updated_at_trg
    BEFORE UPDATE ON webhook_endpoints
    FOR EACH ROW EXECUTE FUNCTION webhook_endpoints_touch_updated_at();
"""


SQL_DOWN = """
DROP TRIGGER IF EXISTS webhook_endpoints_touch_updated_at_trg ON webhook_endpoints;
DROP FUNCTION IF EXISTS webhook_endpoints_touch_updated_at();
DROP TABLE IF EXISTS webhook_endpoints;
"""


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SQL_UP)
        logger.info("0084: Created webhook_endpoints table + indexes + trigger")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SQL_DOWN)
        logger.info("0084: Dropped webhook_endpoints table")
