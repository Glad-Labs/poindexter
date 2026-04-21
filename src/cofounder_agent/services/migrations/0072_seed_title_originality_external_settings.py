"""Migration 0072: seed external title-originality settings (GH-87).

The Phase E generate_content stage now runs a DuckDuckGo HTML search for
the exact post title at approval time and applies a QA score penalty
when the title appears verbatim in external results (see
``services/title_originality_external.py``). This migration seeds the
three ``app_settings`` rows that control the behavior:

* ``title_originality_external_check_enabled`` — master kill-switch.
  Defaults to ``true``.
* ``title_originality_external_penalty`` — points subtracted from the
  QA score when a verbatim external duplicate is found. Default ``-50``.
* ``title_originality_cache_ttl_hours`` — how long to cache a DDG result
  for a given title. Default ``24`` hours. DDG rate-limits aggressively,
  so retries during the same pipeline run must hit the cache.

Idempotent: ``ON CONFLICT DO NOTHING`` leaves any operator-tuned value
alone. Safe to re-run. Down migration deletes the three rows.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


_SEED_ROWS = (
    (
        "title_originality_external_check_enabled",
        "true",
        "content",
        "GH-87: enable DuckDuckGo HTML search for the exact post title at "
        "approval time. Verbatim external matches subtract "
        "title_originality_external_penalty from the QA score.",
    ),
    (
        "title_originality_external_penalty",
        "-50",
        "content",
        "GH-87: points subtracted from the QA score when the post title "
        "appears verbatim in external search results. Stored as a negative "
        "integer for human readability; the service code takes the abs value.",
    ),
    (
        "title_originality_cache_ttl_hours",
        "24",
        "content",
        "GH-87: TTL (hours) for the in-process cache that dedupes repeated "
        "DuckDuckGo queries for the same title. DDG rate-limits aggressively, "
        "so tightening this below 1h is unwise.",
    ),
)


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
            logger.info(
                "Table 'app_settings' missing — skipping migration 0072"
            )
            return
        for key, value, category, description in _SEED_ROWS:
            await conn.execute(
                """
                INSERT INTO app_settings (
                    key, value, category, description, is_secret
                )
                VALUES ($1, $2, $3, $4, false)
                ON CONFLICT (key) DO NOTHING
                """,
                key, value, category, description,
            )
        logger.info(
            "Migration 0072: seeded %d title-originality external settings "
            "(if not already set)",
            len(_SEED_ROWS),
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        keys = [r[0] for r in _SEED_ROWS]
        await conn.execute(
            "DELETE FROM app_settings WHERE key = ANY($1)", keys,
        )
        logger.info(
            "Migration 0072 rolled back: removed %d title-originality settings",
            len(keys),
        )
