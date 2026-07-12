"""Content-embedding topic deduplication (spec §1.4 escalation, 2026-07-12).

Third engine alongside the lexical (:mod:`services.topic_dedup`) and title-
embedding (:mod:`services.topic_dedup_semantic`) dedupers. Where those compare a
candidate's TITLE against existing TITLES, this compares the candidate against
published-post CONTENT via the same pgvector search the ``create_post`` dedup
guard uses (:mod:`services.topic_dedup_guard` →
``poindexter.memory.MemoryClient.find_similar_posts`` over ``source_table='posts'``).

Why: title similarity undercounts content duplication. The VRAM near-duplicate
that motivated this (task ``b740e4b8``) scored only 0.55 title-similarity against
"The VRAM Currency Problem" — the post it most heavily re-stated — but 0.735 on
content. Title dedup would waste a full ~17-min generation; content dedup
suppresses the topic at proposal time.

Selected via ``app_settings.topic_dedup_engine='content_embedding'`` (the default
as of 2026-07-12). Threshold ``topic_dedup_existing_threshold_content`` (default
0.70, calibrated against the live corpus: the VRAM cluster scores 0.65-0.735,
unrelated controls <=0.60).

The vs-existing pass uses content embeddings; the intra-batch pass (same-scrape
near-duplicate candidates, which have no content yet) delegates to the lexical
word-overlap engine.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# app_settings key + fallback. Default seeded in
# ``services.settings_defaults.DEFAULTS`` so production reads the DB value; this
# constant is the last-resort fallback when a SiteConfig has no row.
SETTING_KEY = "topic_dedup_existing_threshold_content"
DEFAULT_EXISTING_THRESHOLD = 0.70


class _TopicLike(Protocol):
    """Minimal shape topic-deduplication needs. Mirrors the other two engines so
    all three are drop-in interchangeable via ``get_deduplicator``."""

    title: str
    is_duplicate: bool


class ContentEmbeddingDeduplicator:
    """Mark candidate topics as duplicates by CONTENT-embedding similarity to
    published posts. API-compatible with ``services.topic_dedup.TopicDeduplicator``
    and ``services.topic_dedup_semantic.SemanticDeduplicator``.
    """

    def __init__(self, pool: Any, *, site_config: Any) -> None:
        self.pool = pool
        self._site_config = site_config

    def _threshold(self) -> float:
        try:
            return float(
                self._site_config.get_float(SETTING_KEY, DEFAULT_EXISTING_THRESHOLD)
            )
        except Exception:
            return DEFAULT_EXISTING_THRESHOLD

    async def mark_duplicates(self, topics: list[_TopicLike]) -> list[_TopicLike]:
        """Run both passes — content vs-existing + lexical intra-batch. Mutates
        in place; returns the same list so callers can chain."""
        if not topics:
            return topics
        await self.mark_against_existing(topics)
        self.mark_intra_batch(topics)
        return topics

    async def mark_against_existing(self, topics: list[_TopicLike]) -> list[_TopicLike]:
        """Mark candidates whose CONTENT-similarity to a published post clears
        ``topic_dedup_existing_threshold_content``.

        Reuses ``MemoryClient.find_similar_posts`` (the same search behind the
        ``create_post`` dedup guard), which applies ``min_similarity`` as a true
        cosine floor at the base pgvector level — so any hit it returns is a
        genuine content near-duplicate. Fail-open: a broken search must never
        block topic discovery.
        """
        if not topics:
            return topics
        fresh = [t for t in topics if not t.is_duplicate and (t.title or "").strip()]
        if not fresh:
            return topics
        threshold = self._threshold()
        try:
            from poindexter.memory import MemoryClient

            async with MemoryClient() as mem:
                for topic in fresh:
                    title = topic.title.strip()
                    try:
                        hits = await mem.find_similar_posts(
                            title, limit=1, min_similarity=threshold
                        )
                    except Exception as exc:  # noqa: BLE001 — fail open per candidate
                        logger.warning(
                            "[DEDUP/content] search failed for %r — allowing "
                            "(fail-open): %s",
                            title[:60],
                            exc,
                        )
                        continue
                    if hits:
                        topic.is_duplicate = True
                        logger.info(
                            "[DEDUP/content] vs-existing: %r ~ published post "
                            "(content cosine >= %.2f)",
                            title[:40],
                            threshold,
                        )
        except Exception as exc:  # noqa: BLE001 — MemoryClient construction failed
            logger.warning(
                "[DEDUP/content] MemoryClient unavailable — skipping content "
                "dedup (fail-open): %s",
                exc,
            )
        return topics

    def mark_intra_batch(self, topics: list[_TopicLike]) -> list[_TopicLike]:
        """Same-scrape near-duplicate candidates. Candidates have no content
        yet, so delegate to the lexical word-overlap intra-batch pass rather
        than embedding bare titles."""
        from services.topic_dedup import TopicDeduplicator

        # Delegate for the side effect (marks is_duplicate in place). The two
        # engines' structural _TopicLike protocols are identical, so the
        # arg-type note is a Pyright list-invariance false positive.
        TopicDeduplicator(self.pool, site_config=self._site_config).mark_intra_batch(
            topics  # type: ignore[arg-type]
        )
        return topics


__all__ = [
    "ContentEmbeddingDeduplicator",
    "SETTING_KEY",
    "DEFAULT_EXISTING_THRESHOLD",
]
