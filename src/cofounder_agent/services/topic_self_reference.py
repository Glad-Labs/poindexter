"""Self-reference gate — blocks topic candidates that point back at the
operator's own web properties.

Incident (2026-07-27): the ``glad-labs`` discovery batch ``6322bd8b`` ranked
the operator's **own homepage** (``Glad Labs - AI & Technology Insights``,
https://www.gladlabs.io/) as its #1 external topic candidate. Ten such
candidates had accumulated in ``topic_pool`` — the homepage, ``/product``,
``/posts``, ``/category/technology``, an already-**published** post, plus the
brand's X profile and Crunchbase entry.

Two independent defects produced them, and this module is the second half of
the fix:

1. ``WebSearchSource`` derived its search queries from the niche's
   ``target_audience_tags`` — audience personas, not subject keywords — so it
   literally searched the web for ``"Glad Labs indie-devs"``. The search
   engine discarded the persona token as noise and did entity lookup on the
   brand name. Fixed at the source (see ``topic_sources/web_search.py``);
   that stops the *cause*.
2. Nothing anywhere in the ingest path rejected a candidate whose URL was the
   operator's own site. This module is that guard — a backstop that holds no
   matter how a future source resolves its queries.

Scope is deliberately narrow: **host matching only**, no title/brand-name
heuristics. A published post legitimately discussing the operator's own brand
is valid topic material; a link *to* the operator's own site is not. Matching
on the brand string in a title would reject the former along with the latter.

Consequence worth stating plainly: a host filter cannot catch brand-owned
properties on third-party domains (``x.com/_gladlabs``,
``crunchbase.com/organization/gladlabs``). Those were dragged in by the
brand-name queries of defect 1 and disappear once queries stop naming the
brand. Operators who still want them blocked list the specific hosts in
``topic_source_excluded_domains``.

Configuration (``feedback_db_first_config``):

- Owned hosts are derived automatically from ``site_url`` /
  ``public_site_url``, so a fresh tenant is protected with zero setup.
- ``topic_source_excluded_domains`` — optional comma-separated extra hosts
  for additional owned properties. Default empty.

Matching is subdomain-aware (``blog.example.com`` matches an owned
``example.com``) and ``www.``-insensitive, and it never matches a
lookalike suffix: ``notexample.com`` is not ``example.com``.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

EXCLUDED_DOMAINS_KEY = "topic_source_excluded_domains"

# Settings keys that name a site the operator owns. Both are seeded on a
# standard install; either alone is enough to derive the owned host.
_SITE_URL_KEYS = ("site_url", "public_site_url")

REASON_SELF_REFERENTIAL = "self_referential_url"


def normalize_host(url: str | None) -> str:
    """Extract a comparable host from a URL or bare hostname.

    Lowercases, drops scheme / port / credentials, and strips a leading
    ``www.``. Returns ``""`` when nothing host-like can be parsed — callers
    treat that as "unknown", never as a match.

    Non-string input yields ``""`` rather than being coerced. A settings stub
    or test double that hands back a non-string (``MagicMock``, ``None``, an
    int) must switch the gate *off* for that key, never synthesise a bogus
    owned host that silently starts matching real candidates.
    """
    if not url or not isinstance(url, str):
        return ""
    raw = url.strip()
    if not raw:
        return ""
    # urlparse only populates .netloc when a scheme is present; settings and
    # operator-supplied exclusion entries are often bare hosts ("example.com").
    if "//" not in raw:
        raw = f"//{raw}"
    try:
        host = urlparse(raw).hostname or ""
    except ValueError:
        # Malformed URL (e.g. an unparseable IPv6 literal). Unknown, not a match.
        logger.debug("[topic_self_reference] unparseable url %r", url)
        return ""
    host = host.lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def resolve_owned_hosts(site_config: Any) -> frozenset[str]:
    """Collect the hosts this install considers its own.

    Derived from ``site_url`` / ``public_site_url`` plus any extra hosts in
    ``topic_source_excluded_domains``. Tolerates a full ``SiteConfig``, a
    dict-style stub, or ``None`` — mirroring ``topic_sanity.resolve_min_alpha_words``,
    since the same call sites construct all three.
    """
    if site_config is None:
        return frozenset()

    getter = getattr(site_config, "get", None)
    if not callable(getter):
        return frozenset()

    hosts: set[str] = set()
    for key in _SITE_URL_KEYS:
        try:
            host = normalize_host(getter(key, "") or "")
        except Exception:
            # A settings backend that raises must not disable the guard for
            # the other key; fall through with what we have.
            logger.warning(
                "[topic_self_reference] failed reading %s; continuing", key,
                exc_info=True,
            )
            continue
        if host:
            hosts.add(host)

    try:
        extra = getter(EXCLUDED_DOMAINS_KEY, "") or ""
    except Exception:
        logger.warning(
            "[topic_self_reference] failed reading %s; using derived hosts only",
            EXCLUDED_DOMAINS_KEY, exc_info=True,
        )
        extra = ""
    if isinstance(extra, str):
        for entry in extra.split(","):
            host = normalize_host(entry)
            if host:
                hosts.add(host)

    return frozenset(hosts)


def is_self_referential(url: str | None, owned_hosts: frozenset[str]) -> bool:
    """True when ``url`` points at one of ``owned_hosts`` (or a subdomain).

    An empty/unparseable URL is never self-referential: sources that yield no
    URL (``knowledge``, ``internal_rag``) must pass through untouched.
    """
    if not owned_hosts:
        return False
    host = normalize_host(url)
    if not host:
        return False
    for owned in owned_hosts:
        # Exact match, or a subdomain of an owned host. The explicit dot stops
        # "notexample.com" from matching "example.com".
        if host == owned or host.endswith(f".{owned}"):
            return True
    return False


__all__ = [
    "EXCLUDED_DOMAINS_KEY",
    "REASON_SELF_REFERENTIAL",
    "is_self_referential",
    "normalize_host",
    "resolve_owned_hosts",
]
