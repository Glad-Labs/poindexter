"""CrosspostToDevtoJob — syndicate published posts to Dev.to.

Replaces ``IdleWorker._crosspost_to_devto``. Runs every 4 hours by
default. Finds published posts without a ``metadata->>'devto_url'``,
cross-posts each via ``DevToCrossPostService``, and stores the
returned Dev.to URL back on the post's metadata so we don't re-post.

## Config (``plugin.job.crosspost_to_devto``)

- ``config.batch_size`` (default 3) — posts to cross-post per run
- ``config.file_gitea_issue`` (default false) — whether to file a
  dedup'd issue if any post errored. Default false because Dev.to
  errors are usually transient (rate limits, 5xx) and the job
  retries on its own cadence.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from utils.gitea_issues import create_gitea_issue

logger = logging.getLogger(__name__)


class CrosspostToDevtoJob:
    name = "crosspost_to_devto"
    description = "Syndicate published posts to Dev.to (dedup via metadata.devto_url)"
    schedule = "every 4 hours"
    idempotent = True  # dedup on metadata.devto_url — safe to retry

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        from services.devto_service import DevToCrossPostService
        # Jobs don't have access to the lifespan-bound site_config yet —
        # Phase H step 3 wires site_config through PluginScheduler so jobs
        # get it alongside the pool. Until then, fall back to the module
        # singleton at the call site.
        from services.site_config import site_config as _sc

        batch_size = int(config.get("batch_size", 3))
        file_issue = bool(config.get("file_gitea_issue", False))

        svc = DevToCrossPostService(pool, _sc)

        # Early-out before we touch the DB: if there's no API key there's
        # nothing we can do.
        try:
            api_key = await svc._get_api_key()
        except Exception as e:
            logger.exception("CrosspostToDevtoJob: api key lookup failed: %s", e)
            return JobResult(
                ok=False, detail=f"api key lookup failed: {e}", changes_made=0,
            )

        if not api_key:
            return JobResult(
                ok=True,
                detail="devto_api_key not configured — skipping",
                changes_made=0,
            )

        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, title, slug
                    FROM posts
                    WHERE status = 'published'
                      AND (metadata IS NULL
                           OR metadata->>'devto_url' IS NULL
                           OR metadata->>'devto_url' = '')
                    ORDER BY published_at DESC
                    LIMIT $1
                    """,
                    batch_size,
                )
        except Exception as e:
            logger.exception("CrosspostToDevtoJob: candidate fetch failed: %s", e)
            return JobResult(ok=False, detail=f"fetch failed: {e}", changes_made=0)

        if not rows:
            return JobResult(
                ok=True,
                detail="all published posts already on Dev.to",
                changes_made=0,
            )

        crossposted = 0
        errors: list[str] = []
        for row in rows:
            post_id = str(row["id"])
            try:
                devto_url = await svc.cross_post_by_post_id(post_id)
                if devto_url:
                    crossposted += 1
                    logger.info(
                        "CrosspostToDevtoJob: %s → %s", row["slug"], devto_url,
                    )
                else:
                    errors.append(f"{row['slug']}: no URL returned")
            except Exception as e:
                errors.append(f"{row['slug']}: {e}")

        if errors and file_issue:
            body = (
                "## Dev.to cross-post errors\n\n"
                + "\n".join(f"- {e}" for e in errors[:10])
            )
            await create_gitea_issue(
                f"devto: {len(errors)} cross-post errors of {len(rows)} attempts",
                body,
            )

        detail = (
            f"attempted {len(rows)}, crossposted {crossposted}, "
            f"{len(errors)} errors"
        )
        logger.info("CrosspostToDevtoJob: %s", detail)
        return JobResult(
            ok=True,
            detail=detail,
            changes_made=crossposted,
            metrics={
                "posts_attempted": len(rows),
                "posts_crossposted": crossposted,
                "errors": len(errors),
            },
        )
