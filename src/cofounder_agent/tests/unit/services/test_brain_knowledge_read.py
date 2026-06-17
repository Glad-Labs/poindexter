"""Unit tests for ``services.brain_knowledge_read``."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from services import brain_knowledge_read


def _make_pool(rows=None, row=None):
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=rows or [])
    pool.fetchrow = AsyncMock(return_value=row)
    return pool


class TestQueryKnowledge:
    async def test_returns_rows_as_dicts(self):
        pool = _make_pool(rows=[{"entity": "e", "attribute": "a", "value": "v", "confidence": 1.0, "source": "s", "tags": [], "updated_at": None}])
        result = await brain_knowledge_read.query_knowledge(pool)
        assert result[0]["entity"] == "e"

    async def test_no_filters_no_where_clause(self):
        pool = _make_pool()
        await brain_knowledge_read.query_knowledge(pool)
        sql = pool.fetch.await_args.args[0]
        assert "WHERE" not in sql

    async def test_entity_filter_added(self):
        pool = _make_pool()
        await brain_knowledge_read.query_knowledge(pool, entity="ollama")
        args = pool.fetch.await_args.args
        assert "entity" in args[0]
        assert "%ollama%" in args

    async def test_attribute_filter_added(self):
        pool = _make_pool()
        await brain_knowledge_read.query_knowledge(pool, attribute="status")
        args = pool.fetch.await_args.args
        assert "attribute" in args[0]
        assert "status" in args

    async def test_limit_capped_at_100(self):
        pool = _make_pool()
        await brain_knowledge_read.query_knowledge(pool, limit=500)
        limit_arg = pool.fetch.await_args.args[-1]
        assert limit_arg == 100

    async def test_empty_returns_empty_list(self):
        pool = _make_pool()
        assert await brain_knowledge_read.query_knowledge(pool) == []
