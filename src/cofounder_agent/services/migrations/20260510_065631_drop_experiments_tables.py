"""Drop the experiments + experiment_assignments tables.

Closes ``Glad-Labs/poindexter#202``: the SQL-table-backed
ExperimentService is gone, replaced by
``services/langfuse_experiments.py`` which persists experiments,
assignments, and outcomes into Langfuse instead.

Both tables held 0 rows in production at cutover (verified
2026-05-10), so this migration is non-destructive in practice — no
historical data to migrate. The ``experiments`` table was just
recreated earlier this same session by
``20260510_013927_recreate_experiments_with_key_schema`` after the
2026-05-08 baseline squash captured a half-migrated state; this
migration retires the recreated tables now that Langfuse owns them.

Idempotent — ``DROP TABLE IF EXISTS`` is a no-op when the tables
don't exist.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_SQL = """
DROP TABLE IF EXISTS experiment_assignments;
DROP TABLE IF EXISTS experiments;
"""


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_SQL)
    logger.info(
        "20260510_065631: experiments + experiment_assignments dropped "
        "(harness now lives in Langfuse via services/langfuse_experiments.py)"
    )
