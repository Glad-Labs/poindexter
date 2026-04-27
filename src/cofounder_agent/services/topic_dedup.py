"""Topic deduplication — detect duplicate candidates against the live corpus.

Extracted from ``services/topic_discovery.py`` (#151). Pure
duplicate-detection logic that other systems can reuse — RSS feed
ingestion, manual topic injection via ``poindexter topics propose``,
or any future topic source — without dragging in the full discovery
dispatcher.

Two thresholds (gitea#279, both DB-tunable via app_settings):

- ``topic_dedup_existing_threshold`` (default 0.7) — match against
  already-published posts + in-flight tasks. Permissive because
  coincidental keyword overlap with the hundreds-of-titles corpus is
  common ("Challenge" and "Week" recur across distinct events).
- ``topic_dedup_intra_batch_threshold`` (default 0.65) — match
  between candidates in the same scrape. Slightly tighter, since a
  single source dropping two near-identical headlines usually *is* a
  duplicate.

Deduplicates by content-word overlap rather than exact match, with
a configurable stop-word filter so connective words don't drive
false positives. The :func:`_word_overlap_match` helper returns both
the boolean decision and the actual ratio so callers can log the
score for tuning (gitea#279 was that the legacy 0.4 default was
filtering 14/14 candidates — without instrumented ratios we couldn't
tell why).

Usage::

    from services.topic_dedup import TopicDeduplicator

    dedup = TopicDeduplicator(pool=pool, site_config=site_config)
    await dedup.mark_duplicates(topics)
    fresh = [t for t in topics if not t.is_duplicate]
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Protocol

from services.logger_config import get_logger

logger = get_logger(__name__)


_STOP_WORDS = frozenset(
    "a an the in on of to for and or but is are was were be been by with from "
    "at as it its that this these those not no nor do does did will would "
    "should could can may might shall into your you we they how what why when "
    "where who which have has had here there their about just also than more "
    "most some any all every without actually really need don t s re ve ll "
    "new top best way beyond".split()
)


class _TopicLike(Protocol):
    """Minimal shape topic-deduplication needs.

    Both ``services.topic_discovery.DiscoveredTopic`` and
    ``plugins.topic_source.DiscoveredTopic`` satisfy this — we use a
    Protocol instead of importing the concrete class to avoid the
    circular-import that would result from
    services.topic_discovery → services.topic_dedup → services.topic_discovery.
    """

    title: str
    is_duplicate: bool


def _content_words(title: str) -> set[str]:
    """Extract meaningful words from a title, stripping stop words and punctuation."""
    words = set(re.findall(r"[a-z0-9]+", title.lower()))
    return words - _STOP_WORDS


def _word_overlap_match(
    words_a: set[str], words_b: set[str], threshold: float = 0.4,
) -> tuple[bool, float]:
    """Return ``(is_duplicate, max_overlap_ratio)``.

    Content-word overlap > threshold in either direction = duplicate.
    Both the boolean and the ratio are returned so callers can log
    the actual score for tuning (gitea#279) — previously this only
    returned bool and the log line said "X ≈ Y" with no numeric
    evidence, making the 0.4 default untunable without instrumentation.
    """
    if not words_a or not words_b:
        return False, 0.0
    overlap = len(words_a & words_b)
    ratio_a = overlap / len(words_a)
    ratio_b = overlap / len(words_b)
    max_ratio = max(ratio_a, ratio_b)
    return max_ratio > threshold, max_ratio


class TopicDeduplicator:
    """Mark candidate topics as duplicates against an existing corpus.

    Stateless aside from the pool + site_config dependencies. Two
    public methods:

    - :meth:`mark_duplicates` — full pass: vs-existing + intra-batch.
      The legacy ``TopicDiscovery._deduplicate`` did both; that's the
      same shape callers want.
    - :meth:`mark_against_existing` / :meth:`mark_intra_batch` —
      individual passes when the caller wants finer control (e.g.,
      the manual topic-injection CLI only needs the existing-corpus
      check).
    """

    def __init__(self, pool: Any, *, site_config: Any) -> None:
        self.pool = pool
        self._site_config = site_config

    async def mark_duplicates(
        self, topics: list[_TopicLike],
    ) -> list[_TopicLike]:
        """Run both dedup passes — existing corpus + intra-batch.

        Mutates ``topics`` in place (sets ``is_duplicate`` on matches)
        and returns the same list so callers can chain.
        """
        if not self.pool or not topics:
            return topics
        await self.mark_against_existing(topics)
        self.mark_intra_batch(topics)
        return topics

    # ------------------------------------------------------------------
    # vs-existing-corpus pass
    # ------------------------------------------------------------------

    async def mark_against_existing(
        self, topics: list[_TopicLike],
    ) -> list[_TopicLike]:
        """Mark topics whose title duplicates a published post or
        in-flight task. Uses the ``topic_dedup_existing_threshold``
        knob; default 0.7."""
        if not self.pool or not topics:
            return topics

        threshold = self._site_config.get_float(
            "topic_dedup_existing_threshold", 0.7
        )
        try:
            existing = await self._load_existing_titles()
        except Exception as e:
            logger.warning("[topic_dedup] vs-existing load failed: %s", e)
            return topics

        for topic in topics:
            if topic.is_duplicate:
                continue
            title_lower = topic.title.lower()
            if title_lower in existing:
                topic.is_duplicate = True
                continue
            topic_words = _content_words(title_lower)
            if len(topic_words) < 2:
                continue
            for existing_title in existing:
                existing_words = _content_words(existing_title)
                if len(existing_words) < 2:
                    continue
                is_dup, score = _word_overlap_match(
                    topic_words, existing_words, threshold=threshold,
                )
                if is_dup:
                    topic.is_duplicate = True
                    logger.info(
                        "[DEDUP] vs-existing: '%s' ≈ '%s' "
                        "(overlap=%.2f, threshold=%.2f)",
                        topic.title[:40], existing_title[:40],
                        score, threshold,
                    )
                    break
        return topics

    async def _load_existing_titles(self) -> set[str]:
        """Load published-post titles + in-flight content_tasks topics
        into one lowercase set for the vs-existing pass.

        Rejected topics are EXPLICITLY EXCLUDED (Matt 2026-04-22) —
        rejecting a bad draft shouldn't permanently block the topic
        itself. The window is tunable via app_settings key
        ``qa_topic_dedup_hours`` (default 48).
        """
        rows = await self.pool.fetch(
            "SELECT title FROM posts WHERE status = 'published'"
        )
        published_titles = {r["title"].lower() for r in rows}

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
                pending_topics.add(r["topic"].lower())
            if r.get("title"):
                pending_topics.add(r["title"].lower())

        return published_titles | pending_topics

    # ------------------------------------------------------------------
    # intra-batch pass
    # ------------------------------------------------------------------

    def mark_intra_batch(
        self, topics: list[_TopicLike],
    ) -> list[_TopicLike]:
        """Mark topics that duplicate another candidate in the same
        scrape. Uses the ``topic_dedup_intra_batch_threshold`` knob;
        default 0.65. Synchronous because no DB lookups."""
        if not topics:
            return topics
        threshold = self._site_config.get_float(
            "topic_dedup_intra_batch_threshold", 0.65,
        )
        for i, t1 in enumerate(topics):
            if t1.is_duplicate:
                continue
            t1_words = _content_words(t1.title.lower())
            if len(t1_words) < 2:
                continue
            for t2 in topics[i + 1:]:
                if t2.is_duplicate:
                    continue
                t2_words = _content_words(t2.title.lower())
                if len(t2_words) < 2:
                    continue
                is_dup, score = _word_overlap_match(
                    t1_words, t2_words, threshold=threshold,
                )
                if is_dup:
                    t2.is_duplicate = True
                    logger.info(
                        "[DEDUP] Intra-batch: '%s' ≈ '%s' "
                        "(overlap=%.2f, threshold=%.2f)",
                        t2.title[:40], t1.title[:40],
                        score, threshold,
                    )
        return topics


__all__ = [
    "TopicDeduplicator",
    "_STOP_WORDS",
    "_content_words",
    "_word_overlap_match",
]


# Used by the type checker / for documentation.
_ = Iterable  # noqa: F841
