"""Unit tests for TopicBatchService.get_open_batch_id."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

from services.site_config import SiteConfig
from services.topic_batch_service import TopicBatchService

_NICHE_ID = UUID("00000000-0000-0000-0000-000000000001")
_BATCH_ID = UUID("00000000-0000-0000-0000-000000000002")


def _pool(fetchval_return=None):
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=fetchval_return)

    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    # TopicBatchService.__init__ creates NicheService(pool) which calls pool.fetch — stub it
    pool.fetch = AsyncMock(return_value=[])
    return pool, conn


def _svc(pool):
    return TopicBatchService(pool, site_config=SiteConfig(initial_config={}))


class TestGetOpenBatchId:
    async def test_returns_batch_id_when_open_batch_exists(self):
        pool, conn = _pool(_BATCH_ID)
        result = await _svc(pool).get_open_batch_id(_NICHE_ID)
        assert result == _BATCH_ID

    async def test_returns_none_when_no_open_batch(self):
        pool, conn = _pool(None)
        result = await _svc(pool).get_open_batch_id(_NICHE_ID)
        assert result is None

    async def test_queries_topic_batches_table(self):
        pool, conn = _pool(None)
        await _svc(pool).get_open_batch_id(_NICHE_ID)
        sql = conn.fetchval.await_args.args[0]
        assert "topic_batches" in sql

    async def test_filters_by_niche_id(self):
        pool, conn = _pool(None)
        await _svc(pool).get_open_batch_id(_NICHE_ID)
        args = conn.fetchval.await_args.args
        assert _NICHE_ID in args

    async def test_filters_by_open_status(self):
        pool, conn = _pool(None)
        await _svc(pool).get_open_batch_id(_NICHE_ID)
        sql = conn.fetchval.await_args.args[0]
        assert "open" in sql

    async def test_different_niche_ids_pass_through(self):
        other = uuid4()
        pool, conn = _pool(None)
        await _svc(pool).get_open_batch_id(other)
        args = conn.fetchval.await_args.args
        assert other in args
