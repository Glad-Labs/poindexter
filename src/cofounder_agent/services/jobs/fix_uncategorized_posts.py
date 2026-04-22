"""FixUncategorizedPostsJob — assign a default category to NULL-category posts.

Replaces ``IdleWorker._fix_uncategorized_posts``. Runs every 24h by
default. Finds up to N published posts with ``category_id IS NULL``
and assigns them to a configurable default category (``technology``
slug by default).

## Why not just enforce NOT NULL

The category table is seeded, but posts can slip in with NULL
category_id via:
- A direct INSERT that bypasses the app
- A legacy post imported before the migration added the FK
- A race condition where the category is deleted after the post is
  created but before the FK was marked NOT NULL

This job is the cleanup layer — checks periodically, assigns a safe
default, and files a Gitea issue so the operator can manually
reassign if the default isn't appropriate.

Config (``plugin.job.fix_uncategorized_posts``):
- ``config.batch_size`` (default 5) — posts to update per run
- ``config.default_category_slug`` (default ``"technology"``) — slug
  of the category to assign when no better one can be determined
- ``config.file_gitea_issue`` (default true)
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from utils.gitea_issues import create_gitea_issue

logger = logging.getLogger(__name__)


class FixUncategorizedPostsJob:
    name = "fix_uncategorized_posts"
    description = "Assign default category to published posts with category_id IS NULL"
    schedule = "every 24 hours"
    idempotent = True  # Re-running does nothing once all posts are categorized

    async def run(
        self, pool: Any, config: dict[str, Any], *, site_config: Any = None,
    ) -> JobResult:
        batch_size = int(config.get("batch_size", 5))
        default_slug = str(config.get("default_category_slug", "technology"))
        file_issue = bool(config.get("file_gitea_issue", True))

        try:
            async with pool.acquire() as conn:
                posts = await conn.fetch(
                    "SELECT id, title FROM posts "
                    "WHERE status = 'published' AND category_id IS NULL "
                    "LIMIT $1",
                    batch_size,
                )

                if not posts:
                    return JobResult(
                        ok=True,
                        detail="all published posts already categorized",
                        changes_made=0,
                    )

                default_cat = await conn.fetchval(
                    "SELECT id FROM categories WHERE slug = $1",
                    default_slug,
                )
                if default_cat is None:
                    return JobResult(
                        ok=False,
                        detail=(
                            f"default category slug {default_slug!r} not found — "
                            "seed categories table or update default_category_slug"
                        ),
                        changes_made=0,
                    )

                fixed = 0
                for post in posts:
                    try:
                        await conn.execute(
                            "UPDATE posts SET category_id = $1 WHERE id = $2",
                            default_cat, post["id"],
                        )
                        fixed += 1
                    except Exception as e:
                        logger.warning(
                            "FixUncategorizedPostsJob: update failed for %s: %s",
                            post.get("id"), e,
                        )
        except Exception as e:
            logger.exception("FixUncategorizedPostsJob: query failed: %s", e)
            return JobResult(ok=False, detail=f"query failed: {e}", changes_made=0)

        if fixed and file_issue:
            await create_gitea_issue(
                f"content: assigned {default_slug} category to {fixed} uncategorized posts",
                f"Posts defaulted to `{default_slug}` category. "
                "Review and reassign if a different category fits better.",
                site_config=site_config,
            )

        detail = f"assigned {default_slug!r} to {fixed} of {len(posts)} post(s)"
        logger.info("FixUncategorizedPostsJob: %s", detail)
        return JobResult(
            ok=True,
            detail=detail,
            changes_made=fixed,
            metrics={
                "posts_found": len(posts),
                "posts_updated": fixed,
            },
        )
