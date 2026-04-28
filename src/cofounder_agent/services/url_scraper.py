"""URL scraper — extract title + content from any URL for topic seeding (#230).

Supports:
- Article pages (blog posts, news) — via trafilatura
- GitHub repos (via README endpoint)
- arXiv papers (via abstract page)
- Product pages (generic OG metadata)

#204: generic article extraction now uses **trafilatura** (Apache 2.0)
instead of hand-rolled BeautifulSoup heuristics. Trafilatura is a
peer-reviewed library purpose-built for clean text + metadata
extraction, used by NLP research groups for the same kind of corpus
prep our research stage does. Github/arXiv routes still use bespoke
scrapers because they're API-driven and don't benefit from trafilatura.

Uses httpx for fetch (timeouts + user-agent control), trafilatura for
HTML parsing on the generic path, BeautifulSoup4 retained for the
arXiv route's structured-element selectors.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx
import trafilatura
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15.0
MAX_CONTENT_CHARS = 50000  # safety cap on extracted text

# Fallback used when no site_config is passed (e.g. by legacy callers
# mid-Phase-H rollout). Keeps the scraper running even if the DI chain
# hasn't reached every call site yet — it just sends a generic UA.
_FALLBACK_USER_AGENT = (
    "Mozilla/5.0 (compatible; PoindexterBot/1.0; no-contact-configured) "
    "AI content pipeline topic scraper"
)


def _build_user_agent(site_config: Any) -> str:
    """Build the URL-scraper User-Agent string from site_config (#198).

    Uses site_contact_url for the bot identifier so operators can bring
    their own brand. Falls back to a neutral generic if unset.

    Args:
        site_config: SiteConfig instance (DI — Phase H). ``None`` returns
            the generic fallback UA so legacy callers that haven't been
            threaded through yet still work.
    """
    if site_config is None:
        return _FALLBACK_USER_AGENT
    contact = site_config.get("site_contact_url", "").strip()
    bot_name = site_config.get("scraper_bot_name", "PoindexterBot/1.0").strip()
    identifier = f"+{contact}" if contact else "no-contact-configured"
    return (
        f"Mozilla/5.0 (compatible; {bot_name}; {identifier}) "
        "AI content pipeline topic scraper"
    )


class URLScrapeError(Exception):
    """Raised when a URL cannot be scraped."""


async def scrape_url(
    url: str,
    timeout: float = DEFAULT_TIMEOUT,
    *,
    site_config: Any = None,
) -> dict:
    """Scrape a URL and return structured content.

    Returns:
        {
            "url": str,
            "title": str,
            "content_preview": str (first 500 chars),
            "content_full": str (up to MAX_CONTENT_CHARS),
            "content_type": str ("article" | "github" | "arxiv" | "product" | "generic"),
            "author": str | None,
            "published_at": str | None,  # ISO date if detected
            "excerpt": str | None,       # from og:description or meta description
            "word_count": int,
        }

    Raises:
        URLScrapeError: if the URL can't be fetched or parsed.

    Args:
        url: the URL to scrape.
        timeout: HTTP request timeout in seconds.
        site_config: SiteConfig instance (DI — Phase H). Used to build
            the User-Agent string and to resolve ``arxiv_base_url`` for
            the arXiv scraper. ``None`` falls back to sensible defaults
            so callers mid-rollout still work.
    """
    if not url or not url.startswith(("http://", "https://")):
        raise URLScrapeError(f"Invalid URL: {url!r}")

    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()

    # Route to specialized scrapers for known platforms
    if "github.com" in hostname:
        return await _scrape_github(url, timeout, site_config=site_config)
    if "arxiv.org" in hostname:
        return await _scrape_arxiv(url, timeout, site_config=site_config)

    # Default: generic HTML article extraction
    return await _scrape_generic(url, timeout, site_config=site_config)


async def _fetch(url: str, timeout: float, *, site_config: Any) -> str:
    """Fetch HTML with a reasonable user agent."""
    user_agent = _build_user_agent(site_config)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=5.0),
        headers={"User-Agent": user_agent},
        follow_redirects=True,
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text


async def _scrape_generic(url: str, timeout: float, *, site_config: Any) -> dict:
    """Extract title + main content from a generic HTML page.

    Uses trafilatura's bare_extraction for both metadata and body text.
    Falls back to a BeautifulSoup minimum (just <title>) if trafilatura
    can't make sense of the page — matches the legacy behavior of
    "always return *something* with a title", needed by callers that
    seed topics from arbitrary URLs an operator pasted.
    """
    try:
        html = await _fetch(url, timeout, site_config=site_config)
    except httpx.HTTPError as e:
        raise URLScrapeError(f"Fetch failed: {e}") from e

    doc = trafilatura.bare_extraction(html, output_format="python", with_metadata=True)

    if doc is not None:
        # Trafilatura already runs the OG/twitter/H1/title precedence
        # we used to do by hand, plus a much more thorough boilerplate
        # stripper. Map its Document fields onto our existing dict shape
        # so callers and tests see no API change.
        title = (doc.title or "").strip() or "Untitled"
        excerpt = (doc.description or "").strip()
        author = doc.author or None
        published_at = doc.date or None
        # ``text`` is the cleaned narrative body (boilerplate removed,
        # scripts/styles/nav/footer dropped); ``raw_text`` keeps more
        # structure but includes some noise. Stick with ``text`` here.
        body = (doc.text or "").strip()
    else:
        # Trafilatura couldn't parse — happens for bare-bones HTML like
        # ``<html><body><p>orphan</p></body></html>``. Pull just the
        # <title> tag with BS4 so we don't break the
        # "scrape_url returns *something*" contract.
        soup = BeautifulSoup(html, "html.parser")
        title = (_first_text(soup, "title") or "Untitled").strip()
        excerpt = ""
        author = None
        published_at = None
        body = ""

    content_full = re.sub(r"\n{3,}", "\n\n", body)[:MAX_CONTENT_CHARS]
    content_preview = content_full[:500]
    word_count = len(content_full.split())

    return {
        "url": url,
        "title": title[:300],
        "content_preview": content_preview,
        "content_full": content_full,
        "content_type": "article",
        "author": author[:200] if author else None,
        "published_at": published_at,
        "excerpt": excerpt[:500] if excerpt else None,
        "word_count": word_count,
    }


async def _scrape_github(url: str, timeout: float, *, site_config: Any) -> dict:
    """Extract README + metadata from a GitHub repo URL.

    Accepts: https://github.com/owner/repo[/...]
    """
    parts = urlparse(url).path.strip("/").split("/")
    if len(parts) < 2:
        return await _scrape_generic(url, timeout, site_config=site_config)

    owner, repo = parts[0], parts[1]
    api_url = f"https://api.github.com/repos/{owner}/{repo}"
    readme_url = f"https://api.github.com/repos/{owner}/{repo}/readme"

    user_agent = _build_user_agent(site_config)
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=5.0),
            headers={"User-Agent": user_agent, "Accept": "application/vnd.github+json"},
        ) as client:
            repo_resp = await client.get(api_url)
            repo_data = repo_resp.json() if repo_resp.is_success else {}

            readme_resp = await client.get(
                readme_url,
                headers={"Accept": "application/vnd.github.raw"},
            )
            readme_text = readme_resp.text if readme_resp.is_success else ""
    except httpx.HTTPError as e:
        raise URLScrapeError(f"GitHub fetch failed: {e}") from e

    title = f"{repo_data.get('full_name') or f'{owner}/{repo}'} — {repo_data.get('description') or 'GitHub repo'}"
    content_full = readme_text[:MAX_CONTENT_CHARS]

    return {
        "url": url,
        "title": title[:300],
        "content_preview": content_full[:500],
        "content_full": content_full,
        "content_type": "github",
        "author": repo_data.get("owner", {}).get("login"),
        "published_at": repo_data.get("pushed_at"),
        "excerpt": repo_data.get("description"),
        "word_count": len(content_full.split()),
    }


async def _scrape_arxiv(url: str, timeout: float, *, site_config: Any) -> dict:
    """Extract abstract from arXiv paper URL.

    Handles both /abs/ and /pdf/ URLs.
    arxiv_base_url setting lets operators point at a mirror or
    local-proxied instance (#198).
    """
    if site_config is None:
        _arxiv_base = "https://arxiv.org"
    else:
        _arxiv_base = site_config.get("arxiv_base_url", "https://arxiv.org").rstrip("/")
    # Normalize to /abs/ URL for HTML scraping
    m = re.search(r"arxiv\.org/(abs|pdf)/(\d+\.\d+)", url)
    if m:
        arxiv_id = m.group(2)
        abs_url = f"{_arxiv_base}/abs/{arxiv_id}"
    else:
        abs_url = url

    try:
        html = await _fetch(abs_url, timeout, site_config=site_config)
    except httpx.HTTPError as e:
        raise URLScrapeError(f"arXiv fetch failed: {e}") from e

    soup = BeautifulSoup(html, "html.parser")

    title = _first_text(soup, "h1.title") or _first_text(soup, "title") or "arXiv paper"
    title = title.replace("Title:", "").strip()

    abstract_el = soup.find("blockquote", class_="abstract")
    abstract = abstract_el.get_text(separator=" ", strip=True) if abstract_el else ""
    abstract = abstract.replace("Abstract:", "").strip()

    authors_el = soup.find("div", class_="authors")
    authors = authors_el.get_text(strip=True) if authors_el else None

    return {
        "url": abs_url,
        "title": title[:300],
        "content_preview": abstract[:500],
        "content_full": abstract[:MAX_CONTENT_CHARS],
        "content_type": "arxiv",
        "author": authors[:500] if authors else None,
        "published_at": None,
        "excerpt": abstract[:500],
        "word_count": len(abstract.split()),
    }


def _first_text(soup: BeautifulSoup, selector: str) -> str:
    """Get first matching element's text, or empty string."""
    el = soup.select_one(selector) if "." in selector or "#" in selector else soup.find(selector)
    return el.get_text(strip=True) if el else ""
