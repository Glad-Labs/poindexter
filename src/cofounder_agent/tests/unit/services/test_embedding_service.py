"""
Unit tests for services/embedding_service.py

Tests EmbeddingService orchestration: embed_post, embed_brain_knowledge,
embed_all_posts (batch), deduplication via content hashing, and error handling.
All external dependencies (OllamaClient, EmbeddingsDatabase) are mocked.
"""

import hashlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.embedding_service import EmbeddingService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_EMBEDDING = [0.1, 0.2, 0.3, 0.4, 0.5]


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _make_post(post_id=1, title="Test Title", excerpt="Test excerpt", content="Test content"):
    return {"id": post_id, "title": title, "excerpt": excerpt, "content": content}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ollama():
    mock = AsyncMock()
    mock.embed = AsyncMock(return_value=SAMPLE_EMBEDDING)
    mock.embed_batch = AsyncMock(return_value=[SAMPLE_EMBEDDING, SAMPLE_EMBEDDING])
    return mock


@pytest.fixture
def embeddings_db():
    mock = AsyncMock()
    mock.needs_reembedding = AsyncMock(return_value=True)
    mock.store_embedding = AsyncMock(return_value="emb-uuid-123")
    return mock


@pytest.fixture
def service(ollama, embeddings_db):
    return EmbeddingService(ollama_client=ollama, embeddings_db=embeddings_db)


# ---------------------------------------------------------------------------
# _content_hash
# ---------------------------------------------------------------------------


class TestContentHash:
    def test_deterministic(self):
        assert EmbeddingService._content_hash("hello") == _content_hash("hello")

    def test_different_input_different_hash(self):
        assert EmbeddingService._content_hash("a") != EmbeddingService._content_hash("b")


# ---------------------------------------------------------------------------
# embed_post
# ---------------------------------------------------------------------------


class TestEmbedPost:
    @pytest.mark.asyncio
    async def test_embeds_new_post(self, service, ollama, embeddings_db):
        post = _make_post()
        result = await service.embed_post(post)

        assert result == "emb-uuid-123"
        ollama.embed.assert_awaited_once()
        embeddings_db.store_embedding.assert_awaited_once()

        # Verify store_embedding was called with correct source_type
        call_kwargs = embeddings_db.store_embedding.call_args
        assert call_kwargs.kwargs["source_type"] == "post"
        assert call_kwargs.kwargs["source_id"] == "1"
        assert call_kwargs.kwargs["metadata"] == {"title": "Test Title"}

    @pytest.mark.asyncio
    async def test_skips_unchanged_post(self, service, ollama, embeddings_db):
        embeddings_db.needs_reembedding.return_value = False

        result = await service.embed_post(_make_post())

        assert result is None
        ollama.embed.assert_not_awaited()
        embeddings_db.store_embedding.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_truncates_content_to_2000_chars(self, service, ollama, embeddings_db):
        long_content = "x" * 5000
        post = _make_post(content=long_content)

        await service.embed_post(post)

        # The text passed to ollama.embed should have content truncated at 2000
        embedded_text = ollama.embed.call_args[0][0]
        expected = f"Test Title\nTest excerpt\n{'x' * 2000}"
        assert embedded_text == expected

    @pytest.mark.asyncio
    async def test_embed_post_raises_on_ollama_error(self, service, ollama, embeddings_db):
        ollama.embed.side_effect = RuntimeError("Ollama down")

        with pytest.raises(RuntimeError, match="Ollama down"):
            await service.embed_post(_make_post())

    @pytest.mark.asyncio
    async def test_embed_post_raises_on_db_store_error(self, service, ollama, embeddings_db):
        embeddings_db.store_embedding.side_effect = Exception("DB write failed")

        with pytest.raises(Exception, match="DB write failed"):
            await service.embed_post(_make_post())

    @pytest.mark.asyncio
    async def test_content_hash_passed_to_db(self, service, ollama, embeddings_db):
        post = _make_post()
        combined = f"{post['title']}\n{post['excerpt']}\n{post['content'][:2000]}"
        expected_hash = _content_hash(combined)

        await service.embed_post(post)

        call_kwargs = embeddings_db.store_embedding.call_args.kwargs
        assert call_kwargs["content_hash"] == expected_hash


# ---------------------------------------------------------------------------
# embed_brain_knowledge
# ---------------------------------------------------------------------------


class TestEmbedBrainKnowledge:
    @pytest.mark.asyncio
    async def test_embeds_new_knowledge(self, service, ollama, embeddings_db):
        result = await service.embed_brain_knowledge("Glad Labs", "mission", "democratize AI")

        assert result == "emb-uuid-123"
        ollama.embed.assert_awaited_once()

        call_kwargs = embeddings_db.store_embedding.call_args.kwargs
        assert call_kwargs["source_type"] == "brain_knowledge"
        assert call_kwargs["source_id"] == "Glad Labs::mission"
        assert call_kwargs["metadata"] == {"entity": "Glad Labs", "attribute": "mission"}

    @pytest.mark.asyncio
    async def test_skips_unchanged_knowledge(self, service, ollama, embeddings_db):
        embeddings_db.needs_reembedding.return_value = False

        result = await service.embed_brain_knowledge("Glad Labs", "mission", "democratize AI")

        assert result is None
        ollama.embed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_combined_text_format(self, service, ollama, embeddings_db):
        await service.embed_brain_knowledge("Entity", "attr", "val")

        embedded_text = ollama.embed.call_args[0][0]
        assert embedded_text == "Entity attr: val"

    @pytest.mark.asyncio
    async def test_raises_on_ollama_error(self, service, ollama, embeddings_db):
        ollama.embed.side_effect = RuntimeError("GPU OOM")

        with pytest.raises(RuntimeError, match="GPU OOM"):
            await service.embed_brain_knowledge("E", "A", "V")

    @pytest.mark.asyncio
    async def test_raises_on_db_error(self, service, ollama, embeddings_db):
        embeddings_db.store_embedding.side_effect = Exception("DB fail")

        with pytest.raises(Exception, match="DB fail"):
            await service.embed_brain_knowledge("E", "A", "V")


# ---------------------------------------------------------------------------
# embed_all_posts (batch)
# ---------------------------------------------------------------------------


class TestEmbedAllPosts:
    @pytest.mark.asyncio
    async def test_embeds_all_new_posts(self, service, ollama, embeddings_db):
        posts = [_make_post(post_id=1), _make_post(post_id=2)]

        result = await service.embed_all_posts(posts)

        assert result == {"embedded": 2, "skipped": 0, "failed": 0}
        ollama.embed_batch.assert_awaited_once()
        assert embeddings_db.store_embedding.await_count == 2

    @pytest.mark.asyncio
    async def test_skips_unchanged_posts(self, service, ollama, embeddings_db):
        embeddings_db.needs_reembedding.return_value = False
        posts = [_make_post(post_id=1), _make_post(post_id=2)]

        result = await service.embed_all_posts(posts)

        assert result == {"embedded": 0, "skipped": 2, "failed": 0}
        ollama.embed_batch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mixed_new_and_unchanged(self, service, ollama, embeddings_db):
        # First post needs re-embedding, second doesn't
        embeddings_db.needs_reembedding.side_effect = [True, False]
        ollama.embed_batch.return_value = [SAMPLE_EMBEDDING]
        posts = [_make_post(post_id=1), _make_post(post_id=2)]

        result = await service.embed_all_posts(posts)

        assert result == {"embedded": 1, "skipped": 1, "failed": 0}
        ollama.embed_batch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_list(self, service, ollama, embeddings_db):
        result = await service.embed_all_posts([])

        assert result == {"embedded": 0, "skipped": 0, "failed": 0}
        ollama.embed_batch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_batch_embed_failure_returns_all_failed(self, service, ollama, embeddings_db):
        ollama.embed_batch.side_effect = RuntimeError("Batch failed")
        posts = [_make_post(post_id=1), _make_post(post_id=2)]

        result = await service.embed_all_posts(posts)

        assert result == {"embedded": 0, "skipped": 0, "failed": 2}

    @pytest.mark.asyncio
    async def test_individual_store_failure(self, service, ollama, embeddings_db):
        # First store succeeds, second fails
        embeddings_db.store_embedding.side_effect = [
            "emb-1",
            Exception("DB write failed"),
        ]
        posts = [_make_post(post_id=1), _make_post(post_id=2)]

        result = await service.embed_all_posts(posts)

        assert result == {"embedded": 1, "skipped": 0, "failed": 1}

    @pytest.mark.asyncio
    async def test_needs_reembedding_check_failure_skips(self, service, ollama, embeddings_db):
        # needs_reembedding raises for one post, should be counted as skipped
        embeddings_db.needs_reembedding.side_effect = [Exception("DB read fail"), True]
        ollama.embed_batch.return_value = [SAMPLE_EMBEDDING]
        posts = [_make_post(post_id=1), _make_post(post_id=2)]

        result = await service.embed_all_posts(posts)

        assert result["skipped"] == 1
        assert result["embedded"] == 1
