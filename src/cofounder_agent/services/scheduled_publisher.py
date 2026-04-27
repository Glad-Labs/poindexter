"""
Scheduled Post Publisher

Background coroutine that publishes posts whose scheduled publication
time has arrived. Runs every 60 seconds.

Final-publish-approval gate
---------------------------

When the ``pipeline_gate_final_publish_approval`` app_setting is on,
the publisher pauses each row at its slot instead of flipping it to
``published``. The pause writes
``awaiting_gate='final_publish_approval'`` (plus artifact + timestamp)
and notifies the operator. The row stays at ``status='scheduled'``;
the partial WHERE-clause filter on subsequent ticks skips it until
the operator clears the gate via
``poindexter approve-publish <post_id>``. After that the next tick
publishes normally.

When the gate is off (default), behavior is the original one-step
flip.
"""

import asyncio
import json
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
    and published_at <= NOW(), then publishes them — unless the
    final-publish-approval gate is enabled, in which case the
    publisher pauses the row for operator review instead.

    Args:
        get_pool: Callable that returns the asyncpg connection pool
        site_config: SiteConfig instance (DI — Phase H, GH#95). When
            None, the poll interval falls back to the documented 60s
            default and the gate is treated as disabled. Production
            callers (main.py lifespan) pass ``app.state.site_config``
            so app_settings tuning works.
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

            gate_on = _publish_gate_enabled(site_config)

            async with pool.acquire() as conn:
                if gate_on:
                    # Fetch candidates without flipping status. Filter out
                    # rows that are already paused at any gate so we don't
                    # re-notify on every tick.
                    candidates = await conn.fetch(
                        """
                        SELECT id, title, slug, published_at
                          FROM posts
                         WHERE status = 'scheduled'
                           AND published_at <= NOW()
                           AND awaiting_gate IS NULL
                         ORDER BY published_at ASC
                         LIMIT 50
                        """
                    )
                    for row in candidates:
                        await _pause_post_for_gate(
                            row=row, pool=pool, site_config=site_config,
                        )
                else:
                    rows = await conn.fetch(
                        """
                        UPDATE posts
                        SET status = 'published', updated_at = NOW()
                        WHERE status = 'scheduled'
                          AND published_at <= NOW()
                          AND awaiting_gate IS NULL
                        RETURNING id, title
                        """
                    )
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


def _publish_gate_enabled(site_config: Any) -> bool:
    """Return True iff the final-publish-approval gate is on.

    Routes through the same enable check the mid-pipeline gates use so
    the operator's mental model is uniform: every gate is a
    ``pipeline_gate_<slug>`` flag in app_settings.
    """
    if site_config is None:
        return False
    try:
        from services.approval_service import is_gate_enabled
        from services.posts_approval_service import FINAL_PUBLISH_GATE
    except Exception:
        return False
    return is_gate_enabled(FINAL_PUBLISH_GATE, site_config)


async def _pause_post_for_gate(
    *,
    row: Any,
    pool: Any,
    site_config: Any,
) -> None:
    """Pause one post at the publish gate. Failures are logged and
    swallowed so a single bad row can't kill the publisher loop."""
    from services.posts_approval_service import (
        FINAL_PUBLISH_GATE,
        pause_post_at_gate,
    )

    artifact = {
        "post_id": str(row["id"]),
        "slug": row.get("slug") or "",
        "title": row.get("title") or "",
        "scheduled_for": (
            row["published_at"].isoformat()
            if row.get("published_at") is not None
            and hasattr(row["published_at"], "isoformat")
            else str(row.get("published_at"))
        ),
    }
    try:
        result = await pause_post_at_gate(
            post_id=str(row["id"]),
            gate_name=FINAL_PUBLISH_GATE,
            artifact=artifact,
            site_config=site_config,
            pool=pool,
        )
        logger.info(
            "[scheduled_publisher] Paused at final_publish_approval: %s (%s) "
            "notify=%s",
            artifact["title"], artifact["post_id"],
            json.dumps(result.get("notify", {}), default=str),
        )
    except Exception as exc:
        logger.error(
            "[scheduled_publisher] Failed to pause post %s at "
            "final_publish_approval: %s",
            row.get("id"), exc, exc_info=True,
        )
