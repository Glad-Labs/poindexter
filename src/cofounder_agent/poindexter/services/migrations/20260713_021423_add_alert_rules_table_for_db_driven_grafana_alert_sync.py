"""Migration 20260713_021423_add_alert_rules_table_for_db_driven_grafana_alert_sync: add alert_rules table for DB-driven Grafana alert sync

ISSUE: Glad-Labs/poindexter#848

``brain/alert_sync.py`` (GH-28) has read from this table since it was
written, but the table itself was never migrated — every historical
migration was audited during the 2026-07-11 Phase G squash and none of
them created it. The sync loop has been warning "alert_rules table not
present — run migrations" every ~15 min since day one because the half
it depends on was never shipped.

Rather than delete the (well-tested: 581 lines + 702 lines of tests)
sync code, this finishes the feature: the table, keyed the same way as
the other 5 declarative data-plane tables (``name`` UNIQUE, an
``enabled`` gate, an ``updated_at`` touch trigger). Columns mirror
exactly what ``alert_sync.sync_alert_rules`` selects — see its
docstring for the Grafana-payload mapping (``promql_query`` may be
PromQL or a raw SQL SELECT; ``duration`` is a Grafana "for" string like
``"5m"``).

No seed rows: unlike the other 5 surfaces (which ship a curated
starter set), this one lets the operator decide what's alert-worthy
via ``poindexter alerts create`` rather than this migration presuming
it — a wrong guess here would be an unwanted page, not just noise.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS alert_rules (
                id uuid DEFAULT gen_random_uuid() NOT NULL PRIMARY KEY,
                name text NOT NULL UNIQUE,
                promql_query text NOT NULL,
                threshold double precision NOT NULL,
                duration text DEFAULT '5m'::text NOT NULL,
                severity text DEFAULT 'warning'::text NOT NULL,
                enabled boolean DEFAULT false NOT NULL,
                labels_json jsonb DEFAULT '{}'::jsonb NOT NULL,
                annotations_json jsonb DEFAULT '{}'::jsonb NOT NULL,
                created_at timestamp with time zone DEFAULT now() NOT NULL,
                updated_at timestamp with time zone DEFAULT now() NOT NULL
            )
            """
        )
        await conn.execute(
            """
            CREATE OR REPLACE FUNCTION alert_rules_touch_updated_at()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$ BEGIN NEW.updated_at = now(); RETURN NEW; END; $$
            """
        )
        await conn.execute(
            """
            DROP TRIGGER IF EXISTS alert_rules_touch_updated_at_trg ON alert_rules
            """
        )
        await conn.execute(
            """
            CREATE TRIGGER alert_rules_touch_updated_at_trg
            BEFORE UPDATE ON alert_rules
            FOR EACH ROW EXECUTE FUNCTION alert_rules_touch_updated_at()
            """
        )
    logger.info(
        "Migration add_alert_rules_table_for_db_driven_grafana_alert_sync: applied"
    )


async def down(pool) -> None:
    """Revert the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            "DROP TRIGGER IF EXISTS alert_rules_touch_updated_at_trg ON alert_rules"
        )
        await conn.execute("DROP FUNCTION IF EXISTS alert_rules_touch_updated_at()")
        await conn.execute("DROP TABLE IF EXISTS alert_rules")
    logger.info(
        "Migration add_alert_rules_table_for_db_driven_grafana_alert_sync: reverted"
    )
