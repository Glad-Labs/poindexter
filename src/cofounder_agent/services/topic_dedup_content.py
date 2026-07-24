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

    Two vs-existing passes (2026-07-24, incident task ``342a26b7``):

    1. **Recent-coverage** (:mod:`services.topic_recent_coverage`) — the
       candidate composite (title + summary/angle when the wrapper exposes a
       ``summary`` attr) against composites of recently published posts and
       in-flight tasks. Like-for-like short text separates true re-treads
       (0.79-0.86) from same-domain neighbours (≤0.70) where title-vs-content
       cannot; matches are NAMED (``duplicate_of`` annotation + log line).
    2. **Content-embedding** — the original title-vs-post-content pgvector
       search at ``topic_dedup_existing_threshold_content`` (0.70, calibrated
       on the VRAM cluster), kept unchanged for the re-tread-with-new-angle
       class the composite pass can't see.

    ``niche_slug`` scopes the recent-coverage refs to the candidate's niche
    (+ niche-less legacy refs) so a dev_diary founder-log entry never blocks
    a glad-labs evergreen treatment of the same work.
    """

    def __init__(
        self, pool: Any, *, site_config: Any, niche_slug: str | None = None,
    ) -> None:
        self.pool = pool
        self._site_config = site_config
        self._niche_slug = niche_slug

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
                await self._mark_recent_coverage(fresh, mem)
                for topic in fresh:
                    if topic.is_duplicate:
                        continue
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
                        hit_meta = getattr(hits[0], "metadata", None) or {}
                        logger.info(
                            "[DEDUP/content] vs-existing: %r ~ published post "
                            "%r (content cosine >= %.2f)",
                            title[:40],
                            (hit_meta.get("title") or "(untitled)")[:40],
                            threshold,
                        )
        except Exception as exc:  # noqa: BLE001 — MemoryClient construction failed
            logger.warning(
                "[DEDUP/content] MemoryClient unavailable — skipping content "
                "dedup (fail-open): %s",
                exc,
            )
        return topics

    async def _mark_recent_coverage(
        self, fresh: list[_TopicLike], memory_client: Any,
    ) -> None:
        """Recent-coverage pass — composite candidate text vs recent
        published/in-flight composites. Marks + annotates in place; every
        failure path inside is fail-open (the index loader logs + returns
        ``None``)."""
        from services.topic_recent_coverage import RecentCoverageIndex

        index = await RecentCoverageIndex.load(
            self.pool,
            site_config=self._site_config,
            memory_client=memory_client,
            niche_slug=self._niche_slug,
        )
        if index is None:
            return
        from services.topic_recent_coverage import compose_text

        for topic in fresh:
            if topic.is_duplicate:
                continue
            # Angle/summary attr varies by wrapper: the batch sweep's
            # _DedupCandidate exposes .summary; DiscoveredTopic (tap
            # ingest) carries the internal_rag angle in .description.
            text = compose_text(
                topic.title,
                getattr(topic, "summary", None)
                or getattr(topic, "description", None),
            )
            match = await index.embed_and_match(text)
            if match is None:
                continue
            topic.is_duplicate = True
            # Wrappers that declare the field (the batch sweep's
            # _DedupCandidate) get the named match for the operator-facing
            # finding; foreign topic shapes (DiscoveredTopic) just get the
            # flag + log line.
            if hasattr(topic, "duplicate_of"):
                topic.duplicate_of = match.as_dict()  # type: ignore[attr-defined]
            when = (
                match.published_at.date().isoformat()
                if match.published_at else "in flight"
            )
            logger.info(
                "[DEDUP/recent-coverage] %r ≈ %r (%s, %s; cosine=%.3f ≥ %.2f)",
                topic.title[:40], match.title[:40], match.kind, when,
                match.similarity, index.threshold,
            )

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
