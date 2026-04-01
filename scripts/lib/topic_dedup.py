"""Shared topic deduplication utilities.

Used by daemon.py, scheduled-content.py, and any other script that needs
to check whether a candidate topic is too similar to existing content.

Supports two modes:
1. Fuzzy string matching (original) — fast, no external deps
2. Semantic similarity via pgvector embeddings (preferred) — compares against
   the 768-dim nomic-embed-text vectors already stored for published posts
"""

import json
import logging
import re
import urllib.request

logger = logging.getLogger(__name__)

# pgvector / Ollama configuration for semantic dedup
_PGVECTOR_DSN = "postgresql://gladlabs:gladlabs-brain-local@localhost:5433/gladlabs_brain"
_OLLAMA_URL = "http://127.0.0.1:11434"
_EMBED_MODEL = "nomic-embed-text"


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


# ---------------------------------------------------------------------------
# Semantic deduplication via pgvector embeddings
# ---------------------------------------------------------------------------

def _embed_via_ollama(text):
    """Get embedding vector from Ollama nomic-embed-text (synchronous).

    Returns list[float] (768 dims) or None on failure.
    """
    import httpx

    try:
        resp = httpx.post(
            f"{_OLLAMA_URL}/api/embed",
            json={"model": _EMBED_MODEL, "input": text},
            timeout=30.0,
        )
        resp.raise_for_status()
        embeddings = resp.json().get("embeddings", [])
        if embeddings:
            return embeddings[0]
    except Exception as e:
        logger.debug("Ollama embedding failed: %s", e)
    return None


def _search_pgvector(embedding, threshold=0.85, limit=3):
    """Search pgvector for posts with cosine similarity above threshold.

    Returns list of (similarity, text_preview, source_id) tuples, or None on failure.
    """
    import psycopg2

    vector_str = "[" + ",".join(str(v) for v in embedding) + "]"
    try:
        conn = psycopg2.connect(_PGVECTOR_DSN)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 1 - (embedding <=> %s::vector) AS similarity,
                           text_preview, source_id
                    FROM embeddings
                    WHERE source_table = 'posts'
                      AND 1 - (embedding <=> %s::vector) >= %s
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (vector_str, vector_str, threshold, vector_str, limit),
                )
                rows = cur.fetchall()
            return [(float(r[0]), r[1], r[2]) for r in rows]
        finally:
            conn.close()
    except Exception as e:
        logger.debug("pgvector search failed: %s", e)
    return None


def is_topic_duplicate_semantic(topic, threshold=0.68):
    """Check if a topic is semantically too similar to existing published posts.

    Uses Ollama nomic-embed-text to embed the candidate topic, then searches
    pgvector for published posts (source_table='posts') with cosine similarity
    above the threshold.

    Note: post embeddings are of full article content (not just titles), so
    title-to-article cosine similarity is compressed — unrelated topics score
    ~0.58-0.61, same-domain topics ~0.63-0.65, and near-duplicates ~0.68-0.72.
    The default threshold of 0.68 targets genuine duplicates.

    Falls back to False (allow the topic) if Ollama or pgvector are unavailable;
    callers should combine this with the existing fuzzy matching as a safety net.

    Args:
        topic: The candidate topic string.
        threshold: Cosine similarity threshold (0-1). Default 0.68.

    Returns:
        True if a semantically similar post already exists.
    """
    embedding = _embed_via_ollama(topic)
    if embedding is None:
        logger.warning("Semantic dedup unavailable (Ollama down?), skipping")
        return False

    results = _search_pgvector(embedding, threshold=threshold)
    if results is None:
        logger.warning("Semantic dedup unavailable (pgvector down?), skipping")
        return False

    if results:
        best_sim, preview, source_id = results[0]
        logger.info(
            "Semantic duplicate found: %.3f similarity — '%s' ~ '%s'",
            best_sim, topic[:50], (preview or source_id or "?")[:50],
        )
        return True

    return False
