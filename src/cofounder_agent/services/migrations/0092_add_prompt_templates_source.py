"""Migration 0092: add ``source`` column to ``prompt_templates`` for
Pro-tier prompt gating (gitea#225).

The Pro tier ($9.99/mo, $89.99/yr) ships an upgraded prompt pack that
should override free defaults when ``app_settings.premium_active='true'``.
Without a way to distinguish premium rows from default rows on import,
the activate flow can't tell which rows are user-installed vs. premium-
delivered, and the prompt loader can't override defaults cleanly.

This migration adds a single ``source`` column with a CHECK constraint
limiting values to ``'default'`` (free, ships in the open-source repo)
or ``'premium'`` (delivered via Lemon Squeezy file delivery on activation).

All existing rows get backfilled to ``'default'`` — they were installed
before the Pro tier shipped and represent the free baseline.

The prompt loader reads premium rows in ``services.prompt_manager``;
they layer over defaults when ``premium_active='true'``.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


SQL_UP = """
ALTER TABLE prompt_templates
    ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'default'
        CHECK (source IN ('default', 'premium'));

CREATE INDEX IF NOT EXISTS idx_prompt_templates_source_active
    ON prompt_templates (source, is_active)
    WHERE is_active = true;
"""

SQL_DOWN = """
DROP INDEX IF EXISTS idx_prompt_templates_source_active;
ALTER TABLE prompt_templates DROP COLUMN IF EXISTS source;
"""


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SQL_UP)
        # Existing rows get the DEFAULT value automatically. Confirm count
        # for ops visibility — non-zero means the migration is informative
        # rather than just additive on a fresh DB.
        count = await conn.fetchval(
            "SELECT count(*) FROM prompt_templates WHERE source = 'default'"
        )
        logger.info(
            "0092: prompt_templates.source added (default check constraint, "
            "index on source+is_active). %d existing rows tagged 'default'.",
            count,
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SQL_DOWN)
        logger.info("0092: dropped prompt_templates.source")
