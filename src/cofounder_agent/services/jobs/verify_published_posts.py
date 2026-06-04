"""VerifyPublishedPostsJob — check recently published posts return HTTP 200.

Replaces ``IdleWorker._verify_published_posts``. Runs every 30 minutes
by default (matches the legacy cadence). Fetches posts published in the
last N hours, GETs each one on the live site, and records any genuine
non-200 response to ``audit_log`` as a ``publish_verify_failed`` event.

This is the canary that catches:
- Static export publishing to the wrong bucket
- Revalidation webhook failures leaving stale 404 pages
- Post published but slug collision makes it unreachable
- CDN caching issues after slug changes

## Edge-challenge ≠ content outage

If the edge (Cloudflare) answers with a bot *challenge* — HTTP 403/429/503
carrying a ``cf-mitigated`` header — the post is NOT unreachable to real
readers; the edge blocked our automated request (a browser solves the
challenge and gets 200). Those responses are recorded as a distinct,
lower-severity ``publish_verify_edge_blocked`` audit event plus a
``warning`` ``verify_blocked_by_edge`` finding (operator should check bot
settings / allowlist the monitor), NOT the ``critical``
``post_verification_failure`` page reserved for genuine 404/5xx outages.

This is defense-in-depth: on 2026-06-04, manually-enabled Cloudflare Bot
Fight Mode 403-challenged this canary (and `POST /api/revalidate`) from the
worker's egress IP, paging ``critical`` every 30 min on a false positive
while real readers saw 200. The challenge is IP-reputation + UA based, so a
UA swap does NOT reliably bypass it — the correct fix is at the edge
(disable BFM / Super-BFM verified-bots allow / IP allowlist). This guard
just stops the monitor from mistaking that for a content outage.

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
from utils.edge_challenge import is_edge_challenge
from utils.findings import emit_finding

logger = logging.getLogger(__name__)


class VerifyPublishedPostsJob:
    name = "verify_published_posts"
    description = "GET recently-published posts and alert on non-200 responses"
    schedule = "every 30 minutes"
    idempotent = True  # Read-only on the live site

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        window_hours = int(config.get("window_hours", 24))
        batch_size = int(config.get("batch_size", 20))
        file_issue = bool(config.get("file_gitea_issue", True))

        # DI seam (glad-labs-stack#330)
        sc = config.get("_site_config")
        site_url = (sc.get("site_url", "") if sc is not None else "").rstrip("/")
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
        edge_blocked: list[dict[str, Any]] = []

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
                    elif is_edge_challenge(resp):
                        # CDN bot-challenge — NOT a content outage. Real
                        # readers still reach the post; only our automated
                        # request was blocked. Tracked separately so it
                        # never feeds the critical "not reachable" page.
                        edge_blocked.append({
                            "slug": row["slug"],
                            "title": (row["title"] or "")[:50],
                            "status": resp.status_code,
                            "reason": "edge_challenge",
                        })
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

        # Record genuine failures to audit_log (best-effort).
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

        # Record edge-challenges under a DISTINCT event type so they never
        # feed the critical post_verification_failure path / Discord page.
        for f in edge_blocked:
            try:
                async with pool.acquire() as conn:
                    await conn.execute(
                        "INSERT INTO audit_log (event_type, source, details, severity) "
                        "VALUES ($1, $2, $3, $4)",
                        "publish_verify_edge_blocked",
                        "verify_published_posts_job",
                        json.dumps(f),
                        "warning",
                    )
            except Exception as e:
                logger.debug(
                    "VerifyPublishedPostsJob: edge audit_log insert failed for %s: %s",
                    f.get("slug"), e,
                )

        if failures and file_issue:
            body_lines = [
                f"- `/posts/{f['slug']}` ({f['title']}) → {f['status']}"
                for f in failures[:10]
            ]
            emit_finding(
                source="verify_published_posts",
                kind="post_verification_failure",
                severity="critical",
                title=f"publish-verify: {len(failures)}/{len(rows)} recent posts not reachable",
                body="## Failed to verify\n\n" + "\n".join(body_lines),
                dedup_key="post_verification_failures",
                extra={"failure_count": len(failures), "checked_count": len(rows)},
            )

        if edge_blocked and file_issue:
            edge_lines = [
                f"- `/posts/{f['slug']}` ({f['title']}) → {f['status']}"
                for f in edge_blocked[:10]
            ]
            emit_finding(
                source="verify_published_posts",
                kind="verify_blocked_by_edge",
                severity="warning",
                title=(
                    f"publish-verify: CDN challenged {len(edge_blocked)}/{len(rows)} "
                    "monitor request(s)"
                ),
                body=(
                    "## Monitor challenged by the CDN — not a content outage\n\n"
                    "Cloudflare returned a bot **managed challenge** (HTTP "
                    "403/429/503 with `cf-mitigated`) to this monitor's request. "
                    "Real readers (browsers) still reach these posts — only the "
                    "automated check was blocked.\n\n"
                    + "\n".join(edge_lines)
                    + "\n\n## Remediation\n"
                    "1. Allowlist this monitor at Cloudflare (skip Bot Fight Mode / "
                    "managed challenge for UA `GladLabsMonitor`), **or**\n"
                    "2. **Security → Bots → Verified bots: Allow** if a bot rule was "
                    "recently enabled, **or**\n"
                    "3. Override the UA via "
                    "`app_settings.verify_published_posts_user_agent`.\n\n"
                    "⚠️ If real crawlers (Googlebot/Bingbot) hit the same UA-based "
                    "challenge, search indexing is at risk — check Cloudflare "
                    "**Security → Events** for verified-bot challenges."
                ),
                dedup_key="verify_blocked_by_edge",
                extra={
                    "edge_blocked_count": len(edge_blocked),
                    "checked_count": len(rows),
                },
            )

        detail = (
            f"checked {len(rows)} recently-published post(s), "
            f"{verified} ok, {len(failures)} failed, "
            f"{len(edge_blocked)} edge-challenged"
        )
        logger.info("VerifyPublishedPostsJob: %s", detail)
        return JobResult(
            # Non-200s are content-pipeline findings, not a job failure.
            ok=True,
            detail=detail,
            changes_made=len(failures) + len(edge_blocked),
            metrics={
                "posts_checked": len(rows),
                "posts_verified": verified,
                "posts_failed": len(failures),
                "posts_edge_blocked": len(edge_blocked),
            },
        )
