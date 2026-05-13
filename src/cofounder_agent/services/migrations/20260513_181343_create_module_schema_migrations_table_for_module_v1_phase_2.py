"""Create the ``module_schema_migrations`` table that records every
per-module migration that's been applied.

ISSUE: Glad-Labs/poindexter#490

Phase 2 of Module v1. Substrate migrations continue to use the
existing ``schema_migrations`` table; modules record into THIS
table so two modules can each ship a migration named ``init.py``
without colliding. The compound key (module_name, migration_name)
is the natural identity. ``applied_at`` is the audit timestamp.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_UP_SQL = """
CREATE TABLE IF NOT EXISTS module_schema_migrations (
    id            SERIAL PRIMARY KEY,
    module_name   VARCHAR(64)  NOT NULL,
    migration_name VARCHAR(255) NOT NULL,
    applied_at    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (module_name, migration_name)
);
CREATE INDEX IF NOT EXISTS module_schema_migrations_module_idx
    ON module_schema_migrations (module_name);
"""


async def up(pool) -> None:
    """Apply the migration. Idempotent via ``IF NOT EXISTS``."""
    async with pool.acquire() as conn:
        await conn.execute(_UP_SQL)
        logger.info(
            "Migration create_module_schema_migrations_table_for_module_v1_phase_2: applied"
        )


async def down(pool) -> None:
    """Drop the tracking table."""
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS module_schema_migrations")
        logger.info(
            "Migration create_module_schema_migrations_table_for_module_v1_phase_2 down: reverted"
        )
