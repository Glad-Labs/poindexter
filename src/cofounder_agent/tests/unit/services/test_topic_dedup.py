"""Unit tests for ``services/topic_dedup.py`` (#151).

The dedup logic itself is tested end-to-end via
``test_topic_discovery.py`` (which now exercises the same code path
through TopicDiscovery._deduplicate → TopicDeduplicator.mark_duplicates).
This file adds focused tests on the new module's public surface so
future refactors of TopicDiscovery don't accidentally break direct
TopicDeduplicator usage from other ingest paths (manual topic
injection CLI, RSS, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from services.topic_dedup import (
    TopicDeduplicator,
    _content_words,
    _word_overlap_match,
)


@dataclass
class _FakeTopic:
    title: str
    is_duplicate: bool = False


class _FakeSiteConfig:
    def __init__(self, **floats: float) -> None:
        self._floats = floats

    def get_float(self, key: str, default: float) -> float:
        return self._floats.get(key, default)

    def get_int(self, key: str, default: int) -> int:
        return default


class _FakePool:
    def __init__(
        self,
        published_titles: list[str],
        in_flight_titles: list[str],
    ) -> None:
        self.published_titles = published_titles
        self.in_flight_titles = in_flight_titles

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        sql_norm = " ".join(sql.split())
        if "FROM posts WHERE status = 'published'" in sql_norm:
            return [{"title": t} for t in self.published_titles]
        if "FROM content_tasks" in sql_norm:
            return [
                {"topic": None, "title": t}
                for t in self.in_flight_titles
            ]
        raise AssertionError(f"unexpected SQL: {sql_norm[:60]}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestContentWords:
    def test_strips_stopwords(self):
        assert _content_words("the quick brown fox") == {"quick", "brown", "fox"}

    def test_strips_punctuation(self):
        assert _content_words("AI: the future!") == {"ai", "future"}

    def test_lowercase(self):
        assert _content_words("Python WEB Framework") == {"python", "web", "framework"}


class TestWordOverlapMatch:
    def test_above_threshold_is_duplicate(self):
        a = {"python", "web", "framework"}
        b = {"python", "web", "scraper"}
        is_dup, score = _word_overlap_match(a, b, threshold=0.6)
        # 2/3 overlap = 0.67 → duplicate at 0.6 threshold.
        assert is_dup is True
        assert score == pytest.approx(2 / 3, rel=1e-3)

    def test_below_threshold_not_duplicate(self):
        a = {"a", "b", "c", "d"}
        b = {"d", "e", "f", "g"}
        is_dup, score = _word_overlap_match(a, b, threshold=0.5)
        # 1/4 = 0.25 → not duplicate.
        assert is_dup is False
        assert score == pytest.approx(0.25)

    def test_empty_sets_not_duplicate(self):
        is_dup, score = _word_overlap_match(set(), {"x"}, threshold=0.4)
        assert is_dup is False
        assert score == 0.0


# ---------------------------------------------------------------------------
# TopicDeduplicator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestTopicDeduplicator:
    async def test_marks_existing_match_as_duplicate(self):
        pool = _FakePool(
            published_titles=["Building Async Python Applications"],
            in_flight_titles=[],
        )
        cfg = _FakeSiteConfig(topic_dedup_existing_threshold=0.5)
        dedup = TopicDeduplicator(pool, site_config=cfg)
        topics = [
            _FakeTopic(title="Building Async Python Applications With AsyncIO"),
            _FakeTopic(title="Rust Memory Safety Patterns"),
        ]
        await dedup.mark_against_existing(topics)
        assert topics[0].is_duplicate is True
        assert topics[1].is_duplicate is False

    async def test_intra_batch_marks_second_match_only(self):
        cfg = _FakeSiteConfig(topic_dedup_intra_batch_threshold=0.5)
        dedup = TopicDeduplicator(_FakePool([], []), site_config=cfg)
        topics = [
            _FakeTopic(title="Async Python Tutorial"),
            _FakeTopic(title="Python Async Tutorial"),  # same words, different order
            _FakeTopic(title="Rust Memory Safety"),
        ]
        dedup.mark_intra_batch(topics)
        assert topics[0].is_duplicate is False  # first wins
        assert topics[1].is_duplicate is True   # second is the dup
        assert topics[2].is_duplicate is False

    async def test_mark_duplicates_combines_both(self):
        pool = _FakePool(
            published_titles=["Existing Post About Kubernetes"],
            in_flight_titles=[],
        )
        cfg = _FakeSiteConfig(
            topic_dedup_existing_threshold=0.5,
            topic_dedup_intra_batch_threshold=0.5,
        )
        dedup = TopicDeduplicator(pool, site_config=cfg)
        topics = [
            _FakeTopic(title="Existing Kubernetes Post Patterns"),  # vs-existing dup
            _FakeTopic(title="Building Modern Web Apps"),
            _FakeTopic(title="Modern Web Apps Building"),  # intra-batch dup of #1
        ]
        await dedup.mark_duplicates(topics)
        assert topics[0].is_duplicate is True
        assert topics[1].is_duplicate is False
        assert topics[2].is_duplicate is True

    async def test_skips_when_no_pool(self):
        cfg = _FakeSiteConfig()
        dedup = TopicDeduplicator(None, site_config=cfg)
        topics = [_FakeTopic(title="x")]
        result = await dedup.mark_duplicates(topics)
        assert result is topics
        assert topics[0].is_duplicate is False

    async def test_skips_short_titles(self):
        """Titles with fewer than 2 content words can't reliably match
        — skip them rather than over-flag every short string."""
        cfg = _FakeSiteConfig(topic_dedup_existing_threshold=0.4)
        pool = _FakePool(published_titles=["AI"], in_flight_titles=[])
        dedup = TopicDeduplicator(pool, site_config=cfg)
        topics = [_FakeTopic(title="AI Strategy Roadmap")]
        await dedup.mark_against_existing(topics)
        # Existing "AI" has only 1 content word → skipped, no false dup.
        assert topics[0].is_duplicate is False
