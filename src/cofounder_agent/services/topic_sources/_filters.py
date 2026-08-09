"""Shared filters for TopicSource plugins.

Every source produces raw titles that need the same downstream cleanup:
classify into a category, reject news/merch/junk, normalize whitespace.
Pulling this logic out of ``services.topic_discovery`` so each source
can depend on a narrow helper module rather than the full dispatcher.
"""

from __future__ import annotations

import re

# Category-specific search queries for DuckDuckGo. This is the home now
# (the planned end-state noted when the re-export shim was added); the
# legacy ``services.topic_discovery`` re-exports it for backward compat
# until that module is retired (``project_topic_discovery_consolidation``).
CATEGORY_SEARCHES = {
    "technology": [
        "latest AI developer tools 2026",
        "new programming frameworks 2026",
        "cloud infrastructure trends",
    ],
    "startup": [
        "solo founder success stories 2026",
        "bootstrapped SaaS launch tips",
        "indie hacker revenue milestones",
    ],
    "security": [
        "latest cybersecurity threats developers",
        "API security best practices 2026",
        "zero trust architecture practical guide",
    ],
    "engineering": [
        "software architecture patterns 2026",
        "developer productivity engineering",
        "CI/CD pipeline best practices",
    ],
    "insights": [
        "state of software development 2026",
        "developer survey results latest",
        "tech industry trends predictions",
    ],
    "business": [
        "AI business automation 2026",
        "content marketing for developers",
        "SaaS metrics that matter",
    ],
    "hardware": [
        "best GPU for AI inference 2026",
        "AMD vs NVIDIA gaming benchmarks",
        "PC hardware news reviews 2026",
    ],
    "gaming": [
        "upcoming PC games 2026",
        "indie game development news",
        "game engine updates Unreal Unity Godot",
    ],
}

# Patterns that indicate news / current events / merch / personal anecdotes
# — not evergreen editorial content. Moved verbatim from TopicDiscovery._NEWS_PATTERNS.
_NEWS_PATTERNS = [
    r"\b(?:police|arrest|charged|sentenced|indicted|convicted|alleged)\b",
    r"\b(?:lawsuit|sued|court|judge|ruling|verdict)\b",
    r"\b(?:killed|dead|dies|died|shooting|crash)\b",
    r"\b(?:election|voted|senator|congress|parliament|president)\b",
    r"\b(?:earthquake|hurricane|flood|wildfire|tornado)\b",
    r"\b(?:shirt|merch|sticker|swag|coupon|discount|sale|buy now)\b",
    r"\b(?:my experience|my journey|i tried|i built|dear diary)\b",
]
_NEWS_RE = [re.compile(p, re.IGNORECASE) for p in _NEWS_PATTERNS]


def classify_category(title: str) -> str:
    """Classify a title into a category by keyword overlap.

    Returns ``"technology"`` when no category scores above zero — the
    sensible default for tech-adjacent content. Uses simple keyword
    counting, not vector similarity: fast, deterministic, no LLM call.
    """
    title_lower = title.lower()
    scores: dict[str, int] = {}
    for cat, searches in CATEGORY_SEARCHES.items():
        keywords = " ".join(searches).lower().split()
        score = sum(1 for kw in keywords if kw in title_lower)
        scores[cat] = score
    best = max(scores, key=lambda k: scores.get(k, 0)) if scores else "technology"
    return best if scores.get(best, 0) > 0 else "technology"


def is_news_or_junk(title: str) -> bool:
    """Reject breaking news, current events, personal anecdotes, and merch."""
    for pattern in _NEWS_RE:
        if pattern.search(title):
            return True
    # Too short to be a real topic
    if len(title.split()) < 4:
        return True
    return False


# --- search-query junk (distinct from title junk) ------------------------
#
# ``is_news_or_junk`` is tuned for scraped article TITLES and rejects anything
# under four words. Search queries are short by nature, and the short ones are
# the valuable ones: "5090 local llm" (3 words) converts at 7.1% CTR, "fast api
# best practices" at 20%. Running queries through the title filter would throw
# away exactly the high-intent traffic. Hence a separate predicate.
#
# What it rejects is traffic that isn't reader demand at all. From the
# 2026-08-09 GSC audit: of the impressions Google will name, over half came from
# one cluster of 103 reorderings of "cadquery official documentation parametric
# cad python" — 3,696 impressions, ZERO clicks. Plus `site:` audits, repo paths
# (`zhanymkanov/fastapi-best-practices`), and numeric fragments (`8000-6400`).
# None of those are topics anybody searched for wanting to read something.

# Google search operators. A query using one is a tool or an operator auditing
# the index, never a reader. `site:www.gladlabs.io` was live in the topic
# source's output — it would have become an article titled "Site:Www.Gladlabs.Io".
_SEARCH_OPERATOR_RE = re.compile(
    r"\b(?:site|inurl|intitle|allintitle|allinurl|intext|allintext"
    r"|filetype|ext|cache|related|link|imagesize|before|after|source)\s*:",
    re.IGNORECASE,
)

# Explicit URLs and repo/file paths — navigation, not research.
# Deliberately NOT a bare-domain rule: `\S+\.(com|io|dev)` would reject
# "socket.io tutorial" and "next.js routing", which are real topics.
_URLISH_RE = re.compile(r"https?://|\bwww\.|\S+/\S+")

# Nothing to write about: no letters at all (`8000-6400`, `2026 2027`).
_NO_LETTERS_RE = re.compile(r"^[^A-Za-z]*$")


def is_junk_search_query(query: str, *, brand_tokens: tuple[str, ...] = ()) -> bool:
    """True when a GSC query is not reader demand and must not become a topic.

    ``brand_tokens`` are matched as substrings and should be full brand
    phrases / domains ("glad labs", "gladlabs.io"), never bare words — a
    single-token brand like "labs" would reject legitimate queries. The caller
    derives them from ``site_name`` / ``company_name`` / ``site_domain``.
    """
    q = (query or "").strip()
    if not q:
        return True
    if _NO_LETTERS_RE.match(q):
        return True
    if _SEARCH_OPERATOR_RE.search(q):
        return True
    if _URLISH_RE.search(q):
        return True
    lowered = q.lower()
    return any(t and t in lowered for t in brand_tokens)


def permutation_clusters(queries: list[str], *, min_variants: int) -> set[str]:
    """Return the queries that belong to a same-token-bag reordering cluster.

    A human does not search six words, then the same six in another order, then
    another — 103 times. Grouping on the sorted token bag catches the whole
    cluster no matter which ordering shows up first, which a per-query rule
    cannot do. ``min_variants`` is the number of distinct orderings that marks a
    bag as machine-generated; two phrasings of the same idea are normal, so keep
    the default well above 2.
    """
    if min_variants < 2:
        return set()
    bags: dict[tuple[str, ...], set[str]] = {}
    for q in queries:
        tokens = tuple(sorted(t for t in (q or "").lower().split() if t))
        if not tokens:
            continue
        bags.setdefault(tokens, set()).add(q)
    return {
        q for variants in bags.values() if len(variants) >= min_variants for q in variants
    }


def rewrite_as_blog_topic(title: str) -> str:
    """Clean a scraped title into an evergreen blog topic.

    Returns the empty string for titles that should be filtered out —
    caller uses that to decide whether to keep the topic.

    Originally moved verbatim from ``TopicDiscovery._rewrite_as_blog_topic``.
    A "strip trailing author name" pass (2026-04-13) was removed here
    (2026-07-13): it matched any 2-3 Title-Case words preceded by a
    lowercase character, which is indistinguishable from an ordinary
    headline ending ("...Are Wearing Us Out") and was silently mangling
    real titles on every tap run — the single largest source of
    ``topic_sanity_rejected`` findings across devto/hackernews/web_search.
    """
    # Reject product launches / announcements
    if re.match(r"^(?:Launch|Show|Ask|Tell)\s+HN\b", title, re.IGNORECASE):
        return ""
    # Reject news / current events / junk
    if is_news_or_junk(title):
        return ""
    # Reject academic papers / government publications
    if re.search(
        r"(?:Special Publication|NIST|RFC \d{3,}|arXiv|doi\.org|ISBN)",
        title, re.IGNORECASE,
    ):
        return ""
    # Reject titles that are mostly ALLCAPS (academic / government docs)
    words = title.split()
    if len(words) >= 3:
        caps_words = sum(1 for w in words if w.isupper() and len(w) > 2)
        if caps_words / len(words) > 0.4:
            return ""
    # Remove bracket prefixes: [Show HN], [OC], etc.
    title = re.sub(r"^\[.*?\]\s*", "", title)
    # Remove site-name suffixes: | Site Name, - Blog Name
    title = re.sub(r"\s*\|.*$", "", title)
    title = re.sub(r"\s*[-–—]\s*\w+\.?\w*$", "", title)
    # Remove leading product-name + colon ("Freestyle: Sandboxes..." → "Sandboxes...")
    title = re.sub(r"^[A-Z][\w]*(?:\s+[A-Z][\w]*)?\s*[:–—]\s*", "", title)
    # Reject if too short after cleanup — both a character floor (catches
    # single long words) and a word-count floor (catches short sequences
    # of short words), since the suffix-strip passes above can legitimately
    # collapse a "Brand | Tagline"-style title down to just the brand name.
    title = title.strip()
    if len(title) < 10 or len(title.split()) < 2:
        return ""
    return title
