"""Drop the experiments + experiment_assignments tables.

ISSUE: Glad-Labs/poindexter#202

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

IRREVERSIBLE — see :func:`down` below. The Langfuse-backed harness
that replaced these tables stores experiment data in a completely
different shape; recreating these SQL tables from the Langfuse store
would be net-new work, not a one-line revert. Pattern matches
``20260509_130047_drop_pipeline_reviews.py``.
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


async def down(pool) -> None:
    """Refuse to revert.

    The experiment harness moved to Langfuse on 2026-05-10 (see #202).
    Both SQL tables were empty at cutover, so recreating them would
    produce empty tables that no live code reads — pure dead schema.
    If we ever needed to roll back, write a fresh forward migration
    that materialises whatever shape the operator actually needs from
    the Langfuse store (which is a different shape than these tables).
    """
    raise NotImplementedError(
        "20260510_065631_drop_experiments_tables is irreversible — the "
        "harness lives in Langfuse via services/langfuse_experiments.py. "
        "Write a forward migration if you need a SQL-shaped view of the "
        "Langfuse data."
    )
