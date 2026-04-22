"""AnalyzeTopicGapsJob — flag empty / low-coverage / stale categories.

Replaces ``IdleWorker._analyze_topic_gaps``. Runs every 24h by default.
Produces three classes of findings:

- **Empty categories**: defined in ``categories`` but zero published
  posts. Usually means a category was added for future work that never
  materialized.
- **Low coverage**: categories with 1–(``low_threshold``-1) posts.
  Candidate for targeted topic-discovery cycles.
- **Stale categories**: categories whose latest published post is
  older than ``stale_days`` days. Signals the topic has gone cold in
  the content pipeline.

Any finding triggers a dedup'd Gitea issue so an operator can adjust
the topic-discovery bias.

## Config (``plugin.job.analyze_topic_gaps``)

- ``config.low_threshold`` (default 5) — "low coverage" band upper
  bound
- ``config.stale_days`` (default 14) — category is stale if no post
  newer than this
- ``config.file_gitea_issue`` (default true)
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from utils.gitea_issues import create_gitea_issue

logger = logging.getLogger(__name__)


class AnalyzeTopicGapsJob:
    name = "analyze_topic_gaps"
    description = "Flag empty, low-coverage, and stale categories for topic-discovery rebalancing"
    schedule = "every 24 hours"
    idempotent = True  # Read-only analysis

    async def run(
        self, pool: Any, config: dict[str, Any], *, site_config: Any = None,
    ) -> JobResult:
        low_threshold = int(config.get("low_threshold", 5))
        stale_days = int(config.get("stale_days", 14))
        file_issue = bool(config.get("file_gitea_issue", True))

        try:
            async with pool.acquire() as conn:
                categories = await conn.fetch(
                    """
                    SELECT c.name, COUNT(p.id) as posts
                    FROM categories c
                    LEFT JOIN posts p ON c.id = p.category_id
                      AND p.status = 'published'
                    GROUP BY c.name
                    ORDER BY posts ASC
                    """,
                )
                stale = await conn.fetch(
                    """
                    SELECT c.name, MAX(p.published_at) as latest
                    FROM categories c
                    JOIN posts p ON c.id = p.category_id
                      AND p.status = 'published'
                    GROUP BY c.name
                    HAVING MAX(p.published_at) < NOW() - INTERVAL '1 day' * $1
                    """,
                    stale_days,
                )
        except Exception as e:
            logger.exception("AnalyzeTopicGapsJob: query failed: %s", e)
            return JobResult(ok=False, detail=f"query failed: {e}", changes_made=0)

        empty = [r["name"] for r in categories if r["posts"] == 0]
        low = [
            f"{r['name']} ({r['posts']})"
            for r in categories
            if 0 < r["posts"] < low_threshold
        ]
        stale_names = [r["name"] for r in stale]

        suggestions: list[str] = []
        if empty:
            suggestions.append(
                f"Empty categories need posts: {', '.join(empty)}",
            )
        if low:
            suggestions.append(f"Low coverage: {', '.join(low)}")
        if stale_names:
            suggestions.append(
                f"Stale categories (no post in {stale_days}d): {', '.join(stale_names)}",
            )

        if suggestions and file_issue:
            body = "## Topic Gap Analysis\n\n" + "\n".join(f"- {s}" for s in suggestions)
            # Phase H (GH#95): transitional singleton import at the call
            # site — this Job hasn't yet migrated its ``run()`` signature
            # to accept ``site_config``. When it does, pass self's bound
            # instance instead.
            await create_gitea_issue(
                f"content: topic gaps — {len(empty)} empty, "
                f"{len(low)} low, {len(stale_names)} stale",
                body,
                site_config=site_config,
            )

        detail = (
            f"{len(empty)} empty, {len(low)} low, {len(stale_names)} stale"
            if suggestions else "all categories healthy"
        )
        logger.info("AnalyzeTopicGapsJob: %s", detail)
        return JobResult(
            ok=True,
            detail=detail,
            changes_made=len(suggestions),
            metrics={
                "empty_categories": len(empty),
                "low_coverage_categories": len(low),
                "stale_categories": len(stale_names),
            },
        )
