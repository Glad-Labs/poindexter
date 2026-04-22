"""
Scheduled Post Publisher

Background coroutine that publishes posts whose scheduled publication
time has arrived. Runs every 60 seconds.
"""

import asyncio
from typing import Any

from services.logger_config import get_logger
# Phase H (GH#95): bind SiteConfig singleton at module-load. The old
# try/except lazy import inside the coroutine is gone; the reference is
# resolved here (safe — the singleton's VALUES are populated later,
# but the reference itself is always importable). Callers may override
# via the ``site_config`` kwarg for tests + alternative wiring.
from services.site_config import site_config as _default_site_config

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
        site_config: SiteConfig instance (DI — Phase H, GH#95).
            ``None`` resolves the module singleton — transitional while
            main.py (the sole production caller) finishes its own
            Phase H migration; an explicit instance wins.
    """
    sc = site_config if site_config is not None else _default_site_config
    # Poll interval tunable via app_settings.scheduled_publisher_poll_seconds (#198)
    _poll_interval = sc.get_int("scheduled_publisher_poll_seconds", 60)
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
