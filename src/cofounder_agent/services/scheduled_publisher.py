"""
Scheduled Post Publisher

Background coroutine that publishes posts whose scheduled publication
time has arrived. Runs every 60 seconds.
"""

import asyncio
from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)


async def run_scheduled_publisher(
    get_pool,
    *,
    site_config: Any = None,
) -> None:
    """
    Background loop that checks for posts with status='scheduled'
    and published_at <= NOW(), then publishes them.

    Args:
        get_pool: Callable that returns the asyncpg connection pool
        site_config: SiteConfig instance (DI — Phase H, GH#95). When
            None, the poll interval falls back to the documented 60s
            default. Production callers (main.py lifespan) pass
            ``app.state.site_config`` so app_settings tuning works.
    """
    if site_config is not None:
        _poll_interval = site_config.get_int("scheduled_publisher_poll_seconds", 60)
    else:
        _poll_interval = 60
    logger.info(
        "[scheduled_publisher] Started (poll interval: %ds)", _poll_interval
    )
    first_run = True
    while True:
        try:
            if first_run:
                first_run = False
            else:
                await asyncio.sleep(_poll_interval)
            pool = await get_pool()
            if not pool:
                continue

            async with pool.acquire() as conn:
                rows = await conn.fetch("""
                    UPDATE posts
                    SET status = 'published', updated_at = NOW()
                    WHERE status = 'scheduled' AND published_at <= NOW()
                    RETURNING id, title
                    """)
                if rows:
                    for row in rows:
                        logger.info(
                            "[scheduled_publisher] Published scheduled post: %s (%s)",
                            row["title"],
                            row["id"],
                        )
        except asyncio.CancelledError:
            logger.info("[scheduled_publisher] Shutting down")
            break
        except Exception as e:
            logger.error("[scheduled_publisher] Error: %s", e, exc_info=True)
