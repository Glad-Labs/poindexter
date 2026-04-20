"""
Database Sync Service for Split Architecture

Handles bidirectional sync between local brain DB and cloud (production) DB:

  PUSH (local -> cloud): Published posts, categories, tags, post_tags
  PULL (cloud -> local): page_views (web_analytics), newsletter subscriber counts

Cloud DB:   Public site data only
Local DB:   Full operational data + pulled metrics for Grafana

All operations use upsert (INSERT ON CONFLICT UPDATE) for idempotency.
Graceful failure: if either DB is unreachable, log and skip.
"""

import os
from datetime import datetime, timezone
from typing import Any

import asyncpg

from services.logger_config import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Connection strings (env-only — #198: no hardcoded DSNs)
# Empty strings mean "not configured"; the caller / constructor raises
# before using them.
# ---------------------------------------------------------------------------
CLOUD_DATABASE_URL = os.getenv("CLOUD_DATABASE_URL") or os.getenv("DATABASE_URL", "")
LOCAL_DATABASE_URL = os.getenv("LOCAL_DATABASE_URL", "")


class SyncService:
    """
    Async service for syncing data between local and cloud PostgreSQL databases.

    Usage from FastAPI:
        sync = SyncService()
        await sync.connect()
        await sync.push_post(post_id)
        await sync.close()

    Usage standalone:
        async with SyncService() as sync:
            await sync.push_all_posts()
    """

    def __init__(
        self,
        cloud_url: str | None = None,
        local_url: str | None = None,
    ):
        import os

        self.cloud_url = cloud_url or os.getenv("CLOUD_DATABASE_URL", CLOUD_DATABASE_URL)
        self.local_url = local_url or os.getenv("LOCAL_DATABASE_URL", LOCAL_DATABASE_URL)
        self._cloud_pool: asyncpg.Pool | None = None
        self._local_pool: asyncpg.Pool | None = None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "SyncService":
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open connection pools to both databases."""
        if not self.cloud_url:
            logger.info("Sync disabled — no CLOUD_DATABASE_URL configured (#198)")
            self._cloud_pool = None
        else:
            try:
                self._cloud_pool = await asyncpg.create_pool(
                    self.cloud_url, min_size=2, max_size=5, timeout=15, command_timeout=30,
                )
                logger.info("Connected to cloud DB")
            except Exception as exc:
                logger.error("Failed to connect to cloud DB: %s", exc)
                self._cloud_pool = None

        if not self.local_url:
            logger.info("Sync disabled — no LOCAL_DATABASE_URL configured (#198)")
            self._local_pool = None
        else:
            try:
                self._local_pool = await asyncpg.create_pool(
                    self.local_url, min_size=2, max_size=5, timeout=15, command_timeout=30,
                )
                logger.info("Connected to local DB")
            except Exception as exc:
                logger.error("Failed to connect to local DB: %s", exc)
                self._local_pool = None

    async def close(self) -> None:
        """Close both connection pools gracefully."""
        for label, pool in [("cloud", self._cloud_pool), ("local", self._local_pool)]:
            if pool:
                try:
                    await pool.close()
                    logger.info("Closed %s DB pool", label)
                except Exception as exc:
                    logger.warning("Error closing %s pool: %s", label, exc)
        self._cloud_pool = None
        self._local_pool = None

    def _require_pools(self) -> bool:
        """Return True if both pools are live; log and return False otherwise."""
        if not self._cloud_pool:
            logger.error("Cloud DB pool is not connected -- skipping sync operation")
            return False
        if not self._local_pool:
            logger.error("Local DB pool is not connected -- skipping sync operation")
            return False
        return True

    # ======================================================================
    # PUSH: local -> cloud
    # ======================================================================

    async def push_post(self, post_id: str) -> bool:
        """
        Read a single published post from local DB and upsert it to cloud DB.

        Syncs the post row, its category, all associated tags, and post_tags
        junction entries.  Returns True on success, False on failure/skip.
        """
        if not self._require_pools():
            return False

        try:
            async with self._local_pool.acquire() as local:
                # Fetch the post
                post = await local.fetchrow(
                    "SELECT * FROM posts WHERE id = $1", post_id,
                )
                if not post:
                    logger.warning("Post %s not found in local DB", post_id)
                    return False

                # Fetch category if present
                category = None
                if post["category_id"]:
                    category = await local.fetchrow(
                        "SELECT * FROM categories WHERE id = $1", post["category_id"],
                    )

                # Fetch tags via post_tags junction
                tag_rows = await local.fetch(
                    """
                    SELECT t.* FROM tags t
                    JOIN post_tags pt ON pt.tag_id = t.id
                    WHERE pt.post_id = $1
                    """,
                    post_id,
                )

                # Fetch post_tags entries
                post_tag_rows = await local.fetch(
                    "SELECT * FROM post_tags WHERE post_id = $1", post_id,
                )

            # Upsert to cloud
            async with self._cloud_pool.acquire() as cloud:
                async with cloud.transaction():
                    # 1. Category
                    if category:
                        await self._upsert_category(cloud, category)

                    # 2. Tags
                    for tag in tag_rows:
                        await self._upsert_tag(cloud, tag)

                    # 3. Post
                    await self._upsert_post(cloud, post)

                    # 4. Post-tag associations
                    for pt in post_tag_rows:
                        await self._upsert_post_tag(cloud, pt)

            logger.info("Pushed post %s to cloud DB", post_id)
            return True

        except Exception as exc:
            logger.error("Failed to push post %s: %s", post_id, exc, exc_info=True)
            return False

    async def push_all_posts(self) -> dict[str, int]:
        """
        Push all published posts from local DB to cloud DB.

        Returns dict with counts: {"pushed": N, "skipped": M, "failed": F}
        """
        if not self._require_pools():
            return {"pushed": 0, "skipped": 0, "failed": 0}

        stats = {"pushed": 0, "skipped": 0, "failed": 0}

        try:
            async with self._local_pool.acquire() as local:
                rows = await local.fetch(
                    "SELECT id FROM posts WHERE status = 'published' ORDER BY published_at",
                )

            logger.info("Found %d published posts to sync", len(rows))

            for row in rows:
                pid = str(row["id"])
                ok = await self.push_post(pid)
                if ok:
                    stats["pushed"] += 1
                else:
                    stats["failed"] += 1

        except Exception as exc:
            logger.error("push_all_posts failed: %s", exc, exc_info=True)

        logger.info(
            "Push complete: %d pushed, %d failed",
            stats["pushed"], stats["failed"],
        )
        return stats

    # ------------------------------------------------------------------
    # Upsert helpers (cloud writes)
    # ------------------------------------------------------------------

    @staticmethod
    async def _upsert_category(conn: asyncpg.Connection, cat: asyncpg.Record) -> None:
        await conn.execute(
            """
            INSERT INTO categories (id, name, slug, description, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (id) DO UPDATE SET
                name        = EXCLUDED.name,
                slug        = EXCLUDED.slug,
                description = EXCLUDED.description,
                updated_at  = EXCLUDED.updated_at
            """,
            cat["id"], cat["name"], cat["slug"],
            cat.get("description"), cat["created_at"], cat["updated_at"],
        )

    @staticmethod
    async def _upsert_tag(conn: asyncpg.Connection, tag: asyncpg.Record) -> None:
        await conn.execute(
            """
            INSERT INTO tags (id, name, slug, description, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (id) DO UPDATE SET
                name        = EXCLUDED.name,
                slug        = EXCLUDED.slug,
                description = EXCLUDED.description,
                updated_at  = EXCLUDED.updated_at
            """,
            tag["id"], tag["name"], tag["slug"],
            tag.get("description"), tag["created_at"], tag["updated_at"],
        )

    @staticmethod
    async def _upsert_post(conn: asyncpg.Connection, post: asyncpg.Record) -> None:
        await conn.execute(
            """
            INSERT INTO posts (
                id, title, slug, content, excerpt,
                featured_image_url, cover_image_url,
                author_id, category_id, status,
                seo_title, seo_description, seo_keywords,
                created_by, updated_by,
                created_at, updated_at, published_at
            )
            VALUES (
                $1, $2, $3, $4, $5,
                $6, $7,
                $8, $9, $10,
                $11, $12, $13,
                $14, $15,
                $16, $17, $18
            )
            ON CONFLICT (id) DO UPDATE SET
                title              = EXCLUDED.title,
                slug               = EXCLUDED.slug,
                content            = EXCLUDED.content,
                excerpt            = EXCLUDED.excerpt,
                featured_image_url = EXCLUDED.featured_image_url,
                cover_image_url    = EXCLUDED.cover_image_url,
                author_id          = EXCLUDED.author_id,
                category_id        = EXCLUDED.category_id,
                status             = EXCLUDED.status,
                seo_title          = EXCLUDED.seo_title,
                seo_description    = EXCLUDED.seo_description,
                seo_keywords       = EXCLUDED.seo_keywords,
                updated_by         = EXCLUDED.updated_by,
                updated_at         = EXCLUDED.updated_at,
                published_at       = EXCLUDED.published_at
            """,
            post["id"], post["title"], post["slug"], post["content"], post.get("excerpt"),
            post.get("featured_image_url"), post.get("cover_image_url"),
            post.get("author_id"), post.get("category_id"),
            post.get("status", "draft"),
            post.get("seo_title"), post.get("seo_description"), post.get("seo_keywords"),
            post.get("created_by"), post.get("updated_by"),
            post["created_at"], post["updated_at"], post.get("published_at"),
        )

    @staticmethod
    async def _upsert_post_tag(conn: asyncpg.Connection, pt: asyncpg.Record) -> None:
        await conn.execute(
            """
            INSERT INTO post_tags (post_id, tag_id)
            VALUES ($1, $2)
            ON CONFLICT (post_id, tag_id) DO NOTHING
            """,
            pt["post_id"], pt["tag_id"],
        )

    # ======================================================================
    # PULL: cloud -> local  (metrics for Grafana)
    # ======================================================================

    async def pull_metrics(self) -> dict[str, Any]:
        """
        Pull analytics and subscriber metrics from cloud DB into local DB
        so Grafana dashboards can query them locally.

        Syncs:
          - web_analytics rows (page_views, sessions, etc.)
          - newsletter_subscribers aggregate counts

        Returns dict summarising what was pulled.
        """
        if not self._require_pools():
            return {"status": "skipped", "reason": "pools not connected"}

        result: dict[str, Any] = {}

        try:
            result["page_views"] = await self.pull_page_views()
        except Exception as exc:
            logger.error("Failed to pull page_views: %s", exc, exc_info=True)
            result["page_views"] = {"error": str(exc)}

        try:
            result["web_analytics"] = await self._pull_web_analytics()
        except Exception as exc:
            logger.error("Failed to pull web_analytics: %s", exc, exc_info=True)
            result["web_analytics"] = {"error": str(exc)}

        try:
            result["newsletter"] = await self._pull_newsletter_stats()
        except Exception as exc:
            logger.error("Failed to pull newsletter stats: %s", exc, exc_info=True)
            result["newsletter"] = {"error": str(exc)}

        logger.info("Pull metrics complete: %s", result)
        return result

    async def pull_page_views(self) -> dict[str, Any]:
        """
        Pull raw page_views rows from cloud DB into local DB.

        Uses created_at watermark to only fetch new rows.
        Upserts by id so re-runs are safe.
        """
        if not self._require_pools():
            return {"status": "skipped", "reason": "pools not connected"}

        try:
            # Get the latest created_at in local DB
            async with self._local_pool.acquire() as local:
                # Ensure the table exists locally
                await local.execute("""
                    CREATE TABLE IF NOT EXISTS page_views (
                        id SERIAL PRIMARY KEY,
                        path TEXT,
                        slug TEXT,
                        referrer TEXT,
                        user_agent TEXT,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                row = await local.fetchrow(
                    "SELECT MAX(created_at) AS max_ts FROM page_views",
                )
                last_ts = row["max_ts"] if row else None

            # Fetch new rows from cloud
            async with self._cloud_pool.acquire() as cloud:
                if last_ts:
                    rows = await cloud.fetch(
                        "SELECT id, path, slug, referrer, user_agent, created_at "
                        "FROM page_views WHERE created_at > $1 ORDER BY created_at LIMIT 5000",
                        last_ts,
                    )
                else:
                    rows = await cloud.fetch(
                        "SELECT id, path, slug, referrer, user_agent, created_at "
                        "FROM page_views ORDER BY created_at LIMIT 5000",
                    )

            if not rows:
                return {"rows_pulled": 0}

            # Upsert into local DB
            async with self._local_pool.acquire() as local:
                async with local.transaction():
                    for r in rows:
                        await local.execute(
                            """
                            INSERT INTO page_views (id, path, slug, referrer, user_agent, created_at)
                            VALUES ($1, $2, $3, $4, $5, $6)
                            ON CONFLICT (id) DO NOTHING
                            """,
                            r["id"], r.get("path"), r.get("slug"),
                            r.get("referrer"), r.get("user_agent"), r["created_at"],
                        )

            logger.info("Pulled %d page_views rows from cloud", len(rows))
            return {"rows_pulled": len(rows)}

        except Exception as exc:
            logger.error("Failed to pull page_views: %s", exc, exc_info=True)
            return {"error": str(exc)}

    async def _pull_web_analytics(self) -> dict[str, Any]:
        """Pull web_analytics rows from cloud that are newer than local max."""
        async with self._local_pool.acquire() as local:
            row = await local.fetchrow(
                "SELECT MAX(tracked_at) AS max_ts FROM web_analytics",
            )
            last_ts = row["max_ts"] if row else None

        async with self._cloud_pool.acquire() as cloud:
            if last_ts:
                rows = await cloud.fetch(
                    "SELECT * FROM web_analytics WHERE tracked_at > $1 ORDER BY tracked_at",
                    last_ts,
                )
            else:
                rows = await cloud.fetch(
                    "SELECT * FROM web_analytics ORDER BY tracked_at",
                )

        if not rows:
            return {"rows_pulled": 0}

        async with self._local_pool.acquire() as local:
            async with local.transaction():
                for r in rows:
                    await local.execute(
                        """
                        INSERT INTO web_analytics (
                            id, page_id, sessions, users, page_views,
                            bounce_rate, avg_session_duration, conversion_rate,
                            tracked_date, tracked_at
                        )
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                        ON CONFLICT (id) DO UPDATE SET
                            page_views           = EXCLUDED.page_views,
                            sessions             = EXCLUDED.sessions,
                            users                = EXCLUDED.users,
                            bounce_rate          = EXCLUDED.bounce_rate,
                            avg_session_duration = EXCLUDED.avg_session_duration,
                            conversion_rate      = EXCLUDED.conversion_rate,
                            tracked_date         = EXCLUDED.tracked_date,
                            tracked_at           = EXCLUDED.tracked_at
                        """,
                        r["id"], r.get("page_id"),
                        r.get("sessions", 0), r.get("users", 0), r.get("page_views", 0),
                        r.get("bounce_rate"), r.get("avg_session_duration"),
                        r.get("conversion_rate"),
                        r.get("tracked_date"), r["tracked_at"],
                    )

        return {"rows_pulled": len(rows)}

    async def pull_newsletter_subscribers(self) -> dict[str, Any]:
        """
        Pull newsletter_subscribers rows from cloud DB into local DB.

        Uses updated_at watermark to only fetch new/changed rows.
        Upserts by id so re-runs are safe (handles unsubscribes, verification updates).
        """
        if not self._require_pools():
            return {"status": "skipped", "reason": "pools not connected"}

        try:
            # Ensure the table exists locally
            async with self._local_pool.acquire() as local:
                await local.execute("""
                    CREATE TABLE IF NOT EXISTS newsletter_subscribers (
                        id SERIAL PRIMARY KEY,
                        email VARCHAR(255) NOT NULL UNIQUE,
                        first_name VARCHAR(100),
                        last_name VARCHAR(100),
                        company VARCHAR(255),
                        interest_categories JSONB,
                        subscribed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        ip_address VARCHAR(45),
                        user_agent TEXT,
                        verified BOOLEAN DEFAULT FALSE,
                        verification_token VARCHAR(255),
                        verified_at TIMESTAMP WITH TIME ZONE,
                        unsubscribed_at TIMESTAMP WITH TIME ZONE,
                        unsubscribe_reason TEXT,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        marketing_consent BOOLEAN DEFAULT FALSE
                    )
                """)
                row = await local.fetchrow(
                    "SELECT MAX(updated_at) AS max_ts FROM newsletter_subscribers",
                )
                last_ts = row["max_ts"] if row else None

            # Fetch new/updated rows from cloud
            async with self._cloud_pool.acquire() as cloud:
                if last_ts:
                    rows = await cloud.fetch(
                        "SELECT * FROM newsletter_subscribers "
                        "WHERE updated_at > $1 ORDER BY updated_at LIMIT 1000",
                        last_ts,
                    )
                else:
                    rows = await cloud.fetch(
                        "SELECT * FROM newsletter_subscribers "
                        "ORDER BY updated_at LIMIT 1000",
                    )

            if not rows:
                return {"rows_pulled": 0}

            # Upsert into local DB
            async with self._local_pool.acquire() as local:
                async with local.transaction():
                    for r in rows:
                        await local.execute(
                            """
                            INSERT INTO newsletter_subscribers (
                                id, email, first_name, last_name, company,
                                interest_categories, subscribed_at, ip_address, user_agent,
                                verified, verification_token, verified_at,
                                unsubscribed_at, unsubscribe_reason,
                                created_at, updated_at, marketing_consent
                            )
                            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
                            ON CONFLICT (id) DO UPDATE SET
                                email              = EXCLUDED.email,
                                first_name         = EXCLUDED.first_name,
                                last_name          = EXCLUDED.last_name,
                                company            = EXCLUDED.company,
                                interest_categories = EXCLUDED.interest_categories,
                                subscribed_at      = EXCLUDED.subscribed_at,
                                verified           = EXCLUDED.verified,
                                verified_at        = EXCLUDED.verified_at,
                                unsubscribed_at    = EXCLUDED.unsubscribed_at,
                                unsubscribe_reason = EXCLUDED.unsubscribe_reason,
                                updated_at         = EXCLUDED.updated_at,
                                marketing_consent  = EXCLUDED.marketing_consent
                            """,
                            r["id"], r["email"], r.get("first_name"), r.get("last_name"),
                            r.get("company"), r.get("interest_categories"),
                            r.get("subscribed_at"), r.get("ip_address"), r.get("user_agent"),
                            r.get("verified", False), r.get("verification_token"),
                            r.get("verified_at"), r.get("unsubscribed_at"),
                            r.get("unsubscribe_reason"),
                            r["created_at"], r["updated_at"],
                            r.get("marketing_consent", False),
                        )

            logger.info("Pulled %d newsletter_subscribers rows from cloud", len(rows))
            return {"rows_pulled": len(rows)}

        except Exception as exc:
            logger.error("Failed to pull newsletter_subscribers: %s", exc, exc_info=True)
            return {"error": str(exc)}

    async def _pull_newsletter_stats(self) -> dict[str, Any]:
        """
        Pull aggregate newsletter stats from cloud and store as a
        metrics snapshot in local sync_metrics table for Grafana.
        """
        async with self._cloud_pool.acquire() as cloud:
            stats = await cloud.fetchrow(
                """
                SELECT
                    COUNT(*)                                                AS total,
                    COUNT(*) FILTER (WHERE verified = true)                 AS verified,
                    COUNT(*) FILTER (WHERE unsubscribed_at IS NOT NULL)     AS unsubscribed,
                    MAX(subscribed_at)                                      AS latest_signup
                FROM newsletter_subscribers
                """,
            )

        snapshot = {
            "total": stats["total"],
            "verified": stats["verified"],
            "unsubscribed": stats["unsubscribed"],
            "latest_signup": str(stats["latest_signup"]) if stats["latest_signup"] else None,
        }

        # Store snapshot in a local sync_metrics table (created if missing)
        async with self._local_pool.acquire() as local:
            await local.execute(
                """
                CREATE TABLE IF NOT EXISTS sync_metrics (
                    id SERIAL PRIMARY KEY,
                    metric_name VARCHAR(100) NOT NULL,
                    metric_value JSONB NOT NULL,
                    synced_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                )
                """,
            )
            import json

            await local.execute(
                """
                INSERT INTO sync_metrics (metric_name, metric_value)
                VALUES ('newsletter_subscribers', $1::jsonb)
                """,
                json.dumps(snapshot),
            )

        return snapshot

    # ======================================================================
    # STATUS
    # ======================================================================

    async def get_status(self) -> dict[str, Any]:
        """
        Return a status summary comparing local and cloud data.

        Includes row counts, latest timestamps, and connection health.
        """
        status: dict[str, Any] = {
            "cloud_connected": self._cloud_pool is not None,
            "local_connected": self._local_pool is not None,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

        if self._cloud_pool:
            try:
                async with self._cloud_pool.acquire() as cloud:
                    row = await cloud.fetchrow(
                        """
                        SELECT
                            (SELECT COUNT(*) FROM posts)       AS posts,
                            (SELECT COUNT(*) FROM categories)  AS categories,
                            (SELECT COUNT(*) FROM tags)        AS tags
                        """,
                    )
                    status["cloud"] = dict(row) if row else {}
            except Exception as exc:
                status["cloud"] = {"error": str(exc)}

        if self._local_pool:
            try:
                async with self._local_pool.acquire() as local:
                    row = await local.fetchrow(
                        """
                        SELECT
                            (SELECT COUNT(*) FROM posts)                             AS posts_total,
                            (SELECT COUNT(*) FROM posts WHERE status = 'published')  AS posts_published,
                            (SELECT COUNT(*) FROM categories)                        AS categories,
                            (SELECT COUNT(*) FROM tags)                              AS tags
                        """,
                    )
                    status["local"] = dict(row) if row else {}

                    # Latest sync_metrics entry
                    sync_row = await local.fetchrow(
                        """
                        SELECT metric_name, metric_value, synced_at
                        FROM sync_metrics
                        ORDER BY synced_at DESC LIMIT 1
                        """,
                    )
                    if sync_row:
                        status["last_metric_sync"] = {
                            "metric": sync_row["metric_name"],
                            "synced_at": str(sync_row["synced_at"]),
                        }
            except Exception as exc:
                status["local"] = {"error": str(exc)}

        return status
