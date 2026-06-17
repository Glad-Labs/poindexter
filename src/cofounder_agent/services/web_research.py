"""
Web Research — free local web search + content extraction.

Replaces Serper ($0.001/search) with DuckDuckGo (free, no API key).
Fetches and extracts clean text from top results.

Sources:
1. DuckDuckGo search (free, no rate limits for reasonable usage)
2. HTTP fetch + BeautifulSoup extraction (clean text from HTML)
3. Falls back gracefully if search or fetch fails

Usage:
    from services.web_research import WebResearcher
    researcher = WebResearcher(site_config=site_config)
    results = await researcher.search("FastAPI best practices 2026")
    # Returns list of {title, url, snippet, content} dicts

2026-05-29 — SiteConfig DI migration (#272 leaf batch 2) converted this
module from the module-level ``site_config`` singleton + ``set_site_config``
setter + ``_sc()`` accessor to constructor DI. The ``WebResearcher`` class
now takes ``site_config`` in ``__init__`` and reads its ``web_research_*``
tunables via ``self._web_research_int``. The composition root
(``services/container.py::AppContainer.web_research``) wires one; callers
that aren't yet migrated build a per-call instance from their own
lifespan-bound SiteConfig (caller-bridge pattern,
``WebResearcher(site_config=site_config)``).
"""

import asyncio
import random

import httpx
from bs4 import BeautifulSoup

from services.logger_config import get_logger
from services.site_config import SiteConfig
from services.url_scraper import URLScrapeError, _safe_get

logger = get_logger(__name__)


# Max content to extract per page (chars) — default only, tunable via
# app_settings.web_research_max_content_chars.
MAX_CONTENT_CHARS = 2000
# Request timeout — default only, tunable via app_settings.web_research_fetch_timeout_seconds.
FETCH_TIMEOUT = 10
# Max concurrent fetches — default only, tunable via app_settings.web_research_max_concurrent.
MAX_CONCURRENT = 3


class WebResearcher:
    """Free web research using DuckDuckGo + content extraction."""

    def __init__(self, *, site_config: SiteConfig):
        self._site_config = site_config

    def _web_research_int(self, key: str, default: int) -> int:
        """Read a web_research_* tunable from app_settings with a safe default.

        Indirected through a method so the lookup happens at call time
        (picks up live app_settings changes without a restart) rather than
        being frozen at import time (#198).
        """
        try:
            return self._site_config.get_int(f"web_research_{key}", default)
        except Exception:
            return default

    async def search(self, query: str, num_results: int = 5) -> list[dict[str, str]]:
        """Search the web and extract content from top results.

        Returns list of dicts with: title, url, snippet, content
        """
        # Step 1: DuckDuckGo search
        search_results = await self._ddg_search(query, num_results)
        if not search_results:
            logger.warning("[RESEARCH] DuckDuckGo returned no results for: %s", query[:50])
            return []

        # Step 2: Fetch and extract content from top results (concurrent)
        sem = asyncio.Semaphore(self._web_research_int("max_concurrent", MAX_CONCURRENT))
        async def fetch_one(result):
            async with sem:
                content = await self._extract_content(result["url"])
                result["content"] = content
                return result

        tasks = [fetch_one(r) for r in search_results]
        enriched = await asyncio.gather(*tasks, return_exceptions=True)

        results = []
        for r in enriched:
            if isinstance(r, Exception):
                logger.warning("[RESEARCH] Web fetch failed (non-fatal): %s", r)
            elif isinstance(r, dict) and r.get("url"):
                results.append(r)

        logger.info("[RESEARCH] Web search: %d results for '%s'", len(results), query[:50])
        return results

    async def search_simple(self, query: str, num_results: int = 5) -> list[dict[str, str]]:
        """Quick search — just titles, URLs, and snippets. No content extraction."""
        return await self._ddg_search(query, num_results)

    async def _ddg_search(self, query: str, num_results: int) -> list[dict[str, str]]:
        """Search DuckDuckGo (free, no API key needed).

        Under concurrent pipeline load DDG rate-limits/throttles and the
        client raises (often surfacing as ``No results found``), which used
        to zero out web fact-check grounding on the first failure
        (glad-labs-stack#877). Bounded exponential backoff + jitter spreads
        simultaneous bursts so a transient throttle no longer kills research.
        A hard per-attempt timeout still caps each try, and we still degrade
        to ``[]`` after the retry budget.
        """
        try:
            from ddgs import DDGS
        except ImportError:
            try:
                from duckduckgo_search import DDGS  # type: ignore[no-redef]  # legacy fallback
            except Exception as e:  # both client libs unavailable
                logger.warning("[RESEARCH] DuckDuckGo client unavailable: %s", e)
                return []

        # Run in thread to avoid blocking (ddgs is sync)
        def _search():
            with DDGS() as ddgs:
                return list(ddgs.text(query, max_results=num_results))

        loop = asyncio.get_running_loop()
        # Hard cap per attempt: DDG sometimes hangs or rate-limits silently.
        # asyncio.wait_for guarantees the pipeline won't stall on search.
        _search_timeout = self._web_research_int("search_timeout_seconds", 20)
        attempts = max(1, self._web_research_int("ddg_retry_attempts", 3))
        base_delay = self._web_research_int("ddg_retry_base_delay_ms", 500) / 1000.0

        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                raw = await asyncio.wait_for(
                    loop.run_in_executor(None, _search), timeout=_search_timeout
                )
            except asyncio.TimeoutError:
                # A hung search shouldn't burn the rest of the retry budget.
                logger.warning(
                    "[RESEARCH] DuckDuckGo search timed out after %ds for: %s",
                    _search_timeout, query[:50],
                )
                return []
            except Exception as e:  # noqa: BLE001 — DDG throttle/ratelimit
                last_exc = e
                if attempt + 1 >= attempts:
                    break
                delay = base_delay * (2 ** attempt) + random.uniform(0.0, base_delay)
                logger.debug(
                    "[RESEARCH] DuckDuckGo attempt %d/%d failed (%s); retrying in %.2fs",
                    attempt + 1, attempts, e, delay,
                )
                await asyncio.sleep(delay)
                continue

            return [
                {
                    "title": r.get("title", ""),
                    "url": r.get("href", r.get("link", "")),
                    "snippet": r.get("body", r.get("snippet", "")),
                    "content": "",
                }
                for r in raw
                if r.get("href") or r.get("link")
            ]

        logger.warning(
            "[RESEARCH] DuckDuckGo search failed after %d attempt(s): %s",
            attempts, last_exc,
        )
        return []

    async def _extract_content(self, url: str) -> str:
        """Fetch a URL and extract clean text content.

        Fetches go through the shared SSRF guard (``url_scraper._safe_get``):
        redirects are followed manually with an IP-denylist re-check on every
        hop (loopback / RFC1918 / link-local / cloud-metadata / CGNAT / IPv6
        ULA). DuckDuckGo search results are attacker-influenceable open-web
        pages, and the worker co-hosts Prometheus / pgAdmin / Postgres on
        localhost — so without this a result page can 302 us into the internal
        admin plane and bleed those responses into draft research text (#1289).

        A blocked or failed fetch degrades to "" exactly like any other fetch
        miss, so legitimate URLs are unaffected. Operators who genuinely need
        internal scraping flip ``app_settings.url_scraper_allow_internal_ips``.
        """
        if not url:
            return ""

        _fetch_timeout = self._web_research_int("fetch_timeout_seconds", FETCH_TIMEOUT)
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(_fetch_timeout, connect=3.0),
                follow_redirects=False,  # _safe_get does manual redirects + per-hop IP re-check
            ) as client:
                resp = await _safe_get(
                    client,
                    url,
                    self._site_config,
                    extra_headers={
                        "User-Agent": "Mozilla/5.0 (compatible; ContentResearcher/1.0)",
                    },
                )
                if resp.status_code != 200:
                    return ""

                html = resp.text
                soup = BeautifulSoup(html, "html.parser")

                # Remove script/style/nav elements
                for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
                    tag.decompose()

                # Extract text from article or main content
                article = soup.find("article") or soup.find("main") or soup.find("body")
                if not article:
                    return ""

                text = article.get_text(separator="\n", strip=True)

                # Clean up: collapse whitespace, limit length
                lines = [line.strip() for line in text.split("\n") if line.strip()]
                clean = "\n".join(lines)

                return clean[: self._web_research_int("max_content_chars", MAX_CONTENT_CHARS)]

        except URLScrapeError as e:
            # SSRF guard fired (internal IP / non-http redirect) or DNS/redirect
            # failure. Log louder than a generic miss so a blocked fetch is
            # visible on the surface, then degrade to "" like any failed fetch.
            logger.warning("[RESEARCH] Refused/failed fetch for %s: %s", url[:80], e)
            return ""
        except Exception as e:
            logger.debug("[RESEARCH] Content extraction failed for %s: %s", url[:50], e)
            return ""

    def format_for_prompt(self, results: list[dict[str, str]], max_chars: int = 3000) -> str:
        """Format research results for injection into an LLM prompt."""
        if not results:
            return ""

        lines = ["WEB RESEARCH (current sources — cite these, do not fabricate URLs):"]
        total = 0

        for i, r in enumerate(results, 1):
            title = r.get("title", "Untitled")
            url = r.get("url", "")
            snippet = r.get("snippet", "")
            content = r.get("content", "")

            entry = f"\n{i}. [{title}]({url})\n   {snippet}"
            if content:
                # Add first paragraph of extracted content
                first_para = content.split("\n")[0][:300]
                entry += f"\n   Key content: {first_para}"

            if total + len(entry) > max_chars:
                break
            lines.append(entry)
            total += len(entry)

        return "\n".join(lines)
