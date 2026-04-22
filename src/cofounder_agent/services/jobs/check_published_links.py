"""CheckPublishedLinksJob — sample published posts for broken URLs.

Replaces ``IdleWorker._check_published_links``. Runs every 6 hours by
default, picks N random published posts, extracts outbound URLs from
their content, and HEAD-checks each. Broken URLs (4xx/5xx, unreachable)
are returned in the JobResult and optionally filed as a deduplicated
Gitea issue.

Config (``plugin.job.check_published_links``):
- ``config.sample_size`` (default 3) — how many posts to check per run
- ``config.urls_per_post`` (default 10) — cap to keep a single post
  with a huge link dump from eating the whole budget
- ``config.file_gitea_issue`` (default true) — if false, we return the
  finding but don't file anything
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from plugins.job import JobResult
from utils.gitea_issues import create_gitea_issue

logger = logging.getLogger(__name__)

_URL_PATTERN = re.compile(r'https?://[^\s\)"<>]+')


class CheckPublishedLinksJob:
    name = "check_published_links"
    description = "Sample published posts and HEAD-check external links for 4xx/5xx/unreachable"
    schedule = "every 6 hours"
    idempotent = True  # HEAD requests are read-only

    async def run(
        self, pool: Any, config: dict[str, Any], *, site_config: Any,
    ) -> JobResult:
        sample_size = int(config.get("sample_size", 3))
        urls_per_post = int(config.get("urls_per_post", 10))
        file_issue = bool(config.get("file_gitea_issue", True))

        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, title, content
                    FROM posts
                    WHERE status = 'published'
                    ORDER BY RANDOM()
                    LIMIT $1
                    """,
                    sample_size,
                )
        except Exception as e:
            logger.exception("CheckPublishedLinksJob: fetch failed: %s", e)
            return JobResult(ok=False, detail=f"fetch failed: {e}", changes_made=0)

        if not rows:
            return JobResult(ok=True, detail="no published posts to check", changes_made=0)

        site_domain = site_config.get("site_domain", "localhost")

        broken: list[dict[str, Any]] = []
        checked = 0
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=3.0),
            follow_redirects=True,
        ) as client:
            for row in rows:
                urls = _URL_PATTERN.findall(row["content"] or "")
                for url in urls[:urls_per_post]:
                    if site_domain in url:
                        continue  # internal links don't count
                    checked += 1
                    try:
                        resp = await client.head(url, timeout=8)
                        if resp.status_code >= 400:
                            broken.append({
                                "post": (row["title"] or "")[:40],
                                "url": url,
                                "status": resp.status_code,
                            })
                    except Exception:
                        # HEAD can fail for many benign reasons (server
                        # hostile to HEAD, TLS hiccup). Still record it so
                        # the finding is actionable.
                        broken.append({
                            "post": (row["title"] or "")[:40],
                            "url": url,
                            "status": "unreachable",
                        })

        if broken and file_issue:
            body = "## Broken Links Found\n\n" + "\n".join(
                f"- [{b['post']}] {b['url']} → {b['status']}" for b in broken[:10]
            )
            await create_gitea_issue(
                f"links: {len(broken)} broken URLs in published posts",
                body,
                site_config=site_config,
            )

        detail = f"checked {checked} URL(s) across {len(rows)} post(s), {len(broken)} broken"
        logger.info("CheckPublishedLinksJob: %s", detail)
        return JobResult(
            ok=True,
            detail=detail,
            changes_made=len(broken),
            metrics={"urls_checked": checked, "urls_broken": len(broken)},
        )
