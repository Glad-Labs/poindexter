"""Shared filters for TopicSource plugins.

Every source produces raw titles that need the same downstream cleanup:
classify into a category, reject news/merch/junk, normalize whitespace.
Pulling this logic out of ``services.topic_discovery`` so each source
can depend on a narrow helper module rather than the full dispatcher.
"""

from __future__ import annotations

import re

# Re-export from the legacy home so nothing else in the codebase
# breaks while we migrate. These should eventually live here only.
from services.topic_discovery import CATEGORY_SEARCHES


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
    best = max(scores, key=scores.get) if scores else "technology"
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


def rewrite_as_blog_topic(title: str) -> str:
    """Clean a scraped title into an evergreen blog topic.

    Returns the empty string for titles that should be filtered out —
    caller uses that to decide whether to keep the topic.

    Moved verbatim from ``TopicDiscovery._rewrite_as_blog_topic`` so
    sources don't have to instantiate the dispatcher just to reach
    this helper.
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
    # Strip trailing author names when title is long enough
    if len(title.split()) >= 6:
        title = re.sub(
            r"(?<=[a-z.,;:)])\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2}\s*$",
            "", title,
        )
    # Reject if too short after cleanup
    title = title.strip()
    if len(title) < 10:
        return ""
    return title
