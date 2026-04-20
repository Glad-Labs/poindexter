"""SyncNewsletterSubscribersJob — pull cloud newsletter_subscribers to local brain DB.

Replaces ``IdleWorker._sync_newsletter_subscribers``. Runs every 30
minutes by default. Watermark-based pull (MAX(updated_at) in local)
from the cloud ``newsletter_subscribers`` table into the local brain
DB, so subscriber queries that power the newsletter service stay
local + fast.

Requires ``DATABASE_URL`` env var to locate the cloud DB (we don't
DI it into the job because the cloud pool may not be the pool the
scheduler has). Absent that, skips cleanly.

## Config (``plugin.job.sync_newsletter_subscribers``)

- ``config.batch_size`` (default 1000) — max rows per pull
"""

from __future__ import annotations

import logging
import os
from typing import Any

from plugins.job import JobResult

logger = logging.getLogger(__name__)


# Kept in one place to keep the UPSERT column order + type map
# obvious; any schema change to newsletter_subscribers only needs an
# edit here.
_LOCAL_TABLE_DDL = """
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
"""

_UPSERT_SQL = """
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
"""


class SyncNewsletterSubscribersJob:
    name = "sync_newsletter_subscribers"
    description = "Pull newsletter_subscribers rows from cloud DB into local brain DB"
    schedule = "every 30 minutes"
    idempotent = True  # Watermark + ON CONFLICT DO UPDATE

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        cloud_url = os.getenv("DATABASE_URL") or ""
        if not cloud_url:
            return JobResult(
                ok=True,
                detail="no DATABASE_URL — skipping",
                changes_made=0,
            )

        batch_size = int(config.get("batch_size", 1000))

        try:
            import asyncpg
        except ImportError:
            return JobResult(
                ok=False,
                detail="asyncpg not available",
                changes_made=0,
            )

        # Ensure local table exists + read watermark.
        try:
            async with pool.acquire() as conn:
                await conn.execute(_LOCAL_TABLE_DDL)
                row = await conn.fetchrow(
                    "SELECT MAX(updated_at) AS max_ts FROM newsletter_subscribers"
                )
                last_ts = row["max_ts"] if row else None
        except Exception as e:
            logger.exception("SyncNewsletterSubscribersJob: local DDL/watermark failed: %s", e)
            return JobResult(ok=False, detail=f"local setup failed: {e}", changes_made=0)

        # Pull from cloud.
        try:
            cloud = await asyncpg.connect(cloud_url)
            try:
                if last_ts:
                    rows = await cloud.fetch(
                        "SELECT * FROM newsletter_subscribers "
                        "WHERE updated_at > $1 ORDER BY updated_at LIMIT $2",
                        last_ts, batch_size,
                    )
                else:
                    rows = await cloud.fetch(
                        "SELECT * FROM newsletter_subscribers "
                        "ORDER BY updated_at LIMIT $1",
                        batch_size,
                    )
            finally:
                await cloud.close()
        except Exception as e:
            logger.exception("SyncNewsletterSubscribersJob: cloud pull failed: %s", e)
            return JobResult(ok=False, detail=f"cloud pull failed: {e}", changes_made=0)

        if not rows:
            logger.debug("SyncNewsletterSubscribersJob: 0 new rows")
            return JobResult(
                ok=True, detail="no new subscribers", changes_made=0,
            )

        # Batch upsert into local DB under a single transaction.
        try:
            async with pool.acquire() as conn, conn.transaction():
                for r in rows:
                    await conn.execute(
                        _UPSERT_SQL,
                        r["id"], r["email"], r.get("first_name"), r.get("last_name"),
                        r.get("company"), r.get("interest_categories"),
                        r.get("subscribed_at"), r.get("ip_address"), r.get("user_agent"),
                        r.get("verified", False), r.get("verification_token"),
                        r.get("verified_at"), r.get("unsubscribed_at"),
                        r.get("unsubscribe_reason"),
                        r["created_at"], r["updated_at"],
                        r.get("marketing_consent", False),
                    )
        except Exception as e:
            logger.exception("SyncNewsletterSubscribersJob: local upsert failed: %s", e)
            return JobResult(ok=False, detail=f"upsert failed: {e}", changes_made=0)

        logger.info("SyncNewsletterSubscribersJob: pulled %d rows", len(rows))
        return JobResult(
            ok=True,
            detail=f"synced {len(rows)} subscriber rows",
            changes_made=len(rows),
            metrics={"rows_synced": len(rows)},
        )
