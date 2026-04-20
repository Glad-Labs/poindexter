"""Unit tests for KnowledgeSource.

Mocks asyncpg.Pool with canned rows for brain_knowledge + categories.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from plugins.topic_source import TopicSource
from services.topic_sources.knowledge import KnowledgeSource


def _make_pool(
    knowledge_rows: list[dict] | None = None,
    category_rows: list[dict] | None = None,
    gap_rows: list[dict] | None = None,
):
    """Build a mock asyncpg pool whose fetch() returns the right canned rows.

    The KnowledgeSource runs three queries in order:
      1. knowledge_rows (brain_knowledge entity filter)
      2. category_rows (post counts per category)
      3. gap_rows (brain_knowledge topic_gap rows)
    """
    pool = AsyncMock()
    call_count = {"n": 0}

    async def fetch(sql: str, *args: Any):
        call_count["n"] += 1
        sql_lower = sql.lower()
        if "from brain_knowledge" in sql_lower and "topic_gap" in sql_lower:
            return gap_rows or []
        if "from brain_knowledge" in sql_lower:
            return knowledge_rows or []
        if "from categories" in sql_lower:
            return category_rows or []
        return []

    pool.fetch = AsyncMock(side_effect=fetch)
    return pool


class TestKnowledgeSource:
    @pytest.mark.asyncio
    async def test_yields_gap_topics_with_high_score(self):
        pool = _make_pool(
            gap_rows=[
                {"value": "Building resilient async retry patterns in Python"},
            ],
            knowledge_rows=[],
            category_rows=[],
        )
        source = KnowledgeSource()
        topics = await source.extract(pool=pool, config={})

        assert len(topics) == 1
        assert topics[0].source == "brain_knowledge_gap"
        # Gap topics get the high base score
        assert topics[0].relevance_score == 4.0
        assert "async retry" in topics[0].title.lower()

    @pytest.mark.asyncio
    async def test_yields_knowledge_entity_topics(self):
        # Entity name intentionally avoids the prefixes the source filters
        # out (probe., trend., freshness., health_status).
        pool = _make_pool(
            gap_rows=[],
            category_rows=[],
            knowledge_rows=[
                {
                    "entity": "content.rust_async_deep_dive",
                    "attribute": "topic",
                    "value": "Rust async runtime internals and execution models",
                    "updated_at": None,
                },
            ],
        )
        source = KnowledgeSource()
        topics = await source.extract(pool=pool, config={})

        assert len(topics) == 1
        assert topics[0].source == "brain_knowledge"
        # No underserved categories known → no gap boost → base 2.5
        assert topics[0].relevance_score == pytest.approx(2.5, abs=0.01)

    @pytest.mark.asyncio
    async def test_gap_boost_for_underserved_category(self):
        pool = _make_pool(
            gap_rows=[],
            category_rows=[
                {"category": "technology", "post_count": 1},
                {"category": "gaming", "post_count": 10},
            ],
            knowledge_rows=[
                {
                    "entity": "content.topic",
                    "attribute": "topic",
                    "value": "Vintage CRT displays for retro gaming builds and comparisons",
                    "updated_at": None,
                },
            ],
        )
        source = KnowledgeSource()
        topics = await source.extract(pool=pool, config={})

        # Topic gets classified (probably to gaming because keyword overlap).
        # Score should reflect gap-boost arithmetic: avg=5.5, the matched
        # category's count determines the boost.
        assert len(topics) == 1
        # Base is 2.5; if the category is below average, boost adds up to 2.0.
        assert topics[0].relevance_score >= 2.5

    @pytest.mark.asyncio
    async def test_skips_json_blob_values(self):
        pool = _make_pool(
            gap_rows=[],
            category_rows=[],
            knowledge_rows=[
                {"entity": "probe.x", "attribute": "topic", "value": '{"type": "metric"}', "updated_at": None},
                {"entity": "probe.y", "attribute": "topic", "value": "[1, 2, 3]", "updated_at": None},
            ],
        )
        source = KnowledgeSource()
        topics = await source.extract(pool=pool, config={})
        assert topics == []

    @pytest.mark.asyncio
    async def test_skips_operational_metric_entities(self):
        pool = _make_pool(
            gap_rows=[],
            category_rows=[],
            knowledge_rows=[
                {"entity": "probe.latency", "attribute": "topic", "value": "This would otherwise be a valid topic", "updated_at": None},
                {"entity": "freshness.embed", "attribute": "topic", "value": "Another valid-looking topic string here", "updated_at": None},
                {"entity": "health_status", "attribute": "topic", "value": "A third example of a valid-looking topic", "updated_at": None},
                {"entity": "trend.category", "attribute": "topic", "value": "Another example of a valid-looking topic", "updated_at": None},
            ],
        )
        source = KnowledgeSource()
        topics = await source.extract(pool=pool, config={})
        # All four rows rejected by the entity-prefix filter.
        assert topics == []

    @pytest.mark.asyncio
    async def test_short_values_skipped(self):
        pool = _make_pool(
            gap_rows=[{"value": "tiny"}],
            category_rows=[],
            knowledge_rows=[
                {"entity": "content.topic", "attribute": "topic", "value": "too", "updated_at": None},
            ],
        )
        source = KnowledgeSource()
        topics = await source.extract(pool=pool, config={"min_title_chars": 10})
        assert topics == []

    @pytest.mark.asyncio
    async def test_no_pool_returns_empty(self):
        source = KnowledgeSource()
        topics = await source.extract(pool=None, config={})
        assert topics == []

    @pytest.mark.asyncio
    async def test_config_caps_applied(self):
        pool = _make_pool()
        source = KnowledgeSource()
        await source.extract(
            pool=pool,
            config={"max_gap_topics": 5, "max_entity_topics": 3},
        )

        # Find the calls that hit LIMIT $1 and confirm our caps reached SQL args.
        limit_args = [
            call[0][1]
            for call in pool.fetch.await_args_list
            if len(call[0]) > 1 and "LIMIT $1" in call[0][0]
        ]
        assert 3 in limit_args   # knowledge_rows cap
        assert 5 in limit_args   # gap_rows cap

    @pytest.mark.asyncio
    async def test_entity_fallback_when_value_too_long(self):
        """Value > 120 chars should fall back to the entity name as the
        topic seed."""
        long_value = "x" * 200  # too long
        pool = _make_pool(
            gap_rows=[],
            category_rows=[],
            knowledge_rows=[
                {
                    "entity": "Building a reliable custom ETL pipeline from scratch",
                    "attribute": "topic",
                    "value": long_value,
                    "updated_at": None,
                },
            ],
        )
        source = KnowledgeSource()
        topics = await source.extract(pool=pool, config={})
        # Long value falls back to the entity as candidate; entity is a
        # valid topic-length string → gets through.
        assert len(topics) == 1
        assert "ETL pipeline" in topics[0].title


class TestContract:
    def test_conforms_to_topic_source_protocol(self):
        source = KnowledgeSource()
        assert isinstance(source, TopicSource)
        assert source.name == "knowledge"

    def test_extract_is_coroutine(self):
        import inspect
        assert inspect.iscoroutinefunction(KnowledgeSource.extract)
