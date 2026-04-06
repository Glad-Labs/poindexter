"""Tests for embeddings_db service."""

import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


@pytest.fixture
def mock_pool():
    pool = MagicMock()
    conn = AsyncMock()
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = ctx
    return pool, conn


@pytest.fixture
def db(mock_pool):
    from services.embeddings_db import EmbeddingsDatabase

    pool, _ = mock_pool
    return EmbeddingsDatabase(pool)


class TestStoreEmbedding:
    @pytest.mark.asyncio
    async def test_store_returns_id(self, db, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = {"id": "emb-123"}
        result = await db.store_embedding(
            source_type="post",
            source_id="p1",
            content_hash="abc123",
            embedding=[0.1, 0.2, 0.3],
        )
        assert result == "emb-123"
        conn.fetchrow.assert_called_once()

    @pytest.mark.asyncio
    async def test_store_with_metadata(self, db, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = {"id": "emb-456"}
        result = await db.store_embedding(
            source_type="post",
            source_id="p2",
            content_hash="def456",
            embedding=[0.5, 0.6],
            metadata={"title": "test"},
            embedding_model="custom-model",
        )
        assert result == "emb-456"
        call_args = conn.fetchrow.call_args[0]
        assert call_args[0].strip().startswith("INSERT INTO embeddings")
        assert call_args[5] == "custom-model"

    @pytest.mark.asyncio
    async def test_store_raises_on_db_error(self, db, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.side_effect = RuntimeError("db error")
        with pytest.raises(RuntimeError, match="db error"):
            await db.store_embedding("post", "p1", "hash", [0.1])

    @pytest.mark.asyncio
    async def test_store_none_row_returns_none(self, db, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = None
        result = await db.store_embedding("post", "p1", "hash", [0.1])
        assert result is None


class TestSearchSimilar:
    @pytest.mark.asyncio
    async def test_search_returns_results(self, db, mock_pool):
        _, conn = mock_pool
        conn.fetch.return_value = [
            {
                "source_table": "post",
                "source_id": "p1",
                "content_hash": "abc",
                "metadata": None,
                "similarity": 0.95,
            }
        ]
        results = await db.search_similar([0.1, 0.2], limit=5)
        assert len(results) == 1
        conn.fetch.assert_called_once()

    @pytest.mark.asyncio
    async def test_search_with_source_type_filter(self, db, mock_pool):
        _, conn = mock_pool
        conn.fetch.return_value = []
        results = await db.search_similar([0.1], source_type="post")
        assert results == []
        query = conn.fetch.call_args[0][0]
        assert "source_table = $2" in query

    @pytest.mark.asyncio
    async def test_search_without_source_type(self, db, mock_pool):
        _, conn = mock_pool
        conn.fetch.return_value = []
        results = await db.search_similar([0.1])
        query = conn.fetch.call_args[0][0]
        assert "source_table = $2" not in query

    @pytest.mark.asyncio
    async def test_search_db_error_returns_empty(self, db, mock_pool):
        _, conn = mock_pool
        conn.fetch.side_effect = RuntimeError("db error")
        results = await db.search_similar([0.1])
        assert results == []


class TestGetEmbedding:
    @pytest.mark.asyncio
    async def test_get_existing(self, db, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = {
            "id": "e1",
            "source_table": "post",
            "source_id": "p1",
            "content_hash": "abc",
            "metadata": None,
            "created_at": "2025-01-01",
            "updated_at": "2025-01-01",
        }
        result = await db.get_embedding("post", "p1")
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_not_found(self, db, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = None
        result = await db.get_embedding("post", "p999")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_db_error_returns_none(self, db, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.side_effect = RuntimeError("db error")
        result = await db.get_embedding("post", "p1")
        assert result is None


class TestDeleteEmbeddings:
    @pytest.mark.asyncio
    async def test_delete_by_type_and_id(self, db, mock_pool):
        _, conn = mock_pool
        conn.execute.return_value = "DELETE 3"
        result = await db.delete_embeddings("post", "p1")
        assert result == 3

    @pytest.mark.asyncio
    async def test_delete_all_by_type(self, db, mock_pool):
        _, conn = mock_pool
        conn.execute.return_value = "DELETE 10"
        result = await db.delete_embeddings("post")
        assert result == 10
        query = conn.execute.call_args[0][0]
        assert "source_id" not in query

    @pytest.mark.asyncio
    async def test_delete_db_error_returns_zero(self, db, mock_pool):
        _, conn = mock_pool
        conn.execute.side_effect = RuntimeError("db error")
        result = await db.delete_embeddings("post")
        assert result == 0


class TestNeedsReembedding:
    @pytest.mark.asyncio
    async def test_no_existing_embedding(self, db, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = None
        assert await db.needs_reembedding("post", "p1", "newhash") is True

    @pytest.mark.asyncio
    async def test_hash_matches(self, db, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = {"content_hash": "samehash"}
        assert await db.needs_reembedding("post", "p1", "samehash") is False

    @pytest.mark.asyncio
    async def test_hash_differs(self, db, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = {"content_hash": "oldhash"}
        assert await db.needs_reembedding("post", "p1", "newhash") is True

    @pytest.mark.asyncio
    async def test_db_error_returns_true(self, db, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.side_effect = RuntimeError("db error")
        assert await db.needs_reembedding("post", "p1", "hash") is True
