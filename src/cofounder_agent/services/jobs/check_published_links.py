"""CheckPublishedLinksJob — sample published posts for broken URLs.

Replaces ``IdleWorker._check_published_links``. Runs every 6 hours by
default, picks N random published posts, extracts outbound URLs from
their content, and HEAD-checks each. Genuinely broken URLs (404/410,
5xx, unreachable) are returned in the JobResult and optionally filed as
a deduplicated finding.

## Access-restricted ≠ broken

A probe sent with the default library UA (``python-httpx/x.y``) is
fast-path-rejected by many WAFs — Wikipedia 403s it outright while
serving a real browser 200. So this job:

1. Sends the shared crawler ``User-Agent`` (``utils.crawler_ua``,
   ``+app_settings.crawler_contact_url``) — the actual fix for the
   Wikipedia/Cloudflare 403 false positives.
2. Treats ``401/403/429`` (auth-gate / bot-block / rate-limit) as
   *access-restricted*, not broken — the link works for a human reader,
   so it's counted separately and never filed. Mirrors the
   ``is_edge_challenge`` skip (Cloudflare managed challenge). The set is
   operator-tunable via ``app_settings.link_check_skip_status_codes``.
3. Retries a ``405 Method Not Allowed`` with GET — some servers reject
   HEAD but serve the resource fine (same fallback as ``citation_verifier``).

Config (``plugin.job.check_published_links``):
- ``config.sample_size`` (default 3) — how many posts to check per run
- ``config.urls_per_post`` (default 10) — cap to keep a single post
  with a huge link dump from eating the whole budget
- ``config.file_gitea_issue`` (default true) — if false, we return the
  finding but don't file anything

App settings (read via the ``_site_config`` DI seam):
- ``crawler_contact_url`` — operator contact URL embedded in the UA
- ``link_check_skip_status_codes`` — CSV of HTTP codes to treat as
  access-restricted (default ``401,403,429``)
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from plugins.job import JobResult
from utils.crawler_ua import build_crawler_ua
from utils.edge_challenge import is_edge_challenge
from utils.findings import emit_finding

logger = logging.getLogger(__name__)

_URL_PATTERN = re.compile(r'https?://[^\s\)"<>]+')

# 4xx codes meaning "the destination is up but refuses our automated /
# anonymous request" — a bot-block (403), auth-gate (401), or rate-limit
# (429). Not a broken link for a human reader. Genuinely-dead links still
# surface as 404/410/5xx/unreachable. Operator-overridable via
# ``app_settings.link_check_skip_status_codes``.
_DEFAULT_ACCESS_RESTRICTED_CODES: frozenset[int] = frozenset({401, 403, 429})


def _parse_status_codes(csv: str) -> frozenset[int]:
    """Parse a CSV of HTTP status codes; skip blanks/non-ints (logged)."""
    out: set[int] = set()
    for part in (csv or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(int(part))
        except ValueError:
            logger.warning(
                "check_published_links: ignoring non-int skip code %r", part
            )
    return frozenset(out)


class CheckPublishedLinksJob:
    name = "check_published_links"
    description = "Sample published posts and HEAD-check external links for 4xx/5xx/unreachable"
    schedule = "every 6 hours"
    idempotent = True  # HEAD requests are read-only

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
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

        # DI seam (glad-labs-stack#330): scheduler seeds `_site_config`
        # into the config dict at fire time. Tests/standalone callers
        # without a SiteConfig fall back to safe defaults.
        sc = config.get("_site_config")
        site_domain = sc.get("site_domain", "localhost") if sc is not None else "localhost"
        # Send a real browser-ish UA — the default httpx UA is 403'd by many
        # WAFs (Wikipedia), the root cause of broken-link false positives. The
        # shared helper folds in crawler_contact_url with the OSS leak guard.
        user_agent = build_crawler_ua(sc, product="PoindexterLinkCheck")
        skip_csv = sc.get("link_check_skip_status_codes", "") if sc is not None else ""
        access_restricted_codes = (
            _parse_status_codes(skip_csv) or _DEFAULT_ACCESS_RESTRICTED_CODES
        )

        broken: list[dict[str, Any]] = []
        edge_challenged = 0
        access_restricted = 0
        checked = 0
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=3.0),
            follow_redirects=True,
            headers={"User-Agent": user_agent},
        ) as client:
            for row in rows:
                urls = _URL_PATTERN.findall(row["content"] or "")
                for url in urls[:urls_per_post]:
                    if site_domain in url:
                        continue  # internal links don't count
                    checked += 1
                    try:
                        resp = await client.head(url, timeout=8)
                        # Some servers reject HEAD (405) but serve GET fine —
                        # retry once with a lightweight GET before judging.
                        if resp.status_code == 405:
                            resp = await client.get(url, timeout=8)
                        if resp.status_code >= 400:
                            if is_edge_challenge(resp):
                                # The destination's CDN (Cloudflare) challenged
                                # our automated request — the link works for real
                                # users, so it's NOT broken. Skip it (don't file).
                                edge_challenged += 1
                                continue
                            if resp.status_code in access_restricted_codes:
                                # 401/403/429 — the host is up but refuses our
                                # automated/anonymous request (bot-block, auth-gate,
                                # rate-limit). Fine for a human reader; not broken.
                                access_restricted += 1
                                continue
                            broken.append({
                                "post": (row["title"] or "")[:40],
                                "url": url,
                                "status": resp.status_code,
                            })
                    except Exception:
                        # HEAD/GET can fail for many benign reasons (server
                        # hostile to the method, TLS hiccup). Still record it so
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
            emit_finding(
                source="check_published_links",
                kind="broken_link",
                severity="warn",
                title=f"links: {len(broken)} broken URLs in published posts",
                body=body,
                dedup_key="broken_links",
                extra={"broken_count": len(broken), "checked_count": checked},
            )

        detail = (
            f"checked {checked} URL(s) across {len(rows)} post(s), "
            f"{len(broken)} broken, {edge_challenged} edge-challenged, "
            f"{access_restricted} access-restricted (skipped)"
        )
        logger.info("CheckPublishedLinksJob: %s", detail)
        return JobResult(
            ok=True,
            detail=detail,
            changes_made=len(broken),
            metrics={
                "urls_checked": checked,
                "urls_broken": len(broken),
                "urls_edge_challenged": edge_challenged,
                "urls_access_restricted": access_restricted,
            },
        )
