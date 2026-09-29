"""Migration 20260928_231739: drop the sync_metrics table, orphaned when
SyncService was removed

ISSUE: Glad-Labs/poindexter#1114

``sync_metrics`` was the two-database-era sync's scratch table. ``SyncService``
created it lazily (``CREATE TABLE IF NOT EXISTS sync_metrics ...``), wrote a
newsletter-subscriber snapshot into it (``_pull_newsletter_stats``) and read the
latest row back (``get_status``). Glad-Labs/poindexter#1112 deleted the class.
The Phase G baseline had already captured the table, because prod carried it, so
every fresh install still creates it.

Verified 2026-09-28, before writing this:

* Nothing in the tree writes or reads it. A grep over every tracked file finds
  the name only in ``0000_baseline.schema.sql``.
* Nothing depends on it. On prod (read-only) no view or function references it,
  no foreign key points at it, and no ``retention_policies``, ``external_taps``,
  ``publishing_adapters``, ``alert_rules`` or ``app_settings`` row names it. No
  Grafana panel queries it.
* It holds 138 rows, all copies of the same ``newsletter_subscribers`` snapshot
  (``{total, verified, unsubscribed, latest_signup}``), written 2026-04-08 to
  2026-04-09. Nothing has written since the hosted target was decommissioned.
  They stay in ordinary database backups.

This is the first migration after the baseline to drop a table, so the choices
are written down:

* ``DROP TABLE IF EXISTS`` with no ``CASCADE``. Nothing first-party depends on
  the table, and if an operator's own view or function does, the migration
  should fail and say so rather than silently cascade that object away.
* ``0000_baseline.schema.sql`` is left alone. It is a frozen snapshot that later
  migrations mutate, exactly as the newsletter-column drop did, so a fresh
  install creates the table in the baseline and drops it here.
* ``down()`` recreates the structure only. The rows are not restored.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Drop the orphaned table. No-op on installs that no longer have it."""
    async with pool.acquire() as conn:
        present = await conn.fetchval("SELECT to_regclass('sync_metrics') IS NOT NULL")
        # Count first so the log records what was removed; the table is tiny.
        rows = await conn.fetchval("SELECT count(*) FROM sync_metrics") if present else 0
        await conn.execute("DROP TABLE IF EXISTS sync_metrics")
    logger.info(
        "Migration drop_the_sync_metrics_table_orphaned_by_the_sync_service_removal: "
        "applied (%s)",
        f"dropped sync_metrics with {rows} row(s)" if present else "sync_metrics not present",
    )


async def down(pool) -> None:
    """Recreate the empty table with the columns it had in the baseline.

    Structure-only restore: nothing reads or writes ``sync_metrics`` any more,
    so an empty table changes no behaviour. The dropped rows are not restored.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sync_metrics (
                id SERIAL PRIMARY KEY,
                metric_name VARCHAR(100) NOT NULL,
                metric_value JSONB NOT NULL,
                synced_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    logger.info(
        "Migration drop_the_sync_metrics_table_orphaned_by_the_sync_service_removal: "
        "reverted (empty sync_metrics recreated)"
    )
