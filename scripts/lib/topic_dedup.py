"""Shared topic deduplication utilities.

Used by daemon.py, scheduled-content.py, and any other script that needs
to check whether a candidate topic is too similar to existing content.
"""

import json
import re
import urllib.request


def normalize_words(text):
    """Lowercase, strip punctuation, split into words."""
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).split()


def get_ngrams(words, n):
    """Return set of n-grams (tuples of n consecutive words)."""
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def extract_template_base(topic):
    """Extract the template pattern from a topic, ignoring fill-in values.

    E.g. "The Hidden Costs of Local LLMs Nobody Talks About"
      -> "the hidden costs of ... nobody talks about"

    Returns the first 4 and last 4 words joined, which captures the template
    skeleton while ignoring the variable middle.
    """
    words = normalize_words(topic)
    if len(words) <= 6:
        return " ".join(words)
    return " ".join(words[:4]) + " ... " + " ".join(words[-4:])


def is_too_similar(topic, existing_topics):
    """Check if topic is too similar to any existing topic.

    Similarity checks:
    1. Exact match (case-insensitive)
    2. Shares 3+ consecutive words with an existing topic
    3. Same template skeleton (first 4 + last 4 words match)
    """
    topic_words = normalize_words(topic)
    topic_ngrams = get_ngrams(topic_words, 3)
    topic_base = extract_template_base(topic)

    for existing in existing_topics:
        existing_lower = existing.lower()
        # Exact match
        if topic.lower() == existing_lower:
            return True
        existing_words = normalize_words(existing)
        # 3+ consecutive word overlap
        existing_ngrams = get_ngrams(existing_words, 3)
        shared = topic_ngrams & existing_ngrams
        if len(shared) >= 1:
            return True
        # Same template skeleton
        if extract_template_base(existing) == topic_base:
            return True
    return False


def fetch_existing_topics(api_url, auth_header):
    """Fetch both recent task topics AND published post titles for dedup."""
    topics = set()
    # Recent tasks (pending, in-progress, awaiting approval, etc.)
    try:
        req = urllib.request.Request(
            f"{api_url}/api/tasks?limit=50",
            headers={"Authorization": auth_header},
        )
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
        for t in data.get("tasks", []):
            topic = t.get("topic", "")
            if topic:
                topics.add(topic)
    except Exception:
        pass
    # Published posts — check titles to avoid duplicating already-published content
    try:
        req = urllib.request.Request(
            f"{api_url}/api/posts?limit=100",
            headers={"Authorization": auth_header},
        )
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
        for p in data.get("posts", data if isinstance(data, list) else []):
            title = p.get("title", "")
            if title:
                topics.add(title)
    except Exception:
        pass
    return topics
