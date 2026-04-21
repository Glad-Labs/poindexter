"""External-article title originality check (GH-87).

Background
----------
Before GH-87 the originality check in :mod:`services.title_generation`
compared the proposed title against our own corpus of recently-published
post titles. That missed the case where an external author published
the same headline days before we did: on 2026-04-19 we shipped a post
whose title was verbatim identical to a dev.to piece from 3 days earlier
(see the issue for details).

This module adds a *web-search* duplicate check that runs at approval
time. It hits DuckDuckGo's HTML endpoint
(``https://html.duckduckgo.com/html/?q=...``) with the exact title
wrapped in quotes, parses the result titles out of the simple HTML
response, and computes similarity against what it finds.

Design constraints
------------------
1. **``httpx``, not ``ddgs``.** The ``ddgs`` / ``duckduckgo_search``
   packages transitively pull in ``primp.pyd``, which crashes on
   Windows under aggressive DDG rate-limiting (see the project MEMORY
   note). We issue a plain ``httpx.AsyncClient`` GET with a normal
   browser ``User-Agent`` instead.
2. **Fail-open on rate-limit / CAPTCHA / network error.** The check is
   a nice-to-have, not a hard gate. When we can't verify, we return
   the "looks original, we didn't actually check" shape so the pipeline
   continues. A Prometheus counter
   (``poindexter_title_originality_external_fail_open_total``) tracks
   how often that fires so we can tell whether the check is actually
   functioning in production.
3. **24h in-process cache.** DDG rate-limits aggressively; the same
   title will typically be checked multiple times during a pipeline
   run (generate → regenerate → QA). The cache is a plain module-level
   dict keyed on a normalised form of the title; TTL comes from
   ``app_settings.title_originality_cache_ttl_hours`` (default 24).

Public surface
--------------
:func:`check_external_title_duplicates` — the coroutine the QA /
content-generation stage calls. Returns a structured dict with
``verbatim_match``, ``near_match``, ``penalty``, ``matches``,
``fail_open`` fields so the caller can decide whether to reject,
apply a score penalty, or just surface a warning.

:func:`clear_cache` — test helper, drops all cached entries.
"""

from __future__ import annotations

import html
import logging
import re
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Metrics — fail-open counter
# ---------------------------------------------------------------------------


try:
    from prometheus_client import Counter

    TITLE_ORIGINALITY_FAIL_OPEN = Counter(
        "poindexter_title_originality_external_fail_open_total",
        "External title originality check fail-open events (rate-limit / error)",
        ["reason"],
    )
except Exception:  # pragma: no cover — prometheus_client optional in tests
    class _NoopCounter:
        def labels(self, **_kw):
            return self

        def inc(self, *_args, **_kw):
            return None

    TITLE_ORIGINALITY_FAIL_OPEN = _NoopCounter()  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


@dataclass
class ExternalOriginalityResult:
    """Structured result the caller uses to decide rejection vs penalty.

    Attributes:
        verbatim_match: True if a search result title matched the probe
            title verbatim (after case+whitespace+punctuation normalisation).
        near_match: True if a search result title was >=0.85 similarity
            but not a full verbatim match. Used to surface an approver warning.
        penalty: Score penalty to subtract from the QA score. 0 if nothing
            matched or the check failed open.
        matches: List of ``{"title": str, "url": str}`` dicts of the
            externally-found titles that triggered the match.
        fail_open: True if the check couldn't actually run (rate-limit,
            CAPTCHA, network error) and we defaulted to "no match".
        fail_reason: Human-readable reason for the fail-open, if any.
    """

    verbatim_match: bool = False
    near_match: bool = False
    penalty: int = 0
    matches: list[dict[str, str]] = field(default_factory=list)
    fail_open: bool = False
    fail_reason: str | None = None

    def to_dict(self) -> dict:
        return {
            "verbatim_match": self.verbatim_match,
            "near_match": self.near_match,
            "penalty": self.penalty,
            "matches": self.matches,
            "fail_open": self.fail_open,
            "fail_reason": self.fail_reason,
        }


# ---------------------------------------------------------------------------
# Cache — process-local, TTL-based
# ---------------------------------------------------------------------------


# key: normalised title string  -> (expires_at_epoch, ExternalOriginalityResult)
_CACHE: dict[str, tuple[float, ExternalOriginalityResult]] = {}


def clear_cache() -> None:
    """Drop every cached originality result. Test/reset helper."""
    _CACHE.clear()


def _cache_key(title: str) -> str:
    """Cache key = lowercased, punctuation-stripped, whitespace-collapsed title.

    This is deliberately aggressive so that two probes that differ only in
    punctuation or casing collide on one cache entry.
    """
    lowered = title.lower().strip()
    no_punct = re.sub(r"[^\w\s]", " ", lowered)
    collapsed = re.sub(r"\s+", " ", no_punct).strip()
    return collapsed


def _cache_ttl_seconds() -> int:
    """Read the TTL from app_settings; default 24h. Failures → 24h."""
    try:
        from services.site_config import site_config
        hours = site_config.get_int("title_originality_cache_ttl_hours", 24)
    except Exception:
        hours = 24
    # Guardrail: never a non-positive TTL, never more than a week.
    hours = max(1, min(hours, 24 * 7))
    return hours * 3600


def _cache_lookup(key: str) -> ExternalOriginalityResult | None:
    entry = _CACHE.get(key)
    if entry is None:
        return None
    expires_at, value = entry
    if time.time() >= expires_at:
        _CACHE.pop(key, None)
        return None
    return value


def _cache_store(key: str, value: ExternalOriginalityResult) -> None:
    _CACHE[key] = (time.time() + _cache_ttl_seconds(), value)


# ---------------------------------------------------------------------------
# DuckDuckGo HTML endpoint
# ---------------------------------------------------------------------------


DDG_HTML_ENDPOINT = "https://html.duckduckgo.com/html/"
DDG_USER_AGENT = (
    # A plain desktop Firefox UA. The DDG HTML endpoint returns machine-
    # readable HTML for most user agents; we avoid anything identifiable as
    # a bot since the endpoint rate-limits headless-looking clients hard.
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0"
)
DDG_TIMEOUT_SECONDS = 10.0


# DDG's HTML result block looks roughly like::
#
#   <a class="result__a" ...>Result Title Here</a>
#
# Followed (optionally) by the URL in a sibling tag. We parse titles with
# a tolerant regex so we don't drag in selectolax for this one use case.
_RESULT_TITLE_RE = re.compile(
    r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)

# CAPTCHA / blocked response detection. DDG tends to return a short HTML
# body with any of these tokens when rate-limited.
_BLOCK_TOKENS = (
    "anomaly",
    "captcha",
    "unusual traffic",
    "rate limit",
    "too many requests",
)


def _strip_html_tags(s: str) -> str:
    """Cheap HTML tag stripper — the DDG result titles contain <b>…</b>."""
    no_tags = re.sub(r"<[^>]+>", "", s)
    return html.unescape(no_tags).strip()


def _normalise_for_compare(s: str) -> str:
    """Normalise a title string for similarity comparison.

    Lowercases, strips outer quotes, collapses internal whitespace, and
    treats different dash/apostrophe glyphs as the same character. This
    catches "We Shipped It" vs "We shipped it." as a verbatim match even
    though the raw strings differ by casing + punctuation.
    """
    if not s:
        return ""
    # Unicode dash/quote normalisation
    out = (
        s.replace("‘", "'")
        .replace("’", "'")
        .replace("“", '"')
        .replace("”", '"')
        .replace("–", "-")
        .replace("—", "-")
    )
    out = out.lower().strip().strip('"').strip("'").strip()
    out = re.sub(r"\s+", " ", out)
    # Drop trailing punctuation that's not semantically meaningful
    out = re.sub(r"[\.!\?:;,]+$", "", out)
    return out


async def _fetch_ddg_html(title: str) -> tuple[str | None, str | None]:
    """Call the DDG HTML endpoint. Returns ``(html, None)`` or ``(None, reason)``.

    We do NOT raise on failure — the caller uses the reason string to
    decide the fail-open path.
    """
    query = f'"{title}"'
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(DDG_TIMEOUT_SECONDS, connect=5.0),
            follow_redirects=True,
            headers={
                "User-Agent": DDG_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
            },
        ) as client:
            resp = await client.get(DDG_HTML_ENDPOINT, params={"q": query})
    except httpx.TimeoutException:
        return None, "timeout"
    except httpx.HTTPError as e:
        return None, f"httpx_error:{type(e).__name__}"
    except Exception as e:  # pragma: no cover — defensive
        return None, f"unexpected:{type(e).__name__}"

    if resp.status_code == 429:
        return None, "rate_limited"
    if resp.status_code >= 500:
        return None, f"server_error:{resp.status_code}"
    if resp.status_code != 200:
        return None, f"http_{resp.status_code}"

    body = resp.text or ""
    lowered = body.lower()
    if any(tok in lowered for tok in _BLOCK_TOKENS) and len(body) < 4000:
        # Short body + block-y words = DDG showed us a CAPTCHA / block page
        # rather than real results.
        return None, "captcha"

    return body, None


def _parse_ddg_results(body: str, limit: int = 10) -> list[dict[str, str]]:
    """Parse the DDG HTML result blocks out of the response body."""
    results: list[dict[str, str]] = []
    for m in _RESULT_TITLE_RE.finditer(body):
        url = html.unescape(m.group(1)).strip()
        title = _strip_html_tags(m.group(2))
        if not title:
            continue
        results.append({"title": title, "url": url})
        if len(results) >= limit:
            break
    return results


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def _read_settings() -> tuple[bool, int]:
    """Return ``(enabled, penalty)`` with safe defaults on config failure."""
    try:
        from services.site_config import site_config
        enabled = site_config.get_bool(
            "title_originality_external_check_enabled", True,
        )
        penalty = site_config.get_int(
            "title_originality_external_penalty", -50,
        )
    except Exception:
        enabled, penalty = True, -50
    # Penalty is stored as a negative int in settings for human readability.
    # We surface the absolute value so the caller can apply it as a penalty
    # without worrying about sign.
    return enabled, abs(int(penalty))


async def check_external_title_duplicates(title: str) -> ExternalOriginalityResult:
    """Search DDG for the exact title; return a structured originality result.

    Flow:

    1. Bail out early if the feature is disabled in ``app_settings``.
    2. Check the process-local cache (keyed on normalised title).
    3. Hit the DDG HTML endpoint with the quoted title.
    4. Parse the response into ``{title, url}`` dicts.
    5. Compute similarity for each result; classify as verbatim / near /
       unrelated.
    6. Build + cache + return an :class:`ExternalOriginalityResult`.

    Any failure in step 3 or 4 is treated as fail-open: we log + bump the
    Prometheus counter and return an all-zero result with ``fail_open=True``
    so the pipeline continues.
    """
    if not title or not title.strip():
        return ExternalOriginalityResult()

    enabled, penalty = _read_settings()
    if not enabled:
        logger.debug("[TITLE_ORIG_EXT] External check disabled via settings")
        return ExternalOriginalityResult()

    key = _cache_key(title)
    cached = _cache_lookup(key)
    if cached is not None:
        logger.debug("[TITLE_ORIG_EXT] Cache hit for %r", title[:60])
        return cached

    body, reason = await _fetch_ddg_html(title)
    if body is None:
        logger.warning(
            "[TITLE_ORIG_EXT] Fail-open on DDG query (reason=%s): %r",
            reason, title[:60],
        )
        try:
            TITLE_ORIGINALITY_FAIL_OPEN.labels(reason=reason or "unknown").inc()
        except Exception:  # pragma: no cover — counter is fire-and-forget
            pass
        # Deliberately do NOT cache fail-open results — we want the next
        # call to try again rather than pretending we know the answer for
        # the next 24 hours.
        return ExternalOriginalityResult(
            fail_open=True, fail_reason=reason or "unknown",
        )

    results = _parse_ddg_results(body)
    probe = _normalise_for_compare(title)
    verbatim_match = False
    near_match = False
    matches: list[dict[str, str]] = []

    for r in results:
        ext_title = r.get("title", "")
        ext_norm = _normalise_for_compare(ext_title)
        if not ext_norm:
            continue
        if ext_norm == probe:
            verbatim_match = True
            matches.append(r)
            continue
        # Similarity ratio catches near-duplicates: "We Shipped It — Here's
        # How" vs "We Shipped It! Here Is How" should trigger a near-match.
        sim = SequenceMatcher(None, probe, ext_norm).ratio()
        if sim >= 0.90:
            # Very-close near-match — treat as verbatim for penalty purposes.
            verbatim_match = True
            matches.append(r)
        elif sim >= 0.80:
            near_match = True
            matches.append(r)

    result = ExternalOriginalityResult(
        verbatim_match=verbatim_match,
        near_match=near_match and not verbatim_match,
        penalty=penalty if verbatim_match else 0,
        matches=matches,
        fail_open=False,
    )
    _cache_store(key, result)

    if verbatim_match:
        logger.warning(
            "[TITLE_ORIG_EXT] Verbatim external duplicate for %r: %s",
            title[:60],
            matches[0].get("url") if matches else "?",
        )
    elif near_match:
        logger.info(
            "[TITLE_ORIG_EXT] Near-match external title for %r (%d candidates)",
            title[:60], len(matches),
        )
    else:
        logger.debug(
            "[TITLE_ORIG_EXT] No external duplicates for %r (%d results scanned)",
            title[:60], len(results),
        )

    return result
