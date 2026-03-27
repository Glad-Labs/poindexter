"""
Scheduled Post Publisher

Background coroutine that publishes posts whose scheduled publication
time has arrived. Runs every 60 seconds.
"""

import asyncio

from services.logger_config import get_logger

logger = get_logger(__name__)


async def run_scheduled_publisher(get_pool):
    """
    Background loop that checks for posts with status='scheduled'
    and published_at <= NOW(), then publishes them.

    Args:
        get_pool: Callable that returns the asyncpg connection pool
    """
    logger.info("[scheduled_publisher] Started")
    first_run = True
    while True:
        try:
            if first_run:
                first_run = False
            else:
                await asyncio.sleep(60)
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
