"""Tests for embeddings_db service."""

import json
from unittest.mock import AsyncMock, MagicMock

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


class TestStoreEmbeddingChunkIndex:
    """#625 — chunk_index must reach the INSERT so distinct chunks coexist.

    The natural key is ``(source_table, source_id, chunk_index,
    embedding_model)``. Before the fix the INSERT omitted ``chunk_index``,
    so it always defaulted to 0 and chunk 1 clobbered chunk 0 on conflict.
    """

    @pytest.mark.asyncio
    async def test_chunk_index_defaults_to_zero(self, db, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = {"id": "e1"}
        await db.store_embedding("post", "p1", "h", [0.1])
        args = conn.fetchrow.call_args[0]
        # chunk_index is the 11th positional value ($11) — last param.
        assert args[11] == 0

    @pytest.mark.asyncio
    async def test_chunk_index_passed_through(self, db, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = {"id": "e1"}
        await db.store_embedding("post", "p1", "h", [0.1], chunk_index=3)
        args = conn.fetchrow.call_args[0]
        assert args[11] == 3

    @pytest.mark.asyncio
    async def test_insert_lists_chunk_index_column(self, db, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = {"id": "e1"}
        await db.store_embedding("post", "p1", "h", [0.1])
        sql = conn.fetchrow.call_args[0][0]
        assert "chunk_index" in sql, (
            "INSERT must list chunk_index so chunk 1 doesn't clobber chunk 0"
        )

    @pytest.mark.asyncio
    async def test_two_chunks_both_stored_distinctly(self, db, mock_pool):
        """Two chunks of the same source send DISTINCT chunk_index values.

        With a real DB + the natural-key UNIQUE constraint, distinct
        chunk_index values mean BOTH rows persist (no ON CONFLICT
        clobber). Here we assert the two INSERTs carry the two distinct
        chunk_index values — the fix that makes coexistence possible.
        """
        _, conn = mock_pool
        conn.fetchrow.side_effect = [{"id": "chunk-0"}, {"id": "chunk-1"}]

        id0 = await db.store_embedding("post", "p1", "h0", [0.1], chunk_index=0)
        chunk0_args = conn.fetchrow.call_args[0]
        id1 = await db.store_embedding("post", "p1", "h1", [0.2], chunk_index=1)
        chunk1_args = conn.fetchrow.call_args[0]

        assert id0 == "chunk-0"
        assert id1 == "chunk-1"
        # The two INSERTs carry distinct chunk_index values ($11), so the
        # natural-key UNIQUE constraint keeps both rows instead of one
        # overwriting the other.
        assert chunk0_args[11] == 0
        assert chunk1_args[11] == 1
        assert chunk0_args[11] != chunk1_args[11]


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
        await db.search_similar([0.1])
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


class TestGetEmbeddingDeterminism:
    """#626 — fetch must be deterministic when multiple chunks/models exist."""

    @pytest.mark.asyncio
    async def test_query_orders_for_determinism(self, db, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = None
        await db.get_embedding("post", "p1")
        query = conn.fetchrow.call_args[0][0]
        assert "ORDER BY chunk_index, embedding_model" in query
        assert "LIMIT 1" in query

    @pytest.mark.asyncio
    async def test_no_model_filter_uses_two_params(self, db, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = None
        await db.get_embedding("post", "p1")
        args = conn.fetchrow.call_args[0]
        # SQL + source_table + source_id, no embedding_model param.
        assert args[1] == "post"
        assert args[2] == "p1"
        assert len(args) == 3
        assert "embedding_model = $3" not in args[0]

    @pytest.mark.asyncio
    async def test_model_filter_adds_param_and_clause(self, db, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = None
        await db.get_embedding("post", "p1", embedding_model="nomic-embed-text")
        args = conn.fetchrow.call_args[0]
        assert "embedding_model = $3" in args[0]
        assert args[3] == "nomic-embed-text"
        assert "ORDER BY chunk_index, embedding_model" in args[0]


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


class TestStoreEmbeddingFormatting:
    @pytest.mark.asyncio
    async def test_vector_string_format(self, db, mock_pool):
        """Embedding list should be serialized as `[v1,v2,v3]` for pgvector."""
        _, conn = mock_pool
        conn.fetchrow.return_value = {"id": "e1"}
        await db.store_embedding("post", "p1", "h", [0.1, 0.2, 0.3])
        args = conn.fetchrow.call_args[0]
        # vector_str is the 4th positional value ($4)
        assert args[4] == "[0.1,0.2,0.3]"

    @pytest.mark.asyncio
    async def test_default_embedding_model(self, db, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = {"id": "e1"}
        await db.store_embedding("post", "p1", "h", [0.1])
        args = conn.fetchrow.call_args[0]
        assert args[5] == "nomic-embed-text"

    @pytest.mark.asyncio
    async def test_metadata_json_serialization(self, db, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = {"id": "e1"}
        meta = {"title": "test", "tags": ["a", "b"]}
        await db.store_embedding("post", "p1", "h", [0.1], metadata=meta)
        args = conn.fetchrow.call_args[0]
        # metadata_json is the 6th positional value ($6)
        decoded = json.loads(args[6])
        assert decoded == meta

    @pytest.mark.asyncio
    async def test_none_metadata_passed_as_null(self, db, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = {"id": "e1"}
        await db.store_embedding("post", "p1", "h", [0.1])
        args = conn.fetchrow.call_args[0]
        assert args[6] is None

    @pytest.mark.asyncio
    async def test_empty_embedding_serializes_to_brackets(self, db, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = {"id": "e1"}
        await db.store_embedding("post", "p1", "h", [])
        args = conn.fetchrow.call_args[0]
        assert args[4] == "[]"

    @pytest.mark.asyncio
    async def test_timestamps_are_utc_now(self, db, mock_pool):
        from datetime import datetime, timezone
        _, conn = mock_pool
        conn.fetchrow.return_value = {"id": "e1"}
        before = datetime.now(timezone.utc)
        await db.store_embedding("post", "p1", "h", [0.1])
        after = datetime.now(timezone.utc)

        args = conn.fetchrow.call_args[0]
        # fetchrow(sql, *params) — call_args[0] is (sql, *params), so
        # index 0 is the SQL string. After the #198 schema fix the
        # params are:
        #   [1]  source_table
        #   [2]  source_id
        #   [3]  content_hash
        #   [4]  vector_str
        #   [5]  embedding_model
        #   [6]  metadata_json
        #   [7]  text_preview
        #   [8]  writer
        #   [9]  created_at
        #   [10] updated_at
        created_at, updated_at = args[9], args[10]
        assert before <= created_at <= after
        assert created_at == updated_at  # both set to `now` in store_embedding


class TestSearchSimilarParameters:
    @pytest.mark.asyncio
    async def test_min_similarity_passed_in_query(self, db, mock_pool):
        _, conn = mock_pool
        conn.fetch.return_value = []
        await db.search_similar([0.1, 0.2], min_similarity=0.75)
        args = conn.fetch.call_args[0]
        # No source_type → vector at $1, min_similarity at $2, limit at $3
        assert args[2] == 0.75

    @pytest.mark.asyncio
    async def test_limit_passed_in_query_no_filter(self, db, mock_pool):
        _, conn = mock_pool
        conn.fetch.return_value = []
        await db.search_similar([0.1], limit=25)
        args = conn.fetch.call_args[0]
        assert args[3] == 25

    @pytest.mark.asyncio
    async def test_limit_passed_in_query_with_filter(self, db, mock_pool):
        _, conn = mock_pool
        conn.fetch.return_value = []
        await db.search_similar([0.1], source_type="post", limit=15)
        args = conn.fetch.call_args[0]
        # vector $1, source $2, min_sim $3, limit $4
        assert args[4] == 15

    @pytest.mark.asyncio
    async def test_default_limit_is_10(self, db, mock_pool):
        _, conn = mock_pool
        conn.fetch.return_value = []
        await db.search_similar([0.1])
        args = conn.fetch.call_args[0]
        assert args[3] == 10

    @pytest.mark.asyncio
    async def test_default_min_similarity_is_zero(self, db, mock_pool):
        _, conn = mock_pool
        conn.fetch.return_value = []
        await db.search_similar([0.1])
        args = conn.fetch.call_args[0]
        assert args[2] == 0.0

    @pytest.mark.asyncio
    async def test_query_uses_cosine_distance_operator(self, db, mock_pool):
        _, conn = mock_pool
        conn.fetch.return_value = []
        await db.search_similar([0.1])
        query = conn.fetch.call_args[0][0]
        assert "<=>" in query  # pgvector cosine distance operator
        assert "1 - (embedding <=> $1::vector)" in query

    @pytest.mark.asyncio
    async def test_results_ordered_by_distance(self, db, mock_pool):
        _, conn = mock_pool
        conn.fetch.return_value = []
        await db.search_similar([0.1])
        query = conn.fetch.call_args[0][0]
        assert "ORDER BY embedding <=> $1::vector" in query


class TestDeleteEmbeddingsSqlShape:
    @pytest.mark.asyncio
    async def test_delete_by_id_uses_two_param_query(self, db, mock_pool):
        _, conn = mock_pool
        conn.execute.return_value = "DELETE 1"
        await db.delete_embeddings("post", "p1")
        args = conn.execute.call_args[0]
        assert "source_table = $1 AND source_id = $2" in args[0]
        assert args[1] == "post"
        assert args[2] == "p1"

    @pytest.mark.asyncio
    async def test_delete_all_by_type_one_param(self, db, mock_pool):
        _, conn = mock_pool
        conn.execute.return_value = "DELETE 5"
        await db.delete_embeddings("post")
        args = conn.execute.call_args[0]
        assert "source_table = $1" in args[0]
        assert "source_id" not in args[0]
        assert args[1] == "post"
        assert len(args) == 2  # SQL + 1 param

    @pytest.mark.asyncio
    async def test_zero_deletions_returns_zero(self, db, mock_pool):
        _, conn = mock_pool
        conn.execute.return_value = "DELETE 0"
        result = await db.delete_embeddings("post", "missing")
        assert result == 0


class TestNeedsReembeddingParameters:
    @pytest.mark.asyncio
    async def test_passes_source_type_and_id_to_query(self, db, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = None
        await db.needs_reembedding("brain_knowledge", "kn-1", "newhash")
        args = conn.fetchrow.call_args[0]
        assert args[1] == "brain_knowledge"
        assert args[2] == "kn-1"

    @pytest.mark.asyncio
    async def test_query_only_selects_content_hash(self, db, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = None
        await db.needs_reembedding("post", "p1", "h")
        query = conn.fetchrow.call_args[0][0]
        assert "SELECT content_hash" in query

    @pytest.mark.asyncio
    async def test_query_orders_for_determinism(self, db, mock_pool):
        """#626 — needs_reembedding must compare a deterministic row."""
        _, conn = mock_pool
        conn.fetchrow.return_value = None
        await db.needs_reembedding("post", "p1", "h")
        query = conn.fetchrow.call_args[0][0]
        assert "ORDER BY chunk_index, embedding_model" in query
        assert "LIMIT 1" in query

    @pytest.mark.asyncio
    async def test_model_filter_adds_param_and_clause(self, db, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = None
        await db.needs_reembedding(
            "post", "p1", "h", embedding_model="nomic-embed-text"
        )
        args = conn.fetchrow.call_args[0]
        assert "embedding_model = $3" in args[0]
        assert args[3] == "nomic-embed-text"


class TestStoreEmbeddingDimensionsLogged:
    @pytest.mark.asyncio
    async def test_returns_string_id_even_if_uuid_object(self, db, mock_pool):
        """If the DB returns a UUID object, it should be coerced to string."""
        from uuid import UUID
        _, conn = mock_pool
        uid = UUID("12345678-1234-5678-1234-567812345678")
        conn.fetchrow.return_value = {"id": uid}
        result = await db.store_embedding("post", "p1", "h", [0.1])
        assert isinstance(result, str)
        assert result == str(uid)
