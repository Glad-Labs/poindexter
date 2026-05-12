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

import ipaddress
import logging
import re
import socket
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from services.site_config import SiteConfig

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15.0
MAX_CONTENT_CHARS = 50000  # safety cap on extracted text

# SSRF guard — bound on how many redirects we follow + re-check (audit
# 2026-05-12 P0 #5). Keeps an attacker page from chaining hops indefinitely.
MAX_REDIRECTS = 5

# IPv4 / IPv6 ranges we refuse to scrape against. Anything resolving inside
# one of these is treated as "internal" and rejected unless the operator has
# explicitly flipped ``app_settings.url_scraper_allow_internal_ips=true``.
#
# Rationale:
#   - 127.0.0.0/8       loopback                  (Prometheus, pgAdmin, etc.)
#   - 10.0.0.0/8        RFC 1918 private
#   - 172.16.0.0/12     RFC 1918 private
#   - 192.168.0.0/16    RFC 1918 private
#   - 169.254.0.0/16    link-local + cloud metadata (AWS/GCP/Azure)
#   - 100.64.0.0/10     CGNAT / Tailscale tailnet IPs
#   - 0.0.0.0/8         "this network" / unspecified-source
#   - ::1/128           IPv6 loopback
#   - fc00::/7          IPv6 unique-local (ULA, equiv of RFC 1918)
#   - fe80::/10         IPv6 link-local
#   - ::/128            IPv6 unspecified
#   - ::ffff:0:0/96     IPv4-mapped IPv6 (re-check the embedded v4 separately)
_BLOCKED_NETWORKS_V4: tuple[ipaddress.IPv4Network, ...] = (
    ipaddress.IPv4Network("127.0.0.0/8"),
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
    ipaddress.IPv4Network("169.254.0.0/16"),
    ipaddress.IPv4Network("100.64.0.0/10"),
    ipaddress.IPv4Network("0.0.0.0/8"),
)

_BLOCKED_NETWORKS_V6: tuple[ipaddress.IPv6Network, ...] = (
    ipaddress.IPv6Network("::1/128"),
    ipaddress.IPv6Network("fc00::/7"),
    ipaddress.IPv6Network("fe80::/10"),
    ipaddress.IPv6Network("::/128"),
)

# Lifespan-bound SiteConfig; main.py wires this via set_site_config().
# Falls back to a fresh env-fallback instance when unset.
site_config: SiteConfig = SiteConfig()


def set_site_config(sc: SiteConfig) -> None:
    """Wire the lifespan-bound SiteConfig instance for this module."""
    global site_config
    site_config = sc


def _sc() -> SiteConfig:
    """Return the wired SiteConfig (kept for back-compat; new code reads the module attr directly)."""
    return site_config


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


class SSRFBlockedError(URLScrapeError):
    """Raised when a URL resolves to an IP we refuse to scrape.

    Sub-classes :class:`URLScrapeError` so existing callers that catch the
    parent exception (e.g. ``routes/topics_routes.py``) continue to behave
    sensibly — the request fails closed with no internal-page content
    bleeding into the response, which is the entire point of the guard.
    """


def _is_blocked_ip(ip_str: str) -> bool:
    """Return True if *ip_str* lies inside one of the blocked CIDR ranges.

    Also catches IPv4-mapped IPv6 (``::ffff:127.0.0.1``) by extracting the
    embedded v4 and re-checking. Anything that fails to parse as an IP is
    treated as blocked — refusing the unknown is safer than allowing it.
    """
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return True

    if isinstance(addr, ipaddress.IPv6Address):
        # IPv4-mapped IPv6 (e.g. ``::ffff:127.0.0.1``) — extract + re-check.
        if addr.ipv4_mapped is not None:
            mapped = addr.ipv4_mapped
            return any(mapped in net for net in _BLOCKED_NETWORKS_V4)
        return any(addr in net for net in _BLOCKED_NETWORKS_V6)

    return any(addr in net for net in _BLOCKED_NETWORKS_V4)


def _resolve_hostname(hostname: str, port: int) -> list[str]:
    """Resolve *hostname* to every IP (v4 + v6) the OS knows about.

    Uses ``socket.getaddrinfo`` (DNS-aware, IPv4+IPv6) — never
    ``socket.gethostbyname`` (IPv4-only). Returned IPs are de-duplicated.
    """
    try:
        infos = socket.getaddrinfo(
            hostname, port, proto=socket.IPPROTO_TCP,
        )
    except socket.gaierror as exc:
        raise URLScrapeError(f"DNS lookup failed for {hostname}: {exc}") from exc

    seen: set[str] = set()
    out: list[str] = []
    for info in infos:
        sockaddr = info[4]
        ip = sockaddr[0]
        if ip not in seen:
            seen.add(ip)
            out.append(ip)
    return out


def _resolve_and_check(url: str, site_config_obj: SiteConfig | None = None) -> None:
    """Resolve *url*'s hostname and reject if any IP is in the denylist.

    Called before every HTTP request and after every redirect. The operator
    override ``app_settings.url_scraper_allow_internal_ips`` (default false)
    short-circuits the check — flip it briefly for legitimate internal
    scraping, flip it back when done.

    NOTE on DNS rebinding: an attacker can return a public IP at check time
    then a private IP at connect time. The current implementation resolves
    once and trusts the kernel to reuse that answer within the same
    short-lived connection (httpx's default connector caches the resolution
    for the lifetime of the request). For full rebinding immunity we'd need
    to pin the connection to the resolved IP literal and pass ``Host:``
    separately — deferred (TODO #495) since "resolve + check before each
    hop" closes ~95% of the attack surface and is reviewable in 50 lines.

    Raises:
        SSRFBlockedError: if any resolved IP is private/loopback/etc.
        URLScrapeError: if the URL is malformed or DNS fails.
    """
    sc = site_config_obj if site_config_obj is not None else _sc()
    if sc.get_bool("url_scraper_allow_internal_ips", default=False):
        return

    parsed = urlparse(url)
    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        raise URLScrapeError(f"URL has no hostname: {url!r}")

    # Reject literal IPs that are already in a blocked range — saves a DNS
    # round-trip and means ``http://127.0.0.1:9091`` is rejected even if
    # the loopback resolver isn't reachable.
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None and _is_blocked_ip(str(literal)):
        raise SSRFBlockedError(
            f"Refusing to scrape internal IP {hostname} ({url!r}). "
            "Set app_settings.url_scraper_allow_internal_ips=true to allow."
        )

    # Standard names that resolve to loopback regardless of /etc/hosts.
    if hostname in {"localhost", "localhost.localdomain"}:
        raise SSRFBlockedError(
            f"Refusing to scrape loopback hostname {hostname!r} ({url!r}). "
            "Set app_settings.url_scraper_allow_internal_ips=true to allow."
        )

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    ips = _resolve_hostname(hostname, port)
    if not ips:
        raise URLScrapeError(f"No IPs resolved for {hostname}")

    blocked = [ip for ip in ips if _is_blocked_ip(ip)]
    if blocked:
        raise SSRFBlockedError(
            f"Refusing to scrape {url!r} — {hostname} resolved to "
            f"blocked IP(s): {', '.join(blocked)}. "
            "Set app_settings.url_scraper_allow_internal_ips=true to allow."
        )


async def _safe_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    site_config_obj: SiteConfig | None = None,
    extra_headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Manual redirect loop with an IP re-check at every hop (SSRF guard).

    ``follow_redirects=True`` on the httpx client is intentionally disabled
    so we can re-resolve the target hostname before each new request. An
    attacker page can otherwise 302 to ``http://127.0.0.1:9091`` and the
    library will follow without re-running our denylist.
    """
    current = url
    for hop in range(MAX_REDIRECTS + 1):
        _resolve_and_check(current, site_config_obj)
        kwargs: dict = {}
        if extra_headers:
            kwargs["headers"] = extra_headers
        resp = await client.get(current, **kwargs)

        if not (300 <= resp.status_code < 400):
            return resp

        location = resp.headers.get("location")
        if not location:
            return resp

        next_url = urljoin(current, location)
        # Reject non-http(s) redirect targets (e.g. file://, gopher://).
        next_parsed = urlparse(next_url)
        if next_parsed.scheme not in ("http", "https"):
            raise SSRFBlockedError(
                f"Refusing redirect to non-http(s) URL: {next_url!r}"
            )

        if hop == MAX_REDIRECTS:
            raise URLScrapeError(
                f"Too many redirects ({MAX_REDIRECTS}) starting at {url!r}"
            )
        current = next_url

    # Unreachable in practice; the loop returns or raises above.
    raise URLScrapeError(f"Redirect loop exhausted for {url!r}")


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
    """Fetch HTML with a reasonable user agent.

    Performs an SSRF check before each request and after every redirect via
    :func:`_safe_get` — internal IPs (loopback, RFC1918, link-local, CGNAT,
    cloud-metadata, IPv6 ULA/link-local) are refused unless the operator
    explicitly enables ``url_scraper_allow_internal_ips`` (audit P0 #5).
    """
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=5.0),
        headers={"User-Agent": _build_user_agent()},
        follow_redirects=False,  # we do manual redirects to re-run the IP check
    ) as client:
        resp = await _safe_get(client, url)
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
            follow_redirects=False,  # SSRF guard — see _safe_get docstring
        ) as client:
            repo_resp = await _safe_get(client, api_url)
            repo_data = repo_resp.json() if repo_resp.is_success else {}

            readme_resp = await _safe_get(
                client,
                readme_url,
                extra_headers={"Accept": "application/vnd.github.raw"},
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
