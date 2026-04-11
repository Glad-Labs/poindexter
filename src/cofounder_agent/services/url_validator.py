"""
URL Validator Service — Content Pipeline Hallucination Prevention

Validates URLs found in generated content to detect broken links and
hallucinated references. Designed to run non-blocking alongside the
content pipeline: warnings are logged but publication is never held up.

Usage:
    from services.url_validator import get_url_validator

    validator = get_url_validator()
    urls = validator.extract_urls(markdown_content)
    results = await validator.validate_urls(urls)
"""

import os as _os
import re
import time
from typing import Dict, List, Optional, Tuple

import httpx

from services.logger_config import get_logger
from services.site_config import site_config as _sc

logger = get_logger(__name__)

# Cache entry: (is_valid: bool, status_code: int | None, checked_at: float)
_CacheEntry = tuple[bool, int | None, float]

# 7 days in seconds
_CACHE_TTL = 7 * 24 * 60 * 60

# Internal domains to skip (no point validating our own URLs during generation)
_site_domain = _sc.get("site_domain", "localhost:3000").split(":")[0]
_SKIP_DOMAINS = {_site_domain, f"www.{_site_domain}", "localhost", "127.0.0.1"}

# Regex for extracting URLs from markdown / HTML content
# Matches http(s):// URLs in markdown links, raw URLs, and href attributes
_URL_PATTERN = re.compile(
    r'(?:'
    r'(?:href=["\'])([^"\']+)["\']'           # href="..." or href='...'
    r'|'
    r'\[(?:[^\]]*)\]\(([^)]+)\)'              # [text](url)
    r'|'
    r'(https?://[^\s<>\"\')\]]+)'             # bare URLs
    r')',
    re.IGNORECASE,
)


class URLValidator:
    """Async URL validator with in-memory cache and batch support."""

    def __init__(self, timeout: float = 5.0, cache_ttl: int = _CACHE_TTL):
        self._timeout = timeout
        self._cache_ttl = cache_ttl
        self._cache: dict[str, _CacheEntry] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_urls(self, content: str) -> list[str]:
        """Extract all unique URLs from markdown / HTML content.

        Skips internal gladlabs.io URLs and anchor-only fragments.
        """
        if not content:
            return []

        urls: list[str] = []
        seen: set = set()

        for match in _URL_PATTERN.finditer(content):
            # The pattern has 3 capture groups; exactly one will be non-None
            url = match.group(1) or match.group(2) or match.group(3)
            if not url:
                continue

            # Strip trailing punctuation that regex may have captured
            url = url.rstrip(".,;:!?")

            # Skip anchors and non-http schemes
            if not url.startswith(("http://", "https://")):
                continue

            # Skip internal domains
            if self._is_internal(url):
                continue

            if url not in seen:
                seen.add(url)
                urls.append(url)

        return urls

    async def validate_url(self, url: str) -> bool:
        """Validate a single URL via HTTP HEAD request.

        Returns True for 2xx/3xx responses, False otherwise.
        Uses cached result if available and not expired.
        """
        # Check cache first
        cached = self._cache_get(url)
        if cached is not None:
            return cached

        is_valid = False
        status_code = None

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
                headers={"User-Agent": f"{_sc.get('site_name', 'ContentPipeline')}-LinkChecker/1.0"},
            ) as client:
                resp = await client.head(url)
                status_code = resp.status_code

                # Some servers reject HEAD; fall back to GET with stream
                if status_code == 405:
                    resp = await client.get(url, headers={"Range": "bytes=0-0"})
                    status_code = resp.status_code

                is_valid = status_code < 400

        except httpx.TimeoutException:
            logger.debug("URL validation timed out: %s", url)
        except httpx.HTTPError as exc:
            logger.debug("URL validation HTTP error for %s: %s", url, exc)
        except Exception as exc:
            logger.debug("URL validation unexpected error for %s: %s", url, exc)

        self._cache_set(url, is_valid, status_code)
        return is_valid

    async def validate_urls(self, urls: list[str]) -> dict[str, str]:
        """Batch-validate a list of URLs.

        Returns dict mapping each URL to "valid" or "invalid".
        """
        if not urls:
            return {}

        import asyncio

        results: dict[str, str] = {}

        async def _check(u: str):
            ok = await self.validate_url(u)
            results[u] = "valid" if ok else "invalid"

        # Run all checks concurrently (each has its own timeout)
        await asyncio.gather(*[_check(u) for u in urls], return_exceptions=True)

        # Any URL that raised an exception during gather gets marked invalid
        for u in urls:
            if u not in results:
                results[u] = "invalid"

        return results

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    def _cache_get(self, url: str) -> bool | None:
        entry = self._cache.get(url)
        if entry is None:
            return None
        is_valid, _status, checked_at = entry
        if time.time() - checked_at > self._cache_ttl:
            del self._cache[url]
            return None
        return is_valid

    def _cache_set(self, url: str, is_valid: bool, status_code: int | None):
        self._cache[url] = (is_valid, status_code, time.time())

    def cache_stats(self) -> dict[str, int]:
        """Return cache statistics for monitoring."""
        now = time.time()
        active = sum(1 for _, _, t in self._cache.values() if now - t <= self._cache_ttl)
        return {"total_cached": len(self._cache), "active": active}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_internal(url: str) -> bool:
        """Check if URL belongs to an internal domain we should skip."""
        try:
            from urllib.parse import urlparse
            host = urlparse(url).hostname or ""
            return host in _SKIP_DOMAINS
        except Exception:
            return False


# ============================================================================
# Module-level singleton
# ============================================================================

_instance: URLValidator | None = None


def get_url_validator() -> URLValidator:
    """Get the module-level URLValidator singleton."""
    global _instance
    if _instance is None:
        _instance = URLValidator()
    return _instance
