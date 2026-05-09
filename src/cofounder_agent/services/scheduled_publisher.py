"""
Scheduled Post Publisher

Background coroutine that publishes posts whose scheduled publication
time has arrived. Runs every 60 seconds.

Glad-Labs/poindexter#327: every promotion to ``status='published''``
now triggers ISR revalidation via the shared
``services.revalidation_service.trigger_isr_revalidate`` helper.
Previously this loop only flipped the row in postgres and relied on
the next ISR window (5 min) to surface the post on Vercel — which
left a window where new posts existed in the cloud DB but did not
appear on www.gladlabs.io.
"""

import asyncio

from services.logger_config import get_logger
from services.site_config import SiteConfig

# Lifespan-bound SiteConfig; main.py wires this via set_site_config().
# Defaults to a fresh env-fallback instance until the lifespan setter
# fires. Tests can either patch this attribute directly or call
# ``set_site_config()`` for explicit wiring.
site_config: SiteConfig = SiteConfig()


def set_site_config(sc: SiteConfig) -> None:
    """Wire the lifespan-bound SiteConfig instance for this module."""
    global site_config
    site_config = sc


logger = get_logger(__name__)


async def run_scheduled_publisher(get_pool, *, site_config=None):
    """
    Background loop that checks for posts with status='scheduled'
    and published_at <= NOW(), then publishes them.

    Args:
        get_pool: Callable that returns the asyncpg connection pool
        site_config: Optional SiteConfig (Phase H DI seam — GH#95). When
            provided, used to read ``scheduled_publisher_poll_seconds``;
            falls back to the module singleton for back-compat.
    """
    # Poll interval tunable via app_settings.scheduled_publisher_poll_seconds (#198)
    try:
        if site_config is not None:
            _poll_interval = int(
                site_config.get("scheduled_publisher_poll_seconds", 60)
            )
        else:
            _poll_interval = site_config.get_int(
                "scheduled_publisher_poll_seconds", 60,
            )
    except Exception:
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
                # #327: pull the slug back too so we can revalidate the
                # post-specific path. Previously only id/title were
                # returned and the loop never triggered ISR busting,
                # so promoted posts sat invisible for ≤5 min.
                # `distributed_at` gates both the RSS feed
                # (app/feed.xml/route.ts) and the static R2 index export
                # (static_export_service.export_posts_index). Posts
                # promoted via this loop sat invisible from both surfaces
                # because the original UPDATE only flipped status — see
                # https://github.com/Glad-Labs/poindexter (RSS staleness
                # + missing-from-/posts bug, 2026-05-01). COALESCE
                # preserves any pre-set value (re-promotion edge case).
                rows = await conn.fetch("""
                    UPDATE posts
                    SET status = 'published',
                        updated_at = NOW(),
                        distributed_at = COALESCE(distributed_at, NOW())
                    WHERE status = 'scheduled' AND published_at <= NOW()
                    RETURNING id, title, slug
                    """)
                if rows:
                    for row in rows:
                        logger.info(
                            "[scheduled_publisher] Published scheduled post: %s (%s)",
                            row["title"],
                            row["id"],
                        )
                        # Glad-Labs/poindexter#327: every promotion must
                        # bust the Vercel ISR cache, otherwise the post
                        # won't appear on www.gladlabs.io until the next
                        # 5-minute window.
                        await _revalidate_for_row(row)
        except asyncio.CancelledError:
            logger.info("[scheduled_publisher] Shutting down")
            break
        except Exception as e:
            logger.error("[scheduled_publisher] Error: %s", e, exc_info=True)


async def _revalidate_for_row(row) -> None:
    """Trigger ISR revalidation for a freshly-promoted scheduled post.

    Pulled out as a helper so the main loop body stays readable and
    tests can patch a single symbol.

    Never raises — revalidation failure must not poison the loop or
    block subsequent rows in the same batch.
    """
    try:
        slug = row["slug"]
    except (KeyError, TypeError):
        slug = None
    if not slug:
        try:
            row_id = row["id"]
        except (KeyError, TypeError):
            row_id = "?"
        logger.warning(
            "[scheduled_publisher] Skipping revalidation — no slug on row %s",
            row_id,
        )
        return
    try:
        from services.revalidation_service import trigger_isr_revalidate
        ok = await trigger_isr_revalidate(slug)
        if ok:
            logger.info(
                "[scheduled_publisher] ISR revalidation triggered for %s",
                slug,
            )
        else:
            logger.warning(
                "[scheduled_publisher] ISR revalidation returned failure for %s",
                slug,
            )
    except Exception as reval_err:
        logger.warning(
            "[scheduled_publisher] Revalidation raised for %s (non-fatal): %s",
            slug, reval_err,
        )
