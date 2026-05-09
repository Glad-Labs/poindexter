"""URL scraper — extract title + content from any URL for topic seeding (#230).

Supports:
- Article pages (blog posts, news)
- GitHub repos (via README endpoint)
- arXiv papers (via abstract page)
- Product pages (generic OG metadata)

Uses httpx + beautifulsoup4 (already deps). Respects timeouts, user-agent,
and returns a structured dict compatible with DiscoveredTopic.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from services.site_config import SiteConfig

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15.0
MAX_CONTENT_CHARS = 50000  # safety cap on extracted text

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


def _build_user_agent() -> str:
    """Build the URL-scraper User-Agent string from site_config (#198).

    Lazy — read per-call so post-lifespan edits to ``site_contact_url`` /
    ``scraper_bot_name`` take effect without a worker restart. Uses
    site_contact_url for the bot identifier so operators can bring their
    own brand. Falls back to a neutral generic if unset.
    """
    sc = _sc()
    contact = sc.get("site_contact_url", "").strip()
    bot_name = sc.get("scraper_bot_name", "PoindexterBot/1.0").strip()
    identifier = f"+{contact}" if contact else "no-contact-configured"
    return (
        f"Mozilla/5.0 (compatible; {bot_name}; {identifier}) "
        "AI content pipeline topic scraper"
    )


class URLScrapeError(Exception):
    """Raised when a URL cannot be scraped."""


async def scrape_url(url: str, timeout: float = DEFAULT_TIMEOUT) -> dict:
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
    """
    if not url or not url.startswith(("http://", "https://")):
        raise URLScrapeError(f"Invalid URL: {url!r}")

    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()

    # Route to specialized scrapers for known platforms
    if "github.com" in hostname:
        return await _scrape_github(url, timeout)
    if "arxiv.org" in hostname:
        return await _scrape_arxiv(url, timeout)

    # Default: generic HTML article extraction
    return await _scrape_generic(url, timeout)


async def _fetch(url: str, timeout: float) -> str:
    """Fetch HTML with a reasonable user agent."""
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=5.0),
        headers={"User-Agent": _build_user_agent()},
        follow_redirects=True,
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text


async def _scrape_generic(url: str, timeout: float) -> dict:
    """Extract title + main content from a generic HTML page."""
    try:
        html = await _fetch(url, timeout)
    except httpx.HTTPError as e:
        raise URLScrapeError(f"Fetch failed: {e}") from e

    soup = BeautifulSoup(html, "html.parser")

    # Title priority: og:title > <h1> > <title>
    title = (
        _meta_content(soup, "og:title")
        or _meta_content(soup, "twitter:title")
        or _first_text(soup, "h1")
        or _first_text(soup, "title")
        or "Untitled"
    ).strip()

    # Excerpt
    excerpt = (
        _meta_content(soup, "og:description")
        or _meta_content(soup, "description")
        or _meta_content(soup, "twitter:description")
        or ""
    ).strip()

    # Author
    author = _meta_content(soup, "article:author") or _meta_content(soup, "author")

    # Published date
    published_at = (
        _meta_content(soup, "article:published_time")
        or _meta_content(soup, "og:published_time")
        or _meta_content(soup, "date")
    )

    # Main content: strip scripts/styles/nav/footer, extract text
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript", "form"]):
        tag.decompose()

    # Prefer <article> or <main>, fall back to body
    main = soup.find("article") or soup.find("main") or soup.find("body")
    content_full = ""
    if main:
        # Get text, collapse whitespace
        text = main.get_text(separator="\n", strip=True)
        content_full = re.sub(r"\n{3,}", "\n\n", text)[:MAX_CONTENT_CHARS]

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


async def _scrape_github(url: str, timeout: float) -> dict:
    """Extract README + metadata from a GitHub repo URL.

    Accepts: https://github.com/owner/repo[/...]
    """
    parts = urlparse(url).path.strip("/").split("/")
    if len(parts) < 2:
        return await _scrape_generic(url, timeout)

    owner, repo = parts[0], parts[1]
    api_url = f"https://api.github.com/repos/{owner}/{repo}"
    readme_url = f"https://api.github.com/repos/{owner}/{repo}/readme"

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=5.0),
            headers={"User-Agent": _build_user_agent(), "Accept": "application/vnd.github+json"},
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


async def _scrape_arxiv(url: str, timeout: float) -> dict:
    """Extract abstract from arXiv paper URL.

    Handles both /abs/ and /pdf/ URLs.
    arxiv_base_url setting lets operators point at a mirror or
    local-proxied instance (#198).
    """
    _arxiv_base = _sc().get("arxiv_base_url", "https://arxiv.org").rstrip("/")
    # Normalize to /abs/ URL for HTML scraping
    m = re.search(r"arxiv\.org/(abs|pdf)/(\d+\.\d+)", url)
    if m:
        arxiv_id = m.group(2)
        abs_url = f"{_arxiv_base}/abs/{arxiv_id}"
    else:
        abs_url = url

    try:
        html = await _fetch(abs_url, timeout)
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


def _meta_content(soup: BeautifulSoup, name: str) -> str:
    """Extract <meta name=X> or <meta property=X> content attribute."""
    el = soup.find("meta", attrs={"name": name})
    if el and el.get("content"):
        return el["content"]
    el = soup.find("meta", attrs={"property": name})
    if el and el.get("content"):
        return el["content"]
    return ""


def _first_text(soup: BeautifulSoup, selector: str) -> str:
    """Get first matching element's text, or empty string."""
    el = soup.select_one(selector) if "." in selector or "#" in selector else soup.find(selector)
    return el.get_text(strip=True) if el else ""
