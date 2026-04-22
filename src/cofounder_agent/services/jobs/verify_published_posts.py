"""VerifyPublishedPostsJob — check recently published posts return HTTP 200.

Replaces ``IdleWorker._verify_published_posts``. Runs every 30 minutes
by default (matches the legacy cadence). Fetches posts published in the
last N hours, GETs each one on the live site, and records any non-200
response to ``audit_log`` as a ``publish_verify_failed`` event.

This is the canary that catches:
- Static export publishing to the wrong bucket
- Revalidation webhook failures leaving stale 404 pages
- Post published but slug collision makes it unreachable
- CDN caching issues after slug changes

## Config (``plugin.job.verify_published_posts``)

- ``config.window_hours`` (default 24) — only verify posts younger
  than this
- ``config.batch_size`` (default 20) — cap per run (the more recent,
  the more they matter)
- ``config.file_gitea_issue`` (default true) — file one dedup'd
  issue per cycle if any failed
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from plugins.job import JobResult
from utils.gitea_issues import create_gitea_issue

logger = logging.getLogger(__name__)


class VerifyPublishedPostsJob:
    name = "verify_published_posts"
    description = "GET recently-published posts and alert on non-200 responses"
    schedule = "every 30 minutes"
    idempotent = True  # Read-only on the live site

    async def run(
        self, pool: Any, config: dict[str, Any], *, site_config: Any,
    ) -> JobResult:
        window_hours = int(config.get("window_hours", 24))
        batch_size = int(config.get("batch_size", 20))
        file_issue = bool(config.get("file_gitea_issue", True))

        site_url = site_config.get("site_url", "").rstrip("/")
        if not site_url:
            return JobResult(
                ok=False,
                detail="site_url not configured in app_settings",
                changes_made=0,
            )

        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT id, title, slug FROM posts "
                    "WHERE status = 'published' "
                    "  AND published_at > NOW() - make_interval(hours => $1) "
                    "ORDER BY published_at DESC LIMIT $2",
                    window_hours, batch_size,
                )
        except Exception as e:
            logger.exception("VerifyPublishedPostsJob: fetch failed: %s", e)
            return JobResult(ok=False, detail=f"fetch failed: {e}", changes_made=0)

        if not rows:
            return JobResult(
                ok=True,
                detail=f"no posts published in last {window_hours}h",
                changes_made=0,
            )

        verified = 0
        failures: list[dict[str, Any]] = []

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=3.0),
            follow_redirects=True,
        ) as client:
            for row in rows:
                url = f"{site_url}/posts/{row['slug']}"
                try:
                    resp = await client.get(url, timeout=10)
                    if resp.status_code == 200:
                        verified += 1
                    else:
                        failures.append({
                            "slug": row["slug"],
                            "title": (row["title"] or "")[:50],
                            "status": resp.status_code,
                        })
                except Exception as e:
                    failures.append({
                        "slug": row["slug"],
                        "title": (row["title"] or "")[:50],
                        "status": f"error: {e}",
                    })

        # Record each failure to audit_log (best-effort).
        for f in failures:
            try:
                async with pool.acquire() as conn:
                    await conn.execute(
                        "INSERT INTO audit_log (event_type, source, details, severity) "
                        "VALUES ($1, $2, $3, $4)",
                        "publish_verify_failed",
                        "verify_published_posts_job",
                        json.dumps(f),
                        "warning",
                    )
            except Exception as e:
                logger.debug(
                    "VerifyPublishedPostsJob: audit_log insert failed for %s: %s",
                    f.get("slug"), e,
                )

        if failures and file_issue:
            body_lines = [
                f"- `/posts/{f['slug']}` ({f['title']}) → {f['status']}"
                for f in failures[:10]
            ]
            await create_gitea_issue(
                f"publish-verify: {len(failures)}/{len(rows)} recent posts not reachable",
                "## Failed to verify\n\n" + "\n".join(body_lines),
            )

        detail = (
            f"checked {len(rows)} recently-published post(s), "
            f"{verified} ok, {len(failures)} failed"
        )
        logger.info("VerifyPublishedPostsJob: %s", detail)
        return JobResult(
            # Non-200s are content-pipeline findings, not a job failure.
            ok=True,
            detail=detail,
            changes_made=len(failures),
            metrics={
                "posts_checked": len(rows),
                "posts_verified": verified,
                "posts_failed": len(failures),
            },
        )
