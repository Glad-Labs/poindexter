"""Migration 0069: seed ``alt_text_budget`` app_setting.

GitHub issue Glad-Labs/poindexter#84 — the inline image alt-text
generator now honours a DB-configurable character budget so operators
can tune the tradeoff between descriptive detail and a11y verbosity.

Default: 120 chars (covers ~95% of well-formed descriptive alts
without encouraging mid-word chopping). No env-var fallback — all
tunables go through ``app_settings`` per project convention.

Idempotent: ``INSERT ... ON CONFLICT DO NOTHING`` leaves an existing
operator-set value alone.
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


async def up(pool) -> None:
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "app_settings"):
            logger.warning(
                "Table 'app_settings' missing — skipping migration 0069"
            )
            return

        await conn.execute(
            """
            INSERT INTO app_settings (key, value, category, description, is_secret)
            VALUES (
                'alt_text_budget',
                '120',
                'content',
                'Character budget for inline <img alt="..."> text. '
                'The alt generator produces complete sentences within '
                'this budget; over-budget drafts are re-summarised or '
                'fall back to a topic-based template — never mid-word '
                'truncated. Ref: GH-84.',
                false
            )
            ON CONFLICT (key) DO NOTHING
            """
        )
        logger.info(
            "Migration 0069: seeded alt_text_budget=120 (if not already set)"
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "app_settings"):
            return
        await conn.execute(
            "DELETE FROM app_settings WHERE key = 'alt_text_budget'"
        )
        logger.info("Migration 0069 rolled back: removed alt_text_budget")
