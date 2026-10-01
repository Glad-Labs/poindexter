"""Migration: add topic scope columns to niches.

ISSUE: Glad-Labs/poindexter#1127

A niche had no field saying what it covers. The topic ranker scored
candidates only against goal weights whose descriptions name no subject,
so the subject was decided by whichever sources fed the pool. These
columns let an operator state it per niche:

- ``topic_subject`` — what the niche covers, in plain prose. NULL (every
  existing niche) means no scope: sweeps behave exactly as before.
- ``topic_exclusions`` — subjects that are out of scope even when they sit
  next to the subject ("video game news", "startup marketing").
- ``topic_scope_filter`` — when true and a subject is set, an LLM scope
  check drops out-of-scope candidates before ranking. When false the
  subject only steers ranking.

Purely additive. Set via ``poindexter topics niche set-scope``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE niches ADD COLUMN IF NOT EXISTS topic_subject text"
        )
        await conn.execute(
            "ALTER TABLE niches ADD COLUMN IF NOT EXISTS topic_exclusions "
            "text[] NOT NULL DEFAULT '{}'::text[]"
        )
        await conn.execute(
            "ALTER TABLE niches ADD COLUMN IF NOT EXISTS topic_scope_filter "
            "boolean NOT NULL DEFAULT true"
        )
    logger.info("Migration add_topic_scope_columns_to_niches: applied")


async def down(pool) -> None:
    """Revert the migration."""
    async with pool.acquire() as conn:
        await conn.execute("ALTER TABLE niches DROP COLUMN IF EXISTS topic_scope_filter")
        await conn.execute("ALTER TABLE niches DROP COLUMN IF EXISTS topic_exclusions")
        await conn.execute("ALTER TABLE niches DROP COLUMN IF EXISTS topic_subject")
    logger.info("Migration add_topic_scope_columns_to_niches: reverted")
