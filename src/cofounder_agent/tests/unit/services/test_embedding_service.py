"""
Unit tests for services/embedding_service.py

Covers EmbeddingService:
- _content_hash: deterministic SHA-256 output
- embed_post: skips re-embedding when content hash unchanged
- embed_post: generates embedding and stores when hash changed
- embed_post: raises on ollama or db failure
- embed_brain_knowledge: skips when unchanged
- embed_brain_knowledge: stores embedding for new knowledge
- embed_brain_knowledge: raises on failure
- embed_all_posts: returns all-skipped when nothing needs embedding
- embed_all_posts: batch-embeds and stores, counts correctly
- embed_all_posts: reports failed count when store_embedding raises
- embed_all_posts: reports all failed when batch embedding itself fails
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.embedding_service import EmbeddingService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_ollama():
    """Create a mock OllamaClient."""
    client = AsyncMock()
    client.embed = AsyncMock(return_value=[0.1, 0.2, 0.3])
    client.embed_batch = AsyncMock(return_value=[[0.1, 0.2], [0.3, 0.4]])
    return client


def _make_db():
    """Create a mock EmbeddingsDatabase."""
    db = AsyncMock()
    db.needs_reembedding = AsyncMock(return_value=True)
    db.store_embedding = AsyncMock(return_value="emb-id-123")
    return db


# ---------------------------------------------------------------------------
# _content_hash
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestContentHash:
    def test_deterministic(self):
        h1 = EmbeddingService._content_hash("hello world")
        h2 = EmbeddingService._content_hash("hello world")
        assert h1 == h2

    def test_different_for_different_input(self):
        h1 = EmbeddingService._content_hash("hello")
        h2 = EmbeddingService._content_hash("world")
        assert h1 != h2

    def test_returns_hex_string(self):
        h = EmbeddingService._content_hash("test")
        assert len(h) == 64  # SHA-256 hex digest length
        assert all(c in "0123456789abcdef" for c in h)


# ---------------------------------------------------------------------------
# embed_post
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEmbedPost:
    @pytest.mark.asyncio
    async def test_skips_when_unchanged(self):
        ollama = _make_ollama()
        db = _make_db()
        db.needs_reembedding = AsyncMock(return_value=False)

        svc = EmbeddingService(ollama, db)
        result = await svc.embed_post({"id": "p1", "title": "T", "excerpt": "E", "content": "C"})

        assert result is None
        ollama.embed.assert_not_awaited()
        db.store_embedding.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_embeds_and_stores_when_changed(self):
        ollama = _make_ollama()
        db = _make_db()

        svc = EmbeddingService(ollama, db)
        result = await svc.embed_post({"id": "p1", "title": "T", "excerpt": "E", "content": "C"})

        assert result == "emb-id-123"
        ollama.embed.assert_awaited_once()
        db.store_embedding.assert_awaited_once()
        # Verify store_embedding was called with correct source_type
        call_kwargs = db.store_embedding.call_args
        assert call_kwargs.kwargs.get("source_type") or call_kwargs[1].get("source_type", call_kwargs[0][0] if call_kwargs[0] else None) == "post"

    @pytest.mark.asyncio
    async def test_raises_on_embed_failure(self):
        ollama = _make_ollama()
        ollama.embed = AsyncMock(side_effect=RuntimeError("Ollama down"))
        db = _make_db()

        svc = EmbeddingService(ollama, db)
        with pytest.raises(RuntimeError, match="Ollama down"):
            await svc.embed_post({"id": "p1", "title": "T", "excerpt": "E", "content": "C"})

    @pytest.mark.asyncio
    async def test_truncates_content_to_2000_chars(self):
        ollama = _make_ollama()
        db = _make_db()

        svc = EmbeddingService(ollama, db)
        long_content = "x" * 5000
        await svc.embed_post({"id": "p1", "title": "T", "excerpt": "E", "content": long_content})

        # The text passed to ollama.embed should contain at most 2000 chars of content
        embed_arg = ollama.embed.call_args[0][0]
        # Format: "T\nE\n" + content[:2000]
        assert len(embed_arg) == len("T\nE\n") + 2000


# ---------------------------------------------------------------------------
# embed_brain_knowledge
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEmbedBrainKnowledge:
    @pytest.mark.asyncio
    async def test_skips_when_unchanged(self):
        ollama = _make_ollama()
        db = _make_db()
        db.needs_reembedding = AsyncMock(return_value=False)

        svc = EmbeddingService(ollama, db)
        result = await svc.embed_brain_knowledge("Glad Labs", "mission", "democratize AI")

        assert result is None
        ollama.embed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stores_embedding_for_new_knowledge(self):
        ollama = _make_ollama()
        db = _make_db()

        svc = EmbeddingService(ollama, db)
        result = await svc.embed_brain_knowledge("Glad Labs", "mission", "democratize AI")

        assert result == "emb-id-123"
        ollama.embed.assert_awaited_once()
        db.store_embedding.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_source_id_format(self):
        ollama = _make_ollama()
        db = _make_db()

        svc = EmbeddingService(ollama, db)
        await svc.embed_brain_knowledge("Entity", "attr", "val")

        # needs_reembedding should be called with source_id = "Entity::attr"
        call_args = db.needs_reembedding.call_args[0]
        assert call_args[0] == "brain_knowledge"
        assert call_args[1] == "Entity::attr"

    @pytest.mark.asyncio
    async def test_raises_on_failure(self):
        ollama = _make_ollama()
        ollama.embed = AsyncMock(side_effect=RuntimeError("fail"))
        db = _make_db()

        svc = EmbeddingService(ollama, db)
        with pytest.raises(RuntimeError):
            await svc.embed_brain_knowledge("E", "A", "V")


# ---------------------------------------------------------------------------
# embed_all_posts
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEmbedAllPosts:
    @pytest.mark.asyncio
    async def test_all_skipped(self):
        ollama = _make_ollama()
        db = _make_db()
        db.needs_reembedding = AsyncMock(return_value=False)

        svc = EmbeddingService(ollama, db)
        posts = [
            {"id": "p1", "title": "T1", "excerpt": "E1", "content": "C1"},
            {"id": "p2", "title": "T2", "excerpt": "E2", "content": "C2"},
        ]
        result = await svc.embed_all_posts(posts)

        assert result == {"embedded": 0, "skipped": 2, "failed": 0}
        ollama.embed_batch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_batch_embeds_and_stores(self):
        ollama = _make_ollama()
        db = _make_db()

        svc = EmbeddingService(ollama, db)
        posts = [
            {"id": "p1", "title": "T1", "excerpt": "E1", "content": "C1"},
            {"id": "p2", "title": "T2", "excerpt": "E2", "content": "C2"},
        ]
        result = await svc.embed_all_posts(posts)

        assert result["embedded"] == 2
        assert result["skipped"] == 0
        assert result["failed"] == 0
        ollama.embed_batch.assert_awaited_once()
        assert db.store_embedding.call_count == 2

    @pytest.mark.asyncio
    async def test_counts_store_failures(self):
        ollama = _make_ollama()
        db = _make_db()
        db.store_embedding = AsyncMock(side_effect=RuntimeError("store failed"))

        svc = EmbeddingService(ollama, db)
        posts = [{"id": "p1", "title": "T", "excerpt": "E", "content": "C"}]
        result = await svc.embed_all_posts(posts)

        assert result["failed"] == 1
        assert result["embedded"] == 0

    @pytest.mark.asyncio
    async def test_all_failed_when_batch_embed_fails(self):
        ollama = _make_ollama()
        ollama.embed_batch = AsyncMock(side_effect=RuntimeError("batch fail"))
        db = _make_db()

        svc = EmbeddingService(ollama, db)
        posts = [
            {"id": "p1", "title": "T1", "excerpt": "E1", "content": "C1"},
            {"id": "p2", "title": "T2", "excerpt": "E2", "content": "C2"},
        ]
        result = await svc.embed_all_posts(posts)

        assert result["failed"] == 2
        assert result["embedded"] == 0
