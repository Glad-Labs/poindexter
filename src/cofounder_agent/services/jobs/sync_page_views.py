"""SyncPageViewsJob — pull new page_views into the local brain DB.

Replaces ``IdleWorker._sync_page_views``. Runs every 30 minutes by
default (matches the pre-refactor ``_is_due("sync_page_views", 30)``).

Used by Grafana dashboards that show traffic trends — the cloud DB
holds authoritative page-view events, and this job keeps a local
copy in sync with a high-water-mark watermark strategy.

Config (``plugin.job.sync_page_views``):
- ``enabled`` (default ``true``)
- ``interval_seconds`` (default 1800)
- ``config.batch_size`` (default 5000) — rows per fetch
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult

logger = logging.getLogger(__name__)


class SyncPageViewsJob:
    name = "sync_page_views"
    description = "Pull new page_views rows from the cloud DB into local brain DB"
    schedule = "every 30 minutes"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        from services.site_config import site_config
        cloud_url = site_config.get("database_url", "") or ""
        if not cloud_url:
            return JobResult(
                ok=True,
                detail="no database_url configured — skipping",
                changes_made=0,
            )

        batch_size = int(config.get("batch_size", 5000))

        try:
            import asyncpg
        except ImportError:
            return JobResult(ok=False, detail="asyncpg not available", changes_made=0)

        try:
            # Ensure local table exists (idempotent).
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS page_views (
                        id SERIAL PRIMARY KEY,
                        path TEXT,
                        slug TEXT,
                        referrer TEXT,
                        user_agent TEXT,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                last_ts = await conn.fetchval(
                    "SELECT MAX(created_at) FROM page_views"
                )

            cloud = await asyncpg.connect(cloud_url)
            try:
                if last_ts:
                    rows = await cloud.fetch(
                        "SELECT id, path, slug, referrer, user_agent, created_at "
                        "FROM page_views WHERE created_at > $1 "
                        "ORDER BY created_at LIMIT $2",
                        last_ts, batch_size,
                    )
                else:
                    rows = await cloud.fetch(
                        "SELECT id, path, slug, referrer, user_agent, created_at "
                        "FROM page_views ORDER BY created_at LIMIT $1",
                        batch_size,
                    )
            finally:
                await cloud.close()

            if not rows:
                return JobResult(ok=True, detail="no new rows", changes_made=0)

            async with pool.acquire() as conn:
                async with conn.transaction():
                    for row in rows:
                        await conn.execute(
                            """
                            INSERT INTO page_views
                              (path, slug, referrer, user_agent, created_at)
                            VALUES ($1, $2, $3, $4, $5)
                            """,
                            row["path"],
                            row["slug"],
                            row["referrer"],
                            row["user_agent"],
                            row["created_at"],
                        )

            return JobResult(
                ok=True,
                detail=f"synced {len(rows)} new page_views",
                changes_made=len(rows),
            )

        except Exception as e:
            logger.exception("SyncPageViewsJob failed: %s", e)
            return JobResult(ok=False, detail=str(e), changes_made=0)
