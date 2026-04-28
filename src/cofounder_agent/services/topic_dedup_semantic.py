"""Semantic topic deduplication using sentence embeddings (#201).

Alternative engine alongside the legacy word-overlap dedup in
``services/topic_dedup.py``. Catches paraphrased duplicates that the
lexical-overlap baseline misses (e.g. "Top Cybersecurity Threats" vs
"Major Cyber Risks to Watch") by comparing sentence embeddings.

Engine selection
----------------

The operator picks the engine via ``app_settings.topic_dedup_engine``:

- ``word_overlap`` (default) → the legacy ``TopicDeduplicator``.
  Right call at small scale + when paraphrase coverage isn't worth
  the model-load cost.
- ``bertopic`` / ``semantic`` → this module. Loads
  ``all-MiniLM-L6-v2`` (~80MB) once on first call and keeps it in
  process memory. Slower first run, fast steady-state.

Why ``all-MiniLM-L6-v2``
------------------------

Small, fast, well-validated for sentence-similarity at our title
length (5-15 words). 80MB download vs the 300MB+ of the larger
mpnet variants. Easy to swap by setting
``app_settings.topic_dedup_embedding_model`` to e.g.
``all-mpnet-base-v2`` if recall starts mattering more than latency.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Iterable, Protocol

logger = logging.getLogger(__name__)


# Module-level model cache. Loading the sentence-transformer model
# is ~3-5 seconds on first call and ~80MB resident; we pay the cost
# once per worker process and reuse.
_model_lock = threading.Lock()
_model_cache: dict[str, Any] = {}


def _get_model(model_name: str) -> Any:
    """Lazy-load + cache a sentence-transformer model.

    Thread-safe: two callers racing on the same name see only one
    load; subsequent calls skip the lock entirely (fast path).
    """
    if model_name in _model_cache:
        return _model_cache[model_name]
    with _model_lock:
        if model_name not in _model_cache:
            from sentence_transformers import SentenceTransformer
            logger.info(
                "[topic_dedup_semantic] Loading sentence-transformer: %s "
                "(first call — subsequent calls reuse the cached model)",
                model_name,
            )
            _model_cache[model_name] = SentenceTransformer(model_name)
    return _model_cache[model_name]


class _TopicLike(Protocol):
    """Minimal shape topic-deduplication needs.

    Mirrors the same Protocol as ``services.topic_dedup`` so the two
    engines are drop-in interchangeable. Implementations exist on
    both ``services.topic_discovery.DiscoveredTopic`` and
    ``plugins.topic_source.DiscoveredTopic``.
    """

    title: str
    is_duplicate: bool


class SemanticDeduplicator:
    """Mark candidate topics as duplicates by sentence-embedding cosine
    similarity. API-compatible with ``services.topic_dedup.TopicDeduplicator``
    so callers can switch engines via app_settings without code changes.
    """

    DEFAULT_MODEL = "all-MiniLM-L6-v2"
    # Thresholds calibrated on real title pairs: paraphrased dupes
    # ("Top Cyber Threats" vs "Major Cyber Risks") land in 0.65-0.85,
    # unrelated topics score <0.15 with this model. 0.65 catches the
    # paraphrase class without flagging cross-topic neighbors.
    # Tunable via topic_dedup_*_threshold_semantic in app_settings.
    DEFAULT_EXISTING_THRESHOLD = 0.65
    DEFAULT_INTRA_BATCH_THRESHOLD = 0.65

    def __init__(self, pool: Any, *, site_config: Any) -> None:
        self.pool = pool
        self._site_config = site_config

    # ------------------------------------------------------------------
    # Public API — mirrors TopicDeduplicator
    # ------------------------------------------------------------------

    async def mark_duplicates(
        self, topics: list[_TopicLike],
    ) -> list[_TopicLike]:
        """Run both passes — vs-existing + intra-batch.

        Mutates in place; returns the same list so callers can chain.
        """
        if not self.pool or not topics:
            return topics
        await self.mark_against_existing(topics)
        self.mark_intra_batch(topics)
        return topics

    async def mark_against_existing(
        self, topics: list[_TopicLike],
    ) -> list[_TopicLike]:
        """Mark topics whose embedding is too close to a published or
        in-flight title. Threshold via
        ``topic_dedup_existing_threshold_semantic`` (default 0.85)."""
        if not self.pool or not topics:
            return topics

        existing = await self._load_existing_titles()
        if not existing:
            return topics

        candidate_titles = [t.title for t in topics]
        all_titles = candidate_titles + list(existing)

        embeddings = self._embed(all_titles)
        n_candidates = len(candidate_titles)
        candidate_embs = embeddings[:n_candidates]
        existing_embs = embeddings[n_candidates:]

        threshold = self._get_threshold(
            "topic_dedup_existing_threshold_semantic",
            self.DEFAULT_EXISTING_THRESHOLD,
        )

        # Cosine similarity matrix (candidates × existing). Embeddings
        # from sentence-transformers are unit-normalized by default so
        # dot product == cosine similarity.
        import numpy as np
        similarities = np.dot(candidate_embs, existing_embs.T)

        existing_titles_list = list(existing)
        for i, topic in enumerate(topics):
            if topic.is_duplicate:
                continue
            row = similarities[i]
            best_idx = int(np.argmax(row))
            best_score = float(row[best_idx])
            if best_score >= threshold:
                topic.is_duplicate = True
                logger.info(
                    "[DEDUP/semantic] vs-existing: %r ≈ %r "
                    "(cosine=%.3f, threshold=%.2f)",
                    topic.title[:40], existing_titles_list[best_idx][:40],
                    best_score, threshold,
                )
        return topics

    def mark_intra_batch(
        self, topics: list[_TopicLike],
    ) -> list[_TopicLike]:
        """Mark candidates that are semantically near another in the
        same scrape. Threshold via
        ``topic_dedup_intra_batch_threshold_semantic`` (default 0.85).
        Synchronous — no DB hit."""
        if not topics or len(topics) < 2:
            return topics

        threshold = self._get_threshold(
            "topic_dedup_intra_batch_threshold_semantic",
            self.DEFAULT_INTRA_BATCH_THRESHOLD,
        )

        titles = [t.title for t in topics]
        embeddings = self._embed(titles)

        import numpy as np
        # Upper-triangular similarity matrix — pair (i, j) only
        # checked once with j > i. Lazy short-circuit: skip pairs
        # where t1 is already a duplicate.
        sims = np.dot(embeddings, embeddings.T)

        for i, t1 in enumerate(topics):
            if t1.is_duplicate:
                continue
            for j in range(i + 1, len(topics)):
                t2 = topics[j]
                if t2.is_duplicate:
                    continue
                score = float(sims[i, j])
                if score >= threshold:
                    t2.is_duplicate = True
                    logger.info(
                        "[DEDUP/semantic] Intra-batch: %r ≈ %r "
                        "(cosine=%.3f, threshold=%.2f)",
                        t2.title[:40], t1.title[:40], score, threshold,
                    )
        return topics

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _embed(self, texts: list[str]) -> Any:
        """Encode texts to a (N, D) numpy array. Uses the cached model."""
        model_name = self._get_model_name()
        model = _get_model(model_name)
        return model.encode(texts, normalize_embeddings=True, show_progress_bar=False)

    def _get_model_name(self) -> str:
        """Resolve the embedding model name from app_settings, with
        fallback to the module default."""
        try:
            return self._site_config.get(
                "topic_dedup_embedding_model", self.DEFAULT_MODEL,
            ) or self.DEFAULT_MODEL
        except Exception:
            return self.DEFAULT_MODEL

    def _get_threshold(self, key: str, default: float) -> float:
        try:
            return float(self._site_config.get_float(key, default))
        except Exception:
            return default

    async def _load_existing_titles(self) -> set[str]:
        """Lift the same loader the lexical engine uses.

        Mirrors ``TopicDeduplicator._load_existing_titles`` exactly so
        the two engines compare against the identical corpus — only
        the similarity math differs.
        """
        rows = await self.pool.fetch(
            "SELECT title FROM posts WHERE status = 'published'"
        )
        published_titles = {r["title"] for r in rows if r.get("title")}

        try:
            dedup_hours = self._site_config.get_int(
                "qa_topic_dedup_hours", 48,
            )
        except Exception:
            dedup_hours = 48

        task_rows = await self.pool.fetch(
            """
            SELECT topic, title FROM content_tasks
            WHERE created_at > NOW() - ($1 || ' hours')::interval
              AND status IN ('pending', 'in_progress', 'awaiting_approval', 'published')
            """,
            str(dedup_hours),
        )
        pending_topics: set[str] = set()
        for r in task_rows:
            if r.get("topic"):
                pending_topics.add(r["topic"])
            if r.get("title"):
                pending_topics.add(r["title"])

        return published_titles | pending_topics


# ---------------------------------------------------------------------------
# Engine selector
# ---------------------------------------------------------------------------


def get_deduplicator(pool: Any, *, site_config: Any) -> Any:
    """Return the operator-selected deduplicator engine.

    ``app_settings.topic_dedup_engine`` picks between:
    - ``word_overlap`` (default) → ``services.topic_dedup.TopicDeduplicator``
    - ``bertopic`` / ``semantic`` → ``SemanticDeduplicator``

    Either return value satisfies the same ``mark_duplicates`` /
    ``mark_against_existing`` / ``mark_intra_batch`` API, so callers
    can swap without conditional logic.
    """
    try:
        engine = (site_config.get("topic_dedup_engine", "word_overlap") or "word_overlap").lower()
    except Exception:
        engine = "word_overlap"

    if engine in ("bertopic", "semantic"):
        return SemanticDeduplicator(pool, site_config=site_config)

    from services.topic_dedup import TopicDeduplicator
    return TopicDeduplicator(pool, site_config=site_config)


__all__ = [
    "SemanticDeduplicator",
    "get_deduplicator",
]


# Used by the type checker / for documentation.
_ = Iterable  # noqa: F841
