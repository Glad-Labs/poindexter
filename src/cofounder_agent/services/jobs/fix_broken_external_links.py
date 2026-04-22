"""FixBrokenExternalLinksJob — strip external links returning 404.

Replaces ``IdleWorker._fix_broken_external_links``. Samples N published
posts per run, extracts external URLs from the content (markdown + HTML
anchor forms), HEAD/GET-checks each one, and removes the anchor (keeping
link text) for any URL returning 404 or unreachable.

Skips domains that match the site itself (internal) and known-noisy
domains like ``pexels`` / ``cloudinary`` that serve legitimate
expiring-CDN 404s.

Unlike ``CheckPublishedLinksJob``, this one *rewrites* posts — broken
URLs removed, not just reported. A Gitea issue is still filed when
cleanups happen so the operator has an audit trail.

Config (``plugin.job.fix_broken_external_links``):
- ``config.sample_size`` (default 5)
- ``config.urls_per_post`` (default 10)
- ``config.skip_domains`` (default ("pexels", "cloudinary")) — URL
  substrings whose links are always skipped
- ``config.file_gitea_issue`` (default true)
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from plugins.job import JobResult
from utils.gitea_issues import create_gitea_issue

logger = logging.getLogger(__name__)

_MD_URL_RE = re.compile(r"\]\((https?://[^\s\)]+)\)")
_HTML_URL_RE = re.compile(r'href="(https?://[^"]+)"')
_DEFAULT_SKIP_DOMAINS: tuple[str, ...] = ("pexels", "cloudinary")


def _extract_external_urls(
    content: str,
    site_domain: str,
    skip_domains: tuple[str, ...],
) -> set[str]:
    """Pull external URLs from content, minus internal + skip-domain entries.

    Pulled out of Job.run for unit-testability — the URL extraction +
    filter logic is worth pinning down in isolation.
    """
    md_urls = _MD_URL_RE.findall(content)
    html_urls = _HTML_URL_RE.findall(content)
    return {
        u.rstrip(".,;:)")
        for u in md_urls + html_urls
        if site_domain not in u and not any(s in u for s in skip_domains)
    }


def _strip_url_from_content(content: str, url: str) -> str:
    """Remove markdown + HTML anchor forms of ``url`` from content.

    Link text is preserved; only the anchor wrapper is stripped so the
    narrative stays readable.
    """
    esc = re.escape(url)
    content = re.sub(r"\[([^\]]+)\]\(" + esc + r"\)", r"\1", content)
    content = re.sub(
        r'<a[^>]*href="' + esc + r'"[^>]*>([^<]*)</a>',
        r"\1",
        content,
    )
    return content


class FixBrokenExternalLinksJob:
    name = "fix_broken_external_links"
    description = "Strip external URLs that return 404 / unreachable from published posts"
    schedule = "every 24 hours"
    idempotent = True  # Re-running is safe: removed links won't be re-checked

    async def run(
        self, pool: Any, config: dict[str, Any], *, site_config: Any,
    ) -> JobResult:
        sample_size = int(config.get("sample_size", 5))
        urls_per_post = int(config.get("urls_per_post", 10))
        file_issue = bool(config.get("file_gitea_issue", True))
        skip_domains_cfg = config.get("skip_domains")
        if skip_domains_cfg is None:
            skip_domains = _DEFAULT_SKIP_DOMAINS
        else:
            skip_domains = tuple(str(s) for s in skip_domains_cfg)

        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT id, title, content FROM posts "
                    "WHERE status = 'published' AND content LIKE '%http%' "
                    "ORDER BY RANDOM() LIMIT $1",
                    sample_size,
                )
        except Exception as e:
            logger.exception("FixBrokenExternalLinksJob: fetch failed: %s", e)
            return JobResult(ok=False, detail=f"fetch failed: {e}", changes_made=0)

        if not rows:
            return JobResult(
                ok=True,
                detail="no published posts with http links",
                changes_made=0,
            )

        site_domain = site_config.get("site_domain", "localhost")
        broken_total = 0
        posts_fixed = 0
        urls_checked = 0

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(8.0, connect=3.0),
            follow_redirects=True,
        ) as client:
            for row in rows:
                content = row["content"] or ""
                urls = _extract_external_urls(content, site_domain, skip_domains)
                broken: set[str] = set()
                for url in list(urls)[:urls_per_post]:
                    urls_checked += 1
                    try:
                        resp = await client.get(
                            url,
                            headers={"User-Agent": "Mozilla/5.0"},
                            timeout=8,
                        )
                        if resp.status_code == 404:
                            broken.add(url)
                    except Exception:
                        # Network/DNS/TLS errors count as broken — conservative
                        # but keeps the auto-cleanup bounded to actual dead
                        # links in practice (seen in legacy idle_worker).
                        broken.add(url)

                if broken:
                    new_content = content
                    for url in broken:
                        new_content = _strip_url_from_content(new_content, url)
                    try:
                        async with pool.acquire() as conn:
                            await conn.execute(
                                "UPDATE posts SET content = $1, updated_at = NOW() "
                                "WHERE id = $2",
                                new_content, row["id"],
                            )
                        broken_total += len(broken)
                        posts_fixed += 1
                    except Exception as e:
                        logger.warning(
                            "FixBrokenExternalLinksJob: update failed for %s: %s",
                            row.get("id"), e,
                        )

        if posts_fixed and file_issue:
            await create_gitea_issue(
                f"links: removed {broken_total} broken external URLs from {posts_fixed} posts",
                "Auto-cleaned 404 / unreachable external links. "
                "Link text preserved; anchors stripped.",
            )

        detail = (
            f"sampled {len(rows)} post(s), checked {urls_checked} URL(s), "
            f"rewrote {posts_fixed}"
        )
        logger.info("FixBrokenExternalLinksJob: %s", detail)
        return JobResult(
            ok=True,
            detail=detail,
            changes_made=posts_fixed,
            metrics={
                "posts_scanned": len(rows),
                "urls_checked": urls_checked,
                "urls_removed": broken_total,
                "posts_rewritten": posts_fixed,
            },
        )
