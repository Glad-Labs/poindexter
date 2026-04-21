"""
Migration 0080: Drop clearly dead empty tables.

Part of gitea#271 Phase 4.2 — empty-table audit. Each table listed below
has **all three** of:
  - 0 rows in production at audit time (2026-04-21).
  - 0 SQL references in ``services/``, ``routes/``, or ``utils/`` (tests OK).
  - 0 inbound foreign key constraints.

Dropping them reclaims catalog bloat and makes the schema honest about
what code actually uses. All are IF EXISTS so the migration is idempotent.

Intentionally NOT dropped:
  - `authors` / `categories` / `users` / `sites` — empty but CMS-model tables
    with live code paths (even if unused right now).
  - Feedback-loop tables we just wired in Phase 3.A/B (model_performance,
    routing_outcomes, content_revisions, gpu_task_sessions, revenue_events,
    subscriber_events, external_metrics, post_performance).
  - `experiments`, `fine_tuning_jobs`, `learning_patterns`,
    `quality_metrics_daily`, `quality_improvement_logs` — scaffolded for
    upcoming work (gitea#273, GH#50, GH#82).
  - Gitea-owned tables (action_*, commit_*, issue_*, org_*, etc.) — left
    entirely alone, they belong to the self-hosted Gitea instance sharing
    this DB, not to Poindexter.

Reclaim: marginal (a few kB of catalog / index metadata per table), but
schema clarity is the real win.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


_DROP_TABLES = [
    "brain_queue",
    "user_blocking",
    "two_factor",
    "webauthn_credential",
    "training_datasets",
    "app_state",
    "upload",
    "version",
]


SQL_UP = "\n".join(
    f"DROP TABLE IF EXISTS public.{t} CASCADE;" for t in _DROP_TABLES
)


SQL_DOWN = """
-- Irreversible. Pull from backup if a dropped table is needed.
SELECT 1;
"""


async def up(pool) -> None:
    async with pool.acquire() as conn:
        before = {}
        for t in _DROP_TABLES:
            exists = await conn.fetchval(
                "SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename=$1", t,
            )
            if exists:
                count = await conn.fetchval(f"SELECT COUNT(*) FROM {t}")  # noqa: S608
                before[t] = count or 0
            else:
                before[t] = None
        await conn.execute(SQL_UP)
        logger.info(
            "0080: dropped %s dead empty tables: %s",
            sum(1 for v in before.values() if v is not None),
            {k: v for k, v in before.items() if v is not None},
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SQL_DOWN)
        logger.info("0080 down is a no-op — restore from backup if needed")
