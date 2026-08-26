"""FlagMissingSeoJob — surface published posts without seo_title/description.

Replaces ``IdleWorker._fix_missing_seo`` (the name was aspirational —
the original method only *flagged* posts, it never filled anything in).

Runs every 12 hours by default. Queries up to N published posts where
``seo_title`` or ``seo_description`` is NULL/empty, files a dedup'd
Gitea issue listing them, and returns the count in JobResult.

Config (``plugin.job.flag_missing_seo``):
- ``config.limit`` (default 10) — max posts to report per run
- ``config.file_gitea_issue`` (default true)
- ``config.excluded_templates`` (default ``[]``) — template slugs to ignore.

**``dev_diary`` was removed from that default 2026-08-09, and the exclusion is
why this job went silent while the problem grew.** It last emitted a
``missing_seo`` finding on 2026-06-05. Not because the backlog cleared — because
by then every remaining offender WAS a dev_diary post, and both this job and
``fix_missing_seo`` were blind to them. Missing descriptions accumulated 5 (May)
→ 13 (Jun) → 15 (Jul) → 6 (Aug) = 39, all dev_diary, while the finding stream
read as "solved". Classic sweeper-vs-backlog trap: judge a sweeper by the
backlog it is supposed to drain, never by its own counter.

The original rationale — build-in-public posts don't need SEO — was already
falsified: they are indexed and in the sitemap (2026-06-02 audit), and as of
poindexter#3156 the pipeline generates their metadata like any other post. The
exclusion was the only thing keeping those 39 broken.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from utils.exception_format import describe_exception
from utils.findings import emit_finding

logger = logging.getLogger(__name__)


class FlagMissingSeoJob:
    name = "flag_missing_seo"
    description = "Flag published posts missing SEO title or description"
    schedule = "every 12 hours"
    idempotent = True  # Read-only — no writes to posts

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        limit = int(config.get("limit", 10))
        file_issue = bool(config.get("file_gitea_issue", True))
        # Default empty on purpose — see the module docstring. Excluding a
        # template here makes this job blind to it AND silences the finding that
        # would have reported the growing backlog.
        excluded_templates: list[str] = list(config.get("excluded_templates", []))

        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT p.id, p.title FROM posts p
                    LEFT JOIN pipeline_tasks pt
                           ON pt.task_id::text = p.metadata->>'pipeline_task_id'
                    WHERE p.status = 'published'
                      AND COALESCE(pt.template_slug, '') != ALL($2::text[])
                      AND (p.seo_title IS NULL OR p.seo_title = ''
                           OR p.seo_description IS NULL OR p.seo_description = '')
                    LIMIT $1
                    """,
                    limit,
                    excluded_templates,
                )
        except Exception as e:
            logger.exception("FlagMissingSeoJob: query failed: %s", e)
            return JobResult(ok=False, detail=f"query failed: {describe_exception(e)}", changes_made=0)

        if not rows:
            return JobResult(
                ok=True,
                detail="all published posts have SEO metadata",
                changes_made=0,
            )

        titles = [(r["title"] or "")[:40] for r in rows]
        if file_issue:
            body = "## Posts Missing SEO\n\n" + "\n".join(f"- {t}" for t in titles)
            emit_finding(
                source="flag_missing_seo",
                kind="missing_seo",
                severity="warn",
                title=f"seo: {len(rows)} posts missing SEO title or description",
                body=body,
                dedup_key="missing_seo",
                extra={"missing_count": len(rows)},
            )

        detail = f"found {len(rows)} post(s) with missing SEO metadata"
        logger.info("FlagMissingSeoJob: %s", detail)
        return JobResult(
            ok=True,
            detail=detail,
            changes_made=len(rows),
            metrics={"posts_missing_seo": len(rows)},
        )
