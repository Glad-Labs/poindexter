"""
Citation verifier — Phase 6 / GH#54.

Every external URL in generated content gets HTTP-HEAD-verified during
QA. Dead links (non-2xx, timeout, DNS failure) are surfaced as issues the
multi-model QA rolls into its final verdict. Optional configurable gates:
the stage can warn-only, or reject when the dead-link ratio crosses a
threshold.

Design choices:
  - Internal links (site_url + /posts/…) are NOT probed here; the writer
    already has a known-slug allowlist and scrub_fabricated_links catches
    hallucinated internal refs. HEAD'ing our own URLs would just create
    a feedback loop in tests.
  - We dedupe before probing — each unique URL is HEAD'd at most once.
  - Max-concurrency cap stops a 50-URL post from fanning out to 50
    simultaneous sockets.
  - Per-URL timeout is short (default 8s) because a DoS'd or dead
    reference shouldn't be the bottleneck for an otherwise-good post.
  - We accept 200-399 as "alive" (many sources 302-redirect to canonical,
    some legit cache-CDN redirect loops; a 4xx is "broken" signal).
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field

import httpx

from services.site_config import SiteConfig
from utils.crawler_ua import build_crawler_ua

logger = logging.getLogger(__name__)


# 2026-05-29 — SiteConfig DI migration (#272 leaf batch 2) converted this
# module from the module-level ``site_config`` singleton + ``set_site_config``
# setter + free ``verify_citations`` function to constructor DI via a
# ``CitationVerifier`` class taking ``site_config`` in ``__init__``. The
# ``_build_citation_headers`` helper (User-Agent built from
# ``crawler_contact_url``) + ``verify_citations`` became instance methods.
# The pure ``extract_urls`` / ``verdict_from_report`` /
# ``append_sources_section`` helpers + the dataclasses stay module-level
# (no SiteConfig dependency). The shared ``http_client`` plumbing
# (``set_http_client`` / module-level ``http_client``) is a SEPARATE
# concern wired by ``services/http_client.py`` at lifespan startup and
# pinned by ``test_http_client.py`` — it stays module-level. The
# composition root (``services/container.py::AppContainer.citation_verifier``)
# wires a ``CitationVerifier``; callers that aren't yet migrated build a
# per-call instance from their own lifespan-bound SiteConfig (caller-bridge
# pattern, ``CitationVerifier(site_config=site_config)``).


# Lifespan-bound shared httpx.AsyncClient — main.py wires this via
# set_http_client() at startup. ``verify_citations`` prefers it when
# wired so the connection pool stays warm across the per-task
# fan-out (5-50 URLs per content task).
http_client: httpx.AsyncClient | None = None


def set_http_client(client: httpx.AsyncClient | None) -> None:
    """Wire the lifespan-bound shared httpx.AsyncClient."""
    global http_client
    http_client = client


# Match markdown link [text](url) — capture url only. We also catch
# bare-URL autolinks <https://...> and plain-paste https://... outside
# markdown link syntax, since the writer occasionally emits either form.
_MD_LINK_RE = re.compile(r"\[[^\]]*?\]\((https?://[^\s)]+)\)")
_AUTOLINK_RE = re.compile(r"<(https?://[^>\s]+)>")
_BARE_URL_RE = re.compile(
    r"(?<![\[\(<\"'])https?://[^\s)\]>\"']+",
)


@dataclass
class CitationIssue:
    url: str
    reason: str  # "dead", "timeout", "dns", "bad_status"
    detail: str
    status_code: int | None = None


@dataclass
class CitationReport:
    total_urls: int
    unique_urls: int
    alive: list[str] = field(default_factory=list)
    dead: list[CitationIssue] = field(default_factory=list)
    dead_ratio: float = 0.0

    def summary(self) -> str:
        if not self.unique_urls:
            return "No external URLs to verify"
        if not self.dead:
            return f"All {self.unique_urls} external URL(s) verified alive"
        return (
            f"{len(self.dead)}/{self.unique_urls} dead "
            f"({self.dead_ratio:.0%}): "
            + ", ".join(
                f"{issue.url} ({issue.reason})"
                for issue in self.dead[:3]
            )
            + ("…" if len(self.dead) > 3 else "")
        )


def extract_urls(content: str, site_url: str | None = None) -> list[str]:
    """Pull every external URL out of ``content``.

    Skips URLs that point at ``site_url`` (internal links don't need
    HEAD verification — the writer's internal-link allowlist handles
    those). Dedupes while preserving first-seen order.
    """
    if not content:
        return []
    urls: list[str] = []
    for pattern in (_MD_LINK_RE, _AUTOLINK_RE, _BARE_URL_RE):
        for m in pattern.finditer(content):
            url = m.group(1) if pattern is not _BARE_URL_RE else m.group(0)
            url = url.rstrip(".,;:!?)")  # trailing punctuation from prose
            urls.append(url)

    site_origin: str | None = None
    if site_url:
        # Normalise to origin-form (no trailing slash/path) for prefix match.
        from urllib.parse import urlparse
        try:
            u = urlparse(site_url)
            if u.scheme and u.netloc:
                site_origin = f"{u.scheme}://{u.netloc}"
        except Exception:  # noqa: BLE001 — malformed setting, fall back to no filter
            site_origin = None

    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        if site_origin and url.startswith(site_origin):
            continue
        out.append(url)
    return out


def _build_citation_headers(site_config: SiteConfig) -> dict[str, str]:
    """Build the User-Agent + Accept headers for HEAD probes.

    The User-Agent follows the standard crawler-UA convention
    (``Mozilla/5.0 (compatible; PoindexterCitationVerifier/1.0; +<url>)``)
    built by the shared ``utils.crawler_ua.build_crawler_ua`` helper, which
    owns the ``app_settings.crawler_contact_url`` lookup + the OSS contact-URL
    leak guard (the ``+url`` portion is omitted when the setting is unset, so
    forks don't ship the source operator's ``gladlabs.io`` as a default).

    Some servers block the default httpx UA, so the UA keeps the
    "browser-ish + compatible" framing to maximize reachability-signal
    accuracy.
    """
    ua = build_crawler_ua(site_config, product="PoindexterCitationVerifier")
    return {"User-Agent": ua, "Accept": "*/*"}


async def _head_one(
    client: httpx.AsyncClient,
    url: str,
    timeout_s: float,
    headers: dict[str, str],
) -> tuple[str, CitationIssue | None]:
    """HEAD a single URL. Returns (url, None) on alive, (url, issue) on dead."""
    try:
        resp = await client.head(
            url,
            timeout=timeout_s,
            follow_redirects=True,
            headers=headers,
        )
        status = resp.status_code
        # Some servers 405 on HEAD but 200 on GET. Fall back to a lightweight GET.
        if status == 405:
            resp = await client.get(
                url,
                timeout=timeout_s,
                follow_redirects=True,
                headers=headers,
            )
            status = resp.status_code
        if 200 <= status < 400:
            return (url, None)
        return (url, CitationIssue(
            url=url,
            reason="bad_status",
            detail=f"HTTP {status}",
            status_code=status,
        ))
    except httpx.TimeoutException as exc:
        return (url, CitationIssue(
            url=url, reason="timeout", detail=str(exc) or "timeout",
        ))
    except (httpx.ConnectError, httpx.NetworkError) as exc:
        return (url, CitationIssue(
            url=url, reason="dns", detail=str(exc) or "network error",
        ))
    except Exception as exc:  # noqa: BLE001 — any other error → dead
        return (url, CitationIssue(
            url=url, reason="dead", detail=f"{type(exc).__name__}: {exc}",
        ))


class CitationVerifier:
    """External-citation reachability checker wired to a SiteConfig.

    Constructed by ``AppContainer.citation_verifier`` (#272 leaf batch 2).
    Holds only the injected ``SiteConfig`` (read for the crawler
    ``crawler_contact_url`` User-Agent). HTTP fan-out prefers the
    module-level lifespan-bound shared ``http_client`` (wired by
    ``services/http_client.py``) and falls back to a per-call client.
    """

    def __init__(self, *, site_config: SiteConfig) -> None:
        self._site_config = site_config

    def _build_citation_headers(self) -> dict[str, str]:
        """Instance shim over the module-level header builder."""
        return _build_citation_headers(self._site_config)

    async def verify_citations(
        self,
        content: str,
        *,
        site_url: str | None = None,
        timeout_s: float = 8.0,
        concurrency: int = 5,
    ) -> CitationReport:
        """Extract every external URL from ``content`` and verify it's reachable.

        Never raises — individual URL failures become entries in ``report.dead``,
        and a full-pipeline failure (httpx import, etc) returns an empty report
        so the caller can treat "couldn't verify" as "no blocking issue".
        """
        urls = extract_urls(content, site_url=site_url)
        if not urls:
            return CitationReport(total_urls=0, unique_urls=0)

        headers = self._build_citation_headers()
        sem = asyncio.Semaphore(max(1, concurrency))

        async def _bound(client: httpx.AsyncClient, url: str):
            async with sem:
                return await _head_one(client, url, timeout_s, headers)

        try:
            # Prefer the lifespan-bound shared client (warm connection pool
            # across the per-task fan-out); fall back to a per-call client
            # only when nothing has been wired (tests, CLI one-shots).
            if http_client is not None:
                results = await asyncio.gather(
                    *(_bound(http_client, u) for u in urls),
                    return_exceptions=False,
                )
            else:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(timeout_s, connect=min(3.0, timeout_s)),
                    headers=headers,
                ) as client:
                    results = await asyncio.gather(
                        *(_bound(client, u) for u in urls),
                        return_exceptions=False,
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[citation_verifier] bulk verification failed: %s — returning empty report",
                exc,
            )
            return CitationReport(total_urls=len(urls), unique_urls=len(urls))

        alive: list[str] = []
        dead: list[CitationIssue] = []
        for url, issue in results:
            if issue is None:
                alive.append(url)
            else:
                dead.append(issue)

        unique = len(urls)
        return CitationReport(
            total_urls=unique,
            unique_urls=unique,
            alive=alive,
            dead=dead,
            dead_ratio=(len(dead) / unique) if unique else 0.0,
        )


async def verdict_from_report(
    report: CitationReport,
    *,
    max_dead_ratio: float = 0.3,
    min_citations: int = 0,
) -> tuple[bool, str]:
    """Apply policy thresholds to a report.

    Returns (passed, reason). ``passed=True`` means the content is OK by
    citation policy. ``reason`` is a human-readable summary either way so
    callers can surface it in QA feedback.
    """
    if report.unique_urls < min_citations:
        return (False, (
            f"Only {report.unique_urls} external citation(s) — minimum is {min_citations}"
        ))
    if max_dead_ratio > 0 and report.dead_ratio > max_dead_ratio:
        dead_list = ", ".join(f"{d.url} ({d.reason})" for d in report.dead[:5])
        return (False, (
            f"{report.dead_ratio:.0%} of citations dead (max {max_dead_ratio:.0%}): {dead_list}"
        ))
    return (True, report.summary())


def append_sources_section(content: str, urls: list[str]) -> str:
    """If ``content`` has no ## Sources/References section, append one
    listing ``urls``. Idempotent — existing Sources/References sections
    are left alone. Designed for finalize_task.py to call after the
    content has passed QA.
    """
    if not content or not urls:
        return content
    # Already has one? Leave it.
    if re.search(r"(?m)^\s*#{1,3}\s*(sources|references)\b", content, re.IGNORECASE):
        return content
    lines = ["", "", "## Sources"]
    lines.extend(f"- <{u}>" for u in urls)
    return content.rstrip() + "\n".join(lines) + "\n"
