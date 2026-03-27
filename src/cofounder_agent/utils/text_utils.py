"""
Text Processing Utilities

Shared helpers for keyword extraction, text normalization, and content
parsing used across seo_content_generator, unified_metadata_service,
quality_service, approval_routes, and task_routes.
"""

import json
import re
from typing import List, Optional, Tuple, Union

# ---------------------------------------------------------------------------
# Stopwords
# ---------------------------------------------------------------------------

#: Common English words that carry no SEO value.
#: Taken from the canonical list in seo_content_generator._extract_keywords.
_STOPWORDS: frozenset = frozenset(
    {
        # Pronouns / determiners
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "must",
        "i",
        "me",
        "my",
        "we",
        "you",
        "your",
        "he",
        "she",
        "it",
        "they",
        "them",
        "this",
        "that",
        "these",
        "those",
        "which",
        "what",
        "who",
        "where",
        "when",
        "why",
        "with",
        "from",
        "by",
        "about",
        "as",
        "just",
        "only",
        "so",
        "than",
        "very",
        # Common verbs
        "can",
        "make",
        "made",
        "use",
        "used",
        "say",
        "said",
        "get",
        "got",
        "go",
        "went",
        "come",
        "came",
        "take",
        "took",
        "know",
        "knew",
        "think",
        "thought",
        # Generic / low-value nouns
        "data",
        "information",
        "content",
        "post",
        "article",
        "blog",
        "website",
        "page",
        "thing",
        "things",
        "stuff",
        "way",
        "time",
        "year",
        "day",
        "week",
        "month",
        # Adverbs / conjunctions / prepositions
        "also",
        "more",
        "most",
        "some",
        "any",
        "all",
        "each",
        "every",
        "other",
        "first",
        "second",
        "third",
        "last",
        "new",
        "old",
        "right",
        "left",
        "good",
        "bad",
        "like",
        "such",
        "example",
        "however",
        "therefore",
        "because",
        "while",
        "another",
        "through",
        "during",
        "before",
        "after",
        "between",
        "above",
        "below",
        "even",
        "than",
        "then",
        "there",
        "here",
        "now",
        "today",
        "their",
        "shall",
        "within",
        "until",
        "among",
        "via",
        "throughout",
        "toward",
        "towards",
        "upon",
        "without",
        "against",
    }
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_keywords_from_text(text: str, count: int = 5) -> List[str]:
    """
    Extract the most frequent meaningful keywords from *text*.

    Uses word-frequency analysis after stripping Markdown syntax and
    filtering through a comprehensive English stop-word list.  Only words
    that are 4–20 characters long and appear at least twice in *text* are
    considered candidates; single-occurrence terms are excluded as noise.

    This is the canonical implementation; call it instead of the private
    ``_extract_keywords`` / ``_extract_keywords_from_content`` helpers in
    individual service files.

    Args:
        text:  Raw content string (may contain Markdown formatting).
        count: Maximum number of keywords to return.

    Returns:
        List of keywords sorted by descending frequency, at most *count* items.
        Returns an empty list if no qualifying keywords are found.

    Examples:
        >>> extract_keywords_from_text("Python Python Python rocks rocks", count=2)
        ['python', 'rocks']
    """
    # Strip common Markdown punctuation before tokenising
    clean = re.sub(r"[#*`_\-\[\](){}]", "", text).lower()
    words = re.findall(r"\b[a-z]{4,}\b", clean)

    freq: dict = {}
    for word in words:
        if word not in _STOPWORDS and 4 <= len(word) <= 20:
            freq[word] = freq.get(word, 0) + 1

    # Require at least 2 occurrences to reduce noise
    candidates = [(w, f) for w, f in freq.items() if f >= 2]
    candidates.sort(key=lambda x: x[1], reverse=True)
    return [w for w, _ in candidates[:count]]


def extract_keywords_from_title(title: str, count: int = 7) -> List[str]:
    """
    Fallback keyword extraction for when only the post title is available.

    Removes a small set of common English function words and returns the
    remaining content words, up to *count* items.

    This replaces ``UnifiedMetadataService._extract_keywords_fallback``.

    Args:
        title: Post title string.
        count: Maximum number of keywords to return.

    Returns:
        List of content words from the title, or ``[title[:20]]`` if none
        remain after filtering.
    """
    _TITLE_STOPWORDS = frozenset(
        {"a", "an", "the", "and", "or", "but", "is", "are", "to", "of", "in", "on", "for"}
    )
    words = title.lower().split()
    keywords = [w.strip(".,;:") for w in words if w not in _TITLE_STOPWORDS and len(w) > 3]
    return keywords[:count] if keywords else [title[:20]]


def extract_title_from_content(content: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract a leading Markdown heading from *content* and return it separately.

    LLMs often prefix their output with a Markdown title such as::

        # Building a PC in 2026: A Comprehensive Guide

    This function detects that pattern, strips the heading line, and returns
    both the extracted title and the remaining body text.

    Args:
        content: Raw generated content string (may be ``None`` or empty).

    Returns:
        A ``(title, cleaned_content)`` tuple.  *title* is ``None`` when no
        heading is found; *cleaned_content* is the original *content* in that
        case (not a copy).

    Examples:
        >>> extract_title_from_content("# My Title\\n\\nContent here")
        ('My Title', 'Content here')
        >>> extract_title_from_content("No title here")
        (None, 'No title here')
    """
    if not content:
        return None, content

    match = re.match(r"^#+\s+(.+?)(?:\n|$)", content.strip())
    if match:
        title = match.group(1).strip()
        cleaned = re.sub(r"^#+\s+.+?(?:\n|$)", "", content.strip(), count=1)
        return title, cleaned.strip()

    return None, content


def normalize_seo_keywords(keywords: Union[str, list, None]) -> str:
    """
    Normalise an SEO keyword value to a comma-separated string for DB storage.

    The raw keyword value coming out of the AI pipeline can be:
    - A JSON-encoded list string: ``'["keyword1", "keyword2"]'``
    - A plain comma-separated string: ``"keyword1, keyword2"``
    - A Python list: ``["keyword1", "keyword2"]``
    - ``None`` or empty

    Args:
        keywords: Raw SEO keyword value in any of the above formats.

    Returns:
        A comma-separated string of trimmed, non-empty keywords, or ``""``
        when the input is empty / unparseable.

    Examples:
        >>> normalize_seo_keywords('["ai", "machine learning"]')
        'ai, machine learning'
        >>> normalize_seo_keywords(["ai", "ml"])
        'ai, ml'
        >>> normalize_seo_keywords("ai, ml")
        'ai, ml'
    """
    if not keywords:
        return ""
    if isinstance(keywords, str):
        try:
            parsed = json.loads(keywords)
            if isinstance(parsed, list):
                return ", ".join(str(kw).strip() for kw in parsed if kw)
            return keywords
        except (json.JSONDecodeError, TypeError):
            return keywords
    if isinstance(keywords, list):
        return ", ".join(str(kw).strip() for kw in keywords if kw)
    return ""
