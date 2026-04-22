"""FlagMissingSeoJob — surface published posts without seo_title/description.

Replaces ``IdleWorker._fix_missing_seo`` (the name was aspirational —
the original method only *flagged* posts, it never filled anything in).

Runs every 12 hours by default. Queries up to N published posts where
``seo_title`` or ``seo_description`` is NULL/empty, files a dedup'd
Gitea issue listing them, and returns the count in JobResult.

Config (``plugin.job.flag_missing_seo``):
- ``config.limit`` (default 10) — max posts to report per run
- ``config.file_gitea_issue`` (default true)
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from utils.gitea_issues import create_gitea_issue

logger = logging.getLogger(__name__)


class FlagMissingSeoJob:
    name = "flag_missing_seo"
    description = "Flag published posts missing SEO title or description"
    schedule = "every 12 hours"
    idempotent = True  # Read-only — no writes to posts

    async def run(
        self, pool: Any, config: dict[str, Any], *, site_config: Any = None,
    ) -> JobResult:
        limit = int(config.get("limit", 10))
        file_issue = bool(config.get("file_gitea_issue", True))

        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, title FROM posts
                    WHERE status = 'published'
                      AND (seo_title IS NULL OR seo_title = ''
                           OR seo_description IS NULL OR seo_description = '')
                    LIMIT $1
                    """,
                    limit,
                )
        except Exception as e:
            logger.exception("FlagMissingSeoJob: query failed: %s", e)
            return JobResult(ok=False, detail=f"query failed: {e}", changes_made=0)

        if not rows:
            return JobResult(
                ok=True,
                detail="all published posts have SEO metadata",
                changes_made=0,
            )

        titles = [(r["title"] or "")[:40] for r in rows]
        if file_issue:
            body = "## Posts Missing SEO\n\n" + "\n".join(f"- {t}" for t in titles)
            await create_gitea_issue(
                f"seo: {len(rows)} posts missing SEO title or description",
                body,
                site_config=site_config,
            )

        detail = f"found {len(rows)} post(s) with missing SEO metadata"
        logger.info("FlagMissingSeoJob: %s", detail)
        return JobResult(
            ok=True,
            detail=detail,
            changes_made=len(rows),
            metrics={"posts_missing_seo": len(rows)},
        )
