"""Fetch a seed URL and extract title + excerpt for topic seeding (GH-42).

Matt's workflow: sees a dev.to article, HN story, or press release and
wants to riff on it. This module turns a bare URL into a topic seed +
"Source article" attribution that the writer stage injects into the
research context. Attribution matters — we once shipped a Herrington-
style copycat title because no one told the writer where the idea came
from (see GH-42 acceptance criterion #3).

Deliberate design choices:

* **httpx only, no primp/ddgs.** primp crashes on Windows; the rest of
  the pipeline already standardised on httpx so we stay consistent.
* **No BeautifulSoup in the hot path.** A handful of regexes handles
  every real case Matt throws at this and keeps the dependency surface
  thin enough that it's trivial to reason about. If a future caller
  needs full DOM parsing, ``services/url_scraper.py`` already provides
  the heavier scraper — this module is the "single URL → topic seed"
  lightweight cousin.
* **Typed errors, not string matching.** :class:`SeedURLError` carries
  a ``reason`` field (``network``, ``http_error``, ``login_wall``,
  ``too_large``, ``no_title``, ``invalid_url``) so the route can map
  each to the right HTTP status and the tests can assert structurally.
* **Config reads via site_config.** Timeout / UA / max_bytes are all
  app_settings keys (seeded by migration 0074) per the project's
  DB-first configuration convention — no direct environment reads.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Settings keys + hardcoded fallback defaults
# ---------------------------------------------------------------------------
# These defaults ONLY apply when site_config hasn't loaded yet (tests, cold
# import). Production reads should hit the DB-seeded app_settings values
# from migration 0074. Keep these numbers in sync with the migration seed
# so the tests and migration don't drift.

_SETTING_TIMEOUT: Final[str] = "seed_url_fetch_timeout_seconds"
_SETTING_USER_AGENT: Final[str] = "seed_url_user_agent"
_SETTING_MAX_BYTES: Final[str] = "seed_url_max_bytes"

_DEFAULT_TIMEOUT_SECONDS: Final[float] = 10.0
_DEFAULT_MAX_BYTES: Final[int] = 1_048_576  # 1 MiB
_DEFAULT_USER_AGENT: Final[str] = (
    # Generic recent-Chrome UA. Many news sites 403 on obvious bot UAs.
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# Login-wall detection
# ---------------------------------------------------------------------------
# Pages that require auth typically embed one of these phrases in the
# rendered HTML. We only check if the page has NO meaningful extractable
# content — i.e. we fetched HTML but couldn't get a title AND an excerpt
# that survives the login-wall filter. This keeps false positives down
# on articles that happen to discuss login forms.

_LOGIN_WALL_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"sign\s*in\s*to\s*continue", re.IGNORECASE),
    re.compile(r"please\s*sign\s*in", re.IGNORECASE),
    re.compile(r"log\s*in\s*to\s*continue", re.IGNORECASE),
    re.compile(r"subscribe\s*to\s*continue\s*reading", re.IGNORECASE),
    re.compile(r"become\s*a\s*(?:paid\s*)?subscriber", re.IGNORECASE),
    re.compile(r"this\s*content\s*is\s*for\s*(?:members|subscribers)", re.IGNORECASE),
)


# ---------------------------------------------------------------------------
# HTML extraction regexes
# ---------------------------------------------------------------------------
# DOTALL so these match across newlines inside long <title>/<h1> tags.

_TITLE_RE: Final[re.Pattern[str]] = re.compile(
    r"<title[^>]*>(.*?)</title>",
    re.IGNORECASE | re.DOTALL,
)
_H1_RE: Final[re.Pattern[str]] = re.compile(
    r"<h1[^>]*>(.*?)</h1>",
    re.IGNORECASE | re.DOTALL,
)
_META_DESC_RE: Final[re.Pattern[str]] = re.compile(
    r"<meta\s+[^>]*?"
    r"(?:name|property)\s*=\s*[\"'](?:description|og:description|twitter:description)[\"']"
    r"[^>]*?content\s*=\s*[\"']([^\"']*)[\"']",
    re.IGNORECASE,
)
# Alt form: content= attribute comes first.
_META_DESC_RE_ALT: Final[re.Pattern[str]] = re.compile(
    r"<meta\s+[^>]*?content\s*=\s*[\"']([^\"']*)[\"']"
    r"[^>]*?(?:name|property)\s*=\s*[\"'](?:description|og:description|twitter:description)[\"']",
    re.IGNORECASE,
)
_P_TAG_RE: Final[re.Pattern[str]] = re.compile(
    r"<p[^>]*>(.*?)</p>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_STRIP_RE: Final[re.Pattern[str]] = re.compile(r"<[^>]+>")
_WHITESPACE_RE: Final[re.Pattern[str]] = re.compile(r"\s+")

# A paragraph this short is almost always navigation/footer noise, not
# the article's opening paragraph.
_MIN_EXCERPT_CHARS: Final[int] = 40


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SeedURLResult:
    """Structured output of a successful seed-URL fetch."""

    url: str
    title: str
    excerpt: str
    status_code: int
    content_length: int  # bytes actually read (<= max_bytes)


class SeedURLError(Exception):
    """Raised when a seed URL cannot be turned into a usable topic seed.

    ``reason`` is a stable short string so the route handler and tests
    can key off of it without string-matching the human-facing message.
    """

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _get_timeout_seconds() -> float:
    try:
        import services.site_config as _scm
        _sc = _scm.site_config
        return _sc.get_float(_SETTING_TIMEOUT, _DEFAULT_TIMEOUT_SECONDS)
    except Exception:  # pragma: no cover — site_config absent in bare imports
        return _DEFAULT_TIMEOUT_SECONDS


def _get_user_agent() -> str:
    try:
        import services.site_config as _scm
        _sc = _scm.site_config
        ua = _sc.get(_SETTING_USER_AGENT, _DEFAULT_USER_AGENT)
        return ua or _DEFAULT_USER_AGENT
    except Exception:  # pragma: no cover
        return _DEFAULT_USER_AGENT


def _get_max_bytes() -> int:
    try:
        import services.site_config as _scm
        _sc = _scm.site_config
        return _sc.get_int(_SETTING_MAX_BYTES, _DEFAULT_MAX_BYTES)
    except Exception:  # pragma: no cover
        return _DEFAULT_MAX_BYTES


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------


def _strip_html(html: str) -> str:
    """Strip tags + collapse whitespace — for text inside <title>/<p>/<h1>."""
    no_tags = _TAG_STRIP_RE.sub(" ", html)
    # HTML entity decode for the handful of common entities we see in titles.
    no_tags = (
        no_tags.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&nbsp;", " ")
    )
    return _WHITESPACE_RE.sub(" ", no_tags).strip()


def _extract_title(html: str) -> str:
    """Title priority: <title> → first <h1>. Empty string if neither usable."""
    m = _TITLE_RE.search(html)
    if m:
        title = _strip_html(m.group(1))
        if title:
            return title
    m = _H1_RE.search(html)
    if m:
        title = _strip_html(m.group(1))
        if title:
            return title
    return ""


def _extract_excerpt(html: str) -> str:
    """Excerpt priority: meta description → first sufficiently-long <p>."""
    for pattern in (_META_DESC_RE, _META_DESC_RE_ALT):
        m = pattern.search(html)
        if m:
            excerpt = _strip_html(m.group(1))
            if excerpt:
                return excerpt

    # Fall back to the first <p> that's substantial enough to be prose.
    for match in _P_TAG_RE.finditer(html):
        candidate = _strip_html(match.group(1))
        if len(candidate) >= _MIN_EXCERPT_CHARS:
            return candidate
    return ""


def _looks_like_login_wall(html: str) -> bool:
    """Heuristic: does this HTML contain an auth gate phrase?"""
    return any(pat.search(html) for pat in _LOGIN_WALL_PATTERNS)


def _validate_url(url: str) -> None:
    if not url or not isinstance(url, str):
        raise SeedURLError("URL is empty or not a string", reason="invalid_url")
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise SeedURLError(
            f"URL must use http:// or https:// (got {parsed.scheme or 'no scheme'!r})",
            reason="invalid_url",
        )
    if not parsed.netloc:
        raise SeedURLError("URL has no host", reason="invalid_url")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def fetch_seed_url(
    url: str,
    *,
    timeout_seconds: float | None = None,
    user_agent: str | None = None,
    max_bytes: int | None = None,
    client: httpx.AsyncClient | None = None,
) -> SeedURLResult:
    """Fetch a URL and extract title + excerpt.

    Args:
        url: The URL to fetch. Must be http:// or https://.
        timeout_seconds: Override fetch timeout. Default: from site_config
            ``seed_url_fetch_timeout_seconds`` (seeded at 10s).
        user_agent: Override User-Agent header. Default: from site_config
            ``seed_url_user_agent`` (seeded with a generic Chrome UA).
        max_bytes: Truncate the response body at this many bytes. Default:
            from site_config ``seed_url_max_bytes`` (seeded at 1 MiB).
        client: Optional pre-built ``httpx.AsyncClient`` — tests inject
            a ``MockTransport``-backed client here.

    Returns:
        :class:`SeedURLResult` with title, excerpt, status code, and the
        number of bytes actually read.

    Raises:
        SeedURLError: On any failure. ``reason`` identifies the class of
            failure — one of ``invalid_url``, ``network``, ``http_error``,
            ``login_wall``, ``too_large``, ``no_title``.
    """
    _validate_url(url)

    timeout = timeout_seconds if timeout_seconds is not None else _get_timeout_seconds()
    ua = user_agent if user_agent is not None else _get_user_agent()
    cap = max_bytes if max_bytes is not None else _get_max_bytes()

    headers = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=min(timeout, 5.0)),
            follow_redirects=True,
            headers=headers,
        )

    try:
        try:
            resp = await client.get(url, headers=headers)
        except httpx.TimeoutException as exc:
            raise SeedURLError(
                f"Fetch timed out after {timeout:.1f}s: {url}",
                reason="network",
            ) from exc
        except httpx.HTTPError as exc:
            # Network-layer problems (DNS, connection refused, TLS).
            raise SeedURLError(
                f"Network error fetching {url}: {exc}",
                reason="network",
            ) from exc

        if resp.status_code >= 400:
            # 404 / 403 / 500 → clear HTTP error. Don't try to extract
            # anything; the body is usually an error page.
            raise SeedURLError(
                f"HTTP {resp.status_code} from {url}",
                reason="http_error",
            )

        # Truncate body at max_bytes. httpx already has the full response
        # in memory at this point (no streaming) but we still enforce the
        # cap so downstream code never sees more than max_bytes of HTML.
        body_bytes = resp.content or b""
        truncated = False
        if len(body_bytes) > cap:
            body_bytes = body_bytes[:cap]
            truncated = True
            logger.info(
                "[SEED_URL] Truncated body from %d to %d bytes for %s",
                len(resp.content), cap, url,
            )

        # Decode as best-effort UTF-8 with replacement — we only need
        # enough text to extract a title.
        try:
            html = body_bytes.decode(resp.encoding or "utf-8", errors="replace")
        except (LookupError, TypeError):
            html = body_bytes.decode("utf-8", errors="replace")

        title = _extract_title(html)
        excerpt = _extract_excerpt(html)

        # Login-wall: only escalate to an error if we ALSO failed to get a
        # useful title/excerpt. News articles routinely embed "sign in"
        # strings in the footer — those aren't login walls.
        if _looks_like_login_wall(html) and (not title or not excerpt):
            raise SeedURLError(
                f"Login wall detected at {url} — no extractable article",
                reason="login_wall",
            )

        if not title:
            raise SeedURLError(
                f"Could not extract a title from {url}",
                reason="no_title",
            )

        logger.info(
            "[SEED_URL] Fetched %s: title=%r, excerpt=%d chars, bytes=%d%s",
            url, title[:80], len(excerpt), len(body_bytes),
            " (truncated)" if truncated else "",
        )

        return SeedURLResult(
            url=url,
            title=title,
            excerpt=excerpt,
            status_code=resp.status_code,
            content_length=len(body_bytes),
        )
    finally:
        if owns_client:
            await client.aclose()


def build_source_attribution(result: SeedURLResult) -> str:
    """Format a seed-URL result as a research-context preamble.

    The exact "Source article:" label matters — multi-model QA and the
    writer's system prompt both grep for it when deciding whether to
    cite the originating URL. See GH-42 acceptance criterion #3 —
    attribution is what keeps us out of Herrington-pattern copycat
    territory.
    """
    lines = [
        "Source article:",
        f"URL: {result.url}",
        f"Title: {result.title}",
    ]
    if result.excerpt:
        lines.append(f"Excerpt: {result.excerpt}")
    return "\n".join(lines)
