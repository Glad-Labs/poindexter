"""Migration 0137: add dispatch tracking columns to ``alert_events``.

Pairs with ``brain/alert_dispatcher.py`` and the simplification in
``routes/alertmanager_webhook_routes.py``. Today the worker's webhook
handler both persists the alert AND fans it out to Telegram/Discord
inline. With the unification (Glad-Labs/poindexter#340 prep), the
webhook becomes a pure persistence sink and the brain daemon owns
all outbound dispatch.

To make that split observable we need two new columns on ``alert_events``:

- ``dispatched_at TIMESTAMPTZ`` — NULL while the row is waiting for the
  brain to pick it up, set to NOW() once the brain has attempted
  delivery (success OR error). The brain's poll query is
  ``WHERE dispatched_at IS NULL ORDER BY id LIMIT N``, so this column
  is the queue cursor.
- ``dispatch_result TEXT`` — short status string. ``'sent'`` on success;
  ``'error: <truncated message>'`` on failure. Lets operators grep
  ``alert_events`` directly to see which alerts actually reached
  Telegram/Discord without trawling brain logs.

An index on ``dispatched_at`` (partial, IS NULL) keeps the brain's
poll query cheap as the table grows — only undispatched rows live in
the index.

Idempotent: ``ADD COLUMN IF NOT EXISTS`` + ``CREATE INDEX IF NOT
EXISTS``. Safe to re-run.

Backfill posture: existing rows are LEFT NULL for ``dispatched_at``.
That means after this migration runs, the brain dispatcher will
re-attempt every historical alert exactly once on its first poll.
That's the intended behaviour — the worker stopped dispatching at
the same commit, so without backfill those rows would never reach
the operator. If the operator wants to skip the backlog they can
``UPDATE alert_events SET dispatched_at = NOW(), dispatch_result =
'backfill_skipped' WHERE dispatched_at IS NULL`` BEFORE deploying
the new brain daemon.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


async def _table_exists(conn, table: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
            "WHERE table_name = $1)",
            table,
        )
    )


async def _column_exists(conn, table: str, column: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
            "WHERE table_name = $1 AND column_name = $2)",
            table, column,
        )
    )


async def up(pool) -> None:
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "alert_events"):
            # The table is created lazily by the alertmanager webhook
            # route's _ensure_table on first request. If it doesn't exist
            # yet there's nothing to extend — the route's CREATE TABLE
            # IF NOT EXISTS doesn't include these columns, but the
            # dispatcher poll handles the column-missing case by
            # treating it as "no work to do" until the columns appear.
            # This migration will be a no-op on fresh installs that
            # haven't received any alerts yet.
            logger.info(
                "Migration 0137: alert_events table missing — skipping "
                "(will be created lazily on first webhook hit; rerun this "
                "migration after that to add dispatch columns)"
            )
            return

        await conn.execute(
            "ALTER TABLE alert_events "
            "ADD COLUMN IF NOT EXISTS dispatched_at TIMESTAMPTZ"
        )
        await conn.execute(
            "ALTER TABLE alert_events "
            "ADD COLUMN IF NOT EXISTS dispatch_result TEXT"
        )

        # Partial index — only undispatched rows live in the index, so
        # the brain's `WHERE dispatched_at IS NULL` poll stays O(log N
        # of pending) regardless of total table size.
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_alert_events_undispatched "
            "ON alert_events (id) "
            "WHERE dispatched_at IS NULL"
        )

        logger.info(
            "Migration 0137: added dispatched_at + dispatch_result "
            "columns and idx_alert_events_undispatched index"
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "alert_events"):
            return
        await conn.execute(
            "DROP INDEX IF EXISTS idx_alert_events_undispatched"
        )
        await conn.execute(
            "ALTER TABLE alert_events DROP COLUMN IF EXISTS dispatch_result"
        )
        await conn.execute(
            "ALTER TABLE alert_events DROP COLUMN IF EXISTS dispatched_at"
        )
        logger.info(
            "Migration 0137 rolled back: removed dispatch columns + index"
        )
