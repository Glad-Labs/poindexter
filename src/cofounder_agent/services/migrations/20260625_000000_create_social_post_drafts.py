"""Create social_post_drafts table for Postiz social distribution."""
from __future__ import annotations

from typing import Any


async def up(pool: Any) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS social_post_drafts (
                id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                pipeline_task_id TEXT        NOT NULL REFERENCES pipeline_tasks(task_id),
                post_id          UUID        REFERENCES posts(id),
                platform         TEXT        NOT NULL,
                content          TEXT        NOT NULL,
                platform_config  JSONB       NOT NULL DEFAULT '{}',
                status           TEXT        NOT NULL DEFAULT 'pending',
                postiz_post_id   TEXT,
                error            TEXT,
                retry_count      INT         NOT NULL DEFAULT 0,
                last_retry_at    TIMESTAMPTZ,
                created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
                approved_at      TIMESTAMPTZ,
                posted_at        TIMESTAMPTZ
            );
            CREATE INDEX IF NOT EXISTS idx_social_post_drafts_pipeline_task_id
                ON social_post_drafts (pipeline_task_id);
            CREATE INDEX IF NOT EXISTS idx_social_post_drafts_post_id
                ON social_post_drafts (post_id);
            CREATE INDEX IF NOT EXISTS idx_social_post_drafts_status
                ON social_post_drafts (status);
            """
        )
