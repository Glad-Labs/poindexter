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
    researcher = WebResearcher()
    results = await researcher.search("FastAPI best practices 2026")
    # Returns list of {title, url, snippet, content} dicts
"""

import asyncio

import httpx
from bs4 import BeautifulSoup

from services.logger_config import get_logger
from services.site_config import SiteConfig

logger = get_logger(__name__)

# Lifespan-bound SiteConfig; main.py wires this via set_site_config().
# Falls back to a fresh env-fallback instance when unset.
_site_config: SiteConfig | None = None


def set_site_config(sc: SiteConfig) -> None:
    """Wire the lifespan-bound SiteConfig instance for this module."""
    global _site_config
    _site_config = sc


def _sc() -> SiteConfig:
    """Return the wired SiteConfig, or a fresh env-fallback instance."""
    return _site_config if _site_config is not None else SiteConfig()


def _web_research_int(key: str, default: int) -> int:
    """Read a web_research_* tunable from app_settings with a safe default.

    Indirected through a function so the lookup happens at call time
    (picks up live app_settings changes without a restart) rather than
    being frozen at import time (#198).
    """
    try:
        return _sc().get_int(f"web_research_{key}", default)
    except Exception:
        return default


# Max content to extract per page (chars) — default only, tunable via
# app_settings.web_research_max_content_chars.
MAX_CONTENT_CHARS = 2000
# Request timeout — default only, tunable via app_settings.web_research_fetch_timeout_seconds.
FETCH_TIMEOUT = 10
# Max concurrent fetches — default only, tunable via app_settings.web_research_max_concurrent.
MAX_CONCURRENT = 3


class WebResearcher:
    """Free web research using DuckDuckGo + content extraction."""

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
        sem = asyncio.Semaphore(_web_research_int("max_concurrent", MAX_CONCURRENT))
        async def fetch_one(result):
            async with sem:
                content = await self._extract_content(result["url"])
                result["content"] = content
                return result

        tasks = [fetch_one(r) for r in search_results]
        enriched = await asyncio.gather(*tasks, return_exceptions=True)

        results = []
        for r in enriched:
            if isinstance(r, dict) and r.get("url"):
                results.append(r)

        logger.info("[RESEARCH] Web search: %d results for '%s'", len(results), query[:50])
        return results

    async def search_simple(self, query: str, num_results: int = 5) -> list[dict[str, str]]:
        """Quick search — just titles, URLs, and snippets. No content extraction."""
        return await self._ddg_search(query, num_results)

    async def _ddg_search(self, query: str, num_results: int) -> list[dict[str, str]]:
        """Search DuckDuckGo (free, no API key needed)."""
        try:
            try:
                from ddgs import DDGS
            except ImportError:
                from duckduckgo_search import DDGS  # legacy fallback

            # Run in thread to avoid blocking (ddgs is sync)
            def _search():
                with DDGS() as ddgs:
                    results = list(ddgs.text(query, max_results=num_results))
                return results

            loop = asyncio.get_running_loop()
            # Hard cap: DDG sometimes hangs or rate-limits silently.
            # asyncio.wait_for guarantees the pipeline won't stall on search.
            _search_timeout = _web_research_int("search_timeout_seconds", 20)
            try:
                raw = await asyncio.wait_for(
                    loop.run_in_executor(None, _search), timeout=_search_timeout
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "[RESEARCH] DuckDuckGo search timed out after %ds for: %s",
                    _search_timeout, query[:50],
                )
                return []

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
        except Exception as e:
            logger.warning("[RESEARCH] DuckDuckGo search failed: %s", e)
            return []

    async def _extract_content(self, url: str) -> str:
        """Fetch a URL and extract clean text content."""
        if not url:
            return ""

        _fetch_timeout = _web_research_int("fetch_timeout_seconds", FETCH_TIMEOUT)
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(_fetch_timeout, connect=3.0),
                follow_redirects=True,
            ) as client:
                resp = await client.get(
                    url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (compatible; ContentResearcher/1.0)",
                    },
                    timeout=_fetch_timeout,
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

                return clean[: _web_research_int("max_content_chars", MAX_CONTENT_CHARS)]

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
