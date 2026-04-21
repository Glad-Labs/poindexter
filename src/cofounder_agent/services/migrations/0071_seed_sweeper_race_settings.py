"""Migration 0071: seed sweeper-race-condition settings (GH-90).

GitHub issue Glad-Labs/poindexter#90 — the stale-task sweeper was racing
the worker and auto-cancelling tasks that were actively being processed.
The fix requires the sweeper to guard on ``updated_at`` freshness and the
worker to heartbeat ``updated_at`` during long stages. Both are tunable
from the DB per project convention (no os.getenv, no silent defaults).

Seeds three app_settings:

1. ``stale_task_timeout_minutes`` (default 180) — the sweeper's freshness
   cutoff. A task is eligible for auto-cancel only when its ``updated_at``
   is older than this many minutes. Existing value (if any) is preserved.
2. ``worker_heartbeat_interval_seconds`` (default 30) — how often the
   worker stamps ``updated_at = NOW()`` on the task row while a stage is
   running. Must be well under ``stale_task_timeout_minutes * 60`` or the
   sweeper can still race.
3. ``brain_auto_cancel_grace_minutes`` (default 10) — extra safety margin
   on top of ``stale_task_timeout_minutes`` that the brain daemon adds
   before flipping status to failed. Acts as a second line of defence in
   case the worker's heartbeat was briefly delayed by a long GPU call.

Idempotent: ``INSERT ... ON CONFLICT DO NOTHING`` leaves any operator-set
value untouched.
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


_SEEDS = [
    (
        "stale_task_timeout_minutes",
        "180",
        "pipeline",
        "Stale-task sweeper cutoff. The brain daemon's auto-cancel "
        "only fires when a task's updated_at is older than this many "
        "minutes AND status is still in_progress. Worker heartbeats "
        "updated_at during long stages so the cutoff reflects real "
        "activity, not row-creation time. Ref: GH-90.",
    ),
    (
        "worker_heartbeat_interval_seconds",
        "30",
        "pipeline",
        "Worker heartbeat cadence. While processing a single task the "
        "TaskExecutor stamps content_tasks.updated_at = NOW() every N "
        "seconds so the sweeper's freshness clause stays accurate "
        "during long writer/QA/image stages. Must be < "
        "stale_task_timeout_minutes * 60 by a healthy margin. Ref: GH-90.",
    ),
    (
        "brain_auto_cancel_grace_minutes",
        "10",
        "pipeline",
        "Extra grace period the brain daemon adds on top of "
        "stale_task_timeout_minutes before flipping a stuck task to "
        "failed. Second line of defence against missed heartbeats "
        "during long single-shot GPU calls. Ref: GH-90.",
    ),
]


async def up(pool) -> None:
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "app_settings"):
            logger.warning(
                "Table 'app_settings' missing — skipping migration 0071"
            )
            return

        for key, value, category, description in _SEEDS:
            await conn.execute(
                """
                INSERT INTO app_settings (key, value, category, description, is_secret)
                VALUES ($1, $2, $3, $4, false)
                ON CONFLICT (key) DO NOTHING
                """,
                key, value, category, description,
            )
        logger.info(
            "Migration 0071: seeded %d sweeper-race settings "
            "(stale_task_timeout_minutes, worker_heartbeat_interval_seconds, "
            "brain_auto_cancel_grace_minutes)",
            len(_SEEDS),
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "app_settings"):
            return
        keys = [k for k, *_ in _SEEDS]
        await conn.execute(
            "DELETE FROM app_settings WHERE key = ANY($1::text[])",
            keys,
        )
        logger.info("Migration 0071 rolled back: removed sweeper-race settings")
